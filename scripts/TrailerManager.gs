/**
 * @OnlyCurrentDoc
 *
 * Trailer Manager (automation edition)
 *
 * The brains now live in trailer_automation.py on the theater PC. It reads Deluxe's emails + the TrailMix bulletin,
 * scans both projectors, decides what to download/delete, and PUSHES the result here. This script is the sheet side:
 *   - doPost(): the web endpoint the PC talks to (needs the secret below)
 *   - writes the Available / Currently Downloaded / Download / Delete / Needs Review / Auto-Ignored / Special Ads / Status tabs
 *   - rebuilds the Printout (trailers grouped by rating) and keeps the Special Advertisements block current
 *   - keeps the "Slug Rules" tab (KEEP / IGNORE / ALLOW / MAP) that the PC reads every run
 *
 * ONE-TIME SETUP
 *   1. Put a long random phrase in WEB_SECRET below (and the same phrase in SHEET_TOKEN in trailer_automation.py).
 *   2. Deploy > New deployment > type "Web app". Execute as: Me.  Who has access: Anyone.  Copy the URL into SHEET_URL.
 *   3. Run "Trailer Manager > Set up tabs" once (creates Slug Rules and moves your Blacklist / TMDB Overrides into it).
 *   After any later edit to this script: Deploy > Manage deployments > pencil > Version: New version > Deploy.
 */

// ===== CONFIGURATION (all in one spot) =====
const WEB_SECRET = "";

// Sheet names
const AVAILABLE_SHEET = "Available";
const DOWNLOADED_SHEET = "Currently Downloaded";
const DOWNLOAD_SHEET = "Download";
const DELETE_SHEET = "Delete";
const PRINTOUT_SHEET = "Printout";
const NEEDS_REVIEW_SHEET = "Needs Review";
const SLUG_RULES_SHEET = "Slug Rules";
const AUTO_IGNORED_SHEET = "Auto-Ignored";
const SPECIAL_ADS_SHEET = "Special Ads";
const STATUS_SHEET = "Status";
// old tabs. Still read once to fill Slug Rules, then left alone.
const BLACKLIST_SHEET = "Blacklist";
const TMDB_OVERRIDES_SHEET = "TMDB Overrides";

// Column names in "Currently Downloaded"
const COL_FLAT_DCP = "Projector 1";
const COL_SCOPE_DCP = "Projector 2";
const COL_FORMATTED_NAME = "Formatted Name";
const COL_RELEASE_DATE = "Release Date";
const COL_RATING = "Rating";
const COL_FILM_NAME = "Film Name";
const COL_TMDB_ID = "TMDB ID";

const PRINTOUT_MARKER_TEXT = "EXCLUDE EVERYTHING BELOW THIS LINE";
const PRINTOUT_BLANK_ROWS_BEFORE_MARKER = 2;   // blank rows kept between the trailer grid and the marker line
const SPECIAL_HEADER_TEXT = "Special Advertisements";   // header of the special-ads table under the marker

// ===== STANDARD FORMATTING =====
const STANDARD_FONT_FAMILY = "Calibri";
const STANDARD_FONT_SIZE = 10;
const STANDARD_FONT_COLOR = "#000000";

// Sheets that have a real header row worth keeping bold.
const SHEET_HEADER_ROWS = {
  [AVAILABLE_SHEET]: 1,
  [DOWNLOADED_SHEET]: 1,
  [NEEDS_REVIEW_SHEET]: 1,
  [SLUG_RULES_SHEET]: 1,
  [AUTO_IGNORED_SHEET]: 1,
  [SPECIAL_ADS_SHEET]: 1,
  [STATUS_SHEET]: 1,
  [TMDB_OVERRIDES_SHEET]: 1,
  [PRINTOUT_SHEET]: 1
};

// =============================================
// MENU
// =============================================
function onOpen() {
  SpreadsheetApp.getUi().createMenu('Trailer Manager')
      .addItem('Rebuild Printout', 'generatePrintout')
      .addItem('Standardize Formatting', 'formatAllSheets')
      .addSeparator()
      .addItem('Set up tabs (Slug Rules)', 'setupTabs')
      .addItem('Show automation status', 'showStatus')
      .addToUi();
}

