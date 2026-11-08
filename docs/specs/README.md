# Especificaciones pendientes de Interpreter (2026-11-08)

Pedidos de Jorge Gamez tras probar la versión 0.5.1 (orbe). **Todo esto está especificado y NO implementado**, salvo lo
marcado «hecho». Orden recomendado de trabajo (el primero es un problema que le molesta a diario):

| # | Spec | Qué resuelve | Estado |
|---|---|---|---|
| A | [`A-microfono-unificado.md`](A-microfono-unificado.md) | El micrófono «se apaga solo»; confusión entre «Silenciar» y «Micrófono de Windows activado»; recuperación automática | **Hecha (0.6.0)** — falta que la persona la pruebe |
| B | [`B-orbe-carga-sonido-retraso.md`](B-orbe-carga-sonido-retraso.md) | El ojo es el botón de iniciar; porcentaje de carga dentro del ojo; sonido futurista al terminar; retraso visible en el orbe | Solo especificada |
| C | [`C-ajustes-rediseno.md`](C-ajustes-rediseno.md) | Formulario de Ajustes pequeño, con menú lateral y el estilo Ford Blue (claro, azul `#0000FF`) de Visión; categorías Idiomas, Audio, Voces y motor, Análisis | Solo especificada |

Lo ya hecho y su estado están en [`../HANDOFF.md`](../HANDOFF.md) (§17 y §18). Reglas del proyecto en [`../../AGENTS.md`](../../AGENTS.md).

## Respuesta a la pregunta «decime vos» (opinión del agente)
Sí: unificar. Tener a la vez «Silenciar» (de la aplicación) y «Micrófono de Windows activado» (del sistema) es confuso y,
además, no resuelve el problema real (el micrófono «se apaga solo»). Propuesta: **un solo concepto «Micrófono encendido/apagado»**
(botón del orbe, casilla y botón de la ventana reflejan lo mismo), donde «encender» **fuerza** un ciclo real (apagar y encender
en Windows y reiniciar la captura) y un vigilante lo repara solo mientras se interpreta. Detalle en la spec A.

## Decisiones abiertas (necesitan a la persona)
1. **¿Qué reemplaza a «Historial» en el orbe?** Recomendado: «Idiomas ⇄» (invertir dirección). Otras: «Voz», «Glosario».
2. **¿Apagar el micrófono en Windows también silencia a otras aplicaciones que lo usen?** Sí. ¿Prefiere apagar solo dentro de Interpreter?
3. **Sonido de «listo»:** ¿un solo tono futurista corto (propuesto) o elegir entre varios? ¿Por los auriculares predeterminados de Windows (propuesto)?
4. **Consolidar todo en el orbe** (panel de ajustes anclado, ver HANDOFF §17): la spec C define el formulario pequeño con menú
   lateral como paso previo; el panel anclado sería una fase posterior.

## Reglas de trabajo (recordatorio para quien implemente)
- No romper lo que funciona: cada cambio detrás de pruebas; la ventana de ajustes conserva los NOMBRES de los controles existentes
  (`cmb_source`, `cmb_target`, `cmb_input`, `cmb_output`, `chk_mic_on`, `sld_mic`, `btn_toggle`, `btn_mute`, `log`, `lbl_delay`…) para no romper `tests/`.
- Subir la versión (`__init__.py`, `pyproject.toml`) **y** las líneas `VERSIONES` de los dos `.bat`; avisar a la persona qué reiniciar/recargar.
- Las pruebas nunca tocan el micrófono real (`tests/conftest.py` instala uno falso) ni abren audio ni descargan modelos.
- Rendimiento primero: nada de animación o análisis que robe CPU al intérprete (medir; la animación completa se apaga sola si pinta lento).
