import datetime
import json

import pytest

import ai
import camp
import db
import scheduler
import senders

MON_11AM = datetime.datetime(2026, 10, 26, 5, 30)           # Monday 11:00 IST
SUN = datetime.datetime(2026, 10, 25, 5, 30)                  # Sunday 11:00 IST
MON_10PM = datetime.datetime(2026, 10, 26, 16, 30)            # Monday 22:00 IST


def lead(i, phone=True, mobile=True):
    intl = (f"+91 9{i:04d} {i:05d}" if mobile else f"+91 11 2345 {i:04d}") if phone else ""
    return {"place_id": f"p{i}", "name": f"Biz {i}", "phone": "x" if phone else "", "intl_phone": intl,
            "address": "Gurugram", "rating": 4.0, "rating_count": 5, "maps_url": ""}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    db.save("tiffin gurgaon", [lead(i) for i in range(1, 9)])
    cid = camp.create("Test campaign")
    form = {"name": "Test campaign", "link": "https://example.com/offer", "per_hour": "60", "daily_cap": "200",
            "window_start": "10:00", "window_end": "19:00", "max_messages": "3", "min_gap_days": "3"}

    class F(dict):
        def getlist(self, k):
            return self.get(k, []) if isinstance(self.get(k, []), list) else [self[k]]
    f = F(form, days=list("012345"), statuses=["New"], mobile_only="1", mark_contacted="1", kw=["tiffin gurgaon"])
    clean, kws, errs = camp.validate(f, {"tiffin gurgaon"})
    assert errs == [] and kws == ["tiffin gurgaon"]
    camp.update(cid, clean, kws)
    for t in ("Hello {name}, offer {link}. Reply STOP to opt out.", "Hi {name}! See {link}. Reply STOP to opt out.",
              "Namaste {name} {link} Reply STOP to opt out."):
        camp.add_variant(cid, t, approved=1)
    camp.set_status(cid, "active")
    return cid


def C(cid):
    return camp.get(cid)


def test_window_days_start_end(env):
    c = C(env)
    assert scheduler.check(c, MON_11AM) == "open"
    assert scheduler.check(c, MON_10PM) == "window" and scheduler.check(c, SUN) == "day"
    c["start_at"] = camp.fmt(MON_11AM + datetime.timedelta(hours=1))
    assert scheduler.check(c, MON_11AM) == "scheduled"
    c["start_at"], c["end_at"] = None, camp.fmt(MON_11AM - datetime.timedelta(minutes=1))
    assert scheduler.check(c, MON_11AM) == "ended"
    assert camp.next_open(C(env), SUN).startswith("Mon 26 Oct, 10:00")


def test_pacing_rotation_and_no_repeat_leads(env):
    t = MON_11AM
    assert scheduler.tick(t) == {env: "sent"}
    assert scheduler.tick(t + datetime.timedelta(seconds=30)) == {env: "interval"}
    for i in range(1, 6):
        assert scheduler.tick(t + datetime.timedelta(seconds=61 * i)) == {env: "sent"}
    log, total = camp.log(env, 1, 50)
    assert total == 6 and len({m["place_id"] for m in log}) == 6                        # nobody messaged twice
    assert [m["variant_id"] for m in reversed(log)] == [1, 2, 3, 1, 2, 3]               # rotates
    assert all("Biz" in m["body"] and "{name}" not in m["body"] and "example.com" in m["body"] for m in log)


def test_daily_cap_and_outside_window(env):
    with db.tx() as c:
        c.execute("UPDATE campaigns SET daily_cap=2, per_hour=3600 WHERE id=?", (env,))
    t = MON_11AM
    got = [scheduler.tick(t + datetime.timedelta(seconds=2 * i))[env] for i in range(4)]
    assert got == ["sent", "sent", "cap", "cap"]
    assert scheduler.tick(MON_10PM)[env] == "window"


