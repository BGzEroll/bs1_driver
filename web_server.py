from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from defaults import WEB_PORT


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class WebServer:
    def __init__(self, controller, host: str = "127.0.0.1", port: int = WEB_PORT):
        self.controller = controller
        self.host = host
        self.port = port
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        handler = self.make_handler()
        self.httpd = ReusableThreadingHTTPServer((self.host, self.port), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="bs1-web", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()

    def make_handler(self):
        controller = self.controller

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path == "/":
                    self.send_text(INDEX_HTML, "text/html; charset=utf-8")
                elif path == "/style.css":
                    self.send_text(STYLE_CSS, "text/css; charset=utf-8")
                elif path == "/app.js":
                    self.send_text(APP_JS, "application/javascript; charset=utf-8")
                elif path == "/api/state":
                    self.send_json(controller.state.snapshot())
                elif path == "/api/config":
                    cfg = controller.get_config()
                    cfg["config_path"] = str(controller.config_store.path)
                    self.send_json(cfg)
                else:
                    self.send_error(404)

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                try:
                    if path == "/api/config":
                        payload = self.read_json()
                        cfg = controller.update_config(payload)
                        self.send_json(cfg)
                    elif path == "/api/reset-learning":
                        cfg = controller.reset_learning()
                        self.send_json(cfg)
                    elif path == "/api/reconnect":
                        threading.Thread(target=controller.reconnect, daemon=True).start()
                        self.send_json({"ok": True})
                    else:
                        self.send_error(404)
                except Exception as exc:
                    self.send_json({"ok": False, "error": str(exc)}, status=500)

            def read_json(self) -> dict:
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length <= 0:
                    return {}
                data = self.rfile.read(length)
                parsed = json.loads(data.decode("utf-8"))
                if not isinstance(parsed, dict):
                    raise ValueError("JSON body must be an object")
                return parsed

            def send_json(self, data: Any, status: int = 200) -> None:
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def send_text(self, text: str, content_type: str) -> None:
                body = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>BS1 Controller</title>
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <main>
    <header>
      <div>
        <h1>BS1 Controller</h1>
        <p>BLE 自动控温 · Web 端口 1919</p>
      </div>
      <button id="reconnect">重新连接</button>
    </header>

    <section class="status-grid">
      <article><span>蓝牙</span><strong id="connected">--</strong></article>
      <article><span>CPU</span><strong id="cpu">--</strong></article>
      <article><span>GPU</span><strong id="gpu">--</strong></article>
      <article><span>控制温度</span><strong id="control-temp">--</strong></article>
      <article><span>当前 RPM</span><strong id="current-rpm">--</strong></article>
      <article><span>目标 RPM</span><strong id="target-rpm">--</strong></article>
    </section>

    <section class="panel">
      <div class="panel-title">
        <h2>智能控温</h2>
        <button id="save">保存配置</button>
      </div>
      <div class="form-grid">
        <label>目标温度 <input id="target-temp" type="number" min="45" max="90" /></label>
        <label>最小 RPM 变化 <input id="min-rpm-change" type="number" min="20" max="400" /></label>
        <label>升速限幅 <input id="ramp-up" type="number" min="50" max="1200" /></label>
        <label>降速限幅 <input id="ramp-down" type="number" min="50" max="1200" /></label>
        <label>学习速率 <input id="learn-rate" type="number" min="1" max="10" /></label>
        <label>学习窗口 <input id="learn-window" type="number" min="3" max="24" /></label>
        <label>学习延迟 <input id="learn-delay" type="number" min="0" max="8" /></label>
        <label>滞回温差 <input id="hysteresis" type="number" min="0" max="8" /></label>
      </div>
      <div class="toggles">
        <label><input id="learning" type="checkbox" /> 自动学习</label>
        <label><input id="spike-filter" type="checkbox" /> 温度尖峰过滤</label>
        <label><input id="predictive" type="checkbox" /> 预测前馈</label>
        <select id="learning-bias">
          <option value="balanced">均衡</option>
          <option value="cooling">偏散热</option>
          <option value="quiet">偏安静</option>
        </select>
      </div>
    </section>

    <section class="panel">
      <div class="panel-title">
        <h2>单一风扇曲线</h2>
        <button id="reset-learning">清空学习偏移</button>
      </div>
      <svg id="curve-chart" viewBox="0 0 720 220" role="img"></svg>
      <div id="curve-table" class="curve-table"></div>
    </section>

    <section class="panel subtle">
      <div>配置文件：<code id="config-path">--</code></div>
      <div>最近状态：<span id="error">--</span></div>
    </section>
  </main>
  <script src="/app.js"></script>
</body>
</html>
"""


STYLE_CSS = r""":root {
  color-scheme: light;
  --bg: #f5faf8;
  --panel: #ffffff;
  --ink: #14221f;
  --muted: #65736f;
  --line: #dbe7e3;
  --accent: #1f9d7a;
  --accent-2: #2878b8;
  --warn: #c05621;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: radial-gradient(circle at 20% 0%, #e2f5ef 0, transparent 32rem), var(--bg);
  color: var(--ink);
  font-family: "Segoe UI", system-ui, sans-serif;
}
main { max-width: 1120px; margin: 0 auto; padding: 28px; }
header { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 20px; }
h1 { margin: 0; font-size: 30px; }
h2 { margin: 0; font-size: 18px; }
p { margin: 6px 0 0; color: var(--muted); }
button {
  border: 1px solid var(--line);
  background: var(--ink);
  color: white;
  border-radius: 8px;
  padding: 9px 13px;
  cursor: pointer;
}
button:hover { background: #233c36; }
.status-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}
article, .panel {
  background: color-mix(in srgb, var(--panel) 92%, transparent);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 8px 30px rgba(20,34,31,.06);
}
article { padding: 14px; }
article span { display: block; color: var(--muted); font-size: 12px; }
article strong { display: block; margin-top: 8px; font-size: 22px; }
.panel { padding: 16px; margin-top: 12px; }
.panel-title { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 14px; }
.form-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
label { color: var(--muted); font-size: 13px; }
input, select {
  width: 100%;
  margin-top: 5px;
  border: 1px solid var(--line);
  border-radius: 7px;
  padding: 8px 9px;
  background: #fbfefd;
  color: var(--ink);
}
.toggles { display: flex; align-items: center; flex-wrap: wrap; gap: 14px; margin-top: 14px; }
.toggles label { display: flex; align-items: center; gap: 7px; }
.toggles input { width: auto; margin: 0; }
.curve-table { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }
.curve-point { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; align-items: center; }
.curve-point span { color: var(--muted); font-size: 12px; }
svg { width: 100%; height: 220px; background: #f7fbfa; border: 1px solid var(--line); border-radius: 8px; }
.subtle { color: var(--muted); display: grid; gap: 8px; }
code { color: var(--accent-2); }
@media (max-width: 860px) {
  main { padding: 18px; }
  .status-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .form-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .curve-table { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
"""


APP_JS = r"""let config = null;

const $ = (id) => document.getElementById(id);

async function getJson(url) {
  const r = await fetch(url);
  return await r.json();
}

async function postJson(url, body = {}) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return await r.json();
}

function tempText(v) { return v > 0 ? `${v}°C` : '--'; }
function rpmText(v) { return v > 0 ? `${v}` : '--'; }

async function refreshState() {
  const s = await getJson('/api/state');
  $('connected').textContent = s.connected ? '已连接' : '未连接';
  $('cpu').textContent = tempText(s.cpu_temp || 0);
  $('gpu').textContent = tempText(s.gpu_temp || 0);
  $('control-temp').textContent = tempText(s.control_temp || 0);
  $('current-rpm').textContent = rpmText(s.current_rpm || 0);
  $('target-rpm').textContent = rpmText(s.target_rpm || s.last_sent_rpm || 0);
  $('error').textContent = s.last_error || '正常';
}

async function loadConfig() {
  config = await getJson('/api/config');
  const sc = config.smart_control;
  $('config-path').textContent = config.config_path || '';
  $('target-temp').value = sc.target_temp;
  $('min-rpm-change').value = sc.min_rpm_change;
  $('ramp-up').value = sc.ramp_up_limit;
  $('ramp-down').value = sc.ramp_down_limit;
  $('learn-rate').value = sc.learn_rate;
  $('learn-window').value = sc.learn_window;
  $('learn-delay').value = sc.learn_delay;
  $('hysteresis').value = sc.hysteresis;
  $('learning').checked = !!sc.learning;
  $('spike-filter').checked = !!sc.filter_transient_spike;
  $('predictive').checked = !!sc.predictive_boost;
  $('learning-bias').value = sc.learning_bias || 'balanced';
  renderCurve();
}

function renderCurve() {
  const table = $('curve-table');
  table.innerHTML = '';
  config.fan_curve.forEach((p, i) => {
    const row = document.createElement('label');
    row.className = 'curve-point';
    row.innerHTML = `<span>${p.temperature}°C</span><input data-idx="${i}" type="number" min="0" max="5000" value="${p.rpm}">`;
    table.appendChild(row);
  });
  drawChart();
}

function drawChart() {
  const svg = $('curve-chart');
  const points = config.fan_curve;
  const minT = points[0].temperature, maxT = points[points.length - 1].temperature;
  const minR = Math.min(...points.map(p => p.rpm)), maxR = Math.max(...points.map(p => p.rpm));
  const x = (t) => 36 + (t - minT) / (maxT - minT) * 648;
  const y = (r) => 184 - (r - minR) / Math.max(1, maxR - minR) * 148;
  const d = points.map((p, i) => `${i ? 'L' : 'M'} ${x(p.temperature).toFixed(1)} ${y(p.rpm).toFixed(1)}`).join(' ');
  svg.innerHTML = `
    <line x1="36" y1="184" x2="684" y2="184" stroke="#cbd8d4"/>
    <line x1="36" y1="36" x2="36" y2="184" stroke="#cbd8d4"/>
    <path d="${d}" fill="none" stroke="#1f9d7a" stroke-width="3"/>
    ${points.map(p => `<circle cx="${x(p.temperature)}" cy="${y(p.rpm)}" r="4" fill="#2878b8"/>`).join('')}
  `;
}

async function saveConfig() {
  const smart = {
    target_temp: Number($('target-temp').value),
    min_rpm_change: Number($('min-rpm-change').value),
    ramp_up_limit: Number($('ramp-up').value),
    ramp_down_limit: Number($('ramp-down').value),
    learn_rate: Number($('learn-rate').value),
    learn_window: Number($('learn-window').value),
    learn_delay: Number($('learn-delay').value),
    hysteresis: Number($('hysteresis').value),
    learning: $('learning').checked,
    filter_transient_spike: $('spike-filter').checked,
    predictive_boost: $('predictive').checked,
    learning_bias: $('learning-bias').value
  };
  const curve = config.fan_curve.map((p, i) => ({ temperature: p.temperature, rpm: Number(document.querySelector(`input[data-idx="${i}"]`).value) }));
  config = await postJson('/api/config', { smart_control: smart, fan_curve: curve });
  renderCurve();
}

$('save').addEventListener('click', saveConfig);
$('reconnect').addEventListener('click', () => postJson('/api/reconnect'));
$('reset-learning').addEventListener('click', async () => { config = await postJson('/api/reset-learning'); renderCurve(); });

loadConfig();
refreshState();
setInterval(refreshState, 1000);
"""
