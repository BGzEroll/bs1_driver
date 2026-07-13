from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


PDH_FMT_DOUBLE = 0x00000200
NVML_SUCCESS = 0
NVML_TEMPERATURE_GPU = 0


class _PdhValueUnion(ctypes.Union):
    _fields_ = [
        ("long_value", ctypes.c_long),
        ("double_value", ctypes.c_double),
        ("large_value", ctypes.c_longlong),
        ("ansi_string_value", ctypes.c_char_p),
        ("wide_string_value", ctypes.c_wchar_p),
    ]


class _PdhFormattedCounterValue(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [
        ("c_status", wintypes.DWORD),
        ("value", _PdhValueUnion),
    ]


@dataclass
class TemperatureSample:
    cpu_temp: int = 0
    gpu_temp: int = 0
    cpu_power: float = 0.0
    gpu_power: float = 0.0
    control_temp: int = 0
    cpu_model: str = ""
    gpu_model: str = ""
    cpu_temp_source: str = ""
    gpu_temp_source: str = ""
    temperature_ok: bool = False
    temperature_error: str = ""


class PdhThermalZoneReader:
    COUNTER_PATHS = (
        r"\Thermal Zone Information(\_TZ.THRM)\Temperature",
        r"\Thermal Zone Information(_TZ.THRM)\Temperature",
        r"\Thermal Zone Information(THRM)\Temperature",
    )
    RETRY_SECONDS = 30.0

    def __init__(self) -> None:
        self._pdh = ctypes.WinDLL("pdh.dll")
        self._query = ctypes.c_void_p()
        self._counter = ctypes.c_void_p()
        self._counter_path = ""
        self._retry_after = 0.0
        self.last_error = ""
        self._configure_api()

    @property
    def source_name(self) -> str:
        return "Windows PDH Thermal Zone"

    def read(self) -> int:
        if not self._query.value:
            if time.monotonic() < self._retry_after:
                return 0
            try:
                self._open()
            except Exception as exc:
                self.last_error = str(exc)
                self._retry_after = time.monotonic() + self.RETRY_SECONDS
                return 0

        try:
            raw = self._collect_value()
            temp = normalize_thermal_zone_temperature(raw)
            if temp <= 0:
                raise RuntimeError(f"PDH returned invalid Thermal Zone value: {raw}")
            self.last_error = ""
            return temp
        except Exception as exc:
            self.last_error = str(exc)
            self.close()
            self._retry_after = time.monotonic() + self.RETRY_SECONDS
            return 0

    def close(self) -> None:
        if self._query.value:
            self._pdh.PdhCloseQuery(self._query)
        self._query = ctypes.c_void_p()
        self._counter = ctypes.c_void_p()
        self._counter_path = ""

    def _configure_api(self) -> None:
        self._pdh.PdhOpenQueryW.argtypes = [ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        self._pdh.PdhOpenQueryW.restype = wintypes.LONG
        self._pdh.PdhAddEnglishCounterW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._pdh.PdhAddEnglishCounterW.restype = wintypes.LONG
        self._pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        self._pdh.PdhCollectQueryData.restype = wintypes.LONG
        self._pdh.PdhGetFormattedCounterValue.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(_PdhFormattedCounterValue),
        ]
        self._pdh.PdhGetFormattedCounterValue.restype = wintypes.LONG
        self._pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]
        self._pdh.PdhCloseQuery.restype = wintypes.LONG

    def _open(self) -> None:
        errors = []
        for path in self.COUNTER_PATHS:
            query = ctypes.c_void_p()
            counter = ctypes.c_void_p()
            status = self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(query))
            if status != 0:
                errors.append(f"PdhOpenQueryW=0x{status & 0xFFFFFFFF:08X}")
                continue
            status = self._pdh.PdhAddEnglishCounterW(query, path, 0, ctypes.byref(counter))
            if status == 0:
                self._query = query
                self._counter = counter
                self._counter_path = path
                try:
                    if normalize_thermal_zone_temperature(self._collect_value()) > 0:
                        return
                except Exception as exc:
                    errors.append(f"{path}: {exc}")
                self.close()
                continue
            self._pdh.PdhCloseQuery(query)
            errors.append(f"{path}: PdhAddEnglishCounterW=0x{status & 0xFFFFFFFF:08X}")
        raise RuntimeError("Thermal Zone counter unavailable" + (f" ({'; '.join(errors)})" if errors else ""))

    def _collect_value(self) -> float:
        status = self._pdh.PdhCollectQueryData(self._query)
        if status != 0:
            raise RuntimeError(f"PdhCollectQueryData=0x{status & 0xFFFFFFFF:08X}")
        value = _PdhFormattedCounterValue()
        counter_type = wintypes.DWORD()
        status = self._pdh.PdhGetFormattedCounterValue(
            self._counter,
            PDH_FMT_DOUBLE,
            ctypes.byref(counter_type),
            ctypes.byref(value),
        )
        if status != 0 or value.c_status != 0:
            raise RuntimeError(
                f"PdhGetFormattedCounterValue=0x{status & 0xFFFFFFFF:08X}, "
                f"CStatus=0x{value.c_status:08X}"
            )
        return float(value.double_value)


