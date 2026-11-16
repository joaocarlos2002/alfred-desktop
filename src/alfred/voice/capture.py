"""Captura contínua do microfone em quadros de 80 ms, distribuída a assinantes.

O callback do PortAudio só copia o quadro para uma fila; uma thread própria entrega os quadros
aos assinantes (detector de palavra de ativação, gravador de comando). Nada é gravado em disco.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

from alfred.voice.devices import DeviceBackend, resolve_device
from alfred.voice.vad import FRAME_SAMPLES, SAMPLE_RATE, Frame

logger = logging.getLogger(__name__)

type FrameListener = Callable[[Frame], object]

RESTART_DELAYS = (0.5, 1.0, 2.0, 5.0)


class InputStreamLike(Protocol):
    active: bool

    def start(self) -> None: ...

    def close(self) -> None: ...


type StreamFactory = Callable[..., InputStreamLike]


def sounddevice_stream(**kwargs: Any) -> InputStreamLike:
    """``RawInputStream``: entrega o buffer bruto do PortAudio.

    O ``InputStream`` do sounddevice altera ``ndarray.shape`` a cada quadro, o que o NumPy 2.5
    marca como obsoleto; o aviso dispara dentro do callback CFFI. O buffer bruto evita esse
    caminho e uma cópia intermediária.
    """
    import sounddevice as sd

    stream: InputStreamLike = sd.RawInputStream(**kwargs)
    return stream


class AudioCapture:
    def __init__(
        self,
        backend: DeviceBackend,
        *,
        stream_factory: StreamFactory = sounddevice_stream,
        on_device_change: Callable[[str | None, bool], None] | None = None,
    ) -> None:
        self._backend = backend
        self._factory = stream_factory
        self._on_device_change = on_device_change
        self._listeners: list[FrameListener] = []
        self._lock = threading.Lock()
        self._frames: queue.SimpleQueue[Frame | None] = queue.SimpleQueue()
        self._stream: InputStreamLike | None = None
        self._dispatcher: threading.Thread | None = None
        self._watchdog: threading.Thread | None = None
        self._stop = threading.Event()
        self._lost = threading.Event()
        self.device_name: str | None = None
        self.requested_device: str | None = None
        self.frames_captured = 0

    # ------------------------------------------------------------------ assinantes
    def subscribe(self, listener: FrameListener) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    # ------------------------------------------------------------------ ciclo de vida
    @property
    def running(self) -> bool:
        return self._stream is not None and not self._lost.is_set()

    def start(self, device: str | None = None) -> None:
        self.requested_device = device
        self._stop.clear()
        self._dispatcher = threading.Thread(target=self._dispatch, name="alfred-audio", daemon=True)
        self._dispatcher.start()
        self._open()
        self._watchdog = threading.Thread(target=self._watch, name="alfred-audio-wd", daemon=True)
        self._watchdog.start()

    def stop(self) -> None:
        self._stop.set()
        self._lost.set()  # acorda o watchdog
        self._close_stream()
        self._frames.put(None)
        for thread in (self._dispatcher, self._watchdog):
            if thread is not None:
                thread.join(timeout=3)
        self._dispatcher = self._watchdog = None
        self._lost.clear()

    def switch_device(self, device: str | None) -> None:
        self.requested_device = device
        self._close_stream()
        self._open()

    # ------------------------------------------------------------------ internos
    def _open(self) -> None:
        selected = resolve_device(self._backend, "input", self.requested_device)
        if selected is None:
            raise RuntimeError("Nenhum microfone disponível")
        stream = self._factory(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            device=selected.index,
            callback=self._callback,
            finished_callback=self._on_finished,
        )
        stream.start()
        self._stream = stream
        self._lost.clear()
        if (
            self.requested_device
            and self.requested_device.casefold() not in selected.name.casefold()
        ):
            logger.warning(
                "Microfone '%s' indisponível; usando '%s'", self.requested_device, selected.name
            )
        self.device_name = selected.name
        logger.info("Captura de áudio em '%s' (16 kHz, mono)", selected.name)
        if self._on_device_change is not None:
            self._on_device_change(selected.name, True)

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                logger.debug("Falha ao fechar o stream de áudio", exc_info=True)

    def _callback(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        if status:
            logger.debug("Status do áudio: %s", status)
        self._frames.put(np.frombuffer(indata, dtype=np.int16).copy())

    def _on_finished(self) -> None:
        if not self._stop.is_set():
            logger.warning("Stream de áudio encerrado inesperadamente (dispositivo removido?)")
            self._lost.set()

    def _dispatch(self) -> None:
        while True:
            frame = self._frames.get()
            if frame is None:
                return
            self.frames_captured += 1
            with self._lock:
                listeners = list(self._listeners)
            for listener in listeners:
                try:
                    listener(frame)
                except Exception:
                    logger.exception("Assinante de áudio falhou")

    def _watch(self) -> None:
        """Recria o stream quando ele cai, com espera crescente."""
        attempt = 0
        while not self._stop.is_set():
            self._lost.wait()
            if self._stop.is_set():
                return
            if self._on_device_change is not None and attempt == 0:
                self._on_device_change(self.device_name, False)
            time.sleep(RESTART_DELAYS[min(attempt, len(RESTART_DELAYS) - 1)])
            if self._stop.is_set():
                return
            self._close_stream()
            refresh = getattr(self._backend, "refresh", None)
            try:
                if callable(refresh):
                    refresh()
                self._open()
                attempt = 0
            except Exception as exc:
                attempt += 1
                logger.warning("Microfone indisponível (%s); nova tentativa em breve", exc)
