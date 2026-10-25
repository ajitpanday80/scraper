# Places search

Search box -> Google Places API (New) Text Search -> SQLite + CSV (name, phone, address, rating).

    pip install -r requirements.txt
    export GOOGLE_PLACES_API_KEY=...   # key must allow "Places API (New)"
    python app.py                      # http://localhost:5000

Notes: Text Search returns ~20 results/page, and Google caps a query at about 60 results.
For more coverage, run narrower queries (e.g. per sector/area); places are deduped by place_id.
Phone fields are billed at a higher tier -- check current Google Maps Platform pricing.
Run tests: `pytest`
