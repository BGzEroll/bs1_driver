@echo off
setlocal
cd /d "%~dp0"
set CONDA_ROOT=C:\software\anaconda3
set PY=%CONDA_ROOT%\python.exe
set DOTNET_EXE=%CD%\..\.dotnet\dotnet.exe
set THRM_ROOT=%CD%\..\THRM
set LHM_ROOT=%THRM_ROOT%\temp\LibreHardwareMonitor

if not exist "%DOTNET_EXE%" (
  echo ERROR: Local .NET SDK not found at "%DOTNET_EXE%".
  exit /b 1
)
if not exist "%LHM_ROOT%\LibreHardwareMonitorLib\LibreHardwareMonitorLib.csproj" (
  echo ERROR: LibreHardwareMonitor source not found at "%LHM_ROOT%".
  exit /b 1
)

set DOTNET_ROOT=%CD%\..\.dotnet
set DOTNET_CLI_TELEMETRY_OPTOUT=1
echo Building THRM temperature bridge from LibreHardwareMonitor source...
"%DOTNET_EXE%" publish "%CD%\helpers\TempBridge.csproj" ^
  -c Release ^
  -o "%CD%\helpers\publish" ^
  --self-contained true ^
  /p:Platform=x64 ^
  /p:ThrmRoot="%THRM_ROOT%" ^
  /p:LibreHardwareMonitorRoot="%LHM_ROOT%"
if errorlevel 1 exit /b 1

"%PY%" -m pip install --upgrade pyinstaller bleak
"%PY%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name BS1Controller ^
  --icon "%CD%\assets\thrm.ico" ^
  --paths "%CD%" ^
  --hidden-import bleak.backends.winrt.client ^
  --hidden-import bleak.backends.winrt.scanner ^
  --collect-submodules winrt ^
  --exclude-module bleak.backends.corebluetooth ^
  --exclude-module bleak.backends.bluezdbus ^
  --exclude-module bleak.backends.p4android ^
  --add-data "%CD%\helpers\publish;helpers" ^
  --add-binary "%CONDA_ROOT%\Library\bin\ffi.dll;." ^
  --add-binary "%CONDA_ROOT%\Library\bin\libssl-3-x64.dll;." ^
  --add-binary "%CONDA_ROOT%\Library\bin\libcrypto-3-x64.dll;." ^
  --add-binary "%CONDA_ROOT%\Library\bin\liblzma.dll;." ^
  --add-binary "%CONDA_ROOT%\Library\bin\libexpat.dll;." ^
  --add-binary "%CONDA_ROOT%\Library\bin\libmpdec-4.dll;." ^
  main.py
endlocal
