---
spec: A-microfono-unificado
estado: ESPECIFICADA (sin código)
creada: 2026-11-08
prioridad: 1 (afecta el uso diario)
---

# A — Micrófono unificado: encender/apagar de verdad y recuperarse solo

## 1. Problema (palabras de la persona, resumidas)
- En el orbe, «Micrófono» silencia la aplicación; la casilla «Micrófono de Windows activado» silencia el micrófono de Windows. No se
  entiende la diferencia.
- **A veces el micrófono «se apaga solo» y no la escuchan.** Para arreglarlo debe **desmarcar y volver a marcar** «Micrófono de
  Windows activado»; si detiene la interpretación, a veces tiene que repetirlo. No pasa siempre.
- Propuesta de la persona: que pulsar «Micrófono» sea un **apagado/encendido real** (no solo «silenciar»), y que sea lo mismo que la casilla.

## 2. Qué se sabe y qué no
- Visión descubrió lo mismo (spec `control-microfono-windows`): **al iniciar Windows el micrófono predeterminado puede quedar
  SILENCIADO** sin ningún error: `getUserMedia`/PortAudio devuelven un flujo válido que solo trae silencio.
- **Causa del «se apaga solo»: NO diagnosticada.** Hipótesis a descartar con registro: (a) otro programa o el botón de silencio del
  auricular Bluetooth (WL5024) cambia el silencio del dispositivo en Windows; (b) el cambio de perfil Bluetooth (HFP) deja el flujo de
  captura «colgado» y marcar/desmarcar lo reinicia; (c) Windows silencia al arrancar o al reconectar el dispositivo.
- Por eso la spec exige **registrar** cada cambio de estado del micrófono con hora (REQ-A6) además de repararlo.

## 3. Diseño

### 3.1 Un solo concepto: «Micrófono encendido / apagado»
Tres controles que muestran y cambian **el mismo estado** (hoy son dos estados distintos):
1. Botón **Micrófono** del orbe (clic; la rueda sigue cambiando el volumen).
2. Casilla **«Micrófono activado»** (reemplaza «Micrófono de Windows activado») en Ajustes > Audio.
3. Botón **Silenciar** de la ventana (`btn_mute`; se renombra «Apagar micrófono» / «Encender micrófono»).

Semántica (opinión del agente: es lo más claro para quien no distingue «silenciar» de «apagar»):
- **Apagar** = silencio de la aplicación (`engine.set_muted(True)`: lo que se diga NO se traduce) **y** silencio del micrófono en Windows
  (`sysmic.set_mute(id, True)`; el volumen queda como estaba).
- **Encender** = *ciclo forzado*: `set_mute(False)` aunque Windows diga que ya está activo, **más** (si se está interpretando)
  reinicio de la captura (`Engine.restart_capture()`), más silencio de la aplicación en `False`. Un «encender» siempre deja el
  micrófono realmente vivo, sin que la persona tenga que desmarcar y marcar.
- Aviso honesto: apagar el micrófono en Windows también lo apaga para otras aplicaciones que lo usen. Configuración opcional
  «Apagar solo dentro de Interpreter» (no toca Windows). Por defecto: ambos.

### 3.2 Vigilante («Mantener el micrófono activo mientras interpreto», activado por defecto)
Mientras `EngineState.RUNNING` y el micrófono esté **encendido** según la persona:
- **Cada 2 s** lee el estado en Windows (lectura barata; `sysmic.list_microphones()` + `match_device`). Si Windows lo muestra
  silenciado (lo hizo otro): lo vuelve a activar, muestra «El micrófono se había silenciado solo; lo reactivé» (burbuja/caption del
  orbe y registro) y cuenta el evento.
- **Silencio digital:** si el nivel de entrada es exactamente 0 (o < 1e-6) durante ≥ 6 s **sin** haberlo apagado la persona:
  reinicia la captura una vez (`restart_capture`), con tope de **1 reinicio cada 30 s**; si persiste, avisa «No llega audio del
  micrófono (¿botón de silencio del auricular?)». Un micrófono real casi nunca da ceros exactos, así que no hay falsos positivos por silencio de sala.
