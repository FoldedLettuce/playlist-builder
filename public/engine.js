/*
 * Playlist & schedule engine.
 * Pure logic, no DOM and no network. Runs in the browser (window.PB) and in Node (require).
 *
 * Big rules baked in (all from the theatre manual + Liam's notes):
 *  - Feature (the film itself) starts EXACTLY leadMin (10) minutes after the posted showtime.
 *  - Playlist start time = showtime + 10:00 - (preshow length, including 10s reframe pauses).
 *  - Trailers: same rating or below, never the film itself, soonest-releasing N are picked
 *    (release >= first day of the playlist) and then played furthest-out first.
 *  - Playlists are named "SVW## Title" and split at SV week boundaries (Monday) and whenever
 *    an ad changes mid week (suffix .5, .6, ...).
 *  - Theatre swaps if turnaround < 15 min, 5 min is the hard minimum, otherwise flag it.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory()
  else root.PB = factory()
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict'

  const RATINGS = ['G', 'PG', 'PG-13', 'R', 'NC-17']

  const DEFAULT_SLOTS = [
    { id: 'S1', time: '15:30', screen: 1, block: 0 },
    { id: 'S2', time: '16:00', screen: 2, block: 0 },
    { id: 'S3', time: '18:30', screen: 1, block: 1 },
    { id: 'S4', time: '19:00', screen: 2, block: 1 },
  ]

  const DEFAULT_CFG = {
    leadMin: 10, // feature starts this long after the posted showtime
    maxRunDays: 7, // never schedule a film for more than this many days (bookings aren't known past that)
    gapIdealMin: 15, // ideal minimum between a show ending and the next preshow starting
    gapHardMin: 5, // absolute minimum turnaround
    preshowMinMin: 25,
    preshowMaxMin: 30,
    trailerRange: [2, 7],
    pauseSec: 10, // every Projector Flat / Scope / F-200 cue pauses the playlist this long
    blackStartSec: 60,
    blackEndSec: 5,
    defaultPreshowSec: 31, // only used if the sheet has no PRESHOW row
    defaultRatingCardSec: 6, // only used if the sheet has no RATING_CARD row
    toleranceSec: 0.5,
    screens: {
      1: { name: 'Cinema 1', ip: '10.0.0.187' },
      2: { name: 'Cinema 2', ip: '10.0.0.188' },
    },
    names: { blackStart: 'Black MOS 1min', blackEnd: 'Black MOS 5sec' },
  }

  /* ---------------------------------------------------------------- utils */
  const pad = (n) => String(n).padStart(2, '0')

  function normRating(r) {
    if (r == null) return ''
    const s = String(r).trim().toUpperCase().replace(/\s+/g, '')
    if (s === 'PG13') return 'PG-13'
    if (s === 'NC17') return 'NC-17'
    return s
  }
  const rank = (r) => RATINGS.indexOf(normRating(r))

  function parseDate(v) {
    if (v == null || v === '') return null
    if (v instanceof Date) return v.toISOString().slice(0, 10)
    const s = String(v).trim()
    let m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/)
    if (m) return `${m[1]}-${pad(m[2])}-${pad(m[3])}`
    m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})/)
    if (m) {
      let y = +m[3]
      if (y < 100) y += 2000
      return `${y}-${pad(m[1])}-${pad(m[2])}`
    }
    return null
  }
  const dayNum = (iso) => {
    const [y, m, d] = iso.split('-').map(Number)
    return Math.round(Date.UTC(y, m - 1, d) / 86400000)
  }
  const fromDayNum = (n) => new Date(n * 86400000).toISOString().slice(0, 10)
  const addDays = (iso, n) => fromDayNum(dayNum(iso) + n)
  const dow = (iso) => new Date(dayNum(iso) * 86400000).getUTCDay() // 0 = Sun
  const mondayOf = (iso) => addDays(iso, -((dow(iso) + 6) % 7))
  const daysBetween = (a, b) => dayNum(b) - dayNum(a)
  function dateRange(a, b) {
    const out = []
    for (let d = a; d <= b; d = addDays(d, 1)) out.push(d)
    return out
  }

  // SV week: weeks start Monday. Week 1 = first Monday of the calendar year (of that Monday).
  // Check: Mon 2026-11-23 => SVW47.
  function svWeek(iso) {
    const mon = mondayOf(iso)
    const y = +mon.slice(0, 4)
    const jan1 = `${y}-01-01`
    const firstMon = addDays(jan1, (8 - dow(jan1)) % 7)
    return 1 + daysBetween(firstMon, mon) / 7
  }

  function parseDur(v) {
    if (v == null || v === '') return null
    if (typeof v === 'number') return isFinite(v) ? v : null
    const s = String(v).trim()
    if (/^-?\d+(\.\d+)?$/.test(s)) return parseFloat(s)
    const m = s.match(/^(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$/)
    if (m) return +(m[1] || 0) * 3600 + +m[2] * 60 + parseFloat(m[3])
    return null
  }
  const parseHM = (s) => {
    const m = String(s).match(/^(\d{1,2}):(\d{2})/)
    return m ? +m[1] * 3600 + +m[2] * 60 : null
  }
  const hms = (sec) => {
    const s = Math.round(sec)
    return [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60]
  }
  const fmtHMS = (sec) => {
    const [h, m, s] = hms(sec)
    return `${h}:${pad(m)}:${pad(s)}`
  }
  const fmtTC = (sec) => {
    const [h, m, s] = hms(sec)
    return `${pad(h)}:${pad(m)}:${pad(s)}`
  }
  const fmtClock = (sec) => fmtTC(((Math.round(sec) % 86400) + 86400) % 86400)
  function fmt12(sec, withSec) {
    const [h, m, s] = hms(((Math.round(sec) % 86400) + 86400) % 86400)
    const h12 = h % 12 === 0 ? 12 : h % 12
    return `${h12}:${pad(m)}${withSec ? ':' + pad(s) : ''} ${h < 12 ? 'AM' : 'PM'}`
  }
  const fmtMin = (sec) => {
    const m = Math.round(sec / 60)
    return `${m} min`
  }
  const fmtShortDate = (iso) => {
    const [, m, d] = iso.split('-')
    return `${+m}/${+d}`
  }

  const normTitle = (t) => String(t || '').toLowerCase().replace(/[^a-z0-9]+/g, '')
  const sameTitle = (a, b) => {
    const x = normTitle(a)
    const y = normTitle(b)
    if (!x || !y) return false
    return x === y || (Math.min(x.length, y.length) >= 6 && (x.startsWith(y) || y.startsWith(x)))
  }

  const normFormat = (f) => {
    const s = String(f || '').trim().toUpperCase().replace(/[\s_]+/g, '')
    if (s === 'S' || s === 'SCOPE') return 'SCOPE'
    if (s === 'F' || s === 'FLAT') return 'FLAT'
    if (s === 'F-200' || s === 'F200') return 'F200'
    return ''
  }

  /* ----------------------------------------------------------- data load */
  /**
   * rawRows: array of objects keyed by the "Playlist Data" tab header names.
   * Returns ctx used by buildPlan.
   */
  function loadData(rawRows) {
    const warnings = []
    const rows = rawRows.map((r, i) => {
      const o = { _row: i + 2 }
      for (const k in r) o[String(k).trim().toLowerCase().replace(/[\s-]+/g, '_')] = r[k]
      return o
    })
    const features = []
    const trailerMap = new Map()
    const ads = []
    const ROLES = ['SV_EARLY', 'SV_LATE', 'COKE', 'CHEVY', 'MARQUEE', 'PRESHOW', 'RATING_CARD']

    rows.forEach((r, order) => {
      const role = String(r.role || '').trim().toUpperCase().replace(/[\s-]+/g, '_')
      if (!role) return
      const cpl = String(r.cpl_name || '').trim()
      const runtime = parseDur(r.runtime_sec)
      const format = normFormat(r.format)
      if (role !== 'FEATURE' && role !== 'TRAILER' && !ROLES.includes(role)) {
        warnings.push(`Row ${r._row}: unknown role "${r.role}" ignored`)
        return
      }
      if (!cpl) {
        warnings.push(`Row ${r._row}: ${role} has no cpl_name, skipped`)
        return
      }
      if (runtime == null) warnings.push(`Row ${r._row}: ${role} "${cpl}" has no runtime_sec`)

      if (role === 'FEATURE') {
        features.push({
          key: cpl,
          title: String(r.title || cpl).trim(),
          rating: normRating(r.rating),
          framing: format || 'SCOPE',
          cpl,
          runtime,
          ffec: parseDur(r.ffec_sec),
          openDate: parseDate(r.release_date),
          notes: r.notes || '',
        })
      } else if (role === 'TRAILER') {
        const key = normTitle(r.title)
        const ver = String(r.version || '').trim()
        const vnum = parseInt((ver.match(/\d+/) || ['0'])[0], 10)
        const variant = format === 'SCOPE' ? 'scope' : format === 'FLAT' ? 'flat' : null
        if (!variant) {
          warnings.push(`Row ${r._row}: TRAILER "${cpl}" needs format SCOPE or FLAT`)
          return
        }
        const mapKey = `${key}#${vnum}`
        let t = trailerMap.get(mapKey)
        if (!t) {
          t = {
            key,
            title: String(r.title).trim(),
            version: ver,
            vnum,
            rating: normRating(r.rating),
            rank: rank(r.rating),
            releaseDate: parseDate(r.release_date),
            order,
            scope: null,
            flat: null,
          }
          trailerMap.set(mapKey, t)
        }
        t[variant] = { cpl, runtime }
      } else {
        const rl = String(r.rating || '').trim()
        const ratings =
          !rl || rl.toUpperCase() === 'ALL'
            ? null
            : rl.split(/[,\s]+/).filter(Boolean).map(normRating)
        ads.push({
          role,
          format: format === 'F200' ? 'FLAT' : format || 'ANY',
          ratings,
          start: parseDate(r.start_date),
          end: parseDate(r.end_date),
          cpl,
          runtime,
          onlyFor: r.only_for_title ? String(r.only_for_title) : null,
          notes: r.notes || '',
          order,
        })
      }
    })

    // one trailer per film: keep the highest version number
    const best = new Map()
    for (const t of trailerMap.values()) {
      const cur = best.get(t.key)
      if (!cur || t.vnum > cur.vnum || (t.vnum === cur.vnum && t.order > cur.order)) best.set(t.key, t)
    }
    const trailers = [...best.values()].sort((a, b) => a.order - b.order)
    features.sort((a, b) => (b.openDate || '').localeCompare(a.openDate || '') || a.title.localeCompare(b.title))
    return { features, trailers, ads, warnings }
  }

  /* ------------------------------------------------------------- ad lookup */
  function findAd(ctx, role, date, rating, wantFmt, title) {
    const c = ctx.ads.filter(
      (a) =>
        a.role === role &&
        (!a.start || a.start <= date) &&
        (!a.end || a.end >= date) &&
        (a.format === 'ANY' || a.format === wantFmt) &&
        (!a.ratings || a.ratings.includes(rating)) &&
        (!a.onlyFor || sameTitle(a.onlyFor, title))
    )
    if (!c.length) return { ad: null, ambiguous: [] }
    const key = (a) => [a.onlyFor ? 1 : 0, a.format === wantFmt ? 1 : 0, a.ratings ? 1 : 0, a.start || '']
    const cmp = (a, b) => {
      const ka = key(a)
      const kb = key(b)
      for (let i = 0; i < ka.length; i++) {
        if (ka[i] > kb[i]) return -1
        if (ka[i] < kb[i]) return 1
      }
      return b.order - a.order
    }
    c.sort(cmp)
    const ambiguous = c
      .slice(1)
      .filter((o) => JSON.stringify(key(o)) === JSON.stringify(key(c[0])) && o.cpl !== c[0].cpl)
    return { ad: c[0], ambiguous }
  }

  function adsOnDay(ctx, film, date) {
    const want = film.framing === 'SCOPE' ? 'SCOPE' : 'FLAT'
    const r = film.rating
    const t = film.title
    return {
      early: findAd(ctx, 'SV_EARLY', date, r, 'SCOPE', t),
      late: findAd(ctx, 'SV_LATE', date, r, 'SCOPE', t),
      coke: findAd(ctx, 'COKE', date, r, want, t),
      chevy: findAd(ctx, 'CHEVY', date, r, want, t),
      marquee: findAd(ctx, 'MARQUEE', date, r, want, t),
      preshow: findAd(ctx, 'PRESHOW', date, r, want, t),
      card: findAd(ctx, 'RATING_CARD', date, r, want, t),
    }
  }
  const adSig = (res) =>
    Object.keys(res)
      .map((k) => (res[k].ad ? res[k].ad.cpl : '-'))
      .join('|')

  /* ------------------------------------------------------------- trailers */
  function pickTrailers(ctx, film, segStart, n) {
    const fk = film.framing === 'SCOPE' ? 'scope' : 'flat'
    const cands = ctx.trailers.filter(
      (t) =>
        t.rank !== -1 &&
        t.rank <= film.rank &&
        t[fk] &&
        !sameTitle(t.title, film.title) &&
        !film.exclude.has(t.key)
    )
    const isUp = (t) => t.releaseDate && t.releaseDate >= segStart
    const soonest = (a, b) => a.releaseDate.localeCompare(b.releaseDate) || a.order - b.order
    const recent = (a, b) => (b.releaseDate || '').localeCompare(a.releaseDate || '') || b.order - a.order
    // Pools in priority order: same-rating upcoming, lower-rating upcoming,
    // then (only if we have to) already-released same-rating, already-released lower-rating.
    const pools = [
      { list: cands.filter((t) => t.rank === film.rank && isUp(t)).sort(soonest), backfill: false },
      { list: cands.filter((t) => t.rank < film.rank && isUp(t)).sort(soonest), backfill: false },
      { list: cands.filter((t) => t.rank === film.rank && !isUp(t)).sort(recent), backfill: true },
      { list: cands.filter((t) => t.rank < film.rank && !isUp(t)).sort(recent), backfill: true },
    ]
    const chosen = []
    for (const p of pools) for (const t of p.list) if (chosen.length < n) chosen.push({ t, backfill: p.backfill })
    // asc by release then reverse => furthest-out first, ties flip sheet order
    chosen.sort(
      (a, b) =>
        (a.t.releaseDate || '').localeCompare(b.t.releaseDate || '') || a.t.order - b.t.order
    )
    chosen.reverse()
    return {
      list: chosen.map(({ t, backfill }) => ({
        key: t.key,
        title: t.title,
        version: t.version,
        rating: t.rating,
        releaseDate: t.releaseDate,
        cpl: t[fk].cpl,
        runtime: t[fk].runtime,
        backfill,
        lower: t.rank < film.rank,
      })),
      available: cands.length,
    }
  }

  /* ----------------------------------------------------------- playlists */
  function assemble(ctx, cfg, film, seg, trailers) {
    const warnings = seg.warnings.map((w) => Object.assign({}, w))
    const items = []
    const add = (o) => {
      const it = Object.assign({ cues: [], note: '' }, o)
      if (it.runtime == null || isNaN(it.runtime)) {
        warnings.push({ level: 'error', text: `No runtime for "${it.cpl || it.label}", so times are NOT reliable` })
        it.runtime = 0
        it.missing = true
      }
      items.push(it)
      return it
    }
    const cue = (it, label, at) =>
      it.cues.push({ label, at: at || 0, pause: /^Projector /.test(label) ? cfg.pauseSec : 0 })
    const A = seg.ads
    const flat = film.framing !== 'SCOPE'

    const b1 = add({ kind: 'black', label: 'Black MOS 1min', cpl: cfg.names.blackStart, runtime: cfg.blackStartSec })
    cue(b1, 'Show Start - Scope')
    if (A.early.ad) add({ kind: 'sv', label: 'SV ad (early)', cpl: A.early.ad.cpl, runtime: A.early.ad.runtime })
    if (A.late.ad) add({ kind: 'sv', label: 'SV ad (late)', cpl: A.late.ad.cpl, runtime: A.late.ad.runtime })
    let coke = null
    if (A.coke.ad) {
      coke = add({ kind: 'coke', label: 'Coke ad', cpl: A.coke.ad.cpl, runtime: A.coke.ad.runtime })
      cue(coke, 'Coke')
      if (flat) cue(coke, 'Projector Flat')
    }
    let chevy = null
    if (A.chevy.ad) chevy = add({ kind: 'chevy', label: 'Chevy ad', cpl: A.chevy.ad.cpl, runtime: A.chevy.ad.runtime })
    const trItems = trailers.map((t, i) =>
      add({
        kind: 'trailer',
        label: `Trailer ${i + 1}: ${t.title}`,
        cpl: t.cpl,
        runtime: t.runtime,
        trailerKey: t.key,
        note: `${t.rating || 'NR'} · opens ${t.releaseDate ? fmtShortDate(t.releaseDate) : '?'}${t.backfill ? ' · already released' : ''}${t.lower ? ' · lower rating, not enough same-rating' : ''}`,
      })
    )
    const firstTrailerSlot = chevy || trItems[0]
    if (firstTrailerSlot) cue(firstTrailerSlot, 'Trailers')
    if (flat && !coke) {
      // no Coke ad to carry the reframe, put it on the first clip after the SV ads
      const t = chevy || trItems[0]
      if (t) cue(t, 'Projector Flat')
      else warnings.push({ level: 'error', text: 'Flat film but nothing to carry the Projector Flat cue' })
    }
    if (A.marquee.ad) add({ kind: 'marquee', label: 'Marquee ad (last trailer)', cpl: A.marquee.ad.cpl, runtime: A.marquee.ad.runtime })

    const pre = add({
      kind: 'preshow',
      label: 'DeSimone PreShow',
      cpl: A.preshow.ad ? A.preshow.ad.cpl : `(DeSimone PreShow ${flat ? 'flat' : 'scope'})`,
      runtime: A.preshow.ad ? A.preshow.ad.runtime : cfg.defaultPreshowSec,
    })
    cue(pre, 'Feature')
    if (!A.preshow.ad) warnings.push({ level: 'warn', text: `No PRESHOW row in the sheet, assumed ${cfg.defaultPreshowSec}s` })
    const card = add({
      kind: 'card',
      label: `Rating card ${film.rating}`,
      cpl: A.card.ad ? A.card.ad.cpl : `(${film.rating} rating card ${flat ? 'flat' : 'scope'})`,
      runtime: A.card.ad ? A.card.ad.runtime : cfg.defaultRatingCardSec,
    })
    if (!A.card.ad) warnings.push({ level: 'warn', text: `No ${film.rating} RATING_CARD row in the sheet, assumed ${cfg.defaultRatingCardSec}s` })
    const feat = add({ kind: 'feature', label: film.title, cpl: film.feature.cpl, runtime: film.feature.runtime })
    if (film.framing === 'F200') cue(feat, 'Projector F-200', 0)
    if (film.feature.ffec != null && film.feature.ffec < feat.runtime) cue(feat, 'Credits', film.feature.ffec)
    else warnings.push({ level: 'warn', text: 'No FFEC marker time for the feature, so the Credits cue cannot be placed' })
    const b2 = add({ kind: 'black', label: 'Black MOS 5sec', cpl: cfg.names.blackEnd, runtime: cfg.blackEndSec })
    cue(b2, 'Show End')

    // timeline: tc = playlist timecode (what GDC shows), wall = real elapsed time incl. reframe pauses
    let tc = 0
    let wall = 0
    for (const it of items) {
      const pause = it.cues.filter((c) => c.at === 0).reduce((s, c) => s + c.pause, 0)
      it.pause = pause
      wall += pause
      it.tc = tc
      it.wall = wall
      tc += it.runtime
      wall += it.runtime
    }
    const trailerSum = trItems.reduce((s, i) => s + i.runtime, 0)
    return {
      items,
      warnings,
      trailers,
      preshowSec: feat.wall,
      featureTC: feat.tc,
      featureRuntime: feat.runtime,
      totalTC: tc,
      totalWall: wall,
      trailerSum,
      reliable: !items.some((i) => i.missing),
    }
  }

  function buildSegmentPlaylist(ctx, cfg, film, seg) {
    const [lo, hi] = cfg.trailerRange
    const mid = ((cfg.preshowMinMin + cfg.preshowMaxMin) / 2) * 60
    let counts
    if (film.trailerCount) counts = [film.trailerCount]
    else {
      counts = []
      for (let n = lo; n <= hi; n++) counts.push(n)
    }
    let best = null
    for (const n of counts) {
      const { list, available } = pickTrailers(ctx, film, seg.start, n)
      if (!list.length && n > counts[0] && best) continue
      const built = assemble(ctx, cfg, film, seg, list)
      const pre = built.preshowSec
      const lo_ = cfg.preshowMinMin * 60
      const hi_ = cfg.preshowMaxMin * 60
      const outside = pre < lo_ ? lo_ - pre : pre > hi_ ? pre - hi_ : 0
      const score = [outside > 0 ? 1 : 0, Math.round(outside), Math.abs(list.length - 4.5), Math.abs(pre - mid)]
      if (list.length < n) score[2] += 100 // couldn't fill the count, prefer ones that could
      if (!best || cmpArr(score, best.score) < 0) best = { score, built, n: list.length, available }
    }
    const b = best.built
    const w = b.warnings
    if (!b.trailers.length) w.push({ level: 'error', text: `No eligible trailers (rating ${film.rating} or below)` })
    if (b.trailers.some((t) => t.backfill))
      w.push({ level: 'warn', text: 'Not enough upcoming trailers, so some already-released ones were used' })
    const preMin = b.preshowSec / 60
    if (preMin < cfg.preshowMinMin || preMin > cfg.preshowMaxMin)
      w.push({ level: 'warn', text: `Preshow is ${fmtHMS(b.preshowSec)}, outside your ${cfg.preshowMinMin}-${cfg.preshowMaxMin} min target` })
    return b
  }
  function cmpArr(a, b) {
    for (let i = 0; i < a.length; i++) {
      if (a[i] < b[i]) return -1
      if (a[i] > b[i]) return 1
    }
    return 0
  }

  /* ------------------------------------------------------------ segments */
  function segmentsFor(ctx, cfg, film) {
    const days = dateRange(film.start, film.end)
    const segs = []
    for (const d of days) {
      const ads = adsOnDay(ctx, film, d)
      const sig = svWeek(d) + '#' + adSig(ads)
      const last = segs[segs.length - 1]
      if (last && last.sig === sig) last.end = d
      else segs.push({ sig, start: d, end: d, svw: svWeek(d), ads })
    }
    for (const s of segs) {
      // which ad period of its SV week is this? (0 => "SVW47", 1 => "SVW47.5", 2 => ".6" ...)
      const mon = mondayOf(s.start)
      const periods = []
      let prev = null
      for (let i = 0; i < 7; i++) {
        const d = addDays(mon, i)
        const sig = adSig(adsOnDay(ctx, film, d))
        if (sig !== prev) periods.push(d)
        prev = sig
      }
      let idx = 0
      periods.forEach((p, i) => {
        if (p <= s.start) idx = i
      })
      s.periodIdx = idx
      s.suffix = idx === 0 ? '' : '.' + (4 + idx)
      s.warnings = []
      const A = s.ads
      const w = (level, text) => s.warnings.push({ level, text })
      if (!A.early.ad) w('error', `No SV early ad for ${film.rating} starting ${fmtShortDate(s.start)} (is it ingested and in the sheet?)`)
      if (!A.late.ad) w('error', `No SV late ad for ${film.rating} starting ${fmtShortDate(s.start)} (is it ingested and in the sheet?)`)
      if (!A.coke.ad) w('warn', `No Coke ad found for ${fmtShortDate(s.start)}`)
      if (!A.chevy.ad) w('info', 'No Chevy ad active, first trailer carries the Trailers cue')
      for (const k of Object.keys(A))
        if (A[k].ambiguous.length)
          w('warn', `More than one ${k} ad matches (${[A[k].ad.cpl, ...A[k].ambiguous.map((x) => x.cpl)].join(' / ')}), using the first. Check the sheet`)
    }
    return segs
  }

  /* ---------------------------------------------------------------- plan */
  function resolveFilm(ctx, f) {
    const feature = ctx.features.find((x) => x.key === f.featureKey)
    if (!feature) return null
    const rating = normRating(f.ratingOverride || feature.rating)
    return {
      id: f.id,
      feature,
      title: feature.title,
      rating,
      rank: rank(rating),
      framing: feature.framing,
      start: f.start,
      end: f.end,
      slots: f.slots || [],
      trailerCount: f.trailerCount || null,
      exclude: new Set(f.exclude || []),
    }
  }

  function buildPlan(ctx, input, cfgIn) {
    const cfg = Object.assign({}, DEFAULT_CFG, cfgIn || {})
    const slots = input.slots || DEFAULT_SLOTS
    const slotById = Object.fromEntries(slots.map((s) => [s.id, s]))
    const flags = []
    const flag = (level, text) => {
      if (!flags.some((f) => f.text === text)) flags.push({ level, text })
    }
    const films = []
    for (const f of input.films || []) {
      if (!f.featureKey) continue
      const film = resolveFilm(ctx, f)
      if (!film) {
        flag('error', `A selected feature is no longer in the sheet`)
        continue
      }
      if (!film.start || !film.end || film.end < film.start) {
        flag('error', `${film.title}: check the start and end dates`)
        continue
      }
      if (daysBetween(film.start, film.end) + 1 > cfg.maxRunDays) {
        flag('error', `${film.title}: that's ${daysBetween(film.start, film.end) + 1} days. Never schedule more than ${cfg.maxRunDays} (set the end date to ${fmtShortDate(addDays(film.start, cfg.maxRunDays - 1))} or earlier)`)
        continue
      }
      if (film.rank === -1) {
        flag('error', `${film.title}: rating is blank in the sheet, pick one so trailers and ads can be chosen`)
        continue
      }
      if (film.feature.runtime == null) {
        flag('error', `${film.title}: the sheet has no runtime for this feature`)
        continue
      }
      film.segments = segmentsFor(ctx, cfg, film)
      for (const seg of film.segments) {
        const pl = buildSegmentPlaylist(ctx, cfg, film, seg)
        pl.id = `${film.id}|${seg.start}`
        pl.film = film
        pl.title = film.title
        pl.svw = seg.svw
        pl.suffix = seg.suffix
        pl.name = `SVW${pad(seg.svw)}${seg.suffix} ${film.title}`
        pl.start = seg.start
        pl.end = seg.end
        pl.framing = film.framing
        pl.rating = film.rating
        pl.screens = new Set()
        seg.playlist = pl
        for (const w of pl.warnings) if (w.level !== 'info') flag(w.level, `${pl.name}: ${w.text}`)
      }
      films.push(film)
    }

    // ---- per-day shows + theatre assignment
    const allDays = new Set()
    films.forEach((f) => dateRange(f.start, f.end).forEach((d) => allDays.add(d)))
    const days = [...allDays].sort()
    const dayPlans = []
    for (const d of days) {
      const shows = []
      const used = {}
      for (const film of films) {
        if (d < film.start || d > film.end) continue
        const seg = film.segments.find((s) => s.start <= d && d <= s.end)
        for (const sid of film.slots) {
          const slot = slotById[sid]
          if (!slot) continue
          if (used[sid]) {
            flag('error', `${used[sid]} and ${film.title} are both on the ${fmt12(parseHM(slot.time))} showtime on ${fmtShortDate(d)}`)
            continue
          }
          used[sid] = film.title
          const pl = seg.playlist
          const showSec = parseHM(slot.time)
          const start = Math.round(showSec + cfg.leadMin * 60 - pl.preshowSec)
          shows.push({
            day: d,
            slot,
            film,
            pl,
            showSec,
            start,
            featureStart: start + pl.preshowSec,
            featureEnd: start + pl.preshowSec + pl.featureRuntime,
            end: start + pl.totalWall,
            screen: slot.screen,
          })
        }
      }
      assignScreens(shows, cfg, (lvl, txt) => flag(lvl, `${fmtShortDate(d)}: ${txt}`))
      dayPlans.push({ day: d, shows })
    }

    // ---- schedule entries (group consecutive days)
    const entries = []
    const open = new Map()
    for (const dp of dayPlans) {
      const seenKeys = new Set()
      for (const s of dp.shows) {
        const key = `${s.pl.id}|${s.screen}|${s.slot.id}`
        seenKeys.add(key)
        const cur = open.get(key)
        if (cur && cur.endDate === addDays(dp.day, -1)) {
          cur.endDate = dp.day
          cur.days++
        } else {
          const e = { key, pl: s.pl, screen: s.screen, slot: s.slot, film: s.film, first: s, startDate: dp.day, endDate: dp.day, days: 1 }
          entries.push(e)
          open.set(key, e)
        }
        s.pl.screens.add(s.screen)
      }
    }
    for (const e of entries) {
      const s = e.first
      e.extraDays = e.days - 1
      e.startSec = s.start
      e.startClock = fmtClock(s.start)
      e.showtime = s.slot.time
      e.showtimeSec = s.showSec
      e.featureStartSec = s.featureStart
      e.featureEndSec = s.featureEnd
      e.endSec = s.end
      e.preshowSec = s.pl.preshowSec
      e.verify = verifyEntry(e, cfg)
    }
    entries.sort(
      (a, b) =>
        a.screen - b.screen || a.startDate.localeCompare(b.startDate) || a.showtimeSec - b.showtimeSec
    )
    for (const e of entries)
      if (!e.verify.ok) flag('error', `${e.pl.name} at ${e.showtime}: timing check failed (${e.verify.msg})`)

    // ---- theatre plan (group days with identical layout)
    const theater = []
    for (const dp of dayPlans) {
      const sig = JSON.stringify(dp.shows.map((s) => [s.screen, s.slot.id, s.pl.id]).sort())
      const last = theater[theater.length - 1]
      if (last && last.sig === sig && last.to === addDays(dp.day, -1)) last.to = dp.day
      else theater.push({ sig, from: dp.day, to: dp.day, shows: dp.shows })
    }

    const playlists = []
    films.forEach((f) => f.segments.forEach((s) => playlists.push(s.playlist)))
    playlists.sort((a, b) => a.start.localeCompare(b.start) || a.title.localeCompare(b.title))
    for (const pl of playlists) pl.screenList = [...pl.screens].sort()
    flags.sort((a, b) => ({ error: 0, warn: 1, info: 2 })[a.level] - ({ error: 0, warn: 1, info: 2 })[b.level])
    return { cfg, films, playlists, schedule: entries, theater, flags }
  }

  /** Theatre assignment for one day. Mutates show.screen / gapBefore / status. */
  function assignScreens(shows, cfg, flag) {
    const ideal = cfg.gapIdealMin * 60
    const hard = cfg.gapHardMin * 60
    const blocks = [...new Set(shows.map((s) => s.slot.block))].sort()
    const screenEnd = { 1: null, 2: null }
    for (const b of blocks) {
      const bs = shows.filter((s) => s.slot.block === b).sort((x, y) => x.slot.screen - y.slot.screen || x.showSec - y.showSec)
      const opts = []
      if (bs.length === 1) {
        const dflt = bs[0].slot.screen
        opts.push([dflt], [dflt === 1 ? 2 : 1])
      } else {
        const dflt = bs.map((s) => s.slot.screen)
        opts.push(dflt)
        const swapped = dflt.map((x) => (x === 1 ? 2 : 1))
        if (new Set(dflt).size === dflt.length) opts.push(swapped)
      }
      const scored = opts.map((o) => {
        let min = Infinity
        o.forEach((scr, i) => {
          if (screenEnd[scr] != null) min = Math.min(min, bs[i].start - screenEnd[scr])
        })
        return { o, min }
      })
      let pick = scored[0]
      let status = 'ok'
      if (scored[0].min < ideal) {
        const alt = scored.slice(1).find((x) => x.min >= ideal)
        if (alt) pick = alt
        else {
          pick = scored.reduce((a, c) => (c.min > a.min ? c : a), scored[0])
          if (pick.min >= hard) status = 'tight'
          else status = 'conflict'
        }
      }
      bs.forEach((s, i) => {
        s.screen = pick.o[i]
        s.swapped = s.screen !== s.slot.screen
        s.gapBefore = screenEnd[s.screen] == null ? null : s.start - screenEnd[s.screen]
        s.status = s.gapBefore == null || s.gapBefore >= ideal ? 'ok' : s.gapBefore >= hard ? 'tight' : 'conflict'
      })
      bs.forEach((s) => (screenEnd[s.screen] = s.end))
      if (status === 'tight')
        flag('warn', `Tight turnaround (under ${cfg.gapIdealMin} min but over ${cfg.gapHardMin}) for the ${bs.map((s) => s.slot.time).join(' / ')} shows even after swapping`)
      if (status === 'conflict')
        flag('error', `Turnaround under ${cfg.gapHardMin} min for the ${bs.map((s) => s.slot.time).join(' / ')} shows, no theatre layout works. Needs a manual call`)
    }
  }

  function verifyEntry(e, cfg) {
    // Independent re-add of the playlist items from the scheduled start time
    const tol = cfg.toleranceSec
    const pl = e.pl
    let t = e.startSec
    let featStart = null
    for (const it of pl.items) {
      t += it.pause
      if (it.kind === 'feature') featStart = t
      t += it.runtime
    }
    const wantFeat = e.showtimeSec + cfg.leadMin * 60
    const wantEnd = wantFeat + pl.featureRuntime + cfg.blackEndSec
    const checks = []
    const c1 = Math.abs(featStart - wantFeat)
    const c2 = Math.abs(t - wantEnd)
    checks.push({ name: `Feature starts ${cfg.leadMin}:00 after showtime`, ok: c1 <= tol, got: fmtClock(featStart), want: fmtClock(wantFeat), off: featStart - wantFeat })
    checks.push({ name: 'Show ends when feature ends + black', ok: c2 <= tol, got: fmtClock(t), want: fmtClock(wantEnd), off: t - wantEnd })
    if (!pl.reliable) checks.push({ name: 'All runtimes known', ok: false, got: 'missing', want: '', off: 0 })
    const ok = checks.every((c) => c.ok)
    return {
      ok,
      checks,
      msg: checks.filter((c) => !c.ok).map((c) => c.name).join(', '),
      featureStartActual: featStart,
      endActual: t,
    }
  }

  /** Plain-JSON snapshot of a plan (no Sets / circular refs) for saving to history. */
  function summarize(plan) {
    const clk = fmtClock
    return {
      films: plan.films.map((f) => ({
        title: f.title, rating: f.rating, framing: f.framing, start: f.start, end: f.end, slots: f.slots,
        runtimeSec: f.feature.runtime, ffecSec: f.feature.ffec, cpl: f.feature.cpl,
      })),
      playlists: plan.playlists.map((pl) => ({
        name: pl.name, title: pl.title, svw: pl.svw, suffix: pl.suffix, start: pl.start, end: pl.end,
        rating: pl.rating, framing: pl.framing, screens: pl.screenList,
        preshowSec: pl.preshowSec, featureTC: pl.featureTC, featureRuntimeSec: pl.featureRuntime,
        totalTC: pl.totalTC, totalWall: pl.totalWall, reliable: pl.reliable,
        warnings: pl.warnings,
        items: pl.items.map((it, i) => ({ n: i + 1, kind: it.kind, label: it.label, cpl: it.cpl, runtimeSec: it.runtime, tc: it.tc, wallSec: it.wall, cues: it.cues, note: it.note })),
      })),
      schedule: plan.schedule.map((e) => ({
        playlist: e.pl.name, screen: e.screen, slotId: e.slot.id, showtime: e.showtime,
        startDate: e.startDate, endDate: e.endDate, extraDays: e.extraDays,
        startClock: e.startClock, featureStart: clk(e.featureStartSec), featureEnd: clk(e.featureEndSec), showEnd: clk(e.endSec),
        preshowSec: e.preshowSec, ok: e.verify.ok, msg: e.verify.msg, offSec: e.verify.checks[0].off,
      })),
      theater: plan.theater.map((g) => ({
        from: g.from, to: g.to,
        shows: g.shows.map((s) => ({
          screen: s.screen, swapped: !!s.swapped, showtime: s.slot.time, film: s.film.title, playlist: s.pl.name,
          preshowStart: clk(s.start), showEnd: clk(s.end), gapMin: s.gapBefore == null ? null : Math.round(s.gapBefore / 60), status: s.status || 'ok',
        })),
      })),
      flags: plan.flags,
    }
  }

  /* ---------------------------------------------------------- UI helpers */
  function defaultFilmSlots(n) {
    if (n <= 1) return [['S1', 'S2', 'S3', 'S4']]
    if (n === 2) return [['S1', 'S3'], ['S2', 'S4']]
    if (n === 3) return [['S1', 'S3'], ['S2'], ['S4']]
    return [['S1'], ['S2'], ['S3'], ['S4']]
  }
  function defaultDates(feature, today) {
    const nextMon = addDays(mondayOf(today), 7)
    const start = feature.openDate && feature.openDate > today ? feature.openDate : nextMon
    return { start, end: addDays(start, 6) }
  }

  return {
    RATINGS, DEFAULT_CFG, DEFAULT_SLOTS,
    loadData, buildPlan, summarize, findAd, pickTrailers,
    defaultFilmSlots, defaultDates,
    util: { parseDate, addDays, mondayOf, dow, dateRange, svWeek, parseDur, parseHM, fmtHMS, fmtTC, fmtClock, fmt12, fmtMin, fmtShortDate, normTitle, normRating, rank, daysBetween },
  }
})