function runStep_(stepFn) {
  const ui = SpreadsheetApp.getUi();
  try { ui.alert(stepFn()); } catch (e) { Logger.log(e); ui.alert('An error occurred: ' + e.message); }
}
function generatePrintout() { runStep_(doGeneratePrintout_); }
function formatAllSheets() { runStep_(doFormatAllSheets_); }

function setupTabs() {
  runStep_(function () {
    const n = getRules_([]).length;
    return 'Slug Rules is ready (' + n + ' rules).\n\nActions you can use in column B:\n' +
           '  KEEP    never touch this trailer (installer test trailers)\n' +
           '  IGNORE  never download this slug\n' +
           '  ALLOW   download it even though the auto-ignore rules would skip it\n' +
           '  MAP     use this TMDB ID (column C) and/or release date (column D)';
  });
}

function showStatus() {
  runStep_(function () {
    const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(STATUS_SHEET);
    if (!sh) return 'No Status tab yet. The theater PC has not pushed anything.';
    return sh.getDataRange().getValues().map(function (r) { return r[0] + ': ' + r[1]; }).join('\n');
  });
}

// =============================================
// WEB ENDPOINT (called by trailer_automation.py)
// =============================================
function doGet() {
  return ContentService.createTextOutput('Trailer Manager endpoint is up. (POST only. Nothing to see here.)');
}

function doPost(e) {
  let out;
  const lock = LockService.getScriptLock();
  try {
    lock.waitLock(30000);
    const req = JSON.parse(e.postData.contents);
    if (!WEB_SECRET || WEB_SECRET.indexOf('CHANGE-ME') === 0) {
      out = { ok: false, error: 'WEB_SECRET has not been changed in the script' };
    } else if (!req || req.token !== WEB_SECRET) {
      out = { ok: false, error: 'bad token' };
    } else if (req.action === 'ping') {
      out = { ok: true, message: 'pong from ' + SpreadsheetApp.getActiveSpreadsheet().getName() };
    } else if (req.action === 'rules') {
      out = { ok: true, rules: getRules_(req.seed || []) };
    } else if (req.action === 'push') {
      out = pushData_(req);
    } else {
      out = { ok: false, error: 'unknown action' };
    }
  } catch (err) {
    out = { ok: false, error: String(err && err.message ? err.message : err) };
  } finally {
    try { lock.releaseLock(); } catch (x) {}
  }
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
}

// ---------------------------------------------
// Slug Rules
// ---------------------------------------------
function cellIso_(ss, v) {
  if (v instanceof Date && !isNaN(v)) return Utilities.formatDate(v, ss.getSpreadsheetTimeZone(), 'yyyy-MM-dd');
  return (v === null || v === undefined) ? '' : String(v).trim();
}

function getRules_(seed) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = getOrCreateSheet(ss, SLUG_RULES_SHEET, ["Slug", "Action", "TMDB ID", "Release Date", "Note"]);
  const props = PropertiesService.getDocumentProperties();
  sh.getRange(1, 1, sh.getMaxRows(), 1).setNumberFormat('@');      // slugs must stay text (NOVEMBER1963 would become a date)

  if (!props.getProperty('RULES_MIGRATED_V1')) {
    const present = {};
    sh.getDataRange().getValues().slice(1).forEach(function (r) { if (r[0]) present[String(r[0]).trim().toUpperCase()] = true; });
    const add = [];
    function put(slug, action, tmdb, note) {
      const k = String(slug).trim().toUpperCase();
      if (!k || present[k]) return;
      present[k] = true;
      add.push([String(slug).trim(), action, tmdb || '', '', note]);
    }
    (seed || []).forEach(function (s) { put(s, 'KEEP', '', 'installer test trailer'); });
    const bl = ss.getSheetByName(BLACKLIST_SHEET);
    if (bl) bl.getRange(1, 1, Math.max(bl.getLastRow(), 1), 1).getValues().forEach(function (r) {
      if (r[0] && typeof r[0] === 'string') put(r[0], 'IGNORE', '', 'from old Blacklist tab');
    });
    const ov = ss.getSheetByName(TMDB_OVERRIDES_SHEET);
    if (ov) ov.getDataRange().getValues().slice(1).forEach(function (r) {
      if (r[0] && r[1]) put(r[0], 'MAP', String(r[1]).replace(/\.0$/, ''), 'from old TMDB Overrides tab');
    });
    if (add.length) {
      const start = Math.max(sh.getLastRow(), 1) + 1;
      ensureSize_(sh, start + add.length - 1, 5);
      sh.getRange(start, 1, add.length, 5).setValues(add);
    }
    props.setProperty('RULES_MIGRATED_V1', 'yes');
    applyStandardFont(sh);
  }

  const out = [];
  sh.getDataRange().getValues().slice(1).forEach(function (r) {
    const slug = r[0] ? String(r[0]).trim() : '';
    if (!slug) return;
    out.push({ slug: slug, action: String(r[1] || '').trim().toUpperCase(),
               tmdb: String(r[2] === null || r[2] === undefined ? '' : r[2]).replace(/\.0$/, '').trim(),
               release: cellIso_(ss, r[3]), note: String(r[4] || '') });
  });
  return out;
}

