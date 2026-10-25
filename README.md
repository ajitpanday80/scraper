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

Never commit keys or passwords. Use HTTPS in production (`COOKIE_SECURE=1`).
Phone fields are billed at a higher Google tier; check current Google Maps Platform pricing.
Contacting leads is subject to TRAI DND and data-protection rules.

## Campaigns (test mode)
Pick the keywords whose leads should be messaged, write your idea, let the AI (OpenRouter) draft variants, approve the ones you want, set when to start/stop, the daily window, days and pace, then start.
- **Variants** are rotated least-used first; drafts are never sent until approved; every approved variant must contain an opt-out line (e.g. "Reply STOP to opt out.").
- **Per-person limits** (set per campaign, default **max 3 messages, at least 3 days apart**) are counted on the phone number across *all* campaigns. Test-mode and failed sends don't count.
- **Opt-out list**: numbers there are never messaged. Leads you mark "Not interested" are skipped unless you include that status.
- **Pacing**: messages per hour, a daily cap, daily window (default 10:00-19:00, always within `SEND_WINDOW_LIMIT`) and days of the week. 5 failures in a row pause a campaign.
- **Sending is simulated for now** (`senders.py`): nothing is sent to anyone. A real provider must be added there. On the official WhatsApp Business API and DLT-registered SMS in India, cold messages need pre-approved templates and recipient consent; check the current rules before enabling one.
- The scheduler runs inside the app process; keep a single gunicorn worker. Set `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` (or pick a model per campaign).

Tests: `pytest`
