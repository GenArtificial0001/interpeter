---
spec: C-ajustes-rediseno
estado: ESPECIFICADA (sin código)
creada: 2026-11-08
prioridad: 3
---

# C — Ajustes rediseñado: pequeño, menú lateral y colores Ford Blue (como Visión)

## 1. Pedido (resumido)
- Al pulsar **Ajustes** en el orbe se abre un formulario: hoy es una ventana larga y apilada (Idiomas, Audio, retraso, transcripción, perfil…).
  Debe ser **más acorde al orbe y más organizado**.
- **Pequeño, no tan grande.**
- **Menú lateral** que lleve a cada categoría, **con colores Ford Blue «como tiene Visión en apariencia»**.
- Categorías pedidas:
  1. **Idiomas**
  2. **Audio**
  3. **Análisis** — el retraso (sale de Ajustes y se ve también en el orbe), la **transcripción** y «demás análisis relevantes»
  4. **Voces y motor** — Voces, Reconocimiento y Aceleración, **con la pausa para cerrar cada frase**.

## 2. Apariencia: paleta «Oscuro · Ford Blue» de Visión
Fuente: `C:\Proyectos\VISION-Ford\dev\Vision\frontend\css\` (tema `[data-theme="dark-blue"]` sobre `:root`). Tokens a copiar a `ui/theme.py`:

| Token Visión | Valor | Uso en Interpreter |
|---|---|---|
| `--canvas` | `#0B0C0E` | fondo de la ventana |
| `--surface` | `#16181C` | tarjetas / páginas |
| `--surface-2` | `#1E2126` | campos, encabezados |
| `--surface-3` | `#24272D` | hover de filas/botones |
| `--ink` / `--ink-soft` / `--ink-faint` | `#F2F3F5` / `#9AA3AE` / `#666D78` | texto principal / secundario / terciario |
| `--line` / `--line-strong` | `#2A2E35` / `#383D46` | bordes finos / campos |
| `--accent` | `#4477DD` | acento (texto, bordes, selección) — 4,17:1 sobre `--surface` |
| `--accent-2` | `#6699FF` | hover / brillo |
| `--accent-solid` | `#1C396D` | RELLENO sólido con texto blanco (11,32:1): botón primario, ítem activo del menú |
| `--accent-soft` | `#16223F` | fondo de selección |
| `--signal-green/amber/red` | `#3DDC84` / `#E8A93B` / `#FF4D5E` | estados bien / aviso / error |
| `--sidebar-grad-1/2` | `#0E0F12` → `#08090A` | degradado del menú lateral |

Tipografía: Segoe UI (la de Windows). Esquinas 8–10 px; scroll fino en azul `#4477DD` sobre `#1E2126`. Mantener el estado actual del
orbe (cian `#35c8ff`) tal cual: el orbe conserva su estética del diseño de la persona.

## 3. Estructura

### 3.1 Ventana
- Se abre desde «Ajustes» del orbe (y desde «Modo clásico»). **Tamaño inicial 640×460, mínimo 560×400**; centrada junto al orbe;
  recuerda posición y tamaño. Título «Ajustes · Interpreter 0.x.y».
- **Menú lateral** (ancho ≈ 150 px, degradado `--sidebar-grad`): arriba el ícono del orbe y «Interpreter»; botones **Idiomas · Audio ·
  Voces y motor · Análisis** (checkables, uno activo con fondo `--accent-solid`, borde izquierdo `--accent`); abajo la versión y un
  botón «Volver al orbe». Atajos Ctrl+1…4.
- A la derecha: título de la página y una o dos tarjetas (`--surface`) con los controles; `QStackedWidget` para las páginas.
- **No hay botón «Iniciar»** como protagonista: lo hace el ojo del orbe (spec B). Se conserva un `btn_toggle` pequeño en el pie solo como
  respaldo cuando el orbe no está abierto.
- Banner de «micrófono apagado» (`mute_banner`) y avisos (`lbl_warning`) arriba de la página, con los colores de señal.

### 3.2 Páginas y contenido (se conservan los NOMBRES de los controles actuales)
1. **Idiomas:** «Yo hablo» (`cmb_source`), «Ellos escuchan» (`cmb_target`), botón **⇄ invertir** (nuevo, intercambia ambos), aviso de idioma sin voz (`lbl_warning`).
2. **Audio:** micrófono (`cmb_input`); **Micrófono activado** (`chk_mic_on`) + volumen (`sld_mic`, `lbl_mic_vol`) + «Reconectar micrófono»
   (spec A); salida (`cmb_output`) y estado del navegador/extensión (`lbl_browser`); **Anti-eco** (`chk_echo`); «Probar bucle / Enviar tono»
   (`btn_loop`, `lbl_loop`); nivel de entrada (`level_bar`, pequeño).
