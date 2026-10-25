import csv
import io
import threading
import uuid

from flask import Flask, Response, jsonify, render_template_string, request

from scraper.maps import scrape

app = Flask(__name__)
JOBS = {}

PAGE = """<!doctype html><title>Maps Scraper</title>
<style>body{font:15px system-ui;max-width:900px;margin:2rem auto;padding:0 1rem}
input{padding:.5rem;width:60%}button{padding:.5rem 1rem}
table{border-collapse:collapse;width:100%;margin-top:1rem}td,th{border:1px solid #ccc;padding:.4rem;text-align:left}</style>
<h2>Google Maps lead scraper</h2>
<form id=f><input id=q placeholder="tiffin service in gurgaon" required>
<input id=n type=number value=20 min=1 max=120 style="width:5rem"> <button>Search</button></form>
<p id=s></p><p id=dl></p><table id=t></table>
<script>
f.onsubmit=async e=>{e.preventDefault();s.textContent='Starting...';dl.innerHTML='';
const r=await fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q.value,limit:+n.value})});
const {id}=await r.json();
const tick=async()=>{const j=await (await fetch('/status/'+id)).json();
s.textContent=j.status+' '+j.done+'/'+j.total;
t.innerHTML='<tr><th>Name<th>Phone<th>Address</tr>'+j.results.map(x=>`<tr><td>${esc(x.name)}<td>${esc(x.phone)}<td>${esc(x.address)}</tr>`).join('');
if(j.status=='done'||j.status=='error'){s.textContent=j.status+(j.error?': '+j.error:'');dl.innerHTML=`<a href="/csv/${id}">Download CSV</a>`;return}
setTimeout(tick,2000)};tick()};
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
</script>"""


def _run(job_id, query, limit):
    job = JOBS[job_id]

    def progress(done, total, results):
        job.update(done=done, total=total, results=list(results))

    try:
        job["results"] = scrape(query, limit=limit, on_progress=progress)
        job["status"] = "done"
    except Exception as e:
        job.update(status="error", error=str(e))


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.post("/start")
def start():
    data = request.get_json()
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status": "running", "done": 0, "total": 0, "results": [], "error": ""}
    limit = max(1, min(int(data.get("limit", 20)), 120))
    threading.Thread(target=_run, args=(job_id, data["query"], limit), daemon=True).start()
    return jsonify(id=job_id)


@app.get("/status/<job_id>")
def status(job_id):
    return jsonify(JOBS.get(job_id, {"status": "error", "error": "unknown job", "done": 0, "total": 0, "results": []}))


@app.get("/csv/<job_id>")
def csv_download(job_id):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["name", "phone", "address", "maps_url"])
    w.writeheader()
    w.writerows(JOBS.get(job_id, {}).get("results", []))
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=results.csv"})


if __name__ == "__main__":
    app.run(port=5000)
