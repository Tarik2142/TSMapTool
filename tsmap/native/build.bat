@echo off
rem Builds raster.dll (64-bit, for a 64-bit Python) with the Visual Studio C compiler (Build Tools are enough).
setlocal
set VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe
if not exist "%VSWHERE%" (
    echo Visual Studio Installer not found
    exit /b 1
)
for /f "usebackq delims=" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set VS=%%i
if not defined VS (
    echo Visual Studio C++ build tools not found
    exit /b 1
)
call "%VS%\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1 || exit /b 1
cd /d "%~dp0"
cl /nologo /O2 /MT /LD /W3 raster.c /Fe:raster.dll /link /NOLOGO || exit /b 1
del raster.obj raster.lib raster.exp 2>nul
echo raster.dll built
