const test = require('node:test')
const assert = require('node:assert')
const PB = require('../public/engine.js')
const sample = require('../data/sample-rows.json')
const U = PB.util

const row = (o) => Object.assign({ role: '', format: '', title: '', version: '', rating: '', release_date: '', start_date: '', end_date: '', cpl_name: '', runtime_sec: '', ffec_sec: '', only_for_title: '' }, o)

/* A tiny synthetic dataset with numbers that are easy to check by hand */
function synth() {
  const r = []
  r.push(row({ role: 'FEATURE', format: 'SCOPE', title: 'Long Epic', rating: 'PG-13', release_date: '2026-11-20', cpl_name: 'LONG_FTR', runtime_sec: 9300, ffec_sec: 8800 }))
  r.push(row({ role: 'FEATURE', format: 'SCOPE', title: 'Mid Movie', rating: 'PG-13', release_date: '2026-11-20', cpl_name: 'MID_FTR', runtime_sec: 6600, ffec_sec: 6200 }))
  r.push(row({ role: 'FEATURE', format: 'FLAT', title: 'Flat Movie', rating: 'PG-13', release_date: '2026-11-20', cpl_name: 'FLAT_FTR', runtime_sec: 5400, ffec_sec: 5000 }))
  r.push(row({ role: 'FEATURE', format: 'F-200', title: 'Wide Movie', rating: 'PG-13', release_date: '2026-11-20', cpl_name: 'F200_FTR', runtime_sec: 5400, ffec_sec: 5000 }))
  for (const [fmt, c] of [['SCOPE', 'S'], ['FLAT', 'F']]) {
    r.push(row({ role: 'COKE', format: fmt, rating: 'ALL', cpl_name: 'COKE_' + c, runtime_sec: 61 }))
    r.push(row({ role: 'CHEVY', format: fmt, rating: 'ALL', cpl_name: 'CHEVY_' + c, runtime_sec: 60 }))
    r.push(row({ role: 'PRESHOW', format: fmt, rating: 'ALL', cpl_name: 'PRE_' + c, runtime_sec: 31 }))
    r.push(row({ role: 'RATING_CARD', format: fmt, rating: 'PG-13', cpl_name: 'CARD_' + c, runtime_sec: 6 }))
    ;[['TA', '2027-02-01'], ['TB', '2027-01-01'], ['TC', '2026-12-25'], ['TD', '2026-12-18'], ['TE', '2026-12-10'], ['TF', '2026-11-27']].forEach(([n, d]) =>
      r.push(row({ role: 'TRAILER', format: fmt, title: 'Trailer ' + n, version: 'TLR-1', rating: 'PG-13', release_date: d, cpl_name: `${n}_${c}`, runtime_sec: 150 }))
    )
  }
  r.push(row({ role: 'SV_EARLY', format: 'SCOPE', rating: 'PG-13', start_date: '2026-11-23', end_date: '2026-11-29', cpl_name: 'SVE_47', runtime_sec: 420 }))
  r.push(row({ role: 'SV_LATE', format: 'SCOPE', rating: 'PG-13', start_date: '2026-11-23', end_date: '2026-11-29', cpl_name: 'SVL_47', runtime_sec: 300 }))
  r.push(row({ role: 'SV_EARLY', format: 'SCOPE', rating: 'PG-13', start_date: '2026-11-30', end_date: '2026-12-06', cpl_name: 'SVE_48', runtime_sec: 420 }))
  r.push(row({ role: 'SV_LATE', format: 'SCOPE', rating: 'PG-13', start_date: '2026-11-30', end_date: '2026-12-06', cpl_name: 'SVL_48', runtime_sec: 300 }))
  return r
}

test('SV week numbering resets with the calendar year', () => {
  assert.equal(U.svWeek('2026-11-23'), 47)
  assert.equal(U.svWeek('2026-11-29'), 47)
  assert.equal(U.svWeek('2026-11-30'), 48)
  assert.equal(U.svWeek('2026-12-28'), 52)
  assert.equal(U.svWeek('2027-01-04'), 1)
})

test("trailer order matches Liam's two examples (soonest N, played furthest-out first)", () => {
  const ctx = PB.loadData(sample)
  const film = { title: 'Some PG-13 Film', rating: 'PG-13', rank: 2, framing: 'SCOPE', exclude: new Set() }
  const names = (d) => PB.pickTrailers(ctx, film, d, 5).list.map((t) => t.title)
  assert.deepEqual(names('2026-11-25'), ['Artificial', 'Mr. Irrelevant', 'Dune: Part Three', 'Avengers: Doomsday', 'Focker In-Law'])
  assert.deepEqual(names('2026-12-04'), ['Live Like That', 'Artificial', 'Mr. Irrelevant', 'Dune: Part Three', 'Avengers: Doomsday'])
})

