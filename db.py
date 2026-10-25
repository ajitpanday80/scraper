import sqlite3

DB = "places.db"
COLS = ["place_id", "name", "phone", "intl_phone", "address", "rating", "rating_count", "maps_url"]


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE IF NOT EXISTS searches(id INTEGER PRIMARY KEY, query TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS places(
      place_id TEXT PRIMARY KEY, name TEXT, phone TEXT, intl_phone TEXT, address TEXT,
      rating REAL, rating_count INTEGER, maps_url TEXT);
    CREATE TABLE IF NOT EXISTS search_places(search_id INTEGER, place_id TEXT, PRIMARY KEY(search_id, place_id));
    """)
    return c


def save(query, results):
    """Store a search. Places are deduplicated globally by place_id."""
    with conn() as c:
        sid = c.execute("INSERT INTO searches(query) VALUES (?)", (query,)).lastrowid
        for r in results:
            c.execute(f"INSERT OR REPLACE INTO places({','.join(COLS)}) VALUES ({','.join('?'*len(COLS))})",
                      [r[k] for k in COLS])
            c.execute("INSERT OR IGNORE INTO search_places VALUES (?,?)", (sid, r["place_id"]))
    return sid


def get(sid):
    with conn() as c:
        q = c.execute("SELECT query FROM searches WHERE id=?", (sid,)).fetchone()
        rows = c.execute("SELECT p.* FROM places p JOIN search_places s USING(place_id) WHERE s.search_id=?", (sid,)).fetchall()
    return (q["query"] if q else None), [dict(r) for r in rows]


def history():
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT s.id, s.query, s.created_at, COUNT(sp.place_id) n FROM searches s "
            "LEFT JOIN search_places sp ON sp.search_id=s.id GROUP BY s.id ORDER BY s.id DESC")]
