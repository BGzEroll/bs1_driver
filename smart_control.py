from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ControlResult:
    target_rpm: int
    base_rpm: int
    control_temp: int
    spike_suppressed: bool
    learning_changed: bool
    should_send: bool


@dataclass
class ThermalSample:
    at: float
    cpu_temp: int
    gpu_temp: int
    cpu_power: float
    gpu_power: float


@dataclass
class ThermalPrediction:
    cpu_temp: int = 0
    gpu_temp: int = 0
    control_temp: int = 0
    cpu_rise: float = 0.0
    gpu_rise: float = 0.0


@dataclass
class EqPoint:
    rpm: int
    temp: int


@dataclass
class StableSample:
    bucket_idx: int = -1
    mean_temp: int = 0
    mean_rpm: int = 0
    local_eff: float = 0.0
    have_eff: bool = False
    ready: bool = False


PREDICTION_HISTORY_LENGTH = 6
PREDICTION_MIN_SAMPLES = 3
PREDICTION_HORIZON_SECONDS = 6.0
MAX_PREDICTED_RISE = 6.0
MAX_POWER_LEAD = 3.0
MAX_TEMPERATURE_SLOPE = 2.0

HARD_OFFSET_CAP = 600
STABLE_TEMP_BAND = 2
STABLE_MIN_SAMPLES = 6
STABLE_RPM_BAND = 120
EFF_HISTORY_LEN = 6
MIN_RPM_SPAN_FOR_EFF = 80
EFF_FLOOR_PER_RPM = 0.0008
EFF_CEIL_PER_RPM = 0.05
DEFAULT_EFF_PER_RPM = 0.008
MAX_LEARN_STEP = 80
LEARN_STEP_DEAD_RPM = 20
MIN_SAFETY_STEP = 20
DEFAULT_TARGET_TEMP = 70

OFFSET_SMOOTH_PASSES = 2
OFFSET_SMOOTH_PULL_LIMIT = 30
OFFSET_SMOOTH_SELF_WEIGHT = 0.7
OFFSET_SMOOTH_NEIGHBOR_WEIGHT = 0.15
OFFSET_SMOOTH_RADIUS = 2
EQ_CONSISTENCY_BAND = 3

NOISE_PROFILE_MIN_POINTS = 3
NOISE_PROFILE_MIN_SPAN_RPM = 500
NOISE_PROFILE_MIN_RISE_DB = 1.0
NOISE_GAIN_RAW_MIN = 0.3
NOISE_GAIN_RAW_MAX = 2.0
NOISE_GAIN_MIN = 0.4
NOISE_GAIN_MAX = 1.8
NOISE_WEIGHT_BASELINE = 4.0


