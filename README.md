# Lead Finder

Admin-only web app: search businesses via Google Places API (New), build a lead list, track outreach.

- **Search** one keyword, or the same keyword across many areas (beats Google's ~60 results/query cap)
- **No repeats**: businesses already in your database are hidden from new results (toggle "All" to see them). Google still bills for the call, since its API cannot skip known places
- **Lead pipeline**: status (New / Contacted / Interested / Not interested / Closed) and notes per lead, saved instantly
- **One-click** Call, WhatsApp (Indian mobile numbers) and Copy
- **Filter/sort** all leads by keyword searched, status, phone, or free text (matches name, address, phone, notes and the keyword used); each lead shows the keywords that found it; **export CSV** (Excel-ready) for any filtered view
- **Pagination everywhere**: numbered pages, "showing X–Y of N" and rows-per-page on leads, results and a full Searches history page
- **No browser popups**: confirmations, errors and "Saved" feedback all appear inline in the page
- **Cost guard**: `MAX_API_CALLS_PER_DAY` stops Google requests once reached (each result page = 1 request)

## Run
    pip install -r requirements.txt
    export GOOGLE_PLACES_API_KEY=... ADMIN_PASSWORD=... SECRET_KEY=$(python -c 'import secrets;print(secrets.token_hex(32))')
    python app.py            # http://127.0.0.1:6761  (login: ADMIN_USER, default "admin")

Production: `gunicorn --workers 1 --threads 4 --bind 0.0.0.0:6761 app:app`.
**Keep `--workers 1`**: search progress and the login lockout counter live in memory.
Existing databases are upgraded automatically on first start. Back up `places.db`.

## Google Places API key
The key is only needed to **search for new businesses**. Without it the app still runs: search shows "GOOGLE_PLACES_API_KEY is not set on the server", while leads you already have, **Add lead** (on the Leads page, one at a time), CSV export, campaigns and WhatsApp all keep working.

Get a key:
1. Sign in at [console.cloud.google.com](https://console.cloud.google.com) and create a project (top bar → project picker → **New project**).
2. Turn on billing for the project (**Billing** → link a billing account). Google requires a card even for usage that stays inside the free monthly allowance.
3. **APIs & Services → Library** → search **Places API (New)** → **Enable**. It must be the *New* one; the app calls `places.googleapis.com/v1`, which the legacy "Places API" does not cover.
4. **APIs & Services → Credentials → Create credentials → API key**, and copy it.
5. Click the key to restrict it:
   - **API restrictions** → *Restrict key* → tick only **Places API (New)**.
   - **Application restrictions** → *IP addresses* → add your server's public IP (find it with `curl -s ifconfig.me`).
6. Recommended: **Billing → Budgets & alerts** → add a monthly budget so you get an email before costs grow.

Add it to the server:

    sudo nano /etc/places/env                 # add or edit the line:  GOOGLE_PLACES_API_KEY=AIza...
    sudo systemctl restart places

Running by hand instead? `export GOOGLE_PLACES_API_KEY=AIza...` before `python app.py`. Check it works by running one small search (one keyword, no areas).

Cost: each result page (up to 20 businesses) is one billed request, so one keyword uses up to 3 requests and a 10-area search up to 30. Phone numbers put the request in a higher-priced tier. See Google's current [Places API pricing](https://developers.google.com/maps/billing-and-pricing/pricing) and keep `MAX_API_CALLS_PER_DAY` (default 300) as a hard daily stop.

Never commit keys or passwords. Use HTTPS in production (`COOKIE_SECURE=1`).
Phone fields are billed at a higher Google tier; check current Google Maps Platform pricing.
Contacting leads is subject to TRAI DND and data-protection rules.

## Campaigns
Pick the keywords whose leads should be messaged, write your idea, let the AI (OpenRouter) draft variants, approve the ones you want, set when to start/stop, the daily window, days and pace, then start.
- **Variants** are rotated least-used first; drafts are never sent until approved; every approved variant must contain an opt-out line (e.g. "Reply STOP to opt out.").
- **Per-person limits** (set per campaign, default **max 3 messages, at least 3 days apart**) are counted on the phone number across *all* campaigns. Test-mode and failed sends don't count.
- **Opt-out list**: numbers there are never messaged. Leads you mark "Not interested" are skipped unless you include that status.
- **Pacing**: messages per day (the campaign stops for the day once reached), messages per hour, daily window (default 10:00-19:00, always within `SEND_WINDOW_LIMIT`) and days of the week. 5 failures in a row pause a campaign.
- **Send via**: *Test mode* (simulated, nothing is sent) or *WhatsApp* (real messages through `wa-bridge`, below). Cold messaging in India is subject to TRAI and data-protection rules; check them before sending for real.
- The scheduler runs inside the app process; keep a single gunicorn worker. Set `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` (or pick a model per campaign).

Tests: `pytest`

## WhatsApp sending (whatsapp-web.js)
Unofficial: WhatsApp can ban the linked number. Use a dedicated, warmed-up SIM.

Set up the bridge once (needs Node 18+; tested on Node 24):

    cd wa-bridge && npm ci                       # installs the pinned whatsapp-web.js and a bundled Chrome
    mkdir -p /etc/wa-bridge
    TOKEN=$(python3 -c 'import secrets;print(secrets.token_hex(24))')
    printf 'WA_BRIDGE_TOKEN=%s\nUPLOAD_DIR=/root/scarper/uploads\n' "$TOKEN" > /etc/wa-bridge/env
    echo "WA_BRIDGE_TOKEN=$TOKEN" >> /etc/places/env   # the app must use the same token
    chmod 600 /etc/wa-bridge/env /etc/places/env
    cp ../deploy/wa-bridge.service ../deploy/places.service /etc/systemd/system/   # edit paths if not /root/scarper
    systemctl daemon-reload && systemctl enable --now wa-bridge places

- `wa-bridge/` is a Node service (`systemctl status wa-bridge`) on 127.0.0.1:3399; it shares `WA_BRIDGE_TOKEN` with the app.
- Open **WhatsApp** in the sidebar, scan the QR code (WhatsApp → Linked devices). **Switch number** unlinks it and shows a new QR.
- Set a campaign's *Send via* to WhatsApp. One message every random 3-4 minutes (`WA_GAP_MIN/WA_GAP_MAX` seconds), shared by all WhatsApp campaigns.
- Daily limit ramps per number: 10/day (days 0-2), 20 (3-6), 30 (7-13), 50 after. `WA_MAX_PER_DAY` caps it, `WA_WARMUP=0` disables the ramp.
- Replies such as STOP add the sender to the opt-out list. Numbers not on WhatsApp are skipped and never pause a campaign.

## Countries, time zones and replies
- Every lead is tagged with a country from its international phone number (`+1` is split into US/Canada by address). Fetching shows a per-country count; leads and results can be filtered by country.
- A campaign is created for one country (or "Any"). Its audience and keyword list only include that country's leads. Its sending window, start and stop are read in the recipients' time zone (automatic from the country, or pick one in Settings).
- Replies from leads (or from numbers you messaged) are saved on the **Replies** page. A lead that replied gets no more automatic messages; STOP-style replies also opt the number out.

## Upgrading an existing install
If you already run an older copy, pull the new code and restart both services. Your database, uploads and linked WhatsApp session are kept.

    cd /root/scarper                          # wherever you installed it
    cp places.db places.db.bak                # back up first
    git pull
    venv/bin/pip install -r requirements.txt
    cd wa-bridge && npm ci && cd ..           # REQUIRED: installs the pinned whatsapp-web.js (see below)
    systemctl restart wa-bridge places

Notes for this upgrade:
- **whatsapp-web.js is pinned to an upstream commit** (`064a3d5`), not the 1.34.7 release. WhatsApp Web renamed message and chat ids (`id._serialized` → `id.$1`) in July 2026; 1.34.7 cannot send with the new ids (`TypeError: Cannot read properties of undefined (reading 'id')`). `npm ci` installs the fixed version from `package-lock.json`. Do not run `npm update whatsapp-web.js`: that would go back to the broken release.
- **"Can't link new devices right now" on the phone**: the bridge used to report itself as Chrome 101 on macOS, which WhatsApp treats as an outdated browser. It now reports the Chrome it really runs. After upgrading, delete the cached WhatsApp Web copy so a current one is loaded: `rm -f wa-bridge/.wwebjs_cache/*.html`, then `systemctl restart wa-bridge`. If the phone still refuses, wait a few hours: too many link attempts are rate-limited by WhatsApp.
- **New settings**: compare your env files with `.env.example`. If `/etc/wa-bridge/env` or `WA_BRIDGE_TOKEN` in `/etc/places/env` is missing, follow the WhatsApp setup steps above. If you never set up the bridge, the app still works; WhatsApp sending just shows as "not running".
- **Service files** now ship in `deploy/`. If yours were written by hand, compare them with `deploy/*.service`.
- Database changes are applied automatically when the app starts.
