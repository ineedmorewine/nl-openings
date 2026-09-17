import { isAirOrOceanOnly, isSuitable, fold } from "./filters.js";

const NEW_WINDOW_DAYS = 7;
const STORAGE_MARKS = "nl-openings:marks";
const STORAGE_FILTERS = "nl-openings:filters";
const SOURCE_PROBLEM_ORDER = ["read_failed", "unsupported_vendor", "no_careers_system_found",
  "site_unreachable", "no_address", "not_checked_yet", "ok"];

const state = {
  strings: {},
  jobs: [],
  generatedAt: null,
  sources: [],
  companies: new Map(),
  defaultFilters: {},
  filters: {},
  marks: {},
  ui: { tab: "new", search: "", group: "", sort: "newest", airOcean: false, everything: false, showHidden: false },
};

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- helpers

function t(key, vars = {}) {
  const template = key.split(".").reduce((node, part) => (node ? node[part] : undefined), state.strings);
  if (typeof template !== "string") return key;
  return template.replace(/\{(\w+)\}/g, (_, name) => (name in vars ? String(vars[name]) : `{${name}}`));
}

function readStorage(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function writeStorage(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* Storage unavailable (private mode); marks simply do not persist. */
  }
}

function asDate(isoDay) {
  return isoDay ? new Date(`${isoDay}T12:00:00Z`) : null;
}

function shiftDay(isoDay, days) {
  const d = asDate(isoDay);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function formatDay(isoDay) {
  const d = asDate(isoDay);
  return `${d.getUTCDate()} ${state.strings.months[d.getUTCMonth()]}`;
}

function safeUrl(url) {
  return /^https?:\/\//i.test(url || "") ? url : null;
}

function el(tag, attributes = {}, children = []) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === null || value === undefined || value === false) continue;
    if (name === "text") node.textContent = value;
    else if (name === "className") node.className = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value);
  }
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

// ---------------------------------------------------------------- job selection

function runDay() {
  return state.generatedAt ? state.generatedAt.slice(0, 10) : null;
}

function recencyDay(job) {
  return job.posted_at || job.first_seen;
}

function isNew(job) {
  const today = runDay();
  if (!today) return false;
  const cutoff = shiftDay(today, -NEW_WINDOW_DAYS);
  if (job.baseline) return Boolean(job.posted_at) && job.posted_at >= cutoff;
  return job.first_seen >= cutoff;
}

function arrivedOnLastRun(job) {
  return !job.baseline && job.first_seen === runDay();
}

function passesFilters(job) {
  const company = state.companies.get(job.company_id) || {};
  const { ui, filters } = state;
  const mark = state.marks[job.id] || {};
  if (mark.hidden && !ui.showHidden) return false;
  if (ui.group && company.group !== ui.group) return false;
  if (!ui.everything && !isSuitable(job.title, filters, company.extra_keywords || [])) return false;
  if (!ui.everything && !ui.airOcean && isAirOrOceanOnly(job.title, filters)) return false;
  if (ui.search) {
    const haystack = fold(`${job.title} ${company.name || ""} ${job.location || ""}`);
    if (!fold(ui.search).split(/\s+/).every((word) => haystack.includes(word))) return false;
  }
  return true;
}

function jobsForTab(tab) {
  const matching = state.jobs.filter(passesFilters);
  if (tab === "new") {
    return matching.filter(isNew).sort((a, b) =>
      (b.baseline ? b.posted_at : b.first_seen).localeCompare(a.baseline ? a.posted_at : a.first_seen)
      || recencyDay(b).localeCompare(recencyDay(a)));
  }
  if (state.ui.sort === "company") {
    const name = (job) => (state.companies.get(job.company_id) || {}).name || "";
    return matching.sort((a, b) => name(a).localeCompare(name(b)) || recencyDay(b).localeCompare(recencyDay(a)));
  }
  return matching.sort((a, b) => recencyDay(b).localeCompare(recencyDay(a)));
}

// ---------------------------------------------------------------- rendering

function renderMasthead() {
  $("updated").textContent = state.generatedAt
    ? t("updated", {
      date: new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })
        .format(new Date(state.generatedAt)),
    })
    : t("never_updated");

  const rail = $("track-rail");
  rail.replaceChildren(...state.sources.map((source) => {
    const kind = source.status === "ok" ? "ok" : (source.status === "read_failed" ? "failed" : "");
    return el("i", { className: kind, title: source.name });
  }));
  const ok = state.sources.filter((s) => s.status === "ok").length;
  $("track-caption").replaceChildren(
    document.createTextNode(`${t("track_caption", { ok, total: state.sources.length })}. `),
    el("strong", { text: t("track_link") }),
  );
}

