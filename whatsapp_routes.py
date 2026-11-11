import requests
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

import db
import whatsapp
from paging import pager

bp = Blueprint("wa", __name__, url_prefix="/whatsapp")


def _usage(s):
    if s["state"] != "ready":
        return {"limit": 0, "age": 0, "today": 0}
    limit, age = whatsapp.daily_limit(s["number"])
    return {"limit": limit, "age": age, "today": whatsapp.sent_today()}


@bp.get("/")
def page():
    s = whatsapp.status()
    return render_template("whatsapp.html", s=s, **_usage(s))


@bp.get("/status.json")
def status_json():
    s = whatsapp.status()
    return jsonify({**s, **_usage(s)})


@bp.post("/switch")
def switch():
    try:
        whatsapp.switch_number()
        flash("Number unlinked. Scan the new QR code with the phone you want to use.", "ok")
    except requests.RequestException:
        flash("Could not reach the WhatsApp bridge. Is the wa-bridge service running?", "err")
    return redirect(url_for("wa.page"))


REPLY_PER = (25, 50, 100)


@bp.get("/replies")
def replies():
    per = request.args.get("per", type=int)
    per = per if per in REPLY_PER else 25
    unread = request.args.get("show") == "unread"
    _, total = db.replies(1, 1, unread)
    pg = pager(total, request.args.get("page", 1, type=int), per, REPLY_PER)
    rows, _ = db.replies(pg["page"], per, unread)
    args = {k: v for k, v in request.args.items() if k != "page"}
    return render_template("replies.html", rows=rows, pg=pg, args=args, unread=unread)


@bp.post("/replies/read")
def replies_read():
    rid = request.form.get("id", type=int)
    db.mark_read(rid)
    if rid is None:
        flash("All replies marked as read.", "ok")
    back = request.form.get("back", "")
    return redirect(back if back.startswith("/") and not back.startswith("//") else url_for("wa.replies"))
