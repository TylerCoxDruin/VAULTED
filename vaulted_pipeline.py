#!/usr/bin/env python3
"""
VAULTED Podcast Pipeline
========================
Fully automated daily podcast production: research → script → audio.

Flow:
  1. Search the web (Brave API) + RSS feeds for fresh scam/fraud stories
  2. Score & rank by relevance, recency, and narrative potential
  3. Select the 3 best stories (fallback to AI-generated if needed)
  4. Ollama writes a storytelling-first podcast script (~2,600 words)
  5. Second Ollama pass strips out anything that sounds AI-written
  6. Save dated .txt script to Scripts/
  7. Submit to Mimika Studio for voice synthesis
  8. Save final .mp3 to Audio/

Requirements:
  - Python 3.8+
  - pip install feedparser requests beautifulsoup4
  - Brave Search API key in vaulted_config.json (free at brave.com/search/api)
  - Ollama running:  ollama serve  &&  ollama pull llama3.1:8b
  - Mimika Studio:  ./bin/mimikactl up

Usage:
  python3 vaulted_pipeline.py                  # Full pipeline (today)
  python3 vaulted_pipeline.py --date 03-25     # Specific date
  python3 vaulted_pipeline.py --dry-run        # Research only, no LLM/TTS
  python3 vaulted_pipeline.py --skip-tts       # Script only, skip audio
  python3 vaulted_pipeline.py --tts-only       # Re-run TTS on existing script
"""

import json
import os
import sys
import re
import time
import argparse
import hashlib
import logging
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import feedparser
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "vaulted_config.json"
HISTORY_PATH = SCRIPT_DIR / ".vaulted_history.json"
NAME_LOG_PATH = SCRIPT_DIR / ".vaulted_names.json"
LOG_PATH = SCRIPT_DIR / "Logs"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vaulted")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_history() -> dict:
    if HISTORY_PATH.exists():
        with open(HISTORY_PATH) as f:
            return json.load(f)
    return {"used_hashes": [], "used_titles": [], "last_run": None}


def save_history(history: dict):
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)


# ---------------------------------------------------------------------------
# Name Pool — large diverse roster of first names + job titles
# Names are tracked monthly so the same name won't repeat within ~30 days.
# Within a single episode the pipeline picks 3 unique names (one per story).
# ---------------------------------------------------------------------------

NAME_POOL = [
    # Format: (first_name, job_title)
    ("Dana",    "office manager"),
    ("Marcus",  "retired teacher"),
    ("Elena",   "graphic designer"),
    ("Sarah",   "marketing consultant"),
    ("James",   "warehouse supervisor"),
    ("Priya",   "software developer"),
    ("Carla",   "dental hygienist"),
    ("Trevor",  "electrician"),
    ("Nadia",   "paralegal"),
    ("Gerald",  "retired postal worker"),
    ("Simone",  "property manager"),
    ("Kevin",   "truck driver"),
    ("Yolanda", "home health aide"),
    ("Aaron",   "high school gym teacher"),
    ("Fatima",  "pharmacy technician"),
    ("Doug",    "HVAC contractor"),
    ("Renee",   "bookkeeper"),
    ("Miles",   "sous chef"),
    ("Ingrid",  "physical therapist"),
    ("Calvin",  "car dealership finance manager"),
    ("Rosa",    "school librarian"),
    ("Patrick", "insurance adjuster"),
    ("Tasha",   "nail salon owner"),
    ("Leonard", "retired firefighter"),
    ("Amara",   "nursing student"),
    ("Hector",  "landscaping business owner"),
    ("Gloria",  "church administrator"),
    ("Darnell", "correctional officer"),
    ("Wren",    "freelance photographer"),
    ("Irene",   "grocery store manager"),
    ("Tobias",  "middle school principal"),
    ("Cynthia", "court reporter"),
    ("Omar",    "food truck operator"),
    ("Leila",   "dental receptionist"),
    ("Frank",   "retired accountant"),
    ("Bianca",  "yoga studio owner"),
    ("Wendell", "building superintendent"),
    ("Nora",    "pediatric nurse"),
    ("Andre",   "sanitation worker"),
    ("Claudia", "real estate agent"),
    ("Jerome",  "security guard"),
    ("Mia",     "dog groomer"),
    ("Rupert",  "retired bank teller"),
    ("Destiny", "cosmetology student"),
    ("Barry",   "auto mechanic"),
    ("Trisha",  "social worker"),
    ("Winston", "pest control technician"),
    ("Alicia",  "catering business owner"),
    ("Desmond", "high school football coach"),
    ("Fiona",   "tax preparer"),
    ("Ronnie",  "deli owner"),
    ("Celeste", "medical biller"),
    ("Travis",  "plumber"),
    ("Miriam",  "retired nurse"),
    ("Quincy",  "retail store manager"),
    ("Lena",    "childcare worker"),
    ("Stanley", "retired city bus driver"),
    ("Monique", "hair salon owner"),
    ("Clifton", "building inspector"),
    ("Yvonne",  "church daycare director"),
    ("Sidney",  "pizza shop owner"),
    ("Vivian",  "home appraiser"),
    ("Grant",   "shipping coordinator"),
    ("Naomi",   "veterinary assistant"),
    ("Otis",    "retired machine operator"),
    ("Keisha",  "claims adjuster"),
    ("Roland",  "retired military chaplain"),
    ("Pam",     "travel agent"),
    ("Emmett",  "produce market owner"),
    ("Crystal", "apartment leasing agent"),
    ("Victor",  "retired detective"),
    ("Adelle",  "preschool teacher"),
    ("Chester", "retired welder"),
    ("Tamara",  "payroll coordinator"),
    ("Lionel",  "restaurant owner"),
    ("Sandra",  "hospice nurse"),
    ("Marvin",  "independent contractor"),
    ("Jade",    "nail technician"),
    ("Harold",  "retired letter carrier"),
    ("Yara",    "dietitian"),
    ("Clyde",   "auto body shop owner"),
    ("Vanessa", "HR coordinator"),
    ("Theodore","retired principal"),
    ("Mona",    "event planner"),
    ("Reggie",  "youth baseball coach"),
    ("Dawn",    "county clerk"),
    ("Bryant",  "construction foreman"),
    ("Elise",   "speech therapist"),
    ("Norm",    "retired electrician"),
    ("Lacey",   "bakery owner"),
    ("Tyrone",  "school bus driver"),
    ("Brianna", "certified nursing assistant"),
    ("Howard",  "retired pharmacist"),
    ("Selena",  "day spa owner"),
    ("Kirk",    "airport ground crew"),
    ("Tamika",  "urban planner"),
    ("Alton",   "retired steelworker"),
    ("Gwen",    "optician"),
    ("Deon",    "youth program director"),
    ("Patrice", "community college instructor"),
]


def load_name_log() -> dict:
    """Load name usage log: {name: "YYYY-MM" of last use}."""
    if NAME_LOG_PATH.exists():
        with open(NAME_LOG_PATH) as f:
            return json.load(f)
    return {}


def save_name_log(log: dict):
    with open(NAME_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)


def pick_episode_names(count: int = 3) -> list[tuple]:
    """
    Pick `count` unique names for this episode.
    Names used this calendar month are excluded — they won't repeat for ~30 days.
    Returns list of (name, job_title) tuples.
    """
    log = load_name_log()
    this_month = datetime.now().strftime("%Y-%m")

    available = [(n, j) for (n, j) in NAME_POOL if log.get(n) != this_month]

    # If somehow everything is used this month, reset and use the full pool
    if len(available) < count:
        log.info("  Name pool exhausted for this month — resetting")
        available = list(NAME_POOL)

    import random
    chosen = random.sample(available, min(count, len(available)))

    # Mark them used this month
    for name, _ in chosen:
        log[name] = this_month
    save_name_log(log)

    return chosen


def article_hash(title: str, link: str = "") -> str:
    return hashlib.md5(f"{title.lower().strip()}|{link}".encode()).hexdigest()


def _clean_html(text: str) -> str:
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    clean = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", clean)[:1500]


def _parse_date(entry) -> Optional[str]:
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                dt = datetime(*parsed[:6])
                return dt.isoformat()
            except Exception:
                pass
    return None


# ---------------------------------------------------------------------------
# Stage 1a: Google News RSS Search (free, no API key required)
# ---------------------------------------------------------------------------
# Google News lets you search by keyword via RSS — completely free.
# URL format: https://news.google.com/rss/search?q=QUERY&hl=en-US&gl=US&ceid=US:en
GOOGLE_NEWS_QUERIES = [
    "scam fraud victims",
    "phishing attack warning",
    "identity theft scheme arrested",
    "online scam new method",
    "romance scam crypto",
    "government impersonation scam",
    "smishing vishing text scam",
    "investment fraud scheme",
    "fake website shopping scam",
    "ransomware attack",
    "elder fraud scam",
    "deepfake AI scam",
]

GOOGLE_NEWS_BASE = "https://news.google.com/rss/search"

def search_google_news(config: dict) -> list[dict]:
    """
    Search Google News RSS for fresh scam/fraud stories.
    Free, no API key, no rate limits (within reason).
    Runs a rotating selection of queries each day for variety.
    """
    keywords = [kw.lower() for kw in config["search_keywords"]]
    articles = []

    # Rotate which queries run based on the day of the week
    # so we get variety without hammering all 12 every morning
    day_offset = datetime.now().weekday()  # 0=Mon, 4=Fri
    num_queries = 6  # run 6 queries per day
    queries_today = []
    for i in range(num_queries):
        queries_today.append(GOOGLE_NEWS_QUERIES[(day_offset * num_queries + i) % len(GOOGLE_NEWS_QUERIES)])

    log.info(f"  Google News searches: {queries_today}")

    for query in queries_today:
        try:
            params = {
                "q": query,
                "hl": "en-US",
                "gl": "US",
                "ceid": "US:en",
            }
            # feedparser handles Google News RSS directly
            url = f"{GOOGLE_NEWS_BASE}?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
            parsed = feedparser.parse(url)

            count = 0
            for entry in parsed.entries[:8]:
                title = entry.get("title", "").strip()
                # Google News titles often include " - Source Name" at the end
                source_match = re.search(r"\s+-\s+(.+)$", title)
                source_name = source_match.group(1) if source_match else "Google News"
                clean_title = re.sub(r"\s+-\s+.+$", "", title).strip()

                article = {
                    "title": clean_title,
                    "link": entry.get("link", ""),
                    "summary": _clean_html(entry.get("summary", "")),
                    "published": _parse_date(entry),
                    "source": source_name,
                    "source_priority": 1,
                    "category": "google_news",
                    "from_search": True,
                    "search_query": query,
                }
                article["relevance"] = _score_relevance(article, keywords)
                articles.append(article)
                count += 1

            log.info(f"  '{query}': {count} results")
            time.sleep(0.3)  # small delay between requests

        except Exception as e:
            log.warning(f"  Google News search failed for '{query}': {e}")

    return articles


# ---------------------------------------------------------------------------
# Stage 1b: RSS Feeds
# ---------------------------------------------------------------------------
def fetch_all_feeds(config: dict) -> list[dict]:
    """Fetch and parse all configured RSS feeds."""
    feeds = config["rss_feeds"]
    keywords = [kw.lower() for kw in config["search_keywords"]]
    all_articles = []

    for feed_info in feeds:
        name = feed_info["name"]
        url = feed_info["url"]
        priority = feed_info.get("priority", 3)
        category = feed_info.get("category", "general")

        try:
            parsed = feedparser.parse(url)
            if parsed.bozo and not parsed.entries:
                log.warning(f"  RSS failed: {name}")
                continue

            count = 0
            for entry in parsed.entries[:15]:
                article = {
                    "title": entry.get("title", "").strip(),
                    "link": entry.get("link", ""),
                    "summary": _clean_html(entry.get("summary", "")),
                    "published": _parse_date(entry),
                    "source": name,
                    "source_priority": priority,
                    "category": category,
                    "from_search": False,
                }
                article["relevance"] = _score_relevance(article, keywords)
                all_articles.append(article)
                count += 1

            if count > 0:
                log.info(f"  RSS [{name}]: {count} articles")
        except Exception as e:
            log.warning(f"  RSS error [{name}]: {e}")

    return all_articles


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _score_relevance(article: dict, keywords: list[str]) -> float:
    """Score an article 0-100 based on how good a VAULTED story it would make."""
    text = f"{article['title']} {article.get('summary', '')}".lower()
    score = 0.0

    # 1. Keyword relevance (up to 40 pts)
    hits = sum(1 for kw in keywords if kw in text)
    score += min(hits * 7, 40)

    # 2. Recency (up to 30 pts) — prefer stories from this week
    pub = article.get("published")
    if pub:
        try:
            if isinstance(pub, str) and "ago" in pub.lower():
                # Brave returns strings like "3 hours ago", "2 days ago"
                if "hour" in pub or "minute" in pub:
                    score += 30
                elif "day" in pub:
                    days = int(re.search(r"(\d+)", pub).group(1)) if re.search(r"\d+", pub) else 3
                    score += max(0, 30 - days * 3)
                elif "week" in pub:
                    score += 10
            else:
                pub_date = datetime.fromisoformat(pub)
                days_old = (datetime.now() - pub_date).days
                score += max(0, 30 - days_old * 2)
        except Exception:
            pass

    # 3. Source quality (up to 15 pts)
    priority = article.get("source_priority", 3)
    score += max(0, (4 - priority) * 5)

    # 4. Story potential — words that signal a compelling narrative (up to 15 pts)
    narrative_words = [
        "victim", "lost", "stolen", "stole", "arrested", "charged", "sentenced",
        "million", "billion", "warning", "alert", "new", "emerging", "devastating",
        "investigation", "ring", "gang", "elderly", "retiree", "family", "children",
        "hospital", "school", "bank", "crypto", "romance", "deepfake", "ai",
        "record", "massive", "surge", "wave", "outbreak", "targeting",
    ]
    story_hits = sum(1 for w in narrative_words if w in text)
    score += min(story_hits * 2, 15)

    return round(score, 1)


def select_top_stories(
    articles: list[dict],
    history: dict,
    count: int = 3,
) -> list[dict]:
    """Pick the best non-repeated stories with category and scam-type diversity."""
    used_hashes = set(history.get("used_hashes", []))
    recent_scam_types = get_recent_scam_types()

    # Deduplicate by title similarity and filter used ones
    seen_titles = set()
    fresh = []
    for a in articles:
        h = article_hash(a["title"], a["link"])
        title_key = re.sub(r"\W+", " ", a["title"].lower()).strip()[:60]
        if h not in used_hashes and title_key not in seen_titles and a["title"]:
            fresh.append(a)
            seen_titles.add(title_key)

    # Soft-penalize scam types used recently — penalty fades linearly over the cooldown window
    for a in fresh:
        scam_type = classify_story_type(a)
        a["scam_type"] = scam_type
        if scam_type in recent_scam_types:
            days_ago = recent_scam_types[scam_type]
            penalty = max(0.0, 1.0 - (days_ago / SCAM_TYPE_COOLDOWN_DAYS))
            a["relevance"] = a["relevance"] * (1.0 - 0.4 * penalty)  # max 40% penalty

    fresh.sort(key=lambda a: a["relevance"], reverse=True)

    # Pick with feed-category diversity
    selected = []
    used_categories = []
    for article in fresh:
        if len(selected) >= count:
            break
        cat = article["category"]
        if used_categories.count(cat) < 2:
            selected.append(article)
            used_categories.append(cat)

    # Fill remaining slots if needed
    if len(selected) < count:
        remaining = [a for a in fresh if a not in selected]
        selected.extend(remaining[: count - len(selected)])

    if recent_scam_types:
        log.info(f"  Recent scam types (last {SCAM_TYPE_COOLDOWN_DAYS}d): {dict(list(recent_scam_types.items())[:5])}")

    return selected


