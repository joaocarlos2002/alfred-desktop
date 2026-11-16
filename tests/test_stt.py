from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from alfred.config.store import SettingsStore
from alfred.core.event_bus import EventBus
from alfred.core.settings_service import SettingsService
from alfred.voice.stt import SpeechToText, SpeechToTextModule


class FakeWhisper:
    def __init__(self, size: str, text: str = " Que horas são? ") -> None:
        self.size = size
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, audio: Any, **kwargs: Any) -> tuple[Any, Any]:
        self.calls.append({"dtype": audio.dtype, **kwargs})
        return iter([SimpleNamespace(text=self.text)]), SimpleNamespace(language="pt")


class Factory:
    def __init__(self) -> None:
        self.created: list[FakeWhisper] = []

    def __call__(self, size: str) -> FakeWhisper:
        model = FakeWhisper(size)
        self.created.append(model)
        return model


def audio(seconds: float = 1.5) -> np.ndarray:
    return np.zeros(int(16000 * seconds), dtype=np.int16)


def test_loads_lazily_and_transcribes_in_portuguese() -> None:
    factory = Factory()
    stt = SpeechToText(factory, model_size="tiny")
    assert not stt.loaded

    result = stt.transcribe(audio(1.5))

    assert result.text == "Que horas são?"
    assert result.audio_seconds == 1.5
    assert result.latency_seconds >= 0
    assert stt.loaded
    call = factory.created[0].calls[0]
    assert call["language"] == "pt"
    assert call["dtype"] == np.float32
    stt.transcribe(audio())
    assert stt.loads == 1  # reutiliza o modelo carregado
    stt.unload()


def test_unloads_after_idle_and_reloads_on_demand() -> None:
    factory = Factory()
    stt = SpeechToText(factory, idle_unload_seconds=0.05)
    stt.transcribe(audio())

    deadline = time.monotonic() + 2
    while stt.loaded and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not stt.loaded

    stt.transcribe(audio())
    assert stt.loads == 2
    stt.unload()


def test_changing_model_size_unloads_current_model() -> None:
    factory = Factory()
    stt = SpeechToText(factory, model_size="tiny")
    stt.transcribe(audio())

    stt.configure(model_size="small", idle_unload_seconds=60)
    assert not stt.loaded
    stt.transcribe(audio())
    assert [m.size for m in factory.created] == ["tiny", "small"]
    stt.unload()


def test_empty_transcript() -> None:
    stt = SpeechToText(lambda size: FakeWhisper(size, text="  "))
    assert stt.transcribe(audio()).empty
    stt.unload()


async def test_module_applies_settings_and_unloads_when_voice_off(tmp_path: Path) -> None:
    bus = EventBus()
    await bus.start()
    settings = SettingsService(SettingsStore(tmp_path / "settings.json"), bus)
    await settings.start()
    stt = SpeechToText(Factory())
    module = SpeechToTextModule(stt, settings, bus)
    await module.start()
    assert stt.model_size == "base"

    await settings.update("voice", enabled=True, stt_model="tiny", stt_idle_unload_seconds=30)
    await bus.drain(timeout=1)
    assert (stt.model_size, stt.idle_unload_seconds) == ("tiny", 30)
    stt.transcribe(audio())
    await settings.update("voice", enabled=False)
    await bus.drain(timeout=1)
    assert not stt.loaded
    await module.stop()
    await bus.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="SAPI + modelo real")
@pytest.mark.windows
def test_real_whisper_transcribes_portuguese(tmp_path: Path) -> None:
    import wave

    import win32com.client

    from alfred.infrastructure.paths import models_dir
    from alfred.voice.stt import faster_whisper_factory

    path = tmp_path / "comando.wav"
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
    voice.Speak("Abra o bloco de notas e escreva uma lista de compras.")
    stream.Close()
    with wave.open(str(path)) as wav:
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)

    stt = SpeechToText(faster_whisper_factory(models_dir()), model_size="base")
    result = stt.transcribe(samples)
    stt.unload()

    text = result.text.lower()
    assert "bloco de notas" in text
    assert "compras" in text
    print(f"\n'{result.text}' — {result.audio_seconds}s de áudio em {result.latency_seconds}s")
