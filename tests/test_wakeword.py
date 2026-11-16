from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from alfred.config.store import SettingsStore
from alfred.core.event_bus import EventBus
from alfred.core.settings_service import SettingsService
from alfred.domain.voice import VoiceModelStatus, WakeWordDetected
from alfred.voice.capture import AudioCapture
from alfred.voice.models import ModelSpec, ensure_model, is_installed
from alfred.voice.vad import FRAME_SAMPLES
from alfred.voice.wakeword import REFRACTORY_S, WakeWordDetector, keyword_confidence
from alfred.voice.wakeword_module import WakeWordModule

RNG = np.random.default_rng(7)


def speech() -> np.ndarray:
    t = np.arange(FRAME_SAMPLES) / 16000
    return (6000 * np.sin(2 * np.pi * 200 * t) + RNG.normal(0, 200, FRAME_SAMPLES)).astype(np.int16)


def silence() -> np.ndarray:
    return RNG.normal(0, 30, FRAME_SAMPLES).astype(np.int16)


def payload(word: str | None, conf: float = 1.0, text: str | None = None) -> str:
    result = [{"word": word, "conf": conf}] if word else []
    return json.dumps({"result": result, "text": text if text is not None else (word or "")})


class ScriptedRecognizer:
    """Gramática: devolve ``stage1`` após N quadros. Completo: devolve ``full_text``."""

    def __init__(self, grammar: bool, script: dict[str, Any]) -> None:
        self.grammar = grammar
        self.script = script
        self.fed = 0

    def AcceptWaveform(self, data: bytes) -> bool:  # noqa: N802
        self.fed += 1
        return self.grammar and self.fed >= self.script["after"]

    def Result(self) -> str:  # noqa: N802
        return str(self.script["stage1"])

    def FinalResult(self) -> str:  # noqa: N802
        if self.grammar:
            return str(self.script.get("final", payload(None)))
        return json.dumps({"text": self.script["full_text"]})


def make(script: dict[str, Any], **kwargs: Any) -> tuple[WakeWordDetector, list[float]]:
    detections: list[float] = []

    def factory(words: list[str] | None) -> ScriptedRecognizer:
        return ScriptedRecognizer(words is not None, script)

    detector = WakeWordDetector(factory, lambda word, conf: detections.append(conf), **kwargs)
    return detector, detections


def feed(detector: WakeWordDetector, frames: int) -> bool:
    return any([detector.process(speech()) for _ in range(frames)])


def test_keyword_confidence() -> None:
    assert keyword_confidence(payload("alfredo", 0.95)) == 0.95
    assert keyword_confidence(payload("Fred", 0.7)) == 0.7
    assert keyword_confidence(payload("alface", 0.99)) == 0.0
    assert keyword_confidence(json.dumps({"text": ""})) == 0.0


def test_detects_when_both_stages_agree() -> None:
    detector, detections = make(
        {"after": 3, "stage1": payload("alfredo", 0.97), "full_text": "alfredo que horas são"}
    )
    assert feed(detector, 3)
    assert detections == [0.97]


def test_verification_rejects_stage1_false_positive() -> None:
    detector, detections = make(
        {"after": 3, "stage1": payload("alfredo", 0.97), "full_text": "o alfaiate chegou"}
    )
    assert not feed(detector, 3)
    assert detections == []
    assert detector.stage1_triggers == 1
    assert detector.verifications_rejected == 1


def test_low_confidence_is_ignored_without_verification() -> None:
    detector, _ = make(
        {"after": 3, "stage1": payload("fred", 0.6), "full_text": "fred"}, min_confidence=0.9
    )
    assert not feed(detector, 3)
    assert detector.stage1_triggers == 0


def test_silence_skips_recognition_and_flushes_pending_result() -> None:
    script = {
        "after": 10_000,
        "stage1": payload(None),
        "final": payload("alfredo", 0.95),
        "full_text": "alfredo",
    }
    detector, detections = make(script)
    feed(detector, 2)
    results = [detector.process(silence()) for _ in range(20)]
    assert any(results)  # resultado pendente confirmado após a pausa
    assert detections == [0.95]


def test_refractory_period_and_suppression() -> None:
    now = [100.0]
    detector, detections = make(
        {"after": 1, "stage1": payload("alfredo", 0.99), "full_text": "alfredo"},
        clock=lambda: now[0],
    )
    assert feed(detector, 1)
    assert not feed(detector, 1)  # logo em seguida: ignorado
    now[0] += REFRACTORY_S + 0.1
    detector.suppressed = True  # ex.: o próprio ALFRED falando
    assert not feed(detector, 1)
    detector.suppressed = False
    assert feed(detector, 1)
    assert len(detections) == 2


# ------------------------------------------------------------------ modelos
SPEC = ModelSpec("modelo-teste", "https://exemplo/modelo.zip", ("final.mdl", "conf/mfcc.conf"), 1)


def zip_with(files: dict[str, bytes]) -> Any:
    def download(url: str, target: Path) -> None:
        with zipfile.ZipFile(target, "w") as zf:
            for name, data in files.items():
                zf.writestr(name, data)

    return download