function renderTabs() {
  const counts = { new: jobsForTab("new").length, all: jobsForTab("all").length };
  for (const button of document.querySelectorAll(".tabs button")) {
    const tab = button.dataset.tab;
    const selected = tab === state.ui.tab;
    button.setAttribute("aria-selected", String(selected));
    const label = t(`tab_${tab}`);
    button.replaceChildren(document.createTextNode(label));
    if (tab in counts) button.append(el("span", { className: "tab-count", text: String(counts[tab]) }));
  }
  $("toolbar").hidden = state.ui.tab === "sources";
  $("sort").hidden = state.ui.tab !== "all";
}

function renderJob(job) {
  const company = state.companies.get(job.company_id) || { name: job.company_id };
  const mark = state.marks[job.id] || {};
  const url = safeUrl(job.url);
  const day = recencyDay(job);
  const [dayNumber, month] = formatDay(day).split(" ");

  const labels = [];
  if (arrivedOnLastRun(job)) labels.push(el("span", { className: "label arrived", text: t("arrived_last_run") }));
  if (mark.applied) labels.push(el("span", { className: "label applied", text: t("applied") }));
  if (job.labels && job.labels.dutch_required) labels.push(el("span", { className: "label dutch", text: t("label_dutch_required") }));
  if (job.labels && job.labels.written_in_dutch && !job.labels.dutch_required) {
    labels.push(el("span", { className: "label", text: t("label_written_in_dutch") }));
  }
  if (!job.location) labels.push(el("span", { className: "label", text: t("label_location_missing") }));

  const where = el("p", { className: "where" }, [
    el("span", { className: "company", text: company.name }),
    job.location ? document.createTextNode(`, ${job.location}`) : null,
  ]);

  const datesNote = job.posted_at && job.posted_at !== job.first_seen && !job.baseline
    ? el("p", { className: "dates-note", text: t("posted_and_seen", { posted: formatDay(job.posted_at), seen: formatDay(job.first_seen) }) })
    : null;

  const title = url
    ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer", text: job.title })
    : document.createTextNode(job.title);

  const actions = el("div", { className: "actions" }, [
    url ? el("a", { className: "primary", href: url, target: "_blank", rel: "noopener noreferrer", text: t("open_posting") }) : null,
    el("button", {
      type: "button", className: "quiet", text: mark.applied ? t("undo_applied") : t("mark_applied"),
      onclick: () => toggleMark(job.id, "applied"),
    }),
    el("button", {
      type: "button", className: "quiet", text: mark.hidden ? t("unhide") : t("hide"),
      onclick: () => toggleMark(job.id, "hidden"),
    }),
  ]);

  return el("article", { className: `job${mark.hidden ? " is-hidden" : ""}` }, [
    el("div", { className: "plate" }, [
      el("span", { className: "day", text: dayNumber }),
      el("span", { className: "month", text: month }),
      el("span", { className: "kind", text: job.posted_at ? t("posted") : t("seen") }),
    ]),
    el("div", {}, [el("h3", {}, [title]), where, datesNote, labels.length ? el("div", { className: "labels" }, labels) : null]),
    actions,
  ]);
}

function renderJobs() {
  const list = $("list");
  if (!state.generatedAt) {
    $("count").textContent = "";
    list.replaceChildren(el("p", { className: "empty", text: t("empty_no_data") }));
    return;
  }
  const jobs = jobsForTab(state.ui.tab);
  $("count").textContent = jobs.length === 1 ? t("count_showing_one") : t("count_showing", { n: jobs.length });
  if (!jobs.length) {
    list.replaceChildren(el("p", { className: "empty", text: state.ui.tab === "new" ? t("empty_new") : t("empty_filtered") }));
    return;
  }
  list.replaceChildren(...jobs.map(renderJob));
}

function sourceStatusText(source) {
  const base = source.status === "unsupported_vendor"
    ? t("status_unsupported_vendor", { vendor: source.vendor || "?" })
    : t(`status_${source.status || "not_checked_yet"}`);
  if (source.status !== "ok" && source.showing_old_data && source.last_success) {
    return `${base}. ${t("status_old_data", { date: formatDay(source.last_success) })}`;
  }
  return base;
}

function renderSources() {
  $("count").textContent = "";
  const order = (s) => {
    const index = SOURCE_PROBLEM_ORDER.indexOf(s.status);
    return index === -1 ? 0 : index;
  };
  const sorted = [...state.sources].sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name));
  const rows = sorted.map((source) => {
    const kind = source.status === "ok" ? "ok" : (source.status === "read_failed" ? "failed" : "");
    return el("article", { className: "source" }, [
      el("span", { className: `dot ${kind}`, "aria-hidden": "true" }),
      el("div", {}, [
        el("h3", { text: source.name }),
        el("p", { className: "entities", text: [t(`groups.${source.group}`), ...(source.entities || [])].join(", ") }),
        el("p", { className: "status", text: sourceStatusText(source) }),
      ]),
      el("div", { className: "meta" }, [
        el("div", { text: t("sources_jobs", { n: source.nl_jobs || 0 }) }),
        el("div", { text: source.last_success ? t("sources_last_success", { date: formatDay(source.last_success) }) : t("sources_never") }),
      ]),
    ]);
  });
  $("list").replaceChildren(el("p", { className: "sources-intro", text: t("sources_intro") }), ...rows);
}

