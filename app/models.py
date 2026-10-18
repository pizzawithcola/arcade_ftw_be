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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    score: Mapped["Score | None"] = relationship(
        back_populates="player", uselist=False, cascade="all, delete-orphan"
    )


class Score(Base):
    """One row per player: the four games plus their total.

    A wide row rather than one row per stage. The show is a fixed set of four
    games and the questions asked of this table are leaderboards ordered by
    total, so the trade is deliberate: adding a fifth game means a migration.

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
