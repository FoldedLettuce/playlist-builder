/* UI for the playlist builder. All scheduling logic lives in engine.js. */
(function () {
  'use strict'
  const U = PB.util
  const CFG = PB.DEFAULT_CFG
  const LS_KEY = 'pb-state-v1'

  let ctx = null
  let meta = null
  let state = loadState()
  const openPlaylists = new Set()
  let lastPlan = null
  let noteText = ''
  let saveMsg = ''

  /* ---------------------------------------------------------------- state */
  function loadState() {
    try {
      const s = JSON.parse(localStorage.getItem(LS_KEY))
      if (s && Array.isArray(s.films) && Array.isArray(s.slots)) return s
    } catch (e) {}
    return { films: [newFilm(), newFilm()], slots: JSON.parse(JSON.stringify(PB.DEFAULT_SLOTS)), slotsTouched: false }
  }
  function save() {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(state))
    } catch (e) {}
  }
  let idSeq = Date.now()
  function newFilm() {
    return { id: 'f' + idSeq++, featureKey: '', start: '', end: '', datesTouched: false, slots: [], ratingOverride: '', trailerCount: 0, exclude: [] }
  }
  function applyDefaultSlots() {
    if (state.slotsTouched) return
    const d = PB.defaultFilmSlots(state.films.length)
    state.films.forEach((f, i) => (f.slots = d[i] ? d[i].slice() : []))
  }
  function todayISO() {
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  }

  /* -------------------------------------------------------------- helpers */
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
  const mdy = (iso) => {
    const [y, m, d] = iso.split('-')
    return `${m}/${d}/${y}`
  }
  const dayLabel = (iso) => `${DOW[U.dow(iso)]} ${U.fmtShortDate(iso)}`
  const rangeLabel = (a, b) => (a === b ? dayLabel(a) : `${dayLabel(a)} – ${dayLabel(b)}`)
  const copyable = (text, cls) => `<span class="copy ${cls || ''}" data-copy="${esc(text)}" title="Click to copy">${esc(text)}</span>`
  const scr = (n) => CFG.screens[n].name

  /* -------------------------------------------------------------- loading */
  async function load(force) {
    const btn = document.getElementById('refreshBtn')
    btn.disabled = true
    try {
      const r = await fetch('api/rows' + (force ? '?refresh=1' : ''), { credentials: 'same-origin' })
      if (!r.ok) throw new Error('HTTP ' + r.status)
      meta = await r.json()
      ctx = PB.loadData(meta.rows)
      document.getElementById('dataStatus').textContent =
        `${meta.source === 'sample' ? 'SAMPLE data' : 'Sheet'} · ${ctx.features.length} features · ${ctx.trailers.length} trailers · loaded ${new Date(meta.fetchedAt).toLocaleTimeString()}`
      // drop selections that vanished from the sheet
      state.films.forEach((f) => {
        if (f.featureKey && !ctx.features.some((x) => x.key === f.featureKey)) f.featureKey = ''
      })
      render()
    } catch (e) {
      document.getElementById('app').innerHTML = `<div class="alert alert-danger">Could not load the sheet data (${esc(e.message)}). Try Refresh, and check the server log if it keeps happening.</div>`
    } finally {
      btn.disabled = false
    }
  }

  /* --------------------------------------------------------------- render */
  function render() {
    if (!ctx) return
    save()
    const input = {
      slots: state.slots,
      films: state.films.map((f) => ({ id: f.id, featureKey: f.featureKey, start: f.start, end: f.end, slots: f.slots, ratingOverride: f.ratingOverride, trailerCount: f.trailerCount, exclude: f.exclude })),
    }
    let plan
    try {
      plan = PB.buildPlan(ctx, input)
    } catch (e) {
      console.error(e)
      document.getElementById('app').innerHTML = `<div class="alert alert-danger">Something went wrong building the plan: ${esc(e.message)}</div>`
      return
    }
    lastPlan = plan
    document.getElementById('banner').innerHTML =
      meta.source === 'sample'
        ? `<div class="alert alert-warning py-2"><b>SAMPLE DATA.</b> Names and dates come from your real sheet, but every run length is a placeholder, so the times below are for testing only (and saving is switched off). Add the <span class="mono">Playlist Data</span> tab (see the prompt file), then set <span class="mono">dataSource: 'sheet'</span> in server.js.</div>`
        : ''
    const html = [
      filmsCard(),
      flagsHtml(plan),
      saveBar(plan),
      plan.schedule.length || plan.playlists.length ? theaterCard(plan) + playlistsCard(plan) + scheduleCard(plan) : emptyState(),
      dataNotes(),
    ].join('')
    document.getElementById('app').innerHTML = html
    restoreOpen()
    document.getElementById('footnote').innerHTML =
      `Feature starts <b>${CFG.leadMin}:00</b> after the posted showtime. Playlist start = showtime + ${CFG.leadMin}:00 − preshow length. ` +
      `Each Projector Flat / Scope / F-200 cue pauses the playlist ${CFG.pauseSec}s and is counted. Turnaround target ≥ ${CFG.gapIdealMin} min, minimum ${CFG.gapHardMin} min. ` +
      `SV weeks start Monday and restart at the first Monday of the calendar year. A film can run at most ${CFG.maxRunDays} days.`
  }

  function saveBar(plan) {
    if (!plan.playlists.length) return ''
    const can = meta.canSave
    return `<div class="d-flex align-items-center mb-3">
      <input class="form-control form-control-sm mr-2" style="max-width:320px" id="noteBox" placeholder="Optional note for the history" value="${esc(noteText)}">
      <button class="btn btn-sm btn-success" data-act="save" ${can ? '' : 'disabled title="Saving is off while using sample data"'}>&#128190; Save this plan</button>
      <span class="ml-3 small" id="saveMsg">${saveMsg}</span></div>`
  }

  function planLabel(plan) {
    const titles = plan.films.map((f) => f.title).join(' + ')
    const a = plan.films.map((f) => f.start).sort()[0]
    const b = plan.films.map((f) => f.end).sort().slice(-1)[0]
    const svw = plan.playlists.length ? 'SVW' + String(plan.playlists[0].svw).padStart(2, '0') : ''
    return `${svw} · ${titles} · ${U.fmtShortDate(a)}–${U.fmtShortDate(b)}`
  }

  async function savePlan() {
    if (!lastPlan || !lastPlan.playlists.length) return
    if (lastPlan.flags.some((f) => f.level === 'error') && !confirm('This plan has errors. Save it anyway?')) return
    const msg = document.getElementById('saveMsg')
    if (msg) msg.textContent = 'Saving…'
    try {
      const r = await fetch('api/plans', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: planLabel(lastPlan), note: noteText, input: state, plan: PB.summarize(lastPlan), fetchedAt: meta.fetchedAt }),
      })
      const j = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(j.error || 'HTTP ' + r.status)
      saveMsg = `<span class="status-ok">&#10003; Saved as #${j.id}</span>`
      noteText = ''
    } catch (e) {
      saveMsg = `<span class="status-bad">Not saved: ${esc(e.message)}</span>`
    }
    render()
  }

  function emptyState() {
    return `<div class="card"><div class="card-body text-center text-muted py-5">Pick a feature above and your playlists and schedule times show up here.</div></div>`
  }

  /* ---- 1. films */
  function filmsCard() {
    const opts = (sel) =>
      `<option value="">Pick a feature…</option>` +
      ctx.features
        .map((f) => `<option value="${esc(f.key)}" ${f.key === sel ? 'selected' : ''}>${esc(f.title)} (${esc(f.rating || 'NR')} · ${f.framing === 'F200' ? 'F-200' : f.framing.toLowerCase()})</option>`)
        .join('')
    const rows = state.films
      .map((f, i) => {
        const feat = ctx.features.find((x) => x.key === f.featureKey)
        const slotBtns = state.slots
          .map((s) => {
            const on = f.slots.includes(s.id)
            return `<button type="button" class="btn btn-sm slot-btn ${on ? 'btn-primary' : 'btn-outline-secondary'}" data-act="slot" data-film="${f.id}" data-slot="${s.id}">${U.fmt12(U.parseHM(s.time)).replace(' ', '')}<br><small>#${s.screen}</small></button>`
          })
          .join(' ')
        const ratingSel = ['', 'G', 'PG', 'PG-13', 'R'].map((r) => `<option value="${r}" ${f.ratingOverride === r ? 'selected' : ''}>${r || 'rating: auto'}</option>`).join('')
        const cntSel = [0, 3, 4, 5, 6].map((n) => `<option value="${n}" ${f.trailerCount === n ? 'selected' : ''}>${n ? n + ' trailers' : 'trailers: auto'}</option>`).join('')
        return `<div class="film-row bg-white rounded border p-2 mb-2">
          <div class="form-row align-items-center">
            <div class="col-lg-4 mb-2"><select class="custom-select" data-act="feature" data-film="${f.id}">${opts(f.featureKey)}</select>
              ${feat ? `<div class="small text-muted mt-1">${U.fmtHMS(feat.runtime || 0)} runtime · ${feat.framing === 'F200' ? 'F-200 flat' : feat.framing.toLowerCase()}${feat.openDate ? ' · opens ' + U.fmtShortDate(feat.openDate) : ''}</div>` : ''}</div>
            <div class="col-lg-3 mb-2">
              <div class="input-group input-group-sm">
                <input type="date" class="form-control" value="${f.start}" data-act="start" data-film="${f.id}">
                <div class="input-group-prepend input-group-append"><span class="input-group-text">to</span></div>
                <input type="date" class="form-control" value="${f.end}" ${f.start ? `min="${f.start}" max="${U.addDays(f.start, CFG.maxRunDays - 1)}"` : ''} data-act="end" data-film="${f.id}">
              </div>
              <div class="mt-1 small">end: <a href="#" data-act="endq" data-q="tue" data-film="${f.id}">Tue</a> · <a href="#" data-act="endq" data-q="thu" data-film="${f.id}">Thu</a> · <a href="#" data-act="endq" data-q="sun" data-film="${f.id}">Sun</a> · <a href="#" data-act="endq" data-q="7" data-film="${f.id}">7 days</a> <span class="text-muted">(max 7)</span></div>
            </div>
            <div class="col-lg-3 mb-2">${slotBtns}</div>
            <div class="col-lg-2 mb-2 d-flex">
              <div class="flex-grow-1">
                <select class="custom-select custom-select-sm mb-1" data-act="rating" data-film="${f.id}">${ratingSel}</select>
                <select class="custom-select custom-select-sm" data-act="count" data-film="${f.id}">${cntSel}</select>
              </div>
              ${state.films.length > 1 ? `<a href="#" class="ml-2 x-btn" data-act="remove" data-film="${f.id}" title="Remove film">&#10005;</a>` : ''}
            </div>
          </div>
          ${f.exclude.length ? `<div class="small text-muted">Skipping trailers: ${f.exclude.map((k) => esc((ctx.trailers.find((t) => t.key === k) || {}).title || k)).join(', ')} <a href="#" data-act="unexclude" data-film="${f.id}">(undo)</a></div>` : ''}
        </div>`
      })
      .join('')
    const times = state.slots
      .map((s) => `<span class="mr-3 nowrap">#${s.screen} <input type="time" value="${s.time}" data-act="slottime" data-slot="${s.id}" class="form-control form-control-sm d-inline-block" style="width:110px"></span>`)
      .join('')
    return `<div class="card mb-3"><div class="card-body">
      <div class="section-title mb-2">1. What's playing</div>
      ${rows}
      <div class="d-flex align-items-center">
        <button class="btn btn-sm btn-outline-primary" data-act="addfilm">+ Add film</button>
        <details class="ml-4 small"><summary class="text-muted">Showtimes (default 3:30 / 4:00 / 6:30 / 7:00)</summary><div class="mt-2">${times}</div></details>
        ${state.slotsTouched ? `<a href="#" class="ml-3 small" data-act="resetslots">reset showtime picks</a>` : ''}
      </div>
    </div></div>`
  }

  /* ---- flags */
  function flagsHtml(plan) {
    const f = plan.flags.filter((x) => x.level !== 'info')
    if (!f.length) return plan.playlists.length ? `<div class="alert alert-success py-2">All timing checks passed. Every feature starts ${CFG.leadMin}:00 after its showtime.</div>` : ''
    const errs = f.filter((x) => x.level === 'error')
    const warns = f.filter((x) => x.level === 'warn')
    return (
      (errs.length ? `<div class="alert alert-danger py-2"><b>Needs your attention</b><ul class="mb-0 pl-3">${errs.map((x) => `<li>${esc(x.text)}</li>`).join('')}</ul></div>` : '') +
      (warns.length ? `<div class="alert alert-warning py-2"><b>Heads up</b><ul class="mb-0 pl-3">${warns.map((x) => `<li>${esc(x.text)}</li>`).join('')}</ul></div>` : '')
    )
  }

  /* ---- 2. theatre plan */
  function theaterCard(plan) {
    const blocks = plan.theater
      .map((g) => {
        const rows = g.shows
          .slice()
          .sort((a, b) => a.screen - b.screen || a.showSec - b.showSec)
          .map((s) => {
            const gap = s.gapBefore == null ? '—' : `${Math.round(s.gapBefore / 60)} min`
            const cls = s.status === 'ok' ? 'status-ok' : s.status === 'tight' ? 'status-tight' : 'status-bad'
            return `<tr><td>${scr(s.screen)}${s.swapped ? ' <span class="badge badge-warning">swapped</span>' : ''}</td><td class="mono">${U.fmt12(s.showSec)}</td><td>${esc(s.film.title)}</td><td class="mono">${U.fmt12(s.start, true)}</td><td class="mono">${U.fmt12(s.end, true)}</td><td class="${cls}">${gap}</td></tr>`
          })
          .join('')
        return `<div class="mb-3"><div class="font-weight-bold mb-1">${esc(rangeLabel(g.from, g.to))}</div>
          <table class="table table-sm table-bordered bg-white mb-0"><thead class="thead-light"><tr><th>Screen</th><th>Showtime</th><th>Film</th><th>Preshow starts</th><th>Show ends</th><th>Turnaround before preshow</th></tr></thead><tbody>${rows}</tbody></table></div>`
      })
      .join('')
    return `<div class="card mb-3"><div class="card-body"><div class="section-title mb-2">2. Theatre plan <small class="text-muted font-weight-normal">(who plays where, swaps worked out for you)</small></div>${blocks}</div></div>`
  }

  /* ---- 3. playlists */
  function playlistsCard(plan) {
    const cards = plan.playlists
      .map((pl) => {
        const id = pl.id
        const builtOn = pl.screenList.map((n) => `<span class="badge badge-info mr-1">${scr(n)}</span>`).join('')
        const warns = pl.warnings.filter((w) => w.level !== 'info')
        const rows = pl.items
          .map((it, i) => {
            const cues = it.cues
              .map((c) => {
                const at = c.at ? `<span class="text-muted"> @ FFEC ${U.fmtTC(c.at)}</span>` : ''
                return `<div><span class="badge badge-dark tag-cue">&#9889; ${esc(c.label)}</span>${at}${c.pause ? ` <span class="text-muted small">(pauses ${c.pause}s)</span>` : ''}</div>`
              })
              .join('')
            const x = it.kind === 'trailer' ? ` <span class="x-btn" data-act="exclude" data-film="${pl.film.id}" data-key="${esc(it.trailerKey)}" title="Skip this trailer">&#10005;</span>` : ''
            const rowCls = it.kind === 'feature' ? 'table-primary' : it.kind === 'trailer' ? '' : 'text-muted'
            return `<tr class="${rowCls}"><td>${i + 1}</td><td class="mono nowrap">${U.fmtTC(it.tc)}</td>
              <td>${esc(it.label)}${x}${it.note ? `<div class="small text-muted">${esc(it.note)}</div>` : ''}</td>
              <td class="cpl mono">${it.cpl ? copyable(it.cpl) : '<span class="text-danger">?</span>'}</td>
              <td class="mono nowrap">${U.fmtHMS(it.runtime)}</td><td>${cues}</td></tr>`
          })
          .join('')
        return `<details class="card mb-2" data-id="${esc(id)}" ${warns.some((w) => w.level === 'error') ? 'open' : ''}>
          <summary class="card-header py-2 d-flex flex-wrap align-items-center">
            <span class="font-weight-bold mr-3">${copyable(pl.name)}</span>
            ${builtOn}
            <span class="text-muted small ml-2">${esc(rangeLabel(pl.start, pl.end))} · ${pl.rating} · ${pl.framing === 'F200' ? 'F-200' : pl.framing.toLowerCase()} · preshow ${U.fmtHMS(pl.preshowSec)} · ${pl.trailers.length} trailers · total ${U.fmtHMS(pl.totalTC)}</span>
            ${warns.length ? `<span class="badge badge-${warns.some((w) => w.level === 'error') ? 'danger' : 'warning'} ml-2">${warns.length} note${warns.length > 1 ? 's' : ''}</span>` : ''}
          </summary>
          <div class="card-body p-2">
            ${warns.map((w) => `<div class="small ${w.level === 'error' ? 'text-danger' : 'text-warning'}">&#9888; ${esc(w.text)}</div>`).join('')}
            <div class="small text-muted mb-1">Build it on: ${pl.screenList.map((n) => `<a href="http://${CFG.screens[n].ip}" target="_blank" rel="noopener">${scr(n)} (${CFG.screens[n].ip})</a>`).join(' and ')}. Click a name to copy it.</div>
            <div class="table-responsive"><table class="table table-sm table-bordered bg-white mb-0">
              <thead class="thead-light"><tr><th>#</th><th>Starts at</th><th>Clip</th><th>CPL / package name</th><th>Length</th><th>Cues to add</th></tr></thead>
              <tbody>${rows}</tbody></table></div>
          </div></details>`
      })
      .join('')
    return `<div class="card mb-3"><div class="card-body"><div class="section-title mb-2">3. Build these playlists <small class="text-muted font-weight-normal">(in Playback &gt; Edit, top to bottom)</small></div>${cards}</div></div>`
  }

  /* ---- 4. schedule */
  function scheduleCard(plan) {
    const groups = [1, 2]
      .map((n) => {
        const es = plan.schedule.filter((e) => e.screen === n)
        if (!es.length) return ''
        const rows = es
          .map((e) => {
            const v = e.verify
            const off = v.checks[0].off
            const offTxt = Math.abs(off) > 0.05 ? ` <span class="text-muted small">(${off > 0 ? '+' : ''}${off.toFixed(1)}s)</span>` : ''
            return `<tr>
              <td>${copyable(e.pl.name)}</td>
              <td class="nowrap">${copyable(mdy(e.startDate))} <span class="text-muted small">${DOW[U.dow(e.startDate)]}</span></td>
              <td class="big-time mono nowrap">${copyable(e.startClock)}</td>
              <td class="text-center">${e.extraDays ? copyable(String(e.extraDays)) : '<span class="text-muted">—</span>'}${e.extraDays ? `<div class="small text-muted">thru ${DOW[U.dow(e.endDate)]} ${U.fmtShortDate(e.endDate)}</div>` : ''}</td>
              <td class="mono nowrap">${U.fmt12(e.showtimeSec)}</td>
              <td class="mono nowrap">${U.fmtClock(e.featureStartSec)}${offTxt}</td>
              <td class="mono nowrap">${U.fmtClock(e.featureEndSec)}</td>
              <td class="mono nowrap">${U.fmtClock(e.endSec)}</td>
              <td class="${v.ok ? 'status-ok' : 'status-bad'}">${v.ok ? '&#10003; OK' : '&#10007; ' + esc(v.msg)}</td></tr>`
          })
          .join('')
        return `<div class="mb-3"><div class="font-weight-bold mb-1">${scr(n)} <a class="small font-weight-normal" href="http://${CFG.screens[n].ip}" target="_blank" rel="noopener">open GDC page</a></div>
          <div class="table-responsive"><table class="table table-sm table-bordered bg-white mb-0">
          <thead class="thead-light"><tr><th>Show (playlist)</th><th>Start date</th><th>Start time (24h)</th><th>Repeat on next</th><th>Posted showtime</th><th>Feature starts</th><th>Feature ends</th><th>Show ends</th><th>Check</th></tr></thead>
          <tbody>${rows}</tbody></table></div></div>`
      })
      .join('')
    return `<div class="card mb-3"><div class="card-body"><div class="section-title mb-1">4. Schedule it <small class="text-muted font-weight-normal">(Playback &gt; Schedule &gt; New. Repeat More, "Everyday")</small></div>
      <div class="small text-muted mb-2">Set the seconds exactly as shown. Then check the week view once everything's in.</div>${groups}</div></div>`
  }

  function dataNotes() {
    if (!ctx.warnings.length) return ''
    return `<details class="small text-muted mb-3"><summary>${ctx.warnings.length} note(s) about the sheet data</summary><ul class="mb-0">${ctx.warnings.slice(0, 80).map((w) => `<li>${esc(w)}</li>`).join('')}</ul></details>`
  }

  /* -------------------------------------------------------------- events */
  function restoreOpen() {
    document.querySelectorAll('details[data-id]').forEach((d) => {
      if (openPlaylists.has(d.dataset.id)) d.open = true
      d.addEventListener('toggle', () => (d.open ? openPlaylists.add(d.dataset.id) : openPlaylists.delete(d.dataset.id)))
    })
  }
  const filmOf = (id) => state.films.find((f) => f.id === id)

  document.addEventListener('click', (ev) => {
    const copyEl = ev.target.closest('[data-copy]')
    if (copyEl) {
      ev.preventDefault()
      ev.stopPropagation()
      copyText(copyEl.dataset.copy)
      copyEl.classList.add('copied')
      setTimeout(() => copyEl.classList.remove('copied'), 700)
      return
    }
    const el = ev.target.closest('[data-act]')
    if (!el) return
    const act = el.dataset.act
    if (['feature', 'start', 'end', 'rating', 'count', 'slottime'].includes(act)) return // handled on change
    ev.preventDefault()
    if (act === 'save') return savePlan()
    saveMsg = ''
    const f = filmOf(el.dataset.film)
    if (act === 'addfilm') {
      state.films.push(newFilm())
      applyDefaultSlots()
    } else if (act === 'remove') {
      state.films = state.films.filter((x) => x.id !== el.dataset.film)
      applyDefaultSlots()
    } else if (act === 'slot' && f) {
      state.slotsTouched = true
      const s = el.dataset.slot
      f.slots = f.slots.includes(s) ? f.slots.filter((x) => x !== s) : f.slots.concat(s)
    } else if (act === 'resetslots') {
      state.slotsTouched = false
      applyDefaultSlots()
    } else if (act === 'endq' && f && f.start) {
      const q = el.dataset.q
      if (q === '7') f.end = U.addDays(f.start, CFG.maxRunDays - 1)
      else {
        const want = { tue: 2, thu: 4, sun: 0 }[q]
        for (let i = 1; i < CFG.maxRunDays; i++) {
          const d = U.addDays(f.start, i)
          if (U.dow(d) === want) {
            f.end = d
            break
          }
        }
      }
      f.datesTouched = true
    } else if (act === 'exclude' && f) {
      f.exclude = f.exclude.concat(el.dataset.key)
    } else if (act === 'unexclude' && f) {
      f.exclude = []
    }
    render()
  })

  document.addEventListener('change', (ev) => {
    const el = ev.target.closest('[data-act]')
    if (!el) return
    const act = el.dataset.act
    const f = filmOf(el.dataset.film)
    saveMsg = ''
    if (act === 'feature' && f) {
      f.featureKey = el.value
      f.exclude = []
      f.ratingOverride = ''
      const feat = ctx.features.find((x) => x.key === el.value)
      if (feat && !f.datesTouched) {
        const d = PB.defaultDates(feat, todayISO())
        f.start = d.start
        f.end = d.end
      }
    } else if (act === 'start' && f) {
      f.start = el.value
      f.datesTouched = true
      if (f.end && f.end < f.start) f.end = f.start
    } else if (act === 'end' && f) {
      f.end = el.value
      f.datesTouched = true
    } else if (act === 'rating' && f) f.ratingOverride = el.value
    else if (act === 'count' && f) f.trailerCount = parseInt(el.value, 10) || 0
    else if (act === 'slottime') {
      const s = state.slots.find((x) => x.id === el.dataset.slot)
      if (s && el.value) s.time = el.value
    } else return
    render()
  })

  document.addEventListener('input', (ev) => {
    if (ev.target.id === 'noteBox') noteText = ev.target.value
  })
  document.getElementById('refreshBtn').addEventListener('click', () => load(true))

  function copyText(t) {
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(t).catch(() => fallbackCopy(t))
    else fallbackCopy(t)
  }
  function fallbackCopy(t) {
    const ta = document.createElement('textarea')
    ta.value = t
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
    } catch (e) {}
    document.body.removeChild(ta)
  }

  load(false)
})()