function render() {
  renderTabs();
  if (state.ui.tab === "sources") renderSources();
  else renderJobs();
}

// ---------------------------------------------------------------- actions

function toggleMark(jobId, kind) {
  const mark = { ...(state.marks[jobId] || {}) };
  mark[kind] = !mark[kind];
  if (!mark.applied && !mark.hidden) delete state.marks[jobId];
  else state.marks[jobId] = mark;
  writeStorage(STORAGE_MARKS, state.marks);
  render();
}

function selectTab(tab) {
  state.ui.tab = tab;
  render();
  document.querySelector(`.tabs button[data-tab="${tab}"]`).focus();
}

function linesOf(textarea) {
  return textarea.value.split("\n").map((line) => line.trim()).filter(Boolean);
}

function openKeywords() {
  $("kw-include").value = (state.filters.include || []).join("\n");
  $("kw-exclude").value = (state.filters.exclude || []).join("\n");
  $("kw-air-ocean").value = (state.filters.air_ocean || []).join("\n");
  $("keywords").showModal();
}

function saveKeywords() {
  state.filters = {
    ...state.defaultFilters,
    include: linesOf($("kw-include")),
    exclude: linesOf($("kw-exclude")),
    air_ocean: linesOf($("kw-air-ocean")),
  };
  writeStorage(STORAGE_FILTERS, state.filters);
  $("keywords").close();
  render();
}

function resetKeywords() {
  state.filters = structuredClone(state.defaultFilters);
  writeStorage(STORAGE_FILTERS, null);
  $("keywords").close();
  render();
}

// ---------------------------------------------------------------- setup

function applyStaticText() {
  document.title = t("page_title");
  for (const node of document.querySelectorAll("[data-text]")) node.textContent = t(node.dataset.text);
  const search = $("search");
  search.placeholder = t("search_label");
  search.setAttribute("aria-label", t("search_label"));

  const group = $("group");
  group.setAttribute("aria-label", t("group_all"));
  group.replaceChildren(
    el("option", { value: "", text: t("group_all") }),
    ...Object.keys(state.strings.groups || {}).map((key) => el("option", { value: key, text: t(`groups.${key}`) })),
  );

  const sort = $("sort");
  sort.setAttribute("aria-label", t("sort_label"));
  sort.replaceChildren(
    el("option", { value: "newest", text: t("sort_newest") }),
    el("option", { value: "company", text: t("sort_company") }),
  );
}

function bindEvents() {
  for (const button of document.querySelectorAll(".tabs button")) {
    button.addEventListener("click", () => selectTab(button.dataset.tab));
  }
  $("track").addEventListener("click", () => selectTab("sources"));
  $("search").addEventListener("input", (event) => { state.ui.search = event.target.value.trim(); render(); });
  $("group").addEventListener("change", (event) => { state.ui.group = event.target.value; render(); });
  $("sort").addEventListener("change", (event) => { state.ui.sort = event.target.value; render(); });
  $("air-ocean").addEventListener("change", (event) => { state.ui.airOcean = event.target.checked; render(); });
  $("everything").addEventListener("change", (event) => { state.ui.everything = event.target.checked; render(); });
  $("show-hidden").addEventListener("change", (event) => { state.ui.showHidden = event.target.checked; render(); });
  $("edit-keywords").addEventListener("click", openKeywords);
  $("kw-save").addEventListener("click", saveKeywords);
  $("kw-reset").addEventListener("click", resetKeywords);
}

async function loadJson(path) {
  const response = await fetch(path, { cache: "no-cache" });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

async function start() {
  state.strings = await loadJson("strings.json");
  applyStaticText();
  try {
    const [jobs, sources, filters] = await Promise.all([
      loadJson("data/jobs.json"), loadJson("data/sources.json"), loadJson("data/filters.json"),
    ]);
    state.jobs = jobs.jobs || [];
    state.generatedAt = jobs.generated_at;
    state.sources = sources.sources || [];
    state.companies = new Map(state.sources.map((s) => [s.id, s]));
    state.defaultFilters = filters;
    state.filters = readStorage(STORAGE_FILTERS, null) || structuredClone(filters);
    state.marks = readStorage(STORAGE_MARKS, {});
  } catch (error) {
    console.error(error);
    $("list").replaceChildren(el("p", { className: "empty", text: t("load_error") }));
    return;
  }
  bindEvents();
  renderMasthead();
  render();
}

start();
