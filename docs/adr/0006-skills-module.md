# ADR-0006: Módulo de skills como camada acima de voz, IA e automação

- **Status:** Aceito
- **Data:** 2026-11-17

## Contexto

O usuário quer capacidades extras que possa ligar e desligar, como ditado (#124) e aprendizado
sobre os programas instalados (#125). Essas capacidades combinam módulos que, pelas fronteiras do
ADR-0001, não podem se importar entre si. O ditado, por exemplo, usa a transcrição (`voice`) e a
digitação (`automation`). Também não podem virar um atalho em volta das permissões (ADR-0002).

## Decisão

- Nova camada `alfred.skills`, **acima** de `ai | voice | automation | triggers | memory` e
  abaixo de `ui`, verificada pelo import-linter.
- Contrato `Skill` (`skills/base.py`): `key`, `name`, `description`, `tools()`, `commands()`,
  `activate()` e `deactivate()`.
- `SkillManager` (módulo `skills`) registra as skills. Na ativação, adiciona as ferramentas ao
  `ToolRegistry` e as frases ao `CommandRegistry`; na desativação, remove as duas, **sem
  reiniciar**.
- Uma exceção em `activate()` desliga só aquela skill. O problema aparece em
  Configurações › Skills e na saúde do módulo, e o ALFRED segue funcionando.
- O estado fica em `settings.json` (`skills.enabled`), como as demais preferências (ADR-0004).
- As ferramentas das skills são ferramentas comuns: passam pelo orquestrador, pela allowlist,
  pela validação, pelas permissões e pela contaminação por conteúdo externo (threat model T1/T2).

## Alternativas consideradas

- **Plugins carregados dinamicamente de uma pasta.** Executariam código de terceiros dentro do
  ALFRED, com acesso a teclado, tela e credenciais. Fica para quando houver assinatura ou sandbox.
- **Skills dentro de `automation` ou `voice`.** Obrigaria esses módulos a se importarem entre si,
  quebrando as fronteiras do ADR-0001.

## Consequências

- Uma capacidade nova vira uma classe `Skill` registrada na raiz de composição (`app.py`), sem
  tocar no orquestrador.
- `ToolRegistry` ganhou `unregister()`.
- Skills só podem ser escritas por quem altera o código do ALFRED. Não há loja nem instalação de
  terceiros.