def test_audience_rules(env):
    c = C(env)
    assert camp.audience_count(c, MON_11AM) == 8
    db.save("tiffin gurgaon", [lead(20, phone=False), lead(21, mobile=False)])
    db.save("other keyword", [lead(30)])
    assert camp.audience_count(C(env), MON_11AM) == 8                                    # no phone / landline / other keyword out
    camp.add_optouts("9 0001 00001\n+91 90002 00002, junk", "asked to stop")
    assert camp.audience_count(C(env), MON_11AM) == 6 and camp.add_optouts("abc")[1] == 1
    db.update_lead("p3", "Not interested")
    assert camp.audience_count(C(env), MON_11AM) == 5                                    # status filter
    other = camp.create("Other")
    with db.tx() as t:
        t.execute("INSERT INTO messages(campaign_id, place_id, phone, status, sent_at) VALUES (?,?,?,?,?)",
                  (other, "p4", "919000400004", "sent", camp.fmt(MON_11AM - datetime.timedelta(days=2))))
    assert camp.audience_count(C(env), MON_11AM) == 4                                    # messaged 2 days ago: gap of 3 days not met
    assert camp.audience_count(C(env), MON_11AM + datetime.timedelta(days=1)) == 5       # exactly 3 days later: allowed again


def test_max_three_messages_and_gap_per_person(env):
    """A person gets at most `max_messages` real messages in total, spaced at least `min_gap_days` apart, across campaigns."""
    phone = "919000400004"                                                               # lead p4 (+91 90004 00004)
    def ins(cid, ago, status="sent"):
        with db.tx() as t:
            t.execute("INSERT INTO messages(campaign_id, place_id, phone, status, sent_at) VALUES (?,?,?,?,?)",
                      (cid, "p4", phone, status, camp.fmt(MON_11AM - datetime.timedelta(days=ago))))
    n = lambda: camp.audience_count(C(env), MON_11AM)
    base = n()
    assert base == 8
    ins(901, 10); ins(902, 7)                                                             # two earlier messages, long enough ago
    assert n() == base                                                                    # 2 of 3 used -> still eligible
    ins(903, 4)                                                                           # third message
    assert n() == base - 1                                                                # 3 of 3 used -> never again, even though the gap passed
    with db.tx() as t:
        t.execute("DELETE FROM messages WHERE campaign_id IN (901,902,903)")
    ins(901, 10, "test"); ins(902, 1, "test"); ins(903, 0, "failed")
    assert n() == base                                                                    # test-mode and failed sends never count
    ins(904, 2)
    assert n() == base - 1                                                                # sent 2 days ago -> wait for the 3-day gap
    with db.tx() as t:
        t.execute("UPDATE campaigns SET min_gap_days=1 WHERE id=?", (env,))
    assert n() == base                                                                    # the gap is configurable
    with db.tx() as t:
        t.execute("UPDATE campaigns SET max_messages=1 WHERE id=?", (env,))
    assert n() == base - 1                                                                # so is the maximum
    same_number = camp.create("Other campaign")
    with db.tx() as t:                                                                    # same phone under a different place record
        t.execute("INSERT INTO places(place_id,name,phone,intl_phone,status) VALUES ('dup','Dup Biz','x','+91 90004 00004','New')")
        t.execute("INSERT INTO search_places VALUES (1,'dup',1)")
    assert n() == base - 1                                                                # keyed on the phone number, not the place record


def test_test_mode_touches_nothing_real(env):
    scheduler.tick(MON_11AM)
    m = camp.log(env, 1, 5)[0][0]
    assert m["status"] == "test"
    assert [r["status"] for r in db.query_places(text=m["name"])[0]] == ["New"]           # lead status unchanged
    assert camp.stats(env, MON_11AM)["test"] == 1 and camp.audience_count(C(env), MON_11AM) == 7
    camp.reset_test(env)
    assert camp.audience_count(C(env), MON_11AM) == 8


def test_real_send_marks_contacted_and_starts_cooldown(env):
    class Real:
        def send(self, phone, text, media, variant):
            return senders.Result(ok=True, ref="abc")
    assert scheduler.tick(MON_11AM, Real()) == {env: "sent"}
    m = camp.log(env, 1, 5)[0][0]
    assert m["status"] == "sent" and db.query_places(text=m["name"])[0][0]["status"] == "Contacted"


def test_five_failures_in_a_row_pause_campaign(env):
    class Bad:
        def send(self, *a):
            raise RuntimeError("provider down")
    for i in range(5):
        assert scheduler.tick(MON_11AM + datetime.timedelta(seconds=61 * i), Bad())[env] == "failed"
    c = C(env)
    assert c["status"] == "paused" and "5 messages in a row" in c["note"]
    assert scheduler.tick(MON_11AM + datetime.timedelta(minutes=30)) == {}                # paused campaigns are skipped


