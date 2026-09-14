"""ORM models — one table per entity in the design spec's data model."""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    venue: Mapped[str]


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    email: Mapped[str] = mapped_column(unique=True)
    # Covers swap notifications and duty reminders only — login links are
    # always sent, since without one there is no way back into the app.
    email_notifications: Mapped[bool] = mapped_column(default=True)


class Slot(Base):
    __tablename__ = "slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    # Nullable: some activities (e.g. a not-yet-scheduled winter term) are
    # known to exist before a date is fixed. Such slots are excluded from
    # the calendar grid and from date-ordered "upcoming" queries, and shown
    # in a separate "not yet scheduled" section instead.
    date: Mapped[date | None] = mapped_column(default=None)
    start_time: Mapped[time]
    end_time: Mapped[time]
    station: Mapped[str]
    duty_name: Mapped[str]
    venue: Mapped[str]
    # The player whose family staffs this slot. Parents often know each
    # other by their kids' names rather than their own, so both are shown.
    child_name: Mapped[str | None] = mapped_column(default=None)
    note: Mapped[str | None] = mapped_column(default=None)
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("people.id"), default=None
    )

    team: Mapped[Team] = relationship()
    person: Mapped[Person | None] = relationship()


class SwapRequest(Base):
    __tablename__ = "swap_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    proposer_slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id"))
    target_slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id"))
    status: Mapped[str] = mapped_column(default="pending")
    created_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)

    proposer_slot: Mapped[Slot] = relationship(foreign_keys=[proposer_slot_id])
    target_slot: Mapped[Slot] = relationship(foreign_keys=[target_slot_id])


class LoginToken(Base):
    __tablename__ = "login_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(unique=True)
    email: Mapped[str]
    expires_at: Mapped[datetime]
    consumed_at: Mapped[datetime | None] = mapped_column(default=None)
