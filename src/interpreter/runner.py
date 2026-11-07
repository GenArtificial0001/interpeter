"""Ejecuta una etapa en su propio hilo, conectada por colas.

Patrón: ``queue_in -> process() -> queue_out``. Cierra con ``PIPELINE_END`` sin bloquearse, descarta los
mensajes obsoletos (cancelación por generación) y reporta los errores sin tumbar el resto del pipeline.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any, Protocol

from .cancel import CancelScope
from .messages import PIPELINE_END

log = logging.getLogger(__name__)

ErrorCallback = Callable[[str, BaseException], None]


class Stage(Protocol):
    """Contrato mínimo de una etapa: recibe un mensaje y produce cero o más."""

    name: str

    def process(self, item: Any) -> Iterator[Any]: ...


class StageRunner:
    def __init__(
        self,
        stage: Stage,
        queue_in: queue.Queue,
        queue_out: queue.Queue,
        cancel: CancelScope,
        on_error: ErrorCallback | None = None,
    ) -> None:
        self.stage = stage
        self.queue_in = queue_in
        self.queue_out = queue_out
        self.cancel = cancel
        self.on_error = on_error
        self.stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"stage-{stage.name}", daemon=True)
        self.dropped_stale = 0

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self.stop_event.set()
        # Desbloquea el get() si estaba esperando; si la cola está llena no hace falta: el bucle revisa
        # stop_event cada 0,1 s.
        with contextlib.suppress(queue.Full):
            self.queue_in.put_nowait(PIPELINE_END)
        self._thread.join(timeout)

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        try:
            while not self.stop_event.is_set():
                try:
                    item = self.queue_in.get(timeout=0.1)
                except queue.Empty:
                    continue
                if item is PIPELINE_END:
                    break
                generation = getattr(item, "generation", None)
                if generation is not None and self.cancel.is_stale(generation):
                    self.dropped_stale += 1
                    continue
                try:
                    for out in self.stage.process(item):
                        out_gen = getattr(out, "generation", None)
                        if out_gen is not None and self.cancel.is_stale(out_gen):
                            self.dropped_stale += 1
                            break  # el resto de la salida de este enunciado ya es obsoleta
                        self.queue_out.put(out)
                except Exception as exc:  # noqa: BLE001 - una etapa rota no debe matar el hilo
                    log.exception("Error en la etapa %s", self.stage.name)
                    if self.on_error is not None:
                        self.on_error(self.stage.name, exc)
        finally:
            self.queue_out.put(PIPELINE_END)