def test_model_is_downloaded_extracted_and_reused(tmp_path: Path) -> None:
    files = {"modelo-teste/final.mdl": b"x", "modelo-teste/conf/mfcc.conf": b"y"}
    path = ensure_model(SPEC, tmp_path, download=zip_with(files))
    assert (path / "final.mdl").exists()
    assert is_installed(SPEC, tmp_path)

    def fail(url: str, target: Path) -> None:
        raise AssertionError("não deveria baixar de novo")

    assert ensure_model(SPEC, tmp_path, download=fail) == path
    assert [p.name for p in tmp_path.iterdir()] == ["modelo-teste"]  # sem lixo temporário


def test_incomplete_or_malicious_archives_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="incompleto"):
        ensure_model(SPEC, tmp_path, download=zip_with({"modelo-teste/final.mdl": b"x"}))
    with pytest.raises(ValueError, match="suspeito"):
        ensure_model(SPEC, tmp_path, download=zip_with({"../../fora.txt": b"x"}))
    assert not is_installed(SPEC, tmp_path)


# ------------------------------------------------------------------ módulo
class NullBackend:
    def query_devices(self) -> list[dict[str, Any]]:
        return []

    @property
    def default_device(self) -> tuple[int, int]:
        return (-1, -1)


async def test_module_follows_settings_and_publishes_detection(tmp_path: Path) -> None:
    bus = EventBus()
    await bus.start()
    settings = SettingsService(SettingsStore(tmp_path / "settings.json"), bus)
    await settings.start()
    capture = AudioCapture(NullBackend())
    loads: list[int] = []
    script = {"after": 1, "stage1": payload("alfredo", 0.99), "full_text": "alfredo"}

    def loader() -> Any:
        loads.append(1)
        return lambda words: ScriptedRecognizer(words is not None, script)

    events: list[Any] = []

    async def record(event: Any) -> None:
        events.append(event)

    bus.subscribe(WakeWordDetected, record)
    bus.subscribe(VoiceModelStatus, record)
    module = WakeWordModule(capture, settings, bus, loader)
    await module.start()
    assert module.detector is None  # voz desligada por padrão: nada carregado
    assert loads == []

    await settings.update("voice", enabled=True)
    await bus.drain(timeout=1)
    assert module.detector is not None
    for listener in list(capture._listeners):
        listener(speech())
    await bus.drain(timeout=1)
    assert any(isinstance(e, WakeWordDetected) for e in events)
    assert [e.status for e in events if isinstance(e, VoiceModelStatus)] == [
        "downloading",
        "ready",
    ]

    await settings.update("voice", wake_word_confidence=0.95)
    await bus.drain(timeout=1)
    assert module.detector.min_confidence == 0.95
    await settings.update("voice", wake_word_enabled=False)
    await bus.drain(timeout=1)
    assert module.detector is None
    assert capture._listeners == []
    await module.stop()
    await bus.stop()


async def test_model_failure_degrades_health(tmp_path: Path) -> None:
    bus = EventBus()
    await bus.start()
    settings = SettingsService(SettingsStore(tmp_path / "settings.json"), bus)
    await settings.start()
    await settings.update("voice", enabled=True)

    def broken() -> Any:
        raise OSError("sem internet")

    module = WakeWordModule(AudioCapture(NullBackend()), settings, bus, broken)
    await module.start()
    health = await module.health()
    assert health.status == "degraded"
    assert "sem internet" in (health.detail or "")
    await module.stop()
    await bus.stop()


# ------------------------------------------------------------------ modelo real
@pytest.mark.skipif(sys.platform != "win32", reason="SAPI + modelo real")
@pytest.mark.windows
def test_real_model_detects_alfred_and_ignores_other_speech(tmp_path: Path) -> None:
    import wave

    import win32com.client

    from alfred.infrastructure.paths import models_dir
    from alfred.voice.models import VOSK_SMALL_PT
    from alfred.voice.wakeword import vosk_factory

    def synth(text: str) -> np.ndarray:
        path = tmp_path / "clip.wav"
        fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
        fmt.Type = 18
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Format = fmt
        stream.Open(str(path), 3)
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        for token in voice.GetVoices():
            if "Portuguese" in token.GetDescription():
                voice.Voice = token
        voice.AudioOutputStream = stream
        voice.Speak(text)
        stream.Close()
        with wave.open(str(path)) as wav:
            audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
        pad = np.zeros(16000, dtype=np.int16)
        return np.concatenate([pad, audio, pad])

    factory = vosk_factory(str(ensure_model(VOSK_SMALL_PT, models_dir())))

    def detections(text: str) -> int:
        found: list[float] = []
        detector = WakeWordDetector(factory, lambda w, c: found.append(c))
        audio = synth(text)
        for start in range(0, audio.size - FRAME_SAMPLES, FRAME_SAMPLES):
            detector.process(audio[start : start + FRAME_SAMPLES])
        return len(found)

    assert detections("Alfred, que horas são?") == 1
    assert detections("O alfaiate entregou o terno ontem.") == 0
    assert detections("A mercadoria ficou presa na alfândega.") == 0
