import pytest

from app.storage.realtime import RealtimeHub


@pytest.mark.anyio
async def test_overflowed_subscriber_gets_close_sentinel_and_is_dropped() -> None:
    hub = RealtimeHub(recent_limit=10)
    queue = await hub.subscribe()

    while not queue.full():
        queue.put_nowait("backlog")

    await hub.broadcast_status({"status": "overflow"})

    # The stale queue is drained and left with only the close sentinel.
    assert queue.get_nowait() is None
    assert queue.empty()

    # The subscriber is no longer part of the hub: further broadcasts skip it.
    await hub.broadcast_status({"status": "again"})
    assert queue.empty()


@pytest.mark.anyio
async def test_healthy_subscriber_still_receives_broadcasts() -> None:
    hub = RealtimeHub(recent_limit=10)
    queue = await hub.subscribe()

    await hub.broadcast_status({"status": "ok"})

    data = queue.get_nowait()
    assert data is not None
    assert "status" in data
