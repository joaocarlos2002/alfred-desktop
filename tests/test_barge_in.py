from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from alfred.core.event_bus import EventBus
from alfred.domain.hotkeys import HotkeyAction, HotkeyPressed
from alfred.domain.voice import SpeechFinished, SpeechStarted, WakeWordDetected
from alfred.voice.barge_in import BargeInModule, mentions_wake_word
from alfred.voice.tts import SpeechPlayer


class FakeSpeaker:
    def __init__(self) -> None:
        self.speaking = False
        self.stops = 0

    def stop_speaking(self) -> None:
        self.stops += 1
        self.speaking = False


@pytest.fixture
async def bus() -> AsyncIterator[EventBus]:
    bus = EventBus()
    await bus.start()
    yield bus
    await bus.stop()


def test_mentions_wake_word() -> None:
    assert mentions_wake_word("Aqui é o Alfred, às suas ordens.")
    assert mentions_wake_word("Olá, Fred!")
    assert not mentions_wake_word("São dez horas da manhã.")


@pytest.mark.parametrize(
    "event",
    [
        HotkeyPressed(action=HotkeyAction.ACTIVATE, hotkey="ctrl+alt+space"),
        WakeWordDetected(word="alfred", confidence=0.95),
    ],
)
async def test_hotkey_or_wake_word_interrupts_speech(bus: EventBus, event: Any) -> None:
    speaker = FakeSpeaker()
    module = BargeInModule(bus, speaker)
    await module.start()

    await bus.publish(event)  # sem fala: nada a interromper
    await bus.drain(timeout=1)
    assert speaker.stops == 0

    speaker.speaking = True
    await bus.publish(event)
    await bus.drain(timeout=1)
    assert speaker.stops == 1
    assert module.interruptions == 1
    await module.stop()


async def test_emergency_hotkey_is_not_a_barge_in(bus: EventBus) -> None:
    speaker = FakeSpeaker()
    speaker.speaking = True
    module = BargeInModule(bus, speaker)
    await module.start()
    await bus.publish(HotkeyPressed(action=HotkeyAction.EMERGENCY, hotkey="x"))
    await bus.drain(timeout=1)
    assert speaker.stops == 0  # a emergência tem caminho próprio (#10)
    await module.stop()


async def test_own_speech_with_wake_word_suppresses_detector(bus: EventBus) -> None:
    detector = SimpleNamespace(suppressed=False)
    module = BargeInModule(bus, FakeSpeaker(), SimpleNamespace(detector=detector), echo_tail=0.05)
    await module.start()

    await bus.publish(SpeechStarted(text="Eu sou o Alfred."))
    await bus.drain(timeout=1)
    assert detector.suppressed
    await bus.publish(SpeechFinished(text="Eu sou o Alfred.", interrupted=False))
    await bus.drain(timeout=1)
    assert detector.suppressed  # eco da sala: ainda suprimido
    await asyncio.sleep(0.1)
    assert not detector.suppressed

    await bus.publish(SpeechStarted(text="São dez horas."))
    await bus.drain(timeout=1)
    assert not detector.suppressed  # sem a palavra: escuta normal (permite barge-in)
    await module.stop()


async def test_end_to_end_interrupt_under_300ms(bus: EventBus) -> None:
    class Synth:
        rate = 0

        def synthesize(self, text: str) -> tuple[np.ndarray, int]:
            return np.full(16000 * 5, 8000, dtype=np.int16), 16000

    class RealtimeOutput:
        def start(self) -> None: ...

        def write(self, data: bytes) -> None:
            time.sleep(len(data) / 2 / 16000)

        def stop(self) -> None: ...

        def close(self) -> None: ...

    finished: list[float] = []
    player = SpeechPlayer(
        Synth(),
        output_factory=lambda rate, device: RealtimeOutput(),
        on_finish=lambda text, interrupted: finished.append(time.perf_counter()),
    )

    class PlayerSpeaker:
        @property
        def speaking(self) -> bool:
            return player.speaking

        def stop_speaking(self) -> None:
            player.stop()

    module = BargeInModule(bus, PlayerSpeaker())
    await module.start()
    player.say("Uma resposta longa de cinco segundos.")
    while not player.speaking:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.2)

    pressed = time.perf_counter()
    await bus.publish(HotkeyPressed(action=HotkeyAction.ACTIVATE, hotkey="ctrl+alt+space"))
    while not finished:
        await asyncio.sleep(0.005)
    assert finished[0] - pressed < 0.3
    await module.stop()
    player.close()
