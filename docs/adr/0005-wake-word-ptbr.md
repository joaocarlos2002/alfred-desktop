# ADR-0005: Palavra de ativação "Alfred" em PT-BR com Vosk em duas etapas

- **Status:** Aceito (provisório até existir um modelo openWakeWord próprio — #78)
- **Data:** 2026-11-16
- **Issue:** #19

## Contexto

A especificação sugere openWakeWord, "sujeito a validação para PT-BR". A palavra é "Alfred",
pronunciada em português (em geral "Álfredi"). Requisitos: detecção contínua com baixo consumo,
poucos falsos positivos e operação local.

## Método

`scripts/spikes/wakeword_benchmark.py` gera 420 trechos sintéticos com a voz SAPI pt-BR (Maria),
variando velocidade e tom: 8 frases positivas, 16 negativas comuns, 8 negativas com vizinhos
fonéticos **fora** de qualquer lista de ajuste ("alfândega", "alfinete", "Frederico", "frete")
e 3 confundíveis ("Alfredo", "Fred") — cada uma limpa, com ruído a 20 dB e a 10 dB de SNR.
Windows 11, CPU de desktop, 1 thread.

| Detector | Limpo | Ruído 20 dB | Ruído 10 dB | Falsos positivos (negativos) | Vizinhos não vistos | CPU (× tempo real) | RAM |
| -------- | ----- | ----------- | ----------- | ---------------------------- | ------------------- | ------------------ | --- |
| Vosk small-pt, gramática `["alfredo","fred","[unk]"]`, parciais | 100% | — | 100% | 93 / 128 | 61 / 64 | 0,022 | 88 MB |
| Vosk, gramática, confiança ≥ 0,9 | 100% | 100% | 94% | 25 / 192 (~103/h) | 54 / 96 | 0,021 | 88 MB |
| Vosk, gramática + 150 distratoras, confiança ≥ 0,5 | 59% | — | 69% | 0 / 128 | 8 / 64 | 0,038 | 88 MB |
| Vosk, modelo de linguagem completo | 100% | 66% | 0% | **0** | **0 / 96** | 0,278 | 85 MB |
| **Duas etapas: gramática (≥ 0,9) → verificação com modelo completo** | **100%** | **66%** | **0%** | **0** | **0 / 96** | 0,021 + 0,278 só nos disparos | 88 MB (modelo único) |
| faster-whisper tiny (transcrição) | 100% | — | 28% | 0 / 128 | — | 0,217 | 114 MB, 12 s de carga |
| openWakeWord (`hey_jarvis`, só custo) | n/a | n/a | n/a | n/a | n/a | 0,043 | 38 MB |

Observações:

- **"alfred" não está no vocabulário** do modelo pt do Vosk; "alfredo" e "fred" estão e cobrem a
  pronúncia brasileira.
- Gramática restrita **sozinha** força qualquer fala para as poucas palavras disponíveis: alta
  detecção, mas dezenas de falsos positivos por hora. Distratoras reduzem falsos positivos, mas
  derrubam a detecção.
- O **modelo completo** não confunde vizinhos fonéticos, mas é caro para rodar continuamente.
- **openWakeWord não tem modelo para "Alfred"** (nem para PT-BR); usá-lo exige treinar um modelo
  próprio, fora do escopo deste spike.

## Decisão

Palavra de ativação em **duas etapas sobre um único modelo Vosk small-pt (~88 MB)**:

1. **Detecção barata e contínua** — gramática `["alfredo", "fred", "[unk]"]` com confiança por
   palavra ≥ limiar (padrão 0,9; a "sensibilidade" nas configurações ajusta esse limiar). Só
   processa áudio quando o VAD indica fala: em silêncio o custo é praticamente zero.
2. **Verificação** — quando a etapa 1 dispara, o mesmo trecho (buffer de ~2 s) é reconhecido com o
   modelo de linguagem completo; só ativa se a transcrição contiver "alfred(o)/fred".

O modelo é baixado sob demanda para `data/models` e carregado apenas com a voz ativada.

## Alternativas descartadas

- **openWakeWord pronto** — sem modelo PT-BR/"Alfred". Mantido como **solução definitiva**: treinar
  um modelo próprio (#78), que tende a ser mais leve (≈ 0,04 × tempo real, 38 MB) e mais robusto
  a ruído.
- **Whisper tiny como detector** — 10× mais CPU, 12 s de carga e baixa detecção com ruído.
- **Gramática sem verificação** — falsos positivos demais para uma ativação por voz.

## Consequências e limites conhecidos

- **Ruído**: a detecção cai para ~66% a 20 dB de SNR e não funciona com ruído forte (10 dB). O
  atalho de ativação continua disponível.
- **"Alfredo" e "Fred" também ativam** (~92%) — limitação do vocabulário do modelo.
- Números obtidos com **voz sintética**; precisam ser revalidados com gravações reais.
- RAM: +~88 MB quando a voz está ativada (a voz é desligada por padrão).
