"""Google Maps place scraper: Playwright browser routed through a Decodo proxy."""
import os
import random
import re
import time
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright

PLACE_LINK = 'a[href*="/maps/place/"]'


def proxy_config():
    user = os.environ.get("DECODO_USER")
    if not user:
        return None
    return {
        "server": os.environ.get("DECODO_PROXY", "https://gate.decodo.com:7000"),
        "username": user,
        "password": os.environ.get("DECODO_PASS", ""),
    }


def _pause(lo=0.8, hi=2.2):
    time.sleep(random.uniform(lo, hi))


def _dismiss_consent(page):
    for label in ("Accept all", "Reject all", "I agree"):
        btn = page.locator(f'button:has-text("{label}")').first
        if btn.count():
            try:
                btn.click(timeout=3000)
                return
            except Exception:
                pass


def extract_place(page):
    """Read name/phone/address from a loaded place detail page."""
    name = ""
    h1 = page.locator("h1").first
    if h1.count():
        name = h1.inner_text().strip()

    address = ""
    addr = page.locator('button[data-item-id="address"]').first
    if addr.count():
        address = (addr.get_attribute("aria-label") or addr.inner_text()).strip()
        address = re.sub(r"^Address:\s*", "", address)

    phone = ""
    ph = page.locator('button[data-item-id^="phone:tel:"]').first
    if ph.count():
        phone = (ph.get_attribute("data-item-id") or "").split("phone:tel:")[-1]
        if not phone:
            phone = re.sub(r"^Phone:\s*", "", ph.get_attribute("aria-label") or "")
    return {"name": name, "phone": phone, "address": address}


def _collect_links(page, limit):
    feed = page.locator('div[role="feed"]').first
    try:
        feed.wait_for(timeout=15000)
    except Exception:
        return []  # single-result redirect or blocked
    seen, stagnant = [], 0
    while len(seen) < limit and stagnant < 3:
        hrefs = page.eval_on_selector_all(PLACE_LINK, "els => els.map(e => e.href)")
        new = [h for h in dict.fromkeys(hrefs) if h not in seen]
        seen.extend(new)
        stagnant = 0 if new else stagnant + 1
        if page.get_by_text("You've reached the end of the list").count():
            break
        feed.evaluate("el => el.scrollTo(0, el.scrollHeight)")
        _pause(1.0, 2.0)
    return seen[:limit]


def scrape(query, limit=20, headless=True, on_progress=None):
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, proxy=proxy_config())
        ctx = browser.new_context(locale="en-IN", viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        page.goto(f"https://www.google.com/maps/search/{quote_plus(query)}?hl=en", timeout=60000)
        _dismiss_consent(page)

        links = _collect_links(page, limit)
        if not links and page.locator("h1").count():
            results.append({**extract_place(page), "maps_url": page.url})
        for i, url in enumerate(links, 1):
            _pause()
            try:
                page.goto(url, timeout=45000)
                page.locator("h1").first.wait_for(timeout=15000)
                results.append({**extract_place(page), "maps_url": url})
            except Exception:
                pass  # skip failed place, keep going
            if on_progress:
                on_progress(i, len(links), results)
        browser.close()
    return results
