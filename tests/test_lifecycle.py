from __future__ import annotations

import asyncio
import logging

import pytest

from alfred.core.lifecycle import DependencyError, ModuleManager, ModuleState, StartupError
from alfred.domain.module import Health, HealthStatus, Module


class FakeModule(Module):
    def __init__(
        self,
        name: str,
        events: list[str],
        *,
        depends_on: tuple[str, ...] = (),
        optional: bool = False,
        fail_start: bool = False,
        fail_stop: bool = False,
        hang_stop: bool = False,
    ) -> None:
        self.name = name
        self.depends_on = depends_on
        self.optional = optional
        self._events = events
        self._fail_start = fail_start
        self._fail_stop = fail_stop
        self._hang_stop = hang_stop

    async def start(self) -> None:
        self._events.append(f"start:{self.name}")
        if self._fail_start:
            raise RuntimeError(f"{self.name} quebrou")

    async def stop(self) -> None:
        self._events.append(f"stop:{self.name}")
        if self._hang_stop:
            await asyncio.sleep(10)
        if self._fail_stop:
            raise RuntimeError(f"{self.name} não parou")


def make_manager(*modules: Module, stop_timeout: float = 1.0) -> ModuleManager:
    manager = ModuleManager(start_timeout=1.0, stop_timeout=stop_timeout, health_timeout=1.0)
    for module in modules:
        manager.register(module)
    return manager


async def test_starts_in_dependency_order_and_stops_in_reverse() -> None:
    events: list[str] = []
    manager = make_manager(
        FakeModule("ui", events, depends_on=("core",)),
        FakeModule("core", events, depends_on=("config",)),
        FakeModule("config", events),
    )

    await manager.start_all()
    await manager.stop_all()

    assert events == [
        "start:config",
        "start:core",
        "start:ui",
        "stop:ui",
        "stop:core",
        "stop:config",
    ]
    assert set(manager.states.values()) == {ModuleState.STOPPED}


def test_duplicate_module_is_rejected() -> None:
    manager = make_manager(FakeModule("a", []))
    with pytest.raises(DependencyError, match="duplicado"):
        manager.register(FakeModule("a", []))


def test_missing_dependency_is_rejected() -> None:
    manager = make_manager(FakeModule("a", [], depends_on=("ghost",)))
    with pytest.raises(DependencyError, match="ghost"):
        manager.resolve_order()


def test_cycle_is_detected() -> None:
    manager = make_manager(
        FakeModule("a", [], depends_on=("b",)),
        FakeModule("b", [], depends_on=("c",)),
        FakeModule("c", [], depends_on=("a",)),
    )
    with pytest.raises(DependencyError, match="circular: a -> b -> c -> a"):
        manager.resolve_order()


async def test_optional_failure_does_not_abort_and_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[str] = []
    manager = make_manager(
        FakeModule("core", events),
        FakeModule("voice", events, optional=True, fail_start=True),
        FakeModule("wake", events, optional=True, depends_on=("voice",)),
        FakeModule("ui", events),
    )

    with caplog.at_level(logging.WARNING):
        await manager.start_all()

    states = manager.states
    assert states["core"] is ModuleState.RUNNING
    assert states["ui"] is ModuleState.RUNNING
    assert states["voice"] is ModuleState.FAILED
    assert states["wake"] is ModuleState.SKIPPED
    assert "start:wake" not in events
    assert "voice" in caplog.text
    await manager.stop_all()


async def test_required_failure_stops_started_modules_and_raises() -> None:
    events: list[str] = []
    manager = make_manager(
        FakeModule("config", events),
        FakeModule("core", events, depends_on=("config",), fail_start=True),
        FakeModule("ui", events, depends_on=("core",)),
    )

    with pytest.raises(StartupError) as exc:
        await manager.start_all()

    assert exc.value.module == "core"
    assert events == ["start:config", "start:core", "stop:config"]
    assert manager.states["ui"] is ModuleState.PENDING


async def test_required_module_with_failed_optional_dependency_aborts() -> None:
    events: list[str] = []
    manager = make_manager(
        FakeModule("voice", events, optional=True, fail_start=True),
        FakeModule("pipeline", events, depends_on=("voice",)),
    )

    with pytest.raises(StartupError, match="pipeline"):
        await manager.start_all()


async def test_stop_timeout_and_errors_do_not_block_other_modules(
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[str] = []
    manager = make_manager(
        FakeModule("a", events),
        FakeModule("b", events, fail_stop=True),
        FakeModule("c", events, hang_stop=True),
        stop_timeout=0.05,
    )
    await manager.start_all()

    with caplog.at_level(logging.ERROR):
        await manager.stop_all()

    assert events[-3:] == ["stop:c", "stop:b", "stop:a"]
    assert manager.states["a"] is ModuleState.STOPPED
    assert manager.states["b"] is ModuleState.FAILED
    assert manager.states["c"] is ModuleState.FAILED
    assert "excedeu o tempo" in caplog.text


async def test_health_reports_running_and_failed_modules() -> None:
    class Degraded(FakeModule):
        async def health(self) -> Health:
            return Health(status=HealthStatus.DEGRADED, detail="sem microfone")

    manager = make_manager(
        Degraded("voice", []),
        FakeModule("vision", [], optional=True, fail_start=True),
    )
    await manager.start_all()

    report = await manager.health()

    assert report["voice"].status is HealthStatus.DEGRADED
    assert report["vision"].status is HealthStatus.UNHEALTHY
    await manager.stop_all()
