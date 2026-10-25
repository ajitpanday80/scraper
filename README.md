# Maps lead scraper

Search box -> Google Maps results -> name / phone / address table + CSV.
Browser traffic goes through a Decodo proxy.

## Setup
    pip install -r requirements.txt && playwright install chromium
    cp .env.example .env   # fill Decodo creds, then: export $(cat .env | xargs)
    python app.py          # http://localhost:5000

## Caveats
- Untested against live Google/Decodo (no credentials in dev). Selectors on the
  Maps *detail page* (`data-item-id` attrs) are the most stable parts but Google changes markup.
- Decodo proxy host/port/username format: verify in your dashboard. For India-located
  results, use a India-geo username per Decodo docs.
- Google Maps ToS prohibit scraping; collecting phone numbers for outreach may be
  subject to India's DPDP Act / TRAI DND rules. Your responsibility.
- Run tests: `CHROMIUM_PATH=/path/to/chromium pytest`