// ---------------------------------------------
// Writing the tabs
// ---------------------------------------------
function ensureSize_(sh, rows, cols) {
  if (sh.getMaxRows() < rows) sh.insertRowsAfter(sh.getMaxRows(), rows - sh.getMaxRows());
  if (sh.getMaxColumns() < cols) sh.insertColumnsAfter(sh.getMaxColumns(), cols - sh.getMaxColumns());
}

function pushData_(req) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const wrote = [];
  (req.tabs || []).forEach(function (tab) {
    if (tab.mode === 'columns') writeColumnsTab_(ss, tab); else writeReplaceTab_(ss, tab);
    wrote.push(tab.name + '=' + tab.rows.length);
  });
  const notes = [];
  [doGeneratePrintout_, function () { return syncSpecialAds_(ss, req.specials || []); }, doFormatAllSheets_].forEach(function (fn) {
    try { notes.push(fn()); } catch (e) { Logger.log(e); notes.push('FAILED: ' + e.message); }
  });
  return { ok: true, summary: wrote.join(', '), notes: notes };
}

/** Clears the tab and writes headers + rows. Used for every tab except Currently Downloaded. */
function writeReplaceTab_(ss, tab) {
  let sh = ss.getSheetByName(tab.name);
  const isNew = !sh;
  if (!sh) sh = ss.insertSheet(tab.name);
  sh.clear();
  const rows = [];
  if (tab.headers && tab.headers.length) rows.push(tab.headers);
  tab.rows.forEach(function (r) { rows.push(r); });
  if (rows.length === 0) return;
  const width = Math.max.apply(null, rows.map(function (r) { return r.length; }));
  const grid = rows.map(function (r) { const c = r.slice(); while (c.length < width) c.push(''); return c; });
  ensureSize_(sh, grid.length, width);
  // Text cells are written as plain text so Sheets cannot turn a name like NOVEMBER1963 into a date.
  // Real dates (2026-11-16) and numbers are left alone.
  const fmts = grid.map(function (r) {
    return r.map(function (v) {
      return (typeof v === 'string' && v !== '' && !/^\d{4}-\d{2}-\d{2}$/.test(v)) ? '@' : 'General';
    });
  });
  const rng = sh.getRange(1, 1, grid.length, width);
  rng.setNumberFormats(fmts);
  rng.setValues(grid);
  if (isNew) sh.autoResizeColumns(1, width);
}

