"""Campaign data layer: settings, audience selection, variants, media, message log, opt-outs."""
import datetime
import os
import re
from zoneinfo import ZoneInfo

import db

TZ = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Kolkata"))
LIMIT = tuple(os.environ.get("SEND_WINDOW_LIMIT", "09:00-21:00").split("-"))  # earliest start / latest end of any daily window
MAX_PER_HOUR = int(os.environ.get("MAX_PER_HOUR", 120))
MAX_DAILY = 2000
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
LANGUAGES = ["English", "Hinglish", "Hindi"]
FMT = "%Y-%m-%d %H:%M:%S"
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")
MEDIA_EXT = {"image": {"jpg", "jpeg", "png", "webp", "gif"}, "video": {"mp4", "mov", "webm"}, "document": {"pdf"}}
MAX_MEDIA = 25 * 1024 * 1024


# ---------- time (stored as naive UTC strings; shown in TIMEZONE) ----------
def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def parse(s):
    return datetime.datetime.strptime(s, FMT) if s else None


def fmt(dt):
    return dt.strftime(FMT) if dt else None


def to_local(dt):
    return dt.replace(tzinfo=datetime.timezone.utc).astimezone(TZ)


def input_to_utc(s):
    """'2026-10-25T14:30' typed in local time -> naive UTC (None if blank/invalid)."""
    try:
        local = datetime.datetime.strptime(s.strip(), "%Y-%m-%dT%H:%M")
    except (ValueError, AttributeError):
        return None
    return local.replace(tzinfo=TZ).astimezone(datetime.timezone.utc).replace(tzinfo=None)


def utc_to_input(s):
    return to_local(parse(s)).strftime("%Y-%m-%dT%H:%M") if s else ""


def human(s):
    return to_local(parse(s)).strftime("%a %d %b, %H:%M") if s else "—"


def local_day_start_utc(now):
    local = to_local(now)
    mid = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return mid.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def next_open(c, now):
    """Local datetime string for when the sending window next opens."""
    local = to_local(now)
    for d in range(8):
        day = local + datetime.timedelta(days=d)
        if str(day.weekday()) in c["days"]:
            h, m = map(int, c["window_start"].split(":"))
            opens = day.replace(hour=h, minute=m, second=0, microsecond=0)
            if opens > local:
                return opens.strftime("%a %d %b, %H:%M")
    return "—"


# ---------- campaigns ----------
def _row(r):
    c = dict(r)
    c["days"] = [d for d in (c["days"] or "").split(",") if d]
    c["statuses"] = [s for s in (c["statuses"] or "").split(",") if s]
    return c


def create(name):
    with db.tx() as c:
        cid = c.execute("INSERT INTO campaigns(name, created_at) VALUES (?,?)", (name, fmt(utcnow()))).lastrowid
    return cid


def get(cid):
    with db.tx() as c:
        r = c.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
        if not r:
            return None
        camp = _row(r)
        camp["keywords"] = [k[0] for k in c.execute("SELECT keyword FROM campaign_keywords WHERE campaign_id=? ORDER BY keyword", (cid,))]
    return camp


def active():
    with db.tx() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM campaigns WHERE status='active' ORDER BY id")]
    return [get(i) for i in ids]


def list_page(page, per):
    with db.tx() as c:
        total = c.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
        rows = [dict(r) for r in c.execute("SELECT id FROM campaigns ORDER BY id DESC LIMIT ? OFFSET ?", (per, (page - 1) * per))]
    return [{**get(r["id"]), **stats(r["id"])} for r in rows], total


def count():
    with db.tx() as c:
        return c.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]


SETTING_COLS = ["name", "idea", "link", "language", "ai_model", "start_at", "end_at", "window_start", "window_end", "days",
                "per_hour", "daily_cap", "max_messages", "min_gap_days", "statuses", "mobile_only", "mark_contacted"]


def update(cid, clean, keywords):
    with db.tx() as c:
        c.execute(f"UPDATE campaigns SET {','.join(k + '=?' for k in SETTING_COLS)} WHERE id=?",
                  [clean[k] for k in SETTING_COLS] + [cid])
        c.execute("DELETE FROM campaign_keywords WHERE campaign_id=?", (cid,))
        c.executemany("INSERT INTO campaign_keywords VALUES (?,?)", [(cid, k) for k in keywords])


def set_status(cid, status, note=""):
    with db.tx() as c:
        c.execute("UPDATE campaigns SET status=?, note=? WHERE id=?", (status, note, cid))