def test_no_variants_ended_and_stale_claims(env):
    for v in camp.variants(env):
        camp.update_variant(env, v["id"], approved=0)
    assert scheduler.tick(MON_11AM) == {env: "no_variants"}
    with db.tx() as t:
        t.execute("UPDATE campaigns SET end_at=? WHERE id=?", (camp.fmt(MON_11AM - datetime.timedelta(hours=1)), env))
    assert scheduler.tick(MON_11AM) == {env: "ended"} and C(env)["status"] == "completed"


def test_claim_is_exclusive(env):
    c = C(env)
    assert camp.claim_next(c, MON_11AM)[0] == "ready"
    assert camp.claim_next(c, MON_11AM)[0] == "interval"                                  # a second worker gets nothing
    camp.clear_stale(MON_11AM + datetime.timedelta(minutes=11))
    assert camp.stats(env, MON_11AM)["failed"] == 1


def test_validation_rules(env):
    class F(dict):
        def getlist(self, k):
            return self.get(k, [])
    bad = F(name="", link="ftp://x", window_start="07:00", window_end="22:00", per_hour="9999", daily_cap="x", days=[], statuses=[])
    clean, kws, errs = camp.validate(bad, set())
    text = " ".join(errs)
    for needle in ("name", "http", "between 09:00 and 21:00", "Messages per hour", "Daily limit", "day of the week", "status"):
        assert needle in text


def test_ai_variants(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ai.AIError, match="OPENROUTER_API_KEY"):
        ai.generate_variants("idea", "", "English", 3, "m")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    with pytest.raises(ai.AIError, match="No AI model"):
        ai.generate_variants("idea", "", "English", 3)
    seen = {}

    class R:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "```json\n" + json.dumps(["Hi {name}, we deliver. Reply STOP to opt out.", "Hello {name}, try us", ""]) + "\n```"}}]}

    def post(url, **kw):
        seen.update(url=url, **kw)
        return R()
    monkeypatch.setattr(ai.requests, "post", post)
    out = ai.generate_variants("tiffin offer", "", "English", 5, "some/model")
    assert len(out) == 2 and all("STOP" in v for v in out)                                  # opt-out line always present
    assert seen["json"]["model"] == "some/model" and seen["headers"]["Authorization"] == "Bearer k" and "openrouter.ai" in seen["url"]
    with pytest.raises(ai.AIError, match="idea"):
        ai.generate_variants(" ", "", "English", 3, "m")


# ---------- web flow ----------
import io