# ---------------------------------------------------------------------------
# Stage 2: Script Generation via Ollama
# ---------------------------------------------------------------------------

# ---- SYSTEM PROMPT --------------------------------------------------------
SYSTEM_PROMPT = """\
You are writing a script for VAULTED, a true-crime-style daily podcast about scams, phishing, and fraud. The host is Ty — a real person who sounds like he's talking directly to a friend, not reading a report.

TONE & VOICE:
- Storytelling-first. Every story opens like a thriller. Do NOT reveal it's a scam until the listener is already hooked and invested in the person.
- Conversational and direct. Ty speaks like a human being. Never clinical, never formal.
- Genuinely angry on behalf of victims. When something bad happens to someone in a story, Ty reacts like a real person — not a narrator observing from a distance.
- Uses "you" constantly — pulling the listener into the story, making it feel like it could happen to them.
- Rhetorical questions and mid-story reactions throughout: "here's the thing that gets me...", "and this is where it gets really stupid", "I've seen this play out a dozen times and it never stops being infuriating."
- Ty interrupts the story to react in his own voice. Not just at the end — mid-story, mid-paragraph if it fits. A real host doesn't wait until the recap to have opinions. He has them in real time.

SENTENCE RHYTHM — THIS IS CRITICAL:
Do NOT fall into the AI trap of "Short sentence. Short sentence. Emotional punchline." repeated over and over. That cadence puts listeners into a trance by minute ten. Vary it deliberately. Mix short punchy sentences with long, rambling, slightly-messy ones that breathe — sentences where Ty is clearly thinking out loud, backtracking a little, adding detail he almost forgot. Real human speech is not a drum machine. It has stumbles, asides, and momentum shifts built in.

COLD OPEN LANGUAGE:
Write cold opens that feel grounded and specific, not cinematic. Real people don't notice "screens glowing like flares" or "sickly yellow light." They notice mundane specific things — the hum of the refrigerator, that they've been staring at the same email for ten minutes, that their kid is asleep down the hall. The tension in a good cold open comes from recognizing something ordinary gone wrong, not from film-school lighting descriptions.

BANNED EMOTIONAL CLICHÉS:
Never use these or anything like them: "cold pit in the stomach", "stomach drops", "heart races", "blood runs cold", "hands trembling", "world closes in", "sickly glow", "screen glowing like a flare". These are AI defaults. A real person describing anxiety says something specific — "she just kept refreshing the page, like the number was going to change", "he didn't sleep that night, just sat there doing the math over and over."

EPISODE STRUCTURE (follow this EXACTLY):
1. COLD OPEN (80-100 words): Start mid-story, dropped into a scene from Story 1 — a specific person, a specific ordinary moment that starts to feel wrong. No cinematic descriptions. No poetic lighting. Just a real person in a real situation. Do NOT say "Welcome to VAULTED" or anything show-related. Do NOT mention scams or fraud yet. End on a cliff-hanger line that makes the listener need to know what happens next.
[INTRO_STING]
2. INTRO (30-50 words): After the music, Ty steps back in naturally — not like a news anchor, like a person. Ground the listener with who's talking and what show this is, then say something real about the energy of today's episode. No headlines list, no summary. Just Ty, honest and easy, bringing the listener back in.

CRITICAL — THE SHOW SIGNATURE:
The show name and host name must appear somewhere in the intro, but it must feel casual, never announced. The phrase "this is VAULTED" must NEVER end a sentence on its own — it must flow into the next clause. A period after VAULTED makes the TTS voice drop into a TV-announcer cadence. Keep it mid-thought.

RIGHT: "I'm Ty — this is VAULTED — let's get into it."
WRONG: "I'm Ty — this is VAULTED. Alright..." ← the period kills the flow

VARIETY IS MANDATORY — the intro must feel different every episode. The phrase "Alright, X stories today, and I'm not gonna lie, this first one still gets me" is retired. Use a different approach every time.

INTRO SENTENCE STRUCTURE — CRITICAL: Do NOT write the intro as a series of short choppy sentences separated by periods. "I'm Ty. Welcome to VAULTED. We're jumping straight in today." — that reads fine on paper but TTS treats each period as a hard stop with no breath, so it fires out as a rapid staccato burst with zero pause between thoughts. Instead, connect short thoughts with dashes or commas so the voice can flow: "I'm Ty — this is VAULTED — and we're jumping straight in." Also: never end the intro on a trailing single word like "So." or "Right." or "Okay." — those dangle in the audio and sound unfinished. The intro should end on a complete thought that propels into the story, not a hanging word.

Examples of the range:

- "I'm Ty — this is VAULTED — and we're back. Three stories. None of them are fine. Let's go."
- "VAULTED, I'm Ty — I've been thinking about this first one since I read it. It's one of those."
- "Hey. I'm Ty — VAULTED — glad you're here. We've got a lot to cover and I don't want to waste your time, so."
- "I'm Ty, this is VAULTED — today's a heavy one. Not the worst I've done, but close. Let's do it."
- "VAULTED. I'm Ty. You know how sometimes a scam sounds so obvious you think you'd never fall for it? Today we test that."
- "I'm Ty — VAULTED — three stories, one worse than the last. Back into it."
- "I'm Ty — this is VAULTED — and honestly, the story we're picking back up on, I kept thinking about it. Here we go."
- "VAULTED, I'm Ty — no preamble, I just want to get you back to this one. It gets worse."

Vary the placement of the show name, vary the energy, vary the length. Never write two intros that sound structurally the same. The only constants: Ty's name, the show name, and that it sounds like a real person picking back up — not a broadcast.
[PAUSE]
3. STORY 1 (650-750 words): The VERY FIRST SENTENCE must be a brief verbal bridge back to the cold open scene — reconnect the listener without re-summarizing. Examples: "Okay, so back to Dana." / "Right, so there she is — seventeen tabs open, phone in her hand." / "So here's what was actually happening in that apartment." Then build the full narrative arc: establish the victim's life before the scam, show the scam unfolding step by step in chronological order, explain the mechanics in Ty's own voice ("and here's what was actually happening the whole time"), show the specific financial and emotional aftermath with real numbers and real consequences. Every story must end with a concrete specific outcome — a dollar amount, a sentencing, a consequence — never vague "she lost a lot of money" language.
[TRANSITION_1]
4. STORY 2 (700-800 words): Fresh story, same full-arc structure. Establish the victim as a real person with a real life. Show the scam unfolding in chronological sequence — day one, the escalation, the moment they realized. End with a specific concrete outcome. NO bullet points.
[TRANSITION_2]
5. STORY 3 (700-800 words): Same full-arc structure. Same rules.
[PAUSE]
6. HOW TO STAY SAFE (300-350 words): Ty talking to the listener like a friend who just watched three people get hurt. NOT a list. He should explicitly reference at least one specific moment from each of the three stories by the victim's name — "remember what happened to Dana when she...", "this is exactly what got Marcus" — then pull a common thread across all three and turn it into actionable plain-language advice. Make the listener feel like Ty is talking directly to them. End with one memorable sentence — the kind of thing you'd repeat to a friend.
7. OUTRO (40-50 words): Use the exact closing instruction provided in the prompt.
[OUTRO_STING]

LENGTH: Aim for 15-20 minutes of audio — roughly 2,300 to 3,100 words at 155 WPM. Going a little over is fine. Write until each story is fully told, not until a word count is hit.

STORY CRAFT RULES:
- Show the victim as a fully realized person before anything bad happens. Give them a job, a family detail, a small humanizing moment. The listener needs to be invested in this person before the scam starts.
- Show the scam in chronological sequence. Day one looked like this. Then this happened. Then this. The listener should feel like they're watching it unfold in real time, not being briefed on it.
- Every time the scammer does something clever or manipulative, Ty should name what they did and why it worked. Not in a clinical way — in a "I can't believe how good this person was at this" way.
- At least once per story, Ty should turn directly to the listener: "And I want you to think about this for a second, because this is the part that actually could happen to you."

WRITING RULES — NON-NEGOTIABLE:
- Zero bullet points or numbered lists in any narrative section. Period.
- Never use: "furthermore", "moreover", "in conclusion", "it is important to note", "it's worth mentioning", "in today's digital age", "it goes without saying", "verify out of band", "out-of-band verification", "attack vector", "attack surface", "threat landscape", "end user", "credential" (say "login" or "password" instead), "scary" or "the scary part" (say what specifically is unsettling — be precise, not vague), "the damage was done" (find a specific way to describe the actual consequence), "coffee" or "cup of coffee" as a scene-setting prop (it's used in almost every cold open — find something else: a phone screen, a half-eaten lunch, a dog barking down the hall, a TV on in the background — anything specific and different).
- Never use time-of-day greetings: "tonight", "this morning", "this evening", "this afternoon". People listen to podcasts at all hours and days after release. If a time reference is needed in the intro, use "today" — it's always correct.
- Never start consecutive sentences with the same word.
- Fictionalize ALL victim names and personal details. Use ONLY the victim names provided in the prompt for the three main story characters — do not substitute or invent different names for the main victims. Supporting characters (spouse, coworker, bank agent, family member) may have freely varied names.
- Use these exact audio markers in the script — they trigger music/silence in production and are NEVER read aloud:
    [INTRO_STING]  — after the cold open, before the intro
    [PAUSE]        — after the intro, and after Story 3 / before How to Stay Safe
    [TRANSITION_1] — between Story 1 and Story 2
    [TRANSITION_2] — between Story 2 and Story 3
    [OUTRO_STING]  — at the very end, after the outro words
- Use specific invented details — dollar amounts, cities, timestamps, job titles — to make stories feel real.
- ALWAYS write numbers and dollar amounts as words, never digits. Write "one point one million dollars" not "$1.1 million". Write "fifty thousand dollars" not "$50,000". Write "forty-two percent" not "42%". This is a spoken audio show — digits sound broken when read aloud.
- When explaining how a scam works, have Ty explain it mid-story in his own voice: "And here's what was actually happening the whole time..." Never break into a Wikipedia-style explainer.
- DRAMATIC EMPHASIS VIA REPETITION: If a phrase or number is repeated for impact — especially dollar amounts — the [PAUSE] goes AFTER the echo, not before it. The structure is: statement → echo → silence. This is the order that lands hardest. CORRECT: "She lost forty-five thousand dollars. Forty-five thousand dollars. [PAUSE]" — the echo hits, THEN silence falls, and the listener sits with it. WRONG: "She lost forty-five thousand dollars. [PAUSE] Forty-five thousand dollars." — that version cuts off the momentum before the echo even lands. Always: say it, say it again, then let it breathe. This rule is mandatory for any repeated dollar amount or phrase used for dramatic weight.
- NARRATOR STEPPING IN: When Ty breaks from narrating the story to speak directly to the listener, the shift needs a gear-change — a moment where the listener can feel the energy slow down and the frame change. DO NOT snap directly from fast-moving narration into commentary. Write a transition sentence that decelerates: something that ends the story beat, then opens the commentary breath. The verbal signal phrase that follows should feel unhurried, like Ty sitting back before he says what he's about to say.

  RETIRED — never use these again: "And look, I want to stop here for a second because..." (used in almost every episode), "I need you to think about this for a second" (overused), "I need you to listen" (too preachy).

  USE INSTEAD — rotate through these and vary freely:
  - "Okay — real talk —"
  - "Here's the thing about that, and I'm talking to you directly now —"
  - "Step outside the story for a second."
  - "Let me just — pause here for a sec."
  - "I keep coming back to this part."
  - "That's the moment I want you to sit with."
  - "Here's what gets me about this."
  - "And I want to be honest with you about something."
  - "This is the part that actually matters."
  - "I'm going to break from the story for just a minute."

  Never use the same opener twice in a single episode. Never just pivot mid-sentence from narration to commentary with no signal at all.
- TEACH THE TERMS: VAULTED is education, not just storytelling. Use the real industry terms — social engineering, phishing, smishing, vishing, spoofing, pretexting, account takeover. But every time a term appears for the first time in an episode, Ty defines it conversationally in the same breath. Not like a textbook — like a friend explaining it: "That's called social engineering — it's not hacking a computer, it's hacking the person. Getting them to hand over access willingly." The listener leaves knowing the word and what it actually means. The only exception: pure insider jargon with no listener value ("verify out of band", "threat vector", "attack surface") — skip these entirely and just say what you mean.
- HOW TO STAY SAFE — SPECIFIC AND ACTIONABLE: Advice must be concrete, not vague. Not "be careful online" — "if someone calls claiming to be your bank, hang up and call the number on the back of your card. Every time. No exceptions." Use the real terms here too — this is where the listener connects the story to the vocabulary they'll use if it happens to them.
- OUTRO PACING — CRITICAL: The outro must be written to be read SLOWLY. Short, deliberate sentences with clear space between them. Ty is not rushing to get off air — he's closing a conversation. The listener should feel the episode land before it ends. No long run-on sentences in the outro. No lists. Just a few short, warm, unhurried thoughts, then out. Think of it like the last thing someone says before hanging up a good phone call — measured, genuine, not in a hurry. If it reads fast, rewrite it slower.
"""

# Day-of-week outro instructions
# PACE NOTE (applies to ALL days): The outro must be written to be delivered SLOWLY.
# Short sentences. Deliberate pauses between thoughts. Ty is not rushing out the door —
# he's wrapping up a conversation. The listener should feel the episode landing, not ending.
# Every outro MUST say the day name somewhere naturally — listeners need that anchor.
OUTRO_BY_DAY = {
    "Monday":    "End with a clean Monday sign-off. Say 'Monday' somewhere in it naturally — the listener needs to hear what day it is. Ty thanks the listener, says his name and the show name, acknowledges it's the start of the week — easy, warm, unhurried. Write it slowly — short thoughts, space between them. NEVER rush. Examples: 'That's VAULTED. I'm Ty — hope Monday treated you well. We'll be back tomorrow.' / 'Thanks for starting the week with us. I'm Ty — this is VAULTED. See you Tuesday.' / 'That's all for a Monday. I'm Ty — stay sharp out there, and we'll see you tomorrow.'",
    "Tuesday":   "End with a Tuesday sign-off. Say 'Tuesday' somewhere in it naturally — always name the day. Ty thanks the listener, says his name and the show name, keeps it warm and unhurried. Write it slowly — deliberate, not rushed. NEVER end without naming Tuesday. Examples: 'That's VAULTED for a Tuesday. I'm Ty — appreciate you being here. See you tomorrow.' / 'Thanks for listening. I'm Ty — hope your Tuesday was a good one. VAULTED is back tomorrow.' / 'That's all for Tuesday. I'm Ty — stay safe out there, and we'll see you Wednesday.'",
    "Wednesday": "End with a Wednesday sign-off — acknowledge the midweek without making it a big deal. Say 'Wednesday' in it somewhere naturally. Ty thanks the listener, says his name and the show name. Slow and warm — not rushed. Examples: 'Halfway through the week. That's VAULTED — I'm Ty. See you Thursday.' / 'That's all for a Wednesday. Thanks for being here. I'm Ty — back tomorrow.' / 'Wednesday done. I'm Ty — this is VAULTED. Stay safe, and we'll see you tomorrow.'",
    "Thursday":  "End with a Thursday sign-off — name the day, hint that Friday is one more sleep away without overdoing it. Ty thanks the listener, says his name and the show name. Slow and deliberate. Examples: 'That's VAULTED for Thursday. I'm Ty — one more episode before the weekend. See you Friday.' / 'Thanks for listening on a Thursday. I'm Ty — we've got one more tomorrow, then the weekend. Stay safe.' / 'Almost there. That's VAULTED — I'm Ty. See you Friday for the last one of the week.'",
    "Friday":    "End with a genuine Friday send-off that feels earned — not performative, just real. Say 'Friday' and make it clear there are NO new episodes over the weekend — VAULTED returns Monday. Write it slowly — this one gets the most space. Ty wraps the week, gives something warm to carry into the weekend. Vary the energy every week. NEVER say 'back tomorrow' on a Friday. Examples: 'That's the week. I'm Ty — this is VAULTED. Enjoy your Friday, stay sharp out there, and we'll see you Monday.' / 'Five for the week. I'm Ty — thanks for spending it with us. No new episodes this weekend, but VAULTED is back Monday. Have a good one.' / 'That's a wrap on a Friday. Stay safe out there — seriously, after everything we just covered. I'm Ty, this is VAULTED, and we'll see you next week.'",
    "Saturday":  "End with: 'Thanks for listening to VAULTED. I'm Ty. Stay safe out there.'",
    "Sunday":    "End with: 'Thanks for listening to VAULTED. I'm Ty. Stay safe out there — new episodes start again Monday.'",
}

