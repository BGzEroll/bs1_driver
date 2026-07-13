from __future__ import annotations

WEB_PORT = 1919
CONFIG_NAME = "bs1-controller.config"

DEFAULT_FAN_CURVE = [
    {"temperature": 30, "rpm": 1000},
    {"temperature": 35, "rpm": 1200},
    {"temperature": 40, "rpm": 1400},
    {"temperature": 45, "rpm": 1600},
    {"temperature": 50, "rpm": 1800},
    {"temperature": 55, "rpm": 2000},
    {"temperature": 60, "rpm": 2300},
    {"temperature": 65, "rpm": 2600},
    {"temperature": 70, "rpm": 2900},
    {"temperature": 75, "rpm": 3200},
    {"temperature": 80, "rpm": 3500},
    {"temperature": 85, "rpm": 3800},
    {"temperature": 90, "rpm": 4000},
    {"temperature": 95, "rpm": 4000},
    {"temperature": 100, "rpm": 4000},
    {"temperature": 105, "rpm": 4000},
    {"temperature": 110, "rpm": 4000},
]

DEFAULT_SMART_CONTROL = {
    "learning": True,
    "predictive_boost": True,
    "learning_bias": "balanced",
    "filter_transient_spike": True,
    "target_temp": 68,
    "aggressiveness": 5,
    "hysteresis": 2,
    "min_rpm_change": 50,
    "ramp_up_limit": 220,
    "ramp_down_limit": 160,
    "learn_rate": 3,
    "learn_window": 8,
    "learn_delay": 3,
    "overheat_weight": 8,
    "rpm_delta_weight": 5,
    "noise_weight": 4,
    "trend_gain": 5,
    "max_learn_offset": 300,
    "learned_offsets": [],
    "noise_profile": [],
    "noise_profile_updated_at": 0,
}


def default_config() -> dict:
    return {
        "version": 1,
        "web_port": WEB_PORT,
        "autostart": False,
        "temp_update_rate": 2,
        "temp_source": "max",
        "fan_curve": [dict(point) for point in DEFAULT_FAN_CURVE],
        "smart_control": dict(DEFAULT_SMART_CONTROL),
    }
