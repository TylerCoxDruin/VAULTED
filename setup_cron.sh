#!/bin/bash
# ============================================================
# VAULTED Cron Setup — run this ONCE to schedule the batch job
# Schedules vaulted_batch.sh to run every Sunday at 3:00 AM.
# Generates Mon-Fri episodes for the coming week.
# ============================================================

VAULTED_DIR="$(cd "$(dirname "$0")" && pwd)"
BATCH_SCRIPT="$VAULTED_DIR/vaulted_batch.sh"

# Make sure the batch script is executable
chmod +x "$BATCH_SCRIPT"

# The cron line: 3am every Sunday (0 = Sunday)
CRON_LINE="0 3 * * 0 $BATCH_SCRIPT"

echo "Setting up VAULTED cron job..."
echo "  Script:   $BATCH_SCRIPT"
echo "  Schedule: Every Sunday at 3:00 AM"
echo ""

# Add to crontab without duplicating
(crontab -l 2>/dev/null | grep -v "vaulted_batch.sh"; echo "$CRON_LINE") | crontab -

# Verify it was added
if crontab -l | grep -q "vaulted_batch.sh"; then
    echo "✅ Cron job installed successfully!"
    echo ""
    echo "Your full crontab:"
    crontab -l
else
    echo "❌ Something went wrong. Try adding manually:"
    echo "  1. Run: crontab -e"
    echo "  2. Add this line:"
    echo "     $CRON_LINE"
fi

echo ""
echo "The pipeline will run every Sunday at 3:00 AM."
echo "All 5 episodes should be ready by early afternoon."
echo ""
echo "Logs will be saved to: $VAULTED_DIR/Logs/batch_YYYY-MM-DD.log"
echo ""
echo "To remove this job later, run:"
echo "  crontab -l | grep -v 'vaulted_batch.sh' | crontab -"
