# Editor de Subtitulos - Instrucciones

Aplicacion de escritorio en Python para editar subtitulos SRT y VTT con reproductor de video sincronizado. Funciona en Windows.

## Instalacion paso a paso

### 1. Instalar Python (si no lo tienes)

Descarga Python 3.10 o superior desde https://www.python.org/downloads/

**Importante:** durante la instalacion marca la casilla "Add Python to PATH".

### 2. Instalar las dependencias

Abre PowerShell o CMD en la carpeta donde guardaste los archivos y ejecuta:

```
pip install -r requirements.txt
```

Eso instala:
- **PyQt6** — la interfaz gráfica (obligatorio)
- **faster-whisper** — transcripción automática (opcional, ~200MB de instalación)

Si solo quieres editar subtítulos y no necesitas la transcripción automática, puedes instalar solo PyQt6:

```
pip install PyQt6
```

### 3. Ejecutar el programa

```
python subtitle_editor.py
```

## Como usarlo

1. Click en **Abrir video** y selecciona tu archivo (.mp4, .avi, .mkv, etc.)
2. Click en **Abrir subtitulos** y selecciona tu archivo .srt o .vtt
3. Los subtitulos aparecen en la tabla de la izquierda. Click en cualquier fila salta el video a ese momento.
4. **Toda la edición se hace directamente en la tabla**:
   - Columna **Texto** → **un solo clic** abre el editor multilínea dentro de la celda
     - **Enter** divide el subtítulo en la posición del cursor (estilo HappyScribe)
     - **Shift+Enter** inserta un salto de línea (texto multilínea)
     - **Ctrl+Enter** guarda y cierra el editor
     - **Esc** cancela y restaura el texto anterior
   - Columna **Inicio** o **Fin** → doble clic para editar. Acepta formato `HH:MM:SS,mmm` o `HH:MM:SS.mmm`

## Edición fluida entre subtítulos adyacentes (estilo HappyScribe)

Mientras editas el texto de un subtítulo en la celda (doble clic):

- **Backspace al inicio del texto** → mueve la primera palabra al subtítulo anterior automáticamente. Los tiempos se ajustan precisamente usando los timestamps por palabra.
- **Delete al final del texto** → mueve la última palabra al subtítulo siguiente automáticamente.

También están disponibles como botones en la toolbar y atajos:

- **⬆ Mover palabra al anterior** (Ctrl+Shift+↑)
- **⬇ Mover palabra al siguiente** (Ctrl+Shift+↓)
- **⇤ Fusionar con anterior** (Ctrl+Shift+M) — junta el subtítulo actual con el anterior
- **⇥ Fusionar con siguiente** (Ctrl+M) — junta el actual con el siguiente

Todos los movimientos son reversibles con Ctrl+Z.

## Highlight de palabra durante reproducción (karaoke)

Cuando los subtítulos fueron generados por Whisper, cada palabra trae su propio timestamp. Mientras se reproduce el video:

- La palabra que se está diciendo en ese instante se **ilumina en amarillo** sobre el video.
- Si editas el texto manualmente, el highlight de esa fila se desactiva (los timestamps de palabra ya no estarían sincronizados).
- Si cargaste subtítulos desde un SRT/VTT/ASS, no hay highlight de palabra — solo se muestra el subtítulo completo.
5. Botones de la barra de herramientas:
   - **+ Agregar**: inserta un subtitulo nuevo despues del seleccionado, con tiempo de inicio = posicion actual del video
   - **- Eliminar**: borra los subtitulos seleccionados
   - **Dividir**: parte el subtitulo seleccionado en dos
   - **Buscar/Reemplazar**: busqueda con reemplazo individual o masivo
   - **Marcar tiempo de inicio/fin**: usa el momento actual del video como inicio o fin del subtitulo seleccionado
6. **Guardar** sobrescribe el archivo. **Guardar como** te permite cambiar entre SRT y VTT.

