# Desempenho

Metas iniciais (seção 9 da especificação), sujeitas a benchmark:

| Métrica | Meta |
| ------- | ---- |
| Inicialização | ≤ 5 s |
| RAM em idle | ≤ 150 MB |
| CPU média em idle | ≤ 2% |
| Interface | até 60 FPS |

Medição com `uv run python scripts/measure_idle.py --seconds 30 [--headless]`: inicia o ALFRED
com diretório de dados vazio (inclui criar o banco e migrar o schema), espera o log
"ALFRED em execução" e amostra o interpretador a cada 1 s. A CPU é normalizada pelo número
de núcleos.

## Histórico

| Data | Versão | Modo | Inicialização | RAM média | CPU média (máx.) |
| ---- | ------ | ---- | ------------- | --------- | ---------------- |
| 2026-11-16 | 0.1.0 (Fase 1, #8) | Qt + bandeja | 1,75 s | 97,9 MB | 0,02% (0,13%) |
| 2026-11-16 | 0.1.0 (Fase 1, #8) | headless | 1,18 s | 69,7 MB | 0,00% (0,00%) |
| 2026-11-16 | 0.1.0 (Fase 2 completa) | Qt + bandeja + widget + overlay | 1,46 s | 117,3 MB | 0,03% (0,13%) |

Máquina: Windows 11 Pro (build 26300), Python 3.12.16. Os consumos de modelos de IA e de
reconhecimento de voz serão medidos separadamente quando esses módulos existirem.

## Voz (medido separadamente, como pede a seção 9)

Reconhecimento de fala (faster-whisper, CPU, int8), 6 s de fala pt-BR sintetizada:

| Modelo | 1ª transcrição (inclui carga) | Com modelo carregado | RAM adicional |
| ------ | ----------------------------- | -------------------- | ------------- |
| tiny | 1,28 s | 0,36 s | +114 MB |
| **base** (padrão) | 1,19 s | 0,56 s | +133 MB |
| small | — (primeiro download ~460 MB) | 1,81 s | +327 MB |

O modelo é descarregado após `voice.stt_idle_unload_seconds` sem uso (padrão 120 s).

Palavra de ativação (Vosk small-pt, ADR-0005): ~88 MB enquanto a ativação por voz está ligada;
CPU ≈ 0,02 × tempo real sobre trechos de fala, ~0 em silêncio.

Pilha de voz completa em execução (2026-11-16, ao fim da Fase 3), medida no processo com os
backends reais — microfone aberto (FIFINE, 16 kHz), palavra de ativação ativa, STT e TTS sob
demanda, 15 s em ambiente real (sem a interface Qt):

| Módulos | Inicialização | CPU média | RAM do processo |
| ------- | ------------- | --------- | --------------- |
| 14 (núcleo + áudio, wake word, STT, TTS, barge-in, pipeline) | 0,7 s | 0,97% | 212 MB |

A meta de RAM em idle (≤ 150 MB) vale para o ALFRED com a voz desligada (padrão); com a voz
ligada, o custo adicional vem principalmente do modelo da palavra de ativação (~88 MB).
