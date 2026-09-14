"""Swedish email rendering.

The whole group goes in To: (spec D4) so that reply-all reaches everyone on
duty — coordinating the handover is the point of the heads-up message.
"""

from __future__ import annotations

from datetime import date

from .html_template import render_html
from .models import Message, Occurrence, Role

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


def _names(occ: Occurrence) -> str:
    return ", ".join(p.display for p in occ.people)


def _contacts(occ: Occurrence) -> str:
    return ", ".join(
        f"{p.display} ({p.email})" if p.name else p.email for p in occ.people
    )


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
    when = format_date_sv(occ.due)
    lines: list[str] = []
    handover: str | None = None
    key_location: str | None = None

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
                f"{_contacts(previous)} — hör av er till dem om nycklar "
                "eller frågor."
            )
            lines.append(handover)
    else:
        subject = f"Påminnelse — {chore} i morgon"
        headline = f"{chore} i morgon"
        intro = f"Hej! I morgon, {when}, är det er tur för {chore}."
        lines.append(intro)
        lines.append("")
        lines.append(f"Denna gång: {_names(occ)}.")

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
            intro=intro,
            handover=handover,
            key_location=key_location,
            schedule_link=schedule_link,
            documents_link=documents_link,
        ),
    )
