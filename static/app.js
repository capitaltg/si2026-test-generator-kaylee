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
  preset: null, // active GovCon template key (null = normal builder mode)
  presetKind: null, // "form" | "data" — how the active preset renders/exports
  presets: [], // template gallery, from GET /templates
  custom: [], // user-saved templates, from localStorage
  loadedTemplateKey: null, // key of the saved template currently loaded (for "Update")
  galleryFilter: "all", // gallery toggle bar: all | form | doc | data | custom
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

// --- saved templates (localStorage) ----------------------------------------
// Custom templates the user saves live entirely in the browser. Each is a
// snapshot of the studio: either a Builder setup (fields + theme + table) or a
// bookmark onto a GovCon preset (base + opts). They appear in the gallery under
// the "Mine" filter with kind "custom".
const TPL_STORE = "fixtura.templates.v1";

// The gallery toggle bar: [filter key, label]. "all" shows everything; the kind
// filters match a preset's `kind`; "custom" is the user's saved templates.
const GALLERY_FILTERS = [
  ["all", "All"],
  ["form", "Forms"],
  ["doc", "Documents"],
  ["data", "Datasets"],
  ["custom", "Saved"],
];

function loadCustomTemplates() {
  try {
    return JSON.parse(localStorage.getItem(TPL_STORE)) || [];
  } catch (e) {
    return [];
  }
}

function persistCustomTemplates() {
  try {
    localStorage.setItem(TPL_STORE, JSON.stringify(state.custom));
  } catch (e) {
    toast("Couldn't save — browser storage may be full");
  }
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
  const tabs = ["builder", "templates", "ddl", "describe", "csv", "json"];
  const labels = { builder: "Builder", templates: "Templates", ddl: "DDL", describe: "Describe", csv: "CSV", json: "JSON" };
  $("#inputTabs").innerHTML = tabs
    .map(
      (t) =>
        `<button class="tab ${state.inputTab === t ? "active" : ""}" data-tab="${t}">${labels[t]}</button>`
    )
    .join("");
}

// The template gallery: a card per GovCon preset. The active one is highlighted.
// Picking a card is what turns on preset mode; there's also a card to leave it.
function galleryHtml() {
  const badges = { form: "Real gov form", doc: "Document", data: "Dataset", custom: "Saved" };
  // Built-in presets and the user's saved templates share one card grid; the
  // toggle bar just filters which kinds show.
  const all = state.presets.concat(state.custom);
  const filter = state.galleryFilter;
  const shown = filter === "all" ? all : all.filter((p) => p.kind === filter);

  const bar = GALLERY_FILTERS.map(
    ([key, label]) =>
      `<button class="tpl-filter ${state.galleryFilter === key ? "active" : ""}" ` +
      `data-filter="${key}">${label}</button>`
  ).join("");

  const cards = shown
    .map((p) => {
      const active = state.preset === p.key;
      const isCustom = p.kind === "custom";
      // Custom cards are divs (not buttons) so the title can be edited inline;
      // role/tabindex keep them keyboard-activatable.
      const tag = isCustom ? "div" : "button";
      const attrs = isCustom ? ' role="button" tabindex="0"' : "";
      return (
        `<${tag} class="tpl-card ${active ? "active" : ""}"${attrs} data-preset="${escapeHtml(p.key)}">` +
        `<div class="tpl-top"><span class="tpl-name">${escapeHtml(p.label)}</span>` +
        `<span class="tpl-badge tpl-badge-${p.kind}">${escapeHtml(badges[p.kind] || p.kind)}</span></div>` +
        `<div class="tpl-desc">${escapeHtml(p.description)}</div>` +
        (isCustom
          ? `<span class="tpl-act tpl-rename" data-rename="${escapeHtml(p.key)}" title="Rename">✎</span>` +
            `<span class="tpl-act tpl-del" data-del="${escapeHtml(p.key)}" title="Delete">✕</span>`
          : "") +
        `</${tag}>`
      );
    })
    .join("");

  const empty = shown.length
    ? ""
    : filter === "custom"
      ? `<div class="tpl-empty">No saved templates yet. Set up the Builder (or pick a ` +
        `GovCon template), then hit <b>Save</b> up top.</div>`
      : `<div class="tpl-empty">Nothing here.</div>`;

  return (
    `<div class="tpl-intro">Pick a GovCon document template — or one of your own saved ` +
    `setups. It autogenerates realistic, internally-consistent data — change the ` +
    `<b>seed</b> above to re-roll.</div>` +
    `<div class="tpl-filterbar">${bar}</div>` +
    `<div class="tpl-gallery">${cards}</div>` +
    empty
  );
}

