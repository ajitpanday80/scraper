"""WhatsApp sender: talks to the wa-bridge Node service (whatsapp-web.js) over localhost.

Unofficial automation: WhatsApp may ban the linked number. Use a dedicated, warmed-up SIM, never a personal one.
Switching numbers needs no config change: use "Switch number" on the WhatsApp page and scan the new QR code.
"""
import os
import re

import requests

import camp
import db
from senders import Result

URL = os.environ.get("WA_BRIDGE_URL", "http://127.0.0.1:3399")
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")


def _headers():
    return {"X-Token": os.environ.get("WA_BRIDGE_TOKEN", "")}


def status():
    """{'state': ready|qr|starting|disconnected|offline, 'qr': data-URL, 'number', 'name'}"""
    try:
        r = requests.get(f"{URL}/status", headers=_headers(), timeout=5)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        return {"state": "offline", "qr": "", "number": "", "name": ""}


def switch_number():
    """Unlink the current number and start a fresh QR login."""
    r = requests.post(f"{URL}/logout", headers=_headers(), timeout=30)
    r.raise_for_status()


# ---------- anti-ban: warm-up ramp ----------
# (days since this number was first linked here, max messages per day). A fresh number sending a lot is the classic ban trigger.
RAMP = [(3, 10), (7, 20), (14, 30), (10**6, 50)]
WARMUP = os.environ.get("WA_WARMUP", "1") != "0"
MAX_PER_DAY = int(os.environ.get("WA_MAX_PER_DAY", 50))


def first_seen(number, now):
    with db.tx() as t:
        t.execute("INSERT OR IGNORE INTO wa_numbers VALUES (?,?)", (number, camp.fmt(now)))
        return camp.parse(t.execute("SELECT first_seen FROM wa_numbers WHERE number=?", (number,)).fetchone()[0])


def daily_limit(number, now=None):
    """(limit, age_in_days) for a linked number. Every new number starts the ramp from day 0."""
    now = now or camp.utcnow()
    age = (now - first_seen(number, now)).days
    limit = next(n for days, n in RAMP if age < days) if WARMUP else MAX_PER_DAY
    return min(limit, MAX_PER_DAY), age


def sent_today(now=None):
    now = now or camp.utcnow()
    with db.tx() as t:
        return t.execute("SELECT COUNT(*) FROM messages m JOIN campaigns k ON k.id=m.campaign_id WHERE k.channel='whatsapp' "
                         "AND m.status IN ('sent','sending') AND m.sent_at>=?", (camp.fmt(camp.local_day_start_utc(now)),)).fetchone()[0]


# ---------- anti-ban: honour opt-outs ----------
STOP = re.compile(r"\b(stop|unsubscribe|opt[ -]?out|do not (message|contact|disturb)|don'?t (message|contact|disturb)|"
                  r"remove me|not interested|band kar\w*|mat bhej\w*)\b", re.I)


def process_inbox():
    """Handle incoming WhatsApp messages. Replies from leads (or numbers we messaged) are saved for the Replies page;
    STOP-style replies also opt the sender out and mark the lead 'Not interested'. Returns how many were opted out."""
    try:
        r = requests.get(f"{URL}/inbox", headers=_headers(), timeout=5)
        r.raise_for_status()
        msgs = r.json()
    except (requests.RequestException, ValueError):
        return 0
    n = 0
    for m in msgs:
        phone, body = re.sub(r"\D", "", m.get("from", "")), (m.get("body") or "").strip()
        if not 11 <= len(phone) <= 15 or not body:
            continue
        known, pid = db.lead_for_phone(phone)
        if known:
            db.add_reply(phone, pid, body)
        if len(body) <= 80 and STOP.search(body):
            camp.add_optouts(phone, "replied: " + body[:40])
            with db.tx() as t:
                t.execute("UPDATE places SET status='Not interested' WHERE replace(replace(replace(COALESCE(intl_phone,''),' ',''),'+',''),'-','')=?", (phone,))
            n += 1
    return n


class WhatsAppSender:
    name = "WhatsApp (linked number, whatsapp-web.js)"
    simulated = False

    def ready(self):
        return status()["state"] == "ready"

    def allowed(self, now):
        """None if this number may send now, else why not (daily warm-up limit reached)."""
        st = status()
        if st["state"] != "ready":
            return "not connected"
        limit, age = daily_limit(st["number"], now)
        return None if sent_today(now) < limit else f"daily limit {limit} reached (number linked {age} days ago)"

    def send(self, phone, text, media, variant):
        files = [{"path": os.path.abspath(os.path.join(UPLOAD_DIR, m["filename"])), "kind": m["kind"]} for m in media or []]
        try:
            r = requests.post(f"{URL}/send", json={"phone": phone, "text": text, "media": files},
                              headers=_headers(), timeout=120).json()
        except (requests.RequestException, ValueError) as e:
            return Result(ok=False, error=f"WhatsApp bridge unreachable: {type(e).__name__}")
        if r.get("ok"):
            return Result(ok=True, ref=r.get("id", ""))
        # lead=True: this one number can't be messaged; the sender itself is fine, so it must not count towards auto-pause
        return Result(ok=False, error=r.get("error", "unknown error"), lead_error=bool(r.get("lead")))
