# ADR-0004: Configurações em JSON, fora do SQLite

- **Status:** Aceito
- **Data:** 2026-11-16

## Contexto

A seção 7 da especificação lista `settings` entre as entidades do banco. As configurações, porém,
são necessárias **antes** de o banco subir (nível e destino dos logs, diretórios, atalhos) e
precisam continuar recuperáveis quando o banco estiver corrompido ou numa versão de schema
incompatível.

## Decisão

As configurações ficam em `settings.json` no diretório de dados, gerenciadas por
`alfred.config.store.SettingsStore`:

- escrita atômica, `schema_version` com migrações e backup do arquivo anterior;
- campo inválido volta ao padrão sem descartar os demais; arquivo ilegível vai para backup;
- alterações em tempo de execução passam pelo `SettingsService` e publicam `SettingsChanged`.

O banco **não** possui tabela `settings`. As demais entidades da seção 7 permanecem no SQLite
(ADR-0003).

## Alternativas consideradas

- **Tabela `settings` no SQLite** — exigiria o banco ativo e migrado antes do logging, e uma falha
  do banco impediria até a inicialização em modo seguro.
- **Duas fontes (arquivo + tabela)** — risco de divergência sem benefício claro.

## Consequências

- O usuário (ou o suporte) consegue inspecionar e corrigir as configurações com um editor de texto.
- Configurações não participam de transações do banco — aceitável, pois não há operação que precise
  alterar configuração e dados atomicamente.
