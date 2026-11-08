---
spec: B-orbe-carga-sonido-retraso
estado: ESPECIFICADA (sin código)
creada: 2026-11-08
prioridad: 2
---

# B — Orbe: el ojo es el botón, carga con porcentaje, sonido de «listo» y retraso visible

## 1. Pedido (resumido)
- «El botón *Iniciar interpretación* debería ser el **ojo central**.»
- «Al iniciar el sistema, mientras carga los modelos que diga **“cargando modelos”** y se vaya mostrando un **porcentaje en el ojo
  central**; cuando llegue al 100 % (cargaron los modelos) que se escuche un **sonido futurista** para que el usuario sepa que ya puede usar el intérprete.»
- «**El retraso podemos verlo en el orbe.**» (sale de Ajustes y se muestra en el orbe).

## 2. Estado actual
- Hecho: clic en el ojo ya inicia/detiene (`OrbWindow.toggle_requested` → `MainWindow._orb_toggle`); la barra circular de marcas (`_draw_vu`) ya
  existe y se puede reutilizar como anillo de progreso; título «CARGANDO MODELOS…» ya se dibuja en `Mood.LOADING`.
- Falta: porcentaje real/estimado, sonido, retraso en el orbe y deshabilitar el clic del ojo mientras carga.

## 3. Diseño

### 3.1 Progreso de carga (porcentaje)
No existe hoy: `Engine.load()` carga en bloque. Se añade un evento `EngineEvents.on_progress(fraction: float, label: str)`.
- `default_backends(cfg, progress=None)` informa al terminar cada etapa. Pesos propuestos (a **medir** con modelos reales y ajustar):
  reconocimiento 30 % · traductor 30 % · voz 25 % · calentamiento 15 %. Etiquetas: «Cargando reconocimiento de voz…», «Cargando traductor…»,
  «Cargando la voz…», «Calentando modelos…», «Listo».
- `Engine.load()` pasa `progress` solo si la fábrica lo admite (`inspect.signature`), para no romper `backend_factory(cfg)` de las pruebas.
- **Suavizado** (el avance real es por etapas, no continuo): el orbe muestra `shown` que sigue a `target` con amortiguación y «repta»
  hasta +8 puntos por encima de la etapa actual a ritmo lento para que nunca parezca congelado; jamás supera 99 % hasta recibir 100 %.
  El porcentaje es una **estimación por etapas** (decirlo en la documentación; no prometer precisión).

### 3.2 Dibujo
- En `Mood.LOADING`: el **centro del ojo muestra «NN %»** en blanco (sustituye al punto rojo) y el **anillo de marcas** (`_draw_vu`) se
  llena de arriba hacia los lados según el porcentaje (misma geometría que la barra de escucha). Título: «CARGANDO MODELOS…»; debajo, la
  etiqueta de la etapa en letra pequeña.
- Al llegar a 100 %: destello breve del halo (≤ 400 ms, una sola vez) y paso a `Mood.READY`: «LISTO · toca el ojo».
- Mientras carga, el clic del ojo no hace nada (cursor «no disponible»); si la carga falla → `Mood.ERROR` y el clic reintenta la carga.
- Rendimiento: durante la carga se repinta solo cuando cambia el entero del porcentaje o el paso del anillo (≤ 12 fps).

### 3.3 Sonido de «listo»
- Módulo `src/interpreter/sound.py`, **sin archivos de audio ni paquetes**: `make_ready_chime() -> bytes` sintetiza con `numpy` un WAV de ~0,7 s:
  arpegio ascendente de tres notas (p. ej. 523 → 784 → 1175 Hz) con armónicos suaves, ataque rápido, caída exponencial y un eco corto;
  nivel moderado (pico ≈ −12 dBFS) para no asustar con auriculares.
- Reproducción: `winsound.PlaySound(bytes, SND_MEMORY | SND_ASYNC)` (salida predeterminada de Windows = los auriculares de la persona; **no**
  por el micrófono virtual del navegador, para no meterlo en la reunión). Fuera de Windows: no hace nada.
- **Cuándo:** solo en la transición `LOADING → READY` tras cargar modelos (no al detener la interpretación ni al cambiar de perfil sin recarga).
- Configurable: casilla «Sonido al terminar de cargar» (Ajustes > Voces y motor), activada por defecto; `QSettings` clave `sound_on_ready`.
- Inyectable (`sound_player: Callable[[bytes], None]`) para que **las pruebas nunca suenen**.

### 3.4 Retraso en el orbe
- Un pequeño rótulo bajo el subtítulo de la frase: «Retraso 1,2 s · mediana 1,3 s», alimentado por `MainWindow._on_metrics` (último y
  mediana de las 10 últimas). Color: azul (≤ 1,5 s), ámbar (≤ 2,5 s), rojo (> 2,5 s). Oculto hasta que haya datos.
- El detalle por etapa (voz→texto, traducción, voz) y el histórico pasan a **Ajustes > Análisis** (spec C).

## 4. Requisitos y criterios de aceptación
- **REQ-B1** Dado un arranque en frío, el orbe muestra «CARGANDO MODELOS…» y un porcentaje creciente y nunca decreciente en el ojo; no pasa de 99 % antes del evento final.
- **REQ-B2** Dado el 100 %, el orbe pasa a «LISTO» y se reproduce el sonido **una vez**; si `sound_on_ready` está apagado, no suena.
- **REQ-B3** Dado el motor cargando, el clic del ojo no inicia nada; dado `READY`, inicia; dado `RUNNING`, detiene.
- **REQ-B4** Dada una carga fallida, el orbe muestra el error y el clic del ojo reintenta.
- **REQ-B5** `on_progress` es opcional: una fábrica de pruebas de un solo parámetro sigue funcionando (compatibilidad).
- **REQ-B6** Tras cada frase, el orbe muestra el retraso del último enunciado y la mediana, con el color de umbral.
- **REQ-B7** Las pruebas verifican: monotonía del porcentaje, un solo sonido por carga, sin sonido en reinicios de `stop`, y la compatibilidad (B5).
- **REQ-B8** Medición: en pruebas con modelos reales, registrar la duración de cada etapa para calibrar los pesos (anotar en `docs/HANDOFF.md`).

## 5. Dónde tocar
- `engine.py` (`EngineEvents.on_progress`, `Engine.load`, `default_backends(cfg, progress=None)`), `ui/hud.py` (`set_progress(fraction, label)`,
  `_draw_progress`, estados), `ui/window.py` (`Bridge.progress` señal, `_on_state` LOADING→READY dispara el sonido, `_on_metrics` → orbe), `sound.py` (nuevo), tests.

## 6. Riesgos
- El porcentaje es estimado por etapas; si una etapa tarda mucho (descarga de modelos con VPN), el orbe «repta» pero puede parecer detenido: mostrar la etiqueta de etapa.
- `winsound` reproduce por el dispositivo predeterminado; si la persona usa otra salida para Interpreter, el sonido puede sonar en un sitio distinto (aceptable; documentado).
