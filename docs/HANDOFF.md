# HANDOFF — estado completo del proyecto Interpreter (para continuar con otra IA)

Escrito el 2026-11-07 al cierre de la sesión de trabajo con Claude Code. Léelo junto con
[`AGENTS.md`](../AGENTS.md) (reglas y comandos) y los planes en [`docs/plans/`](plans/).
Todo lo que aquí se dice como «verificado» se midió o se probó realmente; lo demás está marcado como no
verificado o como hipótesis.

---

## 1. Resumen ejecutivo

- **Qué es:** intérprete de voz local en tiempo real. Hablas español → los demás oyen inglés/portugués/francés
  en Teams, Meet o Zoom (en el navegador), con voz genérica o con tu voz clonada.
- **Versiones al cierre:** app **0.2.4**, extensión del navegador **0.3.3**. Rama de trabajo `dev`; `main` está
  en 0.2.3 (commit `27fad77`) y se actualiza solo cuando el usuario lo pide.
- **Funciona y está verificado en reuniones reales (el usuario lo confirmó):** Teams web, Google Meet y Zoom web
  con el micrófono virtual; glosario; lectura de cifras en voz alta; voz clonada rápida (YourTTS).
- **Lo que NO está resuelto:** los *microcortes* que oyó el interlocutor con la voz clonada rápida en una
  llamada, y la latencia (mediana 1,3–1,8 s; 3–4 s con Whisper *small* y frases largas). Ver §8 y §9.
  Se aplicaron varias mejoras (0.2.3 / 0.3.3) **que el usuario aún no ha probado**.

## 2. Línea de tiempo (commits relevantes en `dev`)

| Commit | Qué |
|---|---|
| `bfaa841`, `2d4d41b` | Micrófono virtual del navegador: servidor WebSocket local + extensión MV3; verificado en Edge/Chrome reales. |
| `a859121` | Perfil «Clonado rápido» (YourTTS) y lanzador con el entorno completo. |
| `7710fe2` | **Glosario** (SharePoint, SAP…): prompt a Whisper, corrección, protección en la traducción, pronunciación. |
| `e38206d`…`e8c2249` | Extensión 0.3.x: Zoom web; manifest corregido (ver §6); pista clonada por la página sigue viva. |
| `9c78855` | App 0.2.0: versión visible en la ventana y en los `.bat` (con prueba que evita el desfase). |
| `1ac58e1` | 0.2.1: la voz lee las cifras (`numbers.py`). |
| `f324788` → revertido en `0b01a6c` | 0.3.0: alemán, chino y japonés. **El usuario pidió revertirlo** (ver §10). |
| `8868f33` | 0.2.2: números dichos uno por uno aceptados; cifras sin pasar por el traductor; avisos viejos. |
| `5c0f9db` | 0.2.3 + extensión 0.3.3: menos cortes y espera (ver §9). |
| `bc5fbf4` | 0.2.4: inglés → español (voz genérica/XTTS); el glosario solo corrige lo oído en español. |

## 3. Arquitectura

```
micrófono ─► MicCapture (hilo, cola) ─► Segmenter + AdaptiveEnergyVAD (frases)
          ─► [Pipeline: hilos con colas acotadas, max_pending=3, CancelScope]
             STT (Whisper base/small; codificador en GPU Intel por OpenVINO si hay)
          ─► MT (NLLB-200 distilled 600M, int8, greedy; glosario protege términos)
          ─► TTS (MMS rápido | YourTTS clonado rápido | XTTSv2 clonado lento; cifras→palabras, pronunciación)
          ─► salida: Player (dispositivo) | WebMicPlayer → WebSocket 127.0.0.1:8765 → extensión → página
```

- **Motor** (`engine.py`): `Engine` carga los modelos (`load`), `start/stop`, eventos para la UI (estado,
  nivel, transcripción, traducción, descartes, métricas, clientes del navegador). `EngineConfig` guarda
  idioma, perfil, pausa, acelerador, etc. `voice_problem`/`VOICE_LANGS` dicen qué perfil habla qué idioma (YourTTS no habla
  español); la UI avisa y `start()` da un error claro.
