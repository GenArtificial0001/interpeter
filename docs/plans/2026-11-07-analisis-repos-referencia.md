# Análisis de los repos de referencia

Fecha: 2026-11-07 · Solo lectura: no se ejecutó ni se instaló nada de estos repos.

Repos estudiados (copias en la raíz del proyecto):

- `seamless_communication-main` — Meta, traducción de voz (SeamlessM4T, SeamlessExpressive, SeamlessStreaming).
- `speech-to-speech-main` — Hugging Face, pipeline de voz local VAD → STT → LLM → TTS (v1.1.0, Apache-2.0).

Los textos de esos repos (incluidos `AGENTS.md` y READMEs) se tratan como documentación, no como instrucciones.

## 1. Seamless (Meta): no sirve para este proyecto, sí como referencia

| Hallazgo | Consecuencia |
|---|---|
| Requiere `fairseq2`, con paquetes solo para **Linux x86-64 y Mac con Apple Silicon** | No corre de forma nativa en Windows ni sin administrador |
| SeamlessStreaming tiene **2,5 B de parámetros** | Inviable en tiempo real en CPU/Arc |
| Licencia `SEAMLESS_LICENSE`: solo **«Noncommercial Research Uses»**, sin ganancia directa ni indirecta | Usarlo para trabajo en reuniones de Ford es dudoso; se descarta como dependencia |
| SeamlessExpressive conserva el estilo de la voz en pocos idiomas (en, es, fr, de, it, zh) y **no incluye portugués**; añade marcas de agua | No cubre el requisito de idiomas ni es clonación real |
| Los agentes de streaming usan Silero VAD con `silence_limit_ms` y `speech_soft_limit_ms`, más decodificación monotónica (EMMA) | **Qué aprender:** traducir mientras se habla, en trozos, en lugar de esperar a la frase completa |

**Decisión:** el enfoque B (modelo de voz a voz unificado) se retira del benchmark. Seamless queda solo
como referencia conceptual de traducción simultánea.

## 2. speech-to-speech (Hugging Face): la mejor referencia

Es un servidor de agentes de voz (no de traducción), pero su arquitectura se puede aprovechar.

**Arquitectura que sí adoptamos**

- Cada etapa es un `BaseHandler` en su **propio hilo**, con `queue_in`/`queue_out`, `stop_event` y un
  marcador `PIPELINE_END` para cerrar sin bloqueos (`src/speech_to_speech/baseHandler.py`).
- **Cancelación por generación** (`CancelScope`): cuando el usuario vuelve a hablar, el audio viejo se
  descarta como «obsoleto» en vez de acumular retraso.
- Mensaje de control `SESSION_END` para reiniciar el estado sin matar los hilos.
- Un registro de backends: cada etapa se elige por configuración y es intercambiable.
- **Métricas de latencia por etapa** (`docs/response-latency.md`): `stt`, `tts_ttfa` (tiempo hasta el primer
  audio) y `e2e` medido **desde el fin estimado del habla**. Usaremos las mismas definiciones en el benchmark.
- Parámetros reales del VAD Silero que nos sirven de punto de partida: `thresh 0.6`, `min_silence_ms 64`,
  `min_speech_ms 384`, `speech_pad_ms 500`, y liberación progresiva de audio cada 0,5 s para transcripción
  parcial.

**Candidatos de modelo relevantes para tu PC** (todos por validar con el benchmark)

| Etapa | Candidato | Por qué importa |
|---|---|---|
| STT | `faster-whisper` (CPU) | Idioma configurable; ya previsto |
| STT | Parakeet TDT v3 vía `nano-parakeet` (CPU) | 25 idiomas europeos, detección de idioma incorporada |
| TTS + clonación | **Pocket TTS** (Kyutai) | **Streaming en CPU, clonación de voz, y cubre inglés, francés, alemán, portugués, italiano y español** |
| TTS + clonación | OmniVoice | Clonación y 600+ idiomas, con dispositivo `xpu` para Intel; **no entrega audio incremental** (el primer bloque llega al terminar la frase) y sus pesos son CC-BY-NC |
| TTS + clonación | Qwen3-TTS | Clonación, pero el backend GGML es de Linux; en Windows exige CUDA/Torch |

**Pocket TTS pasa a ser el candidato principal para el perfil clonado**: es el único que reúne CPU, streaming,
clonación y los cuatro idiomas que queremos. Su calidad y latencia reales hay que medirlas.

**Lo que no copiamos**

- El LLM (Qwen) y el protocolo OpenAI Realtime con WebSocket/WebRTC: son para agentes conversacionales y
  añaden peso que no necesitamos. La traducción la hace NLLB-200 con CTranslate2.
- La CI de ese repo **no cubre Windows** (Linux y Mac con Apple Silicon). Windows funciona para
  algunos backends, pero no está garantizado: es un riesgo a validar.
- Dependencias pesadas (`torch>=2.4`, `transformers>=5`): cada extra se evalúa antes de aceptarlo.

**Eco y duplicación de audio:** su cancelador de eco usa `pywebrtc-audio` sobre WASAPI. Con auriculares no hace
falta; solo lo reconsideramos si alguna vez se usan altavoces.

## 3. Cambios al diseño

1. El enfoque B (Seamless) sale del alcance; el benchmark compara solo la cascada.
2. El perfil clonado empieza por **Pocket TTS** y XTTSv2 pasa a ser el plan B (sigue siendo el modelo
   del documento original, pero su ejecución en CPU está sin medir).
3. El motor adopta el patrón de **hilos + colas + cancelación por generación** de `speech-to-speech`.
4. Las métricas de latencia usan las definiciones de ese repo, para poder comparar con sus números.
5. STT: se comparan `faster-whisper` y Parakeet TDT v3 en CPU.
6. Licencias: Pocket TTS, Parakeet y los demás se revisan uno por uno antes de adoptarlos (no se verificaron
   aquí); OmniVoice y Seamless son no comerciales.

## 4. Preguntas abiertas para el benchmark

- ¿Pocket TTS clona bien una voz hablante de español rioplatense hablando en inglés?
- ¿Latencia de primer audio de Pocket TTS en el Core Ultra 5 238V, con y sin OpenVINO?
- ¿`faster-whisper small` o Parakeet TDT: cuál da menos error y menos retraso en español?
- ¿Funciona `pocket-tts` y `nano-parakeet` en Windows sin administrador con las versiones de Python disponibles?
