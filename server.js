/*
 * Playlist Builder server
 * - Serves the static UI in /public
 * - GET  /api/rows   rows of the "Playlist Data" tab (Google service account, creds never reach the browser)
 * - POST /api/plans  save a plan snapshot to the permanent history database
 * - <adminPath>      hidden admin page (own password) to browse saved plans, export, and read the roadmap
 *
 * Everything you need to edit is in the CONFIG block below.
 */
const express = require('express')
const path = require('path')
const fs = require('fs')
const crypto = require('crypto')

/* ============================== CONFIG ============================== */
const CONFIG = {
  port: process.env.PORT || 3000,

  // 'sheet' = read the real Google Sheet, 'sample' = use data/sample-rows.json (placeholder run lengths)
  dataSource: process.env.DATA_SOURCE || 'sample',

  // Google Sheet to read. Share it (Viewer is enough) with the service account's email address.
  sheetId: process.env.SHEET_ID || 'PASTE_SHEET_ID_HERE',
  tabName: process.env.TAB_NAME || 'Playlist Data',

  // Service account key. Either point at the JSON file, or paste the whole JSON object into serviceAccount.
  serviceAccountFile: process.env.SERVICE_ACCOUNT_FILE || path.join(__dirname, 'service-account.json'),
  serviceAccount: null,

  // Login in front of the whole app (staff). Leave both blank to turn it off (local testing only).
  basicAuthUser: process.env.APP_USER || '',
  basicAuthPass: process.env.APP_PASS || '',

  // Permanent history. Back this file up. It lives in ./db by default, so mount that folder as a volume in Docker.
  dbFile: process.env.DB_FILE || path.join(__dirname, 'db', 'history.db'),

  // Hidden admin page. Pick an obscure path and a long password. No password = admin page is switched off.
  adminPath: process.env.ADMIN_PATH || '/admin',
  adminPassword: process.env.ADMIN_PASSWORD || '',
  adminSessionHours: 12,

  // Saving SAMPLE data would put junk in a history that can never be deleted, so it's blocked unless you flip this (tests only).
  allowSampleSave: process.env.ALLOW_SAMPLE_SAVE === '1',

  // How long to reuse sheet data before re-reading it (the Refresh button always forces a re-read)
  cacheSeconds: 60,
}
/* ==================================================================== */

const app = express()
app.disable('x-powered-by')
app.set('trust proxy', true)

// Docker healthcheck / uptime monitor target. Sits before the staff login on purpose and reveals nothing.
app.get('/healthz', (req, res) => res.type('text').send('ok'))

/* ------------------------------------------------------------ staff login */
if (CONFIG.basicAuthUser && CONFIG.basicAuthPass) {
  app.use((req, res, next) => {
    const h = req.headers.authorization || ''
    const [scheme, enc] = h.split(' ')
    if (scheme === 'Basic' && enc) {
      const [u, ...p] = Buffer.from(enc, 'base64').toString().split(':')
      if (safeEq(u, CONFIG.basicAuthUser) && safeEq(p.join(':'), CONFIG.basicAuthPass)) return next()
    }
    res.set('WWW-Authenticate', 'Basic realm="Playlist Builder"')
    res.status(401).send('Login required')
  })
} else if (CONFIG.dataSource === 'sheet') {
  console.warn('WARNING: no basic auth set and data source is the real sheet. Do not expose this to the internet like this.')
}

function safeEq(a, b) {
  const x = crypto.createHash('sha256').update(String(a)).digest()
  const y = crypto.createHash('sha256').update(String(b)).digest()
  return crypto.timingSafeEqual(x, y)
}