class SmartController:
    def __init__(self, config: dict):
        self.configure(config)
        self.raw_history: list[int] = []
        self.control_history: list[int] = []
        self.last_target_rpm = -1
        self.last_control_temp = -1
        self.steady = StableObserver(len(self.curve))
        self.predictor = ThermalPredictor()

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
        self.control_history = keep_tail(self.control_history + [control_temp], 24)
        control_suppressed = False
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
            prediction = self.predictor.observe(
                {
                    "cpu_temp": int(temp_sample.get("cpu_temp") or 0),
                    "gpu_temp": int(temp_sample.get("gpu_temp") or 0),
                    "cpu_power": float(temp_sample.get("cpu_power") or 0.0),
                    "gpu_power": float(temp_sample.get("gpu_power") or 0.0),
                    "control_temp": control_temp,
                    "control_source": str(temp_sample.get("control_source") or "max"),
                },
                time.monotonic(),
                str(temp_sample.get("control_source") or "max"),
                int(self.smart.get("trend_gain", 5)),
            )
            if prediction.control_temp > control_temp:
                control_temp = prediction.control_temp
        else:
            self.predictor.reset()

        min_rpm, max_rpm = curve_bounds(self.curve)
        base_rpm = curve_rpm(control_temp, self.curve)
        target = calculate_target_rpm(control_temp, self.curve, self.offsets, self.smart)
        if target <= 0:
            target = base_rpm
        if target > 0:
            target = clamp(target, min_rpm, max_rpm)

        if should_apply_ramp_limit(target, self.last_target_rpm):
            target = apply_ramp_limit(
                target,
                self.last_target_rpm,
                int(self.smart.get("ramp_up_limit", 220)),
                int(self.smart.get("ramp_down_limit", 160)),
            )
            if target > 0:
                target = clamp(target, min_rpm, max_rpm)

        observed_rpm = current_rpm if current_rpm > 0 else target
        learning_changed = False
        if self.smart.get("learning", True) and not spike_suppressed:
            steady = self.steady.observe(learning_temp, observed_rpm, self.curve, self.smart)
            if steady.ready and steady.bucket_idx >= 0:
                self.offsets, learning_changed = learn_steady_offset(
                    steady.bucket_idx,
                    steady.mean_temp,
                    steady.mean_rpm,
                    steady.local_eff,
                    steady.have_eff,
                    self.curve,
                    self.offsets,
                    self.smart,
                )
        elif not self.smart.get("learning", True):
            self.steady.reset()

        should_send = should_send_target(
            target,
            self.last_target_rpm,
            int(self.smart.get("min_rpm_change", 50)),
            device_target_rpm,
            current_rpm,
        )
        if should_send:
            self.last_target_rpm = target
        self.last_control_temp = learning_temp
        return ControlResult(target, base_rpm, control_temp, spike_suppressed, learning_changed, should_send)


class ThermalPredictor:
    def __init__(self) -> None:
        self.samples: list[ThermalSample] = []

    def reset(self) -> None:
        self.samples.clear()

    def observe(self, temp: dict[str, Any], at: float, source: str, trend_gain: int) -> ThermalPrediction:
        if not at:
            at = time.monotonic()
        sample = ThermalSample(
            at=at,
            cpu_temp=int(temp.get("cpu_temp") or 0),
            gpu_temp=int(temp.get("gpu_temp") or 0),
            cpu_power=float(temp.get("cpu_power") or 0.0),
            gpu_power=float(temp.get("gpu_power") or 0.0),
        )
        self.samples.append(sample)
        if len(self.samples) > PREDICTION_HISTORY_LENGTH:
            self.samples = self.samples[-PREDICTION_HISTORY_LENGTH:]

        gain = normalized_trend_gain(trend_gain)
        cpu_rise = predicted_rise(self.samples, lambda s: s.cpu_temp, lambda s: s.cpu_power, gain)
        gpu_rise = predicted_rise(self.samples, lambda s: s.gpu_temp, lambda s: s.gpu_power, gain)
        cpu_temp = sample.cpu_temp + round_float(cpu_rise) if sample.cpu_temp > 0 else 0
        gpu_temp = sample.gpu_temp + round_float(gpu_rise) if sample.gpu_temp > 0 else 0
        return ThermalPrediction(
            cpu_temp=cpu_temp,
            gpu_temp=gpu_temp,
            control_temp=resolve_control_temp(cpu_temp, gpu_temp, source),
            cpu_rise=cpu_rise,
            gpu_rise=gpu_rise,
        )


