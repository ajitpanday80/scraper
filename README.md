# Places admin

Admin-only web app: enter a keyword, fetch businesses via Google Places API (New), store in SQLite, export CSV.

    pip install -r requirements.txt
    export GOOGLE_PLACES_API_KEY=...  ADMIN_PASSWORD=...  SECRET_KEY=$(python -c 'import secrets;print(secrets.token_hex(32))')
    python app.py        # http://127.0.0.1:6761  (login: ADMIN_USER, default "admin")

Never commit the password or API key. Serve behind HTTPS in production (set COOKIE_SECURE=1);
for a public deployment use a real WSGI server (e.g. gunicorn), not `python app.py`.
Google caps one query at ~60 results; use narrower keywords for more. Phone fields are billed at a higher tier.
Tests: `pytest`
