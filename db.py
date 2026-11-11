import datetime
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager

DB = os.environ.get("DB_PATH", "places.db")
# code -> (name, calling code, default time zone). Everything else with a phone is 'XX'; leads without a phone have country ''.
COUNTRIES = {
    "IN": ("India", "91", "Asia/Kolkata"), "US": ("United States", "1", "America/Chicago"), "CA": ("Canada", "1", "America/Toronto"),
    "GB": ("United Kingdom", "44", "Europe/London"), "AE": ("United Arab Emirates", "971", "Asia/Dubai"),
    "AU": ("Australia", "61", "Australia/Sydney"), "SG": ("Singapore", "65", "Asia/Singapore"),
    "SA": ("Saudi Arabia", "966", "Asia/Riyadh"), "DE": ("Germany", "49", "Europe/Berlin"), "FR": ("France", "33", "Europe/Paris"),
    "NZ": ("New Zealand", "64", "Pacific/Auckland"), "ZA": ("South Africa", "27", "Africa/Johannesburg"),
    "MY": ("Malaysia", "60", "Asia/Kuala_Lumpur"), "PK": ("Pakistan", "92", "Asia/Karachi"), "BD": ("Bangladesh", "880", "Asia/Dhaka"),
    "LK": ("Sri Lanka", "94", "Asia/Colombo"), "NP": ("Nepal", "977", "Asia/Kathmandu"), "IE": ("Ireland", "353", "Europe/Dublin"),
    "NG": ("Nigeria", "234", "Africa/Lagos"), "KE": ("Kenya", "254", "Africa/Nairobi"), "BR": ("Brazil", "55", "America/Sao_Paulo"),
    "MX": ("Mexico", "52", "America/Mexico_City"),
}
ANY_COUNTRY = "ANY"
_BY_CALLING = {v[1]: k for k, v in COUNTRIES.items() if k != "CA"}   # '1' -> US; Canada is told apart by address


def country_name(code):
    return {"": "No phone", "NONE": "No phone", "XX": "Other countries", ANY_COUNTRY: "Any country"}.get(code) or COUNTRIES.get(code, (code,))[0]


def country_of(intl_phone, address=""):
    """Country of a lead from its international number ('+1 936-508-2479' -> 'US'); '' when there is no number."""
    d = "".join(ch for ch in (intl_phone or "") if ch.isdigit())
    if not d:
        return ""
    for n in (3, 2, 1):
        code = _BY_CALLING.get(d[:n])
        if code:
            return "CA" if code == "US" and "canada" in (address or "").lower() else code
    return "XX"


