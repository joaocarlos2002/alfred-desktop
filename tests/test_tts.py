from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pytest

from alfred.config.settings import VoiceSettings
from alfred.config.store import SettingsStore
from alfred.core.event_bus import EventBus
from alfred.core.settings_service import SettingsService
from alfred.domain.voice import SpeechFinished, SpeechStarted
from alfred.voice.models import FileModelSpec, ensure_files
from alfred.voice.tts import SpeechPlayer, TtsModule, split_sentences


def wait_for(predicate: Callable[[], bool], timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


class FakeSynth:
    def __init__(self, seconds: float = 0.2, name: str = "fake") -> None:
        self.seconds = seconds
        self.name = name
        self.rate = 0
        self.texts: list[str] = []

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        self.texts.append(text)
        return np.full(int(16000 * self.seconds), 10000, dtype=np.int16), 16000


class FakeOutput:
    instances: ClassVar[list[FakeOutput]] = []

    def __init__(self, rate: int, device: int | None, realtime: bool = True) -> None:
        self.rate = rate
        self.device = device
        self.realtime = realtime
        self.written: list[np.ndarray] = []
        self.closed = False
        FakeOutput.instances.append(self)

    def start(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        chunk = np.frombuffer(data, dtype=np.int16)
        self.written.append(chunk)
        if self.realtime:
            time.sleep(chunk.size / self.rate)  # como um dispositivo real, bloqueia

    def stop(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def reset_outputs() -> None:
    FakeOutput.instances.clear()


def test_split_sentences() -> None:
    assert split_sentences("Bom dia. Tudo bem? Sim!  ") == ["Bom dia.", "Tudo bem?", "Sim!"]
    assert split_sentences("Sem pontuação") == ["Sem pontuação"]


def test_plays_sentences_in_order_with_volume_and_callbacks() -> None:
    events: list[tuple[str, Any]] = []
    synth = FakeSynth(seconds=0.05)
    player = SpeechPlayer(
        synth,
        output_factory=lambda rate, device: FakeOutput(rate, device, realtime=False),
        device=lambda: 3,
        on_start=lambda text: events.append(("start", text)),
        on_finish=lambda text, interrupted: events.append(("finish", interrupted)),
    )
    player.volume = 0.5
    player.say("Primeira frase. Segunda frase.")

    wait_for(lambda: ("finish", False) in events)
    assert synth.texts == ["Primeira frase.", "Segunda frase."]
    output = FakeOutput.instances[0]
    assert output.device == 3
    assert output.closed
    assert int(np.concatenate(output.written).max()) == 5000  # volume 50%
    assert events[0] == ("start", "Primeira frase. Segunda frase.")
    player.close()


def test_stop_interrupts_quickly_and_clears_queue() -> None:
    finished: list[bool] = []
    synth = FakeSynth(seconds=5.0)
    player = SpeechPlayer(
        synth,
        output_factory=lambda rate, device: FakeOutput(rate, device),
        on_finish=lambda text, interrupted: finished.append(interrupted),
    )
    player.say("Uma resposta bem longa.")
    player.say("Outra resposta que não deve ser falada.")
    wait_for(lambda: player.speaking)
    time.sleep(0.2)

    started = time.perf_counter()
    player.stop()
    wait_for(lambda: finished == [True])
    assert time.perf_counter() - started < 0.3
    time.sleep(0.2)
    assert finished == [True]  # a segunda fala foi descartada
    assert synth.texts == ["Uma resposta bem longa."]
    player.close()


def test_say_with_interrupt_replaces_current_speech() -> None:
    finished: list[tuple[str, bool]] = []
    player = SpeechPlayer(
        FakeSynth(seconds=5.0),
        output_factory=lambda rate, device: FakeOutput(rate, device),
        on_finish=lambda text, interrupted: finished.append((text, interrupted)),
    )
    player.say("Longa.")
    wait_for(lambda: player.speaking)
    player.say("Urgente.", interrupt=True)
    wait_for(lambda: len(finished) == 1)
    assert finished[0] == ("Longa.", True)
    wait_for(lambda: player.current_text == "Urgente.")
    player.close()


def test_ensure_files_downloads_missing_files_atomically(tmp_path: Path) -> None:
    spec = FileModelSpec("voz", (("https://x/a.onnx", "a.onnx"), ("https://x/a.json", "a.json")), 1)
    calls: list[str] = []

    def download(url: str, target: Path) -> None:
        calls.append(url)
        target.write_bytes(b"data")

    ensure_files(spec, tmp_path, download=download)
    ensure_files(spec, tmp_path, download=download)
    assert calls == ["https://x/a.onnx", "https://x/a.json"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.json", "a.onnx"]


async def test_module_switches_engine_and_tunes_from_settings(tmp_path: Path) -> None:
    bus = EventBus()
    await bus.start()
    settings = SettingsService(SettingsStore(tmp_path / "settings.json"), bus)
    await settings.start()
    built: list[str] = []

    def builder(voice: VoiceSettings) -> FakeSynth:
        built.append(voice.tts_engine)
        return FakeSynth(seconds=0.02, name=voice.tts_engine)

    module = TtsModule(
        settings,
        bus,
        builder,
        output_factory=lambda rate, device: FakeOutput(rate, device, realtime=False),
    )
    events: list[Any] = []

    async def record(event: Any) -> None:
        events.append(event)

    bus.subscribe(SpeechStarted, record)
    bus.subscribe(SpeechFinished, record)
    await module.start()
    module.say("Olá.")
    wait_for(lambda: not module.speaking and len(FakeOutput.instances) == 1)
    await bus.drain(timeout=1)
    assert [type(e).__name__ for e in events] == ["SpeechStarted", "SpeechFinished"]

    await settings.update("voice", tts_engine="piper", tts_volume=40, tts_rate=2)
    await bus.drain(timeout=1)
    assert built == ["sapi", "piper"]
    assert module.player is not None
    assert module.player.volume == 0.4
    assert module.player.synthesizer.rate == 2  # type: ignore[attr-defined]
    await module.stop()
    await bus.stop()


# ------------------------------------------------------------------ engines reais
def _energy(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))


@pytest.mark.skipif(sys.platform != "win32", reason="SAPI")
@pytest.mark.windows
def test_real_sapi_speaks_portuguese() -> None:
    from alfred.voice.tts import SapiSynthesizer

    voices = SapiSynthesizer.available_voices()
    assert any("Portuguese" in v for v in voices)
    result: dict[str, Any] = {}

    def run() -> None:  # SAPI precisa de COM inicializado na thread que sintetiza
        result["audio"], result["rate"] = SapiSynthesizer().synthesize(
            "Bom dia. Em que posso ajudar?"
        )

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=30)
    assert result["rate"] == 22050
    assert result["audio"].size > 22050  # mais de 1 s de fala
    assert _energy(result["audio"]) > 300


@pytest.mark.skipif(sys.platform != "win32", reason="Piper + download")
@pytest.mark.windows
def test_real_piper_speaks_portuguese() -> None:
    from alfred.infrastructure.paths import models_dir
    from alfred.voice.models import PIPER_PT_BR_FABER
    from alfred.voice.tts import PiperSynthesizer

    folder = ensure_files(PIPER_PT_BR_FABER, models_dir() / "piper")
    audio, rate = PiperSynthesizer(folder / "pt_BR-faber-medium.onnx").synthesize(
        "Bom dia. Em que posso ajudar?"
    )
    assert rate == 22050
    assert audio.size > 22050
    assert _energy(audio) > 300


@pytest.mark.skipif(sys.platform != "win32", reason="dispositivo real")
@pytest.mark.windows
def test_real_output_device_plays_silence() -> None:
    """Abre o dispositivo de saída real e toca silêncio (não incomoda quem está usando o PC)."""
    from alfred.voice.devices import SoundDeviceBackend, resolve_device
    from alfred.voice.tts import sounddevice_output

    device = resolve_device(SoundDeviceBackend(), "output", None)
    assert device is not None
    stream = sounddevice_output(22050, device.index)
    stream.start()
    stream.write(np.zeros(4410, dtype=np.int16).tobytes())
    stream.stop()
    stream.close()


def test_downloaded_file_with_wrong_hash_is_discarded(tmp_path: Path) -> None:
    import hashlib

    from alfred.voice.models import FileModelSpec, IntegrityError, ensure_files

    good = b"modelo"
    spec = FileModelSpec(
        "voz",
        (("https://x/a.onnx", "a.onnx", hashlib.sha256(good).hexdigest()),),
        1,
    )

    def tampered(url: str, target: Path) -> None:
        target.write_bytes(b"adulterado")

    with pytest.raises(IntegrityError, match="hash inesperado"):
        ensure_files(spec, tmp_path, download=tampered)
    assert list(tmp_path.iterdir()) == []  # nada adulterado fica instalado
    ensure_files(spec, tmp_path, download=lambda url, target: target.write_bytes(good))
    assert (tmp_path / "a.onnx").read_bytes() == good


def test_pinned_voice_models_have_hashes() -> None:
    from alfred.voice.models import PIPER_PT_BR_FABER, VOSK_SMALL_PT

    assert VOSK_SMALL_PT.sha256 is not None
    assert all(len(entry) == 3 for entry in PIPER_PT_BR_FABER.files)
