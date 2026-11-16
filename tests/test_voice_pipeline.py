from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from alfred.app import bootstrap
from alfred.core.event_bus import EventBus
from alfred.core.voice_commands import (
    VoiceCommandHandler,
    spoken_date,
    spoken_time,
    strip_wake_word,
)
from alfred.domain.assistant import ListeningStarted, ListeningStopped
from alfred.domain.events import Event
from alfred.domain.hotkeys import HotkeyAction, HotkeyPressed
from alfred.domain.tasks import TaskFinished, TaskStatus
from alfred.domain.voice import (
    SpeakRequested,
    StopSpeakingRequested,
    UtteranceTranscribed,
    WakeWordDetected,
)
from alfred.infrastructure.db.task_repository import SqlTaskRepository
from alfred.voice.pipeline import PipelineState, VoicePipeline
from alfred.voice.vad import FRAME_SAMPLES, Endpointer

RNG = np.random.default_rng(3)
NOON = datetime(2026, 11, 16, 13, 1)


def speech() -> np.ndarray:
    t = np.arange(FRAME_SAMPLES) / 16000
    return (6000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)


def silence() -> np.ndarray:
    return RNG.normal(0, 30, FRAME_SAMPLES).astype(np.int16)


# ------------------------------------------------------------------ comandos
def test_spoken_time_and_date() -> None:
    assert spoken_time(datetime(2026, 1, 1, 1, 1)) == "É 1 hora e 1 minuto."
    assert spoken_time(datetime(2026, 1, 1, 13, 0)) == "São 13 horas em ponto."
    assert spoken_time(NOON) == "São 13 horas e 1 minuto."
    assert spoken_date(NOON) == "Hoje é segunda-feira, 16 de novembro de 2026."


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Alfred, que horas são?", "que horas são?"),
        ("Ei Alfredo que dia é hoje", "que dia é hoje"),
        ("que horas são", "que horas são"),
    ],
)
def test_strip_wake_word(raw: str, expected: str) -> None:
    assert strip_wake_word(raw) == expected


@pytest.fixture
async def bus() -> AsyncIterator[EventBus]:
    bus = EventBus()
    await bus.start()
    yield bus
    await bus.stop()


async def record(bus: EventBus, *types: type[Event]) -> list[Event]:
    seen: list[Event] = []

    async def handler(event: Event) -> None:
        seen.append(event)

    for kind in types:
        bus.subscribe(kind, handler)
    return seen


@pytest.mark.parametrize(
    ("text", "command", "reply"),
    [
        ("Alfred, que horas são?", "hora", "São 13 horas e 1 minuto."),
        ("Que dia é hoje?", "data", "Hoje é segunda-feira"),
        ("Alfred.", "vazio", "Sim? Estou ouvindo."),
        ("Abra o navegador", "desconhecido", "Ainda não sei executar esse pedido"),
    ],
)
async def test_interpret(bus: EventBus, text: str, command: str, reply: str) -> None:
    handler = VoiceCommandHandler(bus, emergency=_noop, clock=lambda: NOON)
    outcome = await handler.interpret(text)
    assert outcome.command == command
    assert outcome.reply is not None
    assert reply in outcome.reply


async def _noop() -> None:
    return None


async def test_stop_and_emergency_commands(bus: EventBus) -> None:
    calls: list[str] = []

    async def emergency() -> None:
        calls.append("emergencia")

    seen = await record(bus, StopSpeakingRequested)
    handler = VoiceCommandHandler(bus, emergency=emergency)
    assert (await handler.interpret("Silêncio!")).command == "parar_fala"
    assert (await handler.interpret("Alfred, pare tudo")).command == "emergencia"
    await bus.drain(timeout=1)
    assert calls == ["emergencia"]
    assert len(seen) == 1


async def test_each_utterance_becomes_a_real_task(bus: EventBus) -> None:
    seen = await record(bus, SpeakRequested, TaskFinished)
    handler = VoiceCommandHandler(bus, emergency=_noop, clock=lambda: NOON)
    await handler.handle(
        UtteranceTranscribed(
            text="Abra o Excel", audio_seconds=1, latency_seconds=0.5, trigger="hotkey"
        )
    )
    await bus.drain(timeout=1)
    reply = next(e for e in seen if isinstance(e, SpeakRequested))
    finished = next(e for e in seen if isinstance(e, TaskFinished))
    assert "Ainda não sei" in reply.text
    assert finished.status is TaskStatus.FAILED  # não fingir sucesso


