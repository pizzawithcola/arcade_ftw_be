import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import Player, Score


async def test_player_and_score_round_trip() -> None:
    async with SessionLocal() as session:
        player = Player(nickname="integration-tester", event="hk_employee")
        session.add(player)
        await session.flush()
        session.add(
            Score(
                player_id=player.id,
                hallucination_score=30,
                data_score=40,
                sabotage_score=-5,
                ultimate_score=20,
            )
        )
        await session.commit()
        player_id = player.id

    async with SessionLocal() as session:
        saved = (
            await session.execute(select(Score).where(Score.player_id == player_id))
        ).scalar_one()
        assert saved.hallucination_score == 30
        assert saved.data_score == 40
        assert saved.sabotage_score == -5
        assert saved.ultimate_score == 20


async def test_total_is_the_sum_of_the_four_games() -> None:
    """Including a negative score, because a bad hallucination round can lose points."""
    async with SessionLocal() as session:
        player = Player(nickname="totals-tester", event="hk_employee")
        session.add(player)
        await session.flush()
        session.add(
            Score(
                player_id=player.id,
                hallucination_score=10,
                data_score=20,
                sabotage_score=-30,
                ultimate_score=40,
            )
        )
        await session.commit()
        player_id = player.id

    async with SessionLocal() as session:
        saved = (
            await session.execute(select(Score).where(Score.player_id == player_id))
        ).scalar_one()
        assert saved.total_score == 40


async def test_the_database_refuses_to_store_a_wrong_total() -> None:
    """Generated in the database, so app code cannot drift from the rule."""
    async with SessionLocal() as session:
        player = Player(nickname="generated-tester", event="sg_employee")
        session.add(player)
        await session.flush()
        await session.commit()
        player_id = player.id

    with pytest.raises(Exception):  # noqa: BLE001 - any refusal is the point
        async with SessionLocal() as session:
            await session.execute(
                text(
                    "insert into scores (player_id, hallucination_score, total_score) "
                    "values (:pid, 10, 999)"
                ),
                {"pid": player_id},
            )
            await session.commit()


async def test_a_player_has_at_most_one_score_row() -> None:
    """player_id is the primary key now, not half of a composite."""
    async with SessionLocal() as session:
        player = Player(nickname="one-row-tester", event="sg_employee")
        session.add(player)
        await session.flush()
        session.add(Score(player_id=player.id, data_score=10))
        await session.commit()
        player_id = player.id

    with pytest.raises(IntegrityError):
        async with SessionLocal() as session:
            session.add(Score(player_id=player_id, data_score=20))
            await session.commit()


async def test_deleting_a_player_takes_the_score_with_it() -> None:
    async with SessionLocal() as session:
        player = Player(nickname="cascade-tester", event="hk_fintech_week")
        session.add(player)
        await session.flush()
        session.add(Score(player_id=player.id, ultimate_score=50))
        await session.commit()
        player_id = player.id

    async with SessionLocal() as session:
        await session.execute(
            text("delete from players where id = :pid"), {"pid": player_id}
        )
        await session.commit()

    async with SessionLocal() as session:
        left = (
            await session.execute(select(Score).where(Score.player_id == player_id))
        ).scalar_one_or_none()
        assert left is None