def delete(cid):
    with db.tx() as c:
        for t in ("campaign_keywords", "campaign_variants", "campaign_media", "messages"):
            c.execute(f"DELETE FROM {t} WHERE campaign_id=?", (cid,))
        c.execute("DELETE FROM campaigns WHERE id=?", (cid,))


def validate(form, known_keywords):
    """Turn the settings form into clean values. Returns (clean, keywords, errors)."""
    errs, d = [], {}

    def num(key, lo, hi, label, default):
        try:
            v = int(form.get(key, default))
        except (TypeError, ValueError):
            errs.append(f"{label} must be a number.")
            return default
        if not lo <= v <= hi:
            errs.append(f"{label} must be between {lo} and {hi}.")
        return min(max(v, lo), hi)

    d["name"] = form.get("name", "").strip()[:80]
    if not d["name"]:
        errs.append("Give the campaign a name.")
    d["idea"] = form.get("idea", "").strip()[:2000]
    d["link"] = form.get("link", "").strip()[:500]
    if d["link"] and not re.match(r"^https?://\S+$", d["link"]):
        errs.append("The link must start with http:// or https://")
    d["language"] = form.get("language") if form.get("language") in LANGUAGES else "English"
    d["ai_model"] = form.get("ai_model", "").strip()[:120]
    if d["ai_model"] and not re.match(r"^[\w.\-:/]+$", d["ai_model"]):
        errs.append("The AI model name has invalid characters.")
    start, end = input_to_utc(form.get("start_at", "")), input_to_utc(form.get("end_at", ""))
    d["start_at"], d["end_at"] = fmt(start), fmt(end)
    if start and end and end <= start:
        errs.append("The stop time must be after the start time.")
    ws, we = form.get("window_start", "10:00"), form.get("window_end", "19:00")
    if not (re.match(r"^\d\d:\d\d$", ws) and re.match(r"^\d\d:\d\d$", we)):
        errs.append("Enter the daily sending hours as times.")
        ws, we = "10:00", "19:00"
    elif not ws < we:
        errs.append("The daily window must end after it starts.")
    elif ws < LIMIT[0] or we > LIMIT[1]:
        errs.append(f"Daily sending hours must stay between {LIMIT[0]} and {LIMIT[1]}.")
    d["window_start"], d["window_end"] = ws, we
    days = sorted({x for x in form.getlist("days") if x in "0123456" and len(x) == 1})
    if not days:
        errs.append("Pick at least one day of the week.")
    d["days"] = ",".join(days)
    d["per_hour"] = num("per_hour", 1, MAX_PER_HOUR, "Messages per hour", 30)
    d["daily_cap"] = num("daily_cap", 1, MAX_DAILY, "Daily limit", 200)
    d["max_messages"] = num("max_messages", 1, 10, "Maximum messages per person", 3)
    d["min_gap_days"] = num("min_gap_days", 1, 365, "Minimum days between messages to the same person", 3)
    sts = [s for s in form.getlist("statuses") if s in db.STATUSES]
    if not sts:
        errs.append("Pick at least one lead status to include.")
    d["statuses"] = ",".join(sts)
    d["mobile_only"] = 1 if form.get("mobile_only") else 0
    d["mark_contacted"] = 1 if form.get("mark_contacted") else 0
    kws = [k for k in dict.fromkeys(form.getlist("kw")) if k in known_keywords]
    return d, kws, errs


# ---------- audience ----------
def _audience(c, now):
    q = lambda xs: ",".join("?" * len(xs))
    w = ["COALESCE(p.intl_phone,'') != ''",
         f"COALESCE(p.status,'New') IN ({q(c['statuses'])})",
         f"p.place_id IN (SELECT sp.place_id FROM search_places sp JOIN searches s ON s.id=sp.search_id WHERE s.query IN ({q(c['keywords'])}))",
         "NOT EXISTS (SELECT 1 FROM messages m WHERE m.campaign_id=? AND m.place_id=p.place_id)",
         "NOT EXISTS (SELECT 1 FROM optouts o WHERE o.phone = replace(replace(replace(p.intl_phone,' ',''),'+',''),'-',''))"]
    a = [*c["statuses"], *c["keywords"], c["id"]]
    if c["mobile_only"]:
        w.append("p.intl_phone GLOB '+91 [6-9]*'")
    # Per-person limits count real sends to the same phone number across ALL campaigns (test-mode sends don't count).
    num = "replace(replace(replace(p.intl_phone,' ',''),'+',''),'-','')"
    w.append(f"(SELECT COUNT(*) FROM messages m3 WHERE m3.phone = {num} AND m3.status IN ('sent','sending')) < ?")
    a.append(c["max_messages"])
    w.append(f"NOT EXISTS (SELECT 1 FROM messages m2 WHERE m2.phone = {num} AND m2.status IN ('sent','sending') AND m2.sent_at > ?)")
    a.append(fmt(now - datetime.timedelta(days=c["min_gap_days"])))
    return " AND ".join(w), a


