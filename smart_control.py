from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class ControlResult:
    target_rpm: int
    base_rpm: int
    control_temp: int
    spike_suppressed: bool
    learning_changed: bool
    should_send: bool


class SmartController:
    def __init__(self, config: dict):
        self.configure(config)
        self.raw_history: list[int] = []
        self.control_history: list[int] = []
        self.last_target_rpm = -1
        self.last_control_temp = -1
        self.predict_last_temp = 0
        self.predict_last_time = 0.0
        self.steady = StableObserver(len(self.curve))

    def configure(self, config: dict) -> None:
        self.curve = normalize_curve(config.get("fan_curve", []))
        self.smart = dict(config.get("smart_control", {}))
        self.offsets = list(self.smart.get("learned_offsets") or [0] * len(self.curve))
        if len(self.offsets) != len(self.curve):
            self.offsets = (self.offsets + [0] * len(self.curve))[: len(self.curve)]

    def learned_offsets(self) -> list[int]:
        return list(self.offsets)

    def reset_learning(self) -> None:
        self.offsets = [0] * len(self.curve)
        self.steady.reset()

    def compute(self, temp_sample: dict[str, Any], current_rpm: int, device_target_rpm: int) -> ControlResult:
        raw_temp = int(temp_sample.get("control_temp") or 0)
        if raw_temp <= 0:
            return ControlResult(0, 0, 0, False, False, False)

        sample_temp = raw_temp
        sample_suppressed = False
        if self.smart.get("filter_transient_spike", True):
            sample_temp, sample_suppressed = filter_transient_sample(
                raw_temp, self.raw_history, int(self.smart.get("hysteresis", 2))
            )
        self.raw_history = keep_tail(self.raw_history + [raw_temp], 6)

        control_temp = sample_temp
        control_suppressed = False
        self.control_history = keep_tail(self.control_history + [control_temp], 24)
        if self.smart.get("filter_transient_spike", True):
            control_temp, control_suppressed = filter_transient_spike(
                control_temp,
                self.control_history,
                int(self.smart.get("target_temp", 68)),
                int(self.smart.get("hysteresis", 2)),
            )
        spike_suppressed = sample_suppressed or control_suppressed

        learning_temp = control_temp
        if self.smart.get("predictive_boost", True):
            control_temp = max(control_temp, self.predictive_temp(control_temp, float(temp_sample.get("gpu_power") or 0)))

        base_rpm = curve_rpm(control_temp, self.curve)
        target = curve_rpm(control_temp, effective_curve(self.curve, self.offsets, self.smart))
        if target <= 0:
            target = base_rpm
        min_rpm, max_rpm = curve_bounds(self.curve)
        target = clamp(target, min_rpm, max_rpm)
        if self.last_target_rpm >= 0:
            target = apply_ramp_limit(
                target,
                self.last_target_rpm,
                int(self.smart.get("ramp_up_limit", 220)),
                int(self.smart.get("ramp_down_limit", 160)),
            )
            target = clamp(target, min_rpm, max_rpm)

        observed_rpm = current_rpm if current_rpm > 0 else target
        learning_changed = False
        if self.smart.get("learning", True) and not spike_suppressed:
            steady = self.steady.observe(learning_temp, observed_rpm, self.curve, self.smart)
            if steady.ready:
                self.offsets, learning_changed = learn_steady_offset(
                    steady.bucket_idx,
                    steady.mean_temp,
                    steady.mean_rpm,
                    self.curve,
                    self.offsets,
                    self.smart,
                )
        elif not self.smart.get("learning", True):
            self.steady.reset()

        should_send = should_send_target(target, self.last_target_rpm, int(self.smart.get("min_rpm_change", 50)), device_target_rpm, current_rpm)
        if should_send:
            self.last_target_rpm = target
        self.last_control_temp = learning_temp
        return ControlResult(target, base_rpm, control_temp, spike_suppressed, learning_changed, should_send)

    def predictive_temp(self, control_temp: int, gpu_power: float) -> int:
        now = time.monotonic()
        if not self.predict_last_time:
            self.predict_last_time = now
            self.predict_last_temp = control_temp
            return control_temp
        dt = max(now - self.predict_last_time, 0.1)
        delta = control_temp - self.predict_last_temp
        self.predict_last_time = now
        self.predict_last_temp = control_temp
        if delta <= 0:
            return control_temp
        gain = int(self.smart.get("trend_gain", 5))
        boost = min(8, max(0, round(delta / dt * gain * 0.4)))
        if gpu_power >= 200:
            boost = max(boost, 2)
        return control_temp + boost


