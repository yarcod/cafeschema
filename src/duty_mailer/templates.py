"""Swedish email rendering.

The mail names people, never their addresses: the group is sent as Bcc, and
a parent's address is not ours to show other parents.
"""

from __future__ import annotations

from datetime import date

from .html_template import render_html
from .models import Message, Occurrence, Person, Role

# Hardcoded rather than locale-derived: CI runners have no sv_SE locale.
MONTHS_SV = (
    "januari", "februari", "mars", "april", "maj", "juni",
    "juli", "augusti", "september", "oktober", "november", "december",
)
WEEKDAYS_SV = (
    "måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag",
)


def format_date_sv(d: date) -> str:
    """E.g. 'lördag 19 september'."""
    return f"{WEEKDAYS_SV[d.weekday()]} {d.day} {MONTHS_SV[d.month - 1]}"


def format_time_range_sv(occ: Occurrence) -> str:
    """E.g. '12:30–16:30', or '' when the schedule carries no times.

    A date can hold several shifts, so without this two reminders for the
    same Saturday would arrive looking identical.
    """
    if occ.start_time is None or occ.end_time is None:
        return ""
    return f"{occ.start_time:%H:%M}–{occ.end_time:%H:%M}"


def _when(occ: Occurrence) -> str:
    """The date, and the shift within it when the schedule says."""
    return " ".join(filter(None, (format_date_sv(occ.due), format_time_range_sv(occ))))


def _place(occ: Occurrence) -> str | None:
    """Where to turn up — one arena can hold more than one café."""
    parts = [part for part in (occ.venue, occ.station) if part]
    return " — ".join(parts) or None


NAMELESS = "(namn saknas)"


def _public_name(person: Person) -> str:
    """Some rows have the address stored as the name; that is no name."""
    if not person.name or "@" in person.name:
        return NAMELESS
    return person.name


def _names(occ: Occurrence) -> str:
    """Names only. A person without one gets a placeholder, never the address."""
    return ", ".join(_public_name(p) for p in occ.people)


def render(
    occ: Occurrence,
    role: Role,
    previous: Occurrence | None,
    *,
    chore: str,
    schedule_link: str | None,
    documents_link: str | None = None,
) -> Message:
    """Render one occurrence into a ready-to-send Swedish message.

    `previous` has already been filtered for adjacency by the caller; if it
    is not None, the handover line is shown.
    """
    when = _when(occ)
    hours = format_time_range_sv(occ)
    place = _place(occ)
    lines: list[str] = []
    handover: str | None = None
    key_location: str | None = None
    # The instruction for this duty when the schedule source knows it, so the
    # link lands on the right café's PDF rather than on the whole shelf.
    documents_link = occ.document_link or documents_link

    if role is Role.FORHANDSBESKED:
        subject = f"Er tur snart — {chore} {when}"
        headline = f"Er tur snart"
        intro = f"Hej! Ni står på tur för {chore} {when}."
        lines.append(intro)
        lines.append("")
        lines.append(f"Denna gång: {_names(occ)}.")
        if previous is not None:
            lines.append("")
            key_location = previous.key_location
            if key_location:
                lines.append(f"Nycklarna: {key_location}")
            handover = (
                f"Förra gången ({format_date_sv(previous.due)}) var det "
                f"{_names(previous)}."
            )
            lines.append(handover)
    else:
        # The hours go in the subject too: a day can hold several shifts, so
        # "i morgon" alone leaves two identical-looking mails in the inbox.
        subject = " ".join(filter(None, (f"Påminnelse — {chore} i morgon", hours)))
        headline = f"{chore} i morgon"
        intro = f"Hej! I morgon, {when}, är det er tur för {chore}."
        lines.append(intro)
        lines.append("")
        lines.append(f"Denna gång: {_names(occ)}.")

    if place:
        lines.append("")
        lines.append(f"Plats: {place}")

    if schedule_link:
        lines.append("")
        lines.append(f"Schema: {schedule_link}")
    if documents_link:
        lines.append(f"Instruktioner: {documents_link}")

    return Message(
        to=tuple(p.email for p in occ.people),
        subject=subject,
        body="\n".join(lines).strip(),
        html_body=render_html(
            subject=subject,
            headline=headline,
            when=when,
            names=_names(occ),
            place=place,
            intro=intro,
            handover=handover,
            key_location=key_location,
            schedule_link=schedule_link,
            documents_link=documents_link,
        ),
    )
