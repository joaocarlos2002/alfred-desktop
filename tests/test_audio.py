from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pytest

from alfred.config.store import SettingsStore
from alfred.core.event_bus import EventBus
from alfred.core.settings_service import SettingsService
from alfred.voice import capture as capture_module
from alfred.voice.capture import AudioCapture
from alfred.voice.devices import list_devices, resolve_device
from alfred.voice.module import AudioInputChanged, AudioModule
from alfred.voice.vad import FRAME_SAMPLES, Endpointer, EnergyVAD, level_dbfs

RNG = np.random.default_rng(42)


def noise(level: float = 80.0) -> np.ndarray:
    return (RNG.normal(0, level, FRAME_SAMPLES)).astype(np.int16)


def speech(amplitude: float = 6000.0) -> np.ndarray:
    t = np.arange(FRAME_SAMPLES) / 16000
    return (amplitude * np.sin(2 * np.pi * 220 * t) + RNG.normal(0, 200, FRAME_SAMPLES)).astype(
        np.int16
    )


# ------------------------------------------------------------------ VAD
def test_level_dbfs() -> None:
    assert level_dbfs(np.zeros(FRAME_SAMPLES, dtype=np.int16)) < -100
    assert -4 < level_dbfs(np.full(FRAME_SAMPLES, 32767, dtype=np.int16)) <= 0


def test_vad_detects_speech_over_adaptive_noise_floor() -> None:
    vad = EnergyVAD()
    for _ in range(50):
        assert not vad.process(noise())
    assert vad.process(speech())
    for _ in range(vad.hangover_frames):
        assert vad.process(noise())  # hangover: pausa curta não corta a fala
    assert not vad.process(noise())


def test_vad_adapts_to_louder_room() -> None:
    vad = EnergyVAD()
    for _ in range(200):
        vad.process(noise(level=600))  # sala barulhenta, mas constante
    assert not vad.process(noise(level=600))


def test_endpointer_outcomes() -> None:
    ep = Endpointer(silence_ms=400)
    outcome = None
    for frame in [noise()] * 5 + [speech()] * 10 + [noise()] * 20:
        outcome = ep.process(frame) or outcome
        if outcome:
            break
    assert outcome == "end"

    silent = Endpointer(no_speech_timeout_ms=800)
    results = [silent.process(noise()) for _ in range(12)]
    assert "no_speech" in results


# ------------------------------------------------------------------ dispositivos
class FakeBackend:
    def __init__(self, devices: list[dict[str, Any]], default: tuple[int, int] = (1, 2)) -> None:
        self.devices = devices
        self.default = default
        self.refreshes = 0

    def query_devices(self) -> list[dict[str, Any]]:
        return self.devices

    @property
    def default_device(self) -> tuple[int, int]:
        return self.default

    def refresh(self) -> None:
        self.refreshes += 1


def _device(name: str, hostapi: int, inputs: int, outputs: int) -> dict[str, Any]:
    return {
        "name": name,
        "hostapi": hostapi,
        "max_input_channels": inputs,
        "max_output_channels": outputs,
    }


DEVICES = [
    _device("Mapeador de som - Input", 0, 2, 0),
    _device("Microfone (FIFINE Microphone)", 0, 1, 0),
    _device("Headphones (HD Audio)", 0, 0, 2),
    _device("Microfone (FIFINE Microphone)", 1, 1, 0),
]


def test_lists_devices_of_default_host_api_only() -> None:
    inputs = list_devices(FakeBackend(DEVICES), "input")
    assert [d.index for d in inputs] == [0, 1]
    assert next(d for d in inputs if d.is_default).name.startswith("Microfone")
    assert [d.name for d in list_devices(FakeBackend(DEVICES), "output")] == [
        "Headphones (HD Audio)"
    ]


def test_resolves_by_name_and_falls_back_to_default() -> None:
    backend = FakeBackend(DEVICES)
    for name, expected in [("mapeador", 0), ("USB inexistente", 1), (None, 1)]:
        device = resolve_device(backend, "input", name)
        assert device is not None
        assert device.index == expected


