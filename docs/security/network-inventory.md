# Inventário de saídas de rede

Lista de todo código do ALFRED capaz de abrir conexões de rede (Seção 9, issue #46). A fonte da
verdade é `NETWORK_FLOWS` em `src/alfred/core/privacy.py`. O teste
`tests/test_privacy.py::test_every_network_capable_module_is_in_the_inventory` varre o código e
falha quando surge uma saída nova que não está registrada aqui.

| Módulo | Fluxo | Destino | O que é enviado | Controle |
|---|---|---|---|---|
| `alfred/ai/ollama.py` | IA local (Ollama) | `127.0.0.1:11434`, este computador | pedidos, contexto e memórias; imagens quando você pede uma análise de tela | não sai do computador |
| `alfred/ai/setup.py` | instalação da IA local (`alfred --setup-ai`) | `ollama.com` (instalador); modelos pelo servidor Ollama | nada seu; download público, instalador executado só com assinatura Authenticode válida da Ollama | só na instalação ou a seu pedido |
| `alfred/diagnostics.py` | autodiagnóstico (`alfred --self-check`) | `127.0.0.1:11434`, este computador | nada; só lista os modelos do Ollama local | só quando você roda o diagnóstico |
| `alfred/ai/openai_compat.py` | IA externa | URL em `ai.external_base_url` | pedidos, contexto e memórias; imagens só com autorização a cada envio | `privacy.network_enabled` (desligado por padrão) e escolha do provedor |
| `alfred/voice/models.py` | download dos modelos de voz (Vosk, Piper) | `alphacephei.com`, `huggingface.co` | nada seu; download público com SHA-256 conferido | só quando o modelo falta |
| `alfred/voice/stt.py` | download do Whisper | `huggingface.co` | nada seu; telemetria do `huggingface_hub` desligada; depois disso, `local_files_only` | só quando o modelo falta |
| `alfred/automation/browser.py` | navegador do ALFRED (perfil dedicado) | os sites que você pedir | o mesmo que um navegador comum | só páginas pedidas; envio de formulários exige confirmação |

## O que nunca sai do computador

- **Áudio do microfone.** Wake word (Vosk) e transcrição (Whisper) rodam localmente, e o áudio
  não é gravado em disco.
- **Capturas de tela.** Ficam em memória. Só vão a um provedor externo de análise se você
  autorizar aquele envio específico.
- **Histórico, memória, gatilhos e permissões**, guardados no SQLite local.
- **Credenciais.** Ficam no Windows Credential Manager e só seguem no cabeçalho de autenticação
  para o provedor que você configurou.

## Modo offline

Sem internet, continuam funcionando: comandos de voz locais (hora, data, parar, abrir e fechar
aplicativos, controle de janelas), atalhos, gatilhos, histórico, memória, captura de tela, cursor
virtual e a IA local (com o Ollama instalado). Um pedido que depende de IA externa recebe uma
resposta honesta de indisponibilidade. Testes:
`integration/test_app_flows.py::test_essential_functions_work_without_remote_services` e
`test_privacy.py::test_nothing_leaves_the_computer_with_defaults`.

A seção **Configurações › Privacidade** mostra o estado atual de cada fluxo e diz se algo está
saindo do computador naquele momento.