- **Descartes** (visibles en la UI con su motivo): `sin_texto`, `idioma_distinto`, `poca_confianza`,
  `traduccion_vacia`, `sin_audio`, `cola` (cola llena), `eco`, `silenciado`.
- **Anti-eco:** si la frase empieza mientras suena la traducción por un dispositivo (`Player.busy_until` +
  `ECHO_TAIL_S=0.6`) se ignora. Con salida al navegador no se aplica (no suena en la sala).
- **Filtros del reconocimiento** (`WhisperSTT.transcribe`): se calcula la probabilidad del idioma de entrada
  y el logprob medio. Se rechaza si `p_idioma < 0.35` o `logprob < -1.0`, con excepciones: frases con
  términos del glosario (`p ≥ 0.03` y `logprob ≥ -0.8`) y frases que son solo números (`logprob ≥ -0.5`).
  Cada frase queda en `logs/interpreter.log` con `p_idioma`, `logprob`, términos y ACEPTADO/RECHAZADO.
  **Es la mejor herramienta de diagnóstico**: léelo antes de teorizar.
- **Segmentador** (`vad.py`): cierra con la pausa elegida (320/480/700/1000 ms; por defecto 480). Desde 0.2.3,
  tras 5 s de voz cierra en la primera pausa ≥200 ms; el corte forzado a 12 s ya no recorta voz real.
- **Glosario** (`glossary.py`, `glossary.json` junto a la app, ignorado por git): ver README. Cada entrada
  tiene término, variantes que «oye» Whisper, pronunciación y traducciones fijas por idioma.
- **Cifras** (`numbers.py`): `spell_numbers(text, lang)` antes de sintetizar (la voz descarta los dígitos:
  «The numbers are 1, 2, 3» sonaba «The numbers are and»); `is_numeric_phrase`/`is_digits_only` en la entrada.
- **Datos locales** (`app_home()`): la raíz del proyecto cuando se ejecuta desde el código; `INTERPRETER_HOME`
  lo cambia; `%LOCALAPPDATA%\Interpreter` si estuviera empaquetado. Contiene `models/` (HF + coqui),
  `voices/mi_voz.wav` (muestra de 25 s del usuario), `logs/`, `models/.ready` (si existe, modo sin red).

## 4. Perfiles de voz

| Perfil | Motor | Idiomas | Velocidad | Notas |
|---|---|---|---|---|
| Rápido (genérica) | MMS-TTS (VITS) | es, en, pt, fr | muy rápida | voz robótica; sin clonar |
| Clonado rápido | YourTTS (coqui) | **solo en, fr-fr, pt-br** | RTF 0,14–0,23 | usa la muestra del usuario; licencia CC BY-NC-ND |
| Clonado lento | XTTSv2 (coqui) | es, en, pt, fr (XTTS soporta más) | **RTF ≈1,8: no es tiempo real en CPU** | se corta en frases; solo útil con aceleración |

El usuario prefiere **Clonado rápido**. Probó «Rápido (genérica)» y dijo «funciona muy bien». Los cortes que
reportó fueron con Clonado rápido.

## 5. Entorno

- Windows 11 Enterprise, laptop Ford, Core Ultra 5 238V (8 núcleos, GPU Intel Arc, NPU), 31,6 GB RAM, sin admin.
- Python 3.13. `.venv` (dev/pruebas) y `.venv-clone` (runtime). Dependencias en `pyproject.toml`
  (extras `ml`, `clone`, `ov`, `ui`, `dev`; `num2words` está en `ml`).
- Repositorio interno de pip de Ford: tiene `openvino`, `onnxruntime`, `jieba`, `janome`; **no** tiene
  `faster-whisper`, `ctranslate2`, `piper-tts`, `kokoro-onnx`, `sherpa-onnx`, `optimum`, `openvino-genai`,
  `pypinyin`, `cutlet`, `fugashi`, `silero-vad`, `webrtcvad`.
