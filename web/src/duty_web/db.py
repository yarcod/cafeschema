"""Engine/session construction. One SQLite file, one shared schema.

":memory:" is a special case: plain SQLAlchemy pooling hands out a fresh,
independent in-memory database per connection, so a session created by a
test fixture and a session later created inside a Flask route handler
would each see an empty database. StaticPool pins the engine to a single
connection so every session opened from it shares the same in-memory data
— required for the test suite's pattern of seeding via one session and
reading back via another (e.g. through the Flask test client). A real
on-disk path needs no such pinning; the file itself is the shared state.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base


def _enable_foreign_keys(dbapi_connection, connection_record) -> None:
    """SQLite ignores FOREIGN KEY constraints by default, per connection.

    Without this, a Slot with a dangling team_id or person_id (e.g. from an
    import against a team_id that was never created) is written silently
    instead of failing loudly.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(db_path: str) -> Engine:
    if db_path == ":memory:":
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
        )
    event.listen(engine, "connect", _enable_foreign_keys)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
