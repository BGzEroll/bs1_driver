from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from defaults import CONFIG_NAME, DEFAULT_FAN_CURVE, DEFAULT_SMART_CONTROL, WEB_PORT, default_config


class ConfigStore:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.path = base_dir / CONFIG_NAME

    def load(self) -> dict:
        cfg = default_config()
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    cfg = merge_config(cfg, loaded)
            except Exception:
                backup = self.path.with_suffix(self.path.suffix + ".broken")
                try:
                    self.path.replace(backup)
                except Exception:
                    pass
        cfg = normalize_config(cfg)
        self.save(cfg)
        return cfg

    def save(self, cfg: dict) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        normalized = normalize_config(cfg)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)


def merge_config(base: dict, patch: dict) -> dict:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            nested = dict(out[key])
            nested.update(value)
            out[key] = nested
        else:
            out[key] = value
    return out


def normalize_config(cfg: dict[str, Any]) -> dict:
    out = default_config()
    out.update({k: v for k, v in cfg.items() if k in out})
    out["web_port"] = WEB_PORT
    out["temp_update_rate"] = clamp_int(out.get("temp_update_rate"), 1, 10, 2)
    out["temp_source"] = "max"
    out["autostart"] = bool(out.get("autostart"))
    out["fan_curve"] = [dict(point) for point in DEFAULT_FAN_CURVE]
    smart = dict(DEFAULT_SMART_CONTROL)
    if isinstance(out.get("smart_control"), dict):
        smart.update(out["smart_control"])
    smart["learning"] = bool(smart.get("learning"))
    smart["predictive_boost"] = bool(smart.get("predictive_boost"))
    smart["filter_transient_spike"] = bool(smart.get("filter_transient_spike"))
    smart["learning_bias"] = "balanced"
    smart["target_temp"] = clamp_int(smart.get("target_temp"), 45, 90, 68)
    smart["aggressiveness"] = clamp_int(smart.get("aggressiveness"), 1, 10, 5)
    smart["hysteresis"] = clamp_int(smart.get("hysteresis"), 0, 8, 2)
    smart["min_rpm_change"] = clamp_int(smart.get("min_rpm_change"), 20, 400, 50)
    smart["ramp_up_limit"] = clamp_int(smart.get("ramp_up_limit"), 50, 1200, 220)
    smart["ramp_down_limit"] = clamp_int(smart.get("ramp_down_limit"), 50, 1200, 160)
    smart["learn_rate"] = clamp_int(smart.get("learn_rate"), 1, 10, 3)
    smart["learn_window"] = clamp_int(smart.get("learn_window"), 3, 24, 8)
    smart["learn_delay"] = clamp_int(smart.get("learn_delay"), 0, 8, 3)
    smart["overheat_weight"] = clamp_int(smart.get("overheat_weight"), 1, 12, 8)
    smart["rpm_delta_weight"] = clamp_int(smart.get("rpm_delta_weight"), 1, 12, 5)
    smart["noise_weight"] = clamp_int(smart.get("noise_weight"), 0, 12, 4)
    smart["trend_gain"] = clamp_int(smart.get("trend_gain"), 1, 12, 5)
    smart["max_learn_offset"] = clamp_int(smart.get("max_learn_offset"), 100, 2000, 300)
    smart["learned_offsets"] = normalize_offsets(smart.get("learned_offsets"), len(out["fan_curve"]))
    smart["noise_profile"] = normalize_noise_profile(smart.get("noise_profile"))
    smart["noise_profile_updated_at"] = clamp_int(smart.get("noise_profile_updated_at"), 0, 9999999999, 0)
    out["smart_control"] = smart
    return out


def normalize_curve(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return [dict(point) for point in DEFAULT_FAN_CURVE]
    points = []
    for point in value:
        if not isinstance(point, dict):
            continue
        temp = clamp_int(point.get("temperature"), 0, 110, None)
        rpm = clamp_int(point.get("rpm"), 0, 5000, None)
        if temp is None or rpm is None:
            continue
        points.append({"temperature": temp, "rpm": rpm})
    points.sort(key=lambda p: p["temperature"])
    deduped = []
    for point in points:
        if deduped and deduped[-1]["temperature"] == point["temperature"]:
            deduped[-1] = point
        else:
            deduped.append(point)
    if len(deduped) < 2:
        return [dict(point) for point in DEFAULT_FAN_CURVE]
    last_rpm = 0
    for point in deduped:
        point["rpm"] = max(point["rpm"], last_rpm)
        last_rpm = point["rpm"]
    return deduped


def normalize_offsets(value: Any, size: int) -> list[int]:
    offsets = []
    if isinstance(value, list):
        for item in value[:size]:
            offsets.append(clamp_int(item, -2000, 2000, 0) or 0)
    while len(offsets) < size:
        offsets.append(0)
    return offsets


def normalize_noise_profile(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    points = []
    for item in value:
        if not isinstance(item, dict):
            continue
        rpm = clamp_int(item.get("rpm"), 1, 20000, None)
        try:
            db = float(item.get("db"))
        except (TypeError, ValueError):
            continue
        if rpm is None or db != db or db < -200 or db > 200:
            continue
        points.append({"rpm": rpm, "db": db})
    points.sort(key=lambda p: p["rpm"])
    deduped = []
    for point in points[:64]:
        if deduped and deduped[-1]["rpm"] == point["rpm"]:
            deduped[-1] = point
        else:
            deduped.append(point)
    if len(deduped) < 2:
        return []
    min_db = min(point["db"] for point in deduped)
    for point in deduped:
        point["db"] = point["db"] - min_db
    return deduped


def clamp_int(value: Any, low: int, high: int, fallback: int | None) -> int | None:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, n))
