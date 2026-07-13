from __future__ import annotations

import threading
import time
from typing import Any

from bs1_ble import BS1BleClient, BS1Status
from config_store import ConfigStore, normalize_config
from defaults import DEFAULT_SMART_CONTROL, default_config
from runtime_state import RuntimeState
from smart_control import SmartController
from temperature import TemperatureReader


class Controller:
    def __init__(self, config_store: ConfigStore, config: dict):
        self.config_store = config_store
        self.config_lock = threading.RLock()
        self.config = normalize_config(config)
        self.state = RuntimeState()
        self.temp_reader = TemperatureReader()
        self.smart = SmartController(self.config)
        self.ble = BS1BleClient(self.on_bs1_status, self.on_bs1_disconnect)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_learning_save = time.monotonic()
        self.reconnect_after = 0.0

    def start(self) -> None:
        self.ble.start()
        self.thread = threading.Thread(target=self.run_loop, name="bs1-control-loop", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.ble.stop()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        self.save_learning(force=True)

    def run_loop(self) -> None:
        while not self.stop_event.is_set():
            loop_started = time.monotonic()
            cfg = self.get_config()

            if not self.ble.is_connected() and time.monotonic() >= self.reconnect_after:
                self.try_connect()

            sample = self.temp_reader.read()
            self.state.update(
                cpu_temp=sample.cpu_temp,
                gpu_temp=sample.gpu_temp,
                gpu_power=round(sample.gpu_power, 1),
                control_temp=sample.control_temp,
                control_source=sample.control_source,
            )
            if sample.error:
                self.state.set_error(sample.error)

            if self.ble.is_connected() and sample.control_temp > 0:
                snapshot = self.state.snapshot()
                result = self.smart.compute(
                    {
                        "control_temp": sample.control_temp,
                        "gpu_power": sample.gpu_power,
                    },
                    int(snapshot.get("current_rpm") or 0),
                    int(snapshot.get("target_rpm") or 0),
                )
                self.state.update(
                    target_rpm=result.target_rpm,
                    computed_base_rpm=result.base_rpm,
                    computed_control_temp=result.control_temp,
                    spike_suppressed=result.spike_suppressed,
                )
                if result.should_send and result.target_rpm > 0:
                    try:
                        if self.ble.set_target_rpm(result.target_rpm):
                            self.state.update(last_sent_rpm=result.target_rpm)
                            self.state.clear_error()
                    except Exception as exc:
                        self.state.set_error(f"BLE write failed: {exc}")
                        self.reconnect_after = time.monotonic() + 5
                if result.learning_changed:
                    self.mark_learning_dirty()

            self.save_learning_if_needed()
            elapsed = time.monotonic() - loop_started
            delay = max(0.2, float(cfg.get("temp_update_rate", 2)) - elapsed)
            self.stop_event.wait(delay)

    def try_connect(self) -> None:
        try:
            if self.ble.connect():
                self.state.update(
                    connected=True,
                    device_name=self.ble.device_name,
                    device_address=self.ble.device_address,
                    last_error="",
                )
                self.reconnect_after = 0
        except Exception as exc:
            self.state.update(connected=False)
            self.state.set_error(str(exc))
            self.reconnect_after = time.monotonic() + 10

    def reconnect(self) -> None:
        self.reconnect_after = 0
        try:
            self.ble.disconnect(timeout=3)
        except Exception:
            pass
        self.try_connect()

    def on_bs1_status(self, status: BS1Status) -> None:
        self.state.update(
            connected=True,
            current_rpm=status.current_rpm,
            target_rpm=status.target_rpm,
            work_mode=status.work_mode,
            gear_setting=status.gear_setting,
            max_gear=status.max_gear,
            selected_gear=status.selected_gear,
        )

    def on_bs1_disconnect(self, reason: str) -> None:
        self.state.update(connected=False)
        self.state.set_error(reason)
        self.reconnect_after = time.monotonic() + 5

    def get_config(self) -> dict:
        with self.config_lock:
            return normalize_config(self.config)

    def update_config(self, patch: dict[str, Any]) -> dict:
        with self.config_lock:
            next_cfg = dict(self.config)
            if "smart_control" in patch and isinstance(patch["smart_control"], dict):
                smart = dict(next_cfg.get("smart_control", {}))
                allowed = {
                    "target_temp",
                    "min_rpm_change",
                    "ramp_up_limit",
                    "ramp_down_limit",
                    "learning",
                    "filter_transient_spike",
                    "predictive_boost",
                }
                for key in allowed:
                    if key in patch["smart_control"]:
                        smart[key] = patch["smart_control"][key]
                smart["learning_bias"] = "balanced"
                smart["learn_rate"] = DEFAULT_SMART_CONTROL["learn_rate"]
                smart["learn_window"] = DEFAULT_SMART_CONTROL["learn_window"]
                smart["learn_delay"] = DEFAULT_SMART_CONTROL["learn_delay"]
                smart["hysteresis"] = DEFAULT_SMART_CONTROL["hysteresis"]
                smart["learned_offsets"] = self.smart.learned_offsets()
                next_cfg["smart_control"] = smart
            if "temp_update_rate" in patch:
                next_cfg["temp_update_rate"] = patch["temp_update_rate"]
            if "autostart" in patch:
                next_cfg["autostart"] = bool(patch["autostart"])
            self.config = normalize_config(next_cfg)
            self.smart.configure(self.config)
            self.config_store.save(self.config)
            return self.config

    def reset_defaults(self) -> dict:
        with self.config_lock:
            current_autostart = bool(self.config.get("autostart"))
            self.config = normalize_config(default_config())
            self.config["autostart"] = current_autostart
            self.smart.configure(self.config)
            self.config_store.save(self.config)
            self.state.update(learning_dirty=False)
            return self.get_config()

    def reset_learning(self) -> dict:
        with self.config_lock:
            self.smart.reset_learning()
            self.config["smart_control"]["learned_offsets"] = self.smart.learned_offsets()
            self.config_store.save(self.config)
            self.state.update(learning_dirty=False)
            return self.get_config()

    def mark_learning_dirty(self) -> None:
        with self.config_lock:
            self.config["smart_control"]["learned_offsets"] = self.smart.learned_offsets()
            self.state.update(learning_dirty=True)

    def save_learning_if_needed(self) -> None:
        self.save_learning(force=False)

    def save_learning(self, force: bool = False) -> None:
        if not self.state.snapshot().get("learning_dirty"):
            return
        if not force and time.monotonic() - self.last_learning_save < 25:
            return
        with self.config_lock:
            self.config["smart_control"]["learned_offsets"] = self.smart.learned_offsets()
            self.config_store.save(self.config)
            self.last_learning_save = time.monotonic()
            self.state.update(learning_dirty=False)
