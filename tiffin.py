"""Push the leads of chosen searches to the MyTiffin directory (POST /api/v1/kitchens).

One call carries up to 500 kitchens; listOnJoin and freeDays are the same for the whole push. MyTiffin skips phones it already
knows and answers with an invite link per kitchen, which is stored on the lead. Only Indian mobile leads are sent.
"""
import os
import re

import requests

import camp
import db

URL = os.environ.get("MYTIFFIN_URL", "https://mytiffin.online")
BATCH = 500
STATES = {x.lower() for x in (
    "Andhra Pradesh,Arunachal Pradesh,Assam,Bihar,Chhattisgarh,Goa,Gujarat,Haryana,Himachal Pradesh,Jharkhand,Karnataka,Kerala,"
    "Madhya Pradesh,Maharashtra,Manipur,Meghalaya,Mizoram,Nagaland,Odisha,Punjab,Rajasthan,Sikkim,Tamil Nadu,Telangana,Tripura,"
    "Uttar Pradesh,Uttarakhand,West Bengal,Delhi,Chandigarh,Puducherry,Jammu and Kashmir,Ladakh").split(",")}
STATES_NOISE = re.compile(r"^(india|bharat)$", re.I)


def configured():
    return bool(os.environ.get("MYTIFFIN_API_KEY"))


def split_address(address):
    """'Shop 4, Sector 56, Gurugram, Haryana 122011, India' -> (area, city, pincode). Best effort: Google's addresses vary."""
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    if parts and STATES_NOISE.match(parts[-1]):
        parts.pop()
    pin = ""
    if parts:
        m = re.search(r"\b(\d{3}\s?\d{3})\b", parts[-1])
        if m:
            pin = m.group(1).replace(" ", "")
            if parts[-1].strip() == m.group(1):          # the pincode stood alone: drop it, the state comes before
                parts.pop()
            else:
                parts[-1] = parts[-1].replace(m.group(1), "").strip()
    if parts and (pin or parts[-1].lower() in STATES):
        parts.pop()                                       # the state ("Haryana")
    city = parts[-1] if parts else ""
    area = parts[-2] if len(parts) >= 2 else ""
    return area[:60], city[:60], pin


def kitchen(lead):
    area, city, pin = split_address(lead.get("address"))
    return {"name": lead["name"], "phone": lead["intl_phone"], "address": (lead.get("address") or "")[:250],
            "area": area, "city": city, "pincode": pin}


ELIGIBLE = ("p.country='IN' AND " + db.MOBILE_SQL + " AND p.tiffin_at IS NULL AND COALESCE(p.name,'') != '' "
            "AND NOT EXISTS (SELECT 1 FROM optouts o WHERE o.phone = replace(replace(replace(COALESCE(p.intl_phone,''),' ',''),'+',''),'-',''))")


def overview(keywords=None):
    """Per keyword: all leads, ready to send, already in MyTiffin. With `keywords`, only those."""
    ks = [k for k in db.keywords() if keywords is None or k["keyword"] in keywords]
    with db.tx() as c:
        for k in ks:
            base = ("FROM places p WHERE p.place_id IN (SELECT sp.place_id FROM search_places sp JOIN searches s ON s.id=sp.search_id WHERE s.query=?)")
            k["ready"] = c.execute(f"SELECT COUNT(*) {base} AND {ELIGIBLE}", (k["keyword"],)).fetchone()[0]
            k["pushed"] = c.execute(f"SELECT COUNT(*) {base} AND p.tiffin_at IS NOT NULL", (k["keyword"],)).fetchone()[0]
    return ks


def pending(keywords):
    """Leads of these keywords that would be sent, each once, oldest first."""
    if not keywords:
        return []
    q = ",".join("?" * len(keywords))
    with db.tx() as c:
        return [dict(r) for r in c.execute(
            f"SELECT p.place_id, p.name, p.intl_phone, p.address FROM places p WHERE {ELIGIBLE} AND p.place_id IN "
            f"(SELECT sp.place_id FROM search_places sp JOIN searches s ON s.id=sp.search_id WHERE s.query IN ({q})) "
            "ORDER BY p.added_at, p.place_id", list(keywords))]


def _key10(phone):
    return re.sub(r"\D", "", phone or "")[-10:]


def push(keywords, list_on_join, free_days, session=None):
    """Send every pending lead of the chosen keywords. Returns a summary dict; never raises on API errors."""
    key = os.environ.get("MYTIFFIN_API_KEY", "")
    leads = pending(keywords)
    out = {"sent": len(leads), "added": 0, "skipped": [], "error": "", "linked": 0}
    free_days = max(0, min(365, int(free_days)))
    for i in range(0, len(leads), BATCH):
        chunk = leads[i:i + BATCH]
        body = {"listOnJoin": bool(list_on_join), "freeDays": free_days, "kitchens": [kitchen(x) for x in chunk]}
        try:
            r = (session or requests).post(f"{URL}/api/v1/kitchens", json=body, timeout=60,
                                           headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        except requests.RequestException as e:
            out["error"] = f"Could not reach MyTiffin ({type(e).__name__}). Nothing in this batch was marked."
            break
        if r.status_code != 200:
            msg = ""
            try:
                msg = r.json().get("error") or r.json().get("message") or ""
            except ValueError:
                pass
            out["error"] = {401: "MyTiffin rejected the API key.", 429: "MyTiffin says too many requests. Try again later."}.get(r.status_code) or f"MyTiffin error {r.status_code}: {msg}"[:200]
            break
        data = r.json()
        out["added"] += data.get("added", 0)
        out["skipped"] += data.get("skipped", [])
        by_phone = {_key10(k.get("phone")): k for k in data.get("kitchens", [])}
        now = camp.fmt(camp.utcnow())
        with db.tx() as t:
            for lead in chunk:
                k = by_phone.get(_key10(lead["intl_phone"]))
                if k:                                      # added now, or it was already known to MyTiffin
                    t.execute("UPDATE places SET tiffin_at=?, tiffin_id=?, tiffin_invite=? WHERE place_id=?",
                              (now, k.get("id", ""), k.get("invite") or "", lead["place_id"]))
                    out["linked"] += 1
    if out["linked"] or out["error"] == "":
        with db.tx() as t:
            t.execute("INSERT INTO tiffin_pushes(at, keywords, list_on_join, free_days, sent, added, skipped, error) VALUES (?,?,?,?,?,?,?,?)",
                      (camp.fmt(camp.utcnow()), "; ".join(keywords), int(bool(list_on_join)), free_days, out["sent"], out["added"],
                       len(out["skipped"]), out["error"]))
    return out


def history(n=10):
    with db.tx() as t:
        return [dict(r) for r in t.execute("SELECT * FROM tiffin_pushes ORDER BY id DESC LIMIT ?", (n,))]