## Transcripción automática (Whisper)

Si tienes `faster-whisper` instalado, puedes generar subtítulos desde el audio:

1. Carga un archivo de video o audio (`Abrir video/audio`)
2. Aparecerá un diálogo que pregunta qué hacer:
   - **Crear subtítulos automáticamente** → arranca la transcripción
   - **Cargar archivo existente** → abre el selector de SRT/VTT/ASS
   - **Por ahora nada** → solo reproducir
3. Si elegiste transcribir, pide:
   - **Idioma:** Español, Inglés, Portugués, Francés o detección automática
   - **Modelo:** Tiny / Base / Small (recomendado) / Medium / Large v3
4. La primera vez que uses un modelo, se descarga (entre 75MB y 3GB según el tamaño).
5. La transcripción corre **en tu propia computadora**, sin enviar audio a internet.
6. Aproximadamente, el modelo "Small" tarda lo mismo que dura el audio. Medium tarda 2-3x.
7. Cuando termine, los subtítulos aparecen en la tabla listos para editar y guardar.

También puedes lanzar la transcripción directamente desde el botón **🎙 Transcribir audio** (Ctrl+G) si ya tienes el video cargado.

## Segmentación inteligente (en la transcripción)

Cuando transcribes con Whisper, el programa ahora **detecta las pausas reales** del audio y corta el subtítulo ahí, en vez de pegarte 30 palabras corridas. Las reglas:

- **Pausa ≥ 400ms entre palabras** → nuevo subtítulo
- **Signo de fin de oración** (`.` `!` `?`) y el subtítulo ya tiene 15+ caracteres → nuevo subtítulo
- **Más de 80 caracteres acumulados** sin pausa → fuerza un corte (safety net)

Esto hace que cada subtítulo sea una **idea natural**, fácil de leer al ritmo del audio.

## Formato profesional de subtítulos

Para que los subtítulos sean cómodos de leer mientras se escucha el video, deben seguir estándares de la industria (Netflix, BBC, etc):

- **Máximo 2 líneas por subtítulo**
- **Máximo 42 caracteres por línea**
- **Líneas balanceadas** (similares en longitud)
- **Cortar en puntos lógicos** (después de comas, conjunciones)

El programa hace esto automáticamente:

1. **Durante la transcripción:** el diálogo de Whisper trae un checkbox "Aplicar formato profesional al terminar" activado por defecto.
2. **A mano sobre subtítulos ya cargados:** botón **✨ Auto-formato** en la toolbar (Ctrl+Shift+F). Toma cada subtítulo y lo reformatea. Reversible con Ctrl+Z.

El auto-formato prefiere cortar después de signos de puntuación cercanos al centro. Si no hay puntuación, parte en el espacio que más balancee las dos líneas.

## Atajos de teclado

| Atajo            | Accion                       |
|------------------|------------------------------|
| Ctrl+O           | Abrir video                  |
| Ctrl+L           | Abrir subtitulos             |
| Ctrl+S           | Guardar                      |
| Ctrl+Shift+S     | Guardar como                 |
| Ctrl+Z           | Deshacer                     |
| Ctrl+Y / Ctrl+Shift+Z | Rehacer                 |
| Ctrl+N           | Agregar subtitulo            |
| Del              | Eliminar subtitulos          |
| Ctrl+D           | Dividir subtitulo            |
| Ctrl+F           | Buscar y reemplazar          |
| Ctrl+Shift+F     | Auto-formato (2 líneas balanceadas) |
| Ctrl+T           | Saltar al tiempo seleccionado|
| Ctrl+[           | Marcar inicio en tiempo actual|
| Ctrl+]           | Marcar fin en tiempo actual  |
| Espacio          | Play / Pausa                 |
| Flecha arriba    | Subir volumen (+5)           |
| Flecha abajo     | Bajar volumen (-5)           |
| Flecha izquierda | Retroceder 5 segundos        |
| Flecha derecha   | Avanzar 5 segundos           |
| Ctrl+Espacio     | Play / Pausa (alternativo, funciona aunque estés escribiendo) |

