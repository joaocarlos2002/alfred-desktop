import importlib

import pytest

import alfred
from alfred.__main__ import main


@pytest.mark.parametrize("module", alfred.MODULES)
def test_module_imports(module: str) -> None:
    imported = importlib.import_module(f"alfred.{module}")
    assert imported.__doc__, f"alfred.{module} deve documentar sua responsabilidade"


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert alfred.__version__ in capsys.readouterr().out


def test_main_without_args_returns_zero() -> None:
    assert main([]) == 0