# ---- STORY DRAFT PROMPT ---------------------------------------------------
STORY_DRAFT_PROMPT = """\
Write today's VAULTED podcast script. Use the story seeds below as your raw material — take the real event, fictionalize the victim, and build each one into a gripping human story.

Today is {day_of_week}.

VICTIM NAMES FOR THIS EPISODE — USE THESE EXACT NAMES, ONE PER STORY, IN ORDER:
Story 1 victim: {name1} ({job1})
Story 2 victim: {name2} ({job2})
Story 3 victim: {name3} ({job3})
Do NOT use any other names for the main victims. Supporting characters (spouse, daughter, coworker, etc.) may use different names — vary them freely.

{terms_context}
STORY SEED 1 — {s1_type}
Title: {s1_title}
Source: {s1_source}
What happened: {s1_summary}

STORY SEED 2 — {s2_type}
Title: {s2_title}
Source: {s2_source}
What happened: {s2_summary}

STORY SEED 3 — {s3_type}
Title: {s3_title}
Source: {s3_source}
What happened: {s3_summary}

OUTRO INSTRUCTION — USE THIS EXACTLY:
{outro_instruction}

LENGTH GUIDANCE:
Write until each story is fully told — don't pad, but don't cut short either. The episode should run 15 to 20 minutes of audio. At 155 words per minute, that's roughly 2,300 to 3,100 words total. Going slightly over is fine. Real podcasts run long sometimes — that's human.

Rough section targets to keep you oriented (these are guides, not rules):
- Cold open: ~80-100 words
- Intro: ~30-50 words
- Story 1 continuation: ~650-800 words
- Story 2: ~650-800 words
- Story 3: ~650-800 words
- How to Stay Safe: ~300-400 words
- Outro: ~40-60 words

If a story needs more space to land properly, give it more space. If a section is naturally shorter but feels complete, leave it. The priority is always that the listener is engaged — not that you hit an exact number.

Begin writing now. Start with the cold open. No preamble, no title, no section labels. Drop straight into a scene.
"""

# ---- FALLBACK: AI-GENERATED STORIES PROMPT --------------------------------
GENERATE_STORIES_PROMPT = """\
Today's real news search didn't turn up enough compelling scam stories. Generate 3 realistic, plausible scam story seeds based on current scam trends. These should feel like they could have happened this week — they're composite scenarios based on real patterns, not specific real events.

For each story, provide:
- A one-line title
- The scam type (e.g., "romance scam", "IRS impersonation", "fake job offer")
- A 3-4 sentence summary of what happened to the victim and how the scam worked
- Why it's particularly relevant or dangerous right now

Make them diverse — different victim types (e.g., elderly person, young professional, small business owner), different scam mechanics, and different platforms (phone, email, social media, text).

Return them in this exact format:
STORY 1:
Title: [title]
Type: [scam type]
Summary: [3-4 sentences]
Why now: [1-2 sentences]

STORY 2:
[same format]

STORY 3:
[same format]
"""

# ---- HUMANIZE PROMPT ------------------------------------------------------
HUMANIZE_PROMPT = """\
You are editing a podcast script to make it sound like a real human recorded it — not written by AI. Go through every single sentence and ask: "would a real person say this out loud?"

CRITICAL: Do NOT shorten the script. Do NOT cut content. Do NOT summarize. Your job is to transform the language, not reduce it. The final output must be at least as long as the input — ideally slightly longer because you're adding micro-reactions and human asides.

PRESERVE THESE EXACTLY — do not touch or move them, they are audio production markers:
[INTRO_STING], [PAUSE], [TRANSITION_1], [TRANSITION_2], [OUTRO_STING]

SPECIFIC THINGS TO FIX:
1. Find any sentence that sounds "written" rather than "spoken" and rewrite it in spoken language. Example: "The perpetrators utilized sophisticated social engineering techniques" → "These guys were good. Like, genuinely scary good at getting inside your head."
2. Add micro-reactions throughout — when something shocking happens in the story, Ty should react in real time. A "That's insane." A "I still can't believe this worked." A "And this is where it gets dark." These should feel spontaneous, not placed at predictable intervals.
3. Vary sentence length aggressively. After two long sentences, throw in a three-word sentence. Then a question. Mix the rhythm constantly.
4. Add natural spoken openers to paragraphs: "So here's the thing.", "Look.", "And this is important.", "I want you to think about this for a second.", "Here's what nobody tells you."
5. Find any place where Ty tells instead of shows — and show instead. Don't say "the victim was scared", describe the behavior that showed fear.
6. Check the FIRST SENTENCE of Story 1 (after [PAUSE]): it must be a clear verbal bridge back to the cold open. If it isn't, rewrite it to reconnect. Something like "Okay, so back to [name]." or "Right — so where were we." The listener just heard theme music; they need to be guided back.
7. Check the "How to Stay Safe" section: it must reference specific moments from each of the three stories by the victim's name. If it reads like a generic PSA, rewrite it so Ty is clearly talking about the actual people from today's episode.
8. Double-check: no bullet points in the narrative sections, no formal language, no AI tells — especially "moreover", "furthermore", "it's worth noting".
9. RHETORICAL QUESTIONS: This script is read by a voice synthesis engine that does not naturally rising-inflect questions. Any rhetorical question that relies purely on vocal inflection to land will fall flat — it'll sound like a statement with a question mark. Rewrite them as statements or restructure so the meaning lands without the inflection. BAD: "You know that sound?" GOOD: "You know that sound. That low hum — the kind that fades into the background after a while." The question mark is not your friend here.

Return ONLY the revised script. No notes, no commentary, no "here's what I changed."

SCRIPT TO REVISE:
{script}
"""

# ---- METADATA PROMPT -------------------------------------------------------
METADATA_PROMPT = """\
You are writing distribution metadata for a podcast episode of VAULTED — a true-crime-style show about scams, phishing, and fraud, hosted by Ty.

Read the full episode script below and generate distribution metadata.

Return ONLY a valid JSON object with these exact keys. No markdown fences, no commentary, no explanation — just the raw JSON object starting with {{ and ending with }}:

{{
  "title": "A punchy 5-8 word episode title. No 'VAULTED:' prefix. Lead with the hook — the most alarming or intriguing thing about today's episode.",
  "tagline": "One sentence. The elevator pitch for this specific episode. What makes a new listener click play right now.",
  "show_notes": "2-3 paragraphs in Ty's casual spoken voice. Paragraph 1: tease all three stories without spoiling outcomes. Paragraph 2: what the listener will actually learn — the real terms, the real tactics, what to watch for. Paragraph 3: short call to action (subscribe, leave a review, share with someone who needs to hear this).",
  "faq": [
    {{"q": "Question a listener would actually Google after hearing this episode", "a": "Clear 2-3 sentence answer in plain language — no jargon"}},
    {{"q": "...", "a": "..."}},
    {{"q": "...", "a": "..."}},
    {{"q": "...", "a": "..."}},
    {{"q": "...", "a": "..."}}
  ],
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8", "tag9", "tag10"]
}}

RULES:
- title: punchy and specific, not generic. "How a Fake Bank Agent Drained a Teacher's Life Savings" not "Bank Fraud Story"
- tagline: creates urgency or curiosity. Should make someone stop scrolling
- show_notes: casual Ty voice — no bullet points, no formal language, sounds like a human wrote it
- faq: include the key fraud terms taught in the episode (e.g. "What is social engineering?", "What is smishing?"). Answers should be the kind of plain-English explanation Ty gives in the episode
- tags: mix of specific scam types, platforms, audience keywords, and broad discovery terms (always include "scam", "fraud", "true crime", "podcast")

SCRIPT:
{script}
"""


# ---- TTS HUMANIZE PROMPT --------------------------------------------------
TTS_HUMANIZE_PROMPT = """\
You are a TTS voice director. Your job is to make a podcast script breathe like a real human recording — not by changing words, but by adding punctuation and micro-imperfections that tell the voice engine exactly how to perform each line.

WHAT YOU ARE DOING:
You are adding spoken-language cues so that a voice synthesis engine sounds like a real person telling a story, not a machine reading text. The listener should not be able to tell this was synthesized.

RULES — READ THESE CAREFULLY:
1. DO NOT change, add, or remove actual words in the narrative. The story content is locked.
2. You MAY add: em dashes (—), ellipses (...), commas for breath marks, and the filler sounds "uh" or "um" where they'd sound 100% natural. Nothing else.
3. You MAY create sentence restarts by splitting a word with an em dash: "He — he just stood there."
4. DO NOT touch any audio production markers — leave [INTRO_STING], [PAUSE], [TRANSITION_1], [TRANSITION_2], [OUTRO_STING] completely unchanged.
5. DO NOT over-do it. One or two imperfections per paragraph. This should feel natural, not like a parody of a nervous speaker.

WHAT EACH CUE DOES IN THE TTS ENGINE:
- Em dash mid-sentence (—) → creates a hard, dramatic pause. Use for weight, not just rhythm.
  Example: "She handed over everything — her savings, her retirement, all of it."
- Ellipsis (...) → creates a slow trail-off or hesitation. Use when something hangs in the air.
  Example: "He just... didn't say anything."
- Sentence restart with em dash → mimics a real speaker catching themselves or emphasizing.
  Example: "And he — he actually believed it." (conveys disbelief or shock)
- "uh" or "um" before a beat → sounds like a real person searching for the right word.
  Example: "She called the bank — or, uh, who she thought was the bank."
- Extra comma → breath mark. Add to any sentence over 15 words with no natural pause point.
  Example: "He transferred the money, waited for a confirmation, and then the line went dead."

SPECIFIC MOMENTS TO TARGET:
- Any sentence describing a shocking number, loss, or outcome → add an em dash before the key detail
- Any place Ty reacts mid-story → add an ellipsis or restart
- Any sentence over 20 words with no internal punctuation → add at least one breath comma
- Any place where a word or phrase is repeated for emphasis → the repeat should have a restart before it
- The moment just before a major reveal → trail off with an ellipsis right before it
- Any rhetorical question ending in "?" → rewrite as a statement. TTS does not rising-inflect questions naturally so they land flat. "You know that sound?" → "You know that sound." or "That sound — you know the one."
- SHORT CONSECUTIVE SENTENCES: If two or more sentences in a row are each under 8 words and separated only by periods, the TTS fires them out as a rapid staccato burst with no breathing room between them. Connect them with an em dash instead of separate periods: "I'm Ty. Welcome to VAULTED. Here we go." → "I'm Ty — this is VAULTED — here we go." This is especially important in the intro and outro where short declarative sentences cluster together.
- TRAILING SINGLE WORDS: Any sentence or paragraph that ends with a dangling single word like "So." or "Right." or "Okay." should be rewritten or absorbed into the preceding sentence. These land dead in audio.

Return ONLY the modified script. No notes, no commentary, no list of changes.

SCRIPT:
{script}
"""


# ---------------------------------------------------------------------------
# Weekly Term Tracker
# Scans scripts from this week (Mon–today) to find which fraud/security terms
# have already been defined. Injects this into the script prompt so the LLM
# doesn't re-explain the same term from scratch in every episode.
# ---------------------------------------------------------------------------

TRACKABLE_TERMS = [
    "social engineering",
    "phishing",
    "smishing",
    "vishing",
    "spoofing",
    "pretexting",
    "account takeover",
    "SIM swapping",
    "SIM swap",
    "pig butchering",
    "romance scam",
    "crypto ATM",
    "deepfake",
    "credential stuffing",
    "ransomware",
    "money mule",
    "impersonation",
    "identity theft",
    "two-factor authentication",
    "dark web",
]


def get_terms_explained_this_week(config: dict, current_date_str: str) -> list[str]:
    """
    Return list of terms already defined/explained in any episode script
    from this calendar week (Mon through the day before today).
    """
    scripts_dir = SCRIPT_DIR / config["output"]["scripts_dir"]
    if not scripts_dir.exists():
        return []

    try:
        year = datetime.now().year
        current_dt = datetime.strptime(f"{year}-{current_date_str}", "%Y-%m-%d")
    except Exception:
        current_dt = datetime.now()

    # Find Monday of the current week
    week_start = current_dt - timedelta(days=current_dt.weekday())

    explained = set()
    for script_file in scripts_dir.glob("vaulted_*.txt"):
        # Parse date from filename: vaulted_MM-DD.txt
        m = re.search(r"vaulted_(\d{2}-\d{2})\.txt", script_file.name)
        if not m:
            continue
        try:
            file_dt = datetime.strptime(f"{year}-{m.group(1)}", "%Y-%m-%d")
        except Exception:
            continue
        # Only look at scripts from this week that are BEFORE today
        if week_start <= file_dt < current_dt:
            try:
                text = script_file.read_text().lower()
                for term in TRACKABLE_TERMS:
                    if term.lower() in text:
                        explained.add(term)
            except Exception:
                continue

    return sorted(explained)


def classify_story_type(article: dict) -> str:
    """Infer the type of scam for the prompt."""
    text = f"{article['title']} {article.get('summary', '')}".lower()
    types = {
        "romance scam": ["romance", "dating", "relationship", "love"],
        "investment/crypto fraud": ["crypto", "bitcoin", "investment", "trading", "ponzi"],
        "IRS/government impersonation": ["irs", "tax", "social security", "government", "federal"],
        "phishing attack": ["phishing", "email", "credential", "login", "password"],
        "phone/vishing scam": ["phone", "call", "vishing", "voice", "robocall"],
        "fake job/employment scam": ["job", "hiring", "employment", "work from home", "career"],
        "tech support scam": ["tech support", "microsoft", "apple", "computer", "virus"],
        "online shopping fraud": ["shopping", "store", "order", "package", "delivery"],
        "ransomware": ["ransomware", "encrypted", "ransom", "hospital", "infrastructure"],
        "AI/deepfake scam": ["deepfake", "ai", "voice clone", "artificial intelligence", "fake video"],
        "smishing": ["text message", "sms", "smishing", "usps", "package notification"],
        "elder fraud": ["elderly", "senior", "grandparent", "retiree", "nursing"],
        "data breach": ["data breach", "leaked", "exposed", "database", "records stolen"],
    }
    for scam_type, keywords in types.items():
        if any(kw in text for kw in keywords):
            return scam_type
    return "fraud/scam"


