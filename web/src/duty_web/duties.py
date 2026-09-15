"""What parents need to know about a duty beyond what the schedule file says.

The trainer's spreadsheet names an activity and, for the arena duties, an
arena. It doesn't say which café inside that building, and it doesn't say
which instruction to read before the shift. That is the team's own knowledge,
it changes about once a season, and it has to agree in three places (the web
app, the reminder mail, and the next schedule import) — so it lives here.

`venue` and `station` are *written onto the slots* rather than looked up at
render time: scripts/build_seed_xlsx.py fills them when a schedule is built,
and scripts/rename_duty_profiles.py backfills the slots already imported. So
everything that reads a Slot sees them without consulting this table. The
document is the exception — Slot has no column for it, and one would only
restate what is written here.

The club's remaining instructions (Arena A övre plan, WA C, Hindås,
Landvetter Pinntorp) are uploaded to the volume but not mapped: no duty is
held there yet. Arena A would be `station="Café Arena A, övre plan"` with
"Cafeteria_Instruktion_W_Arena_A_övre_plan.pdf".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DutyProfile:
    venue: str
    station: str
    document: str | None


DUTY_PROFILES: dict[str, DutyProfile] = {
    "Bästkustcupen": DutyProfile(
        venue="Mölnlycke idrottshall",
        station="Café",
        document="Cafeteria_Instruktion_MIH.pdf",
    ),
    "Cafépass": DutyProfile(
        venue="Wallenstam arena",
        station="Café Arena B, nedre plan",
        document="Cafeteria_Instruktion_W_Arena_B_nedre_plan.pdf",
    ),
}

# The spreadsheet's own names for duties that are now called something else.
# Both arena-hosting terms are the same duty to a parent, so they share a
# name; the undated block already tells VT27 apart from the autumn shifts.
RENAMED_DUTIES: dict[str, str] = {
    "Arena värdskap höst": "Cafépass",
    "Arena värdskap vinter": "Cafépass",
}

_BY_KEY = {name.casefold(): name for name in DUTY_PROFILES}
_RENAMES_BY_KEY = {name.casefold(): new for name, new in RENAMED_DUTIES.items()}


def canonical_duty_name(name: str | None) -> str:
    """The name a duty goes by now. Anything unrenamed passes through."""
    stripped = " ".join((name or "").split())
    return _RENAMES_BY_KEY.get(stripped.casefold(), stripped)


def profile_for(name: str | None) -> DutyProfile | None:
    """The profile for a duty, under its current name or its old one.

    Accepting the old spelling keeps a database that hasn't been migrated
    yet from losing its instruction links.
    """
    canonical = canonical_duty_name(name)
    key = _BY_KEY.get(canonical.casefold())
    return DUTY_PROFILES[key] if key is not None else None
