# AGENTS.md — guía para agentes de IA (opencode, Claude Code, Codex, etc.)

Este archivo es lo PRIMERO que debe leer cualquier IA que continúe el proyecto. El estado completo, las
decisiones y el historial están en [`docs/HANDOFF.md`](docs/HANDOFF.md); léelo a continuación.

## Qué es

**Interpreter**: intérprete de voz **local** en tiempo real para videollamadas. El usuario habla español y los
demás oyen inglés (o portugués/francés), con voz sintética o clonada de la suya. Todo corre en su laptop
(sin nube). Aplicación de escritorio PySide6 + una extensión de navegador que presenta la voz traducida como
«micrófono virtual» a Teams, Meet y Zoom **web**.

- Idioma del proyecto: **español** (código, comentarios, docstrings, UI, commits, docs). Respeta eso al editar.
- Usuario: Jorge Gamez (analista Material Flow & IE en Ford). Hispanohablante, directo, no técnico en
  detalles de programación. Quiere que le **digas siempre cómo ejecutar/probar** cada cambio (ver «Cómo
  trabajar con el usuario»).

## Reglas duras (no negociables)

1. **Equipo corporativo de Ford, SIN permisos de administrador, detrás de VPN/proxy.** No instales drivers,
   no pidas admin, no cambies políticas del equipo.
2. **No saltarse el repositorio interno de pip** (Artifactory de Ford). Si un paquete no está en el índice
   (p. ej. `pypinyin`, `cutlet`, `piper-tts`, `faster-whisper`, `ctranslate2`), NO lo bajes de PyPI a mano ni
   vendorices código ajeno para esquivarlo: documenta que falta y propón pedirlo a TI.
3. **Todo local**: nada de nube ni de FordLLM para el motor de interpretación. Los modelos se descargan de
   Hugging Face UNA vez (con VPN) y luego se trabaja sin red (`HF_HUB_OFFLINE=1`).
4. **No guardar audio** salvo la muestra de voz del usuario (`voices/mi_voz.wav`). Nunca versionar `.env`,
   `models/`, `logs/`, `voices/`, `data/`, `glossary.json`, `*.wav`.
5. **No tocar cuentas ni reuniones reales** en pruebas automáticas: no abrir Teams/Meet/Zoom reales, no
   iniciar sesión, no reproducir sonido por los altavoces (los scripts usan `--mute-audio`).
6. **Licencias de modelos las acepta el usuario** (XTTSv2 CPML, YourTTS CC BY-NC-ND) con el diálogo de la app.
   No exportes `COQUI_TOS_AGREED=1` por él.
7. **Git**: identidad de commit `GenArtificial0001 <genartificial0001@gmail.com>` (NO el correo de Ford).
   Trabaja y sube a `dev`. `main` solo se actualiza cuando el usuario dice que validó (fast-forward desde
   `dev`). Remoto: `git@github-interpeter:GenArtificial0001/interpeter.git` (alias SSH con Deploy Key propio;
   ver `FORD-github-push` en `docs/HANDOFF.md`). Termina los commits con el trailer de coautoría de tu IA.

## Comandos

Desde `C:\Proyectos\Interpreter` (Windows, Git Bash o PowerShell):

```bash
# Ejecutar la app (es lo que usa el usuario; usa .venv-clone y se actualiza solo desde el código fuente)
"Iniciar Interpreter.bat"            # sin consola; registros en logs\interpreter.log
"Iniciar Interpreter (consola).bat"  # con consola, para ver errores

# Desarrollo (entorno .venv: torch/transformers sin coqui; suficiente para las pruebas)
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider      # ~165 pruebas, sin modelos (todo falso)
.venv\Scripts\python.exe -m ruff format src tests              # formato
.venv\Scripts\python.exe -m ruff check src tests               # lint (límite de 110 columnas)
.venv\Scripts\python.exe -m mypy src                           # ~10 avisos previos de stubs faltantes, ninguno propio

# Verificación de la extensión con un navegador REAL (Edge/Chrome, perfil temporal, sin tocar los del usuario)
.venv-clone\Scripts\python.exe scripts\verify_extension.py --browser edge     # o chrome
```

Hay DOS entornos virtuales:
- `.venv` → desarrollo y pruebas (sin coqui-tts, con `num2words` instalado a mano).
- `.venv-clone` → **runtime real** (torch 2.7.1+cpu, transformers 4.57.6, coqui-tts, PySide6, openvino,
  websockets). Los `.bat` usan este. Para probar con modelos reales usa este intérprete y `HF_HUB_OFFLINE=1`.

