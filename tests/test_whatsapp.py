import datetime

import camp
import db
import scheduler
import senders
from test_campaigns import MON_11AM, env, lead  # noqa: F401  (env is a fixture)

MIN = datetime.timedelta(minutes=1)


class Fake:
    def __init__(self, up=True, err=None, lead=False):
        self.up, self.err, self.lead, self.calls = up, err, lead, 0

    def ready(self):
        return self.up

    def send(self, phone, text, media, variant):
        self.calls += 1
        if self.err:
            return senders.Result(ok=False, error=self.err, lead_error=self.lead)
        return senders.Result(ok=True, ref="wamid")


def wa(cid):
    with db.tx() as t:
        t.execute("UPDATE campaigns SET channel='whatsapp' WHERE id=?", (cid,))


def test_gap_is_random_3_to_4_minutes_and_stable(env):
    wa(env)
    c = camp.get(env)
    gaps = {round(camp.gap_seconds(c, i)) for i in range(1, 60)}
    assert all(180 <= g <= 240 for g in gaps) and len(gaps) > 20           # random, inside 3-4 min
    assert camp.gap_seconds(c, 7) == camp.gap_seconds(c, 7)                 # same wait on every 5s tick


def test_whatsapp_waits_the_gap_between_messages(env):
    wa(env)
    f = Fake()
    assert scheduler.tick(MON_11AM, f) == {env: "sent"}
    assert scheduler.tick(MON_11AM + 2 * MIN + 50 * (MIN / 60), f) == {env: "interval"}       # 2m50s: too soon
    assert scheduler.tick(MON_11AM + 4 * MIN + MIN / 60, f) == {env: "sent"}                  # 4m01s: always allowed
    assert f.calls == 2


def test_gap_is_shared_by_all_whatsapp_campaigns(env):
    wa(env)
    other = camp.create("Second")
    with db.tx() as t:
        t.execute("UPDATE campaigns SET channel='whatsapp', status='active', per_hour=60, window_start='10:00', window_end='19:00', "
                  "days='0,1,2,3,4,5', statuses='New', mobile_only=1 WHERE id=?", (other,))
        t.execute("INSERT INTO campaign_keywords VALUES (?, 'tiffin gurgaon')", (other,))
    camp.add_variant(other, "Hi {name} Reply STOP", approved=1)
    f = Fake()
    res = scheduler.tick(MON_11AM, f)
    assert sorted(res.values()) == ["interval", "sent"] and f.calls == 1                      # one number, one message per gap


def test_nothing_is_claimed_while_whatsapp_is_offline(env):
    wa(env)
    f = Fake(up=False)
    for i in range(7):
        assert scheduler.tick(MON_11AM + 5 * i * MIN, f) == {env: "sender_offline"}
    assert f.calls == 0 and camp.log(env, 1, 50)[1] == 0 and camp.get(env)["status"] == "active"


def test_numbers_not_on_whatsapp_never_pause_the_campaign(env):
    wa(env)
    f = Fake(err="number is not on WhatsApp", lead=True)
    for i in range(6):
        assert scheduler.tick(MON_11AM + 5 * i * MIN, f) == {env: "failed"}
    assert camp.get(env)["status"] == "active"
    assert len({m["place_id"] for m in camp.log(env, 1, 50)[0]}) == 6                         # each failed lead is not retried


def test_real_bridge_errors_still_pause(env):
    wa(env)
    f = Fake(err="WhatsApp is not connected")
    for i in range(5):
        scheduler.tick(MON_11AM + 5 * i * MIN, f)
    assert camp.get(env)["status"] == "paused"


def test_capacity_and_channel_validation(env):
    c = {**camp.get(env), "channel": "whatsapp", "window_start": "10:00", "window_end": "19:00"}
    assert camp.day_capacity(c) == 9 * 60 // 3.5 // 1 or camp.day_capacity(c) in range(150, 156)
    assert senders.get("whatsapp").name.startswith("WhatsApp") and senders.get("junk").simulated


def test_bridge_client_reports_offline_when_unreachable(monkeypatch):
    import whatsapp
    monkeypatch.setattr(whatsapp, "URL", "http://127.0.0.1:1")
    assert whatsapp.status()["state"] == "offline"
    r = whatsapp.WhatsAppSender().send("919876543210", "hi", [], {})
    assert not r.ok and "unreachable" in r.error and not r.lead_error


