"""ORM models — one table per entity in the design spec's data model."""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import Column, ForeignKey, Table, UniqueConstraint, false
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
    # Short, lowercase name used in URLs (?lag=f15), as the team's own
    # instruction folder under the documents dir, and in the just recipes.
    slug: Mapped[str] = mapped_column(unique=True)
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
    """A child on a team. Duties are assigned to the player, not a parent.

    The roster (data/f15_parent_mailing_list.csv, exported from 360Player)
    is authoritative for who exists and who their parents are; the schedule
    only says which player staffs which shift.
    """

    __tablename__ = "players"
    # A name is only unique within a team: two teams in the club can each
    # have an Elsa Berg, and they are different children.
    __table_args__ = (UniqueConstraint("team_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    # True when a parent is one of the team's leaders in 360Player: leaders'
    # families don't staff shifts. Recomputed by every roster import, never
    # set by hand (spec D5).
    exempt: Mapped[bool] = mapped_column(default=False, server_default=false())

    team: Mapped[Team] = relationship()
    parents: Mapped[list[Person]] = relationship(
        secondary=parent_players, back_populates="players", order_by="Person.id"
    )


# Where a slot came from. The coach's file owns "import" slots; a shift made
# under Pass is "admin", and schedule.import never deletes or moves it.
ORIGIN_IMPORT = "import"
ORIGIN_ADMIN = "admin"


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
    # The station this shift is held at. Nullable until every slot has been
    # linked (migrate.py does it for imported ones); venue and station above
    # stay as denormalized text so /api/schedule and the mailer read them
    # unchanged.
    station_id: Mapped[int | None] = mapped_column(
        ForeignKey("stations.id"), default=None
    )
    origin: Mapped[str] = mapped_column(default=ORIGIN_IMPORT, server_default=ORIGIN_IMPORT)

    team: Mapped[Team] = relationship()
    player: Mapped[Player | None] = relationship()
    station_row: Mapped[Station | None] = relationship()


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


ROLE_LEADER = "leader"
ROLE_ANSVARIG = "ansvarig"


class Venue(Base):
    """A place the club staffs shifts at, shared by every team (spec D4)."""

    __tablename__ = "venues"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    # normalize_key(name): what makes "Café" and "cafe" the same place.
    name_key: Mapped[str] = mapped_column(unique=True)

    stations: Mapped[list[Station]] = relationship(
        back_populates="venue", order_by="Station.name"
    )


class Station(Base):
    """Where in a venue a shift is held, and the instruction for it."""

    __tablename__ = "stations"
    __table_args__ = (UniqueConstraint("venue_id", "name_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str]
    name_key: Mapped[str]
    # A bare filename, looked up in the slot's team folder first and then
    # among the club's shared documents (see routes/document_routes.py).
    document: Mapped[str | None] = mapped_column(default=None)

    venue: Mapped[Venue] = relationship(back_populates="stations")


class TeamRole(Base):
    """A person's role in one team: a leader (from 360Player) or an ansvarig."""

    __tablename__ = "team_roles"

    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    role: Mapped[str] = mapped_column(primary_key=True)

    person: Mapped[Person] = relationship()
    team: Mapped[Team] = relationship()


class ClubAdmin(Base):
    __tablename__ = "club_admins"

    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"), primary_key=True)

    person: Mapped[Person] = relationship()


class ApiKey(Base):
    """A person's key for the admin API. Only a hash of the key is stored."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"))
    # The public start of the key ("cs_1a2b3c4d"), safe to show and log.
    prefix: Mapped[str]
    key_hash: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)

    person: Mapped[Person] = relationship()


class NameAlias(Base):
    """A spelling in the coach's file that means one of the team's players.

    Saved when an ansvarig answers "Vem är 'Elin Exempel'?" in Granska, so
    the next import of the same file neither asks again nor leaves her seats
    free. spelling_key is roster.normalize_name(spelling).
    """

    __tablename__ = "name_aliases"
    __table_args__ = (UniqueConstraint("team_id", "spelling_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    spelling_key: Mapped[str]
    spelling: Mapped[str]
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))


class ActivityStation(Base):
    """Where a team's activity without a duty profile is held.

    Saved when an ansvarig answers "Var hålls 'X'?" in Granska, so the next
    import puts those shifts at the same station instead of asking again
    (and replacing them with unplaced copies). activity_key is
    names.normalize_key(activity).
    """

    __tablename__ = "activity_stations"
    __table_args__ = (UniqueConstraint("team_id", "activity_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    activity_key: Mapped[str]
    activity: Mapped[str]
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))

    station: Mapped[Station] = relationship()
