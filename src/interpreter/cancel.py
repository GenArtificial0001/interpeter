"""Cancelación por generación, compartida por todas las etapas."""

from __future__ import annotations

import threading


class CancelScope:
    """Contador de generaciones seguro entre hilos.

    ``cancel()`` incrementa la generación: todo mensaje que nació antes queda *obsoleto* y las etapas lo
    descartan. Así, si el usuario sigue hablando, no se reproduce una traducción ya vieja.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._generation = 0

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def cancel(self) -> int:
        with self._lock:
            self._generation += 1
            return self._generation

    def is_stale(self, generation: int) -> bool:
        with self._lock:
            return generation < self._generation
