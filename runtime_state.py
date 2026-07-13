from __future__ import annotations

import threading
import time
from typing import Any


class RuntimeState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {
            "connected": False,
            "device_name": "",
            "device_address": "",
            "cpu_temp": 0,
            "gpu_temp": 0,
            "cpu_power": 0.0,
            "gpu_power": 0.0,
            "cpu_model": "",
            "gpu_model": "",
            "selected_gpu_device": "auto",
            "cpu_sensors": [],
            "gpu_sensors": [],
            "gpu_devices": [],
            "bridge_ok": False,
            "bridge_error": "",
            "control_temp": 0,
            "control_source": "max",
            "current_rpm": 0,
            "target_rpm": 0,
            "last_sent_rpm": 0,
            "work_mode": "",
            "gear_setting": "",
            "selected_gear": "",
            "max_gear": "",
            "heartbeat_age": 0,
            "last_error": "",
            "learning_dirty": False,
            "updated_at": int(time.time() * 1000),
        }

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            self._data.update(kwargs)
            self._data["updated_at"] = int(time.time() * 1000)

    def set_error(self, message: str) -> None:
        self.update(last_error=message)

    def clear_error(self) -> None:
        self.update(last_error="")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)
