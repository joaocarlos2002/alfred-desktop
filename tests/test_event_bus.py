from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator

import pytest

from alfred.core.event_bus import (
    EventBus,
    EventBusError,
    NoResponderError,
    RequestTimeoutError,
)
from alfred.domain.events import Event, Query, correlation_scope, current_correlation_id


class Ping(Event):
    n: int


class Pong(Event):
    n: int


class SpecialPing(Ping):
    pass


class Add(Query[int]):
    a: int
    b: int


@pytest.fixture
async def bus() -> AsyncIterator[EventBus]:
    bus = EventBus(queue_size=100, drain_timeout=1.0)
    await bus.start()
    yield bus
    await bus.stop()


async def test_delivers_to_type_subclass_and_catch_all(bus: EventBus) -> None:
    pings: list[Ping] = []
    everything: list[Event] = []

    async def on_ping(e: Ping) -> None:
        pings.append(e)

    async def on_any(e: Event) -> None:
        everything.append(e)

    bus.subscribe(Ping, on_ping)
    bus.subscribe(Event, on_any)

    await bus.publish(Ping(n=1))
    await bus.publish(SpecialPing(n=2))
    await bus.publish(Pong(n=3))
    await bus.drain(timeout=1)

    assert [p.n for p in pings] == [1, 2]
    assert [type(e).__name__ for e in everything] == ["Ping", "SpecialPing", "Pong"]


async def test_order_is_preserved_per_subscriber(bus: EventBus) -> None:
    seen: list[int] = []

    async def on_ping(e: Ping) -> None:
        await asyncio.sleep(0.001 * (e.n % 3))
        seen.append(e.n)

    bus.subscribe(Ping, on_ping)
    for n in range(50):
        await bus.publish(Ping(n=n))
    await bus.drain(timeout=2)

    assert seen == list(range(50))


async def test_failing_handler_is_isolated(bus: EventBus, caplog: pytest.LogCaptureFixture) -> None:
    healthy: list[int] = []

    async def broken(e: Ping) -> None:
        if e.n == 1:
            raise RuntimeError("boom")

    async def ok(e: Ping) -> None:
        healthy.append(e.n)

    failing = bus.subscribe(Ping, broken, name="broken")
    bus.subscribe(Ping, ok)

    with caplog.at_level(logging.ERROR):
        for n in range(3):
            await bus.publish(Ping(n=n))
        await bus.drain(timeout=1)

    assert healthy == [0, 1, 2]
    assert failing.failures == 1
    assert failing.delivered == 2
    assert "broken" in caplog.text


async def test_slow_subscriber_does_not_delay_others(bus: EventBus) -> None:
    release = asyncio.Event()
    fast_done = asyncio.Event()

    async def slow(e: Ping) -> None:
        await release.wait()

    async def fast(e: Ping) -> None:
        fast_done.set()

    bus.subscribe(Ping, slow)
    bus.subscribe(Ping, fast)
    await bus.publish(Ping(n=1))

    await asyncio.wait_for(fast_done.wait(), 1)
    release.set()


async def test_full_queue_applies_backpressure() -> None:
    bus = EventBus(queue_size=1)
    await bus.start()
    release = asyncio.Event()

    async def slow(e: Ping) -> None:
        await release.wait()

    bus.subscribe(Ping, slow)
    await bus.publish(Ping(n=0))  # em tratamento
    await asyncio.sleep(0)
    await bus.publish(Ping(n=1))  # ocupa a fila

    blocked = asyncio.create_task(bus.publish(Ping(n=2)))
    await asyncio.sleep(0.05)
    assert not blocked.done()

    release.set()
    await asyncio.wait_for(blocked, 1)
    await bus.stop()


async def test_correlation_id_propagates_through_chain(bus: EventBus) -> None:
    pongs: list[Pong] = []

    async def on_ping(e: Ping) -> None:
        assert current_correlation_id() == e.correlation_id
        await bus.publish(Pong(n=e.n))

    async def on_pong(e: Pong) -> None:
        pongs.append(e)

    bus.subscribe(Ping, on_ping)
    bus.subscribe(Pong, on_pong)

    origin = Ping(n=7)
    await bus.publish(origin)
    await bus.drain(timeout=1)
    await bus.drain(timeout=1)

    assert pongs[0].correlation_id == origin.correlation_id
    assert pongs[0].id != origin.id


def test_new_events_outside_chain_get_fresh_correlation_ids() -> None:
    assert Ping(n=1).correlation_id != Ping(n=2).correlation_id
    with correlation_scope(Ping(n=0).correlation_id) as cid:
        assert Ping(n=3).correlation_id == cid


async def test_request_response(bus: EventBus) -> None:
    async def add(q: Add) -> int:
        return q.a + q.b

    bus.respond(Add, add)

    assert await bus.request(Add(a=2, b=3)) == 5


async def test_request_timeout(bus: EventBus) -> None:
    async def never(q: Add) -> int:
        await asyncio.sleep(10)
        return 0

    bus.respond(Add, never)

    with pytest.raises(RequestTimeoutError):
        await bus.request(Add(a=1, b=1), timeout=0.05)


async def test_request_without_responder(bus: EventBus) -> None:
    with pytest.raises(NoResponderError):
        await bus.request(Add(a=1, b=1))


async def test_only_one_responder_per_query(bus: EventBus) -> None:
    async def add(q: Add) -> int:
        return 0

    bus.respond(Add, add)
    with pytest.raises(EventBusError):
        bus.respond(Add, add)


async def test_unsubscribe_stops_delivery(bus: EventBus) -> None:
    seen: list[int] = []

    async def on_ping(e: Ping) -> None:
        seen.append(e.n)

    sub = bus.subscribe(Ping, on_ping)
    await bus.publish(Ping(n=1))
    await bus.drain(timeout=1)
    sub.unsubscribe()
    await bus.publish(Ping(n=2))
    await bus.drain(timeout=1)

    assert seen == [1]


async def test_taps_observe_every_event_and_are_isolated(bus: EventBus) -> None:
    audited: list[str] = []

    def broken_tap(e: Event) -> None:
        raise RuntimeError("tap quebrado")

    bus.add_tap(broken_tap)
    bus.add_tap(lambda e: audited.append(type(e).__name__))

    async def add(q: Add) -> int:
        return 0

    bus.respond(Add, add)
    await bus.publish(Ping(n=1))
    await bus.request(Add(a=0, b=0))

    assert audited == ["Ping", "Add"]


async def test_publish_threadsafe(bus: EventBus) -> None:
    received = asyncio.Event()

    async def on_ping(e: Ping) -> None:
        received.set()

    bus.subscribe(Ping, on_ping)
    threading.Thread(target=bus.publish_threadsafe, args=(Ping(n=1),)).start()

    await asyncio.wait_for(received.wait(), 1)


async def test_stop_drains_pending_events() -> None:
    bus = EventBus()
    seen: list[int] = []

    async def on_ping(e: Ping) -> None:
        await asyncio.sleep(0.001)
        seen.append(e.n)

    bus.subscribe(Ping, on_ping)
    await bus.start()
    for n in range(10):
        await bus.publish(Ping(n=n))
    await bus.stop()

    assert seen == list(range(10))
    assert not bus.running


def test_publish_threadsafe_requires_running_bus() -> None:
    with pytest.raises(EventBusError):
        EventBus().publish_threadsafe(Ping(n=1))
