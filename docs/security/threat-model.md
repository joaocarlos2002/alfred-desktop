# Threat model do ALFRED

Revisão de segurança da issue #45, com base na Seção 6 da especificação. Atualize este documento
sempre que surgir uma ferramenta, um provedor ou uma fonte de dados nova.

## Escopo e premissas

O ALFRED é um assistente **local** para um único usuário do Windows 11. Ele ouve o microfone
(depois da palavra de ativação), fala, controla aplicações (UI Automation, teclado, navegador
dedicado), captura a tela sob demanda e consulta um modelo de IA (local por padrão).

- O usuário logado e a conta do Windows são confiáveis. Um atacante com acesso à conta já tem
  tudo o que o ALFRED tem.
- **O modelo de IA não é confiável.** Ele pode errar ou ser manipulado por conteúdo de terceiros.
  Toda a defesa está no orquestrador, nas permissões e nas confirmações, nunca no "bom
  comportamento" do modelo (ADR-0002).
- Conteúdo de terceiros (páginas, textos de janelas, a tela) é **dado não confiável**.

## Ativos

| Ativo | Onde fica |
|---|---|
| Controle do computador (apps, teclado, mouse, navegador) | ferramentas de `automation/` |
| Chaves de provedores externos | Windows Credential Manager (`keyring`) |
| Áudio do microfone e imagens da tela | só em memória, processados localmente |
| Histórico, memória, gatilhos, permissões | SQLite em `%LOCALAPPDATA%\ALFRED` |
| Integridade dos modelos de voz e das dependências | `data/models`, `uv.lock` |

## Fronteiras de confiança

```
 usuário ──voz/atalho/UI──▶ ALFRED (orquestrador, permissões, confirmações) ──▶ ferramentas ──▶ Windows/apps
                               ▲                    │
          conteúdo externo ────┘ (páginas, janelas, tela)   └──▶ modelo de IA (local; externo só com rede ligada)
```

## Ameaças, mitigações e testes

| ID | Ameaça | Mitigações | Testes |
|---|---|---|---|
| T1 | **Prompt injection** via página, janela ou tela levando a ações não pedidas (ex.: abrir o PowerShell e digitar um comando) | (1) Ferramentas que leem conteúdo externo são marcadas como `untrusted_output`, e o resultado vai ao modelo dentro de `conteudo_externo` com um aviso; o prompt de sistema diz que esse conteúdo é dado, nunca instrução. (2) **Contaminação por tarefa:** depois que a tarefa lê conteúdo externo, *toda* ação que não seja leitura exige confirmação, mesmo com a política "permitir". (3) A confirmação mostra os argumentos reais e o motivo. | `test_prompt_injection.py`, `integration/test_e2e_desktop.py` |
| T2 | **Escalonamento de ferramentas**: chamar ferramentas fora da allowlist ou com argumentos perigosos | Allowlist e validação Pydantic no `ToolRegistry`; o modelo não executa nada (ADR-0002). Categorias sensíveis (`ALWAYS_CONFIRM`) sempre pedem confirmação. `needs_confirmation` por chamada: interpretadores e ferramentas administrativas (`cmd`, PowerShell, Terminal, `regedit`, `mmc`, WSL...) e URLs `file://`. "Negar" nunca é sobreposto. | `test_tools.py`, `test_permissions.py`, `test_prompt_injection.py` |
| T3 | **Vazamento de credenciais** | Chaves só no Credential Manager, nunca em arquivo, banco ou log. O redator de logs registra cada segredo lido. Parâmetros sensíveis (`password`, `token`, `Bearer ...`) são mascarados no histórico. O cabeçalho `Authorization` só vai para a URL configurada do provedor. | `test_external_ai.py`, `test_logging.py`, `test_activity_panel.py::test_mask_sensitive_hides_secret_fields_and_values` |
| T4 | **Envio de áudio ou tela para fora** | STT e wake word locais (Vosk e Whisper com `local_files_only`). Captura só sob demanda, com política negar/perguntar/permitir e indicação visual. Imagem para IA externa exige autorização a cada vez. Provedores externos bloqueados pelo interruptor `privacy.network_enabled` (desligado por padrão). | `test_screen_capture.py`, `test_vision.py`, `test_external_ai.py::test_network_switch_off_makes_no_request_at_all` |
| T5 | **Loops e abuso de gatilhos** | Limite de frequência, detecção de ciclos por causalidade, profundidade máxima e circuit breaker; bloqueios registrados e notificados. Gatilhos passam pelo orquestrador e pelas permissões. | `test_trigger_guard.py`, `test_triggers.py` |
| T6 | **Disputa com o usuário pelo input** ou input "preso" | `InputLock` global; pausa enquanto o usuário digita; a interrupção de emergência cancela tudo e solta teclas e botões. | `test_input.py`, `test_emergency.py`, `test_scheduler.py` |
| T7 | **Confirmação falsificada** | As confirmações são da interface do ALFRED, e o modelo não tem ferramenta para respondê-las. Prazo esgotado conta como recusa. | `test_confirmation.py`, `integration/test_app_flows.py::test_sensitive_operation_waits_for_confirmation` |
| T8 | **Cadeia de suprimentos** (dependências e modelos) | `uv.lock` fixa as versões. `pip-audit` roda na CI. Os modelos de voz têm **SHA-256 fixado** e são descartados se o hash não bater; a extração de zip é protegida contra *zip slip*. O Whisper vem do Hugging Face Hub, que verifica os arquivos. | job `security` da CI, `test_tts.py::test_downloaded_file_with_wrong_hash_is_discarded`, `test_wakeword.py` |
| T9 | **Navegador automatizado** usado contra o usuário | Perfil dedicado, separado do navegador pessoal. Envio de formulários exige confirmação. Depois de ler uma página, navegar de novo exige confirmação (T1). | `test_browser.py` |
| T10 | **Segredos no repositório** | `gitleaks` na CI (`.gitleaks.toml` libera só os valores fictícios dos testes). | job `security` da CI |
| T11 | **Comando de voz de terceiros** (alguém na sala, áudio de um vídeo) | Ações sensíveis exigem confirmação na tela. A interrupção de emergência é um atalho físico. | `test_permissions.py` |
| T12 | **Dados locais em repouso** | Ficam no perfil do usuário (ACL do Windows). "Esquecer tudo" apaga memória e conversas. Nada de segredos no banco. | `test_memory.py` |

