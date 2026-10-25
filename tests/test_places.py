import pytest
import requests

import places


class FakeResp:
    def __init__(self, data, code=200):
        self._d, self.status_code, self.text = data, code, str(data)

    def json(self):
        return self._d


def test_search_paginates_and_parses(monkeypatch):
    pages = [
        {"places": [{"id": "a", "displayName": {"text": "A"}, "nationalPhoneNumber": "1", "formattedAddress": "x"}], "nextPageToken": "t"},
        {"places": [{"id": "b", "displayName": {"text": "B"}}]},
    ]
    monkeypatch.setattr(places.requests, "post", lambda *a, **k: FakeResp(pages.pop(0)))
    calls = []
    out = places.search("q", "key", on_request=lambda: calls.append(1))
    assert [p["name"] for p in out] == ["A", "B"] and out[0]["phone"] == "1" and out[1]["phone"] == ""
    assert len(calls) == 2


def test_api_error_and_network_error(monkeypatch):
    monkeypatch.setattr(places.requests, "post", lambda *a, **k: FakeResp({"error": {"message": "blocked"}}, 403))
    with pytest.raises(places.PlacesError, match="blocked"):
        places.search("q", "k")

    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(places.requests, "post", boom)
    with pytest.raises(places.PlacesError, match="Could not reach"):
        places.search("q", "k")