def test_warmup_ramp_restarts_for_every_new_number(env):
    import whatsapp
    t0 = MON_11AM
    assert whatsapp.daily_limit("919000000001", t0) == (10, 0)
    assert whatsapp.daily_limit("919000000001", t0 + datetime.timedelta(days=5))[0] == 20
    assert whatsapp.daily_limit("919000000001", t0 + datetime.timedelta(days=10))[0] == 30
    assert whatsapp.daily_limit("919000000001", t0 + datetime.timedelta(days=30))[0] == 50
    assert whatsapp.daily_limit("919000000002", t0 + datetime.timedelta(days=30)) == (10, 0)      # switched number: ramp restarts


def test_daily_limit_blocks_sending_for_the_whole_day(env, monkeypatch):
    import whatsapp
    wa(env)
    db.save("tiffin gurgaon", [lead(i) for i in range(20, 40)])
    monkeypatch.setattr(whatsapp, "status", lambda: {"state": "ready", "number": "919000000009", "qr": "", "name": ""})
    s = whatsapp.WhatsAppSender()
    sent = 0
    for i in range(14):
        sent += scheduler.tick(MON_11AM + 5 * i * MIN, _Stub(s)) [env] == "sent"
    assert sent == 10                                                                         # day-0 limit
    assert scheduler.tick(MON_11AM + 90 * MIN, _Stub(s)) == {env: "warmup_limit"}


class _Stub:
    """Real limit checks, fake delivery."""
    def __init__(self, real):
        self.real = real

    def ready(self):
        return True

    def allowed(self, now):
        return self.real.allowed(now)

    def send(self, *a):
        return senders.Result(ok=True, ref="x")


def test_stop_replies_become_opt_outs(env, monkeypatch):
    import whatsapp

    class R:
        def raise_for_status(self): pass
        def json(self):
            return [{"from": "919000100001", "body": "STOP"}, {"from": "919000100002", "body": "Please do not message me again"},
                    {"from": "919000100003", "body": "Yes send me the menu"}, {"from": "919000100004", "body": "I will stop by tomorrow " + "x" * 80}]
    monkeypatch.setattr(whatsapp.requests, "get", lambda *a, **k: R())
    assert whatsapp.process_inbox() == 2
    with db.tx() as t:
        got = {r[0] for r in t.execute("SELECT phone FROM optouts")}
    assert got == {"919000100001", "919000100002"}


def _inbox(monkeypatch, msgs):
    import whatsapp

    class R:
        def raise_for_status(self): pass
        def json(self): return msgs
    monkeypatch.setattr(whatsapp.requests, "get", lambda *a, **k: R())


def test_replies_are_saved_for_leads_and_shown(env, monkeypatch):
    import whatsapp
    phone = db.clean_phone("+91 9" + "0001 00001")                       # lead 1 = +91 90001 00001
    _inbox(monkeypatch, [{"from": phone, "body": "Yes, send the menu"}, {"from": "919000999999", "body": "hi (a personal contact)"},
                         {"from": phone, "body": "[attachment]"}])
    whatsapp.process_inbox()
    rows, total = db.replies()
    assert total == 2 and {r["body"] for r in rows} == {"Yes, send the menu", "[attachment]"}      # stranger ignored
    assert rows[0]["name"] == "Biz 1" and db.unread_replies() == 2
    assert db.query_places(text="Biz 1")[0][0]["replied"] == 2
    db.mark_read(rows[0]["id"])
    assert db.unread_replies() == 1
    db.mark_read()
    assert db.unread_replies() == 0


def test_people_who_replied_get_no_more_messages(env, monkeypatch):
    import whatsapp
    before = camp.audience_count(camp.get(env))
    _inbox(monkeypatch, [{"from": db.clean_phone("+91 90002 00002"), "body": "who is this?"}])
    whatsapp.process_inbox()
    assert camp.audience_count(camp.get(env)) == before - 1


def test_stop_reply_is_saved_and_opts_out(env, monkeypatch):
    import whatsapp
    _inbox(monkeypatch, [{"from": db.clean_phone("+91 90003 00003"), "body": "STOP"}])
    assert whatsapp.process_inbox() == 1
    assert db.replies()[1] == 1
    with db.tx() as t:
        assert t.execute("SELECT COUNT(*) FROM optouts").fetchone()[0] == 1
        assert t.execute("SELECT status FROM places WHERE place_id='p3'").fetchone()[0] == "Not interested"
