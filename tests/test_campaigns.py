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
    r = web.post_("/campaigns/new", country="IN", name="October push")
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
    assert "messages can go out a day" not in flashes(r)                                          # 30/h over 9h fits 50
    r = web.post_("/campaigns/1/settings", follow=True, name="October push", idea="Fresh tiffins", link="https://x.co/a", language="English",
                  window_start="10:00", window_end="12:00", days=["0", "1"], statuses=["New"], per_hour="10", daily_cap="50", max_messages="3", min_gap_days="3",
                  mobile_only="1", kw=["tiffin gurgaon"])
    assert "only about 20 messages can go out a day, not 50" in flashes(r) and camp.get(1)["daily_cap"] == 50  # saved, but warned
    web.post_("/campaigns/1/settings", name="October push", idea="Fresh tiffins", link="https://x.co/a", language="English",
              window_start="10:00", window_end="19:00", days=["0", "1"], statuses=["New"], per_hour="30", daily_cap="50", max_messages="3", min_gap_days="3",
              mobile_only="1", kw=["tiffin gurgaon"])

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
    web.post_("/campaigns/new", country="IN", name="AI one")
    r = web.post_("/campaigns/1/messages/generate", n="3", follow=True)
    assert "OPENROUTER_API_KEY" in flashes(r) or "Write the basic idea" in flashes(r)
    monkeypatch.setattr(ai, "generate_variants", lambda idea, link, lang, n, model=None: [f"Hi {{name}} v{i} Reply STOP to opt out." for i in range(n)])
    r = web.post_("/campaigns/1/messages/generate", n="3", follow=True)
    vs = camp.variants(1)
    assert len(vs) == 3 and all(v["source"] == "ai" and not v["approved"] for v in vs)                # drafts only


def test_media_upload_rules(web):
    web.post_("/campaigns/new", country="IN", name="Media")
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


def test_manual_lead_is_picked_up_by_a_running_campaign(env):
    pid, err = db.add_manual("Sharma Tiffin", "98765 43210", "Sector 4", "met at expo", "tiffin gurgaon")
    assert pid and not err
    row = db.query_places(text="Sharma")[0][0]
    assert row["intl_phone"] == "+91 98765 43210" and "tiffin gurgaon" in row["tags"] and row["notes"] == "met at expo"
    assert db.add_manual("Other", "+91 9876543210")[0] is None                          # same number, different format
    assert db.add_manual("", "9876543211")[1] and db.add_manual("X", "123")[1]          # name and phone required
    before = camp.audience_count(camp.get(env))
    pid2, _ = db.add_manual("New Keyword Co", "9811122233", keyword="catering delhi")
    assert camp.audience_count(camp.get(env)) == before                                 # different keyword: not targeted yet
    assert camp.add_keywords(env, ["catering delhi"]) == 1 and camp.add_keywords(env, ["catering delhi"]) == 0
    assert camp.audience_count(camp.get(env)) == before + 1                             # running campaign gains the lead


def test_add_lead_and_keyword_routes(web):
    r = web.post_("/leads/add", follow=True, name="Hand Added", phone="9822233344", keyword="my list")
    assert "Added Hand Added" in flashes(r) and any(x["name"] == "Hand Added" for x in db.query_places()[0])
    assert "already in your leads" in flashes(web.post_("/leads/add", follow=True, name="Dup", phone="+91 98222 33344"))
    assert b"Add a lead manually" in web.get("/leads").data
    cid = camp.create("C")
    r = web.post_(f"/campaigns/{cid}/keywords", follow=True, kw=["my list", "not a keyword"])
    assert "Added 1 keyword" in flashes(r) and camp.get(cid)["keywords"] == ["my list"]
    assert "Tick at least one" in flashes(web.post_(f"/campaigns/{cid}/keywords", follow=True, kw=["my list"]))
    assert b"Add more keywords" in web.get(f"/campaigns/{cid}").data


@pytest.mark.parametrize("raw,digits", [
    ("98765 43210", "919876543210"),            # no country code
    ("9876543210", "919876543210"),
    ("098765 43210", "919876543210"),           # national trunk 0
    ("+91 98765-43210", "919876543210"),
    ("+91-(98765) 43210", "919876543210"),
    ("(+91) 98765.43210", "919876543210"),
    ("0091 98765 43210", "919876543210"),       # 00 international prefix
    ("+91 (0) 98765 43210", "919876543210"),    # trunk zero in brackets
    ("91 98765 43210", "919876543210"),
    ("  98765 43210 x12  ", "919876543210"),    # extension dropped
    ("98765 43210 / 98765 43211", "919876543210"),  # several numbers: first wins
    ("98765 43210, 011 2345 6789", "919876543210"),
    ("98765 43210 or 98765 43211", "919876543210"),
    ("011-2345 6789", "911123456789"),          # landline with STD code
    ("+44 20 7946 0958", "442079460958"),       # explicit foreign number is kept
    ("tel: 98765 43210", ""),                   # letters: refuse to guess
    ("98765", ""), ("", ""), ("12345678901234567", ""), ("+91 98765 4321", ""),   # wrong length
])
def test_phone_cleaning(raw, digits):
    assert db.clean_phone(raw) == digits