/* --------------------------------------------------------------- database */
const { DatabaseSync } = require('node:sqlite')
fs.mkdirSync(path.dirname(CONFIG.dbFile), { recursive: true })
const db = new DatabaseSync(CONFIG.dbFile)
db.exec(`
  PRAGMA journal_mode = WAL;
  CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    saved_at TEXT NOT NULL,
    label TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    data_source TEXT NOT NULL,
    sheet_fetched_at TEXT,
    saved_from_ip TEXT,
    error_count INTEGER NOT NULL DEFAULT 0,
    warn_count INTEGER NOT NULL DEFAULT 0,
    input_json TEXT NOT NULL,
    plan_json TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS plan_playlists (
    plan_id INTEGER NOT NULL REFERENCES plans(id),
    name TEXT NOT NULL, title TEXT, rating TEXT, framing TEXT,
    start_date TEXT, end_date TEXT, screens TEXT, preshow_sec REAL, total_tc REAL
  );
  CREATE TABLE IF NOT EXISTS plan_shows (
    plan_id INTEGER NOT NULL REFERENCES plans(id),
    playlist TEXT NOT NULL, screen INTEGER, showtime TEXT,
    start_date TEXT, end_date TEXT, start_clock TEXT,
    feature_start TEXT, feature_end TEXT, show_end TEXT, ok INTEGER
  );
  CREATE INDEX IF NOT EXISTS idx_plan_playlists_name ON plan_playlists(name);
  CREATE INDEX IF NOT EXISTS idx_plan_shows_playlist ON plan_shows(playlist);
  -- "store forever": nothing in the history can be edited or deleted, not even by accident
  CREATE TRIGGER IF NOT EXISTS plans_no_delete BEFORE DELETE ON plans BEGIN SELECT RAISE(ABORT, 'saved plans are permanent'); END;
  CREATE TRIGGER IF NOT EXISTS plans_no_update BEFORE UPDATE ON plans BEGIN SELECT RAISE(ABORT, 'saved plans are permanent'); END;
  CREATE TRIGGER IF NOT EXISTS plan_playlists_no_delete BEFORE DELETE ON plan_playlists BEGIN SELECT RAISE(ABORT, 'saved plans are permanent'); END;
  CREATE TRIGGER IF NOT EXISTS plan_shows_no_delete BEFORE DELETE ON plan_shows BEGIN SELECT RAISE(ABORT, 'saved plans are permanent'); END;
`)

function setting(key, make) {
  const r = db.prepare('SELECT value FROM settings WHERE key = ?').get(key)
  if (r) return r.value
  const v = make()
  db.prepare('INSERT INTO settings (key, value) VALUES (?, ?)').run(key, v)
  return v
}
const sessionSecret = setting('session_secret', () => crypto.randomBytes(32).toString('hex'))

/* ------------------------------------------------------------ sheet rows */
let cache = { at: 0, payload: null }
let authClient = null

async function getAuth() {
  if (authClient) return authClient
  const { GoogleAuth } = require('google-auth-library')
  const opts = { scopes: ['https://www.googleapis.com/auth/spreadsheets.readonly'] }
  if (CONFIG.serviceAccount) opts.credentials = CONFIG.serviceAccount
  else if (fs.existsSync(CONFIG.serviceAccountFile)) opts.keyFile = CONFIG.serviceAccountFile
  else throw new Error('No service account found. Set serviceAccountFile or serviceAccount in server.js CONFIG.')
  authClient = await new GoogleAuth(opts).getClient()
  return authClient
}

async function readSheet() {
  const client = await getAuth()
  const range = encodeURIComponent(`'${CONFIG.tabName}'`)
  // FORMATTED_VALUE gives dates exactly as shown in the sheet. The engine parses YYYY-MM-DD and M/D/YYYY.
  const url = `https://sheets.googleapis.com/v4/spreadsheets/${CONFIG.sheetId}/values/${range}?valueRenderOption=FORMATTED_VALUE`
  const res = await client.request({ url })
  const values = res.data.values || []
  if (!values.length) throw new Error(`Tab "${CONFIG.tabName}" is empty`)
  const header = values[0].map((h) => String(h).trim())
  return values.slice(1).map((r) => {
    const o = {}
    header.forEach((h, i) => (o[h] = r[i] == null ? '' : r[i]))
    return o
  })
}

app.get('/api/rows', async (req, res) => {
  try {
    const force = req.query.refresh === '1'
    if (!force && cache.payload && Date.now() - cache.at < CONFIG.cacheSeconds * 1000) return res.json(cache.payload)
    let rows
    if (CONFIG.dataSource === 'sheet') rows = await readSheet()
    else rows = JSON.parse(fs.readFileSync(path.join(__dirname, 'data', 'sample-rows.json'), 'utf8'))
    cache = { at: Date.now(), payload: { source: CONFIG.dataSource, fetchedAt: new Date().toISOString(), rows, canSave: CONFIG.dataSource !== 'sample' || CONFIG.allowSampleSave } }
    res.json(cache.payload)
  } catch (e) {
    console.error(e)
    res.status(502).json({ error: 'Could not read the sheet. Check the server log.' }) // never leak internals
  }
})

