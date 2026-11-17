# Testa o instalador do ALFRED de ponta a ponta numa máquina limpa (CI) — issue #49.
# Uso: pwsh scripts/test_installer.ps1 -Installer dist/ALFRED-Setup-<versão>.exe
# Instala (com "iniciar com o Windows"), confere arquivos e configuração, roda o --self-check no
# executável instalado, atualiza por cima, desinstala mantendo os dados e, por fim, reinstala e
# desinstala apagando os dados. A IA local (download de GBs) não é instalada aqui.
param([Parameter(Mandatory = $true)][string]$Installer)
$ErrorActionPreference = "Stop"

$appDir = Join-Path $env:LOCALAPPDATA "Programs\ALFRED"
$dataDir = Join-Path $env:LOCALAPPDATA "ALFRED"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"

function Assert($condition, $message) {
    if (-not $condition) { throw "FALHOU: $message" }
    Write-Host "ok: $message"
}

function Install([string]$tasks) {
    $p = Start-Process -FilePath $Installer -Wait -PassThru -ArgumentList @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TASKS=`"$tasks`"", "/LOG=`"$env:TEMP\alfred-setup.log`""
    )
    Assert ($p.ExitCode -eq 0) "instalador terminou com código 0 (tarefas: $tasks)"
}

function Uninstall([string[]]$extra) {
    $uninstaller = Join-Path $appDir "unins000.exe"
    $p = Start-Process -FilePath $uninstaller -Wait -PassThru -ArgumentList (@("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") + $extra)
    Assert ($p.ExitCode -eq 0) "desinstalador terminou com código 0"
    Start-Sleep -Seconds 3  # o desinstalador termina de apagar a pasta em segundo plano
}

# 1. Instalação limpa
Install "autostart"
Assert (Test-Path (Join-Path $appDir "alfred.exe")) "alfred.exe instalado em $appDir"
Assert (Test-Path (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\ALFRED.lnk")) "atalho no Menu Iniciar"
$settings = Get-Content (Join-Path $dataDir "settings.json") -Raw | ConvertFrom-Json
Assert ($settings.general.start_with_windows -eq $true) "tarefa 'iniciar com o Windows' gravada na configuração"

# 2. O executável instalado funciona
$report = Join-Path $env:TEMP "alfred-check.json"
$p = Start-Process -FilePath (Join-Path $appDir "alfred.exe") -Wait -PassThru -ArgumentList @("--self-check", "--self-check-output", "`"$report`"")
Assert ($p.ExitCode -eq 0) "--self-check do executável instalado passou"

# 3. Atualização por cima
Install ""
$settings = Get-Content (Join-Path $dataDir "settings.json") -Raw | ConvertFrom-Json
Assert ($settings.general.start_with_windows -eq $false) "reinstalar sem a tarefa desliga a inicialização automática"

# 4. Desinstalar mantendo os dados
New-ItemProperty -Path $runKey -Name "ALFRED" -Value "`"$appDir\alfred.exe`"" -Force | Out-Null
Uninstall @()
Assert (-not (Test-Path (Join-Path $appDir "alfred.exe"))) "programa removido"
Assert (Test-Path $dataDir) "dados do usuário mantidos por padrão"
Assert ($null -eq (Get-ItemProperty -Path $runKey -Name "ALFRED" -ErrorAction SilentlyContinue)) "inicialização automática removida do registro"

# 5. Reinstalar e desinstalar apagando os dados
Install ""
Uninstall @("/REMOVEDATA")
Assert (-not (Test-Path $dataDir)) "dados apagados com /REMOVEDATA"
Write-Host "Instalador: todos os testes passaram."
