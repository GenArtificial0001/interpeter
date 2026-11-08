# HANDOFF — estado completo del proyecto Interpreter (para continuar con otra IA)

Escrito el 2026-11-07 al cierre de la sesión de trabajo con Claude Code. Léelo junto con
[`AGENTS.md`](../AGENTS.md) (reglas y comandos) y los planes en [`docs/plans/`](plans/).
Todo lo que aquí se dice como «verificado» se midió o se probó realmente; lo demás está marcado como no
verificado o como hipótesis.

---

## 1. Resumen ejecutivo

- **Qué es:** intérprete de voz local en tiempo real. Hablas español → los demás oyen inglés/portugués/francés
  en Teams, Meet o Zoom (en el navegador), con voz genérica o con tu voz clonada.
- **Versiones al cierre:** app **0.8.0**, extensión del navegador **0.3.3**. Rama de trabajo `dev`; `main` está
  en 0.2.3 (commit `27fad77`) y se actualiza solo cuando el usuario lo pide.
- **Funciona y está verificado en reuniones reales (el usuario lo confirmó):** Teams web, Google Meet y Zoom web
  con el micrófono virtual; glosario; lectura de cifras en voz alta; voz clonada rápida (YourTTS).
- **Lo que NO está resuelto:** los *microcortes* que oyó el interlocutor con la voz clonada rápida en una
  llamada, y la latencia (mediana 1,3–1,8 s; 3–4 s con Whisper *small* y frases largas). Ver §8 y §9.
  Se aplicaron varias mejoras (0.2.3 / 0.3.3) **que el usuario aún no ha probado**.

> 0.2.5: `phrasebook.py` traduce sin el modelo las frases cortas de uso diario (NLLB las inventa: «Hello.» →
> «¿Qué pasa?»); `clean_translation` descarta réplicas inventadas tras « - »; Whisper descarta alucinaciones
> conocidas («Thanks for watching»); los avisos de idioma se borran al cambiar «Yo hablo».

> 0.3.1: **voces de hombre y de mujer** en el perfil «Rápido (voz genérica)» (`voices.py`): catálogo por idioma
> (MMS, YourTTS incorporadas, Coqui css10 y voces de Windows/SAPI con el género que informa Windows), selector «Voz»
> en la ventana, recordado por idioma; las de YourTTS piden aceptar su licencia. Probadas con tono (F0) y Whisper.

> 0.4.0: **modo orbe** (`ui/hud.py`): interfaz alternativa con ventana sin marco y transparente (diseño de la persona:
> anillos, barras, orbe con punto de estado, Ajustes/Historial/Micrófono). Opcional: botón «Modo orbe» en la ventana
> clásica; clic derecho en el orbe → «Volver a la ventana clásica». Dibujado con `QPainter` (sin imágenes ni desenfoques,
> por la CPU). Primer prototipo: el diseño completo (partículas, destellos, escritura árabe) NO está; ver §16.

> 0.4.1–0.5.0: el orbe arranca en animación **«ligera»** (solo una barra de escucha **circular alrededor del ojo**,
> ~0,4 % de un núcleo; «completa» y «sin animación» en el menú, y la completa se apaga sola si pintar tarda >30 ms).
> **Micrófono de Windows** (`audio/sysmic.py`, Core Audio por `ctypes`, mismo enfoque que Visión): casilla «Micrófono de
> Windows activado» + control de volumen en la ventana; en el orbe, la rueda sobre «Micrófono» cambia el volumen (±5) y el
> menú activa/silencia. Cerrar la aplicación detiene el servidor del micrófono virtual (puerto 8765) y, si algo se cuelga,
> fuerza el fin del proceso a los 5 s (`__main__.shutdown`). Las pruebas usan un micrófono FALSO (`tests/conftest.py`):
> **nunca** se probó escribir en el micrófono real; la lectura real sí (laptop: «Microphone Array», 94 %).

> 0.5.1: iconos y textos del orbe más pequeños y dentro del anillo (fuentes en píxeles relativos al orbe, tamaño inicial
> 480); **`--orb`** abre siempre en modo orbe (lo usará Visión: `Iniciar Interpreter.bat --orb`), **`--clasica`** fuerza la
> ventana clásica; sin argumentos, la última vista usada. Los `.bat` pasan los argumentos (`%*`). Si el orbe no puede abrirse,
> se muestra la ventana clásica (nunca queda invisible). Pregunta abierta del usuario: consolidar TODO dentro del orbe (§17).

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