/* ---------------------------------------------------------- save a plan */
const str = (v, n) => String(v == null ? '' : v).slice(0, n)

app.post('/api/plans', express.json({ limit: '4mb' }), (req, res) => {
  try {
    if (CONFIG.dataSource === 'sample' && !CONFIG.allowSampleSave)
      return res.status(409).json({ error: 'Sample data is not saved. Switch the server to the real sheet first.' })
    const b = req.body || {}
    const plan = b.plan
    if (!plan || !Array.isArray(plan.playlists) || !Array.isArray(plan.schedule) || !plan.playlists.length)
      return res.status(400).json({ error: 'Nothing to save yet. Pick a feature first.' })
    if (!b.input || typeof b.input !== 'object') return res.status(400).json({ error: 'Missing input' })
    const flags = Array.isArray(plan.flags) ? plan.flags : []
    const errors = flags.filter((f) => f.level === 'error').length
    const warns = flags.filter((f) => f.level === 'warn').length
    const savedAt = new Date().toISOString()
    db.exec('BEGIN')
    try {
      const r = db
        .prepare('INSERT INTO plans (saved_at,label,note,data_source,sheet_fetched_at,saved_from_ip,error_count,warn_count,input_json,plan_json) VALUES (?,?,?,?,?,?,?,?,?,?)')
        .run(savedAt, str(b.label, 200) || 'Untitled plan', str(b.note, 1000), CONFIG.dataSource, str(b.fetchedAt, 40), str(req.ip, 80), errors, warns, JSON.stringify(b.input), JSON.stringify(plan))
      const id = Number(r.lastInsertRowid)
      const insPl = db.prepare('INSERT INTO plan_playlists VALUES (?,?,?,?,?,?,?,?,?,?)')
      for (const p of plan.playlists) insPl.run(id, str(p.name, 300), str(p.title, 300), str(p.rating, 10), str(p.framing, 10), str(p.start, 10), str(p.end, 10), (p.screens || []).join(','), Number(p.preshowSec) || 0, Number(p.totalTC) || 0)
      const insSh = db.prepare('INSERT INTO plan_shows VALUES (?,?,?,?,?,?,?,?,?,?,?)')
      for (const s of plan.schedule)
        insSh.run(id, str(s.playlist, 300), Number(s.screen) || 0, str(s.showtime, 8), str(s.startDate, 10), str(s.endDate, 10), str(s.startClock, 8), str(s.featureStart, 8), str(s.featureEnd, 8), str(s.showEnd, 8), s.ok ? 1 : 0)
      db.exec('COMMIT')
      res.json({ id, savedAt })
    } catch (e) {
      db.exec('ROLLBACK')
      throw e
    }
  } catch (e) {
    console.error(e)
    res.status(500).json({ error: 'Could not save. Check the server log.' })
  }
})

/* ------------------------------------------------------------- admin page */
const ADMIN = CONFIG.adminPath.replace(/\/+$/, '') || '/admin'
const adminOn = !!CONFIG.adminPassword

function sign(v) {
  return crypto.createHmac('sha256', sessionSecret).update(v).digest('hex')
}
function makeToken() {
  const exp = String(Date.now() + CONFIG.adminSessionHours * 3600 * 1000)
  return `${exp}.${sign(exp)}`
}
function validToken(t) {
  const [exp, sig] = String(t || '').split('.')
  if (!exp || !sig || !/^\d+$/.test(exp) || Number(exp) < Date.now()) return false
  return safeEq(sig, sign(exp))
}
function cookieOf(req, name) {
  const m = (req.headers.cookie || '').split(/;\s*/).find((c) => c.startsWith(name + '='))
  return m ? decodeURIComponent(m.slice(name.length + 1)) : ''
}
const fails = new Map() // ip -> [timestamps]
function tooMany(ip) {
  const now = Date.now()
  const list = (fails.get(ip) || []).filter((t) => now - t < 15 * 60 * 1000)
  fails.set(ip, list)
  return list.length >= 5
}
function requireAdmin(req, res, next) {
  if (validToken(cookieOf(req, 'pb_admin'))) return next()
  res.status(401).json({ error: 'login' })
}

