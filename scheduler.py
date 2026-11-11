"""Background sender: once every few seconds, each active campaign may send at most one message."""
import datetime
import logging
import os
import threading
import time

import camp
import db
import senders
import whatsapp

log = logging.getLogger("scheduler")
_started = False


def check(c, now):
    """Why a campaign would or would not send right now (no side effects)."""
    if c["end_at"] and now >= camp.parse(c["end_at"]):
        return "ended"
    if c["start_at"] and now < camp.parse(c["start_at"]):
        return "scheduled"
    local = camp.to_local(now, camp.tz_of(c))
    if str(local.weekday()) not in c["days"]:
        return "day"
    if not (c["window_start"] <= local.strftime("%H:%M") < c["window_end"]):
        return "window"
    return "open"


def process(c, now, sender=None):
    code = check(c, now)
    if code == "ended":
        camp.set_status(c["id"], "completed", "Reached its scheduled stop time.")
        return code
    if code != "open":
        return code
    sender = sender or senders.get(c["channel"])
    if hasattr(sender, "ready") and not sender.ready():  # e.g. WhatsApp logged out or switching number: wait, don't fail leads
        return "sender_offline"
    if hasattr(sender, "allowed") and sender.allowed(now):
        return "warmup_limit"
    reason, job = camp.claim_next(c, now)
    if reason != "ready":
        return reason
    if not camp.valid_phone(job["phone"]):  # last line of defence: never hand a lead without a number to a sender
        camp.finish_message(job["id"], "failed", "no valid phone number")
        return "no_phone"
    try:
        res = sender.send(job["phone"], job["text"], camp.media(c["id"]), job["variant"])
    except Exception as e:  # a provider crash must not kill the loop
        res = senders.Result(ok=False, error=f"{type(e).__name__}: {e}")
    if not res.ok:
        camp.finish_message(job["id"], "failed", res.error)
        if not res.lead_error and camp.recent_failures(c["id"]):
            camp.set_status(c["id"], "paused", "Paused automatically: 5 messages in a row failed. Check the sender, then resume.")
        return "failed"
    camp.finish_message(job["id"], "test" if res.simulated else "sent", res.ref)
    if not res.simulated and c["mark_contacted"]:
        with db.tx() as t:
            t.execute("UPDATE places SET status='Contacted' WHERE place_id=? AND COALESCE(status,'New')='New'", (job["place_id"],))
    return "sent"


def tick(now=None, sender=None):
    now = now or camp.utcnow()
    camp.clear_stale(now)
    return {c["id"]: process(c, now, sender) for c in camp.active()}


def start_background(interval=5):
    global _started
    if _started or os.environ.get("DISABLE_SCHEDULER") == "1":
        return
    _started = True

    def loop():
        while True:
            try:
                whatsapp.process_inbox()
                tick()
            except Exception:
                log.exception("scheduler tick failed")
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True, name="campaign-scheduler").start()