test('never picks a trailer rated above the film, never the film itself', () => {
  const ctx = PB.loadData(sample)
  const pg = { title: 'Hexed', rating: 'PG', rank: 1, framing: 'FLAT', exclude: new Set() }
  const list = PB.pickTrailers(ctx, pg, '2026-11-25', 6).list
  assert.ok(list.length > 0)
  assert.ok(list.every((t) => PB.util.rank(t.rating) <= 1), 'only PG or below')
  assert.ok(!list.some((t) => t.title === 'Hexed'))
  assert.ok(list.every((t) => /_F_/.test(t.cpl)), 'flat film gets flat trailers')
})

test('feature starts exactly 10:00 after the showtime and times are exact', () => {
  const ctx = PB.loadData(synth())
  const plan = PB.buildPlan(ctx, {
    films: [{ id: 'a', featureKey: 'LONG_FTR', start: '2026-11-23', end: '2026-11-25', slots: ['S1'], trailerCount: 5 }],
  })
  assert.equal(plan.schedule.length, 1)
  const e = plan.schedule[0]
  // preshow = 60+420+300+61+60+5*150+31+6 = 1688s (28:08)  =>  15:40:00 - 28:08 = 15:11:52
  assert.equal(e.preshowSec, 1688)
  assert.equal(e.startClock, '15:11:52')
  assert.equal(U.fmtClock(e.featureStartSec), '15:40:00')
  assert.equal(U.fmtClock(e.featureEndSec), '18:15:00')
  assert.equal(U.fmtClock(e.endSec), '18:15:05')
  assert.equal(e.pl.name, 'SVW47 Long Epic')
  assert.equal(e.extraDays, 2)
  assert.ok(e.verify.ok)
})

test('your swap scenario: long 3:30 show forces the 6:30 and 7:00 to swap theatres', () => {
  const ctx = PB.loadData(synth())
  const plan = PB.buildPlan(ctx, {
    films: [
      { id: 'a', featureKey: 'LONG_FTR', start: '2026-11-23', end: '2026-11-25', slots: ['S1', 'S3'], trailerCount: 5 },
      { id: 'b', featureKey: 'MID_FTR', start: '2026-11-23', end: '2026-11-25', slots: ['S2', 'S4'], trailerCount: 5 },
    ],
  })
  const by = (film, slot) => plan.schedule.find((e) => e.film.id === film && e.slot.id === slot)
  assert.equal(by('a', 'S1').screen, 1)
  assert.equal(by('b', 'S2').screen, 2)
  assert.equal(by('a', 'S3').screen, 2, '6:30 goes to #2')
  assert.equal(by('b', 'S4').screen, 1, '7:00 goes to #1')
  // 4:00 show out at 18:00:05, 6:30 preshow starts 18:11:52 => 11:47 gap, under 15 but over 5 => tight warning
  const tight = plan.flags.find((f) => /Tight turnaround/.test(f.text))
  assert.ok(tight, 'tight turnaround flagged')
  assert.ok(!plan.flags.some((f) => f.level === 'error'), 'no errors: ' + JSON.stringify(plan.flags))
  // playlist A must be built on BOTH servers
  const plA = plan.playlists.find((p) => p.film.id === 'a')
  assert.deepEqual(plA.screenList, [1, 2])
  plan.schedule.forEach((e) => assert.ok(e.verify.ok))
})

test('normal week: no swap, no flags', () => {
  const ctx = PB.loadData(synth())
  const plan = PB.buildPlan(ctx, {
    films: [
      { id: 'a', featureKey: 'FLAT_FTR', start: '2026-11-23', end: '2026-11-25', slots: ['S1', 'S3'], trailerCount: 4 },
      { id: 'b', featureKey: 'MID_FTR', start: '2026-11-23', end: '2026-11-25', slots: ['S2', 'S4'], trailerCount: 4 },
    ],
  })
  assert.deepEqual(plan.schedule.map((e) => e.screen), [1, 1, 2, 2])
  assert.equal(plan.flags.filter((f) => f.level !== 'info').length, 0, JSON.stringify(plan.flags))
})

test('flag when no theatre layout can give 5 minutes', () => {
  const r = synth()
  r.push(row({ role: 'FEATURE', format: 'SCOPE', title: 'Giant One', rating: 'PG-13', release_date: '2026-11-20', cpl_name: 'GIANT1', runtime_sec: 10800, ffec_sec: 10300 }))
  r.push(row({ role: 'FEATURE', format: 'SCOPE', title: 'Giant Two', rating: 'PG-13', release_date: '2026-11-20', cpl_name: 'GIANT2', runtime_sec: 10800, ffec_sec: 10300 }))
  const ctx = PB.loadData(r)
  const plan = PB.buildPlan(ctx, {
    films: [
      { id: 'a', featureKey: 'GIANT1', start: '2026-11-23', end: '2026-11-23', slots: ['S1', 'S3'], trailerCount: 5 },
      { id: 'b', featureKey: 'GIANT2', start: '2026-11-23', end: '2026-11-23', slots: ['S2', 'S4'], trailerCount: 5 },
    ],
  })
  assert.ok(plan.flags.some((f) => f.level === 'error' && /manual call/.test(f.text)))
})