# ------------------------------------------------------------------ pipeline
class FakeCapture:
    def __init__(self, running: bool = True) -> None:
        self.running = running
        self.listeners: list[Callable[[np.ndarray], object]] = []

    def subscribe(self, listener: Callable[[np.ndarray], object]) -> Callable[[], None]:
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def push(self, frame: np.ndarray) -> None:
        for listener in list(self.listeners):
            listener(frame)


class FakeStt:
    def __init__(self, text: str = "que horas são", fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.audio: list[np.ndarray] = []

    def transcribe(self, audio: np.ndarray) -> Any:
        if self.fail:
            raise RuntimeError("modelo indisponível")
        self.audio.append(audio)
        return SimpleNamespace(
            text=self.text, audio_seconds=audio.size / 16000, latency_seconds=0.2
        )


def endpointer() -> Endpointer:
    return Endpointer(silence_ms=400, no_speech_timeout_ms=800)


async def wait_idle(pipeline: VoicePipeline) -> None:
    for _ in range(200):
        if pipeline.state is PipelineState.IDLE:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(pipeline.state)


async def test_wake_word_listens_until_silence_and_transcribes(bus: EventBus) -> None:
    capture, stt = FakeCapture(), FakeStt()
    pipeline = VoicePipeline(bus, capture, stt, endpointer_factory=endpointer)
    seen = await record(bus, ListeningStarted, ListeningStopped, UtteranceTranscribed)
    await pipeline.start()

    await bus.publish(WakeWordDetected(word="alfred", confidence=0.95))
    await bus.drain(timeout=1)
    assert pipeline.state is PipelineState.LISTENING
    for frame in [speech()] * 10 + [silence()] * 10:
        capture.push(frame)
    await wait_idle(pipeline)
    await bus.drain(timeout=1)

    assert [type(e).__name__ for e in seen] == [
        "ListeningStarted",
        "ListeningStopped",
        "UtteranceTranscribed",
    ]
    utterance = seen[-1]
    assert isinstance(utterance, UtteranceTranscribed)
    assert utterance.trigger == "wake_word"
    assert stt.audio[0].size >= 10 * FRAME_SAMPLES
    # Após a escuta só resta o pre-roll (janela de 3 s em memória, nunca gravada em disco).
    assert len(capture.listeners) == 1
    await pipeline.stop()


async def test_no_speech_ends_without_transcribing(bus: EventBus) -> None:
    capture, stt = FakeCapture(), FakeStt()
    pipeline = VoicePipeline(bus, capture, stt, endpointer_factory=endpointer)
    seen = await record(bus, UtteranceTranscribed, ListeningStopped)
    await pipeline.start()
    await pipeline.begin("hotkey")
    for _ in range(15):
        capture.push(silence())
    await wait_idle(pipeline)
    await bus.drain(timeout=1)
    assert [type(e).__name__ for e in seen] == ["ListeningStopped"]
    assert stt.audio == []
    await pipeline.stop()


async def test_hotkey_toggles_push_to_talk(bus: EventBus) -> None:
    capture, stt = FakeCapture(), FakeStt()
    pipeline = VoicePipeline(bus, capture, stt, endpointer_factory=endpointer)
    seen = await record(bus, UtteranceTranscribed)
    await pipeline.start()
    press = HotkeyPressed(action=HotkeyAction.ACTIVATE, hotkey="ctrl+alt+space")

    await bus.publish(press)
    await bus.drain(timeout=1)
    for _ in range(5):
        capture.push(speech())
    await bus.publish(press)  # segundo toque encerra
    await bus.drain(timeout=1)
    await wait_idle(pipeline)
    await bus.drain(timeout=1)
    assert len(seen) == 1
    await pipeline.stop()


async def test_ignored_when_microphone_is_off(bus: EventBus) -> None:
    pipeline = VoicePipeline(bus, FakeCapture(running=False), FakeStt())
    await pipeline.start()
    assert await pipeline.begin("hotkey") is False
    assert pipeline.state is PipelineState.IDLE
    await pipeline.stop()


async def test_transcription_failure_apologizes(bus: EventBus) -> None:
    capture = FakeCapture()
    pipeline = VoicePipeline(bus, capture, FakeStt(fail=True), endpointer_factory=endpointer)
    seen = await record(bus, SpeakRequested)
    await pipeline.start()
    await pipeline.begin("hotkey")
    for frame in [speech()] * 5 + [silence()] * 10:
        capture.push(frame)
    await wait_idle(pipeline)
    await bus.drain(timeout=1)
    assert "não consegui entender" in seen[0].text  # type: ignore[attr-defined]
    await pipeline.stop()


async def test_voice_command_task_is_persisted(tmp_path: Path) -> None:
    app, _ = bootstrap(tmp_path)
    assert app.database is not None
    await app.manager.start_all()
    try:
        await app.bus.publish(
            UtteranceTranscribed(
                text="Alfred, que horas são?",
                audio_seconds=1.2,
                latency_seconds=0.4,
                trigger="wake_word",
            )
        )
        for _ in range(5):
            await app.bus.drain(timeout=1)
        (task,) = await SqlTaskRepository(app.database).list_tasks()
        assert task.title == "Alfred, que horas são?"
        assert task.origin == "voice"
        assert task.status is TaskStatus.SUCCEEDED
        assert [s.tool for s in task.steps] == ["interpretar_comando", "responder_por_voz"]
    finally:
        await app.manager.stop_all()


# ------------------------------------------------------------------ ponta a ponta real
@pytest.mark.skipif(sys.platform != "win32", reason="SAPI + modelos reais")
@pytest.mark.windows
async def test_real_end_to_end_alfred_que_horas_sao(tmp_path: Path, bus: EventBus) -> None:
    """Áudio sintético → palavra de ativação (Vosk) → escuta → Whisper → comando → resposta."""
    import wave

    import win32com.client

    from alfred.infrastructure.paths import models_dir
    from alfred.voice.models import VOSK_SMALL_PT, ensure_model
    from alfred.voice.stt import SpeechToText, faster_whisper_factory
    from alfred.voice.wakeword import WakeWordDetector, vosk_factory

    def synth(text: str) -> np.ndarray:
        path = tmp_path / "fala.wav"
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
            return np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)

    capture = FakeCapture()
    loop = asyncio.get_running_loop()
    detector = WakeWordDetector(
        vosk_factory(str(ensure_model(VOSK_SMALL_PT, models_dir()))),
        lambda word, conf: loop.call_soon_threadsafe(
            bus.publish_threadsafe, WakeWordDetected(word=word, confidence=conf)
        ),
    )
    capture.subscribe(detector.process)
    stt = SpeechToText(faster_whisper_factory(models_dir()), model_size="base")
    pipeline = VoicePipeline(bus, capture, stt)
    commands = VoiceCommandHandler(bus, emergency=_noop, clock=lambda: NOON)
    replies = await record(bus, SpeakRequested)
    await pipeline.start()
    await commands.start()

    pad = np.zeros(8000, dtype=np.int16)
    tail = np.zeros(16000 * 3, dtype=np.int16)
    audio = np.concatenate([pad, synth("Alfred, que horas são?"), tail])
    for start in range(0, audio.size - FRAME_SAMPLES, FRAME_SAMPLES):
        capture.push(audio[start : start + FRAME_SAMPLES])
        await asyncio.sleep(0.002)  # quadros chegando continuamente, como do microfone
    for _ in range(100):
        await asyncio.sleep(0.05)
        if replies:
            break
    await wait_idle(pipeline)
    await bus.drain(timeout=2)
    stt.unload()
    await commands.stop()
    await pipeline.stop()

    assert replies, "nenhuma resposta: palavra de ativação ou transcrição falhou"
    assert replies[0].text == "São 13 horas e 1 minuto."  # type: ignore[attr-defined]
