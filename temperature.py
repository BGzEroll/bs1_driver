from __future__ import annotations

import json
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TemperatureSample:
    cpu_temp: int
    gpu_temp: int
    cpu_power: float
    gpu_power: float
    control_temp: int
    control_source: str = "max"
    cpu_model: str = ""
    gpu_model: str = ""
    selected_gpu_device: str = "auto"
    cpu_sensors: list[dict[str, Any]] = field(default_factory=list)
    gpu_sensors: list[dict[str, Any]] = field(default_factory=list)
    gpu_devices: list[dict[str, Any]] = field(default_factory=list)
    bridge_ok: bool = False
    bridge_error: str = ""
    error: str = ""


class TemperatureReader:
    def __init__(self) -> None:
        self._nvidia_smi = shutil.which("nvidia-smi")
        self._bridge_path = find_bridge_path()
        self._bridge: subprocess.Popen[str] | None = None
        self._bridge_lines: queue.Queue[str] = queue.Queue()
        self._bridge_lock = threading.RLock()

    def read(self, selection: dict[str, Any] | None = None) -> TemperatureSample:
        selection = selection if isinstance(selection, dict) else {}
        bridge_error = ""
        try:
            data = self._read_bridge(selection)
        except Exception as exc:
            data = {}
            bridge_error = str(exc)

        cpu_sensors = normalize_sensors(data.get("CpuSensors"))
        gpu_sensors = normalize_sensors(data.get("GpuSensors"))
        gpu_devices = normalize_gpu_devices(data.get("GpuDevices"))
        cpu = select_cpu_temperature(cpu_sensors, selection.get("cpu_sensors"), parse_int(data.get("CpuTemp")))
        gpu = parse_int(data.get("GpuTemp"))
        cpu_power = parse_float(data.get("CpuPower"))
        power = parse_float(data.get("GpuPower"))
        if gpu <= 0:
            gpu, power = self.read_gpu_temp_power()

        cpu = cpu if 0 < cpu < 150 else 0
        gpu = gpu if 0 < gpu < 150 else 0
        cpu_power = cpu_power if 0 <= cpu_power <= 2000 else 0.0
        power = power if 0 <= power <= 2000 else 0.0
        control = max(cpu, gpu)
        error = ""
        if cpu <= 0:
            error = "CPU temperature unavailable"
            if bridge_error:
                error += f": {bridge_error}"
        elif control <= 0:
            error = "CPU/GPU temperature unavailable"
        return TemperatureSample(
            cpu_temp=cpu,
            gpu_temp=gpu,
            cpu_power=cpu_power,
            gpu_power=power,
            control_temp=control,
            cpu_model=str(data.get("CpuModel") or ""),
            gpu_model=str(data.get("GpuModel") or ""),
            selected_gpu_device=str(data.get("SelectedGpuDevice") or "auto"),
            cpu_sensors=cpu_sensors,
            gpu_sensors=gpu_sensors,
            gpu_devices=gpu_devices,
            bridge_ok=bool(data),
            bridge_error=bridge_error,
            error=error,
        )

    def close(self) -> None:
        with self._bridge_lock:
            process = self._bridge
            self._bridge = None
            if process is None:
                return
            try:
                if process.poll() is None and process.stdin:
                    process.stdin.write(json.dumps({"Type": "Exit", "Data": ""}) + "\n")
                    process.stdin.flush()
                    process.wait(timeout=2)
            except Exception:
                try:
                    process.terminate()
                except Exception:
                    pass

    def read_gpu_temp_power(self) -> tuple[int, float]:
        if not self._nvidia_smi:
            self._nvidia_smi = shutil.which("nvidia-smi")
        if not self._nvidia_smi:
            return 0, 0.0
        try:
            output = run_hidden(
                [
                    self._nvidia_smi,
                    "--query-gpu=temperature.gpu,power.draw",
                    "--format=csv,noheader,nounits",
                ],
                timeout=1.5,
            )
        except Exception:
            return 0, 0.0
        line = output.strip().splitlines()[0] if output.strip() else ""
        parts = [part.strip() for part in line.split(",")]
        temp = parse_int(parts[0]) if parts else 0
        power = parse_float(parts[1]) if len(parts) > 1 else 0.0
        return (temp if 0 < temp < 150 else 0), (power if 0 <= power <= 2000 else 0.0)

    def _read_bridge(self, selection: dict[str, Any]) -> dict[str, Any]:
        command = {
            "Type": "GetTemperature",
            "Data": json.dumps(
                {
                    "TempSource": "max",
                    "GpuDevice": clean_selection(selection.get("gpu_device")),
                    "CpuSensor": "auto",
                    "GpuSensor": clean_selection(selection.get("gpu_sensor")),
                }
            ),
        }
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                with self._bridge_lock:
                    self._ensure_bridge()
                    assert self._bridge is not None and self._bridge.stdin is not None
                    self._bridge.stdin.write(json.dumps(command) + "\n")
                    self._bridge.stdin.flush()
                    response = json.loads(self._bridge_lines.get(timeout=6))
                    data = response.get("Data")
                    if not response.get("Success") or not isinstance(data, dict):
                        raise RuntimeError(str(response.get("Error") or "TempBridge read failed"))
                    return data
            except Exception as exc:
                last_error = exc
                self._stop_bridge()
        raise RuntimeError(str(last_error or "TempBridge unavailable"))

    def _ensure_bridge(self) -> None:
        if self._bridge is not None and self._bridge.poll() is None:
            return
        if self._bridge_path is None:
            raise FileNotFoundError("BS1TempBridge.exe is not packaged")

        self._bridge_lines = queue.Queue()
        startupinfo = None
        creationflags = 0
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW
        self._bridge = subprocess.Popen(
            [str(self._bridge_path)],
            cwd=str(self._bridge_path.parent),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        threading.Thread(target=self._collect_bridge_output, name="temp-bridge-output", daemon=True).start()
        ready = self._bridge_lines.get(timeout=30)
        if ready != "READY:STDIO":
            raise RuntimeError(f"unexpected TempBridge startup response: {ready}")

    def _collect_bridge_output(self) -> None:
        process = self._bridge
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                stripped = line.strip()
                if stripped:
                    self._bridge_lines.put(stripped)
        except Exception:
            return

    def _stop_bridge(self) -> None:
        process = self._bridge
        self._bridge = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


def find_bridge_path() -> Path | None:
    roots = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        roots.append(Path(bundle_root))
    roots.append(Path(__file__).resolve().parent)
    for root in roots:
        for relative in (Path("helpers/BS1TempBridge.exe"), Path("helpers/publish/BS1TempBridge.exe")):
            candidate = root / relative
            if candidate.is_file():
                return candidate
    return None


def select_cpu_temperature(sensors: list[dict[str, Any]], selected: Any, fallback: int) -> int:
    keys = {str(key) for key in selected} if isinstance(selected, list) else set()
    values = [sensor["value"] for sensor in sensors if sensor["key"] in keys and sensor["value"] > 0]
    if values:
        return int(round(sum(values) / len(values)))
    return fallback


def normalize_sensors(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    sensors = []
    for item in value:
        if not isinstance(item, dict):
            continue
        key = str(item.get("Key") or "").strip()
        name = str(item.get("Name") or key).strip()
        temp = parse_int(item.get("Value"))
        if key and 0 < temp < 150:
            sensors.append({"key": key, "name": name, "value": temp})
    return sensors


def normalize_gpu_devices(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    devices = []
    for item in value:
        if not isinstance(item, dict):
            continue
        key = str(item.get("Key") or "").strip()
        if not key:
            continue
        devices.append(
            {
                "key": key,
                "name": str(item.get("Name") or key).strip(),
                "vendor": str(item.get("Vendor") or "").strip(),
                "sensors": normalize_sensors(item.get("Sensors")),
            }
        )
    return devices


def clean_selection(value: Any) -> str:
    text = str(value or "auto").strip()
    return text if text else "auto"


def run_hidden(args: list[str], timeout: float) -> str:
    startupinfo = None
    creationflags = 0
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = subprocess.CREATE_NO_WINDOW
    completed = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=timeout,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    return completed.stdout


def parse_int(value: Any) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def parse_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