test('flat film adds a 10s reframe pause, F-200 adds two, and the feature still lands on the dot', () => {
  const ctx = PB.loadData(synth())
  const plan = PB.buildPlan(ctx, {
    films: [
      { id: 'f', featureKey: 'FLAT_FTR', start: '2026-11-23', end: '2026-11-23', slots: ['S1'], trailerCount: 4 },
      { id: 'w', featureKey: 'F200_FTR', start: '2026-11-23', end: '2026-11-23', slots: ['S2'], trailerCount: 4 },
    ],
  })
  const f = plan.schedule.find((e) => e.film.id === 'f')
  const w = plan.schedule.find((e) => e.film.id === 'w')
  const cueNames = (pl) => pl.items.flatMap((i) => i.cues.map((c) => c.label))
  assert.ok(cueNames(f.pl).includes('Projector Flat'))
  assert.ok(!cueNames(f.pl).includes('Projector F-200'))
  assert.ok(cueNames(w.pl).includes('Projector F-200'))
  // flat: playlist timecode to feature = 60+420+300+61+60+600+31+6 = 1538, wall = +10
  assert.equal(f.pl.featureTC, 1538)
  assert.equal(f.pl.preshowSec, 1548)
  assert.equal(w.pl.preshowSec, 1558)
  assert.equal(U.fmtClock(f.featureStartSec), '15:40:00')
  assert.equal(U.fmtClock(w.featureStartSec), '16:10:00')
  assert.ok(f.verify.ok && w.verify.ok)
  assert.equal(f.pl.items.find((i) => i.kind === 'coke').cues.map((c) => c.label).join(), 'Coke,Projector Flat')
})

test('playlist order and cues match the manual example', () => {
  const ctx = PB.loadData(synth())
  const plan = PB.buildPlan(ctx, { films: [{ id: 'a', featureKey: 'LONG_FTR', start: '2026-11-23', end: '2026-11-23', slots: ['S1'], trailerCount: 4 }] })
  const pl = plan.playlists[0]
  assert.deepEqual(pl.items.map((i) => i.kind), ['black', 'sv', 'sv', 'coke', 'chevy', 'trailer', 'trailer', 'trailer', 'trailer', 'preshow', 'card', 'feature', 'black'])
  const cues = pl.items.map((i) => i.cues.map((c) => c.label + '@' + c.at).join('+'))
  assert.equal(cues[0], 'Show Start - Scope@0')
  assert.equal(cues[3], 'Coke@0')
  assert.equal(cues[4], 'Trailers@0')
  assert.equal(cues[9], 'Feature@0')
  assert.equal(cues[11], 'Credits@8800')
  assert.equal(cues[12], 'Show End@0')
})

test('week split: new SV week and mid-week ad change both create new playlists (.5)', () => {
  const ctx = PB.loadData(sample)
  const plan = PB.buildPlan(ctx, {
    films: [{ id: 'a', featureKey: ctx.features.find((f) => /Hunger/.test(f.title)).key, start: '2026-11-23', end: '2026-11-29', slots: ['S1', 'S3'] }],
  })
  assert.deepEqual(plan.playlists.map((p) => [p.name, p.start, p.end]), [
    ['SVW47 The Hunger Games: Sunrise on the Reaping', '2026-11-23', '2026-11-25'],
    ['SVW47.5 The Hunger Games: Sunrise on the Reaping', '2026-11-26', '2026-11-29'],
  ])
  const p1 = plan.playlists[0]
  const p2 = plan.playlists[1]
  assert.ok(p1.items.some((i) => /\.147_/.test(i.cpl)))
  assert.ok(p2.items.some((i) => /\.478_/.test(i.cpl)))
  // a film that opens after the switch is still the ".5" playlist
  const plan2 = PB.buildPlan(ctx, {
    films: [{ id: 'h', featureKey: ctx.features.find((f) => f.title === 'Hexed').key, start: '2026-11-27', end: '2026-12-03', slots: ['S2'] }],
  })
  assert.equal(plan2.playlists[0].name, 'SVW47.5 Hexed')
  assert.equal(plan2.playlists[1].name, 'SVW48 Hexed')
})