class StableObserver:
    def __init__(self, curve_len: int):
        self.curve_len = max(curve_len, 1)
        self.alloc_buffers(self.curve_len)

    def alloc_buffers(self, curve_len: int) -> None:
        self.samples: list[list[int]] = [[] for _ in range(curve_len)]
        self.rpm_samples: list[list[int]] = [[] for _ in range(curve_len)]
        self.history: list[list[EqPoint]] = [[] for _ in range(curve_len)]
        self.settle = [0] * curve_len
        self.last_t = [0] * curve_len
        self.last_r = [0] * curve_len
        self.seen = [False] * curve_len

    def curve_len_value(self) -> int:
        return self.curve_len

    def resize(self, curve_len: int) -> None:
        curve_len = max(curve_len, 1)
        if self.curve_len == curve_len:
            self.reset()
            return
        self.curve_len = curve_len
        self.alloc_buffers(curve_len)

    def reset(self) -> None:
        for i in range(self.curve_len):
            self.samples[i].clear()
            self.rpm_samples[i].clear()
            self.settle[i] = 0
            self.last_t[i] = 0
            self.last_r[i] = 0
            self.seen[i] = False

    def observe(self, temp: int, rpm: int, curve: list[dict], cfg: dict) -> StableSample:
        if len(curve) != self.curve_len:
            self.resize(len(curve))
        idx = pick_bucket(temp, curve)
        if idx < 0 or idx >= len(self.samples):
            return StableSample()

        window = stable_sample_window(cfg)
        delay = stable_sample_delay(cfg)
        rpm_band = stable_rpm_range(cfg)

        if self.seen[idx]:
            temp_jump = abs(temp - self.last_t[idx]) > STABLE_TEMP_BAND + 1
            rpm_jump = rpm > 0 and self.last_r[idx] > 0 and abs(rpm - self.last_r[idx]) > rpm_band
            if temp_jump or rpm_jump:
                self.samples[idx].clear()
                self.rpm_samples[idx].clear()
                self.settle[idx] = 0
        else:
            self.seen[idx] = True
            self.settle[idx] = 0
        self.last_t[idx] = temp
        self.last_r[idx] = rpm

        if self.settle[idx] < delay:
            self.settle[idx] += 1
            return StableSample(bucket_idx=idx)

        self.samples[idx].append(temp)
        self.rpm_samples[idx].append(rpm)
        if len(self.samples[idx]) > window:
            self.samples[idx] = self.samples[idx][-window:]
            self.rpm_samples[idx] = self.rpm_samples[idx][-window:]

        if len(self.samples[idx]) < window:
            return StableSample(bucket_idx=idx)

        temps = self.samples[idx]
        rpms = self.rpm_samples[idx]
        if max(temps) - min(temps) > STABLE_TEMP_BAND:
            return StableSample(bucket_idx=idx)
        if max(rpms) - min(rpms) > rpm_band:
            return StableSample(bucket_idx=idx)

        mean_t = sum(temps) // len(temps)
        mean_r = sum(rpms) // len(rpms)
        self.samples[idx].clear()
        self.rpm_samples[idx].clear()
        self.settle[idx] = 0

        self.record_equilibrium(idx, mean_r, mean_t)
        eff, have_eff = self.local_efficiency(idx)
        return StableSample(idx, mean_t, mean_r, eff, have_eff, True)

    def record_equilibrium(self, idx: int, rpm: int, temp: int) -> None:
        if idx < 0 or idx >= len(self.history):
            return
        replaced = False
        kept: list[EqPoint] = []
        for point in self.history[idx]:
            if not replaced and abs(point.rpm - rpm) < MIN_RPM_SPAN_FOR_EFF:
                kept.append(EqPoint(rpm, temp))
                replaced = True
                continue
            if not stale_equilibrium(point, rpm, temp):
                kept.append(point)
        if not replaced:
            kept.append(EqPoint(rpm, temp))
        if len(kept) > EFF_HISTORY_LEN:
            kept = kept[-EFF_HISTORY_LEN:]
        self.history[idx] = kept

    def local_efficiency(self, idx: int) -> tuple[float, bool]:
        if idx < 0 or idx >= len(self.history):
            return 0.0, False
        hist = self.history[idx]
        if len(hist) < 2:
            return 0.0, False
        rpms = [p.rpm for p in hist]
        if max(rpms) - min(rpms) < MIN_RPM_SPAN_FOR_EFF:
            return 0.0, False
        mean_r = sum(p.rpm for p in hist) / len(hist)
        mean_t = sum(p.temp for p in hist) / len(hist)
        cov = 0.0
        var_r = 0.0
        for point in hist:
            dr = point.rpm - mean_r
            cov += dr * (point.temp - mean_t)
            var_r += dr * dr
        if var_r <= 0:
            return 0.0, False
        eff = -cov / var_r
        if eff < EFF_FLOOR_PER_RPM:
            eff = EFF_FLOOR_PER_RPM
        if eff > EFF_CEIL_PER_RPM:
            eff = EFF_CEIL_PER_RPM
        return eff, True


