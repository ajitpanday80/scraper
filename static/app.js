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
    else if (t.closest('form.toolbar') && t.hasAttribute('data-auto')) t.closest('form').submit();
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
  window.addEventListener('resize', function () { closeMenu(); });
  window.addEventListener('scroll', function (e) { if (openMenu && !openMenu.menu.contains(e.target)) closeMenu(); }, true);

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
      var r = btn.getBoundingClientRect(), mh = Math.min(menu.scrollHeight, 300);
      menu.style.minWidth = Math.max(r.width, 170) + 'px';
      menu.style.left = Math.min(r.left, window.innerWidth - menu.offsetWidth - 8) + 'px';
      menu.style.top = (window.innerHeight - r.bottom < mh + 12 && r.top > mh + 12 ? r.top - mh - 6 : r.bottom + 6) + 'px';
      btn.setAttribute('aria-expanded', 'true'); openMenu = { menu: menu, btn: btn };
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
  Array.prototype.forEach.call(document.querySelectorAll('select'), enhance);

  /* ---------- search job progress ---------- */
  var j = document.getElementById('job');
  if (j) {
    var bar = j.querySelector('.progress i'), spin = document.getElementById('spin'), txt = document.getElementById('jobtext');
    (function poll() {
      fetch('/job/' + j.dataset.id + '.json').then(function (r) { return r.json(); }).then(function (d) {
        bar.style.width = Math.max(4, d.total ? Math.round(100 * d.done / d.total) : 0) + '%';
        txt.textContent = 'Searched ' + d.done + ' of ' + d.total + ' · ' + d.found + ' unique businesses found';
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
})();
