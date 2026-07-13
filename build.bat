@echo off
setlocal
cd /d "%~dp0"
set CONDA_ROOT=C:\software\anaconda3
set PY=%CONDA_ROOT%\python.exe
"%PY%" -m pip install --upgrade pyinstaller bleak
"%PY%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name BS1Controller ^
  --paths "%CD%" ^
  --hidden-import bleak.backends.winrt.client ^
  --hidden-import bleak.backends.winrt.scanner ^
  --collect-submodules winrt ^
  --exclude-module bleak.backends.corebluetooth ^
  --exclude-module bleak.backends.bluezdbus ^
  --exclude-module bleak.backends.p4android ^
  --add-binary "%CONDA_ROOT%\Library\bin\ffi.dll;." ^
  --add-binary "%CONDA_ROOT%\Library\bin\libssl-3-x64.dll;." ^
  --add-binary "%CONDA_ROOT%\Library\bin\libcrypto-3-x64.dll;." ^
  --add-binary "%CONDA_ROOT%\Library\bin\liblzma.dll;." ^
  --add-binary "%CONDA_ROOT%\Library\bin\libexpat.dll;." ^
  --add-binary "%CONDA_ROOT%\Library\bin\libmpdec-4.dll;." ^
  main.py
endlocal
