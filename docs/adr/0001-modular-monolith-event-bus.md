# ADR-0001: Modular Monolith com event bus interno

- **Status:** Aceito
- **Data:** 2026-11-16

## Contexto

ALFRED é uma aplicação desktop de usuário único que precisa ser leve em idle (meta: até 150 MB de
RAM e até 2% de CPU), iniciar em até 5 s e funcionar offline. Ela reúne capacidades muito diferentes
(voz, automação, IA, UI) que evoluem em ritmos diferentes e precisam ser testáveis isoladamente.

## Decisão

Adotar um **Modular Monolith**: um único processo Python com módulos independentes em
`src/alfred/` (`core`, `domain`, `ai`, `voice`, `automation`, `triggers`, `memory`, `ui`,
`infrastructure`, `config`).

- Os módulos se comunicam por **contratos explícitos** (definidos em `domain`) e por um
  **event bus assíncrono** em processo (`asyncio`), com eventos tipados (Pydantic).
- As dependências entre módulos seguem camadas, verificadas automaticamente na CI
  (ver [module-dependencies.md](../architecture/module-dependencies.md)).
- A composição (instanciar módulos e ligar dependências) acontece apenas na raiz de composição
  (`alfred.app`).

## Alternativas consideradas

- **Microsserviços / múltiplos processos com IPC** — overhead de memória e inicialização
  incompatível com as metas de idle, além de complexidade operacional sem necessidade comprovada
  (Diretriz 8).
- **Monolito sem fronteiras** — rápido no início, mas gera acoplamento entre voz, UI e automação e
  dificulta testes por módulo.

## Consequências

- Baixa latência entre módulos e um único artefato para empacotar.
- Operações bloqueantes (áudio, Win32, modelos) precisam rodar em threads/executores para não
  travar o event loop.
- Se no futuro um componente precisar de isolamento (ex.: um runtime de modelo), ele pode ser
  extraído para um processo separado atrás do mesmo contrato.