import app as appmod


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", str(tmp_path / "t.db"))
    monkeypatch.setattr(camp, "UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    db.save("tiffin gurgaon", [lead(i) for i in range(1, 6)])
    c = appmod.app.test_client()
    c.get("/login")
    with c.session_transaction() as s:
        tok = s["csrf"]
    c.post("/login", data={"username": "admin", "password": "pw", "csrf": tok})
    c.get("/")  # rendering a page creates the session's CSRF token

    def post(url, follow=False, **data):
        with c.session_transaction() as s:
            data["csrf"] = s["csrf"]
        return c.post(url, data=data, follow_redirects=follow, content_type="multipart/form-data" if "file" in data else None)
    c.post_ = post
    return c


def flashes(resp):
    return resp.get_data(as_text=True)


def test_campaign_web_flow(web, monkeypatch):
    assert web.get("/campaigns/").status_code == 200
    r = web.post_("/campaigns/new", name="October push")
    assert r.status_code == 302 and r.headers["Location"].endswith("/campaigns/1/settings")
    page = web.get("/campaigns/1/settings").get_data(as_text=True)
    assert "tiffin gurgaon" in page and "Asia/Kolkata" in page
    r = web.post_("/campaigns/1/settings", follow=True, name="October push", idea="Fresh tiffins", link="https://x.co/a", language="English",
                  window_start="06:00", window_end="19:00", days=["0", "1"], statuses=["New"], per_hour="30", daily_cap="50", max_messages="3", min_gap_days="3", kw=["tiffin gurgaon"])
    assert "between 09:00 and 21:00" in flashes(r) and camp.get(1)["keywords"] == []                # invalid -> nothing saved
    r = web.post_("/campaigns/1/settings", follow=True, name="October push", idea="Fresh tiffins", link="https://x.co/a", language="English",
                  window_start="10:00", window_end="19:00", days=["0", "1"], statuses=["New"], per_hour="30", daily_cap="50", max_messages="3", min_gap_days="3",
                  mobile_only="1", kw=["tiffin gurgaon", "not a real keyword"])
    c = camp.get(1)
    assert "Settings saved" in flashes(r) and c["keywords"] == ["tiffin gurgaon"] and c["days"] == ["0", "1"] and c["daily_cap"] == 50

    # cannot start yet
    r = web.post_("/campaigns/1/start", follow=True, ack="1")
    assert "Approve at least one message variant" in flashes(r) and camp.get(1)["status"] == "draft"
    # opt-out line is mandatory for approval
    web.post_("/campaigns/1/messages/add", body="Hi {name} {link} buy now")
    r = web.post_("/campaigns/1/messages/1", follow=True, body="Hi {name} {link} buy now", action="approve")
    assert "opt-out line" in flashes(r) and not camp.variants(1)[0]["approved"]
    r = web.post_("/campaigns/1/messages/1", follow=True, body="Hi {name} {link} Reply STOP to opt out.", action="approve")
    assert camp.variants(1)[0]["approved"]
    r = web.post_("/campaigns/1/start", follow=True)
    assert "Tick the confirmation box" in flashes(r) and camp.get(1)["status"] == "draft"
    r = web.post_("/campaigns/1/start", follow=True, ack="1")
    assert camp.get(1)["status"] == "active" and "Campaign started" in flashes(r)

    scheduler.tick(MON_11AM)
    page = web.get("/campaigns/1").get_data(as_text=True)
    assert "simulated" in page and "Biz 1" in page and "Test mode" in page and "Clear test run" in page
    assert "Campaigns" in web.get("/campaigns/").get_data(as_text=True) and "October push" in web.get("/campaigns/").get_data(as_text=True)

    web.post_("/campaigns/1/pause")
    assert camp.get(1)["status"] == "paused" and scheduler.tick(MON_11AM + datetime.timedelta(minutes=5)) == {}
    r = web.post_("/campaigns/1/delete", follow=True)
    assert camp.get(1) is None and "Campaign deleted" in flashes(r)


def test_ai_generate_route_and_errors(web, monkeypatch):
    web.post_("/campaigns/new", name="AI one")
    r = web.post_("/campaigns/1/messages/generate", n="3", follow=True)
    assert "OPENROUTER_API_KEY" in flashes(r) or "Write the basic idea" in flashes(r)
    monkeypatch.setattr(ai, "generate_variants", lambda idea, link, lang, n, model=None: [f"Hi {{name}} v{i} Reply STOP to opt out." for i in range(n)])
    r = web.post_("/campaigns/1/messages/generate", n="3", follow=True)
    vs = camp.variants(1)
    assert len(vs) == 3 and all(v["source"] == "ai" and not v["approved"] for v in vs)                # drafts only


def test_media_upload_rules(web):
    web.post_("/campaigns/new", name="Media")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
    r = web.post_("/campaigns/1/media", file=(io.BytesIO(png), "Offer Banner.png"), follow=True)
    m = camp.media(1)
    assert len(m) == 1 and m[0]["kind"] == "image" and m[0]["filename"] != "Offer Banner.png"
    assert web.get(f"/campaigns/media/{m[0]['filename']}").status_code == 200
    assert web.get("/campaigns/media/../../app.py").status_code == 404 and web.get("/campaigns/media/evil.png").status_code == 404
    r = web.post_("/campaigns/1/media", file=(io.BytesIO(b"MZ"), "virus.exe"), follow=True)
    assert "Allowed files" in flashes(r) and len(camp.media(1)) == 1
    web.post_(f"/campaigns/1/media/{m[0]['id']}/delete")
    assert camp.media(1) == []


def test_optout_pages(web):
    r = web.post_("/campaigns/optouts/add", numbers="98765 43210\n+91 90000 11111", reason="replied STOP", follow=True)
    page = r.get_data(as_text=True)
    assert "Added 2" in page and "+919876543210" in page and "replied STOP" in page
    assert "9876543210" in web.get("/campaigns/optouts?q=98765").get_data(as_text=True) and "9000011111" not in web.get("/campaigns/optouts?q=98765").get_data(as_text=True)
    web.post_("/campaigns/optouts/remove", phone="919876543210")
    assert "+919876543210" not in web.get("/campaigns/optouts").get_data(as_text=True)


def test_campaign_pages_need_login_and_csrf(web):
    anon = appmod.app.test_client()
    for url in ("/campaigns/", "/campaigns/1", "/campaigns/optouts", "/campaigns/media/" + "a" * 24 + ".png"):
        assert anon.get(url).status_code == 302
    assert web.post("/campaigns/new", data={"name": "x"}).status_code == 400


def test_limits_hold_end_to_end_across_campaigns(tmp_path, monkeypatch):
    """One person, four campaigns, real sends: day 0 ok, day 1 blocked (gap), day 3 ok, day 6 ok, day 9 blocked (max 3)."""
    monkeypatch.setattr(db, "DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    db.save("tiffin gurgaon", [lead(1)])

    class Real:
        def send(self, *a):
            return senders.Result(ok=True, ref="r")
    ids = []
    for i in range(4):
        cid = camp.create(f"C{i}")
        with db.tx() as t:
            t.execute("UPDATE campaigns SET status='active', days='0,1,2,3,4,5,6', statuses='New,Contacted,Interested,Not interested,Closed', per_hour=60 WHERE id=?", (cid,))
            t.execute("INSERT INTO campaign_keywords VALUES (?, 'tiffin gurgaon')", (cid,))
        camp.add_variant(cid, "Hi {name} Reply STOP to opt out.", approved=1)
        ids.append(cid)
    day = lambda d: MON_11AM + datetime.timedelta(days=d)
    run = lambda cid, d: scheduler.process(camp.get(cid), day(d), Real())
    assert run(ids[0], 0) == "sent"
    assert run(ids[1], 1) == "no_audience"            # only 1 day since the last message
    assert run(ids[1], 3) == "sent"                   # 3 days later -> allowed
    assert run(ids[2], 6) == "sent"                   # third and last
    assert run(ids[3], 9) == "no_audience"            # lifetime maximum of 3 reached
    assert run(ids[3], 400) == "no_audience"          # ... even much later
    with db.tx() as t:
        assert t.execute("SELECT COUNT(*) FROM messages WHERE status='sent'").fetchone()[0] == 3


def test_leads_without_a_phone_number_are_never_messaged(env):
    """Missing, blank, partial or malformed numbers are excluded, with mobile-only on or off."""
    bad = [("n1", ""), ("n2", None), ("n3", "+91"), ("n4", "+91 98"), ("n5", "   "), ("n6", "+91 98765 4321x"), ("n7", "call us")]
    with db.tx() as t:
        for pid, intl in bad:
            t.execute("INSERT INTO places(place_id,name,phone,intl_phone,status) VALUES (?,?,?,?,'New')", (pid, f"No phone {pid}", "098", intl))
            t.execute("INSERT INTO search_places VALUES (1,?,1)", (pid,))
    for mobile_only in (1, 0):
        with db.tx() as t:
            t.execute("UPDATE campaigns SET mobile_only=?, per_hour=3600 WHERE id=?", (mobile_only, env))
        assert camp.audience_count(C(env), MON_11AM) == 8                  # only the 8 leads with real numbers
    for i in range(12):                                                     # run the whole audience through the scheduler
        scheduler.tick(MON_11AM + datetime.timedelta(seconds=2 * i))
    sent = camp.log(env, 1, 100)[0]
    assert len(sent) == 8 and not any(m["place_id"].startswith("n") for m in sent)
    assert all(camp.valid_phone(m["phone"]) for m in sent)


def test_sender_is_never_called_without_a_valid_number(env, monkeypatch):
    with db.tx() as t:
        mid = t.execute("INSERT INTO messages(campaign_id, place_id, phone, status, sent_at) VALUES (?,?,?,?,?)",
                        (env, "p1", "", "sending", camp.fmt(MON_11AM))).lastrowid
    monkeypatch.setattr(camp, "claim_next", lambda c, now: ("ready", {"id": mid, "place_id": "p1", "phone": "", "text": "x", "variant": {}}))
    called = []

    class Spy:
        def send(self, *a):
            called.append(a)
            return senders.Result(ok=True)
    assert scheduler.process(C(env), MON_11AM, Spy()) == "no_phone"
    assert called == [] and camp.log(env, 1, 5)[0][0]["error"] == "no valid phone number"
    assert camp.valid_phone("919876543210") and not camp.valid_phone("91987") and not camp.valid_phone("91 98765 43210")
