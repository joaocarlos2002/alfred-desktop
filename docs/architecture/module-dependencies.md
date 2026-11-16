# Dependências entre módulos

As fronteiras abaixo são verificadas automaticamente na CI com
[import-linter](https://import-linter.readthedocs.io/) (`uv run lint-imports`, configuração em
`pyproject.toml`). Um PR que viole uma regra falha na CI.

## Camadas

Um módulo só pode importar módulos de camadas **abaixo** dele. Módulos na mesma linha são
**independentes** entre si (não podem se importar).

```text
ui
ai | voice | automation | triggers | memory
core
infrastructure
config
domain
```

| Módulo | Pode importar |
| ------ | ------------- |
| `domain` | apenas a stdlib e bibliotecas externas (ex.: Pydantic) |
| `config` | `domain` |
| `infrastructure` | `config`, `domain` |
| `core` | `infrastructure`, `config`, `domain` |
| `ai`, `voice`, `automation`, `triggers`, `memory` | `core` e abaixo — nunca um ao outro |
| `ui` | todos os anteriores |

## Raiz de composição

`alfred.app` e `alfred.__main__` ficam fora das camadas: são os únicos pontos que instanciam os
módulos e ligam suas dependências. A comunicação entre módulos irmãos (ex.: `ai` pedindo uma ação
de `automation`) acontece via contratos em `domain` e pelo event bus — nunca por import direto
(ver [ADR-0001](../adr/0001-modular-monolith-event-bus.md) e
[ADR-0002](../adr/0002-ai-never-executes-directly.md)).