function renderInputPanel() {
  const panel = $("#inputPanel");
  if (state.inputTab === "templates") {
    panel.innerHTML = galleryHtml();
    return;
  }
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
  // A data preset is a real dataset, so it gets the same text-format tabs the
  // builder does (minus PDF). A form/doc preset renders a fixed PDF, so it has
  // no tabs at all.
  const isData = state.preset && state.presetKind === "data";
  const tabs = isData ? ["table", "json", "csv", "sql"] : ["table", "json", "csv", "sql", "pdf"];
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
  // Only a form/doc preset (fixed PDF) hides the tabs; a data preset keeps them.
  const tplForm = state.preset && state.presetKind !== "data";
  $("#outputTabs").style.display = tplForm ? "none" : "";
  $("#pdfCtrl").style.display = !state.preset && isPdf ? "" : "none";
  $("#copy").style.display = tplForm || isPdf ? "none" : "";
  renderRowsCtrl();
}

function renderRowsCtrl() {
  // Common counts live in the dropdown; the box beside it holds any other
  // number. Whichever currently holds the active count is highlighted; the
  // other stays neutral (the box fades to its "custom" placeholder).
  // The count means different things per context, so label it accordingly:
  // separate generated documents are "Copies"; the PDF doc-per-row tab is
  // "Docs"; everything else is table "Rows".
  const lbl = $("#rowsLbl");
  if (lbl) {
    if (state.preset && state.presetKind !== "data") lbl.textContent = "Copies";
    else if (!state.preset && state.outputTab === "pdf" && state.pdfMode === "docs") lbl.textContent = "Docs";
    else lbl.textContent = "Rows";
  }
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
  // A preset's columns come from the generated records; the builder's come from
  // the configured field list.
  const cols = state.preset
    ? Object.keys(state.rows[0]).map((n) => ({ name: n, label: n }))
    : state.fields.map((f) => ({ name: f.name, label: state.labels[f.type] || f.type }));
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
        cols.map((c) => cellHtml(row[c.name])).join("") +
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
  // A data preset exports its records; the builder exports its field schema.
  const body = state.preset
    ? { preset: state.preset, rows: Math.min(state.rowCount, PREVIEW_CAP), seed: state.seed, format, table: state.preset }
    : { fields: buildFields(), rows: Math.min(state.rowCount, PREVIEW_CAP), seed: state.seed, format, table: state.tableName };
  const resp = await postJSON("/export", body);
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
  // A form/doc preset is driven entirely by the preset (a fixed PDF). The
  // builder and data presets are tab-driven and share the rendering below.
  if (state.preset && state.presetKind !== "data") return generatePreset(Number(state.rowCount) || 0);
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

// Switch the studio into (or out of) template mode. An empty key clears it.
function selectPreset(key) {
  // Entering a GovCon preset (or clearing to the builder) drops any loaded
  // custom-template context — "Update" no longer applies.
  state.loadedTemplateKey = null;
  if (!key) {
    state.preset = null;
    state.presetKind = null;
  } else {
    const p = state.presets.find((x) => x.key === key);
    if (!p) return;
    state.preset = key;
    state.presetKind = p.kind;
    // Forms are heavy (one filled form per row); default to a sensible few.
    if (p.kind === "form" && Number(state.rowCount) > 10) state.rowCount = 3;
    // A data preset has no PDF tab; if PDF was active, fall back to the table.
    if (p.kind === "data" && state.outputTab === "pdf") state.outputTab = "table";
  }
  renderInputPanel();
  renderOutputTabs();
  generate();
}

// --- saving & restoring templates ------------------------------------------

// Saving is file-like: a template's identity is its name. Save writes the
// current Builder setup under a name; if that name is already taken by a
// DIFFERENT template, the modal asks whether to Replace it or save a copy.
// Renaming is handled inline on the card (see startInlineRename), not here.
let pendingSnapshot = null; // studio snapshot awaiting a name in the modal
let pendingClash = null; // an existing template whose name the new one collides with

// Capture the current Builder setup as a saveable snapshot. (Saving a GovCon
// preset as a template is deferred to a later ticket — see openSaveModal.)
function currentTemplateSnapshot() {
  return {
    origin: "builder",
    fields: JSON.parse(JSON.stringify(state.fields)),
    theme: $("#theme").value,
    customColor: $("#customColor").value,
    table: state.tableName,
    seed: state.seed,
    rowCount: state.rowCount,
  };
}

// One-line card summary of a snapshot: "N fields · seed X · N rows".
function describeSnapshot(s) {
  return `${s.fields.length} field${s.fields.length === 1 ? "" : "s"} · seed ${s.seed} · ${s.rowCount} rows`;
}

// The saved template whose name matches `name` (case-insensitive), or null.
function templateByName(name) {
  const n = name.trim().toLowerCase();
  return state.custom.find((c) => c.label.trim().toLowerCase() === n) || null;
}

// True if any OTHER template uses this name — used to keep inline renames and
// "Save a copy" from producing duplicates.
function nameTaken(name, exceptKey) {
  const clash = templateByName(name);
  return !!clash && clash.key !== exceptKey;
}

// Derive a free name from a base by appending " (2)", " (3)", … as needed.
function uniqueName(base) {
  let name = base;
  let i = 2;
  while (nameTaken(name, null)) name = `${base} (${i++})`;
  return name;
}

function showModalError(msg) {
  const el = $("#saveModalErr");
  el.textContent = msg;
  el.hidden = false;
}
function clearModalError() {
  $("#saveModalErr").hidden = true;
}

// The saved template currently loaded, if it still exists.
function loadedTemplate() {
  return state.loadedTemplateKey
    ? state.custom.find((c) => c.key === state.loadedTemplateKey)
    : null;
}

// Return the modal to its normal (non-conflict) state: just Cancel + Save.
function resetSaveConflict() {
  pendingClash = null;
  $("#saveModalCopy").hidden = true;
  $("#saveModalReplace").hidden = true;
  $("#saveModalOk").hidden = false;
  clearModalError();
}

function openSaveModal() {
  // Only Builder setups are saveable for now. GovCon presets have no editable
  // knobs yet, so saving one would just be a thin seed bookmark — revisit later.
  if (state.preset) {
    toast("Saving GovCon templates comes later — Builder setups only for now");
    return;
  }
  pendingSnapshot = currentTemplateSnapshot();
  resetSaveConflict();
  const loaded = loadedTemplate();
  $("#saveModalSub").textContent =
    `Builder · ${pendingSnapshot.fields.length} field${pendingSnapshot.fields.length === 1 ? "" : "s"} · seed ${pendingSnapshot.seed}`;
  // Prefill the loaded template's name so re-saving it just overwrites it.
  $("#saveModalName").value = loaded ? loaded.label : "";
  $("#saveModal").hidden = false;
  $("#saveModalName").focus();
  $("#saveModalName").select();
}

function closeSaveModal() {
  $("#saveModal").hidden = true;
  pendingSnapshot = null;
  resetSaveConflict();
}

// After a save, refresh the gallery on the "Saved" filter so the card is there.
function showSavedGallery() {
  state.inputTab = "templates";
  state.galleryFilter = "custom";
  renderInputTabs();
  renderInputPanel();
}

// Create a brand-new saved template from the pending snapshot.
function createTemplate(name) {
  const entry = Object.assign(
    { key: "custom:" + Date.now(), label: name, kind: "custom", description: describeSnapshot(pendingSnapshot) },
    pendingSnapshot
  );
  state.custom.push(entry);
  state.loadedTemplateKey = entry.key; // now "loaded", so re-saving overwrites it
  persistCustomTemplates();
  closeSaveModal();
  showSavedGallery();
  toast('Saved "' + name + '"');
}

// Overwrite an existing template with the pending snapshot (keeps its key).
function overwriteTemplate(target, name) {
  Object.assign(target, pendingSnapshot, { label: name, description: describeSnapshot(pendingSnapshot) });
  state.loadedTemplateKey = target.key;
  persistCustomTemplates();
  closeSaveModal();
  showSavedGallery();
  toast('Updated "' + name + '"');
}

// The Save button / Enter. Decides among create, silent-overwrite, and prompting.
function attemptSave() {
  const name = $("#saveModalName").value.trim();
  if (!name || !pendingSnapshot) {
    $("#saveModalName").focus();
    return;
  }
  const clash = templateByName(name);
  if (!clash) return createTemplate(name); // free name → new template
  if (clash.key === state.loadedTemplateKey) return overwriteTemplate(clash, name); // saving over the one you loaded
  // Name belongs to a different template — ask before clobbering it.
  pendingClash = clash;
  showModalError('A template named "' + name + '" already exists.');
  $("#saveModalOk").hidden = true;
  $("#saveModalCopy").hidden = false;
  $("#saveModalReplace").hidden = false;
}

// Inline rename: turn a saved card's title into an editable field in place.
// Commit on Enter/blur, cancel on Escape. Rejects a name another template uses.
let renamingKey = null;
function startInlineRename(cardEl, key) {
  const nameEl = cardEl.querySelector(".tpl-name");
  if (!nameEl || renamingKey) return;
  renamingKey = key;
  const original = nameEl.textContent;
  nameEl.contentEditable = "true";
  nameEl.classList.add("editing");
  nameEl.focus();
  // Select the whole name so typing replaces it.
  const range = document.createRange();
  range.selectNodeContents(nameEl);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);

  const finish = (commit) => {
    nameEl.removeEventListener("keydown", onKey);
    nameEl.removeEventListener("blur", onBlur);
    nameEl.contentEditable = "false";
    nameEl.classList.remove("editing");
    renamingKey = null;
    const next = nameEl.textContent.trim();
    if (!commit || !next || next === original) {
      nameEl.textContent = original; // revert
      return;
    }
    if (nameTaken(next, key)) {
      toast('A template named "' + next + '" already exists');
      nameEl.textContent = original;
      return;
    }
    const t = state.custom.find((c) => c.key === key);
    if (t) {
      t.label = next;
      persistCustomTemplates();
    }
    nameEl.textContent = next;
    toast('Renamed to "' + next + '"');
  };
  const onKey = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      nameEl.blur();
    } else if (e.key === "Escape") {
      e.preventDefault();
      finish(false);
    }
  };
  const onBlur = () => finish(true);
  nameEl.addEventListener("keydown", onKey);
  nameEl.addEventListener("blur", onBlur);
}

