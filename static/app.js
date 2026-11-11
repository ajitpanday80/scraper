(function () {
  'use strict';
  var csrf = (document.querySelector('meta[name=csrf]') || {}).content;
  var CHEV = '<svg class="dd-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
  var CHECK = '<svg class="ck" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';

  /* ---------- inline feedback next to the control (no popups) ---------- */
  function feedback(el, ok) {
    var host = el.closest('td') || el.parentNode;
    var old = host.querySelector('.sv'); if (old) old.remove();
    var s = document.createElement('span');
    s.className = 'sv' + (ok ? '' : ' err');
    s.textContent = ok ? 'Saved' : 'Not saved - reload the page and try again';
    host.appendChild(s);
    if (ok) setTimeout(function () { s.remove(); }, 2300);
  }

  function save(pid, body, el) {
    fetch('/lead/' + encodeURIComponent(pid), {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF': csrf }, body: JSON.stringify(body)
    }).then(function (r) { feedback(el, r.ok); }).catch(function () { feedback(el, false); });
  }

  document.addEventListener('change', function (e) {
    var t = e.target;
    if (t.matches('select.st')) { t.dataset.s = t.value; save(t.dataset.pid, { status: t.value }, t); }
    else if (t.matches('input.notes')) save(t.dataset.pid, { notes: t.value }, t);
    else if (t.hasAttribute('data-auto') && t.closest('form')) t.closest('form').submit();
  });

  /* ---------- copy ---------- */
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-copy]'); if (!b) return;
    var t = b.dataset.copy, done = function () { b.lastChild.textContent = 'Copied'; setTimeout(function () { b.lastChild.textContent = 'Copy'; }, 1100); };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(t).then(done);
    else { var a = document.createElement('textarea'); a.value = t; document.body.appendChild(a); a.select(); document.execCommand('copy'); a.remove(); done(); }
  });

  /* ---------- inline two-step delete ---------- */
  document.addEventListener('click', function (e) {
    var r = e.target.closest('[data-reveal]'), c = e.target.closest('[data-cancel]');
    if (r) { var f = r.closest('.delform'); r.hidden = true; f.querySelector('.confirm').hidden = false; f.closest('.item').classList.add('confirming'); }
    if (c) { var g = c.closest('.delform'); g.querySelector('.confirm').hidden = true; g.querySelector('[data-reveal]').hidden = false; g.closest('.item').classList.remove('confirming'); }
  });

  /* ---------- custom dropdowns (native <select> stays in the DOM for forms) ---------- */
  var openMenu = null;
  function closeMenu(focusBtn) {
    if (!openMenu) return;
    openMenu.menu.remove(); openMenu.btn.setAttribute('aria-expanded', 'false');
    if (focusBtn) openMenu.btn.focus();
    openMenu = null;
  }
  document.addEventListener('mousedown', function (e) { if (openMenu && !openMenu.menu.contains(e.target) && !openMenu.btn.contains(e.target)) closeMenu(); });
  function follow(e) {  // keep the menu attached to its button; close only if the button scrolls out of view
    if (!openMenu || (e && e.target instanceof Node && openMenu.menu.contains(e.target))) return;
    var r = openMenu.btn.getBoundingClientRect();
    if (r.bottom < 0 || r.top > window.innerHeight) return closeMenu();
    openMenu.place();
  }
  window.addEventListener('resize', follow);
  window.addEventListener('scroll', follow, true);

  function enhance(sel) {
    if (sel.dataset.ddDone) return; sel.dataset.ddDone = '1';
    var isStatus = sel.classList.contains('st');
    var wrap = document.createElement('div');
    wrap.className = 'dd' + (isStatus ? ' dd-st' : '');
    if (sel.hasAttribute('data-dd-wide')) wrap.setAttribute('data-wide', '');
    sel.parentNode.insertBefore(wrap, sel); wrap.appendChild(sel); sel.classList.add('dd-native'); sel.tabIndex = -1;
    var btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'dd-btn'; btn.setAttribute('aria-haspopup', 'listbox'); btn.setAttribute('aria-expanded', 'false');
    btn.setAttribute('aria-label', sel.getAttribute('aria-label') || 'Choose');
    btn.innerHTML = '<span class="dd-dot"></span><span class="dd-label"></span>' + CHEV;
    wrap.insertBefore(btn, sel);
    var label = btn.querySelector('.dd-label');

    function sync() {
      var o = sel.options[sel.selectedIndex];
      label.textContent = o ? o.text : ''; label.title = label.textContent;
      if (isStatus) wrap.setAttribute('data-s', sel.value);
    }
    sync(); sel.addEventListener('change', sync);

    function open(focusSelected) {
      closeMenu();
      var menu = document.createElement('div'); menu.className = 'dd-menu'; menu.setAttribute('role', 'listbox');
      Array.prototype.forEach.call(sel.options, function (o, i) {
        var it = document.createElement('button'); it.type = 'button'; it.className = 'dd-opt'; it.setAttribute('role', 'option');
        it.setAttribute('aria-selected', i === sel.selectedIndex ? 'true' : 'false'); it.dataset.i = i;
        if (isStatus) { it.setAttribute('data-s', o.value); it.innerHTML = '<span class="dd-dot on"></span>'; }
        var tx = document.createElement('span'); tx.className = 'tx'; tx.textContent = o.text; tx.title = o.text; it.appendChild(tx);
        it.insertAdjacentHTML('beforeend', CHECK);
        it.addEventListener('click', function () {
          var changed = sel.selectedIndex !== i;
          sel.selectedIndex = i; sync(); closeMenu(true);
          if (changed) sel.dispatchEvent(new Event('change', { bubbles: true }));
        });
        menu.appendChild(it);
      });
      document.body.appendChild(menu);
      function place() {
        var r = btn.getBoundingClientRect(), mh = Math.min(menu.scrollHeight, 300);
        menu.style.minWidth = Math.max(r.width, 170) + 'px';
        menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8)) + 'px';
        menu.style.top = (window.innerHeight - r.bottom < mh + 12 && r.top > mh + 12 ? r.top - mh - 6 : r.bottom + 6) + 'px';
      }
      place();
      btn.setAttribute('aria-expanded', 'true'); openMenu = { menu: menu, btn: btn, place: place };
      var sel_ = menu.querySelector('[aria-selected=true]') || menu.firstChild;
      if (focusSelected) sel_.focus(); else if (sel_.scrollIntoView) sel_.scrollIntoView({ block: 'nearest' });
      menu.addEventListener('keydown', function (e) {
        var items = Array.prototype.slice.call(menu.children), k = items.indexOf(document.activeElement);
        if (e.key === 'ArrowDown') { e.preventDefault(); items[Math.min(k + 1, items.length - 1)].focus(); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); items[Math.max(k - 1, 0)].focus(); }
        else if (e.key === 'Home') { e.preventDefault(); items[0].focus(); }
        else if (e.key === 'End') { e.preventDefault(); items[items.length - 1].focus(); }
        else if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); closeMenu(true); }
      });
    }
    btn.addEventListener('click', function () { if (openMenu && openMenu.btn === btn) closeMenu(); else open(false); });
    btn.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); open(true); }
    });
  }

  /* ---------- time, date-time and suggestion widgets: in-page UI instead of the browser's native popups ---------- */
  var CAL = '<svg class="ic sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4.5" width="18" height="16" rx="3"/><path d="M8 2.5v4M16 2.5v4M3 10h18"/></svg>';
  var ARROW = function (d) { return '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="' + (d < 0 ? '15 6 9 12 15 18' : '9 6 15 12 9 18') + '"/></svg>'; };
  var pad = function (n) { return (n < 10 ? '0' : '') + n; };
  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
  function fire(i) { i.dispatchEvent(new Event('input', { bubbles: true })); }
  function nums(a, b, step) { var r = []; for (var i = a; i <= b; i += step) r.push(pad(i)); return r; }
  function withValue(list, v) { if (list.indexOf(v) < 0) { list.push(v); list.sort(); } return list; }
  function makeSelect(label, values, cur) {
    var sl = document.createElement('select'); sl.setAttribute('aria-label', label);
    values.forEach(function (v) { var o = document.createElement('option'); o.value = o.textContent = v; sl.appendChild(o); });
    sl.value = cur; return sl;
  }

  function enhanceTime(inp) {
    var lo = inp.getAttribute('min') || '00:00', hi = inp.getAttribute('max') || '23:59';
    var p = (/^\d\d:\d\d$/.test(inp.value) ? inp.value : lo).split(':');
    inp.type = 'hidden';
    var box = el('div', 'timepick');
    var hs = makeSelect('Hour', withValue(nums(+lo.slice(0, 2), +hi.slice(0, 2), 1), p[0]), p[0]);
    var ms = makeSelect('Minute', withValue(nums(0, 55, 5), p[1]), p[1]);
    box.appendChild(hs); box.appendChild(el('span', 'tsep', ':')); box.appendChild(ms);
    inp.parentNode.insertBefore(box, inp.nextSibling);
    box.addEventListener('click', function (e) { e.preventDefault(); });
    var busy = false;
    function apply() {
      if (busy) return;
      var v = hs.value + ':' + ms.value;
      if (v < lo) v = lo; else if (v > hi) v = hi;
      busy = true;
      hs.value = v.slice(0, 2); ms.value = v.slice(3);
      hs.dispatchEvent(new Event('change')); ms.dispatchEvent(new Event('change'));   // refresh the dropdown labels
      busy = false;
      inp.value = v; fire(inp);
    }
    hs.addEventListener('change', apply); ms.addEventListener('change', apply);
  }

  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  function enhanceDateTime(inp) {
    var sp = inp.parentNode.querySelector('span');
    inp.type = 'hidden';
    var wrap = el('div', 'dtpick'), btn = el('button', 'dd-btn', CAL + '<span class="dd-label"></span>' + CHEV);
    btn.type = 'button'; btn.setAttribute('aria-haspopup', 'dialog'); btn.setAttribute('aria-expanded', 'false');
    btn.setAttribute('aria-label', sp ? sp.textContent : 'Date and time');
    var clear = el('button', 'dt-clear', 'Clear'); clear.type = 'button';
    wrap.appendChild(btn); wrap.appendChild(clear);
    inp.parentNode.insertBefore(wrap, inp.nextSibling);
    wrap.addEventListener('click', function (e) { e.preventDefault(); });   // the surrounding <label> must not re-click the button
    var panel = null, view = null, tm = null;

    function parse() { var m = /^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)$/.exec(inp.value); return m ? { y: +m[1], mo: +m[2] - 1, d: +m[3], h: m[4], mi: m[5] } : null; }
    function paint() {
      var v = parse(), lab = btn.querySelector('.dd-label');
      lab.textContent = v ? new Date(v.y, v.mo, v.d).toLocaleDateString('en-GB', v.y === new Date().getFullYear() ? { day: 'numeric', month: 'short' } : { day: 'numeric', month: 'short', year: 'numeric' }) + ', ' + v.h + ':' + v.mi : 'Not set';
      lab.classList.toggle('dim', !v); clear.hidden = !v; wrap.classList.toggle('has', !!v);
    }
    function set(y, mo, d) { inp.value = y + '-' + pad(mo + 1) + '-' + pad(d) + 'T' + tm.h + ':' + tm.mi; paint(); fire(inp); }
    function close() { if (panel) { panel.remove(); panel = null; btn.setAttribute('aria-expanded', 'false'); } }
    function clearAll() { inp.value = ''; paint(); fire(inp); close(); }

    function render() {
      var v = parse(), now = new Date();
      panel.innerHTML = '';
      var head = el('div', 'cal-head'), prev = el('button', 'cal-nav', ARROW(-1)), next = el('button', 'cal-nav', ARROW(1));
      prev.type = next.type = 'button'; prev.setAttribute('aria-label', 'Previous month'); next.setAttribute('aria-label', 'Next month');
      prev.onclick = function () { view.m--; if (view.m < 0) { view.m = 11; view.y--; } render(); };
      next.onclick = function () { view.m++; if (view.m > 11) { view.m = 0; view.y++; } render(); };
      head.appendChild(prev); head.appendChild(el('b', null, MONTHS[view.m] + ' ' + view.y)); head.appendChild(next);
      panel.appendChild(head);
      var grid = el('div', 'cal-grid');
      ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'].forEach(function (d) { grid.appendChild(el('i', null, d)); });
      var off = (new Date(view.y, view.m, 1).getDay() + 6) % 7, n = new Date(view.y, view.m + 1, 0).getDate(), i;
      for (i = 0; i < off; i++) grid.appendChild(el('span'));
      for (i = 1; i <= n; i++) (function (d) {
        var b = el('button', 'cal-day', d); b.type = 'button';
        if (v && v.y === view.y && v.mo === view.m && v.d === d) b.classList.add('sel');
        if (now.getFullYear() === view.y && now.getMonth() === view.m && now.getDate() === d) b.classList.add('today');
        b.onclick = function () { set(view.y, view.m, d); render(); };
        grid.appendChild(b);
      })(i);
      panel.appendChild(grid);
      var foot = el('div', 'cal-foot'), hs = makeSelect('Hour', withValue(nums(0, 23, 1), tm.h), tm.h), ms = makeSelect('Minute', withValue(nums(0, 55, 5), tm.mi), tm.mi);
      var onTime = function () { tm = { h: hs.value, mi: ms.value }; var c = parse(); if (c) set(c.y, c.mo, c.d); };
      hs.addEventListener('change', onTime); ms.addEventListener('change', onTime);
      var done = el('button', 'btn sm', 'Done'); done.type = 'button'; done.onclick = function () { close(); btn.focus(); };
      foot.appendChild(el('span', 'hint', 'Time')); foot.appendChild(hs); foot.appendChild(el('span', 'tsep', ':')); foot.appendChild(ms); foot.appendChild(done);
      panel.appendChild(foot);
      enhance(hs); enhance(ms);
    }
    function open() {
      var v = parse(), t = new Date();
      tm = v ? { h: v.h, mi: v.mi } : { h: '09', mi: '00' };
      view = v ? { y: v.y, m: v.mo } : { y: t.getFullYear(), m: t.getMonth() };
      panel = el('div', 'cal'); panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', 'Choose date and time');
      wrap.appendChild(panel); render(); btn.setAttribute('aria-expanded', 'true');
      var r = panel.getBoundingClientRect();
      if (r.right > window.innerWidth - 8) { panel.style.left = 'auto'; panel.style.right = '0'; }
    }
    btn.addEventListener('click', function () { if (panel) close(); else open(); });
    clear.addEventListener('click', clearAll);
    document.addEventListener('mousedown', function (e) { if (panel && !wrap.contains(e.target) && !e.target.closest('.dd-menu')) close(); });
    wrap.addEventListener('keydown', function (e) { if (e.key === 'Escape' && panel) { close(); btn.focus(); } });
    paint();
  }

  function enhanceSuggest(inp) {
    var list = []; try { list = JSON.parse(inp.getAttribute('data-suggest')); } catch (e) { /* no suggestions */ }
    var menu = null;
    function close() { if (menu) { menu.remove(); menu = null; } }
    function show() {
      var q = inp.value.trim().toLowerCase();
      var hits = list.filter(function (k) { var l = k.toLowerCase(); return l.indexOf(q) >= 0 && l !== q; }).slice(0, 8);
      close(); if (!hits.length) return;
      menu = el('div', 'dd-menu'); menu.setAttribute('role', 'listbox');
      hits.forEach(function (k) {
        var it = el('button', 'dd-opt'); it.type = 'button'; it.setAttribute('role', 'option');
        var tx = el('span', 'tx'); tx.textContent = k; tx.title = k; it.appendChild(tx);
        it.addEventListener('click', function () { inp.value = k; close(); inp.focus(); fire(inp); });
        menu.appendChild(it);
      });
      document.body.appendChild(menu);
      var r = inp.getBoundingClientRect();
      menu.style.minWidth = r.width + 'px'; menu.style.left = r.left + 'px'; menu.style.top = (r.bottom + 6) + 'px';
      menu.addEventListener('keydown', function (e) {
        var items = Array.prototype.slice.call(menu.children), k = items.indexOf(document.activeElement);
        if (e.key === 'ArrowDown') { e.preventDefault(); items[Math.min(k + 1, items.length - 1)].focus(); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); if (k <= 0) inp.focus(); else items[k - 1].focus(); }
        else if (e.key === 'Escape') { e.preventDefault(); close(); inp.focus(); }
      });
    }
    inp.addEventListener('focus', show); inp.addEventListener('input', show);
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') close();
      else if (e.key === 'ArrowDown' && menu) { e.preventDefault(); menu.firstChild.focus(); }
    });
    document.addEventListener('mousedown', function (e) { if (menu && e.target !== inp && !menu.contains(e.target)) close(); });
    window.addEventListener('scroll', function (e) { if (menu && !menu.contains(e.target)) close(); }, true);
  }

  Array.prototype.forEach.call(document.querySelectorAll('input[type=time]'), enhanceTime);
  Array.prototype.forEach.call(document.querySelectorAll('input[type=datetime-local]'), enhanceDateTime);
  Array.prototype.forEach.call(document.querySelectorAll('input[data-suggest]'), enhanceSuggest);
  Array.prototype.forEach.call(document.querySelectorAll('select'), enhance);

  /* ---------- keyword filter + busy buttons ---------- */
  document.addEventListener('input', function (e) {
    if (!e.target.matches('[data-filter]')) return;
    var q = e.target.value.toLowerCase();
    Array.prototype.forEach.call(document.querySelectorAll(e.target.dataset.filter + ' .chk'), function (l) { l.hidden = l.textContent.toLowerCase().indexOf(q) < 0; });
  });
  document.addEventListener('submit', function (e) {
    var b = e.target.getAttribute && e.target.getAttribute('data-busy'); if (!b) return;
    var btn = e.target.querySelector('button.btn'); if (btn) { btn.textContent = b; btn.disabled = true; }
  });

  /* ---------- search job progress ---------- */
  var j = document.getElementById('job');
  if (j) {
    var bar = j.querySelector('.progress i'), spin = document.getElementById('spin'), txt = document.getElementById('jobtext');
    (function poll() {
      fetch('/job/' + j.dataset.id + '.json').then(function (r) { return r.json(); }).then(function (d) {
        bar.style.width = Math.max(4, d.total ? Math.round(100 * d.done / d.total) : 0) + '%';
        txt.textContent = 'Searched ' + d.done + ' of ' + d.total + ' · ' + d.found + ' unique businesses found';
        var cc = document.getElementById('jobcountries');
        if (cc && d.countries) {
          cc.innerHTML = '';
          d.countries.forEach(function (c) { var p = document.createElement('span'); p.className = 'pill'; p.textContent = c.name + ' '; var b = document.createElement('b'); b.textContent = c.n; p.appendChild(b); cc.appendChild(p); });
        }
        if (d.status === 'running') return setTimeout(poll, 1500);
        if (d.url && !d.message) return (location.href = d.url);
        spin.style.display = 'none';
        var m = document.getElementById('jobmsg'); m.innerHTML = '';
        if (d.message) { var p = document.createElement('p'); p.className = 'flash'; p.textContent = d.message; m.appendChild(p); }
        if (d.url) { var a = document.createElement('a'); a.className = 'btn'; a.href = d.url; a.textContent = 'View ' + d.found + ' saved results'; m.appendChild(a); }
        else { var b = document.createElement('a'); b.className = 'btn ghost'; b.href = '/'; b.textContent = 'Back'; m.appendChild(b); }
      }).catch(function () { setTimeout(poll, 3000); });
    })();
  }

  /* ---------- campaign settings: what the window + pace allow per day ---------- */
  var cap = document.getElementById('capacity');
  if (cap) {
    var form = cap.closest('form');
    var mins = function (v) { var p = (v || '').split(':'); return p.length < 2 ? 0 : +p[0] * 60 + +p[1]; };
    var showCapacity = function () {
      var day = +form.daily_cap.value || 0, hour = +form.per_hour.value || 0;
      var max = Math.floor(Math.max(mins(form.window_end.value) - mins(form.window_start.value), 0) * hour / 60);
      cap.textContent = day > max
        ? 'At ' + hour + ' per hour in this window, only about ' + max + ' messages fit in a day, not ' + day + '. Raise messages per hour or widen the window.'
        : 'Up to ' + day + ' messages a day, about one every ' + (hour > 60 ? Math.round(3600 / hour) + ' sec' : Math.round(60 / Math.max(hour, 1)) + ' min') + ' until the day\'s limit is reached (the window fits ' + max + ').';
      cap.style.color = day > max ? 'var(--bad)' : '';
    };
    form.addEventListener('input', showCapacity);
    showCapacity();
  }
})();
