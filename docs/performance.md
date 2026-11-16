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

Máquina: Windows 11 Pro (build 26300), Python 3.12.16. Os consumos de modelos de IA e de
reconhecimento de voz serão medidos separadamente quando esses módulos existirem.
