# Desempenho

Metas iniciais (seção 9 da especificação), sujeitas a benchmark:

| Métrica | Meta |
| ------- | ---- |
| Inicialização | ≤ 5 s |
| RAM em idle | ≤ 150 MB |
| CPU média em idle | ≤ 2% |
| Interface | até 60 FPS |

Medição com `uv run python scripts/measure_idle.py --seconds 30 [--headless] [--voice]`: inicia o
ALFRED com diretório de dados vazio e lock de instância próprio (dá para medir com o ALFRED do usuário
aberto; `--voice` reaproveita os modelos já baixados via `ALFRED_MODELS_DIR`) (inclui criar o banco e migrar o schema), espera o log
"ALFRED em execução" e amostra o interpretador a cada 1 s. A CPU é normalizada pelo número
de núcleos.

## Histórico

| Data | Versão | Modo | Inicialização | RAM média | CPU média (máx.) |
| ---- | ------ | ---- | ------------- | --------- | ---------------- |
| 2026-11-16 | 0.1.0 (Fase 1, #8) | Qt + bandeja | 1,75 s | 97,9 MB | 0,02% (0,13%) |
| 2026-11-16 | 0.1.0 (Fase 1, #8) | headless | 1,18 s | 69,7 MB | 0,00% (0,00%) |
| 2026-11-16 | 0.1.0 (Fase 2 completa) | Qt + bandeja + widget + overlay | 1,46 s | 117,3 MB | 0,03% (0,13%) |
| 2026-11-16 | 0.1.0 (Fase 7, antes do #47) | headless | 4,74 s | 92,4 MB | 0,00% (0,00%) |
| 2026-11-16 | 0.1.0 (Fase 7, antes do #47) | Qt completo | 3,54 s | 137,8 MB | 0,02% (0,26%) |
| 2026-11-16 | 0.1.0 (Fase 7, antes do #47) | Qt completo + voz | **5,48 s** | 240,6 MB | 0,07% (0,26%) |
| 2026-11-16 | 0.1.0 (#47) | headless | 1,74 s | 87,8 MB | 0,00% (0,00%) |
| 2026-11-16 | 0.1.0 (#47) | Qt completo | 1,86 s | 133,4 MB | 0,00% (0,00%) |
| 2026-11-16 | 0.1.0 (#47) | Qt completo + voz | 2,52 s | 236,9 MB | 0,07% (0,52%) |

Máquina: Windows 11 Pro (build 26300), Python 3.12.16, 16 GB de RAM, RTX 3060 (8 GB).
"Qt completo" inclui todos os módulos das Fases 1–6 (automação, gatilhos, memória, orquestrador).

### Otimizações do #47

| Problema medido | Causa | Correção | Ganho |
| --------------- | ----- | -------- | ----- |
| Inicialização de 3,5–5,5 s (com voz acima da meta) | o módulo `apps` esperava o `Get-StartApps` do PowerShell (~1 s) | lista carregada em segundo plano; abrir/fechar app espera só se ainda estiver carregando | −1 s |
| `httpx` importado na inicialização | import no topo dos provedores de IA | import sob demanda | −0,2 s |
| Primeiro pedido à IA após ociosidade | carga do modelo na GPU (~17–20 s) | pré-carga ao detectar a palavra de ativação, enquanto o usuário fala | −(duração da fala) |

`tests/test_performance.py` impede regressões: o ponto de entrada não pode importar módulos pesados
(numpy, Whisper, Vosk, onnxruntime, pywinauto, Playwright, mss, httpx, QtWidgets), a lista de apps
não pode atrasar o `start()` e a pré-carga nunca toca provedores externos.

### Interface (`uv run python scripts/bench_ui.py`)

Monitor de 60 Hz, renderização real (não *offscreen*):

| Animação | FPS | Quadro (mediana) | Quadro (p95) |
| -------- | --- | ---------------- | ------------ |
| Widget "processando" | 62,3 | 16,0 ms | 16,9 ms |
| Cursor virtual em movimento | 60,7 | 16,0 ms | 17,6 ms |

### IA local (`uv run python scripts/bench_ai.py`), medida separadamente

Ollama 0.40.16, `qwen2.5:7b` (Q4_K_M, 4,8 GB, 100% na GPU):

| Métrica | Valor |
| ------- | ----- |
| Primeira resposta com o modelo descarregado | 19,7–20,8 s (carga dos pesos na GPU) |
| Mesma situação com pré-carga ao ouvir "Alfred" + 3 s de fala | 16,6 s após o fim da fala |
| Resposta com o modelo carregado (mediana) | 1,1 s (42 tokens/s) |
| Planejar uma chamada de ferramenta ("abra a calculadora") | 0,59 s, escolheu `abrir_aplicativo` |
| RAM do processo Ollama | ~28 MB (os pesos ficam na VRAM: 4,8 GB) |

O modelo fica carregado por `keep_alive` = 5 min após o último uso e então libera a VRAM
(Diretriz 3).

## Desvios documentados

- **RAM com a voz ligada: ~237 MB** (meta: 150 MB). A meta vale para o padrão, com a voz desligada
  (133 MB). O adicional vem do modelo da palavra de ativação (Vosk, ~88 MB) e da pilha de áudio.
  Com `voice.stt_model = "small"`, a transcrição soma ~330 MB enquanto o modelo está carregado,
  e ele é descarregado após `voice.stt_idle_unload_seconds` (120 s). O modelo `base` (padrão)
  soma ~130 MB.
- **Primeiro pedido à IA após 5 min parado: ~17–20 s.** É o tempo de carregar ~5 GB de pesos na
  GPU. Manter o modelo carregado o tempo todo violaria a Diretriz 3. A pré-carga ao ouvir
  "Alfred" esconde a duração da fala, e com o modelo quente a resposta leva ~1 s. Os consumos de modelos de IA e de
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