/** Fills only the named columns of Currently Downloaded (from row 2 down). Everything else on the sheet is left alone. */
function writeColumnsTab_(ss, tab) {
  const sh = ss.getSheetByName(tab.name);
  if (!sh) throw new Error("Missing sheet '" + tab.name + "'");
  let lastCol = Math.max(sh.getLastColumn(), 1);
  const headers = sh.getRange(1, 1, 1, lastCol).getValues()[0];
  ensureSize_(sh, tab.rows.length + 1, lastCol);
  tab.headers.forEach(function (h, j) {
    let idx = headers.indexOf(h);
    if (idx < 0) {
      lastCol++;
      ensureSize_(sh, 1, lastCol);
      sh.getRange(1, lastCol).setValue(h);
      headers.push(h);
      idx = headers.length - 1;
    }
    const col = idx + 1;
    if (sh.getMaxRows() > 1) sh.getRange(2, col, sh.getMaxRows() - 1, 1).clearContent();
    if (tab.rows.length) {
      const vals = tab.rows.map(function (r) {
        let v = r[j];
        if (h === COL_RELEASE_DATE && typeof v === 'string' && /^\d{4}-\d{2}-\d{2}/.test(v)) {
          v = new Date(+v.substr(0, 4), +v.substr(5, 2) - 1, +v.substr(8, 2));
        }
        return [v === null || v === undefined ? '' : v];
      });
      sh.getRange(2, col, vals.length, 1).setValues(vals);
      if (h === COL_RELEASE_DATE) sh.getRange(2, col, vals.length, 1).setNumberFormat('M/d/yyyy');
    }
  });
}

// ---------------------------------------------
// Special Advertisements block on the Printout (below the marker)
// ---------------------------------------------
/**
 * Adds a row for every current special ad that is not already there, and removes rows this script added once the
 * campaign has ended. Rows it adds carry a cell note starting with "auto:" . Rows you typed yourself are never touched.
 * A new campaign is skipped if one of your own rows already mentions all of its asset numbers.
 */
function syncSpecialAds_(ss, specials) {
  const sh = ss.getSheetByName(PRINTOUT_SHEET);
  if (!sh) return 'Special ads: no Printout sheet';
  const last = sh.getLastRow();
  const vals = sh.getRange(1, 1, last, 4).getValues();
  let header = -1;
  for (let i = 0; i < vals.length; i++) {
    if (String(vals[i][0]).trim() === SPECIAL_HEADER_TEXT) { header = i + 1; break; }
  }
  if (header < 0) return 'Special ads: could not find a "' + SPECIAL_HEADER_TEXT + '" header on the Printout';

  let end = header;
  for (let r = header + 1; r <= last; r++) {
    if (vals[r - 1].every(function (c) { return String(c).trim() === ''; })) break;
    end = r;
  }
  const active = {};
  specials.forEach(function (s) { if (s.active) active[s.key] = s; });

  const notes = end > header ? sh.getRange(header + 1, 1, end - header, 1).getNotes() : [];
  const managed = {};
  const manualText = [];
  const toDelete = [];
  for (let k = 0; k < notes.length; k++) {
    const note = notes[k][0] || '';
    if (note.indexOf('auto:') === 0) {
      const key = note.substring(5);
      managed[key] = true;
      if (!active[key]) toDelete.push(header + 1 + k);
    } else {
      manualText.push(String(vals[header + k][0]));
    }
  }
  toDelete.sort(function (a, b) { return b - a; }).forEach(function (r) { sh.deleteRow(r); });
  end -= toDelete.length;

  const joined = manualText.join(' | ');
  let added = 0;
  Object.keys(active).forEach(function (key) {
    if (managed[key]) return;
    const s = active[key];
    if (s.match && s.match.length && s.match.every(function (m) { return joined.indexOf(m) >= 0; })) return;
    const r = end + 1;
    ensureSize_(sh, r, 4);
    if (end > header) sh.getRange(end, 1, 1, 4).copyTo(sh.getRange(r, 1, 1, 4), SpreadsheetApp.CopyPasteType.PASTE_FORMAT);
    sh.getRange(r, 1, 1, 4).setValues([[s.label, s.dates, s.position, s.ratings]]).setFontWeight('normal');
    sh.getRange(r, 1).setNote('auto:' + key);
    end = r;
    added++;
  });
  return 'Special ads: ' + added + ' added, ' + toDelete.length + ' removed.';
}