def parse_generated_stories(raw: str) -> list[dict]:
    """Parse the AI-generated fallback stories into article dicts."""
    stories = []
    blocks = re.split(r"STORY \d+:", raw)
    for block in blocks[1:]:  # skip text before first story
        title_m = re.search(r"Title:\s*(.+)", block)
        type_m = re.search(r"Type:\s*(.+)", block)
        summary_m = re.search(r"Summary:\s*([\s\S]+?)(?=Why now:|$)", block)
        if title_m and summary_m:
            stories.append({
                "title": title_m.group(1).strip(),
                "link": "",
                "summary": summary_m.group(1).strip(),
                "published": datetime.now().isoformat(),
                "source": "AI Generated",
                "source_priority": 3,
                "category": "generated",
                "from_search": False,
                "relevance": 50.0,
                "scam_type": type_m.group(1).strip() if type_m else "fraud/scam",
                "is_generated": True,
            })
    return stories


def is_qwen3(model_name: str) -> bool:
    """Check if the model is Qwen3 or Qwen3.5 (both use thinking mode by default)."""
    return "qwen3" in model_name.lower()


def strip_thinking_tags(text: str) -> str:
    """Remove Qwen3/3.5 <think>...</think> blocks from output."""
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    return text.strip()


def call_ollama(prompt: str, system: str, config: dict, temperature: float = 0.7,
                num_predict: int = 12000, model_override: Optional[str] = None) -> str:
    """
    Call Ollama using streaming mode.
    Streaming means we collect tokens as they arrive — no single long wait
    that can time out. Each chunk has its own short timeout (60s), so even
    a 15-minute generation completes without error.

    Args:
        num_predict: Max tokens to generate. Set lower for short tasks (e.g. 800 for
                     metadata) to avoid burning time on a 12k-token budget.
        model_override: Force a specific model (e.g. fallback for mechanical passes).
    """
    ollama_cfg = config["ollama"]
    base_url = ollama_cfg["base_url"]
    model = model_override or ollama_cfg["model"]

    # Qwen3/3.5: suppress thinking mode three ways for belt-and-suspenders reliability:
    #   1. /no_think prefix in the prompt text
    #   2. think: false at the TOP LEVEL of the payload (NOT inside options — Ollama ignores it there)
    #   3. System prompt already tells the model to skip reasoning
    actual_prompt = f"/no_think\n\n{prompt}" if is_qwen3(model) else prompt

    payload = {
        "model":  model,
        "prompt": actual_prompt,
        "system": system,
        "stream": True,
        "think":  False,          # TOP-LEVEL — this is what actually works in Ollama
        "options": {
            "temperature":    temperature,
            "num_predict":    num_predict,
            "top_p":          0.9,
            "repeat_penalty": 1.1,
        },
    }

    log.info(f"  Calling Ollama ({model}) [streaming]...")

    def stream_response(mdl: str, pmt: str) -> str:
        """Stream tokens from Ollama and return the full assembled response."""
        p = f"/no_think\n\n{pmt}" if is_qwen3(mdl) else pmt
        payload["model"] = mdl
        payload["prompt"] = p

        resp = requests.post(
            f"{base_url}/api/generate",
            json=payload,
            stream=True,
            timeout=60,  # timeout per chunk, not total — streaming never times out on long generations
        )
        resp.raise_for_status()

        full_text = []
        token_count = 0
        start_time = time.time()
        # Zero-output watchdog: if we've been streaming for this many seconds and
        # still have 0 visible words (model stuck in thinking mode), abort early.
        # The caller will retry with the fallback model.
        ZERO_OUTPUT_TIMEOUT = 180  # 3 minutes — generous but not 20+

        for line in resp.iter_lines():
            if not line:
                continue
            try:
                chunk = json.loads(line)
                token = chunk.get("response", "")
                full_text.append(token)
                token_count += 1
                # Show a progress dot every 200 tokens so the terminal doesn't look frozen
                if token_count % 200 == 0:
                    elapsed = time.time() - start_time
                    assembled = strip_thinking_tags("".join(full_text))
                    words_so_far = len(assembled.split())
                    log.info(f"    ... {words_so_far} words generated so far ({elapsed:.0f}s)")
                    # Watchdog: model stuck in thinking mode — all tokens inside <think> blocks.
                    # Bail out fast so the batch retry pass can re-run this episode cleanly.
                    if words_so_far == 0 and elapsed > ZERO_OUTPUT_TIMEOUT:
                        log.warning(
                            f"  ⚠ Zero-output watchdog: {elapsed:.0f}s elapsed, "
                            f"{token_count} tokens generated, 0 visible words. "
                            f"Aborting this attempt."
                        )
                        raise RuntimeError("zero_output_timeout")
                if chunk.get("done"):
                    break
            except json.JSONDecodeError:
                continue

        return strip_thinking_tags("".join(full_text).strip())

    try:
        return stream_response(model, prompt)
    except RuntimeError as e:
        if "zero_output_timeout" in str(e):
            log.error("  Ollama stuck in thinking mode — episode will be retried by the batch retry pass.")
        raise
    except requests.ConnectionError:
        log.error("Ollama not reachable. Run: ollama serve")
        raise


def get_fallback_stories(config: dict) -> list[dict]:
    """Ask the LLM to generate realistic fictional story seeds."""
    log.info("  Generating fictional story seeds as fallback...")
    system = "You are a podcast researcher who knows everything about current scam and fraud trends."
    raw = call_ollama(GENERATE_STORIES_PROMPT, system, config, temperature=0.8, num_predict=2000)
    stories = parse_generated_stories(raw)
    log.info(f"  Generated {len(stories)} fictional story seeds")
    return stories


def get_day_of_week(date_str: str) -> str:
    """Get the day of week name from a MM-DD date string."""
    try:
        year = datetime.now().year
        dt = datetime.strptime(f"{year}-{date_str}", "%Y-%m-%d")
        return dt.strftime("%A")  # "Monday", "Friday", etc.
    except Exception:
        return datetime.now().strftime("%A")


def generate_script(stories: list[dict], config: dict, date_str: Optional[str] = None) -> str:
    """Draft the full episode script from selected stories."""
    def story_fields(s: dict, prefix: str) -> dict:
        return {
            f"{prefix}_title": s["title"],
            f"{prefix}_source": s.get("source", "Web"),
            f"{prefix}_summary": s.get("summary", "")[:800],
            f"{prefix}_type": s.get("scam_type") or classify_story_type(s),
        }

    day = get_day_of_week(date_str) if date_str else datetime.now().strftime("%A")
    outro = OUTRO_BY_DAY.get(day, OUTRO_BY_DAY["Monday"])

    # Pick 3 unique names for this episode — tracked monthly to avoid repeats
    names = pick_episode_names(3)
    (name1, job1), (name2, job2), (name3, job3) = names
    log.info(f"  Episode names: {name1} ({job1}), {name2} ({job2}), {name3} ({job3})")

    # Check which terms were already explained earlier this week
    explained = get_terms_explained_this_week(config, date_str or datetime.now().strftime("%m-%d"))
    if explained:
        terms_context = (
            "TERMS ALREADY EXPLAINED THIS WEEK — do NOT re-define these from scratch. "
            "You may still use the words, but don't give the full definition again. "
            "A brief callback is fine (e.g. 'that's spoofing — we covered that earlier this week'). "
            f"Already defined: {', '.join(explained)}.\n\n"
        )
        log.info(f"  Terms already explained this week: {explained}")
    else:
        terms_context = ""

    prompt = STORY_DRAFT_PROMPT.format(
        day_of_week=day,
        outro_instruction=outro,
        name1=name1, job1=job1,
        name2=name2, job2=job2,
        name3=name3, job3=job3,
        terms_context=terms_context,
        **story_fields(stories[0], "s1"),
        **story_fields(stories[1], "s2"),
        **story_fields(stories[2], "s3"),
    )

    log.info(f"  Stage A: Drafting script (day: {day})...")
    return call_ollama(prompt, SYSTEM_PROMPT, config, temperature=0.72, num_predict=12000)


def humanize_script(script: str, config: dict) -> str:
    """Second pass: strip out AI patterns, add human voice."""
    log.info("  Stage B: Humanizing script...")
    system = (
        "You are a podcast script editor. Your only job is to make scripts sound "
        "like a real human talking, not like something written by AI. Be ruthless. "
        "Never cut content — only transform it. The output must be at least as long as the input."
    )
    prompt = HUMANIZE_PROMPT.format(script=script)
    result = call_ollama(prompt, system, config, temperature=0.75, num_predict=10000)

    # If the humanizer shrunk the script significantly, expand the short sections
    original_words = len(script.split())
    result_words = len(result.split())
    if result_words < original_words * 0.9:
        log.warning(f"  Humanizer shrunk script ({original_words}→{result_words} words). Running expansion pass...")
        expand_system = "You are a podcast script writer expanding a script that is too short."
        expand_prompt = f"""\
The following podcast script is too short — it needs to be expanded to reach 2,700-3,100 words total.
Current word count: {result_words} words. Target: at least 2,700 words.

Expand it by:
- Making each story section longer with more detail, more of Ty's reactions, and more victim perspective
- Adding more specific details and human moments that make the stories feel real
- Keeping everything in Ty's natural spoken voice — no lists, no formal language
- Do NOT add new sections or change the structure

Return ONLY the expanded script. No commentary.

SCRIPT:
{result}
"""
        expanded = call_ollama(expand_prompt, expand_system, config, temperature=0.7, num_predict=10000)
        if len(expanded.split()) > result_words:
            log.info(f"  Expansion: {result_words}→{len(expanded.split())} words")
            result = expanded

    return result


def tts_humanize_script(script: str, config: dict) -> str:
    """
    Stage B.5: TTS voice direction pass.

    Adds punctuation-based prosody cues (em dashes, ellipses, breath commas,
    filler sounds) that tell the voice synthesis engine where to pause, hesitate,
    and stumble — without changing any actual words. Audio markers are preserved.

    Gated by config["podcast"]["tts_humanize"] = true.
    """
    log.info("  Stage B.5: TTS voice direction pass...")

    system = (
        "You are a TTS voice director. You add punctuation cues to podcast scripts "
        "to make voice synthesis sound human. You never change words — only punctuation "
        "and filler sounds. You always return the full script with no commentary."
    )
    prompt = TTS_HUMANIZE_PROMPT.format(script=script)
    # Use fast fallback model — TTS direction is a mechanical find-and-replace pass,
    # doesn't need the full 27B model. Saves ~10-12 min per run.
    fast_model = config["ollama"].get("fallback_model") or config["ollama"]["model"]
    result = call_ollama(prompt, system, config, temperature=0.55,
                         num_predict=8000, model_override=fast_model)

    # Sanity checks — if the pass mangled markers or shrank the script badly, revert
    required_markers = ["[INTRO_STING]", "[PAUSE]", "[TRANSITION_1]", "[TRANSITION_2]", "[OUTRO_STING]"]
    missing = [m for m in required_markers if m not in result]
    if missing:
        log.warning(f"  TTS pass dropped markers {missing} — reverting to pre-pass script")
        return script

    original_words = len(script.split())
    result_words   = len(result.split())
    # Allow up to +8% growth (fillers add words) but not more than 15% shrinkage
    if result_words < original_words * 0.85:
        log.warning(
            f"  TTS pass shrank script too much ({original_words}→{result_words} words) — reverting"
        )
        return script

    log.info(f"  TTS pass: {original_words}→{result_words} words (+{result_words - original_words} filler/punct)")
    return result


def generate_metadata(script: str, stories: list[dict], date_str: str, config: dict) -> dict:
    """
    Stage C: Generate episode distribution metadata from the final humanized script.
    Returns a dict with: title, tagline, show_notes, faq, tags, chapters,
    estimated_duration_min, date, episode_sources.
    """
    log.info("  Stage C: Generating episode metadata...")

    # ── LLM pass: title, tagline, show notes, faq, tags ───────────────────
    system = (
        "You are a podcast metadata specialist. You write compelling, SEO-friendly "
        "episode titles, show notes, and FAQs that help podcasts get discovered and clicked. "
        "You always return valid JSON with no extra text."
    )
    prompt = METADATA_PROMPT.format(script=script)
    raw = call_ollama(prompt, system, config, temperature=0.65, num_predict=900)

    # Strip any markdown fences the LLM might have added despite instructions
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"\s*```\s*$", "", raw.strip(), flags=re.MULTILINE)

    metadata: dict = {}
    try:
        metadata = json.loads(raw)
        log.info("  Metadata JSON parsed successfully")
    except json.JSONDecodeError:
        # Try to extract just the JSON object if there's surrounding text
        m = re.search(r"\{[\s\S]+\}", raw)
        if m:
            try:
                metadata = json.loads(m.group(0))
                log.info("  Metadata JSON extracted from response")
            except Exception:
                log.warning("  Metadata JSON parse failed — using fallback data")
                metadata = {}
        else:
            log.warning("  No JSON object found in metadata response — using fallback data")

    # ── Chapter timestamp estimation ───────────────────────────────────────
    # We know the script structure from the markers. Estimate timestamps by
    # measuring word counts per section and dividing by WPM.
    wpm = config["podcast"].get("words_per_minute", 130)

    # Approximate durations of each music file (seconds)
    MUSIC_SECS = {
        "intro":        30,
        "transition_1":  6,
        "transition_2":  7,
        "outro":        30,
    }
    PAUSE_SECS = TTS_PAUSE_MS / 1000.0

    def words_in_segment(text: str, after: str, before: str) -> int:
        """Count words in the script between two marker strings."""
        start = text.find(after)
        end   = text.find(before)
        if start == -1:
            start = 0
        else:
            start += len(after)
        if end == -1:
            end = len(text)
        return len(text[start:end].split())

    def ts(secs: float) -> str:
        """Format seconds as M:SS or H:MM:SS."""
        s = max(0, int(secs))
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

    cold_words  = words_in_segment(script, "",              "[INTRO_STING]")
    intro_words = words_in_segment(script, "[INTRO_STING]", "[PAUSE]")
    s1_words    = words_in_segment(script, "[PAUSE]",       "[TRANSITION_1]")
    s2_words    = words_in_segment(script, "[TRANSITION_1]","[TRANSITION_2]")

    # Story 3 block (between TRANSITION_2 and OUTRO_STING) may contain a [PAUSE]
    # that separates Story 3 from How to Stay Safe
    s3_block_start = script.find("[TRANSITION_2]")
    outro_start    = script.find("[OUTRO_STING]")
    if s3_block_start == -1:
        s3_block = ""
    elif outro_start == -1:
        s3_block = script[s3_block_start:]
    else:
        s3_block = script[s3_block_start:outro_start]

    inner_pause = s3_block.find("[PAUSE]")
    if inner_pause != -1:
        s3_words   = len(s3_block[:inner_pause].split())
        safe_words = len(s3_block[inner_pause:].split())
    else:
        s3_words   = len(s3_block.split())
        safe_words = 325  # fallback estimate

    outro_words = len(script[outro_start:].split()) if outro_start != -1 else 50

    # Walk through the episode timeline building chapter markers
    chapters = []
    cursor = 0.0

    chapters.append({"title": "Cold Open", "time": ts(cursor)})
    cursor += cold_words / wpm * 60

    cursor += MUSIC_SECS["intro"]
    cursor += PAUSE_SECS  # pause after intro tease

    chapters.append({"title": "Welcome to VAULTED", "time": ts(cursor)})
    cursor += intro_words / wpm * 60

    chapters.append({"title": "Story 1", "time": ts(cursor)})
    cursor += s1_words / wpm * 60
    cursor += MUSIC_SECS["transition_1"]

    chapters.append({"title": "Story 2", "time": ts(cursor)})
    cursor += s2_words / wpm * 60
    cursor += MUSIC_SECS["transition_2"]

    chapters.append({"title": "Story 3", "time": ts(cursor)})
    cursor += s3_words / wpm * 60
    cursor += PAUSE_SECS

    chapters.append({"title": "How to Stay Safe", "time": ts(cursor)})
    cursor += safe_words / wpm * 60
    cursor += MUSIC_SECS["outro"]

    chapters.append({"title": "Outro", "time": ts(cursor)})
    cursor += outro_words / wpm * 60

    # ── Stitch it all together ─────────────────────────────────────────────
    metadata["chapters"]               = chapters
    metadata["estimated_duration_min"] = round(cursor / 60, 1)
    metadata["date"]                   = date_str
    metadata["episode_sources"]        = [
        {
            "title":  s["title"],
            "source": s.get("source", ""),
            "link":   s.get("link", ""),
        }
        for s in stories
    ]

    log.info(f"  Chapters: {len(chapters)} | Est. duration: {metadata['estimated_duration_min']} min")
    return metadata


