# -*- mode: python ; coding: utf-8 -*-
"""
Spec de PyInstaller para SubFlow.

Optimizado para reducir el tamano del .exe final. Para compilar:

    pip install pyinstaller
    pyinstaller build.spec

El resultado queda en dist/SubFlow.exe
"""

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_all

# --------------------------------------------------------------------------
# Excludes: modulos que NO queremos meter en el .exe
# --------------------------------------------------------------------------

# Modulos pesados de PyQt6 que NO usamos.
# Lista conservadora: solo excluimos lo que estamos seguros que no se usa,
# ni directamente ni como dependencia interna de los modulos que si usamos.
#
# OJO: QtMultimedia depende internamente de QtNetwork, QtOpenGL y a veces
# QtQml/QtQuick. Por eso NO los excluimos aunque no los importemos directamente.
qt_excludes = [
    "PyQt6.QtBluetooth",
    "PyQt6.QtCharts",
    "PyQt6.QtDataVisualization",
    "PyQt6.QtDBus",
    "PyQt6.QtDesigner",
    "PyQt6.QtHelp",
    "PyQt6.QtLocation",
    "PyQt6.QtNetworkAuth",
    "PyQt6.QtNfc",
    "PyQt6.QtPdf",
    "PyQt6.QtPdfWidgets",
    "PyQt6.QtPositioning",
    "PyQt6.QtPrintSupport",
    "PyQt6.QtRemoteObjects",
    "PyQt6.QtSensors",
    "PyQt6.QtSerialBus",
    "PyQt6.QtSerialPort",
    "PyQt6.QtSpatialAudio",
    "PyQt6.QtSql",
    "PyQt6.QtStateMachine",
    "PyQt6.QtTest",
    "PyQt6.QtTextToSpeech",
    "PyQt6.QtWebChannel",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineQuick",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebSockets",
    "PyQt6.QtWebView",
    "PyQt6.Qt3DAnimation",
    "PyQt6.Qt3DCore",
    "PyQt6.Qt3DExtras",
    "PyQt6.Qt3DInput",
    "PyQt6.Qt3DLogic",
    "PyQt6.Qt3DRender",
]

# Otras libs que tipicamente se cuelan y no usamos
misc_excludes = [
    "tkinter",
    "unittest",
    "pydoc",
    "doctest",
    "test",
    "tests",
    "matplotlib",
    "scipy",
    "pandas",
    "IPython",
    "notebook",
    "jupyter",
    "PIL.ImageTk",
    "torch",         # faster-whisper NO necesita PyTorch (usa CTranslate2)
    "torchaudio",
    "torchvision",
    "tensorflow",
    "transformers",  # solo necesitamos tokenizers, no transformers
]

all_excludes = qt_excludes + misc_excludes

# --------------------------------------------------------------------------
# Datos adicionales que SI hay que incluir
# --------------------------------------------------------------------------

datas = []
binaries = []
hiddenimports_extra = []

# collect_all es mas agresivo: agarra datas + binaries + hiddenimports de cada paquete.
# Esto es lo que asegura que TODAS las DLLs nativas (ctranslate2, libs de PyAV, etc.)
# queden bundleadas. Es comun que sin esto, los .exe crasheen silenciosamente.
for pkg_name in ("faster_whisper", "ctranslate2", "av", "huggingface_hub", "tokenizers", "onnxruntime"):
    try:
        d, b, h = collect_all(pkg_name)
        datas += d
        binaries += b
        hiddenimports_extra += h
    except Exception:
        pass
# Incluir un icono en el bundle (para que QIcon lo encuentre en runtime).
# Busca nombres preferidos y luego cualquier .ico/.png en la carpeta.
def _find_icon():
    # Nombres preferidos en orden de prioridad
    for name in ("SubFlow.ico", "icon.ico", "SubFlow.png", "icon.png"):
        if os.path.exists(name):
            return name
    # Fallback: cualquier .ico o .png
    for ext in (".ico", ".png"):
        for f in os.listdir("."):
            if f.lower().endswith(ext):
                return f
    return None

_icon_file = _find_icon()
if _icon_file:
    datas.append((_icon_file, "."))

hiddenimports = list(hiddenimports_extra)
# A veces PyInstaller no detecta los modulos lazy-loaded de faster-whisper
hiddenimports += collect_submodules("faster_whisper")

# --------------------------------------------------------------------------
# Analisis principal
# --------------------------------------------------------------------------

a = Analysis(
    ["subtitle_editor.py"],
    pathex=[],
    binaries=binaries,   # incluye DLLs nativas de ctranslate2, av, onnxruntime
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=all_excludes,
    noarchive=False,
    optimize=2,  # equivalente a python -OO: quita asserts y docstrings
)

pyz = PYZ(a.pure)

# Icono del .exe (lo que se ve en el Explorador). Reutilizamos el mismo
# archivo detectado mas arriba. PyInstaller prefiere .ico para el icono
# del ejecutable; si solo hay .png puede que algunos Windows no lo muestren.
_exe_icon = _icon_file

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SubFlow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,         # DESACTIVADO: UPX corrompe DLLs nativas de ctranslate2 / onnxruntime
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # sin consola negra detras (modo windowed)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_exe_icon,    # icono del .exe (lo que se ve en el Explorador)
    version="version.txt" if os.path.exists("version.txt") else None,
)