- OpenVINO: GPU y NPU visibles. El codificador de Whisper corre en la GPU (f16) → STT base 1206 ms →
  541 ms (`generate` directo) → ~283 ms. La NPU no compila el codificador (formas dinámicas).
- Hugging Face: descargas con VPN y **sin symlinks** (Windows sin admin): `ensure_cached` solo existe en el
  commit revertido; en `main`/`dev` actuales las voces MMS ya están descargadas. Si añades un idioma nuevo
  recupera esa función (`git show f324788 -- src/interpreter/stages/backends.py`).

## 6. Extensión del navegador (`browser-extension/`)

- MV3. `background.js` (service worker) mantiene el WebSocket `ws://127.0.0.1:8765`, reparte el audio por
  puertos a las pestañas y reporta cuántas pestañas de reunión hay (`{"type":"tabs","n":N,"v":"0.3.3"}`).
  `bridge.js` (aislado) recibe del worker y hace `postMessage` a `inject.js` (mundo principal), que sustituye
  `getUserMedia` por una pista alimentada con la voz traducida, añade el dispositivo «Interpreter (voz
  traducida)» a `enumerateDevices` y agenda `AudioBufferSource` con `playHead`.
- **ID fijo** `lodhkanpadafbgdfgelmeaadelgongkg` (clave `key` del manifest). El servidor solo acepta ese origen
  (`EXTENSION_IDS` en `audio/webmic.py`). Si cambias la clave, actualiza ambos.
- **Regla que ya rompió la extensión una vez:** con `match_origin_as_fallback: true`, TODA ruta de los
  `matches` debe ser `/*` (Chrome/Edge rechazan el manifest ENTERO si no). Por eso Zoom es `https://*.zoom.us/*`
  y no `/wc/*`. `tests/test_manifest.py` y `verify_extension.py` (comprobación 0) lo vigilan.
- Protocolo: binario `uint32 LE` (frecuencia) + `int16` mono; JSON `hello/flush/ping`; la extensión envía `tabs`.
- **Cargar/actualizar:** `edge://extensions` → modo desarrollador → «Cargar descomprimida» →
  `C:\Proyectos\Interpreter\browser-extension`; tras cada cambio hay que pulsar **recargar** (Edge no lo hace).
- **Verificación real** (`scripts/verify_extension.py`, Edge 157 y Chrome 156): ~30 comprobaciones; incluye
  escenarios de Meet/Zoom (restricciones, paradas, iframes, cambio de dispositivo, dos pestañas), (6)
  continuidad de una frase real de YourTTS y (7) una «llamada» WebRTC/Opus con y sin DTX. Usa un puerto libre
  al azar para NO enviar tonos a los navegadores del usuario. Bajo carga alta del equipo puede dar falsos
  fallos (el servicio tarda en conectar): repítelo.
- Teams/Zoom **de escritorio** no pueden usar la extensión (hace falta cable físico TRRS+aux o VB-Cable).

## 7. Qué está verificado y qué no

**Verificado en reunión real por el usuario:** micrófono virtual en Teams web, Meet y Zoom web; traducción
es→en fluida; glosario; cifras leídas; reconocimiento de números sueltos (0.2.2, probado en logs).
**Verificado por pruebas automáticas:** ~165 pruebas (`pytest`), extensión en Edge/Chrome reales, continuidad
de voz real y llamada WebRTC sin huecos.
**No verificado:** que los microcortes desaparezcan con 0.2.3/0.3.3; reconexión tras horas con el service
worker suspendido; oír la traducción desde un segundo participante (el usuario se unió desde un celular
Samsung SM-A155M a Zoom); Teams/Zoom de escritorio; XTTS en tiempo real; canal entrante (v1 solo saliente).

## 8. Investigación de los microcortes (lo que se midió)

