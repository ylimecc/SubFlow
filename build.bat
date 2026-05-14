@echo off
REM ===========================================================================
REM Script de build para SubFlow
REM ===========================================================================
REM Genera dist\SubFlow.exe (un solo archivo, sin dependencias externas)
REM
REM Uso:
REM     1. Abre PowerShell o CMD en esta carpeta
REM     2. Ejecuta: build.bat
REM     3. Espera 5-15 minutos
REM     4. El .exe queda en dist\SubFlow.exe
REM ===========================================================================

setlocal ENABLEDELAYEDEXPANSION

echo.
echo ============================================================
echo  SubFlow - Build de ejecutable Windows
echo ============================================================
echo.

REM 1) Verificar Python
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python no esta en el PATH.
    echo Instala Python desde https://www.python.org/downloads/
    echo y marca "Add python.exe to PATH" durante la instalacion.
    pause
    exit /b 1
)

REM 2) Verificar pip
where pip >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pip no esta disponible.
    pause
    exit /b 1
)

REM 3) Instalar dependencias necesarias para el build
REM    Sin -q para que cualquier error transitivo sea visible.
echo [1/3] Instalando dependencias (puede tardar unos minutos la primera vez)...
pip install --upgrade pip
pip install -r requirements-build.txt
if errorlevel 1 (
    echo [ERROR] Fallo la instalacion de dependencias.
    pause
    exit /b 1
)

REM 4) Limpiar builds previos
echo [2/3] Limpiando builds anteriores...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM 5) Compilar con PyInstaller usando el spec
REM Nota: el spec fija upx=False porque UPX corrompe las DLLs nativas de
REM ctranslate2 / onnxruntime. NO actives UPX desde aqui.
echo [3/3] Compilando con PyInstaller... (esto tarda 5-15 min, no cierres la ventana)
pyinstaller --clean build.spec
if errorlevel 1 (
    echo.
    echo [ERROR] Fallo la compilacion.
    pause
    exit /b 1
)

REM 6) Mostrar resultado
echo.
echo ============================================================
echo  COMPILACION COMPLETA
echo ============================================================
echo.
if exist dist\SubFlow.exe (
    echo Listo. Tu .exe esta aqui:
    echo.
    echo     %CD%\dist\SubFlow.exe
    echo.
    for %%I in (dist\SubFlow.exe) do echo     Tamano: %%~zI bytes
    echo.
    echo Pruebalo con doble-clic.
    echo Para distribuirlo, copia ese unico archivo a donde quieras.
) else (
    echo [ERROR] No se encontro el .exe esperado en dist\.
)

echo.
pause
