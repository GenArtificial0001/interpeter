# Interpreter — Diseño

Fecha: 2026-11-07 · Estado: **borrador para revisión** · Repo: https://github.com/GenArtificial0001/interpeter.git

## 1. Objetivo y alcance

Intérprete personal de voz en tiempo real. El usuario habla **español** y los participantes de una
reunión (Teams/Zoom/Meet) lo escuchan en **inglés** (o portugués/francés), idealmente **con su propia
voz**. El idioma original nunca llega a la llamada.

**Alcance v1**

- **Aplicación de escritorio para Windows**, que corre en la misma laptop donde se hace la reunión.
- Un solo canal: la salida del usuario. **Idioma de entrada e idioma de salida seleccionables**
  (por defecto español → inglés; también portugués y francés).
- Todo corre **local**, incluida la transcripción. Sin servicios de pago ni nube. Sin celular.
- No se instalan drivers ni se necesitan permisos de administrador.

**Fuera de alcance v1**

- Traducir lo que dicen los demás (en v1 se usan los subtítulos de Teams). Queda planificado como
  **fase 2** (ver §11): un segundo canal que captura el audio de la reunión por *loopback* de Windows,
  lo traduce a tu idioma y atenúa («ducking») la voz original al 15–20 %.
- Detección de idioma o de hablante.
- Cliente móvil y ejecución en GPU remota (RunPod, Cloud Run): posibles mejoras futuras detrás de las
  mismas interfaces.
- Uso comercial.

## 2. Contexto y restricciones

| Tema | Hecho |
|---|---|
| Equipo | Dell Pro 14 Premium PA14250: Core Ultra 5 238V (8 núcleos), Arc 130V integrada, NPU Intel AI Boost, 31,6 GB RAM, **sin NVIDIA/CUDA** |
| Audio | Códec Cirrus Logic CS42L43 con **jack combinado (TRRS)**; auriculares Dell WL5024 (USB) y JBL WAVE BUDS (Bluetooth) |
| Permisos | Sin administrador: Python y herramientas en el perfil del usuario, sin drivers (no se puede instalar un cable de audio virtual) |
| Red | VPN GlobalProtect con proxy PAC; `huggingface.co` es accesible |
| Licencias | Uso personal: las licencias no comerciales (XTTSv2, NLLB-200) son aceptables |
| Voces | Solo se clonan voces propias o de familiares con su consentimiento |

## 3. Arquitectura

Una sola aplicación con dos partes: la interfaz (hilo principal) y el motor de audio/IA (procesos o
hilos de trabajo, para que la interfaz nunca se congele).

```
Micrófono elegido ─► captura (sounddevice/WASAPI) ─► VAD ─► STT (es) ─► Traducción ─► TTS ─► reproducción
                                                                                              │
                                                                          salida elegida: auriculares (pruebas)
                                                                          o jack de la laptop (hacia Teams)
```

- La app **elige explícitamente** el dispositivo de entrada y el de salida (listas desplegables),
  por lo que no depende del dispositivo predeterminado de Windows.
- Interfaz: ON/OFF, idioma destino, perfil de voz, silencio, selectores de dispositivo, medidores de
  nivel y latencia por etapa. Atajo de teclado para silenciar (opcional).
- Empaquetado como `.exe` con PyInstaller (patrón ya usado en DBManager). Los modelos se descargan a una
  carpeta del usuario, no dentro del `.exe`.

## 4. Pipeline de IA

Cuatro etapas en streaming, cada una detrás de una interfaz común (`VADStage`, `STTStage`, `MTStage`,
`TTSStage`) para poder cambiar de modelo o de backend sin tocar el resto.

| Etapa | Candidato inicial | Notas |
|---|---|---|
| VAD | Silero-VAD | Corta frases/cláusulas; pausa de cierre configurable |
| STT | faster-whisper (modelo `small`/`base`, int8) en la PC | El idioma de entrada lo fija el usuario: no hay detección automática |
| Traducción | NLLB-200 distilled 600M con CTranslate2 (int8) | es→en/pt/fr; ampliable |
| TTS | **Perfil rápido**: Piper (sin clonación). **Perfil clonado**: **Pocket TTS** (candidato principal: CPU, streaming, clonación, en/es/fr/pt/de/it); plan B: XTTSv2 | Se decide con el benchmark; ver `2026-11-07-analisis-repos-referencia.md` |

- El perfil de voz se calcula **una vez** al grabar la muestra (20–30 s) y se guarda en disco local.
- Si el usuario habla mientras suena la frase anterior, la nueva espera en cola (con límite; ver §8).
- Aceleración: CTranslate2 en CPU; evaluar OpenVINO sobre la Arc/NPU para STT y TTS.

**Latencia.** Con traducción simultánea no existe latencia cero: el verbo español suele llegar al final
de la frase. Objetivo de trabajo (a validar con el benchmark, no garantizado):

- Perfil rápido: fin de cláusula → primer audio ≤ 2,5 s.
- Perfil clonado: se mide; si en CPU no es usable, se documenta y se evalúa un backend remoto.

La app registra el tiempo de cada etapa y lo muestra en la interfaz.

