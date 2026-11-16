"""Spike #19 — benchmark de palavra de ativação em PT-BR.

Gera áudios sintéticos com a voz SAPI pt-BR (variações de velocidade e tom, com e sem ruído),
roda os detectores candidatos e imprime métricas em JSON. Os áudios ficam num diretório
temporário e são apagados ao final.

Uso:
    uv pip install vosk openwakeword faster-whisper pywin32
    uv run python scripts/spikes/wakeword_benchmark.py --vosk-model <dir do vosk-model-small-pt>

Limitações: voz sintética (uma voz, variações de tom/velocidade); a taxa de falsos positivos
por hora é extrapolada de alguns minutos de fala negativa — serve para comparar candidatos, não
como número final de produção.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
import unicodedata
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import psutil

SAMPLE_RATE = 16_000
FRAME = 1_280

POSITIVES = [
    "Alfred",
    "Alfred, que horas são?",
    "Ei Alfred, abra o navegador.",
    "Alfred, pare.",
    "Bom dia, Alfred.",
    "Alfred, toque uma música.",
    "Alfred, qual é a previsão do tempo?",
    "Olá Alfred.",
]
NEGATIVES = [
    "Que horas são agora?",
    "Vou almoçar mais tarde com a família.",
    "O alfaiate entregou o terno ontem.",
    "A reunião foi adiada para segunda-feira.",
    "Abra a janela, por favor.",
    "O filme começa às oito da noite.",
    "Precisamos comprar café e açúcar.",
    "A alface está fresca hoje.",
    "Ele trabalha como engenheiro em São Paulo.",
    "O trânsito estava muito lento na avenida.",
    "Amanhã vai chover bastante.",
    "Você viu o jogo de futebol ontem?",
    "Alfa, beta e gama são letras gregas.",
    "O professor explicou a matéria com calma.",
    "Desligue a televisão antes de dormir.",
    "A fruta preferida dela é a manga.",
]
HELD_OUT_NEGATIVES = [
    # Vizinhos fonéticos que NÃO estão na lista de distratoras (avaliação honesta).
    "A mercadoria ficou presa na alfândega.",
    "Ela prendeu o tecido com um alfinete.",
    "O Frederico mora no interior.",
    "O frete grátis acaba hoje.",
    "Ele falou com firmeza na frente de todos.",
    "O alfabeto tem vinte e seis letras.",
    "As frutas frescas chegaram cedo.",
    "Alfinetes e agulhas ficam na gaveta.",
]
DISTRACTORS = (  # noqa: SIM905 - lista longa fica legível como texto
    # Palavras frequentes do português + vizinhos fonéticos conhecidos de "Alfred".
    "a o e de do da em um uma para com não que os as no na se por mais como mas foi ao ele ela "
    "eu você nós eles elas meu minha seu sua isso isto esse essa este esta aqui ali agora hoje "
    "amanhã ontem bom boa dia noite tarde manhã hora horas tempo casa vida mundo trabalho coisa "
    "vez ano anos sim obrigado favor abrir abra fechar feche ligar desligar música filme janela "
    "porta carro água café comida dinheiro frio calor alto baixo onde quando porque quem qual "
    "está estou estava ser ter tem fazer faz ir vai vou ver olha falar fala tudo nada muito "
    "pouco grande pequeno novo nova primeiro depois antes sempre nunca já também só até sem "
    "alfa alface alfaiate frente fresco fresca fria frio fruta frase frade fé alto falta fralda"
).split()
CONFUSABLES = ["O Alfredo chegou cedo.", "Fred é meu vizinho.", "Conheci o senhor Alfredo."]
VARIANTS = [(-2, -4), (0, 0), (2, 4), (1, -6)]  # (velocidade SAPI, tom)

KEYWORD_RE = re.compile(r"\b(alfred|alfredo|alfredi|fred)\b")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


# ------------------------------------------------------------------ síntese
def synthesize(text: str, rate: int, pitch: int, path: Path) -> np.ndarray:
    import win32com.client

    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 18  # SAFT16kHz16BitMono
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format = fmt
    stream.Open(str(path), 3)
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    for token in voice.GetVoices():
        if "Portuguese" in token.GetDescription():
            voice.Voice = token
    voice.AudioOutputStream = stream
    voice.Rate = rate
    voice.Speak(f'<pitch absmiddle="{pitch}">{text}</pitch>', 8)  # SVSFIsXML
    stream.Close()
    with wave.open(str(path)) as wav:
        audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    silence = np.zeros(SAMPLE_RATE // 2, dtype=np.int16)
    return np.concatenate([silence, audio, silence])


def add_noise(audio: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    signal = audio.astype(np.float64)
    voiced = np.abs(signal) > 100
    power = float(np.mean(signal[voiced] ** 2)) if np.any(voiced) else 1.0
    noise = rng.normal(0, np.sqrt(power / 10 ** (snr_db / 10)), audio.size)
    return np.clip(signal + noise, -32768, 32767).astype(np.int16)


@dataclass
class Clip:
    label: str  # positive | negative | confusable
    text: str
    noisy: bool
    audio: np.ndarray
    snr: int | None = None


def build_dataset(tmp: Path) -> list[Clip]:
    rng = np.random.default_rng(19)
    clips: list[Clip] = []
    groups = (
        ("positive", POSITIVES),
        ("negative", NEGATIVES),
        ("heldout", HELD_OUT_NEGATIVES),
        ("confusable", CONFUSABLES),
    )
    for label, texts in groups:
        for i, text in enumerate(texts):
            for j, (rate, pitch) in enumerate(VARIANTS):
                audio = synthesize(text, rate, pitch, tmp / f"{label}_{i}_{j}.wav")
                clips.append(Clip(label, text, False, audio))
                clips.append(Clip(label, text, True, add_noise(audio, 10, rng), snr=10))
                clips.append(Clip(label, text, True, add_noise(audio, 20, rng), snr=20))
    return clips


# ------------------------------------------------------------------ detectores
@dataclass
class Result:
    name: str
    hits: dict[str, list[bool]] = field(default_factory=dict)
    cpu_seconds: float = 0.0
    audio_seconds: float = 0.0
    rss_mb: float = 0.0
    load_seconds: float = 0.0
    notes: str = ""

    def record(self, clip: Clip, detected: bool) -> None:
        key = clip.label + (f"_snr{clip.snr}" if clip.snr else "")
        self.hits.setdefault(key, []).append(detected)


def two_stage(first: Result, second: Result) -> Result:
    """Etapa 1 sempre ligada; etapa 2 só roda (e só custa) quando a 1 dispara."""
    combined = Result(f"duas etapas: {first.name} → {second.name}")
    combined.rss_mb = first.rss_mb + second.rss_mb
    combined.audio_seconds = first.audio_seconds
    triggered = sum(sum(v) for v in first.hits.values())
    total = sum(len(v) for v in first.hits.values())
    combined.cpu_seconds = first.cpu_seconds + second.cpu_seconds * triggered / max(total, 1)
    for key, values in first.hits.items():
        combined.hits[key] = [a and b for a, b in zip(values, second.hits[key], strict=True)]
    combined.notes = f"etapa 2 executada em {triggered}/{total} trechos"
    return combined


def rss_mb() -> float:
    return psutil.Process().memory_info().rss / 2**20


def _vosk_hit(payload: str, min_conf: float) -> bool:
    for word in json.loads(payload).get("result", []):
        if KEYWORD_RE.fullmatch(normalize(word["word"])) and word.get("conf", 1.0) >= min_conf:
            return True
    return False


def run_vosk(
    clips: list[Clip],
    model_dir: str,
    *,
    grammar: bool,
    min_conf: float,
    partials: bool,
    distractors: bool = False,
) -> Result:
    """``grammar``: vocabulário restrito; senão, modelo de linguagem completo.

    Com ``partials`` a detecção usa resultados parciais (sem confiança); sem, só resultados
    finais com confiança por palavra >= ``min_conf``.
    """
    import vosk

    vosk.SetLogLevel(-1)
    before, started = rss_mb(), time.perf_counter()
    model = vosk.Model(model_dir)
    mode = (
        ("gramática+distratoras" if distractors else "gramática") if grammar else "modelo completo"
    )
    label = f"vosk-small-pt ({mode}, " + ("parciais)" if partials else f"conf≥{min_conf})")
    result = Result(label, load_seconds=time.perf_counter() - started)
    result.rss_mb = rss_mb() - before
    vocabulary = ["alfredo", "fred", *(DISTRACTORS if distractors else []), "[unk]"]
    words = json.dumps(vocabulary)
    for clip in clips:
        recognizer = (
            vosk.KaldiRecognizer(model, SAMPLE_RATE, words)
            if grammar
            else vosk.KaldiRecognizer(model, SAMPLE_RATE)
        )
        recognizer.SetWords(True)
        detected = False
        cpu = time.process_time()
        for start in range(0, clip.audio.size, FRAME):
            chunk = clip.audio[start : start + FRAME].tobytes()
            if recognizer.AcceptWaveform(chunk):
                detected |= _vosk_hit(recognizer.Result(), min_conf)
            elif partials:
                partial = json.loads(recognizer.PartialResult())["partial"]
                detected |= bool(KEYWORD_RE.search(normalize(partial)))
        detected |= _vosk_hit(recognizer.FinalResult(), min_conf)
        result.cpu_seconds += time.process_time() - cpu
        result.audio_seconds += clip.audio.size / SAMPLE_RATE
        result.record(clip, detected)
    return result


def run_whisper(clips: list[Clip], model_name: str) -> Result:
    from faster_whisper import WhisperModel

    before, started = rss_mb(), time.perf_counter()
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    result = Result(f"faster-whisper {model_name} (transcrição)")
    result.load_seconds = time.perf_counter() - started
    result.rss_mb = rss_mb() - before
    for clip in clips:
        cpu = time.process_time()
        segments, _ = model.transcribe(
            clip.audio.astype(np.float32) / 32768.0,
            language="pt",
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        text = " ".join(s.text for s in segments)
        result.cpu_seconds += time.process_time() - cpu
        result.audio_seconds += clip.audio.size / SAMPLE_RATE
        result.record(clip, bool(KEYWORD_RE.search(normalize(text))))
    return result


def run_openwakeword(clips: list[Clip]) -> Result:
    import openwakeword
    from openwakeword.model import Model

    openwakeword.utils.download_models(["hey_jarvis"])
    before, started = rss_mb(), time.perf_counter()
    model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    result = Result("openWakeWord (hey_jarvis, só custo)")
    result.load_seconds = time.perf_counter() - started
    result.rss_mb = rss_mb() - before
    result.notes = "Sem modelo para 'Alfred' em PT-BR: exige treinar um modelo próprio."
    for clip in clips:
        cpu = time.process_time()
        for start in range(0, clip.audio.size - FRAME, FRAME):
            model.predict(clip.audio[start : start + FRAME])
        result.cpu_seconds += time.process_time() - cpu
        result.audio_seconds += clip.audio.size / SAMPLE_RATE
    return result


def summarize(result: Result, negative_seconds: float) -> dict[str, object]:
    def rate(key: str) -> float | None:
        values = result.hits.get(key)
        return round(sum(values) / len(values), 3) if values else None

    def total(prefix: str) -> int:
        return sum(sum(v) for k, v in result.hits.items() if k.split("_")[0] == prefix)

    false_positives = total("negative")
    heldout_fp = total("heldout")
    measured = bool(result.hits)
    return {
        "detector": result.name,
        "detecção (limpo)": rate("positive"),
        "detecção (ruído 20 dB)": rate("positive_snr20"),
        "detecção (ruído 10 dB)": rate("positive_snr10"),
        "falsos positivos (negativos)": false_positives if measured else None,
        "falsos positivos/h (extrapolado)": round(
            false_positives / (3 * negative_seconds) * 3600, 1
        )
        if measured
        else None,
        "falsos positivos (vizinhos não vistos, de 96)": heldout_fp if measured else None,
        "ativa com confundíveis": rate("confusable"),
        "CPU (fator tempo real)": round(result.cpu_seconds / result.audio_seconds, 4),
        "RAM do modelo (MB)": round(result.rss_mb, 1),
        "carga (s)": round(result.load_seconds, 2),
        "obs": result.notes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vosk-model", required=True)
    parser.add_argument("--whisper-model", default="tiny")
    parser.add_argument("--vosk-only", action="store_true")
    parser.add_argument("--final", action="store_true", help="só os candidatos finais")
    args = parser.parse_args()
    os.environ.setdefault("OMP_NUM_THREADS", "1")

    with tempfile.TemporaryDirectory(prefix="alfred-wakeword-") as tmp:
        clips = build_dataset(Path(tmp))
    negative_seconds = (
        sum(c.audio.size for c in clips if c.label == "negative" and not c.noisy) / SAMPLE_RATE
    )
    if args.final:
        stage1 = run_vosk(clips, args.vosk_model, grammar=True, min_conf=0.9, partials=False)
        stage2 = run_vosk(clips, args.vosk_model, grammar=False, min_conf=0.0, partials=False)
        results = [stage1, stage2, two_stage(stage1, stage2)]
        print(
            json.dumps(
                [summarize(r, negative_seconds) for r in results], ensure_ascii=False, indent=2
            )
        )
        return 0
    summary = {
        "dataset": {
            "clips": len(clips),
            "positivos": sum(c.label == "positive" for c in clips),
            "negativos": sum(c.label == "negative" for c in clips),
            "confundíveis": sum(c.label == "confusable" for c in clips),
            "minutos de fala negativa (limpo + 2 ruídos)": round(3 * negative_seconds / 60, 2),
        },
        "resultados": [
            summarize(
                run_vosk(clips, args.vosk_model, grammar=True, min_conf=0.0, partials=True),
                negative_seconds,
            ),
            *[
                summarize(
                    run_vosk(
                        clips,
                        args.vosk_model,
                        grammar=True,
                        min_conf=c,
                        partials=False,
                        distractors=True,
                    ),
                    negative_seconds,
                )
                for c in (0.5, 0.8, 0.9)
            ],
            *[
                summarize(
                    run_vosk(clips, args.vosk_model, grammar=True, min_conf=c, partials=False),
                    negative_seconds,
                )
                for c in (0.9, 0.98)
            ],
            summarize(
                run_vosk(clips, args.vosk_model, grammar=False, min_conf=0.0, partials=False),
                negative_seconds,
            ),
            *(
                []
                if args.vosk_only
                else [
                    summarize(run_whisper(clips, args.whisper_model), negative_seconds),
                    summarize(run_openwakeword(clips), negative_seconds),
                ]
            ),
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
