(function () {
  var csrf = (document.querySelector('meta[name=csrf]') || {}).content;

  var tt; function flash() { var t = document.getElementById('toast'); if (!t) return; t.classList.add('show'); clearTimeout(tt); tt = setTimeout(function () { t.classList.remove('show'); }, 1400); }

  function save(pid, body, el) {
    fetch('/lead/' + encodeURIComponent(pid), {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF': csrf }, body: JSON.stringify(body)
    }).then(function (r) { if (r.ok) flash(el); else alert('Could not save (' + r.status + '). Reload the page.'); });
  }

  document.addEventListener('change', function (e) {
    if (e.target.matches('select.st')) { e.target.dataset.s = e.target.value; save(e.target.dataset.pid, { status: e.target.value }, e.target); }
    if (e.target.matches('input.notes')) save(e.target.dataset.pid, { notes: e.target.value }, e.target);
  });

  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-copy]'); if (!b) return;
    var t = b.dataset.copy, done = function () { b.lastChild.textContent = 'Copied'; setTimeout(function () { b.lastChild.textContent = 'Copy'; }, 1000); };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(t).then(done);
    else { var a = document.createElement('textarea'); a.value = t; document.body.appendChild(a); a.select(); document.execCommand('copy'); a.remove(); done(); }
  });

  var j = document.getElementById('job');
  if (j) {
    var bar = j.querySelector('.progress i'), spin = document.getElementById('spin'), txt = document.getElementById('jobtext');
    (function poll() {
      fetch('/job/' + j.dataset.id + '.json').then(function (r) { return r.json(); }).then(function (d) {
        bar.style.width = (d.total ? Math.round(100 * d.done / d.total) : 0) + '%';
        txt.textContent = 'Searched ' + d.done + ' of ' + d.total + ' · ' + d.found + ' unique businesses found';
        if (d.status === 'running') return setTimeout(poll, 1500);
        if (d.url && !d.message) return location.href = d.url;
        spin.style.display = 'none';
        var m = document.getElementById('jobmsg');
        m.innerHTML = '';
        if (d.message) { var p = document.createElement('p'); p.className = 'flash'; p.textContent = d.message; m.appendChild(p); }
        if (d.url) { var a = document.createElement('a'); a.className = 'btn'; a.href = d.url; a.textContent = 'View ' + d.found + ' saved results'; m.appendChild(a); }
        else { var b = document.createElement('a'); b.className = 'btn ghost'; b.href = '/'; b.textContent = 'Back'; m.appendChild(b); }
      }).catch(function () { setTimeout(poll, 3000); });
    })();
  }
})();
