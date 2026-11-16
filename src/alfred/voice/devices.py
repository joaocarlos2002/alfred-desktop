"""Enumeração e seleção de dispositivos de áudio (por nome, não por índice)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

Kind = Literal["input", "output"]


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    channels: int
    is_default: bool


class DeviceBackend(Protocol):
    def query_devices(self) -> Any: ...

    @property
    def default_device(self) -> tuple[int, int]: ...


class SoundDeviceBackend:
    """Adaptador fino sobre ``sounddevice`` (PortAudio)."""

    def query_devices(self) -> Any:
        import sounddevice as sd

        return sd.query_devices()

    @property
    def default_device(self) -> tuple[int, int]:
        import sounddevice as sd

        device = sd.default.device
        return int(device[0]), int(device[1])

    def refresh(self) -> None:
        """Reinicia o PortAudio para enxergar dispositivos conectados depois do início."""
        import sounddevice as sd

        sd._terminate()
        sd._initialize()


def list_devices(backend: DeviceBackend, kind: Kind) -> list[AudioDevice]:
    """Dispositivos da API padrão do sistema (MME), que reamostra para 16 kHz sozinha."""
    devices = list(backend.query_devices())
    default_input, default_output = backend.default_device
    default_index = default_input if kind == "input" else default_output
    if default_index < 0 or default_index >= len(devices):
        return []
    hostapi = devices[default_index]["hostapi"]
    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"
    return [
        AudioDevice(
            index=index,
            name=str(info["name"]),
            channels=int(info[channel_key]),
            is_default=index == default_index,
        )
        for index, info in enumerate(devices)
        if info["hostapi"] == hostapi and info[channel_key] > 0
    ]


def resolve_device(backend: DeviceBackend, kind: Kind, name: str | None) -> AudioDevice | None:
    """Encontra o dispositivo pelo nome; ``None`` ou nome ausente → dispositivo padrão."""
    devices = list_devices(backend, kind)
    if name:
        wanted = name.casefold()
        for device in devices:
            if device.name.casefold() == wanted or wanted in device.name.casefold():
                return device
    return next((d for d in devices if d.is_default), devices[0] if devices else None)
