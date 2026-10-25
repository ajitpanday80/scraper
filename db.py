import datetime
import os
import sqlite3
from contextlib import contextmanager

DB = os.environ.get("DB_PATH", "places.db")
STATUSES = ["New", "Contacted", "Interested", "Not interested", "Closed"]
COLS = ["place_id", "name", "phone", "intl_phone", "address", "rating", "rating_count", "maps_url"]
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
  status TEXT DEFAULT 'New', notes TEXT DEFAULT '', added_at TEXT);
CREATE TABLE IF NOT EXISTS search_places(search_id INTEGER, place_id TEXT, is_new INTEGER DEFAULT 1, PRIMARY KEY(search_id, place_id));
CREATE TABLE IF NOT EXISTS api_usage(day TEXT PRIMARY KEY, calls INTEGER DEFAULT 0);
"""


def _cols(c, table):
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


@contextmanager
def tx():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        c.executescript(SCHEMA)
        # migrate databases created by earlier versions
        for col, ddl in (("status", "TEXT DEFAULT 'New'"), ("notes", "TEXT DEFAULT ''"), ("added_at", "TEXT")):
            if col not in _cols(c, "places"):
                c.execute(f"ALTER TABLE places ADD COLUMN {col} {ddl}")
        if "is_new" not in _cols(c, "search_places"):
            c.execute("ALTER TABLE search_places ADD COLUMN is_new INTEGER DEFAULT 1")
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
            is_new = not c.execute("SELECT 1 FROM places WHERE place_id=?", (r["place_id"],)).fetchone()
            new += is_new
            c.execute(
                f"INSERT INTO places({','.join(COLS)},status,notes,added_at) "
                f"VALUES ({','.join('?' * len(COLS))},'New','',?) ON CONFLICT(place_id) DO UPDATE SET {upd}",
                [r.get(k) for k in COLS] + [now])
            c.execute("INSERT OR IGNORE INTO search_places VALUES (?,?,?)", (sid, r["place_id"], int(is_new)))
        c.execute("UPDATE searches SET new_count=? WHERE id=?", (new, sid))
    return sid, new


def search_info(sid):
    with tx() as c:
        r = c.execute("SELECT * FROM searches WHERE id=?", (sid,)).fetchone()
        total = c.execute("SELECT COUNT(*) FROM search_places WHERE search_id=?", (sid,)).fetchone()[0]
    if not r:
        return None
    return {**dict(r), "total": total, "known": total - (r["new_count"] or 0)}


def _where(text, status, phone, mobile, search_id, new_only=False, keyword=""):
    w, a = ["1=1"], []
    if search_id is not None:
        w.append("p.place_id IN (SELECT place_id FROM search_places WHERE search_id=?"
                 + (" AND is_new=1)" if new_only else ")"))
        a.append(search_id)
    if keyword:
        w.append("p.place_id IN (SELECT sp.place_id FROM search_places sp JOIN searches s ON s.id=sp.search_id WHERE s.query=?)")
        a.append(keyword)
    if text:
        w.append("(p.name LIKE ? OR p.address LIKE ? OR p.phone LIKE ? OR p.notes LIKE ?)")
        a += [f"%{text}%"] * 4
    if status in STATUSES:
        w.append("p.status=?")
        a.append(status)
    if phone:
        w.append("COALESCE(p.phone,'') != ''")
    if mobile:
        w.append("p.intl_phone GLOB '+91 [6-9]*'")
    return " AND ".join(w), a


def query_places(text="", status="", phone=False, mobile=False, sort="newest",
                 page=1, per_page=50, search_id=None, new_only=False, keyword=""):
    """Return (rows, total). per_page=None returns everything (for export)."""
    where, args = _where(text, status, phone, mobile, search_id, new_only, keyword)
    order = SORTS.get(sort, SORTS["newest"])
    sql = (f"SELECT p.*, (SELECT GROUP_CONCAT(q, char(31)) FROM (SELECT DISTINCT s.query AS q FROM search_places sp "
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


def keywords():
    """Every keyword searched so far with the number of distinct leads it found, newest first."""
    with tx() as c:
        return [dict(r) for r in c.execute(
            "SELECT s.query AS keyword, COUNT(DISTINCT sp.place_id) AS n FROM searches s "
            "JOIN search_places sp ON sp.search_id=s.id GROUP BY s.query ORDER BY MAX(s.id) DESC")]


def history(limit=20):
    with tx() as c:
        return [dict(r) for r in c.execute(
            "SELECT s.id, s.query, s.created_at, s.new_count, COUNT(sp.place_id) n FROM searches s "
            "LEFT JOIN search_places sp ON sp.search_id=s.id GROUP BY s.id ORDER BY s.id DESC LIMIT ?", (limit,))]


def stats():
    with tx() as c:
        one = lambda sql: c.execute(sql).fetchone()[0]
        by = {r[0] or "New": r[1] for r in c.execute("SELECT status, COUNT(*) FROM places GROUP BY status")}
        return {
            "total": one("SELECT COUNT(*) FROM places"),
            "with_phone": one("SELECT COUNT(*) FROM places WHERE COALESCE(phone,'') != ''"),
            "mobile": one("SELECT COUNT(*) FROM places WHERE intl_phone GLOB '+91 [6-9]*'"),
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


def usage_today():
    with tx() as c:
        r = c.execute("SELECT calls FROM api_usage WHERE day=date('now')").fetchone()
    return r[0] if r else 0


def add_usage(n=1):
    with tx() as c:
        c.execute("INSERT INTO api_usage(day, calls) VALUES (date('now'), ?) "
                  "ON CONFLICT(day) DO UPDATE SET calls=calls+?", (n, n))
