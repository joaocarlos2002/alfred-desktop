"""Skill de ditado por voz (#124): falar no microfone e o ALFRED escrever no app em foco."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any

import pytest

from alfred.core.event_bus import EventBus
from alfred.domain.assistant import AssistantState, AssistantStateMachine
from alfred.domain.emergency import EmergencyStopped
from alfred.domain.events import Event
from alfred.domain.voice import DictationStarted, DictationStopped, UtteranceTranscribed
from alfred.skills.dictation import DictationSkill, DictationText


# ---------------------------------------------------------------------- texto
def test_spoken_punctuation_capitalization_and_spacing() -> None:
    text = DictationText()
    assert text.feed("olá vírgula tudo bem ponto de interrogação").typed == "Olá, tudo bem?"
    assert text.feed("estou testando o ditado ponto").typed == " Estou testando o ditado."
    assert text.feed("nova linha segunda linha").typed == "\nSegunda linha"
    assert text.feed("dois pontos item um").typed == ": item um"
    assert text.feed("novo parágrafo fim").typed == "\n\nFim"


def test_whisper_punctuation_is_kept_and_not_duplicated() -> None:
    text = DictationText()
    assert text.feed("Bom dia, tudo bem?").typed == "Bom dia, tudo bem?"
    assert text.feed("Sim.").typed == " Sim."


def test_delete_last_phrase_removes_only_what_was_typed() -> None:
    text = DictationText()
    text.feed("primeira frase ponto")
    text.feed("segunda frase errada")
    action = text.feed("apagar última frase")
    assert action.backspaces == len(" Segunda frase errada")
    assert action.typed == ""
    assert text.feed("apaga isso").backspaces == len("Primeira frase.")
    assert text.feed("apaga isso").backspaces == 0  # nada mais a apagar


def test_stop_phrase_and_hallucinations() -> None:
    text = DictationText()
    assert text.feed("fim do ditado").stop
    assert text.feed("Encerrar ditado.").stop
    assert text.feed("Obrigado por assistir!").typed == ""  # alucinação típica em silêncio
    assert text.feed("Legendas pela comunidade Amara.org").typed == ""


# ---------------------------------------------------------------------- sessão
class FakePipeline:
    def __init__(self) -> None:
        self.state = "idle"
        self.begins: list[str] = []

    async def begin(self, trigger: str) -> bool:
        self.begins.append(trigger)
        return True


class FakeKeyboard:
    def __init__(self) -> None:
        self.typed: list[str] = []
        self.pressed: list[str] = []

    async def type_text(self, text: str) -> None:
        self.typed.append(text)

    async def press(self, combo: str) -> None:
        self.pressed.append(combo)


@pytest.fixture
async def bus() -> AsyncIterator[EventBus]:
    bus = EventBus()
    await bus.start()
    yield bus
    await bus.stop()


async def collect(bus: EventBus) -> list[Event]:
    seen: list[Event] = []

    async def keep(event: Event) -> None:
        seen.append(event)

    bus.subscribe(Event, keep)
    return seen


def said(text: str) -> UtteranceTranscribed:
    return UtteranceTranscribed(
        text=text, audio_seconds=1, latency_seconds=0.1, trigger="dictation"
    )


async def settle(bus: EventBus) -> None:
    for _ in range(5):
        await asyncio.sleep(0.03)
        await bus.drain(timeout=1)


async def start_by_voice(skill: DictationSkill) -> str:
    pattern, handler = skill.commands()[0]
    match = re.fullmatch(pattern, "alfred ditado", re.IGNORECASE)
    assert match is not None
    return (await handler(match)).reply


async def test_dictation_session_types_and_stops(bus: EventBus) -> None:
    pipeline, keyboard = FakePipeline(), FakeKeyboard()
    skill = DictationSkill(bus, pipeline, keyboard, rearm_interval=0.01)  # type: ignore[arg-type]
    events = await collect(bus)
    await skill.activate()
    assert "Ditado ativado" in await start_by_voice(skill)
    await settle(bus)
    assert pipeline.begins
    assert set(pipeline.begins) == {"dictation"}
    await bus.publish(said("olá vírgula mundo ponto"))
    await settle(bus)
    assert keyboard.typed == ["Olá, mundo."]
    await bus.publish(said("apagar última frase"))
    await settle(bus)
    assert keyboard.pressed == ["backspace"] * len("Olá, mundo.")
    await bus.publish(said("fim do ditado"))
    await settle(bus)
    assert not skill.active
    begins = len(pipeline.begins)
    await asyncio.sleep(0.05)
    assert len(pipeline.begins) == begins  # parou de re-armar a escuta
    kinds = [type(e) for e in events if isinstance(e, DictationStarted | DictationStopped)]
    assert kinds == [DictationStarted, DictationStopped]
    await skill.deactivate()


async def test_emergency_and_deactivate_stop_dictation(bus: EventBus) -> None:
    skill = DictationSkill(bus, FakePipeline(), FakeKeyboard(), rearm_interval=0.01)  # type: ignore[arg-type]
    await skill.activate()
    await start_by_voice(skill)
    await bus.publish(
        EmergencyStopped(
            trigger="hotkey", cancelled_tasks=0, stuck_tasks=0, released_inputs=(), elapsed_ms=1
        )
    )
    await settle(bus)
    assert not skill.active
    await start_by_voice(skill)
    await skill.deactivate()  # desligar a skill encerra o ditado
    assert not skill.active


async def test_ordinary_utterances_are_ignored_by_dictation(bus: EventBus) -> None:
    keyboard = FakeKeyboard()
    skill = DictationSkill(bus, FakePipeline(), keyboard, rearm_interval=0.01)  # type: ignore[arg-type]
    await skill.activate()
    await bus.publish(said("isto não deveria ser digitado"))  # ditado não está ativo
    await settle(bus)
    assert keyboard.typed == []
    await skill.deactivate()


async def test_command_handler_does_not_send_dictation_to_the_ai(bus: EventBus) -> None:
    from alfred.core.voice_commands import VoiceCommandHandler

    sent: list[str] = []

    async def noop() -> None:
        return None

    handler = VoiceCommandHandler(bus, emergency=noop, fallback=sent.append)
    await handler.handle(said("abra o navegador e apague tudo"))
    assert sent == []


def test_widget_shows_dictation_as_listening() -> None:
    machine = AssistantStateMachine()
    assert machine.apply(DictationStarted()) is AssistantState.LISTENING
    assert machine.detail == "Ditado ativo"
    assert machine.apply(DictationStopped()) is AssistantState.IDLE


def _unused(*_: Any) -> None:
    return None


@pytest.mark.windows
def test_real_synthetic_speech_becomes_punctuated_text(tmp_path: Any) -> None:
    """Áudio sintético (SAPI pt-BR) → Whisper local → ditado: pontuação falada vira símbolo."""
    import sys
    import wave

    if sys.platform != "win32":
        pytest.skip("SAPI do Windows")
    import numpy as np
    import win32com.client

    from alfred.infrastructure.paths import models_dir
    from alfred.voice.stt import SpeechToText, faster_whisper_factory

    path = tmp_path / "ditado.wav"
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 18  # 16 kHz, 16 bits, mono
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format = fmt
    stream.Open(str(path), 3)
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    for token in voice.GetVoices():
        if "Portuguese" in token.GetDescription():
            voice.Voice = token
    voice.AudioOutputStream = stream
    voice.Speak("bom dia vírgula hoje vamos testar o ditado ponto final")
    stream.Close()
    with wave.open(str(path)) as wav:
        audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)

    stt = SpeechToText(faster_whisper_factory(models_dir()), model_size="base")
    transcript = stt.transcribe(audio)
    typed = DictationText().feed(transcript.text).typed
    assert typed.startswith("Bom dia,")
    assert typed.rstrip().endswith(".")
    assert "ditado" in typed.casefold()
    assert "vírgula" not in typed.casefold()
