"""Las pruebas NUNCA tocan el micrófono real de Windows: se instala un micrófono falso en todas."""

from __future__ import annotations

import pytest

from interpreter.audio import sysmic


class FakeMicBackend:
    """Un micrófono de Windows en memoria, con las mismas reglas de lectura/escritura que el real."""

    def __init__(self, muted: bool = False, volume: int = 80, controllable: bool = True) -> None:
        self.mic_id = "{fake}.{mic-1}"
        self.name = "Microphone Array on SoundWire Device (9- Cirrus Logic XU)"
        self.muted, self.volume, self.controllable = muted, volume, controllable
        self.writes: list[tuple] = []
        self.ignore_writes = False  # simula un driver que no aplica el cambio

    def external_mute(self, muted: bool = True) -> None:
        """Simula que OTRO (Windows, el auricular) cambia el silencio sin que lo pida la persona."""
        self.muted = muted

    def list_microphones(self) -> list[sysmic.Microphone]:
        if not self.controllable:
            return [sysmic.Microphone(self.mic_id, self.name, True, None, None)]
        return [sysmic.Microphone(self.mic_id, self.name, True, self.muted, self.volume)]

    def set_mute(self, mic_id: str, muted: bool) -> None:
        self.writes.append(("mute", muted))
        if not self.ignore_writes:
            self.muted = muted

    def set_volume(self, mic_id: str, scalar: float) -> None:
        self.writes.append(("volume", round(scalar * 100)))
        if not self.ignore_writes:
            self.volume = round(scalar * 100)


@pytest.fixture(autouse=True)
def mic_falso():
    backend = FakeMicBackend()
    sysmic.set_backend(backend)
    yield backend
    sysmic.set_backend(None)


@pytest.fixture(autouse=True)
def portaudio_sin_reinicios(monkeypatch):
    """Las pruebas no reinician PortAudio: ``_terminate/_initialize`` desde varios hilos (los sondeos de
    dispositivos de ventanas de prueba) provocan un acceso inválido que tumba toda la suite."""
    monkeypatch.setattr("interpreter.audio.devices.refresh_devices", lambda: None)