STATUSES = ["New", "Contacted", "Interested", "Not interested", "Closed"]
COLS = ["place_id", "name", "phone", "intl_phone", "address", "rating", "rating_count", "maps_url", "country"]
# "Mobile only": drops Indian landlines (Google writes Indian mobiles as 5+5 digits, landlines as STD code + number). Numbers from other countries can't be classified, so they are kept.
MOBILE_SQL = "(p.intl_phone GLOB '+91 [6-9][0-9][0-9][0-9][0-9] [0-9][0-9][0-9][0-9][0-9]' OR (COALESCE(p.intl_phone,'') != '' AND p.intl_phone NOT GLOB '+91 *'))"
SORTS = {
    "newest": "COALESCE(p.added_at,'') DESC, p.name",
    "name": "p.name COLLATE NOCASE",
    "rating": "p.rating DESC, p.rating_count DESC",
    "reviews": "p.rating_count DESC",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches(id INTEGER PRIMARY KEY, query TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, new_count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS places(
  place_id TEXT PRIMARY KEY, name TEXT, phone TEXT, intl_phone TEXT, address TEXT,
  rating REAL, rating_count INTEGER, maps_url TEXT,
  status TEXT DEFAULT 'New', notes TEXT DEFAULT '', added_at TEXT, country TEXT);
CREATE TABLE IF NOT EXISTS search_places(search_id INTEGER, place_id TEXT, is_new INTEGER DEFAULT 1, PRIMARY KEY(search_id, place_id));
CREATE TABLE IF NOT EXISTS api_usage(day TEXT PRIMARY KEY, calls INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS campaigns(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, status TEXT DEFAULT 'draft',
  idea TEXT DEFAULT '', link TEXT DEFAULT '', language TEXT DEFAULT 'English', ai_model TEXT DEFAULT '',
  start_at TEXT, end_at TEXT, window_start TEXT DEFAULT '10:00', window_end TEXT DEFAULT '19:00', days TEXT DEFAULT '0,1,2,3,4,5',
  per_hour INTEGER DEFAULT 30, daily_cap INTEGER DEFAULT 200, max_messages INTEGER DEFAULT 3, min_gap_days INTEGER DEFAULT 3,
  statuses TEXT DEFAULT 'New', mobile_only INTEGER DEFAULT 1, mark_contacted INTEGER DEFAULT 1,
  channel TEXT DEFAULT 'test', created_at TEXT, note TEXT DEFAULT '', country TEXT DEFAULT 'ANY', tz TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS campaign_keywords(campaign_id INTEGER, keyword TEXT, PRIMARY KEY(campaign_id, keyword));
CREATE TABLE IF NOT EXISTS campaign_variants(id INTEGER PRIMARY KEY, campaign_id INTEGER, body TEXT, approved INTEGER DEFAULT 0, source TEXT DEFAULT 'manual', created_at TEXT);
CREATE TABLE IF NOT EXISTS campaign_media(id INTEGER PRIMARY KEY, campaign_id INTEGER, kind TEXT, filename TEXT, original TEXT, size INTEGER);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, campaign_id INTEGER, place_id TEXT, phone TEXT, variant_id INTEGER,
  body TEXT, status TEXT, error TEXT DEFAULT '', sent_at TEXT, UNIQUE(campaign_id, place_id));
CREATE TABLE IF NOT EXISTS replies(id INTEGER PRIMARY KEY, phone TEXT, place_id TEXT, body TEXT, received_at TEXT, is_read INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS tiffin_pushes(id INTEGER PRIMARY KEY, at TEXT, keywords TEXT, list_on_join INTEGER, free_days INTEGER, sent INTEGER, added INTEGER, skipped INTEGER, error TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS wa_numbers(number TEXT PRIMARY KEY, first_seen TEXT);
CREATE TABLE IF NOT EXISTS optouts(phone TEXT PRIMARY KEY, reason TEXT DEFAULT '', added_at TEXT);
"""


def _cols(c, table):
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


@contextmanager
def tx():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        # migrate databases created by earlier versions
        for col, ddl in (("status", "TEXT DEFAULT 'New'"), ("notes", "TEXT DEFAULT ''"), ("added_at", "TEXT")):
            if col not in _cols(c, "places"):
                c.execute(f"ALTER TABLE places ADD COLUMN {col} {ddl}")
        if "is_new" not in _cols(c, "search_places"):
            c.execute("ALTER TABLE search_places ADD COLUMN is_new INTEGER DEFAULT 1")
        for col in ("max_messages", "min_gap_days"):  # per-person contact limits, added after the campaigns table first shipped
            if col not in _cols(c, "campaigns"):
                c.execute(f"ALTER TABLE campaigns ADD COLUMN {col} INTEGER DEFAULT 3")
        for col in ("tiffin_at", "tiffin_id", "tiffin_invite"):   # set when a lead has been pushed to MyTiffin
            if col not in _cols(c, "places"):
                c.execute(f"ALTER TABLE places ADD COLUMN {col} TEXT")
        if "country" not in _cols(c, "places"):
            c.execute("ALTER TABLE places ADD COLUMN country TEXT")
        for col, ddl in (("country", "TEXT DEFAULT 'ANY'"), ("tz", "TEXT DEFAULT ''")):
            if col not in _cols(c, "campaigns"):
                c.execute(f"ALTER TABLE campaigns ADD COLUMN {col} {ddl}")
        for pid, intl, addr in c.execute("SELECT place_id, intl_phone, address FROM places WHERE country IS NULL").fetchall():
            c.execute("UPDATE places SET country=? WHERE place_id=?", (country_of(intl, addr), pid))   # tag leads saved before countries existed
        if "new_count" not in _cols(c, "searches"):
            c.execute("ALTER TABLE searches ADD COLUMN new_count INTEGER DEFAULT 0")
        yield c
        c.commit()
    finally:
        c.close()


def save(label, results):
    """Store a search. Places are upserted by place_id; status/notes of existing leads are kept.
    Returns (search_id, number_of_new_places)."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with tx() as c:
        sid = c.execute("INSERT INTO searches(query) VALUES (?)", (label,)).lastrowid
        new = 0
        upd = ",".join(f"{k}=excluded.{k}" for k in COLS[1:])
        for r in results:
            r = {**r, "country": country_of(r.get("intl_phone"), r.get("address"))}
            is_new = not c.execute("SELECT 1 FROM places WHERE place_id=?", (r["place_id"],)).fetchone()
            new += is_new
            c.execute(
                f"INSERT INTO places({','.join(COLS)},status,notes,added_at) "
                f"VALUES ({','.join('?' * len(COLS))},'New','',?) ON CONFLICT(place_id) DO UPDATE SET {upd}",
                [r.get(k) for k in COLS] + [now])
            c.execute("INSERT OR IGNORE INTO search_places VALUES (?,?,?)", (sid, r["place_id"], int(is_new)))
        c.execute("UPDATE searches SET new_count=? WHERE id=?", (new, sid))
    return sid, new


def _digits(s):
    return "".join(ch for ch in (s or "") if ch.isdigit())


def clean_phone(raw, default_cc="91"):
    """Any way people write a number -> digits with country code ('919876543210'), or '' if it can't be one.

    Handles spaces, dashes, dots, brackets, '+', '00'/'0' prefixes, '(0)' trunk digits, extensions ('x12'),
    several numbers in one cell (first is used) and numbers with no country code (default_cc is added)."""
    s = re.split(r"\s*(?:/|,|;|\n|\bor\b|\bext\.?|\bx(?=\s*\d)|#)\s*", str(raw or "").strip(), maxsplit=1, flags=re.I)[0]
    s = re.sub(r"\(\s*0\s*\)", "", s)                       # +91 (0) 98765... -> trunk zero written in brackets
    if re.search(r"[A-Za-z]", s):                              # '98765 43210 call after 5' etc: don't guess
        return ""
    d = _digits(s).lstrip("0")                                  # 0098..., 098... : no country code starts with 0
    if len(d) == 10:                                            # national number without country code
        d = default_cc + d
    return d if 11 <= len(d) <= 15 and not (d.startswith("91") and len(d) != 12) else ""


def format_phone(digits):
    """Canonical international form, same shape Google returns: '+91 98765 43210'."""
    if not digits:
        return ""
    if digits.startswith("91"):
        rest = digits[2:]
        return f"+91 {rest[:5]} {rest[5:]}" if rest[0] in "6789" else f"+91 {rest}"
    if digits.startswith("1") and len(digits) == 11:  # US / Canada
        return f"+1 {digits[1:4]} {digits[4:7]} {digits[7:]}"
    return "+" + digits


def add_manual(name, phone, address="", notes="", keyword="Manual leads"):
    """Add one lead by hand under a keyword. Returns (place_id or None, error)."""
    name, keyword = name.strip()[:120], (keyword.strip() or "Manual leads")[:80]
    intl = format_phone(clean_phone(phone))
    if not name:
        return None, "Enter the business name."
    if not intl:
        return None, "Enter a valid phone number (10 digits, or with country code)."
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with tx() as c:
        for r in c.execute("SELECT name, intl_phone FROM places WHERE COALESCE(intl_phone,'') != ''"):
            if _digits(r["intl_phone"]) == _digits(intl):
                return None, f"{intl} is already in your leads as “{r['name']}”."
        pid = "manual-" + secrets.token_hex(6)
        c.execute("INSERT INTO places(place_id,name,phone,intl_phone,address,rating,rating_count,maps_url,status,notes,added_at,country) "
                  "VALUES (?,?,?,?,?,NULL,NULL,'','New',?,?,?)",
                  (pid, name, intl.replace("+91 ", "0", 1) if intl.startswith("+91 ") else intl, intl, address.strip()[:300],
                   notes.strip()[:2000], now, country_of(intl, address)))
        row = c.execute("SELECT id FROM searches WHERE query=? ORDER BY id DESC LIMIT 1", (keyword,)).fetchone()
        sid = row["id"] if row else c.execute("INSERT INTO searches(query) VALUES (?)", (keyword,)).lastrowid
        c.execute("INSERT INTO search_places VALUES (?,?,1)", (sid, pid))
        c.execute("UPDATE searches SET new_count=COALESCE(new_count,0)+1 WHERE id=?", (sid,))
    return pid, ""


def search_info(sid):
    with tx() as c:
        r = c.execute("SELECT * FROM searches WHERE id=?", (sid,)).fetchone()
        total = c.execute("SELECT COUNT(*) FROM search_places WHERE search_id=?", (sid,)).fetchone()[0]
    if not r:
        return None
    return {**dict(r), "total": total, "known": total - (r["new_count"] or 0)}


def _where(text, status, phone, mobile, search_id, new_only=False, keyword="", country=""):
    w, a = ["1=1"], []
    if country:
        w.append("COALESCE(p.country,'')=?")
        a.append("" if country == "NONE" else country)
    if search_id is not None:
        w.append("p.place_id IN (SELECT place_id FROM search_places WHERE search_id=?"
                 + (" AND is_new=1)" if new_only else ")"))
        a.append(search_id)
    if keyword:
        w.append("p.place_id IN (SELECT sp.place_id FROM search_places sp JOIN searches s ON s.id=sp.search_id WHERE s.query=?)")
        a.append(keyword)
    if text:
        w.append("(p.name LIKE ? OR p.address LIKE ? OR p.phone LIKE ? OR p.notes LIKE ? OR p.place_id IN "
                 "(SELECT sp.place_id FROM search_places sp JOIN searches s ON s.id=sp.search_id WHERE s.query LIKE ?))")
        a += [f"%{text}%"] * 5
    if status in STATUSES:
        w.append("p.status=?")
        a.append(status)
    if phone:
        w.append("COALESCE(p.phone,'') != ''")
    if mobile:
        w.append(MOBILE_SQL)
    return " AND ".join(w), a


def query_places(text="", status="", phone=False, mobile=False, sort="newest",
                 page=1, per_page=50, search_id=None, new_only=False, keyword="", country=""):
    """Return (rows, total). per_page=None returns everything (for export)."""
    where, args = _where(text, status, phone, mobile, search_id, new_only, keyword, country)
    order = SORTS.get(sort, SORTS["newest"])
    sql = (f"SELECT p.*, (SELECT COUNT(*) FROM replies rp WHERE rp.place_id=p.place_id) AS replied, (SELECT GROUP_CONCAT(q, char(31)) FROM (SELECT DISTINCT s.query AS q FROM search_places sp "
           f"JOIN searches s ON s.id=sp.search_id WHERE sp.place_id=p.place_id)) AS tags "
           f"FROM places p WHERE {where} ORDER BY {order}")
    with tx() as c:
        total = c.execute(f"SELECT COUNT(*) FROM places p WHERE {where}", args).fetchone()[0]
        if per_page:
            sql += " LIMIT ? OFFSET ?"
            args = args + [per_page, (max(page, 1) - 1) * per_page]
        rows = [dict(r) for r in c.execute(sql, args)]
    for r in rows:
        r["tags"] = r["tags"].split("\x1f") if r["tags"] else []
    return rows, total


def keywords(country=None):
    """Every keyword searched so far with the number of distinct leads it found (only leads of `country` when given), newest first."""
    cond, args = ("AND p.country=?", [country]) if country and country != ANY_COUNTRY else ("", [])
    with tx() as c:
        return [dict(r) for r in c.execute(
            "SELECT s.query AS keyword, COUNT(DISTINCT sp.place_id) AS n FROM searches s "
            "JOIN search_places sp ON sp.search_id=s.id JOIN places p ON p.place_id=sp.place_id "
            f"WHERE 1=1 {cond} GROUP BY s.query ORDER BY MAX(s.id) DESC", args)]


def countries(search_id=None, new_only=False):
    """Lead count per country: [{'code', 'name', 'n'}], biggest first. Leads without a phone are code 'NONE'."""
    where, args = "", []
    if search_id is not None:
        where = "WHERE p.place_id IN (SELECT place_id FROM search_places WHERE search_id=?" + (" AND is_new=1)" if new_only else ")")
        args = [search_id]
    with tx() as c:
        rows = c.execute(f"SELECT COALESCE(p.country,'') AS k, COUNT(*) AS n FROM places p {where} GROUP BY k ORDER BY n DESC", args).fetchall()
    return [{"code": r["k"] or "NONE", "name": country_name(r["k"]), "n": r["n"]} for r in rows]


def count_countries(rows):
    """Same shape as countries(), for search results that are not saved yet."""
    cnt = {}
    for r in rows:
        k = country_of(r.get("intl_phone"), r.get("address")) or "NONE"
        cnt[k] = cnt.get(k, 0) + 1
    return [{"code": k, "name": country_name(k), "n": n} for k, n in sorted(cnt.items(), key=lambda kv: -kv[1])]


def history(limit=20, offset=0, text=""):
    where, args = ("WHERE s.query LIKE ?", [f"%{text}%"]) if text else ("", [])
    with tx() as c:
        return [dict(r) for r in c.execute(
            "SELECT s.id, s.query, s.created_at, s.new_count, COUNT(sp.place_id) n FROM searches s "
            f"LEFT JOIN search_places sp ON sp.search_id=s.id {where} GROUP BY s.id ORDER BY s.id DESC LIMIT ? OFFSET ?",
            args + [limit, offset])]


def count_searches(text=""):
    with tx() as c:
        if text:
            return c.execute("SELECT COUNT(*) FROM searches WHERE query LIKE ?", (f"%{text}%",)).fetchone()[0]
        return c.execute("SELECT COUNT(*) FROM searches").fetchone()[0]


def stats():
    with tx() as c:
        one = lambda sql: c.execute(sql).fetchone()[0]
        by = {r[0] or "New": r[1] for r in c.execute("SELECT status, COUNT(*) FROM places GROUP BY status")}
        return {
            "total": one("SELECT COUNT(*) FROM places"),
            "with_phone": one("SELECT COUNT(*) FROM places WHERE COALESCE(phone,'') != ''"),
            "mobile": one(f"SELECT COUNT(*) FROM places p WHERE {MOBILE_SQL}"),
            "searches": one("SELECT COUNT(*) FROM searches"),
            "by_status": {s: by.get(s, 0) for s in STATUSES},
        }


def update_lead(place_id, status=None, notes=None):
    with tx() as c:
        if status is not None:
            c.execute("UPDATE places SET status=? WHERE place_id=?", (status, place_id))
        if notes is not None:
            c.execute("UPDATE places SET notes=? WHERE place_id=?", (notes, place_id))
        return c.total_changes > 0


def delete_search(sid):
    """Delete a search; leads that belong to no other search are removed unless worked on."""
    with tx() as c:
        c.execute("DELETE FROM search_places WHERE search_id=?", (sid,))
        c.execute("DELETE FROM searches WHERE id=?", (sid,))
        c.execute("DELETE FROM places WHERE place_id NOT IN (SELECT place_id FROM search_places) "
                  "AND COALESCE(status,'New')='New' AND COALESCE(notes,'')=''")


def add_reply(phone, place_id, body):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with tx() as c:
        c.execute("INSERT INTO replies(phone, place_id, body, received_at) VALUES (?,?,?,?)", (phone, place_id, body[:500], now))


def lead_for_phone(phone):
    """(known, place_id): known when the number is a lead or we have messaged it; place_id may be None."""
    with tx() as c:
        r = c.execute("SELECT place_id FROM places WHERE replace(replace(replace(COALESCE(intl_phone,''),' ',''),'+',''),'-','')=? LIMIT 1", (phone,)).fetchone()
        sent = c.execute("SELECT 1 FROM messages WHERE phone=? LIMIT 1", (phone,)).fetchone()
    return bool(r or sent), (r[0] if r else None)


def replies(page=1, per=25, unread_only=False):
    where = "WHERE r.is_read=0" if unread_only else ""
    with tx() as c:
        total = c.execute(f"SELECT COUNT(*) FROM replies r {where}").fetchone()[0]
        rows = [dict(r) for r in c.execute(
            "SELECT r.*, p.name, p.status, p.country FROM replies r LEFT JOIN places p ON p.place_id=r.place_id "
            f"{where} ORDER BY r.id DESC LIMIT ? OFFSET ?", (per, (max(page, 1) - 1) * per))]
    return rows, total


def unread_replies():
    with tx() as c:
        return c.execute("SELECT COUNT(*) FROM replies WHERE is_read=0").fetchone()[0]


def mark_read(rid=None):
    with tx() as c:
        if rid is None:
            c.execute("UPDATE replies SET is_read=1")
        else:
            c.execute("UPDATE replies SET is_read=1 WHERE id=?", (rid,))


def usage_today():
    with tx() as c:
        r = c.execute("SELECT calls FROM api_usage WHERE day=date('now')").fetchone()
    return r[0] if r else 0


def add_usage(n=1):
    with tx() as c:
        c.execute("INSERT INTO api_usage(day, calls) VALUES (date('now'), ?) "
                  "ON CONFLICT(day) DO UPDATE SET calls=calls+?", (n, n))