def calculate_target_rpm(temp: int, curve: list[dict], offsets: list[int], cfg: dict) -> int:
    if not curve:
        return 0
    active_offsets = offsets if cfg.get("learning", True) else []
    if cfg.get("learning", True):
        active_offsets, _ = constrain_offsets_to_learning_bias(active_offsets, cfg.get("learning_bias", "balanced"))
    rpm = curve_rpm(temp, build_effective_curve(curve, active_offsets, effective_offset_cap(cfg)))
    if rpm <= 0:
        return 0
    min_rpm, max_rpm = curve_bounds(curve)
    return clamp(rpm, min_rpm, max_rpm)


def build_effective_curve(curve: list[dict], offsets: list[int], cap: int) -> list[dict]:
    min_rpm, max_rpm = curve_bounds(curve)
    out = []
    for i, point in enumerate(curve):
        off = offsets[i] if i < len(offsets) else 0
        off = clamp_offset_for_point(off, point["rpm"], min_rpm, max_rpm, cap)
        out.append({"temperature": point["temperature"], "rpm": clamp(point["rpm"] + off, min_rpm, max_rpm)})
    enforce_non_decreasing_rpm(out)
    return out


def effective_curve(curve: list[dict], offsets: list[int], cfg: dict) -> list[dict]:
    active_offsets = offsets if cfg.get("learning", True) else []
    if cfg.get("learning", True):
        active_offsets, _ = constrain_offsets_to_learning_bias(active_offsets, cfg.get("learning_bias", "balanced"))
    return build_effective_curve(curve, active_offsets, effective_offset_cap(cfg))


def learn_steady_offset(
    bucket: int,
    steady_temp: int,
    steady_rpm: int,
    local_eff: float,
    have_eff: bool,
    curve: list[dict],
    offsets: list[int],
    cfg: dict,
) -> tuple[list[int], bool]:
    if bucket < 0 or bucket >= len(curve):
        return offsets, False

    next_offsets = [offsets[i] if i < len(offsets) else 0 for i in range(len(curve))]
    if steady_rpm <= 0:
        steady_rpm = curve[bucket]["rpm"] + next_offsets[bucket]

    main_delta = solve_learn_step(steady_temp, steady_rpm, local_eff, have_eff, cfg)
    if main_delta == 0:
        return next_offsets, False

    cap = effective_offset_cap(cfg)
    min_rpm, max_rpm = curve_bounds(curve)

    def apply(idx: int, delta: int) -> None:
        if idx < 0 or idx >= len(next_offsets) or delta == 0:
            return
        next_offsets[idx] = clamp_offset_for_point(
            next_offsets[idx] + delta,
            curve[idx]["rpm"],
            min_rpm,
            max_rpm,
            cap,
        )

    apply(bucket, main_delta)
    next_offsets, _ = constrain_offsets_to_learning_bias(next_offsets, cfg.get("learning_bias", "balanced"))
    smooth_offsets(curve, next_offsets, bucket, cap, min_rpm, max_rpm)
    next_offsets, _ = constrain_offsets_to_learning_bias(next_offsets, cfg.get("learning_bias", "balanced"))
    enforce_monotonic_with_offsets(curve, next_offsets, cap, min_rpm, max_rpm)
    return next_offsets, next_offsets != offsets


