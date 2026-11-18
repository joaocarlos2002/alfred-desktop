"""Estado visual do widget e do painel rápido (#113): cor, rótulo e curvas de animação.

O estado visual deriva do estado real do assistente (``AssistantState``) e de ele estar falando
(eventos ``SpeechStarted``/``SpeechFinished``). Nada aqui é decorativo sem lastro: cada modo
corresponde a algo que o ALFRED está fazendo de fato.
"""

from __future__ import annotations

import math
from enum import StrEnum

from alfred.domain.assistant import AssistantState
from alfred.ui.theme import THEME


class VisualMode(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    EXECUTING = "executing"
    SPEAKING = "speaking"
    CONFIRM = "confirm"
    DONE = "done"
    ERROR = "error"


def _rgb(token: str) -> tuple[int, int, int]:
    color = THEME.stage_color(token)
    return color.red(), color.green(), color.blue()


# Cor de cada modo (tokens ``mode_*`` em ``theme.Stage``).
MODE_RGB: dict[VisualMode, tuple[int, int, int]] = {
    mode: _rgb(f"mode_{mode}") for mode in VisualMode
}

MODE_LABEL: dict[VisualMode, str] = {
    VisualMode.IDLE: "Em repouso",
    VisualMode.LISTENING: "Ouvindo",
    VisualMode.THINKING: "Pensando",
    VisualMode.EXECUTING: "Executando",
    VisualMode.SPEAKING: "Falando",
    VisualMode.CONFIRM: "Aguardando você",
    VisualMode.DONE: "Concluído",
    VisualMode.ERROR: "Algo deu errado",
}

# Intensidade da animação (onda do status, anéis) em cada modo.
MODE_ENERGY: dict[VisualMode, float] = {
    VisualMode.IDLE: 0.15,
    VisualMode.LISTENING: 0.8,
    VisualMode.THINKING: 0.45,
    VisualMode.EXECUTING: 0.55,
    VisualMode.SPEAKING: 1.0,
    VisualMode.CONFIRM: 0.6,
    VisualMode.DONE: 0.3,
    VisualMode.ERROR: 0.2,
}


def visual_mode(state: AssistantState, *, speaking: bool = False) -> VisualMode:
    """Ouvir, pedir confirmação e erro têm prioridade sobre a fala."""
    if state is AssistantState.LISTENING:
        return VisualMode.LISTENING
    if state is AssistantState.AWAITING_CONFIRMATION:
        return VisualMode.CONFIRM
    if state is AssistantState.ERROR:
        return VisualMode.ERROR
    if speaking:
        return VisualMode.SPEAKING
    return {
        AssistantState.PROCESSING: VisualMode.THINKING,
        AssistantState.EXECUTING: VisualMode.EXECUTING,
        AssistantState.DONE: VisualMode.DONE,
    }.get(state, VisualMode.IDLE)


# ---------------------------------------------------------------------- curvas
def clamp(x: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, x))


def out_expo(x: float) -> float:
    x = clamp(x)
    return 1.0 if x >= 1 else 1 - 2 ** (-10 * x)


def in_expo(x: float) -> float:
    x = clamp(x)
    return 0.0 if x <= 0 else 2 ** (10 * x - 10)


def out_back(x: float, overshoot: float = 1.70158) -> float:
    """Mola: passa um pouco do alvo e volta."""
    x = clamp(x)
    c3 = overshoot + 1
    return 1 + c3 * (x - 1) ** 3 + overshoot * (x - 1) ** 2


def stagger(elapsed_ms: float, delay_ms: float, duration_ms: float) -> float:
    """Progresso linear (0..1) de um item que começa em ``delay_ms``."""
    return clamp((elapsed_ms - delay_ms) / duration_ms)


def wobble(i: float, t: float) -> float:
    """Ruído suave e determinístico em [-1, 1] (ondas, equalizador)."""
    return (
        math.sin(i * 1.7 + t * 3.1) + math.sin(i * 0.6 - t * 5.3) + math.sin(i * 2.9 + t * 1.3)
    ) / 3


def approach(current: float, target: float, dt: float, rate: float = 0.08) -> float:
    """Aproxima ``current`` de ``target`` de forma independente da taxa de quadros."""
    k = 1.0 - float((1 - rate) ** (dt * 60))
    return current + (target - current) * k