## Mapa del código (`src/interpreter/`)

| Archivo | Qué hace |
|---|---|
| `engine.py` | `Engine` (ciclo de vida, perfiles, eventos), `EngineConfig`, `LANGUAGES`, `make_stt`, `default_backends`. |
| `pipeline.py`, `runner.py`, `cancel.py` | Hilos por etapa + colas acotadas; `CancelScope` descarta lo obsoleto. |
| `stages/base.py` | `STTStage`/`MTStage`/`TTSStage`: glosario, cifras→palabras, motivos de descarte. |
| `stages/backends.py` | `WhisperSTT`/`OVWhisperSTT` (filtros de idioma/confianza), `NLLBTranslator`, `MMSTTS`, `YourTTSClone`, `XTTSClone`, `app_home()`, caché HF. |
| `vad.py` | `AdaptiveEnergyVAD` + `Segmenter` (pausa, corte suave a 5 s, máx. 12 s). |
| `glossary.py` | Términos (SharePoint, SAP…): corrección del reconocimiento, protección en la traducción, pronunciación. |
| `numbers.py` | Cifras a palabras por idioma (la voz sintética no lee «1, 2, 3») y detección de frases solo-números. |
| `audio/io.py`, `devices.py` | Captura (`MicCapture`), salida (`Player`), anti-eco, dispositivos. |
| `audio/webmic.py` | `WebMicServer` (WebSocket 127.0.0.1:8765), `WebMicPlayer`, ID de la extensión. |
| `ui/window.py` | `MainWindow` (PySide6); `ui/glossary_dialog.py`, `ui/voice_dialog.py`, `ui/theme.py`. |
| `bench.py` | Benchmark de etapas (`python -m interpreter.bench`). |

Extensión: `browser-extension/` (`manifest.json` MV3, `background.js` service worker con el WebSocket,
`bridge.js` content script aislado, `inject.js` mundo principal que sustituye `getUserMedia`).

## Convenciones

- Python ≥3.11, `from __future__ import annotations`, docstrings y comentarios en español que expliquen el
  **porqué**. Líneas ≤110. `ruff format` + `ruff check` deben quedar limpios.
- Las etapas se prueban con falsos (`stages/fakes.py`); las pruebas NUNCA descargan modelos ni abren audio real.
  Las pruebas de UI usan `QT_QPA_PLATFORM=offscreen`.
- Un test de `tests/test_ui.py` es intermitente bajo carga (`test_el_boton_de_prueba_envia_un_tono_al_navegador`);
  ya se estabilizó con `pump(...)`. Si falla una sola vez, repite antes de investigar.
- Heredocs largos en Git Bash fallan con comillas anidadas: escribe scripts de parche con la herramienta de
  archivos y ejecútalos con el Python del venv. `ruff format` puede reflujar el código entre parches.

## Lista de cambios de versión (OBLIGATORIA)

El usuario distingue las versiones por el título de la ventana y por las líneas `VERSIONES` de los `.bat`. Al
cambiar `__version__` (`src/interpreter/__init__.py` **y** `pyproject.toml`) o la versión de
`browser-extension/manifest.json`:

1. Actualiza la línea `rem VERSIONES: app X | extension del navegador Y` de **los dos `.bat`** (y el `echo` del
   `.bat` de consola). `tests/test_launchers.py` falla si se desfasan.
2. Si cambió la extensión, dile al usuario que debe **recargarla** en `edge://extensions` (Edge no lo hace solo
   y los `.bat` no pueden recargarla). La ventana muestra la versión conectada y avisa si está desactualizada.
3. Dile SIEMPRE, al final, qué ejecutar y qué comprobar (frase corta y concreta).

## Cómo trabajar con el usuario

- Respuestas en español, claras, sin jerga innecesaria. Si algo no se pudo probar, dilo.
- «Avanza sin preguntarme»: actúa y avisa; pregunta solo lo imprescindible. Confirma antes de acciones
  irreversibles o que afecten a terceros (push a `main`, borrar, enviar mensajes).
- No exageres el alcance: el usuario ya revirtió una vez un cambio «demasiado ambicioso» (idiomas nuevos).
  Prefiere pasos pequeños que mantengan funcionando lo que ya funciona (Teams/Meet/Zoom web verificados).
- Antes de afirmar que algo funciona, mídelo (pruebas, `verify_extension.py`, registros en `logs/interpreter.log`).
