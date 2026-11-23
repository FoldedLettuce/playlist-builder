#!/usr/bin/env python3
"""
trailer_automation.py  -  Bensenville Centre Theatre trailer automation (GDC SR-1000 edition)

What it does, every POLL_MINUTES (default 15):
  1. Reads the automation Gmail (read-only) for
       - Deluxe "Ready for INGESTION" e-delivery emails  -> what is AVAILABLE to ingest
       - the weekly TrailMix bulletin PDF (or a PDF you drop in BULLETIN_DIR) -> real titles + Deluxe's REMOVE list
       - Cinema Productions special ad information emails (brand, dates, Dropbox links) -> Printout's Special Advertisements block
  2. Scans both projectors over FTP (read-only) -> what is INSTALLED, with full untruncated names
  3. Applies your rules (English audio / 5.1 / CCAP preferred, release date, 28-day rule, newer version, remove list)
  4. Pushes the result to your Google Sheet (Available, Currently Downloaded, Download, Delete, Needs Review,
     Auto-Ignored, Special Ads, Status) and rebuilds the Printout
  5. Downloads weekly ad ZIPs (Cielo) and special ad ZIPs (Dropbox links in the information email) into ADS_TARGET_ROOT and tidies old ones
  6. Discord: the MAIN webhook only gets things that need you and MAJOR errors. The LOG webhook (mute it) gets minor
     errors, finished ad downloads and a healthcheck. Uptime Kuma gets a heartbeat every cycle (down on a major problem).

It NEVER uploads, renames, deletes or ingests anything on the servers. Ingest and delete stay manual in the GDC UI.
KDM auto-delivery is not part of this script (it stays disabled in projector_automation.py).

Usage
  python trailer_automation.py              ONE run, then exit. Point Task Scheduler at this, every 15 minutes
  python trailer_automation.py --dry-run    one run, print what it WOULD do, push/alert/download nothing
  python trailer_automation.py --check      test every connection (Gmail, both projectors, sheet, TMDB, Discord, Kuma, ads folder)
  python trailer_automation.py --reset      forget saved state (it rebuilds from Gmail + FTP)
  python trailer_automation.py --dump-installed   read both projectors fresh into installed_dump.csv (names, run lengths, FFEC)
  python trailer_automation.py --loop       old always-on mode (runs every POLL_MINUTES until stopped)
  pip install pdfplumber certifi            (pdfplumber reads the bulletin PDF, certifi fixes the Cielo certificate error)
"""
import argparse
import email
import ftplib
import hashlib
import imaplib
import io
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email import policy
from email.utils import parsedate_to_datetime

# =====================================================================================================
#  CONFIG  -  everything you ever need to edit lives in this one block
# =====================================================================================================

# --- Gmail (the automation account that gets every Deluxe / Cielo email) ---
IMAP_HOST = "imap.gmail.com"
IMAP_USER = ""                     # <- put the real value in .env
IMAP_PASSWORD = ""                 # <- Gmail APP password
IMAP_FOLDER = "INBOX"
IMAP_LOOKBACK_DAYS = 60            # first run (or after --reset) looks this far back

# --- The two projectors' FTP (read-only use) ---
FTP_PROJECTORS = [("Projector 1", "10.0.0.187"), ("Projector 2", "10.0.0.188")]
FTP_USER = ""                      # <- put the real value in .env
FTP_PASSWORD = ""                  # <- put the real value in .env
FTP_PATH = "/"
FTP_MAX_DEPTH = 3
FTP_TIMEOUT = 30

# --- Google Sheet endpoint (deploy the Apps Script as a Web app, paste its URL here) ---
SHEET_URL = ""                     # <- https://script.google.com/macros/s/XXXX/exec
SHEET_TOKEN = ""                   # <- must be the SAME text as WEB_SECRET at the top of TrailerManager.gs

# --- TMDB (read-only token, same one your sheet script used) ---
TMDB_TOKEN = ""                      # <- put the real value in .env (never commit it)

# --- The playlist builder app (decides which trailers are in a running or scheduled playlist) ---
# A trailer that is in a playlist that is still running or scheduled is NEVER put on the delete list, even if it is old
# or a newer version exists. It is held back until the playlist's last day. Leave blank = no protection (a warning is logged).
PLAYLIST_APP_URL = ""              # <- https://your-app-address  (no trailing slash)
PLAYLIST_APP_TOKEN = ""            # <- same text as API_TOKEN in the app's .env

# --- Discord (paste webhook URLs - blank = off) ---
DISCORD_WEBHOOK = ""               # MAIN channel. Only things that need you: downloads, deletes, review, special ads, MAJOR errors
LOG_WEBHOOK = ""                   # LOG channel (mute it). Minor errors, finished ad downloads, and a healthcheck post
LOG_EACH_RUN = True                # one short line to the LOG channel after every run (what it saw and did)
HEALTHCHECK_HOURS = 6              # only used when LOG_EACH_RUN is False: how often the log channel gets a summary
DAILY_DIGEST = True                # main channel: at most ONE "still waiting on you" summary a day...
DIGEST_HOUR = 8                    # ...sent by the first run at or after this hour, and only if something is waiting
ALERT_FORGET_DAYS = 3              # an item that vanished and comes back within this many days does NOT alert again
LOCK_STALE_MINUTES = 30            # a run that has been "going" longer than this is assumed dead and gets replaced
DISCORD_USERNAME = "Trailer Bot"

# --- Uptime Kuma (create a NEW Push monitor for this script, do not reuse another bot's) ---
# In Kuma: Add New Monitor > Monitor Type "Push" > Heartbeat Interval 1200 (20 min, the script pings every 15) > Retries 1.
# Paste the Push URL here. If a major problem is going on, the ping says "down" so Kuma alerts you too.
KUMA_PUSH_URL = ""

# --- Where it keeps its notes. Blank = a folder next to this script. ---
DATA_DIR = ""
BULLETIN_DIR = ""                  # blank = DATA_DIR/bulletins . Drop a TrailMix PDF here and it gets read too.

# --- Ad downloads (this replaces the ad part of projector_automation.py) ---
# Weekly ads: every Cielo "Ready for Download" email has a ZIP link. Special ads: the Dropbox links in the Cinema Productions information email. The script downloads it,
# unzips it into ADS_TARGET_ROOT, and keeps only the newest Flat and Scope CPL in the folder.
ADS_ENABLED = True
ADS_TARGET_ROOT = r"C:\Users\theater\Documents\Content"
ADS_LOOKBACK_DAYS = 10             # only emails newer than this get downloaded (stops a first run from grabbing old ads)
ADS_WEEKLY_DELETE_DAYS = 7         # weekly Chicago ad folders (sv_YYYYMMDD_..._chicago) older than this get archived
ADS_ARCHIVE_ROOT = r"C:\Users\theater\Documents\Content_Archive"   # expired ads are MOVED here (keep it outside the Content folder)
ADS_ARCHIVE_DAYS = 90              # an archived ad folder is deleted (recycle bin) this many days after it was archived
ADS_HBR_FALLBACK_DAYS = 30         # an HBR ad whose email had no end date is archived this many days after it arrived
CIELO_SSL_VERIFY = True            # leave True. Only set False if --check still says the Cielo certificate fails (see notes)
ADS_DROP_DIR = ""                  # blank = DATA_DIR/email_drop . Save a special ad information email here as a .eml file (or forward-save it) and the next run reads it exactly like an inbox email
ADS_MAX_TRIES = 3                  # failed downloads are retried every cycle, then you get one Discord alert

# --- Timing ---
POLL_MINUTES = 15
HEARTBEAT_HOURS = 6                # push to the sheet at least this often even if nothing changed (keeps Status fresh)
TMDB_RECHECK_HOURS = 24            # re-check titles that are unrated / releasing soon this often
TMDB_RECHECK_DAYS_STABLE = 7       # re-check everything else this often

# --- Rules ---
PLAUSIBLE_GAP_DAYS = 200           # TMDB release this far BEFORE the trailer was made = probably the wrong film -> Needs Review
AVAILABLE_DAYS = 28                # an e-delivery email counts as "available" for this many days
DELETE_AFTER_RELEASE_DAYS = 28     # delete trailers this many days after the film's release date
INCLUDE_TEASERS = False            # False = only TLR (trailers). True = also TSR (teasers).

# Never touched, never printed, never deleted. These are your installer's test trailers (matched on the slug).
DEFAULT_KEEP_SLUGS = ["WOLVERINE", "STAR-TREK", "PLANET-51", "ANGELS-DEMONS", "HARRY-POTTER-6",
                      "DEATH-AT-A-FUNERAL", "DATE-NIGHT", "UP", "GDC-2D"]

# Versions you never want (substring match on the whole DCP name, lowercase)
EXCLUDE_SUBSTRINGS = ["imax", "4dx", "dbox", "d-box", "screenx", "regal", "cinemark", "angelikagrtg",
                      "marcusgrtg", "merch"]
# ...and whole-word matches (split on _ and -)
EXCLUDE_TOKENS = ["3d", "reald", "dvis", "plf", "ocap", "opncap"]

# Special ads come ONLY from the Cinema Productions "<Brand> MARQUEE Campaign" information email (the Cielo
# "HBR Campaign" emails are ignored now). That one email has the brand, the dates, the spot length, which films
# it skips and the Dropbox links for the Flat and Scope ZIPs. The script learns code -> brand name from it by
# matching asset ids (MAR.GEI.1126.532 -> GEI), so any brand works with no setup. This table is only a fallback
# for brands whose email did not say the name. Unknown codes show as the raw code.
KNOWN_BRAND_NAMES = {"GOO": "Google", "AMA": "Amazon", "DOO": "DoorDash", "SAM": "Samsung"}
SPECIAL_AD_DEFAULT_RATINGS = "G,PG,PG-13"      # used when no campaign email gave the ratings
SPECIAL_AD_KEEP_DAYS_AFTER_END = 1             # leave the row on the Printout this many days after the end date

# =====================================================================================================
#  END OF CONFIG
# =====================================================================================================

# ---------------------------------------------------------------------------------------------------
#  .env FILE  -  your private settings live in a file called ".env" next to this script, so replacing the script
#  never wipes them. Any setting from the CONFIG block above can be put there as NAME=value (text, numbers, True/False).
#  Lists like FTP_PROJECTORS stay in the script. Run make_env.py once to create the file from your old script.
# ---------------------------------------------------------------------------------------------------
ENV_INFO = {"path": "", "loaded": [], "unknown": [], "error": ""}


