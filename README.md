# ALFRED — Desktop AI Assistant

<p align="center">
  <img src="docs/images/Mordomo%20Elegante%20com%20Cloche%20e%20Tablet.png" alt="ALFRED" width="260">
</p>

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
uv run alfred                 # bandeja do sistema; "Sair" no menu encerra
uv run alfred --headless      # sem interface, para diagnóstico
uv run alfred --log-level DEBUG
```

A inicialização automática com o Windows é controlada pelo item "Iniciar com o Windows" do menu
da bandeja (ou `general.start_with_windows` em `settings.json`) e fica desativada por padrão.

Os dados locais (logs, banco, cache, modelos) ficam em `%LOCALAPPDATA%\ALFRED`. Em
desenvolvimento, use `$env:ALFRED_DATA_DIR = "data"` para gravá-los em `./data`.

Os logs são JSON Lines em `logs/alfred.log`, com rotação por tamanho, `correlation_id` da tarefa
e mascaramento automático de segredos (tokens, chaves de API, senhas).

## Qualidade

```powershell
uv run ruff check .          # lint
uv run ruff format .         # formatação
uv run mypy                  # tipos (strict em domain e core)
uv run lint-imports          # fronteiras entre módulos
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

## Arquitetura

- [Dependências entre módulos](docs/architecture/module-dependencies.md)
- [Architecture Decision Records](docs/adr/README.md)
- [Desempenho](docs/performance.md)
- [Design system](docs/design-system.md)

## Roadmap

O desenvolvimento segue 7 fases, acompanhadas pelos
[milestones](https://github.com/joaocarlos2002/alfred-desktop/milestones) do repositório.