## Resolucion de problemas

**"No se reproduce el video / pantalla negra"**
PyQt6 usa el sistema de Windows Media Foundation. Si tu video usa un codec raro (HEVC, AV1 sin extension instalada) puede no reproducirse. Soluciones:
- Instala los codecs HEVC desde Microsoft Store (gratis o $0.99)
- O convierte el video a MP4 con codec H.264 usando HandBrake o ffmpeg

**"Error al abrir el archivo"**
Si tu .srt usa una codificacion rara, el programa intenta UTF-8 primero y luego Latin-1. Si sigue fallando, abre el archivo en Bloc de notas y guardalo como UTF-8.

## Empacar como ejecutable (.exe)

El proyecto ya viene con un `build.spec` de PyInstaller **optimizado para reducir el tamaño**, además de un script `build.bat` que automatiza todo.

### Build automatico (recomendado)

1. Abre PowerShell o CMD en la carpeta del proyecto.
2. Ejecuta:
   ```
   build.bat
   ```
3. Espera 5-15 minutos. El script:
   - Instala las dependencias necesarias (PyInstaller, PyQt6, faster-whisper)
   - Limpia builds anteriores
   - Compila usando `build.spec`
4. El `.exe` final queda en `dist\SubFlow.exe`.

### Tamaño esperado

| Configuración | Tamaño aproximado |
|---|---|
| Sin optimizar (PyInstaller default) | ~600 MB |
| Con `build.spec` optimizado | **~135 MB** |

El `build.spec` ya viene con todas las exclusiones agresivas (PyQt6 sin Bluetooth/3D/WebEngine, sin PyTorch, sin pandas, etc.) que reducen el tamaño a ~135 MB.

**Sobre UPX:** UPX corrompe las DLLs nativas de `ctranslate2` y `onnxruntime` que usa faster-whisper, así que está **deshabilitado a propósito** en `build.spec`. No intentes activarlo: el `.exe` resultante crasheará al cargar el modelo.

El modelo de Whisper NO se incluye en el `.exe`. Se descarga la primera vez que el usuario transcriba algo (75 MB a 3 GB según el modelo que elija).

### Build manual (si prefieres correr PyInstaller a mano)

```
pip install pyinstaller
pyinstaller build.spec
```

## Estructura del codigo

El archivo `subtitle_editor.py` esta organizado en estos bloques:

1. **Helpers de paths** — `_app_data_dir()`, `_app_log_dir()` para ubicar logs en `%LOCALAPPDATA%\SubFlow\logs\`.
2. **Constantes** — todos los magic numbers (intervalos, limites, warnings) agrupados al inicio para que sea facil ajustarlos.
3. **Modelo y parsers** — clases `Cue`, `WordTiming` y funciones para leer/escribir SRT, VTT, ASS, TXT.
4. **Auto-formato** — `wrap_two_lines`, `wrap_two_lines_report`, `auto_format_cue_text`.
5. **Dialogos** — Buscar/Reemplazar, editor de celda multilinea, transcripcion.
6. **Worker de transcripcion** — `TranscriptionWorker` (QThread, faster-whisper).
7. **Ventana principal `SubtitleEditor`** — toda la UI, el reproductor, la tabla y las acciones.

Es solo un archivo (.py) para que sea facil de leer y modificar.

## Tests

Hay tests automaticos para los parsers, conversion de tiempos y formato:

```
pip install pytest
pytest tests/ -v
```

Los tests NO necesitan PyQt6 instalado (ver `tests/conftest.py`: stubea PyQt6
con `sys.modules`). Cubren round-trip SRT/VTT/ASS, BOM, rechazo de tiempos
negativos, edge cases de `wrap_two_lines` y mas.
