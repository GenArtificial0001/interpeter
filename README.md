# Interpreter

Intérprete de voz **local** en tiempo real para videollamadas. Hablas en tu idioma (p. ej. español) y los
demás participantes de Teams, Zoom o Meet te escuchan en otro (inglés, portugués o francés). Todo corre en
tu PC: sin nube, sin cuentas y sin enviar audio a nadie.

> **Estado:** motor, interfaz de escritorio y benchmark funcionando. Falta la prueba con Teams por el
> bucle físico de audio y la clonación en vivo (necesita aceleración, ver abajo).

## Principios

| Principio | Implementación |
|---|---|
| **Todo local** | Modelos descargados una vez; después funciona sin red. |
| **Sin guardar audio** | El audio vive en memoria. Solo se guardan ajustes y tu muestra de voz. |
| **Sin permisos de administrador** | Python y paquetes por usuario; no instala drivers. |
| **Etapas intercambiables** | VAD, STT, traducción y TTS detrás de interfaces (`stages/base.py`). |
| **Latencia visible** | La ventana muestra el retraso real de cada frase. |

## Cómo funciona

```
micrófono → VAD → STT (Whisper) → traducción (NLLB-200) → TTS → salida elegida → Teams
```

Cada etapa corre en su hilo y se comunican por colas. Si vuelves a hablar, lo que iba en vuelo se
descarta en lugar de acumular retraso.

## Uso

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[ml,ui,ov,dev]"
.venv\Scripts\python -m interpreter          # abre la aplicación
.venv\Scripts\python -m pytest               # pruebas (sin modelos)
```

La primera vez descarga los modelos a `models/` (~3 GB). Benchmark de tu equipo:

```bash
.venv\Scripts\python -m interpreter.bench --tts mms --target en --quantize --beams 1
```

### Hacia Teams sin instalar nada

No hace falta un cable de audio virtual. Un bucle físico sobre el jack combinado de la laptop: divisor
TRRS (macho → 2 hembras: auricular y micrófono) y un cable 3,5 mm macho-macho entre las dos hembras. La
app reproduce por la salida del jack y Teams usa como micrófono la entrada del jack. Detalle en
[docs/plans](docs/plans/2026-11-07-interpreter-design.md).

## Rendimiento medido (Core Ultra 5 238V, solo CPU)

| Perfil | Fin de tu frase → primer audio |
|---|---|
| Rápido (sin clonar), reconocimiento en la GPU Intel (OpenVINO) | **~1,4 s** |
| Rápido (sin clonar), solo CPU | ~2,0 s |
| Clonado (XTTSv2) | ~3,2 s, pero genera 2–2,6× más lento que el habla: no sostenible en CPU |

## Licencias de los modelos

Whisper y NLLB-200 tienen licencias con restricciones (NLLB: CC-BY-NC). XTTSv2 usa la *Coqui Public Model
License* (no comercial) y exige aceptarla: exporta `COQUI_TOS_AGREED=1` solo si la aceptas. El proyecto es
de uso personal.

## Estructura

```
src/interpreter/    motor (audio, VAD, pipeline, etapas, engine) y ui/ (PySide6)
tests/              pruebas unitarias; test_integration_models.py con modelos reales (INTERPRETER_TEST_MODELS=1)
docs/plans/         diseño, plan de implementación y análisis de repos de referencia
```
