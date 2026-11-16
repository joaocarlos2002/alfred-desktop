from __future__ import annotations

import asyncio
import threading

from alfred.app import EXIT_OK, EXIT_STARTUP_FAILED, create_application
from alfred.domain.module import Module


class BackgroundWorker(Module):
    """Módulo que mantém uma tarefa em background, como os módulos reais farão."""

    name = "worker"

    def __init__(self) -> None:
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.task = asyncio.create_task(asyncio.sleep(3600))

    async def stop(self) -> None:
        assert self.task is not None
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


class Broken(Module):
    name = "broken"

    async def start(self) -> None:
        raise RuntimeError("boom")

    async def stop(self) -> None:  # pragma: no cover - nunca iniciado
        pass


async def test_run_and_shutdown_leaves_no_pending_tasks() -> None:
    worker = BackgroundWorker()
    app = create_application([worker])

    asyncio.get_running_loop().call_later(0.05, app.request_shutdown)
    code = await app.run(install_signal_handlers=False)

    assert code == EXIT_OK
    assert worker.task is not None
    assert worker.task.done()
    assert asyncio.all_tasks() == {asyncio.current_task()}


async def test_shutdown_can_be_requested_from_another_thread() -> None:
    app = create_application()
    threading.Timer(0.05, app.request_shutdown).start()

    assert await asyncio.wait_for(app.run(install_signal_handlers=False), 2) == EXIT_OK


async def test_required_startup_failure_returns_error_code() -> None:
    app = create_application([Broken()])

    assert await app.run(install_signal_handlers=False) == EXIT_STARTUP_FAILED
