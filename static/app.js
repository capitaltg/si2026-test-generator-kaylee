/* Fixtura front end (vanilla JS).
 *
 * This is the "behaviour" layer. The look comes from styles.css; the data comes
 * from our FastAPI backend. Nothing here generates data itself: it collects what
 * the user builds, POSTs it to the engine, and renders whatever comes back.
 *
 * State lives in one plain object. After any change we re-render the affected
 * part of the page and (for data-affecting changes) ask the server to
 * regenerate. This mirrors how Toby's prototype worked, minus its framework. */

const state = {
  tableName: "users",
  seed: 42,
  rowCount: 25,
  inputTab: "builder",
  outputTab: "table",
  pdfMode: "table", // PDF flavour: "table" (one big table) or "docs" (page per row)
  // Document-PDF layout: a title, per-field placement (header/body/off), the
  // document style (sheet | letter | form), and a prose template with {field}
  // placeholders used by the letter style.
  pdfDoc: { title: "", placement: {}, style: "sheet", template: "" },
  nextId: 0,
  fields: [],
  groups: [], // grouped type menu from the API
  labels: {}, // type -> human label, for the table header
  rows: [], // last generated preview rows
  exportCache: {}, // format -> text, cleared on each generate
};

const PREVIEW_CAP = 200; // never render more than this many rows on screen
const $ = (sel) => document.querySelector(sel);

// Accent colour themes: name -> [--accent, --accent-dark]. The whole palette is
// driven by these two CSS variables, so swapping them re-colours everything.
// name -> [accent, accent-dark, header]. The header bar recolours with the
// theme too, so switching themes changes the whole chrome, not just the accent.
const ACCENTS = {
  "Federal Blue": ["#005ea2", "#1a4480", "#162e51"],
  Slate: ["#40536b", "#26344a", "#1c2735"],
  Teal: ["#0f7b8a", "#0b5966", "#08414b"],
  Ink: ["#2c2c2c", "#000000", "#111111"],
};

function applyAccent(accent, accentDark, header) {
  const root = document.documentElement.style;
  root.setProperty("--accent", accent);
  root.setProperty("--accent-dark", accentDark);
  root.setProperty("--header-bg", header || accentDark);
}

// Darken a #rrggbb colour by a factor (0..1). Lets us derive a matching darker
// accent and header shade from any custom colour the user picks.
function darken(hex, factor) {
  const num = parseInt(hex.slice(1), 16);
  const r = Math.round(((num >> 16) & 255) * factor);
  const g = Math.round(((num >> 8) & 255) * factor);
  const b = Math.round((num & 255) * factor);
  return "#" + [r, g, b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

function applyCustom(hex) {
  applyAccent(hex, darken(hex, 0.7), darken(hex, 0.45));
}

// --- helpers ---------------------------------------------------------------

function uid() {
  return "f" + state.nextId++;
}

function escapeHtml(v) {
  return String(v)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function postJSON(path, body) {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      detail = (await resp.json()).detail || detail;
    } catch (e) {}
    throw new Error(detail);
  }
  return resp;
}

let toastTimer = null;
function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2400);
}

// Which option inputs a given field type shows: [key, inputType, placeholder].
function optionSpecs(type) {
  if (["int", "float", "price", "money", "age"].includes(type))
    return [["min", "number", "min"], ["max", "number", "max"]];
  if (type === "enum") return [["values", "text", "a, b, c"]];
  if (type === "constant") return [["value", "text", "fixed value"]];
  if (type === "pattern") return [["pattern", "text", "AB-####"]];
  if (["sequence", "autoIncrement"].includes(type))
    return [["prefix", "text", "GS-"], ["start", "number", "1"]];
  if (["date", "datetime"].includes(type))
    return [["start", "date", ""], ["end", "date", ""]];
  return [];
}

// --- rendering -------------------------------------------------------------

function optionsHtml() {
  // The grouped <optgroup> markup for a type dropdown, built once from the API.
  return state.groups
    .map(
      (g) =>
        `<optgroup label="${escapeHtml(g.name)}">` +
        g.types
          .map(
            (t) =>
              `<option value="${escapeHtml(t.value)}">${escapeHtml(t.label)}</option>`
          )
          .join("") +
        `</optgroup>`
    )
    .join("");
}

