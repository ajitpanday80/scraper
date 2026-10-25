import db
import places


class FakeResp:
    status_code = 200

    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def test_search_paginates_and_parses(monkeypatch):
    pages = [
        {"places": [{"id": "a", "displayName": {"text": "A"}, "nationalPhoneNumber": "1", "formattedAddress": "x"}], "nextPageToken": "t"},
        {"places": [{"id": "b", "displayName": {"text": "B"}}]},
    ]
    monkeypatch.setattr(places.requests, "post", lambda *a, **k: FakeResp(pages.pop(0)))
    out = places.search("q", "key")
    assert [p["name"] for p in out] == ["A", "B"] and out[0]["phone"] == "1" and out[1]["phone"] == ""


def test_save_dedupes(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", str(tmp_path / "t.db"))
    r = {k: None for k in db.COLS} | {"place_id": "a", "name": "A"}
    db.save("q1", [r]); sid = db.save("q2", [r, r])
    assert len(db.get(sid)[1]) == 1 and len(db.history()) == 2