def test_phone_display_and_optout_use_the_same_rules():
    assert db.format_phone("919876543210") == "+91 98765 43210"
    assert db.format_phone("911123456789") == "+91 1123456789" and db.format_phone("442079460958") == "+442079460958"
    assert camp.normalize_phone("098765-43210") == "919876543210"


def test_phone_display_is_clean_and_whatsapp_works_for_any_country():
    show, wa, dial = appmod.show_phone, appmod.wa_number, appmod.dial_number
    us = {"phone": "(936) 508-2479", "intl_phone": "+1 936-508-2479"}
    assert show(us) == "+1 936 508 2479" and dial(us) == "+19365082479" and wa(dial(us)) == "19365082479"
    india = {"phone": "098765 43210", "intl_phone": "+91 98765 43210"}
    assert show(india) == "+91 98765 43210" and dial(india) == "+919876543210" and wa(dial(india)) == "919876543210"
    assert wa("+91 11 2345 6789") == ""                                             # Indian landline: no WhatsApp link
    nointl = {"phone": "(832) 304-1850", "intl_phone": ""}                          # no international form stored: 10 digits read as Indian
    assert show({"phone": "098765-43210", "intl_phone": ""}) == "+91 98765 43210"
    assert dial({"phone": "", "intl_phone": ""}) == "" and show(nointl).startswith("+")


def test_mobile_only_skips_indian_landlines_but_keeps_foreign_numbers(env):
    db.save("tiffin gurgaon", [{**lead(30), "intl_phone": "+1 936-508-2479"}, {**lead(31), "intl_phone": "+91 80 1234 5678"},
                               {**lead(32), "intl_phone": "+44 20 7946 0958"}])
    names = {r["name"] for r in db.query_places(mobile=True, per_page=None)[0]}
    assert {"Biz 30", "Biz 32", "Biz 1"} <= names and "Biz 31" not in names
    c = camp.get(env)
    assert c["mobile_only"] == 1
    before = camp.audience_count(c)
    c["mobile_only"] = 0
    assert camp.audience_count(c) == before + 1                        # only the Indian landline differs


def test_country_detection():
    assert db.country_of("+91 98765 43210") == "IN" and db.country_of("+1 936-508-2479", "Spring, TX 77380, USA") == "US"
    assert db.country_of("+1 416 555 0100", "Toronto, ON, Canada") == "CA" and db.country_of("+44 20 7946 0958") == "GB"
    assert db.country_of("+971 4 123 4567") == "AE" and db.country_of("+998 71 123 4567") == "XX" and db.country_of("") == ""


def test_country_tagging_filters_and_campaign_audience(env):
    db.save("it services texas", [{**lead(50), "intl_phone": "+1 936-508-2479", "address": "Spring, TX, USA"},
                                  {**lead(51), "intl_phone": "+1 832-304-1850", "address": "Houston, TX, USA"},
                                  {**lead(52), "intl_phone": "", "phone": ""}])
    by = {c["code"]: c["n"] for c in db.countries()}
    assert by["US"] == 2 and by["IN"] == 8 and by["NONE"] == 1
    assert {r["name"] for r in db.query_places(country="US")[0]} == {"Biz 50", "Biz 51"}
    assert [r["name"] for r in db.query_places(country="NONE")[0]] == ["Biz 52"]
    assert [k["keyword"] for k in db.keywords("US")] == ["it services texas"]                 # only keywords with US leads
    assert {k["keyword"] for k in db.keywords("IN")} == {"tiffin gurgaon"} and len(db.keywords()) == 2
    live = db.count_countries([{"intl_phone": "+1 936-508-2479", "address": "TX, USA"}, {"intl_phone": "+91 98765 43210"}, {"intl_phone": ""}])
    assert {c["code"] for c in live} == {"US", "IN", "NONE"}
    with db.tx() as t:
        t.execute("UPDATE campaigns SET country='US' WHERE id=?", (env,))
        t.execute("INSERT OR IGNORE INTO campaign_keywords VALUES (?, 'it services texas')", (env,))
    c = camp.get(env)
    assert camp.audience_count(c) == 2                                                          # IN leads of 'tiffin gurgaon' are excluded
    c["country"] = "IN"
    assert camp.audience_count(c) == 8
    c["country"] = "ANY"
    assert camp.audience_count(c) == 10


def test_campaign_time_zone_follows_the_country(env):
    with db.tx() as t:
        t.execute("UPDATE campaigns SET country='US', tz='' WHERE id=?", (env,))
    us = camp.get(env)
    assert us["tzname"] == "America/Chicago"
    assert scheduler.check(us, MON_11AM) == "window"          # 11:00 IST is 00:30 in Texas: closed, though the same instant is 'open' in India
    open_in_texas = datetime.datetime(2026, 10, 26, 16, 30)   # 11:30 Central
    assert scheduler.check(us, open_in_texas) == "open"
    with db.tx() as t:
        t.execute("UPDATE campaigns SET tz='Asia/Kolkata' WHERE id=?", (env,))
    assert scheduler.check(camp.get(env), MON_11AM) == "open"                                   # explicit zone wins over the country's
    assert camp.input_to_utc("2026-10-26T10:00", camp.zone("America/Chicago")) == datetime.datetime(2026, 10, 26, 15, 0)


