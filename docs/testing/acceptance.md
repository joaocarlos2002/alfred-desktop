# Critérios de aceitação (Seção 10) e como são verificados

Cada critério da especificação é coberto por testes automatizados e, quando depende de hardware
ou de uma sessão interativa do Windows (microfone, alto-falante, atalhos globais vistos por
outros programas, reinício do sistema), também por um item do checklist manual abaixo.

## Como rodar

| Suíte | Comando | Onde roda |
|---|---|---|
| Unitários + integração | `uv run pytest -m "not windows" --cov` | CI (windows-latest) e local |
| Desktop real (UIA, áudio, SendInput, Ollama) | `uv run pytest -m windows` | Só local, em sessão interativa |

- A CI falha se a cobertura de linhas e ramos ficar abaixo de **85%** (`[tool.coverage.report] fail_under`).
- Testes marcados com `windows` abrem janelas de teste (`tests/fixtures/uia_app.py`, Calculadora) e usam
  modelos locais de voz. São pulados com o motivo quando o recurso não existe (ex.: Ollama não instalado).
- Os E2E de UI Automation usam só os padrões UIA (sem teclas simuladas nem cliques por coordenada)
  e uma aplicação de teste sem dados do usuário.

## Matriz

| # | Critério | Testes automatizados | Manual |
|---|---|---|---|
| 1 | Inicia e opera em segundo plano | `test_app.py`, `test_lifecycle.py`, `test_single_instance.py`, `test_tray.py` | M1 |
| 2 | Inicialização automática configurável | `test_autostart.py` | M2 |
| 3 | Widget apresenta estados reais | `test_assistant_state.py`, `test_widget.py` | M3 |
| 4 | Atalho global em diferentes aplicações | `test_hotkeys.py` (registro real via Win32 em `-m windows`) | M4 |
| 5 | Ativação por voz em PT-BR | `test_wakeword.py`, `test_voice_pipeline.py::test_real_end_to_end_alfred_que_horas_sao` | M5 |
| 6 | Responde por voz | `test_tts.py`, `integration/test_app_flows.py::test_voice_request_runs_through_orchestrator_with_real_history_and_memory` | M5 |
| 7 | Aplicações abertas e controladas | `test_apps.py`, `test_window_service.py`, `test_uia.py::test_real_calculator_without_coordinates` | — |
| 8 | Tarefas com múltiplas aplicações | `integration/test_e2e_desktop.py`, `test_scheduler.py` | — |
| 9 | Cursor virtual sem controlar o físico | `test_virtual_cursor.py`, `test_overlay.py` | M6 |
| 10 | Captura de tela só quando solicitada/autorizada | `test_screen_capture.py`, `test_vision.py` | — |
| 11 | Provedores locais e externos configuráveis | `test_ai_providers.py`, `test_ollama.py`, `test_external_ai.py` | M7 |
| 12 | Gatilhos criados e gerenciados | `test_triggers.py`, `test_trigger_guard.py`, `test_settings_window.py`, `integration/test_app_flows.py::test_trigger_created_by_user_runs_through_orchestrator` | — |
| 13 | Histórico e memória consultáveis e excluíveis | `test_activity_panel.py`, `test_memory.py`, `test_settings_window.py::test_memory_and_triggers_sections_are_wired_in_the_real_app` | — |
| 14 | Operações sensíveis exigem confirmação | `test_permissions.py`, `test_confirmation.py`, `integration/test_app_flows.py::test_sensitive_operation_waits_for_confirmation` | — |
| 15 | Atividades apresentam resultados reais | `test_action_log.py`, `test_orchestrator.py`, `integration/test_app_flows.py` (resultado da ferramenta no histórico) | — |
| 16 | Interrupção de emergência | `test_emergency.py`, `integration/test_app_flows.py::test_emergency_stop_cancels_running_task` | M4 |
| 17 | Funções essenciais sem serviços remotos | `integration/test_app_flows.py::test_essential_functions_work_without_remote_services`, `test_external_ai.py::test_network_switch_off_makes_no_request_at_all` | M7 |

## Checklist manual (antes de cada versão)

Execute numa sessão normal do Windows 11, com o ALFRED instalado. Marque o resultado e anote a versão.

- **M1 — Segundo plano.** Inicie o ALFRED. O ícone aparece na bandeja, sem janela principal. Feche
  todas as janelas e confira que o ícone continua lá e que o atalho de ativação responde.
- **M2 — Inicialização automática.** Ative *Configurações › Geral › Iniciar com o Windows*, reinicie o
  computador e confira que o ícone aparece sem abrir nada manualmente. Desative e repita: não deve abrir.
- **M3 — Widget.** Com o widget visível, fale "Alfred" e observe "ouvindo"; faça um pedido e observe
  "processando"/"executando"; ao final, volte para "pronto". Arraste o widget e reinicie: a posição é mantida.
- **M4 — Atalhos em outros aplicativos.** Com o foco no Bloco de Notas, depois no Edge e depois no
  Explorador de Arquivos, pressione o atalho de ativação (padrão `ctrl+alt+space`): o ALFRED ativa nos três.
  Durante uma tarefa longa, pressione o atalho de emergência: a tarefa para e o aviso aparece na bandeja.
- **M5 — Voz.** Com o microfone padrão, diga "Alfred, que horas são?" e ouça a resposta falada.
  Interrompa uma resposta longa falando "Alfred" (barge-in).
- **M6 — Cursor virtual.** Peça uma ação de interface (ex.: clicar num botão da Calculadora). O cursor
  virtual aparece sobre o elemento e o cursor físico do mouse **não** se move.
- **M7 — Sem rede.** Desconecte a rede. Comandos locais (hora, data, abrir aplicativo) continuam
  funcionando; um pedido que exige IA externa recebe uma resposta honesta de indisponibilidade.
  Com o Ollama instalado e um modelo baixado, os pedidos livres funcionam offline.
