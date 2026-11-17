"""Autodiagnóstico e empacotamento (issue #48)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from alfred import diagnostics
from alfred.__main__ import main as cli_main

ROOT = Path(__file__).resolve().parents[1]


def test_required_failure_fails_and_optional_failure_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> str:
        raise OSError("libvosk.dll não encontrada")

    monkeypatch.setattr(
        diagnostics,
        "CHECKS",
        (("ok", lambda: "carregado", True), ("ia", boom, False)),
    )
    data = diagnostics.report(diagnostics.run_checks())
    assert data["ok"] is True  # só o opcional falhou
    assert data["checks"][1]["detail"] == "OSError: libvosk.dll não encontrada"

    monkeypatch.setattr(diagnostics, "CHECKS", (("voz", boom, True),))
    assert diagnostics.report(diagnostics.run_checks())["ok"] is False


def test_cli_self_check_writes_report_and_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics, "CHECKS", (("banco", lambda: "ok", True),))
    out = tmp_path / "check.json"
    assert cli_main(["--self-check", "--self-check-output", str(out)]) == 0
    data = json.loads(out.read_text("utf-8"))
    assert data["checks"] == [
        {"name": "banco", "ok": True, "detail": "ok", "required": True, "seconds": 0.0}
    ]
    assert data["frozen"] is False


@pytest.mark.skipif(sys.platform != "win32", reason="componentes nativos do Windows")
def test_real_components_load_from_source() -> None:
    """Os componentes obrigatórios carregam de verdade (o Ollama é opcional)."""
    data = diagnostics.report(diagnostics.run_checks())
    failed = [c for c in data["checks"] if c["required"] and not c["ok"]]
    assert failed == []


def test_spec_bundles_runtime_files_and_keeps_models_out() -> None:
    spec = (ROOT / "packaging" / "alfred.spec").read_text("utf-8")
    for needed in ("migrations", "assets", "faster_whisper", "piper", "playwright", "vosk"):
        assert needed in spec
    assert "upx=False" in spec  # menos falsos positivos de antivírus
    assert "models" not in spec.split("datas = [", 1)[1].split("]", 1)[0]  # modelos fora do pacote
    for path in (
        ROOT / "src" / "alfred" / "infrastructure" / "db" / "migrations" / "env.py",
        ROOT / "src" / "alfred" / "ui" / "assets" / "butler_avatar.png",
    ):
        assert path.exists()
