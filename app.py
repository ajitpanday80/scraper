import csv
import io
import os

from flask import Flask, Response, abort, redirect, render_template_string, request

import db
from places import PlacesError, search

app = Flask(__name__)

PAGE = """<!doctype html><title>Places Search</title>
<style>body{font:15px system-ui;max-width:1000px;margin:2rem auto;padding:0 1rem}
input[name=q]{padding:.5rem;width:55%}button{padding:.5rem 1rem}
table{border-collapse:collapse;width:100%;margin-top:1rem}td,th{border:1px solid #ccc;padding:.4rem;text-align:left}
.err{color:#b00}</style>
<h2>Google Places search</h2>
<form method=post action=/search><input name=q placeholder="tiffin service in gurgaon" required value="{{ q or '' }}">
<button>Search</button></form>
{% if error %}<p class=err>{{ error }}</p>{% endif %}
{% if rows is not none %}<h3>{{ q }} — {{ rows|length }} results</h3>
<a href="/csv/{{ sid }}">Download CSV</a>
<table><tr><th>Name<th>Phone<th>Address<th>Rating</tr>
{% for r in rows %}<tr><td><a href="{{ r.maps_url }}">{{ r.name }}</a><td>{{ r.phone }}<td>{{ r.address }}<td>{{ r.rating or '' }} ({{ r.rating_count or 0 }})</tr>{% endfor %}</table>
{% else %}<h3>Past searches</h3><ul>{% for h in hist %}<li><a href="/search/{{ h.id }}">{{ h.query }}</a> — {{ h.n }} places, {{ h.created_at }}</li>{% endfor %}</ul>{% endif %}"""


@app.get("/")
def index():
    return render_template_string(PAGE, rows=None, q=None, error=None, hist=db.history())


@app.post("/search")
def do_search():
    q = request.form["q"].strip()
    key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not key:
        return render_template_string(PAGE, rows=None, q=q, error="GOOGLE_PLACES_API_KEY not set", hist=db.history()), 500
    try:
        results = search(q, key)
    except PlacesError as e:
        return render_template_string(PAGE, rows=None, q=q, error=str(e), hist=db.history()), 502
    return redirect(f"/search/{db.save(q, results)}")


@app.get("/search/<int:sid>")
def view(sid):
    q, rows = db.get(sid)
    if q is None:
        abort(404)
    return render_template_string(PAGE, rows=rows, q=q, sid=sid, error=None, hist=None)


@app.get("/csv/<int:sid>")
def csv_download(sid):
    q, rows = db.get(sid)
    if q is None:
        abort(404)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=db.COLS)
    w.writeheader()
    w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=results.csv"})


if __name__ == "__main__":
    app.run(port=5000)