def save_metadata(metadata: dict, date_str: str, config: dict) -> Path:
    """Save episode metadata JSON alongside the script files."""
    scripts_dir = SCRIPT_DIR / config["output"]["scripts_dir"]
    scripts_dir.mkdir(exist_ok=True)
    meta_path = scripts_dir / f"vaulted_metadata_{date_str}.json"
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    log.info(f"  Metadata saved: {meta_path}")
    return meta_path


def validate_script_markers(script: str) -> list[str]:
    """
    Check that all required audio production markers are present.
    Returns a list of missing marker names (empty = all good).
    """
    required = ["[INTRO_STING]", "[PAUSE]", "[TRANSITION_1]", "[TRANSITION_2]", "[OUTRO_STING]"]
    return [m for m in required if m not in script]


# ---------------------------------------------------------------------------
# Stage 3: Mimika Studio TTS
# ---------------------------------------------------------------------------
TTS_INSTRUCT = (
    "You are a gripping true crime podcast host. "
    "Speak naturally and conversationally — vary your pace and tone. "
    "Slow down and lower your voice for tense or shocking moments. "
    "Speed up slightly during fast-moving narrative beats. "
    "Sound like a real person telling a story to a friend, not reading a script."
)
# Qwen3-TTS silently truncates above ~1,200 chars — keep chunks well under that
TTS_CHUNK_MAX = 900
# Silence inserted at [PAUSE] points (milliseconds)
TTS_PAUSE_MS = 900
# Markers that trigger music file insertion — maps marker name → music config key
MUSIC_MARKERS = {
    "INTRO_STING":   "intro",
    "TRANSITION_1":  "transition_1",
    "TRANSITION_2":  "transition_2",
    "OUTRO_STING":   "outro",
}