class StableSample:
    def __init__(self, bucket_idx=-1, mean_temp=0, mean_rpm=0, ready=False):
        self.bucket_idx = bucket_idx
        self.mean_temp = mean_temp
        self.mean_rpm = mean_rpm
        self.ready = ready


class StableObserver:
    def __init__(self, curve_len: int):
        self.curve_len = max(curve_len, 1)
        self.samples = [[] for _ in range(self.curve_len)]
        self.rpm_samples = [[] for _ in range(self.curve_len)]
        self.settle = [0] * self.curve_len
        self.last_t = [0] * self.curve_len
        self.last_r = [0] * self.curve_len
        self.seen = [False] * self.curve_len

    def reset(self) -> None:
        for i in range(self.curve_len):
            self.samples[i].clear()
            self.rpm_samples[i].clear()
            self.settle[i] = 0
            self.seen[i] = False

    def observe(self, temp: int, rpm: int, curve: list[dict], cfg: dict) -> StableSample:
        if len(curve) != self.curve_len:
            self.__init__(len(curve))
        idx = pick_bucket(temp, curve)
        if idx < 0:
            return StableSample()
        window = clamp(int(cfg.get("learn_window", 8)), 3, 24)
        delay = clamp(int(cfg.get("learn_delay", 3)), 0, 8)
        rpm_band = max(120, int(cfg.get("min_rpm_change", 50)))
        if self.seen[idx]:
            if abs(temp - self.last_t[idx]) > 3 or (rpm > 0 and self.last_r[idx] > 0 and abs(rpm - self.last_r[idx]) > rpm_band):
                self.samples[idx].clear()
                self.rpm_samples[idx].clear()
                self.settle[idx] = 0
        else:
            self.seen[idx] = True
        self.last_t[idx] = temp
        self.last_r[idx] = rpm
        if self.settle[idx] < delay:
            self.settle[idx] += 1
            return StableSample(idx)
        self.samples[idx].append(temp)
        self.rpm_samples[idx].append(rpm)
        self.samples[idx] = keep_tail(self.samples[idx], window)
        self.rpm_samples[idx] = keep_tail(self.rpm_samples[idx], window)
        if len(self.samples[idx]) < window:
            return StableSample(idx)
        if max(self.samples[idx]) - min(self.samples[idx]) > 2:
            return StableSample(idx)
        if max(self.rpm_samples[idx]) - min(self.rpm_samples[idx]) > rpm_band:
            return StableSample(idx)
        mean_t = sum(self.samples[idx]) // len(self.samples[idx])
        mean_r = sum(self.rpm_samples[idx]) // len(self.rpm_samples[idx])
        self.samples[idx].clear()
        self.rpm_samples[idx].clear()
        self.settle[idx] = 0
        return StableSample(idx, mean_t, mean_r, True)


def normalize_curve(curve: list[dict]) -> list[dict]:
    points = [{"temperature": int(p["temperature"]), "rpm": int(p["rpm"])} for p in curve if "temperature" in p and "rpm" in p]
    points.sort(key=lambda p: p["temperature"])
    return points


def curve_rpm(temp: int, curve: list[dict]) -> int:
    if len(curve) < 2:
        return 0
    if temp <= curve[0]["temperature"]:
        return curve[0]["rpm"]
    if temp >= curve[-1]["temperature"]:
        return curve[-1]["rpm"]
    for left, right in zip(curve, curve[1:]):
        if left["temperature"] <= temp <= right["temperature"]:
            span = right["temperature"] - left["temperature"]
            ratio = (temp - left["temperature"]) / span if span else 0
            return int(left["rpm"] + ratio * (right["rpm"] - left["rpm"]))
    return 0


