#!/bin/bash
# ============================================================
# VAULTED Batch Runner
# Runs automatically every Sunday at 3:00 AM via cron.
# Generates the next 5 weekday episodes (Mon-Fri).
# ============================================================

VAULTED_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$VAULTED_DIR/Logs/batch_$(date +%Y-%m-%d).log"
PYTHON=$(which python3)
MIMIKA_URL="http://localhost:7693"
MIMIKA_APP="/Applications/MimikaStudio.app"

mkdir -p "$VAULTED_DIR/Logs"

echo "======================================" >> "$LOG_FILE"
echo "VAULTED Batch — $(date)" >> "$LOG_FILE"
echo "======================================" >> "$LOG_FILE"

# ── Ensure Ollama is running ───────────────────────────────
if ! curl -s http://localhost:11434/health > /dev/null 2>&1; then
    echo "[$(date +%H:%M)] Starting Ollama..." >> "$LOG_FILE"
    ollama serve >> "$LOG_FILE" 2>&1 &
    sleep 10
fi

# ── Ensure Mimika Studio is running ───────────────────────
# Try the API first — if it responds on any endpoint, we're good
mimika_ready() {
    for endpoint in "/api/health" "/api/qwen3/info" "/api/system/info" "/docs"; do
        if curl -s -o /dev/null -w "%{http_code}" "${MIMIKA_URL}${endpoint}" 2>/dev/null | grep -qE "^[1-4]"; then
            return 0
        fi
    done
    return 1
}

if ! mimika_ready; then
    echo "[$(date +%H:%M)] Mimika Studio not running — launching app..." >> "$LOG_FILE"
    if [ -d "$MIMIKA_APP" ]; then
        open "$MIMIKA_APP"
        # Wait up to 90 seconds for the API to become available
        for i in $(seq 1 18); do
            sleep 5
            if mimika_ready; then
                echo "[$(date +%H:%M)] Mimika Studio ready after $((i * 5))s" >> "$LOG_FILE"
                break
            fi
            if [ $i -eq 18 ]; then
                echo "[$(date +%H:%M)] WARNING: Mimika Studio did not start in time." >> "$LOG_FILE"
                echo "  Scripts will still be generated — audio may fail." >> "$LOG_FILE"
            fi
        done
    else
        echo "[$(date +%H:%M)] WARNING: MimikaStudio.app not found at $MIMIKA_APP" >> "$LOG_FILE"
        echo "  Scripts will still be generated — start Mimika manually for audio." >> "$LOG_FILE"
    fi
else
    echo "[$(date +%H:%M)] Mimika Studio is already running." >> "$LOG_FILE"
fi

# ── Run the batch pipeline ─────────────────────────────────
echo "[$(date +%H:%M)] Starting batch (5 episodes)..." >> "$LOG_FILE"
cd "$VAULTED_DIR"
"$PYTHON" vaulted_pipeline.py --batch 5 >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

echo "" >> "$LOG_FILE"
if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date +%H:%M)] ✅ Batch complete." >> "$LOG_FILE"
else
    echo "[$(date +%H:%M)] ⚠️  Batch finished with errors (exit code $EXIT_CODE)." >> "$LOG_FILE"
fi

echo "Finished at: $(date)" >> "$LOG_FILE"
echo "======================================" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
