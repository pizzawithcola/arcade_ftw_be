from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
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

    scores: Mapped[list["Score"]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )


class Score(Base):
    """One row per player per stage, so adding a stage never changes the schema."""

    __tablename__ = "scores"

    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    stage_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    player: Mapped[Player] = relationship(back_populates="scores")