def solve_learn_step(steady_temp: int, steady_rpm: int, eff: float, have_eff: bool, cfg: dict) -> int:
    ceiling = target_temp_ceiling(cfg)
    low_target = ceiling - comfort_band_width(cfg)
    alpha = alpha_from_learn_rate(int(cfg.get("learn_rate", 3)))

    if not have_eff or eff < EFF_FLOOR_PER_RPM:
        eff = DEFAULT_EFF_PER_RPM
    if eff > EFF_CEIL_PER_RPM:
        eff = EFF_CEIL_PER_RPM

    if steady_temp > ceiling:
        step = alpha * (steady_temp - ceiling) / eff
        if step < MIN_SAFETY_STEP:
            step = MIN_SAFETY_STEP
    elif steady_temp < low_target:
        step = -alpha * (low_target - steady_temp) / eff * noise_down_gain(steady_rpm, cfg)
    else:
        return 0

    step = clamp_float(step, -MAX_LEARN_STEP, MAX_LEARN_STEP)
    delta = round_float(step)
    if steady_temp <= ceiling and abs(delta) < LEARN_STEP_DEAD_RPM:
        return 0
    return delta


def smooth_offsets(curve: list[dict], offsets: list[int], center: int, cap: int, min_rpm: int, max_rpm: int) -> None:
    limit = min(len(offsets), len(curve))
    if limit < 3:
        return
    lo = max(center - OFFSET_SMOOTH_RADIUS, 1)
    hi = min(center + OFFSET_SMOOTH_RADIUS, limit - 2)
    if lo > hi:
        return
    work = list(offsets)
    for _ in range(OFFSET_SMOOTH_PASSES):
        work[:] = offsets[:]
        for i in range(lo, hi + 1):
            target = round_float(
                OFFSET_SMOOTH_SELF_WEIGHT * offsets[i]
                + OFFSET_SMOOTH_NEIGHBOR_WEIGHT * offsets[i - 1]
                + OFFSET_SMOOTH_NEIGHBOR_WEIGHT * offsets[i + 1]
            )
            pull = target - offsets[i]
            if pull > OFFSET_SMOOTH_PULL_LIMIT:
                target = offsets[i] + OFFSET_SMOOTH_PULL_LIMIT
            elif pull < -OFFSET_SMOOTH_PULL_LIMIT:
                target = offsets[i] - OFFSET_SMOOTH_PULL_LIMIT
            work[i] = clamp_offset_for_point(target, curve[i]["rpm"], min_rpm, max_rpm, cap)
        offsets[:] = work[:]


def enforce_monotonic_with_offsets(curve: list[dict], offsets: list[int], cap: int, min_rpm: int, max_rpm: int) -> None:
    for i in range(1, min(len(curve), len(offsets))):
        prev = curve[i - 1]["rpm"] + offsets[i - 1]
        curr = curve[i]["rpm"] + offsets[i]
        if curr < prev:
            needed = prev - curve[i]["rpm"]
            offsets[i] = clamp_offset_for_point(needed, curve[i]["rpm"], min_rpm, max_rpm, cap)


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
    if len(recent) < 3:
        return current, False
    if current >= target + 10:
        return current, False
    last = recent[-3:]
    baseline = median3(last[0], last[1], last[2])
    if current - baseline >= max(2, hysteresis + 2):
        return baseline, True
    return current, False


def normalized_trend_gain(value: int) -> float:
    value = clamp(value, 1, 12)
    return 0.45 + value * 0.09


def predicted_rise(samples: list[ThermalSample], temperature: Callable[[ThermalSample], int], power: Callable[[ThermalSample], float], gain: float) -> float:
    if not samples:
        return 0.0
    if temperature(samples[-1]) <= 0:
        return 0.0
    rise = positive_temperature_slope(samples, temperature) * PREDICTION_HORIZON_SECONDS * gain
    rise += power_step_lead(samples, power, gain)
    return clamp_float(rise, 0.0, MAX_PREDICTED_RISE)