## 15. Integración futura con Visión (documentada, sin código)

La persona quiere que Visión (`C:\Proyectos\VISION-Ford\dev\Vision`) tenga una sección **«Widgets»** de atajos propios por
persona que lance aplicaciones como Interpreter (no integrarlo como código: pesa y tiene licencias propias). Está
especificado allí, solo como archivos nuevos (sin commit):
`specs/widgets-atajos/spec.md`, `specs/buscador-archivos/spec.md` y `docs/plans/2026-11-08-widgets-y-buscador-notas-de-diseno.md`
(con mediciones reales y el contrato `widget.json`).

**Pendiente en ESTE proyecto para ser un buen widget (no hecho):** candado de una sola instancia (hoy dos instancias compiten
por el micrófono y el puerto 8765), `--version` y un `widget.json` en la raíz (ejemplo en el Anexo A de esa spec). Debe seguir
lanzándose con `Iniciar Interpreter.bat`; no cambia nada de lo que usa la persona hoy.

## 16. Modo orbe: qué hay y qué falta

- **Hay:** ventana transparente sin marco (`WA_TranslucentBackground`; los píxeles transparentes dejan pasar el clic),
  arrastrar desde cualquier zona libre, rueda del ratón = tamaño, menú (siempre visible / clásica / salir), anillo brillante
  que gira, marcas, barras que reaccionan al micrófono, orbe cuyo punto indica el estado (rojo = interpretando), pulso al
  hablar la traducción, subtítulo con «lo que dijiste → traducción», y nombres nativos de idioma («Español → English»).
- **Falta (el diseño de referencia lo tiene):** partículas y ondas laterales, escritura árabe/otros alfabetos alrededor,
  haces de luz, desenfoque de fondo. Se dejó fuera adrede: costaría CPU y los modelos ya la necesitan. Si se quiere esa
  riqueza visual, la vía es un **PNG con transparencia** exportado del diseño (capas estáticas) y animar solo lo mínimo.
- **No verificado:** cómo se ve y se siente en la pantalla real de la persona (solo se dibujó en pruebas), el consumo de CPU
  durante una reunión, varios monitores / escalado de pantalla alto, y el comportamiento con la barra de tareas.
- Las barras se mueven con el nivel real del micrófono; su forma por barra es un perfil fijo decorativo (no es un espectro real).

## 17. ¿Todo dentro del orbe? (propuesta, aún no hecho)

La persona quiere una sola interfaz: el orbe, abierto siempre cuando lo llama Visión. Es viable y lógico, por fases:
1. **Hecho (0.5.1):** `--orb`, orbe más pequeño y legible.
2. **Siguiente:** «Ajustes» abre un **panel anclado junto al orbe** (tarjeta translúcida con el mismo estilo) con pestañas
   Idiomas · Audio (micrófono, volumen, salida) · Voz (perfil y voz hombre/mujer) · Avanzado (pausa, aceleración,
   glosario, grabar voz). Reutiliza los widgets existentes de `MainWindow` (sin duplicar lógica) montados en un contenedor sin marco.
3. **Después:** «Historial» como panel con la transcripción; avisos y errores como burbujas junto al orbe.
4. La ventana clásica queda como respaldo («Ajustes avanzados») hasta que el panel cubra todo.
Riesgo: ventanas translúcidas con widgets dentro (texto sin suavizado ClearType, foco/teclado). Mitigación: el panel es
una ventana aparte con fondo opaco redondeado, no parte del orbe transparente.

## 18. Notas de diseño del usuario sobre el orbe (para la próxima iteración)