def _load_env():
    path = os.environ.get("TRAILER_ENV_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    ENV_INFO["path"] = path
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except Exception as e:
        ENV_INFO["error"] = str(e)
        return
    g = globals()
    for raw in lines:
        ln = raw.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        if ln.lower().startswith("export "):
            ln = ln[7:].strip()
        k, v = ln.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        cur = g.get(k)
        if not k.isupper() or not isinstance(cur, (str, int, float, bool)):
            ENV_INFO["unknown"].append(k)
            continue
        try:
            if isinstance(cur, bool):
                g[k] = v.strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(cur, int):
                g[k] = int(v)
            elif isinstance(cur, float):
                g[k] = float(v)
            else:
                g[k] = v
            ENV_INFO["loaded"].append(k)
        except ValueError:
            ENV_INFO["unknown"].append(k + " (bad value)")


_load_env()

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = DATA_DIR or BASE
BULL_DIR = BULLETIN_DIR or os.path.join(DATA, "bulletins")
STATE_FILE = os.path.join(DATA, "trailer_state.json")
LOG_FILE = os.path.join(DATA, "trailer_automation.log")

TODAY_OVERRIDE = None              # tests only
RUN = {}                           # facts about the run in progress (for the one-line log post)


def today():
    return TODAY_OVERRIDE or date.today()


# ----------------------------------------------------------------------------------------- logging
def log(msg):
    line = "%s %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:
        print(line.encode("ascii", "replace").decode(), flush=True)
    try:
        os.makedirs(DATA, exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 2_000_000:
            os.replace(LOG_FILE, LOG_FILE + ".old")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ----------------------------------------------------------------------------------------- state
def new_state():
    return {
        "v": 1,
        "imap": {"last_uid": 0, "uidvalidity": None},
        "ledger": {},        # dcp name -> {"first": "YYYY-MM-DD", "subj": "..."}
        "titles": {},        # dcp name -> bulletin trailer title
        "slug_titles": {},   # SLUG -> film title
        "remove": {},        # dcp name -> date first seen on a remove list
        "bulletins": {},     # pdf sha1 -> {"week":..., "file":..., "names": n, "remove": n}
        "ftp": {},           # host -> {"folders": {...}, "last_ok": iso}
        "tmdb": {},          # SLUG -> cached lookup
        "review_first": {},  # SLUG -> date first needing review
        "ignored_first": {}, # SLUG -> date first auto-ignored
        "specials": {},      # key -> campaign
        "brands": {},        # brand code -> name learned from campaign emails
        "campaign_ratings": {},  # asset id -> ratings text learned from campaign emails
        "alerted": [],       # keys already announced on Discord
        "pushed_hash": "",
        "pushed_at": "",
        "ftp_fail": {},
        "ads": {},           # zip url -> {"subj":..., "date":..., "status": "pending|done|failed", "tries": n, "folder": ...}
        "ads_cleanup_day": "",
    }


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            st = json.load(f)
        base = new_state()
        base.update(st)
        return base
    except FileNotFoundError:
        return new_state()
    except Exception as e:
        log("State file unreadable (%s). Backing it up and starting fresh." % e)
        try:
            shutil.copy(STATE_FILE, STATE_FILE + ".bad")
        except Exception:
            pass
        return new_state()


def save_state(st):
    os.makedirs(DATA, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1, sort_keys=True)
    os.replace(tmp, STATE_FILE)


# ----------------------------------------------------------------------------------------- http
def http_json(url, data=None, headers=None, timeout=60):
    hdrs = {"User-Agent": "trailer-automation/1.0"}
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw.decode("utf-8")) if raw.strip() else {}


# ===================================================================================================
#  DCP NAME PARSING + THE RULES THAT DECIDE WHAT YOU WANT
# ===================================================================================================
_DATE = re.compile(r"^\d{8}$")
_FMT = re.compile(r"^([FS])(?:-(\d+))?$")
_LANG = re.compile(r"^([A-Za-z]{2,3})-([A-Za-z]{2,3})(?:-([A-Za-z]+))?$")
_RES = re.compile(r"^[24]K$")
VERSION_RE = re.compile(r"(TLR-\d+[A-Z]?|TLR-[A-Z]+|TLR)", re.I)


def parse_dcp(name):
    """slug_TLR-2[-variant]_F|S[-ratio]_AUDIO-SUB[-CAP]_REGION[-RATING]_SOUND_RES_STUDIO_DATE_VENDOR_SMPTE_OV"""
    parts = name.split("_")
    r = {"name": name, "slug": parts[0], "kind": "", "n": 1, "vtext": "", "fmt": "", "audio": "", "sub": "",
         "cap": "", "region": "", "rtag": "", "sound": "", "res": "", "date": "", "studio": "", "ok": False}
    if len(parts) < 6:
        return r
    tp = parts[1].split("-")
    r["kind"] = tp[0].upper()
    if len(tp) > 1:
        m = re.match(r"^\d+", tp[1])
        if m:
            r["n"] = int(m.group())
    fi = next((i for i in range(2, len(parts)) if _FMT.match(parts[i])), None)
    if fi is None:
        return r
    r["vtext"] = "-".join(tp[1:] + parts[2:fi]).lower()
    r["fmt"] = _FMT.match(parts[fi]).group(1)
    rest = parts[fi + 1:]
    if len(rest) < 2:
        return r
    m = _LANG.match(rest[0])
    if not m:
        return r
    r["audio"], r["sub"], r["cap"] = m.group(1).upper(), m.group(2).upper(), (m.group(3) or "").upper()
    reg = rest[1].split("-")
    r["region"], r["rtag"] = reg[0].upper(), (reg[1].upper() if len(reg) > 1 else "")
    di = next((i for i, t in enumerate(rest) if _DATE.match(t)), None)
    ri = next((i for i, t in enumerate(rest) if _RES.match(t)), None)
    if ri is not None:
        r["res"] = rest[ri]
        r["sound"] = "-".join(rest[2:ri])
    if di is not None:
        r["date"] = rest[di]
        r["studio"] = rest[di - 1] if di > 0 else ""
    r["ok"] = bool(r["date"])
    return r


def version_str(name):
    m = VERSION_RE.search(name)
    return m.group(1).upper() if m else ""


def classify_variant(p, include_teasers=None):
    """Returns (excluded_reason_or_None, penalty, notes). Lower penalty wins. Penalty >= 30 = 'only if nothing cleaner'."""
    if include_teasers is None:
        include_teasers = INCLUDE_TEASERS
    low = p["name"].lower()
    toks = set(re.split(r"[_\-]", low))
    if p["kind"] != "TLR" and not (include_teasers and p["kind"] == "TSR"):
        return ("not a trailer (%s)" % (p["kind"] or "?"), 0, [])
    if not p["ok"]:
        return ("name not understood", 0, [])
    if p["audio"] != "EN":
        return ("non-English audio (%s)" % p["audio"], 0, [])
    if p["cap"] in ("OCAP", "OPNCAP"):
        return ("open captions", 0, [])
    for s in EXCLUDE_SUBSTRINGS:
        if s in low:
            return ("excluded version (%s)" % s, 0, [])
    for t in EXCLUDE_TOKENS:
        if t in toks:
            return ("excluded version (%s)" % t, 0, [])
    sound = p["sound"].lower()
    has51 = bool(re.search(r"(^|-)51(-|$)", sound))
    has71 = bool(re.search(r"(^|-)71(-|$)", sound))
    if not (has51 or has71):
        return ("no 5.1 audio", 0, [])
    pen, notes = 0, []
    if "atmos" in low or "iab" in sound:
        pen += 30
        notes.append("Atmos/IAB")
    if "nird" in toks:
        pen += 30
        notes.append("NIRD")
    if "redband" in low or p["rtag"] == "RB":
        pen += 30
        notes.append("Redband")
    if p["sub"] not in ("XX", "") and p["cap"] != "CCAP":
        pen += 60
        notes.append("has subtitles")
    if has71 and not has51:
        pen += 10
        notes.append("7.1")
    if p["cap"] != "CCAP":
        pen += 5
        notes.append("no CCAP")
    if "generic" in toks:
        pen += 2
        notes.append("Generic")
    if "alt" in toks:
        pen += 1
        notes.append("ALT")
    return (None, pen, notes)


_TITLE_KW = re.compile(r"anniversary|\bMET\b|fathom|\bfest\b|etiquette|\+|\bseason\b|\bencore\b|sing-?along", re.I)


def auto_ignore_reason(p, title):
    """Anniversary / event-cinema items you said you never want. Returns a reason or None."""
    slug = p["slug"]
    m = re.search(r"[A-Za-z\-](\d{2,3})(?:th)?$", slug)
    if m:
        k = int(m.group(1))
        if 10 <= k <= 125 and k % 5 == 0:
            return "anniversary / re-release (%d)" % k
    if re.match(r"^MET(?=[A-Z][a-z]|\d)", slug):
        return "Met Opera / event cinema"
    if re.search(r"etiquette|Fest(?=\d|$|[A-Z])|[a-z]GF\d\d$", slug, re.I if "etiquette" in slug.lower() else 0):
        return "event / festival / etiquette spot"
    if p["studio"] == "FE":
        return "Fathom Events"
    if title and _TITLE_KW.search(title):
        return "event / anniversary title (%s)" % title
    return None


def film_title_from_trailer_title(t):
    return re.sub(r"\s+T[LS]R\b.*$", "", (t or "").strip(), flags=re.I).strip()


def guess_title(slug):
    s = re.sub(r"[_\-]+", " ", slug).strip()
    s = re.sub(r"([a-z])([A-Z0-9])", r"\1 \2", s)
    s = re.sub(r"([0-9])([A-Za-z])", r"\1 \2", s)
    s = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", s)     # "ALongWinter" -> "A Long Winter"
    return s.strip()


class Rules:
    """Built from the Slug Rules tab (actions: KEEP, IGNORE, ALLOW, MAP)."""

    def __init__(self, rows=None):
        self.keep, self.ignore, self.allow, self.maps = set(), set(), set(), {}
        for r in rows or []:
            slug = str(r.get("slug", "")).strip().upper()
            act = str(r.get("action", "")).strip().upper()
            if not slug:
                continue
            if act == "KEEP":
                self.keep.add(slug)
            elif act == "IGNORE":
                self.ignore.add(slug)
            elif act == "ALLOW":
                self.allow.add(slug)
            elif act == "MAP":
                tm = str(r.get("tmdb", "")).strip()
                tm = tm[:-2] if tm.endswith(".0") else tm
                self.maps[slug] = {"tmdb": tm, "release": str(r.get("release", "")).strip()}
        self.keep |= {s.upper() for s in DEFAULT_KEEP_SLUGS}


# ===================================================================================================
#  FTP SCAN (read-only, only re-reads folders it has not seen before)
# ===================================================================================================
MAX_XML_BYTES = 2 * 1024 * 1024


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _ftp_list(ftp, path):
    items = []
    try:
        for name, facts in ftp.mlsd(path, facts=["type", "size"]):
            t = facts.get("type", "")
            if t in ("cdir", "pdir") or name in (".", ".."):
                continue
            size = facts.get("size", "0")
            items.append((name, t == "dir", int(size) if str(size).isdigit() else 0))
        return items
    except (ftplib.error_perm, AttributeError):
        pass
    lines = []
    ftp.retrlines("LIST " + path, lines.append)
    for ln in lines:
        parts = ln.split(None, 8)
        if len(parts) < 9:
            continue
        name = parts[8].split(" -> ")[0]
        if name in (".", ".."):
            continue
        items.append((name, ln.startswith("d"), int(parts[4]) if parts[4].isdigit() else 0))
    return items


def _ftp_open(host, timeout):
    """host may be 'ip' or 'ip:port'. Returns a logged-in passive FTP connection (use as a context manager)."""
    h, _, port = host.partition(":")
    ftp = ftplib.FTP(timeout=timeout)
    ftp.connect(h, int(port or 21))
    ftp.login(FTP_USER, FTP_PASSWORD)
    ftp.set_pasv(True)
    return ftp


def _ftp_fetch(ftp, path):
    buf = io.BytesIO()
    ftp.retrbinary("RETR " + path, buf.write)
    return buf.getvalue()


def _walk_xml(ftp, path, depth, max_depth):
    try:
        entries = _ftp_list(ftp, path)
    except ftplib.all_errors as e:
        log("  FTP: could not list %s (%s)" % (path, e))
        return
    for name, is_dir, size in entries:
        full = posixpath.join(path, name)
        if is_dir:
            if depth < max_depth:
                yield from _walk_xml(ftp, full, depth + 1, max_depth)
        elif name.lower().endswith(".xml") and size <= MAX_XML_BYTES:
            yield full


def _num(el, name):
    for c in el:
        if _local(c.tag) == name and (c.text or "").strip():
            try:
                return float(c.text.strip().split()[0])
            except ValueError:
                return None
    return None


def _rate(el):
    for c in el:
        if _local(c.tag) == "EditRate":
            parts = (c.text or "").split()
            try:
                n = float(parts[0])
                d = float(parts[1]) if len(parts) > 1 else 1.0
                return n / d if d else None
            except (ValueError, IndexError):
                return None
    return None


def cpl_timing(root):
    """(total_seconds, ffec_seconds) from a CPL root. Either can be None. Run length is the sum of every reel's picture
    duration. FFEC is the 'FFEC' marker of the MainMarkers asset, placed on the whole timeline (earlier reels counted)."""
    comp_rate = _rate(root)
    total, ffec, seen = 0.0, None, False
    for reel in (el for el in root.iter() if _local(el.tag) == "Reel"):
        pic = marks = None
        for al in reel:
            if _local(al.tag) != "AssetList":
                continue
            for a in al:
                n = _local(a.tag)
                if n.endswith("Picture") and pic is None:
                    pic = a
                elif n == "MainMarkers" and marks is None:
                    marks = a
        if pic is None:
            continue
        rate = _rate(pic) or comp_rate
        frames = _num(pic, "Duration")
        if frames is None:
            intr = _num(pic, "IntrinsicDuration")
            frames = None if intr is None else intr - (_num(pic, "EntryPoint") or 0)
        if not rate or frames is None:
            return None, None
        if marks is not None and ffec is None:
            mrate = _rate(marks) or rate
            entry = _num(marks, "EntryPoint") or 0
            for mk in marks.iter():
                if _local(mk.tag) != "Marker":
                    continue
                label = next(((c.text or "").strip() for c in mk if _local(c.tag) == "Label"), "")
                off = _num(mk, "Offset")
                if label.upper() == "FFEC" and off is not None:
                    ffec = total + (off - entry) / mrate
                    break
        total += frames / rate
        seen = True
    if not seen:
        return None, None
    return round(total, 3), (round(ffec, 3) if ffec is not None else None)


def parse_cpl(data):
    """{'title','issue','kind','cpl','dur','ffec'} if this XML is a CPL, else None."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    if _local(root.tag) != "CompositionPlaylist":
        return None
    found = {}
    for el in root.iter():
        k = _local(el.tag)
        if k in ("ContentTitleText", "IssueDate", "ContentKind", "Id") and k not in found:
            found[k] = (el.text or "").strip()
    if not found.get("ContentTitleText"):
        return None
    dur, ffec = cpl_timing(root)
    return {"title": found["ContentTitleText"], "issue": found.get("IssueDate", ""),
            "kind": found.get("ContentKind", ""), "cpl": found.get("Id", "").replace("urn:uuid:", ""),
            "dur": dur, "ffec": ffec}


def ftp_scan_host(label, host, hstate):
    """Returns list of {'name','kind','issue','cpl','folder'} for one projector. Raises on connection failure."""
    cache = hstate.setdefault("folders", {})
    now = datetime.now()
    with _ftp_open(host, FTP_TIMEOUT) as ftp:
        root = _ftp_list(ftp, FTP_PATH)
        dirs = [n for n, is_dir, _ in root if is_dir]
        for gone in [f for f in cache if f not in dirs]:
            del cache[gone]
        scanned = 0
        for folder in dirs:
            c = cache.get(folder)
            if c is not None:
                # folders that held no CPL (still copying?) are looked at again, but not more than once an hour
                # records saved by an older version of this script have no run length, so those folders are read once more
                if c["recs"] and all("dur" in r for r in c["recs"]):
                    continue
                if not c["recs"] and (now - datetime.fromisoformat(c["ts"])) < timedelta(hours=1):
                    continue
            recs = []
            for xml_path in _walk_xml(ftp, posixpath.join(FTP_PATH, folder), 0, FTP_MAX_DEPTH):
                try:
                    cpl = parse_cpl(_ftp_fetch(ftp, xml_path))
                except ftplib.all_errors as e:
                    log("  FTP: could not read %s (%s)" % (xml_path, e))
                    continue
                if cpl:
                    recs.append({"name": cpl["title"], "kind": cpl["kind"], "issue": cpl["issue"],
                                 "cpl": cpl["cpl"], "folder": folder, "dur": cpl["dur"], "ffec": cpl["ffec"]})
            cache[folder] = {"recs": recs, "ts": now.isoformat(timespec="seconds")}
            scanned += 1
    hstate["last_ok"] = now.isoformat(timespec="seconds")
    out = [r for c in cache.values() for r in c["recs"]]
    log("FTP %s (%s): %d packages, %d CPLs (%d folders newly read)" % (label, host, len(cache), len(out), scanned))
    return out


# ===================================================================================================
#  BULLETIN (TrailMix PDF)
# ===================================================================================================
_FULL_RE = re.compile(r"[A-Za-z0-9][^\s]*?_(?:TLR|TSR|XSN|TR)[^\s]*?_SMPTE_(?:OV|VF)")
_NEW_RE = re.compile(r"^\s*(?P<studio>\S.*?)\s{2,}(?P<title>\S.*?)\s{2,}(?P<ctt>\S+_T[LS]R\S*)\s+(?P<uuid>[0-9a-f]{8})\b")


def pdf_to_text(path):
    """pdftotext -layout if present, otherwise pdfplumber, otherwise pypdf."""
    exe = shutil.which("pdftotext")
    if exe:
        try:
            out = subprocess.run([exe, "-layout", path, "-"], capture_output=True, timeout=120)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.decode("utf-8", "replace")
        except Exception as e:
            log("pdftotext failed: %s" % e)
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join((pg.extract_text(layout=True) or "") for pg in pdf.pages)
    except ImportError:
        pass
    except Exception as e:
        log("pdfplumber failed: %s" % e)
    try:
        from pypdf import PdfReader
        rd = PdfReader(path)
        return "\n".join((pg.extract_text(extraction_mode="layout") or "") for pg in rd.pages)
    except ImportError:
        raise RuntimeError("Cannot read PDFs. Run:  pip install pdfplumber")


def parse_bulletin(text):
    """Returns {'week','titles':{name:title},'slug_titles':{SLUG:film},'remove':[names],'new':{short:(title,uuid)}}"""
    titles, slug_titles, remove, newc = {}, {}, [], {}
    lines = text.splitlines()
    week = ""
    m = re.search(r"NEW US CONTENT\s+(\d+/\d+),?\s*WEEK\s*(\d+)", text, re.I)
    if m:
        week = "WK %s (%s)" % (m.group(2), m.group(1))
    in_remove = False
    for ln in lines:
        ln = re.sub(r" {3,}", "  ", ln.replace("\t", "  ").rstrip())      # keep column gaps, drop the padding
        if "PLEASE REMOVE THE BELOW" in ln.upper():
            in_remove = True
            continue
        if in_remove:
            for f in _FULL_RE.findall(ln):
                remove.append(f)
            continue
        mn = _NEW_RE.match(ln)
        if mn:
            slug = mn.group("ctt").split("_")[0].upper()
            ft = film_title_from_trailer_title(mn.group("title"))
            if ft:
                slug_titles.setdefault(slug, ft)
            newc[mn.group("ctt")] = (mn.group("title"), mn.group("uuid"))
            continue
        fm = _FULL_RE.search(ln)
        if not fm:
            continue
        name = fm.group(0)
        before = ln[:fm.start()].strip()
        parts = re.split(r"\s{2,}", before) if before else []
        if not parts:
            continue
        title = parts[-1].strip()
        if not re.search(r"\bT[LS]R\b", title, re.I):
            continue
        titles[name] = title
        slug_titles[name.split("_")[0].upper()] = film_title_from_trailer_title(title)
    return {"week": week, "titles": titles, "slug_titles": slug_titles, "remove": sorted(set(remove)), "new": newc}


def ingest_bulletin_pdf(S, pdf_bytes, label, save=True):
    """Parse a bulletin PDF and fold it into state. Returns a short summary string, or None if already known."""
    h = hashlib.sha1(pdf_bytes).hexdigest()
    if h in S["bulletins"]:
        return None
    os.makedirs(BULL_DIR, exist_ok=True)
    fn = os.path.join(BULL_DIR, re.sub(r"[^A-Za-z0-9._ -]", "_", label) or (h + ".pdf"))
    if save and not os.path.exists(fn):
        with open(fn, "wb") as f:
            f.write(pdf_bytes)
    tmp = fn if os.path.exists(fn) else None
    if tmp is None:
        tmp = os.path.join(BULL_DIR, h + ".pdf")
        with open(tmp, "wb") as f:
            f.write(pdf_bytes)
    b = parse_bulletin(pdf_to_text(tmp))
    if len(b["titles"]) < 20:
        raise RuntimeError("bulletin %s parsed only %d catalog rows (layout changed?). Nothing was changed." % (label, len(b["titles"])))
    S["titles"].update(b["titles"])
    for k, v in b["slug_titles"].items():
        S["slug_titles"][k] = v
    for n in b["remove"]:
        S["remove"].setdefault(n, today().isoformat())
    S["bulletins"][h] = {"week": b["week"], "file": os.path.basename(fn), "names": len(b["titles"]),
                         "remove": len(b["remove"]), "when": today().isoformat()}
    return "%s: %d catalog rows, %d on the remove list" % (b["week"] or label, len(b["titles"]), len(b["remove"]))


def scan_bulletin_dir(S):
    msgs = []
    if not os.path.isdir(BULL_DIR):
        return msgs
    for fn in sorted(os.listdir(BULL_DIR)):
        if not fn.lower().endswith(".pdf"):
            continue
        p = os.path.join(BULL_DIR, fn)
        try:
            with open(p, "rb") as f:
                data = f.read()
            r = ingest_bulletin_pdf(S, data, fn, save=False)
            if r:
                msgs.append(r)
        except Exception as e:
            msgs.append("ERROR reading %s: %s" % (fn, e))
    return msgs


# ===================================================================================================
#  GMAIL (read-only)
# ===================================================================================================
_NAME_RE = re.compile(r"[A-Za-z0-9][^\s<>\"]*?_SMPTE_(?:OV|VF)\b")


def _html_to_text(h):
    import html as _h
    h = re.sub(r"<wbr\s*/?>", "", h, flags=re.I)
    h = re.sub(r"<(script|style).*?</\1>", "", h, flags=re.S | re.I)
    h = re.sub(r"<br\s*/?>|</(p|tr|div|li|h\d|td)>", "\n", h, flags=re.I)
    return _h.unescape(re.sub(r"<[^>]+>", "", h))


def _msg_text(msg):
    b = msg.get_body(preferencelist=("html", "plain"))
    if b is None:
        return ""
    t = b.get_content()
    return _html_to_text(t) if b.get_content_type() == "text/html" else t


def _msg_date(msg):
    try:
        return parsedate_to_datetime(msg["date"]).astimezone().date()
    except Exception:
        return today()


def classify_message(frm, subj):
    f, s = (frm or "").lower(), (subj or "").lower()
    if "deluxeone.com" in f and "ready for ingestion from deluxe e-delivery" in s:
        return "drop"
    if "trailmix" in s and "ready for ingestion" not in s:
        return "bulletin?"
    if "cielocinema.com" in f and "ready for download" in s:
        return None if "hbr campaign" in s else "cielo_ad"       # HBR (special ad) emails are ignored, see KNOWN_BRAND_NAMES note
    if "cinemaproductionsinc.com" in f and "campaign" in s:
        return "campaign"
    return None


def norm_ratings(phrase):
    """'All G, PG, & PG-13 Titles' -> 'G,PG,PG-13'.  'All Ratings' -> 'All Ratings'.  'Non-R features' -> 'G,PG,PG-13'."""
    p = (phrase or "").strip().rstrip(".")
    if not p:
        return ""
    if re.search(r"\bnon[\s-]*r\b", p, re.I):
        return "G,PG,PG-13"
    if re.fullmatch(r"all(\s+(ratings|features|titles))?", p, re.I):
        return "All Ratings"
    found = re.findall(r"NC-17|PG-13|PG|G|R", p.upper().replace("TITLES", "").replace("ALL", ""))
    return ",".join(dict.fromkeys(found)) if found else p


def _brand_name(raw):
    raw = re.sub(r"\s+", " ", raw or "").strip(" -:")
    if not raw:
        return ""
    return raw.title() if raw.isupper() or raw.islower() else raw


MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _month_day(mon, day, year):
    mo = MONTHS.get((mon or "")[:3].lower())
    if not mo:
        return None
    try:
        return datetime(year, mo, int(day)).date()
    except ValueError:
        return None


def parse_campaign(subj, text, received=None, html=""):
    """Cinema Productions special ad information email -> everything the script needs, or None if it has no asset ids.
    Keys: ids, code, name, ratings, exclude (list of film titles to skip), start, end (ISO or ''), length (seconds or 0),
    placement, flat_url, scope_url."""
    ids = sorted(set(re.findall(r"MAR\.[A-Z0-9]+\.\d{3,4}\.\d+", text)))
    if not ids:
        return None
    flat = " ".join((text or "").split())
    m = re.search(r"([A-Za-z][A-Za-z0-9&.' ]{1,30}?)\s+will be taking over", flat) or \
        re.search(r"^\s*(.+?)\s+(?:late[- ]?)?(?:marquee|trailer|chevy)\s+campaign", subj or "", re.I)
    name = _brand_name(m.group(1)) if m else ""
    # who it runs on: "run on ALL FEATURES EXCEPT JOSEPHINE", "run on All G, PG, & PG-13 Titles"
    r = re.search(r"run\s+on\s+([^\n\r]+)", text, re.I)
    phrase = r.group(1).strip() if r else ""
    exclude, ratings = [], ""
    ex = re.search(r"\bexcept\s+(.+)$", phrase, re.I)
    if ex:
        exclude = [_brand_name(x) for x in re.split(r"\s*(?:,|&|\band\b)\s*", ex.group(1).strip(" .")) if x.strip()]
        ratings = "All Ratings"
    elif phrase:
        ratings = norm_ratings(phrase)
    year = (received or today()).year
    start = end = None
    ms = re.search(r"Starting\s+(?:[A-Za-z]+,?\s+)?([A-Za-z]+)\s+(\d{1,2})", flat, re.I)
    me = re.search(r"(?:up to and including|through|thru|until|ending(?: on)?)\s+(?:[A-Za-z]+,?\s+)?([A-Za-z]+)\s+(\d{1,2})", flat, re.I)
    if ms:
        start = _month_day(ms.group(1), ms.group(2), year)
    if me:
        end = _month_day(me.group(1), me.group(2), year)
    if start and end and end < start:
        end = _month_day(me.group(1), me.group(2), year + 1)
    ln = re.search(r"spot is\s*:?\s*(\d+)\s*(?:sec|s\b|:)?", flat, re.I)
    placement = "Late Marquee" if re.search(r"marquee", (subj or "") + " " + flat, re.I) else \
                ("Trailer" if re.search(r"trailer", subj or "", re.I) else "")
    links = {"flat": "", "scope": ""}
    for href, label in re.findall(r"<a\s[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", html or "", re.I | re.S):
        href = href.replace("&amp;", "&")
        if "dropbox.com" not in href.lower():
            continue
        lab = re.sub(r"<[^>]+>", "", label).lower()
        kind = "flat" if "flat" in lab else ("scope" if "scope" in lab else "")
        if kind and not links[kind]:
            links[kind] = href
    return {"ids": ids, "code": ids[0].split(".")[1], "name": name, "ratings": ratings, "exclude": exclude,
            "start": start.isoformat() if start else "", "end": end.isoformat() if end else "",
            "length": int(ln.group(1)) if ln else 0, "placement": placement,
            "flat_url": links["flat"], "scope_url": links["scope"]}


def dropbox_direct(url):
    """A Dropbox share link (dl=0) -> the same link forced to download (dl=1). Works without an account."""
    u = urllib.parse.urlparse(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query) if k != "dl"] + [("dl", "1")]
    return urllib.parse.urlunparse(u._replace(query=urllib.parse.urlencode(q)))


def apply_campaign(S, c, subj, received, report):
    """One special ad information email: remember the brand, the dates, who it skips, and queue the Dropbox downloads."""
    if c["name"] and S["brands"].get(c["code"]) != c["name"]:
        S["brands"][c["code"]] = c["name"]
        report.append("campaign email: %s = %s" % (c["code"], c["name"]))
    if c["ratings"]:
        for i in c["ids"]:
            S["campaign_ratings"][i] = c["ratings"]
    if not (c["start"] and c["end"]):
        report.append("campaign email has no readable dates, check by hand: %s" % subj[:60])
        return
    key = "|".join(c["ids"]) + "|" + c["start"] + "|" + c["end"]
    if key not in S["specials"]:
        S["specials"][key] = {"ids": c["ids"], "brand": c["code"], "placement": c["placement"], "start": c["start"],
                              "end": c["end"], "received": received.isoformat(), "alerts": [],
                              "exclude": c["exclude"], "length": c["length"]}
        report.append("special ad %s %s to %s%s" % (",".join(c["ids"]), c["start"], c["end"],
                      (" (skips " + ", ".join(c["exclude"]) + ")") if c["exclude"] else ""))
    if ADS_ENABLED and received >= today() - timedelta(days=ADS_LOOKBACK_DAYS):
        for kind in ("flat", "scope"):
            url = c[kind + "_url"]
            if not url:
                report.append("campaign email has no Dropbox %s link: %s" % (kind, subj[:60]))
                continue
            direct = dropbox_direct(url)
            if direct not in S["ads"]:
                S["ads"][direct] = {"subj": ("%s %s" % (subj, kind))[:90], "date": received.isoformat(), "status": "pending",
                                    "tries": 0, "folder": "", "kind": "hbr", "end": c["end"], "cpls": []}


def scan_email_drop(S, report):
    """Any .eml in the drop folder is read like an inbox email, then moved into a 'done' subfolder."""
    folder = ADS_DROP_DIR or os.path.join(DATA, "email_drop")
    if not os.path.isdir(folder):
        return
    for fn in sorted(os.listdir(folder)):
        full = os.path.join(folder, fn)
        if not (os.path.isfile(full) and fn.lower().endswith(".eml")):
            continue
        try:
            with open(full, "rb") as fh:
                msg = email.message_from_bytes(fh.read(), policy=policy.default)
            kind = classify_message(str(msg["from"]), str(msg["subject"])) or "campaign"
            apply_message(S, msg, kind, report)
            os.makedirs(os.path.join(folder, "done"), exist_ok=True)
            shutil.move(full, os.path.join(folder, "done", fn))
            report.append("dropped email read: %s" % fn)
        except Exception as e:
            report.append("ERROR reading dropped email %s: %s" % (fn, e))


def apply_message(S, msg, kind, report):
    """Fold one downloaded message into state. report collects human-readable notes."""
    subj = str(msg["subject"] or "")
    if kind == "cielo_ad":
        queue_ad_download(S, msg, subj)
    if kind == "drop":
        names = sorted(set(_NAME_RE.findall(_msg_text(msg))))
        d = _msg_date(msg).isoformat()
        new = 0
        for n in names:
            if n not in S["ledger"]:
                S["ledger"][n] = {"first": d, "subj": subj[:90]}
                new += 1
        if new:
            report.append("e-delivery email '%s': %d new names" % (subj[:60], new))
    elif kind == "cielo_ad":
        pass                    # nothing but the download, which was queued above
    elif kind == "campaign":
        raw = ""
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    raw += part.get_content()
                except Exception:
                    pass
        c = parse_campaign(subj, _msg_text(msg), _msg_date(msg), raw)
        if c:
            apply_campaign(S, c, subj, _msg_date(msg), report)
        else:
            report.append("campaign email has no asset ids: %s" % subj[:60])
    elif kind == "bulletin?":
        for part in msg.walk():
            fn = part.get_filename() or ""
            if fn.lower().endswith(".pdf") and "trailmix" in (fn + subj).lower():
                data = part.get_payload(decode=True)
                if data:
                    r = ingest_bulletin_pdf(S, data, fn)
                    if r:
                        report.append("bulletin " + r)


def imap_ingest(S):
    report = []
    M = imaplib.IMAP4_SSL(IMAP_HOST, timeout=60)
    try:
        M.login(IMAP_USER, IMAP_PASSWORD)
        M.select(IMAP_FOLDER, readonly=True)
        uv = None
        try:
            typ, data = M.response("UIDVALIDITY")
            uv = (data[0] or b"").decode() if data else None
        except Exception:
            pass
        st = S["imap"]
        if st["uidvalidity"] not in (None, uv):
            log("Gmail UIDVALIDITY changed; rescanning the lookback window")
            st["last_uid"] = 0
        st["uidvalidity"] = uv
        if st["last_uid"] == 0:
            since = (today() - timedelta(days=IMAP_LOOKBACK_DAYS)).strftime("%d-%b-%Y")
            typ, data = M.uid("SEARCH", None, "SINCE", since)
        else:
            typ, data = M.uid("SEARCH", None, "UID", "%d:*" % (st["last_uid"] + 1))
        uids = sorted(int(x) for x in (data[0] or b"").split() if int(x) > st["last_uid"])
        for uid in uids:
            try:
                typ, d = M.uid("FETCH", str(uid), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
                hdr = email.message_from_bytes(d[0][1], policy=policy.default) if d and d[0] else None
                kind = classify_message(str(hdr["from"]), str(hdr["subject"])) if hdr else None
                if kind:
                    typ, d = M.uid("FETCH", str(uid), "(BODY.PEEK[])")
                    msg = email.message_from_bytes(d[0][1], policy=policy.default)
                    apply_message(S, msg, kind, report)
            except Exception as e:
                log("Gmail: message %d skipped (%s)" % (uid, e))
                report.append("ERROR on message %d: %s" % (uid, e))
            st["last_uid"] = max(st["last_uid"], uid)
        log("Gmail: %d new messages looked at" % len(uids))
        scan_email_drop(S, report)
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return report


# ===================================================================================================
#  AD DOWNLOADS (weekly = Cielo, special = Dropbox)
# ===================================================================================================
CIELO_LINK_RE = re.compile(r"""https?://portal\.cielocinema\.com/[^\s'"<>]*download_zip_dcp/[^\s'"<>]+""", re.I)


def queue_ad_download(S, msg, subj):
    if not ADS_ENABLED:
        return
    d = _msg_date(msg)
    if d < today() - timedelta(days=ADS_LOOKBACK_DAYS):
        return
    raw = ""
    for part in msg.walk():             # the link lives in an href, so read the raw HTML (_msg_text strips it)
        if part.get_content_type() in ("text/html", "text/plain"):
            try:
                raw += part.get_content()
            except Exception:
                pass
    urls = CIELO_LINK_RE.findall(raw.replace("&amp;", "&"))
    if not urls:
        log("Cielo email without a ZIP link: %s" % subj[:60])
        return
    url = urls[0]
    if url not in S["ads"]:
        S["ads"][url] = {"subj": subj[:90], "date": d.isoformat(), "status": "pending", "tries": 0, "folder": "",
                         "kind": "weekly", "end": "", "cpls": []}


def _find_cpl_dir(root):
    """The folder that holds the CPLs: root/dcp if it exists, otherwise the deepest folder with a CPL file."""
    dcp = os.path.join(root, "dcp")
    if os.path.isdir(dcp):
        return dcp
    best, depth = None, -1
    for cur, _dirs, files in os.walk(root):
        for f in files:
            low = f.lower()
            hit = low.endswith(".cpl") or low.endswith(".cpl.xml")
            if not hit:
                try:
                    with open(os.path.join(cur, f), "r", errors="ignore") as fh:
                        hit = "<CompositionPlaylist" in fh.read(4096)
                except Exception:
                    pass
            if hit and cur.count(os.sep) > depth:
                best, depth = cur, cur.count(os.sep)
    return best


def _clean_cpls(folder):
    """Keep only the newest Flat and the newest Scope CPL. Returns a short text summary."""
    cands = []
    for f in os.listdir(folder):
        full = os.path.join(folder, f)
        if not os.path.isfile(full):
            continue
        low = f.lower()
        ok = low.endswith(".cpl") or low.endswith(".cpl.xml")
        if not ok:
            try:
                with open(full, "r", errors="ignore") as fh:
                    ok = "<CompositionPlaylist" in fh.read(4096)
            except Exception:
                pass
        if ok:
            cands.append(full)
    if not cands:
        return "no CPL files found"
    groups = {}
    for path in cands:
        try:
            root = ET.parse(path).getroot()
        except Exception:
            continue
        ann, issue = "", "1970-01-01T00:00:00"
        for el in root.iter():
            if el.tag.endswith("AnnotationText") and not ann:
                ann = (el.text or "").lower()
            if el.tag.endswith("IssueDate") and issue.startswith("1970"):
                issue = (el.text or issue).strip()
        fmt = "flat" if "flat" in ann else "scope" if "scope" in ann else "unknown"
        groups.setdefault(fmt, []).append((issue, path))
    removed = 0
    for fmt, items in groups.items():
        if fmt == "unknown":
            continue
        items.sort(reverse=True)
        for _issue, path in items[1:]:
            try:
                os.remove(path)
                removed += 1
            except Exception as e:
                log("Could not delete old CPL %s: %s" % (path, e))
    return "%d CPL files, removed %d older" % (len(cands), removed)


def _ad_filename(resp, url):
    cd = resp.headers.get("Content-Disposition", "") or ""
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    if m:
        fn = urllib.parse.unquote(m.group(1))
    else:
        fn = re.sub(r"[^A-Za-z0-9._-]+", "-", urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]) + ".zip"
    fn = os.path.basename(fn.replace("\\", "/"))
    return fn or "cielo_ad.zip"


