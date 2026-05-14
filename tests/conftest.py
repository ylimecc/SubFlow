"""Stubs de PyQt6 para que los tests de funciones puras no necesiten Qt.

El modulo `subtitle_editor.py` importa PyQt6 al cargar, asi que sin esto los
tests fallarian en cualquier maquina/CI sin Qt instalado. Como solo testeamos
funciones puras (parsers de SRT/VTT/ASS, conversion de tiempos, wrap de
lineas, etc.), no necesitamos un Qt funcional — solo que el import no rompa.
"""
import sys
import types


def _make_stub_module(name: str) -> types.ModuleType:
    """Crea un modulo stub donde cualquier atributo es una clase vacia.

    Asi sirve para satisfacer `from PyQt6.QtCore import Qt, QUrl, ...` y
    similares. Las clases stub son intercambiables porque los tests nunca
    construyen widgets de verdad.
    """
    mod = types.ModuleType(name)

    class _Stub:
        # Permite ser usado como decorador, contexto, signal, etc.
        def __init__(self, *a, **kw):
            pass

        def __call__(self, *a, **kw):
            return self

        def __getattr__(self, item):
            return _Stub()

        def connect(self, *a, **kw):
            pass

        def emit(self, *a, **kw):
            pass

    def _getattr(item):
        return _Stub

    mod.__getattr__ = _getattr  # type: ignore[attr-defined]
    return mod


def _install_pyqt6_stubs():
    if "PyQt6" in sys.modules:
        return
    root = types.ModuleType("PyQt6")
    sys.modules["PyQt6"] = root
    for sub in (
        "QtCore", "QtGui", "QtWidgets", "QtMultimedia", "QtMultimediaWidgets"
    ):
        full = f"PyQt6.{sub}"
        sys.modules[full] = _make_stub_module(full)


_install_pyqt6_stubs()
