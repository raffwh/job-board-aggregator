"""Pure helper functions shared by ATS fetchers.

These helpers are intentionally free of any network access or global state so
they can be unit-tested in isolation. They cover:

- Workday slug parsing/formatting/validation (Requirements 3.11, 3.12).
- Requisition ID extraction from a Workday posting's ``bulletFields``
  (Requirement 3.9).
- Deriving a primary location hint from a Workday posting's ``externalPath``
  (Requirement 3.10).
- iCIMS host-candidate resolution from a company slug (Requirements 4.1, 4.2).
- Parsing posting dates in the several shapes ATS platforms emit
  (Requirement 7.17).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Workday slug parsing / formatting / validation
# ---------------------------------------------------------------------------

# A Workday Company_List entry has the shape ``tenant|wdN|site_id`` where N is a
# positive integer. The tenant and site_id are lowercased on parse so that
# formatting the parsed parts reproduces the original slug in lowercase
# (Requirement 3.12).
_WORKDAY_SLUG_RE = re.compile(r"^[a-z0-9_-]+\|wd\d+\|[A-Za-z0-9_-]+$", re.IGNORECASE)
_WD_INSTANCE_RE = re.compile(r"^wd(\d+)$", re.IGNORECASE)


def parse_workday_slug(slug: str) -> Optional[Tuple[str, int, str]]:
    """Parse a Workday slug into ``(tenant, instance, site_id)``.

    ``instance`` is the positive integer from the ``wdN`` segment. Returns
    ``None`` when the slug does not match the Workday_Slug format or when the
    tenant itself looks like a ``wdN`` segment (an invalid entry such as
    ``wd1|wd1|iqvia``). Requirements 3.11, 3.12.
    """
    if not isinstance(slug, str):
        return None
    if not validate_workday_slug(slug):
        return None

    tenant, wd_part, site_id = slug.strip().split("|")
    instance = int(_WD_INSTANCE_RE.match(wd_part).group(1))
    return tenant.lower(), instance, site_id.lower()


def format_workday_slug(tenant: str, instance: int, site_id: str) -> str:
    """Format ``(tenant, instance, site_id)`` back into a Workday slug.

    The result is lowercase, so formatting the output of
    :func:`parse_workday_slug` reproduces the original slug in lowercase
    (Requirement 3.12).
    """
    return f"{tenant}|wd{int(instance)}|{site_id}".lower()


def validate_workday_slug(slug: str) -> bool:
    """Return ``True`` when ``slug`` is a usable Workday_Slug.

    The entry must match ``^[a-z0-9_-]+\\|wd\\d+\\|[A-Za-z0-9_-]+$`` and its
    tenant must not itself be a ``wdN`` segment (Requirement 3.11).
    """
    if not isinstance(slug, str):
        return False
    s = slug.strip()
    if not _WORKDAY_SLUG_RE.match(s):
        return False
    tenant = s.split("|", 1)[0]
    if _WD_INSTANCE_RE.match(tenant):
        return False
    return True


# ---------------------------------------------------------------------------
# Requisition ID extraction
# ---------------------------------------------------------------------------

# Matches typical Workday requisition identifiers such as "R1575320",
# "JR-12345", "REQ-000123", or a bare numeric id of 3+ digits.
_REQ_ID_RE = re.compile(r"^[A-Z]{0,4}-?\d{3,}[\w-]*$")


def extract_req_id(bullet_fields) -> Optional[str]:
    """Extract a requisition ID from a Workday posting's ``bulletFields``.

    ``bulletFields`` is a list of short strings Workday attaches to each
    posting; the requisition ID is the first element matching
    ``^[A-Z]{0,4}-?\\d{3,}[\\w-]*$`` (Requirement 3.9). Returns ``None`` when
    no element matches or the input is not a list.
    """
    if not isinstance(bullet_fields, (list, tuple)):
        return None
    for field in bullet_fields:
        if not isinstance(field, str):
            continue
        candidate = field.strip()
        if _REQ_ID_RE.match(candidate):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Workday multi-location: derive a primary location hint from externalPath
# ---------------------------------------------------------------------------


def workday_location_from_path(external_path) -> Optional[str]:
    """Derive a primary location hint from a Workday posting's ``externalPath``.

    Workday external paths look like
    ``/job/<Location-With-Dashes>/<Title>_R1575320``. When ``locationsText`` is
    ``"N Locations"`` the real location is encoded as the path segment after
    ``/job/``. This converts that segment's dashes to spaces, e.g.
    ``/job/So-Paulo-Brazil/...`` -> ``"So Paulo Brazil"`` (Requirement 3.10).

    Returns ``None`` when no location segment can be derived. The result is a
    hint only; the Detail_Enricher replaces it with real values.
    """
    if not isinstance(external_path, str):
        return None
    path = external_path.strip()
    if not path:
        return None

    segments = [seg for seg in path.split("/") if seg]
    location_segment = None
    if "job" in segments:
        idx = segments.index("job")
        if idx + 1 < len(segments):
            location_segment = segments[idx + 1]
    elif segments:
        # Fall back to the first segment if the path does not contain "job".
        location_segment = segments[0]

    if not location_segment:
        return None

    location = location_segment.replace("-", " ").replace("_", " ").strip()
    location = re.sub(r"\s+", " ", location)
    return location or None


# ---------------------------------------------------------------------------
# iCIMS host-candidate resolution
# ---------------------------------------------------------------------------

# Known iCIMS subdomain prefixes. A slug that already begins with one of these
# is a full subdomain (``careers-gbrx``) and must be fetched as
# ``<slug>.icims.com``; prepending ``careers-`` again yields the 404-ing
# ``careers-careers-gbrx.icims.com`` host that caused ~58.5% of iCIMS 404s.
#
# The list combines the known set (Requirement 4.1) with every first path
# segment in ``data/icims_companies.json`` that ends with ``careers``/``jobs``
# and appears more than 5 times (derived by a one-off count during
# implementation, per the design). The trailing hyphen is part of each prefix
# so that ``en-`` does not match a bare slug that merely starts with ``en``.
ICIMS_PREFIXES = (
    "careers-",
    "jobs-",
    "external-",
    "uscareers-",
    "careers2-",
    "internal-",
    "careersen-",
    "uscareers2-",
    "global-",
    "en-",
    # Derived from the Company_List (first segment ends careers/jobs, count > 5):
    "globalcareers-",
    "internationalcareers-",
    "corporatecareers-",
    "ukcareers-",
    "canadacareers-",
    "frcareers-",
    "hourlycareers-",
    "indiacareers-",
)


def icims_host_candidates(slug: str) -> List[str]:
    """Return the ordered iCIMS host candidates for ``slug``.

    When the slug already begins with a known subdomain prefix, the first
    candidate is ``<slug>.icims.com`` and the fallback is
    ``careers-<slug>.icims.com`` (Requirement 4.1). Otherwise the first
    candidate is ``careers-<slug>.icims.com`` and the fallback is
    ``<slug>.icims.com`` (Requirement 4.2).

    Both candidates always end in ``.icims.com`` and the first never starts
    with ``careers-careers-`` unless the slug itself does.
    """
    s = (slug or "").strip().lower()
    if s.startswith(ICIMS_PREFIXES):
        return [f"{s}.icims.com", f"careers-{s}.icims.com"]
    return [f"careers-{s}.icims.com", f"{s}.icims.com"]


# ---------------------------------------------------------------------------
# Posting-date parsing
# ---------------------------------------------------------------------------

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_posted(value) -> Optional[datetime]:
    """Parse a posting date into a timezone-aware UTC ``datetime``.

    Accepts (Requirement 7.17):

    - ISO 8601 with a ``Z`` suffix (``2024-01-02T03:04:05Z``).
    - ISO 8601 with an explicit offset (``2024-01-02T03:04:05+02:00``).
    - ISO 8601 with no offset (treated as UTC).
    - Date-only values (``2024-01-02``).
    - Epoch milliseconds, as an int or a numeric string.

    Returns ``None`` when the value is missing or cannot be parsed. All
    returned datetimes are timezone-aware in UTC so they can be compared and
    sorted consistently.
    """
    if value is None:
        return None

    # Epoch milliseconds given as a number.
    if isinstance(value, bool):
        # bool is a subclass of int; never a valid timestamp.
        return None
    if isinstance(value, (int, float)):
        return _from_epoch_millis(value)

    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    # Epoch milliseconds given as a numeric string (optionally negative).
    if re.fullmatch(r"-?\d+", text):
        try:
            return _from_epoch_millis(int(text))
        except (ValueError, OverflowError, OSError):
            return None

    # Date-only value.
    if _DATE_ONLY_RE.match(text):
        try:
            dt = datetime.strptime(text, "%Y-%m-%d")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    # ISO 8601 with or without Z / offset.
    iso = text
    if iso.endswith("Z") or iso.endswith("z"):
        iso = iso[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _from_epoch_millis(millis) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(float(millis) / 1000.0, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


__all__: List[str] = [
    "parse_workday_slug",
    "format_workday_slug",
    "validate_workday_slug",
    "extract_req_id",
    "workday_location_from_path",
    "ICIMS_PREFIXES",
    "icims_host_candidates",
    "parse_posted",
]