## 5. Interfaz de escritorio

**Decidido: PySide6 (Qt)**, para una ventana propia, pulida y empaquetable en `.exe`. La lógica del
motor no depende de la interfaz: se comunica con ella por eventos (estado, niveles, latencias,
errores), de modo que la UI se puede cambiar o probar sin cargar los modelos.

## 6. Cadena física de audio hacia Teams

La app y Teams corren en la misma laptop y no hay cable de audio virtual (requiere administrador). Se
usa un **bucle de audio físico** sobre el jack combinado de la laptop:

```
Salida de la app ─► jack combinado ─► divisor TRRS ─► hembra «auricular» ──cable TRS macho-macho──► hembra «micrófono» ─► entrada del jack ─► Teams
```

- Material: **divisor de audio 3,5 mm** (macho TRRS → 2 hembras: auricular y micrófono) y un **cable
  3,5 mm macho-macho de dos contactos (TRS)**. Un cable TRRS directo no sirve.
- En Windows el jack aparece como dispositivo de salida y de entrada. La app **reproduce por la salida del
  jack**; Teams/Zoom usa **como micrófono la entrada del jack** y como altavoz los auriculares
  Bluetooth/USB.
- La señal de salida es más fuerte que la de un micrófono: bajar el volumen de la salida del jack y
  ajustar la ganancia de entrada en Windows para evitar saturación o ruido de fondo.
- **Entrada de la voz del usuario** (para la app): micrófono de los auriculares USB (Dell WL5024) o el
  arreglo de micrófonos de la laptop. El micrófono Bluetooth se evita: baja calidad y puede robar la
  salida.
- **Pruebas sin cable:** la salida de la app se elige a los auriculares y se oye la traducción; el bucle
  físico solo hace falta para la prueba con Teams.

## 7. Privacidad y seguridad

- Todo el audio y el texto se procesan en la PC; **nada sale a servicios externos**.
- No se guarda audio por defecto. Los perfiles de voz viven en una carpeta local del usuario, ignorada por git.
- No se versionan modelos, perfiles de voz, `.env` ni claves.
- El audio de las reuniones de trabajo no se procesa en v1 (no hay canal de escucha).
- Política de Ford sobre herramientas de IA/audio en el equipo corporativo: el usuario la consulta.

## 8. Manejo de errores

| Situación | Comportamiento |
|---|---|
| Se desconecta un dispositivo de audio (USB/Bluetooth) | La app pausa, avisa y permite volver a elegir |
| El pipeline va más lento que el audio | Cola limitada; se descartan fragmentos viejos antes que acumular retraso |
| Modelo no encontrado o sin memoria | Mensaje claro; no arranca la interpretación |
| El CPU no da abasto (Teams con cámara compite por los mismos núcleos) | Indicador de carga; sugerir perfil rápido o modelo más pequeño |
| Falso positivo del VAD (ruido) | Umbral y duración mínima configurables; se ignora lo que no produce texto |
| Traducción vacía o rota | Se omite esa frase y se registra |
| Entrada y salida son el mismo dispositivo | Advertencia: provocaría realimentación |

## 9. Pruebas

- **Unitarias**: cada etapa con dobles de prueba para las demás; cola y control de latencia.
- **Integración**: audio WAV grabado → pipeline → texto y audio esperados; mide latencia por etapa.
- **Benchmark** (`scripts/benchmark.py`): tiempo por etapa en la PC real, con frases cortas y largas.
- **Audio/hardware**: lista de dispositivos, prueba de nivel, prueba del bucle físico en el jack.
- **Manual**: llamada de prueba en Teams con una persona de confianza.

## 10. Estructura propuesta del repo

```
docs/plans/            diseño y planes
src/interpreter/       motor (audio, pipeline, etapas) y app de escritorio
tests/
scripts/               benchmark, utilidades
```

Convenciones del workspace: comentarios y documentación en español, configuración desde `.env`.

## 11. Hitos

0. **Benchmark** de las etapas en la PC (decide el perfil de TTS).
1. Pipeline por línea de comandos con archivos WAV.
2. Motor con captura y reproducción en vivo (salida a auriculares).
3. Interfaz de escritorio.
4. Prueba del bucle físico con Teams.
5. Perfil clonado.
6. Empaquetado `.exe`.
7. **Fase 2 (bidireccional):** segundo canal independiente con su propio idioma de entrada/salida.
   Captura el audio de la reunión por loopback WASAPI del dispositivo de salida (sin cable ni
   administrador), traduce y reproduce en tus auriculares, con ducking de la voz original. Duplica la
   carga de CPU, así que depende de los resultados del hito 0.

## 12. Riesgos abiertos

- Latencia de la clonación en CPU (hito 0).
- Competencia por CPU entre la app y una videollamada de Teams en la misma laptop.
- Bucle físico en un solo jack: nivel de señal y ruido (se valida en el hito 4).
- Política de Ford sobre herramientas de IA y hardware de audio en el equipo corporativo.
- Licencias: XTTSv2 y NLLB-200 son no comerciales; cualquier uso fuera de lo personal exige cambiarlos.