def _num_to_words(n: int) -> str:
    """Convert an integer to English words."""
    if n < 0:
        return "negative " + _num_to_words(-n)
    ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight",
            "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
            "sixteen", "seventeen", "eighteen", "nineteen"]
    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
    if n == 0:
        return "zero"
    if n < 20:
        return ones[n]
    if n < 100:
        return tens[n // 10] + (" " + ones[n % 10] if n % 10 else "")
    if n < 1_000:
        return ones[n // 100] + " hundred" + (" " + _num_to_words(n % 100) if n % 100 else "")
    if n < 1_000_000:
        return _num_to_words(n // 1_000) + " thousand" + (" " + _num_to_words(n % 1_000) if n % 1_000 else "")
    if n < 1_000_000_000:
        return _num_to_words(n // 1_000_000) + " million" + (" " + _num_to_words(n % 1_000_000) if n % 1_000_000 else "")
    return _num_to_words(n // 1_000_000_000) + " billion" + (" " + _num_to_words(n % 1_000_000_000) if n % 1_000_000_000 else "")


def preprocess_for_tts(text: str) -> str:
    """Convert numbers, currency, acronyms, and symbols to spoken words for TTS."""

    # ── Acronym expansion ──────────────────────────────────────────────────────
    # Hyphenating acronyms (URL → U-R-L) makes TTS read each letter individually.
    # Longer phrases replace the acronym entirely with a natural spoken equivalent.
    # Applied BEFORE the all-caps handler so these exact replacements win.
    ACRONYM_MAP = {
        # Tech / web
        "URL":   "U-R-L",
        "URLs":  "U-R-Ls",
        "SMS":   "S-M-S",
        "MFA":   "M-F-A",
        "2FA":   "two-factor authentication",
        "OTP":   "O-T-P",
        "VPN":   "V-P-N",
        "IP":    "I-P",
        "QR":    "Q-R",
        "PDF":   "P-D-F",
        "Wi-Fi": "WiFi",
        # Financial / identity
        "PIN":   "P-I-N",
        "ATM":   "A-T-M",
        "SSN":   "S-S-N",
        "DOB":   "date of birth",
        "CVV":   "C-V-V",
        # Agencies / orgs
        "FBI":   "F-B-I",
        "IRS":   "I-R-S",
        "FTC":   "F-T-C",
        "CFPB":  "C-F-P-B",
        "CISA":  "C-I-S-A",
        "IC3":   "I-C-3",
        # Business titles
        "CEO":   "C-E-O",
        "CFO":   "C-F-O",
        "IT":    "I-T",
        # Misc
        "AI":    "A-I",
        "ID":    "I-D",
    }
    for acronym, spoken in ACRONYM_MAP.items():
        # Word-boundary match, case-sensitive for true acronyms
        text = re.sub(rf'\b{re.escape(acronym)}\b', spoken, text)

    # Any remaining all-caps word (3+ letters) not already handled above is an acronym
    # that the TTS would mispronounce. Spell it out letter-by-letter: "USPS" → "U-S-P-S".
    # Exempt "VAULTED" (show name — reads naturally as a word) and anything inside
    # [BRACKETS] (production markers that are never read aloud).
    CAPS_EXEMPT = {"VAULTED"}
    def _spell_caps(m):
        word = m.group(0)
        if word in CAPS_EXEMPT:
            return word.capitalize()  # VAULTED → Vaulted
        return "-".join(list(word))   # USPS → U-S-P-S
    text = re.sub(r'(?<!\[)\b[A-Z]{3,}\b(?!\])', _spell_caps, text)

    def digit_word(d: str) -> str:
        words = ["zero","one","two","three","four","five","six","seven","eight","nine"]
        return words[int(d)] if d.isdigit() else d

    # $X.Y million/billion/trillion/thousand → "X point Y million dollars"
    def currency_scale(m):
        amount, scale = m.group(1), m.group(2).lower()
        amount = amount.replace(",", "")
        if "." in amount:
            integer, decimal = amount.split(".", 1)
            words = _num_to_words(int(integer)) + " point " + " ".join(digit_word(d) for d in decimal)
        else:
            words = _num_to_words(int(amount))
        return words + " " + scale + " dollars"

    text = re.sub(
        r"\$([0-9,]+(?:\.[0-9]+)?)\s*(million|billion|trillion|thousand)",
        currency_scale, text, flags=re.IGNORECASE
    )

    # Plain $X,XXX or $XX → "fifty thousand dollars"
    def plain_currency(m):
        n = int(m.group(1).replace(",", ""))
        return _num_to_words(n) + " dollars"

    text = re.sub(r"\$([0-9][0-9,]*)", plain_currency, text)

    # X% → "X percent"
    def pct(m):
        return _num_to_words(int(m.group(1))) + " percent"

    text = re.sub(r"\b(\d+)%", pct, text)

    # Standalone large numbers with commas: 1,234,567
    def big_number(m):
        return _num_to_words(int(m.group(0).replace(",", "")))

    text = re.sub(r"\b\d{1,3}(?:,\d{3})+\b", big_number, text)

    # Remaining plain integers (skip years 1900–2099 — read as-is)
    def plain_int(m):
        n = int(m.group(0))
        if 1900 <= n <= 2099:
            return m.group(0)
        return _num_to_words(n)

    text = re.sub(r"\b\d+\b", plain_int, text)

    return text


def check_mimika(config: dict) -> bool:
    base_url = config["mimika"]["base_url"]
    endpoints = ["/api/health", "/api/qwen3/info", "/api/system/info", "/docs"]
    for endpoint in endpoints:
        try:
            resp = requests.get(f"{base_url}{endpoint}", timeout=10)
            if resp.status_code < 500:
                return True
        except Exception:
            continue
    return False


def _split_to_voice_chunks(text: str) -> list[dict]:
    """
    Split a block of voice text into TTS-safe chunks (≤TTS_CHUNK_MAX chars).
    Paragraph breaks are hard split points. Returns list of voice items.
    """
    chunks = []
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    for para in paragraphs:
        sentences = re.split(r"(?<=[.!?])\s+", para)
        current = ""
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            candidate = (current + " " + sentence).strip() if current else sentence
            if len(candidate) <= TTS_CHUNK_MAX:
                current = candidate
            else:
                if current:
                    chunks.append({"type": "voice", "text": current, "pause_after": False})
                while len(sentence) > TTS_CHUNK_MAX:
                    chunks.append({"type": "voice", "text": sentence[:TTS_CHUNK_MAX], "pause_after": False})
                    sentence = sentence[TTS_CHUNK_MAX:].strip()
                current = sentence
        if current:
            chunks.append({"type": "voice", "text": current, "pause_after": False})
    return chunks


def chunk_script(script_text: str) -> list[dict]:
    """
    Split script into a flat list of voice chunks and music insertion points.

    Returns items of two types:
      {"type": "voice", "text": str, "pause_after": bool}
      {"type": "music", "key": str}   — key matches a music config entry

    Markers recognised:
      [PAUSE]        → sets pause_after=True on the preceding voice chunk
      [INTRO_STING]  → inserts music item (intro sting)
      [TRANSITION_1] → inserts music item (story 1→2 transition)
      [TRANSITION_2] → inserts music item (story 2→3 transition)
      [OUTRO_STING]  → inserts music item (outro sting)
    All other [BRACKET] content is stripped (never read aloud).
    """
    # Numbers/currency → spoken words before any splitting
    script_text = preprocess_for_tts(script_text)

    # Replace recognised markers with unique newline-wrapped sentinels
    all_marker_names = "|".join(["PAUSE"] + list(MUSIC_MARKERS.keys()))
    script_text = re.sub(
        rf'\s*\[({all_marker_names})\]\s*',
        lambda m: f"\n|||{m.group(1)}|||\n",
        script_text,
    )
    # Strip any remaining [BRACKET] content (stage directions, etc.)
    script_text = re.sub(r"\[.*?\]", "", script_text)

    # Split: re.split with a capturing group gives alternating [text, marker, text, ...]
    parts = re.split(r"\n\|\|\|([A-Z_0-9]+)\|\|\|\n", script_text)

    result: list[dict] = []

    for i, part in enumerate(parts):
        if i % 2 == 0:
            # Text segment — split into voice chunks
            if part.strip():
                result.extend(_split_to_voice_chunks(part))
        else:
            # Marker
            marker = part.strip()
            if marker == "PAUSE":
                # Mark the last voice chunk in result as pause_after
                for j in range(len(result) - 1, -1, -1):
                    if result[j]["type"] == "voice":
                        result[j]["pause_after"] = True
                        break
            elif marker in MUSIC_MARKERS:
                result.append({"type": "music", "key": MUSIC_MARKERS[marker]})

    return [item for item in result if item["type"] == "music" or item.get("text", "").strip()]


def _mimika_url(base_url: str, url_or_path: str) -> str:
    if url_or_path.startswith("http"):
        return url_or_path
    return f"{base_url}{url_or_path}" if url_or_path.startswith("/") else f"{base_url}/{url_or_path}"


def generate_chunk(text: str, chunk_idx: int, config: dict) -> Optional[Path]:
    """Send one text chunk to Mimika, return local WAV path."""
    mimika_cfg = config["mimika"]
    base_url = mimika_cfg["base_url"]
    engine = mimika_cfg.get("engine", "qwen3")
    chunk_timeout = 600  # 10 min per chunk is plenty

    payload = {
        "text": text,
        "mode": mimika_cfg.get("mode", "clone"),
        "voice_name": mimika_cfg.get("voice_name", "VoiceRecordingForCloning"),
        "language": mimika_cfg.get("language", "Auto"),
        "speed": mimika_cfg.get("speed", 1.0),
        "model_size": mimika_cfg.get("model_size", "1.7B"),
        "instruct": TTS_INSTRUCT,
        # Advanced prosody parameters (mirror Mimika Studio sliders)
        # Sliders step in 0.1 increments — use clean 0.1-step values only.
        # temperature: 0.7 keeps the voice consistent throughout a 20-min episode
        #   without going flat. UI default is 0.9 which adds too much variation.
        # top_p: 0.8 tightens token selection slightly; pairs well with temp 0.7
        # top_k: 50 matches UI default — no reason to change
        # repetition_penalty: 1.1 prevents prosody ruts (same rhythm every sentence)
        #   UI default is 1 (off). 1.1 is a gentle nudge, not a heavy hand.
        # seed: -1 = random per chunk (natural variation across the episode)
        "temperature": mimika_cfg.get("temperature", 0.7),
        "top_p":       mimika_cfg.get("top_p", 0.8),
        "top_k":       mimika_cfg.get("top_k", 50),
        "repetition_penalty": mimika_cfg.get("repetition_penalty", 1.1),
        "seed":        mimika_cfg.get("seed", -1),
    }

    try:
        resp = requests.post(
            f"{base_url}/api/{engine}/generate",
            json=payload,
            timeout=chunk_timeout,
        )
        resp.raise_for_status()

        # Direct audio bytes
        ct = resp.headers.get("content-type", "")
        if "audio" in ct or "octet-stream" in ct:
            tmp = Path(tempfile.mktemp(suffix=f"_chunk{chunk_idx:03d}.wav"))
            tmp.write_bytes(resp.content)
            return tmp

        result = resp.json()
        audio_url = (
            result.get("audio_url") or result.get("output_url")
            or result.get("url")
        )
        if not audio_url:
            log.error(f"  Chunk {chunk_idx}: unexpected response: {result}")
            return None

        full_url = _mimika_url(base_url, audio_url)
        tmp = Path(tempfile.mktemp(suffix=f"_chunk{chunk_idx:03d}.wav"))
        dl = requests.get(full_url, stream=True, timeout=120)
        dl.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in dl.iter_content(chunk_size=8192):
                f.write(chunk)
        return tmp

    except Exception as e:
        log.error(f"  Chunk {chunk_idx} failed: {e}")
        return None


def apply_voice_eq(wav_path: Path) -> Path:
    """
    Apply voice processing to a single TTS WAV chunk, in-place:
      1. Resample to 44100 Hz mono — ensures concat compatibility with music files
      2. EQ: +2dB at 1kHz (nasal presence / cuts through music)
      3. Loudnorm: -19 LUFS / -2 TP — consistent level chunk-to-chunk
    Music files are NEVER run through this — voice only.
    Non-fatal: if ffmpeg fails the original is kept untouched.
    """
    import subprocess

    # Diagnostic: log the actual sample rate Mimika returned so we can confirm
    # resampling is working. Runs ffprobe on the raw file before any processing.
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate,codec_name",
             "-of", "default=noprint_wrappers=1",
             str(wav_path)],
            capture_output=True, text=True,
        )
        if probe.stdout.strip():
            log.info(f"  [voice EQ] raw chunk sample rate: {probe.stdout.strip().replace(chr(10), ' ')}")
    except Exception:
        pass  # ffprobe not available — non-fatal

    # Output stereo (ac=2) so voice chunks match music files in the concat.
    # The concat demuxer requires identical channel count across all files —
    # mixing mono voice with stereo music causes sample misreads and pitch shift.
    # -16 LUFS matches the music files (also mastered at -16 LUFS) so levels feel even
    voice_filter = "aresample=44100,equalizer=f=1000:width_type=o:width=1:g=2,loudnorm=I=-16:TP=-1.5:LRA=11"
    tmp = wav_path.with_suffix(".proc.wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav_path),
             "-af", voice_filter, "-ac", "2", str(tmp)],
            check=True, capture_output=True,
        )
        wav_path.unlink()
        tmp.rename(wav_path)
        log.info(f"  [voice EQ] resampled → 44100 Hz stereo, loudnorm -16 LUFS ✓")
    except subprocess.CalledProcessError as e:
        log.warning(f"  [voice EQ] ffmpeg failed — using raw chunk: {e.stderr.decode()[:200]}")
        tmp.unlink(missing_ok=True)
    return wav_path


def stitch_audio(
    stitch_items: list[dict],   # {"type": "voice", "path": Path, "pause_after": bool}
                                # {"type": "music", "key": str}
    output_path: Path,
    config: dict,
    pause_ms: int = TTS_PAUSE_MS,
) -> bool:
    """
    Concatenate voice WAVs and music files with ffmpeg, applying a short
    acrossfade at every music↔voice boundary for a seamless, premium sound.

    Phase 1 — Group items into sections:
        Consecutive voice chunks become a single "voice section".
        Music items are individual sections.
    Phase 2 — Render each voice section to a temp WAV using filter_complex concat.
    Phase 3 — Chain acrossfade across all section files:
        Music fades out (exp curve) while voice fades in (tri curve), overlapping
        by crossfade_seconds. Gives the classic "music ducks under voice" effect.
    """
    import subprocess

    music_cfg = config.get("music", {})
    music_dir = SCRIPT_DIR / music_cfg.get("dir", "Music")
    silence_secs = pause_ms / 1000.0
    crossfade_secs = float(music_cfg.get("crossfade_seconds", 1.0))
    tmp_dir = Path(tempfile.mkdtemp())

    try:
        # ── Phase 1: Group stitch_items into sections ────────────────────────
        # Walk the list, collecting consecutive voice chunks into a buffer.
        # Each flush of the buffer becomes one voice section.
        sections: list[dict] = []
        i = 0
        while i < len(stitch_items):
            item = stitch_items[i]
            if item["type"] == "music":
                filename = music_cfg.get(item["key"])
                if not filename:
                    log.warning(f"  No music file for '{item['key']}' — skipping")
                    i += 1
                    continue
                src = music_dir / filename
                if not src.exists():
                    log.warning(f"  Music file not found: {src} — skipping")
                    i += 1
                    continue
                sections.append({"type": "music", "path": src, "key": item["key"]})
                log.info(f"  Music: [{item['key']}] {filename}")
                i += 1
            elif item["type"] == "voice":
                voice_items = []
                while i < len(stitch_items) and stitch_items[i]["type"] == "voice":
                    voice_items.append(stitch_items[i])
                    i += 1
                sections.append({"type": "voice", "items": voice_items})
            else:
                i += 1

        if not sections:
            log.error("  No audio sections to stitch")
            return False

        # ── Phase 2: Render each voice section to a temp WAV ────────────────
        section_paths: list[Path] = []
        for sec_idx, section in enumerate(sections):
            if section["type"] == "music":
                section_paths.append(section["path"])
                continue

            # Build filter_complex concat for this voice section's chunks
            v_inputs: list[str] = []
            v_labels: list[str] = []
            vidx = 0
            for item in section["items"]:
                v_inputs += ["-i", str(item["path"])]
                v_labels.append(f"[{vidx}:a]")
                vidx += 1
                if item.get("pause_after"):
                    v_inputs += [
                        "-f", "lavfi",
                        "-t", f"{silence_secs:.3f}",
                        "-i", "anullsrc=r=44100:cl=stereo",
                    ]
                    v_labels.append(f"[{vidx}:a]")
                    vidx += 1

            tmp_voice = tmp_dir / f"section_{sec_idx}_voice.wav"
            n_v = len(v_labels)

            if n_v == 1:
                # Single chunk, no trailing silence — copy directly (fastest path)
                shutil.copy2(section["items"][0]["path"], tmp_voice)
            else:
                fc_v = "".join(v_labels) + f"concat=n={n_v}:v=0:a=1[out]"
                cmd_v = (
                    ["ffmpeg", "-y"]
                    + v_inputs
                    + ["-filter_complex", fc_v, "-map", "[out]", str(tmp_voice)]
                )
                subprocess.run(cmd_v, check=True, capture_output=True)

            section_paths.append(tmp_voice)

        # ── Phase 2.5: Process music sections ───────────────────────────────
        # - Intro music: apply a slow fade-in (3 sec) so it doesn't startle.
        #   The music files already have fade-out baked in but not always fade-in.
        # - Transition music (TRANSITION_1, TRANSITION_2): prepend 1.5 sec of
        #   silence so there's a breath between the story and the music.
        processed_section_paths: list[Path] = []
        for sec_idx, section in enumerate(sections):
            src_path = section_paths[sec_idx]
            if section["type"] == "music":
                key = section.get("key", "")
                if key == "intro":
                    # Soft fade-in: volume ramps from 0 to full over 3 seconds
                    faded = tmp_dir / f"section_{sec_idx}_intro_faded.wav"
                    try:
                        subprocess.run(
                            ["ffmpeg", "-y", "-i", str(src_path),
                             "-af", "afade=t=in:st=0:d=3",
                             str(faded)],
                            check=True, capture_output=True,
                        )
                        processed_section_paths.append(faded)
                        log.info(f"  Intro fade-in applied (3s ramp)")
                    except subprocess.CalledProcessError:
                        processed_section_paths.append(src_path)
                elif key in ("transition_1", "transition_2"):
                    # Insert 1.5 sec silence before transition music
                    silence_wav = tmp_dir / f"section_{sec_idx}_pre_silence.wav"
                    subprocess.run(
                        ["ffmpeg", "-y",
                         "-f", "lavfi", "-t", "1.5", "-i", "anullsrc=r=44100:cl=stereo",
                         str(silence_wav)],
                        check=True, capture_output=True,
                    )
                    combined = tmp_dir / f"section_{sec_idx}_transition_padded.wav"
                    subprocess.run(
                        ["ffmpeg", "-y",
                         "-i", str(silence_wav), "-i", str(src_path),
                         "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[out]",
                         "-map", "[out]", str(combined)],
                        check=True, capture_output=True,
                    )
                    processed_section_paths.append(combined)
                    log.info(f"  1.5s pre-silence added before {key}")
                else:
                    processed_section_paths.append(src_path)
            else:
                processed_section_paths.append(src_path)

        # ── Phase 3: Sequential concat across all sections ───────────────────
        # Music files have fade-in/fade-out baked in, so transitions already
        # sound smooth: narrator finishes → music rises in from silence.
        # No overlap needed — overlap (acrossfade) cuts off the narrator.
        n = len(processed_section_paths)
        if n == 0:
            log.error("  No section paths resolved")
            return False

        cf_inputs: list[str] = []
        cf_labels: list[str] = []
        for k, p in enumerate(processed_section_paths):
            cf_inputs += ["-i", str(p)]
            cf_labels.append(f"[{k}:a]")

        filter_complex = "".join(cf_labels) + f"concat=n={n}:v=0:a=1[out]"
        log.info(f"  Stitching {n} sections (sequential)...")

        cmd = (
            ["ffmpeg", "-y"]
            + cf_inputs
            + [
                "-filter_complex", filter_complex,
                "-map", "[out]",
                "-acodec", "libmp3lame", "-q:a", "2",
                str(output_path),
            ]
        )
        subprocess.run(cmd, check=True, capture_output=True)

        size_mb = output_path.stat().st_size / (1024 * 1024)
        log.info(f"  Final audio: {output_path} ({size_mb:.1f} MB)")
        return True

    except subprocess.CalledProcessError as e:
        log.error(f"  ffmpeg stitch failed: {e.stderr.decode()[:500]}")
        return False
    finally:
        for item in stitch_items:
            if item["type"] == "voice":
                item["path"].unlink(missing_ok=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)


def generate_audio(script_path: Path, date_str: str, config: dict) -> Optional[Path]:
    mimika_cfg = config["mimika"]
    audio_dir = SCRIPT_DIR / config["output"].get("audio_dir", "Audio")
    # New episodes land in pending/ for review before publishing
    pending_dir = audio_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    audio_path = pending_dir / f"vaulted_{date_str}.mp3"

    if not check_mimika(config):
        log.error(
            f"Mimika Studio not reachable at {mimika_cfg['base_url']}.\n"
            "  Start it:  open /Applications/MimikaStudio.app\n"
            "  Check API: http://localhost:7693/docs"
        )
        return None

    log.info("  Mimika Studio is running")
    with open(script_path) as f:
        script_text = f.read()

    items = chunk_script(script_text)
    voice_count = sum(1 for it in items if it["type"] == "voice")
    music_count = sum(1 for it in items if it["type"] == "music")
    log.info(f"  Script split into {voice_count} voice chunks + {music_count} music segments")

    stitch_items: list[dict] = []
    voice_idx = 0

    for item in items:
        if item["type"] == "music":
            stitch_items.append({"type": "music", "key": item["key"]})
            continue

        # Voice chunk — generate TTS with retry
        log.info(f"  Voice chunk {voice_idx + 1}/{voice_count} ({len(item['text'])} chars)...")
        wav = None
        for attempt in range(1, 4):
            wav = generate_chunk(item["text"], voice_idx, config)
            if wav is not None:
                break
            if attempt < 3:
                wait = attempt * 15
                log.warning(f"  Chunk {voice_idx + 1} attempt {attempt} failed — retrying in {wait}s...")
                time.sleep(wait)

        if wav is None:
            log.error(f"  Chunk {voice_idx + 1} failed after 3 attempts — aborting TTS")
            for si in stitch_items:
                if si["type"] == "voice":
                    si["path"].unlink(missing_ok=True)
            return None

        # Apply voice EQ per-chunk so music files aren't affected
        apply_voice_eq(wav)
        stitch_items.append({"type": "voice", "path": wav, "pause_after": item["pause_after"]})
        voice_idx += 1

    log.info(f"  Stitching {len(stitch_items)} segments (voice + music)...")
    success = stitch_audio(stitch_items, audio_path, config)
    return audio_path if success else None


# ---------------------------------------------------------------------------
# Stage 4: Save Outputs
# ---------------------------------------------------------------------------
def save_script(script: str, date_str: str, config: dict) -> Path:
    output_dir = SCRIPT_DIR / config["output"]["scripts_dir"]
    output_dir.mkdir(exist_ok=True)
    filename = config["output"]["filename_pattern"].format(date=date_str)
    output_path = output_dir / filename
    with open(output_path, "w") as f:
        f.write(script)
    log.info(f"  Script saved: {output_path}")
    return output_path


EPISODE_LOG_PATH = SCRIPT_DIR / "vaulted_episode_log.json"
# How many days before the same scam type can be the primary type again
SCAM_TYPE_COOLDOWN_DAYS = 60


def load_episode_log() -> list[dict]:
    if EPISODE_LOG_PATH.exists():
        with open(EPISODE_LOG_PATH) as f:
            return json.load(f)
    return []


def save_episode_log(entries: list[dict]):
    with open(EPISODE_LOG_PATH, "w") as f:
        json.dump(entries, f, indent=2)


def get_next_episode_number(entries: list[dict]) -> int:
    if not entries:
        return 1
    return max(e.get("episode_number", 0) for e in entries) + 1


def log_episode(date_str: str, stories: list[dict], audio_path: Optional[Path]):
    """Append this episode to the episode log."""
    entries = load_episode_log()
    scam_types = [classify_story_type(s) for s in stories]
    entries.append({
        "episode_number": get_next_episode_number(entries),
        "date": date_str,
        "timestamp": datetime.now().isoformat(),
        "scam_types": scam_types,
        "story_titles": [s["title"] for s in stories],
        "audio_path": str(audio_path) if audio_path else None,
    })
    save_episode_log(entries)
    log.info(f"  Episode log updated — episode #{entries[-1]['episode_number']}, types: {scam_types}")
    return entries[-1]["episode_number"]


def get_recent_scam_types(days: int = SCAM_TYPE_COOLDOWN_DAYS) -> dict[str, int]:
    """Return {scam_type: days_since_last_use} for types used within the cooldown window."""
    entries = load_episode_log()
    cutoff = datetime.now().timestamp() - (days * 86400)
    recent: dict[str, int] = {}
    for entry in entries:
        try:
            ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
        except Exception:
            continue
        if ts < cutoff:
            continue
        days_ago = int((datetime.now().timestamp() - ts) / 86400)
        for t in entry.get("scam_types", []):
            if t not in recent or recent[t] > days_ago:
                recent[t] = days_ago
    return recent


def write_id3_tags(audio_path: Path, episode_number: int, date_str: str, stories: list[dict]):
    """Write podcast-standard ID3 tags to the MP3, including cover artwork."""
    try:
        from mutagen.id3 import ID3, TIT2, TPE1, TALB, TCON, TRCK, COMM, TDRC, APIC, ID3NoHeaderError
        try:
            tags = ID3(str(audio_path))
        except ID3NoHeaderError:
            tags = ID3()

        story_titles = " | ".join(s["title"] for s in stories)
        scam_types = ", ".join(set(classify_story_type(s) for s in stories))
        month, day = date_str.split("-")
        year = datetime.now().year

        tags["TIT2"] = TIT2(encoding=3, text=f"VAULTED — {month}/{day}/{year} (Ep. {episode_number})")
        tags["TPE1"] = TPE1(encoding=3, text="Ty")
        tags["TALB"] = TALB(encoding=3, text="VAULTED")
        tags["TCON"] = TCON(encoding=3, text="Podcast")
        tags["TRCK"] = TRCK(encoding=3, text=str(episode_number))
        tags["TDRC"] = TDRC(encoding=3, text=str(year))
        tags["COMM"] = COMM(
            encoding=3, lang="eng", desc="desc",
            text=f"Today on VAULTED: {story_titles}. Topics: {scam_types}."
        )

        # Embed cover artwork
        artwork_path = SCRIPT_DIR / "VAULTED_Podcast_Artwork.jpeg"
        if artwork_path.exists():
            try:
                with open(artwork_path, "rb") as img_f:
                    artwork_data = img_f.read()
                tags["APIC"] = APIC(
                    encoding=3,
                    mime="image/jpeg",
                    type=3,   # 3 = Cover (front)
                    desc="Cover",
                    data=artwork_data,
                )
                log.info("  Artwork embedded in MP3")
            except Exception as art_err:
                log.warning(f"  Artwork embed failed (non-fatal): {art_err}")

        tags.save(str(audio_path), v2_version=3)
        log.info(f"  ID3 tags written: Ep. {episode_number} — {story_titles[:60]}...")
    except Exception as e:
        log.warning(f"  ID3 tagging failed (non-fatal): {e}")


def save_research_log(stories: list[dict], all_articles: list[dict], date_str: str):
    LOG_PATH.mkdir(exist_ok=True)
    log_file = LOG_PATH / f"research_{date_str}.json"
    data = {
        "date": date_str,
        "timestamp": datetime.now().isoformat(),
        "total_found": len(all_articles),
        "selected": [
            {
                "title": s["title"],
                "source": s["source"],
                "link": s["link"],
                "score": s["relevance"],
                "published": s.get("published"),
                "generated": s.get("is_generated", False),
            }
            for s in stories
        ],
        "top_candidates": [
            {"title": a["title"], "source": a["source"], "score": a["relevance"]}
            for a in sorted(all_articles, key=lambda x: x["relevance"], reverse=True)[:20]
        ],
    }
    with open(log_file, "w") as f:
        json.dump(data, f, indent=2)
    log.info(f"  Research log: {log_file}")


# ---------------------------------------------------------------------------
# GitHub Distribution
# ---------------------------------------------------------------------------

def _gh_api(token: str, method: str, path: str, payload: dict = None) -> dict:
    """Low-level GitHub API call using requests."""
    url = f"https://api.github.com{path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    }
    resp = requests.request(method, url, json=payload, headers=headers, timeout=30)
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"GitHub API {method} {path} → {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


def _gh_get_file_sha(token: str, owner: str, repo: str, path: str) -> Optional[str]:
    """Return the blob SHA of a file (needed to update it). None if not found."""
    try:
        r = _gh_api(token, "GET", f"/repos/{owner}/{repo}/contents/{path}")
        return r.get("sha")
    except RuntimeError:
        return None


def _gh_put_file(token: str, owner: str, repo: str, path: str,
                 content: str, message: str):
    """Create or update a file in the repo."""
    import base64
    sha = _gh_get_file_sha(token, owner, repo, path)
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
    }
    if sha:
        payload["sha"] = sha
    _gh_api(token, "PUT", f"/repos/{owner}/{repo}/contents/{path}", payload)


def setup_github_repo(config: dict):
    """
    One-time setup: populate the VAULTED GitHub repo with the initial structure
    and enable GitHub Pages on the main branch.

    Run with:  python3 vaulted_pipeline.py --setup-github
    """
    gh = config["github"]
    token, owner, repo = gh["token"], gh["owner"], gh["repo"]
    pages_url = gh["pages_url"]

    log.info("Setting up GitHub repo...")

    # ── README ────────────────────────────────────────────────────────────────
    readme = f"""# VAULTED 🔒

**Real scams. Real victims. Real consequences.**

VAULTED is a daily true-crime podcast covering the scams, phishing schemes, and fraud operations happening right now — explained clearly so you know exactly what to watch for.

New episode every day. Available on Spotify, Apple Podcasts, Amazon Music, and iHeart.

---

## 📡 Subscribe

| Platform | Link |
|---|---|
| Spotify | *Coming soon* |
| Apple Podcasts | *Coming soon* |
| Amazon Music | *Coming soon* |
| iHeart | *Coming soon* |
| RSS Feed | [feed.xml]({pages_url}/feed.xml) |

---

## 🛡️ This Week in Fraud

*Updated weekly — real breakdowns from the latest episodes.*

<!-- WEEKLY_UPDATE_START -->
*Stay tuned — first episode dropping soon.*
<!-- WEEKLY_UPDATE_END -->

---

## 🔐 How to Protect Yourself

### Crypto ATM Scams
- Never deposit cash into a crypto ATM because someone on the phone told you to
- Legitimate companies and government agencies never ask for payment via crypto ATM
- Once cash goes in, it's gone — no chargebacks, no reversals

### Phishing & Smishing
- If a link came from a text message, don't click it — go directly to the company's website
- Real banks never ask you to verify by clicking a link in a text
- Check the sender domain carefully — one character off is intentional

### Romance Scams
- Anyone who builds a relationship online and eventually asks for money is running a scam
- Reverse image search every photo of someone you've never met in person

### Impersonation Fraud
- Government agencies (IRS, SSA, Medicare) never call demanding immediate payment
- If someone claims to be from your bank, hang up and call the number on the back of your card
- Urgency is a weapon — scammers manufacture panic to stop you from thinking clearly

---

*VAULTED is produced independently. All victim names and identifying details are fictionalized.*
"""

    # ── Initial RSS feed ───────────────────────────────────────────────────────
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
  xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>VAULTED</title>
    <link>{pages_url}</link>
    <description>Real scams. Real victims. Real consequences. A daily true-crime podcast covering the fraud operations happening right now.</description>
    <language>en-us</language>
    <itunes:author>Ty</itunes:author>
    <itunes:owner><itunes:name>Ty</itunes:name></itunes:owner>
    <itunes:category text="True Crime"/>
    <itunes:explicit>no</itunes:explicit>
    <itunes:image href="{pages_url}/artwork/cover.jpeg"/>
    <image>
      <url>{pages_url}/artwork/cover.jpeg</url>
      <title>VAULTED</title>
      <link>{pages_url}</link>
    </image>
    <!-- Episodes appended automatically by the pipeline -->
  </channel>
</rss>
"""

    _gh_put_file(token, owner, repo, "README.md", readme, "Initial README")
    log.info("  ✓ README.md")

    _gh_put_file(token, owner, repo, "feed.xml", feed, "Initial RSS feed")
    log.info("  ✓ feed.xml")

    _gh_put_file(token, owner, repo, "episodes/.gitkeep", "", "Create episodes dir")
    log.info("  ✓ episodes/")

    _gh_put_file(token, owner, repo, "artwork/.gitkeep", "", "Create artwork dir")
    log.info("  ✓ artwork/")

    # ── Upload cover artwork ───────────────────────────────────────────────────
    artwork_src = SCRIPT_DIR / gh.get("artwork_file", "VAULTED_Podcast_Artwork.jpeg")
    if artwork_src.exists():
        import base64
        artwork_b64 = base64.b64encode(artwork_src.read_bytes()).decode()
        sha = _gh_get_file_sha(token, owner, repo, "artwork/cover.jpeg")
        payload = {"message": "Add cover artwork", "content": artwork_b64}
        if sha:
            payload["sha"] = sha
        _gh_api(token, "PUT", f"/repos/{owner}/{repo}/contents/artwork/cover.jpeg", payload)
        log.info("  ✓ artwork/cover.jpeg")
    else:
        log.warning(f"  ⚠ Artwork not found at {artwork_src} — skipping")

    # ── Enable GitHub Pages (serve from root of main branch) ──────────────────
    try:
        _gh_api(token, "POST", f"/repos/{owner}/{repo}/pages", {
            "source": {"branch": "main", "path": "/"}
        })
        log.info("  ✓ GitHub Pages enabled")
    except RuntimeError as e:
        if "already enabled" in str(e).lower() or "409" in str(e):
            log.info("  ✓ GitHub Pages already enabled")
        else:
            log.warning(f"  ⚠ Pages setup: {e}")

    log.info(f"\n  RSS feed will be live at: {pages_url}/feed.xml")
    log.info("  (GitHub Pages takes ~60 seconds to go live after first push)")


def publish_episode(audio_path: Path, meta_path: Optional[Path],
                    config: dict, release_date: Optional[str] = None):
    """
    Publish one episode to GitHub:
      1. Upload the MP3 as a GitHub Release asset
      2. Append the episode to feed.xml
      3. Save episode show notes to episodes/
      4. Optionally update the weekly README section

    release_date: ISO date string YYYY-MM-DD for scheduled pubDate.
                  Defaults to today if not provided.
    """
    gh = config["github"]
    token, owner, repo = gh["token"], gh["owner"], gh["repo"]
    pages_url = gh["pages_url"]

    date_str = audio_path.stem.replace("vaulted_", "")  # e.g. "03-27"

    # Determine pubDate
    if release_date:
        pub_dt = datetime.strptime(release_date, "%Y-%m-%d")
    else:
        try:
            year = datetime.now().year
            pub_dt = datetime.strptime(f"{year}-{date_str}", "%Y-%m-%d")
        except ValueError:
            pub_dt = datetime.now()

    pub_date_rfc = pub_dt.strftime("%a, %d %b %Y 06:00:00 +0000")  # 6am UTC
    file_size = audio_path.stat().st_size

    # Load metadata if available
    meta = {}
    if meta_path and meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)

    title = meta.get("title") or f"VAULTED — {pub_dt.strftime('%B %d, %Y')}"
    description = meta.get("show_notes") or "Real scams. Real victims. Real consequences."
    episode_num = meta.get("episode_number", date_str)

    log.info(f"  Publishing episode: {title}")

    # ── 1. Create GitHub Release + upload MP3 ─────────────────────────────────
    tag = f"ep-{date_str}"
    try:
        release = _gh_api(token, "POST", f"/repos/{owner}/{repo}/releases", {
            "tag_name": tag,
            "name": title,
            "body": description[:500],
            "draft": False,
            "prerelease": False,
        })
    except RuntimeError as e:
        if "already_exists" in str(e) or "422" in str(e):
            # Release exists — get it
            releases = _gh_api(token, "GET", f"/repos/{owner}/{repo}/releases")
            release = next((r for r in releases if r["tag_name"] == tag), None)
            if not release:
                raise
        else:
            raise

    upload_url = release["upload_url"].replace("{?name,label}", "")
    filename = audio_path.name
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "audio/mpeg",
    }
    resp = requests.post(
        f"{upload_url}?name={filename}",
        headers=headers,
        data=audio_path.read_bytes(),
        timeout=300,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Asset upload failed: {resp.status_code} {resp.text[:200]}")

    mp3_url = f"https://github.com/{owner}/{repo}/releases/download/{tag}/{filename}"
    log.info(f"  ✓ MP3 uploaded: {mp3_url}")

    # ── 2. Update feed.xml ────────────────────────────────────────────────────
    feed_sha = _gh_get_file_sha(token, owner, repo, "feed.xml")
    import base64
    feed_resp = _gh_api(token, "GET", f"/repos/{owner}/{repo}/contents/feed.xml")
    feed_xml = base64.b64decode(feed_resp["content"]).decode()

    # Build episode item
    item_xml = f"""
    <item>
      <title><![CDATA[{title}]]></title>
      <description><![CDATA[{description}]]></description>
      <pubDate>{pub_date_rfc}</pubDate>
      <enclosure url="{mp3_url}" length="{file_size}" type="audio/mpeg"/>
      <guid isPermaLink="false">{owner}-vaulted-{date_str}</guid>
      <itunes:duration>{meta.get('estimated_duration_min', 17)}:00</itunes:duration>
      <itunes:explicit>no</itunes:explicit>
    </item>"""

    # Insert before closing </channel>
    feed_xml = feed_xml.replace("  </channel>", item_xml + "\n  </channel>")
    _gh_put_file(token, owner, repo, "feed.xml", feed_xml, f"Add episode {date_str}")
    log.info("  ✓ feed.xml updated")

    # ── 3. Save episode show notes ────────────────────────────────────────────
    show_notes = f"# {title}\n\n"
    show_notes += f"**Released:** {pub_dt.strftime('%B %d, %Y')}  \n"
    show_notes += f"**Listen:** [MP3]({mp3_url})\n\n"
    show_notes += f"{description}\n\n"
    if meta.get("faq"):
        show_notes += "## FAQ\n\n"
        for item in meta["faq"]:
            show_notes += f"**{item.get('q','Q')}**  \n{item.get('a','')}\n\n"
    _gh_put_file(token, owner, repo, f"episodes/vaulted-{date_str}.md",
                 show_notes, f"Show notes for {date_str}")
    log.info(f"  ✓ episodes/vaulted-{date_str}.md")

    log.info(f"  Episode live (or scheduled) at: {pages_url}/feed.xml")
    return mp3_url


def publish_batch(config: dict, release_days: Optional[list] = None):
    """
    Publish all generated episodes that haven't been uploaded yet.
    release_days: list of day names e.g. ["mon","tue","wed","thu","fri"]
                  mapped to the next upcoming dates from today.
    """
    audio_dir = SCRIPT_DIR / config["output"].get("audio_dir", "Audio")
    scripts_dir = SCRIPT_DIR / config["output"].get("scripts_dir", "Scripts")

    # Find all finished MP3s
    mp3s = sorted(audio_dir.glob("vaulted_*.mp3"))
    if not mp3s:
        log.error("No MP3 files found in Audio/ to publish.")
        return

    # Map day names to upcoming dates if provided
    date_map = {}
    if release_days:
        day_lookup = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
        today = datetime.now()
        assigned = []
        for day in release_days:
            idx = day_lookup.get(day.lower())
            if idx is None:
                continue
            days_ahead = (idx - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            d = today + timedelta(days=days_ahead)
            assigned.append(d.strftime("%Y-%m-%d"))
        # pair mp3s with dates
        for i, mp3 in enumerate(mp3s):
            if i < len(assigned):
                date_str = mp3.stem.replace("vaulted_", "")
                date_map[date_str] = assigned[i]

    log.info(f"Publishing {len(mp3s)} episode(s)...")
    for mp3 in mp3s:
        date_str = mp3.stem.replace("vaulted_", "")
        meta_path = scripts_dir / f"vaulted_metadata_{date_str}.json"
        release_date = date_map.get(date_str)
        try:
            publish_episode(mp3, meta_path if meta_path.exists() else None,
                            config, release_date=release_date)
        except Exception as e:
            log.error(f"  Failed to publish {mp3.name}: {e}")


# ---------------------------------------------------------------------------
# Approval Workflow
# ---------------------------------------------------------------------------

def process_approvals(config: dict, release_days: Optional[list] = None):
    """
    Process the Audio/approved/ and Audio/declined/ folders.

    Approved episodes → published to GitHub RSS feed, moved to Audio/published/
    Declined episodes → fully regenerated from scratch, new version lands in Audio/pending/

    Triggered by the Siri Shortcut or manually:
        python3 vaulted_pipeline.py --process-approvals
        python3 vaulted_pipeline.py --process-approvals --release-days mon,tue,wed,thu,fri
    """
    audio_dir = SCRIPT_DIR / config["output"].get("audio_dir", "Audio")
    scripts_dir = SCRIPT_DIR / config["output"].get("scripts_dir", "Scripts")

    approved_dir = audio_dir / "approved"
    declined_dir = audio_dir / "declined"
    published_dir = audio_dir / "published"

    approved_dir.mkdir(parents=True, exist_ok=True)
    declined_dir.mkdir(parents=True, exist_ok=True)
    published_dir.mkdir(parents=True, exist_ok=True)

    approved = sorted(approved_dir.glob("vaulted_*.mp3"))
    declined = sorted(declined_dir.glob("vaulted_*.mp3"))

    if not approved and not declined:
        log.info("No episodes in approved/ or declined/ — nothing to do.")
        log.info(f"  Drag episodes into:\n"
                 f"    {approved_dir}  ← to publish\n"
                 f"    {declined_dir}  ← to regenerate")
        return

    results = {"published": [], "regenerated": [], "failed": []}

    # ── Map release days to dates ─────────────────────────────────────────────
    date_map = {}
    if release_days and approved:
        day_lookup = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
        today = datetime.now()
        assigned = []
        for day in release_days:
            idx = day_lookup.get(day.lower())
            if idx is None:
                continue
            days_ahead = (idx - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            d = today + timedelta(days=days_ahead)
            assigned.append(d.strftime("%Y-%m-%d"))
        for i, mp3 in enumerate(approved):
            date_str = mp3.stem.replace("vaulted_", "")
            if i < len(assigned):
                date_map[date_str] = assigned[i]

    # ── Publish approved ──────────────────────────────────────────────────────
    for mp3 in approved:
        date_str = mp3.stem.replace("vaulted_", "")
        meta_path = scripts_dir / f"vaulted_metadata_{date_str}.json"
        release_date = date_map.get(date_str)
        try:
            log.info(f"\n── Publishing approved: {mp3.name} "
                     f"{'→ ' + release_date if release_date else '(immediate)'}  ──")
            publish_episode(
                mp3,
                meta_path if meta_path.exists() else None,
                config,
                release_date=release_date,
            )
            # Move to published/
            dest = published_dir / mp3.name
            if dest.exists():
                dest.unlink()
            mp3.rename(dest)
            results["published"].append(mp3.name)
            log.info(f"  ✓ Moved to published/")
        except Exception as e:
            log.error(f"  ✗ Failed to publish {mp3.name}: {e}")
            results["failed"].append(mp3.name)

    # ── Regenerate declined ───────────────────────────────────────────────────
    for mp3 in declined:
        date_str = mp3.stem.replace("vaulted_", "")
        log.info(f"\n── Regenerating declined: {mp3.name}  ──")
        try:
            # Remove the old audio and script so the pipeline runs fresh
            mp3.unlink()
            old_script = scripts_dir / f"vaulted_{date_str}.txt"
            old_meta   = scripts_dir / f"vaulted_metadata_{date_str}.json"
            if old_script.exists():
                old_script.unlink()
            if old_meta.exists():
                old_meta.unlink()
            # Full regeneration — new research, new script, new audio
            run_pipeline(date_str=date_str)
            results["regenerated"].append(mp3.name)
            log.info(f"  ✓ Regenerated — new episode in Audio/pending/")
        except Exception as e:
            log.error(f"  ✗ Failed to regenerate {mp3.name}: {e}")
            results["failed"].append(mp3.name)

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info("\n══════════ Approval Run Complete ══════════")
    if results["published"]:
        log.info(f"  Published ({len(results['published'])}): {', '.join(results['published'])}")
    if results["regenerated"]:
        log.info(f"  Regenerated ({len(results['regenerated'])}): {', '.join(results['regenerated'])}")
    if results["failed"]:
        log.warning(f"  Failed ({len(results['failed'])}): {', '.join(results['failed'])}")

    return results


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------
def run_pipeline(
    date_str: Optional[str] = None,
    dry_run: bool = False,
    skip_tts: bool = False,
):
    if date_str is None:
        date_str = datetime.now().strftime("%m-%d")

    log.info("=" * 60)
    log.info(f"  VAULTED — Episode for {date_str}")
    log.info("=" * 60)

    config = load_config()
    history = load_history()
    num_stories = config["podcast"]["stories_per_episode"]

    # ── STAGE 1: Research ──────────────────────────────────────────────────
    log.info("\n📡 STAGE 1: Researching stories...")

    web_articles = search_google_news(config)
    rss_articles = fetch_all_feeds(config)
    all_articles = web_articles + rss_articles
    log.info(f"  Total: {len(web_articles)} Google News + {len(rss_articles)} RSS = {len(all_articles)} articles")

    stories = select_top_stories(all_articles, history, count=num_stories)

    # Fallback to AI-generated stories if needed
    if len(stories) < num_stories:
        needed = num_stories - len(stories)
        log.warning(f"  Only {len(stories)} real stories found — generating {needed} fictional ones")
        if not dry_run:
            fallback = get_fallback_stories(config)
            stories.extend(fallback[:needed])

    if not stories:
        log.error("No stories available. Exiting.")
        sys.exit(1)

    log.info(f"\n📋 Selected stories:")
    for i, s in enumerate(stories, 1):
        tag = " [GENERATED]" if s.get("is_generated") else ""
        log.info(f"  {i}. [{s['source']}] {s['title']} (score: {s['relevance']}){tag}")

    save_research_log(stories, all_articles, date_str)

    if dry_run:
        log.info("\n🏁 DRY RUN — research complete, no script generated.")
        return

    # ── STAGE 2: Write script ──────────────────────────────────────────────
    log.info("\n✍️  STAGE 2: Writing script via Ollama...")
    raw_script = generate_script(stories, config, date_str=date_str)

    if not raw_script or len(raw_script.split()) < 800:
        log.error(f"Script too short or empty ({len(raw_script.split())} words). Check Ollama.")
        sys.exit(1)

    log.info(f"  Draft: {len(raw_script.split())} words")

    # ── STAGE 3: Humanize ─────────────────────────────────────────────────
    log.info("\n🎨 STAGE 3: Humanizing...")
    final_script = humanize_script(raw_script, config)

    if not final_script or len(final_script.split()) < 800:
        log.warning("  Humanization produced too little — using raw draft")
        final_script = raw_script

    # Validate all required audio markers are present
    missing_markers = validate_script_markers(final_script)
    if missing_markers:
        log.warning(f"  ⚠️  Script is missing markers: {missing_markers}")
        log.warning("  Audio cues for missing markers will be skipped — episode will still generate.")

    wpm = config["podcast"]["words_per_minute"]
    word_count = len(final_script.split())
    est_min = word_count / wpm
    log.info(f"  Final: {word_count} words (~{est_min:.1f} min at {wpm} WPM)")

    if word_count < 2000:
        log.warning(f"  ⚠️  Script may be too short (target: 2,500-2,900 words)")
    elif word_count > 3500:
        log.warning(f"  ⚠️  Script may be too long (target: 2,500-2,900 words)")

    # ── STAGE 3.5: TTS voice direction ────────────────────────────────────
    if config["podcast"].get("tts_humanize", False):
        log.info("\n🎭 STAGE 3.5: TTS voice direction...")
        tts_result = tts_humanize_script(final_script, config)
        final_script = tts_result
        # Recount words after pass (filler sounds add a few)
        word_count = len(final_script.split())
        est_min = word_count / wpm
        log.info(f"  Post-TTS pass: {word_count} words (~{est_min:.1f} min)")
    else:
        log.info("\n⏭️  STAGE 3.5: Skipped (tts_humanize not enabled in config)")

    # ── STAGE 4: Save script ───────────────────────────────────────────────
    log.info("\n💾 STAGE 4: Saving script...")
    script_path = save_script(final_script, date_str, config)

    # Update used-story history
    for s in stories:
        if not s.get("is_generated"):
            h = article_hash(s["title"], s.get("link", ""))
            if h not in history["used_hashes"]:
                history["used_hashes"].append(h)
    history["used_hashes"] = history["used_hashes"][-500:]
    history["last_run"] = datetime.now().isoformat()
    save_history(history)

    # ── STAGE 4.5: Generate episode metadata ──────────────────────────────
    log.info("\n📋 STAGE 4.5: Generating episode metadata...")
    meta_path = None
    try:
        metadata = generate_metadata(final_script, stories, date_str, config)
        meta_path = save_metadata(metadata, date_str, config)
    except Exception as e:
        log.warning(f"  Metadata generation failed (non-fatal): {e}")

    # ── STAGE 5: TTS via Mimika ────────────────────────────────────────────
    audio_path = None
    if not skip_tts:
        log.info("\n🎙️  STAGE 5: Generating audio via Mimika Studio...")
        audio_path = generate_audio(script_path, date_str, config)
        if not audio_path:
            log.warning("  Audio generation failed. Script is saved — run TTS manually.")
    else:
        log.info("\n⏭️  STAGE 5: Skipped (--skip-tts)")

    # ── STAGE 6: Episode log + ID3 tags ───────────────────────────────────
    log.info("\n📝 STAGE 6: Logging episode...")
    episode_number = log_episode(date_str, stories, audio_path)
    if audio_path and audio_path.exists():
        write_id3_tags(audio_path, episode_number, date_str, stories)

    # ── Summary ───────────────────────────────────────────────────────────
    log.info("\n" + "=" * 60)
    log.info(f"  ✅  VAULTED episode {date_str} complete! (Ep. #{episode_number})")
    log.info(f"  📄  Script:  {script_path}")
    log.info(f"  📊  {word_count} words | ~{est_min:.1f} min")
    if meta_path:
        log.info(f"  📋  Metadata: {meta_path}")
    if audio_path:
        log.info(f"  🎧  Audio:   {audio_path}")
    log.info("=" * 60)

    return script_path, audio_path


# ---------------------------------------------------------------------------
# TTS-Only Mode
# ---------------------------------------------------------------------------
def run_tts_only(date_str: Optional[str] = None):
    if date_str is None:
        date_str = datetime.now().strftime("%m-%d")
    config = load_config()
    scripts_dir = SCRIPT_DIR / config["output"]["scripts_dir"]
    script_path = scripts_dir / config["output"]["filename_pattern"].format(date=date_str)
    if not script_path.exists():
        log.error(f"No script found at {script_path}")
        sys.exit(1)
    log.info(f"Re-running TTS for: {script_path}")
    audio_path = generate_audio(script_path, date_str, config)
    if audio_path:
        log.info(f"✅ Audio: {audio_path}")
        # Write ID3 tags — pull story info from the research log if available
        research_log = LOG_PATH / f"research_{date_str}.json"
        stories = []
        if research_log.exists():
            with open(research_log) as f:
                data = json.load(f)
            stories = data.get("selected", [])
        episode_number = log_episode(date_str, stories, audio_path)
        write_id3_tags(audio_path, episode_number, date_str, stories)
    else:
        log.error("TTS failed.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Batch Mode — generate multiple future episodes in one session
# ---------------------------------------------------------------------------
def get_upcoming_weekdays(count: int, start_from: Optional[datetime] = None) -> list[str]:
    """
    Return the next `count` weekday dates (Mon-Fri) as MM-DD strings,
    starting from tomorrow (or start_from if provided).
    Skips dates that already have a script file.
    """
    config = load_config()
    scripts_dir = SCRIPT_DIR / config["output"]["scripts_dir"]
    pattern = config["output"]["filename_pattern"]

    dates = []
    check_date = (start_from or datetime.now()) + timedelta(days=1)

    # Look ahead up to 60 days to find `count` available weekdays
    for _ in range(60):
        if check_date.weekday() < 5:  # 0=Mon ... 4=Fri
            date_str = check_date.strftime("%m-%d")
            script_path = scripts_dir / pattern.format(date=date_str)
            if not script_path.exists():
                dates.append(date_str)
                if len(dates) >= count:
                    break
        check_date += timedelta(days=1)

    return dates


def run_batch(episodes: int = 10, skip_tts: bool = False):
    """
    Generate multiple future episodes in one run.
    Perfect for stocking up a buffer on days off (Wed/Sun).
    Automatically finds the next N weekdays that don't have a script yet.
    """
    log.info("=" * 60)
    log.info(f"  VAULTED — Batch Mode: generating {episodes} episodes")
    log.info("=" * 60)

    dates = get_upcoming_weekdays(episodes)

    if not dates:
        log.info("  All upcoming weekdays already have scripts — nothing to do!")
        return

    log.info(f"  Episodes to generate: {', '.join(dates)}")
    log.info(f"  This covers {len(dates)} weekdays")

    results = []
    failed = []

    for i, date_str in enumerate(dates, 1):
        log.info(f"\n{'─' * 60}")
        log.info(f"  Episode {i}/{len(dates)}: {date_str}")
        log.info(f"{'─' * 60}")
        try:
            result = run_pipeline(date_str=date_str, skip_tts=skip_tts)
            if result:
                results.append(date_str)
            else:
                failed.append(date_str)
        except Exception as e:
            log.error(f"  Episode {date_str} failed: {e}")
            failed.append(date_str)
            # Keep going — don't let one failure stop the whole batch
            continue

    # ── Retry pass ────────────────────────────────────────────────────────────
    # Give each failed episode one more shot before giving up.
    # A fresh run often succeeds if the first attempt hit a transient Ollama
    # timeout, a flaky RSS fetch, or a TTS chunk error.
    if failed:
        log.info(f"\n{'─' * 60}")
        log.info(f"  RETRY PASS: {len(failed)} episode(s) failed — trying each once more")
        log.info(f"{'─' * 60}")
        still_failed = []
        for date_str in list(failed):
            log.info(f"  Retrying {date_str}...")
            try:
                result = run_pipeline(date_str=date_str, skip_tts=skip_tts)
                if result:
                    results.append(date_str)
                    failed.remove(date_str)
                    log.info(f"  ✅ Retry succeeded: {date_str}")
                else:
                    still_failed.append(date_str)
                    log.warning(f"  ❌ Retry failed again: {date_str}")
            except Exception as e:
                log.error(f"  ❌ Retry exception for {date_str}: {e}")
                still_failed.append(date_str)
        failed = still_failed

    # Final batch summary
    log.info("\n" + "=" * 60)
    log.info(f"  VAULTED Batch Complete")
    log.info(f"  ✅  Generated: {len(results)} episodes  ({', '.join(results) if results else 'none'})")
    if failed:
        log.info(f"  ❌  Failed:    {len(failed)} episodes  ({', '.join(failed)}) — manual retry needed")
    log.info(f"  📦  Buffer:    {len(results)} episodes ready to upload")
    log.info("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="VAULTED Podcast Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 vaulted_pipeline.py                     # Generate today's episode
  python3 vaulted_pipeline.py --batch             # Stock up: next 10 weekdays
  python3 vaulted_pipeline.py --batch 5           # Stock up: next 5 weekdays
  python3 vaulted_pipeline.py --batch --skip-tts  # Scripts only, no audio
  python3 vaulted_pipeline.py --date 04-07        # Specific date
  python3 vaulted_pipeline.py --dry-run           # Research only, no writing
  python3 vaulted_pipeline.py --tts-only          # Re-run audio on saved script
        """,
    )
    parser.add_argument("--date", type=str, default=None,
                        help="Episode date MM-DD (default: today). Ignored in batch mode.")
    parser.add_argument("--batch", nargs="?", const=10, type=int, metavar="N",
                        help="Batch mode: generate next N upcoming weekday episodes (default: 10)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Research only, no LLM or TTS")
    parser.add_argument("--skip-tts", action="store_true",
                        help="Generate script(s) but skip audio generation")
    parser.add_argument("--tts-only", action="store_true",
                        help="Re-run TTS on an existing script (requires --date)")
    parser.add_argument("--setup-github", action="store_true",
                        help="One-time: initialize GitHub repo, upload artwork, enable Pages")
    parser.add_argument("--publish", action="store_true",
                        help="Publish finished episode(s) to GitHub + update RSS feed")
    parser.add_argument("--process-approvals", action="store_true",
                        help="Publish approved/ episodes, regenerate declined/ episodes")
    parser.add_argument("--release-days", type=str, default=None, metavar="DAYS",
                        help="Schedule episodes across days e.g. mon,tue,wed,thu,fri")
    args = parser.parse_args()

    config = load_config()

    if args.setup_github:
        setup_github_repo(config)
    elif args.process_approvals:
        days = [d.strip() for d in args.release_days.split(",")] if args.release_days else None
        process_approvals(config, release_days=days)
    elif args.publish:
        days = [d.strip() for d in args.release_days.split(",")] if args.release_days else None
        publish_batch(config, release_days=days)
    elif args.tts_only:
        run_tts_only(date_str=args.date)
    elif args.batch is not None:
        run_batch(episodes=args.batch, skip_tts=args.skip_tts)
    else:
        run_pipeline(
            date_str=args.date,
            dry_run=args.dry_run,
            skip_tts=args.skip_tts,
        )