3. **Voces y motor** (todo lo técnico de «cómo suena y qué tan rápido»): Perfil de voz (`cmb_profile`), Voz hombre/mujer (`cmb_voice`),
   «Grabar mi voz…» (`btn_rec`) / «Elegir .wav…» (`btn_ref`, `lbl_ref`), **Reconocimiento** (`cmb_stt`), **Aceleración** (`cmb_accel`),
   **Pausa para cerrar frase** (`cmb_pause`), «Glosario…» (`btn_glossary`), casilla «Sonido al terminar de cargar» (spec B) y
   «Animación del orbe» (ligera / completa / ninguna; hoy está en el menú del orbe).
4. **Análisis** (solo lectura; se actualiza solo con la página visible):
   - **Retraso:** el último (`lbl_delay`, número grande) con su desglose (`lbl_detail`: voz→texto, traducción, voz), **mediana, p90, mínimo y
     máximo** de las últimas 50 frases, y barras de las últimas 12 (azul/ámbar/rojo según umbral 1,5 s / 2,5 s).
   - **Contadores de la sesión:** frases traducidas; descartadas por motivo (idioma distinto, eco, poca confianza, cola llena,
     silenciado, sin texto) y % de descartes; tiempo de sesión.
   - **Recursos del proceso:** CPU y memoria (RAM) de Interpreter con `psutil` (ya está en el entorno), refresco 1 s **solo** si la página está visible.
   - **Estado de conexiones:** extensión del navegador (versión, pestañas conectadas), dispositivo de micrófono en uso, eventos del vigilante del micrófono (spec A).
   - **Transcripción** (`log`): lista «Tú → traducción» con hora; botones **Copiar**, **Guardar .txt**, **Limpiar**; máximo 300 líneas (ya existe).
   - (Opcional) exportar las latencias a CSV para análisis externo.

## 4. Requisitos y criterios de aceptación
- **REQ-C1** La ventana abre en ≈ 640×460, con menú lateral de 4 entradas; cambiar de categoría no pierde los valores ni dispara escrituras.
- **REQ-C2** Los colores salen de los tokens de la tabla (constantes en `ui/theme.py`), no valores sueltos; el botón primario y el ítem activo usan `--accent-solid` con texto blanco.
- **REQ-C3** Todos los controles listados existen con el **mismo nombre de atributo** que hoy; `tests/` actuales siguen pasando (las que usan `isVisible()` cambian de página antes).
- **REQ-C4** «Análisis» muestra retraso (último/mediana/p90/mín/máx), contadores por motivo, recursos y la transcripción; no consume CPU cuando no está visible.
- **REQ-C5** «Ajustes» desde el orbe abre/trae al frente esta ventana; «Volver al orbe» la oculta; cerrar la ventana de ajustes NO cierra la aplicación si el orbe está abierto.
- **REQ-C6** El botón ⇄ intercambia idiomas respetando `voice_problem` (si el perfil no habla el nuevo destino, avisa y no intercambia).
- **REQ-C7** La posición/tamaño y la última página se recuerdan (`QSettings`).
- **REQ-C8** Rendimiento: abrir Ajustes durante una reunión no añade más de ~1 % de CPU de un núcleo medido con `psutil`.

## 5. Plan por fases (para no romper lo que funciona)
1. **Tema y estructura**: nuevo `theme.py` (tokens) + `QStackedWidget` con menú lateral reubicando los widgets existentes sin cambiar su lógica.
2. **Página Análisis**: acumulador de métricas (`MetricsStore`: lista circular de latencias, contadores por motivo, hora de inicio) alimentado desde
   `_on_metrics`/`_on_dropped`; barras con `QPainter` (sin dependencias).
3. **Pulido**: atajos, recordar página/tamaño, ⇄, «Copiar/Guardar» de la transcripción.
4. (Fase posterior, ver HANDOFF §17) panel de ajustes **anclado al orbe** y avisos como burbujas.

## 6. Dónde tocar
`src/interpreter/ui/window.py` (`_build_ui`, ~260 líneas: se reparte en páginas), `ui/theme.py` (paleta y hoja de estilos), nuevo
`ui/analysis.py` (página Análisis y `MetricsStore`), `ui/hud.py` (botón «Ajustes» ya emite `settings_requested`), pruebas en `tests/test_ui.py` y `tests/test_hud.py`.

## 7. Riesgos
- Reubicar ~40 controles puede romper pruebas que usan `isVisible()` o dependen del orden de creación (por eso REQ-C3).
- Si el orbe es el único modo y Ajustes falla al abrir, la persona queda sin forma de configurar: mantener siempre un camino de respaldo (`--clasica`).
- «Pequeño» + cuatro páginas con muchos controles: usar desplazamiento vertical dentro de cada página en lugar de agrandar la ventana.