def test_campaign_requires_a_country_and_pages_render(web):
    assert "Choose which country" in flashes(web.post_("/campaigns/new", follow=True, name="No country"))
    r = web.post_("/campaigns/new", follow=True, name="US push", country="US")
    assert b"Who you are messaging" in r.data and b"America/Chicago" in r.data
    assert b"United States" in web.get("/campaigns/").data
    assert b"All countries" in web.get("/leads").data and b"India" in web.get("/leads?country=IN").data


@pytest.mark.parametrize("addr,expect", [
    ("Shop 4, Sector 56, Gurugram, Haryana 122011, India", ("Sector 56", "Gurugram", "122011")),
    ("Baner Rd, Baner, Pune, Maharashtra 411045, India", ("Baner", "Pune", "411045")),
    ("Sector 56, Gurugram, Haryana, 122011, India", ("Sector 56", "Gurugram", "122011")),
    ("Pune, Maharashtra, India", ("", "Pune", "")),
    ("", ("", "", "")),
])
def test_tiffin_address_split(addr, expect):
    import tiffin
    assert tiffin.split_address(addr) == expect


def test_tiffin_push_end_to_end_with_a_fake_server(web, monkeypatch):
    import tiffin
    db.save("tiffin gurgaon", [{**lead(60), "address": "Sector 56, Gurugram, Haryana 122011, India"},
                               {**lead(61), "intl_phone": "+1 936-508-2479"},            # foreign: never sent
                               {**lead(62), "intl_phone": "+91 11 2345 6789"}])           # landline: never sent
    camp.add_optouts("90003 00003")                                                        # lead 3 opted out
    monkeypatch.setenv("MYTIFFIN_API_KEY", "mtk_test_key_000000000000")
    calls = []

    class Resp:
        status_code = 200
        def __init__(self, body): self.body = body
        def json(self): return self.body

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers))
        ks = json["kitchens"]
        return Resp({"added": len(ks) - 1, "skipped": [f"Row 2 ({ks[0]['name']}): already in the directory"],
                     "kitchens": [{"id": f"t{i}", "phone": k["phone"], "invite": f"https://mytiffin.online/signup?invite=x{i}"} for i, k in enumerate(ks)]})
    monkeypatch.setattr(tiffin.requests, "post", fake_post)

    page = web.get("/mytiffin/").data
    assert b"tiffin gurgaon" in page
    pend = tiffin.pending(["tiffin gurgaon"])
    assert all(p["place_id"] != "p3" for p in pend) and not any(p["place_id"] in ("p61", "p62") for p in pend) and len(pend) == 5
    assert b"will be sent" in web.post_("/mytiffin/preview", kw=["tiffin gurgaon"], free_days="30", list_on_join="1").data
    r = web.post_("/mytiffin/send", kw=["tiffin gurgaon"], free_days="45", list_on_join="1")
    assert b"Sent" in r.data and len(calls) == 1 and calls[0][0].endswith("/api/v1/kitchens")
    body = calls[0][1]
    assert body["listOnJoin"] is True and body["freeDays"] == 45 and len(body["kitchens"]) == len(pend)
    assert calls[0][2]["Authorization"] == "Bearer mtk_test_key_000000000000"
    assert {"name", "phone", "address", "area", "city", "pincode"} == set(body["kitchens"][0])
    assert any(k["address"] == "Sector 56, Gurugram, Haryana 122011, India" for k in body["kitchens"])
    assert any(k["city"] == "Gurugram" and k["pincode"] == "122011" for k in body["kitchens"])
    assert tiffin.pending(["tiffin gurgaon"]) == []                                       # second push has nothing left: no duplicates
    row = db.query_places(text="Biz 60")[0][0]
    assert row["tiffin_at"] and row["tiffin_invite"].startswith("https://mytiffin.online/signup?invite=")
    assert tiffin.history()[0]["added"] == len(pend) - 1


def test_tiffin_api_errors_mark_nothing(web, monkeypatch):
    import tiffin
    monkeypatch.setenv("MYTIFFIN_API_KEY", "mtk_test_key_000000000000")

    class Bad:
        status_code = 401
        def json(self): return {"error": "Missing or wrong API key."}
    monkeypatch.setattr(tiffin.requests, "post", lambda *a, **k: Bad())
    res = tiffin.push(["tiffin gurgaon"], True, 30)
    assert "rejected the API key" in res["error"] and res["linked"] == 0 and len(tiffin.pending(["tiffin gurgaon"])) == 5
    monkeypatch.delenv("MYTIFFIN_API_KEY")
    assert b"not set" in web.get("/mytiffin/").data
