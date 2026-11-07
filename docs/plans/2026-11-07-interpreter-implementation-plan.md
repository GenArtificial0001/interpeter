# Interpreter — Plan de implementación

Basado en [el diseño](2026-11-07-interpreter-design.md) (aprobado) y en
[el análisis de repos](2026-11-07-analisis-repos-referencia.md). Todo local, sin nube.

## Restricciones descubiertas el 2026-11-07

El índice de paquetes de Ford (`pip config`: Artifactory «gold») **no tiene** `faster-whisper`,
`ctranslate2`, `silero-vad`, `pocket-tts`, `nano-parakeet`, `piper-tts`, `openvino-genai`, `optimum`
ni `webrtcvad`. **Sí tiene** `torch`, `transformers`, `onnxruntime`, `accelerate`, `sentencepiece`,
`PySide6`, `sounddevice`, `coqui-tts` (incluye XTTSv2), `pyinstaller`, `pytest`, `ruff`, `mypy`.
No se evita el índice corporativo: la pila se adapta a lo disponible. Cualquier paquete que falte se
pide a IT, no se instala desde PyPI público.

Decisiones derivadas:

| Etapa | Elegido | Motivo |
|---|---|---|
| VAD | Energía con histéresis (propio, `vad.py`); Silero ONNX después si se consigue el modelo | `silero-vad` no está en el índice |
| STT | Whisper (`transformers`, CPU): `tiny`/`base`/`small` | `faster-whisper` no está |
| Traducción | NLLB-200 600M (`transformers`, CPU) | `ctranslate2` no está |
| TTS rápido | MMS-TTS (VITS, `transformers`) | `piper-tts` no está; sin clonación |
| TTS clonado | XTTSv2 vía `coqui-tts` | `pocket-tts` no está; licencia CPML no comercial: la acepta el usuario con `COQUI_TOS_AGREED=1` |

## Hitos

### Hito 0 — Benchmark (en curso)
- [x] Esqueleto, `pyproject.toml`, `.gitignore`, git local.
- [x] Interfaces de etapa, `StageRunner` (hilos + colas + cancelación), `Pipeline`, `Segmenter` (VAD).
- [x] Pruebas unitarias del VAD, la cancelación y el pipeline con backends falsos.
- [ ] `python -m interpreter.bench`: tiempos reales de STT, MT y TTS en el Core Ultra 5.
- [ ] Decisión con datos: modelo STT, perfil clonado sí/no, y cuánta CPU queda para Teams.

### Hito 1 — Pipeline por línea de comandos
- `interpreter-cli`: WAV de entrada → WAV de salida con el pipeline real y las métricas por etapa.
- Criterio: la misma frase da el mismo texto traducido que en el benchmark.

### Hito 2 — Audio en vivo
- `audio/devices.py`: listar y elegir dispositivos WASAPI; `audio/capture.py` (entrada → `Segmenter`);
  `audio/playback.py` (cola de salida → dispositivo).
- Salida a auriculares para probar. Criterio: hablar y oír la traducción, con latencia medida en pantalla.

### Hito 3 — Interfaz PySide6
- Ventana: ON/OFF, idioma de entrada/salida, perfil de voz, silencio, dispositivos, medidores y latencias.
- El motor se comunica con la interfaz solo por señales (sin importar Qt en el motor).

### Hito 4 — Bucle físico con Teams
- Divisor TRRS + cable TRS; ajuste de nivel; llamada de prueba.

### Hito 5 — Perfil clonado
- Asistente de grabación de la muestra (20–30 s), guardado local y comparación de calidad.

### Hito 6 — Empaquetado
- PyInstaller `.exe`; modelos en carpeta de usuario; CI en GitHub Actions (Windows) con tests y build.

### Fase 2
- Canal de escucha por loopback WASAPI, traducción al español y ducking de la voz original.

## Reglas de trabajo

- Pruebas primero para la lógica sin modelos; los tests con modelos llevan `@pytest.mark.models`.
- `ruff` y `mypy` limpios antes de cada commit.
- Comentarios y mensajes en español. Sin red en tiempo de ejecución una vez descargados los modelos.
- Nada de audio, modelos ni perfiles de voz en git.
