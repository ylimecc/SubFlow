"""
SubFlow - Editor de Subtitulos
==============================
Editor visual de subtitulos SRT y VTT con reproductor de video/audio sincronizado.

Funciones:
- Cargar video (mp4, avi, mkv, mov, etc.) o audio (mp3, m4a, wav, ogg, flac, aac)
- Cargar archivo de subtitulos (.srt / .vtt)
- Panel de edicion dedicado con campo de texto multilinea para el subtitulo seleccionado
- Editar texto y tiempos de inicio/fin de cada subtitulo
- Subtitulos superpuestos sobre el video durante la reproduccion (estilo VLC)
- Avisos visuales de calidad: duracion muy corta, muy larga, lectura demasiado rapida
- Agregar, eliminar y dividir lineas
- Buscar y reemplazar texto
- Reproductor con controles, click en un subtitulo salta al video en ese momento
- Tema oscuro
- Deshacer / Rehacer global (Ctrl+Z / Ctrl+Y) para todas las acciones
- Guardar como SRT o VTT

Requisitos:
    pip install PyQt6

Uso:
    python subtitle_editor.py
"""

import html
import os
import re
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, QUrl, QTimer, QEvent, QThread, QRect, pyqtSignal
from PyQt6.QtGui import (
    QAction, QBrush, QColor, QFont, QFontMetrics, QIcon, QKeySequence,
    QPainter, QPen, QShortcut, QTextCursor,
)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)


# ----------------------------------------------------------------------------
# Helpers de paths para datos persistentes (logs, etc.)
# ----------------------------------------------------------------------------

def _app_data_dir() -> str:
    """Carpeta donde guardar datos persistentes de la app.

    Windows: %LOCALAPPDATA%\\SubFlow
    Otros: ~/.local/share/SubFlow
    """
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share"
        )
    path = os.path.join(base, "SubFlow")
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        # Fallback: si no podemos crear la carpeta, usar temp
        import tempfile
        path = tempfile.gettempdir()
    return path


def _app_log_dir() -> str:
    """Subcarpeta de logs dentro de _app_data_dir()."""
    path = os.path.join(_app_data_dir(), "logs")
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        path = _app_data_dir()
    return path


def _perf_log(msg: str):
    """Escribe una linea con timestamp ms al log de performance.

    Path: %LOCALAPPDATA%\\SubFlow\\logs\\subflow_perf.log
    El log se rota cuando supera 1MB. Util para diagnosticar freezes en el
    .exe: si el usuario reporta "se cuelga al abrir", el log muestra
    exactamente en que paso quedo.
    """
    try:
        import time as _t
        log_path = os.path.join(_app_log_dir(), "subflow_perf.log")
        line = f"{_t.strftime('%H:%M:%S')}.{int((_t.time() % 1) * 1000):03d}  {msg}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Constantes (numeros antes dispersos por el codigo, agrupados para que sea
# facil ajustarlos sin grepear)
# ----------------------------------------------------------------------------

# UI / Player
POLL_HIGHLIGHT_MS = 100        # cada cuanto refrescamos el highlight del cue actual
SEEK_STEP_MS = 5000            # flechas izq/der al reproducir
VOLUME_STEP = 5                # flechas arriba/abajo al reproducir
NEW_CUE_DURATION_MS = 2000     # duracion por defecto al agregar un cue (Ctrl+N)
END_OF_MEDIA_REWIND_MS = 100   # retroceso al llegar al final del video para no
                               # quedar con frame negro
FIRST_FRAME_PREVIEW_MS = 80    # tiempo de "play silencioso" para forzar render
                               # del primer frame al abrir un video
DOTS_ANIMATION_MS = 500        # animacion de "Cargando..." en el dialogo
SPLIT_GAP_MS = 100             # margen al dividir un cue desde el cursor para
                               # evitar duraciones de 0 ms

# Historial / persistencia
UNDO_HISTORY_LIMIT = 100       # cuantos pasos guardamos de undo
WORKER_WAIT_MS = 5000          # tiempo de gracia al cerrar para que el worker
                               # de transcripcion termine ordenadamente
WORKER_TERMINATE_WAIT_MS = 2000  # despues de terminate() en el peor caso
LOG_ROTATION_BYTES = 1_000_000  # ~1 MB: a partir de aqui se rota el log a .1

# Estandares profesionales de subtitulado (Netflix / BBC)
MIN_CUE_DURATION_S = 0.5       # subtitulo demasiado corto -> warning rojo
MAX_CUE_DURATION_S = 7.0       # subtitulo demasiado largo -> warning naranja
MAX_READING_CPS = 21           # caracteres por segundo legibles

# Altura FIJA de fila. Determinada empiricamente: 76px = ~3 lineas de texto
# que cubre el 99% de los subtitulos profesionales (Netflix max es 2 lineas).
# Las raras excepciones de 4+ lineas se ven truncadas pero el tooltip y el
# editor (doble-click) muestran el texto completo.
#
# Por que altura FIJA y no variable: Qt recomputa el layout de TODA la tabla
# cada vez que cambia un setItem cuando el verticalHeader esta en
# ResizeToContents. Con 541 cues × 6 columnas × cascada de layout, eso
# congela la UI varios minutos. Altura fija = O(1) por operacion.
TABLE_ROW_HEIGHT = 76
TABLE_ROW_PADDING = 8


# ----------------------------------------------------------------------------
# Modelo de datos y parsers
# ----------------------------------------------------------------------------

@dataclass
class WordTiming:
    """Tiempo de inicio/fin de una palabra individual (opcional, solo viene de Whisper)."""
    text: str        # incluye espacio inicial si Whisper lo dio asi
    start_ms: int
    end_ms: int


@dataclass
class Cue:
    """Un subtitulo individual (start_ms, end_ms, text).

    Si fue generado por Whisper, ademas tiene `words` con los tiempos por palabra.
    Si fue cargado desde un archivo SRT/VTT, `words` esta vacio.
    """
    start_ms: int
    end_ms: int
    text: str
    words: List[WordTiming] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


def ms_to_srt_time(ms: int) -> str:
    """Convierte milisegundos a 'HH:MM:SS,mmm' (formato SRT)."""
    if ms < 0:
        ms = 0
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    msec = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{msec:03d}"


def ms_to_vtt_time(ms: int) -> str:
    """Convierte milisegundos a 'HH:MM:SS.mmm' (formato VTT)."""
    return ms_to_srt_time(ms).replace(",", ".")


def time_str_to_ms(s: str) -> int:
    """Acepta 'HH:MM:SS,mmm' o 'HH:MM:SS.mmm' o 'MM:SS,mmm'. Devuelve ms.

    Rechaza tiempos negativos: si la cadena empieza con '-' (o algun componente
    es negativo) lanza ValueError para no esconder corrupcion en SRT/VTT.
    """
    s = s.strip().replace(".", ",")
    if s.startswith("-"):
        raise ValueError(f"Tiempo negativo no permitido: {s}")
    if "," not in s:
        s = s + ",000"
    time_part, ms_part = s.rsplit(",", 1)
    parts = time_part.split(":")
    if len(parts) == 2:
        h = "0"
        m, sec = parts
    elif len(parts) == 3:
        h, m, sec = parts
    else:
        raise ValueError(f"Formato de tiempo invalido: {s}")
    h_i, m_i, sec_i = int(h), int(m), int(sec)
    ms_i = int(ms_part.ljust(3, "0")[:3])
    if h_i < 0 or m_i < 0 or sec_i < 0 or ms_i < 0:
        raise ValueError(f"Tiempo negativo no permitido: {s}")
    return h_i * 3600000 + m_i * 60000 + sec_i * 1000 + ms_i


def parse_srt(text: str) -> List[Cue]:
    """Parsea contenido SRT a lista de Cue.

    Robusto a:
      - Cues que contienen lineas en blanco internas (YouTube, Aegisub).
      - Numero secuencial ausente (algunos generadores lo omiten).
      - CRLF/LF/CR.
      - BOM UTF-8 al inicio.

    Estrategia: en vez de partir por blank-lines (que rompe cues con parrafos),
    localizamos primero todas las lineas de timestamp y el texto de cada cue
    es lo que esta entre dos timestamps consecutivos (excluyendo el numero
    secuencial que precede al siguiente, si existe).
    """
    text = text.lstrip("﻿")
    lines = text.splitlines()
    n = len(lines)
    time_re = re.compile(
        r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})"
    )

    # Indices de todas las lineas que contienen una linea de timestamp.
    ts_indices = [i for i in range(n) if time_re.search(lines[i])]

    cues: List[Cue] = []
    for k, ts_i in enumerate(ts_indices):
        m = time_re.search(lines[ts_i])
        try:
            start = time_str_to_ms(m.group(1))
            end = time_str_to_ms(m.group(2))
        except ValueError:
            continue
        # Texto: desde la linea siguiente al timestamp hasta justo antes del
        # proximo cue. Si la linea inmediatamente anterior al proximo timestamp
        # es un numero secuencial puro, hay que excluirla del texto.
        if k + 1 < len(ts_indices):
            next_ts = ts_indices[k + 1]
            text_end = next_ts
            if next_ts >= 1 and lines[next_ts - 1].strip().isdigit():
                text_end = next_ts - 1
        else:
            text_end = n
        text_lines = lines[ts_i + 1 : text_end]
        # Sacar blank lines de cola (separador entre cues)
        while text_lines and text_lines[-1].strip() == "":
            text_lines.pop()
        cues.append(Cue(start, end, "\n".join(text_lines).strip()))
    return cues


def parse_vtt(text: str) -> List[Cue]:
    """Parsea contenido WebVTT a lista de Cue."""
    text = text.lstrip("﻿")
    cues: List[Cue] = []
    # Quitar header WEBVTT y cualquier metadata inicial
    lines_all = text.splitlines()
    # Encuentra la primera linea de tiempo.
    # Acepta tanto HH:MM:SS.mmm como MM:SS.mmm (la parte de horas es opcional en VTT).
    time_re = re.compile(
        r"((?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{1,3})\s*-->\s*((?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{1,3})"
    )
    i = 0
    n = len(lines_all)
    while i < n:
        line = lines_all[i].strip()
        m = time_re.search(line)
        if m:
            start = time_str_to_ms(m.group(1))
            end = time_str_to_ms(m.group(2))
            i += 1
            text_lines = []
            while i < n and lines_all[i].strip() != "":
                text_lines.append(lines_all[i])
                i += 1
            cues.append(Cue(start, end, "\n".join(text_lines).strip()))
        i += 1
    return cues


def _serialize_timed(
    cues: List[Cue],
    time_fn,
    header: Optional[List[str]] = None,
    include_index: bool = True,
) -> str:
    """Helper comun para SRT/VTT: formatea cada cue como 'tiempo --> tiempo' +
    texto + linea en blanco. Diferencias entre formatos:

    - SRT: incluye numero secuencial al inicio de cada bloque, separador ','
    - VTT: lleva 'WEBVTT' como header, separador '.'
    """
    out: List[str] = list(header) if header else []
    for i, c in enumerate(cues, start=1):
        if include_index:
            out.append(str(i))
        out.append(f"{time_fn(c.start_ms)} --> {time_fn(c.end_ms)}")
        out.append(c.text)
        out.append("")
    return "\n".join(out)


def serialize_srt(cues: List[Cue]) -> str:
    return _serialize_timed(cues, ms_to_srt_time, header=None, include_index=True)


def serialize_vtt(cues: List[Cue]) -> str:
    return _serialize_timed(
        cues, ms_to_vtt_time, header=["WEBVTT", ""], include_index=False
    )


def serialize_txt(cues: List[Cue]) -> str:
    """Exporta solo el texto, sin tiempos. Util para revision/correccion."""
    return "\n\n".join(c.text for c in cues) + "\n"


def ms_to_ass_time(ms: int) -> str:
    """Convierte milisegundos a 'H:MM:SS.cc' (formato ASS, centisegundos)."""
    if ms < 0:
        ms = 0
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    cs = (ms % 1000) // 10
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def serialize_ass(cues: List[Cue]) -> str:
    """Genera un archivo ASS basico (Advanced SubStation Alpha)."""
    header = (
        "[Script Info]\n"
        "Title: Subtitulos exportados\n"
        "ScriptType: v4.00+\n"
        "Collisions: Normal\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        "0,0,0,0,100,100,0,0,1,2,2,2,40,40,60,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )
    lines = [header]
    for c in cues:
        # ASS usa \N para nueva linea, comas escapadas no son necesarias en texto
        text = c.text.replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{ms_to_ass_time(c.start_ms)},{ms_to_ass_time(c.end_ms)},"
            f"Default,,0,0,0,,{text}"
        )
    return "\n".join(lines) + "\n"


def parse_ass(text: str) -> List[Cue]:
    """Lectura basica de ASS/SSA: extrae lineas Dialogue."""
    text = text.lstrip("﻿")
    cues: List[Cue] = []
    in_events = False
    fmt_fields: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("[events]"):
            in_events = True
            continue
        if stripped.startswith("[") and in_events and not stripped.lower().startswith("[events]"):
            in_events = False
            continue
        if not in_events:
            continue
        if stripped.lower().startswith("format:"):
            fmt_fields = [s.strip().lower() for s in stripped[7:].split(",")]
            continue
        if stripped.lower().startswith("dialogue:") and fmt_fields:
            # Dialogue: layer, start, end, style, name, ml, mr, mv, effect, text
            payload = stripped[9:].strip()
            # Por spec ASS, Text siempre esta al final del DATA, pero el orden
            # declarado en Format puede tener Text en cualquier posicion.
            # Para parsear bien:
            #   1. Localizar el indice del campo "text" en fmt_fields.
            #   2. Hacer split por la izquierda exactamente text_idx veces
            #      para extraer los campos previos al texto.
            #   3. Lo que sobre, hacer rsplit por la derecha
            #      (n_fields - text_idx - 1) veces para extraer los posteriores.
            #   4. El medio es el texto (puede contener comas).
            n_fields = len(fmt_fields)
            text_idx = fmt_fields.index("text") if "text" in fmt_fields else n_fields - 1
            left = payload.split(",", text_idx)
            if len(left) != text_idx + 1:
                continue
            leading = left[:text_idx]
            rest = left[text_idx]
            n_trailing = n_fields - text_idx - 1
            if n_trailing > 0:
                right = rest.rsplit(",", n_trailing)
                if len(right) != n_trailing + 1:
                    continue
                text_value = right[0]
                trailing = right[1:]
            else:
                text_value = rest
                trailing = []
            parts = leading + [text_value] + trailing
            row = dict(zip(fmt_fields, parts))
            try:
                start = _ass_time_to_ms(row["start"].strip())
                end = _ass_time_to_ms(row["end"].strip())
            except Exception:
                continue
            txt = row.get("text", "").replace("\\N", "\n").replace("\\n", "\n")
            # Quitar tags ASS basicos del tipo {\\b1}
            txt = re.sub(r"\{[^}]*\}", "", txt).strip()
            cues.append(Cue(start, end, txt))
    return cues


def _ass_time_to_ms(s: str) -> int:
    """Convierte 'H:MM:SS.cc' a milisegundos."""
    h, m, rest = s.split(":")
    sec, cs = rest.split(".")
    return int(h) * 3600000 + int(m) * 60000 + int(sec) * 1000 + int(cs) * 10


def _clean_replaced_text(text: str) -> str:
    """Normaliza el texto resultante de un find/replace.

    Se encarga de los efectos colaterales tipicos al reemplazar con string
    vacio o muy corto:
      - Lineas en blanco al principio/fin (ej. "Speaker:\\nHola" -> "\\nHola")
      - Multiples lineas en blanco consecutivas dentro del texto
      - Espacios duplicados causados por reemplazar una palabra entera por nada

    No deberia aplicarse al texto que el usuario escribe manualmente en el
    editor de celda - solo al resultado de operaciones programaticas como
    find/replace.
    """
    # Colapsa 2+ saltos de linea a uno solo
    text = re.sub(r"\n[\s]*\n+", "\n", text)
    # Colapsa 2+ espacios a uno solo
    text = re.sub(r"  +", " ", text)
    # Quita espacios alrededor de saltos de linea
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return text.strip()


def _decode_subtitle_bytes(data: bytes) -> str:
    """Decodifica bytes de un archivo de subtitulos detectando encoding.

    Orden de deteccion:
      1. BOM UTF-16 LE (\\xff\\xfe) - tipico de Notepad de Windows
      2. BOM UTF-16 BE (\\xfe\\xff)
      3. BOM UTF-8 (\\xef\\xbb\\xbf)
      4. UTF-8 sin BOM
      5. Latin-1 (ultimo recurso, nunca falla)

    Antes existia solo el path UTF-8 + Latin-1 fallback, lo que rompia con
    archivos generados por Notepad (UTF-16 LE con BOM): se cargaban como
    Latin-1 silenciosamente y aparecian caracteres basura tipo "H\\x00o\\x00".
    """
    if data.startswith(b"\xff\xfe"):
        return data.decode("utf-16")
    if data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


# ----------------------------------------------------------------------------
# Auto-formato de subtitulos (estandar industria)
# ----------------------------------------------------------------------------
# Estandares Netflix / BBC / general:
#   - Maximo 2 lineas por subtitulo
#   - Maximo 42 caracteres por linea (Netflix); 37 en algunos (BBC)
#   - Lineas balanceadas (longitudes similares)
#   - Cortar en puntos logicos (comas, conjunciones, no a mitad de frase)
#   - Maximo 21 caracteres por segundo de lectura

