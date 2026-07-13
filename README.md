# BS1 Driver

Lightweight BS1-only BLE fan controller.

## Run

Install the only external dependency:

```powershell
C:\software\anaconda3\python.exe -m pip install -r requirements.txt
```

Start:

```powershell
C:\software\anaconda3\python.exe main.py
```

The local Web UI is fixed at:

```text
http://127.0.0.1:1919
```

The app creates `bs1-controller.config` in this directory.

## Build EXE

```powershell
.\build.bat
```

The packaged app is a single-file Windows executable. The small local window
uses native Win32 controls, so it does not depend on Tcl/Tk or `_tkinter`.
