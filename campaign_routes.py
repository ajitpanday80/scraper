import os
import re
import secrets

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   send_from_directory, url_for)

import ai
import camp
import db
import scheduler
from paging import pager

bp = Blueprint("camp", __name__, url_prefix="/campaigns")
PER = (10, 20, 50)
LOG_PER = (20, 50, 100)
MAX_FILES = 10


def _c(cid):
    c = camp.get(cid)
    if not c:
        abort(404)
    return c


def describe(c, now=None):
    """(label, tone, detail) for a campaign's live state."""
    now = now or camp.utcnow()
    st = c["status"]
    if st == "draft":
        return "Draft", "muted", "Finish setup, approve a message, then start."
    if st == "paused":
        return "Paused", "warn", c["note"] or "Paused by you."
    if st == "completed":
        return "Completed", "muted", c["note"] or "Stopped."
    code = scheduler.check(c, now)
    if code == "scheduled":
        return "Scheduled", "info", f"Starts {camp.human(c['start_at'])}."
    if code in ("day", "window"):
        return "Waiting", "warn", f"Outside the sending window. Opens {camp.next_open(c, now)}."
    if camp.stats(c["id"], now)["today"] >= c["daily_cap"]:
        return "Daily limit reached", "warn", f"Resumes tomorrow from {c['window_start']}."
    if not any(v["approved"] for v in camp.variants(c["id"])):
        return "Needs a message", "warn", "Approve at least one message variant."
    if camp.audience_count(c, now) == 0:
        return "Idle", "info", "No eligible leads right now. New matching leads are picked up automatically."
    return "Running", "ok", f"About one message every {round(3600 / c['per_hour'])} seconds."


def _args():
    return {k: v for k, v in request.args.items() if k != "page"}


def _page(options, default):
    per = request.args.get("per", type=int)
    return (per if per in options else default), max(request.args.get("page", 1, type=int), 1)


# ---------- list / create ----------
@bp.get("/")
def index():
    per, page = _page(PER, 10)
    pg = pager(camp.count(), page, per, PER)
    rows, _ = camp.list_page(pg["page"], per)
    for r in rows:
        r["state"] = describe(r)
        r["audience"] = camp.audience_count(r)
    return render_template("campaigns/list.html", rows=rows, pg=pg, args=_args())


@bp.post("/new")
def new():
    name = request.form.get("name", "").strip()[:80]
    if not name:
        flash("Give the campaign a name.", "err")
        return redirect(url_for("camp.index"))
    return redirect(url_for("camp.settings", cid=camp.create(name)))


# ---------- overview + controls ----------
@bp.get("/<int:cid>")
def overview(cid):
    c = _c(cid)
    per, page = _page(LOG_PER, 20)
    log, total = camp.log(cid, page, per)
    pg = pager(total, page, per, LOG_PER)
    log, _ = camp.log(cid, pg["page"], per)
    vs = camp.variants(cid)
    return render_template("campaigns/overview.html", c=c, tab="overview", state=describe(c), stats=camp.stats(cid),
                           audience=camp.audience_count(c), variants=vs, vlabel={v["id"]: i for i, v in enumerate(vs, 1)},
                           log=log, pg=pg, args=_args())


def _launch_errors(c, ack):
    errs = []
    if not c["keywords"]:
        errs.append("Pick at least one keyword to target (Settings).")
    approved = [v for v in camp.variants(c["id"]) if v["approved"]]
    if not approved:
        errs.append("Approve at least one message variant (Messages).")
    if not ack:
        errs.append("Tick the confirmation box before starting.")
    if c["end_at"] and camp.parse(c["end_at"]) <= camp.utcnow():
        errs.append("The stop time is in the past. Change it in Settings.")
    if c["keywords"] and approved and camp.audience_count(c) == 0:
        errs.append("No eligible leads match these settings yet. Run more searches or widen the audience.")
    return errs