Estado de cada nota (2026-11-08):
| Nota | Estado |
|---|---|
| El rendimiento del intérprete manda; si no, dejar solo la barra de escucha como animación | Hecho (animación «ligera» por defecto, 0,4 % de un núcleo) |
| La barra verde de volumen pasa a ser la «onda» alrededor del ojo; que sea circular | Hecho (marcas blancas que se encienden de arriba a los lados) |
| Activar el micrófono y cambiar su volumen como en la configuración de audio de Visión | Hecho (`audio/sysmic.py`; ventana clásica + rueda en el orbe) |
| El botón de cerrar debe cerrar todo, incluido el servidor (sin puertos abiertos) | Hecho (`__main__.shutdown` + prueba) |
| Iconos y letras demasiado grandes, sobresalen del círculo | Hecho en 0.5.1 (fuentes en píxeles relativos, 480 px) |
| Cuando lo llame Visión, abrir siempre en modo orbe | Hecho (`--orb`; Visión debe lanzar `Iniciar Interpreter.bat --orb`) |
| Consolidar TODO dentro del orbe | Propuesto en §17; falta el panel de ajustes anclado |
| **«Historial» está de más: reemplazarlo por algo más útil** | **Pendiente: elegir con la persona (ver abajo)** |

Candidatos para reemplazar «Historial» (el botón del lado derecho del orbe), de más a menos útil en una reunión:
1. **Idiomas (⇄):** cambiar de dirección o de idioma en un toque (Español→English ↔ English→Español); es lo que más se toca en vivo.
2. **Voz:** elegir al instante voz de hombre o de mujer (hoy está escondido en la ventana clásica).
3. **Glosario:** abrir el glosario o añadir un término rápido que acaba de salir mal.
4. **Ver lo último dicho:** un subtítulo ya existe abajo del orbe, así que «Historial» solo se justifica si fuera algo distinto (p. ej. copiar la última frase).

## 19. Especificaciones pendientes (escritas el 2026-11-08, SIN código)

Carpeta [`docs/specs/`](specs/README.md): **A** micrófono unificado + vigilante (el micrófono «se apaga solo»), **B** orbe con carga en
porcentaje, sonido futurista de «listo» y retraso visible, **C** Ajustes rediseñado (pequeño, menú lateral, colores Ford Blue de
Visión; categorías Idiomas, Audio, Voces y motor, Análisis). Orden recomendado: A → B → C. Cada spec trae REQ-IDs con criterios
de aceptación, archivos a tocar y riesgos. Decisiones abiertas con la persona en `docs/specs/README.md`.

## 20. Spec A implementada (0.6.0) — micrófono unificado

- Un solo interruptor «micrófono encendido/apagado»: botón del orbe, casilla «Micrófono activado» y botón «Apagar/Encender micrófono»
  son lo mismo (`MainWindow._on_mute_toggled` ↔ `_on_mic_toggled`, con `_syncing_mic` contra bucles). Apagar = aplicación sin traducir +
  micrófono silenciado en Windows; **Encender fuerza un ciclo** (`sysmic.force_cycle`: silenciar y reactivar) y, si se interpreta,
  `Engine.restart_capture()` (reabre la captura sin recargar modelos). «Reconectar» (casilla Audio y menú del orbe) hace lo mismo.
- **Vigilante** (`MainWindow._mic_watch`, cada 2 s solo al interpretar y con «Mantener el micrófono activo» marcado): reactiva un
  micrófono que otro silenció y, si pasan 6 s sin ninguna señal, reinicia la captura (máx. 1 cada 30 s; luego avisa). Nunca actúa
  si la persona apagó el micrófono. Cada cambio queda en el registro: `Micrófono: silenciado=… volumen=… dispositivo=… origen=persona|vigilante`.
- **No verificado con el micrófono real**: las pruebas usan el falso; la causa de que «se apague solo» sigue sin diagnóstico (ahora el
  registro mostrará cuándo ocurre). Pendiente: spec B (carga con % y sonido) y C (Ajustes rediseñado).

## 21. Spec B implementada (0.7.0) — carga con porcentaje, sonido y retraso en el orbe

- `EngineEvents.on_progress(fracción, etapa)`; `default_backends(cfg, progress=None)` informa 2 % → 30 % → 60 % → 85 % → 100 %
  (reconocimiento, traductor, voz, calentamiento); `Engine._build_backends` solo pasa `progress` a fábricas que lo admiten.
- Orbe: en carga el **ojo muestra «NN %»** y el anillo de marcas se llena; el número «repta» hasta +8 puntos sobre la etapa informada
  y nunca llega a 100 % antes del evento final (el porcentaje es una **estimación por etapas**, pesos sin calibrar con mediciones).
