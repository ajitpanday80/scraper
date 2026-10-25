import os
from playwright.sync_api import sync_playwright

from scraper.maps import extract_place

HTML = """<h1>Tiffmon Tiffin service</h1>
<button data-item-id="address" aria-label="Address: phase 3, u-16/32, Gurugram">x</button>
<button data-item-id="phone:tel:08307938948" aria-label="Phone: 083079 38948">x</button>"""


def test_extract_place():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH"))
        page = b.new_page()
        page.set_content(HTML)
        assert extract_place(page) == {
            "name": "Tiffmon Tiffin service",
            "phone": "08307938948",
            "address": "phase 3, u-16/32, Gurugram",
        }
        b.close()