// =============================================
// STEP 3: GENERATE PRINTOUT
// =============================================
function doGeneratePrintout_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const downloadedSheet = ss.getSheetByName(DOWNLOADED_SHEET);
  const printoutSheet = ss.getSheetByName(PRINTOUT_SHEET);

  if (!downloadedSheet || !printoutSheet) {
    throw new Error('Could not find "Currently Downloaded" or "Printout" sheet.');
  }

  // Find the marker row that protects everything below it (ads/notes block).
  const colAValues = printoutSheet.getRange(1, 1, printoutSheet.getMaxRows(), 1).getValues();
  let markerRow = null;
  for (let i = 0; i < colAValues.length; i++) {
    if ((colAValues[i][0] || '').toString().trim() === PRINTOUT_MARKER_TEXT) {
      markerRow = i + 1;
      break;
    }
  }

  let markerWasMissing = false;
  if (!markerRow) {
    // No marker found yet — don't touch anything. Place it far below any existing content instead
    // of guessing, so we never risk overwriting whatever's already down there.
    const lastRowWithContent = printoutSheet.getLastRow();
    markerRow = lastRowWithContent + 5;
    printoutSheet.getRange(markerRow, 1).setValue(PRINTOUT_MARKER_TEXT);
    markerWasMissing = true;
    Logger.log(`No marker found. Placed one at row ${markerRow}. Move your ads block below it if needed.`);
  }

  const data = downloadedSheet.getDataRange().getValues();
  const headers = data[0];
  const colMap = getHeaderMap(headers);

  const requiredCols = [COL_FORMATTED_NAME, COL_RELEASE_DATE, COL_RATING];
  for (const col of requiredCols) {
    if (colMap[col] === undefined) throw new Error(`Missing required column in 'Currently Downloaded': ${col}`);
  }

  // Dedupe Flat/Scope rows down to one entry per trailer edition
  let seen = new Set();
  let entries = [];
  for (let i = 1; i < data.length; i++) {
    const row = data[i];
    const name = row[colMap[COL_FORMATTED_NAME]];
    if (!name || seen.has(name)) continue;
    seen.add(name);

    const dateVal = row[colMap[COL_RELEASE_DATE]];
    let parsedDate = null;
    if (dateVal instanceof Date && !isNaN(dateVal)) {
      parsedDate = dateVal;
    } else if (dateVal) {
      const d = new Date(dateVal);
      if (!isNaN(d)) parsedDate = d;
    }

    const rating = (row[colMap[COL_RATING]] || '').toString().trim();
    entries.push({ name: name, date: parsedDate, rating: rating });
  }
  Logger.log(`Printout: ${entries.length} unique trailer editions found.`);

  const RATING_ORDER = ['G', 'PG', 'PG-13', 'R', ''];
  let buckets = { 'G': [], 'PG': [], 'PG-13': [], 'R': [], '': [] };
  entries.forEach(e => {
    const key = buckets.hasOwnProperty(e.rating) ? e.rating : '';
    buckets[key].push(e);
  });

  RATING_ORDER.forEach(key => {
    buckets[key].sort((a, b) => {
      const ta = a.date ? a.date.getTime() : Infinity;
      const tb = b.date ? b.date.getTime() : Infinity;
      if (ta !== tb) return ta - tb;
      return a.name.localeCompare(b.name);
    });
  });

  // Build the grid rows in memory first so we know exactly how many rows we need.
  let gridRows = [['Trailer', 'Release Date', 'Rating', null, 'Trailer', 'Release Date', 'Rating']];
  RATING_ORDER.forEach(key => {
    const list = buckets[key];
    if (list.length === 0) return;

    const leftCount = Math.ceil(list.length / 2);
    const left = list.slice(0, leftCount);
    const right = list.slice(leftCount);

    for (let i = 0; i < leftCount; i++) {
      const l = left[i];
      const r = right[i]; // may be undefined
      gridRows.push([
        l.name, l.date, l.rating || null, null,
        r ? r.name : null, r ? r.date : null, (r && r.rating) ? r.rating : null
      ]);
    }
    gridRows.push([null, null, null, null, null, null, null]); // blank separator row
  });
  if (gridRows.length > 1 && gridRows[gridRows.length - 1].every(v => v === null)) {
    gridRows.pop(); // don't need a trailing separator right before our own blank buffer
  }

  const neededRows = gridRows.length; // includes header row
  const desiredMarkerRow = neededRows + PRINTOUT_BLANK_ROWS_BEFORE_MARKER + 1;

  // Keep the gap above the marker tight: add rows when the list grows, remove them when it shrinks.
  // Only rows ABOVE the marker are ever removed (they are cleared and rewritten anyway).
  if (!markerWasMissing && markerRow < desiredMarkerRow) {
    const rowsToInsert = desiredMarkerRow - markerRow;
    printoutSheet.insertRowsBefore(markerRow, rowsToInsert);
    markerRow = desiredMarkerRow;
    Logger.log(`Grid grew — inserted ${rowsToInsert} rows to push the notes block down and keep it intact.`);
  } else if (!markerWasMissing && markerRow > desiredMarkerRow) {
    const rowsToRemove = markerRow - desiredMarkerRow;
    printoutSheet.deleteRows(desiredMarkerRow, rowsToRemove);
    markerRow = desiredMarkerRow;
    Logger.log(`Grid shrank — removed ${rowsToRemove} empty rows above the marker.`);
  }

  // Only clear the region above the marker — never touches the marker row or anything below it.
  printoutSheet.getRange(1, 1, markerRow - 1, 7).clear();
  printoutSheet.getRange(1, 1, markerRow - 1, 7).setBorder(false, false, false, false, false, false);
  printoutSheet.getRange(1, 1, gridRows.length, 7).setValues(gridRows);
  printoutSheet.getRange(2, 2, gridRows.length - 1, 1).setNumberFormat('M/d/yyyy');
  printoutSheet.getRange(2, 6, gridRows.length - 1, 1).setNumberFormat('M/d/yyyy');

  // Standardize font above the marker only; row 1 (the column titles) stays bold.
  applyPrintoutFormatting(printoutSheet, markerRow);

  Logger.log(`Printout generated. Grid uses rows 1-${gridRows.length}. Marker (and notes below it) protected at row ${markerRow}.`);

  if (markerWasMissing) {
    return `STEP 3: Printout generated, ${entries.length} trailers listed.\nNo marker row was found, so one was added at row ${markerRow}. Move your Coke/Special Advertisements block to start right below that marker row. It'll be protected from here on out.`;
  }
  return `STEP 3: Printout generated, ${entries.length} trailers listed. Your notes block below the marker was left untouched.`;
}