class NvidiaNvmlReader:
    RETRY_SECONDS = 10.0

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._nvml: ctypes.WinDLL | None = None
        self._device = ctypes.c_void_p()
        self._initialized = False
        self._retry_after = 0.0
        self.model = ""
        self.last_error = ""

    @property
    def source_name(self) -> str:
        return "NVIDIA NVML GPU Core"

    def read(self) -> tuple[int, float, str]:
        with self._lock:
            if not self._initialized:
                if time.monotonic() < self._retry_after:
                    return 0, 0.0, self.model
                try:
                    self._initialize()
                except Exception as exc:
                    self.last_error = str(exc)
                    self._retry_after = time.monotonic() + self.RETRY_SECONDS
                    return 0, 0.0, self.model

            try:
                temperature = ctypes.c_uint()
                self._check(
                    self._nvml.nvmlDeviceGetTemperature(
                        self._device,
                        NVML_TEMPERATURE_GPU,
                        ctypes.byref(temperature),
                    ),
                    "nvmlDeviceGetTemperature",
                )
                power = ctypes.c_uint()
                power_status = self._nvml.nvmlDeviceGetPowerUsage(self._device, ctypes.byref(power))
                watts = power.value / 1000.0 if power_status == NVML_SUCCESS else 0.0
                temp = int(temperature.value)
                if not 0 < temp < 150:
                    raise RuntimeError(f"NVML returned invalid GPU temperature: {temp}")
                self.last_error = ""
                return temp, watts, self.model
            except Exception as exc:
                self.last_error = str(exc)
                self.close()
                self._retry_after = time.monotonic() + self.RETRY_SECONDS
                return 0, 0.0, self.model

    def close(self) -> None:
        with self._lock:
            if self._initialized and self._nvml is not None:
                try:
                    self._nvml.nvmlShutdown()
                except Exception:
                    pass
            self._initialized = False
            self._device = ctypes.c_void_p()
            self._nvml = None

    def _initialize(self) -> None:
        nvml = load_nvml_library()
        configure_nvml_api(nvml)
        self._check_with(nvml, nvml.nvmlInit_v2(), "nvmlInit_v2")
        self._nvml = nvml
        self._initialized = True
        try:
            count = ctypes.c_uint()
            self._check(nvml.nvmlDeviceGetCount_v2(ctypes.byref(count)), "nvmlDeviceGetCount_v2")
            if count.value < 1:
                raise RuntimeError("NVML found no NVIDIA GPU")
            self._check(
                nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(self._device)),
                "nvmlDeviceGetHandleByIndex_v2",
            )
            name = ctypes.create_string_buffer(96)
            self._check(nvml.nvmlDeviceGetName(self._device, name, len(name)), "nvmlDeviceGetName")
            self.model = name.value.decode("utf-8", errors="replace").strip()
        except Exception:
            self.close()
            raise

    def _check(self, status: int, operation: str) -> None:
        assert self._nvml is not None
        self._check_with(self._nvml, status, operation)

    @staticmethod
    def _check_with(nvml: ctypes.WinDLL, status: int, operation: str) -> None:
        if status == NVML_SUCCESS:
            return
        try:
            detail = nvml.nvmlErrorString(status)
            message = detail.decode("utf-8", errors="replace") if detail else f"status {status}"
        except Exception:
            message = f"status {status}"
        raise RuntimeError(f"{operation} failed: {message}")