test('missing SV ads are flagged loudly instead of silently producing wrong times', () => {
  const ctx = PB.loadData(sample)
  const plan = PB.buildPlan(ctx, {
    films: [{ id: 'h', featureKey: ctx.features.find((f) => f.title === 'Hexed').key, start: '2026-11-30', end: '2026-12-02', slots: ['S2'] }],
  })
  assert.ok(plan.flags.some((f) => f.level === 'error' && /No SV early ad/.test(f.text)))
})

test('missing runtime marks timing as unreliable', () => {
  const r = synth()
  r.find((x) => x.cpl_name === 'COKE_S').runtime_sec = ''
  const plan = PB.buildPlan(PB.loadData(r), { films: [{ id: 'a', featureKey: 'LONG_FTR', start: '2026-11-23', end: '2026-11-23', slots: ['S1'], trailerCount: 4 }] })
  assert.ok(!plan.schedule[0].verify.ok)
  assert.ok(plan.flags.some((f) => f.level === 'error' && /No runtime/.test(f.text)))
})

test('every sample playlist for this week verifies', () => {
  const ctx = PB.loadData(sample)
  const key = (t) => ctx.features.find((f) => f.title === t).key
  const plan = PB.buildPlan(ctx, {
    films: [
      { id: 'a', featureKey: key('The Hunger Games: Sunrise on the Reaping'), start: '2026-11-23', end: '2026-11-29', slots: ['S1', 'S3'] },
      { id: 'b', featureKey: key('Hexed'), start: '2026-11-25', end: '2026-11-29', slots: ['S2', 'S4'] },
    ],
  })
  assert.ok(plan.schedule.length >= 4)
  plan.schedule.forEach((e) => assert.ok(e.verify.ok, e.pl.name))
  for (const e of plan.schedule) {
    assert.ok(Math.abs(e.featureStartSec - e.showtimeSec - 600) <= 0.5, `${e.pl.name} ${e.showtime}`)
  }
})

test('never more than 7 days per film', () => {
  const ctx = PB.loadData(synth())
  const ok = PB.buildPlan(ctx, { films: [{ id: 'a', featureKey: 'MID_FTR', start: '2026-11-25', end: '2026-12-01', slots: ['S1'] }] })
  assert.equal(ok.schedule.length, 2, '7 days exactly is fine (SVW47 + SVW48 playlists)')
  assert.ok(!ok.flags.some((f) => f.level === 'error'))
  const bad = PB.buildPlan(ctx, { films: [{ id: 'a', featureKey: 'MID_FTR', start: '2026-11-25', end: '2026-12-02', slots: ['S1'] }] })
  assert.equal(bad.schedule.length, 0)
  assert.ok(bad.flags.some((f) => f.level === 'error' && /Never schedule more than 7/.test(f.text)))
})

test('feature-specific SV late ad (Godzilla style) replaces the regular one for that film only', () => {
  const r = synth()
  r.push(row({ role: 'FEATURE', format: 'SCOPE', title: 'Godzilla Zero', rating: 'PG-13', release_date: '2026-11-20', cpl_name: 'GODZ_FTR', runtime_sec: 6000, ffec_sec: 5600 }))
  r.push(row({ role: 'SV_LATE', format: 'SCOPE', rating: 'PG-13', start_date: '2026-11-23', end_date: '2026-11-29', cpl_name: 'SVL_GODZ', runtime_sec: 310, only_for_title: 'Godzilla Zero' }))
  const ctx = PB.loadData(r)
  const plan = PB.buildPlan(ctx, {
    films: [
      { id: 'g', featureKey: 'GODZ_FTR', start: '2026-11-23', end: '2026-11-25', slots: ['S1'], trailerCount: 4 },
      { id: 'm', featureKey: 'MID_FTR', start: '2026-11-23', end: '2026-11-25', slots: ['S2'], trailerCount: 4 },
    ],
  })
  const lateOf = (id) => plan.playlists.find((p) => p.film.id === id).items.filter((i) => i.kind === 'sv')[1].cpl
  assert.equal(lateOf('g'), 'SVL_GODZ')
  assert.equal(lateOf('m'), 'SVL_47')
  assert.ok(!plan.flags.some((f) => /More than one/.test(f.text)), 'a film-specific ad is not "ambiguous"')
})

test('summarize() is plain JSON and round-trips', () => {
  const ctx = PB.loadData(synth())
  const plan = PB.buildPlan(ctx, { films: [{ id: 'a', featureKey: 'LONG_FTR', start: '2026-11-23', end: '2026-11-25', slots: ['S1', 'S3'], trailerCount: 5 }] })
  const sum = PB.summarize(plan)
  const back = JSON.parse(JSON.stringify(sum))
  assert.deepEqual(back, sum)
  assert.equal(back.schedule[0].startClock, '15:11:52')
  assert.equal(back.playlists[0].items.length, 14)
})