// Rehydrate the studio from a saved custom template and regenerate.
function restoreCustomTemplate(key) {
  const t = state.custom.find((c) => c.key === key);
  if (!t) return;
  state.seed = t.seed;
  state.rowCount = t.rowCount;
  $("#seed").value = t.seed;

  if (t.origin === "preset") {
    // A bookmark onto a GovCon preset: selectPreset renders + regenerates.
    selectPreset(t.base);
    return;
  }

  // Builder setup: leave preset mode and rebuild the field cards with fresh ids.
  state.preset = null;
  state.presetKind = null;
  state.loadedTemplateKey = key; // mark it loaded so edits can Update it
  state.tableName = t.table || "users";
  $("#tableName").value = state.tableName;
  state.fields = (t.fields || []).map((f) => ({
    id: uid(),
    name: f.name,
    type: f.type,
    nullPct: f.nullPct || 0,
    opts: Object.assign({}, f.opts),
  }));

  const theme = t.theme || "Federal Blue";
  $("#theme").value = theme;
  if (theme === "Custom") {
    $("#customColor").style.display = "";
    if (t.customColor) $("#customColor").value = t.customColor;
    applyCustom($("#customColor").value);
  } else {
    $("#customColor").style.display = "none";
    applyAccent(...ACCENTS[theme]);
  }

  state.inputTab = "builder";
  renderInputTabs();
  renderInputPanel();
  renderOutputTabs();
  generate();
}

