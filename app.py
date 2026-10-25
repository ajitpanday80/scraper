import csv
import hmac
import io
import os
import secrets
import time

from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, session, url_for)

import db
from places import PlacesError, search

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)  # set SECRET_KEY to keep sessions across restarts
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
MAX_FAILS, LOCK_SECONDS = 5, 300
_fails = {}  # ip -> (count, first_fail_time)


def _eq(a, b):
    return hmac.compare_digest(a.encode(), b.encode())


@app.context_processor
def inject():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return {"csrf": session["csrf"]}


@app.before_request
def guard():
    if request.method == "POST":
        if not _eq(request.form.get("csrf", ""), session.get("csrf", "x")):
            abort(400, "Bad CSRF token")
    if request.endpoint not in ("login", "static") and not session.get("admin"):
        return redirect(url_for("login"))


@app.after_request
def headers(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


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


@app.get("/")
def index():
    return render_template("index.html", hist=db.history())


@app.post("/search")
def do_search():
    q = request.form["q"].strip()
    key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not q:
        return redirect(url_for("index"))
    if not key:
        flash("GOOGLE_PLACES_API_KEY is not set on the server.")
        return redirect(url_for("index"))
    try:
        results = search(q, key)
    except PlacesError as e:
        flash(f"Google API error: {e}")
        return redirect(url_for("index"))
    return redirect(url_for("view", sid=db.save(q, results)))


@app.get("/search/<int:sid>")
def view(sid):
    q, rows = db.get(sid)
    if q is None:
        abort(404)
    return render_template("results.html", q=q, rows=rows, sid=sid)


@app.post("/search/<int:sid>/delete")
def delete(sid):
    db.delete_search(sid)
    return redirect(url_for("index"))


@app.get("/places")
def all_places():
    text = request.args.get("q", "").strip()
    only = request.args.get("phone") == "1"
    return render_template("places.html", rows=db.all_places(text, only), q=text, only=only)


def _csv(rows, name):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=db.COLS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={name}.csv"})


@app.get("/csv/<int:sid>")
def csv_search(sid):
    q, rows = db.get(sid)
    if q is None:
        abort(404)
    return _csv(rows, f"search-{sid}")


@app.get("/places.csv")
def csv_all():
    return _csv(db.all_places(request.args.get("q", "").strip(), request.args.get("phone") == "1"), "all-places")


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)))