if (adminOn) {
  const noindex = (res) => res.set({ 'X-Robots-Tag': 'noindex, nofollow', 'Cache-Control': 'no-store' })
  const adminHtml = fs.readFileSync(path.join(__dirname, 'admin', 'admin.html'), 'utf8').replace(/\{\{BASE\}\}/g, ADMIN)
  app.get(ADMIN, (req, res) => {
    noindex(res)
    res.type('html').send(adminHtml)
  })
  app.get(ADMIN + '/admin.js', (req, res) => {
    noindex(res)
    res.sendFile(path.join(__dirname, 'admin', 'admin.js'))
  })
  app.post(ADMIN + '/api/login', express.json({ limit: '10kb' }), (req, res) => {
    noindex(res)
    if (tooMany(req.ip)) return res.status(429).json({ error: 'Too many tries. Wait 15 minutes.' })
    if (!safeEq((req.body || {}).password || '', CONFIG.adminPassword)) {
      fails.get(req.ip).push(Date.now())
      return res.status(401).json({ error: 'Wrong password' })
    }
    const secure = req.secure || req.headers['x-forwarded-proto'] === 'https'
    res.cookie('pb_admin', makeToken(), { httpOnly: true, sameSite: 'strict', secure, path: ADMIN, maxAge: CONFIG.adminSessionHours * 3600 * 1000 })
    res.json({ ok: true })
  })
  app.post(ADMIN + '/api/logout', (req, res) => {
    res.clearCookie('pb_admin', { path: ADMIN })
    res.json({ ok: true })
  })
  app.get(ADMIN + '/api/session', requireAdmin, (req, res) => res.json({ ok: true }))
  app.get(ADMIN + '/api/plans', requireAdmin, (req, res) => {
    noindex(res)
    const rows = db
      .prepare(
        `SELECT p.id, p.saved_at, p.label, p.note, p.data_source, p.error_count, p.warn_count,
                (SELECT group_concat(name, ' | ') FROM plan_playlists WHERE plan_id = p.id) AS playlists
         FROM plans p ORDER BY p.id DESC`
      )
      .all()
    res.json(rows)
  })
  app.get(ADMIN + '/api/plans/:id', requireAdmin, (req, res) => {
    noindex(res)
    const r = db.prepare('SELECT * FROM plans WHERE id = ?').get(Number(req.params.id))
    if (!r) return res.status(404).json({ error: 'Not found' })
    res.json({ id: r.id, savedAt: r.saved_at, label: r.label, note: r.note, source: r.data_source, fetchedAt: r.sheet_fetched_at, input: JSON.parse(r.input_json), plan: JSON.parse(r.plan_json) })
  })
  app.get(ADMIN + '/api/history/:name', requireAdmin, (req, res) => {
    // every time a playlist name / film showed up, newest first
    const q = '%' + String(req.params.name).replace(/[%_]/g, '') + '%'
    res.json(db.prepare('SELECT plan_id, playlist, screen, showtime, start_date, end_date, start_clock FROM plan_shows WHERE playlist LIKE ? ORDER BY plan_id DESC LIMIT 500').all(q))
  })
  app.get(ADMIN + '/api/export', requireAdmin, (req, res) => {
    const all = db.prepare('SELECT * FROM plans ORDER BY id').all().map((r) => ({ id: r.id, savedAt: r.saved_at, label: r.label, note: r.note, source: r.data_source, fetchedAt: r.sheet_fetched_at, input: JSON.parse(r.input_json), plan: JSON.parse(r.plan_json) }))
    res.set('Content-Disposition', `attachment; filename="playlist-history-${new Date().toISOString().slice(0, 10)}.json"`)
    res.json(all)
  })
  app.get(ADMIN + '/api/roadmap', requireAdmin, (req, res) => {
    const f = path.join(__dirname, 'ROADMAP.md')
    res.type('text/plain').send(fs.existsSync(f) ? fs.readFileSync(f, 'utf8') : '')
  })
} else {
  console.log('Admin page is OFF (set adminPassword in CONFIG to turn it on).')
}

app.use(express.static(path.join(__dirname, 'public')))
app.listen(CONFIG.port, () => console.log(`Playlist Builder on http://localhost:${CONFIG.port}  (data: ${CONFIG.dataSource}${adminOn ? ', admin at ' + ADMIN : ''})`))
