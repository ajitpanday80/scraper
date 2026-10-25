import csv
import hmac
import io
import os
import re
import secrets
import threading
import time

from flask import (Flask, Response, abort, flash, jsonify, redirect,
                   render_template, request, session, url_for)

import db
from places import PlacesError, search

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)  # set SECRET_KEY to keep sessions across restarts
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
DAILY_LIMIT = int(os.environ.get("MAX_API_CALLS_PER_DAY", 300))
MAX_AREAS = 50
MAX_FAILS, LOCK_SECONDS = 5, 300
_fails = {}   # ip -> (count, first_fail_time)
JOBS = {}     # in-memory; the app must run with a single gunicorn worker


def _eq(a, b):
    return hmac.compare_digest(a.encode(), b.encode())


def wa_number(intl):
    """Digits for a wa.me link; only Indian mobile numbers (6-9 prefix) can have WhatsApp."""
    d = re.sub(r"\D", "", intl or "")
    return d if len(d) == 12 and d.startswith("91") and d[2] in "6789" else ""


app.jinja_env.filters["wa"] = wa_number
app.jinja_env.filters["tel"] = lambda s: re.sub(r"[^\d+]", "", s or "")


@app.context_processor
def inject():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return {"csrf": session["csrf"], "statuses": db.STATUSES}


@app.before_request
def guard():
    if request.method == "POST":
        sent = request.form.get("csrf") or request.headers.get("X-CSRF", "")
        if not _eq(sent, session.get("csrf", "x")):
            abort(400, "Bad CSRF token")
    if request.endpoint not in ("login", "static") and not session.get("admin"):
        return redirect(url_for("login"))


@app.after_request
def headers(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


# ---------- auth ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.remote_addr
        n, t0 = _fails.get(ip, (0, 0))
        if n >= MAX_FAILS and time.time() - t0 < LOCK_SECONDS:
            flash("Too many attempts. Try again in a few minutes.")
            return render_template("login.html"), 429
        pw = os.environ.get("ADMIN_PASSWORD")
        ok = bool(pw) and _eq(request.form.get("username", ""), ADMIN_USER) and _eq(request.form.get("password", ""), pw)
        if ok:
            _fails.pop(ip, None)
            session.clear()
            session["admin"] = True
            return redirect(url_for("index"))
        _fails[ip] = (n + 1, t0 if n else time.time())
        flash("Invalid username or password." if pw else "ADMIN_PASSWORD is not set on the server.")
        return render_template("login.html"), 401
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- search jobs ----------
def _tick():
    if db.usage_today() >= DAILY_LIMIT:
        raise PlacesError(f"Daily API limit of {DAILY_LIMIT} requests reached. Raise MAX_API_CALLS_PER_DAY to continue.")
    db.add_usage(1)


def run_job(jid, label, queries):
    job = JOBS[jid]
    found = {}
    try:
        key = os.environ["GOOGLE_PLACES_API_KEY"]
        for q in queries:
            for r in search(q, key, on_request=_tick):
                found.setdefault(r["place_id"], r)
            job["done"] += 1
            job["found"] = len(found)
    except KeyError:
        job["message"] = "GOOGLE_PLACES_API_KEY is not set on the server."
    except PlacesError as e:
        job["message"] = str(e)
    except Exception as e:  # never leave a job stuck in "running"
        job["message"] = f"Unexpected error: {e}"
    finally:
        if found:
            job["sid"], job["new"] = db.save(label, list(found.values()))
        job["status"] = "done" if found else "error"


def _running():
    return any(j["status"] == "running" for j in JOBS.values())


@app.post("/search")
def do_search():
    keyword = request.form.get("q", "").strip()
    if not keyword:
        return redirect(url_for("index"))
    areas = list(dict.fromkeys(a.strip() for a in request.form.get("areas", "").splitlines() if a.strip()))
    if len(areas) > MAX_AREAS:
        flash(f"Only the first {MAX_AREAS} areas were used.")
        areas = areas[:MAX_AREAS]
    if _running():
        flash("A search is already running. Wait for it to finish.")
        return redirect(url_for("index"))
    queries = [f"{keyword} in {a}" for a in areas] or [keyword]
    label = f"{keyword} · {len(areas)} areas" if areas else keyword
    jid = secrets.token_hex(6)
    JOBS[jid] = {"status": "running", "label": label, "done": 0, "total": len(queries),
                 "found": 0, "sid": None, "new": 0, "message": ""}
    threading.Thread(target=run_job, args=(jid, label, queries), daemon=True).start()
    return redirect(url_for("job", jid=jid))


@app.get("/job/<jid>")
def job(jid):
    if jid not in JOBS:
        abort(404)
    return render_template("job.html", jid=jid, job=JOBS[jid])


@app.get("/job/<jid>.json")
def job_json(jid):
    if jid not in JOBS:
        abort(404)
    j = dict(JOBS[jid])
    j["url"] = url_for("view", sid=j["sid"]) if j["sid"] else ""
    return jsonify(j)


# ---------- pages ----------
@app.get("/")
def index():
    return render_template("index.html", stats=db.stats(), hist=db.history(8),
                           used=db.usage_today(), limit=DAILY_LIMIT, max_areas=MAX_AREAS)


def _filters():
    return dict(text=request.args.get("q", "").strip(), status=request.args.get("status", ""),
                phone=request.args.get("phone") == "1", mobile=request.args.get("mobile") == "1",
                sort=request.args.get("sort", "newest"))


def _paged(search_id=None):
    f = _filters()
    page = max(request.args.get("page", 1, type=int), 1)
    rows, total = db.query_places(**f, page=page, search_id=search_id)
    pages = max((total + 49) // 50, 1)
    args = {k: v for k, v in request.args.items() if k != "page"}
    return dict(rows=rows, total=total, page=page, pages=pages, f=f, args=args)


@app.get("/leads")
def leads():
    return render_template("leads.html", **_paged())


@app.get("/search/<int:sid>")
def view(sid):
    info = db.search_info(sid)
    if not info:
        abort(404)
    return render_template("results.html", info=info, sid=sid, **_paged(sid))


@app.post("/search/<int:sid>/delete")
def delete(sid):
    db.delete_search(sid)
    flash("Search deleted. Leads you had worked on were kept.")
    return redirect(url_for("index"))


@app.post("/lead/<pid>")
def lead(pid):
    data = request.get_json(silent=True) or {}
    status, notes = data.get("status"), data.get("notes")
    if status is not None and status not in db.STATUSES:
        abort(400)
    if notes is not None:
        notes = str(notes)[:2000]
    if not db.update_lead(pid, status, notes):
        abort(404)
    return jsonify(ok=True)


# ---------- export ----------
def _csv(rows, name):
    buf = io.StringIO()
    fields = ["name", "phone", "intl_phone", "address", "rating", "rating_count", "status", "notes", "maps_url"]
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    # BOM so Excel reads the UTF-8 correctly
    return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={name}.csv"})


@app.get("/leads.csv")
def csv_all():
    rows, _ = db.query_places(**_filters(), per_page=None)
    return _csv(rows, "leads")


@app.get("/search/<int:sid>.csv")
def csv_search(sid):
    if not db.search_info(sid):
        abort(404)
    rows, _ = db.query_places(**_filters(), per_page=None, search_id=sid)
    return _csv(rows, f"search-{sid}")


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 6761)))
