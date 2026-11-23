/*
 * SAE dev dashboard front end. Vanilla JS, no build step, no frameworks.
 *
 * Data comes from GET /api/state (the snapshot) and the SSE stream /api/stream (named events "state" and "event").
 * Every value from the server is rendered with textContent / createElement only, because event messages carry agent
 * text and game-log names. Owner actions POST JSON with the X-SAE-Token header read from <meta name="sae-token">.
 *
 * Layout: one persistent root per view. Each render refills regions; the two verdict-form hosts (owner banner and
 * gate detail) are only replaced when their gate changes, so typing is never interrupted by a live update.
 */
'use strict';

(function () {
  // ---- constants ---------------------------------------------------------------------------------------------------

  const PIPELINE_CMD = 'uv run python3 tools/game/pipeline.py';
  const STORE_VIEW = 'sae-devdash.view';
  const STORE_GATE = 'sae-devdash.gate';
  const FEED_MAX = 200;
  const RETRY_MIN_MS = 1000;
  const RETRY_MAX_MS = 10000;
  const VALUE_MAX = 200;
  const NOTE_MAX = 500;

  const VIEWS = [
    { id: 'now', label: 'Overview' },
    { id: 'board', label: 'Tasks' },
    { id: 'gates', label: 'Game gates' },
    { id: 'results', label: 'Results' },
  ];

  /* Gate status → label and tone. Every status the snapshot can send has text, never colour alone. */
  const STATUS = {
    pass: { label: 'Passed', tone: 'ok' },
    fail: { label: 'Failed', tone: 'bad' },
    run: { label: 'Running', tone: 'info' },
    owner: { label: 'Needs you', tone: 'warn' },
    skip: { label: 'Skipped', tone: 'muted' },
    todo: { label: 'Not run', tone: 'muted' },
    setup: { label: 'Setup needed', tone: 'warn' },
    hardware: { label: 'Other hardware', tone: 'muted' },
    later: { label: 'Later milestone', tone: 'muted' },
    waiting: { label: 'Waiting', tone: 'muted' },
  };
  const STATUS_ORDER = ['pass', 'run', 'owner', 'fail', 'setup', 'skip', 'waiting', 'hardware', 'later', 'todo'];

  const TASK_COLUMNS = [
    { key: 'done', label: 'Done', dot: 'pass' },
    { key: 'in_progress', label: 'In progress', dot: 'run' },
    { key: 'pending', label: 'Pending', dot: 'todo' },
    { key: 'blocked', label: 'Blocked', dot: 'fail' },
  ];

  const KIND_TONE = { pass: 'ok', done: 'ok', fail: 'bad', owner: 'warn', start: 'info', skip: 'muted' };
  const SOURCE_COLORS = ['#7C83FF', '#3DD68C', '#C084FC', '#F5B544', '#F7706B', '#2DD4BF', '#60A5FA', '#F472B6'];
  const ARCHETYPE_COLORS = ['#7C83FF', '#F5B544', '#F472B6', '#2DD4BF', '#A3E635', '#FB923C', '#60A5FA', '#C084FC'];

  // ---- small helpers -------------------------------------------------------------------------------------------------

  /**
   * Create an element. props: className, text, attrs {name: value}, on {event: handler}, hidden, style {prop: value}.
   * Children are nodes or strings (strings become text nodes); null/false children are skipped.
   */
  function el(tag, props, children) {
    const node = document.createElement(tag);
    const p = props || {};
    if (p.className) node.className = p.className;
    if (p.text !== undefined && p.text !== null) node.textContent = String(p.text);
    if (p.attrs) {
      for (const name of Object.keys(p.attrs)) {
        const value = p.attrs[name];
        if (value !== undefined && value !== null && value !== false) node.setAttribute(name, String(value));
      }
    }
    if (p.on) {
      for (const name of Object.keys(p.on)) node.addEventListener(name, p.on[name]);
    }
    if (p.style) {
      for (const name of Object.keys(p.style)) node.style.setProperty(name, p.style[name]);
    }
    if (p.hidden) node.hidden = true;
    for (const child of children || []) {
      if (child === null || child === undefined || child === false) continue;
      node.append(typeof child === 'string' ? document.createTextNode(child) : child);
    }
    return node;
  }

  function $(id) {
    return document.getElementById(id);
  }

  function storeGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (err) {
      return null;
    }
  }

  function storeSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (err) {
      /* storage blocked (private mode, policy): the dashboard works without it */
    }
  }

  function arr(value) {
    return Array.isArray(value) ? value : [];
  }

  function str(value) {
    return value === null || value === undefined ? '' : String(value);
  }

  function slug(text) {
    return str(text).replace(/[^A-Za-z0-9_-]/g, '_');
  }

  function hashIndex(text, size) {
    let h = 0;
    for (const ch of str(text)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return h % size;
  }

  function parseTs(ts) {
    const t = Date.parse(str(ts));
    return Number.isFinite(t) ? new Date(t) : null;
  }

  function sameDay(a, b) {
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }

  /** Short local time: "14:05" today, "Oct 28 14:05" otherwise. */
  function shortTime(ts) {
    const d = parseTs(ts);
    if (!d) return '';
    const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    if (sameDay(d, new Date())) return hm;
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + hm;
  }

  function fullTime(ts) {
    const d = parseTs(ts);
    return d ? d.toLocaleString() : '';
  }

  function elapsed(fromTs) {
    const d = parseTs(fromTs);
    if (!d) return '';
    let s = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
    const h = Math.floor(s / 3600);
    s -= h * 3600;
    const m = Math.floor(s / 60);
    s -= m * 60;
    if (h > 0) return h + 'h ' + String(m).padStart(2, '0') + 'm';
    if (m > 0) return m + 'm ' + String(s).padStart(2, '0') + 's';
    return s + 's';
  }

  function safeHttpsUrl(text) {
    try {
      const url = new URL(str(text));
      return url.protocol === 'https:' ? url.href : null;
    } catch (err) {
      return null;
    }
  }

  /** Own-property lookup: never reaches Object.prototype (``code`` comes from the server). */
  function own(table, key) {
    return Object.hasOwn(table, key) ? table[key] : undefined;
  }

  function statusOf(code) {
    return own(STATUS, str(code)) || { label: str(code) || 'Unknown', tone: 'muted' };
  }

  function emptyState(text, hint) {
    return el('div', { className: 'empty' }, [el('strong', { text: text }), hint ? ' · ' + hint : null]);
  }

  function badge(code) {
    const st = statusOf(code);
    return el('span', { className: 'badge text-' + st.tone }, [
      el('span', { className: 'dot dot-' + slug(code), attrs: { 'aria-hidden': 'true' } }),
      st.label,
    ]);
  }

  /** Replace the children of ``host``, skipping null / undefined / false (which replaceChildren would print). */
  function fill(host, ...kids) {
    host.replaceChildren(...kids.filter(function (k) {
      return k !== null && k !== undefined && k !== false;
    }));
  }

  /** Mount ``node`` as the only child of ``host`` without detaching it when it is already there. */
  function mountOnce(host, node) {
    if (host.childNodes.length === 1 && host.firstChild === node) return;
    const reset = formResets.get(node);
    if (reset) reset();
    host.replaceChildren(node);
  }

  // ---- page state --------------------------------------------------------------------------------------------------

  const tokenMeta = document.querySelector('meta[name="sae-token"]');
  const TOKEN = tokenMeta ? tokenMeta.getAttribute('content') || '' : '';

  const ui = {
    snap: null,
    view: 'now',
    gate: storeGet(STORE_GATE),
    filter: 'all',
    conn: 'connecting',
    retryInMs: 0,
    lastUpdate: null,
    bannerNote: {}, // ref -> {text, error}
  };
  const freshEvents = new WeakSet();
  const forms = new Map(); // "<place>:<ref>" -> {sig, node}
  const formResets = new WeakMap(); // form node -> clears its status and error lines when it is mounted again
  let renderQueued = false;

  // ---- networking ----------------------------------------------------------------------------------------------------

  /** POST JSON with the page token. Resolves to {ok, status, data, error, stderr}; never rejects. */
  async function post(path, body) {
    let resp;
    try {
      resp = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-SAE-Token': TOKEN },
        body: JSON.stringify(body || {}),
        cache: 'no-store',
        credentials: 'same-origin',
      });
    } catch (err) {
      return { ok: false, status: 0, error: 'Could not reach the dashboard server (' + str(err && err.message) + ').' };
    }
    let data = null;
    let raw = '';
    try {
      raw = await resp.text();
      data = raw ? JSON.parse(raw) : null;
    } catch (err) {
      data = null;
    }
    if (resp.ok) return { ok: true, status: resp.status, data: data };
    let error = 'HTTP ' + resp.status;
    if (data && typeof data.error === 'string') error += ': ' + data.error;
    else if (!data && raw) error += ': ' + raw.slice(0, 500);
    if (resp.status === 403 && data && data.error === 'bad token') {
      error += '. The server restarted since this page loaded; reload the page.';
      showRestart();
    }
    const stderr = data && typeof data.stderr === 'string' ? data.stderr : '';
    if (resp.status === 502) error = 'HTTP 502: sae m0 record failed' + (stderr ? ':' : ' without output.');
    return { ok: false, status: resp.status, data: data, error: error, stderr: stderr };
  }

  let serverPid = null;

  /** Return the server pid from /api/health, or null when unreachable. */
  async function fetchPid() {
    try {
      const resp = await fetch('/api/health', { cache: 'no-store', credentials: 'same-origin' });
      if (!resp.ok) return null;
      const data = await resp.json();
      return data && typeof data.pid === 'number' ? data.pid : null;
    } catch (err) {
      return null;
    }
  }

  /** Compare the server pid with the one this page started against; a new pid means a new token. */
  async function checkRestart() {
    const pid = await fetchPid();
    if (pid === null) return;
    if (serverPid === null) serverPid = pid;
    else if (pid !== serverPid) showRestart();
  }

  function showRestart() {
    $('restart').hidden = false;
  }

  async function fetchState() {
    try {
      const resp = await fetch('/api/state', { cache: 'no-store', credentials: 'same-origin' });
      if (!resp.ok) return false;
      applyState(await resp.json());
      return true;
    } catch (err) {
      return false;
    }
  }

  let source = null;
  let retryMs = RETRY_MIN_MS;
  let retryTimer = null;
  let countdownTimer = null;

  /** Open the SSE stream. Reconnection is ours (1 s doubling to 10 s): EventSource gives up on a 503 or bad status. */
  function connect() {
    if (source) source.close();
    setConn('connecting');
    let es;
    try {
      es = new EventSource('/api/stream');
    } catch (err) {
      scheduleReconnect();
      return;
    }
    source = es;
    es.addEventListener('open', function () {
      retryMs = RETRY_MIN_MS;
      setConn('live');
    });
    es.addEventListener('state', function (e) {
      const snap = parseJson(e.data);
      if (snap) {
        retryMs = RETRY_MIN_MS;
        setConn('live');
        applyState(snap);
      }
    });
    es.addEventListener('event', function (e) {
      const item = parseJson(e.data);
      if (item) addEvent(item);
    });
    es.addEventListener('error', function () {
      if (source !== es) return;
      es.close();
      source = null;
      scheduleReconnect();
    });
  }

  function scheduleReconnect() {
    clearTimeout(retryTimer);
    clearInterval(countdownTimer);
    const wait = retryMs;
    retryMs = Math.min(retryMs * 2, RETRY_MAX_MS);
    const due = Date.now() + wait;
    ui.retryInMs = wait;
    setConn('disconnected');
    countdownTimer = setInterval(function () {
      ui.retryInMs = Math.max(0, due - Date.now());
      renderConn();
    }, 1000);
    retryTimer = setTimeout(async function () {
      clearInterval(countdownTimer);
      setConn('reconnecting');
      await checkRestart();
      await fetchState();
      connect();
    }, wait);
  }

  function parseJson(text) {
    try {
      const value = JSON.parse(text);
      return value && typeof value === 'object' ? value : null;
    } catch (err) {
      return null;
    }
  }

  function setConn(state) {
    ui.conn = state;
    renderConn();
  }

  function applyState(snap) {
    if (!snap || typeof snap !== 'object') return;
    ui.snap = snap;
    ui.lastUpdate = new Date();
    scheduleRender();
  }

  function addEvent(item) {
    if (!ui.snap) return;
    const feed = arr(ui.snap.feed).slice();
    freshEvents.add(item);
    feed.unshift(item);
    ui.snap.feed = feed.slice(0, FEED_MAX);
    ui.lastUpdate = new Date();
    scheduleRender();
  }

  // ---- rendering: shell ----------------------------------------------------------------------------------------------

  function scheduleRender() {
    if (renderQueued) return;
    renderQueued = true;
    window.requestAnimationFrame(function () {
      renderQueued = false;
      render();
    });
  }

  /** Re-render everything, keeping keyboard focus on the logically same control when its node is rebuilt. */
  function render() {
    const active = document.activeElement;
    const focusKey = active && active.dataset ? active.dataset.focusKey : null;
    renderTop();
    renderNav();
    renderEnv();
    renderBanner();
    renderKpis();
    renderView();
    renderFoot();
    const now = document.activeElement;
    if (focusKey && (!now || now === document.body || !now.isConnected)) restoreFocus(focusKey);
  }

  function restoreFocus(key) {
    for (const node of document.querySelectorAll('[data-focus-key]')) {
      if (node.dataset.focusKey === key) {
        node.focus({ preventScroll: true });
        return;
      }
    }
  }

  function renderConn() {
    const pill = $('conn');
    if (ui.conn === 'live') {
      pill.className = 'pill pill-ok';
      pill.textContent = 'Live';
    } else if (ui.conn === 'disconnected') {
      pill.className = 'pill pill-bad';
      pill.textContent = 'Disconnected · retry in ' + Math.max(1, Math.ceil(ui.retryInMs / 1000)) + 's';
    } else if (ui.conn === 'reconnecting') {
      pill.className = 'pill pill-warn';
      pill.textContent = 'Reconnecting';
    } else {
      pill.className = 'pill';
      pill.textContent = 'Connecting';
    }
  }

  function renderTop() {
    const snap = ui.snap;
    $('branch').textContent = snap ? str(snap.branch) || 'unknown' : '…';
    $('view-title').textContent = viewLabel(ui.view);
    $('host').textContent = window.location.host;
    const hooksOff = $('hooks-off');
    hooksOff.hidden = !snap || snap.enabled !== false;
    hooksOff.title = 'Event hooks are off: create .sae-dev/enabled to turn them on';
    const invalid = $('invalid');
    const bad = snap && Number(snap.invalid_lines) > 0 ? Number(snap.invalid_lines) : 0;
    invalid.hidden = bad === 0;
    invalid.textContent = bad + (bad === 1 ? ' unreadable event line' : ' unreadable event lines');
    renderConn();
  }

  function viewLabel(id) {
    const v = VIEWS.find(function (x) {
      return x.id === id;
    });
    return v ? v.label : 'Overview';
  }

  function gateTotals(snap) {
    let passed = 0;
    let total = 0;
    for (const group of arr(snap && snap.gates)) {
      passed += Number(group.passed) || 0;
      total += Number(group.total) || 0;
    }
    return { passed: passed, total: total };
  }

  function navCount(id) {
    const snap = ui.snap;
    if (!snap) return '';
    if (id === 'now') {
      const n = arr(snap.running).length;
      return n ? n + ' running' : '';
    }
    if (id === 'board') {
      const t = snap.tasks || {};
      const blocked = arr(t.blocked).length;
      if (blocked) return blocked + ' blocked';
      const total = TASK_COLUMNS.reduce(function (sum, c) {
        return sum + arr(t[c.key]).length;
      }, 0);
      return total ? String(total) : '';
    }
    if (id === 'gates') {
      const g = gateTotals(snap);
      return g.total ? g.passed + '/' + g.total : '';
    }
    if (id === 'results') {
      return snap.results && snap.results.source ? '' : 'Not run';
    }
    return '';
  }

  function renderNav() {
    const nav = $('nav');
    fill(nav, 
      ...VIEWS.map(function (v) {
        const on = v.id === ui.view;
        return el(
          'button',
          {
            className: 'nav-btn',
            attrs: { type: 'button', 'aria-current': on ? 'page' : null, 'data-focus-key': 'nav:' + v.id },
            on: {
              click: function () {
                setView(v.id, true);
              },
            },
          },
          [el('span', { text: v.label }), el('span', { className: 'nav-count', text: navCount(v.id) })],
        );
      }),
    );
  }

  function setView(id, focusMain) {
    if (!VIEWS.some(function (v) {
      return v.id === id;
    })) {
      id = 'now';
    }
    ui.view = id;
    storeSet(STORE_VIEW, id);
    if (window.location.hash !== '#' + id) {
      try {
        window.history.replaceState(null, '', '#' + id);
      } catch (err) {
        /* history unavailable: the hash is a convenience only */
      }
    }
    for (const root of document.querySelectorAll('.view')) root.hidden = root.dataset.view !== id;
    render();
    if (focusMain) $('nav').querySelector('[aria-current="page"]').focus({ preventScroll: true });
  }

  function renderEnv() {
    const list = $('env');
    const rows = arr(ui.snap && ui.snap.env);
    if (!rows.length) {
      fill(list, 
        el('div', { className: 'env-row' }, [
          el('dt', { text: 'No data yet' }),
          el('dd', { className: 'text-muted', text: 'run the pipeline' }),
        ]),
      );
      return;
    }
    fill(list, 
      ...rows.map(function (r) {
        return el('div', { className: 'env-row' }, [
          el('dt', { text: str(r.k) }),
          el('dd', { className: 'text-' + slug(r.tone || 'muted'), text: str(r.v) }),
        ]);
      }),
    );
  }

  function renderKpis() {
    const host = $('kpis');
    const kpis = arr(ui.snap && ui.snap.kpis);
    if (!kpis.length) {
      fill(host, el('div', { className: 'kpi' }, [el('div', { className: 'kpi-sub', text: 'No data yet' })]));
      return;
    }
    fill(host, 
      ...kpis.map(function (k) {
        const value = str(k.value);
        const missing = value === '' || value === '—';
        return el('div', { className: 'kpi' }, [
          el('div', { className: 'kpi-label', text: str(k.label) }),
          el('div', { className: 'kpi-line' }, [
            el('span', { className: 'kpi-value' + (missing ? ' is-empty' : ''), text: missing ? 'No data yet' : value }),
            k.chip ? el('span', { className: 'chip tone-' + slug(k.tone || 'muted'), text: str(k.chip) }) : null,
          ]),
          missing ? null : el('div', { className: 'kpi-sub', text: str(k.sub) }),
        ]);
      }),
    );
  }

  function renderFoot() {
    const snap = ui.snap;
    const foot = $('foot');
    if (!snap) {
      foot.textContent = 'Waiting for the first snapshot from the dashboard server.';
      return;
    }
    const parts = ['Snapshot ' + (fullTime(snap.generated) || str(snap.generated))];
    if (ui.lastUpdate) parts.push('last update ' + ui.lastUpdate.toLocaleTimeString());
    foot.textContent = parts.join(' · ');
  }

  // ---- owner banner and verdict form -----------------------------------------------------------------------------

  const banner = {
    root: $('banner'),
    body: $('banner-body'),
    actions: $('banner-actions'),
    note: $('banner-note'),
    formHost: $('banner-form'),
  };

  function gateById(id) {
    for (const group of arr(ui.snap && ui.snap.gates)) {
      for (const item of arr(group.items)) {
        if (item.id === id) return item;
      }
    }
    return null;
  }

  function renderBanner() {
    const owner = ui.snap ? ui.snap.owner : null;
    if (!owner || !owner.ref) {
      banner.root.hidden = true;
      fill(banner.formHost);
      return;
    }
    banner.root.hidden = false;
    const ref = str(owner.ref);
    const gate = gateById(ref);
    const title = 'Waiting on you · ' + ref + (gate && gate.title ? ' ' + gate.title : '');
    fill(banner.body, 
      el('span', { className: 'banner-title', text: title, attrs: { id: 'banner-title' } }),
      el('span', { className: 'banner-msg', text: str(owner.msg) }),
      owner.since
        ? el('span', {
            className: 'banner-since',
            text: 'Since ' + shortTime(owner.since),
            attrs: { title: fullTime(owner.since) },
          })
        : null,
    );
    const note = ui.bannerNote[ref];
    const busy = Boolean(note && note.busy);
    if (owner.judged) {
      fill(banner.actions);
    } else {
      fill(banner.actions, 
        el('button', {
          className: 'btn btn-ghost-warn',
          text: busy ? 'Marking done…' : 'Mark done',
          attrs: { type: 'button', 'data-focus-key': 'done:' + ref, disabled: busy, 'aria-busy': busy ? 'true' : null },
          on: {
            click: function () {
              markDone(ref);
            },
          },
        }),
      );
    }
    fill(banner.note, 
      note && note.text
        ? el(note.error ? 'pre' : 'span', { className: note.error ? 'verdict-err' : 'verdict-msg', text: note.text })
        : null,
    );
    banner.note.hidden = !(note && note.text);
    if (owner.judged) {
      mountOnce(banner.formHost, verdictForm('banner', ref, str(owner.question), arr(owner.evidence)));
    } else {
      fill(banner.formHost);
    }
  }

  async function markDone(ref) {
    const current = ui.bannerNote[ref];
    if (current && current.busy) return;
    ui.bannerNote[ref] = { busy: true, text: '', error: false };
    render();
    const res = await post('/api/owner/' + encodeURIComponent(ref) + '/done', {});
    ui.bannerNote[ref] = res.ok ? { text: 'Marked done. The banner clears with the next update.', error: false } : {
      text: res.error,
      error: true,
    };
    scheduleRender();
  }

  /**
   * The verdict form for a judged gate: one labelled input per evidence item, an optional note, Pass / Fail.
   * Cached per place and ref, so live re-renders keep the node (and what the owner typed) in place.
   */
  function verdictForm(place, ref, question, evidence) {
    const sig = JSON.stringify([question, evidence]);
    const cacheKey = place + ':' + ref;
    const hit = forms.get(cacheKey);
    if (hit && hit.sig === sig) return hit.node;

    const base = 'vf-' + place + '-' + slug(ref);
    const inputs = [];
    const fields = evidence.map(function (item, i) {
      const id = base + '-' + i;
      const input = el('input', {
        attrs: { id: id, type: 'text', name: str(item.key), maxlength: VALUE_MAX, autocomplete: 'off', spellcheck: 'false' },
      });
      inputs.push({ key: str(item.key), label: str(item.label) || str(item.key), input: input });
      return el('div', { className: 'field' }, [
        el('label', { text: str(item.label) || str(item.key), attrs: { for: id } }),
        input,
      ]);
    });
    const noteId = base + '-note';
    const note = el('textarea', { attrs: { id: noteId, name: 'note', maxlength: NOTE_MAX, rows: 2 } });
    const msg = el('span', { className: 'verdict-msg', attrs: { role: 'status', 'aria-live': 'polite' } });
    const err = el('pre', { className: 'verdict-err', attrs: { role: 'alert' }, hidden: true });
    const passBtn = el('button', { className: 'btn btn-pass', text: 'Pass', attrs: { type: 'button' } });
    const failBtn = el('button', { className: 'btn btn-fail', text: 'Fail', attrs: { type: 'button' } });

    async function submit(verdict) {
      err.hidden = true;
      err.textContent = '';
      msg.className = 'verdict-msg';
      const values = {};
      for (const f of inputs) {
        const v = f.input.value.trim();
        if (!v) continue;
        if (v.length > VALUE_MAX || /[\u0000-\u001f\u007f]/.test(v)) {
          err.textContent = f.label + ': at most ' + VALUE_MAX + ' printable characters.';
          err.hidden = false;
          f.input.focus();
          return;
        }
        values[f.key] = v;
      }
      const noteText = note.value.replace(/\s*[\r\n]+\s*/g, ' ').trim();
      if (noteText.length > NOTE_MAX) {
        err.textContent = 'Note: at most ' + NOTE_MAX + ' characters.';
        err.hidden = false;
        return;
      }
      passBtn.disabled = true;
      failBtn.disabled = true;
      msg.textContent = 'Recording ' + verdict + '…';
      const res = await post('/api/owner/' + encodeURIComponent(ref) + '/verdict', {
        verdict: verdict,
        values: values,
        ...(noteText ? { note: noteText } : {}),
      });
      passBtn.disabled = false;
      failBtn.disabled = false;
      if (res.ok) {
        msg.className = 'verdict-msg is-ok';
        msg.textContent = 'Recorded ' + (verdict === 'pass' ? 'Pass' : 'Fail') + ' for ' + ref + '.';
        for (const f of inputs) f.input.value = '';
        note.value = '';
        return;
      }
      msg.textContent = '';
      err.textContent = res.stderr ? res.error + '\n' + res.stderr : res.error;
      err.hidden = false;
    }

    passBtn.addEventListener('click', function () {
      submit('pass');
    });
    failBtn.addEventListener('click', function () {
      submit('fail');
    });
    const form = el(
      'form',
      {
        className: 'verdict',
        attrs: { 'aria-label': 'Record a verdict for ' + ref, novalidate: true },
        on: {
          submit: function (e) {
            e.preventDefault();
          },
        },
      },
      [
        el('p', { className: 'verdict-q', text: question || 'Record what you saw in the game.' }),
        fields.length ? el('div', { className: 'verdict-fields' }, fields) : null,
        el('div', { className: 'field' }, [el('label', { text: 'Note (optional)', attrs: { for: noteId } }), note]),
        el('div', { className: 'verdict-actions' }, [passBtn, failBtn, msg]),
        err,
      ],
    );
    formResets.set(form, function () {
      msg.className = 'verdict-msg';
      msg.textContent = '';
      err.hidden = true;
      err.textContent = '';
    });
    forms.set(cacheKey, { sig: sig, node: form });
    return form;
  }

  // ---- views ---------------------------------------------------------------------------------------------------------

  const views = {};

  function renderView() {
    const id = ui.view;
    if (!views[id]) views[id] = own(BUILDERS, id)($('view-' + id));
    views[id].render(ui.snap);
  }

  // Overview: running items, activity feed, pull requests and CI.
  function buildNow(root) {
    const runCount = el('span', { className: 'card-sub' });
    const runList = el('div');
    const filters = el('div', { className: 'filters', attrs: { role: 'group', 'aria-label': 'Filter activity' } });
    const feed = el('ol', { className: 'feed', attrs: { 'aria-label': 'Recent events, newest first' } });
    const ciLine = el('span', { className: 'card-sub' });
    const prList = el('div');
    root.append(
      el('div', { className: 'row' }, [
        el('section', { className: 'card running', attrs: { 'aria-labelledby': 'run-h' } }, [
          el('div', { className: 'card-head' }, [
            el('h2', { className: 'card-title', text: 'Running now', attrs: { id: 'run-h' } }),
            runCount,
          ]),
          runList,
        ]),
        el('section', { className: 'card activity', attrs: { 'aria-labelledby': 'act-h' } }, [
          el('div', { className: 'card-head' }, [
            el('h2', { className: 'card-title', text: 'Activity', attrs: { id: 'act-h' } }),
            filters,
          ]),
          feed,
        ]),
      ]),
      el('section', { className: 'card', attrs: { 'aria-labelledby': 'pr-h' } }, [
        el('div', { className: 'card-head' }, [
          el('h2', { className: 'card-title', text: 'Pull requests and CI', attrs: { id: 'pr-h' } }),
          ciLine,
        ]),
        prList,
      ]),
    );

    return {
      render: function (snap) {
        renderRunning(snap, runCount, runList);
        renderFeed(snap, filters, feed);
        renderGithub(snap, ciLine, prList);
      },
    };
  }

  function renderRunning(snap, count, list) {
    const running = arr(snap && snap.running);
    count.textContent = running.length ? running.length + ' active' : '';
    if (!running.length) {
      fill(list, emptyState('No data yet', 'nothing is running'));
      return;
    }
    fill(list, 
      ...running.map(function (r) {
        const pct = typeof r.pct === 'number' && Number.isFinite(r.pct) ? Math.max(0, Math.min(100, r.pct)) : null;
        const who = str(r.source);
        return el('div', { className: 'run-item' }, [
          el('div', { className: 'run-top' }, [
            el('span', { className: 'run-title', text: str(r.title) }),
            el('span', {
              className: 'run-elapsed',
              text: elapsed(r.started),
              attrs: { 'data-since': str(r.started), title: 'Started ' + fullTime(r.started) },
            }),
          ]),
          el('div', { className: 'run-who' }, [
            el('span', {
              className: 'avatar',
              text: who.charAt(0).toUpperCase() || '?',
              attrs: { 'aria-hidden': 'true' },
              style: { background: SOURCE_COLORS[hashIndex(who, SOURCE_COLORS.length)] },
            }),
            who,
            r.key && r.key !== who ? el('span', { className: 'mono', text: '· ' + str(r.key) }) : null,
            r.stale
              ? el('span', {
                  className: 'chip tone-warn',
                  text: 'Stale · last event ' + (shortTime(r.last) || 'unknown'),
                  attrs: { title: 'No event for 30 min. Last event ' + fullTime(r.last) },
                })
              : null,
          ]),
          el('div', { className: 'run-step', text: str(r.step) }),
          pct === null
            ? el('div', { className: 'card-sub', text: 'No progress reported' })
            : el('div', { className: 'bar', attrs: { role: 'img', 'aria-label': pct + '% complete' } }, [
                el('span', { style: { width: pct + '%' } }),
              ]),
        ]);
      }),
    );
  }

  function renderFeed(snap, filterHost, list) {
    const feed = arr(snap && snap.feed);
    const counts = new Map();
    for (const item of feed) {
      const s = str(item.source);
      counts.set(s, (counts.get(s) || 0) + 1);
    }
    const sources = Array.from(counts.keys())
      .sort(function (a, b) {
        return counts.get(b) - counts.get(a) || a.localeCompare(b);
      })
      .slice(0, 6);
    if (ui.filter !== 'all' && !sources.includes(ui.filter)) sources.push(ui.filter);
    fill(filterHost, 
      ...['all'].concat(sources).map(function (s) {
        return el('button', {
          className: 'filter',
          text: s === 'all' ? 'All' : s,
          attrs: { type: 'button', 'aria-pressed': ui.filter === s ? 'true' : 'false', 'data-focus-key': 'filter:' + s },
          on: {
            click: function () {
              ui.filter = s;
              render();
            },
          },
        });
      }),
    );
    const shown = ui.filter === 'all' ? feed : feed.filter(function (e) {
      return str(e.source) === ui.filter;
    });
    if (!shown.length) {
      fill(list, el('li', { className: 'empty' }, [el('strong', { text: 'No data yet' })]));
      return;
    }
    fill(list, 
      ...shown.map(function (e) {
        const kind = str(e.kind);
        const tone = own(KIND_TONE, kind);
        const li = el('li', {}, [
          el('span', { className: 'feed-time', text: shortTime(e.ts), attrs: { title: fullTime(e.ts) } }),
          el('span', {
            className: 'dot',
            attrs: { 'aria-hidden': 'true' },
            style: { background: SOURCE_COLORS[hashIndex(e.source, SOURCE_COLORS.length)] },
          }),
          el('span', { className: 'feed-text' }, [
            el('span', { className: 'feed-src', text: str(e.source) }),
            tone ? el('span', { className: 'feed-kind chip tone-' + tone, text: kind }) : null,
            e.ref ? el('span', { className: 'feed-ref', text: str(e.ref) }) : null,
            ' ',
            el('span', { text: str(e.msg) }),
          ]),
        ]);
        if (freshEvents.has(e)) {
          li.classList.add('is-new');
          freshEvents.delete(e);
        }
        return li;
      }),
    );
  }

  function renderGithub(snap, ciLine, list) {
    const ci = snap ? snap.ci : null;
    fill(ciLine);
    if (ci && ci.status) {
      const url = safeHttpsUrl(ci.url);
      const status = str(ci.status);
      const tone = status === 'success' ? 'ok' : /fail|cancel|timed_out/.test(status) ? 'bad' : 'info';
      ciLine.append('CI on ' + str(snap.branch) + ': ', el('span', { className: 'chip tone-' + tone, text: status }));
      if (url) ciLine.append(' ', el('a', { text: 'Open run', attrs: { href: url, target: '_blank', rel: 'noopener noreferrer' } }));
    } else {
      ciLine.textContent = 'CI: No data yet';
    }
    const prs = arr(snap && snap.prs);
    if (!prs.length) {
      fill(list, emptyState('No data yet', 'no open pull requests, or gh is not available'));
      return;
    }
    const checkTone = { pass: 'ok', fail: 'bad', pending: 'info', none: 'muted' };
    const checkText = { pass: 'Checks passed', fail: 'Checks failed', pending: 'Checks running', none: 'No checks' };
    fill(list, 
      el(
        'ul',
        { className: 'pr-list' },
        prs.map(function (pr) {
          const c = str(pr.checks);
          return el('li', {}, [
            el('span', { className: 'pr-num', text: '#' + str(pr.number) }),
            el('span', { className: 'pr-title', text: str(pr.title) }),
            pr.isDraft ? el('span', { className: 'chip tone-muted', text: 'Draft' }) : null,
            el('span', { className: 'chip tone-' + (own(checkTone, c) || 'muted'), text: own(checkText, c) || c }),
          ]);
        }),
      ),
    );
  }

  // Tasks: the four columns of docs/dev/tasks.json.
  function buildBoard(root) {
    const board = el('div', { className: 'board' });
    const note = el('div');
    root.append(note, board);
    return {
      render: function (snap) {
        const tasks = (snap && snap.tasks) || {};
        const total = TASK_COLUMNS.reduce(function (n, c) {
          return n + arr(tasks[c.key]).length;
        }, 0);
        fill(note, 
          total
            ? ''
            : el('div', { className: 'card' }, [
                emptyState('No data yet', 'add tasks with: uv run python3 tools/devdash/dd.py task'),
              ]),
        );
        fill(board, 
          ...TASK_COLUMNS.map(function (col) {
            const items = arr(tasks[col.key]);
            const hid = 'col-' + col.key;
            return el('section', { className: 'col', attrs: { 'aria-labelledby': hid } }, [
              el('div', { className: 'col-head' }, [
                el('span', { className: 'dot dot-' + col.dot, attrs: { 'aria-hidden': 'true' } }),
                el('h2', { className: 'card-title', text: col.label, attrs: { id: hid } }),
                el('span', { className: 'col-count', text: String(items.length) }),
              ]),
              items.length ? null : emptyState('No data yet'),
              ...items.map(taskCard),
            ]);
          }),
        );
      },
    };
  }

  function taskCard(t) {
    const url = safeHttpsUrl(t.link);
    const tags = [];
    if (t.tag) tags.push(el('span', { className: 'tag', text: str(t.tag) }));
    if (t.link) {
      tags.push(
        url
          ? el('a', { className: 'tag', text: linkText(url), attrs: { href: url, target: '_blank', rel: 'noopener noreferrer' } })
          : el('span', { className: 'tag', text: str(t.link) }),
      );
    }
    if (t.id) tags.push(el('span', { className: 'tag mono', text: str(t.id) }));
    return el('article', { className: 'task' }, [
      el('div', { className: 'task-title', text: str(t.title) }),
      t.note ? el('div', { className: 'task-note', text: str(t.note) }) : null,
      t.blocked_on ? el('div', { className: 'task-blocked', text: 'Blocked on: ' + str(t.blocked_on) }) : null,
      tags.length ? el('div', { className: 'tags' }, tags) : null,
      t.updated
        ? el('div', { className: 'card-sub', text: 'Updated ' + (shortTime(t.updated) || str(t.updated)) })
        : null,
    ]);
  }

  function linkText(url) {
    try {
      const u = new URL(url);
      const m = u.pathname.match(/\/pull\/(\d+)/);
      return m ? 'PR #' + m[1] : u.host + u.pathname;
    } catch (err) {
      return url;
    }
  }

  // Game gates: grouped chips, a legend with counts, and the selected gate's detail with the verdict form.
  function buildGates(root) {
    const legend = el('ul', { className: 'legend', attrs: { 'aria-label': 'Gate status counts' } });
    const groups = el('div', { className: 'gate-groups' });
    const info = el('div', { className: 'detail-info', style: { display: 'flex', 'flex-direction': 'column', gap: '10px' } });
    const formHost = el('div');
    const detail = el('aside', { className: 'card detail', attrs: { 'aria-labelledby': 'gate-detail-h' } }, [
      el('h2', { className: 'eyebrow', text: 'Gate detail', attrs: { id: 'gate-detail-h' } }),
      info,
      formHost,
    ]);
    root.append(el('div', { className: 'row' }, [el('div', { className: 'gates-main' }, [legend, groups]), detail]));

    return {
      render: function (snap) {
        const all = [];
        for (const g of arr(snap && snap.gates)) for (const item of arr(g.items)) all.push(item);
        if (!all.length) {
          fill(legend);
          fill(groups, el('div', { className: 'card' }, [emptyState('No data yet', 'the gate registry is empty or unreadable')]));
          fill(info, emptyState('No data yet'));
          fill(formHost);
          return;
        }
        const counts = {};
        for (const item of all) counts[item.status] = (counts[item.status] || 0) + 1;
        fill(legend, 
          ...STATUS_ORDER.filter(function (s) {
            return counts[s];
          }).map(function (s) {
            return el('li', {}, [
              el('span', { className: 'dot dot-' + s, attrs: { 'aria-hidden': 'true' } }),
              statusOf(s).label + ' ' + counts[s],
            ]);
          }),
        );
        let selected = all.find(function (x) {
          return x.id === ui.gate;
        });
        if (!selected) {
          const ownerRef = snap.owner ? snap.owner.ref : null;
          selected =
            all.find(function (x) {
              return x.id === ownerRef;
            }) ||
            all.find(function (x) {
              return x.status === 'run';
            }) ||
            all[0];
        }
        fill(groups, ...arr(snap.gates).map(function (g) {
          return gateGroup(g, selected.id);
        }));
        renderGateDetail(selected, info, formHost);
      },
    };
  }

  function gateGroup(g, selectedId) {
    const total = Number(g.total) || 0;
    const passed = Number(g.passed) || 0;
    const summary = passed + ' / ' + total + ' passed';
    const hid = 'stage-' + slug(g.stage);
    return el('section', { className: 'card card-pad', attrs: { 'aria-labelledby': hid } }, [
      el('div', { className: 'group-head' }, [
        el('h2', { className: 'card-title', text: str(g.name), attrs: { id: hid } }),
        el('div', { className: 'group-meter' }, [
          el('div', { className: 'bar is-ok', attrs: { 'aria-hidden': 'true' } }, [
            el('span', { style: { width: (total ? Math.round((passed / total) * 100) : 0) + '%' } }),
          ]),
          el('span', { text: summary }),
        ]),
      ]),
      el(
        'div',
        { className: 'chips' },
        arr(g.items).map(function (x) {
          const st = statusOf(x.status);
          return el(
            'button',
            {
              className: 'gate-chip',
              attrs: {
                type: 'button',
                'aria-pressed': x.id === selectedId ? 'true' : 'false',
                'aria-label': str(x.id) + ', ' + str(x.title) + ': ' + st.label,
                title: str(x.title),
                'data-focus-key': 'gate:' + str(x.id),
              },
              on: {
                click: function () {
                  ui.gate = str(x.id);
                  storeSet(STORE_GATE, ui.gate);
                  render();
                  if (window.matchMedia('(max-width: 720px)').matches) {
                    $('gate-detail-h').scrollIntoView({ block: 'start' });
                  }
                },
              },
            },
            [
              el('span', { className: 'gate-chip-id' }, [
                el('span', { className: 'dot dot-' + slug(x.status), attrs: { 'aria-hidden': 'true' } }),
                str(x.id),
              ]),
              el('span', { className: 'gate-chip-status', text: st.label }),
            ],
          );
        }),
      ),
    ]);
  }

  function renderGateDetail(g, info, formHost) {
    const assumptions = arr(g.unverified);
    const cmd = str(g.cmd);
    const code = el('code', { className: 'code', text: cmd || 'No data yet' });
    fill(info, 
      el('div', { className: 'detail-id' }, [el('span', { className: 'mono', text: str(g.id) }), badge(g.status)]),
      el('div', { className: 'detail-title', text: str(g.title) }),
      el('div', { className: 'detail-text', text: str(g.detail) || 'No data yet' }),
      assumptions.length
        ? el('div', {
            className: 'unverified',
            text: 'UNVERIFIED until M0 proves it: ' + assumptions.join(', '),
          })
        : null,
      el('div', { className: 'rule' }),
      el('div', { className: 'code-box' }, [
        el('div', { className: 'code-head' }, [
          el('span', { text: 'Command' }),
          cmd
            ? el('button', {
                className: 'btn btn-sm',
                text: 'Copy',
                attrs: { type: 'button', 'data-focus-key': 'copy-cmd', 'aria-label': 'Copy the command for ' + str(g.id) },
                on: {
                  click: function (e) {
                    copyText(cmd, e.currentTarget, function () {
                      selectNode(code);
                    });
                  },
                },
              })
            : null,
        ]),
        code,
      ]),
      el('div', { className: 'kv' }, [
        el('span', { text: 'Last run' }),
        el('span', { text: g.when ? shortTime(g.when) || str(g.when) : 'Not run', attrs: { title: fullTime(g.when) } }),
      ]),
      g.judged ? el('div', { className: 'rule' }) : null,
    );
    const owner = ui.snap ? ui.snap.owner : null;
    if (g.judged && owner && owner.judged && owner.ref === g.id) {
      fill(
        formHost,
        el('p', { className: 'verdict-msg', text: 'This gate is waiting on you: record the verdict in the banner above. ' }, [
          el('button', {
            className: 'btn btn-sm',
            text: 'Go to the form',
            attrs: { type: 'button', 'data-focus-key': 'goto-banner' },
            on: {
              click: function () {
                const first = banner.formHost.querySelector('input, textarea');
                banner.root.scrollIntoView({ block: 'start' });
                if (first) first.focus({ preventScroll: true });
              },
            },
          }),
        ]),
      );
    } else if (g.judged) {
      mountOnce(formHost, verdictForm('gate', str(g.id), str(g.question), arr(g.evidence)));
    } else {
      fill(formHost);
    }
  }

  // Results: the newest pipeline run's parse cost, belief, commitments and game impact.
  function buildResults(root) {
    const note = el('p', { className: 'results-note' });
    const parse = el('section', { className: 'card card-pad', attrs: { 'aria-labelledby': 'parse-h' } });
    const belief = el('section', { className: 'card card-pad', attrs: { 'aria-labelledby': 'belief-h' } });
    const commits = el('section', { className: 'card card-pad', attrs: { 'aria-labelledby': 'commit-h' } });
    const impact = el('section', { className: 'card card-pad', attrs: { 'aria-labelledby': 'impact-h' } });
    root.append(note, el('div', { className: 'charts' }, [parse, belief, commits, impact]));
    return {
      render: function (snap) {
        const results = (snap && snap.results) || {};
        note.textContent = results.source
          ? 'From pipeline run ' + str(results.source) + '. Charts show only measured values.'
          : 'Not run: no pipeline results yet. Charts fill in from real Stellaris runs (X3, M0, bench R-gates).';
        renderParse(parse, results);
        renderBelief(belief, arr(results.belief));
        renderCommitments(commits, arr(results.commitments));
        renderImpact(impact, arr(results.impact));
      },
    };
  }

  function chartHead(id, title, sub, extra) {
    return el('div', { className: 'chart-head' }, [
      el('div', {}, [
        el('h2', { className: 'card-title', text: title, attrs: { id: id } }),
        el('div', { className: 'card-sub', text: sub }),
      ]),
      extra || null,
    ]);
  }

  function renderParse(host, results) {
    const rows = arr(results.parse).slice(-24);
    const budget = typeof results.budget_s === 'number' && results.budget_s > 0 ? results.budget_s : 3.0;
    const head = chartHead(
      'parse-h',
      'Parse wall time per autosave',
      'X3-G2 · seconds',
      el('span', { className: 'card-sub text-warn', text: 'budget ' + budget.toFixed(1) + ' s' }),
    );
    if (!rows.length) {
      fill(host, head, emptyState('Not run'));
      return;
    }
    const PLOT = 140;
    const max = Math.max(budget * 1.15, ...rows.map(function (r) {
      return Number(r.wall_s) || 0;
    })) * 1.05;
    const plot = el('div', { className: 'plot' }, [
      el('div', {
        className: 'budget-line',
        attrs: { 'aria-hidden': 'true' },
        style: { bottom: Math.round((budget / max) * PLOT) + 'px' },
      }),
      ...rows.map(function (r) {
        const v = Number(r.wall_s) || 0;
        const over = v > budget;
        return el('div', {
          className: 'plot-col',
          attrs: { role: 'img', 'aria-label': str(r.label) + ': ' + v.toFixed(2) + ' seconds' + (Number(r.rss_bytes) > 0 ? ', ' + Math.round(Number(r.rss_bytes) / (1024 * 1024)) + ' MB RSS' : '') + (over ? ', over budget' : '') },
        }, [
          rows.length > 12
            ? null
            : el('span', { className: 'plot-val' + (over ? ' text-bad' : ''), text: v.toFixed(2) }),
          el('div', { className: 'plot-bar' + (over ? ' is-bad' : ''), style: { height: Math.max(2, Math.round((v / max) * PLOT)) + 'px' } }),
        ]);
      }),
    ]);
    const axis = el('div', { className: 'axis', attrs: { 'aria-hidden': 'true' } }, rows.map(function (r) {
      return el('span', { text: str(r.label), attrs: { title: str(r.label) } });
    }));
    const note = parseNote(rows);
    fill(host, head, plot, axis, el('div', { className: 'card-sub text-warn parse-note', text: note }));
  }

  // The parse numbers come from the Linux parser reading a WSL copy: never present them as the Windows A-24 result.
  function parseNote(rows) {
    const last = rows[rows.length - 1] || {};
    const platform = str(last.platform || 'unknown platform');
    const source = last.source ? ', ' + str(last.source) : '';
    let peak = 0;
    for (const r of rows) peak = Math.max(peak, Number(r.rss_bytes) || 0);
    const rss = peak > 0 ? ' · peak RSS ' + (peak / (1024 * 1024)).toFixed(0) + ' MB' : '';
    return 'UNVERIFIED A-24 (measured on ' + platform + source + ')' + rss;
  }

  function renderBelief(host, rows) {
    const head = chartHead('belief-h', 'Belief about your strategy', 'Probability per archetype, by game date');
    if (!rows.length) {
      fill(host, head, emptyState('Not run'));
      return;
    }
    const MAX_COLS = 30;
    let picked = rows;
    if (rows.length > MAX_COLS) {
      picked = [];
      for (let i = 0; i < MAX_COLS; i += 1) picked.push(rows[Math.round((i * (rows.length - 1)) / (MAX_COLS - 1))]);
    }
    const names = new Set();
    for (const r of picked) for (const k of Object.keys(r.p || {})) names.add(k);
    const archetypes = Array.from(names).sort();
    const color = function (name) {
      return ARCHETYPE_COLORS[archetypes.indexOf(name) % ARCHETYPE_COLORS.length];
    };
    const every = Math.max(1, Math.ceil(picked.length / 6));
    const plot = el('div', { className: 'stack-plot' }, picked.map(function (r) {
      const p = r.p || {};
      const label = str(r.date) + ': ' + archetypes.map(function (a) {
        return a + ' ' + Math.round((Number(p[a]) || 0) * 100) + '%';
      }).join(', ');
      return el('div', { className: 'stack', attrs: { role: 'img', 'aria-label': label, title: label } }, archetypes.map(function (a) {
        const v = Math.max(0, Number(p[a]) || 0);
        return el('div', { style: { flex: v + ' 1 0', background: color(a) } });
      }));
    }));
    const axis = el('div', { className: 'axis stack-axis', attrs: { 'aria-hidden': 'true' } }, picked.map(function (r, i) {
      const date = str(r.date);
      const year = /^\d{4}\./.test(date) ? date.slice(0, 4) : date;
      return el('span', { text: i % every === 0 ? year : '', attrs: { title: date } });
    }));
    const legend = el('ul', { className: 'swatches', attrs: { 'aria-label': 'Archetypes' } }, archetypes.map(function (a) {
      return el('li', {}, [el('span', { className: 'swatch', attrs: { 'aria-hidden': 'true' }, style: { background: color(a) } }), a]);
    }));
    const sub = rows.length > picked.length ? el('div', { className: 'card-sub', text: picked.length + ' of ' + rows.length + ' updates shown, evenly spaced' }) : null;
    fill(host, head, plot, axis, legend, sub);
  }

  function renderCommitments(host, rows) {
    const head = chartHead('commit-h', 'Counter-strategy commitments', '24-month commitments and the reason, newest first');
    if (!rows.length) {
      fill(host, head, emptyState('Not run'));
      return;
    }
    const LIMIT = 50;
    const newest = rows.slice().reverse();
    const shown = newest.slice(0, LIMIT);
    fill(host, 
      head,
      el('ol', { className: 'commits' }, shown.map(function (m) {
        return el('li', {}, [
          el('span', { className: 'commit-when', text: str(m.date) }),
          el('span', { className: 'commit-what', text: str(m.strategy) }),
          el('span', { className: 'commit-why', text: str(m.reason) }),
        ]);
      })),
      newest.length > LIMIT ? el('div', { className: 'card-sub', text: newest.length - LIMIT + ' earlier commitments not shown' }) : null,
    );
  }

  function renderImpact(host, rows) {
    const head = chartHead('impact-h', 'Game impact (R-gates)', 'Measured on the game PC against the release budget');
    if (!rows.length) {
      fill(host, head, emptyState('Not run'));
      return;
    }
    fill(host, 
      head,
      el('div', { className: 'impact' }, rows.map(function (r) {
        const pct = Number(r.pct) || 0;
        const over = pct > 100;
        return el('div', { className: 'impact-row' }, [
          el('div', { className: 'impact-top' }, [
            el('span', { className: 'impact-label', text: str(r.label) }),
            el('span', { className: 'impact-value' + (over ? ' text-bad' : ''), text: str(r.value) + (over ? ' · over budget' : '') }),
          ]),
          el('div', {
            className: 'bar ' + (over ? 'is-bad' : 'is-teal'),
            attrs: { role: 'img', 'aria-label': str(r.label) + ': ' + Math.round(pct) + '% of budget' + (over ? ', over budget' : '') },
          }, [el('span', { style: { width: Math.max(0, Math.min(100, pct)) + '%' } })]),
        ]);
      })),
    );
  }

  const BUILDERS = { now: buildNow, board: buildBoard, gates: buildGates, results: buildResults };

  // ---- clipboard -----------------------------------------------------------------------------------------------------

  function selectNode(node) {
    try {
      const range = document.createRange();
      range.selectNodeContents(node);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    } catch (err) {
      /* selection unsupported: the text stays visible for manual copy */
    }
  }

  /** Copy ``text``; on failure run ``fallback`` (which selects the text for a manual Ctrl+C). */
  async function copyText(text, button, fallback) {
    const original = button.textContent;
    let ok = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        ok = true;
      }
    } catch (err) {
      ok = false;
    }
    if (!ok) fallback();
    button.textContent = ok ? 'Copied' : 'Press Ctrl+C to copy';
    setTimeout(function () {
      button.textContent = original;
    }, 1600);
  }

  function setupCopyPipeline() {
    const button = $('copy-pipeline');
    button.title = PIPELINE_CMD;
    button.addEventListener('click', function () {
      copyText(PIPELINE_CMD, button, function () {
        const bar = $('copy-fallback');
        const input = $('copy-fallback-text');
        input.value = PIPELINE_CMD;
        bar.hidden = false;
        input.focus();
        input.select();
      });
    });
  }

  // ---- clocks and start-up -------------------------------------------------------------------------------------------

  function tickClocks() {
    for (const node of document.querySelectorAll('[data-since]')) node.textContent = elapsed(node.dataset.since);
  }

  function initialView() {
    const fromHash = window.location.hash.replace(/^#/, '');
    if (VIEWS.some(function (v) {
      return v.id === fromHash;
    })) {
      return fromHash;
    }
    const stored = storeGet(STORE_VIEW);
    return VIEWS.some(function (v) {
      return v.id === stored;
    })
      ? stored
      : 'now';
  }

  function start() {
    setupCopyPipeline();
    $('reload').addEventListener('click', function () {
      window.location.reload();
    });
    checkRestart();
    window.addEventListener('hashchange', function () {
      const id = window.location.hash.replace(/^#/, '');
      if (id !== ui.view) setView(id, false);
    });
    setView(initialView(), false);
    setInterval(tickClocks, 1000);
    connect();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
