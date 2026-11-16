# ADR-0002: A IA nunca executa ações diretamente

- **Status:** Aceito
- **Data:** 2026-11-16

## Contexto

ALFRED opera aplicações, teclado, mouse, arquivos e o navegador. Modelos de linguagem podem
alucinar, ser manipulados por conteúdo da tela ou da web (prompt injection) e produzir chamadas
com parâmetros inválidos. A especificação exige autonomia elevada para tarefas comuns e
confirmação explícita para operações sensíveis.

## Decisão

O modelo de IA **apenas propõe** planos e chamadas de ferramentas. Toda ação passa por:

1. **Orquestrador** (`core`): única entidade que executa etapas de uma tarefa.
2. **Registro de ferramentas**: somente ferramentas de uma allowlist, com parâmetros validados
   por schema Pydantic.
3. **Sistema de permissões**: política permitir/perguntar/negar por categoria e por ferramenta;
   operações sensíveis (exclusão/sobrescrita de arquivos, envio de mensagens, compras,
   configurações críticas, comandos administrativos, ações externas irreversíveis) sempre exigem
   confirmação.
4. **Registro de atividades**: toda ação executada ou recusada é auditada.

Nenhum módulo de `ai` importa `automation`; a ligação ocorre via contratos em `domain`.

## Alternativas consideradas

- **Execução direta de código/comandos gerados pelo modelo** — inaceitável do ponto de vista de
  segurança e previsibilidade.
- **Confirmar toda ação** — seguro, mas inviabiliza a autonomia exigida pela especificação.

## Consequências

- Planejamento e execução ficam separados (Diretriz 5) e testáveis com um provedor fake.
- Adicionar uma capacidade nova exige registrar uma ferramenta com schema e categoria de risco.
- O mecanismo de interrupção de emergência atua no orquestrador e na camada de input.
