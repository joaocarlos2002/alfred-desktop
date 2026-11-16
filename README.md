# ALFRED — Desktop AI Assistant

> *Always at your service.*

Assistente pessoal de inteligência artificial para **Windows 11**: roda em segundo plano,
entende comandos por voz ou teclado e executa tarefas diretamente no computador.
Local-first, leve e modular, com suporte opcional a provedores externos de IA.

## Requisitos

- Windows 11
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Setup

```powershell
uv sync --all-extras --dev
uv run alfred --version
```

## Qualidade

```powershell
uv run ruff check .          # lint
uv run ruff format .         # formatação
uv run mypy                  # tipos (strict em domain e core)
uv run pytest --cov          # testes + cobertura
```

Os testes marcados com `@pytest.mark.windows` exigem uma sessão de desktop real e não rodam na CI.

## Estrutura

```text
src/alfred/
├── core/            # orquestração, ciclo de vida e tarefas
├── domain/          # entidades, contratos e regras de negócio
├── ai/              # provedores, contexto, planejamento e ferramentas
├── voice/           # áudio, wake word, STT e TTS
├── automation/      # Windows, aplicações, navegador, teclado e mouse
├── triggers/        # gatilhos
├── memory/          # memória, conversas, preferências e histórico
├── ui/              # widget, dashboard, configurações e overlays
├── infrastructure/  # persistência, logging e integração com o Windows
└── config/          # configurações e valores padrão
data/                # logs, cache, modelos e exportações (não versionado)
docs/                # documentação e ADRs
```

## Roadmap

O desenvolvimento segue 7 fases, acompanhadas pelos
[milestones](https://github.com/joaocarlos2002/alfred-desktop/milestones) do repositório.
