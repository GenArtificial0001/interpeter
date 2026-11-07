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
- [x] `python -m interpreter.bench`: tiempos reales de STT, MT y TTS en el Core Ultra 5.
- [ ] Decisión con datos: modelo STT, perfil clonado sí/no, y cuánta CPU queda para Teams.

**Resultados (2026-11-07, Core Ultra 5 238V, CPU, Python 3.13, torch 2.14, perfil rápido sin clonar,
frases de 2–7 s, mediana sin calentamiento, etapas en serie):**

| Configuración | STT | Traducción | TTS (1.er audio) | Fin de habla → audio |
|---|---|---|---|---|
| Whisper-base fp32, NLLB-600M fp32, 2 haces | 1,60 s | 1,09 s | 0,90 s | **3,6 s** |
| Whisper-base int8, NLLB-600M int8, voraz | 1,25 s | 0,56 s | 0,82 s | **2,6 s** |

- La cuantización int8 y la búsqueda voraz bajaron ~1 s, pero Whisper-base int8 falló una palabra
  («apolizado»); falta evaluar con tu voz real y probar `small` (más preciso, más lento).
- Carga de modelos en frío: ~2 min la primera vez (descarga); ~25 s con caché.
- Siguiente palanca de latencia: **no esperar a la frase completa**. Traducir y sintetizar por
  cláusulas (cortes de coma y pausa corta del VAD) y solapar etapas puede bajar el retraso percibido
  por debajo de ~1,5 s aunque cada etapa tarde lo mismo.
**Perfil clonado (XTTSv2 en CPU, mismo equipo; el usuario aceptó la licencia CPML el 2026-11-07).**
Entorno aparte `.venv-clone` (coqui-tts exige `transformers<5` y `torchaudio`, que en el índice de Ford
solo existe hasta torch 2.7.1). Referencia de voz: una muestra sintética (solo sirve para tiempos).

| Variante | 1.er audio del TTS | TTS completo | Fin de habla → 1.er audio | RTF |
|---|---|---|---|---|
| XTTS sin streaming, huella recalculada en cada frase | 5,6 s | 5,6 s | **6,6 s** | 1,75 |
| XTTS en streaming, huella calculada una sola vez | 1,8 s | 6,0 s (mediana; hasta 15 s) | **3,2 s** (2,4–3,9) | 2,0–2,6 |

- El streaming y la huella única bajan el primer audio de 6,6 s a ~3,2 s.
- Pero el **RTF es 2,0–2,6**: generar 1 s de voz cuesta más de 2 s de CPU, así que una frase de 3 s tarda
  ~7 s en completarse y la reproducción se corta a mitad. **No es sostenible en tiempo real en CPU.**
- Conclusión provisional: en esta PC la voz clonada sirve para frases muy cortas o con un búfer de
  reproducción grande (más latencia); el uso en vivo debe ser el perfil rápido. Para clonar en vivo hace
  falta aceleración (GPU Intel Arc con torch XPU u OpenVINO, no disponibles en el índice hoy) o un modelo
  de clonación más liviano. Se pedirá a IT: `openvino-genai`, `optimum[openvino]` y el torch XPU.

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

## Estado de los hitos (2026-11-07)

| Hito | Estado |
|---|---|
| 0 Benchmark | Hecho (perfil rápido ~2,6 s; clonado en CPU no sostenible) |
| 1 Pipeline por línea de comandos | Cubierto por `interpreter.bench` y la prueba extremo a extremo |
| 2 Audio en vivo | Hecho: captura/reproducción con sounddevice; micrófono y salida de esta laptop abren bien; motor con calentamiento, primer audio ~2,0 s en la prueba extremo a extremo |
| 3 Interfaz PySide6 | Hecha (idiomas, dispositivos, nivel, retraso, transcripción, perfil, ajustes persistentes) |
| 4 Bucle físico con Teams | **Pendiente: requiere el divisor TRRS y el cable, y una llamada de prueba** |
| 5 Perfil clonado | Medido; falta grabar tu muestra de voz y aceleración para uso en vivo |
| 6 Empaquetado | Hecho: `Interpreter.exe` (carpeta ~920 MB) arranca y carga modelos; CI listo para build en GitHub |

Notas del empaquetado: PyInstaller necesita un lanzador con import absoluto (`launcher.py`); sin consola
`sys.stderr` es `None`, por eso `setup_logging` redirige a `logs/console.log`. Los datos van a
`%LOCALAPPDATA%\Interpreter` (o `INTERPRETER_HOME`). Tras la primera carga se crea `models/.ready` y la app
deja de consultar Hugging Face.
