const test = require('node:test')
const assert = require('node:assert')
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { DatabaseSync } = require('node:sqlite')
const PB = require('../public/engine.js')
const sample = require('../data/sample-rows.json')

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'pb-'))
function boot(port, env) {
  const p = spawn('node', ['--no-warnings', path.join(__dirname, '..', 'server.js')], {
    env: Object.assign({}, process.env, { PORT: String(port), DB_FILE: path.join(tmp, `h${port}.db`) }, env),
    stdio: 'ignore',
  })
  return p
}
const wait = async (port) => {
  for (let i = 0; i < 50; i++) {
    try {
      await fetch(`http://localhost:${port}/api/rows`)
      return
    } catch (e) {
      await new Promise((r) => setTimeout(r, 100))
    }
  }
  throw new Error('server did not start')
}
const post = (url, body, headers) => fetch(url, { method: 'POST', headers: Object.assign({ 'Content-Type': 'application/json' }, headers), body: JSON.stringify(body) })

function makePayload() {
  const ctx = PB.loadData(sample)
  const key = (t) => ctx.features.find((f) => f.title === t).key
  const state = { films: [{ id: 'f1', featureKey: key('Hexed'), start: '2026-11-25', end: '2026-12-01', slots: ['S2', 'S4'], ratingOverride: '', trailerCount: 0, exclude: [] }], slots: PB.DEFAULT_SLOTS, slotsTouched: false }
  const plan = PB.buildPlan(ctx, state)
  return { label: 'SVW47 · Hexed · 11/25–12/1', note: 'test note', input: state, plan: PB.summarize(plan), fetchedAt: new Date().toISOString() }
}

test('sample data cannot be saved by default (history is permanent, no junk)', async () => {
  const s = boot(3601, {})
  try {
    await wait(3601)
    const r = await post('http://localhost:3601/api/plans', makePayload())
    assert.equal(r.status, 409)
    const rows = await (await fetch('http://localhost:3601/api/rows')).json()
    assert.equal(rows.canSave, false)
  } finally {
    s.kill()
  }
})

test('admin is off without a password, and the admin html is not publicly served', async () => {
  const s = boot(3602, {})
  try {
    await wait(3602)
    assert.equal((await fetch('http://localhost:3602/admin')).status, 404)
    assert.equal((await fetch('http://localhost:3602/admin.html')).status, 404)
    assert.equal((await fetch('http://localhost:3602/admin/admin.html')).status, 404)
  } finally {
    s.kill()
  }
})

test('save, admin login, browse, export, permanence', async () => {
  const s = boot(3603, { ALLOW_SAMPLE_SAVE: '1', ADMIN_PASSWORD: 'correct horse battery', ADMIN_PATH: '/x-secret' })
  const B = 'http://localhost:3603'
  try {
    await wait(3603)
    assert.equal((await post(B + '/api/plans', { plan: { playlists: [], schedule: [] }, input: {} })).status, 400)
    const ok = await post(B + '/api/plans', makePayload())
    assert.equal(ok.status, 200)
    const { id } = await ok.json()
    assert.equal(id, 1)

    // admin: locked until login
    assert.equal((await fetch(B + '/x-secret/api/plans')).status, 401)
    assert.equal((await fetch(B + '/admin')).status, 404, 'default path is not used')
    assert.equal((await fetch(B + '/x-secret')).status, 200, 'page itself loads (login form)')
    const bad = await post(B + '/x-secret/api/login', { password: 'nope' })
    assert.equal(bad.status, 401)
    const good = await post(B + '/x-secret/api/login', { password: 'correct horse battery' })
    assert.equal(good.status, 200)
    const cookie = good.headers.get('set-cookie').split(';')[0]
    assert.match(good.headers.get('set-cookie'), /HttpOnly/i)
    assert.match(good.headers.get('set-cookie'), /SameSite=Strict/i)
    const H = { cookie }

    const list = await (await fetch(B + '/x-secret/api/plans', { headers: H })).json()
    assert.equal(list.length, 1)
    assert.match(list[0].playlists, /SVW47 Hexed/)
    const det = await (await fetch(B + '/x-secret/api/plans/1', { headers: H })).json()
    assert.equal(det.note, 'test note')
    assert.equal(det.input.films[0].start, '2026-11-25')
    assert.ok(det.plan.schedule.length >= 2)
    const hist = await (await fetch(B + '/x-secret/api/history/Hexed', { headers: H })).json()
    assert.ok(hist.length >= 2)
    const exp = await (await fetch(B + '/x-secret/api/export', { headers: H })).json()
    assert.equal(exp.length, 1)
    const rm = await (await fetch(B + '/x-secret/api/roadmap', { headers: H })).text()
    assert.match(rm, /Roadmap/)

    // forged / tampered cookie is rejected
    const forged = 'pb_admin=' + (Date.now() + 1e9) + '.deadbeef'
    assert.equal((await fetch(B + '/x-secret/api/plans', { headers: { cookie: forged } })).status, 401)
  } finally {
    s.kill()
  }
  // permanence: even direct SQL can't delete or edit saved plans
  const db = new DatabaseSync(path.join(tmp, 'h3603.db'))
  assert.throws(() => db.exec('DELETE FROM plans'), /permanent/)
  assert.throws(() => db.exec("UPDATE plans SET label='x'"), /permanent/)
  assert.throws(() => db.exec('DELETE FROM plan_shows'), /permanent/)
  db.close()
})

test('admin login is rate limited', async () => {
  const s = boot(3604, { ADMIN_PASSWORD: 'pw' })
  try {
    await wait(3604)
    let last
    for (let i = 0; i < 6; i++) last = await post('http://localhost:3604/admin/api/login', { password: 'bad' + i })
    assert.equal(last.status, 429)
    assert.equal((await post('http://localhost:3604/admin/api/login', { password: 'pw' })).status, 429, 'even the right password waits')
  } finally {
    s.kill()
  }
})

test('staff basic auth protects the builder and the save endpoint', async () => {
  const s = boot(3605, { APP_USER: 'staff', APP_PASS: 'hunter2', ALLOW_SAMPLE_SAVE: '1' })
  try {
    await wait(3605).catch(() => {})
    assert.equal((await fetch('http://localhost:3605/')).status, 401)
    assert.equal((await post('http://localhost:3605/api/plans', makePayload())).status, 401)
    const auth = { Authorization: 'Basic ' + Buffer.from('staff:hunter2').toString('base64') }
    assert.equal((await fetch('http://localhost:3605/', { headers: auth })).status, 200)
  } finally {
    s.kill()
  }
})

test('/healthz answers without a login (for Docker and Uptime Kuma)', async () => {
  const s = boot(3606, { APP_USER: 'staff', APP_PASS: 'hunter2' })
  try {
    await wait(3606).catch(() => {})
    const r = await fetch('http://localhost:3606/healthz')
    assert.equal(r.status, 200)
    assert.equal(await r.text(), 'ok')
    assert.equal((await fetch('http://localhost:3606/api/rows')).status, 401, 'everything else still needs the login')
  } finally {
    s.kill()
  }
})
