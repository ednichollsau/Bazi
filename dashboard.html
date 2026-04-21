<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Patient Records · Ear Seed Protocol</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Raleway:wght@300;400;600&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --cream:       #FAF3E4;
      --cream-dark:  #F0E6D3;
      --brown:       #2C1A0E;
      --brown-mid:   #6B4C36;
      --brown-light: #8B6F5C;
      --green:       #3D5A4C;
      --border:      #E0D5C1;
      --font:        'Raleway', sans-serif;
    }
    body { font-family: var(--font); background: var(--cream-dark); color: var(--brown); min-height: 100vh; }

    /* ── Layout ─────────────────────────────────────────────── */
    .layout { display: grid; grid-template-columns: 340px 1fr; height: 100vh; }

    /* ── Sidebar ─────────────────────────────────────────────── */
    .sidebar { background: var(--cream); border-right: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; }

    .sidebar-head { padding: 28px 24px 18px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
    .sidebar-eyebrow { font-size: 9px; font-weight: 600; letter-spacing: 0.3em; text-transform: uppercase; color: var(--green); margin-bottom: 8px; }
    .sidebar-title { font-size: 20px; font-weight: 300; color: var(--brown); margin-bottom: 16px; }
    .sidebar-search { width: 100%; padding: 9px 12px; font-family: var(--font); font-size: 13px; border: 1px solid var(--border); border-radius: 2px; background: var(--cream); color: var(--brown); }
    .sidebar-search:focus { outline: none; border-color: var(--green); }
    .patient-count { font-size: 10px; letter-spacing: 0.08em; color: var(--brown-light); margin-top: 8px; }

    .patient-list { flex: 1; overflow-y: auto; }

    .patient-item { padding: 14px 24px; border-bottom: 1px solid var(--border); cursor: pointer; transition: background 0.15s; }
    .patient-item:hover { background: var(--cream-dark); }
    .patient-item.active { background: var(--cream-dark); border-left: 3px solid var(--green); padding-left: 21px; }
    .pt-name { font-size: 14px; font-weight: 600; color: var(--brown); margin-bottom: 3px; }
    .pt-email { font-size: 11px; color: var(--brown-light); margin-bottom: 4px; }
    .pt-meta { font-size: 10px; letter-spacing: 0.04em; color: var(--brown-light); }
    .pt-principle { font-size: 11px; color: var(--brown-mid); margin-top: 5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    /* ── Main ───────────────────────────────────────────────── */
    .main { overflow-y: auto; padding: 44px 48px; }

    .empty-state { display: flex; align-items: center; justify-content: center; height: 60vh; font-size: 13px; letter-spacing: 0.12em; color: var(--brown-light); }

    .detail-eyebrow { font-size: 9px; font-weight: 600; letter-spacing: 0.3em; text-transform: uppercase; color: var(--green); margin-bottom: 10px; }
    .detail-name { font-size: 28px; font-weight: 300; color: var(--brown); margin-bottom: 8px; }
    .detail-meta { font-size: 12px; letter-spacing: 0.05em; color: var(--brown-light); margin-bottom: 32px; line-height: 1.7; }

    .section-rule { height: 1px; background: var(--border); margin: 36px 0; }
    .section-label { font-size: 9px; font-weight: 600; letter-spacing: 0.3em; text-transform: uppercase; color: var(--green); margin-bottom: 18px; }

    /* Element bars */
    .elem-row { display: flex; align-items: center; gap: 14px; margin-bottom: 9px; }
    .elem-name { width: 44px; font-size: 10px; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase; flex-shrink: 0; }
    .elem-track { flex: 1; height: 4px; background: var(--border); }
    .elem-fill { height: 100%; }
    .elem-state { width: 72px; font-size: 10px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--brown-light); text-align: right; flex-shrink: 0; }

    /* Reading */
    .reading-body h2 { font-size: 9px; font-weight: 600; letter-spacing: 0.3em; text-transform: uppercase; color: var(--green); margin: 24px 0 10px; }
    .reading-body h2:first-child { margin-top: 0; }
    .reading-body h3 { font-size: 9px; font-weight: 600; letter-spacing: 0.3em; text-transform: uppercase; color: var(--brown-light); margin: 22px 0 12px; }
    .reading-body p { font-size: 14px; line-height: 1.8; color: var(--brown-mid); margin-bottom: 10px; }
    .reading-body p.conclusion { font-style: italic; padding-top: 16px; margin-top: 12px; border-top: 1px solid var(--border); }
    .tip-row { display: flex; gap: 10px; align-items: flex-start; padding: 10px 0; border-top: 1px solid var(--border); }
    .tip-row:first-of-type { border-top: none; }
    .tip-tag { font-size: 8px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; border: 1px solid; padding: 2px 6px; white-space: nowrap; flex-shrink: 0; margin-top: 2px; }
    .tip-text { font-size: 13px; line-height: 1.6; color: var(--brown-mid); }

    /* Protocol */
    .pt-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .pt-card { background: var(--cream); border: 1px solid var(--border); }
    .pt-card-head { padding: 11px 16px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
    .pt-card-title { font-size: 9px; font-weight: 600; letter-spacing: 0.3em; text-transform: uppercase; color: var(--green); }
    .pt-card-count { font-size: 10px; color: var(--brown-light); }
    .pt-divider { padding: 5px 16px; font-size: 8px; font-weight: 600; letter-spacing: 0.15em; text-transform: uppercase; color: var(--brown-light); background: rgba(44,26,14,0.03); border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
    .pt-point { padding: 11px 16px; }
    .pt-point + .pt-point { border-top: 1px solid var(--border); }
    .pt-point-top { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 2px; }
    .pt-point-name { font-size: 13px; font-weight: 600; color: var(--brown); }
    .pt-point-meta { font-size: 10px; color: var(--brown-light); margin-bottom: 3px; }
    .pt-point-action { font-size: 12px; line-height: 1.5; color: var(--brown-mid); }
    .pt-body-ref { font-size: 10px; color: var(--brown-light); font-style: italic; margin-top: 3px; }
    .bil-tag { font-size: 7px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; border: 1px solid var(--border); color: var(--brown-light); padding: 1px 4px; vertical-align: middle; margin-left: 4px; }
    .metal-chip { display: inline-flex; align-items: center; gap: 4px; font-size: 7px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; padding: 2px 7px; border-width: 1px; border-style: solid; border-radius: 1px; white-space: nowrap; flex-shrink: 0; }
    .metal-chip.gold   { background: #F8EDD6; border-color: #C4963A; color: #7A5A10; }
    .metal-chip.silver { background: #E8E4DC; border-color: #9E9080; color: #4A3D30; }
    .metal-dot { width: 5px; height: 5px; border-radius: 50%; }
    .metal-chip.gold   .metal-dot { background: #C4963A; }
    .metal-chip.silver .metal-dot { background: #9E9080; }

    /* Notes */
    .notes-area { width: 100%; padding: 12px 14px; font-family: var(--font); font-size: 14px; line-height: 1.75; border: 1px solid var(--border); border-radius: 2px; background: var(--cream); color: var(--brown); resize: vertical; min-height: 120px; }
    .notes-area:focus { outline: none; border-color: var(--green); }
    .notes-footer { display: flex; align-items: center; gap: 14px; margin-top: 10px; }
    .notes-save { padding: 10px 24px; font-family: var(--font); font-size: 11px; font-weight: 600; letter-spacing: 0.18em; text-transform: uppercase; background: var(--green); color: var(--cream); border: none; border-radius: 2px; cursor: pointer; transition: background 0.2s; }
    .notes-save:hover { background: #2e4439; }
    .notes-saved { font-size: 11px; letter-spacing: 0.08em; color: var(--green); opacity: 0; transition: opacity 0.4s; }
    .notes-saved.show { opacity: 1; }

    @media (max-width: 960px) {
      .layout { grid-template-columns: 1fr; height: auto; }
      .sidebar { height: 50vh; }
      .main { padding: 28px 24px; }
      .pt-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
<div class="layout">

  <!-- ── Sidebar ─────────────────────────────────────── -->
  <div class="sidebar">
    <div class="sidebar-head">
      <p class="sidebar-eyebrow">Ed Nicholls Acupuncture</p>
      <p class="sidebar-title">Patient Records</p>
      <input class="sidebar-search" type="text" id="search" placeholder="Search name or email…">
      <p class="patient-count" id="patient-count"></p>
    </div>
    <div class="patient-list" id="patient-list"></div>
  </div>

  <!-- ── Main ────────────────────────────────────────── -->
  <div class="main" id="main">
    <div class="empty-state">Select a patient to view their record</div>
  </div>

</div>
<script>
  const TOKEN     = '__TOKEN__';
  const ELEM_COL  = { Wood:'#5A7A4C', Fire:'#9E5040', Earth:'#A87C35', Metal:'#7A8885', Water:'#4A6E8C' };
  const STATE_PCT = { Absent:4, Low:28, Balanced:58, Excess:100 };
  const INTENT    = { tonify:'Nourish', sedate:'Regulate', regulate:'Regulate Qi' };
  const UNIVERSAL = ['Shen Men','Point Zero','Thalamus Point','Sympathetic Autonomic'];
  const TIP_COL   = { NOURISH:'#3D5A4C', MOVE:'#8B4A3A', REST:'#7A6440', MIND:'#5C6B68', SEASONS:'#8B6E2A' };

  let allPatients = [];
  let activeId    = null;

  // ── Load patient list ──────────────────────────────
  async function loadList() {
    const res = await fetch('/api/patients?token=' + TOKEN);
    if (!res.ok) {
      document.getElementById('patient-list').innerHTML =
        '<p style="padding:20px;font-size:13px;color:#8B3A28">Could not load records.</p>';
      return;
    }
    allPatients = await res.json();
    renderList(allPatients);
  }

  function renderList(patients) {
    const el = document.getElementById('patient-list');
    document.getElementById('patient-count').textContent =
      patients.length + ' record' + (patients.length !== 1 ? 's' : '');
    if (!patients.length) {
      el.innerHTML = '<p style="padding:20px;font-size:13px;color:var(--brown-light)">No records yet.</p>';
      return;
    }
    el.innerHTML = patients.map(p => {
      const dob  = p.year + '-' + String(p.month).padStart(2,'0') + '-' + String(p.day).padStart(2,'0');
      const date = p.created_at ? p.created_at.slice(0,10) : '';
      const isActive = p.id === activeId;
      return '<div class="patient-item' + (isActive ? ' active' : '') + '" data-id="' + p.id + '">'
        + '<div class="pt-name">' + (p.name || '(unnamed)') + '</div>'
        + (p.email ? '<div class="pt-email">' + p.email + '</div>' : '')
        + '<div class="pt-meta">DOB ' + dob + ' &nbsp;·&nbsp; ' + (p.handedness || 'right') + '-handed &nbsp;·&nbsp; ' + date + '</div>'
        + '<div class="pt-principle">' + (p.principle || '') + '</div>'
        + '</div>';
    }).join('');
    // Attach click handlers
    el.querySelectorAll('.patient-item').forEach(function(item) {
      item.addEventListener('click', function() { loadDetail(parseInt(this.dataset.id)); });
    });
  }

  // ── Search ─────────────────────────────────────────
  document.getElementById('search').addEventListener('input', function() {
    const q = this.value.toLowerCase();
    renderList(allPatients.filter(function(p) {
      return (p.name || '').toLowerCase().includes(q) ||
             (p.email || '').toLowerCase().includes(q);
    }));
  });

  // ── Load patient detail ────────────────────────────
  async function loadDetail(id) {
    activeId = id;
    // Highlight sidebar
    document.querySelectorAll('.patient-item').forEach(function(el) {
      el.classList.toggle('active', parseInt(el.dataset.id) === id);
    });
    const res = await fetch('/api/patients/' + id + '?token=' + TOKEN);
    if (!res.ok) {
      document.getElementById('main').innerHTML = '<div class="empty-state">Could not load record.</div>';
      return;
    }
    renderDetail(await res.json());
  }

  // ── Render detail ──────────────────────────────────
  function renderDetail(d) {
    const dob          = d.year + '-' + String(d.month).padStart(2,'0') + '-' + String(d.day).padStart(2,'0');
    const submitted    = d.created_at ? d.created_at.slice(0,16).replace('T',' ') + ' UTC' : '';
    const constitution = typeof d.constitution === 'string' ? JSON.parse(d.constitution) : (d.constitution || {});
    const protocol     = typeof d.protocol     === 'string' ? JSON.parse(d.protocol)     : (d.protocol     || {});

    document.getElementById('main').innerHTML =
      '<p class="detail-eyebrow">Ba Zi &nbsp;&middot;&nbsp; Ear Seed Protocol</p>'
      + '<p class="detail-name">' + (d.name || '(unnamed)') + '</p>'
      + '<p class="detail-meta">'
        + (d.email ? d.email + '<br>' : '')
        + 'DOB ' + dob + (d.hour !== null && d.hour !== undefined ? ' &nbsp;·&nbsp; Hour ' + d.hour : '')
        + ' &nbsp;·&nbsp; ' + (d.handedness || 'right') + '-handed'
        + (submitted ? '<br>' + submitted : '')
      + '</p>'

      + '<div class="section-label">Treatment Principle</div>'
      + '<p style="font-size:15px;color:var(--brown-mid);margin-bottom:8px">' + (d.principle || '') + '</p>'
      + '<p style="font-size:12px;color:var(--brown-light)">Day Master: ' + (d.day_master || '') + '</p>'

      + '<div class="section-rule"></div>'

      + '<div class="section-label">Five Element Constitution</div>'
      + renderBars(constitution)

      + '<div class="section-rule"></div>'

      + '<div class="section-label">Reading</div>'
      + '<div class="reading-body">' + parseReading(d.reading_text || '') + '</div>'

      + '<div class="section-rule"></div>'

      + '<div class="section-label">Ear Seed Protocol &mdash; ' + (d.handedness || 'right') + '-handed</div>'
      + '<div class="pt-grid">' + renderEarCol('left', protocol) + renderEarCol('right', protocol) + '</div>'

      + '<div class="section-rule"></div>'

      + '<div class="section-label">Practitioner Notes</div>'
      + '<textarea class="notes-area" id="notes-area">' + (d.notes || '') + '</textarea>'
      + '<div class="notes-footer">'
        + '<button class="notes-save" onclick="saveNotes(' + d.id + ')">Save notes</button>'
        + '<span class="notes-saved" id="notes-saved">Saved</span>'
      + '</div>';
  }

  function renderBars(constitution) {
    return ['Wood','Fire','Earth','Metal','Water'].map(function(e) {
      var state = (constitution && constitution[e]) || 'Balanced';
      var pct   = STATE_PCT[state] !== undefined ? STATE_PCT[state] : 58;
      return '<div class="elem-row">'
        + '<div class="elem-name" style="color:' + ELEM_COL[e] + '">' + e + '</div>'
        + '<div class="elem-track"><div class="elem-fill" style="width:' + pct + '%;background:' + ELEM_COL[e] + '"></div></div>'
        + '<div class="elem-state">' + state + '</div>'
        + '</div>';
    }).join('');
  }

  function renderEarCol(side, protocol) {
    if (!protocol || !protocol.points) return '';
    var pts     = protocol.points.filter(function(p) { return p.ear === side || p.ear === 'bilateral'; });
    var masters = pts.filter(function(p) { return UNIVERSAL.indexOf(p.name) !== -1; });
    var constit = pts.filter(function(p) { return UNIVERSAL.indexOf(p.name) === -1; });
    var title   = side.charAt(0).toUpperCase() + side.slice(1);

    var inner = masters.map(renderPoint).join('');
    if (constit.length) inner += '<div class="pt-divider">Constitutional points</div>';
    inner += constit.map(renderPoint).join('');

    return '<div class="pt-card">'
      + '<div class="pt-card-head">'
        + '<div class="pt-card-title">' + title + ' ear</div>'
        + '<div class="pt-card-count">' + pts.length + ' point' + (pts.length !== 1 ? 's' : '') + '</div>'
      + '</div>'
      + '<div>' + inner + '</div>'
      + '</div>';
  }

  function renderPoint(p) {
    var metalLabel  = p.metal.charAt(0).toUpperCase() + p.metal.slice(1);
    var intentLabel = INTENT[p.intent] || p.intent;
    var bil = p.ear === 'bilateral' ? '<span class="bil-tag">both</span>' : '';

    var bodyRef = '';
    if (p.point_type === 'organ') {
      if (p.intent === 'tonify' && p.body_point_tonify)
        bodyRef = '<div class="pt-body-ref">Yuan Source — ' + p.body_point_tonify + '</div>';
      else if ((p.intent === 'sedate' || p.intent === 'regulate') && p.body_point_sedate)
        bodyRef = '<div class="pt-body-ref">Luo Point — ' + p.body_point_sedate + '</div>';
    }

    return '<div class="pt-point">'
      + '<div class="pt-point-top">'
        + '<div class="pt-point-name">' + p.name + bil + '</div>'
        + '<div class="metal-chip ' + p.metal + '"><div class="metal-dot"></div>' + metalLabel + '</div>'
      + '</div>'
      + '<div class="pt-point-meta">' + intentLabel + (UNIVERSAL.indexOf(p.name) !== -1 ? ' &middot; Master point' : '') + '</div>'
      + '<div class="pt-point-action">' + p.action + '</div>'
      + bodyRef
      + '</div>';
  }

  function parseReading(text) {
    var lines = text.split('\n');
    var html = '', buf = [], inTips = false, pastTips = false;

    function flush() {
      if (!buf.length) return;
      var s = buf.join(' ').trim();
      if (s) html += '<p' + (pastTips ? ' class="conclusion"' : '') + '>' + s + '</p>';
      buf = [];
    }

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].trim();
      if (line.indexOf('## ') === 0)  { flush(); html += '<h2>' + line.slice(3) + '</h2>'; inTips = false; continue; }
      if (line.indexOf('### ') === 0) { flush(); html += '<h3>' + line.slice(4) + '</h3>'; inTips = true; continue; }
      var tip = line.match(/^\[([A-Z]+)\]\s+(.+)$/);
      if (tip) {
        flush();
        var col = TIP_COL[tip[1]] || '#3D5A4C';
        html += '<div class="tip-row"><span class="tip-tag" style="color:' + col + ';border-color:' + col + '">' + tip[1] + '</span><span class="tip-text">' + tip[2] + '</span></div>';
        continue;
      }
      if (!line) { flush(); if (inTips) { inTips = false; pastTips = true; } continue; }
      buf.push(line);
    }
    flush();
    return html;
  }

  // ── Save notes ─────────────────────────────────────
  async function saveNotes(id) {
    var notes = document.getElementById('notes-area').value;
    var res = await fetch('/api/patients/' + id + '/notes?token=' + TOKEN, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ notes: notes }),
    });
    if (res.ok) {
      var p = allPatients.find(function(x) { return x.id === id; });
      if (p) p.notes = notes;
      var el = document.getElementById('notes-saved');
      el.classList.add('show');
      setTimeout(function() { el.classList.remove('show'); }, 2500);
    }
  }

  loadList();
</script>
</body>
</html>