function fieldCardHtml(field) {
  const opts = optionSpecs(field.type)
    .map(([key, inputType, ph]) => {
      const val = field.opts[key] ?? "";
      const wide = ["values", "value", "pattern"].includes(key);
      return (
        `<label class="opt ${wide ? "opt-wide" : ""}">${key}` +
        `<input data-id="${field.id}" data-opt="${key}" type="${inputType}" ` +
        `class="${wide ? "" : "opt-num"}" value="${escapeHtml(val)}" ` +
        `placeholder="${ph}" /></label>`
      );
    })
    .join("");
  return (
    `<div class="field-card">` +
    `<div class="field-top">` +
    `<input class="field-name" data-id="${field.id}" data-role="name" ` +
    `value="${escapeHtml(field.name)}" spellcheck="false" />` +
    `<select class="field-type" data-id="${field.id}" data-role="type">${optionsHtml()}</select>` +
    `<button class="icon-btn" data-id="${field.id}" data-action="remove" title="Remove">✕</button>` +
    `</div>` +
    `<div class="field-opts">${opts}` +
    `<label class="opt null" title="Percent of rows that are null">null %` +
    `<input class="opt-num" data-id="${field.id}" data-role="nullpct" ` +
    `type="number" value="${field.nullPct || 0}" /></label>` +
    `</div></div>`
  );
}

function renderInputTabs() {
  const tabs = ["builder", "ddl", "describe", "csv", "json"];
  const labels = { builder: "Builder", ddl: "DDL", describe: "Describe", csv: "CSV", json: "JSON" };
  $("#inputTabs").innerHTML = tabs
    .map(
      (t) =>
        `<button class="tab ${state.inputTab === t ? "active" : ""}" data-tab="${t}">${labels[t]}</button>`
    )
    .join("");
}

function renderInputPanel() {
  const panel = $("#inputPanel");
  if (state.inputTab !== "builder") {
    panel.innerHTML = `<div class="placeholder">The <b>${state.inputTab.toUpperCase()}</b> input method arrives in the next update (Phase 5). Use <b>Builder</b> for now.</div>`;
    return;
  }
  panel.innerHTML =
    state.fields.map(fieldCardHtml).join("") +
    `<button class="add-field" data-action="add">+ Add field</button>` +
    `<div class="field-count">${state.fields.length} field${state.fields.length === 1 ? "" : "s"} · generated by the engine, fully seeded</div>`;
  // <select> values can't be set via the markup above, so set them now.
  state.fields.forEach((f) => {
    const sel = panel.querySelector(`select[data-id="${f.id}"]`);
    if (sel) sel.value = f.type;
  });
}

function renderOutputTabs() {
  const tabs = ["table", "json", "csv", "sql", "pdf"];
  $("#outputTabs").innerHTML = tabs
    .map(
      (t) =>
        `<button class="tab ${state.outputTab === t ? "active" : ""}" data-otab="${t}" style="flex:0 0 auto;padding:7px 15px">${t.toUpperCase()}</button>`
    )
    .join("");
  // PDF is just another export format, so it lives with the other tabs. Its
  // Table/Doc toggle only makes sense while PDF is selected, and Copy makes no
  // sense for a binary file, so show each contextually.
  const isPdf = state.outputTab === "pdf";
  $("#pdfCtrl").style.display = isPdf ? "" : "none";
  $("#copy").style.display = isPdf ? "none" : "";
  renderRowsCtrl();
}

function renderRowsCtrl() {
  // Common counts live in the dropdown; the box beside it holds any other
  // number. Whichever currently holds the active count is highlighted; the
  // other stays neutral (the box fades to its "custom" placeholder).
  const presets = [10, 100, 1000, 10000];
  const rc = Number(state.rowCount);
  const isPreset = presets.includes(rc);
  const sel = $("#rowSelect");
  const box = $("#rowCount");
  if (isPreset) sel.value = String(rc);
  sel.classList.toggle("active", isPreset);
  box.classList.toggle("active", !isPreset);
  box.value = isPreset ? "" : state.rowCount;
}

function cellHtml(value) {
  if (value === null) return `<td class="cell-null">NULL</td>`;
  if (typeof value === "boolean") return `<td class="cell-bool">${value}</td>`;
  if (typeof value === "number") return `<td class="cell-num">${value}</td>`;
  return `<td>${escapeHtml(value)}</td>`;
}

