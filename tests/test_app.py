import pytest

import app as appmod
import db
import places


def P(pid, name, phone="", intl="", **kw):
    return {"place_id": pid, "name": name, "phone": phone, "intl_phone": intl, "address": "Gurugram",
            "rating": 4.5, "rating_count": 10, "maps_url": "", **kw}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", "pw-test")
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "k")
    appmod._fails.clear()
    appmod.JOBS.clear()
    return appmod.app.test_client()


def token(c):
    c.get("/login")
    with c.session_transaction() as s:
        return s["csrf"]


def login(c, pw="pw-test"):
    return c.post("/login", data={"username": "admin", "password": pw, "csrf": token(c)})


def post(c, url, **data):
    return c.post(url, data={**data, "csrf": token(c)})


def test_requires_login(client):
    for url in ("/", "/leads", "/leads.csv", "/search/1", "/job/x.json"):
        assert client.get(url).status_code == 302


def test_bad_password_lockout_and_csrf(client):
    for _ in range(5):
        assert login(client, "wrong").status_code == 401
    assert login(client, "pw-test").status_code == 429
    assert client.post("/login", data={"username": "admin", "password": "pw-test"}).status_code == 400


def test_job_saves_and_dedupes_across_areas(client, monkeypatch):
    def fake(q, key, on_request=None):
        return [P("a", "Tiffmon", "083079 38948", "+91 83079 38948")] + ([P("b", "Jain", "09717", "+91 97171 26122")] if "56" in q else [])
    monkeypatch.setattr(appmod, "search", fake)
    appmod.JOBS["j"] = {"status": "running", "label": "tiffin · 2 areas", "done": 0, "total": 2, "found": 0, "sid": None, "new": 0, "message": ""}
    appmod.run_job("j", "tiffin · 2 areas", ["tiffin in Sector 56", "tiffin in Sector 40"])
    j = appmod.JOBS["j"]
    assert j["status"] == "done" and j["found"] == 2 and j["new"] == 2 and j["done"] == 2
    login(client)
    page = client.get(f"/search/{j['sid']}").get_data(as_text=True)
    assert "Tiffmon" in page and "wa.me/918307938948" in page and "2 new leads" in page


def test_search_route_starts_job(client, monkeypatch):
    monkeypatch.setattr(appmod, "search", lambda q, key, on_request=None: [P("a", "X")])
    login(client)
    r = post(client, "/search", q="tiffin", areas="A\nA\nB")
    assert r.status_code == 302 and "/job/" in r.headers["Location"]
    (jid, job), = appmod.JOBS.items()
    assert job["total"] == 2 and job["label"] == "tiffin · 2 areas"


def test_lead_tracking_filters_and_export(client):
    db.save("s", [P("a", "Alpha", "1", "+91 98765 43210"), P("b", "Beta", "2", "+91 11 2345 6789"), P("c", "Gamma")])
    login(client)
    t = token(client)
    r = client.post("/lead/a", json={"status": "Interested", "notes": "call Monday"}, headers={"X-CSRF": t})
    assert r.status_code == 200
    assert client.post("/lead/a", json={"status": "Bogus"}, headers={"X-CSRF": t}).status_code == 400
    assert client.post("/lead/nope", json={"status": "Closed"}, headers={"X-CSRF": t}).status_code == 404
    assert client.post("/lead/a", json={"status": "Closed"}).status_code == 400  # no CSRF
    names = lambda q: [x["name"] for x in db.query_places(**q)[0]]
    assert names({"status": "Interested"}) == ["Alpha"]
    assert names({"mobile": True}) == ["Alpha"]            # Beta is a Delhi landline
    assert sorted(names({"phone": True})) == ["Alpha", "Beta"]
    assert names({"text": "monday"}) == ["Alpha"]          # notes are searchable
    body = client.get("/leads.csv?status=Interested").get_data()
    assert body.startswith(b"\xef\xbb\xbf") and b"Alpha" in body and b"call Monday" in body and b"Beta" not in body


def test_resave_keeps_lead_state(client):
    db.save("s1", [P("a", "Alpha")])
    db.update_lead("a", "Contacted", "hi")
    sid, new = db.save("s2", [P("a", "Alpha Renamed")])
    row = db.query_places()[0][0]
    assert new == 0 and row["status"] == "Contacted" and row["notes"] == "hi" and row["name"] == "Alpha Renamed"


