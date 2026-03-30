#!/bin/bash
# =============================================================================
# VAULTED — Create Siri Shortcut for episode approval
# Run once:  bash ~/Desktop/VaultedPod/create_shortcut.sh
# =============================================================================
#
# This creates a macOS Shortcut called "VAULTED Approve" that you can:
#   - Run from Siri: "Hey Siri, VAULTED Approve"
#   - Pin to your Menu Bar in the Shortcuts app
#   - Add to your iPhone/iPad Home Screen via iCloud sync
#
# WORKFLOW:
#   1. Listen to episodes in Audio/pending/
#   2. Drag approved episodes to Audio/approved/
#   3. Drag declined episodes to Audio/declined/
#   4. Tap the shortcut (or say "Hey Siri, VAULTED Approve")
#   5. Done — approved episodes upload, declined ones regenerate
# =============================================================================

SHORTCUT_NAME="VAULTED Approve"
PIPELINE="$HOME/Desktop/VaultedPod/vaulted_pipeline.py"
APPROVED_DIR="$HOME/Desktop/VaultedPod/Audio/approved"
DECLINED_DIR="$HOME/Desktop/VaultedPod/Audio/declined"

# Create the approval folders if they don't exist
mkdir -p "$APPROVED_DIR" "$DECLINED_DIR"
echo "✓ Created Audio/approved/ and Audio/declined/"

# Write the shortcut as a shell script wrapper
WRAPPER="$HOME/Desktop/VaultedPod/vaulted_approve.sh"
cat > "$WRAPPER" << 'SCRIPT'
#!/bin/bash
# VAULTED Approve — runs via Siri Shortcut
cd "$HOME/Desktop/VaultedPod"

APPROVED=$(ls Audio/approved/vaulted_*.mp3 2>/dev/null | wc -l | tr -d ' ')
DECLINED=$(ls Audio/declined/vaulted_*.mp3 2>/dev/null | tr -d ' ' | wc -l | tr -d ' ')

if [ "$APPROVED" -eq 0 ] && [ "$DECLINED" -eq 0 ]; then
  osascript -e 'display notification "No episodes in approved/ or declined/ folders." with title "VAULTED" subtitle "Nothing to do"'
  exit 0
fi

osascript -e "display notification \"Publishing $APPROVED episode(s), regenerating $DECLINED episode(s)...\" with title \"VAULTED\" subtitle \"Running...\""

# Run the pipeline
python3 "$HOME/Desktop/VaultedPod/vaulted_pipeline.py" --process-approvals >> "$HOME/Desktop/VaultedPod/Logs/approvals.log" 2>&1
EXIT=$?

if [ $EXIT -eq 0 ]; then
  osascript -e "display notification \"$APPROVED published · $DECLINED regenerated\" with title \"VAULTED ✓\" subtitle \"Done\""
else
  osascript -e 'display notification "Check Logs/approvals.log for details." with title "VAULTED ✗" subtitle "Something went wrong"'
fi
SCRIPT

chmod +x "$WRAPPER"
echo "✓ Created vaulted_approve.sh"

# Create the Shortcut via osascript
osascript << APPLESCRIPT
tell application "Shortcuts"
    -- Create a new shortcut that runs the shell script
end tell
APPLESCRIPT

echo ""
echo "════════════════════════════════════════════════"
echo "  Almost done — one manual step in Shortcuts:"
echo "════════════════════════════════════════════════"
echo ""
echo "  1. Open the Shortcuts app"
echo "  2. Click  +  to create a new shortcut"
echo "  3. Name it: VAULTED Approve"
echo "  4. Search for action: 'Run Shell Script'"
echo "  5. Paste this into the script box:"
echo ""
echo "     bash $HOME/Desktop/VaultedPod/vaulted_approve.sh"
echo ""
echo "  6. Save it"
echo "  7. Right-click → 'Add to Dock' or 'Pin in Menu Bar'"
echo ""
echo "  That's it. Siri will also respond to:"
echo "  'Hey Siri, VAULTED Approve'"
echo ""
echo "  To schedule with specific days, edit vaulted_approve.sh"
echo "  and change the --process-approvals line to:"
echo "  --process-approvals --release-days mon,tue,wed,thu,fri"
echo ""
