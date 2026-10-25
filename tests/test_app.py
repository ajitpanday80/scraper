import pytest

import app as appmod
import db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("ADMIN_PASSWORD", "pw-test")
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "k")
    appmod._fails.clear()
    return appmod.app.test_client()


def token(c):
    c.get("/login")
    with c.session_transaction() as s:
        return s["csrf"]


def login(c, pw="pw-test"):
    return c.post("/login", data={"username": "admin", "password": pw, "csrf": token(c)})


def test_requires_login(client):
    assert client.get("/").status_code == 302
    assert client.get("/places.csv").status_code == 302


def test_bad_password_and_lockout(client):
    for _ in range(5):
        assert login(client, "wrong").status_code == 401
    assert login(client, "pw-test").status_code == 429


def test_csrf_enforced(client):
    assert client.post("/login", data={"username": "admin", "password": "pw-test"}).status_code == 400


def test_search_flow(client, monkeypatch):
    fake = [{"place_id": "a", "name": "Tiffmon", "phone": "083079 38948", "intl_phone": "", "address": "Gurugram",
             "rating": 4.9, "rating_count": 66, "maps_url": ""}]
    monkeypatch.setattr(appmod, "search", lambda q, k: fake)
    assert login(client).status_code == 302
    r = client.post("/search", data={"q": "tiffin", "csrf": token(client)})
    assert r.status_code == 302
    page = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Tiffmon" in page and "083079 38948" in page
    assert b"Tiffmon" in client.get("/places.csv").data
    client.post("/search/1/delete", data={"csrf": token(client)})
    assert db.history() == [] and db.all_places() == []
