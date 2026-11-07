"""Descubrimiento y elección de dispositivos de audio (WASAPI en Windows).

La app elige *explícitamente* el dispositivo de entrada y el de salida; no depende del predeterminado de
Windows. Se prefiere la API WASAPI: menor latencia y nombres fiables.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AudioDevice:
    index: int
    name: str
    hostapi: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float

    @property
    def label(self) -> str:
        return f"{self.name} ({self.hostapi})"


def list_devices(kind: str = "all", prefer_hostapi: str | None = "Windows WASAPI") -> list[AudioDevice]:
    """Lista dispositivos. ``kind``: ``input``, ``output`` o ``all``. Filtra por API si existe."""
    import sounddevice as sd

    apis = sd.query_hostapis()
    devices: list[AudioDevice] = []
    for i, d in enumerate(sd.query_devices()):
        dev = AudioDevice(
            index=i,
            name=str(d["name"]),
            hostapi=str(apis[d["hostapi"]]["name"]),
            max_input_channels=int(d["max_input_channels"]),
            max_output_channels=int(d["max_output_channels"]),
            default_samplerate=float(d["default_samplerate"]),
        )
        if kind == "input" and dev.max_input_channels < 1:
            continue
        if kind == "output" and dev.max_output_channels < 1:
            continue
        devices.append(dev)
    if prefer_hostapi and any(d.hostapi == prefer_hostapi for d in devices):
        devices = [d for d in devices if d.hostapi == prefer_hostapi]
    return devices


def find_device(devices: list[AudioDevice], fragment: str) -> AudioDevice | None:
    """Primer dispositivo cuyo nombre contenga ``fragment`` (sin distinguir mayúsculas)."""
    frag = fragment.lower()
    return next((d for d in devices if frag in d.name.lower()), None)


def same_physical_device(a: AudioDevice, b: AudioDevice) -> bool:
    """Entrada y salida del mismo dispositivo provocarían realimentación: la UI debe avisar."""
    return a.index == b.index or a.name == b.name