def effective_curve(curve: list[dict], offsets: list[int], cfg: dict) -> list[dict]:
    min_rpm, max_rpm = curve_bounds(curve)
    cap = min(int(cfg.get("max_learn_offset", 300)), 600)
    bias = cfg.get("learning_bias", "balanced")
    out = []
    last_rpm = 0
    for i, point in enumerate(curve):
        off = offsets[i] if i < len(offsets) else 0
        if bias == "cooling" and off < 0:
            off = 0
        if bias == "quiet" and off > 0:
            off = 0
        off = clamp(off, -cap, cap)
        rpm = clamp(point["rpm"] + off, min_rpm, max_rpm)
        rpm = max(rpm, last_rpm)
        last_rpm = rpm
        out.append({"temperature": point["temperature"], "rpm": rpm})
    return out


def learn_steady_offset(bucket: int, steady_temp: int, steady_rpm: int, curve: list[dict], offsets: list[int], cfg: dict) -> tuple[list[int], bool]:
    if bucket < 0 or bucket >= len(curve):
        return offsets, False
    target = int(cfg.get("target_temp", 68))
    low_target = target - max(int(cfg.get("hysteresis", 2)) + 3, 3)
    learn_rate = clamp(int(cfg.get("learn_rate", 3)), 1, 10)
    alpha = 0.025 + (learn_rate - 1) * 0.0125
    delta = 0
    if steady_temp > target:
        delta = max(20, round(alpha * (steady_temp - target) / 0.008))
    elif steady_temp < low_target:
        delta = -round(alpha * (low_target - steady_temp) / 0.008)
        if abs(delta) < 20:
            delta = 0
    delta = clamp(delta, -80, 80)
    if delta == 0:
        return offsets, False
    next_offsets = list(offsets)
    cap = min(int(cfg.get("max_learn_offset", 300)), 600)
    bias = cfg.get("learning_bias", "balanced")
    next_offsets[bucket] = clamp(next_offsets[bucket] + delta, -cap, cap)
    if bias == "cooling" and next_offsets[bucket] < 0:
        next_offsets[bucket] = 0
    if bias == "quiet" and next_offsets[bucket] > 0:
        next_offsets[bucket] = 0
    return next_offsets, next_offsets != offsets


def filter_transient_sample(current: int, recent: list[int], hysteresis: int) -> tuple[int, bool]:
    if len(recent) < 3:
        return current, False
    last = recent[-3:]
    baseline = median3(last[0], last[1], last[2])
    if max(last) - min(last) > max(2, hysteresis + 1):
        return current, False
    if abs(current - baseline) >= max(5, hysteresis + 4):
        return baseline, True
    return current, False


def filter_transient_spike(current: int, recent: list[int], target: int, hysteresis: int) -> tuple[int, bool]:
    if len(recent) < 3 or current >= target + 10:
        return current, False
    last = recent[-3:]
    baseline = median3(last[0], last[1], last[2])
    if current - baseline >= max(2, hysteresis + 2):
        return baseline, True
    return current, False


def pick_bucket(temp: int, curve: list[dict]) -> int:
    if not curve:
        return -1
    if temp <= curve[0]["temperature"]:
        return 0
    if temp >= curve[-1]["temperature"]:
        return len(curve) - 1
    for i, (left, right) in enumerate(zip(curve, curve[1:])):
        if left["temperature"] <= temp < right["temperature"]:
            midpoint = (left["temperature"] + right["temperature"]) // 2
            return i if temp < midpoint else i + 1
    return len(curve) - 1


def apply_ramp_limit(target: int, previous: int, up: int, down: int) -> int:
    if target > previous:
        return min(previous + up, target)
    if target < previous:
        return max(previous - down, target)
    return target


def should_send_target(target: int, previous: int, min_change: int, device_target: int, current_rpm: int) -> bool:
    if target < 0:
        return False
    if previous < 0:
        return True
    if abs(target - previous) >= min_change:
        return True
    if target > 0 and (device_target == 0 or current_rpm == 0):
        return True
    return abs(target - device_target) >= min_change


def curve_bounds(curve: list[dict]) -> tuple[int, int]:
    if not curve:
        return 0, 4000
    rpms = [int(p["rpm"]) for p in curve]
    return min(rpms), max(rpms)


def keep_tail(items: list, size: int) -> list:
    return items[-size:]


def median3(a: int, b: int, c: int) -> int:
    return sorted([a, b, c])[1]


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))

