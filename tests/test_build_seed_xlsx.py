"""The seed builder's copy of the duty table must match the web app's.

build_seed_xlsx.py runs in this venv, where duty_web isn't installed, so it
keeps its own copy of the profiles. A silent drift would mean the next
schedule import writes a café the app doesn't recognise — so load the app's
module straight off disk and compare.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DUTIES_PATH = REPO_ROOT / "web" / "src" / "duty_web" / "duties.py"


def _load_web_duties():
    spec = importlib.util.spec_from_file_location("duty_web_duties", DUTIES_PATH)
    module = importlib.util.module_from_spec(spec)
    # duties.py defines a dataclass under `from __future__ import annotations`,
    # and @dataclass resolves those annotations through sys.modules — so the
    # module has to be registered before it is executed, not after.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seed_builder():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import build_seed_xlsx

        return build_seed_xlsx
    finally:
        sys.path.pop(0)


def test_the_two_profile_tables_describe_the_same_duties(seed_builder):
    web = _load_web_duties()

    assert set(seed_builder.DUTY_PROFILES) == set(web.DUTY_PROFILES)


def test_venue_and_station_agree_for_every_duty(seed_builder):
    web = _load_web_duties()

    for name, (venue, station) in seed_builder.DUTY_PROFILES.items():
        profile = web.DUTY_PROFILES[name]
        assert (venue, station) == (profile.venue, profile.station), name


def test_the_renames_agree(seed_builder):
    web = _load_web_duties()

    assert seed_builder.RENAMED_DUTIES == web.RENAMED_DUTIES


def test_no_duty_is_renamed_to_a_name_without_a_profile(seed_builder):
    for new_name in seed_builder.RENAMED_DUTIES.values():
        assert new_name in seed_builder.DUTY_PROFILES