def positive_temperature_slope(samples: list[ThermalSample], temperature: Callable[[ThermalSample], int]) -> float:
    if len(samples) < PREDICTION_MIN_SAMPLES:
        return 0.0
    window = samples[-PREDICTION_HISTORY_LENGTH:]
    base = window[0].at
    xs: list[float] = []
    ys: list[float] = []
    for sample in window:
        value = temperature(sample)
        if value <= 0 or sample.at < base:
            continue
        xs.append(sample.at - base)
        ys.append(float(value))
    count = len(xs)
    if count < PREDICTION_MIN_SAMPLES:
        return 0.0
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xx = sum(x * x for x in xs)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    denominator = count * sum_xx - sum_x * sum_x
    if denominator <= 0:
        return 0.0
    slope = (count * sum_xy - sum_x * sum_y) / denominator
    return clamp_float(slope, 0.0, MAX_TEMPERATURE_SLOPE)


def power_step_lead(samples: list[ThermalSample], power: Callable[[ThermalSample], float], gain: float) -> float:
    if len(samples) < PREDICTION_MIN_SAMPLES:
        return 0.0
    current = power(samples[-1])
    if current <= 0:
        return 0.0
    previous = [power(sample) for sample in samples[:-1] if power(sample) > 0]
    if len(previous) < 2:
        return 0.0
    surge = current - sum(previous) / len(previous)
    if surge <= 5:
        return 0.0
    return min(surge * 0.018 * gain, MAX_POWER_LEAD)


def resolve_control_temp(cpu_temp: int, gpu_temp: int, source: str) -> int:
    if source == "cpu":
        return cpu_temp
    if source == "gpu":
        return gpu_temp
    return max(cpu_temp, gpu_temp)


def normalize_curve(curve: list[dict]) -> list[dict]:
    points = []
    for point in curve:
        if not isinstance(point, dict) or "temperature" not in point or "rpm" not in point:
            continue
        points.append({"temperature": int(point["temperature"]), "rpm": int(point["rpm"])})
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


def curve_bounds(curve: list[dict]) -> tuple[int, int]:
    if not curve:
        return 0, 4000
    rpms = [int(p["rpm"]) for p in curve]
    return min(rpms), max(rpms)


def clamp_offset_for_point(offset: int, base_rpm: int, min_rpm: int, max_rpm: int, max_offset: int) -> int:
    low = max(min_rpm - base_rpm, -max_offset)
    high = min(max_rpm - base_rpm, max_offset)
    if low > high:
        return 0
    return clamp(offset, low, high)


def constrain_offsets_to_learning_bias(offsets: list[int], bias: str) -> tuple[list[int], bool]:
    if not offsets:
        return offsets, False
    if bias not in {"cooling", "quiet"}:
        return offsets, False
    out = list(offsets)
    changed = False
    for i, offset in enumerate(out):
        if bias == "cooling" and offset < 0:
            out[i] = 0
            changed = True
        elif bias == "quiet" and offset > 0:
            out[i] = 0
            changed = True
    return out, changed


def enforce_non_decreasing_rpm(curve: list[dict]) -> None:
    for i in range(1, len(curve)):
        if curve[i]["rpm"] < curve[i - 1]["rpm"]:
            curve[i]["rpm"] = curve[i - 1]["rpm"]


def stable_sample_window(cfg: dict) -> int:
    return clamp(int(cfg.get("learn_window", STABLE_MIN_SAMPLES)), 3, 24)


def stable_sample_delay(cfg: dict) -> int:
    return clamp(max(int(cfg.get("learn_delay", 3)), 0), 0, 8)


def stable_rpm_range(cfg: dict) -> int:
    return max(STABLE_RPM_BAND, int(cfg.get("min_rpm_change", 50)))


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


