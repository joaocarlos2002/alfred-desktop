"""Instalação da IA local (#49): Ollama verificado + modelo adequado ao hardware, e ``--set``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from alfred.ai.setup import (
    AISetupError,
    LocalAISetup,
    choose_model,
)


def test_model_follows_the_hardware() -> None:
    assert choose_model(vram_mb=8192, ram_gb=16) == "qwen2.5:7b"
    assert choose_model(vram_mb=None, ram_gb=32) == "qwen2.5:7b"
    assert choose_model(vram_mb=4096, ram_gb=8) == "qwen2.5:3b"
    assert choose_model(vram_mb=None, ram_gb=8) == "qwen2.5:3b"


class Steps:
    def __init__(self) -> None:
        self.messages: list[tuple[str, float | None]] = []

    def __call__(self, message: str, fraction: float | None = None) -> None:
        self.messages.append((message, fraction))


def pull_transport(models: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.16"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in models]})
        if request.url.path == "/api/pull":
            assert json.loads(request.content)["model"] == "qwen2.5:3b"
            lines = [
                {"status": "pulling manifest"},
                {"status": "downloading", "total": 100, "completed": 40},
                {"status": "downloading", "total": 100, "completed": 100},
                {"status": "success"},
            ]
            models.append("qwen2.5:3b")
            body = "\n".join(json.dumps(line) for line in lines)
            return httpx.Response(200, content=body.encode())
        raise AssertionError(request.url.path)

    return httpx.MockTransport(handler)


def setup(
    tmp_path: Path, *, installed: bool, models: list[str], signed: bool = True, **kw: Any
) -> tuple[LocalAISetup, dict[str, Any]]:
    record: dict[str, Any] = {"downloaded": False, "installed": False}
    exe = tmp_path / "Ollama" / "ollama.exe"
    if installed:
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")

    async def download(url: str, target: Path, progress: Any) -> None:
        record["downloaded"] = url
        target.write_bytes(b"MZ")
        progress(1.0)

    def run_installer(path: Path) -> None:
        record["installed"] = True
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"")

    return (
        LocalAISetup(
            transport=pull_transport(models),
            find=lambda: exe if exe.exists() else None,
            download=download,
            verify=lambda path: signed,
            run_installer=run_installer,
            ensure_server=lambda: _true(),
            hardware=lambda: (None, 8),
            workdir=tmp_path,
            **kw,
        ),
        record,
    )


async def _true() -> bool:
    return True


async def test_installs_verified_ollama_then_pulls_the_model(tmp_path: Path) -> None:
    models: list[str] = []
    ai, record = setup(tmp_path, installed=False, models=models)
    steps = Steps()
    result = await ai.run(steps)
    assert record["downloaded"] == "https://ollama.com/download/OllamaSetup.exe"
    assert record["installed"]
    assert result.model == "qwen2.5:3b"
    assert result.installed_ollama
    fractions = [f for _, f in steps.messages if f is not None]
    assert 0.4 in fractions
    assert fractions[-1] == 1.0
    assert "qwen2.5:3b" in models


async def test_skips_what_is_already_there(tmp_path: Path) -> None:
    models = ["qwen2.5:3b"]
    ai, record = setup(tmp_path, installed=True, models=models)
    result = await ai.run(Steps())
    assert record["downloaded"] is False  # Ollama já instalado
    assert result.model == "qwen2.5:3b"
    assert not result.installed_ollama
    assert not result.pulled  # modelo já baixado


async def test_unsigned_installer_is_never_executed(tmp_path: Path) -> None:
    ai, record = setup(tmp_path, installed=False, models=[], signed=False)
    with pytest.raises(AISetupError, match="assinatura"):
        await ai.run(Steps())
    assert not record["installed"]
    assert not any(tmp_path.glob("OllamaSetup*.exe"))  # arquivo suspeito apagado


def test_cli_set_changes_a_validated_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alfred.__main__ import main as cli
    from alfred.config.store import SETTINGS_FILE, SettingsStore

    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    assert cli(["--set", "general.start_with_windows=true"]) == 0
    assert SettingsStore(tmp_path / SETTINGS_FILE).load().settings.general.start_with_windows
    assert cli(["--set", "ai.max_retries=3"]) == 0
    assert SettingsStore(tmp_path / SETTINGS_FILE).load().settings.ai.max_retries == 3
    assert cli(["--set", "ai.max_retries=99"]) == 2  # fora da faixa: recusado, nada muda
    assert cli(["--set", "nada.disso=1"]) == 2
    assert SettingsStore(tmp_path / SETTINGS_FILE).load().settings.ai.max_retries == 3


def test_installer_script_guarantees() -> None:
    iss = (Path(__file__).resolve().parents[1] / "packaging" / "installer.iss").read_text("utf-8")
    assert "PrivilegesRequired=lowest" in iss  # sem administrador
    assert "CloseApplications=yes" in iss  # atualização fecha o ALFRED em execução
    for task in ('Name: "autostart"', 'Name: "localai"', 'Name: "desktopicon"'):
        assert task in iss
    assert '--set general.start_with_windows=true"; Tasks: autostart' in iss
    assert '"--setup-ai"; Tasks: localai' in iss
    assert "uninsdeletevalue" in iss  # desinstalação remove a inicialização automática
    assert "/REMOVEDATA" in iss  # dados só são apagados com escolha explícita


class FakeSetup:
    def __init__(self, fail: bool) -> None:
        self.fail = fail

    async def run(self, progress: Any) -> Any:
        from alfred.ai.setup import SetupResult

        progress("Baixando o modelo qwen2.5:3b…", 0.5)
        if self.fail:
            raise AISetupError("sem internet")
        return SetupResult("qwen2.5:3b", installed_ollama=True, pulled=True)


@pytest.mark.parametrize("fail", [False, True])
def test_setup_window_reports_success_or_failure(fail: bool) -> None:
    from alfred.ui.ai_setup_window import AISetupWindow
    from alfred.ui.qt_runtime import ensure_qt_application, run_on_qt

    ensure_qt_application()
    codes: list[int] = []

    async def scenario() -> tuple[str, bool]:
        window = AISetupWindow(FakeSetup(fail), codes.append)  # type: ignore[arg-type]
        await window.run()
        status, retry = window.status.text(), window.retry.isVisibleTo(window)
        window.close_button.click()
        return status, retry

    status, retry = run_on_qt(scenario())
    if fail:
        assert "sem internet" in status
        assert retry
        assert codes == [1]
    else:
        assert "qwen2.5:3b" in status
        assert not retry
        assert codes == [0]
