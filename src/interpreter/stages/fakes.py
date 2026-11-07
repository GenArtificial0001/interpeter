"""Backends de prueba: deterministas y sin modelos. Sirven para tests y para medir el pipeline vacío."""

from __future__ import annotations

import time
from collections.abc import Iterator

import numpy as np

from .base import MTBackend, STTBackend, TTSBackend


class FakeSTT(STTBackend):
    def __init__(self, text: str = "hola mundo", delay: float = 0.0) -> None:
        self.text = text
        self.delay = delay
        self.calls = 0

    def transcribe(self, audio: np.ndarray, sample_rate: int, language: str) -> str:
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return self.text


class FakeMT(MTBackend):
    def __init__(self, table: dict[str, str] | None = None, delay: float = 0.0) -> None:
        self.table = table or {"hola mundo": "hello world"}
        self.delay = delay

    def translate(self, text: str, source: str, target: str) -> str:
        if self.delay:
            time.sleep(self.delay)
        return self.table.get(text, f"[{target}] {text}")


class FakeTTS(TTSBackend):
    """Emite ``chunks`` trozos de un tono corto; el primero tarda ``delay`` segundos."""

    def __init__(self, sample_rate: int = 24000, chunks: int = 2, delay: float = 0.0) -> None:
        self.sample_rate = sample_rate
        self.chunks = chunks
        self.delay = delay

    def synthesize(self, text: str, language: str) -> Iterator[np.ndarray]:
        if self.delay:
            time.sleep(self.delay)
        n = self.sample_rate // 10
        t = np.arange(n) / self.sample_rate
        for _ in range(self.chunks):
            yield (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
