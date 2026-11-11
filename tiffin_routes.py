from flask import Blueprint, flash, redirect, render_template, request, url_for

import db
import tiffin

bp = Blueprint("tiffin", __name__, url_prefix="/mytiffin")


def _form():
    chosen = [k for k in dict.fromkeys(request.form.getlist("kw")) if k in {x["keyword"] for x in db.keywords()}]
    try:
        days = max(0, min(365, int(request.form.get("free_days", 30))))
    except ValueError:
        days = 30
    return chosen, request.form.get("list_on_join") == "1", days


def _render(**kw):
    ctx = dict(rows=tiffin.overview(), history=tiffin.history(), configured=tiffin.configured(),
               chosen=[], list_on_join=True, free_days=30, preview=None, result=None)
    return render_template("mytiffin.html", **{**ctx, **kw})


@bp.get("/")
def page():
    return _render()


@bp.post("/preview")
def preview():
    chosen, loj, days = _form()
    if not chosen:
        flash("Tick at least one search first.", "err")
        return redirect(url_for("tiffin.page"))
    leads = tiffin.pending(chosen)
    return _render(chosen=chosen, list_on_join=loj, free_days=days,
                   preview={"n": len(leads), "sample": [tiffin.kitchen(x) for x in leads[:8]]})


@bp.post("/send")
def send():
    chosen, loj, days = _form()
    if not tiffin.configured():
        flash("MYTIFFIN_API_KEY is not set on the server.", "err")
        return redirect(url_for("tiffin.page"))
    if not chosen:
        flash("Tick at least one search first.", "err")
        return redirect(url_for("tiffin.page"))
    return _render(list_on_join=loj, free_days=days, result=tiffin.push(chosen, loj, days))