El usuario reportó (con Clonado rápido + Zoom/Meet) que el interlocutor oía «cortes» y que «te cortabas».

Datos:
- Latencia/velocidad del motor **iguales** entre 0.2.0 y 0.3.0 (voz 0,23× tiempo real; retraso mediano 1,3 s)
  → los cortes no vienen de lo que se cambió en 0.3.0.
- Una frase real de YourTTS atraviesa la extensión **sin huecos** (0 ms en 3/3 pruebas, Edge) y una «llamada»
  WebRTC/Opus **con y sin DTX** tampoco la recorta (0–5 ms). → Descartadas: extensión, Opus/DTX.
- Bajo carga del equipo (Zoom con vídeo ≈60 % CPU + app con 6,5 GB RAM) se observó que la cadena
  extensión→página sufre retrasos de >1 s y momentos de «0 pestañas»; el servidor **expulsaba a la extensión si
  tardaba >0,5 s en aceptar un trozo** (`SEND_TIMEOUT_S`), perdiendo la frase y mostrando el aviso «ninguna
  pestaña la recibe». **Corregido en 0.2.3/0.3.3** (4 s, reintento de 3 s, reconexión 100/250 ms).
- Del registro: las frases rechazadas con texto en inglés son, sobre todo, la traducción que vuelve a entrar
  por el micrófono (el filtro de idioma funciona como anti-eco). No son frases del usuario perdidas.
- Hipótesis aún **sin probar**: supresión de ruido de Zoom/Meet recortando la voz sintética (probar «Original
  sound for musicians» en Zoom); micrófono Bluetooth HFP del auricular WL5024; CPU saturada (Zoom + modelos);
  pausa 320 ms partiendo frases.

## 9. Mejoras de velocidad hechas y pendientes

Medido con modelos reales (frases de 2–7 s): Whisper base 0,44–1,7 s, small 0,9–1,1 s (y 3,7 s extremo a
extremo con frases largas); NLLB 0,24–0,8 s; YourTTS 0,57–0,87 s. Cambiar los hilos de torch (3–8) no ayuda.

Hecho en 0.2.3: cierre de frases largas en pausas cortas; YourTTS entrega frase por frase (primer audio antes);
el corte forzado no recorta voz; envío más tolerante.
Ideas pendientes (de más a menos prometedora): (1) traducir y sintetizar por frases mientras el reconocimiento
sigue (solapar etapas); (2) subir la prioridad del proceso (`ABOVE_NORMAL`, sin admin) y limitar hilos cuando
la CPU está saturada; (3) pasar NLLB a OpenVINO/ONNX (no hay `optimum`: conversión manual); (4) acelerar el
decodificador de Whisper (caché estática); (5) recomendar Whisper *base* para latencia y *small* solo para
precisión; (6) registrar `MicCapture.overruns` y subárranques del `Player` en el log para ver glitches.

## 10. Decisiones del usuario que NO hay que rehacer sin preguntar

- **Alemán/chino/japonés:** se implementaron (0.3.0) y el usuario los **revirtió** («fue demasiado ambicioso»).
  Hallazgos por si se retoma (`git revert 0b01a6c` los devuelve): alemán funciona completo con MMS
  (`facebook/mms-tts-deu`) y XTTS; chino y japonés traducen (NLLB) pero **no hay voz**: MMS no los tiene y
  XTTS necesita `pypinyin`/`cutlet`, ausentes en el pip de Ford. Opción de Windows: voces SAPI si se instala el
  idioma (hoy solo hay en-US y es-ES).
- **Todo local**, sin FordLLM ni nube. **Sin cable** mientras use reuniones web.
- El usuario quiere ver siempre la **versión** (título de la ventana + `.bat`).
- Idioma de salida más usado: inglés. Una persona que habla inglés puede elegir «Yo hablo: Inglés» →
  «Ellos escuchan: Español» (solo con voz genérica o XTTS; el texto de «Grabar mi voz» sigue en español).