// =============================================
// STANDARDIZE FORMATTING
// =============================================
function doFormatAllSheets_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheetNames = [AVAILABLE_SHEET, DOWNLOADED_SHEET, DOWNLOAD_SHEET, DELETE_SHEET, NEEDS_REVIEW_SHEET,
                      SLUG_RULES_SHEET, AUTO_IGNORED_SHEET, SPECIAL_ADS_SHEET, STATUS_SHEET];
  sheetNames.forEach(function (name) {
    const sheet = ss.getSheetByName(name);
    if (!sheet) return;
    let bold = [];
    if (name === DOWNLOAD_SHEET) {
      const colA = sheet.getRange(1, 1, sheet.getMaxRows(), 1).getValues();
      for (let i = 0; i < colA.length; i++) {
        if (String(colA[i][0]).trim() === 'Waiting on Matching Format:') bold.push(i + 1);
      }
    }
    applyStandardFont(sheet, bold);
  });

  // Printout is handled separately so we respect the marker boundary.
  const printoutSheet = ss.getSheetByName(PRINTOUT_SHEET);
  if (printoutSheet) {
    const colAValues = printoutSheet.getRange(1, 1, printoutSheet.getMaxRows(), 1).getValues();
    let markerRow = printoutSheet.getMaxRows(); // fallback: format the whole thing if no marker exists yet
    for (let i = 0; i < colAValues.length; i++) {
      if ((colAValues[i][0] || '').toString().trim() === PRINTOUT_MARKER_TEXT) {
        markerRow = i + 1;
        break;
      }
    }
    applyPrintoutFormatting(printoutSheet, markerRow);
  }
  return "Formatting standardized (font 10, black, headers bold).";
}

// =============================================
// HELPERS
// =============================================
function getHeaderMap(headers) {
  let map = {};
  headers.forEach((header, i) => {
    map[header] = i;
  });
  return map;
}

