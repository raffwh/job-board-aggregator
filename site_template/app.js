const STORAGE_KEY = "personalJobBoardState";
const NEW_BADGE_DAYS = 3;

const jobsElement = document.getElementById("jobs");
const statusElement = document.getElementById("status");
const searchElement = document.getElementById("search");
const skillFilterElement = document.getElementById("skillFilter");
const sortElement = document.getElementById("sortBy");
const categoryFilterElement = document.getElementById("categoryFilter");
const levelElement = document.getElementById("levelFilter");
const atsElement = document.getElementById("atsFilter");
const freshnessElement = document.getElementById("freshness");
const dsOnlyElement = document.getElementById("dsOnly");
const remoteOnlyElement = document.getElementById("remoteOnly");
const hideHiddenElement = document.getElementById("hideHidden");
const hideAppliedElement = document.getElementById("hideApplied");
const hideSeniorElement = document.getElementById("hideSenior");
const showMaybeElement = document.getElementById("showMaybe");
const savedOnlyElement = document.getElementById("savedOnly");
const nearBostonElement = document.getElementById("nearBoston");

const SENIOR_TITLE_RE = /\b(principal|director|vp|vice\s*president|head\s*of|chief|staff\s*(data|ml|ai)|distinguished)\b/i;

// Ordered category sections (Requirements 14.18, 14.19).
const CATEGORY_SECTIONS = [
  { key: "data_scientist", label: "Data Scientist", expanded: true },
  { key: "life_sciences_research", label: "Life-Sciences Research / Market Research", expanded: false },
  { key: "other_target", label: "Other Targets", expanded: false },
];

// Readable labels for a maybe job's match_reason (Requirement 14.4).
const MATCH_REASON_LABELS = {
  location_unknown: "location unclear",
  remote_unspecified: "remote, country not stated",
  tier_senior_signal: "senior level",
  title_maybe: "adjacent title",
  title_generic_analyst: "generic analyst",
};

let jobs = [];
let bostonRadiusMiles = 30;
let state = loadState();
const collapsed = {};
for (const section of CATEGORY_SECTIONS) {
  collapsed[section.key] = !section.expanded;
}

function loadState() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
  } catch {
    return {};
  }
}

function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function jobKey(job) {
  return job.id || `${job.company}::${job.title}::${job.url}`;
}

function text(value) {
  return value || "";
}

// Insert all interpolated job text as escaped HTML (Requirement 14.7).
function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Only allow http(s) URLs through; anything else becomes empty (Requirement 14.7).
function safeUrl(value) {
  if (!value) return "";
  try {
    const parsed = new URL(value, window.location.href);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.href;
    }
  } catch {
    return "";
  }
  return "";
}

function skillList(job) {
  return Array.isArray(job.skills) ? job.skills : [];
}

