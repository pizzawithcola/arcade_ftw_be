from datetime import datetime

from sqlalchemy import (
    Computed,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Player(Base):
    __tablename__ = "players"
    __table_args__ = (
        # Identity is per event, so the same nickname at two events is two
        # players. A globally unique nickname would merge two different people
        # who happened to pick the same name, and hand them one scoreboard.
        UniqueConstraint("event", "nickname", name="uq_players_event_nickname"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    nickname: Mapped[str] = mapped_column(String(64))
    # Which event they signed in for. Validated against app.events on the way in.
    event: Mapped[str] = mapped_column(String(32))
    # Which room they are playing in. Nullable because a player exists from the
    # moment they log in, and only joins a room when they enter one.
    room_code: Mapped[str | None] = mapped_column(
        ForeignKey("rooms.code", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    room: Mapped["Room | None"] = relationship(back_populates="players")
    score: Mapped["Score | None"] = relationship(
        back_populates="player", uselist=False, cascade="all, delete-orphan"
    )


class Room(Base):
    """A single running show.

    The engine behind a room lives in memory, but the room itself is a row so
    that membership survives a restart and the admin panel can list what exists.
    """

    __tablename__ = "rooms"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    # Optional: a room can serve any event, so this is a label rather than a key.
    event: Mapped[str | None] = mapped_column(String(32))
    label: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    players: Mapped[list[Player]] = relationship(back_populates="room")


class ScoreEvent(Base):
    """One reported scoring event, deduplicated by its key.

    Reporting is at-least-once on purpose: a kiosk retries, a page reloads, and
    an operator can jump back to a node. The same answer therefore arrives more
    than once, and the composite primary key makes every repeat a no-op instead
    of double points.
    """

    __tablename__ = "score_events"

    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    # "<room>:<node index>:<question index|total>" - built by the server, never
    # taken from the client, so a client cannot forge a fresh key and re-score.
    event_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Score(Base):
    """One row per player: the four sections plus their total.

    A wide row rather than one row per stage. The show is a fixed set of four
    sections (a game plus the quiz that follows it) and the questions asked of
    this table are leaderboards ordered by total, so the trade is deliberate:
    adding a fifth section means a migration.

    `total_score` is a database-generated column, so it can never drift from the
    four parts no matter which code path writes them.
    """

    __tablename__ = "scores"

    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    hallucination_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sabotage_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ultimate_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_score: Mapped[int] = mapped_column(
        Integer,
        Computed(
            "hallucination_score + data_score + sabotage_score + ultimate_score",
            persisted=True,
        ),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    player: Mapped["Player"] = relationship(back_populates="score")
