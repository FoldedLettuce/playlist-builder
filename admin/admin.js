(function () {
  'use strict'
  const U = PB.util
  const BASE = location.pathname.replace(/\/+$/, '')
  const root = document.getElementById('root')
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
  const dl = (iso) => `${DOW[U.dow(iso)]} ${U.fmtShortDate(iso)}`
  const range = (a, b) => (a === b ? dl(a) : `${dl(a)} – ${dl(b)}`)
  const mdy = (iso) => iso.slice(5, 7) + '/' + iso.slice(8, 10) + '/' + iso.slice(0, 4)
  let plans = []
  let tab = 'plans'

  async function api(path, opts) {
    const r = await fetch(BASE + '/api/' + path, Object.assign({ credentials: 'same-origin' }, opts))
    if (r.status === 401 && path !== 'login') {
      showLogin()
      throw new Error('login')
    }
    const j = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error(j.error || 'HTTP ' + r.status)
    return j
  }

  function showLogin(msg) {
    root.innerHTML = `<div class="card mx-auto mt-5" style="max-width:360px"><div class="card-body">
      <h5 class="mb-3">Admin</h5>
      <form id="lf"><input type="password" class="form-control mb-2" id="pw" placeholder="Password" autofocus autocomplete="current-password">
      <button class="btn btn-primary btn-block">Log in</button></form>
      <div class="text-danger small mt-2">${esc(msg || '')}</div></div></div>`
    document.getElementById('lf').addEventListener('submit', async (e) => {
      e.preventDefault()
      try {
        await api('login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: document.getElementById('pw').value }) })
        start()
      } catch (err) {
        showLogin(err.message)
      }
    })
  }

  async function start() {
    try {
      plans = await api('plans')
      shell()
    } catch (e) {
      if (e.message !== 'login') root.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`
    }
  }

  function shell() {
    root.innerHTML = `<div class="d-flex align-items-center mb-3">
      <h4 class="mb-0 mr-4">Playlist Builder admin</h4>
      <ul class="nav nav-pills">
        <li class="nav-item"><a class="nav-link ${tab === 'plans' ? 'active' : ''}" href="#" data-tab="plans">Saved plans (${plans.length})</a></li>
        <li class="nav-item"><a class="nav-link ${tab === 'roadmap' ? 'active' : ''}" href="#" data-tab="roadmap">Roadmap</a></li>
      </ul>
      <span class="ml-auto"><a class="btn btn-sm btn-outline-secondary mr-2" href="${BASE}/api/export">Export everything (JSON)</a>
      <button class="btn btn-sm btn-outline-secondary" id="logout">Log out</button></span></div><div id="view"></div>`
    root.querySelectorAll('[data-tab]').forEach((a) =>
      a.addEventListener('click', (e) => {
        e.preventDefault()
        tab = a.dataset.tab
        shell()
      })
    )
    document.getElementById('logout').addEventListener('click', async () => {
      await api('logout', { method: 'POST' })
      showLogin()
    })
    if (tab === 'roadmap') return roadmap()
    list()
  }

  function list(filter) {
    const f = (filter || '').toLowerCase()
    const rows = plans
      .filter((p) => !f || (p.label + ' ' + p.playlists + ' ' + p.note).toLowerCase().includes(f))
      .map(
        (p) => `<tr class="plan-row" data-id="${p.id}"><td>#${p.id}</td><td class="nowrap">${esc(new Date(p.saved_at).toLocaleString())}</td><td>${esc(p.label)}${p.note ? `<div class="small text-muted">${esc(p.note)}</div>` : ''}</td>
        <td class="small">${esc((p.playlists || '').split(' | ').join(', '))}</td>
        <td>${p.error_count ? `<span class="badge badge-danger">${p.error_count} error${p.error_count > 1 ? 's' : ''}</span> ` : ''}${p.warn_count ? `<span class="badge badge-warning">${p.warn_count} warn</span>` : ''}${!p.error_count && !p.warn_count ? '<span class="status-ok">&#10003;</span>' : ''}</td></tr>`
      )
      .join('')
    document.getElementById('view').innerHTML = `<input class="form-control mb-2" id="flt" placeholder="Filter by film, playlist name, SVW..." value="${esc(filter || '')}">
      <table class="table table-sm table-bordered bg-white"><thead class="thead-light"><tr><th>ID</th><th>Saved</th><th>Plan</th><th>Playlists</th><th>Flags</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5" class="text-muted text-center py-4">Nothing saved yet. Use the Save button on the builder page.</td></tr>'}</tbody></table>`
    const inp = document.getElementById('flt')
    inp.addEventListener('input', () => {
      const pos = inp.selectionStart
      list(inp.value)
      const n = document.getElementById('flt')
      n.focus()
      n.setSelectionRange(pos, pos)
    })
    document.querySelectorAll('tr.plan-row').forEach((tr) => tr.addEventListener('click', () => detail(tr.dataset.id)))
  }

  async function detail(id) {
    const d = await api('plans/' + id)
    const p = d.plan
    const flags = (p.flags || []).filter((f) => f.level !== 'info')
    const theater = p.theater
      .map(
        (g) => `<div class="mb-2"><b>${esc(range(g.from, g.to))}</b><table class="table table-sm table-bordered bg-white mb-0">
        <thead class="thead-light"><tr><th>Screen</th><th>Showtime</th><th>Film</th><th>Preshow starts</th><th>Show ends</th><th>Turnaround</th></tr></thead><tbody>
        ${g.shows
          .slice()
          .sort((a, b) => a.screen - b.screen || a.showtime.localeCompare(b.showtime))
          .map((s) => `<tr><td>Cinema ${s.screen}${s.swapped ? ' <span class="badge badge-warning">swapped</span>' : ''}</td><td class="mono">${U.fmt12(U.parseHM(s.showtime))}</td><td>${esc(s.film)}</td><td class="mono">${s.preshowStart}</td><td class="mono">${s.showEnd}</td><td class="${s.status === 'ok' ? 'status-ok' : s.status === 'tight' ? 'status-tight' : 'status-bad'}">${s.gapMin == null ? '—' : s.gapMin + ' min'}</td></tr>`)
          .join('')}</tbody></table></div>`
      )
      .join('')
    const sched = [1, 2]
      .map((n) => {
        const es = p.schedule.filter((e) => e.screen === n)
        if (!es.length) return ''
        return `<div class="mb-3"><b>Cinema ${n}</b><table class="table table-sm table-bordered bg-white mb-0"><thead class="thead-light"><tr><th>Show (playlist)</th><th>Start date</th><th>Start time</th><th>Repeat next</th><th>Showtime</th><th>Feature starts</th><th>Feature ends</th><th>Show ends</th><th>Check</th></tr></thead><tbody>
        ${es.map((e) => `<tr><td>${esc(e.playlist)}</td><td>${mdy(e.startDate)}</td><td class="mono"><b>${e.startClock}</b></td><td>${e.extraDays || '—'}</td><td class="mono">${U.fmt12(U.parseHM(e.showtime))}</td><td class="mono">${e.featureStart}</td><td class="mono">${e.featureEnd}</td><td class="mono">${e.showEnd}</td><td class="${e.ok ? 'status-ok' : 'status-bad'}">${e.ok ? '&#10003;' : esc(e.msg)}</td></tr>`).join('')}
        </tbody></table></div>`
      })
      .join('')
    const pls = p.playlists
      .map(
        (pl) => `<details class="card mb-2"><summary class="card-header py-2"><b>${esc(pl.name)}</b> <span class="text-muted small">${esc(range(pl.start, pl.end))} · ${pl.rating} · ${pl.framing} · preshow ${U.fmtHMS(pl.preshowSec)} · ${pl.items.filter((i) => i.kind === 'trailer').length} trailers</span></summary>
        <div class="card-body p-2"><table class="table table-sm table-bordered bg-white mb-0"><thead class="thead-light"><tr><th>#</th><th>Starts at</th><th>Clip</th><th>CPL</th><th>Length</th><th>Cues</th></tr></thead><tbody>
        ${pl.items.map((it) => `<tr class="${it.kind === 'feature' ? 'table-primary' : ''}"><td>${it.n}</td><td class="mono">${U.fmtTC(it.tc)}</td><td>${esc(it.label)}${it.note ? `<div class="small text-muted">${esc(it.note)}</div>` : ''}</td><td class="cpl mono">${esc(it.cpl)}</td><td class="mono">${U.fmtHMS(it.runtimeSec)}</td><td>${it.cues.map((c) => `<span class="badge badge-dark">&#9889; ${esc(c.label)}</span>${c.at ? ' @ ' + U.fmtTC(c.at) : ''}`).join(' ')}</td></tr>`).join('')}
        </tbody></table></div></details>`
      )
      .join('')
    document.getElementById('view').innerHTML = `<p><a href="#" id="back">&larr; All saved plans</a></p>
      <div class="d-flex align-items-start mb-2"><div><h5 class="mb-0">#${d.id} · ${esc(d.label)}</h5>
      <div class="small text-muted">Saved ${esc(new Date(d.savedAt).toLocaleString())} · data: ${esc(d.source)}${d.fetchedAt ? ' (sheet read ' + esc(new Date(d.fetchedAt).toLocaleString()) + ')' : ''}${d.note ? ' · note: ' + esc(d.note) : ''}</div></div>
      <button class="btn btn-sm btn-primary ml-auto" id="reopen">Open in builder</button></div>
      ${flags.length ? `<div class="alert alert-warning py-2"><ul class="mb-0 pl-3">${flags.map((f) => `<li>${esc(f.level === 'error' ? 'ERROR: ' : '')}${esc(f.text)}</li>`).join('')}</ul></div>` : ''}
      <h6 class="mt-3">Theatre plan</h6>${theater}<h6 class="mt-3">Schedule</h6>${sched}<h6 class="mt-3">Playlists</h6>${pls}`
    document.getElementById('back').addEventListener('click', (e) => {
      e.preventDefault()
      list()
    })
    document.getElementById('reopen').addEventListener('click', () => {
      try {
        localStorage.setItem('pb-state-v1', JSON.stringify(d.input))
      } catch (e) {}
      location.href = '/'
    })
  }

  async function roadmap() {
    const t = await (await fetch(BASE + '/api/roadmap', { credentials: 'same-origin' })).text()
    let html = ''
    let inList = false
    const inline = (s) => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>')
    for (const line of t.split('\n')) {
      const h = line.match(/^(#{1,3})\s+(.*)/)
      const li = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)/)
      if (inList && !li) {
        html += '</ul>'
        inList = false
      }
      if (h) html += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`
      else if (li) {
        if (!inList) {
          html += '<ul>'
          inList = true
        }
        html += `<li>${inline(li[1])}</li>`
      } else if (line.trim()) html += `<p>${inline(line)}</p>`
    }
    if (inList) html += '</ul>'
    document.getElementById('view').innerHTML = `<div class="card"><div class="card-body roadmap">${html}</div></div>`
  }

  api('session').then(start).catch(() => showLogin())
})()
