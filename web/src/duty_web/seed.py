"""One-time xlsx -> database import. Wipes and reimports Person/Slot.

Safe only because it always runs before any parent has logged in (design
spec: Seeding the database) — there is never live swap state to preserve.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Person, Slot, Team


class SeedError(Exception):
    """Raised when the xlsx cannot be imported. Message is operator-facing."""


def _parse_time_range(raw: str, *, row_number: int) -> tuple[str, str]:
    parts = str(raw).split("-")
    if len(parts) != 2:
        raise SeedError(f"Rad {row_number}: ogiltigt tidsintervall '{raw}'")
    start, end = (p.strip() for p in parts)
    return start, end


def import_schedule(session: Session, path: str | Path, *, team_id: int) -> int:
    path = Path(path)
    if not path.is_file():
        raise SeedError(f"Schemafilen hittades inte: {path}")

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            raise SeedError(f"Schemafilen är tom: {path}") from None

        columns = [str(c).strip().lower() if c is not None else "" for c in header]
        if not any(columns):
            raise SeedError(f"Schemafilen är tom: {path}")

        records = [
            {col: value for col, value in zip(columns, row) if col}
            for row in rows
            if any(cell is not None for cell in row)
        ]
    finally:
        workbook.close()

    # Nothing else creates a Team row, and SQLite's default connection
    # settings don't enforce foreign keys, so importing against a team_id
    # that doesn't exist would otherwise silently write slots with a
    # dangling team_id. Fail loudly, and before wiping the existing
    # Slot/Person data below, rather than after.
    if session.get(Team, team_id) is None:
        raise SeedError(
            f"Ingen grupp med team_id={team_id} finns. Skapa gruppen innan import."
        )

    session.query(Slot).delete()
    session.flush()

    # Slots are replaced wholesale, but Person rows are matched by e-post
    # and kept: a logged-in session stores the person's id, and SQLite
    # hands out the ids of deleted rows again, so recreating people would
    # silently point an existing session at a different parent. People who
    # drop out of the schedule keep their (now slot-less) row for the same
    # reason.
    people_by_email: dict[str, Person] = {
        person.email: person for person in session.query(Person).all()
    }
    count = 0
    try:
        for row_number, record in enumerate(records, start=2):
            email = str(record.get("epost") or "").strip().lower()
            name = str(record.get("namn") or "").strip()
            if not name:
                raise SeedError(f"Rad {row_number}: saknar namn")

            # A row can be missing an e-post when the person isn't in the
            # contact roster yet — the slot is still real and shouldn't be
            # dropped, it's just unfilled (Slot.person_id is nullable for
            # exactly this reason). Keep the intended name visible via note
            # rather than silently losing who it was meant for.
            person = None
            if email:
                person = people_by_email.get(email)
                if person is None:
                    person = Person(name=name, email=email)
                    session.add(person)
                    session.flush()
                    people_by_email[email] = person
                elif person.name != name:
                    person.name = name

            raw_date = record.get("datum")
            raw_date_str = "" if raw_date is None else str(raw_date).strip()
            if isinstance(raw_date, datetime):
                slot_date = raw_date.date()
            elif not raw_date_str:
                # Blank means "not yet scheduled" (e.g. a future term whose
                # exact dates aren't fixed yet) rather than a bad row.
                slot_date = None
            else:
                try:
                    slot_date = datetime.strptime(raw_date_str, "%Y-%m-%d").date()
                except ValueError:
                    raise SeedError(f"Rad {row_number}: ogiltigt datum '{raw_date}'")

            start_str, end_str = _parse_time_range(record.get("tid", ""), row_number=row_number)

            try:
                start_time = datetime.strptime(start_str, "%H:%M").time()
            except ValueError:
                raise SeedError(f"Rad {row_number}: ogiltigt starttid '{start_str}'")

            try:
                end_time = datetime.strptime(end_str, "%H:%M").time()
            except ValueError:
                raise SeedError(f"Rad {row_number}: ogiltigt sluttid '{end_str}'")

            anteckning = record.get("anteckning")
            note = (str(anteckning).strip() or None) if anteckning else None
            if person is None:
                unfilled_note = f"Ej matchad kontakt för '{name}'"
                note = f"{unfilled_note} | {note}" if note else unfilled_note

            session.add(
                Slot(
                    team_id=team_id,
                    date=slot_date,
                    start_time=start_time,
                    end_time=end_time,
                    station=str(record.get("station") or "").strip(),
                    duty_name=str(record.get("syssla") or "").strip(),
                    venue=str(record.get("arena") or "").strip(),
                    child_name=str(record.get("barn") or "").strip() or None,
                    note=note,
                    person_id=person.id if person is not None else None,
                )
            )
            count += 1
    except SeedError:
        session.rollback()
        raise

    session.commit()
    return count