let pendingDeleteKey = null; // saved-template key awaiting delete confirmation

// Open the designed delete-confirmation dialog for a saved template.
function deleteCustomTemplate(key) {
  const t = state.custom.find((c) => c.key === key);
  if (!t) return;
  pendingDeleteKey = key;
  $("#confirmModalSub").innerHTML =
    `<b>${escapeHtml(t.label)}</b> will be removed for good. This can't be undone.`;
  $("#confirmModal").hidden = false;
  $("#confirmOk").focus();
}

function closeConfirmModal() {
  $("#confirmModal").hidden = true;
  pendingDeleteKey = null;
}

function commitDelete() {
  if (!pendingDeleteKey) return;
  if (state.loadedTemplateKey === pendingDeleteKey) state.loadedTemplateKey = null;
  state.custom = state.custom.filter((c) => c.key !== pendingDeleteKey);
  persistCustomTemplates();
  closeConfirmModal();
  renderInputPanel();
}

async function generate() {
  state.exportCache = {}; // fresh generation invalidates cached exports
  const total = Number(state.rowCount) || 0;
  $("#statSeed").textContent = state.seed;
  // A form/doc preset renders a fixed PDF preview. The builder and data presets
  // both produce rows we render through the format tabs.
  if (state.preset && state.presetKind !== "data") return generatePreset(total);
  try {
    const body = state.preset
      ? presetReqBody(Math.min(total, PREVIEW_CAP))
      : { fields: buildFields(), rows: Math.min(total, PREVIEW_CAP), seed: state.seed };
    const resp = await postJSON("/generate", body);
    state.rows = (await resp.json()).rows;
  } catch (e) {
    state.rows = [];
    toast("Could not generate: " + e.message);
  }
  $("#statRows").textContent =
    total.toLocaleString() + (state.preset ? " records" : " rows");
  $("#statNote").textContent =
    total > PREVIEW_CAP
      ? `Showing first ${PREVIEW_CAP} of ${total.toLocaleString()} — export for the full set`
      : "";
  renderOutput();
}

