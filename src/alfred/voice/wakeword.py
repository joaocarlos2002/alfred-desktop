"""Detector da palavra de ativação em duas etapas (ADR-0005).

1. Gramática restrita (``alfredo``/``fred``) com confiança mínima, alimentada só com fala (VAD).
2. Quando dispara, o buffer recente é reconhecido com o modelo completo; só ativa se a transcrição
   contiver a palavra. Elimina os falsos positivos da etapa 1.

Roda na thread de distribuição de áudio; ``on_detect`` é chamado nessa thread.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from collections import deque
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

from alfred.voice.vad import FRAME_MS, EnergyVAD, Frame

logger = logging.getLogger(__name__)

KEYWORDS = ("alfredo", "fred")
KEYWORD_RE = re.compile(r"\b(alfred|alfredo|alfredi|fred)\b")
BUFFER_MS = 2400
SILENCE_RESET_MS = 1200
REFRACTORY_S = 1.5


class Recognizer(Protocol):
    def AcceptWaveform(self, data: bytes) -> bool: ...  # noqa: N802 - API do Vosk

    def Result(self) -> str: ...  # noqa: N802

    def FinalResult(self) -> str: ...  # noqa: N802


type RecognizerFactory = Callable[[list[str] | None], Recognizer]
"""Cria um reconhecedor; com lista de palavras = gramática restrita, ``None`` = modelo completo."""


def vosk_factory(model_path: str) -> RecognizerFactory:
    import vosk

    vosk.SetLogLevel(-1)
    model = vosk.Model(model_path)

    def create(words: list[str] | None) -> Recognizer:
        recognizer: Any = (
            vosk.KaldiRecognizer(model, 16000, json.dumps(words))
            if words is not None
            else vosk.KaldiRecognizer(model, 16000)
        )
        recognizer.SetWords(True)
        result: Recognizer = recognizer
        return result

    return create


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def keyword_confidence(payload: str) -> float:
    """Maior confiança de uma palavra-chave num resultado do Vosk (0 se não houver)."""
    best = 0.0
    for word in json.loads(payload).get("result", []):
        if KEYWORD_RE.fullmatch(normalize(word["word"])):
            best = max(best, float(word.get("conf", 1.0)))
    return best


class WakeWordDetector:
    def __init__(
        self,
        factory: RecognizerFactory,
        on_detect: Callable[[str, float], None],
        *,
        min_confidence: float = 0.9,
        vad: EnergyVAD | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = factory
        self._on_detect = on_detect
        self.min_confidence = min_confidence
        self._vad = vad or EnergyVAD()
        self._clock = clock
        self._buffer: deque[Frame] = deque(maxlen=BUFFER_MS // FRAME_MS)
        self._stage1 = factory([*KEYWORDS, "[unk]"])
        self._silence_ms = 0
        self._last_detection = -REFRACTORY_S
        self.suppressed = False
        """Quando ``True`` (ex.: o próprio ALFRED falando a palavra), detecções são ignoradas."""
        self.stage1_triggers = 0
        self.verifications_rejected = 0

    def process(self, frame: Frame) -> bool:
        """Processa um quadro; devolve ``True`` se a palavra foi detectada agora."""
        self._buffer.append(frame)
        if not self._vad.process(frame):
            self._silence_ms += FRAME_MS
            if self._silence_ms >= SILENCE_RESET_MS:
                detected = self._check(self._stage1.FinalResult())
                self._stage1 = self._factory([*KEYWORDS, "[unk]"])
                self._silence_ms = 0
                return detected
            return False
        self._silence_ms = 0
        if self._stage1.AcceptWaveform(frame.tobytes()):
            return self._check(self._stage1.Result())
        return False

    def _check(self, payload: str) -> bool:
        confidence = keyword_confidence(payload)
        if confidence < self.min_confidence:
            return False
        self.stage1_triggers += 1
        if not self._verify():
            self.verifications_rejected += 1
            logger.debug("Etapa 1 disparou (%.2f), verificação rejeitou", confidence)
            return False
        now = self._clock()
        if self.suppressed or now - self._last_detection < REFRACTORY_S:
            return False
        self._last_detection = now
        self._stage1 = self._factory([*KEYWORDS, "[unk]"])
        logger.info("Palavra de ativação detectada (confiança %.2f)", confidence)
        self._on_detect("alfred", confidence)
        return True

    def _verify(self) -> bool:
        audio = np.concatenate(list(self._buffer)) if self._buffer else np.zeros(0, np.int16)
        full = self._factory(None)
        full.AcceptWaveform(audio.tobytes())
        text = json.loads(full.FinalResult()).get("text", "")
        return bool(KEYWORD_RE.search(normalize(text)))