function tableHtml() {
  if (!state.rows.length) return `<div class="placeholder">No rows yet.</div>`;
  const cols = state.fields.map((f) => ({ name: f.name, label: state.labels[f.type] || f.type }));
  const head =
    `<th class="num">#</th>` +
    cols
      .map(
        (c) =>
          `<th><div class="col-name">${escapeHtml(c.name)}</div><div class="col-type">${escapeHtml(c.label)}</div></th>`
      )
      .join("");
  const body = state.rows
    .map(
      (row, i) =>
        `<tr><td class="num">${i + 1}</td>` +
        state.fields.map((f) => cellHtml(row[f.name])).join("") +
        `</tr>`
    )
    .join("");
  return `<table class="grid"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function codeHtml(text) {
  const lines = text.split("\n").slice(0, 800);
  return (
    `<pre class="code-view">` +
    lines
      .map(
        (t, i) =>
          `<div class="line"><span class="ln">${i + 1}</span><span class="lt">${escapeHtml(t === "" ? " " : t)}</span></div>`
      )
      .join("") +
    `</pre>`
  );
}

async function exportText(format) {
  if (state.exportCache[format] != null) return state.exportCache[format];
  const resp = await postJSON("/export", {
    fields: buildFields(),
    rows: Math.min(state.rowCount, PREVIEW_CAP),
    seed: state.seed,
    format,
    table: state.tableName,
  });
  const text = await resp.text();
  state.exportCache[format] = text;
  return text;
}

// The placement of a field on the document: header, body, or off (hidden).
function pdfPlacement(name) {
  return state.pdfDoc.placement[name] || "body";
}

// Assemble the pdf_config the server expects from the current doc-layout state.
function pdfDocConfig() {
  const d = state.pdfDoc;
  const names = state.fields.map((f) => f.name);
  const header = names.filter((n) => pdfPlacement(n) === "header");
  const body = names.filter((n) => pdfPlacement(n) === "body");
  const cfg = { title: d.title || state.tableName || "Record", style: d.style };
  if (header.length) cfg.header_fields = header;
  if (d.style === "letter" && d.template.trim()) cfg.body_template = d.template;
  else cfg.body_fields = body;
  return cfg;
}

// A starter letter seeded when prose mode is first switched on, so the change is
// immediately visible and there's something concrete to edit.
function defaultDocTemplate() {
  const names = state.fields.map((f) => f.name);
  if (!names.length) return "Dear recipient,\n\n\n\nSincerely,";
  const body = names.map((n) => `${n.replace(/_/g, " ")}: {${n}}`).join("\n");
  return `Dear recipient,\n\n${body}\n\nSincerely,`;
}

// The doc-layout controls (title, per-field placement, optional prose template).
function pdfControlsHtml() {
  const d = state.pdfDoc;
  const n = Number(state.rowCount) || 0;
  const fieldRows = state.fields
    .map((f) => {
      const p = pdfPlacement(f.name);
      const seg = (val, lbl) =>
        `<button class="pdf-seg ${p === val ? "active" : ""}" data-place="${escapeHtml(f.name)}" data-val="${val}">${lbl}</button>`;
      return (
        `<div class="pdf-field"><span class="pdf-fname">${escapeHtml(f.name)}</span>` +
        `<span class="pdf-segs">${seg("header", "Header")}${seg("body", "Body")}${seg("off", "Off")}</span></div>`
      );
    })
    .join("");
  const tokens = state.fields.map((f) => `{${f.name}}`).join("  ");
  const styleBtn = (val, lbl) =>
    `<button class="pdf-seg ${d.style === val ? "active" : ""}" data-style="${val}">${lbl}</button>`;
  const placementLabel =
    d.style === "form"
      ? "Header fields fill the numbered boxes; Body fields become the schedule; an amount-like field becomes the total."
      : d.style === "letter"
        ? "Header fields show above the letter; Body fields are unused in letter mode."
        : "Header fields show in a top block; Body fields list below.";
  return (
    `<div class="pdf-controls">` +
    `<div class="pdf-ctl">Style<span class="pdf-segs">${styleBtn("sheet", "Sheet")}${styleBtn("letter", "Letter")}${styleBtn("form", "Form")}</span></div>` +
    `<label class="pdf-ctl">Title<input id="pdfTitle" type="text" value="${escapeHtml(d.title)}" placeholder="${escapeHtml(state.tableName || "Record")}" /></label>` +
    `<div class="pdf-fields">${fieldRows}</div>` +
    `<div class="pdf-hint">${placementLabel}</div>` +
    (d.style === "letter"
      ? `<textarea id="pdfTemplate" class="pdf-template" placeholder="Dear {vendor},&#10;&#10;This confirms contract {contract_id}…">${escapeHtml(d.template)}</textarea>` +
        `<div class="pdf-hint">Placeholders: ${escapeHtml(tokens)}</div>`
      : "") +
    (n > 500
      ? `<div class="pdf-warn">Document mode is capped at 500 pages — reduce rows to export all ${n.toLocaleString()}.</div>`
      : "") +
    `</div>`
  );
}

// The whole PDF tab: layout controls (docs only) above a live embedded preview.
function pdfPaneHtml() {
  const controls =
    state.pdfMode === "docs"
      ? pdfControlsHtml()
      : `<div class="pdf-controls"><div class="pdf-hint">Table report — the full dataset as one paginated table. Set the row count above and click Export.</div></div>`;
  return (
    `<div class="pdf-pane">` +
    controls +
    `<div class="pdf-preview"><iframe id="pdfFrame" title="PDF preview"></iframe>` +
    `<div class="pdf-preview-note" id="pdfNote">Building preview…</div></div>` +
    `</div>`
  );
}

let pdfPreviewTimer = null;
let lastPdfUrl = null;
// Debounced so typing in the title/template doesn't fire a request per keystroke.
function refreshPdfPreview() {
  clearTimeout(pdfPreviewTimer);
  pdfPreviewTimer = setTimeout(loadPdfPreview, 350);
}

// The preview caption, worded per mode: the table is ONE document measured in
// rows, so "first N of M pages" would wrongly imply N pages; docs really are one
// page per record, so pages is the right unit there.
function previewNote(docs, shown, total) {
  const n = total.toLocaleString();
  if (docs) {
    const unit = total === 1 ? "page" : "pages";
    return shown < total ? `Preview · first ${shown} of ${n} ${unit} · Export for all` : `Preview · ${n} ${unit}`;
  }
  const unit = total === 1 ? "row" : "rows";
  return shown < total ? `Preview · first ${shown} of ${n} ${unit} · Export for all` : `Preview · full table · ${n} ${unit}`;
}

async function loadPdfPreview() {
  const frame = $("#pdfFrame");
  const note = $("#pdfNote");
  if (!frame) return; // not on the PDF tab
  const docs = state.pdfMode === "docs";
  const total = Number(state.rowCount) || 0;
  // Keep the preview snappy: a few pages for docs, a screenful for the table.
  const previewRows = Math.min(total || 1, docs ? 3 : 50);
  const body = {
    fields: buildFields(),
    rows: previewRows,
    seed: state.seed,
    format: docs ? "pdf-docs" : "pdf-table",
    table: state.tableName,
  };
  if (docs) body.pdf_config = pdfDocConfig();
  try {
    const resp = await postJSON("/export", body);
    const blob = await resp.blob();
    if (lastPdfUrl) URL.revokeObjectURL(lastPdfUrl);
    lastPdfUrl = URL.createObjectURL(blob);
    frame.src = lastPdfUrl + "#toolbar=0&navpanes=0&view=FitH";
    if (note) note.textContent = previewNote(docs, previewRows, total);
  } catch (e) {
    if (note) note.textContent = "Preview error: " + e.message;
  }
}

async function renderOutput() {
  const body = $("#outputBody");
  const tab = state.outputTab;
  if (tab === "table") {
    body.innerHTML = tableHtml();
  } else if (tab === "json") {
    body.innerHTML = codeHtml(JSON.stringify(state.rows, null, 2));
  } else if (tab === "pdf") {
    body.innerHTML = pdfPaneHtml();
    refreshPdfPreview();
  } else {
    body.innerHTML = `<div class="placeholder">Building ${tab.toUpperCase()}…</div>`;
    try {
      body.innerHTML = codeHtml(await exportText(tab));
    } catch (e) {
      body.innerHTML = `<div class="placeholder">Error: ${escapeHtml(e.message)}</div>`;
    }
  }
}

// --- data actions ----------------------------------------------------------

function buildFields() {
  // Turn builder state into the engine's schema: name, type, null_pct, options.
  return state.fields.map((f) => {
    const spec = { name: f.name || "field", type: f.type };
    if (Number(f.nullPct) > 0) spec.null_pct = Number(f.nullPct);
    for (const [key, val] of Object.entries(f.opts || {})) {
      if (val === "" || val == null) continue;
      spec[key] = val;
    }
    return spec;
  });
}

async function generate() {
  state.exportCache = {}; // fresh generation invalidates cached exports
  const total = Number(state.rowCount) || 0;
  $("#statSeed").textContent = state.seed;
  try {
    const resp = await postJSON("/generate", {
      fields: buildFields(),
      rows: Math.min(total, PREVIEW_CAP),
      seed: state.seed,
    });
    state.rows = (await resp.json()).rows;
  } catch (e) {
    state.rows = [];
    toast("Could not generate: " + e.message);
  }
  $("#statRows").textContent = total.toLocaleString() + " rows";
  $("#statNote").textContent =
    total > PREVIEW_CAP
      ? `Showing first ${PREVIEW_CAP} of ${total.toLocaleString()} — export for the full set`
      : "";
  renderOutput();
}

function addField() {
  state.fields.push({ id: uid(), name: "field_" + (state.fields.length + 1), type: "word", nullPct: 0, opts: {} });
  renderInputPanel();
  generate();
}

function removeField(id) {
  state.fields = state.fields.filter((f) => f.id !== id);
  renderInputPanel();
  generate();
}

// Shared download: POST /export and save the returned file. `extra` is merged
// into the request body (used to pass pdf_config for the document PDF).
async function saveExport(format, ext, extra) {
  const resp = await postJSON("/export", {
    fields: buildFields(),
    rows: Math.min(Number(state.rowCount) || 0, 50000),
    seed: state.seed,
    format,
    table: state.tableName,
    ...(extra || {}),
  });
  const blob = await resp.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = (state.tableName || "data") + "." + ext;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  toast("Downloaded " + a.download);
}

async function download() {
  // Export follows the active output tab. For PDF the Table/Doc toggle picks the
  // flavour; document mode passes a light pdf_config (the seam where a richer,
  // e.g. award-letter, layout plugs in later — for now it just titles each doc).
  const tab = state.outputTab;
  let format, ext, extra;
  if (tab === "pdf") {
    const docs = state.pdfMode === "docs";
    format = docs ? "pdf-docs" : "pdf-table";
    ext = "pdf";
    extra = docs ? { pdf_config: pdfDocConfig() } : {};
  } else {
    format = tab === "table" ? "csv" : tab;
    ext = { csv: "csv", sql: "sql", json: "json", sqlite: "db" }[format] || format;
    extra = {};
  }
  try {
    await saveExport(format, ext, extra);
  } catch (e) {
    toast("Export failed: " + e.message);
  }
}

async function copyOutput() {
  const tab = state.outputTab;
  if (tab === "pdf") {
    toast("PDF can't be copied — use Export to download");
    return;
  }
  let text;
  if (tab === "table" || tab === "json") text = JSON.stringify(state.rows, null, 2);
  else text = await exportText(tab);
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied " + (tab === "table" ? "JSON" : tab.toUpperCase()) + " to clipboard");
  } catch (e) {
    toast("Copy failed");
  }
}

// --- events ----------------------------------------------------------------

function wireEvents() {
  $("#generate").addEventListener("click", generate);
  $("#download").addEventListener("click", download);
  $("#copy").addEventListener("click", copyOutput);

  // PDF Table/Doc toggle: remember the choice, highlight the active button, and
  // refresh the info panel so it reflects the new style.
  $("#pdfCtrl").addEventListener("click", (e) => {
    const b = e.target.closest(".pdf-mode");
    if (!b) return;
    state.pdfMode = b.dataset.pdfmode;
    document
      .querySelectorAll(".pdf-mode")
      .forEach((x) => x.classList.toggle("active", x.dataset.pdfmode === state.pdfMode));
    if (state.outputTab === "pdf") renderOutput();
  });

  // PDF layout controls live inside the output body, which is re-rendered often,
  // so listen on the stable parent. Title/template edits only refresh the
  // preview (keeps focus); placement and the template toggle re-render the pane.
  const outBody = $("#outputBody");
  outBody.addEventListener("input", (e) => {
    if (e.target.id === "pdfTitle") {
      state.pdfDoc.title = e.target.value;
      refreshPdfPreview();
    } else if (e.target.id === "pdfTemplate") {
      state.pdfDoc.template = e.target.value;
      refreshPdfPreview();
    }
  });
  outBody.addEventListener("click", (e) => {
    const seg = e.target.closest(".pdf-seg");
    if (!seg) return;
    if (seg.dataset.style) {
      state.pdfDoc.style = seg.dataset.style;
      // Seed a starter letter the first time letter mode is chosen.
      if (seg.dataset.style === "letter" && !state.pdfDoc.template.trim()) {
        state.pdfDoc.template = defaultDocTemplate();
      }
    } else if (seg.dataset.place) {
      state.pdfDoc.placement[seg.dataset.place] = seg.dataset.val;
    }
    renderOutput();
  });

  $("#tableName").addEventListener("change", (e) => {
    state.tableName = e.target.value;
  });
  $("#seed").addEventListener("change", (e) => {
    state.seed = Number(e.target.value) || 0;
    generate();
  });
  $("#rowCount").addEventListener("change", (e) => {
    state.rowCount = Math.max(1, Math.min(100000, Number(e.target.value) || 1));
    renderOutputTabs();
    generate();
  });
  // Rows dropdown: picking a preset sets the count and clears the custom box.
  $("#rowSelect").addEventListener("change", (e) => {
    state.rowCount = Number(e.target.value);
    renderOutputTabs();
    generate();
  });

  $("#theme").addEventListener("change", (e) => {
    const name = e.target.value;
    $("#customColor").style.display = name === "Custom" ? "" : "none";
    if (name === "Custom") {
      applyCustom($("#customColor").value);
    } else {
      applyAccent(...ACCENTS[name]);
    }
  });
  $("#customColor").addEventListener("input", (e) => applyCustom(e.target.value));

  $("#outputTabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-otab]");
    if (!b) return;
    state.outputTab = b.dataset.otab;
    renderOutputTabs();
    renderOutput();
  });
  $("#inputTabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (!b) return;
    state.inputTab = b.dataset.tab;
    renderInputTabs();
    renderInputPanel();
  });

  // Delegated events for the builder (its cards are re-rendered often).
  const panel = $("#inputPanel");
  panel.addEventListener("input", (e) => {
    const el = e.target;
    const field = state.fields.find((f) => f.id === el.dataset.id);
    if (!field) return;
    if (el.dataset.role === "name") field.name = el.value;
    else if (el.dataset.role === "nullpct") field.nullPct = el.value;
    else if (el.dataset.opt) {
      field.opts[el.dataset.opt] =
        el.type === "number" ? (el.value === "" ? undefined : Number(el.value)) : el.value;
    }
  });
  panel.addEventListener("change", (e) => {
    const el = e.target;
    const field = state.fields.find((f) => f.id === el.dataset.id);
    if (!field) return;
    if (el.dataset.role === "type") {
      field.type = el.value;
      field.opts = {}; // options differ per type; start fresh
      renderInputPanel();
    }
    generate(); // any committed edit refreshes the preview
  });
  panel.addEventListener("click", (e) => {
    const b = e.target.closest("[data-action]");
    if (!b) return;
    if (b.dataset.action === "add") addField();
    else if (b.dataset.action === "remove") removeField(b.dataset.id);
  });
}

// --- startup ---------------------------------------------------------------

async function init() {
  applyAccent(...ACCENTS["Federal Blue"]);
  try {
    const data = await (await fetch("/field-types")).json();
    state.groups = data.groups;
    data.groups.forEach((g) => g.types.forEach((t) => (state.labels[t.value] = t.label)));
  } catch (e) {
    toast("Could not load field types");
  }

  // A GovCon-flavoured starting schema so the page opens with something real.
  state.fields = [
    { id: uid(), name: "contract_id", type: "autoIncrement", nullPct: 0, opts: { prefix: "GS-", start: 1000 } },
    { id: uid(), name: "vendor", type: "company", nullPct: 0, opts: {} },
    { id: uid(), name: "agency", type: "enum", nullPct: 0, opts: { values: "Dept of Defense, GSA, NASA" } },
    { id: uid(), name: "amount", type: "price", nullPct: 0, opts: { min: 25000, max: 5000000 } },
    { id: uid(), name: "awarded", type: "date", nullPct: 0, opts: {} },
  ];

  wireEvents();
  renderInputTabs();
  renderInputPanel();
  renderOutputTabs();
  generate();
}

init();
