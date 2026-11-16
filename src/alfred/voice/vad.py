"""Detecção de atividade de voz por energia, com piso de ruído adaptativo."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 1_280  # 80 ms
FRAME_MS = FRAME_SAMPLES * 1000 // SAMPLE_RATE

type Frame = npt.NDArray[np.int16]


def level_dbfs(frame: Frame) -> float:
    if frame.size == 0:
        return -120.0
    rms = math.sqrt(float(np.mean(np.square(frame.astype(np.float64)))))
    return 20 * math.log10(max(rms, 1e-9) / 32768.0)


@dataclass
class EnergyVAD:
    """Fala = nível acima do piso de ruído + ``margin_db`` (e acima de ``min_db``).

    O piso acompanha o ruído ambiente só durante silêncio; ``hangover`` mantém o estado de fala
    por alguns quadros para não cortar pausas curtas entre palavras.
    """

    margin_db: float = 12.0
    min_db: float = -48.0
    hangover_frames: int = 4
    floor_adapt: float = 0.05
    floor_rise_loud: float = 0.01
    noise_floor: float = -60.0
    _hang: int = field(default=0, init=False)
    speaking: bool = field(default=False, init=False)

    def process(self, frame: Frame) -> bool:
        level = level_dbfs(frame)
        loud = level > max(self.min_db, self.noise_floor + self.margin_db)
        # Piso de ruído: desce rápido quando o nível cai; sobe devagar mesmo durante "fala",
        # para absorver ruídos constantes (ventilador, ar-condicionado) em poucos segundos.
        if level < self.noise_floor:
            self.noise_floor += (level - self.noise_floor) * 0.5
        else:
            rate = self.floor_rise_loud if loud else self.floor_adapt
            self.noise_floor += (level - self.noise_floor) * rate
        if loud:
            self._hang = self.hangover_frames
            self.speaking = True
        elif self._hang > 0:
            self._hang -= 1  # ainda conta como fala: pausa curta entre palavras
            self.speaking = True
        else:
            self.speaking = False
        return self.speaking


@dataclass
class Endpointer:
    """Decide quando uma fala terminou: houve voz e depois ``silence_ms`` de silêncio."""

    vad: EnergyVAD = field(default_factory=EnergyVAD)
    silence_ms: int = 800
    no_speech_timeout_ms: int = 5000
    max_ms: int = 15000
    heard_speech: bool = field(default=False, init=False)
    _silence: int = field(default=0, init=False)
    _elapsed: int = field(default=0, init=False)

    def process(self, frame: Frame) -> str | None:
        """Devolve ``"end"``, ``"no_speech"`` ou ``"max"`` quando a escuta deve parar."""
        self._elapsed += FRAME_MS
        if self.vad.process(frame):
            self.heard_speech = True
            self._silence = 0
        else:
            self._silence += FRAME_MS
        if self.heard_speech and self._silence >= self.silence_ms:
            return "end"
        if not self.heard_speech and self._elapsed >= self.no_speech_timeout_ms:
            return "no_speech"
        if self._elapsed >= self.max_ms:
            return "max"
        return None
