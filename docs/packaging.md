# Empacotamento

Como o executável distribuível é gerado (issue #48).

## Gerar

```powershell
uv sync --all-extras --group build
uv run python scripts/build.py
```

O script:
1. gera o ícone;
2. roda o PyInstaller com `packaging/alfred.spec`;
3. faz o **teste de fumaça**: `alfred.exe --headless` com diretório de dados vazio precisa criar e migrar o banco e chegar a "ALFRED em execução";
4. roda `alfred.exe --self-check` **dentro do executável**;
5. gera `dist/ALFRED-<versão>-win64.zip`.

A CI faz o mesmo em todo push e PR (job `build`) e publica o zip como artefato `ALFRED-win64`.

## Instalador

```powershell
uv run python scripts/build_installer.py   # requer Inno Setup 6 (winget install JRSoftware.InnoSetup)
```

O resultado é `dist/ALFRED-Setup-<versão>.exe` ([`packaging/installer.iss`](../packaging/installer.iss)):

- Instala por usuário, **sem administrador**, em `%LOCALAPPDATA%\Programs\ALFRED`.
- Tarefas:
  - atalho na área de trabalho;
  - **iniciar com o Windows** (grava a configuração `general.start_with_windows` via `alfred --set`);
  - **IA local** (marcada por padrão): `alfred --setup-ai` abre uma janela com progresso, instala o Ollama
    (instalador oficial, executado **só com assinatura Authenticode válida da Ollama**) e baixa o
    modelo adequado ao hardware (`qwen2.5:7b` com GPU de 6 GB+ ou 16 GB+ de RAM; senão `qwen2.5:3b`).
- **Atualizar**: instalar por cima fecha o ALFRED em execução (Restart Manager) e preserva os dados.
- **Desinstalar**:
  - remove o programa e a inicialização automática;
  - pergunta se apaga os dados (`%LOCALAPPDATA%\ALFRED`). Em modo silencioso, só apaga com `/REMOVEDATA`;
  - o Ollama é um produto separado e permanece instalado.
- **Assinatura de código**: o instalador do ALFRED ainda não é assinado. Não há certificado disponível,
  então o Windows SmartScreen pode avisar na primeira execução.

A CI (job `build`) compila o instalador e roda [`scripts/test_installer.ps1`](../scripts/test_installer.ps1)
numa VM Windows limpa: instalar, conferir arquivos e configuração, `--self-check` no executável
instalado, atualizar por cima, desinstalar mantendo os dados, e reinstalar e desinstalar com `/REMOVEDATA`.

## Escolhas

- **Modo pasta (`onedir`), não `onefile`.** O `onefile` extrai ~550 MB para `%TEMP%` a cada
  execução: inicia bem mais devagar e é a configuração que mais dispara heurísticas de antivírus.
- **Sem UPX**, pelo mesmo motivo (binários compactados são suspeitos para antivírus).
- **Modelos fora do pacote.** Vosk, Piper e Whisper são baixados sob demanda (com SHA-256
  conferido) para `%LOCALAPPDATA%\ALFRED\models`. O modelo de IA fica com o Ollama.
- **Navegador:** o pacote leva só o driver do Playwright e usa o Microsoft Edge do sistema.
- **Imports sob demanda.** O `alfred.spec` inclui todos os submódulos de `alfred` e do Alembic,
  porque o código importa voz, automação e IA dentro das funções (#47).

## Medições (0.1.0, Windows 11, Python 3.12)

| Métrica | PyInstaller 6.22 (onedir) |
|---|---|
| Tempo de build | 2,8 min |
| Tamanho da pasta | 548 MB (zip: 223 MB) |
| Maiores partes | Playwright 110 MB, PySide6 74 MB, PyAV 65 MB, CTranslate2 62 MB, Vosk 52 MB, Piper 45 MB, onnxruntime 45 MB |
| Inicialização, 1ª execução | 8,9–13,4 s (o Windows Defender verifica cada DLL nova uma única vez) |
| Inicialização, execuções seguintes | 2,0–2,7 s (meta: ≤ 5 s) |
| `--self-check` | 11/11 componentes carregados (banco, Qt, PortAudio, Vosk, faster-whisper/CUDA, Piper/onnxruntime, UI Automation, Playwright, mss, Credential Manager, Ollama) |

## PyInstaller × Nuitka

| Critério | PyInstaller | Nuitka |
|---|---|---|
| Build | 3 min, sem compilador C | compila o Python para C: exige MSVC ou MinGW e leva dezenas de minutos para PySide6 + onnxruntime + CTranslate2 |
| Tamanho | 548 MB (dominado por binários nativos de terceiros) | parecido: os mesmos DLLs nativos (CTranslate2, onnxruntime, Vosk, Qt) entram igual; o ganho fica só no bytecode, uma fração pequena |
| Inicialização | 2,0 s aquecido; o tempo é import de bibliotecas nativas e banco, não interpretação | ganho esperado pequeno pelo mesmo motivo |
| Falsos positivos de antivírus | baixos em onedir sem UPX; o bootloader é muito difundido | também existem, e os binários compilados são menos comuns |
| Suporte às bibliotecas | hooks prontos (PySide6, onnxruntime, sounddevice, pywinauto, Playwright) | plugins para PySide6; CTranslate2 e Vosk exigem configuração manual |

**Decisão: PyInstaller.** O peso e a inicialização vêm das bibliotecas nativas, que são iguais nas
duas ferramentas. O PyInstaller entrega o mesmo resultado prático com um build reproduzível de
3 minutos, sem compilador. Nesta máquina não há MSVC, então os números do Nuitka não foram medidos.
Reavaliar se a distribuição exigir proteção do código-fonte ou se o tempo de inicialização virar
problema.

## Diagnóstico no computador do usuário

```powershell
& "$env:LOCALAPPDATA\Programs\ALFRED\alfred.exe" --self-check --self-check-output "$env:TEMP\alfred-check.json"
```

O relatório lista cada componente com `ok`, o detalhe e o tempo de carga. Nenhum dado sai do
computador; a única conexão é a consulta ao Ollama local.