## Achados desta revisão

| # | Severidade | Achado | Situação |
|---|---|---|---|
| F1 | **Crítica** | Conteúdo de página ou janela podia induzir o modelo a abrir um terminal e digitar comandos **sem nenhuma confirmação** (ferramentas de ação são "permitir" por padrão). | **Corrigido**: contaminação por tarefa + delimitação de conteúdo externo (T1). |
| F2 | Alta | `abrir_aplicativo` abria interpretadores de comando e ferramentas administrativas sem confirmação; `abrir_pagina` aceitava `file://` sem confirmação. | **Corrigido**: `needs_confirmation` (T2). |
| F3 | Média | Downloads dos modelos de voz sem verificação de hash. | **Corrigido**: SHA-256 fixado (T8). |
| F4 | Média | Sem auditoria de dependências nem varredura de segredos na CI. | **Corrigido**: job `security`; o `pip-audit` não encontrou vulnerabilidades conhecidas. |
| F5 | Baixa | O banco local não é cifrado em repouso. | **Aceito**: a premissa é a conta do Windows; não há segredos no banco (T3/T12). |
| F6 | Baixa | O usuário pode aprovar uma confirmação maliciosa por engano. | **Mitigado**: a confirmação mostra o motivo e os argumentos reais; não dá para eliminar sem tirar o controle do usuário. |

Nenhum achado crítico ou alto está em aberto.

## Como revisar uma ferramenta nova

1. Ela lê conteúdo de terceiros? Marque `untrusted_output=True`.
2. Algum argumento a torna perigosa (comando, caminho, destinatário, valor)? Implemente `needs_confirmation`.
3. A categoria é sensível (arquivos, mensagens, compras, configurações, administração, ações externas)? Use uma categoria de `ALWAYS_CONFIRM`.
4. Ela pode repetir efeitos? Só marque `idempotent=True` se repetir for inofensivo.
5. Escreva um teste em `test_prompt_injection.py` com o modelo tentando abusar dela.