def audience_count(c, now=None):
    if not c["keywords"] or not c["statuses"]:
        return 0
    where, args = _audience(c, now or utcnow())
    with db.tx() as t:
        return t.execute(f"SELECT COUNT(*) FROM places p WHERE {where}", args).fetchone()[0]


def sample_name(c):
    if not c["keywords"] or not c["statuses"]:
        return "Sample Business"
    where, args = _audience(c, utcnow())
    with db.tx() as t:
        r = t.execute(f"SELECT p.name FROM places p WHERE {where} ORDER BY p.added_at LIMIT 1", args).fetchone()
    return r[0] if r else "Sample Business"


# ---------- variants ----------
def variants(cid):
    with db.tx() as c:
        return [dict(r) for r in c.execute(
            "SELECT v.*, (SELECT COUNT(*) FROM messages m WHERE m.variant_id=v.id AND m.status IN ('sent','test')) AS sent "
            "FROM campaign_variants v WHERE v.campaign_id=? ORDER BY v.id", (cid,))]


def add_variant(cid, body, source="manual", approved=0):
    with db.tx() as c:
        return c.execute("INSERT INTO campaign_variants(campaign_id, body, approved, source, created_at) VALUES (?,?,?,?,?)",
                         (cid, body, approved, source, fmt(utcnow()))).lastrowid


def update_variant(cid, vid, body=None, approved=None):
    with db.tx() as c:
        if body is not None:
            c.execute("UPDATE campaign_variants SET body=? WHERE id=? AND campaign_id=?", (body, vid, cid))
        if approved is not None:
            c.execute("UPDATE campaign_variants SET approved=? WHERE id=? AND campaign_id=?", (approved, vid, cid))


def delete_variant(cid, vid):
    with db.tx() as c:
        c.execute("DELETE FROM campaign_variants WHERE id=? AND campaign_id=?", (vid, cid))


def render(body, name, link):
    return body.replace("{name}", name or "there").replace("{link}", link or "")


def has_opt_out(body):
    return bool(re.search(r"\bstop\b", body, re.I))


# ---------- media ----------
def media(cid):
    with db.tx() as c:
        return [dict(r) for r in c.execute("SELECT * FROM campaign_media WHERE campaign_id=? ORDER BY id", (cid,))]


def add_media(cid, kind, filename, original, size):
    with db.tx() as c:
        c.execute("INSERT INTO campaign_media(campaign_id, kind, filename, original, size) VALUES (?,?,?,?,?)",
                  (cid, kind, filename, original, size))


def delete_media(cid, mid):
    with db.tx() as c:
        r = c.execute("SELECT filename FROM campaign_media WHERE id=? AND campaign_id=?", (mid, cid)).fetchone()
        c.execute("DELETE FROM campaign_media WHERE id=? AND campaign_id=?", (mid, cid))
    return r[0] if r else None


# ---------- sending: atomic claim, outcome, stats ----------
def claim_next(c, now):
    """Atomically pick the next lead + variant and record it as 'sending'.
    Returns (reason, job): reason is 'ready' or why nothing was claimed."""
    interval = 3600 / c["per_hour"]
    with db.tx() as t:
        t.execute("BEGIN IMMEDIATE")  # serialises workers so a lead is never claimed twice
        last = t.execute("SELECT MAX(sent_at) FROM messages WHERE campaign_id=?", (c["id"],)).fetchone()[0]
        if last and (now - parse(last)).total_seconds() < interval:
            return "interval", None
        today = t.execute("SELECT COUNT(*) FROM messages WHERE campaign_id=? AND sent_at>=?",
                          (c["id"], fmt(local_day_start_utc(now)))).fetchone()[0]
        if today >= c["daily_cap"]:
            return "cap", None
        v = t.execute(
            "SELECT v.*, (SELECT COUNT(*) FROM messages m WHERE m.variant_id=v.id) AS n FROM campaign_variants v "
            "WHERE v.campaign_id=? AND v.approved=1 ORDER BY n, v.id LIMIT 1", (c["id"],)).fetchone()
        if not v:
            return "no_variants", None
        if not c["keywords"] or not c["statuses"]:
            return "no_audience", None
        where, args = _audience(c, now)
        lead = t.execute(f"SELECT p.place_id, p.name, p.intl_phone FROM places p WHERE {where} ORDER BY p.added_at, p.place_id LIMIT 1", args).fetchone()
        if not lead:
            return "no_audience", None
        phone = re.sub(r"\D", "", lead["intl_phone"])
        text = render(v["body"], lead["name"], c["link"])
        mid = t.execute("INSERT INTO messages(campaign_id, place_id, phone, variant_id, body, status, sent_at) VALUES (?,?,?,?,?,'sending',?)",
                        (c["id"], lead["place_id"], phone, v["id"], text, fmt(now))).lastrowid
        return "ready", {"id": mid, "place_id": lead["place_id"], "phone": phone, "text": text, "variant": dict(v)}