- `sound.py`: acorde ascendente sintetizado (WAV en memoria, ≈ −12 dBFS) por `winsound` en la salida predeterminada; suena **una vez** al
  pasar LOADING→READY (no al detener); casilla «Sonido al terminar de cargar» (`sound_on_ready`).
- Retraso: «Retraso 1,2 s · mediana 1,3 s» abajo del orbe (azul ≤1,5 s, ámbar ≤2,5 s, rojo). No verificado en pantalla real ni el sonido por los auriculares de la persona.

## 22. Spec C implementada (0.8.0) — Ajustes pequeño, menú lateral, Ford Blue claro

- La ventana (700×500) tiene **menú lateral azul `#0000FF`** con tarjetas blancas (Idiomas · Audio · Voces y motor · Análisis; Ctrl+1…4), barra
  superior azul con el estado y un pie con «Iniciar», «Apagar micrófono» e «Ir al orbe». Cada página tiene su propio desplazamiento. Se
  recuerda la última página (`settings_page`). `ui/theme.py` ahora es el tema **claro Ford Blue** de Visión (tokens en la spec C).
- **Idiomas:** botón ⇄ invierte la dirección (si la voz no habla el nuevo destino, avisa y no cambia). **Audio:** micrófono, interruptor único,
  volumen, «Reconectar», vigilante, salida, anti-eco, bucle. **Voces y motor:** perfil, voz hombre/mujer, grabar voz, reconocimiento,
  aceleración, **pausa para cerrar frase**, glosario y sonido de «listo».
- **Análisis** (`ui/analysis.py`): retraso (último, mediana, p90, mín, máx de las últimas 50 frases), barras de las últimas 12 (azul / ámbar /
  rojo), traducidas y descartadas por motivo, CPU y memoria del proceso (`psutil`, solo con la página visible), estado del navegador,
  intervenciones del vigilante y la transcripción con Copiar / Guardar / Limpiar.
- **Pendiente / no hecho:** la variante «oscura» opcional de Ford Blue; el panel de ajustes anclado al orbe (§17); sustituir «Historial» del orbe
  (la persona debe elegir; recomendado «Idiomas ⇄»). No verificado en la pantalla real de la persona (solo imágenes de prueba).

## 23. Salida de audio por dispositivo «robótica» (0.8.1)

Síntoma: por el micrófono virtual del navegador la voz sonaba perfecta, pero por un dispositivo (auricular ↔ altavoces
de la PC, en ambos sentidos) sonaba robótica, con todas las voces. No es la GPU (solo se usa para el encoder de Whisper;
la síntesis corre en CPU).

Causa probable: `Player` usaba `stream.write` bloqueante, flujo **mono** sobre un dispositivo estéreo (WASAPI compartido)
y latencia mínima. Los micro-cortes de ese patrón no se reportan como `output_underflow` y suenan «robóticos».

Cambio (`audio/io.py`): `Player` ahora usa un `OutputStream` por **callback** con un colchón propio (`_FIFO`), estéreo si el
dispositivo tiene ≥2 salidas y `latency=0.12 s`. Si falta audio entrega silencio en vez de esperar. `busy_until` (anti-eco)
se calcula igual. Tests: `tests/test_player.py` (callback simulado, `_FIFO`, estéreo/mono).

Sin verificar de oído (los tests no suenan en altavoces reales). Si sigue robótico, pedir: perfil de voz, micrófono y salida
usados, y probar otra salida; sospechar de Bluetooth en modo «manos libres» (HFP, 16 kHz) cuando el auricular también
hace de micrófono, de las «mejoras de audio» de Windows o de usar la entrada MME en vez de WASAPI.

## 24. Logo de Ford en Ajustes (0.8.2)

El óvalo de Ford (`src/interpreter/ui/assets/ford-logo.png`, 360 px de ancho, reducido del original de 3500 px) está en la
esquina derecha de la barra superior de Ajustes (`_ford_logo()` en `ui/window.py`, `MainWindow.lbl_logo`). Si el archivo
falta, la ventana abre igual sin logo. `pyproject.toml` declara `assets/*.png` como dato del paquete.

## 25. Instalador para otra máquina (0.9.0)

`dist/Instalar Interpreter.exe` (~11 MB, no se versiona: `dist/` está en `.gitignore`) se genera con
`.venv\Scripts\python scripts\build_installer.py`. Lleva dentro su propio Python (para ABRIR el instalador no hace falta
nada) y la app en `payload.zip` (código, extensión, `.bat`, icono). Icono: `assets/interpreter.ico`, generado por
`scripts/make_icon.py` desde `interpeter-icono.jpg` (círculo azul Ford `#0000FF`, exterior transparente).