class TemperatureReader:
    def __init__(self) -> None:
        self._cpu = PdhThermalZoneReader()
        self._gpu = NvidiaNvmlReader()
        self._cpu_model = read_cpu_model()

    def read(self) -> TemperatureSample:
        cpu = self._cpu.read()
        gpu, gpu_power, gpu_model = self._gpu.read()
        control = max(cpu, gpu)
        errors = []
        if cpu <= 0:
            errors.append(f"CPU temperature unavailable: {self._cpu.last_error or 'PDH Thermal Zone unavailable'}")
        if gpu <= 0:
            errors.append(f"GPU temperature unavailable: {self._gpu.last_error or 'NVIDIA NVML unavailable'}")
        error = "; ".join(errors)
        return TemperatureSample(
            cpu_temp=cpu,
            gpu_temp=gpu,
            cpu_power=0.0,
            gpu_power=gpu_power if 0 <= gpu_power <= 2000 else 0.0,
            control_temp=control,
            cpu_model=self._cpu_model,
            gpu_model=gpu_model,
            cpu_temp_source=self._cpu.source_name,
            gpu_temp_source=self._gpu.source_name,
            temperature_ok=cpu > 0 and gpu > 0,
            temperature_error=error,
        )

    def close(self) -> None:
        self._cpu.close()
        self._gpu.close()


def normalize_thermal_zone_temperature(raw: float) -> int:
    if raw <= 0:
        return 0
    celsius = raw
    if raw > 1000:
        celsius = (raw / 10.0) - 273.15
    elif raw > 200:
        celsius = raw - 273.15
    rounded = int(round(celsius))
    return rounded if 0 < rounded < 150 else 0


def load_nvml_library() -> ctypes.WinDLL:
    candidates = []
    windir = os.environ.get("WINDIR")
    if windir:
        candidates.append(Path(windir) / "System32" / "nvml.dll")
    for env_name in ("ProgramW6432", "ProgramFiles"):
        root = os.environ.get(env_name)
        if root:
            candidates.append(Path(root) / "NVIDIA Corporation" / "NVSMI" / "nvml.dll")
    for candidate in candidates:
        if candidate.is_file():
            return ctypes.WinDLL(str(candidate))
    try:
        return ctypes.WinDLL("nvml.dll")
    except OSError as exc:
        raise FileNotFoundError("NVIDIA NVML library nvml.dll was not found") from exc


def configure_nvml_api(nvml: ctypes.WinDLL) -> None:
    nvml.nvmlInit_v2.argtypes = []
    nvml.nvmlInit_v2.restype = ctypes.c_uint
    nvml.nvmlShutdown.argtypes = []
    nvml.nvmlShutdown.restype = ctypes.c_uint
    nvml.nvmlDeviceGetCount_v2.argtypes = [ctypes.POINTER(ctypes.c_uint)]
    nvml.nvmlDeviceGetCount_v2.restype = ctypes.c_uint
    nvml.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
    nvml.nvmlDeviceGetHandleByIndex_v2.restype = ctypes.c_uint
    nvml.nvmlDeviceGetName.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char), ctypes.c_uint]
    nvml.nvmlDeviceGetName.restype = ctypes.c_uint
    nvml.nvmlDeviceGetTemperature.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_uint),
    ]
    nvml.nvmlDeviceGetTemperature.restype = ctypes.c_uint
    nvml.nvmlDeviceGetPowerUsage.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    nvml.nvmlDeviceGetPowerUsage.restype = ctypes.c_uint
    nvml.nvmlErrorString.argtypes = [ctypes.c_uint]
    nvml.nvmlErrorString.restype = ctypes.c_char_p


def read_cpu_model() -> str:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            return str(value).strip()
    except OSError:
        return ""
