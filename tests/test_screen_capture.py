from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from alfred.automation.screen import CaptureDeniedError, ScreenCaptureService
from alfred.config.store import SettingsStore
from alfred.core.action_log import ActionLog
from alfred.core.event_bus import EventBus
from alfred.core.settings_service import SettingsService
from alfred.domain.activity import ActivityEntry
from alfred.domain.confirmation import ConfirmationDecision
from alfred.domain.screen import ScreenCaptured, ScreenRect

MONITORS = [
    ScreenRect(x=0, y=0, width=3840, height=1080),
    ScreenRect(x=0, y=0, width=1920, height=1080),
]


class MemoryActivity:
    def __init__(self) -> None:
        self.entries: list[ActivityEntry] = []

    async def add(self, entry: ActivityEntry) -> ActivityEntry:
        self.entries.append(entry)
        return entry


class FakeConfirmations:
    def __init__(self, decision: ConfirmationDecision) -> None:
        self.decision = decision
        self.requests: list[dict[str, Any]] = []

    async def request(self, **kwargs: Any) -> ConfirmationDecision:
        self.requests.append(kwargs)
        return self.decision


async def make(
    tmp_path: Path, policy: str, decision: ConfirmationDecision = ConfirmationDecision.APPROVED
) -> tuple[
    ScreenCaptureService, list[ScreenRect], MemoryActivity, list[Any], FakeConfirmations, EventBus
]:
    bus = EventBus()
    await bus.start()
    settings = SettingsService(SettingsStore(tmp_path / "settings.json"), bus)
    await settings.start()
    await settings.update("privacy", screen_capture=policy)
    grabbed: list[ScreenRect] = []
    activity = MemoryActivity()
    events: list[Any] = []

    async def record(event: Any) -> None:
        events.append(event)

    bus.subscribe(ScreenCaptured, record)
    confirmations = FakeConfirmations(decision)
    svc = ScreenCaptureService(
        settings,
        confirmations,
        ActionLog(activity),
        bus,  # type: ignore[arg-type]
        grab=lambda area: grabbed.append(area) or b"\x89PNG fake",
        monitors=lambda: list(MONITORS),
    )
    return svc, grabbed, activity, events, confirmations, bus


async def test_allow_captures_publishes_and_logs_without_image(tmp_path: Path) -> None:
    svc, grabbed, activity, events, confirmations, bus = await make(tmp_path, "allow")
    capture = await svc.capture("analisar janela", target="monitor", monitor=1)
    await bus.drain(timeout=1)
    assert capture.png.startswith(b"\x89PNG")
    assert grabbed == [MONITORS[1]]
    assert confirmations.requests == []
    assert isinstance(events[0], ScreenCaptured)
    entry = activity.entries[-1]
    assert entry.status == "succeeded"
    assert entry.detail["result"] == {"bytes": len(capture.png)}
    assert "png" not in str(entry.detail).lower()
    await bus.stop()


async def test_deny_blocks_without_grabbing(tmp_path: Path) -> None:
    svc, grabbed, activity, events, _, bus = await make(tmp_path, "deny")
    with pytest.raises(CaptureDeniedError):
        await svc.capture("teste")
    assert grabbed == []
    assert activity.entries[-1].status == "denied"
    await bus.drain(timeout=1)
    assert events == []
    await bus.stop()


@pytest.mark.parametrize(
    ("decision", "grabs"), [(ConfirmationDecision.APPROVED, 1), (ConfirmationDecision.TIMEOUT, 0)]
)
async def test_ask_requires_confirmation(
    tmp_path: Path, decision: ConfirmationDecision, grabs: int
) -> None:
    svc, grabbed, _, _, confirmations, bus = await make(tmp_path, "ask", decision)
    region = ScreenRect(x=10, y=20, width=300, height=200)
    if grabs:
        await svc.capture("ler botão", target="region", region=region)
    else:
        with pytest.raises(CaptureDeniedError):
            await svc.capture("ler botão", target="region", region=region)
    assert len(grabbed) == grabs
    assert "300x200" in confirmations.requests[0]["target"]
    await bus.stop()


async def test_invalid_targets(tmp_path: Path) -> None:
    svc, _, _, _, _, bus = await make(tmp_path, "allow")
    with pytest.raises(ValueError, match="região"):
        await svc.capture("x", target="region")
    with pytest.raises(ValueError, match="Monitor 5"):
        await svc.capture("x", monitor=5)
    await bus.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="captura real")
@pytest.mark.windows
async def test_real_capture_returns_png_of_requested_region(tmp_path: Path) -> None:
    from alfred.automation.screen import _grab, monitor_areas

    areas = monitor_areas()
    assert len(areas) >= 2  # desktop virtual + ao menos 1 monitor
    png = _grab(ScreenRect(x=areas[1].x, y=areas[1].y, width=320, height=200))
    assert png.startswith(b"\x89PNG")
    assert int.from_bytes(png[16:20], "big") == 320  # largura no cabeçalho IHDR
    assert int.from_bytes(png[20:24], "big") == 200
    assert list(tmp_path.iterdir()) == []  # nada gravado em disco