def stale_equilibrium(point: EqPoint, rpm: int, temp: int) -> bool:
    if point.rpm < rpm:
        if point.temp + EQ_CONSISTENCY_BAND < temp:
            return True
        max_drop = EFF_CEIL_PER_RPM * (rpm - point.rpm) + EQ_CONSISTENCY_BAND
        return point.temp - temp > max_drop
    if point.rpm > rpm:
        if point.temp > temp + EQ_CONSISTENCY_BAND:
            return True
        max_drop = EFF_CEIL_PER_RPM * (point.rpm - rpm) + EQ_CONSISTENCY_BAND
        return temp - point.temp > max_drop
    return False


def alpha_from_learn_rate(learn_rate: int) -> float:
    learn_rate = clamp(learn_rate, 1, 10)
    return 0.025 + (learn_rate - 1) * 0.0125


def effective_offset_cap(cfg: dict) -> int:
    cap = int(cfg.get("max_learn_offset", 300))
    if cap <= 0 or cap > HARD_OFFSET_CAP:
        return HARD_OFFSET_CAP
    return cap


def target_temp_ceiling(cfg: dict) -> int:
    target = int(cfg.get("target_temp", 0))
    return target if target > 0 else DEFAULT_TARGET_TEMP


def comfort_band_width(cfg: dict) -> int:
    return max(int(cfg.get("hysteresis", 2)) + 3, 3)


def noise_down_gain(rpm: int, cfg: dict) -> float:
    profile = cfg.get("noise_profile") or []
    if len(profile) < NOISE_PROFILE_MIN_POINTS or int(cfg.get("noise_weight", 4)) <= 0 or rpm <= 0:
        return 1.0
    cleaned = []
    for item in profile:
        if not isinstance(item, dict):
            continue
        point_rpm = int(item.get("rpm") or 0)
        db = float(item.get("db") or 0.0)
        cleaned.append({"rpm": point_rpm, "db": db})
    cleaned.sort(key=lambda p: p["rpm"])
    if len(cleaned) < NOISE_PROFILE_MIN_POINTS:
        return 1.0
    span = cleaned[-1]["rpm"] - cleaned[0]["rpm"]
    total_rise = cleaned[-1]["db"] - cleaned[0]["db"]
    if span < NOISE_PROFILE_MIN_SPAN_RPM or total_rise < NOISE_PROFILE_MIN_RISE_DB:
        return 1.0
    local, ok = local_noise_slope(rpm, cleaned)
    if not ok:
        return 1.0
    avg_slope = total_rise / span
    if avg_slope <= 0:
        return 1.0
    raw = clamp_float(local / avg_slope, NOISE_GAIN_RAW_MIN, NOISE_GAIN_RAW_MAX)
    influence = min(float(cfg.get("noise_weight", 4)) / NOISE_WEIGHT_BASELINE, 1.5)
    gain = 1 + (raw - 1) * influence
    return clamp_float(gain, NOISE_GAIN_MIN, NOISE_GAIN_MAX)


def local_noise_slope(rpm: int, profile: list[dict]) -> tuple[float, bool]:
    if len(profile) < 2:
        return 0.0, False
    seg = len(profile) - 2
    for i in range(len(profile) - 1):
        if rpm < profile[i + 1]["rpm"]:
            seg = i
            break
    lo = max(seg - 1, 0)
    hi = min(seg + 2, len(profile) - 1)
    span = profile[hi]["rpm"] - profile[lo]["rpm"]
    if span <= 0:
        return 0.0, False
    slope = (profile[hi]["db"] - profile[lo]["db"]) / span
    return max(slope, 0.0), True


def should_apply_ramp_limit(target: int, previous: int) -> bool:
    return previous > 0 or target == 0


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


def keep_tail(items: list, size: int) -> list:
    return items[-size:]


def median3(a: int, b: int, c: int) -> int:
    if a > b:
        a, b = b, a
    if b > c:
        b, c = c, b
    if a > b:
        a, b = b, a
    return b


def round_float(value: float) -> int:
    if value >= 0:
        return int(value + 0.5)
    return int(value - 0.5)


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
