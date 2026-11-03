"""Smoke test: importing ``scraper`` must not read the 29 MB locations.json.

Requirement 16.5: when a test imports the Scraper module, the Scraper defers
loading ``locations.json`` until location lookup is first used. The lazy
``get_location_maps()`` (an ``lru_cache``'d function) is what makes this hold:
the import-time ``LOCATION_MAPS = build_lookup(...)`` was removed.

The test patches ``builtins.open`` with a spy that records every path it is
asked to open, then re-imports the module under that patch and asserts no
opened path mentions ``locations.json``. ``scraper`` is almost certainly
already imported by the time this test runs (other unit tests import it), so
``importlib.reload`` is used to actually exercise the import path again under
the spy rather than relying on a first import that already happened.

Validates: Requirement 16.5
"""

import builtins
import importlib
from unittest.mock import patch

import scraper


def _make_open_spy():
    """Return (spy_open, opened_paths) wrapping the real ``builtins.open``.

    The spy records the string form of every path argument it receives and
    then delegates to the genuine ``open`` so the module still imports.
    """
    real_open = builtins.open
    opened_paths = []

    def spy_open(file, *args, **kwargs):
        opened_paths.append(str(file))
        return real_open(file, *args, **kwargs)

    return spy_open, opened_paths


def test_importing_scraper_does_not_open_locations_json():
    """Re-importing ``scraper`` opens no path containing ``locations.json``."""
    spy_open, opened_paths = _make_open_spy()

    # Drop any cached location maps so a lazy load would be observable, then
    # reload the module with the spy in place. The reload re-runs module-level
    # code (imports, constants, function defs) exactly as a fresh import would.
    scraper.get_location_maps.cache_clear()
    with patch("builtins.open", spy_open):
        importlib.reload(scraper)

    offenders = [p for p in opened_paths if "locations.json" in p]
    assert not offenders, (
        "import scraper opened locations.json during import: "
        f"{offenders}. Location loading must be deferred to "
        "get_location_maps() (Requirement 16.5)."
    )


def test_spy_observes_open_when_location_maps_are_built():
    """Positive control: the spy does see locations.json once lookup runs.

    This guards against a false pass where the first assertion holds only
    because the spy never observes any ``open`` call. Building the maps through
    ``get_location_maps()`` must open the real ``locations.json`` path.
    """
    spy_open, opened_paths = _make_open_spy()

    scraper.get_location_maps.cache_clear()
    with patch("builtins.open", spy_open):
        scraper.get_location_maps()
    scraper.get_location_maps.cache_clear()

    assert any("locations.json" in p for p in opened_paths), (
        "get_location_maps() should open locations.json; the spy observed "
        f"these paths instead: {opened_paths}"
    )