def _open_cielo(req):
    """Open a Cielo URL. Python's own certificate store on this PC rejects Cielo's chain, so try the certifi bundle
    (what the old 'requests' based script used) first, then the system store."""
    import ssl
    ctxs = []
    if not CIELO_SSL_VERIFY:
        ctxs.append(ssl._create_unverified_context())
    else:
        try:
            import certifi
            ctxs.append(ssl.create_default_context(cafile=certifi.where()))
        except Exception:
            pass
        ctxs.append(ssl.create_default_context())
    last = None
    for ctx in ctxs:
        try:
            return urllib.request.urlopen(req, timeout=60, context=ctx)
        except urllib.error.URLError as e:
            if "CERTIFICATE_VERIFY_FAILED" not in str(e):
                raise
            last = e
    raise last


def download_ad(url):
    """Download one Cielo ZIP and unzip it into ADS_TARGET_ROOT. Returns (folder, note)."""
    import tempfile
    import zipfile
    os.makedirs(ADS_TARGET_ROOT, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    opener = _open_cielo if "cielocinema.com" in url.lower() else (lambda rq: urllib.request.urlopen(rq, timeout=300))
    with opener(req) as r:
        fn = _ad_filename(r, url)
        unique = os.path.splitext(fn)[0]
        final = os.path.join(ADS_TARGET_ROOT, unique)
        if os.path.isdir(final):
            return final, "already there"
        tmp = tempfile.mkdtemp(prefix="cielo_zip_")
        try:
            zpath = os.path.join(tmp, fn)
            with open(zpath, "wb") as fh:
                shutil.copyfileobj(r, fh, 1024 * 1024)
            ext = os.path.join(tmp, "x")
            with zipfile.ZipFile(zpath) as z:
                z.extractall(ext)
            shutil.move(ext, final)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    cpl_dir = _find_cpl_dir(final)
    note = _clean_cpls(cpl_dir) if cpl_dir else "no CPL folder found"
    return final, note


def _cpl_info(folder):
    """[{'title','id'}] for the CPLs in an extracted ad folder, so the ad can be matched on the projectors later."""
    out = []
    root = _find_cpl_dir(folder) if os.path.isdir(folder) else None
    for f in (os.listdir(root) if root else []):
        full = os.path.join(root, f)
        if not os.path.isfile(full):
            continue
        try:
            r = ET.parse(full).getroot()
        except Exception:
            continue
        if not r.tag.endswith("CompositionPlaylist"):
            continue
        title = cid = ""
        for el in r.iter():
            if el.tag.endswith("ContentTitleText") and not title:
                title = (el.text or "").strip()
            if el.tag.split("}")[-1] == "Id" and not cid:
                cid = (el.text or "").replace("urn:uuid:", "").strip()
        out.append({"title": title, "id": cid})
    return out


def _recycle(path):
    try:
        from send2trash import send2trash
        send2trash(path)
    except ImportError:
        shutil.rmtree(path)


def archive_ad(path):
    """Move an expired ad folder to ADS_ARCHIVE_ROOT. Returns the new path."""
    os.makedirs(ADS_ARCHIVE_ROOT, exist_ok=True)
    dest = os.path.join(ADS_ARCHIVE_ROOT, os.path.basename(path))
    if os.path.exists(dest):
        dest += "_" + datetime.now().strftime("%Y%m%d%H%M%S")
    try:
        shutil.move(path, dest)
    except PermissionError:
        # Windows refuses to rename a folder that has a read-only file inside or is open somewhere.
        # Copy it instead, then clear the read-only flags and remove the original.
        def _writable(func, p, _exc):
            try:
                os.chmod(p, 0o700)
                func(p)
            except Exception:
                pass
        shutil.copytree(path, dest, dirs_exist_ok=True)
        shutil.rmtree(path, onerror=_writable)
        if os.path.exists(path):
            shutil.rmtree(dest, ignore_errors=True)        # no half-and-half: leave the original where it was
            raise PermissionError("something still has it open (close Explorer windows or programs using that folder). Will retry")
    os.utime(dest, None)                # the archive clock starts now
    return dest


def ads_housekeeping(S):
    """Once a day: expired ads are moved to the archive, and archive folders older than ADS_ARCHIVE_DAYS are deleted.
       - HBR / special ads expire the day after their campaign end date (the date comes from the information email)
       - weekly Chicago ads (sv_YYYYMMDD_..._chicago) expire ADS_WEEKLY_DELETE_DAYS after the date in the folder name
    Returns a list of text lines about what it did."""
    if S.get("ads_cleanup_day") == today().isoformat():
        return []
    S["ads_cleanup_day"] = today().isoformat()
    did = []
    t = today()
    root_ok = os.path.isdir(ADS_TARGET_ROOT)
    if root_ok:
        for url, a in S["ads"].items():
            f = a.get("folder") or ""
            if a.get("status") != "done" or a.get("kind") != "hbr" or not f or not os.path.isdir(f) or a.get("archived"):
                continue
            end = _d(a.get("end", ""))
            limit = (end + timedelta(days=SPECIAL_AD_KEEP_DAYS_AFTER_END)) if end else \
                    ((_d(a.get("date", "")) or t) + timedelta(days=ADS_HBR_FALLBACK_DAYS))
            if t > limit:
                try:
                    a["folder"], a["archived"] = archive_ad(f), t.isoformat()
                    did.append("archived special ad %s (ended %s)" % (os.path.basename(f), a.get("end") or "?"))
                except Exception as e:
                    did.append("COULD NOT archive %s: %s" % (f, e))
        pat = re.compile(r"^sv_(\d{8})_.*_chicago$", re.I)
        for item in sorted(os.listdir(ADS_TARGET_ROOT)):
            full = os.path.join(ADS_TARGET_ROOT, item)
            m = pat.match(item)
            if not (m and os.path.isdir(full)):
                continue
            try:
                if (t - datetime.strptime(m.group(1), "%Y%m%d").date()).days > ADS_WEEKLY_DELETE_DAYS:
                    archive_ad(full)
                    did.append("archived weekly ad " + item)
            except Exception as e:
                did.append("COULD NOT archive %s: %s" % (item, e))
    arch = os.path.normpath(ADS_ARCHIVE_ROOT)
    if os.path.isdir(arch) and len(arch) > 3 and os.path.normcase(arch) != os.path.normcase(os.path.normpath(ADS_TARGET_ROOT)):
        for item in sorted(os.listdir(arch)):
            full = os.path.join(arch, item)
            try:
                if os.path.isdir(full) and (time.time() - os.path.getmtime(full)) / 86400.0 > ADS_ARCHIVE_DAYS:
                    _recycle(full)
                    did.append("deleted archived ad %s (older than %d days)" % (item, ADS_ARCHIVE_DAYS))
            except Exception as e:
                did.append("COULD NOT delete archived %s: %s" % (item, e))
    if any(x.startswith("COULD NOT") for x in did):
        S["ads_cleanup_day"] = ""        # something failed, try again on the next run instead of waiting a day
    return did


def ads_cycle(S, problems, dry=False):
    if not ADS_ENABLED:
        return
    for url, a in S["ads"].items():
        if a["status"] == "failed" and a.get("retry_day") != today().isoformat():
            a.update(status="pending", tries=0, retry_day=today().isoformat())      # try again once a day
        if a["status"] != "pending":
            continue
        if dry:
            log("Ad waiting to download (dry run): %s" % a["subj"])
            continue
        a["tries"] += 1
        try:
            folder, note = download_ad(url)
            a.update(status="done", folder=folder, cpls=_cpl_info(folder))
            RUN["ads"] = RUN.get("ads", 0) + 1
            log("Ad downloaded: %s -> %s (%s)" % (a["subj"], folder, note))
            # ingesting is a manual job, so this goes to the MAIN channel (not just the muted log)
            main_post(S, "**New ad downloaded, ready to ingest**\n%s\n`%s`\n%s" % (a["subj"], folder, note))
        except Exception as e:
            log("Ad download failed (try %d/%d): %s - %s" % (a["tries"], ADS_MAX_TRIES, a["subj"], e))
            if a["tries"] >= ADS_MAX_TRIES:
                a["status"] = "failed"
                problems.append("ad download FAILED after %d tries: %s (%s)" % (ADS_MAX_TRIES, a["subj"][:60], e))
    if not dry:
        for line in ads_housekeeping(S):
            log("Ads: " + line)
            (problems.append(line) if line.startswith("COULD NOT") else discord_log("**Ad cleanup**\n" + line))


# ===================================================================================================
#  TMDB
# ===================================================================================================
def tmdb_get(path, params=None):
    if not TMDB_TOKEN:
        return None
    url = "https://api.themoviedb.org/3/" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        return http_json(url, headers={"Authorization": "Bearer " + TMDB_TOKEN}, timeout=20)
    except Exception as e:
        log("TMDB error %s: %s" % (path, e))
        return None


def _words(s):
    return [w for w in re.sub(r"[^a-z0-9\s]", " ", (s or "").lower()).split() if len(w) > 1]


def _overlap(a, b):
    if not a:
        return 0.0
    sb = set(b)
    return sum(1 for w in a if w in sb) / len(a)


def _d(s):
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def tmdb_info(details):
    """Pick the US release date (EARLIEST theatrical, so a re-release can't push it out) and the US rating."""
    if not details or not details.get("id"):
        return None
    us = []
    for blk in (details.get("release_dates") or {}).get("results", []):
        if blk.get("iso_3166_1") == "US":
            us = [r for r in blk.get("release_dates", []) if r.get("release_date")]
            break
    rel = None
    if us:
        for types in ([3], [2], [1, 3, 2, 4, 5, 6]):
            ds = sorted(_d(r["release_date"]) for r in us if r.get("type") in types and _d(r["release_date"]))
            if ds:
                rel = ds[0]
                break
    if rel is None and details.get("release_date"):
        rel = _d(details["release_date"])
    rating = ""
    for typ in (3, 5, 4, 1, 2, 6):
        rs = sorted((r for r in us if r.get("type") == typ), key=lambda r: r["release_date"], reverse=True)
        for r in rs:
            c = (r.get("certification") or "").strip()
            if c and c.upper() not in ("NR", "NOT RATED", "UNRATED"):
                rating = c
                break
        if rating:
            break
    return {"id": details["id"], "title": details.get("title", ""), "release": rel.isoformat() if rel else None,
            "rating": rating}


def tmdb_details(tid):
    return tmdb_get("movie/%s" % tid, {"append_to_response": "release_dates"})


_JUNK_TAIL = re.compile(r"\b(?:TR\d+|TLR|TSR|TEASER|TRAILER)\b", re.I)


def title_candidates(title):
    """The bulletin often gives 'Warner Bros. Dune: Part Three'. Try the whole thing, then drop up to 3 leading
    words (studio names) one at a time. Returns a list, best first."""
    t = re.sub(r"\s+", " ", _JUNK_TAIL.sub(" ", title or "")).strip()
    words = t.split()
    out = [t] if t else []
    for k in (1, 2, 3):
        if len(words) - k >= 1:
            out.append(" ".join(words[k:]))
    return list(dict.fromkeys(out))


def tmdb_search(title):
    """Next year, this year (recent), then loose. Repeated for each studio-stripped version of the title."""
    t = today()
    for idx, cand in enumerate(title_candidates(title)):
        words = _words(cand)
        if not words:
            continue
        strict = idx > 0                  # a shortened title has to match almost exactly

        def pick(results, need):
            for c in results[:5]:
                rw = _words(c.get("title", ""))
                if strict:
                    if _overlap(words, rw) >= need and _overlap(rw, words) >= need:
                        return c
                elif _overlap(words, rw) >= 0.5:
                    return c
            return None

        for yr in (t.year + 1, t.year, t.year + 2):
            res = (tmdb_get("search/movie", {"query": cand, "language": "en-US", "year": yr}) or {}).get("results") or []
            if yr == t.year:
                cutoff = t - timedelta(days=92)
                res = [r for r in res if _d(r.get("release_date", "")) and _d(r["release_date"]) >= cutoff]
            c = pick(res, 0.8)
            if c:
                return c["id"], "search"
        res = (tmdb_get("search/movie", {"query": cand, "language": "en-US"}) or {}).get("results") or []
        c = pick(res, 1.0)
        if c:
            return c["id"], "loose"
    return None, "none"


def resolve_film(S, rules, slug, hint):
    """Cached TMDB lookup per slug. Returns dict {id,title,release,rating,src,ok} (ok False = needs review)."""
    key = slug.upper()
    now = datetime.now()
    rule = rules.maps.get(key)
    c = S["tmdb"].get(key)
    if c:
        age = now - datetime.fromisoformat(c["ts"])
        rel = _d(c.get("release") or "")
        soon = rel is not None and abs((rel - today()).days) < 45
        want = timedelta(hours=TMDB_RECHECK_HOURS) if (not c["ok"] or not c.get("rating") or soon) \
            else timedelta(days=TMDB_RECHECK_DAYS_STABLE)
        same_rule = (c.get("rule") or "") == ((rule or {}).get("tmdb", "") + "|" + (rule or {}).get("release", ""))
        if same_rule and c.get("v") == 2 and (c.get("hint") == hint or c["ok"]) and age < want:
            return c
    out = {"ok": False, "id": None, "title": "", "release": None, "rating": "", "src": "none",
           "hint": hint, "ts": now.isoformat(timespec="seconds"), "v": 2,
           "rule": ((rule or {}).get("tmdb", "") + "|" + (rule or {}).get("release", ""))}
    tid, src = None, "none"
    if rule and rule["tmdb"]:
        tid, src = rule["tmdb"], "override"
    elif hint:
        tid, src = tmdb_search(hint)
    if tid:
        info = tmdb_info(tmdb_details(tid))
        if info:
            out.update(info)
            out["ok"], out["src"] = True, src
    if rule and rule["release"] and _d(rule["release"]):
        out["release"] = _d(rule["release"]).isoformat()
    if not out["ok"] and c and c["ok"]:
        return c                      # network hiccup: keep the last good answer
    S["tmdb"][key] = out
    return out


# ===================================================================================================
#  THE DECISION ENGINE
# ===================================================================================================
def build_plan(S, rules, inst_by_host):
    """Pure decision logic. Reads state + what is installed, returns everything the sheet needs."""
    t = today()
    keep = rules.keep
    remove = S["remove"]
    plan = {"warnings": []}

    # ---- what is installed (union of both projectors) ----
    inst = {}
    for label, items in inst_by_host.items():
        for it in items:
            d = inst.setdefault(it["name"], {"hosts": set(), "kind": it["kind"], "cpls": set()})
            d["hosts"].add(label)
            if it.get("cpl"):
                d["cpls"].add(it["cpl"])
    managed = {}
    auto_ignored = {}               # SLUG -> (example name, reason)
    for name, d in inst.items():
        p = parse_dcp(name)
        k = (d["kind"] or "").lower()
        kind_ok = p["kind"] == "TLR" or (INCLUDE_TEASERS and p["kind"] == "TSR")
        if p["slug"].upper() in keep or k not in ("trailer", "") or not kind_ok or name.upper().startswith("GDC"):
            continue
        sl = p["slug"].upper()
        if sl in rules.ignore:
            continue
        if sl not in rules.allow:       # same anniversary / event rule as for new arrivals, applied to what is installed
            why = auto_ignore_reason(p, S["titles"].get(name) or S["slug_titles"].get(sl))
            if why:
                auto_ignored.setdefault(sl, (name, why))
                continue
        managed[name] = p

    # ---- film lookups (cached) ----
    films = {}

    def hint_for(name, slug):
        tt = S["titles"].get(name)
        if tt:
            return film_title_from_trailer_title(tt)
        return S["slug_titles"].get(slug.upper()) or guess_title(slug)

    def film_of(p):
        key = p["slug"].upper()
        if key not in films:
            f = resolve_film(S, rules, p["slug"], hint_for(p["name"], p["slug"]))
            rel = _d(f["release"]) if f["ok"] and f.get("release") else None
            try:
                made = datetime.strptime(p["date"], "%Y%m%d").date()
            except Exception:
                made = None
            if rel and made and f.get("src") != "override" and (made - rel).days > PLAUSIBLE_GAP_DAYS:
                # a trailer that was mastered long AFTER the "release" is almost always the wrong film (old movie, same name)
                f = dict(f, ok=False, src="implausible")
            films[key] = f
        return films[key]

    def fkey(p):
        f = film_of(p)
        return ("t", f["id"]) if f["ok"] else ("s", p["slug"].upper())

    review = {}                     # SLUG -> (example name, guessed title)
    for name, p in managed.items():
        if not film_of(p)["ok"]:
            h = hint_for(name, p["slug"])
            if film_of(p).get("src") == "implausible":
                h += "  <- TMDB matched a %s release, looks like the wrong film. Add a MAP row in Slug Rules" % (
                    (film_of(p).get("release") or "")[:4])
            review.setdefault(p["slug"].upper(), (name, h))

    # ---- deletes ----
    deletes = {}
    for name, p in managed.items():
        f = film_of(p)
        if name in remove:
            deletes[name] = "Deluxe remove list"
        elif p["ok"] and p["audio"] != "EN":
            deletes[name] = "Non-English audio (%s)" % p["audio"]
        elif f["ok"] and f["release"]:
            age = (t - _d(f["release"])).days
            if age > DELETE_AFTER_RELEASE_DAYS:
                deletes[name] = "%d days past release (%s)" % (age, f["release"])
    have = {}
    for name, p in managed.items():
        if name not in deletes:
            k = fkey(p)
            have[k] = max(have.get(k, 0), p["n"])

    # ---- candidates from the e-delivery ledger ----
    window = t - timedelta(days=AVAILABLE_DAYS)
    status = {}                     # dcp name -> (status, note)
    elig = {}
    for name, rec in S["ledger"].items():
        first = _d(rec["first"])
        if first is None or first < window:
            continue
        if name in inst:
            status[name] = ("Installed", "")
            continue
        p = parse_dcp(name)
        slug = p["slug"].upper()
        if name in remove:
            status[name] = ("Skipped", "on Deluxe remove list")
            continue
        if slug in keep:
            status[name] = ("Skipped", "Keep list")
            continue
        if slug in rules.ignore:
            status[name] = ("Skipped", "ignored in Slug Rules")
            continue
        ex, pen, notes = classify_variant(p)
        if ex:
            status[name] = ("Skipped", ex)
            continue
        if slug not in rules.allow:
            title = S["titles"].get(name) or S["slug_titles"].get(slug)
            why = auto_ignore_reason(p, title)
            if why:
                auto_ignored.setdefault(slug, (name, why))
                status[name] = ("Skipped", "auto-ignored: " + why)
                continue
        elig.setdefault((slug, p["n"], p["fmt"]), []).append((pen, -int(p["date"] or 0), name, notes, p))

    editions = {}
    for (slug, n, fmt), lst in elig.items():
        lst.sort(key=lambda x: (x[0], x[1], x[2]))
        pen, _, name, notes, p = lst[0]
        editions.setdefault((slug, n), {})[fmt] = (name, p, pen, notes)
        for other in lst[1:]:
            status[other[2]] = ("Passed over", "a better version is chosen: " + name)

    by_film = {}
    for (slug, n), ed in editions.items():
        any_p = next(iter(ed.values()))[1]
        f = film_of(any_p)
        if not f["ok"]:
            review.setdefault(slug, (any_p["name"], hint_for(any_p["name"], any_p["slug"])))
            for v in ed.values():
                status[v[0]] = ("Needs review", "no TMDB match yet (see Needs Review tab)")
            continue
        by_film.setdefault(fkey(any_p), []).append((n, slug, ed, f))

    downloads, waiting, new_n = [], [], {}
    for fk, eds in by_film.items():
        def score(x):
            n, slug, ed, f = x
            return (n, 1 if ("F" in ed and "S" in ed) else 0, max(int(v[1]["date"] or 0) for v in ed.values()))
        eds.sort(key=score, reverse=True)
        n, slug, ed, f = eds[0]
        for other in eds[1:]:
            for v in other[2].values():
                status[v[0]] = ("Skipped", "newer TLR-%d is available" % n if other[0] < n else "other copy chosen")
        rel = _d(f["release"]) if f["release"] else None
        if rel and rel < t:
            for v in ed.values():
                status[v[0]] = ("Skipped", "film already released (%s)" % f["release"])
            continue
        if have.get(fk, 0) >= n:
            for v in ed.values():
                status[v[0]] = ("Skipped", "already have TLR-%d" % have[fk])
            continue
        notes = sorted({x for v in ed.values() for x in v[3] if x not in ("no CCAP",)})
        if "F" in ed and "S" in ed:
            for fmt in ("F", "S"):
                name, p, pen, nts = ed[fmt]
                downloads.append({"name": name, "film": f["title"], "ver": version_str(name), "note": ", ".join(nts),
                                  "pen": pen})
                status[name] = ("DOWNLOAD", ", ".join(nts))
            new_n[fk] = n
        else:
            fmt = "F" if "F" in ed else "S"
            name, p, pen, nts = ed[fmt]
            waiting.append({"name": name, "note": "Flat only" if fmt == "F" else "Scope only", "film": f["title"],
                            "ver": version_str(name)})
            status[name] = ("Waiting", "only the %s version is out so far" % ("Flat" if fmt == "F" else "Scope"))

    # ---- supersede / duplicate deletes ----
    for name, p in managed.items():
        if name in deletes:
            continue
        k = fkey(p)
        if k in new_n and p["n"] < new_n[k]:
            deletes[name] = "Superseded by TLR-%d" % new_n[k]
    live = {n: p for n, p in managed.items() if n not in deletes}
    top = {}
    for n, p in live.items():
        k = fkey(p)
        top[k] = max(top.get(k, 0), p["n"])
    for n, p in live.items():
        if p["n"] < top[fkey(p)]:
            deletes[n] = "Newer version installed (TLR-%d)" % top[fkey(p)]
    sig = {}
    for n, p in managed.items():
        if n in deletes:
            continue
        sig.setdefault((fkey(p), p["n"], p["fmt"], re.sub(r"_\d{8}_", "_", n)), []).append((int(p["date"] or 0), n))
    for lst in sig.values():
        if len(lst) > 1:
            lst.sort(reverse=True)
            for dt, n in lst[1:]:
                if dt < lst[0][0]:
                    deletes[n] = "Newer copy installed"

    # ---- special ads whose campaign ended, and last week's weekly ads, that are still installed ----
    t = today()
    dead_ids, dead_titles = {}, {}
    live_ids, live_titles = set(), set()        # a newer campaign can reuse the SAME CPL title, so live ones protect it
    for a in S.get("ads", {}).values():
        if a.get("kind") != "hbr":
            continue
        end = _d(a.get("end", ""))
        over = bool(end and t > end + timedelta(days=SPECIAL_AD_KEEP_DAYS_AFTER_END))
        for c in a.get("cpls", []):
            cid, ct = (c.get("id") or "").lower(), c.get("title") or ""
            if over:
                dead_ids[cid] = a["end"]
                dead_titles[ct] = a["end"]
            elif a.get("status") == "done":
                live_ids.add(cid)
                live_titles.add(ct)
    weekly = {}
    for name, d in inst.items():
        if name in managed or name in deletes:
            continue
        if any(c.lower() in live_ids for c in d["cpls"]) or name in live_titles:
            continue
        ended = next((dead_ids[c.lower()] for c in d["cpls"] if c.lower() in dead_ids), None) or dead_titles.get(name)
        if ended:
            deletes[name] = "Special ad ended %s" % _md(ended)
            continue
        m = re.match(r"^(.+?)\s+(\d{8})$", name)
        if m and (d["kind"] or "").lower() == "advertisement":
            weekly.setdefault(m.group(1), []).append((m.group(2), name))
    for lst in weekly.values():
        lst.sort(reverse=True)
        # A weekly ad runs the whole week starting on the date in its name, so the older week is only dead once the
        # newer week has actually STARTED. Until then the newer one is just sitting there waiting.
        cur = next((dt for dt, _ in lst if (_d(dt[:4] + "-" + dt[4:6] + "-" + dt[6:]) or t) <= t), None)
        if not cur:
            continue
        for dt, n in lst:
            if dt < cur:
                deletes[n] = "Older weekly ad (the %s one is playing now)" % cur

    # ---- warnings ----
    labels = [l for l, _ in FTP_PROJECTORS]
    if len(labels) == 2:
        only = sorted(n for n, d in inst.items() if len(d["hosts"]) == 1)
        if only:
            plan["warnings"].append("%d package(s) are on only one projector (e.g. %s)" % (len(only), ", ".join(only[:2])))
    dups = {}
    for label, items in inst_by_host.items():
        c = {}
        for it in items:                      # the same CPL id seen twice is ONE package, only different ids count
            c.setdefault(it["name"], {})[it.get("cpl") or ("?" + it.get("folder", ""))] = it.get("folder", "")
        for n, ids in c.items():
            if len(ids) > 1:
                dups[n] = "%s: folders %s" % (label, ", ".join(sorted(set(ids.values()))))
    if dups:
        plan["warnings"].append("same name with different CPL ids on a projector: %s (usually an old and a new campaign sharing "
                                "a title, delete the older copy in the GDC UI)" % " | ".join("%s [%s]" % (n, dups[n]) for n in sorted(dups)[:3]))

    plan.update({"inst": inst, "managed": managed, "film_of": film_of, "deletes": deletes,
                 "downloads": downloads, "waiting": waiting, "review": review, "auto_ignored": auto_ignored,
                 "status": status, "have": have})
    return plan


# ===================================================================================================
#  KEEP RULE: never delete a trailer that is in a running or scheduled playlist
# ===================================================================================================
def fetch_in_use():
    """Asks the playlist app which trailers are in playlists that are still running or scheduled.
    Returns (dict dcp name -> last playing day 'YYYY-MM-DD', error text or ''). Returns (None, '') when it is not set up."""
    if not PLAYLIST_APP_URL or not PLAYLIST_APP_TOKEN:
        return None, ""
    try:
        url = PLAYLIST_APP_URL.rstrip("/") + "/api/in-use?today=" + today().isoformat()
        d = http_json(url, headers={"Authorization": "Bearer " + PLAYLIST_APP_TOKEN}, timeout=20)
        return {t["cpl"]: t.get("lastDay", "") for t in d.get("trailers", []) if t.get("cpl")}, ""
    except Exception as e:
        return {}, "could not reach the playlist app (%s)" % e


def protect_deletes(plan, in_use, err):
    """Takes trailers out of plan['deletes'] when they are in use. Ads and special ads are not trailers and are left alone.
    If the app cannot be asked, every trailer delete is held back (safer to keep one too long than to pull a live one).
    Returns {name: (why it would be deleted, text about the hold)}. in_use None = rule not set up, nothing is held."""
    kept = {}
    if in_use is None and not err:
        return kept
    for name in list(plan["deletes"]):
        if name not in plan["managed"]:
            continue
        if err:
            kept[name] = (plan["deletes"].pop(name), "held: the playlist app could not be asked")
        elif name in in_use:
            last = in_use[name]
            kept[name] = (plan["deletes"].pop(name), "in a playlist until %s" % (_md(last) if last else "?"))
    return kept


# ===================================================================================================
#  SPECIAL ADS (information emails -> Printout "Special Advertisements" block)
# ===================================================================================================
def _md(iso):
    d = _d(iso)
    return "%d/%d" % (d.month, d.day) if d else iso


def special_rows(S):
    """All campaigns that are still current, in the shape the sheet wants. Also prunes very old ones."""
    t = today()
    out = []
    for key, c in list(S["specials"].items()):
        end = _d(c["end"])
        if end and (t - end).days > 30:
            del S["specials"][key]
            continue
        nums = sorted({i.split(".")[-1] for i in c["ids"]})
        code = c["brand"]
        known = S["brands"].get(code) or KNOWN_BRAND_NAMES.get(code)
        brand = KNOWN_BRAND_NAMES.get(code) or known or code
        ratings = next((S["campaign_ratings"][i] for i in c["ids"] if i in S["campaign_ratings"]), SPECIAL_AD_DEFAULT_RATINGS)
        if c.get("exclude"):
            ratings = "%s (not %s)" % (ratings, ", ".join(c["exclude"]))
        label = "%s ( %s)" % (brand, " & ".join("." + n for n in nums))
        active = bool(end and (t - end).days <= SPECIAL_AD_KEEP_DAYS_AFTER_END)
        out.append({"key": key, "label": label, "dates": "%s - %s" % (_md(c["start"]), _md(c["end"])),
                    "position": (c["placement"] or "").replace("-", " ").title(), "ratings": ratings,
                    "match": nums, "active": active, "start": c["start"], "end": c["end"],
                    "received": c.get("received", ""), "unknown_brand": not known,
                    "assets": ", ".join(c["ids"]), "exclude": c.get("exclude", []), "length": c.get("length", 0)})
    out.sort(key=lambda r: (r["start"], r["label"]))
    return out


# ===================================================================================================
#  SHEET PAYLOAD
# ===================================================================================================
def build_payload(S, plan, specials, inst_by_host, warnings_all):
    t = today()
    labels = [l for l, _ in FTP_PROJECTORS]
    film_of = plan["film_of"]
    managed = plan["managed"]
    rows = []
    for name in sorted(plan["inst"], key=str.lower):
        d = plan["inst"][name]
        row = [name if labels[0] in d["hosts"] else "", name if len(labels) > 1 and labels[1] in d["hosts"] else ""]
        p = managed.get(name)
        if p:
            f = film_of(p)
            if f["ok"]:
                row += [("%s %s" % (f["title"], version_str(name))).strip(), f["release"] or "", f["rating"], f["title"], f["id"]]
            else:
                row += ["", "", "", "", ""]
        else:
            row += ["", "", "", "", ""]
        rows.append(row)
    cd = {"name": "Currently Downloaded", "mode": "columns",
          "headers": ["Projector 1", "Projector 2", "Formatted Name", "Release Date", "Rating", "Film Name", "TMDB ID"],
          "rows": rows}

    dl_rows = [[d["name"], d["note"]] for d in sorted(plan["downloads"], key=lambda x: x["name"])]
    if plan["waiting"]:
        dl_rows += [["", ""], ["Waiting on Matching Format:", ""]]
        dl_rows += [[w["name"], w["note"]] for w in sorted(plan["waiting"], key=lambda x: x["name"])]
    dl = {"name": "Download", "mode": "replace", "headers": [], "rows": dl_rows, "bold_rows": (
        [len(plan["downloads"]) + 2] if plan["waiting"] else [])}
    del_rows = [[n, r] for n, r in sorted(plan["deletes"].items())]
    if plan.get("kept"):
        del_rows += [["", ""], ["DO NOT DELETE YET (in a running or scheduled playlist):", ""]]
        del_rows += [[n, "%s (would be: %s)" % (hold, why)] for n, (why, hold) in sorted(plan["kept"].items())]
    dele = {"name": "Delete", "mode": "replace", "headers": [], "rows": del_rows}

    first = S["review_first"]
    for slug in list(first):
        if slug not in plan["review"]:
            del first[slug]
    for slug in plan["review"]:
        first.setdefault(slug, t.isoformat())
    nr = {"name": "Needs Review", "mode": "replace", "headers": ["Slug", "DCP Name", "Guessed Title", "Date Added"],
          "rows": [[s, plan["review"][s][0], plan["review"][s][1], first[s]] for s in sorted(plan["review"])]}

    ig = S["ignored_first"]
    for slug, (name, why) in plan["auto_ignored"].items():
        ig.setdefault(slug, t.isoformat())
    ai = {"name": "Auto-Ignored", "mode": "replace",
          "headers": ["Slug", "Example DCP", "Why", "First Seen  (to un-ignore: add the slug to Slug Rules with Action ALLOW)"],
          "rows": [[s, v[0], v[1], ig.get(s, "")] for s, v in sorted(plan["auto_ignored"].items())]}

    order = {"DOWNLOAD": 0, "Waiting": 1, "Needs review": 2, "Installed": 3, "Passed over": 4, "Skipped": 5}
    av_rows = []
    for name, (st, note) in plan["status"].items():
        av_rows.append([name, S["ledger"][name]["first"], st, note])
    av_rows.sort(key=lambda r: (order.get(r[2], 9), r[0].lower()))
    av = {"name": "Available", "mode": "replace", "headers": ["DCP Name", "Received", "Status", "Note"], "rows": av_rows}

    sp = {"name": "Special Ads", "mode": "replace",
          "headers": ["Campaign", "Assets", "Placement", "Start", "End", "Received", "Status"],
          "rows": [[s["label"], s["assets"], s["position"], s["start"], s["end"], s["received"],
                    "on Printout" if s["active"] else "ended"] for s in specials]}

    stat_rows = [
        ["Projector 1 / 2 packages", " / ".join(str(len([1 for d in plan["inst"].values() if l in d["hosts"]])) for l in labels)],
        ["Trailers managed", len(plan["managed"])],
        ["To download / waiting / to delete", "%d / %d / %d" % (len(plan["downloads"]) // 2, len(plan["waiting"]), len(plan["deletes"]))],
        ["Needs review / auto-ignored", "%d / %d" % (len(plan["review"]), len(plan["auto_ignored"]))],
        ["Held back from delete (in a playlist)", len(plan.get("kept", {}))],
        ["Available (last %d days)" % AVAILABLE_DAYS, len(plan["status"])],
        ["Deluxe remove list size", len(S["remove"])],
        ["Latest bulletin", ", ".join(sorted({b["week"] for b in S["bulletins"].values() if b["week"]})[-1:]) or "none seen yet"],
        ["Warnings", "; ".join(warnings_all) or "none"],
    ]
    stat = {"name": "Status", "mode": "replace", "headers": ["Item", "Value"], "rows": stat_rows}
    return {"tabs": [cd, av, dl, dele, nr, ai, sp, stat],
            "specials": [{k: s[k] for k in ("key", "label", "dates", "position", "ratings", "match", "active")} for s in specials]}


def payload_hash(payload):
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


# ===================================================================================================
#  SHEET + DISCORD
# ===================================================================================================
def sheet_call(action, extra=None):
    if not SHEET_URL or not SHEET_TOKEN:
        raise RuntimeError("SHEET_URL / SHEET_TOKEN are not filled in")
    body = {"token": SHEET_TOKEN, "action": action}
    body.update(extra or {})
    resp = http_json(SHEET_URL, body, timeout=180)
    if not resp.get("ok"):
        raise RuntimeError("sheet said: %s" % resp.get("error", resp))
    return resp


def discord(text, hook=None):
    hook = hook or DISCORD_WEBHOOK
    if not hook:
        return False
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 1800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    for c in chunks:
        try:
            http_json(hook, {"content": c.strip(), "username": DISCORD_USERNAME}, timeout=20)
        except urllib.error.HTTPError as e:
            if e.code != 204:          # Discord answers 204 No Content on success (urllib may raise on empty JSON)
                log("Discord error: %s" % e)
        except Exception as e:
            if "Expecting value" not in str(e):
                log("Discord error: %s" % e)
        time.sleep(0.4)
    return True


def discord_log(text):
    """Quiet channel. Falls back to nothing (never the main channel) so the main one stays clean."""
    return discord(text, LOG_WEBHOOK) if LOG_WEBHOOK else False


MAJOR_HINTS = ("in a row", "unreachable", "cannot read any projector", "FAILED", "is empty", "not set", "crashed")


def is_major(problem):
    return any(h in problem for h in MAJOR_HINTS)


def kuma(status, msg=""):
    """Uptime Kuma push heartbeat. status is 'up' or 'down'."""
    if not KUMA_PUSH_URL:
        return
    try:
        code, body = _kuma_get(status, msg)
        if code != 200 or '"ok":true' not in body.replace(" ", ""):
            log("Uptime Kuma ping got an unexpected answer (HTTP %s): %s" % (code, _kuma_hint(body)))
    except Exception as e:
        log("Uptime Kuma ping failed: %s" % e)


def _kuma_get(status, msg=""):
    """One push heartbeat. A browser-style User-Agent is sent because proxies/WAFs often block Python's default one."""
    base = KUMA_PUSH_URL.split("?")[0].strip()
    q = urllib.parse.urlencode({"status": status, "msg": (msg or "OK")[:200], "ping": ""})
    req = urllib.request.Request(base + "?" + q, headers={
        "User-Agent": "Mozilla/5.0 (compatible; TrailerAutomation/1.0)", "Accept": "application/json,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.getcode(), r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


def _kuma_hint(body):
    m = re.search(r"<title>(.*?)</title>", body or "", re.I | re.S)
    if m:
        return "a web page titled '%s' came back instead of Kuma's answer (a login, Cloudflare or proxy page in front of Kuma?)" % \
            re.sub(r"\s+", " ", m.group(1)).strip()[:80]
    return (body or "").strip()[:120]


def send_minor(S, minors, dry=False):
    """Minor problems go to the log channel only, each one at most once per day."""
    day = today().isoformat()
    seen = S.get("minor_seen") or {}
    if seen.get("day") != day:
        seen = {"day": day, "keys": []}
    new = [m for m in minors if m not in seen["keys"]]
    if new:
        log("MINOR: " + " / ".join(new))
        if not dry:
            discord_log("**Minor issues**\n" + "\n".join("- " + m for m in new))
            seen["keys"] = seen["keys"] + new
    S["minor_seen"] = seen
    hc = S.setdefault("hc", {"last": "", "cycles": 0, "minor": []})
    for m in new:
        if m not in hc["minor"]:
            hc["minor"].append(m)


def healthcheck(S, plan, majors, dry=False):
    hc = S.setdefault("hc", {"last": "", "cycles": 0, "minor": []})
    hc["cycles"] += 1
    due = (not hc["last"]) or (datetime.now() - datetime.fromisoformat(hc["last"])) >= timedelta(hours=HEALTHCHECK_HOURS)
    if not due or dry:
        return
    lines = ["**Healthcheck %s** - %d cycles since the last one" % ("PROBLEM" if majors else "OK", hc["cycles"])]
    if plan:
        lines.append("To download %d | waiting %d | to delete %d | need review %d" % (
            len(plan["downloads"]) // 2, len(plan["waiting"]), len(plan["deletes"]), len(plan["review"])))
    lines.append("Sheet last updated: %s" % (S.get("pushed_at") or "never"))
    if majors:
        lines += ["Major: " + m for m in majors[:5]]
    if hc["minor"]:
        lines.append("Minor issues since last check (%d):" % len(hc["minor"]))
        lines += ["- " + m for m in hc["minor"][:8]]
    else:
        lines.append("No minor issues.")
    if discord_log("\n".join(lines)):
        hc.update(last=datetime.now().isoformat(timespec="seconds"), cycles=0, minor=[])


def compose_alerts(S, plan, specials, problems):
    """Builds {key: line} for everything that currently deserves attention, grouped by section."""
    t = today()
    sec = {"dl": {}, "del": {}, "rv": {}, "sp": {}, "pr": {}}
    seen = set()
    for d in plan["downloads"]:
        k = (d["film"], d["ver"])
        if k in seen:
            continue
        seen.add(k)
        sec["dl"]["dl:%s|%s" % k] = "%s %s%s (flat + scope)" % (d["film"], d["ver"], " - " + d["note"] if d["note"] else "")
    for w in plan["waiting"]:
        sec["dl"]["wt:" + w["name"]] = "%s %s - waiting on the other format (%s)" % (w["film"], w["ver"], w["note"])
    seen = set()
    for name, why in sorted(plan["deletes"].items()):
        p = plan["managed"].get(name)
        if p:
            f = plan["film_of"](p)
            label = ("%s %s" % (f["title"] or p["slug"], version_str(name))).strip()
        else:
            label = name                  # an ad, not a trailer
        k = (label, why)
        if k in seen:
            continue
        seen.add(k)
        sec["del"]["del:%s|%s" % k] = "%s - %s" % (label, why)
    for slug, (name, guess) in sorted(plan["review"].items()):
        sec["rv"]["rv:" + slug] = "%s (guessed \"%s\")" % (slug, guess)
    for s in specials:
        if not s["active"]:
            continue
        sec["sp"]["sp:" + s["key"]] = "NEW special ad: %s - %s - %s (added to the Printout)%s" % (
            s["label"], s["position"], s["dates"], "  [brand name unknown - showing the code]" if s["unknown_brand"] else "")
        end = _d(s["end"])
        if end and 0 <= (end - t).days <= 1:
            sec["sp"]["spend:" + s["key"]] = "Special ad ENDING %s: %s - pull it after the last show on %s" % (
                "today" if end == t else "tomorrow", s["label"], _md(s["end"]))
    for i, pr in enumerate(problems):
        sec["pr"]["pr:" + pr] = pr
    return sec


def ads_to_ingest(S, plan):
    """Downloaded ads (not archived) whose CPLs are not on either projector yet. Only trusts a plan with a projector scan."""
    inst = (plan or {}).get("inst") or {}
    if not inst:
        return []
    have = {c.lower() for d in inst.values() for c in d.get("cpls", [])}
    out = []
    for a in S.get("ads", {}).values():
        f = a.get("folder") or ""
        if a.get("status") != "done" or a.get("archived") or not f or not os.path.isdir(f):
            continue
        ids = [(c.get("id") or "").lower() for c in a.get("cpls", []) if c.get("id")]
        if ids and not any(i in have for i in ids):
            out.append("%s (%s)" % (a.get("subj", "")[:60], os.path.basename(f)))
    return out


def daily_digest(S, plan, specials, majors, dry=False):
    """At most one 'still waiting on you' message a day, and only when nothing else was posted to the main channel today."""
    t, now = today().isoformat(), datetime.now()
    if dry or not DAILY_DIGEST or now.hour < DIGEST_HOUR or S.get("digest_day") == t or S.get("main_day") == t:
        return
    sec = compose_alerts(S, plan, specials, majors)
    sec["sp"] = {k: v for k, v in sec["sp"].items() if k.startswith("spend:")}      # "NEW special ad" is not news anymore
    lines = []
    waiting_ads = ads_to_ingest(S, plan)
    if waiting_ads:
        lines.append("**Ads downloaded but not on the projectors yet**")
        lines += ["- " + w for w in waiting_ads[:12]]
    for key in ("dl", "del", "rv", "sp", "pr"):
        items = sorted(sec[key].values())
        if items:
            lines.append("**%s**" % SECTION_TITLES[key])
            lines += ["- " + v for v in items[:12]]
            if len(items) > 12:
                lines.append("- ...and %d more (see the sheet)" % (len(items) - 12))
    if lines and main_post(S, "**Still waiting on you**\n" + "\n".join(lines)):
        S["digest_day"] = t


def run_log_line(S, plan, majors, minors, started):
    secs = time.time() - started
    mail = RUN.get("mail", "?")
    if plan:
        body = "download %d | delete %d | review %d | projectors %s" % (
            len(plan["downloads"]) // 2 + len(plan["waiting"]), len(plan["deletes"]), len(plan["review"]),
            RUN.get("proj", "?"))
    else:
        body = "no plan this run"
    parts = ["**%s** %s (%.0fs)" % ("PROBLEM" if majors else "OK", datetime.now().strftime("%H:%M"), secs),
             "mail: %s new" % mail, body, "sheet: %s" % RUN.get("sheet", "-")]
    if RUN.get("ads"):
        parts.append("ads downloaded: %d" % RUN["ads"])
    if minors:
        parts.append("minor: %d" % len(minors))
    if majors:
        parts.append("MAJOR: " + " / ".join(majors)[:200])
    return " | ".join(parts)


SECTION_TITLES = {"dl": "Trailers to DOWNLOAD", "del": "Trailers to DELETE", "rv": "Need a TMDB match (Slug Rules tab)",
                  "sp": "Special ads", "pr": "Heads up"}


def _alerted(S):
    """Alert key -> last day it was seen. (Old state files kept a plain list.)"""
    a = S.get("alerted", {})
    if isinstance(a, list):
        a = {k: today().isoformat() for k in a}
    S["alerted"] = a
    return a


def _remember(S, keys):
    """Mark keys as announced. A key is only forgotten after it has been gone for ALERT_FORGET_DAYS, so an item that
    blinks out for a run (a failed scan, a late email) does not get announced a second time when it comes back."""
    a, t = _alerted(S), today()
    for k in keys:
        a[k] = t.isoformat()
    for k in list(a):
        d = _d(a[k])
        if d and (t - d).days > ALERT_FORGET_DAYS:
            del a[k]


def main_post(S, text):
    """The ONLY way anything reaches the main channel. Remembers the day so the daily digest can stay quiet."""
    ok = discord(text)
    if ok:
        S["main_day"] = today().isoformat()
    return ok


def alert_problems(S, problems, dry=False):
    """Used when a cycle cannot get as far as a plan. Announces each new problem once."""
    new = [p for p in problems if "pr:" + p not in _alerted(S)]
    if new:
        log("ALERT: " + " / ".join(new))
        if not dry:
            main_post(S, "**Heads up**\n" + "\n".join("- " + p for p in new))
            _remember(S, {"pr:" + p for p in problems})


def send_alerts(S, plan, specials, problems, dry=False):
    sec = compose_alerts(S, plan, specials, problems)
    current = {k for d in sec.values() for k in d}
    already = set(_alerted(S))
    if not S.get("alerts_primed"):
        msg = ("Trailer automation is running.\nTo download: %d  |  waiting on pair: %d  |  to delete: %d  |  need review: %d" % (
            len(plan["downloads"]) // 2, len(plan["waiting"]), len(plan["deletes"]), len(plan["review"])))
        log("ALERT (first run): " + msg.replace("\n", " "))
        if not dry:
            main_post(S, msg)
            S["alerts_primed"] = True
            _remember(S, current)
        return
    lines = []
    for key in ("dl", "del", "rv", "sp", "pr"):
        new = [(k, v) for k, v in sec[key].items() if k not in already]
        if new:
            lines.append("**%s**" % SECTION_TITLES[key])
            lines += ["- " + v for _, v in sorted(new, key=lambda x: x[1])]
    if lines:
        log("ALERT: " + " / ".join(l for l in lines if not l.startswith("**")))
        if not dry:
            main_post(S, "\n".join(lines))
    if not dry:
        _remember(S, current)


# ===================================================================================================
#  ONE CYCLE
# ===================================================================================================
def _end_of_run(S, plan, specials, majors, minors, started, dry):
    if dry:
        return
    if LOG_EACH_RUN:
        discord_log(run_log_line(S, plan, majors, minors, started))
    else:
        healthcheck(S, plan, majors, dry)


def run_cycle(dry=False):
    S = load_state()
    problems = []
    RUN.clear()
    started = time.time()
    try:
        # ---- rules from the sheet (falls back to the last copy we saw) ----
        rows = S.get("rules_cache", [])
        if SHEET_URL and SHEET_TOKEN:
            try:
                rows = sheet_call("rules", {"seed": DEFAULT_KEEP_SLUGS})["rules"]
                S["rules_cache"] = rows
            except Exception as e:
                log("Could not read Slug Rules (%s). Using the last copy." % e)
                problems.append("could not read the Slug Rules tab (using last copy)")
        rules = Rules(rows)

        # ---- Gmail ----
        if IMAP_PASSWORD:
            try:
                got = imap_ingest(S)
                RUN["mail"] = len(got)
                for line in got:
                    log("  " + line)
                    if line.startswith("ERROR"):
                        problems.append(line)
                S.setdefault("streak", {})["gmail"] = 0
            except Exception as e:
                log("Gmail failed: %s" % e)
                n = S.setdefault("streak", {}).get("gmail", 0) + 1
                S["streak"]["gmail"] = n
                problems.append(("Gmail has failed %d checks in a row: %s" % (n, e)) if n >= 3 else "Gmail check failed: %s" % e)
        else:
            problems.append("IMAP_PASSWORD is empty - not reading Gmail")
        try:
            ads_cycle(S, problems, dry)
        except Exception as e:
            log("Ad step failed: %s" % e)
            problems.append("ad download step failed: %s" % e)
        for line in scan_bulletin_dir(S):
            log("  bulletin: " + line)
            if line.startswith("ERROR"):
                problems.append(line)

        # ---- projectors ----
        inst_by_host, stale = {}, []
        for label, host in FTP_PROJECTORS:
            hs = S["ftp"].setdefault(host, {})
            try:
                inst_by_host[label] = ftp_scan_host(label, host, hs)
                S["ftp_fail"][host] = 0
            except Exception as e:
                S["ftp_fail"][host] = S["ftp_fail"].get(host, 0) + 1
                log("FTP %s (%s) failed: %s" % (label, host, e))
                if hs.get("folders"):
                    inst_by_host[label] = [r for c in hs["folders"].values() for r in c["recs"]]
                    stale.append(label)
                if S["ftp_fail"][host] >= 2:
                    problems.append("%s (%s) has been unreachable for %d checks" % (label, host, S["ftp_fail"][host]))
        if len(inst_by_host) < len(FTP_PROJECTORS) and not any(hs.get("folders") for hs in S["ftp"].values()):
            problems.append("cannot read any projector - lists not updated")
            alert_problems(S, [p for p in problems if is_major(p)], dry)
            send_minor(S, [p for p in problems if not is_major(p)], dry)
            _end_of_run(S, None, [], [p for p in problems if is_major(p)], [p for p in problems if not is_major(p)], started, dry)
            return S, None, problems

        # ---- decide ----
        plan = build_plan(S, rules, inst_by_host)
        in_use, in_use_err = fetch_in_use()
        plan["kept"] = protect_deletes(plan, in_use, in_use_err)
        if in_use is None and not in_use_err:
            problems.append("trailer keep rule is off (PLAYLIST_APP_URL and PLAYLIST_APP_TOKEN are blank)")
        elif in_use_err:
            problems.append("trailer keep rule: " + in_use_err)
        elif plan["kept"]:
            log("Keep rule: %d trailer(s) held back from the delete list (in a playlist)" % len(plan["kept"]))
        specials = special_rows(S)
        warnings_all = list(plan["warnings"]) + problems + ["%s data is from the last good scan" % l for l in stale]
        payload = build_payload(S, plan, specials, inst_by_host, warnings_all)
        h = payload_hash(payload)
        log("Plan: download %d (+%d waiting)  delete %d  review %d  auto-ignored %d  specials %d" % (
            len(plan["downloads"]) // 2, len(plan["waiting"]), len(plan["deletes"]), len(plan["review"]),
            len(plan["auto_ignored"]), len([s for s in specials if s["active"]])))

        # ---- push to the sheet ----
        if dry:
            log("(dry run: not pushing to the sheet or Discord)")
        elif SHEET_URL and SHEET_TOKEN:
            last = S.get("pushed_at")
            stale_beat = (not last) or (datetime.now() - datetime.fromisoformat(last)) > timedelta(hours=HEARTBEAT_HOURS)
            if h != S.get("pushed_hash") or stale_beat:
                try:
                    r = sheet_call("push", {"tabs": payload["tabs"], "specials": payload["specials"]})
                    S["pushed_hash"], S["pushed_at"] = h, datetime.now().isoformat(timespec="seconds")
                    S.setdefault("streak", {})["sheet"] = 0
                    RUN["sheet"] = "updated"
                    log("Sheet updated: %s" % r.get("summary", "ok"))
                except Exception as e:
                    RUN["sheet"] = "FAILED"
                    log("Sheet push FAILED: %s" % e)
                    n = S.setdefault("streak", {}).get("sheet", 0) + 1
                    S["streak"]["sheet"] = n
                    problems.append(("sheet update has failed %d checks in a row: %s" % (n, e)) if n >= 3
                                    else "sheet update failed: %s" % e)
            else:
                RUN["sheet"] = "unchanged"
                log("Sheet already up to date")
        else:
            problems.append("SHEET_URL / SHEET_TOKEN not set - sheet not updated")

        majors = [p for p in problems if is_major(p)]
        RUN["proj"] = "/".join(str(len(v)) for v in inst_by_host.values())
        send_alerts(S, plan, specials, majors, dry=dry)
        minors = [p for p in problems if not is_major(p)] + list(plan["warnings"])
        send_minor(S, minors, dry)
        daily_digest(S, plan, specials, majors, dry)
        _end_of_run(S, plan, specials, majors, minors, started, dry)
        return S, plan, problems
    finally:
        if not dry:
            save_state(S)
            if sys.exc_info()[0] is None:
                maj = [p for p in problems if is_major(p)]
                kuma("down" if maj else "up", " | ".join(maj) if maj else "OK")


def describe(plan):
    out = []
    out.append("\n=== TO DOWNLOAD (%d files) ===" % len(plan["downloads"]))
    out += ["  %s%s" % (d["name"], "   [%s]" % d["note"] if d["note"] else "") for d in sorted(plan["downloads"], key=lambda x: x["name"])]
    out.append("=== WAITING ON MATCHING FORMAT (%d) ===" % len(plan["waiting"]))
    out += ["  %s   [%s]" % (w["name"], w["note"]) for w in plan["waiting"]]
    out.append("=== TO DELETE (%d) ===" % len(plan["deletes"]))
    out += ["  %s   [%s]" % (n, r) for n, r in sorted(plan["deletes"].items())]
    out.append("=== DO NOT DELETE YET, IN A PLAYLIST (%d) ===" % len(plan.get("kept", {})))
    out += ["  %s   [%s | would be: %s]" % (n, h, w) for n, (w, h) in sorted(plan.get("kept", {}).items())]
    out.append("=== NEEDS REVIEW (%d) ===" % len(plan["review"]))
    out += ["  %s  (guess: %s)" % (s, v[1]) for s, v in sorted(plan["review"].items())]
    out.append("=== AUTO-IGNORED (%d) ===" % len(plan["auto_ignored"]))
    out += ["  %s  -> %s" % (s, v[1]) for s, v in sorted(plan["auto_ignored"].items())]
    return "\n".join(out)


# ===================================================================================================
#  --check
# ===================================================================================================
def run_check():
    ok = True

    def line(name, good, detail=""):
        nonlocal ok
        ok = ok and good
        print("%-22s %s  %s" % (name, "OK  " if good else "FAIL", detail))

    if ENV_INFO["error"]:
        line(".env file", False, "%s could not be read (%s)" % (ENV_INFO["path"], ENV_INFO["error"]))
    elif os.path.isfile(ENV_INFO["path"]):
        line(".env file", True, "%s (%d settings loaded%s)" % (ENV_INFO["path"], len(ENV_INFO["loaded"]),
             ", ignored: " + ", ".join(ENV_INFO["unknown"]) if ENV_INFO["unknown"] else ""))
    else:
        line(".env file", False, "not found at %s (run make_env.py, or the script uses the values written inside it)" % ENV_INFO["path"])

    if ADS_ENABLED:
        try:
            os.makedirs(ADS_TARGET_ROOT, exist_ok=True)
            probe = os.path.join(ADS_TARGET_ROOT, ".write_test")
            with open(probe, "w") as fh:
                fh.write("ok")
            os.remove(probe)
            line("Ads folder", True, ADS_TARGET_ROOT)
            if os.path.isdir(ADS_ARCHIVE_ROOT):
                line("Ads archive", True, ADS_ARCHIVE_ROOT)
            else:
                os.makedirs(ADS_ARCHIVE_ROOT, exist_ok=True)
                line("Ads archive", True, "created " + ADS_ARCHIVE_ROOT)
        except Exception as e:
            line("Ads folder", False, "%s (%s)" % (ADS_TARGET_ROOT, e))

    try:
        M = imaplib.IMAP4_SSL(IMAP_HOST, timeout=30)
        M.login(IMAP_USER, IMAP_PASSWORD)
        typ, d = M.select(IMAP_FOLDER, readonly=True)
        line("Gmail", True, "%s messages in %s" % (d[0].decode(), IMAP_FOLDER))
        M.logout()
    except Exception as e:
        line("Gmail", False, str(e))
    for label, host in FTP_PROJECTORS:
        try:
            with _ftp_open(host, 15) as ftp:
                n = len(_ftp_list(ftp, FTP_PATH))
            line("FTP " + label, True, "%s: %d entries at the top" % (host, n))
        except Exception as e:
            line("FTP " + label, False, "%s: %s" % (host, e))
    try:
        r = sheet_call("ping")
        line("Sheet endpoint", True, r.get("message", ""))
    except Exception as e:
        line("Sheet endpoint", False, str(e))
    d = tmdb_get("configuration")
    line("TMDB", bool(d), "" if d else "no answer (token wrong or no internet)")
    readers = [n for n, f in (("pdftotext", lambda: shutil.which("pdftotext")),
                              ("pdfplumber", lambda: __import__("pdfplumber")),
                              ("pypdf", lambda: __import__("pypdf"))) if _try(f)]
    line("PDF reader", bool(readers), ", ".join(readers) or "none. Run:  pip install pdfplumber")
    if DISCORD_WEBHOOK:
        discord("Trailer automation check: the MAIN channel works.")
        line("Discord main", True, "test message sent")
    else:
        line("Discord main", True, "(no webhook set, alerts off)")
    if LOG_WEBHOOK:
        discord_log("Trailer automation check: the LOG channel works.")
        line("Discord log", True, "test message sent")
    else:
        line("Discord log", True, "(no log webhook set, minor errors only go to the console)")
    if KUMA_PUSH_URL:
        try:
            code, body = _kuma_get("up", "check")
            good = code == 200 and '"ok":true' in body.replace(" ", "")
            line("Uptime Kuma", good, "HTTP %s  %s" % (code, "heartbeat accepted" if good else _kuma_hint(body)))
        except Exception as e:
            line("Uptime Kuma", False, str(e))
    else:
        line("Uptime Kuma", True, "(no push URL set)")
    if ADS_ENABLED:
        try:
            req = urllib.request.Request("https://portal.cielocinema.com/", headers={"User-Agent": "Mozilla/5.0"})
            with _open_cielo(req) as r:
                r.read(100)
            line("Cielo portal SSL", True, "certificate accepted")
        except urllib.error.HTTPError:
            line("Cielo portal SSL", True, "certificate accepted")
        except Exception as e:
            line("Cielo portal SSL", False, "%s  (try: pip install certifi, or see CIELO_SSL_VERIFY)" % e)
    print("\nEverything is ready." if ok else "\nFix the FAIL lines above, then run --check again.")
    return ok


def _try(f):
    try:
        return bool(f())
    except Exception:
        return False


CRASH_FILE = os.path.join(DATA, "last_crash.json")
LOCK_FILE = os.path.join(DATA, "trailer_automation.lock")


def acquire_lock():
    """Task Scheduler may start a run while the last one is still going. Only one runs, the other leaves quietly."""
    os.makedirs(DATA, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if (time.time() - os.path.getmtime(LOCK_FILE)) / 60.0 > LOCK_STALE_MINUTES:
                    os.remove(LOCK_FILE)          # a crashed run left it behind
                    continue
            except OSError:
                pass
            return False
    return False


def release_lock():
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


def rotate_log():
    try:
        if os.path.getsize(LOG_FILE) > 5 * 1024 * 1024:
            os.replace(LOG_FILE, LOG_FILE + ".1")
    except OSError:
        pass


def report_crash(e, tb):
    """A crash goes to the main channel once per distinct error per 6 hours (remembered in a file between runs)."""
    kuma("down", "run crashed: %s" % e)
    key, now = str(e)[:120], time.time()
    try:
        with open(CRASH_FILE) as f:
            last = json.load(f)
    except Exception:
        last = {}
    if last.get("key") != key or now - last.get("at", 0) > 6 * 3600:
        try:
            with open(CRASH_FILE, "w") as f:
                json.dump({"key": key, "at": now}, f)
        except OSError:
            pass
        discord("**Heads up**\n- the run crashed: %s\n(details are in the log channel)" % key)
        discord_log("Run crashed:\n```\n%s\n```" % tb[-1500:])


def dump_installed(path=None):
    """Fresh read of both projectors (nothing saved to state) into a CSV: every package with run length and FFEC."""
    import csv
    path = path or os.path.join(DATA, "installed_dump.csv")
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["projector", "name", "kind", "issue", "dur_sec", "ffec_sec", "cpl_id", "folder"])
        for label, host in FTP_PROJECTORS:
            for r in ftp_scan_host(label, host, {}):
                w.writerow([label, r["name"], r["kind"], r["issue"], r.get("dur") or "", r.get("ffec") or "", r["cpl"], r["folder"]])
                n += 1
    print("Wrote %d rows to %s" % (n, path))


def main():
    global TODAY_OVERRIDE
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="same as no flag (one run, then exit)")
    ap.add_argument("--loop", action="store_true", help="old always-on mode: run every POLL_MINUTES until stopped")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--dump-installed", action="store_true", help="read both projectors fresh and write installed_dump.csv (names, run lengths, FFEC)")
    ap.add_argument("--pdf", help="read one bulletin PDF now (for testing) and show what it found")
    args = ap.parse_args()

    if args.reset:
        for f in (STATE_FILE,):
            if os.path.exists(f):
                os.remove(f)
        print("State cleared.")
        return 0
    if args.check:
        return 0 if run_check() else 1
    if args.dump_installed:
        dump_installed()
        return 0
    if args.pdf:
        b = parse_bulletin(pdf_to_text(args.pdf))
        print("%s: %d catalog rows, %d slugs, %d on the remove list, %d new-content rows" % (
            b["week"], len(b["titles"]), len(b["slugs"]) if "slugs" in b else len(b["slug_titles"]), len(b["remove"]), len(b["new"])))
        for n in b["remove"][:5]:
            print("  remove:", n)
        return 0

    if args.dry_run:
        S, plan, problems = run_cycle(dry=True)
        if plan:
            print(describe(plan))
        for p in problems:
            print("PROBLEM:", p)
        return 0

    if args.loop:
        log("Trailer automation started in loop mode (every %d min). Ctrl+C to stop." % POLL_MINUTES)
        while True:
            try:
                run_cycle()
            except Exception as e:
                tb = traceback.format_exc()
                log("Run crashed:\n" + tb)
                report_crash(e, tb)
            time.sleep(POLL_MINUTES * 60)

    # default: one run, meant to be started by Task Scheduler every 15 minutes
    rotate_log()
    if not acquire_lock():
        log("The previous run is still going. Skipping this one.")
        return 0
    try:
        S, plan, problems = run_cycle()
        for p in problems:
            log("PROBLEM: " + p)
        return 0
    except Exception as e:
        tb = traceback.format_exc()
        log("Run crashed:\n" + tb)
        report_crash(e, tb)
        return 1
    finally:
        release_lock()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