MAX_LINE_CHARS = 42  # Netflix Style Guide
MAX_LINES = 2


def wrap_two_lines_report(
    text: str, max_chars: int = MAX_LINE_CHARS
) -> Tuple[str, bool]:
    """Divide un texto en hasta 2 lineas balanceadas, max_chars cada una.

    Prioriza cortar en signos de puntuacion (coma, punto y coma, dos puntos)
    cercanos al centro. Si no hay puntuacion, corta en el espacio mas cercano
    al centro buscando balancear las longitudes.

    Devuelve (texto_resultado, fits_in_max). `fits_in_max` es True si todas
    las lineas finales caben en `max_chars`. Es False si alguna linea
    excede `max_chars` (p.ej. una sola palabra mas larga que el limite, o
    un texto que no se puede partir en 2 lineas de ese tamano).
    """
    text = " ".join(text.split())  # normaliza espacios
    if not text:
        return text, True
    if len(text) <= max_chars:
        return text, True

    words = text.split(" ")
    if len(words) < 2:
        # Una sola palabra mas larga que max_chars: no podemos partir.
        return text, False

    PUNCT_BREAKS = (",", ";", ":", ".")
    # Para cada corte posible entre palabras, calcular score (menor = mejor):
    #   - penalidad fuerte por excederse de max_chars en cualquier linea
    #   - penalidad suave por desbalance entre las dos lineas
    #   - bonus si la primera linea termina en signo de puntuacion
    best = None  # (score, line1, line2)
    for i in range(len(words) - 1):
        line1 = " ".join(words[: i + 1])
        line2 = " ".join(words[i + 1 :])
        overflow = max(0, len(line1) - max_chars) + max(0, len(line2) - max_chars)
        balance = abs(len(line1) - len(line2))
        punct_bonus = -8 if words[i].endswith(PUNCT_BREAKS) else 0
        score = overflow * 10 + balance + punct_bonus
        if best is None or score < best[0]:
            best = (score, line1, line2)

    _, l1, l2 = best
    result = l1 + "\n" + l2
    fits = len(l1) <= max_chars and len(l2) <= max_chars
    return result, fits


def wrap_two_lines(text: str, max_chars: int = MAX_LINE_CHARS) -> str:
    """Devuelve solo el texto envuelto en hasta 2 lineas (compat).

    Si necesitas saber si el resultado realmente cumple `max_chars`, usa
    `wrap_two_lines_report` que devuelve tambien un flag.
    """
    result, _fits = wrap_two_lines_report(text, max_chars=max_chars)
    return result


def auto_format_cue_text(text: str, max_chars: int = MAX_LINE_CHARS) -> str:
    """Aplica formato profesional al texto de un cue: normaliza espacios,
    capitaliza primera letra si esta en minuscula sin razon, divide en 2 lineas."""
    # Normaliza espacios y saltos existentes
    text = " ".join(text.replace("\n", " ").split())
    if not text:
        return text
    return wrap_two_lines(text, max_chars=max_chars)


# ----------------------------------------------------------------------------
# Dialogo de Buscar / Reemplazar
# ----------------------------------------------------------------------------

class FindReplaceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Buscar y reemplazar")
        self.setMinimumWidth(380)

        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Texto a buscar")
        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("Reemplazar con")

        self.find_btn = QPushButton("Buscar siguiente")
        self.replace_btn = QPushButton("Reemplazar")
        self.replace_all_btn = QPushButton("Reemplazar todo")
        close_btn = QPushButton("Cerrar")
        close_btn.clicked.connect(self.close)

        self.status = QLabel("")
        self.status.setStyleSheet("color: gray;")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Buscar:"))
        layout.addWidget(self.find_input)
        layout.addWidget(QLabel("Reemplazar:"))
        layout.addWidget(self.replace_input)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.find_btn)
        btn_row.addWidget(self.replace_btn)
        btn_row.addWidget(self.replace_all_btn)
        layout.addLayout(btn_row)
        layout.addWidget(self.status)
        layout.addWidget(close_btn)


# ----------------------------------------------------------------------------
# Editor in-place multilinea (para editar texto en la celda de la tabla)
# ----------------------------------------------------------------------------

class CellTextEditor(QTextEdit):
    """QTextEdit pequeno que se usa como editor in-place dentro de la tabla.

    Comportamiento de teclado (estilo HappyScribe / editores profesionales
    de subtitulos):
    - Enter solo = DIVIDIR el subtitulo en la posicion del cursor (crea nuevo
      subtitulo abajo con el texto que sigue al cursor)
    - Shift+Enter = insertar salto de linea dentro del mismo subtitulo
    - Ctrl+Enter = guardar y cerrar el editor (commit)
    - Esc = cancelar (default de Qt)
    - Backspace al inicio (posicion 0, sin seleccion) = mover la primera palabra
      al subtitulo anterior
    - Delete al final = mover la ultima palabra al subtitulo siguiente
    """

    commit_requested = pyqtSignal()
    backspace_at_start = pyqtSignal()
    delete_at_end = pyqtSignal()
    # Emite (posicion_cursor, texto_actual) para que se divida el cue ahi
    split_at_cursor = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.setUndoRedoEnabled(True)
        self.setStyleSheet(
            "QTextEdit { background-color: #1e1e1e; color: white; "
            "border: 2px solid #4a9eff; border-radius: 3px; "
            "padding: 4px; font-size: 13px; }"
        )

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            mods = event.modifiers()
            if mods & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+Enter = guardar y cerrar
                self.commit_requested.emit()
                return
            elif mods & Qt.KeyboardModifier.ShiftModifier:
                # Shift+Enter = salto de linea dentro del mismo cue
                super().keyPressEvent(event)
                return
            else:
                # Enter solo = dividir el cue en la posicion del cursor
                # (estilo HappyScribe / Aegisub)
                cursor = self.textCursor()
                position = cursor.position()
                current_text = self.toPlainText()
                self.split_at_cursor.emit(position, current_text)
                return
        elif event.key() == Qt.Key.Key_Backspace:
            cursor = self.textCursor()
            if cursor.position() == 0 and not cursor.hasSelection():
                self.backspace_at_start.emit()
                return
        elif event.key() == Qt.Key.Key_Delete:
            cursor = self.textCursor()
            doc_len = len(self.toPlainText())
            if cursor.position() == doc_len and not cursor.hasSelection():
                self.delete_at_end.emit()
                return
        super().keyPressEvent(event)