@bp.post("/<int:cid>/<action>")
def control(cid, action):
    c = _c(cid)
    if action == "start" and c["status"] in ("draft", "paused", "completed"):
        errs = _launch_errors(c, request.form.get("ack"))
        for e in errs:
            flash(e, "err")
        if not errs:
            camp.set_status(cid, "active")
            flash("Campaign started. " + describe(camp.get(cid))[2], "ok")
    elif action == "pause" and c["status"] == "active":
        camp.set_status(cid, "paused", "Paused by you.")
        flash("Campaign paused.", "ok")
    elif action == "resume" and c["status"] == "paused":
        errs = _launch_errors(c, request.form.get("ack", "1"))
        for e in errs:
            flash(e, "err")
        if not errs:
            camp.set_status(cid, "active")
            flash("Campaign resumed.", "ok")
    elif action == "stop" and c["status"] in ("active", "paused"):
        camp.set_status(cid, "completed", "Stopped by you.")
        flash("Campaign stopped.", "ok")
    elif action == "reset-test":
        camp.reset_test(cid)
        flash("Test run cleared. The campaign can run through its audience again.", "ok")
    elif action == "delete":
        if c["status"] == "active":
            flash("Pause or stop the campaign before deleting it.", "err")
        else:
            for m in camp.media(cid):
                _remove_file(m["filename"])
            camp.delete(cid)
            flash("Campaign deleted.", "ok")
            return redirect(url_for("camp.index"))
    else:
        flash("That action is not available right now.", "err")
    return redirect(url_for("camp.overview", cid=cid))


# ---------- settings ----------
@bp.route("/<int:cid>/settings", methods=["GET", "POST"])
def settings(cid):
    c = _c(cid)
    kws = db.keywords()
    if request.method == "POST":
        clean, chosen, errs = camp.validate(request.form, {k["keyword"] for k in kws})
        if not errs:
            camp.update(cid, clean, chosen)
            flash("Settings saved.", "ok")
            return redirect(url_for("camp.settings", cid=cid))
        for e in errs:
            flash(e, "err")
        c = {**c, **clean, "keywords": chosen, "days": clean["days"].split(","), "statuses": clean["statuses"].split(",")}
    return render_template("campaigns/settings.html", c=c, tab="settings", all_keywords=kws, days=camp.DAYS,
                           languages=camp.LANGUAGES, audience=camp.audience_count(camp.get(cid)), limit=camp.LIMIT,
                           max_per_hour=camp.MAX_PER_HOUR, tz=str(camp.TZ), env_model=os.environ.get("OPENROUTER_MODEL", ""))


# ---------- messages (variants + media) ----------
@bp.get("/<int:cid>/messages")
def messages(cid):
    c = _c(cid)
    return render_template("campaigns/messages.html", c=c, tab="messages", variants=camp.variants(cid), media=camp.media(cid),
                           sample=camp.sample_name(c), ai_key=bool(os.environ.get("OPENROUTER_API_KEY")),
                           model=c["ai_model"] or os.environ.get("OPENROUTER_MODEL", ""))


@bp.post("/<int:cid>/messages/generate")
def generate(cid):
    c = _c(cid)
    n = min(max(request.form.get("n", 5, type=int), 1), 10)
    try:
        out = ai.generate_variants(c["idea"], c["link"], c["language"], n, c["ai_model"] or None)
    except ai.AIError as e:
        flash(str(e), "err")
    else:
        for body in out:
            camp.add_variant(cid, body, source="ai")
        flash(f"Drafted {len(out)} variants. Read each one, edit if needed, then approve the ones you want sent.", "ok")
    return redirect(url_for("camp.messages", cid=cid))


@bp.post("/<int:cid>/messages/add")
def add_variant(cid):
    _c(cid)
    body = request.form.get("body", "").strip()[:1000]
    if not body:
        flash("Write the message first.", "err")
    else:
        camp.add_variant(cid, body)
        flash("Variant added as a draft.", "ok")
    return redirect(url_for("camp.messages", cid=cid))


