from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class TemperatureSample:
    cpu_temp: int
    gpu_temp: int
    gpu_power: float
    control_temp: int
    control_source: str = "max"
    error: str = ""


class TemperatureReader:
    def __init__(self) -> None:
        self._nvidia_smi = shutil.which("nvidia-smi")

    def read(self) -> TemperatureSample:
        cpu = self.read_cpu_temp()
        gpu, power = self.read_gpu_temp_power()
        control = max(cpu, gpu)
        err = ""
        if control <= 0:
            err = "CPU/GPU temperature unavailable"
        return TemperatureSample(cpu, gpu, power, control, "max", err)

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
        parts = [p.strip() for p in line.split(",")]
        temp = parse_int(parts[0]) if parts else 0
        power = parse_float(parts[1]) if len(parts) > 1 else 0.0
        if temp < 0 or temp > 150:
            temp = 0
        if power < 0 or power > 2000:
            power = 0.0
        return temp, power

    def read_cpu_temp(self) -> int:
        if sys.platform != "win32":
            return 0
        try:
            output = run_hidden(
                [
                    "wmic",
                    "/namespace:\\\\root\\wmi",
                    "PATH",
                    "MSAcpi_ThermalZoneTemperature",
                    "get",
                    "CurrentTemperature",
                    "/value",
                ],
                timeout=1.2,
            )
        except Exception:
            return 0
        match = re.search(r"CurrentTemperature\s*=\s*(\d+)", output)
        if not match:
            return 0
        raw = parse_int(match.group(1))
        celsius = (raw - 2732) // 10
        if 0 < celsius < 150:
            return celsius
        return 0


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


def parse_int(value: str) -> int:
    try:
        return int(float(value.strip()))
    except Exception:
        return 0


def parse_float(value: str) -> float:
    try:
        return float(value.strip())
    except Exception:
        return 0.0