function getOrCreateSheet(ss, name, headers) {
  let sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  }
  return sheet;
}

/**
 * Resets a sheet to the standard font (Carlito 10, black, not bold/italic), then re-bolds
 * its header row (per SHEET_HEADER_ROWS) plus any extra rows passed in (e.g. a dynamic label).
 */
function applyStandardFont(sheet, extraBoldRows) {
  const maxRows = sheet.getMaxRows();
  const maxCols = sheet.getMaxColumns();
  if (maxRows === 0 || maxCols === 0) return;

  sheet.getRange(1, 1, maxRows, maxCols)
       .setFontFamily(STANDARD_FONT_FAMILY)
       .setFontSize(STANDARD_FONT_SIZE)
       .setFontColor(STANDARD_FONT_COLOR)
       .setFontStyle("normal")
       .setFontWeight("normal");

  const headerRow = SHEET_HEADER_ROWS[sheet.getName()];
  if (headerRow) {
    sheet.getRange(headerRow, 1, 1, maxCols).setFontWeight("bold");
  }
  if (extraBoldRows && extraBoldRows.length > 0) {
    extraBoldRows.forEach(r => {
      sheet.getRange(r, 1, 1, maxCols).setFontWeight("bold");
    });
  }
}

/**
 * Same idea as applyStandardFont, but scoped to only the region above the Printout marker row,
 * so the manually-typed ad/notes block below it is never touched.
 */
function applyPrintoutFormatting(sheet, markerRow) {
  const maxCols = Math.max(sheet.getMaxColumns(), 7);
  const regionRows = markerRow - 1;
  if (regionRows <= 0) return;

  sheet.getRange(1, 1, regionRows, maxCols)
       .setFontFamily(STANDARD_FONT_FAMILY)
       .setFontSize(STANDARD_FONT_SIZE)
       .setFontColor(STANDARD_FONT_COLOR)
       .setFontStyle("normal")
       .setFontWeight("normal");

  sheet.getRange(1, 1, 1, maxCols).setFontWeight("bold");

  // Layout: roomy trailer columns, centered dates and ratings, a shaded header, and borders only where there is data.
  // Special Advertisements sits under the same columns (Dates in B, Position in C, Ratings in D), so B, C and D are
  // sized for it: D is wide on purpose so "G,PG,PG-13" fits inside its own box.
  // The trailer columns (A and E) grow to fit the longest title so nothing is cut off. No wrapping.
  let used = 1;
  const colA = sheet.getRange(1, 1, regionRows, 1).getValues();
  const colE = sheet.getRange(1, 5, regionRows, 1).getValues();
  let longest = 0;
  for (let r = 0; r < regionRows; r++) {
    if (colA[r][0] !== '' || colE[r][0] !== '') used = r + 1;
    longest = Math.max(longest, String(colA[r][0]).length, String(colE[r][0]).length);
  }
  const trailerW = Math.min(420, Math.max(245, Math.round(longest * 6.4) + 16));
  const widths = [trailerW, 95, 90, 100, trailerW, 95, 70];
  widths.forEach(function (w, i) { sheet.setColumnWidth(i + 1, w); });
  if (used > 1) sheet.getRange(2, 1, used - 1, 7).setWrap(false).setVerticalAlignment('middle');
  [[1, 3], [5, 3]].forEach(function (blk) {
    const header = sheet.getRange(1, blk[0], 1, blk[1]);
    header.setBackground('#e8eaed').setHorizontalAlignment('left');
    if (used > 1) {
      sheet.getRange(2, blk[0] + 1, used - 1, 2).setHorizontalAlignment('center');
      for (let r = 2; r <= used; r++) {
        if ((blk[0] === 1 ? colA : colE)[r - 1][0] !== '') {
          sheet.getRange(r, blk[0], 1, blk[1]).setBorder(true, true, true, true, false, false, '#bdbdbd', SpreadsheetApp.BorderStyle.SOLID);
        }
      }
    }
    header.setBorder(true, true, true, true, true, false, '#9e9e9e', SpreadsheetApp.BorderStyle.SOLID);
  });
}