// Form/doc preset preview: render a filled-form PDF, one copy per record. (Data
// presets don't come here — they render through the format tabs like the builder.)
async function generatePreset(total) {
  const body = $("#outputBody");
  $("#statRows").textContent = total.toLocaleString() + " docs";
  $("#statNote").textContent = "";
  body.innerHTML =
    `<div class="pdf-pane"><div class="pdf-controls"><div class="pdf-hint">` +
    `Filled real form — one copy per record, each with generated, reconciling data. ` +
    `Change the seed to re-roll; click Export to download.</div></div>` +
    `<div class="pdf-preview"><iframe id="pdfFrame" title="Form preview"></iframe>` +
    `<div class="pdf-preview-note" id="pdfNote">Building preview…</div></div></div>`;
  try {
    const resp = await postJSON("/export", presetReqBody(Math.min(total, 5)));
    const blob = await resp.blob();
    if (lastPdfUrl) URL.revokeObjectURL(lastPdfUrl);
    lastPdfUrl = URL.createObjectURL(blob);
    $("#pdfFrame").src = lastPdfUrl + "#toolbar=0&navpanes=0&view=FitH";
    const note = $("#pdfNote");
    if (note) note.textContent = total > 5 ? `Preview · first 5 of ${total} forms · Export for all` : `Preview · ${total} form${total === 1 ? "" : "s"}`;
  } catch (e) {
    const note = $("#pdfNote");
    if (note) note.textContent = "Preview error: " + e.message;
  }
}

