# Notas de la prueba del instalador (otra máquina)

Registro de lo raro que se fue viendo al probar `Instalar Interpreter (prueba).bat` y la app instalada (0.9.1).
Estado: ✅ corregido · 🔧 en curso · ⏳ pendiente / sin decidir. Quien prueba puede añadir líneas al final.

## Errores graves

| # | Qué pasó | Causa | Estado |
|---|---|---|---|
| 1 | En la app instalada, Audio muestra «El puerto 8765 está ocupado por otro programa o no se puede abrir… (No module named 'websockets')». El micrófono virtual del navegador NO funciona. | `websockets` se importa en `audio/webmic.py` pero no estaba en `pyproject.toml`: en el entorno de desarrollo lo traía otro paquete por casualidad, en una instalación limpia no. `psutil` (página Análisis) estaba igual de «prestado» por `accelerate`. | 🔧 0.9.3: declarados `websockets>=13` y `psutil>=5.9`; el instalador los comprueba con `import`; test `tests/test_dependencias.py` falla si cualquier librería importada no está declarada. Falta verificar con una instalación limpia real. |

## Textos / diseño del instalador

| # | Qué se vio | Estado |
|---|---|---|
| 2 | «Modelos ya copiados (opcional)» no se entendía. | ✅ 0.9.2: «Copiar modelos desde otra PC (opcional)» + explicación debajo. |

## Lo que se ve en la captura de la app instalada (a revisar, sin decidir)

| # | Observación | Estado |
|---|---|---|
| 3 | Las casillas (Micrófono activado, Mantener el micrófono activo, Anti-eco) se ven como cuadros azules gruesos con centro blanco: no se distingue marcada de desmarcada. | ⏳ revisar el estilo `QCheckBox::indicator` en `ui/theme.py`. |
| 4 | Los desplegables cortan el texto («Microphone Array on SoundWire Device (…», «Micrófono virtual del naveg…»). | ⏳ ventana pequeña por diseño (700×500); valorar tooltip con el nombre completo o más ancho. |
| 5 | El borde oscuro a la izquierda del botón activo del menú («Audio») se ve recortado. | ⏳ revisar el estilo `QPushButton#nav:checked`. |

## Orbe

| # | Qué se pidió | Estado |
|---|---|---|
| 7 | Reemplazar «Historial» del orbe. Primero se propuso «Idiomas ⇄» (el usuario dijo que no), luego «Glosario» (elegido por error por mí; el usuario nunca lo pidió). | ✅ 0.9.3: botón derecho = «Entrada» (menú para elegir el micrófono) y un icono nuevo «Salida» abajo (menú para elegir dónde suena la voz traducida); «Micrófono» (silenciar) queda abajo a la izquierda. Los menús cambian el mismo desplegable de Ajustes. El botón «Glosario» de Ajustes no se tocó. |

| 8 | Los menús de Entrada/Salida salían con letra negra sobre fondo negro (ilegibles). | ✅ 0.10.0: `theme.MENU_STYLE` (fondo blanco, selección azul suave) en todos los menús del orbe. |
| 9 | Cerrar la ventana de Ajustes apagaba el intérprete; debía solo ocultarla como al arrancar, y «Ajustes» del orbe debía ocultarla al pulsarlo otra vez. | ✅ 0.10.0: con el orbe abierto, la X de Ajustes solo oculta; «Ajustes» alterna mostrar/ocultar. |
| 10 | Iconos lineales blancos en el orbe para operar todo desde él: «Hablo» (persona con la mano levantada + idioma), «Escuchan» (oreja + idioma), «Voz» (boca), más Entrada, Salida, Micrófono, Ajustes y «Cerrar». El primer icono de la mano salió «horrible». | ✅ 0.10.0: se rehízo la mano (persona + brazo + tres dedos). |
| 11 | Falta un icono de cerrar desde el orbe. | ✅ 0.10.0: «Cerrar» (símbolo de apagado, arriba a la derecha) cierra la aplicación completa. |
| 12 | Agrandar un poco el orbe. | ✅ 0.10.0: tamaño inicial 480 → 640 px (la rueda sigue cambiándolo entre 320 y 900). |

| 13 | No se veía si las casillas estaban marcadas (Micrófono activado, Mantener el micrófono activo, Anti-eco, Sonido al terminar de cargar). | ✅ 0.10.1: son interruptores ON/OFF (`ui/toggle.py`, mismo comportamiento que un QCheckBox). |
| 14 | Glosario y «Grabar mi voz» salían con letra oscura sobre fondo oscuro (modo oscuro de Windows). | ✅ 0.10.1: la app fuerza el esquema claro de Qt, `theme.py` da fondo claro a diálogos y tablas, y un test los renderiza con paleta oscura y comprueba el contraste. |
| 15 | Icono de «Yo hablo»: dejar solo el usuario, sin mano. | ✅ 0.10.1: cabeza y hombros. |
| 16 | «Cerrar» al centro de abajo (cambiar lugar con Micrófono) y tooltip al pasar el ratón. | ✅ 0.10.1: Cerrar centrado abajo, Micrófono arriba a la derecha; todos los botones del orbe tienen tooltip. Los botones y el título llevan un fondo translúcido oscuro para leerse sobre escritorios claros. |
| 17 | Añadir un icono a la izquierda (arriba) para equilibrar con «Micrófono»: nivel / volumen del micrófono. | ✅ 0.10.1: botón «Nivel» (barras que se encienden según el volumen de Windows); su menú sube/baja 10 % o fija 25/50/75/100 %. |
| 18 | El anillo grande del orbe dejaba fuera los rótulos y fondos de los iconos, y «CARGANDO MODELOS…» desbordaba. | ✅ 0.10.2: anillo de 0,52 a 0,70 del radio; botones pegados a su borde (radio 0,54); el título/subtítulo se achican o recortan con «…» para caber entre «Nivel» y «Micrófono». |

## Para decidir

| # | Tema | Estado |
|---|---|---|
| 6 | La otra PC tiene Python 3.14. El instalador acepta 3.11–3.13. Las dependencias base se resuelven en 3.14, la voz clonada (`clone`) no (exige `torch<2.8`). No se probó que la app arranque en 3.14. | ⏳ ver memoria `instalador-python-otra-maquina`. |

## Regla que sale de esto

Todo lo que el instalador deje instalado se verifica contra una instalación **limpia** (venv nuevo), nunca contra el
entorno de desarrollo. `tests/test_dependencias.py` y los `import` finales del instalador existen para eso.

## Notas de quien prueba (añadir aquí)

-
