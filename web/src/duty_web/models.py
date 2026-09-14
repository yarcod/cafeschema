"""ORM models — one table per entity in the design spec's data model."""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import Column, ForeignKey, Table
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# A duty belongs to a player's family, and either parent may staff or trade
# it, so the link is many-to-many: a player has one or two parents on file,
# and a parent can have siblings on the same team.
parent_players = Table(
    "parent_players",
    Base.metadata,
    Column("person_id", ForeignKey("people.id"), primary_key=True),
    Column("player_id", ForeignKey("players.id"), primary_key=True),
)


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

    players: Mapped[list[Player]] = relationship(
        secondary=parent_players, back_populates="parents"
    )


class Player(Base):
    """A child on the team. Duties are assigned to the player, not a parent.

    The roster (data/f15_parent_mailing_list.csv, exported from 360Player)
    is authoritative for who exists and who their parents are; the schedule
    only says which player staffs which shift.
    """

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))

    team: Mapped[Team] = relationship()
    parents: Mapped[list[Person]] = relationship(
        secondary=parent_players, back_populates="players", order_by="Person.id"
    )


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
    # The player's name as the schedule spelled it. It records what was
    # imported, so a swap deliberately leaves it alone; the current owner is
    # always player_id. Only read as a fallback when player_id is NULL,
    # which is how an unmatched row still shows who it was meant for.
    child_name: Mapped[str | None] = mapped_column(default=None)
    note: Mapped[str | None] = mapped_column(default=None)
    # Nullable: a shift whose player isn't on the roster is still real, it
    # just has nobody who can act on it until the roster catches up.
    player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id"), default=None
    )

    team: Mapped[Team] = relationship()
    player: Mapped[Player | None] = relationship()


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