function populateAtsOptions() {
  const values = [...new Set(jobs.map((job) => job.ats).filter(Boolean))].sort();
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    atsElement.appendChild(option);
  }
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Date unavailable";
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function jobDate(job) {
  const value = job.published_at || job.updated_at;
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

// A job is "new" when its first_seen is within NEW_BADGE_DAYS (Requirement 14.13).
function isNew(job, now = Date.now()) {
  const seen = Date.parse(job.first_seen);
  if (Number.isNaN(seen)) return false;
  return now - seen <= NEW_BADGE_DAYS * 24 * 60 * 60 * 1000;
}

// Freshness filter on posting date OR first_seen (Requirement 14.14).
function matchesFreshness(job, days, now = Date.now()) {
  if (!days) return true;
  const windowMs = days * 24 * 60 * 60 * 1000;
  const posted = jobDate(job);
  const seen = Date.parse(job.first_seen);
  const postedOk = posted > 0 && now - posted <= windowMs;
  const seenOk = !Number.isNaN(seen) && now - seen <= windowMs;
  return postedOk || seenOk;
}

// Category filter (Requirement 14.20). Empty value means all categories.
function matchesCategory(job, category) {
  if (!category) return true;
  return job.category === category;
}

// Near Boston: remote jobs, or jobs within the configured radius (Requirement 14.5).
function isNearBoston(job) {
  return (
    job.workplace_type === "remote" ||
    (job.distance_miles != null && job.distance_miles <= bostonRadiusMiles)
  );
}

// Case-insensitive skill filter; comma-separated terms must all match (Requirement 14.6).
function matchesSkillFilter(job, filterValue) {
  const terms = filterValue
    .split(",")
    .map((term) => term.trim().toLowerCase())
    .filter(Boolean);
  if (terms.length === 0) return true;
  const skills = skillList(job).map((skill) => skill.toLowerCase());
  return terms.every((term) => skills.some((skill) => skill.includes(term)));
}

function reasonBadge(job) {
  if (job.match_type !== "maybe" || !job.match_reason) return "";
  let label;
  if (job.match_reason === "location_new_england") {
    const miles = job.distance_miles != null ? ` ${Math.round(job.distance_miles)} mi` : "";
    label = `New England,${miles}`.trim();
  } else {
    label = MATCH_REASON_LABELS[job.match_reason] || job.match_reason;
  }
  return `<span class="badge reason">${escapeHtml(label)}</span>`;
}

function sortJobs(list) {
  const mode = sortElement.value;
  const copy = [...list];

  if (mode === "company") {
    copy.sort((a, b) =>
      text(a.company).localeCompare(text(b.company)) ||
      jobDate(b) - jobDate(a)
    );
  } else if (mode === "title") {
    copy.sort((a, b) =>
      text(a.title).localeCompare(text(b.title)) ||
      jobDate(b) - jobDate(a)
    );
  } else if (mode === "newest") {
    copy.sort((a, b) =>
      jobDate(b) - jobDate(a) ||
      text(a.company).localeCompare(text(b.company)) ||
      text(a.title).localeCompare(text(b.title))
    );
  } else {
    // "priority": Data Scientist first (title_priority ascending), then newest (Requirement 14.11).
    copy.sort((a, b) =>
      ((a.title_priority || 2) - (b.title_priority || 2)) ||
      jobDate(b) - jobDate(a) ||
      text(a.company).localeCompare(text(b.company)) ||
      text(a.title).localeCompare(text(b.title))
    );
  }

  return copy;
}

function passesFilters(job, filters) {
  const key = jobKey(job);
  const jobState = state[key] || {};

  if (filters.hideHidden && jobState.hidden) return false;
  if (filters.hideApplied && jobState.applied) return false;
  if (filters.hideSenior && SENIOR_TITLE_RE.test(job.title)) return false;
  if (job.match_type === "maybe" && !filters.showMaybe) return false;
  if (filters.dsOnly && job.title_priority !== 1) return false;
  if (filters.level && job.skill_level !== filters.level) return false;
  if (filters.ats && job.ats !== filters.ats) return false;
  if (filters.remoteOnly && !job.remote) return false;
  if (filters.savedOnly && !jobState.saved) return false;
  if (filters.nearBoston && !isNearBoston(job)) return false;
  if (!matchesFreshness(job, filters.freshness)) return false;
  if (!matchesSkillFilter(job, filters.skillFilter)) return false;

  const haystack = `${text(job.title)} ${text(job.company)} ${text(job.location)} ${skillList(job).join(" ")}`.toLowerCase();
  return haystack.includes(filters.query);
}

function buildRow(job) {
  const key = jobKey(job);
  const jobState = state[key] || {};

  const row = document.createElement("div");
  row.className = "job";
  if (jobState.hidden) row.classList.add("is-hidden");
  if (jobState.applied) row.classList.add("is-applied");
  if (jobState.saved) row.classList.add("is-saved");

  const url = safeUrl(job.url);
  const titleHtml = `<div class="title">${escapeHtml(job.title)}</div>`;
  const titleBlock = url
    ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${titleHtml}</a>`
    : titleHtml;

  const skills = skillList(job);
  const skillsHtml = skills.length
    ? `<div class="skills">${skills
        .map((skill) => `<span class="skill">${escapeHtml(skill)}</span>`)
        .join("")}</div>`
    : "";

  const summaryHtml = job.summary
    ? `<div class="summary">${escapeHtml(job.summary)}</div>`
    : "";

  row.innerHTML = `
    <div class="job-main">
      ${titleBlock}
      <div class="meta">
        <span class="badge">${escapeHtml(job.ats)}</span>
        <span class="badge">${escapeHtml(job.skill_level)}</span>
        ${job.title_priority === 1 ? '<span class="badge ds">Data Scientist</span>' : ""}
        ${job.remote ? '<span class="badge">Remote</span>' : ""}
        ${isNew(job) ? '<span class="badge new">New</span>' : ""}
        ${reasonBadge(job)}
        ${escapeHtml(job.company)} · ${escapeHtml(job.location)}
      </div>
      <div class="dates">
        Posted: ${escapeHtml(formatDate(job.published_at || job.updated_at))}
        · First seen: ${escapeHtml(formatDate(job.first_seen))}
      </div>
      ${skillsHtml}
      ${summaryHtml}
    </div>
    <div class="job-actions">
      <button data-action="save" class="${jobState.saved ? "active-save" : ""}">
        ${jobState.saved ? "Saved" : "Save"}
      </button>
      <button data-action="apply" class="${jobState.applied ? "active-apply" : ""}">
        ${jobState.applied ? "Applied" : "Mark applied"}
      </button>
      <button data-action="hide">
        ${jobState.hidden ? "Unhide" : "Hide"}
      </button>
    </div>
  `;

  row.querySelector('[data-action="save"]').addEventListener("click", () => {
    jobState.saved = !jobState.saved;
    state[key] = jobState;
    saveState();
    render();
  });

  row.querySelector('[data-action="apply"]').addEventListener("click", () => {
    jobState.applied = !jobState.applied;
    state[key] = jobState;
    saveState();
    render();
  });

  row.querySelector('[data-action="hide"]').addEventListener("click", () => {
    jobState.hidden = !jobState.hidden;
    state[key] = jobState;
    saveState();
    render();
  });

  return row;
}

// Draw one collapsible section header and its job rows (Requirements 14.18, 14.19).
function renderSection(section, sectionJobs) {
  const wrapper = document.createElement("section");
  wrapper.className = "category-section";
  if (collapsed[section.key]) wrapper.classList.add("collapsed");

  const header = document.createElement("button");
  header.type = "button";
  header.className = "category-header";
  header.innerHTML = `
    <span class="toggle">${collapsed[section.key] ? "▸" : "▾"}</span>
    <span class="label">${escapeHtml(section.label)}</span>
    <span class="count">(${sectionJobs.length.toLocaleString()})</span>
  `;
  header.addEventListener("click", () => {
    collapsed[section.key] = !collapsed[section.key];
    render();
  });
  wrapper.appendChild(header);

  const body = document.createElement("div");
  body.className = "category-body";
  // Primary first, then maybe, keeping each section's primary/maybe split (Requirement 14.18).
  const primary = sectionJobs.filter((job) => job.match_type !== "maybe");
  const maybe = sectionJobs.filter((job) => job.match_type === "maybe");
  for (const job of [...primary, ...maybe].slice(0, 500)) {
    body.appendChild(buildRow(job));
  }
  wrapper.appendChild(body);

  return wrapper;
}

function render() {
  const filters = {
    query: searchElement.value.trim().toLowerCase(),
    skillFilter: skillFilterElement.value.trim(),
    category: categoryFilterElement.value,
    level: levelElement.value,
    ats: atsElement.value,
    freshness: freshnessElement.value ? Number(freshnessElement.value) : 0,
    dsOnly: dsOnlyElement.checked,
    remoteOnly: remoteOnlyElement.checked,
    nearBoston: nearBostonElement.checked,
    hideHidden: hideHiddenElement.checked,
    hideApplied: hideAppliedElement.checked,
    hideSenior: hideSeniorElement.checked,
    showMaybe: showMaybeElement.checked,
    savedOnly: savedOnlyElement.checked,
  };

  const visible = sortJobs(jobs.filter((job) => passesFilters(job, filters)));

  statusElement.textContent = `${visible.length.toLocaleString()} matching jobs`;

  jobsElement.innerHTML = "";

  let shown = 0;
  for (const section of CATEGORY_SECTIONS) {
    if (filters.category && filters.category !== section.key) continue;
    const sectionJobs = visible.filter((job) => matchesCategory(job, section.key));
    if (sectionJobs.length === 0) continue;
    jobsElement.appendChild(renderSection(section, sectionJobs));
    shown += sectionJobs.length;
  }

  if (shown === 0) {
    const empty = document.createElement("p");
    empty.id = "status";
    empty.textContent = "No jobs match the current filters.";
    jobsElement.appendChild(empty);
  }
}

fetch("metadata.json")
  .then((response) => (response.ok ? response.json() : null))
  .then((data) => {
    if (data && typeof data.boston_radius_miles === "number") {
      bostonRadiusMiles = data.boston_radius_miles;
    }
  })
  .catch(() => {})
  .finally(() => {
    fetch("jobs.json")
      .then((response) => response.json())
      .then((data) => {
        jobs = data;
        populateAtsOptions();
        render();
      })
      .catch(() => {
        statusElement.textContent = "Could not load job data.";
      });
  });

[
  searchElement,
  skillFilterElement,
  sortElement,
  categoryFilterElement,
  levelElement,
  atsElement,
  freshnessElement,
  dsOnlyElement,
  remoteOnlyElement,
  nearBostonElement,
  hideHiddenElement,
  hideAppliedElement,
  hideSeniorElement,
  showMaybeElement,
  savedOnlyElement,
].forEach((element) => element.addEventListener("input", render));
