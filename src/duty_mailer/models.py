"""Domain models.

Plain, frozen data. Construction enforces the invariants so that no other
layer has to re-check them, and so adapters cannot produce an invalid
Occurrence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from enum import Enum

MAX_LEAD_TIMES = 2


class Role(Enum):
    """Which of the two messages an occurrence is getting."""

    FORHANDSBESKED = "forhandsbesked"
    PAMINNELSE = "paminnelse"


@dataclass(frozen=True)
class Person:
    email: str
    name: str | None = None
    external_id: str | None = None

    @property
    def display(self) -> str:
        """Name if we have one, otherwise the address."""
        return self.name or self.email


@dataclass(frozen=True)
class Occurrence:
    """One shift, handled by the group standing there together.

    A shift, not a day: a Saturday holding a 09–12, a 12–15 and a 15–18 is
    three occurrences, each naming only its own people. Keying this on the
    date alone was the bug that told every family on duty that Saturday they
    were on with all the others.

    The times are optional because the tabular sources (xlsx, google_csv)
    have no time column — such a roster still gets one occurrence per date,
    and renders exactly as it always did.
    """

    due: date
    people: tuple[Person, ...]
    lead_days: tuple[int, ...] = (1,)
    key_location: str | None = None
    # The instruction for this particular duty, as the web app links it.
    # None falls back to the document index from config.yaml.
    document_link: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    # Where to turn up. One arena can hold more than one café, so the venue
    # alone doesn't say where to stand; None when the schedule doesn't say,
    # or when the shift's rows disagree.
    venue: str | None = None
    station: str | None = None
    # The team's slug, "" for the tabular sources. Keeps key handovers within
    # one team: the Kansli handles each team on its own.
    team: str = ""

    def __post_init__(self) -> None:
        if not self.people:
            raise ValueError(f"{self.due}: varje tillfälle behöver minst en person")

        lead_days = tuple(self.lead_days)
        if len(lead_days) > MAX_LEAD_TIMES:
            raise ValueError(
                f"{self.due}: högst {MAX_LEAD_TIMES} påminnelser per tillfälle, "
                f"fick {lead_days}"
            )
        if len(set(lead_days)) != len(lead_days):
            raise ValueError(f"{self.due}: dubblerade antal dagar innan: {lead_days}")
        if any(d <= 0 for d in lead_days):
            raise ValueError(
                f"{self.due}: antal dagar innan måste vara positiva, fick {lead_days}"
            )

        # Descending order makes the largest offset (the heads-up) first,
        # which is the order the rest of the code reasons in.
        object.__setattr__(self, "lead_days", tuple(sorted(lead_days, reverse=True)))


@dataclass(frozen=True)
class Message:
    """A rendered email, ready to send.

    `body` is the plain-text version and always present; `html_body` is an
    alternative rendering of the same content, sent alongside it so clients
    that can't or won't show HTML still get a readable mail.
    """

    to: tuple[str, ...]
    subject: str
    body: str
    html_body: str | None = None
