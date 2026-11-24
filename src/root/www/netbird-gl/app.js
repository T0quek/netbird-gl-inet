(function () {
  'use strict';
  var API = '/netbird-gl/api';
  var POLL_MS = 5000;
  var CLOUD_URL = 'https://api.netbird.io:443';
  var timer = null;
  var busy = false;
  var data = null;
  var wizardForced = false;   // wizard opened manually (re-enroll)
  var wiz = { step: 1, type: '', url: '', key: '', keyMask: '', done: false,
    opts: { accept_routes: true, use_dns: true, guest_access: false }, sw: 'keep' };

  var ERR = {
    csrf: 'Request rejected (CSRF protection).',
    origin: 'Request from a foreign origin rejected.',
    auth_gl: 'Your admin panel session has expired. Please log in again.',
    auth_gl_unavailable: 'Session check is unavailable on this firmware. Run "netbird-gl set-password" on the router.',
    auth_password: 'Please log in.',
    bad_password: 'Wrong password.',
    locked: 'Too many attempts – wait 5 minutes.',
    no_password: 'No password set. Run "netbird-gl set-password" on the router.',
    no_exit_node: 'Select an exit node first.',
    accept_routes_off: 'Turn on "Accept routes from other peers" (Advanced) to use an exit node.',
    invalid_exit_node: 'That exit node is no longer offered to this router.',
    invalid_management_url: 'Invalid server address – it must start with https://.',
    invalid_setup_key: 'Invalid setup key.',
    invalid_switch_func: 'Invalid toggle button function.',
    enroll_failed: 'Could not connect. Check the setup key and the server address.',
    no_binary: 'NetBird is still being downloaded – try again in a moment.',
    check_failed: 'Could not check for updates (no internet?).',
    busy: 'Another operation is in progress.',
    network: 'Cannot reach the router.'
  };
  var STATUS = {
    Connected: ['Connected', 'is-ok'], Connecting: ['Connecting…', 'is-warn'], Idle: ['Disconnected', ''],
    NeedsLogin: ['Login required – use Re-enroll', 'is-warn'], LoginFailed: ['Cannot log in to the server', 'is-bad'],
    SessionExpired: ['Session expired', 'is-bad']
  };

  function $(id) { return document.getElementById(id); }
  function each(sel, fn) { Array.prototype.forEach.call(document.querySelectorAll(sel), fn); }
  function text(id, v) { $(id).textContent = (v === undefined || v === null || v === '') ? '—' : String(v); }
  function fmtTime(ts) { return ts ? new Date(ts * 1000).toLocaleString() : '—'; }
  function show(id) { ['loading', 'wizard', 'dash', 'login'].forEach(function (p) { $(p).hidden = p !== id; }); }

  /* ------------------------------------------------ theme sync ------- */
  // Mirror the GL panel's theme stylesheets (/theme/base.css, /theme/<name>/index.css).
  function themeHrefs() {
    var list = [];
    try {
      if (window.parent && window.parent !== window) {
        var links = window.parent.document.querySelectorAll('link[rel="stylesheet"][id^="theme"]');
        Array.prototype.forEach.call(links, function (l) {
          var h = l.getAttribute('href') || '';
          if (/^\/theme\/[A-Za-z0-9_\/.-]+\.css(\?.*)?$/.test(h)) { list.push(h.split('?')[0]); }
        });
      }
    } catch (e) { list = []; }
    if (!list.length) { list = ['/theme/base.css', '/theme/default/index.css']; }
    return list;
  }
  function syncTheme() {
    var want = themeHrefs();
    var have = Array.prototype.map.call(document.querySelectorAll('link[data-gl-theme]'),
      function (l) { return l.getAttribute('href'); });
    if (want.join('|') === have.join('|')) { return; }
    each('link[data-gl-theme]', function (l) { l.parentNode.removeChild(l); });
    want.forEach(function (h) {
      var l = document.createElement('link');
      l.rel = 'stylesheet';
      l.href = h;
      l.setAttribute('data-gl-theme', '1');
      document.head.appendChild(l);
    });
  }
  function watchTheme() {
    syncTheme();
    try {
      if (window.parent !== window && window.MutationObserver) {
        new MutationObserver(syncTheme).observe(window.parent.document.head, { childList: true, subtree: true, attributes: true });
      }
    } catch (e) { /* standalone page */ }
  }

  /* ------------------------------------------------ punycode -------- */
  // Display IDN labels (xn--...) as Unicode, e.g. "router-podróżny" (RFC 3492 decoder).
  function punyDecode(input) {
    var base = 36, tMin = 1, tMax = 26, skew = 38, damp = 700, n = 128, i = 0, bias = 72, out = [];
    var b = input.lastIndexOf('-');
    var j;
    for (j = 0; j < (b > 0 ? b : 0); j++) { out.push(input.charCodeAt(j)); }
    function adapt(delta, num, first) {
      var k = 0;
      delta = first ? Math.floor(delta / damp) : delta >> 1;
      delta += Math.floor(delta / num);
      while (delta > ((base - tMin) * tMax) >> 1) { delta = Math.floor(delta / (base - tMin)); k += base; }
      return Math.floor(k + (base - tMin + 1) * delta / (delta + skew));
    }
    for (var idx = b > 0 ? b + 1 : 0; idx < input.length;) {
      var oldi = i, w = 1;
      for (var k = base; ; k += base) {
        if (idx >= input.length) { throw new Error('bad'); }
        var c = input.charCodeAt(idx++);
        var digit = c - 48 < 10 ? c - 22 : c - 65 < 26 ? c - 65 : c - 97 < 26 ? c - 97 : base;
        if (digit >= base) { throw new Error('bad'); }
        i += digit * w;
        var t = k <= bias ? tMin : (k >= bias + tMax ? tMax : k - bias);
        if (digit < t) { break; }
        w *= base - t;
      }
      bias = adapt(i - oldi, out.length + 1, oldi === 0);
      n += Math.floor(i / (out.length + 1));
      i %= out.length + 1;
      out.splice(i++, 0, n);
    }
    return String.fromCodePoint.apply(null, out);
  }
  function idnToUnicode(name) {
    return String(name || '').split('.').map(function (l) {
      if (l.toLowerCase().indexOf('xn--') !== 0) { return l; }
      try { return punyDecode(l.slice(4)); } catch (e) { return l; }
    }).join('.');
  }

  /* ------------------------------------------------ embedding -------- */
  // Inside the GL panel the iframe grows with its content, so the panel page
  // scrolls as a whole (like every other GL page) instead of a nested scrollbar.
  var frame = null;
  try { frame = window.frameElement; } catch (e) { frame = null; }
  function fitFrame() {
    if (!frame) { return; }
    var h = Math.ceil(document.body.getBoundingClientRect().height);
    if (h > 0 && frame.style.height !== h + 'px') {
      frame.style.height = h + 'px';
      if (frame.parentElement) { frame.parentElement.style.height = 'auto'; }
    }
  }
  function watchSize() {
    if (!frame) { return; }
    document.documentElement.classList.add('embedded');
    frame.setAttribute('scrolling', 'no');
    if (window.ResizeObserver) { new ResizeObserver(fitFrame).observe(document.body); }
    window.addEventListener('load', fitFrame);
    fitFrame();
  }

  /* ------------------------------------------------ helpers ---------- */
  var toastTimer = null;
  function toast(t, isErr) {
    var el = $('toast');
    el.textContent = t;
    el.className = 'toast' + (isErr ? ' is-error' : '');
    el.hidden = false;
    // keep the message inside the visible part of the GL page
    var top = 16;
    try {
      if (frame) {
        var r = frame.getBoundingClientRect();
        top = Math.max(16, Math.min(-r.top + 70, r.height - 60));
      }
    } catch (e) { top = 16; }
    el.style.top = top + 'px';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.hidden = true; }, isErr ? 6000 : 3000);
  }
  function errText(e) { return ERR[e] || ('Error: ' + e); }

  function req(method, action, params) {
    var opt = { method: method, credentials: 'same-origin', cache: 'no-store', headers: { 'X-NBGL': '1' } };
    if (params) {
      opt.headers['Content-Type'] = 'application/x-www-form-urlencoded';
      opt.body = Object.keys(params).map(function (k) {
        return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
      }).join('&');
    }
    return fetch(API + '?a=' + action, opt).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: 'network' }; }).then(function (d) {
        d.httpStatus = r.status;
        return d;
      });
    }, function () { return { ok: false, error: 'network' }; });
  }

  function act(action, params, okText) {
    if (busy) { return Promise.resolve({ ok: false, error: 'busy' }); }
    busy = true;
    return req('POST', action, params || {}).then(function (d) {
      busy = false;
      if (d.ok) { if (okText) { toast(okText); } }
      else if (d.httpStatus === 401) { showLogin(d.error); return d; }
      else { toast(errText(d.error), true); }
      poll();
      return d;
    });
  }

  function setSw(id, on) {
    var el = $(id);
    if (el.classList.contains('is-busy')) { return; }
    el.classList.toggle('is-on', !!on);
    el.setAttribute('aria-checked', on ? 'true' : 'false');
  }
  function swOn(id) { return $(id).classList.contains('is-on'); }
  function bindSwitch(id, handler) {
    $(id).addEventListener('click', function () {
      var el = $(id);
      if (el.disabled || el.classList.contains('is-busy')) { return; }
      var next = !swOn(id);
      el.classList.toggle('is-on', next);
      el.classList.add('is-busy');
      Promise.resolve(handler(next)).then(function () { el.classList.remove('is-busy'); poll(); });
    });
  }
  function setSelect(id, val) {
    var el = $(id);
    if (document.activeElement === el) { return; }
    el.value = val;
  }

  /* ------------------------------------------------ login ------------ */
  function showLogin(mode, err) {
    show('login');
    var gl = mode === 'auth_gl' || mode === 'auth_gl_unavailable';
    $('login-form').hidden = gl;
    $('login-gl').hidden = mode !== 'auth_gl';
    $('login-text').textContent = err ? errText(err) : errText(mode);
  }

  /* ------------------------------------------------ wizard ----------- */
  // the wizard is only for a router that was never set up - afterwards a
  // NetBird outage keeps the dashboard; setting up again = "Re-enroll…"
  function needsSetup(d) { return !d.enrolled; }
  function openWizard(manual) {
    wizardForced = !!manual;
    wiz.step = 1;
    wiz.done = false;
    wiz.key = '';
    wiz.keyMask = '';
    $('wiz-key').value = '';
    if (data) {
      var url = data.settings.management_url || CLOUD_URL;
      wiz.type = url === CLOUD_URL ? 'cloud' : 'self';
      wiz.url = wiz.type === 'self' ? url : '';
      $('wiz-url').value = wiz.url;
      wiz.opts.accept_routes = data.settings.accept_routes;
      wiz.opts.use_dns = data.settings.use_dns;
      wiz.opts.guest_access = data.settings.guest_access;
    }
    $('wiz-cancel').hidden = !manual;
    $('wiz-result').hidden = true;
    renderWizard();
    show('wizard');
  }
  function closeWizard() { wizardForced = false; poll(); }

  function wizValid(step) {
    if (step === 1) {
      if (wiz.type === 'cloud') { return true; }
      if (wiz.type !== 'self') { return false; }
      return /^https:\/\/[A-Za-z0-9.-]+(:[0-9]{1,5})?(\/[A-Za-z0-9._~\/-]*)?$/.test(wiz.url.trim());
    }
    if (step === 2) { return /^[A-Za-z0-9-]{8,128}$/.test(wiz.key.trim()); }
    return true;
  }
  function renderWizard() {
    var prep = data && !data.binary.present;
    $('wiz-prep').hidden = !prep;
    each('.step[data-step]', function (s) { s.hidden = prep || Number(s.getAttribute('data-step')) !== wiz.step; });
    each('#stepper li', function (li) {
      var n = Number(li.getAttribute('data-s'));
      li.classList.toggle('is-active', n === wiz.step);
      li.classList.toggle('is-done', n < wiz.step || (n === 4 && wiz.done));
    });
    each('.choice-item', function (b) {
      var on = b.getAttribute('data-type') === wiz.type;
      b.classList.toggle('is-active', on);
      b.setAttribute('aria-checked', on ? 'true' : 'false');
    });
    $('wiz-url-row').hidden = wiz.type !== 'self';
    $('wiz-url-hint').hidden = wiz.type !== 'self';
    ['accept_routes', 'use_dns', 'guest_access'].forEach(function (k) { setSw('wiz-' + k, wiz.opts[k]); });
    $('wiz-switch').value = wiz.sw;
    if (wiz.step === 4) {
      $('sum-server').textContent = wiz.type === 'cloud' ? 'NetBird Cloud' : wiz.url.trim();
      var k = wiz.key.trim();
      if (k) { wiz.keyMask = k.slice(0, 8) + '…'; }
      $('sum-key').textContent = wiz.keyMask || '—';
      var o = [];
      if (wiz.opts.accept_routes) { o.push('peer routes'); }
      if (wiz.opts.use_dns) { o.push('NetBird DNS'); }
      if (wiz.opts.guest_access) { o.push('guest network'); }
      if (wiz.sw !== 'keep') { o.push('toggle: ' + (wiz.sw === 'NetBird-VPN' ? 'connection' : 'exit node')); }
      $('sum-opts').textContent = o.length ? o.join(', ') : 'defaults';
    }
    $('wiz-back').hidden = wiz.step === 1 || wiz.done;
    var next = $('wiz-next');
    next.hidden = prep;
    next.textContent = wiz.done ? 'Open dashboard' : (wiz.step === 4 ? 'Connect' : 'Next');
    next.disabled = busy || !wizValid(wiz.step);
    $('wiz-url').classList.toggle('is-error', wiz.type === 'self' && wiz.url !== '' && !wizValid(1));
  }
  function wizResult(cls, msg, spinner) {
    var r = $('wiz-result');
    while (r.firstChild) { r.removeChild(r.firstChild); }
    if (spinner) { var s = document.createElement('span'); s.className = 'spinner'; r.appendChild(s); }
    r.appendChild(document.createTextNode(msg));
    r.className = 'tips ' + cls;
    r.hidden = false;
  }
  function wizConnect() {
    var url = wiz.type === 'cloud' ? CLOUD_URL : wiz.url.trim();
    var settings = { accept_routes: wiz.opts.accept_routes ? '1' : '0', use_dns: wiz.opts.use_dns ? '1' : '0',
      guest_access: wiz.opts.guest_access ? '1' : '0' };
    if (wiz.sw !== 'keep') { settings.switch_func = wiz.sw; }
    busy = true;
    renderWizard();
    wizResult('is-info', 'Connecting to ' + (wiz.type === 'cloud' ? 'NetBird Cloud' : url) + '…', true);
    req('POST', 'settings', settings).then(function (d) {
      if (!d.ok && d.httpStatus === 401) { busy = false; showLogin(d.error); return null; }
      return req('POST', 'enroll', { management_url: url, setup_key: wiz.key.trim() });
    }).then(function (d) {
      busy = false;
      if (!d) { return; }
      if (d.ok) {
        wiz.done = true;
        wizardForced = true;   // stay on the success screen until the user continues
        wiz.key = '';
        $('wiz-key').value = '';
        wizResult('is-success', 'Connected. This router is now a NetBird peer.');
      } else {
        wizResult('is-error', errText(d.error));
      }
      renderWizard();
    });
  }
  function bindWizard() {
    each('.choice-item', function (b) {
      b.addEventListener('click', function () { wiz.type = b.getAttribute('data-type'); renderWizard(); });
    });
    $('wiz-url').addEventListener('input', function () { wiz.url = this.value; renderWizard(); });
    $('wiz-key').addEventListener('input', function () { wiz.key = this.value; renderWizard(); });
    ['accept_routes', 'use_dns', 'guest_access'].forEach(function (k) {
      $('wiz-' + k).addEventListener('click', function () { wiz.opts[k] = !wiz.opts[k]; renderWizard(); });
    });
    $('wiz-switch').addEventListener('change', function () { wiz.sw = this.value; renderWizard(); });
    $('wiz-back').addEventListener('click', function () {
      if (wiz.step > 1) { wiz.step -= 1; $('wiz-result').hidden = true; renderWizard(); }
    });
    $('wiz-next').addEventListener('click', function () {
      if (wiz.done) { closeWizard(); return; }
      if (!wizValid(wiz.step)) { return; }
      if (wiz.step < 4) { wiz.step += 1; renderWizard(); return; }
      wizConnect();
    });
    $('wiz-cancel').addEventListener('click', closeWizard);
  }

  /* ------------------------------------------------ dashboard -------- */
  function renderDash(d) {
    var nb = d.nb || null;
    var st;
    if (d.daemon !== 'running' || !nb) { st = ['Daemon stopped', 'is-bad']; }
    else { st = STATUS[nb.daemonStatus] || [nb.daemonStatus || 'Unknown', '']; }
    if (nb && nb.daemonStatus === 'Idle' && !d.state.vpn) { st = ['Turned off', '']; }

    var pill = $('title-pill');
    pill.textContent = st[0];
    pill.className = 'pill ' + st[1];
    $('conn-dot').className = 'dot ' + st[1];
    text('v-status', st[0]);
    text('v-ip', nb && nb.netbirdIp ? nb.netbirdIp.split('/')[0] : '');
    text('v-fqdn', nb && nb.fqdn ? idnToUnicode(nb.fqdn) : '');
    var doms = (d.dns && d.dns.domains) || [];
    var dnsTxt = doms.map(idnToUnicode).join(', ');
    text('v-dns', !d.settings.use_dns ? 'off' : (dnsTxt || 'waiting for NetBird'));
    text('v-mgmt', d.settings.management_url === CLOUD_URL ? 'NetBird Cloud' : d.settings.management_url);
    text('v-peers', nb && nb.peers ? nb.peers.connected + ' of ' + nb.peers.total : '');
    setSw('sw-vpn', d.state.vpn);

    // exit node
    var sel = $('s-exit_node');
    if (document.activeElement !== sel) {
      while (sel.options.length > 1) { sel.remove(1); }
      var exits = 0;
      var cur = '';
      (d.networks || []).forEach(function (n, i) {
        if (!n.exit) { return; }
        exits += 1;
        var o = document.createElement('option');
        o.value = String(i);
        o.textContent = n.id;
        sel.appendChild(o);
        if (n.id === d.settings.exit_node) { cur = String(i); }
      });
      if (d.settings.exit_node && !cur) {
        var o2 = document.createElement('option');
        o2.value = 'keep';
        o2.textContent = d.settings.exit_node + ' (not available)';
        sel.appendChild(o2);
        cur = 'keep';
      }
      sel.value = cur;
      $('exit-hint').hidden = exits > 0;
    }
    var active = (d.networks || []).filter(function (n) { return n.exit && n.selected; }).map(function (n) { return n.id; });
    var exitState;
    if (!d.state.exit) { exitState = ['Off – using the router\'s own internet', '']; }
    else if (active.length) { exitState = ['On – traffic leaves via ' + active.join(', '), 'is-ok']; }
    else { exitState = ['On – waiting for the exit node', 'is-warn']; }
    text('v-exit', exitState[0]);
    $('exit-dot').className = 'dot ' + exitState[1];
    setSw('sw-exit', d.state.exit);
    $('sw-exit').disabled = !d.settings.exit_node || !d.settings.accept_routes;
    if (!d.settings.accept_routes) { text('v-exit', 'Needs "Accept routes from other peers" (Advanced)'); }

    // toggle button
    var fn = d['switch'] && d['switch'].func;
    var fsel = $('s-switch_func');
    var other = fsel.querySelector('option[data-other]');
    if (fn && fn !== 'NetBird-VPN' && fn !== 'NetBird-Exit-Node') {
      if (!other) {
        other = document.createElement('option');
        other.setAttribute('data-other', '1');
        fsel.appendChild(other);
      }
      other.value = fn;
      other.textContent = 'Other: ' + fn;
    } else if (other) { fsel.removeChild(other); }
    setSelect('s-switch_func', fn || 'none');
    setSw('s-switch_invert', d.settings.switch_invert);

    // updates
    var u = d.update || {};
    text('u-cur', d.binary.version);
    text('u-lat', u.latest ? u.latest + ' (checked ' + fmtTime(u.last_check) + ')' : '');
    if (u.job === 'running') { text('u-auto', 'updating now…'); }
    else if (u.available && u.auto_at) { text('u-auto', fmtTime(u.auto_at)); }
    else if (u.available) { text('u-auto', 'off – update manually'); }
    else { text('u-auto', 'nothing to install'); }
    var up = $('upd-pill');
    up.textContent = u.job === 'running' ? 'Updating' : (u.available ? 'Update available' : (u.latest ? 'Up to date' : ''));
    up.className = 'pill ' + (u.available ? 'is-warn' : 'is-ok');
    $('btn-update').disabled = !u.available || u.job === 'running';
    setSw('s-auto_check', d.settings.auto_check);
    setSw('s-auto_update', d.settings.auto_update);
    setSelect('s-update_delay_days', String(d.settings.update_delay_days));
    var ban = $('banner');
    ban.hidden = !(u.available && u.job !== 'running');
    if (!ban.hidden) {
      $('banner-text').textContent = 'NetBird ' + u.latest + ' is available' +
        (u.auto_at ? ' and will be installed automatically on ' + fmtTime(u.auto_at) + '.' : '.');
    }

    // advanced
    ['accept_routes', 'use_dns', 'guest_access', 'allow_inbound', 'kill_switch'].forEach(function (k) {
      setSw('s-' + k, d.settings[k]);
    });
    $('ks-row').hidden = !(d.guard && d.guard.blocking);
    var dx = $('s-dns_extra');
    if (document.activeElement !== dx) { dx.value = d.settings.dns_extra || ''; }
    $('btn-logout').hidden = d.auth !== 'password';

    // peers
    var tb = $('peers');
    while (tb.firstChild) { tb.removeChild(tb.firstChild); }
    var peers = (nb && nb.peers && nb.peers.details) || [];
    $('peers-pill').textContent = peers.length ? String(peers.length) : '';
    $('peers-pill').className = 'pill is-info';
    if (!peers.length) {
      var c0 = tb.insertRow().insertCell();
      c0.colSpan = 5;
      c0.textContent = 'No peers yet';
    }
    peers.forEach(function (p) {
      var tr = tb.insertRow();
      var online = p.status === 'Connected';
      var c = tr.insertCell();
      var dot = document.createElement('i');
      dot.className = 'dot ' + (online ? 'is-ok' : '');
      c.appendChild(dot);
      c.appendChild(document.createTextNode(idnToUnicode((p.fqdn || '').split('.')[0]) || '—'));
      c.title = p.fqdn || '';
      tr.insertCell().textContent = p.netbirdIp || '—';
      tr.insertCell().textContent = p.status || '—';
      tr.insertCell().textContent = p.connectionType || '—';
      tr.insertCell().textContent = p.latency ? Math.max(1, Math.round(p.latency / 1e6)) + ' ms' : '—';
    });

    var lg = $('log');
    var atEnd = lg.scrollTop + lg.clientHeight >= lg.scrollHeight - 4;
    lg.textContent = d.log || '(empty)';
    if (atEnd) { lg.scrollTop = lg.scrollHeight; }
    text('v-app', d.app);
  }

  function render(d) {
    data = d;
    if (u_job_error(d)) { toast('Update failed (' + d.update.error + ')', true); }
    if (wizardForced || needsSetup(d)) {
      if ($('wizard').hidden) { openWizard(wizardForced); } else { renderWizard(); }
      return;
    }
    renderDash(d);
    show('dash');
  }
  var lastErr = '';
  function u_job_error(d) {
    var e = (d.update && d.update.error) || '';
    var isNew = e && e !== lastErr;
    lastErr = e;
    return isNew;
  }

  function poll() {
    clearTimeout(timer);
    req('GET', 'status').then(function (d) {
      if (d.ok) { render(d); }
      else if (d.httpStatus === 401 || d.httpStatus === 503) { showLogin(d.error); }
      else { toast(errText(d.error), true); }
      timer = setTimeout(poll, POLL_MS);
    });
  }

  function bindDash() {
    bindSwitch('sw-vpn', function (on) { return act(on ? 'connect' : 'disconnect', null, on ? 'Connecting…' : 'Disconnected.'); });
    bindSwitch('sw-exit', function (on) { return act(on ? 'exit_on' : 'exit_off', null, on ? 'Exit node enabled.' : 'Exit node disabled.'); });
    ['switch_invert', 'auto_check', 'auto_update', 'accept_routes', 'use_dns', 'guest_access', 'allow_inbound', 'kill_switch'].forEach(function (k) {
      bindSwitch('s-' + k, function (on) {
        var p = {};
        p[k] = on ? '1' : '0';
        return act('settings', p, 'Saved.');
      });
    });
    $('s-exit_node').addEventListener('change', function () {
      if (this.value === 'keep') { return; }
      act('settings', this.value === '' ? { exit_node: '' } : { exit_node_idx: this.value }, 'Exit node saved.');
      this.blur();
    });
    $('s-switch_func').addEventListener('change', function () {
      if (this.querySelector('option:checked[data-other]')) { return; }
      act('settings', { switch_func: this.value }, 'Toggle button updated.');
      this.blur();
    });
    $('s-dns_extra').addEventListener('change', function () {
      act('settings', { dns_extra: this.value.trim() }, 'DNS domains saved.');
      this.blur();
    });
    $('s-update_delay_days').addEventListener('change', function () {
      act('settings', { update_delay_days: this.value }, 'Saved.');
      this.blur();
    });
    each('[data-act]', function (b) {
      b.addEventListener('click', function () {
        var a = b.getAttribute('data-act');
        if (a === 'update_now' && !window.confirm('Update NetBird now? The connection drops for a few seconds.')) { return; }
        if (a === 'restart' && !window.confirm('Restart the NetBird daemon?')) { return; }
        act(a, null, { update_now: 'Update started.', update_check: 'Checked.', restart: 'Daemon restarted.' }[a]);
      });
    });
    $('btn-reenroll').addEventListener('click', function () { openWizard(true); });
    $('btn-logout').addEventListener('click', function () { act('logout').then(function () { showLogin('auth_password'); }); });
    $('login-form').addEventListener('submit', function (e) {
      e.preventDefault();
      req('POST', 'login', { password: $('l-pw').value }).then(function (d) {
        $('l-pw').value = '';
        if (d.ok) { poll(); } else { showLogin('auth_password', d.error); }
      });
    });
  }

  watchTheme();
  watchSize();
  bindWizard();
  bindDash();
  poll();
}());