# ------------------------------------------------------------------ captura
class FakeStream:
    instances: ClassVar[list[FakeStream]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.active = False
        self.closed = False
        FakeStream.instances.append(self)

    def start(self) -> None:
        self.active = True

    def close(self) -> None:
        self.closed = True
        self.active = False

    def feed(self, frame: np.ndarray) -> None:
        self.kwargs["callback"](frame.tobytes(), len(frame), None, None)

    def die(self) -> None:
        self.kwargs["finished_callback"]()


@pytest.fixture(autouse=True)
def fast_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeStream.instances.clear()
    monkeypatch.setattr(capture_module, "RESTART_DELAYS", (0.01,))


def wait_for(predicate: Callable[[], bool], timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert predicate()


def test_capture_opens_16khz_mono_and_dispatches_frames() -> None:
    received: list[np.ndarray] = []
    capture = AudioCapture(FakeBackend(DEVICES), stream_factory=FakeStream)
    capture.subscribe(received.append)
    capture.start("FIFINE")

    stream = FakeStream.instances[-1]
    assert stream.kwargs["samplerate"] == 16000
    assert stream.kwargs["channels"] == 1
    assert stream.kwargs["blocksize"] == FRAME_SAMPLES
    assert stream.kwargs["device"] == 1
    frame = speech()
    stream.feed(frame)
    wait_for(lambda: len(received) == 1)
    assert np.array_equal(received[0], frame)
    assert capture.device_name == "Microfone (FIFINE Microphone)"
    capture.stop()
    assert stream.closed


def test_failing_listener_does_not_stop_others() -> None:
    received: list[int] = []
    capture = AudioCapture(FakeBackend(DEVICES), stream_factory=FakeStream)

    def broken(frame: np.ndarray) -> None:
        raise RuntimeError("boom")

    capture.subscribe(broken)
    unsubscribe = capture.subscribe(lambda f: received.append(len(f)))
    capture.start()
    FakeStream.instances[-1].feed(noise())
    wait_for(lambda: received == [FRAME_SAMPLES])
    unsubscribe()
    capture.stop()


def test_stream_loss_is_recovered_and_reported() -> None:
    changes: list[tuple[str | None, bool]] = []
    backend = FakeBackend(DEVICES)
    capture = AudioCapture(
        backend, stream_factory=FakeStream, on_device_change=lambda d, a: changes.append((d, a))
    )
    capture.start()
    FakeStream.instances[-1].die()  # microfone desconectado

    wait_for(lambda: len(FakeStream.instances) == 2 and capture.running)
    assert backend.refreshes >= 1
    assert changes[1] == ("Microfone (FIFINE Microphone)", False)
    assert changes[-1][1] is True
    capture.stop()


def test_switch_device_without_restarting_app() -> None:
    capture = AudioCapture(FakeBackend(DEVICES), stream_factory=FakeStream)
    capture.start()
    capture.switch_device("Mapeador")
    assert FakeStream.instances[0].closed
    assert FakeStream.instances[-1].kwargs["device"] == 0
    capture.stop()


async def test_module_follows_voice_settings(tmp_path: Path) -> None:
    bus = EventBus()
    await bus.start()
    settings = SettingsService(SettingsStore(tmp_path / "settings.json"), bus)
    await settings.start()
    capture = AudioCapture(FakeBackend(DEVICES), stream_factory=FakeStream)
    module = AudioModule(capture, settings, bus)
    capture._on_device_change = module.notify_device_change
    seen: list[AudioInputChanged] = []

    async def record(event: AudioInputChanged) -> None:
        seen.append(event)

    bus.subscribe(AudioInputChanged, record)
    await module.start()
    assert not capture.running  # voz desligada por padrão: microfone fechado

    await settings.update("voice", enabled=True)
    await bus.drain(timeout=1)
    assert capture.running
    await settings.update("voice", input_device="Mapeador")
    await bus.drain(timeout=1)
    assert FakeStream.instances[-1].kwargs["device"] == 0
    await settings.update("voice", enabled=False)
    await bus.drain(timeout=1)
    assert not capture.running
    assert seen
    assert seen[0].available
    await module.stop()
    await bus.stop()


def test_no_audio_is_written_to_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    capture = AudioCapture(FakeBackend(DEVICES), stream_factory=FakeStream)
    capture.start()
    for _ in range(20):
        FakeStream.instances[-1].feed(speech())
    wait_for(lambda: capture.frames_captured == 20)
    capture.stop()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="Win32")
@pytest.mark.windows
def test_real_microphone_capture() -> None:
    from alfred.voice.devices import SoundDeviceBackend

    frames: list[np.ndarray] = []
    done = threading.Event()

    def collect(frame: np.ndarray) -> None:
        frames.append(frame)
        if len(frames) >= 12:
            done.set()

    capture = AudioCapture(SoundDeviceBackend())
    capture.subscribe(collect)
    capture.start()
    assert done.wait(timeout=5), "nenhum áudio recebido do microfone"
    capture.stop()
    assert all(f.shape == (FRAME_SAMPLES,) for f in frames)