## 11. Cómo probar cada cosa (checklist para quien continúe)

1. `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` y ruff limpios.
2. Cambio en la extensión → `verify_extension.py` en Edge y Chrome; subir versión del manifest; actualizar los `.bat`.
3. Cambio de motor → medir con modelos reales (`.venv-clone`, `HF_HUB_OFFLINE=1`) y mirar `logs/interpreter.log`.
4. Entregar al usuario: qué cerrar/abrir (`Iniciar Interpreter.bat`), qué recargar (`edge://extensions`), qué
   decir/hacer y qué resultado esperar.
5. Commit en `dev` con identidad `GenArtificial0001` y push (`GIT_SSH_COMMAND="ssh -o BatchMode=yes" git push origin dev`).
   `main` solo con orden expresa del usuario (`git checkout main && git merge --ff-only dev && git push origin main`).

## 12. Problemas conocidos y trucos

- `ensure_cached`/symlinks: sin admin, `huggingface_hub` falla al descargar; hay que desactivar
  `are_symlinks_supported` temporalmente (ver `f324788`).
- La UI no refresca avisos viejos: desde 0.2.2 se borran al conectarse una pestaña.
- Si `verify_extension.py` falla con «clients=0» o «puente SIN conectar», el equipo está cargado: repetir.
- «Whisper small» + frases largas llega a 3–4 s de retraso en este equipo; base es más ágil.
- «buenísima» → «buena», nombres propios y siglas dependen del modelo; el glosario con variantes lo mitiga.
- Las carpetas `seamless_communication-main/` y `speech-to-speech-main/` son repos de referencia (ignorados por
  git) que el usuario pidió estudiar; `docs/plans/2026-11-07-analisis-repos-referencia.md` resume lo aprendido.
- Documentos del diseño original y el plan de implementación en `docs/plans/`.

## 13. Git, red de Ford y despliegue

- Remoto `origin` = `git@github-interpeter:GenArtificial0001/interpeter.git` (repositorio **privado**). El
  alias `github-interpeter` está en `~/.ssh/config` con un **Deploy Key propio** (un repo = una clave; con
  permiso de escritura) y `ProxyCommand` por `connect.exe` (de Git for Windows) hacia el proxy HTTP de Ford
  `internet.ford.com:83` (SSH no sabe usar un proxy HTTP por sí solo).
- Si `git push` se cuelga o falla: `GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new" timeout 60 git push origin dev`
  y `ssh -T git@github-interpeter` (debe saludar con el nombre del repo). Sin VPN GlobalProtect conectada no hay
  salida a internet; el proxy de Ford puede cambiar según la región del gateway (leer el PAC en
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings` → `AutoConfigURL`).
- El usuario es quien registra Deploy Keys en GitHub y quien decide cuándo se promueve `dev` → `main`.
- CI (`.github/workflows/ci.yml`) corre las pruebas (sin modelos) en Windows al hacer push/PR a `main`.
- Empaquetado `.exe` (PyInstaller, `launcher.py`): quedó **obsoleto**; el usuario usa solo los `.bat`.
  No lo reactives sin pedirlo.

## 14. Primeros pasos recomendados para quien continúe

1. Leer `AGENTS.md`, este archivo y `logs/interpreter.log` (la última sesión).
2. Pedir al usuario que pruebe 0.2.3 + extensión 0.3.3 en una reunión real con **Clonado rápido** y comprobar si
   siguen los cortes; si siguen, pedir que pruebe «Original sound for musicians» en Zoom y la pausa de 700 ms.
3. Si los cortes persisten con CPU alta: subir prioridad del proceso, registrar `overruns`/subárranques, y
   medir (`scripts/verify_extension.py` escenario 6/7 bajo carga).
4. Trabajar la latencia (ideas del §9) con mediciones antes/después en el mismo equipo.
5. Mantener la lista de cambios de versión de `AGENTS.md` y avisar siempre al usuario cómo probar.
