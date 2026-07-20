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
  const tabs = ["table", "json", "csv", "sql"];
  $("#outputTabs").innerHTML = tabs
    .map(
      (t) =>
        `<button class="tab ${state.outputTab === t ? "active" : ""}" data-otab="${t}" style="flex:0 0 auto;padding:7px 15px">${t.toUpperCase()}</button>`
    )
    .join("");
  const presetValues = [10, 100, 1000, 10000];
  const isPreset = presetValues.includes(Number(state.rowCount));
  document.querySelectorAll(".preset").forEach((b) => {
    b.classList.toggle("active", Number(b.dataset.rows) === Number(state.rowCount));
  });
  // When a preset is selected, leave the custom box empty so its faded "custom"
  // placeholder shows. Only when the count is a custom value does the box hold
  // (and highlight) that number.
  const box = $("#rowCount");
  box.value = isPreset ? "" : state.rowCount;
  box.classList.toggle("active", !isPreset);
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

async function renderOutput() {
  const body = $("#outputBody");
  const tab = state.outputTab;
  if (tab === "table") {
    body.innerHTML = tableHtml();
  } else if (tab === "json") {
    body.innerHTML = codeHtml(JSON.stringify(state.rows, null, 2));
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

async function download() {
  const format = state.outputTab === "table" ? "csv" : state.outputTab;
  try {
    const resp = await postJSON("/export", {
      fields: buildFields(),
      rows: Math.min(Number(state.rowCount) || 0, 50000),
      seed: state.seed,
      format,
      table: state.tableName,
    });
    const blob = await resp.blob();
    const ext = { csv: "csv", sql: "sql", json: "json", sqlite: "db" }[format] || format;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (state.tableName || "data") + "." + ext;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    toast("Downloaded " + a.download);
  } catch (e) {
    toast("Export failed: " + e.message);
  }
}

async function copyOutput() {
  const tab = state.outputTab;
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

  // Row-count preset buttons + output tabs (both in the output bar).
  $("#rowsCtrl").addEventListener("click", (e) => {
    const b = e.target.closest(".preset");
    if (!b) return;
    state.rowCount = Number(b.dataset.rows);
    renderOutputTabs();
    generate();
  });
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
