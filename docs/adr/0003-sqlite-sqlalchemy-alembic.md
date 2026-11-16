# ADR-0003: Persistência local com SQLite, SQLAlchemy e Alembic

- **Status:** Aceito
- **Data:** 2026-11-16

## Contexto

ALFRED armazena localmente configurações, conversas, tarefas, gatilhos, permissões, memórias e
logs de atividade. Os dados são de um único usuário, precisam sobreviver a falhas e evoluir de
schema entre versões, sem exigir um servidor de banco.

## Decisão

- **SQLite** como banco local, em modo **WAL**, armazenado no diretório de dados do usuário.
- **SQLAlchemy 2.x** como ORM, confinado a `infrastructure`. O domínio expõe apenas interfaces de
  repositório e entidades próprias — nenhum tipo do SQLAlchemy vaza para `domain`.
- **Alembic** para migrações, executadas automaticamente na inicialização, com backup do arquivo
  do banco antes de aplicar uma migração.

## Alternativas consideradas

- **Arquivos JSON** — sem transações, consultas ou migrações confiáveis.
- **DuckDB / bancos embarcados alternativos** — otimizados para analytics, não para escrita
  transacional frequente.
- **SQLite sem ORM** — menos dependências, porém mais código manual de mapeamento e migração.

## Consequências

- Zero infraestrutura externa e funcionamento offline.
- Acesso ao banco precisa ser serializado/assíncrono para não bloquear o event loop.
- Credenciais **não** são armazenadas no SQLite: ficam no Windows Credential Manager.