@bp.post("/<int:cid>/messages/<int:vid>")
def edit_variant(cid, vid):
    _c(cid)
    body = request.form.get("body", "").strip()[:1000]
    action = request.form.get("action", "save")
    if not body:
        flash("A message cannot be empty.", "err")
    elif action == "approve" and not camp.has_opt_out(body):
        camp.update_variant(cid, vid, body=body, approved=0)
        flash("Not approved: add an opt-out line such as “Reply STOP to opt out.”", "err")
    else:
        camp.update_variant(cid, vid, body=body, approved={"approve": 1, "unapprove": 0}.get(action))
        flash({"approve": "Variant approved.", "unapprove": "Variant moved back to draft."}.get(action, "Saved."), "ok")
    return redirect(url_for("camp.messages", cid=cid) + f"#v{vid}")


@bp.post("/<int:cid>/messages/<int:vid>/delete")
def delete_variant(cid, vid):
    _c(cid)
    camp.delete_variant(cid, vid)
    flash("Variant deleted.", "ok")
    return redirect(url_for("camp.messages", cid=cid))


def _remove_file(name):
    try:
        os.remove(os.path.join(camp.UPLOAD_DIR, name))
    except OSError:
        pass


@bp.post("/<int:cid>/media")
def upload(cid):
    _c(cid)
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose a file to attach.", "err")
    elif len(camp.media(cid)) >= MAX_FILES:
        flash(f"You can attach up to {MAX_FILES} files.", "err")
    else:
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        kind = next((k for k, exts in camp.MEDIA_EXT.items() if ext in exts), None)
        f.stream.seek(0, 2)
        size = f.stream.tell()
        f.stream.seek(0)
        if not kind:
            flash("Allowed files: JPG, PNG, WEBP, GIF images, MP4/MOV/WEBM video and PDF.", "err")
        elif size > camp.MAX_MEDIA:
            flash("That file is too large (25 MB maximum).", "err")
        else:
            os.makedirs(camp.UPLOAD_DIR, exist_ok=True)
            stored = f"{secrets.token_hex(12)}.{ext}"
            f.save(os.path.join(camp.UPLOAD_DIR, stored))
            camp.add_media(cid, kind, stored, re.sub(r"[^\w.\- ]", "_", f.filename)[:120], size)
            flash("File attached.", "ok")
    return redirect(url_for("camp.messages", cid=cid))


@bp.post("/<int:cid>/media/<int:mid>/delete")
def delete_media(cid, mid):
    _c(cid)
    name = camp.delete_media(cid, mid)
    if name:
        _remove_file(name)
    flash("File removed.", "ok")
    return redirect(url_for("camp.messages", cid=cid))


@bp.get("/media/<name>")
def media_file(name):
    if not re.fullmatch(r"[0-9a-f]{24}\.[a-z0-9]{2,4}", name):
        abort(404)
    return send_from_directory(os.path.abspath(camp.UPLOAD_DIR), name)


# ---------- opt-outs ----------
@bp.get("/optouts")
def optouts():
    q = request.args.get("q", "").strip()
    per, page = _page(PER, 20)
    pg = pager(camp.optouts(q, 1, 1)[1], page, per, PER)
    rows, _ = camp.optouts(q, pg["page"], per)
    return render_template("campaigns/optouts.html", rows=rows, pg=pg, args=_args(), q=q)


@bp.post("/optouts/add")
def optouts_add():
    added, skipped = camp.add_optouts(request.form.get("numbers", ""), request.form.get("reason", "manual").strip() or "manual")
    flash(f"Added {added} number(s) to the opt-out list." + (f" {skipped} could not be read." if skipped else ""), "ok" if added else "err")
    return redirect(url_for("camp.optouts"))


@bp.post("/optouts/remove")
def optouts_remove():
    camp.remove_optout(request.form.get("phone", ""))
    flash("Number removed from the opt-out list.", "ok")
    return redirect(request.form.get("back") if request.form.get("back", "").startswith("/") and not request.form.get("back", "").startswith("//") else url_for("camp.optouts"))