Código: `src/interpreter_setup/` (`core.py` lógica y funciones sueltas probables; `gui.py` ventana tkinter;
`__main__.py` con `--cli [--verificar] [--dir] [--clone] [--sin-modelos] [--modelos-desde] [--proxy]`).
Flujo: 1 verifica (Windows x64, disco, Python, índice de pip, Hugging Face) → 2 busca Python 3.11–3.13 (si no hay,
`winget`/instalador de python.org solo para el usuario) → 3 copia la app y crea `.venv` → 4 `pip install -e ".[ml,ui,ov]"`
(+`clone` opcional) con el índice que YA tenga el equipo (espejo de Ford; nunca `--index-url`) → 5 modelos (descarga
de Hugging Face a `models/hf`, o copia desde una carpeta `models` de otra PC) → 6 comprueba los `import` y crea accesos
directos (escritorio y menú Inicio) al `.bat`, con el icono. Registro en `logs/instalacion_<fecha>.log`.
La app instalada usa `app_home()` = carpeta de instalación (instalación editable en modo desarrollo). XTTS no se
descarga: exige aceptar la licencia de Coqui (`COQUI_TOS_AGREED=1`), decisión del usuario.
Probado: tests (`tests/test_setup.py`), el `.exe` abre y lee la versión, y una instalación real parcial
(extraer + venv + dependencias base + accesos). NO probado: instalación completa con torch/modelos en otra PC, ni la
instalación automática de Python (depende del proxy/permisos de esa máquina).

### 25.1 Probar el instalador con .bat (0.9.1)
`Instalar Interpreter (prueba).bat` empaqueta el código actual (`scripts/build_installer.py --solo-payload`) y abre la
misma ventana del instalador sin compilar el `.exe`. Usa `.venv` (o `py`/`python`). Elegir una carpeta de instalación
distinta del repo (por defecto `%USERPROFILE%\Interpreter`).

## 26. Orbe: Entrada / Salida y dependencias declaradas (0.9.3)
- `websockets` y `psutil` faltaban en `pyproject.toml` (en el entorno de desarrollo llegaban de rebote): en una
  instalación limpia el puerto 8765 del micrófono virtual no abría. Ahora están declaradas, el instalador las comprueba
  con `import` y `tests/test_dependencias.py` falla si alguna librería importada no está declarada.
- Orbe (`ui/hud.py`): «Historial» desaparece. Botones: `settings` (izq.), `input` (der., icono de onda), `mic`
  (abajo-izq., silenciar) y `output` (abajo-der., icono de altavoz). `input_requested`/`output_requested` abren un `QMenu`
  junto al cursor (`MainWindow._pick_device`) con los dispositivos del desplegable de Ajustes.
- Notas de la prueba del instalador: `docs/NOTAS-PRUEBA-INSTALADOR.md`.

## 27. Orbe operable por completo (0.10.0)
Botones del orbe en arco (`OrbWindow.SLOTS`, grados: 180 izquierda → 0 derecha por abajo; `close` arriba a la derecha):
`speak` Hablo (+idioma) · `voice` Voz · `input` Entrada · `mic` Micrófono (silenciar) · `output` Salida · `settings`
Ajustes · `listen` Escuchan (+idioma) · `close` Cerrar. Cada selector abre un `QMenu` (`MainWindow._pick_from`/`_pick_voice`)
con las opciones del desplegable de Ajustes; elegir una equivale a elegirla allí. El menú contextual del orbe añade
Invertir idiomas, Pausa para cerrar frase, Anti-eco y Glosario. `theme.MENU_STYLE` da el estilo legible a los menús.
Ajustes: con el orbe abierto, la X de la ventana solo la oculta (`closeEvent` con `event.spontaneous()`); «Ajustes» del
orbe alterna mostrar/ocultar; «Cerrar» (o «Salir de Interpreter») cierra todo (motor y puerto 8765 incluidos).
Tamaño inicial del orbe: 640 px. Los iconos se dibujan con `QPainter` (`_icon_*` en `ui/hud.py`), blancos y lineales.
