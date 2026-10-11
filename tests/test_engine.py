"""The engine drives the show on its own clock, so these tests are the safety net."""

import asyncio

from app.engine import Engine, Phase


def make_engine(durations: list[float] | None = None) -> Engine:
    durations = durations or [0.2, 0.2, 0.2]
    return Engine(
        flow=[
            {"type": "intro", "title": f"node {i}", "duration_sec": d}
            for i, d in enumerate(durations)
        ]
    )


async def wait_until(predicate, timeout: float = 3.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


async def stop(engine: Engine) -> None:
    """Leave no background task behind for the next test."""
    await engine.finish()
    if engine._runner is not None:
        engine._runner.cancel()
        try:
            await engine._runner
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


async def test_it_advances_without_being_told_to() -> None:
    engine = make_engine()
    await engine.start()

    assert engine.phase is Phase.PLAYING
    assert engine.index == 0
    assert await wait_until(lambda: engine.index == 1)
    assert await wait_until(lambda: engine.index == 2)
    assert await wait_until(lambda: engine.phase is Phase.FINISHED)

    await stop(engine)


async def test_pause_freezes_the_remaining_time() -> None:
    engine = make_engine([0.5, 0.5])
    await engine.start()
    await asyncio.sleep(0.05)
    await engine.pause()

    assert engine.phase is Phase.PAUSED
    frozen = engine.snapshot()["remaining_ms"]
    assert 0 < frozen <= 500

    # Time passes while paused, and neither the index nor the remaining time
    # move - that is the whole point of freezing rather than moving a deadline.
    await asyncio.sleep(0.3)
    assert engine.index == 0
    assert engine.snapshot()["remaining_ms"] == frozen

    await engine.resume()
    assert engine.phase is Phase.PLAYING
    # Resuming continues from where it stopped, so it must not jump forward.
    assert engine.index == 0
    assert await wait_until(lambda: engine.index == 1)

    await stop(engine)


async def test_finished_resumes_when_jumped_back() -> None:
    engine = make_engine()
    await engine.finish()
    assert engine.phase is Phase.FINISHED
    assert engine.snapshot()["remaining_ms"] == 0

    await engine.go_to(1)
    assert engine.phase is Phase.PLAYING
    assert engine.index == 1
    assert engine.snapshot()["remaining_ms"] > 0

    await stop(engine)


async def test_go_to_rejects_an_index_outside_the_flow() -> None:
    engine = make_engine()
    for bad in (-1, 3):
        try:
            await engine.go_to(bad)
        except ValueError:
            continue
        raise AssertionError(f"index {bad} should have been rejected")


async def test_snapshot_carries_the_node_and_the_server_clock() -> None:
    engine = make_engine()
    await engine.start()
    snapshot = engine.snapshot()

    assert snapshot["node"]["title"] == "node 0"
    assert snapshot["node_count"] == 3
    assert snapshot["remaining_ms"] > 0
    # Clients anchor their countdown on this instead of trusting their own clock.
    assert snapshot["server_time_ms"] > 0

    await stop(engine)


async def test_the_broadcast_for_a_new_node_carries_time_left() -> None:
    """Clients count down from this value, so a fresh node must not report zero."""
    engine = make_engine([0.15, 5.0])
    seen: list[dict] = []

    async def listener() -> None:
        seen.append(engine.snapshot())

    engine.subscribe(listener)
    await engine.start()
    assert await wait_until(lambda: engine.index == 1)

    assert seen[0]["node"]["title"] == "node 0"
    assert seen[0]["remaining_ms"] > 0
    assert seen[1]["node"]["title"] == "node 1"
    assert seen[1]["remaining_ms"] > 0

    await stop(engine)


async def test_listeners_are_told_about_every_change() -> None:
    engine = make_engine([0.1, 0.1])
    seen: list[str] = []

    async def listener() -> None:
        seen.append(engine.phase.value)

    engine.subscribe(listener)
    engine.subscribe(listener)  # subscribing twice must not double up
    assert len(engine._listeners) == 1

    await engine.start()
    assert seen == ["playing"]

    assert await wait_until(lambda: engine.phase is Phase.FINISHED)
    assert seen[-1] == "finished"

    await stop(engine)