- Debe poder desactivarse (casilla) y **nunca** actuar si la persona apagó el micrófono.

### 3.3 «Reconectar micrófono»
Acción manual (menú del orbe y Ajustes > Audio): ciclo de silencio en Windows + `restart_capture()` sin recargar modelos.

### 3.4 Registro para diagnosticar
Cada cambio observado se escribe en `logs/interpreter.log` con el formato:
`Micrófono: silenciado=<bool> volumen=<n> dispositivo=<nombre> origen=<persona|vigilante|externo>`. Con eso se verá en qué
momentos exactos «se apaga solo» y se podrá correlacionar con unirse a una reunión, el auricular, etc.

## 4. Requisitos y criterios de aceptación
- **REQ-A1** Dado el orbe en marcha, cuando se pulsa «Micrófono», entonces cambia el estado único (apagado↔encendido); la casilla y
  `btn_mute` lo reflejan al instante sin disparar escrituras duplicadas.
- **REQ-A2** Dado un micrófono que Windows ya muestra como activo, cuando se pulsa «Encender», entonces se ejecuta igualmente el ciclo
  (escribe silencio=True y luego False) y, si se interpreta, se reinicia la captura.
- **REQ-A3** Dado «Apagar», entonces la aplicación no traduce y Windows reporta el micrófono silenciado; el volumen NO cambia; al
  «Encender» vuelve exactamente el volumen anterior.
- **REQ-A4** Dado el vigilante activo y el micrófono silenciado por un tercero, en ≤ 4 s queda activo de nuevo y se avisa una vez por episodio.
- **REQ-A5** Dado nivel exactamente 0 durante 6 s sin que la persona lo haya apagado, se reinicia la captura (máx. 1 por 30 s) y,
  si persiste, se avisa; si la persona lo apagó, no se hace nada.
- **REQ-A6** Cada cambio de estado queda en el registro con hora y origen.
- **REQ-A7** Si Windows no deja controlar el micrófono (driver sin control de volumen), el concepto sigue funcionando solo a nivel de
  la aplicación y el vigilante de «silenciado por Windows» se desactiva con un aviso; nunca se afirma algo sin verificar (se relee el estado tras escribir, como en `sysmic.py`).
- **REQ-A8** Las pruebas usan el micrófono falso (`tests/conftest.py::FakeMicBackend`); se amplía con `external_mute()` para simular que
  «otro» lo silencia. **Nunca** se escribe en el micrófono real en pruebas.

## 5. Dónde tocar (código existente)
- `src/interpreter/audio/sysmic.py` — `set_mute`, `set_volume`, `match_device` (hecho). Añadir `force_cycle(mic_id)` (mute True→False con relectura).
- `src/interpreter/engine.py` — añadir `restart_capture()` (detiene `_capture`, crea otra con `_capture_factory`, la inicia; sin recargar modelos). Exponer
  el nivel de entrada con marca de tiempo del último nivel > 0 (o calcularlo en `MainWindow._on_level`).
- `src/interpreter/ui/window.py` — `_on_mute_toggled`, `_on_mic_toggled`, `_refresh_mic`, `_orb_toggle_sys_mic`, `_on_level`: unificar en
  `set_mic_enabled(on: bool, origin: str)`; vigilante con `QTimer(2000)` activo solo en RUNNING. Ajuste persistente `keep_mic_alive`.
- `src/interpreter/ui/hud.py` — el botón «Micrófono» emite `mic_requested` (ya existe); mostrar su estado (rojo = apagado; ya existe `muted`).
- Pruebas: `tests/test_sysmic.py`, `tests/test_hud.py`, `tests/test_ui.py`, `tests/test_engine.py`.

## 6. Riesgos
- Apagar el micrófono de Windows afecta a otras aplicaciones (documentado y configurable).
- Un vigilante demasiado agresivo podría pelear con la persona (por eso nunca actúa si ella lo apagó, y tiene tope de reinicios).
- COM/Core Audio desde el hilo de la interfaz: lecturas de ~10–50 ms cada 2 s; si se nota, mover el vigilante a un hilo propio con `QTimer`+señal.
