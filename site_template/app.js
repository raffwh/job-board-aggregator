const STORAGE_KEY = "personalJobBoardState";

const jobsElement = document.getElementById("jobs");
const statusElement = document.getElementById("status");
const searchElement = document.getElementById("search");
const sortElement = document.getElementById("sortBy");
const levelElement = document.getElementById("levelFilter");
const atsElement = document.getElementById("atsFilter");
const remoteOnlyElement = document.getElementById("remoteOnly");
const hideHiddenElement = document.getElementById("hideHidden");
const hideAppliedElement = document.getElementById("hideApplied");
const showMaybeElement = document.getElementById("showMaybe");

let jobs = [];
let state = loadState();

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
  return `${job.company}::${job.title}::${job.url}`;
}

function text(value) {
  return value || "";
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

function sortJobs(list) {
  const mode = sortElement.value;
  const copy = [...list];
  if (mode === "company") {
    copy.sort((a, b) => text(a.company).localeCompare(text(b.company)));
  } else if (mode === "title") {
    copy.sort((a, b) => text(a.title).localeCompare(text(b.title)));
  } else {
    copy.sort((a, b) => text(b.updated_at).localeCompare(text(a.updated_at)));
  }
  return copy;
}

function render() {
  const query = searchElement.value.trim().toLowerCase();
  const level = levelElement.value;
  const ats = atsElement.value;
  const remoteOnly = remoteOnlyElement.checked;
  const hideHidden = hideHiddenElement.checked;
  const hideApplied = hideAppliedElement.checked;

  let visible = jobs.filter((job) => {
    const key = jobKey(job);
    const jobState = state[key] || {};

    if (hideHidden && jobState.hidden) return false;
    if (hideApplied && jobState.applied) return false;
    if (job.match_type === "maybe" && !showMaybeElement.checked) return false;
    if (level && job.skill_level !== level) return false;
    if (ats && job.ats !== ats) return false;
    if (remoteOnly && !job.remote) return false;

    const haystack = `${text(job.title)} ${text(job.company)} ${text(job.location)}`.toLowerCase();
    return haystack.includes(query);
  });

  visible = sortJobs(visible);

  statusElement.textContent = `${visible.length.toLocaleString()} matching jobs`;

  jobsElement.innerHTML = "";

  for (const job of visible.slice(0, 500)) {
    const key = jobKey(job);
    const jobState = state[key] || {};

    const row = document.createElement("div");
    row.className = "job";
    if (jobState.hidden) row.classList.add("is-hidden");
    if (jobState.applied) row.classList.add("is-applied");
    if (jobState.saved) row.classList.add("is-saved");

    row.innerHTML = `
      <div class="job-main">
        <a href="${job.url}" target="_blank" rel="noopener noreferrer">
          <div class="title">${text(job.title)}</div>
        </a>
        <div class="meta">
          <span class="badge">${text(job.ats)}</span>
          <span class="badge">${text(job.skill_level)}</span>
          ${job.remote ? '<span class="badge">Remote</span>' : ""}
          ${text(job.company)} · ${text(job.location)}
        </div>
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

    jobsElement.appendChild(row);
  }
}

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

[searchElement, sortElement, levelElement, atsElement, remoteOnlyElement, hideHiddenElement, hideAppliedElement, showMaybeElement]
  .forEach((element) => element.addEventListener("input", render));