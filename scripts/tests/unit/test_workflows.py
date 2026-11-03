"""Static checks on the GitHub Actions workflows (offline, no network).

These tests parse the workflow YAML with PyYAML and assert the safety
invariants from Requirement 15 hold without running anything in CI:

- No ``Feashliaa`` reference survives in either workflow (Requirement 15.1).
- Every Publishing_Step is gated on ``refs/heads/main`` so branch runs never
  touch live data (Requirement 15.2): the trend commit, the release
  create/clean, the anomaly issue, and the Data_Repo push in the
  Scrape_Workflow; the Pages upload and the whole deploy job in the
  Personal_Workflow.
- The old root-level ``static.yml`` deployer is gone, leaving the Personal
  Workflow as the only Pages deployer (Requirement 15.9).
- Both workflows set ``JD_BACKEND`` (Requirement 15.6).
- A ``pytest`` step runs before the scraper step in both workflows so a failing
  Offline_Test_Suite stops the run before any scraping (Requirements 15.4, 15.5).
- In the Personal_Workflow the previous ``jobs.json`` download precedes
  ``build_site.py`` and tolerates failure (Requirement 15.10).

Validates Requirements 15.1, 15.2, 15.4, 15.5, 15.6, 15.9, 15.10.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")


WORKFLOWS_DIR = Path(__file__).resolve().parents[3] / ".github" / "workflows"
SCRAPE_WORKFLOW = WORKFLOWS_DIR / "scrape-jobs.yml"
PERSONAL_WORKFLOW = WORKFLOWS_DIR / "update-job-board.yml"

MAIN_REF = "refs/heads/main"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(_read_text(path))


def _steps(workflow: dict, job_name: str) -> list[dict]:
    return workflow["jobs"][job_name].get("steps", []) or []


def _step_by_name_contains(steps: list[dict], needle: str) -> dict:
    """Return the first step whose name contains ``needle`` (case-insensitive)."""
    for step in steps:
        name = (step.get("name") or "").lower()
        if needle.lower() in name:
            return step
    raise AssertionError(f"no step whose name contains {needle!r}")


def _step_index(steps: list[dict], predicate) -> int:
    for i, step in enumerate(steps):
        if predicate(step):
            return i
    return -1


def _gated_on_main(step: dict) -> bool:
    cond = step.get("if")
    return isinstance(cond, str) and MAIN_REF in cond


# --- Both workflows parse and files exist -----------------------------------


def test_workflow_files_exist_and_parse():
    assert SCRAPE_WORKFLOW.is_file(), "scrape-jobs.yml is missing"
    assert PERSONAL_WORKFLOW.is_file(), "update-job-board.yml is missing"
    # Must be valid YAML mappings with jobs.
    assert isinstance(_load_yaml(SCRAPE_WORKFLOW).get("jobs"), dict)
    assert isinstance(_load_yaml(PERSONAL_WORKFLOW).get("jobs"), dict)


# --- Requirement 15.1: no upstream reference --------------------------------


@pytest.mark.parametrize("path", [SCRAPE_WORKFLOW, PERSONAL_WORKFLOW])
def test_no_feashliaa_reference(path):
    text = _read_text(path)
    assert "Feashliaa" not in text, f"{path.name} still references the upstream owner"
    # Case-insensitive guard against lower/mixed-case variants.
    assert "feashliaa" not in text.lower(), f"{path.name} still references the upstream owner"


# --- Requirement 15.9: the old static deployer is gone ----------------------


def test_static_workflow_absent():
    static_yml = WORKFLOWS_DIR / "static.yml"
    assert not static_yml.exists(), "static.yml must be deleted (single Pages deployer)"


# --- Requirement 15.6: JD_BACKEND is set in both workflows ------------------


def test_scrape_workflow_sets_jd_backend():
    wf = _load_yaml(SCRAPE_WORKFLOW)
    job_env = wf["jobs"]["scrape"].get("env", {})
    # Set at the job level in the Scrape_Workflow.
    assert "JD_BACKEND" in job_env, "Scrape_Workflow does not set JD_BACKEND"
    assert "JD_BACKEND" in _read_text(SCRAPE_WORKFLOW)


def test_personal_workflow_sets_jd_backend():
    wf = _load_yaml(PERSONAL_WORKFLOW)
    steps = _steps(wf, "build")
    # Set on the scraper step's env in the Personal_Workflow.
    has_jd_backend = any("JD_BACKEND" in (step.get("env") or {}) for step in steps)
    assert has_jd_backend, "Personal_Workflow does not set JD_BACKEND on any step"


# --- Requirements 15.4, 15.5: pytest runs before the scraper ----------------


def _is_pytest_step(step: dict) -> bool:
    run = step.get("run")
    return isinstance(run, str) and "pytest" in run


def _is_scraper_step(step: dict) -> bool:
    run = step.get("run")
    return isinstance(run, str) and "scraper.py" in run


def test_scrape_workflow_pytest_before_scraper():
    steps = _steps(_load_yaml(SCRAPE_WORKFLOW), "scrape")
    pytest_idx = _step_index(steps, _is_pytest_step)
    scraper_idx = _step_index(steps, _is_scraper_step)
    assert pytest_idx != -1, "Scrape_Workflow has no pytest step"
    assert scraper_idx != -1, "Scrape_Workflow has no scraper step"
    assert pytest_idx < scraper_idx, "pytest must run before the scraper step"


def test_personal_workflow_pytest_before_scraper():
    steps = _steps(_load_yaml(PERSONAL_WORKFLOW), "build")
    pytest_idx = _step_index(steps, _is_pytest_step)
    scraper_idx = _step_index(steps, _is_scraper_step)
    assert pytest_idx != -1, "Personal_Workflow has no pytest step"
    assert scraper_idx != -1, "Personal_Workflow has no scraper step"
    assert pytest_idx < scraper_idx, "pytest must run before the scraper step"


# --- Requirement 15.2: every Publishing_Step is gated on main ---------------


def test_scrape_workflow_publishing_steps_gated_on_main():
    steps = _steps(_load_yaml(SCRAPE_WORKFLOW), "scrape")

    # The Publishing_Steps named by the design: the trend commit, release
    # create/clean, the anomaly issue, and the Data_Repo push. The read-only
    # "Fetch existing data from data repo" clone is deliberately NOT a
    # Publishing_Step -- it runs on every ref gated only on DATA_REPO -- so it
    # is excluded here.
    publishing_step_names = [
        "clean old releases",   # release delete
        "create release",       # release create
        "trend history",        # commit trend history to main
        "check for platform anomalies",
        "open issue on anomaly",
        "push regenerated data",  # Data_Repo push
    ]

    for key in publishing_step_names:
        step = _step_by_name_contains(steps, key)
        assert _gated_on_main(step), (
            f"Publishing_Step {step.get('name')!r} is not gated on {MAIN_REF}"
        )

    # The Data_Repo push additionally requires DATA_REPO to be set.
    push = _step_by_name_contains(steps, "push regenerated data")
    assert "env.DATA_REPO != ''" in (push.get("if") or ""), (
        "Data_Repo push must also require DATA_REPO to be set"
    )

    # The read-only data fetch is NOT gated on main (runs on every ref).
    fetch = _step_by_name_contains(steps, "fetch existing data")
    assert not _gated_on_main(fetch), (
        "the read-only data fetch should run on every ref, not be main-gated"
    )


def test_personal_workflow_pages_upload_and_deploy_gated_on_main():
    wf = _load_yaml(PERSONAL_WORKFLOW)
    build_steps = _steps(wf, "build")

    pages_upload = _step_by_name_contains(build_steps, "pages artifact")
    assert _gated_on_main(pages_upload), "Pages upload must be gated on main"

    # The deploy job as a whole is gated on main.
    deploy_job = wf["jobs"]["deploy"]
    deploy_if = deploy_job.get("if")
    assert isinstance(deploy_if, str) and MAIN_REF in deploy_if, (
        "deploy job must be gated on main"
    )


# --- Requirement 15.10: previous jobs.json download precedes build_site -----


def test_personal_workflow_previous_jobs_download_before_build_site():
    steps = _steps(_load_yaml(PERSONAL_WORKFLOW), "build")

    def _is_prev_jobs_step(step: dict) -> bool:
        run = step.get("run")
        return isinstance(run, str) and "jobs.json" in run and "curl" in run

    def _is_build_site_step(step: dict) -> bool:
        run = step.get("run")
        return isinstance(run, str) and "build_site.py" in run

    prev_idx = _step_index(steps, _is_prev_jobs_step)
    build_idx = _step_index(steps, _is_build_site_step)
    assert prev_idx != -1, "no previous jobs.json download step"
    assert build_idx != -1, "no build_site.py step"
    assert prev_idx < build_idx, "previous jobs.json download must precede build_site.py"

    # The download must tolerate failure: a `|| echo ...` fallback keeps the
    # step from failing the run when the file is missing.
    prev_step = steps[prev_idx]
    assert "||" in prev_step["run"], (
        "previous jobs.json download must tolerate failure (non-fatal)"
    )
