"""Windows UI Automation (pywinauto, backend UIA) — interação sem coordenadas (Diretriz 4).

Toda chamada COM roda numa única thread dedicada com COM inicializado (UIA é sensível a threads).
Elementos são localizados por ``AutomationId`` (não depende do idioma), nome ou tipo de controle.
"""

from __future__ import annotations

import contextlib
import warnings
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, TypeVar

from alfred.domain.screen import ScreenRect

T = TypeVar("T")


class ElementNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class ElementQuery:
    automation_id: str | None = None
    name: str | None = None
    control_type: str | None = None

    def criteria(self) -> dict[str, str]:
        found = {
            "auto_id": self.automation_id,
            "title": self.name,
            "control_type": self.control_type,
        }
        return {k: v for k, v in found.items() if v}

    def describe(self) -> str:
        return ", ".join(f"{k}={v!r}" for k, v in self.criteria().items()) or "(vazio)"


@dataclass(frozen=True)
class ElementInfo:
    name: str
    control_type: str
    automation_id: str
    rect: ScreenRect
    enabled: bool


def _import_pywinauto() -> Any:
    # pywinauto 0.6.9 tem escapes inválidos que geram SyntaxWarning na primeira compilação.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        import pywinauto

    return pywinauto


def _init_com() -> None:
    with contextlib.suppress(Exception):
        import pythoncom

        pythoncom.CoInitialize()


def _info(wrapper: Any) -> ElementInfo:
    element = wrapper.element_info
    r = element.rectangle
    return ElementInfo(
        name=str(element.name or ""),
        control_type=str(element.control_type or ""),
        automation_id=str(element.automation_id or ""),
        rect=ScreenRect(
            x=int(r.left),
            y=int(r.top),
            width=max(0, int(r.width())),
            height=max(0, int(r.height())),
        ),
        enabled=bool(element.enabled),
    )


class UiaClient:
    def __init__(self, *, timeout: float = 5.0) -> None:
        self.timeout = timeout
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="alfred-uia", initializer=_init_com
        )

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def call(self, fn: Callable[..., T], *args: Any) -> T:
        """Executa ``fn`` na thread UIA (bloqueante; use via ``asyncio.to_thread``)."""
        return self._executor.submit(fn, *args).result(timeout=self.timeout + 10)

    # ------------------------------------------------------------------ na thread UIA
    def _window(self, hwnd: int) -> Any:
        pywinauto = _import_pywinauto()
        return pywinauto.Desktop(backend="uia").window(handle=hwnd)

    def _element(self, hwnd: int, query: ElementQuery) -> Any:
        spec = self._window(hwnd).child_window(**query.criteria())
        try:
            return spec.wait("exists enabled", timeout=self.timeout)
        except Exception as exc:
            raise ElementNotFoundError(
                f"Elemento não encontrado ({query.describe()}) em {self.timeout:.0f} s"
            ) from exc

    def _find(self, hwnd: int, query: ElementQuery) -> ElementInfo:
        return _info(self._element(hwnd, query))

    def _invoke(self, hwnd: int, query: ElementQuery) -> ElementInfo:
        element = self._element(hwnd, query)
        try:
            element.invoke()
        except Exception:
            element.click_input()  # sem padrão Invoke: clique no centro do elemento
        return _info(element)

    def _set_text(self, hwnd: int, query: ElementQuery, text: str) -> ElementInfo:
        element = self._element(hwnd, query)
        element.set_edit_text(text)
        return _info(element)

    def _select(self, hwnd: int, query: ElementQuery, item: str) -> ElementInfo:
        element = self._element(hwnd, query)
        element.select(item)
        return _info(element)

    def _expand(self, hwnd: int, query: ElementQuery) -> ElementInfo:
        element = self._element(hwnd, query)
        element.expand()
        return _info(element)

    def _read(self, hwnd: int, query: ElementQuery) -> str:
        element = self._element(hwnd, query)
        with contextlib.suppress(Exception):
            value = element.get_value()
            if value:
                return str(value)
        return str(element.window_text())

    def _tree(self, hwnd: int, limit: int) -> list[ElementInfo]:
        found: list[ElementInfo] = []
        for wrapper in self._window(hwnd).descendants():
            info = _info(wrapper)
            if info.name or info.automation_id:
                found.append(info)
            if len(found) >= limit:
                break
        return found

    # ------------------------------------------------------------------ API pública
    def find(self, hwnd: int, query: ElementQuery) -> ElementInfo:
        return self.call(self._find, hwnd, query)

    def invoke(self, hwnd: int, query: ElementQuery) -> ElementInfo:
        return self.call(self._invoke, hwnd, query)

    def set_text(self, hwnd: int, query: ElementQuery, text: str) -> ElementInfo:
        return self.call(self._set_text, hwnd, query, text)

    def select(self, hwnd: int, query: ElementQuery, item: str) -> ElementInfo:
        return self.call(self._select, hwnd, query, item)

    def expand(self, hwnd: int, query: ElementQuery) -> ElementInfo:
        return self.call(self._expand, hwnd, query)

    def read_text(self, hwnd: int, query: ElementQuery) -> str:
        return self.call(self._read, hwnd, query)

    def tree(self, hwnd: int, limit: int = 200) -> list[ElementInfo]:
        """Elementos com nome ou AutomationId — resumo para o contexto da IA."""
        return self.call(self._tree, hwnd, limit)