def test_delete_keeps_worked_leads(client):
    sid, _ = db.save("s", [P("a", "Alpha"), P("b", "Beta")])
    db.update_lead("a", "Contacted")
    db.delete_search(sid)
    assert [r["name"] for r in db.query_places()[0]] == ["Alpha"] and db.history() == []


def test_daily_limit(client, monkeypatch):
    monkeypatch.setattr(appmod, "DAILY_LIMIT", 2)
    appmod._tick(); appmod._tick()
    with pytest.raises(places.PlacesError, match="Daily API limit"):
        appmod._tick()
    assert db.usage_today() == 2


def test_dashboard_and_old_db_migration(client, tmp_path):
    import sqlite3
    c = sqlite3.connect(db.DB)  # database as created by the previous version
    c.executescript("CREATE TABLE places(place_id TEXT PRIMARY KEY,name TEXT,phone TEXT,intl_phone TEXT,address TEXT,rating REAL,rating_count INTEGER,maps_url TEXT);"
                    "CREATE TABLE searches(id INTEGER PRIMARY KEY,query TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);"
                    "INSERT INTO places VALUES('o','Old One','1','+91 98000 00000','x',4,1,'');")
    c.commit(); c.close()
    login(client)
    page = client.get("/").get_data(as_text=True)
    assert "Total leads" in page
    assert db.query_places()[0][0]["status"] == "New"


def test_known_places_hidden_by_default(client):
    s1, n1 = db.save("first", [P("a", "Alpha"), P("b", "Beta")])
    s2, n2 = db.save("second", [P("b", "Beta"), P("c", "Gamma")])
    assert (n1, n2) == (2, 1)
    names = lambda **k: sorted(r["name"] for r in db.query_places(search_id=s2, **k)[0])
    assert names(new_only=True) == ["Gamma"] and names() == ["Beta", "Gamma"]
    info = db.search_info(s2)
    assert (info["total"], info["new_count"], info["known"]) == (2, 1, 1)
    login(client)
    page = client.get(f"/search/{s2}").get_data(as_text=True)
    assert "Gamma" in page and "Beta" not in page and "1 already in your system" in page
    assert "Beta" in client.get(f"/search/{s2}?scope=all").get_data(as_text=True)
    assert b"Beta" not in client.get(f"/search/{s2}.csv").data and b"Beta" in client.get(f"/search/{s2}.csv?scope=all").data


def test_search_with_nothing_new_shows_message(client):
    db.save("first", [P("a", "Alpha")])
    s2, n = db.save("again", [P("a", "Alpha")])
    login(client)
    page = client.get(f"/search/{s2}").get_data(as_text=True)
    assert n == 0 and "Nothing new this time" in page and "Show them anyway" in page


def test_filter_leads_by_keyword(client):
    db.save("tiffin service in gurgaon", [P("a", "Alpha"), P("b", "Beta")])
    db.save("dhaba in delhi", [P("b", "Beta"), P("c", "Gamma")])
    names = lambda kw: sorted(r["name"] for r in db.query_places(keyword=kw)[0])
    assert names("tiffin service in gurgaon") == ["Alpha", "Beta"]
    assert names("dhaba in delhi") == ["Beta", "Gamma"]
    assert names("") == ["Alpha", "Beta", "Gamma"]
    beta = [r for r in db.query_places()[0] if r["name"] == "Beta"][0]
    assert sorted(beta["tags"]) == ["dhaba in delhi", "tiffin service in gurgaon"]
    assert [k["keyword"] for k in db.keywords()] == ["dhaba in delhi", "tiffin service in gurgaon"]
    login(client)
    page = client.get("/leads?kw=dhaba+in+delhi").get_data(as_text=True)
    assert "Gamma" in page and "Alpha" not in page and "All keywords" in page
    csvdata = client.get("/leads.csv?kw=dhaba+in+delhi").get_data(as_text=True)
    assert "keywords" in csvdata.splitlines()[0] and "dhaba in delhi" in csvdata and "Alpha" not in csvdata


def test_favicon_public_and_no_browser_popups(client):
    assert client.get("/favicon.ico").status_code == 200 and client.get("/static/favicon.svg").status_code == 200
    import pathlib
    for f in list(pathlib.Path("templates").glob("*.html")) + list(pathlib.Path("static").glob("*.js")):
        src = f.read_text()
        for bad in ("alert(", "confirm(", "prompt(", "onsubmit=", "window.open"):
            assert bad not in src, f"{bad} found in {f}"
