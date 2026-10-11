import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import Player, Score


async def test_player_and_score_round_trip() -> None:
    async with SessionLocal() as session:
        player = Player(nickname="integration-tester", country="AE")
        session.add(player)
        await session.flush()
        session.add(Score(player_id=player.id, stage_key="hallucinate", score=42))
        await session.commit()
        player_id = player.id

    async with SessionLocal() as session:
        saved = (
            await session.execute(select(Score).where(Score.player_id == player_id))
        ).scalar_one()
        assert saved.score == 42


async def test_one_score_row_per_player_and_stage() -> None:
    """The composite key is what lets a new stage ship without a migration."""
    async with SessionLocal() as session:
        player = Player(nickname="unique-stage-tester")
        session.add(player)
        await session.flush()
        session.add(Score(player_id=player.id, stage_key="datashadows", score=10))
        await session.commit()
        player_id = player.id

    with pytest.raises(IntegrityError):
        async with SessionLocal() as session:
            session.add(Score(player_id=player_id, stage_key="datashadows", score=20))
            await session.commit()