def finish_message(mid, status, error=""):
    with db.tx() as t:
        t.execute("UPDATE messages SET status=?, error=? WHERE id=?", (status, error[:300], mid))


def recent_failures(cid, n=5):
    with db.tx() as t:
        rows = [r[0] for r in t.execute("SELECT status FROM messages WHERE campaign_id=? AND status IN ('sent','test','failed') ORDER BY id DESC LIMIT ?", (cid, n))]
    return len(rows) == n and all(s == "failed" for s in rows)


def clear_stale(now):
    with db.tx() as t:
        t.execute("UPDATE messages SET status='failed', error='interrupted before it was sent' WHERE status='sending' AND sent_at < ?",
                  (fmt(now - datetime.timedelta(minutes=10)),))


def reset_test(cid):
    with db.tx() as t:
        t.execute("DELETE FROM messages WHERE campaign_id=? AND status='test'", (cid,))


def stats(cid, now=None):
    now = now or utcnow()
    with db.tx() as t:
        one = lambda sql, *a: t.execute(sql, a).fetchone()[0]
        return {
            "sent": one("SELECT COUNT(*) FROM messages WHERE campaign_id=? AND status='sent'", cid),
            "test": one("SELECT COUNT(*) FROM messages WHERE campaign_id=? AND status='test'", cid),
            "failed": one("SELECT COUNT(*) FROM messages WHERE campaign_id=? AND status='failed'", cid),
            "today": one("SELECT COUNT(*) FROM messages WHERE campaign_id=? AND sent_at>=?", cid, fmt(local_day_start_utc(now))),
        }


def log(cid, page, per):
    with db.tx() as t:
        total = t.execute("SELECT COUNT(*) FROM messages WHERE campaign_id=?", (cid,)).fetchone()[0]
        rows = [dict(r) for r in t.execute(
            "SELECT m.*, p.name FROM messages m LEFT JOIN places p ON p.place_id=m.place_id WHERE m.campaign_id=? "
            "ORDER BY m.id DESC LIMIT ? OFFSET ?", (cid, per, (page - 1) * per))]
    return rows, total


# ---------- opt-outs ----------
def normalize_phone(s):
    d = re.sub(r"\D", "", s or "")
    if len(d) == 10:
        d = "91" + d
    return d if 11 <= len(d) <= 15 else ""


def add_optouts(text, reason="manual"):
    added = skipped = 0
    with db.tx() as t:
        for raw in re.split(r"[\n,;]+", text or ""):
            if not raw.strip():
                continue
            p = normalize_phone(raw)
            if not p:
                skipped += 1
                continue
            added += t.execute("INSERT OR IGNORE INTO optouts VALUES (?,?,?)", (p, reason[:100], fmt(utcnow()))).rowcount
    return added, skipped


def remove_optout(phone):
    with db.tx() as t:
        t.execute("DELETE FROM optouts WHERE phone=?", (phone,))


def optouts(text, page, per):
    where, args = ("WHERE phone LIKE ?", [f"%{re.sub(r'[^0-9]', '', text)}%"]) if re.sub(r"\D", "", text or "") else ("", [])
    with db.tx() as t:
        total = t.execute(f"SELECT COUNT(*) FROM optouts {where}", args).fetchone()[0]
        rows = [dict(r) for r in t.execute(f"SELECT * FROM optouts {where} ORDER BY added_at DESC, phone LIMIT ? OFFSET ?", args + [per, (page - 1) * per])]
    return rows, total