class MultiLineTextDelegate(QStyledItemDelegate):
    """Delegate que abre un editor multilinea grande al hacer doble-clic
    en la celda de texto del subtitulo.

    Tambien override sizeHint() con cache: la implementacion default de Qt
    hace text layout completo en cada llamada (super lento con wordWrap=True
    y 500+ filas). Con cache, sizeHint es O(1) en hits y O(text_len) en miss.
    Esto es lo que permite que open/replace masivos no bloqueen la UI.
    """

    # Signals para que la ventana principal reaccione a operaciones especiales
    move_word_to_previous = pyqtSignal(int)  # emite la fila
    move_word_to_next = pyqtSignal(int)
    # Emite (fila, posicion_cursor, texto_actual) para dividir el cue
    split_cue_at_cursor = pyqtSignal(int, int, str)

    # Cache de altura indexado por (text, width). Crece linealmente con
    # cantidad de cues unicos vistos. Se limpia en clear_height_cache().
    _MIN_HEIGHT = 28          # 1 linea + padding minimo
    _MAX_HEIGHT = 200         # cap para no romper la UI con textos enormes
    _PAD = 12                 # padding vertical

    def __init__(self, parent=None):
        super().__init__(parent)
        self._height_cache = {}

    def clear_height_cache(self):
        """Llamar cuando cambia el font de la tabla."""
        self._height_cache.clear()

    def createEditor(self, parent, option, index):
        editor = CellTextEditor(parent)
        editor.commit_requested.connect(lambda: self._commit_and_close(editor))
        row = index.row()
        editor.backspace_at_start.connect(
            lambda: self._on_backspace_at_start(editor, row)
        )
        editor.delete_at_end.connect(
            lambda: self._on_delete_at_end(editor, row)
        )
        editor.split_at_cursor.connect(
            lambda pos, text: self._on_split_at_cursor(editor, row, pos, text)
        )
        return editor

    def _commit_and_close(self, editor):
        self.commitData.emit(editor)
        self.closeEditor.emit(editor)

    def _on_backspace_at_start(self, editor, row):
        # Cierra el editor sin guardar cambios y difiere la operacion al
        # siguiente tick del event loop. Esto evita que Qt entre en un estado
        # inconsistente al modificar la tabla mientras un editor sigue activo.
        self.closeEditor.emit(editor)
        QTimer.singleShot(0, lambda: self.move_word_to_previous.emit(row))

    def _on_delete_at_end(self, editor, row):
        self.closeEditor.emit(editor)
        QTimer.singleShot(0, lambda: self.move_word_to_next.emit(row))

    def _on_split_at_cursor(self, editor, row, position, current_text):
        # Cerrar editor sin commit y diferir la division
        self.closeEditor.emit(editor)
        QTimer.singleShot(
            0, lambda: self.split_cue_at_cursor.emit(row, position, current_text)
        )

    def setEditorData(self, editor: CellTextEditor, index):
        text = index.model().data(index, Qt.ItemDataRole.EditRole)
        editor.setPlainText(str(text or ""))
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        editor.setTextCursor(cursor)
        editor.setFocus()

    def setModelData(self, editor: CellTextEditor, model, index):
        model.setData(index, editor.toPlainText(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        rect = option.rect
        min_height = 90
        if rect.height() < min_height:
            rect.setHeight(min_height)
        editor.setGeometry(rect)


# ----------------------------------------------------------------------------
# Transcripcion automatica con faster-whisper
# ----------------------------------------------------------------------------

class TranscriptionWorker(QThread):
    """Ejecuta faster-whisper en un hilo separado para no bloquear la UI."""

    progress = pyqtSignal(int, str)         # porcentaje (0-100), ultimo texto
    # start_ms, end_ms, texto, lista de (texto_palabra, start_ms, end_ms)
    cue_ready = pyqtSignal(int, int, str, list)
    info_ready = pyqtSignal(str, float)     # idioma detectado, duracion segundos
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    # Umbrales de segmentacion inteligente (ajustables a futuro)
    PAUSE_THRESHOLD_MS = 395      # >= esta pausa entre palabras = nuevo subtitulo
    MAX_CUE_CHARS = 100           # safety net si no hay pausas largas (subido para dejar
                                  # que las oraciones lleguen al . ! ? final antes del corte)
    SENTENCE_END_MIN_CHARS = 15   # min chars antes de cortar en . ! ?

    def __init__(self, file_path: str, model_size: str, language, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.model_size = model_size
        self.language = language  # "es", "en", None=auto
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        # ---- Logger detallado del worker (para diagnosticar crashes nativos) ----
        # El log se guarda en %LOCALAPPDATA%\SubFlow\logs\ (Windows) o
        # ~/.local/share/SubFlow/logs/ (otros) para no ensuciar el home del
        # usuario ni filtrar su nombre en logs que vaya a compartir. Modo
        # append: se conservan los intentos anteriores hasta cierto tamaño.
        worker_log_path = os.path.join(_app_log_dir(), "subflow_worker.log")

        def wlog(msg):
            try:
                with open(worker_log_path, "a", encoding="utf-8") as f:
                    f.write(msg + "\n")
                    f.flush()
            except Exception:
                pass

        # Si el log paso de ~1 MB, rotar (renombrar a .1 y empezar de cero).
        try:
            if os.path.exists(worker_log_path) and os.path.getsize(worker_log_path) > LOG_ROTATION_BYTES:
                rot_path = worker_log_path + ".1"
                if os.path.exists(rot_path):
                    os.remove(rot_path)
                os.replace(worker_log_path, rot_path)
        except Exception:
            pass
        wlog(f"=== Worker iniciado ===")
        wlog(f"Modelo: {self.model_size}, idioma: {self.language}, archivo: {self.file_path}")

        try:
            wlog("Importando faster_whisper...")
            from faster_whisper import WhisperModel  # type: ignore
            wlog("Import OK")
        except ImportError as e:
            wlog(f"ImportError: {e}")
            self.failed.emit(
                "faster-whisper no esta instalado.\n\n"
                "Instalalo abriendo PowerShell o CMD y ejecutando:\n\n"
                "    pip install faster-whisper\n\n"
                "Luego vuelve a intentar transcribir."
            )
            return
        except Exception as e:
            wlog(f"Error inesperado importando: {type(e).__name__}: {e}")
            self.failed.emit(f"No se pudo cargar faster-whisper: {e}")
            return
        try:
            # int8 = mejor para CPU sin GPU
            wlog(f"Cargando modelo {self.model_size} (device=cpu, compute_type=int8)...")
            model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            wlog("Modelo cargado OK")
            wlog("Llamando transcribe()...")
            segments, info = model.transcribe(
                self.file_path,
                language=self.language,
                beam_size=5,
                vad_filter=True,           # filtra silencios largos
                vad_parameters={"min_silence_duration_ms": 500},
                word_timestamps=True,      # CLAVE: necesario para detectar pausas
                condition_on_previous_text=False,  # reduce repeticiones/alucinaciones
            )
            wlog("transcribe() retorno generator, iterando segmentos...")
            total_duration = info.duration if info.duration else 0
            self.info_ready.emit(info.language, total_duration)

            # ---- Segmentacion inteligente -----------------------------------
            # Acumula palabras y emite un subtitulo cuando detecta:
            # 1) pausa >= PAUSE_THRESHOLD_MS entre palabras
            # 2) fin de oracion (. ! ?) si el cue ya tiene suficiente texto
            # 3) longitud excede MAX_CUE_CHARS (safety net)
            current_words = []  # lista de objetos word con .start, .end, .word

            def flush():
                if not current_words:
                    return None
                text = "".join(w.word for w in current_words).strip()
                if not text:
                    current_words.clear()
                    return None
                start_ms = int(current_words[0].start * 1000)
                end_ms = int(current_words[-1].end * 1000)
                # Lista de palabras con timestamps para highlight tipo karaoke
                words_data = [
                    (w.word, int(w.start * 1000), int(w.end * 1000))
                    for w in current_words
                ]
                current_words.clear()
                return (start_ms, end_ms, text, words_data)

            def emit_progress(last_text_preview: str, last_end_s: float):
                pct = 0
                if total_duration > 0:
                    pct = max(0, min(100, int(last_end_s / total_duration * 100)))
                self.progress.emit(pct, last_text_preview[:80])

            for segment in segments:
                if self._cancel:
                    break

                # Si por alguna razon no hay word_timestamps, fallback al segmento entero
                if not segment.words:
                    text = segment.text.strip()
                    if text:
                        self.cue_ready.emit(
                            int(segment.start * 1000),
                            int(segment.end * 1000),
                            text,
                            [],  # sin word timestamps
                        )
                        emit_progress(text, segment.end)
                    continue

                for word in segment.words:
                    if self._cancel:
                        break

                    # Decidir si cortar ANTES de agregar esta palabra
                    if current_words:
                        prev_end = current_words[-1].end
                        gap_ms = (word.start - prev_end) * 1000
                        text_so_far = "".join(w.word for w in current_words).strip()

                        should_split = False
                        reason = ""
                        if gap_ms >= self.PAUSE_THRESHOLD_MS:
                            should_split = True
                            reason = "pausa"
                        elif (
                            text_so_far.endswith((".", "!", "?"))
                            and len(text_so_far) >= self.SENTENCE_END_MIN_CHARS
                        ):
                            should_split = True
                            reason = "fin de oracion"
                        elif len(text_so_far) + len(word.word) > self.MAX_CUE_CHARS:
                            should_split = True
                            reason = "longitud"

                        if should_split:
                            flushed = flush()
                            if flushed:
                                start_ms, end_ms, text, words_data = flushed
                                self.cue_ready.emit(start_ms, end_ms, text, words_data)
                                emit_progress(text, end_ms / 1000)

                    current_words.append(word)

            # Emitir el ultimo subtitulo pendiente
            flushed = flush()
            if flushed:
                start_ms, end_ms, text, words_data = flushed
                self.cue_ready.emit(start_ms, end_ms, text, words_data)
                emit_progress(text, end_ms / 1000)

            self.finished_ok.emit()
        except Exception as e:
            self.failed.emit(f"Error durante la transcripcion:\n{e}")


class TranscribeOptionsDialog(QDialog):
    """Pide al usuario el idioma y modelo antes de transcribir."""

    MODEL_INFO = [
        ("tiny",   "Tiny — 75 MB, instantáneo, calidad básica"),
        ("base",   "Base — 150 MB, rápido, calidad aceptable"),
        ("small",  "Small — 500 MB, calidad muy buena (recomendado)"),
        ("medium", "Medium — 1.5 GB, calidad alta, lento"),
        ("large-v3-turbo", "Large Turbo — 1.5 GB, máxima calidad y rápido"),
    ]
    # Tamanos aproximados de cada modelo en bytes (para el mensaje de descarga)
    MODEL_SIZES_GB = {
        "tiny": 0.075, "base": 0.15, "small": 0.5,
        "medium": 1.5, "large-v3-turbo": 1.5, "large-v3": 3.0,
    }

    LANGUAGES = [
        ("es", "Español"),
        ("en", "Inglés"),
        ("pt", "Portugués"),
        ("fr", "Francés"),
        (None, "Detección automática"),
    ]

    def __init__(self, parent=None, file_path: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Crear subtítulos automáticamente")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)

        header = QLabel("<b>Transcripción automática con Whisper</b>")
        header.setStyleSheet("font-size: 14px;")
        layout.addWidget(header)

        info = QLabel(
            "Se generarán subtítulos a partir del audio de:\n"
            f"<i>{os.path.basename(file_path)}</i>\n\n"
            "La primera vez que uses un modelo se descargará automáticamente "
            "(150 MB - 3 GB según el tamaño). La transcripción corre en tu CPU, "
            "sin enviar audio a internet."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #bbb; font-size: 12px;")
        layout.addWidget(info)

        layout.addSpacing(8)
        layout.addWidget(QLabel("Idioma del audio:"))
        self.language_combo = QComboBox()
        for code, label in self.LANGUAGES:
            self.language_combo.addItem(label, code)
        layout.addWidget(self.language_combo)

        layout.addSpacing(4)
        layout.addWidget(QLabel("Tamaño del modelo:"))
        self.model_combo = QComboBox()
        for code, label in self.MODEL_INFO:
            self.model_combo.addItem(label, code)
        self.model_combo.setCurrentIndex(2)  # Small por defecto
        layout.addWidget(self.model_combo)

        # Checkbox de auto-formato
        self.auto_format_check = QCheckBox(
            "Aplicar formato profesional al terminar (2 líneas, máx 42 caracteres)"
        )
        self.auto_format_check.setChecked(True)
        self.auto_format_check.setStyleSheet("margin-top: 8px;")
        layout.addWidget(self.auto_format_check)
        fmt_help = QLabel(
            "Recomendado. Divide cada subtítulo en 2 líneas balanceadas para que "
            "sea fácil leer mientras se escucha el video (estándar Netflix)."
        )
        fmt_help.setWordWrap(True)
        fmt_help.setStyleSheet("color: #888; font-size: 11px; margin-left: 22px;")
        layout.addWidget(fmt_help)

        warn = QLabel(
            "⏱ Tiempo aproximado en CPU: el modelo Small tarda aproximadamente "
            "el mismo tiempo que dura el audio. Medium tarda 2-3 veces más."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #999; font-size: 11px; margin-top: 8px;")
        layout.addWidget(warn)

        btn_row = QHBoxLayout()
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        start = QPushButton("Iniciar transcripción")
        start.setDefault(True)
        start.clicked.connect(self.accept)
        btn_row.addStretch(1)
        btn_row.addWidget(cancel)
        btn_row.addWidget(start)
        layout.addLayout(btn_row)

    def selected_language(self):
        return self.language_combo.currentData()

    def selected_model(self) -> str:
        return self.model_combo.currentData()

    def auto_format_enabled(self) -> bool:
        return self.auto_format_check.isChecked()


class TranscriptionProgressDialog(QDialog):
    """Ventana modal que muestra el progreso de la transcripcion."""

    cancelled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transcribiendo audio...")
        self.setMinimumWidth(500)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # Texto principal de estado (con animacion de puntos al inicio)
        self._base_status_text = "Cargando modelo, esto puede tardar la primera vez"
        self.status_label = QLabel(self._base_status_text + "...")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Barra indeterminada (animacion en loop) hasta que el worker reporte progreso real
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # 0,0 = indeterminada (anima sola)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        # Subtexto: explica que el modelo se descarga la primera vez
        self.hint_label = QLabel(
            "💡 La primera vez que usas un modelo se descarga (75 MB a 1.5 GB).\n"
            "Las siguientes veces será mucho más rápido."
        )
        self.hint_label.setStyleSheet("color: #888; font-size: 11px; font-style: italic;")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        self.preview_label.setStyleSheet(
            "background-color: #1e1e1e; color: #ddd; padding: 8px; "
            "border-radius: 3px; font-style: italic;"
        )
        self.preview_label.setMinimumHeight(60)
        layout.addWidget(self.preview_label)

        self.count_label = QLabel("0 subtítulos generados")
        self.count_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.count_label)

        btn_row = QHBoxLayout()
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addStretch(1)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

        self._cue_count = 0
        self._info_received = False  # se vuelve True cuando ya tenemos duracion total

        # Timer que anima los puntos del texto de estado (... -> .. -> . -> ...)
        # mientras estemos en modo indeterminado. Asi el usuario VE que algo pasa.
        self._dot_state = 0
        self._dots_timer = QTimer(self)
        self._dots_timer.setInterval(DOTS_ANIMATION_MS)
        self._dots_timer.timeout.connect(self._tick_dots)
        self._dots_timer.start()

    def _tick_dots(self):
        # Solo animar mientras estemos en modo "cargando modelo"
        if self._info_received:
            return
        self._dot_state = (self._dot_state + 1) % 4
        dots = "." * (self._dot_state if self._dot_state > 0 else 1)
        # Padding con espacios para que el ancho del texto no cambie
        dots_padded = dots.ljust(3)
        self.status_label.setText(f"{self._base_status_text}{dots_padded}")

    def _on_cancel(self):
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Cancelando...")
        self._dots_timer.stop()
        self.cancelled.emit()

    def on_info(self, detected_lang: str, duration_s: float):
        # Whisper ya empezo a transcribir: cambiamos a barra de progreso real
        self._info_received = True
        self._dots_timer.stop()
        self.hint_label.setVisible(False)  # ya no aplica el hint de descarga
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setValue(0)
        mins = int(duration_s // 60)
        secs = int(duration_s % 60)
        self.status_label.setText(
            f"Transcribiendo... Idioma: {detected_lang}  |  "
            f"Duración: {mins}m {secs}s"
        )

    def on_progress(self, pct: int, text: str):
        self.progress_bar.setValue(pct)
        if text:
            self.preview_label.setText(f"\"{text}...\"")

    def on_cue(self):
        self._cue_count += 1
        self.count_label.setText(f"{self._cue_count} subtítulos generados")


class OpenMediaActionDialog(QDialog):
    """Pregunta al usuario que hacer despues de cargar video/audio."""

    CHOICE_TRANSCRIBE = 1
    CHOICE_LOAD_SUBTITLES = 2
    CHOICE_NOTHING = 3

    def __init__(self, parent=None, file_name: str = "", is_audio: bool = False):
        super().__init__(parent)
        self.setWindowTitle("¿Qué deseas hacer?")
        self.setMinimumWidth(460)
        self.choice = self.CHOICE_NOTHING

        layout = QVBoxLayout(self)

        kind = "audio" if is_audio else "video"
        header = QLabel(f"<b>Cargaste un {kind}:</b> {file_name}")
        header.setStyleSheet("font-size: 13px;")
        header.setWordWrap(True)
        layout.addWidget(header)

        question = QLabel("¿Qué quieres hacer ahora?")
        question.setStyleSheet("font-size: 13px; margin-top: 8px;")
        layout.addWidget(question)

        btn_transcribe = QPushButton("🎙  Crear subtítulos automáticamente\n(transcribir el audio)")
        btn_transcribe.setMinimumHeight(60)
        btn_transcribe.setStyleSheet("text-align: left; padding: 10px;")
        btn_transcribe.clicked.connect(lambda: self._choose(self.CHOICE_TRANSCRIBE))
        layout.addWidget(btn_transcribe)

        btn_load = QPushButton("📂  Cargar archivo de subtítulos existente\n(SRT, VTT, ASS)")
        btn_load.setMinimumHeight(60)
        btn_load.setStyleSheet("text-align: left; padding: 10px;")
        btn_load.clicked.connect(lambda: self._choose(self.CHOICE_LOAD_SUBTITLES))
        layout.addWidget(btn_load)

        btn_skip = QPushButton("Por ahora nada — solo reproducir")
        btn_skip.clicked.connect(lambda: self._choose(self.CHOICE_NOTHING))
        layout.addWidget(btn_skip)

    def _choose(self, c: int):
        self.choice = c
        self.accept()


# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# Timeline visual de cues
# ----------------------------------------------------------------------------

class TimelineWidget(QWidget):
    """Visualizacion temporal de cues debajo del video, estilo HappyScribe.

    - Eje horizontal con marcas de tiempo (auto-tick segun zoom).
    - Bloques rectangulares por cue, ancho proporcional a su duracion.
    - Cursor vertical sincronizado con la posicion del player.
    - Click en bloque: emite cue_clicked(row) + seek_requested(start_ms).
    - Click en zona vacia: emite seek_requested(ms).
    - Wheel: zoom in/out manteniendo el punto bajo el cursor fijo.
    - Auto-scroll cuando el cursor del player se acerca al borde derecho.

    El widget recibe los cues mediante una callable (get_cues_fn) para
    siempre leer el estado actual sin tener que sincronizar set_cues despues
    de cada edicion.
    """

    cue_clicked = pyqtSignal(int)        # emite el row del cue clickeado
    seek_requested = pyqtSignal(int)     # emite ms para hacer seek en el player
    # Drag de un bloque cambia los tiempos del cue. Emite (row, start_ms, end_ms)
    # solo en mouseRelease (para no spamear el undo durante el drag).
    cue_time_changed = pyqtSignal(int, int, int)
    # Mientras se arrastra (no cambia el cue todavia) emite estos para
    # que la app pueda mostrar feedback en otro lado (status bar, etc.).
    cue_drag_preview = pyqtSignal(int, int, int)  # row, start_ms, end_ms

    PX_PER_SECOND_DEFAULT = 60
    PX_PER_SECOND_MIN = 5
    PX_PER_SECOND_MAX = 500
    AXIS_HEIGHT = 18
    BLOCK_PAD = 4
    EDGE_GRAB_PX = 6           # zona en pixels para detectar resize cursor
    MIN_CUE_DURATION_MS = 100  # no permitir colapsar el cue
    SNAP_PX = 4                # snap a vecinos si esta dentro de N pixels

    # Modos de drag
    DRAG_NONE = 0
    DRAG_MOVE = 1
    DRAG_RESIZE_LEFT = 2
    DRAG_RESIZE_RIGHT = 3

    def __init__(self, get_cues_fn, parent=None):
        super().__init__(parent)
        self._get_cues = get_cues_fn
        self._position_ms = 0
        self._duration_ms = 0
        self._selected_row = -1
        self._px_per_second = self.PX_PER_SECOND_DEFAULT
        self._view_start_ms = 0
        self._auto_follow = True
        # Estado de drag
        self._drag_mode = self.DRAG_NONE
        self._drag_row = -1
        self._drag_orig_start = 0
        self._drag_orig_end = 0
        self._drag_anchor_ms = 0
        self._drag_current_start = 0
        self._drag_current_end = 0
        self.setMinimumHeight(70)
        self.setMaximumHeight(90)
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #1a1a1a;")

    # ---- API publica ----

    def set_position(self, ms: int):
        self._position_ms = max(0, ms)
        if self._auto_follow:
            self._maybe_scroll_to_follow()
        self.update()

    def set_duration(self, ms: int):
        self._duration_ms = max(0, ms)
        self.update()

    def set_selected_row(self, row: int):
        if row != self._selected_row:
            self._selected_row = row
            # Centrar la vista en el cue seleccionado si esta fuera de pantalla
            cues = self._get_cues()
            if 0 <= row < len(cues):
                cue = cues[row]
                if not (self._view_start_ms <= cue.start_ms <= self._view_end_ms()):
                    self._view_start_ms = max(0, cue.start_ms - self._view_span_ms() // 4)
            self.update()

    def refresh(self):
        """Llamar despues de cualquier cambio en self.cues para repintar."""
        self.update()

    # ---- Helpers de geometria ----

    def _view_span_ms(self) -> int:
        return int(self.width() * 1000 / self._px_per_second)

    def _view_end_ms(self) -> int:
        return self._view_start_ms + self._view_span_ms()

    def _ms_to_x(self, ms: int) -> int:
        return int((ms - self._view_start_ms) / 1000 * self._px_per_second)

    def _x_to_ms(self, x: float) -> int:
        return max(0, int(self._view_start_ms + x * 1000 / self._px_per_second))

    def _maybe_scroll_to_follow(self):
        cursor_x = self._ms_to_x(self._position_ms)
        w = self.width()
        if cursor_x < 0 or cursor_x > w * 0.85:
            # Recentrar dejando 30% a la izquierda del cursor
            self._view_start_ms = max(
                0, self._position_ms - int(w * 0.3 * 1000 / self._px_per_second)
            )

    @staticmethod
    def _format_time(ms: int) -> str:
        s = ms // 1000
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _pick_tick_interval(self) -> int:
        """Elige un intervalo de tick que de ~80 px de separacion."""
        target_ms = 80 * 1000 / self._px_per_second
        for candidate in (500, 1000, 2000, 5000, 10000, 30000, 60000, 300000, 600000):
            if candidate >= target_ms:
                return candidate
        return 1200000

    # ---- Pintado ----

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            self._draw_axis(painter)
            self._draw_cue_blocks(painter)
            self._draw_player_cursor(painter)
        finally:
            painter.end()

    def _draw_axis(self, painter: QPainter):
        # Linea base de la track
        painter.setPen(QPen(QColor("#444"), 1))
        painter.drawLine(0, self.AXIS_HEIGHT, self.width(), self.AXIS_HEIGHT)
        # Tick marks + labels
        tick_ms = self._pick_tick_interval()
        first_tick = (self._view_start_ms // tick_ms) * tick_ms
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        t = first_tick
        while True:
            x = self._ms_to_x(t)
            if x > self.width():
                break
            if x >= -50:
                painter.setPen(QPen(QColor("#666"), 1))
                painter.drawLine(x, self.AXIS_HEIGHT - 4, x, self.AXIS_HEIGHT)
                painter.setPen(QColor("#aaa"))
                painter.drawText(x + 3, self.AXIS_HEIGHT - 4, self._format_time(t))
            t += tick_ms

    def _draw_cue_blocks(self, painter: QPainter):
        cues = self._get_cues()
        if not cues:
            return
        track_top = self.AXIS_HEIGHT + self.BLOCK_PAD
        track_h = self.height() - self.AXIS_HEIGHT - 2 * self.BLOCK_PAD
        view_end = self._view_end_ms()
        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)
        fm = QFontMetrics(font)
        for i, c in enumerate(cues):
            # Si este cue se esta arrastrando, dibujarlo en su posicion preview
            if i == self._drag_row and self._drag_mode != self.DRAG_NONE:
                cue_start = self._drag_current_start
                cue_end = self._drag_current_end
            else:
                cue_start = c.start_ms
                cue_end = c.end_ms
            # Skip cues totalmente fuera de la vista
            if cue_end < self._view_start_ms or cue_start > view_end:
                continue
            x1 = self._ms_to_x(cue_start)
            x2 = self._ms_to_x(cue_end)
            x1 = max(0, x1)
            x2 = min(self.width(), x2)
            w = x2 - x1
            if w < 2:
                continue
            # Color del bloque: arrastrado > seleccionado > normal
            if i == self._drag_row and self._drag_mode != self.DRAG_NONE:
                fill = QColor("#ec4899")  # rosa para drag activo
                border = QColor("#f9a8d4")
            elif i == self._selected_row:
                fill = QColor("#4a90d9")
                border = QColor("#6bb6ff")
            else:
                fill = QColor("#2c5985")
                border = QColor("#1a3a5a")
            painter.fillRect(x1, track_top, w, track_h, fill)
            painter.setPen(QPen(border, 1))
            painter.drawRect(x1, track_top, w, track_h)
            # Texto del cue (truncado con elipsis)
            if w > 25:
                text = c.text.replace("\n", " ").strip()
                elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, w - 8)
                painter.setPen(QColor("#fff"))
                text_rect = QRect(x1 + 4, track_top, w - 8, track_h)
                painter.drawText(
                    text_rect,
                    int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                    elided,
                )

    def _draw_player_cursor(self, painter: QPainter):
        x = self._ms_to_x(self._position_ms)
        if 0 <= x <= self.width():
            painter.setPen(QPen(QColor("#ff4444"), 2))
            painter.drawLine(x, 0, x, self.height())

    # ---- Mouse: detectar zona del cue (centro vs bordes) para drag/resize ----

    def _find_cue_at_x(self, x: float):
        """Devuelve (row, mode) donde mode es DRAG_MOVE/RESIZE_LEFT/RESIZE_RIGHT
        o (-1, DRAG_NONE) si no hay cue bajo el mouse.

        Si el x cae dentro de EDGE_GRAB_PX de un borde, devuelve modo resize.
        Si cae en el centro, devuelve modo move.
        """
        cues = self._get_cues()
        ms = self._x_to_ms(x)
        for i, c in enumerate(cues):
            if c.start_ms <= ms <= c.end_ms:
                left_x = self._ms_to_x(c.start_ms)
                right_x = self._ms_to_x(c.end_ms)
                if abs(x - left_x) <= self.EDGE_GRAB_PX:
                    return (i, self.DRAG_RESIZE_LEFT)
                if abs(x - right_x) <= self.EDGE_GRAB_PX:
                    return (i, self.DRAG_RESIZE_RIGHT)
                return (i, self.DRAG_MOVE)
        return (-1, self.DRAG_NONE)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        row, mode = self._find_cue_at_x(x)
        if row < 0:
            # Click en zona vacia: seek
            self.seek_requested.emit(self._x_to_ms(x))
            return
        # Click en bloque: empezamos drag (puede ser un click corto sin mover)
        cue = self._get_cues()[row]
        self._drag_mode = mode
        self._drag_row = row
        self._drag_orig_start = cue.start_ms
        self._drag_orig_end = cue.end_ms
        self._drag_anchor_ms = self._x_to_ms(x)
        self._drag_current_start = cue.start_ms
        self._drag_current_end = cue.end_ms
        # Auto-follow off mientras se arrastra
        self._auto_follow = False
        # Notificar seleccion al arrancar (igual que el click clasico)
        self.cue_clicked.emit(row)

    def mouseMoveEvent(self, event):
        x = event.position().x()
        # Sin drag activo: actualizar cursor segun zona bajo el mouse
        if self._drag_mode == self.DRAG_NONE:
            row, mode = self._find_cue_at_x(x)
            if mode == self.DRAG_MOVE:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            elif mode in (self.DRAG_RESIZE_LEFT, self.DRAG_RESIZE_RIGHT):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        # Drag activo: calcular nuevos tiempos
        ms = self._x_to_ms(x)
        delta = ms - self._drag_anchor_ms
        new_start = self._drag_orig_start
        new_end = self._drag_orig_end
        if self._drag_mode == self.DRAG_MOVE:
            new_start = self._drag_orig_start + delta
            new_end = self._drag_orig_end + delta
            # No permitir negativo
            if new_start < 0:
                shift = -new_start
                new_start += shift
                new_end += shift
        elif self._drag_mode == self.DRAG_RESIZE_LEFT:
            new_start = self._drag_orig_start + delta
            new_start = max(0, new_start)
            # Respetar duracion minima
            if new_end - new_start < self.MIN_CUE_DURATION_MS:
                new_start = new_end - self.MIN_CUE_DURATION_MS
        elif self._drag_mode == self.DRAG_RESIZE_RIGHT:
            new_end = self._drag_orig_end + delta
            if new_end - new_start < self.MIN_CUE_DURATION_MS:
                new_end = new_start + self.MIN_CUE_DURATION_MS

        self._drag_current_start = new_start
        self._drag_current_end = new_end
        # Cambiar cursor a closed hand mientras se mueve
        if self._drag_mode == self.DRAG_MOVE:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.cue_drag_preview.emit(self._drag_row, new_start, new_end)
        self.update()

    def mouseReleaseEvent(self, event):
        if self._drag_mode == self.DRAG_NONE:
            return
        # Si el usuario solo clickeo sin arrastrar (no movio en ms), tratamos
        # como click clasico: seek al inicio del cue
        if (self._drag_current_start == self._drag_orig_start
                and self._drag_current_end == self._drag_orig_end):
            self.seek_requested.emit(self._drag_orig_start)
        else:
            # Hubo movimiento real: commitear el cambio
            self.cue_time_changed.emit(
                self._drag_row,
                self._drag_current_start,
                self._drag_current_end,
            )
        # Reset estado de drag
        self._drag_mode = self.DRAG_NONE
        self._drag_row = -1
        self.setCursor(Qt.CursorShape.ArrowCursor)
        # Re-activar auto-follow despues de un breve delay (no inmediato
        # para no patear la vista justo al soltar)
        QTimer.singleShot(2000, self._reactivate_auto_follow)
        self.update()

    def wheelEvent(self, event):
        # Zoom manteniendo el punto bajo el cursor del mouse en su posicion x.
        x = event.position().x()
        cursor_ms = self._x_to_ms(x)
        delta = event.angleDelta().y()
        if delta > 0:
            new_pps = min(self.PX_PER_SECOND_MAX, self._px_per_second * 1.25)
        else:
            new_pps = max(self.PX_PER_SECOND_MIN, self._px_per_second / 1.25)
        if new_pps == self._px_per_second:
            return
        self._px_per_second = new_pps
        # Recalcular view_start para que cursor_ms quede en x
        self._view_start_ms = max(0, cursor_ms - int(x * 1000 / self._px_per_second))
        # Desactivar auto-follow temporalmente cuando el usuario interactua
        self._auto_follow = False
        QTimer.singleShot(2000, self._reactivate_auto_follow)
        self.update()

    def _reactivate_auto_follow(self):
        self._auto_follow = True


# ----------------------------------------------------------------------------
# Ventana principal
# ----------------------------------------------------------------------------

class SubtitleEditor(QMainWindow):
    # Indices de columna - usar SIEMPRE estas constantes en vez de numeros
    # crudos para no acoplar el codigo a la posicion fisica de las columnas.
    COL_INDEX = 0
    COL_START = 1
    COL_END = 2
    COL_DURATION = 3
    COL_CPS = 4
    COL_TEXT = 5
    COLS = ["#", "Inicio", "Fin", "Duracion", "c/s", "Texto"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SubFlow")
        self.resize(1400, 850)
        self._apply_global_style()

        self.cues: List[Cue] = []
        self.current_subtitle_path: Optional[str] = None
        self.current_video_path: Optional[str] = None
        self._updating_table = False  # evita recursion al editar celda
        self._last_search_row = -1
        self._current_overlay_row = -1
        self._current_overlay_word_idx = -1
        # Pila de deshacer/rehacer. Cada entrada es (snapshot de cues, fila seleccionada).
        self._undo_stack: List[Tuple[List[Cue], int]] = []
        self._redo_stack: List[Tuple[List[Cue], int]] = []
        self._max_undo = UNDO_HISTORY_LIMIT
        # Cambios sin guardar (para preguntar al cerrar)
        self._modified = False

        self._build_video()
        self._build_table()
        self._build_layout()
        self._build_toolbar()
        self._build_shortcuts()

        # Filtro de eventos a nivel de aplicacion para atajos de reproduccion
        # (espacio, flechas) que funcionen sin importar donde este el foco,
        # excepto cuando el usuario esta escribiendo en un editor de texto.
        QApplication.instance().installEventFilter(self)

        # Timer para resaltar el subtitulo actual durante reproduccion
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(POLL_HIGHLIGHT_MS)
        self.poll_timer.timeout.connect(self._highlight_current_cue)
        self.poll_timer.start()

        self.statusBar().showMessage(
            "Listo. Carga un video/audio (Ctrl+O). Un clic en cualquier celda de texto la abre para editar."
        )

    # ------------------------------- UI build -------------------------------

    def _apply_global_style(self):
        """Tema oscuro consistente para toda la app."""
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background-color: #232323; color: #e8e8e8; }
            QToolBar { background-color: #2b2b2b; border-bottom: 1px solid #1a1a1a; padding: 4px; spacing: 4px; }
            QToolBar QToolButton {
                background-color: #3a3a3a; color: #e8e8e8; border: 1px solid #4a4a4a;
                padding: 6px 12px; border-radius: 4px; font-size: 13px;
            }
            QToolBar QToolButton:hover { background-color: #4a4a4a; border-color: #5a5a5a; }
            QToolBar QToolButton:pressed { background-color: #2a2a2a; }
            QToolBar::separator { background-color: #4a4a4a; width: 1px; margin: 4px 6px; }

            QTableWidget {
                background-color: #1e1e1e; color: #e8e8e8;
                gridline-color: #3a3a3a; border: 1px solid #3a3a3a;
                alternate-background-color: #262626; font-size: 13px;
                selection-background-color: #2563eb; selection-color: white;
            }
            QTableWidget::item { padding: 6px; }
            QHeaderView::section {
                background-color: #2b2b2b; color: #e8e8e8;
                padding: 6px; border: none; border-right: 1px solid #1a1a1a;
                border-bottom: 1px solid #1a1a1a; font-weight: bold;
            }

            QStatusBar { background-color: #2b2b2b; color: #ccc; }
            QSlider::groove:horizontal { background: #3a3a3a; height: 6px; border-radius: 3px; }
            QSlider::handle:horizontal {
                background: #4a9eff; width: 14px; height: 14px;
                margin: -4px 0; border-radius: 7px;
            }
            QSlider::handle:horizontal:hover { background: #6bb0ff; }

            QPushButton { background-color: #3a3a3a; color: #e8e8e8; border: 1px solid #4a4a4a;
                padding: 6px 12px; border-radius: 4px; }
            QPushButton:hover { background-color: #4a4a4a; }

            QMessageBox, QDialog { background-color: #2b2b2b; color: #e8e8e8; }
            QLineEdit { background-color: #1e1e1e; color: #e8e8e8; border: 1px solid #4a4a4a;
                padding: 4px; border-radius: 3px; }
            QLineEdit:focus { border-color: #4a9eff; }
            QSplitter::handle { background-color: #1a1a1a; }
            QSplitter::handle:horizontal { width: 4px; }
            QSplitter::handle:vertical { height: 4px; }
            """
        )

    def _build_video(self):
        # Widget de video real
        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background-color: black;")

        # Etiqueta de subtitulo superpuesta sobre el video
        self.subtitle_overlay = QLabel(self.video_widget)
        self.subtitle_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_overlay.setWordWrap(True)
        self.subtitle_overlay.setStyleSheet(
            "QLabel {"
            "  color: white;"
            "  background-color: rgba(0, 0, 0, 160);"
            "  font-size: 20px;"
            "  font-weight: 600;"
            "  padding: 8px 14px;"
            "  border-radius: 6px;"
            "}"
        )
        self.subtitle_overlay.hide()

        # Panel de "modo audio" (cuando no hay video)
        self.audio_panel = QWidget()
        self.audio_panel.setStyleSheet("background-color: #1e1e1e;")
        audio_layout = QVBoxLayout(self.audio_panel)
        audio_icon = QLabel("\U0001F3B5")  # nota musical
        audio_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        audio_icon.setStyleSheet("font-size: 96px; color: #888;")
        self.audio_filename_label = QLabel("Modo audio")
        self.audio_filename_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.audio_filename_label.setStyleSheet("color: #ddd; font-size: 16px;")
        self.audio_subtitle_label = QLabel("")
        self.audio_subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.audio_subtitle_label.setWordWrap(True)
        self.audio_subtitle_label.setStyleSheet(
            "color: white; font-size: 22px; font-weight: 600; padding: 16px;"
        )
        audio_layout.addStretch(1)
        audio_layout.addWidget(audio_icon)
        audio_layout.addWidget(self.audio_filename_label)
        audio_layout.addSpacing(20)
        audio_layout.addWidget(self.audio_subtitle_label)
        audio_layout.addStretch(2)

        # Stack: alterna entre video y panel de audio segun el archivo cargado
        self.media_stack = QStackedWidget()
        self.media_stack.addWidget(self.video_widget)  # indice 0
        self.media_stack.addWidget(self.audio_panel)   # indice 1

        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.audio_output.setVolume(0.7)

        # Controles de reproduccion
        self.play_btn = QPushButton()
        self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_btn.clicked.connect(self.toggle_play)

        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self.player.setPosition)

        self.time_label = QLabel("00:00:00 / 00:00:00")
        self.time_label.setMinimumWidth(160)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setMaximumWidth(120)
        self.volume_slider.valueChanged.connect(
            lambda v: self.audio_output.setVolume(v / 100)
        )

        # Selector de velocidad de reproduccion (estilo YouTube)
        self.speed_combo = QComboBox()
        for rate, label in [
            (0.5,  "0.5x"),
            (0.75, "0.75x"),
            (1.0,  "1x"),
            (1.25, "1.25x"),
            (1.5,  "1.5x"),
            (1.75, "1.75x"),
            (2.0,  "2x"),
        ]:
            self.speed_combo.addItem(label, rate)
        self.speed_combo.setCurrentIndex(2)  # 1x por defecto
        self.speed_combo.setMaximumWidth(80)
        self.speed_combo.setToolTip("Velocidad de reproducción")
        self.speed_combo.currentIndexChanged.connect(self._on_speed_changed)

        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_state_changed)
        self.player.errorOccurred.connect(self._on_player_error)
        self.player.hasVideoChanged.connect(self._on_has_video_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._first_frame_shown = False

        # Banda de controles del reproductor (play, slider, tiempo, volumen).
        # La envolvemos en un widget propio con fondo gris oscuro para
        # separarla visualmente del video (arriba) y del preview de subtitulos
        # (abajo).
        self.controls_bar = QWidget()
        self.controls_bar.setObjectName("controlsBar")
        self.controls_bar.setStyleSheet(
            "#controlsBar { background-color: #2b2b2b; "
            "border-top: 1px solid #1a1a1a; border-bottom: 1px solid #1a1a1a; }"
        )
        controls = QHBoxLayout(self.controls_bar)
        controls.setContentsMargins(10, 8, 10, 8)
        controls.setSpacing(8)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.position_slider)
        controls.addWidget(self.time_label)
        controls.addWidget(QLabel("Vel:"))
        controls.addWidget(self.speed_combo)
        controls.addWidget(QLabel("Vol:"))
        controls.addWidget(self.volume_slider)

        # Barra de preview de subtitulos (estilo YouTube). Muestra el subtitulo
        # actual abajo del reproductor, en grande y sobre fondo negro.
        self.subtitle_preview_bar = QLabel(
            "Los subtítulos aparecerán aquí cuando reproduzcas el video."
        )
        self.subtitle_preview_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_preview_bar.setWordWrap(True)
        self.subtitle_preview_bar.setMinimumHeight(90)
        self.subtitle_preview_bar.setMaximumHeight(140)
        self.subtitle_preview_bar.setTextFormat(Qt.TextFormat.RichText)
        self.subtitle_preview_bar.setStyleSheet(
            "QLabel {"
            "  background-color: #000;"
            "  color: white;"
            "  font-size: 20px;"
            "  font-weight: 500;"
            "  padding: 14px 24px;"
            "}"
        )

        # Timeline visual abajo del video (estilo HappyScribe).
        # Lee self.cues mediante un callback para no requerir set_cues
        # explicito despues de cada edicion.
        self.timeline = TimelineWidget(get_cues_fn=lambda: self.cues)
        self.timeline.cue_clicked.connect(self._on_timeline_cue_clicked)
        self.timeline.seek_requested.connect(self.player.setPosition)
        self.timeline.cue_time_changed.connect(self._on_timeline_cue_time_changed)
        self.timeline.cue_drag_preview.connect(self._on_timeline_drag_preview)
        self.player.positionChanged.connect(self.timeline.set_position)
        self.player.durationChanged.connect(self.timeline.set_duration)

        self.video_panel = QWidget()
        v_layout = QVBoxLayout(self.video_panel)
        v_layout.setContentsMargins(0, 0, 0, 0)
        v_layout.setSpacing(0)
        v_layout.addWidget(self.media_stack, stretch=1)
        v_layout.addWidget(self.subtitle_preview_bar)
        v_layout.addWidget(self.controls_bar)
        v_layout.addWidget(self.timeline)

    def resizeEvent(self, event):
        # Reposiciona el overlay cuando la ventana cambia de tamano
        super().resizeEvent(event)
        if self.subtitle_overlay.isVisible():
            self._reposition_overlay()

    def _update_title(self):
        """Actualiza el titulo de la ventana mostrando un asterisco si hay cambios."""
        base = "SubFlow"
        path = self.current_subtitle_path
        name = f" — {os.path.basename(path)}" if path else ""
        dirty = " *" if self._modified else ""
        self.setWindowTitle(f"{base}{name}{dirty}")

    def closeEvent(self, event):
        """Al cerrar, si hay cambios sin guardar pregunta al usuario.

        Tambien se asegura de cancelar y esperar al hilo de transcripcion para
        evitar el crash 'QThread: Destroyed while thread is still running' en
        Windows.
        """
        # 1) Si hay transcripcion corriendo, preguntar y cancelarla limpiamente
        worker = getattr(self, "_transcription_worker", None)
        if worker is not None and worker.isRunning():
            res = QMessageBox.question(
                self,
                "Transcripción en curso",
                "Hay una transcripción corriendo. ¿Cancelarla y cerrar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if res != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            try:
                worker.cancel()
            except Exception:
                pass
            # Esperamos hasta 5s a que el hilo termine ordenadamente. Si no,
            # forzamos terminate() para no quedar colgados al cerrar.
            if not worker.wait(WORKER_WAIT_MS):
                try:
                    worker.terminate()
                    worker.wait(WORKER_TERMINATE_WAIT_MS)
                except Exception:
                    pass

        # 2) Cambios sin guardar
        if not self._modified:
            event.accept()
            return
        ret = QMessageBox.question(
            self,
            "Cambios sin guardar",
            f"Tienes {len(self.cues)} subtítulos con cambios sin guardar.\n\n"
            "¿Qué deseas hacer?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if ret == QMessageBox.StandardButton.Save:
            # Guardar y, si el guardado fue exitoso, cerrar
            had_path = bool(self.current_subtitle_path)
            self.save_subtitles()
            # Si se canceló el dialogo "Guardar como" (no había path previo),
            # current_subtitle_path sigue siendo None: no cerramos.
            if not self.current_subtitle_path and not had_path:
                event.ignore()
                return
            # Si el guardado fallo, _modified seguira en True
            if self._modified:
                event.ignore()
                return
            event.accept()
        elif ret == QMessageBox.StandardButton.Discard:
            event.accept()
        else:
            event.ignore()

    def _reposition_overlay(self):
        w = self.video_widget.width()
        h = self.video_widget.height()
        overlay_w = int(w * 0.9)
        self.subtitle_overlay.setFixedWidth(overlay_w)
        self.subtitle_overlay.adjustSize()
        oh = self.subtitle_overlay.height()
        self.subtitle_overlay.move((w - overlay_w) // 2, max(20, h - oh - 20))

    def _on_has_video_changed(self, has_video: bool):
        # Cambia entre vista de video y vista de audio
        self.media_stack.setCurrentIndex(0 if has_video else 1)

    def _on_media_status_changed(self, status):
        """Maneja transiciones del media player.

        - LoadedMedia: muestra el primer frame del video (sin esto la pantalla
          queda negra hasta que el usuario presiona play).
        - EndOfMedia: retrocede al penultimo frame para que el video no
          desaparezca cuando llega al final.
        """
        if status == QMediaPlayer.MediaStatus.LoadedMedia and not self._first_frame_shown:
            self._first_frame_shown = True
            if self.player.hasVideo():
                self._show_first_frame()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            # Cuando el video llega al final, Qt limpia el video output y
            # queda negro. Retrocedemos 100ms y pausamos para conservar el
            # ultimo frame visible.
            dur = self.player.duration()
            if dur > 200:
                self.player.setPosition(dur - END_OF_MEDIA_REWIND_MS)
            self.player.pause()

    def _show_first_frame(self):
        """Reproduce 80ms con volumen 0 para forzar el render del primer frame."""
        try:
            self._preview_original_volume = self.audio_output.volume()
            self.audio_output.setVolume(0)
            self.player.play()
            QTimer.singleShot(FIRST_FRAME_PREVIEW_MS, self._end_first_frame_preview)
        except Exception as e:
            # Antes era except: pass silencioso. Ahora logueamos para diagnostico
            # (la pantalla negra sigue como fallback, pero queda rastro del fallo).
            _perf_log(f"_show_first_frame FALLO: {type(e).__name__}: {e}")

    def _end_first_frame_preview(self):
        self.player.pause()
        self.player.setPosition(0)
        if hasattr(self, "_preview_original_volume"):
            self.audio_output.setVolume(self._preview_original_volume)

    def _build_table(self):
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.SelectedClicked
            | QTableWidget.EditTrigger.EditKeyPressed
        )
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.verticalHeader().setDefaultSectionSize(TABLE_ROW_HEIGHT)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        # Sorting OFF: con setSortingEnabled(True), cada setItem dispara un
        # re-sort, lo que es O(N log N) por setItem. Mantener False explicito.
        self.table.setSortingEnabled(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_INDEX, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_START, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_END, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_DURATION, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_CPS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_TEXT, QHeaderView.ResizeMode.Stretch)
        # Si cambia el ancho de la columna Texto, invalidamos el cache de
        # alturas del delegate (las keys (text, width) quedaron stale).
        # Throttle: 150ms post-drag.
        self._cache_clear_timer = QTimer(self)
        self._cache_clear_timer.setSingleShot(True)
        self._cache_clear_timer.setInterval(150)
        self._cache_clear_timer.timeout.connect(self._invalidate_height_cache)
        header.sectionResized.connect(self._on_column_resized)

        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        # Tambien escuchamos currentCellChanged para casos donde
        # itemSelectionChanged no se dispara correctamente
        self.table.currentCellChanged.connect(self._on_current_cell_changed)
        # Un clic en la columna de Texto abre el editor inmediatamente.
        # En las demas columnas el clic solo selecciona la fila (comportamiento normal).
        self.table.cellClicked.connect(self._on_cell_clicked)

        # Editor in-place multilinea para la columna de Texto (columna 4).
        # Al hacer doble clic, sale un cuadro grande para editar comodamente.
        self._text_delegate = MultiLineTextDelegate(self.table)
        self._text_delegate.move_word_to_previous.connect(
            self._on_delegate_move_word_to_previous
        )
        self._text_delegate.move_word_to_next.connect(
            self._on_delegate_move_word_to_next
        )
        self._text_delegate.split_cue_at_cursor.connect(
            self._on_delegate_split_cue_at_cursor
        )
        self.table.setItemDelegateForColumn(self.COL_TEXT, self._text_delegate)

    def _build_layout(self):
        # Splitter principal: tabla | video. Toda la edicion se hace en la tabla
        # via doble-clic (texto multilinea en la columna Texto, tiempos en
        # Inicio/Fin).
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.table)
        splitter.addWidget(self.video_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([700, 700])
        self.setCentralWidget(splitter)

    def _build_toolbar(self):
        tb = QToolBar("Principal")
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(text, slot, shortcut=None, visible=True):
            """Crea una accion. Si visible=False, no la mete a la toolbar pero
            sigue funcionando como atajo de teclado registrado en la ventana."""
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            if visible:
                tb.addAction(a)
            else:
                # Sin boton visible, pero el shortcut sigue funcionando
                # cuando la accion esta agregada como child del QMainWindow
                self.addAction(a)
            return a

        # ===== Botones visibles en la toolbar (solo los esenciales) =====
        act("Abrir video/audio", self.open_video, "Ctrl+O")
        act("Abrir subtítulos", self.open_subtitles, "Ctrl+L")
        act("🎙 Transcribir audio", self.start_transcription_flow, "Ctrl+G")
        tb.addSeparator()
        act("Guardar", self.save_subtitles, "Ctrl+S")
        act("Guardar como...", self.save_subtitles_as, "Ctrl+Shift+S")
        tb.addSeparator()
        self.undo_action = act("⟲ Deshacer", self._undo, "Ctrl+Z")
        self.redo_action = act("⟳ Rehacer", self._redo, "Ctrl+Y")
        self.undo_action.setEnabled(False)
        self.redo_action.setEnabled(False)
        tb.addSeparator()
        act("🔍 Buscar/Reemplazar", self.open_find_replace, "Ctrl+F")

        # ===== Acciones ocultas: solo atajos de teclado (no clutter visual) =====
        # Estas funciones siguen disponibles pero no aparecen como botones.
        # Documentadas en el menu Ayuda y en las instrucciones del programa.
        act("Agregar subtitulo", self.add_cue, "Ctrl+N", visible=False)
        act("Eliminar subtitulo", self.delete_cue, "Del", visible=False)
        act("Dividir subtitulo", self.split_cue, "Ctrl+D", visible=False)
        act("Mover palabra al anterior", self.move_first_word_to_previous, "Ctrl+Shift+Up", visible=False)
        act("Mover palabra al siguiente", self.move_last_word_to_next, "Ctrl+Shift+Down", visible=False)
        act("Fusionar con anterior", self.merge_with_previous, "Ctrl+Shift+M", visible=False)
        act("Fusionar con siguiente", self.merge_with_next, "Ctrl+M", visible=False)
        act("Auto-formato", self.auto_format_all, "Ctrl+Shift+F", visible=False)
        act("Saltar a tiempo seleccionado", self.seek_to_selected, "Ctrl+T", visible=False)
        act("Marcar tiempo de inicio", self.set_start_to_current, "Ctrl+[", visible=False)
        act("Marcar tiempo de fin", self.set_end_to_current, "Ctrl+]", visible=False)

    def _build_shortcuts(self):
        # Ctrl+Espacio sigue funcionando como alternativa cuando el foco
        # esta en un editor de texto (Espacio solo escribe espacios)
        sc = QShortcut(QKeySequence("Ctrl+Space"), self)
        sc.activated.connect(self.toggle_play)
        # Atajo alternativo para rehacer
        sc_redo = QShortcut(QKeySequence("Ctrl+Shift+Z"), self)
        sc_redo.activated.connect(self._redo)

    # --------------- Filtro de eventos global (atajos de reproduccion) ------

    def eventFilter(self, obj, event):
        """Intercepta teclas de reproduccion antes de que lleguen al widget enfocado.

        - Espacio = play/pausa
        - Flecha arriba/abajo = volumen +/-
        - Flecha izquierda/derecha = retroceder/avanzar 5 segundos
        Si el foco esta en un editor de texto, dejamos pasar la tecla normalmente.
        """
        if event.type() == QEvent.Type.KeyPress:
            # Solo modificadores normales (sin Ctrl/Alt) para no chocar con otros atajos
            mods = event.modifiers()
            no_modifiers = mods == Qt.KeyboardModifier.NoModifier
            if no_modifiers:
                fw = QApplication.focusWidget()
                in_text_editor = isinstance(fw, (QLineEdit, QTextEdit))
                if not in_text_editor:
                    key = event.key()
                    if key == Qt.Key.Key_Space:
                        self.toggle_play()
                        return True
                    elif key == Qt.Key.Key_Up:
                        self._adjust_volume(+VOLUME_STEP)
                        return True
                    elif key == Qt.Key.Key_Down:
                        self._adjust_volume(-VOLUME_STEP)
                        return True
                    elif key == Qt.Key.Key_Left:
                        self._seek_relative(-SEEK_STEP_MS)
                        return True
                    elif key == Qt.Key.Key_Right:
                        self._seek_relative(+SEEK_STEP_MS)
                        return True
        return super().eventFilter(obj, event)

    def _adjust_volume(self, delta: int):
        new_val = max(0, min(100, self.volume_slider.value() + delta))
        self.volume_slider.setValue(new_val)
        self.statusBar().showMessage(f"Volumen: {new_val}%", 1500)

    def _on_speed_changed(self, idx: int):
        rate = self.speed_combo.itemData(idx)
        if rate is None:
            return
        self.player.setPlaybackRate(float(rate))
        self.statusBar().showMessage(f"Velocidad: {rate}x", 1500)

    def _seek_relative(self, delta_ms: int):
        new_pos = max(0, self.player.position() + delta_ms)
        dur = self.player.duration()
        if dur > 0:
            new_pos = min(new_pos, dur)
        self.player.setPosition(new_pos)

    # ---------------------------- Deshacer / Rehacer ------------------------

    def _snapshot(self) -> Tuple[List[Cue], int]:
        """Captura el estado actual de los subtitulos y la fila seleccionada.

        Optimizacion (B7): COMPARTE las referencias a WordTiming entre
        snapshots. Antes hacia deep-copy de cada WordTiming, lo que con 541
        cues x 30 words avg x 100 snapshots = 1.6M objetos vivos en memoria
        y ~250ms por _push_undo. Ahora: solo copia la LISTA (para que la
        reasignacion de cue.words no afecte snapshots), pero comparte los
        WordTiming. Los WordTiming nunca se mutan en el codigo (verificado:
        cue.words siempre se reasigna con `cue.words = lista_nueva`, nunca
        con `cue.words[i] = ...` o `.append()`), asi que compartir es seguro.

        Resultado: ~16x menos memoria, ~25x mas rapido por snapshot.
        """
        snapshot_cues = [
            Cue(c.start_ms, c.end_ms, c.text, list(c.words))
            for c in self.cues
        ]
        return (snapshot_cues, self.table.currentRow())

    def _push_undo(self):
        """Guarda el estado actual en la pila de deshacer y limpia la de rehacer."""
        self._undo_stack.append(self._snapshot())
        if len(self._undo_stack) > self._max_undo:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._modified = True
        self._update_undo_redo_buttons()
        self._update_title()

    def _diff_apply(self, new_cues: List["Cue"]):
        """Reemplaza self.cues por new_cues redibujando solo las filas que
        realmente cambiaron.

        Usado por undo/redo para evitar el hang de _reload_table sobre tablas
        grandes (con setWordWrap=True, _reload_table en 500+ filas tarda 10+s).

        Casos:
          - Cambio estructural (longitud distinta): full _reload_table
            (raro: solo en add/delete cue).
          - Misma longitud + solo cambio de texto: _bulk_update_text_only (mas
            rapido, no resize).
          - Misma longitud + cambio de timing: _bulk_reload_rows (recrea items).
        """
        if len(new_cues) != len(self.cues):
            self.cues = new_cues
            self._reload_table()
            return
        only_text_changed: List[int] = []
        full_changed: List[int] = []
        for i, (old, new) in enumerate(zip(self.cues, new_cues)):
            if old.start_ms != new.start_ms or old.end_ms != new.end_ms:
                full_changed.append(i)
            elif old.text != new.text:
                only_text_changed.append(i)
            # Cambios solo en `words` no afectan la tabla; karaoke se sincroniza
            # solo con el siguiente tick del overlay.
        self.cues = new_cues
        if full_changed:
            self._bulk_reload_rows(full_changed)
        if only_text_changed:
            self._bulk_update_text_only(only_text_changed)

    def _undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(self._snapshot())
        cues, row = self._undo_stack.pop()
        self._diff_apply(cues)
        if 0 <= row < len(self.cues):
            self.table.selectRow(row)
        self._update_undo_redo_buttons()
        self.statusBar().showMessage(
            f"Deshacer aplicado. Quedan {len(self._undo_stack)} pasos."
        )

    def _redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(self._snapshot())
        cues, row = self._redo_stack.pop()
        self._diff_apply(cues)
        if 0 <= row < len(self.cues):
            self.table.selectRow(row)
        self._update_undo_redo_buttons()
        self.statusBar().showMessage(
            f"Rehacer aplicado. Quedan {len(self._redo_stack)} pasos."
        )

    def _update_undo_redo_buttons(self):
        if hasattr(self, "undo_action"):
            self.undo_action.setEnabled(bool(self._undo_stack))
            self.redo_action.setEnabled(bool(self._redo_stack))

    def _clear_history(self):
        """Limpia ambas pilas. Llamar al cargar un archivo nuevo."""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._update_undo_redo_buttons()

    def _confirm_discard_current_work(self) -> bool:
        """Pregunta al usuario antes de descartar la sesion actual.

        Se llama desde `open_video` y `open_subtitles` cuando ya hay cues
        cargados, para no perder trabajo silenciosamente.

        Devuelve True si se puede proceder (sesion vacia, usuario aceptó,
        o guardo OK). False si el usuario cancelo o el guardado fallo.
        """
        if not self.cues:
            return True
        if not self._modified:
            ret = QMessageBox.question(
                self,
                "Iniciar nuevo proyecto",
                f"Hay {len(self.cues)} subtitulo(s) cargados.\n\n"
                "Se cerraran al abrir el nuevo archivo. ¿Continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            return ret == QMessageBox.StandardButton.Yes
        # Hay cambios sin guardar: mismo flujo que closeEvent
        ret = QMessageBox.question(
            self,
            "Cambios sin guardar",
            f"Tienes {len(self.cues)} subtitulos con cambios sin guardar.\n\n"
            "¿Que deseas hacer antes de abrir el nuevo archivo?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if ret == QMessageBox.StandardButton.Save:
            had_path = bool(self.current_subtitle_path)
            self.save_subtitles()
            # Si cancelo el "Guardar como" (no habia path previo), abortar
            if not self.current_subtitle_path and not had_path:
                return False
            # Si fallo el guardado, _modified sigue True
            if self._modified:
                return False
            return True
        if ret == QMessageBox.StandardButton.Discard:
            return True
        return False  # Cancel

    def _reset_session(self):
        """Limpia la sesion actual: cues, historial, path y titulo.

        No toca el video cargado ni el player (eso lo maneja el caller, ya
        sea para reemplazarlo o conservarlo).
        """
        self.cues = []
        self._reload_table()
        self.current_subtitle_path = None
        self._clear_history()
        self._modified = False
        self._update_title()

    # ----------------------------- Acciones ---------------------------------

    def open_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar video o audio",
            "",
            "Multimedia (*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm *.mp3 *.m4a *.wav *.ogg *.flac *.aac);;"
            "Videos (*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm);;"
            "Audio (*.mp3 *.m4a *.wav *.ogg *.flac *.aac);;"
            "Todos los archivos (*.*)",
        )
        if not path:
            return
        # Si ya hay subtitulos cargados, confirmar antes de descartarlos.
        # Sin esto, abrir un nuevo video pisaba silenciosamente el trabajo.
        if not self._confirm_discard_current_work():
            return
        if self.cues:
            # Confirmacion fue afirmativa: empezar de cero
            self._reset_session()
        self.current_video_path = path
        # Resetear bandera para que el nuevo video muestre su primer frame
        self._first_frame_shown = False
        self.player.setSource(QUrl.fromLocalFile(path))
        # Suposicion inicial basada en la extension. hasVideoChanged confirmara despues.
        ext = os.path.splitext(path)[1].lower()
        is_audio = ext in {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac"}
        if is_audio:
            self.media_stack.setCurrentIndex(1)
            self.audio_filename_label.setText(os.path.basename(path))
        else:
            self.media_stack.setCurrentIndex(0)
        self.statusBar().showMessage(
            f"{'Audio' if is_audio else 'Video'}: {os.path.basename(path)}"
        )

        # Si no hay subtitulos cargados todavia, preguntar al usuario que quiere hacer
        if not self.cues:
            dlg = OpenMediaActionDialog(self, file_name=os.path.basename(path), is_audio=is_audio)
            dlg.exec()
            if dlg.choice == OpenMediaActionDialog.CHOICE_TRANSCRIBE:
                self.start_transcription_flow()
            elif dlg.choice == OpenMediaActionDialog.CHOICE_LOAD_SUBTITLES:
                self.open_subtitles()
            # CHOICE_NOTHING: no hacer nada

    def start_transcription_flow(self):
        """Punto de entrada para transcribir el audio actualmente cargado."""
        if not self.current_video_path:
            QMessageBox.information(
                self,
                "Sin audio",
                "Primero carga un archivo de video o audio "
                "(boton 'Abrir video/audio' o Ctrl+O).",
            )
            return
        # Advertir si hay subtitulos existentes que se van a reemplazar
        if self.cues:
            res = QMessageBox.question(
                self,
                "Reemplazar subtítulos",
                f"Ya hay {len(self.cues)} subtítulos cargados. "
                "Si transcribes ahora, se reemplazarán por el resultado de Whisper.\n\n"
                "Podrás deshacer con Ctrl+Z si te arrepientes.\n\n"
                "¿Continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if res != QMessageBox.StandardButton.Yes:
                return

        opts = TranscribeOptionsDialog(self, file_path=self.current_video_path)
        if opts.exec() != QDialog.DialogCode.Accepted:
            return

        # Snapshot para deshacer
        self._push_undo()
        # Limpiar para acumular las cues nuevas
        self.cues = []
        self._reload_table()
        # Guardar la preferencia de auto-formato para aplicarla al terminar
        self._transcription_auto_format = opts.auto_format_enabled()

        # Configurar el worker
        self._transcription_worker = TranscriptionWorker(
            file_path=self.current_video_path,
            model_size=opts.selected_model(),
            language=opts.selected_language(),
            parent=self,
        )

        # Dialogo de progreso
        self._progress_dialog = TranscriptionProgressDialog(self)

        # Conexiones. Usamos QueuedConnection explicitamente para garantizar
        # que las senales del worker (que corre en otro hilo) se procesen en el
        # event loop de la UI y nunca antes de que exec() del dialogo arranque.
        self._transcription_worker.info_ready.connect(
            self._progress_dialog.on_info, Qt.ConnectionType.QueuedConnection
        )
        self._transcription_worker.progress.connect(
            self._progress_dialog.on_progress, Qt.ConnectionType.QueuedConnection
        )
        self._transcription_worker.cue_ready.connect(
            self._on_transcribed_cue, Qt.ConnectionType.QueuedConnection
        )
        self._transcription_worker.finished_ok.connect(
            self._on_transcription_done, Qt.ConnectionType.QueuedConnection
        )
        self._transcription_worker.failed.connect(
            self._on_transcription_failed, Qt.ConnectionType.QueuedConnection
        )
        self._progress_dialog.cancelled.connect(self._transcription_worker.cancel)

        # Difiere el start() un tick para garantizar que el event loop del
        # dialogo modal ya esta corriendo cuando llegue la primera senal
        # (evita race condition con 'failed' inmediato por ImportError).
        QTimer.singleShot(0, self._transcription_worker.start)
        self._progress_dialog.exec()  # modal hasta que termine o cancele

    def _on_transcribed_cue(self, start_ms: int, end_ms: int, text: str, words_data: list):
        """Se llama por cada segmento que Whisper produce."""
        words = [WordTiming(text=w, start_ms=s, end_ms=e) for (w, s, e) in words_data]
        self.cues.append(Cue(start_ms, end_ms, text, words))
        # Insertar la nueva fila al final sin recargar toda la tabla (mas rapido)
        i = len(self.cues) - 1
        self._updating_table = True
        self.table.setRowCount(len(self.cues))
        self._populate_row(i, self.cues[i])
        self._updating_table = False
        # NO resizeRowToContents: altura fija (TABLE_ROW_HEIGHT) en _build_table.
        if hasattr(self, "_progress_dialog"):
            self._progress_dialog.on_cue()

    def _on_transcription_done(self):
        if hasattr(self, "_progress_dialog"):
            self._progress_dialog.accept()
        # Aplicar auto-formato si el usuario lo solicito
        applied_format = False
        if getattr(self, "_transcription_auto_format", False) and self.cues:
            changes = 0
            for c in self.cues:
                new = auto_format_cue_text(c.text)
                if new != c.text:
                    c.text = new
                    changes += 1
            if changes > 0:
                self._reload_table()
                applied_format = True
        msg = f"Transcripción completa. {len(self.cues)} subtítulos generados."
        if applied_format:
            msg += " Formato profesional aplicado."
        msg += " Guarda con Ctrl+S."
        self.statusBar().showMessage(msg)
        # Quitar la referencia al archivo previo de subtitulos: estos son nuevos
        self.current_subtitle_path = None
        # Marcar como modificado: hay subtitulos generados sin guardar
        self._modified = True
        self._update_title()

    def _on_transcription_failed(self, msg: str):
        if hasattr(self, "_progress_dialog"):
            self._progress_dialog.reject()
        QMessageBox.critical(self, "Error de transcripción", msg)
        self.statusBar().showMessage("Transcripción fallida.")

    def open_subtitles(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar subtitulos",
            "",
            "Subtitulos (*.srt *.vtt *.ass *.ssa);;"
            "SubRip (*.srt);;"
            "WebVTT (*.vtt);;"
            "SubStation Alpha (*.ass *.ssa);;"
            "Todos los archivos (*.*)",
        )
        if not path:
            return
        # Si ya hay subtitulos cargados, confirmar antes de pisarlos.
        if not self._confirm_discard_current_work():
            return
        try:
            with open(path, "rb") as f:
                content = _decode_subtitle_bytes(f.read())
        except OSError as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir el archivo:\n{e}")
            return
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".vtt":
                self.cues = parse_vtt(content)
            elif ext in (".ass", ".ssa"):
                self.cues = parse_ass(content)
            else:
                self.cues = parse_srt(content)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo leer el archivo:\n{e}")
            return
        self.current_subtitle_path = path
        self._clear_history()
        self._modified = False
        self._update_title()
        self._reload_table()
        self.statusBar().showMessage(
            f"Cargados {len(self.cues)} subtitulos desde {os.path.basename(path)}"
        )

    def save_subtitles(self):
        if not self.current_subtitle_path:
            return self.save_subtitles_as()
        self._write_to(self.current_subtitle_path)

    def save_subtitles_as(self):
        # Por defecto, sugiere el formato actual del archivo (o SRT si es nuevo)
        default_name = self.current_subtitle_path or "subtitulos.srt"
        default_ext = os.path.splitext(default_name)[1].lower() or ".srt"
        # Ordena los filtros para que el formato actual sea el primero
        filters_order = {
            ".srt": [
                "SubRip - SRT (*.srt)",
                "WebVTT (*.vtt)",
                "SubStation Alpha (*.ass)",
                "Texto plano (*.txt)",
            ],
            ".vtt": [
                "WebVTT (*.vtt)",
                "SubRip - SRT (*.srt)",
                "SubStation Alpha (*.ass)",
                "Texto plano (*.txt)",
            ],
            ".ass": [
                "SubStation Alpha (*.ass)",
                "SubRip - SRT (*.srt)",
                "WebVTT (*.vtt)",
                "Texto plano (*.txt)",
            ],
            ".ssa": [
                "SubStation Alpha (*.ass)",
                "SubRip - SRT (*.srt)",
                "WebVTT (*.vtt)",
                "Texto plano (*.txt)",
            ],
            ".txt": [
                "Texto plano (*.txt)",
                "SubRip - SRT (*.srt)",
                "WebVTT (*.vtt)",
                "SubStation Alpha (*.ass)",
            ],
        }
        filters = filters_order.get(default_ext, filters_order[".srt"])
        filter_str = ";;".join(filters)

        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Guardar subtitulos - elige formato en el menu de abajo",
            default_name,
            filter_str,
        )
        if not path:
            return
        # Asegura extension correcta segun el filtro elegido
        ext_map = {
            "srt": ".srt", "vtt": ".vtt", "ass": ".ass", "txt": ".txt",
        }
        for key, ext in ext_map.items():
            if key in selected_filter.lower() and not path.lower().endswith(ext):
                path += ext
                break
        self._write_to(path)
        self.current_subtitle_path = path

    def _write_to(self, path: str):
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".vtt":
                content = serialize_vtt(self.cues)
            elif ext in (".ass", ".ssa"):
                content = serialize_ass(self.cues)
            elif ext == ".txt":
                content = serialize_txt(self.cues)
            else:
                content = serialize_srt(self.cues)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            self._modified = False
            self._update_title()
            self.statusBar().showMessage(f"Guardado: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo guardar:\n{e}")

    def add_cue(self):
        # Inserta un nuevo cue despues del seleccionado, o al final
        self._push_undo()
        row = self.table.currentRow()
        pos_ms = self.player.position() or 0
        new_cue = Cue(pos_ms, pos_ms + NEW_CUE_DURATION_MS, "")
        if row < 0 or row >= len(self.cues):
            self.cues.append(new_cue)
            new_idx = len(self.cues) - 1
        else:
            self.cues.insert(row + 1, new_cue)
            new_idx = row + 1
        self._insert_row_at(new_idx, new_cue)
        self.table.selectRow(new_idx)

    def delete_cue(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        self._push_undo()
        # rows ya esta en orden descendente: borramos de abajo hacia arriba
        # para que los indices superiores no se invaliden por los bajos.
        for r in rows:
            if 0 <= r < len(self.cues):
                del self.cues[r]
                self._updating_table = True
                self.table.removeRow(r)
                self._updating_table = False
        # Renumeramos una sola vez al final desde la fila mas baja afectada.
        self._renumber_index_column_from(min(rows))

    def split_cue(self):
        """Divide el cue seleccionado en dos.

        Si hay un editor abierto en la celda de Texto, parte segun la posicion
        del cursor (delegando al mismo flujo que Enter dentro del editor) para
        que el comportamiento sea identico entre Ctrl+D y Enter-en-celda.
        Si no hay editor abierto, parte en la mitad por palabras.
        """
        row = self.table.currentRow()
        if row < 0 or row >= len(self.cues):
            return

        # Si hay un editor abierto sobre la celda de Texto, delegar al split
        # por posicion de cursor (reusa la logica del delegate).
        if self.table.state() == QTableWidget.State.EditingState:
            fw = QApplication.focusWidget()
            if isinstance(fw, CellTextEditor):
                position = fw.textCursor().position()
                current_text = fw.toPlainText()
                # Cerrar el editor sin guardar y diferir para no chocar con Qt
                self._text_delegate.closeEditor.emit(fw)
                QTimer.singleShot(
                    0,
                    lambda r=row, p=position, t=current_text:
                        self._on_delegate_split_cue_at_cursor(r, p, t),
                )
                return

        # Sin editor abierto: comportamiento clasico, partir por mitad de palabras
        self._push_undo()
        c = self.cues[row]
        mid = (c.start_ms + c.end_ms) // 2
        words = c.text.split()
        if len(words) >= 2:
            half = len(words) // 2
            text_a = " ".join(words[:half])
            text_b = " ".join(words[half:])
        else:
            text_a, text_b = c.text, ""
        self.cues[row] = Cue(c.start_ms, mid, text_a)
        self.cues.insert(row + 1, Cue(mid, c.end_ms, text_b))
        self._reload_row(row)
        self._insert_row_at(row + 1, self.cues[row + 1])
        self.table.selectRow(row + 1)

    def open_find_replace(self):
        # Reutiliza un diálogo existente para no acumular instancias huérfanas
        # con señales conectadas (cada open creaba un dialog nuevo y, como close
        # solo oculta, las instancias antiguas seguian vivas).
        existing = getattr(self, "_find_dialog", None)
        if existing is not None:
            try:
                existing.raise_()
                existing.activateWindow()
                existing.find_input.setFocus()
                existing.show()
                return
            except RuntimeError:
                # El dialogo ya fue destruido por Qt: crear uno nuevo
                self._find_dialog = None

        dlg = FindReplaceDialog(self)
        # Cuando el usuario cierra el dialogo, lo descartamos de verdad para
        # liberar las conexiones de senal.
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dlg.destroyed.connect(lambda _=None: setattr(self, "_find_dialog", None))
        dlg.find_btn.clicked.connect(lambda: self._find_next(dlg))
        dlg.replace_btn.clicked.connect(lambda: self._replace_one(dlg))
        dlg.replace_all_btn.clicked.connect(lambda: self._replace_all(dlg))
        self._find_dialog = dlg
        dlg.show()

    def _find_next(self, dlg: FindReplaceDialog):
        needle = dlg.find_input.text()
        if not needle:
            return
        start = self._last_search_row + 1
        for i in range(start, len(self.cues)):
            if needle.lower() in self.cues[i].text.lower():
                self.table.selectRow(i)
                self._last_search_row = i
                dlg.status.setText(f"Encontrado en linea {i + 1}")
                return
        # Buscar desde el principio
        for i in range(0, start):
            if needle.lower() in self.cues[i].text.lower():
                self.table.selectRow(i)
                self._last_search_row = i
                dlg.status.setText(f"Encontrado en linea {i + 1} (desde el inicio)")
                return
        dlg.status.setText("No se encontraron coincidencias")
        self._last_search_row = -1

    def _replace_one(self, dlg: FindReplaceDialog):
        needle = dlg.find_input.text()
        replacement = dlg.replace_input.text()
        row = self.table.currentRow()
        if row < 0 or not needle:
            return
        # Reemplazo case-insensitive del primer match en el cue actual
        pattern = re.compile(re.escape(needle), re.IGNORECASE)
        new_text, n = pattern.subn(replacement, self.cues[row].text, count=1)
        if n:
            self._push_undo()
            self.cues[row].text = new_text
            self._reload_row(row)
            self.table.selectRow(row)
            dlg.status.setText(f"Reemplazado en linea {row + 1}")
        self._find_next(dlg)

    def _replace_all(self, dlg: FindReplaceDialog):
        needle = dlg.find_input.text()
        replacement = dlg.replace_input.text()
        if not needle:
            return
        pattern = re.compile(re.escape(needle), re.IGNORECASE)
        # Un solo pase con subn: cuenta y reemplaza a la vez. Ademas
        # registramos los indices que cambiaron para poder repintar solo
        # esas filas (mucho mas barato que _reload_table en archivos grandes).
        new_texts: List[str] = []
        changed_indices: List[int] = []
        total = 0
        for i, c in enumerate(self.cues):
            new_text, n = pattern.subn(replacement, c.text)
            if n:
                # Limpiar resultados feos: lineas en blanco que quedan al
                # reemplazar (ej. "Speaker:\nQue tal" -> "" + "\nQue tal")
                # y whitespace al inicio/fin.
                new_text = _clean_replaced_text(new_text)
                changed_indices.append(i)
                total += n
            new_texts.append(new_text)
        if total == 0:
            dlg.status.setText("Sin coincidencias")
            return
        self._push_undo()
        for i, new in enumerate(new_texts):
            self.cues[i].text = new
        # B5: limpiar word timestamps de los cues cambiados. Despues de un
        # replace, los WordTiming.text ya no coinciden con cue.text, asi que
        # el karaoke se desfasaria. Mejor sin karaoke en esas filas que con
        # karaoke incorrecto. (El undo restaura los words originales.)
        for i in changed_indices:
            self.cues[i].words = []
        self._bulk_update_text_only(changed_indices)
        dlg.status.setText(f"{total} reemplazos realizados")

    def auto_format_all(self):
        """Aplica auto-formato (2 lineas balanceadas, max 42 chars) a todos los cues."""
        if not self.cues:
            return
        # Detectar cambios y guardar los indices afectados en un solo pase.
        # Solo repintamos las filas que cambiaron (no la tabla entera).
        changed_indices: List[int] = []
        new_texts: List[str] = []
        for i, c in enumerate(self.cues):
            new = auto_format_cue_text(c.text)
            new_texts.append(new)
            if new != c.text:
                changed_indices.append(i)
        if not changed_indices:
            self.statusBar().showMessage("Auto-formato: ya estaban formateados, nada que cambiar.")
            return

        self._push_undo()
        for c, new in zip(self.cues, new_texts):
            c.text = new
        # B5: limpiar word timestamps de los cues cambiados. Auto-formato
        # reorganiza saltos de linea -> los WordTiming dejan de matchear
        # cue.text. (El undo restaura.)
        for i in changed_indices:
            self.cues[i].words = []
        self._bulk_update_text_only(changed_indices)
        self.statusBar().showMessage(
            f"Auto-formato aplicado a {len(changed_indices)} subtítulo(s). Ctrl+Z para deshacer."
        )

    # --------- Operaciones entre cues adyacentes (estilo HappyScribe) -------

    def _on_delegate_move_word_to_previous(self, row: int):
        """Disparado cuando el usuario presiona Backspace al inicio del editor."""
        self.table.selectRow(row)
        self.move_first_word_to_previous()

    def _on_delegate_move_word_to_next(self, row: int):
        """Disparado cuando el usuario presiona Delete al final del editor."""
        self.table.selectRow(row)
        self.move_last_word_to_next()

    def _on_delegate_split_cue_at_cursor(self, row: int, position: int, current_text: str):
        """Disparado cuando el usuario presiona Enter en medio del texto.

        Divide el cue en la posicion del cursor: lo que esta antes se queda en
        el cue actual, lo que sigue se va a un cue nuevo justo abajo. Los
        tiempos se reparten por proporcion de caracteres.
        """
        if not (0 <= row < len(self.cues)):
            return
        cue = self.cues[row]

        before = current_text[:position].rstrip()
        after = current_text[position:].lstrip()

        # Si una de las dos mitades queda vacia, no tiene sentido dividir
        if not before or not after:
            # Solo commit del cambio de texto si hubo modificacion
            if current_text != cue.text:
                self._push_undo()
                cue.text = current_text
                cue.words = []
                self._reload_row(row)
            return

        # Calcular el momento de corte por proporcion de caracteres del texto original
        # (mas estable que usar current_text, que puede tener ediciones recientes)
        total_chars = len(current_text) or 1
        ratio = len(before) / total_chars
        ratio = max(0.05, min(0.95, ratio))  # evita extremos
        original_start = cue.start_ms
        original_end = cue.end_ms
        split_ms = original_start + int((original_end - original_start) * ratio)
        # Asegura que el nuevo cue tenga al menos 100ms
        if split_ms <= original_start + 50:
            split_ms = original_start + SPLIT_GAP_MS
        if split_ms >= original_end - 50:
            split_ms = original_end - SPLIT_GAP_MS

        self._push_undo()

        # Cue actual = texto de antes del cursor
        cue.text = before
        cue.end_ms = split_ms
        cue.words = []  # los timestamps por palabra ya no son confiables

        # Cue nuevo = texto de despues del cursor
        new_cue = Cue(
            start_ms=split_ms,
            end_ms=original_end,
            text=after,
            words=[],
        )
        self.cues.insert(row + 1, new_cue)

        self._reload_row(row)
        self._insert_row_at(row + 1, new_cue)
        self.table.selectRow(row + 1)
        self.statusBar().showMessage(
            f"Subtitulo dividido en 2. Ctrl+Z para deshacer."
        )

    def _words_match_text(self, cue: Cue) -> bool:
        """True si los WordTiming siguen alineados con el texto actual del cue.

        Despues de un replace, auto-format o edicion manual, cue.text puede
        cambiar pero cue.words no. Esta funcion detecta esa desincronizacion
        para que move_first/last_word_to_* no use timestamps stale (B5).
        """
        if not cue.words:
            return False
        plain_words = cue.text.split()
        if len(plain_words) != len(cue.words):
            return False
        # Comparar palabra por palabra (caso strip por whitespace en WordTiming)
        for plain, w in zip(plain_words, cue.words):
            if plain != w.text.strip():
                return False
        return True

    def move_first_word_to_previous(self):
        """Mueve la primera palabra del cue actual al final del cue anterior.

        Si hay word timestamps Y siguen sincronizados con el texto, los tiempos
        se ajustan con precision. Si no, se estima por proporcion de caracteres.

        Optimizacion: solo refresca las 2 filas afectadas, no toda la tabla.
        """
        row = self.table.currentRow()
        if row < 1 or row >= len(self.cues):
            self.statusBar().showMessage("No hay subtitulo anterior al cual mover la palabra.")
            return
        cur = self.cues[row]
        prev = self.cues[row - 1]

        plain_words = cur.text.split()
        if len(plain_words) <= 1:
            return self.merge_with_previous()

        self._push_undo()

        # B5: solo confiar en cur.words si esta sincronizado con cur.text.
        # Si no, los timestamps son stale y caemos al fallback de proporcion.
        if self._words_match_text(cur) and len(cur.words) >= 2:
            first = cur.words[0]
            rest = cur.words[1:]
            prev.text = (prev.text.rstrip() + " " + first.text.strip()).strip()
            prev.words = list(prev.words) + [first]
            prev.end_ms = first.end_ms
            cur.text = "".join(w.text for w in rest).strip()
            cur.words = list(rest)
            cur.start_ms = rest[0].start_ms
        else:
            first_word = plain_words[0]
            rest_text = " ".join(plain_words[1:])
            total_chars = len(cur.text) or 1
            first_chars = len(first_word) + 1
            time_for_first = int(cur.duration_ms * first_chars / total_chars)
            new_boundary = cur.start_ms + time_for_first
            prev.text = (prev.text.rstrip() + " " + first_word).strip()
            prev.end_ms = new_boundary
            prev.words = []
            cur.text = rest_text
            cur.start_ms = new_boundary
            cur.words = []

        # Solo refrescar las 2 filas afectadas (mucho mas rapido que recargar toda la tabla)
        self._reload_row(row - 1)
        self._reload_row(row)
        self.table.selectRow(row)
        self.statusBar().showMessage(
            f"Palabra movida al subtitulo #{row}. Ctrl+Z para deshacer."
        )

    def move_last_word_to_next(self):
        """Mueve la ultima palabra del cue actual al inicio del cue siguiente."""
        row = self.table.currentRow()
        if row < 0 or row >= len(self.cues) - 1:
            self.statusBar().showMessage("No hay subtitulo siguiente al cual mover la palabra.")
            return
        cur = self.cues[row]
        nxt = self.cues[row + 1]

        plain_words = cur.text.split()
        if len(plain_words) <= 1:
            return self.merge_with_next()

        self._push_undo()

        # B5: solo confiar en cur.words si esta sincronizado con cur.text.
        if self._words_match_text(cur) and len(cur.words) >= 2:
            last = cur.words[-1]
            rest = cur.words[:-1]
            nxt.text = (last.text.strip() + " " + nxt.text.lstrip()).strip()
            nxt.words = [last] + list(nxt.words)
            nxt.start_ms = last.start_ms
            cur.text = "".join(w.text for w in rest).strip()
            cur.words = list(rest)
            cur.end_ms = rest[-1].end_ms
        else:
            last_word = plain_words[-1]
            rest_text = " ".join(plain_words[:-1])
            total_chars = len(cur.text) or 1
            last_chars = len(last_word) + 1
            time_for_last = int(cur.duration_ms * last_chars / total_chars)
            new_boundary = cur.end_ms - time_for_last
            nxt.text = (last_word + " " + nxt.text.lstrip()).strip()
            nxt.start_ms = new_boundary
            nxt.words = []
            cur.text = rest_text
            cur.end_ms = new_boundary
            cur.words = []

        # Solo refrescar las 2 filas afectadas
        self._reload_row(row)
        self._reload_row(row + 1)
        self.table.selectRow(row)
        self.statusBar().showMessage(
            f"Palabra movida al subtitulo #{row + 2}. Ctrl+Z para deshacer."
        )

    def merge_with_previous(self):
        """Fusiona el cue actual con el anterior (todo el contenido)."""
        row = self.table.currentRow()
        if row < 1 or row >= len(self.cues):
            self.statusBar().showMessage("No hay subtitulo anterior al cual fusionar.")
            return
        cur = self.cues[row]
        prev = self.cues[row - 1]

        self._push_undo()
        prev.text = (prev.text.rstrip() + " " + cur.text.lstrip()).strip()
        prev.words = list(prev.words) + list(cur.words)
        prev.end_ms = cur.end_ms
        del self.cues[row]
        self._reload_row(row - 1)
        self._remove_row_at(row)
        self.table.selectRow(row - 1)
        self.statusBar().showMessage(
            f"Subtitulos fusionados. Ctrl+Z para deshacer."
        )

    def merge_with_next(self):
        """Fusiona el cue actual con el siguiente (todo el contenido)."""
        row = self.table.currentRow()
        if row < 0 or row >= len(self.cues) - 1:
            self.statusBar().showMessage("No hay subtitulo siguiente al cual fusionar.")
            return
        cur = self.cues[row]
        nxt = self.cues[row + 1]

        self._push_undo()
        cur.text = (cur.text.rstrip() + " " + nxt.text.lstrip()).strip()
        cur.words = list(cur.words) + list(nxt.words)
        cur.end_ms = nxt.end_ms
        del self.cues[row + 1]
        self._reload_row(row)
        self._remove_row_at(row + 1)
        self.table.selectRow(row)
        self.statusBar().showMessage(
            f"Subtitulos fusionados. Ctrl+Z para deshacer."
        )

    def seek_to_selected(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.cues):
            self.player.setPosition(self.cues[row].start_ms)

    def set_start_to_current(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.cues):
            new_ms = self.player.position()
            if new_ms == self.cues[row].start_ms:
                return
            self._push_undo()
            self.cues[row].start_ms = new_ms
            self._reload_row(row)

    def set_end_to_current(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.cues):
            new_ms = self.player.position()
            if new_ms == self.cues[row].end_ms:
                return
            self._push_undo()
            self.cues[row].end_ms = new_ms
            self._reload_row(row)

    # --------------------------- Tabla helpers ------------------------------

    def _reload_table(self):
        """Reconstruye la tabla entera desde self.cues.

        TRUCO CRITICO: setRowCount() debe correr ANTES de bloquear el modelo,
        porque emite rowsInserted que la view necesita para registrar las
        filas. Si bloqueamos antes, la view se queda sin saber que hay filas
        (tabla visualmente vacia aunque los items esten en memoria).

        Despues de setRowCount, bloqueamos model + table + headers para que
        los setItem del loop no cascadeen layouts/repaints (eso era el freeze).

        Al final, desbloqueamos y emitimos UN dataChanged + viewport.update()
        para que la view repinte con los datos nuevos.
        """
        self._updating_table = True

        # 1) setRowCount SIN bloquear el modelo: rowsInserted/rowsRemoved
        #    debe llegar a la view para que sepa cuantas filas hay.
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(self.cues))
        except Exception:
            self.table.setUpdatesEnabled(True)
            raise

        # 2) AHORA bloqueamos modelo + table + headers para que setItem
        #    no dispare cascada de signals.
        model = self.table.model()
        blockers = [
            x for x in (
                self.table,
                model,
                self.table.selectionModel(),
                self.table.horizontalHeader(),
                self.table.verticalHeader(),
            )
            if x is not None
        ]
        for b in blockers:
            b.blockSignals(True)
        try:
            for i, c in enumerate(self.cues):
                self._populate_row(i, c)
        finally:
            for b in blockers:
                b.blockSignals(False)
            self.table.setUpdatesEnabled(True)
            self._updating_table = False

        # 3) Notificar a la view que toda la data cambio.
        if model is not None and len(self.cues) > 0:
            try:
                top_left = model.index(0, 0)
                bottom_right = model.index(len(self.cues) - 1, len(self.COLS) - 1)
                model.dataChanged.emit(top_left, bottom_right)
            except Exception:
                pass
        self.table.viewport().update()

        if hasattr(self, "timeline"):
            self.timeline.refresh()

    def _reload_row(self, i: int):
        self._updating_table = True
        self._populate_row(i, self.cues[i])
        self._updating_table = False
        # NO resizeRowToContents: altura fija (TABLE_ROW_HEIGHT) en _build_table.
        if hasattr(self, "timeline"):
            self.timeline.refresh()

    def _populate_row(self, i: int, c: Cue):
        """Llena las 6 celdas de la fila i con los datos del cue, calculando
        severidad de warnings y aplicando colores/tooltips correspondientes.

        Estandares (Netflix / BBC):
          - duracion < 0.5s -> rojo (muy corto)
          - duracion > 7s -> naranja (muy largo)
          - cps > 21 -> amarillo (lectura rapida)
          - linea > 42 chars -> warning amarillo
        """
        dur_s = c.duration_ms / 1000
        cps = (len(c.text) / dur_s) if dur_s > 0 else 0

        # Detectar problemas y severidad para el icono de la columna #
        problems = []
        long_lines = [len(line) for line in c.text.split("\n") if len(line) > MAX_LINE_CHARS]
        severity = None  # None | "warning" | "error"
        if dur_s < MIN_CUE_DURATION_S:
            problems.append(f"duracion muy corta (<{MIN_CUE_DURATION_S}s)")
            severity = "error"
        elif dur_s > MAX_CUE_DURATION_S:
            problems.append(f"duracion muy larga (>{MAX_CUE_DURATION_S}s)")
            severity = severity or "warning"
        if cps > MAX_READING_CPS:
            problems.append(f"lectura rapida ({cps:.0f} cps)")
            severity = severity or "warning"
        if long_lines:
            problems.append(f"linea(s) >{MAX_LINE_CHARS} chars")
            severity = severity or "warning"

        # COL_INDEX con icono ⚠ + color segun severidad
        idx_text = f"⚠ {i + 1}" if severity else str(i + 1)
        idx_item = QTableWidgetItem(idx_text)
        idx_item.setFlags(idx_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if severity == "error":
            idx_item.setForeground(QBrush(QColor("#ff6b6b")))
        elif severity == "warning":
            idx_item.setForeground(QBrush(QColor("#ffd93d")))
        if problems:
            idx_item.setToolTip(" · ".join(problems))
        self.table.setItem(i, self.COL_INDEX, idx_item)

        self.table.setItem(i, self.COL_START, QTableWidgetItem(ms_to_srt_time(c.start_ms)))
        self.table.setItem(i, self.COL_END, QTableWidgetItem(ms_to_srt_time(c.end_ms)))

        # COL_DURATION con color (rojo si <0.5s, naranja si >7s)
        dur_item = QTableWidgetItem(f"{dur_s:.2f}s")
        dur_item.setFlags(dur_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if dur_s < MIN_CUE_DURATION_S:
            dur_item.setForeground(QBrush(QColor("#ff6b6b")))
            dur_item.setToolTip(f"Muy corto (<{MIN_CUE_DURATION_S}s)")
        elif dur_s > MAX_CUE_DURATION_S:
            dur_item.setForeground(QBrush(QColor("#ffa94d")))
            dur_item.setToolTip(f"Muy largo (>{MAX_CUE_DURATION_S}s)")
        self.table.setItem(i, self.COL_DURATION, dur_item)

        # COL_CPS centrado, amarillo si >21
        cps_item = QTableWidgetItem(f"{cps:.0f}c/s")
        cps_item.setFlags(cps_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        cps_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
        if cps > MAX_READING_CPS:
            cps_item.setForeground(QBrush(QColor("#ffd93d")))
            cps_item.setToolTip(f"Lectura rapida: {cps:.1f} cps (max {MAX_READING_CPS})")
        else:
            cps_item.setToolTip(f"{cps:.1f} caracteres por segundo")
        self.table.setItem(i, self.COL_CPS, cps_item)

        # COL_TEXT con tooltip mostrando conteo por linea (estilo HappyScribe)
        text_item = QTableWidgetItem(c.text)
        line_info = []
        for line in c.text.split("\n"):
            n = len(line)
            mark = " ⚠" if n > MAX_LINE_CHARS else ""
            line_info.append(f"  {n:>3}c{mark}  {line}")
        tooltip = "Lineas:\n" + "\n".join(line_info)
        if problems:
            tooltip += "\n\n⚠ " + " · ".join(problems)
        text_item.setToolTip(tooltip)
        if cps > MAX_READING_CPS:
            text_item.setForeground(QBrush(QColor("#ffd93d")))
        self.table.setItem(i, self.COL_TEXT, text_item)

    def _on_column_resized(self, col: int, old: int, new: int):
        """Si cambia el ancho de la columna Texto, las alturas cacheadas
        (indexadas por (text, width)) quedan stale. Throttle para no
        spamear durante un drag continuo.
        """
        if col == self.COL_TEXT:
            self._cache_clear_timer.start()

    def _invalidate_height_cache(self):
        """Limpia el cache del delegate y fuerza a Qt a re-medir filas.
        Llamado tras un drag de columna que cambio el ancho.
        """
        if hasattr(self, "_text_delegate"):
            self._text_delegate.clear_height_cache()
        # ResizeToContents recalcula al cambiar la geometria.
        self.table.viewport().update()

    # ------------------------------------------------------------------
    # Helpers de mutacion puntual de la tabla (alternativa a _reload_table).
    #
    # _reload_table reconstruye TODA la tabla (setRowCount + repopulate +
    # resizeRowsToContents). Para archivos con miles de cues eso bloquea la
    # UI varios segundos por cada edicion. Estos helpers permiten mutar 1-2
    # filas sin tocar el resto.
    # ------------------------------------------------------------------

    def _renumber_index_column_from(self, start_row: int):
        """Actualiza el numero secuencial de la columna # desde start_row.

        Necesario despues de insertar/borrar filas. Solo toca COL_INDEX (mucho
        mas barato que repopular toda la fila). Preserva el prefijo de warning
        '⚠' si la fila ya lo tenia.
        """
        self._updating_table = True
        for i in range(start_row, len(self.cues)):
            item = self.table.item(i, self.COL_INDEX)
            if item is None:
                item = QTableWidgetItem(str(i + 1))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(i, self.COL_INDEX, item)
            else:
                # Preservar el prefijo "⚠ " si lo tenia
                prefix = "⚠ " if item.text().startswith("⚠") else ""
                item.setText(f"{prefix}{i + 1}")
        self._updating_table = False

    def _insert_row_at(self, i: int, cue: Cue):
        """Inserta una fila en la posicion i sin reconstruir el resto.

        Asume que self.cues YA tiene el cue en la posicion i (la lista se
        actualiza primero por el caller, esta funcion solo sincroniza la tabla).
        """
        self._updating_table = True
        self.table.insertRow(i)
        self._populate_row(i, cue)
        self._updating_table = False
        # NO resizeRowToContents: altura fija (TABLE_ROW_HEIGHT) en _build_table.
        # Las filas que se desplazaron hacia abajo conservan numero stale en col 0
        self._renumber_index_column_from(i + 1)
        if hasattr(self, "timeline"):
            self.timeline.refresh()

    def _remove_row_at(self, i: int):
        """Borra la fila i sin reconstruir el resto.

        Asume que self.cues ya NO tiene el cue en i (la lista se actualiza
        primero por el caller, esta funcion solo sincroniza la tabla).
        """
        self._updating_table = True
        self.table.removeRow(i)
        self._updating_table = False
        # Las filas que subieron una posicion conservan numero stale en col 0
        self._renumber_index_column_from(i)
        if hasattr(self, "timeline"):
            self.timeline.refresh()

    def _all_signal_blockers(self):
        """Helper: lista de objetos cuyos signals bloquear en bulk SIN cambio
        de filas (replace, auto-formato, etc.).

        Aca SI incluimos el modelo: como no cambia rowCount, no necesitamos
        que rowsInserted llegue. Bloquear el modelo evita la cascada de
        dataChanged que disparaba el freeze.

        Para operaciones que CAMBIAN rowCount (open, transcripcion), usar
        un patron distinto: setRowCount primero (sin bloqueo), despues
        bloquear, despues setItem. Ver _reload_table.
        """
        return [
            x for x in (
                self.table,
                self.table.model(),
                self.table.selectionModel(),
                self.table.horizontalHeader(),
                self.table.verticalHeader(),
            )
            if x is not None
        ]

    def _bulk_reload_rows(self, indices):
        """Repaint eficiente de muchas filas cuando el contenido completo cambia."""
        if not indices:
            return
        self._updating_table = True
        blockers = self._all_signal_blockers()
        model = self.table.model()
        self.table.setUpdatesEnabled(False)
        for b in blockers:
            b.blockSignals(True)
        try:
            for i in indices:
                if 0 <= i < len(self.cues):
                    self._populate_row(i, self.cues[i])
        finally:
            for b in blockers:
                b.blockSignals(False)
            self.table.setUpdatesEnabled(True)
            self._updating_table = False
        # Tras desbloquear, notificar a la view del rango cambiado.
        if model is not None and indices:
            try:
                top_left = model.index(min(indices), 0)
                bottom_right = model.index(max(indices), len(self.COLS) - 1)
                model.dataChanged.emit(top_left, bottom_right)
            except Exception:
                pass
        self.table.viewport().update()
        if hasattr(self, "timeline"):
            self.timeline.refresh()

    def _bulk_update_text_only(self, indices):
        """Path rapido para operaciones que solo cambian el texto del cue
        (find/replace, auto-formato).

        Actualiza in-place: texto, c/s (numero + color), tooltip por linea,
        y warning icon en col # si la severidad cambio. NO toca timestamps.
        """
        if not indices:
            return
        warn_color = QBrush(QColor("#ffd93d"))
        err_color = QBrush(QColor("#ff6b6b"))
        empty = QBrush()  # NoBrush = color por default del tema
        self._updating_table = True
        blockers = self._all_signal_blockers()
        model = self.table.model()
        self.table.setUpdatesEnabled(False)
        for b in blockers:
            b.blockSignals(True)
        try:
            for i in indices:
                if not (0 <= i < len(self.cues)):
                    continue
                c = self.cues[i]
                dur_s = c.duration_ms / 1000
                cps = (len(c.text) / dur_s) if dur_s > 0 else 0
                long_lines_present = any(
                    len(line) > MAX_LINE_CHARS for line in c.text.split("\n")
                )

                # COL_TEXT
                text_item = self.table.item(i, self.COL_TEXT)
                if text_item is not None:
                    text_item.setText(c.text)
                    # Tooltip por linea
                    line_info = []
                    for line in c.text.split("\n"):
                        n = len(line)
                        mark = " ⚠" if n > MAX_LINE_CHARS else ""
                        line_info.append(f"  {n:>3}c{mark}  {line}")
                    tt = "Lineas:\n" + "\n".join(line_info)
                    if cps > MAX_READING_CPS:
                        text_item.setForeground(warn_color)
                        tt += f"\n\n⚠ lectura rapida ({cps:.0f} cps)"
                    else:
                        text_item.setForeground(empty)
                    text_item.setToolTip(tt)

                # COL_CPS
                cps_item = self.table.item(i, self.COL_CPS)
                if cps_item is not None:
                    cps_item.setText(f"{cps:.0f}c/s")
                    if cps > MAX_READING_CPS:
                        cps_item.setForeground(warn_color)
                        cps_item.setToolTip(
                            f"Lectura rapida: {cps:.1f} cps (max {MAX_READING_CPS})"
                        )
                    else:
                        cps_item.setForeground(empty)
                        cps_item.setToolTip(f"{cps:.1f} caracteres por segundo")

                # COL_INDEX warning icon (la severidad puede haber cambiado)
                idx_item = self.table.item(i, self.COL_INDEX)
                if idx_item is not None:
                    severity = None
                    problems = []
                    if dur_s < MIN_CUE_DURATION_S:
                        problems.append(f"duracion muy corta")
                        severity = "error"
                    elif dur_s > MAX_CUE_DURATION_S:
                        problems.append(f"duracion muy larga")
                        severity = severity or "warning"
                    if cps > MAX_READING_CPS:
                        problems.append(f"{cps:.0f} cps")
                        severity = severity or "warning"
                    if long_lines_present:
                        problems.append(f">{MAX_LINE_CHARS} chars/linea")
                        severity = severity or "warning"
                    idx_item.setText(f"⚠ {i + 1}" if severity else str(i + 1))
                    if severity == "error":
                        idx_item.setForeground(err_color)
                    elif severity == "warning":
                        idx_item.setForeground(warn_color)
                    else:
                        idx_item.setForeground(empty)
                    idx_item.setToolTip(" · ".join(problems) if problems else "")
        finally:
            for b in blockers:
                b.blockSignals(False)
            self.table.setUpdatesEnabled(True)
            self._updating_table = False
        if model is not None and indices:
            try:
                top_left = model.index(min(indices), 0)
                bottom_right = model.index(max(indices), len(self.COLS) - 1)
                model.dataChanged.emit(top_left, bottom_right)
            except Exception:
                pass
        self.table.viewport().update()
        if hasattr(self, "timeline"):
            self.timeline.refresh()

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._updating_table:
            return
        row = item.row()
        col = item.column()
        if row >= len(self.cues):
            return
        text = item.text()
        try:
            if col == self.COL_START:
                new_ms = time_str_to_ms(text)
                if new_ms == self.cues[row].start_ms:
                    return
                self._push_undo()
                self.cues[row].start_ms = new_ms
            elif col == self.COL_END:
                new_ms = time_str_to_ms(text)
                if new_ms == self.cues[row].end_ms:
                    return
                self._push_undo()
                self.cues[row].end_ms = new_ms
            elif col == self.COL_TEXT:
                if text == self.cues[row].text:
                    return
                self._push_undo()
                self.cues[row].text = text
                # El texto cambio: los timestamps por palabra ya no estan alineados.
                # Los limpiamos para evitar highlight incorrecto en reproduccion.
                self.cues[row].words = []
            # Refresca columna de duracion
            self._reload_row(row)
        except ValueError as e:
            # Solo capturamos errores de parseo de tiempo o validacion.
            # Cualquier otra excepcion deberia propagar para no enmascarar bugs.
            QMessageBox.warning(self, "Valor invalido", str(e))
            self._reload_row(row)

    def _on_row_selected(self):
        """Slot conectado a itemSelectionChanged.

        Ya NO salta el video automaticamente: si el usuario navega con las
        flechas del teclado para revisar los subtitulos, el video debe quedarse
        quieto. El seek se hace explicitamente en _on_cell_clicked (al hacer
        click con el raton) o con Ctrl+T (saltar al seleccionado).

        Tambien sincroniza la fila seleccionada con el highlight del timeline.
        """
        if hasattr(self, "timeline"):
            self.timeline.set_selected_row(self.table.currentRow())

    def _on_current_cell_changed(self, current_row: int, current_col: int,
                                  prev_row: int, prev_col: int):
        """Sincroniza el highlight del timeline cuando cambia la celda actual."""
        if hasattr(self, "timeline"):
            self.timeline.set_selected_row(current_row)

    def _on_timeline_cue_clicked(self, row: int):
        """Click en un bloque del timeline: selecciona esa fila en la tabla.

        El seek lo hace timeline.seek_requested -> player.setPosition (ya
        conectado).
        """
        if 0 <= row < len(self.cues):
            self.table.selectRow(row)

    def _on_timeline_cue_time_changed(self, row: int, new_start_ms: int, new_end_ms: int):
        """Termino un drag/resize en el timeline: aplicar el cambio de tiempo
        al cue, push undo, y refrescar la fila en la tabla.
        """
        if not (0 <= row < len(self.cues)):
            return
        cue = self.cues[row]
        if cue.start_ms == new_start_ms and cue.end_ms == new_end_ms:
            return  # sin cambios
        self._push_undo()
        cue.start_ms = max(0, new_start_ms)
        cue.end_ms = max(cue.start_ms + 1, new_end_ms)
        # Limpiar word timestamps: ya no estan alineados con la nueva ventana
        cue.words = []
        self._reload_row(row)
        self.statusBar().showMessage(
            f"Cue #{row + 1}: {ms_to_srt_time(cue.start_ms)} → "
            f"{ms_to_srt_time(cue.end_ms)} (Ctrl+Z para deshacer)"
        )

    def _on_timeline_drag_preview(self, row: int, start_ms: int, end_ms: int):
        """Mientras se arrastra un bloque, mostrar feedback en la status bar."""
        dur = (end_ms - start_ms) / 1000
        self.statusBar().showMessage(
            f"Cue #{row + 1}: {ms_to_srt_time(start_ms)} → "
            f"{ms_to_srt_time(end_ms)}  ({dur:.2f}s)"
        )

    def _on_cell_clicked(self, row: int, column: int):
        """Manejo del click sobre una celda.

        - Si fue en la columna Texto: abre el editor in-place.
        - Si fue en cualquier otra columna: salta el video al inicio del cue
          (comportamiento documentado: "click en una fila salta el video").
        """
        if not (0 <= row < len(self.cues)):
            return
        if column == self.COL_TEXT:
            item = self.table.item(row, column)
            if item is None:
                return
            # Solo abrir editor si no esta ya en modo edicion
            if self.table.state() != QTableWidget.State.EditingState:
                self.table.editItem(item)
            return
        # Click en columnas #/Inicio/Fin/Duracion/c/s: saltar al tiempo
        if self.player.source().isValid():
            self.player.setPosition(self.cues[row].start_ms)

    # -------------------------- Player callbacks ----------------------------

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _on_position_changed(self, pos: int):
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(pos)
        dur = self.player.duration()
        self.time_label.setText(
            f"{ms_to_srt_time(pos).split(',')[0]} / {ms_to_srt_time(dur).split(',')[0]}"
        )

    def _on_duration_changed(self, dur: int):
        self.position_slider.setRange(0, dur)

    def _on_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_btn.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause)
            )
        else:
            self.play_btn.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
            )

    def _on_player_error(self, error, error_string):
        if error != QMediaPlayer.Error.NoError:
            self.statusBar().showMessage(f"Error de video: {error_string}")

    def _highlight_current_cue(self):
        if not self.cues:
            self._set_overlay_html("")
            return
        pos = self.player.position()
        # OPTIMIZACION (P1): cursor + bisect en vez de scan lineal.
        # Antes iterabamos los 541 cues cada 100ms = 5410 comparaciones/seg.
        # Ahora: chequear el ultimo activo primero (caso comun en playback
        # continuo) y si fallo, bisect O(log N) para encontrar el correcto.
        active_idx = -1
        last = self._current_overlay_row
        if 0 <= last < len(self.cues):
            c = self.cues[last]
            if c.start_ms <= pos <= c.end_ms:
                active_idx = last
            elif last + 1 < len(self.cues):
                # Caso comun en playback: el cue siguiente
                c2 = self.cues[last + 1]
                if c2.start_ms <= pos <= c2.end_ms:
                    active_idx = last + 1
        if active_idx < 0:
            # Fallback: bisect O(log N). Encuentra el ultimo cue cuyo
            # start_ms <= pos, despues chequea si pos <= end_ms.
            import bisect
            idx = bisect.bisect_right(
                self.cues, pos, key=lambda c: c.start_ms
            ) - 1
            if 0 <= idx < len(self.cues):
                c = self.cues[idx]
                if c.start_ms <= pos <= c.end_ms:
                    active_idx = idx
        if active_idx >= 0 and (
            self.table.currentRow() != active_idx
            and self.table.state() != QTableWidget.State.EditingState
        ):
            self.table.blockSignals(True)
            self.table.selectRow(active_idx)
            self.table.blockSignals(False)

        # Determinar la palabra activa dentro del cue actual (si tiene words)
        active_word_idx = -1
        if active_idx >= 0:
            cue = self.cues[active_idx]
            if cue.words:
                for wi, w in enumerate(cue.words):
                    if w.start_ms <= pos <= w.end_ms:
                        active_word_idx = wi
                        break

        # Actualizar overlay solo si cambio el cue o la palabra activa
        if (active_idx != self._current_overlay_row or
                active_word_idx != self._current_overlay_word_idx):
            self._current_overlay_row = active_idx
            self._current_overlay_word_idx = active_word_idx
            if active_idx >= 0:
                html_text = self._build_overlay_html(self.cues[active_idx], active_word_idx)
                self._set_overlay_html(html_text)
            else:
                self._set_overlay_html("")

    def _build_overlay_html(self, cue: Cue, active_word_idx: int) -> str:
        """Construye HTML del subtitulo con la palabra activa resaltada en amarillo.

        Usa cue.text como fuente de verdad para la estructura (incluyendo saltos
        de linea de auto-formato) y solo envuelve la N-esima palabra de cue.text
        en un <span> amarillo. Asi se evita el flicker visual cuando el mapeo
        fallaba.
        """
        text = cue.text
        # Sin word timestamps o sin palabra activa: render directo
        if not cue.words or active_word_idx < 0 or active_word_idx >= len(cue.words):
            return html.escape(text).replace("\n", "<br>")

        # Dividir cue.text por whitespace pero conservando los separadores
        # (espacios y saltos de linea) para que el render preserve el formato.
        tokens = re.split(r"(\s+)", text)
        word_index = 0
        result_parts = []
        for token in tokens:
            if not token:
                continue
            if token.strip():
                # Es una palabra (texto sin espacios)
                if word_index == active_word_idx:
                    result_parts.append(
                        '<span style="color: #ffeb3b; '
                        'background-color: rgba(255,235,59,0.18);">'
                        f'{html.escape(token)}</span>'
                    )
                else:
                    result_parts.append(html.escape(token))
                word_index += 1
            else:
                # Whitespace: preservar pero convertir \n a <br>
                result_parts.append(html.escape(token).replace("\n", "<br>"))
        return "".join(result_parts)

    def _set_overlay_html(self, html_text: str):
        """Muestra HTML en el overlay del video, en el panel de audio y en la
        barra de preview de subtitulos abajo del reproductor.

        Si hay subtitulos cargados pero el momento actual del video no tiene
        ninguno activo, deja la barra VACIA (no muestra el placeholder), asi
        no parpadea visualmente entre cues.
        El placeholder solo aparece cuando no hay subtitulos cargados aun.
        """
        if html_text:
            self.subtitle_overlay.setTextFormat(Qt.TextFormat.RichText)
            self.subtitle_overlay.setText(html_text)
            self.subtitle_overlay.show()
            self._reposition_overlay()
            self.audio_subtitle_label.setTextFormat(Qt.TextFormat.RichText)
            self.audio_subtitle_label.setText(html_text)
            self.subtitle_preview_bar.setText(html_text)
        else:
            self.subtitle_overlay.hide()
            self.audio_subtitle_label.setText("")
            # Si ya hay subtitulos cargados, dejar la barra VACIA (sin placeholder)
            if self.cues:
                self.subtitle_preview_bar.setText("")
                return
            self.subtitle_preview_bar.setText(
                "<span style='color:#666; font-size:14px; font-weight: 400;'>"
                "Los subtitulos apareceran aqui cuando reproduzcas el video."
                "</span>"
            )


# ----------------------------------------------------------------------------
# Punto de entrada
# ----------------------------------------------------------------------------

def resource_path(relative_path: str) -> str:
    """Resuelve una ruta de recurso tanto en modo desarrollo como en .exe empaquetado.

    PyInstaller mete los datos extra en una carpeta temporal cuya ruta queda
    en sys._MEIPASS. En modo desarrollo, los archivos estan en el directorio
    del script.
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def load_app_icon() -> Optional[QIcon]:
    """Busca un icono en la carpeta de la app.

    Prueba en este orden:
    1. SubFlow.ico, icon.ico, SubFlow.png, icon.png (nombres preferidos)
    2. Cualquier archivo .ico de la carpeta
    3. Cualquier archivo .png de la carpeta
    """
    for name in ("SubFlow.ico", "icon.ico", "SubFlow.png", "icon.png"):
        path = resource_path(name)
        if os.path.exists(path):
            return QIcon(path)
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    try:
        files = os.listdir(base)
    except OSError:
        return None
    for ext in (".ico", ".png"):
        for fname in files:
            if fname.lower().endswith(ext):
                return QIcon(os.path.join(base, fname))
    return None



def _setup_crash_logger():
    """Captura cualquier excepcion no controlada y la escribe a un archivo de log.

    Util para diagnosticar crashes del .exe (modo windowed) donde no hay consola.
    El log queda en %LOCALAPPDATA%\\SubFlow\\logs\\subflow_error.log en modo
    APPEND: si la app crashea, se reinicia y vuelve a crashear, los tracebacks
    de los intentos previos NO se pierden. Rotacion simple a .1 si supera
    LOG_ROTATION_BYTES.
    """
    import traceback
    from datetime import datetime
    log_path = os.path.join(_app_log_dir(), "subflow_error.log")

    def excepthook(exc_type, exc_value, exc_tb):
        try:
            if os.path.exists(log_path) and os.path.getsize(log_path) > LOG_ROTATION_BYTES:
                rot_path = log_path + ".1"
                if os.path.exists(rot_path):
                    os.remove(rot_path)
                os.replace(log_path, rot_path)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n=== SubFlow crash ===\n")
                f.write(f"Fecha: {datetime.now().isoformat(timespec='seconds')}\n")
                f.write(f"Tipo: {exc_type.__name__}\n")
                f.write(f"Mensaje: {exc_value}\n\n")
                f.write("Traceback completo:\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook


def main():
    _setup_crash_logger()
    app = QApplication(sys.argv)
    app.setApplicationName("SubFlow")
    # No usamos setApplicationDisplayName porque Windows lo concatena al titulo
    # de la ventana causando "SubFlow * - SubFlow". El titulo ya lo manejamos
    # nosotros en _update_title.
    icon = load_app_icon()
    if icon is not None:
        app.setWindowIcon(icon)
    win = SubtitleEditor()
    if icon is not None:
        win.setWindowIcon(icon)
    # Abrir maximizada (no fullscreen sin marco - el usuario aun puede
    # restaurar o minimizar con los botones de la barra de titulo).
    win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
