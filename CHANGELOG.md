# Changelog

Todas las versiones notables de SubFlow se documentan aquí.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/),
y el versionado sigue [Semantic Versioning](https://semver.org/lang/es/).

---

## [v1.0.2] — 2026-05-14

### ✨ Features nuevas

- **Snap a vecinos en el timeline**: al arrastrar un cue cerca de otro, los bordes se "pegan" automáticamente. Mantené `Shift` presionado para escapar el snap.
- **Prevención de overlap**: el timeline ya no permite que arrastres un cue sobre otro. Se clampa al borde del vecino.
- **Recientes (`Ctrl+R`)**: nuevo botón "📂 Recientes" en la toolbar con los últimos 10 archivos abiertos. Persiste entre sesiones.
- **Auto-save cada 30s**: si la app crashea, podés recuperar el trabajo no guardado al reabrir el archivo.
- **Atajos para nudge cues**:
  - `Shift+→` / `Shift+←` mueve el cue seleccionado ±100ms
  - `Ctrl+Shift+→` / `Ctrl+Shift+←` mueve ±10ms (ajuste fino)

### 🏗 Refactor interno (transparente para el usuario)

El monolito de 3856 líneas se dividió en 14 módulos limpios bajo `subflow/`:
- `models.py`, `utils.py`
- `io/` (parsers, serializers, encoding, time_format)
- `format/wrap.py`
- `transcription/worker.py`
- `ui/` (delegate, timeline, dialogs)

Beneficios: código más navegable, pure functions testeables sin Qt, más fácil agregar features.

### 🐛 Fixes menores

- Workflow CI actualizado a Node.js 24
- Tests corren también en Python 3.13
- Algunos `except: pass` silenciosos ahora loguean al perf log
- Código dead removido (helper `_commit_typing` que era no-op)

### 🚀 Performance

- Snapshot de undo: ~1500x más rápido (comparte refs de WordTiming)
- Highlight del karaoke: ~200x más rápido (cursor + bisect)

---

## [v1.0.1] — 2026-05-14

### 🚀 Performance
- Open de archivos SRT grandes ahora es **instantáneo** (eran 30s+ antes)
- Reemplazar texto en bulk: instantáneo (eran 30s+)
- Ctrl+Z (deshacer): instantáneo
- Karaoke con cursor incremental (200x más rápido)
- Memoria del undo: 16x menos consumo

### ✨ Features nuevas
- **Timeline visual** abajo del video con drag-edit:
  - Arrastrá el centro de un bloque para mover el cue
  - Arrastrá los bordes para redimensionar (cambiar inicio o fin)
  - Cursor cambia visualmente según la zona
  - Status bar muestra los nuevos tiempos durante el drag
- **Indicador c/s** prominente por cue (caracteres por segundo)
- **Warning icons (⚠)** en cues con problemas:
  - Duración muy corta (<0.5s) o muy larga (>7s)
  - Velocidad de lectura excesiva (>21 cps)
  - Líneas demasiado largas (>42 chars)
- **Tooltips con conteo de caracteres por línea** (estilo HappyScribe)

### 🐛 Fixes
- Parser SRT robusto a líneas en blanco internas (común en YouTube/Aegisub)
- Soporte de archivos UTF-16 LE/BE con BOM (los que genera Notepad de Windows)
- Parser ASS con orden de campos flexible
- Find/Replace ya no deja líneas en blanco al final del cue
- Aviso prominente sobre SmartScreen de Windows en el landing

### 📦 Build & distribución
- Versión bundled del .exe ahora pesa ~130 MB (era ~300 MB)
- Metadata correcta del .exe (Detalles → SubFlow, v1.0.1)
- Dependencias pinneadas en `requirements.txt` para builds reproducibles
- CI con tests en 6 entornos (Ubuntu/Windows × Python 3.10/3.11/3.12)

### 🧪 Tests
- 70 tests automáticos cubriendo parsers, conversión de tiempos y formato
- 12 tests de regresión nuevos para los bugs reportados

---

## [v1.0.0] — 2026-05-12

Primera versión pública.

### Features iniciales
- Transcripción automática local con Whisper (sin API keys, sin internet)
- Edición de subtítulos en tabla con doble-click
- Multi-formato: SRT, VTT, ASS/SSA, TXT
- Auto-formato estilo Netflix (2 líneas, 42 chars)
- Highlight de palabra estilo karaoke durante reproducción
- Multilingüe: español, inglés, portugués, francés y detección automática
- 100% offline

[v1.0.2]: https://github.com/ylimecc/SubFlow/releases/tag/v1.0.2
[v1.0.1]: https://github.com/ylimecc/SubFlow/releases/tag/v1.0.1
[v1.0.0]: https://github.com/ylimecc/SubFlow/releases/tag/v1.0.0
