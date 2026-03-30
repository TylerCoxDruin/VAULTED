#!/bin/bash
# ============================================================
# VAULTED Pipeline Setup
# ============================================================
# Run this once to set up everything you need.
# Usage: chmod +x vaulted_setup.sh && ./vaulted_setup.sh
# ============================================================

set -e

echo "========================================"
echo "  VAULTED Pipeline Setup"
echo "========================================"
echo ""

# 1. Python dependencies
echo "📦 Installing Python dependencies..."
pip3 install feedparser requests beautifulsoup4 --break-system-packages -q 2>/dev/null || \
pip3 install feedparser requests beautifulsoup4 -q
echo "   ✅ Python packages installed"

# 2. Check for Ollama
echo ""
echo "🤖 Checking for Ollama..."
if command -v ollama &> /dev/null; then
    echo "   ✅ Ollama found: $(ollama --version 2>/dev/null || echo 'installed')"

    # Check if a model is available
    echo "   Checking for models..."
    if ollama list 2>/dev/null | grep -q "llama3"; then
        echo "   ✅ llama3 model found"
    else
        echo "   ⚠️  No llama3 model found. Pulling llama3.1:8b (this may take a while)..."
        echo "   Run: ollama pull llama3.1:8b"
    fi
else
    echo "   ❌ Ollama not found!"
    echo ""
    echo "   Install Ollama:"
    echo "     macOS:  brew install ollama"
    echo "     or:     curl -fsSL https://ollama.com/install.sh | sh"
    echo ""
    echo "   Then pull a model:"
    echo "     ollama pull llama3.1:8b"
    echo ""
    echo "   And start the server:"
    echo "     ollama serve"
fi

# 3. Create directories
echo ""
echo "📁 Creating directories..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SCRIPT_DIR/Scripts"
mkdir -p "$SCRIPT_DIR/Logs"
echo "   ✅ Scripts/ and Logs/ directories ready"

# 4. Quick connectivity test
echo ""
echo "📡 Testing RSS feed connectivity..."
python3 -c "
import feedparser
feed = feedparser.parse('https://krebsonsecurity.com/feed/')
if feed.entries:
    print(f'   ✅ RSS working - got {len(feed.entries)} articles from KrebsOnSecurity')
else:
    print('   ⚠️  Could not fetch RSS feeds. Check your internet connection.')
" 2>/dev/null || echo "   ⚠️  RSS test failed - check feedparser installation"

echo ""
echo "========================================"
echo "  Setup Complete!"
echo "========================================"
echo ""
echo "  Quick start:"
echo "    python3 vaulted_pipeline.py --dry-run   # Test research only"
echo "    python3 vaulted_pipeline.py              # Full pipeline"
echo ""
echo "  Make sure Ollama is running before the full pipeline:"
echo "    ollama serve"
echo ""
