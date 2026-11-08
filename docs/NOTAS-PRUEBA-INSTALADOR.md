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

## Para decidir

| # | Tema | Estado |
|---|---|---|
| 6 | La otra PC tiene Python 3.14. El instalador acepta 3.11–3.13. Las dependencias base se resuelven en 3.14, la voz clonada (`clone`) no (exige `torch<2.8`). No se probó que la app arranque en 3.14. | ⏳ ver memoria `instalador-python-otra-maquina`. |

## Regla que sale de esto

Todo lo que el instalador deje instalado se verifica contra una instalación **limpia** (venv nuevo), nunca contra el
entorno de desarrollo. `tests/test_dependencias.py` y los `import` finales del instalador existen para eso.

## Notas de quien prueba (añadir aquí)

-
