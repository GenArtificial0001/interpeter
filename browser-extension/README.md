# Interpreter: micrófono virtual para Teams / Meet web

Hace que **Teams (o Meet) en el navegador** reciba la voz traducida de Interpreter como si fuera tu
micrófono. No necesita cable, ni driver, ni permisos de administrador.

```
Tu voz → Interpreter (traduce) → WebSocket local 127.0.0.1:8765 → extensión → «micrófono» de Teams web
```

El micrófono real **no se abre ni se envía** a la llamada: Teams solo recibe lo que sale de Interpreter,
y entre frase y frase, silencio.

## Instalar (una vez)

1. Abre `edge://extensions` (o `chrome://extensions`).
2. Activa **Modo de desarrollador** (interruptor de la esquina). Si está bloqueado o no aparece, tu
   organización lo restringe: ver «Si no se puede instalar».
3. **Cargar descomprimida** y elige la carpeta `C:\Proyectos\Interpreter\browser-extension`.
4. Debe aparecer «Interpreter: micrófono virtual» activada.

Después de actualizar el código de la extensión, pulsa el botón de recargar de su tarjeta.

## Usar

1. Abre Interpreter (`Iniciar Interpreter.bat`).
2. En **Salida** elige **«Micrófono virtual del navegador (Teams web)»**. Debe aparecer
   «Esperando la extensión…» y, al abrir Teams web, **«✓ Navegador conectado (1)»**.
3. Abre Teams en el navegador (`teams.microsoft.com`) y únete a la reunión. No hace falta elegir el
   micrófono: Teams usará el de Interpreter. En la lista de micrófonos aparece
   **«Interpreter (voz traducida)»**.
4. Pulsa **Iniciar interpretación** y habla en español.

Para probar sin reunión: con la salida del navegador elegida, el botón **«Enviar tono al navegador»**
manda un tono de 1,5 s; en una llamada de prueba de Teams web se oye.

## Comprobar que está funcionando

En la pestaña de Teams, abre la consola de desarrollador (F12) y ejecuta:

```js
window.__interpreterVirtualMicInfo()
// { conectado: true, trozosRecibidos: 42, audioContext: "running" }
```

- `conectado: false` → Interpreter no está abierto o la salida no es «Micrófono virtual del navegador».
- `trozosRecibidos` sube al hablar → todo llega bien.
- `audioContext: "suspended"` → pulsa cualquier tecla o haz clic en la página (política de autoplay).

## Qué está verificado y qué no

Verificado con un navegador real (`scripts\verify_extension.py`, 10 comprobaciones, Edge 157 y Chrome 156): la
extensión se conecta, entrega un tono de 440 Hz con ~120 ms de latencia, vuelve al silencio al cortar, aparece
el dispositivo «Interpreter (voz traducida)» y **el micrófono real nunca se abre** (ni por la API moderna, ni
por el prototipo, ni por la API antigua). Se ejecuta con:

```
.venv-clone\\Scripts\\python.exe scripts\\verify_extension.py            # Edge, y Chrome si hace falta
.venv-clone\\Scripts\\python.exe scripts\\verify_extension.py --browser chrome
```

**Todavía no verificado:** con Teams y Meet reales. No hay informes públicos de que Teams web acepte un
micrófono sustituido por una extensión; trátalo como una hipótesis que se confirma en la primera reunión de
prueba. Tampoco se probó la reconexión tras suspender el service worker durante horas.

## Identificador fijo

El `manifest.json` lleva la clave pública (`key`) que fija el ID de la extensión:
`lodhkanpadafbgdfgelmeaadelgongkg` (también en `EXTENSION_ID.txt`). El servidor de Interpreter solo acepta
conexiones de ese ID: otra extensión, o cualquier página web, es rechazada. Si cambias la clave, actualiza
`EXTENSION_IDS` en `src/interpreter/audio/webmic.py`.

## Qué cubre y qué no

| | |
|---|---|
| Teams en el navegador (Edge, Chrome) | Sí |
| Google Meet en el navegador | Sí (misma técnica) |
| **Teams de escritorio** | **No**: la app de escritorio no es una página web. Para eso hace falta el cable o VB-Cable |

## Seguridad

- El servidor solo escucha en `127.0.0.1` y rechaza conexiones de páginas que no sean de reuniones.
- La extensión solo se inyecta en los dominios de Teams y Meet declarados en `manifest.json`.
- La extensión no lee ni envía nada de la página: solo reemplaza el micrófono por el audio local.

## Si no se puede instalar

- Si «Modo de desarrollador» está bloqueado, pide a TI que lo permita o que publiquen la extensión.
  Consulta `edge://policy` para ver qué políticas están activas.
- Alternativas: el cable físico (divisor TRRS + cable aux) o VB-Audio Virtual Cable, que sirven también
  para Teams de escritorio.

## Desarrollo

`manifest.json` (MV3) · `background.js` (service worker: WebSocket y reparto a las pestañas) ·
`bridge.js` (content script aislado) · `inject.js` (content script en el mundo de la página: sustituye
`getUserMedia`). El protocolo del servidor está documentado en `src/interpreter/audio/webmic.py`.
