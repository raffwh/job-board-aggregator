"""One-off company discovery for Workable, Recruitee, Breezy.
Run manually, NOT in the daily workflow. Commits data/<platform>_companies.json.

Usage:
    python scripts/discover_companies.py --platform recruitee
    python scripts/discover_companies.py --platform breezy
    python scripts/discover_companies.py --platform workable
    python scripts/discover_companies.py --platform all
"""

import argparse
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
HEADERS = {"User-Agent": "personal-job-board-discovery/1.0"}
REQUEST_TIMEOUT = 10
MAX_WORKERS = 12

JUNK_SLUGS = {
    "www", "api", "app", "mail", "email", "status", "blog", "docs",
    "support", "help", "cdn", "static", "assets", "go", "my",
    "login", "auth", "careers", "jobs", "apply", "dashboard",
}


def ct_log_slugs(domain_suffix):
    url = f"https://crt.sh/?q=%25.{domain_suffix}&output=json"
    slugs = set()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=120)
        resp.raise_for_status()
        entries = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logging.error("crt.sh failed for %s: %s", domain_suffix, exc)
        return slugs
    for entry in entries:
        for name in str(entry.get("name_value", "")).splitlines():
            name = name.strip().lower()
            if name.startswith("*."):
                name = name[2:]
            if not name.endswith("." + domain_suffix):
                continue
            prefix = name[: -len(domain_suffix)].rstrip(".")
            if prefix and "." not in prefix:
                slugs.add(prefix)
    return slugs


def workable_slugs_from_common_crawl(max_pages=50):
    info = requests.get("https://index.commoncrawl.org/collinfo.json",
                        headers=HEADERS, timeout=30)
    info.raise_for_status()
    api = info.json()[0]["cdx-api"]
    base = {"url": "apply.workable.com/*", "output": "json",
            "filter": "status:200", "collapse": "urlkey"}
    pages = requests.get(api, params={**base, "showNumPages": "true"},
                         headers=HEADERS, timeout=60)
    pages.raise_for_status()
    num_pages = min(int(pages.json().get("pages", 1)), max_pages)
    logging.info("Common Crawl: scanning %d pages", num_pages)

    slugs = set()
    pattern = re.compile(r"apply\.workable\.com/([a-z0-9][a-z0-9\-]{1,60})/?")
    for page in range(num_pages):
        try:
            resp = requests.get(api, params={**base, "page": page},
                                headers=HEADERS, timeout=120)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logging.warning("CC page %s failed: %s", page, exc)
            continue
        for line in resp.text.splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            m = pattern.match(record.get("url", ""))
            if m:
                slugs.add(m.group(1))
        time.sleep(1)
    return slugs


def validate_workable(slug):
    try:
        resp = requests.get(
            f"https://apply.workable.com/api/v1/widget/accounts/{slug}",
            headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None
        payload = resp.json()
        if not isinstance(payload.get("jobs"), list):
            return None
        return {"name": payload.get("name") or slug, "slug": slug}
    except (requests.RequestException, ValueError):
        return None


def validate_recruitee(slug):
    try:
        resp = requests.get(f"https://{slug}.recruitee.com/api/offers/",
                            headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None
        if not isinstance(resp.json().get("offers"), list):
            return None
        return {"name": slug, "slug": slug}
    except (requests.RequestException, ValueError):
        return None


def validate_breezy(slug):
    for url in (f"https://{slug}.breezy.hr/json",
                f"https://app.breezy.hr/json/{slug}"):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue
            payload = resp.json()
            positions = payload.get("positions", []) if isinstance(payload, dict) else payload
            if isinstance(positions, list):
                return {"name": slug, "slug": slug}
        except (requests.RequestException, ValueError):
            continue
    return None


def validate_all(slugs, validator, platform):
    candidates = sorted(s for s in slugs if s and s not in JUNK_SLUGS and len(s) <= 63)
    logging.info("%s: validating %d candidates", platform, len(candidates))
    valid = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(validator, s): s for s in candidates}
        for i, fut in enumerate(as_completed(futures), 1):
            result = fut.result()
            if result:
                valid.append(result)
            if i % 250 == 0:
                logging.info("%s: %d/%d checked, %d valid",
                             platform, i, len(candidates), len(valid))
    valid.sort(key=lambda c: c["slug"])
    return valid


def write_companies(platform, companies):
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"{platform}_companies.json"
    with open(path, "w") as fh:
        json.dump(companies, fh, indent=2)
        fh.write("\n")
    logging.info("Wrote %d companies -> %s", len(companies), path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform",
                        choices=["workable", "recruitee", "breezy", "all"],
                        default="all")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.platform in ("recruitee", "all"):
        write_companies("recruitee", validate_all(
            ct_log_slugs("recruitee.com"), validate_recruitee, "recruitee"))
    if args.platform in ("breezy", "all"):
        write_companies("breezy", validate_all(
            ct_log_slugs("breezy.hr"), validate_breezy, "breezy"))
    if args.platform in ("workable", "all"):
        write_companies("workable", validate_all(
            workable_slugs_from_common_crawl(), validate_workable, "workable"))


if __name__ == "__main__":
    main()