function presetReqBody(rows) {
  return { preset: state.preset, rows, seed: state.seed, format: "pdf", table: state.preset };
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
  // Template mode exports the preset (a filled form PDF, or the dataset).
  if (state.preset) {
    try {
      if (state.presetKind !== "data") {
        const resp = await postJSON("/export", presetReqBody(Math.min(Number(state.rowCount) || 1, 200)));
        const blob = await resp.blob();
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = state.preset + ".pdf";
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
        toast("Downloaded " + a.download);
      } else {
        // Data preset: export in the active tab's format (Table downloads CSV).
        const fmt = state.outputTab === "table" ? "csv" : state.outputTab;
        const ext = { csv: "csv", json: "json", sql: "sql" }[fmt] || fmt;
        const resp = await postJSON("/export", {
          preset: state.preset,
          rows: Math.min(Number(state.rowCount) || 0, 50000),
          seed: state.seed,
          format: fmt,
          table: state.preset,
        });
        const blob = await resp.blob();
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = state.preset + "." + ext;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
        toast("Downloaded " + a.download);
      }
    } catch (e) {
      toast("Export failed: " + e.message);
    }
    return;
  }
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

  // Save-as-template: open the modal, save on OK/Enter. When the name collides
  // with another template, Replace/Save-a-copy appear; editing the name returns
  // to the normal state.
  $("#savePreset").addEventListener("click", openSaveModal);
  $("#saveModalOk").addEventListener("click", attemptSave);
  $("#saveModalReplace").addEventListener("click", () => {
    if (pendingClash) overwriteTemplate(pendingClash, $("#saveModalName").value.trim());
  });
  $("#saveModalCopy").addEventListener("click", () => {
    createTemplate(uniqueName($("#saveModalName").value.trim()));
  });
  $("#saveModalCancel").addEventListener("click", closeSaveModal);
  $("#saveModalName").addEventListener("input", resetSaveConflict);
  $("#saveModalName").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      // In a name conflict, Enter takes the primary (Replace) action.
      if (pendingClash) overwriteTemplate(pendingClash, $("#saveModalName").value.trim());
      else attemptSave();
    } else if (e.key === "Escape") closeSaveModal();
  });
  $("#saveModal").addEventListener("click", (e) => {
    if (e.target.id === "saveModal") closeSaveModal();
  });

  // Delete-confirmation dialog: confirm on Delete/Enter, dismiss on
  // Cancel/Esc/backdrop.
  $("#confirmOk").addEventListener("click", commitDelete);
  $("#confirmCancel").addEventListener("click", closeConfirmModal);
  $("#confirmModal").addEventListener("click", (e) => {
    if (e.target.id === "confirmModal") closeConfirmModal();
  });
  // Esc closes whichever dialog is open; Enter confirms a pending delete.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!$("#saveModal").hidden) closeSaveModal();
      if (!$("#confirmModal").hidden) closeConfirmModal();
    } else if (e.key === "Enter" && !$("#confirmModal").hidden) {
      commitDelete();
    }
  });

  // PDF Table/Doc toggle: remember the choice, highlight the active button, and
  // refresh the info panel so it reflects the new style.
  $("#pdfCtrl").addEventListener("click", (e) => {
    const b = e.target.closest(".pdf-mode");
    if (!b) return;
    state.pdfMode = b.dataset.pdfmode;
    document
      .querySelectorAll(".pdf-mode")
      .forEach((x) => x.classList.toggle("active", x.dataset.pdfmode === state.pdfMode));
    renderRowsCtrl();
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
    // Navigating to any tab exits template mode, so the normal output controls
    // (tabs, export options) come back.
    if (state.preset) {
      state.preset = null;
      state.presetKind = null;
      renderOutputTabs();
      generate();
    }
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
    // Ignore clicks landing in a title that's currently being renamed inline.
    if (e.target.isContentEditable) return;
    // Rename / delete affordances on a saved card — handle before the card.
    const ren = e.target.closest("[data-rename]");
    if (ren) {
      startInlineRename(ren.closest(".tpl-card"), ren.dataset.rename);
      return;
    }
    const del = e.target.closest("[data-del]");
    if (del) {
      deleteCustomTemplate(del.dataset.del);
      return;
    }
    // Gallery toggle bar.
    const filt = e.target.closest("[data-filter]");
    if (filt) {
      state.galleryFilter = filt.dataset.filter;
      renderInputPanel();
      return;
    }
    // Template gallery cards / the "back to builder" button.
    const card = e.target.closest("[data-preset]");
    if (card) {
      const key = card.dataset.preset;
      if (key.startsWith("custom:")) {
        restoreCustomTemplate(key);
        return;
      }
      if (!key) state.inputTab = "builder"; // clearing returns to the builder
      selectPreset(key);
      if (!key) renderInputTabs();
      return;
    }
    const b = e.target.closest("[data-action]");
    if (!b) return;
    if (b.dataset.action === "add") addField();
    else if (b.dataset.action === "remove") removeField(b.dataset.id);
  });

  // Custom cards are divs; make Enter open them (buttons do this natively).
  panel.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.target.isContentEditable) return;
    const card = e.target.closest('.tpl-card[role="button"]');
    if (card) {
      e.preventDefault();
      restoreCustomTemplate(card.dataset.preset);
    }
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
  try {
    state.presets = (await (await fetch("/templates")).json()).presets;
  } catch (e) {
    state.presets = [];
  }
  state.custom = loadCustomTemplates();

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
