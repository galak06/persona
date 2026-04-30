#!/usr/bin/env bash
# Pinterest Standard Access upgrade — 90s demo script.
#
# Produces the full on-screen sequence Pinterest asks for:
#   1. How the app authenticates (OAuth 2.0 consent flow)
#   2. What it does (dry-run of 4-pins-per-recipe backfill)
#   3. Destination (claimed domain, business board)
#
# Usage:
#   1. Open a terminal window at a comfortable size (80x30 cols rough).
#   2. Press Cmd+Shift+5 → pick "Record Entire Screen" or a window selection.
#   3. Click Record.
#   4. In terminal:   bash social-automation/scripts/record_pinterest_demo.sh
#   5. When prompted, click Allow on Pinterest's consent page.
#   6. After the "End of demo" banner, stop recording (Cmd+Ctrl+Esc, or
#      click the stop icon in the macOS menu bar).
#
# Flags:
#   --skip-oauth   : rehearsal mode; shows an already-authenticated message
#                    instead of opening the browser (preserves your token).
#
set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SKIP_OAUTH="${1:-}"

pause() { sleep "${1:-2}"; }

banner() {
  echo
  echo "════════════════════════════════════════════════════════════════"
  echo "  $1"
  echo "════════════════════════════════════════════════════════════════"
  echo
}

# ---------- intro ----------

clear
banner "dogfoodandfun-publisher — Pinterest API v5 integration"
cat <<'INTRO'

  App ID:             1564031
  Purpose:            Automated publishing of first-party recipe content
                      from dogfoodandfun.com to our own Pinterest business
                      profile (pinterest.com/dogfoodandfun/).
  Authenticated user: our own Pinterest business account only.
  End users:          none — this is a server-side publisher, no UI.
  Data handling:      write-only; no Pinterest data is read or shared.

INTRO
pause 8

# ---------- step 1: OAuth ----------

banner "Step 1 / 3  —  OAuth 2.0 authentication"

if [[ "$SKIP_OAUTH" == "--skip-oauth" ]]; then
  cat <<'OAUTH_SKIP'
[rehearsal mode — skipping live OAuth run to preserve token]

Helper:           social-automation/scripts/pinterest_oauth.py
Redirect URI:     http://localhost:8765/callback
Scopes requested: boards:read  boards:write  pins:read  pins:write  user_accounts:read
Granted to:       pinterest.com/dogfoodandfun/
Token storage:    social-automation/.claude/settings.local.json
Refresh token:    stored, used to rotate access tokens before 30-day expiry

OAUTH_SKIP
  pause 10
else
  echo "Launching scripts/pinterest_oauth.py ..."
  echo "Your browser will open Pinterest's consent screen — click Allow."
  echo
  pause 4
  python3 "$ROOT/social-automation/scripts/pinterest_oauth.py"
  pause 3
fi

# ---------- step 2: publisher dry-run ----------

banner "Step 2 / 3  —  Publisher dry-run"
cat <<'PUB_INTRO'
For every new recipe published on dogfoodandfun.com, the app creates 4
branded Pins (one per carousel slide) on our own "Homemade Dog Recipes"
board. Each Pin links back to the source recipe page on our site.

Running scripts/pinterest_backfill.py in default dry-run mode shows the
full flow: resolving the WP post, locating the 4 slide images in our WP
media library, and composing 4 distinct Pin descriptions.

PUB_INTRO
pause 7

python3 "$ROOT/social-automation/recipe-publisher/scripts/pinterest_backfill.py"
pause 4

# ---------- step 3: destination ----------

banner "Step 3 / 3  —  Destination board & account state"
cat <<'DEST'

Profile:           https://www.pinterest.com/dogfoodandfun/
Board:             https://www.pinterest.com/dogfoodandfun/homemade-dog-recipes/
Domain verified:   dogfoodandfun.com  (via <meta p:domain_verify> tag)
Account type:      Business
Privacy policy:    https://dogfoodandfun.com/privacy-policy/

All Pins will be published exclusively to our own board, linking back
to our own site. No third-party user data, no scraping, no resale.

DEST
pause 10

banner "End of demo  —  requesting Standard access to enable live pin creation"
pause 4
