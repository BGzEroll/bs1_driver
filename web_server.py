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
                    elif path == "/api/reset-defaults":
                        cfg = controller.reset_defaults()
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
      </div>
      <div class="header-actions">
        <button id="reconnect" type="button">重新连接</button>
        <button id="advanced-open" class="secondary" type="button">高级设置</button>
      </div>
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
        <div>
          <h2>风扇曲线</h2>
          <p class="subhead">基础曲线固定为 BS1 默认曲线，学习曲线由自动学习偏移实时生成。</p>
        </div>
        <button id="reset-learning" class="secondary" type="button">清空学习偏移</button>
      </div>
      <div class="legend">
        <span><i class="base"></i>基础曲线</span>
        <span><i class="learned"></i>学习曲线</span>
        <span><i class="current"></i><b id="current-temp-label">当前 --°C</b></span>
      </div>
      <div class="chart-wrap">
        <svg id="curve-chart" viewBox="0 0 760 300" role="img" aria-label="风扇曲线"></svg>
        <div id="curve-tooltip" class="curve-tooltip" aria-hidden="true"></div>
      </div>
    </section>

  </main>

  <div id="advanced-backdrop" class="modal-backdrop" hidden>
    <section class="advanced-dialog" role="dialog" aria-modal="true" aria-labelledby="advanced-title">
      <div class="advanced-header">
        <h2 id="advanced-title">高级设置</h2>
        <button id="advanced-close" class="icon-button secondary" type="button" aria-label="关闭高级设置" title="关闭">&times;</button>
      </div>
      <div class="advanced-content">
        <section class="advanced-section">
          <div class="section-title">
            <h3>智能控温</h3>
            <div class="actions">
              <button id="save" type="button">保存配置</button>
              <button id="reset-defaults" class="secondary" type="button">恢复默认配置</button>
            </div>
          </div>
          <div class="form-grid">
            <label>目标温度 <input id="target-temp" type="number" min="45" max="90" /></label>
            <label>最小 RPM 变化 <input id="min-rpm-change" type="number" min="20" max="400" /></label>
            <label>升速限幅 <input id="ramp-up" type="number" min="50" max="1200" /></label>
            <label>降速限幅 <input id="ramp-down" type="number" min="50" max="1200" /></label>
          </div>
          <div class="toggles">
            <label><input id="learning" type="checkbox" /> 自动学习</label>
            <label><input id="spike-filter" type="checkbox" /> 温度尖峰过滤</label>
            <label><input id="predictive" type="checkbox" /> 预测前馈</label>
          </div>
        </section>

        <section class="advanced-section advanced-status">
          <div>配置文件：<code id="config-path">--</code></div>
          <div>最近状态：<span id="error">--</span></div>
        </section>
      </div>
    </section>
  </div>
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
  --accent: #16856b;
  --accent-2: #2878b8;
  --danger: #d43b2a;
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
h1 { margin: 0; font-size: 30px; letter-spacing: 0; }
h2 { margin: 0; font-size: 18px; letter-spacing: 0; }
p { margin: 6px 0 0; color: var(--muted); }
button {
  border: 1px solid var(--ink);
  background: var(--ink);
  color: white;
  border-radius: 8px;
  padding: 9px 13px;
  cursor: pointer;
}
button:hover { background: #233c36; }
button.secondary {
  background: #fbfefd;
  color: var(--ink);
  border-color: var(--line);
}
button.secondary:hover { background: #eef7f3; }
.header-actions { display: flex; gap: 8px; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
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
.subhead { font-size: 13px; }
.form-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
label { color: var(--muted); font-size: 13px; }
input {
  width: 100%;
  margin-top: 5px;
  border: 1px solid var(--line);
  border-radius: 7px;
  padding: 8px 9px;
  background: #fbfefd;
  color: var(--ink);
}
.modal-backdrop {
  position: fixed;
  z-index: 100;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(20,34,31,.38);
}
.modal-backdrop[hidden] { display: none; }
.advanced-dialog {
  width: min(820px, 100%);
  max-height: calc(100vh - 48px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  box-shadow: 0 24px 70px rgba(20,34,31,.24);
}
.advanced-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 18px;
  border-bottom: 1px solid var(--line);
}
.icon-button {
  width: 36px;
  height: 36px;
  padding: 0;
  font-size: 24px;
  line-height: 1;
}
.advanced-content { overflow-y: auto; }
.advanced-section { padding: 18px; }
.advanced-section + .advanced-section { border-top: 1px solid var(--line); }
.section-title { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 14px; }
.section-title h3 { margin: 0; font-size: 16px; letter-spacing: 0; }
.advanced-status { color: var(--muted); display: grid; gap: 8px; font-size: 13px; }
body.modal-open { overflow: hidden; }
.toggles { display: flex; align-items: center; flex-wrap: wrap; gap: 14px; margin-top: 14px; }
.toggles label { display: flex; align-items: center; gap: 7px; }
.toggles input { width: auto; margin: 0; }
.legend { display: flex; flex-wrap: wrap; gap: 18px; align-items: center; margin: 0 0 10px; color: var(--muted); font-size: 13px; }
.legend span { display: inline-flex; align-items: center; gap: 7px; }
.legend i { width: 24px; height: 3px; border-radius: 999px; display: inline-block; }
.legend .base { background: var(--accent-2); }
.legend .learned { background: repeating-linear-gradient(to right, var(--accent) 0 7px, transparent 7px 12px); }
.legend .current { width: 2px; height: 18px; background: repeating-linear-gradient(to bottom, var(--danger) 0 4px, transparent 4px 8px); }
.chart-wrap { position: relative; }
svg { width: 100%; height: 300px; background: #f7fbfa; border: 1px solid var(--line); border-radius: 8px; }
.axis-label { fill: #65736f; font-size: 12px; }
.tick { stroke: #dbe7e3; stroke-width: 1; }
.curve-base { fill: none; stroke: var(--accent-2); stroke-width: 3; }
.curve-learned { fill: none; stroke: var(--accent); stroke-width: 3; stroke-dasharray: 8 7; stroke-linecap: round; }
.curve-node { cursor: crosshair; }
.node-hit { fill: transparent; pointer-events: all; }
.node-base { fill: #ffffff; stroke: var(--accent-2); stroke-width: 2; }
.node-learned { fill: #ffffff; stroke: var(--accent); stroke-width: 2; }
.curve-node:hover .node-base,
.curve-node:hover .node-learned { stroke-width: 3; }
.current-line { stroke: var(--danger); stroke-width: 2; stroke-dasharray: 6 6; }
.current-tag { fill: var(--danger); font-size: 12px; font-weight: 600; }
.curve-tooltip {
  position: absolute;
  min-width: 158px;
  padding: 10px 12px;
  border: 1px solid rgba(20,34,31,.12);
  border-radius: 8px;
  background: rgba(255,255,255,.96);
  box-shadow: 0 12px 34px rgba(20,34,31,.16);
  color: var(--ink);
  font-size: 12px;
  line-height: 1.7;
  opacity: 0;
  transform: translate(12px, -8px);
  pointer-events: none;
  transition: opacity .18s ease, transform .18s ease;
  z-index: 5;
}
.curve-tooltip.show { opacity: 1; transform: translate(12px, -12px); }
.curve-tooltip strong { display: block; font-size: 13px; margin-bottom: 4px; }
.curve-tooltip span { display: flex; justify-content: space-between; gap: 18px; color: var(--muted); }
.curve-tooltip b { color: var(--ink); font-weight: 600; }
code { color: var(--accent-2); }
@media (max-width: 860px) {
  main { padding: 18px; }
  header, .panel-title, .section-title { align-items: flex-start; flex-direction: column; }
  .status-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .form-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .actions { justify-content: flex-start; }
  .modal-backdrop { padding: 10px; }
  .advanced-dialog { max-height: calc(100vh - 20px); }
}
"""


APP_JS = r"""let config = null;
let currentTemp = 0;

const $ = (id) => document.getElementById(id);

async function getJson(url) {
  const r = await fetch(url);
  return await r.json();
}

async function postJson(url, body = {}) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  return await r.json();
}

function tempText(v) { return v > 0 ? `${Math.round(v)}°C` : '--'; }
function rpmText(v) { return v > 0 ? `${Math.round(v)}` : '--'; }
function clamp(v, low, high) { return Math.max(low, Math.min(high, v)); }

async function refreshState() {
  const s = await getJson('/api/state');
  currentTemp = Number(s.control_temp || 0);
  $('connected').textContent = s.connected ? '已连接' : '未连接';
  $('cpu').textContent = tempText(s.cpu_temp || 0);
  $('gpu').textContent = tempText(s.gpu_temp || 0);
  $('control-temp').textContent = tempText(currentTemp);
  $('current-rpm').textContent = rpmText(s.current_rpm || 0);
  $('target-rpm').textContent = rpmText(s.target_rpm || s.last_sent_rpm || 0);
  $('error').textContent = s.last_error || '正常';
  $('current-temp-label').textContent = currentTemp > 0 ? `当前 ${Math.round(currentTemp)}°C` : '当前 --°C';
  if (config) drawChart();
}

async function loadConfig() {
  config = await getJson('/api/config');
  const sc = config.smart_control;
  $('config-path').textContent = config.config_path || '';
  $('target-temp').value = sc.target_temp;
  $('min-rpm-change').value = sc.min_rpm_change;
  $('ramp-up').value = sc.ramp_up_limit;
  $('ramp-down').value = sc.ramp_down_limit;
  $('learning').checked = !!sc.learning;
  $('spike-filter').checked = !!sc.filter_transient_spike;
  $('predictive').checked = !!sc.predictive_boost;
  drawChart();
}

function learnedCurve() {
  const points = config.fan_curve || [];
  const offsets = config.smart_control?.learned_offsets || [];
  const cap = Math.min(Number(config.smart_control?.max_learn_offset || 300), 600);
  const rpms = points.map(p => p.rpm);
  const minRpm = Math.min(...rpms);
  const maxRpm = Math.max(...rpms);
  let lastRpm = 0;
  return points.map((p, i) => {
    const offset = clamp(Number(offsets[i] || 0), -cap, cap);
    const rpm = clamp(Math.max(lastRpm, p.rpm + offset), minRpm, maxRpm);
    lastRpm = rpm;
    return { temperature: p.temperature, rpm };
  });
}

function showCurveTooltip(event) {
  const tooltip = $('curve-tooltip');
  const wrap = tooltip.parentElement.getBoundingClientRect();
  const temp = event.currentTarget.dataset.temp;
  const baseRpm = event.currentTarget.dataset.baseRpm;
  const learnedRpm = event.currentTarget.dataset.learnedRpm;
  tooltip.innerHTML = `
    <strong>${temp}°C</strong>
    <span>基础曲线 <b>${baseRpm} RPM</b></span>
    <span>学习曲线 <b>${learnedRpm} RPM</b></span>
  `;
  tooltip.style.left = `${event.clientX - wrap.left + 12}px`;
  tooltip.style.top = `${event.clientY - wrap.top - 6}px`;
  tooltip.classList.add('show');
}

function moveCurveTooltip(event) {
  const tooltip = $('curve-tooltip');
  const wrap = tooltip.parentElement.getBoundingClientRect();
  tooltip.style.left = `${event.clientX - wrap.left + 12}px`;
  tooltip.style.top = `${event.clientY - wrap.top - 6}px`;
}

function hideCurveTooltip() {
  $('curve-tooltip').classList.remove('show');
}

function bindCurveTooltip() {
  document.querySelectorAll('.curve-node').forEach((node) => {
    node.addEventListener('mouseenter', showCurveTooltip);
    node.addEventListener('mousemove', moveCurveTooltip);
    node.addEventListener('mouseleave', hideCurveTooltip);
  });
}

function drawChart() {
  const svg = $('curve-chart');
  const base = config?.fan_curve || [];
  if (base.length < 2) {
    svg.innerHTML = '';
    return;
  }
  const learned = learnedCurve();
  const temps = base.map(p => p.temperature);
  const allRpms = base.concat(learned).map(p => p.rpm);
  const minT = Math.min(...temps);
  const maxT = Math.max(...temps);
  const minR = Math.max(0, Math.floor(Math.min(...allRpms) / 500) * 500);
  const maxR = Math.ceil(Math.max(...allRpms) / 500) * 500;
  const left = 56, right = 730, top = 26, bottom = 248;
  const width = right - left, height = bottom - top;
  const x = (t) => left + (t - minT) / (maxT - minT) * width;
  const y = (r) => bottom - (r - minR) / Math.max(1, maxR - minR) * height;
  const path = (points) => points.map((p, i) => `${i ? 'L' : 'M'} ${x(p.temperature).toFixed(1)} ${y(p.rpm).toFixed(1)}`).join(' ');
  const tempTicks = [30, 40, 50, 60, 70, 80, 90, 100, 110].filter(t => t >= minT && t <= maxT);
  const rpmTicks = [];
  for (let r = minR; r <= maxR; r += 500) rpmTicks.push(r);
  const currentX = currentTemp > 0 ? clamp(x(currentTemp), left, right) : null;
  svg.innerHTML = `
    ${rpmTicks.map(r => `
      <line class="tick" x1="${left}" y1="${y(r).toFixed(1)}" x2="${right}" y2="${y(r).toFixed(1)}"></line>
      <text class="axis-label" x="12" y="${(y(r) + 4).toFixed(1)}">${r}</text>
    `).join('')}
    ${tempTicks.map(t => `
      <line class="tick" x1="${x(t).toFixed(1)}" y1="${top}" x2="${x(t).toFixed(1)}" y2="${bottom}"></line>
      <text class="axis-label" x="${(x(t) - 10).toFixed(1)}" y="278">${t}°</text>
    `).join('')}
    <line x1="${left}" y1="${bottom}" x2="${right}" y2="${bottom}" stroke="#b9c9c4"></line>
    <line x1="${left}" y1="${top}" x2="${left}" y2="${bottom}" stroke="#b9c9c4"></line>
    <path class="curve-base" d="${path(base)}"></path>
    <path class="curve-learned" d="${path(learned)}"></path>
    ${base.map((p, i) => `
      <g class="curve-node" data-temp="${p.temperature}" data-base-rpm="${p.rpm}" data-learned-rpm="${learned[i].rpm}">
        <circle class="node-hit" cx="${x(p.temperature).toFixed(1)}" cy="${y(p.rpm).toFixed(1)}" r="12"></circle>
        <circle class="node-hit" cx="${x(p.temperature).toFixed(1)}" cy="${y(learned[i].rpm).toFixed(1)}" r="12"></circle>
        <circle class="node-base" cx="${x(p.temperature).toFixed(1)}" cy="${y(p.rpm).toFixed(1)}" r="4"></circle>
        <circle class="node-learned" cx="${x(p.temperature).toFixed(1)}" cy="${y(learned[i].rpm).toFixed(1)}" r="4"></circle>
      </g>
    `).join('')}
    ${currentX === null ? '' : `
      <line class="current-line" x1="${currentX.toFixed(1)}" y1="${top}" x2="${currentX.toFixed(1)}" y2="${bottom}"></line>
      <text class="current-tag" x="${clamp(currentX + 8, left, right - 72).toFixed(1)}" y="20">当前 ${Math.round(currentTemp)}°C</text>
    `}
  `;
  bindCurveTooltip();
}

async function saveConfig() {
  const smart = {
    target_temp: Number($('target-temp').value),
    min_rpm_change: Number($('min-rpm-change').value),
    ramp_up_limit: Number($('ramp-up').value),
    ramp_down_limit: Number($('ramp-down').value),
    learning: $('learning').checked,
    filter_transient_spike: $('spike-filter').checked,
    predictive_boost: $('predictive').checked
  };
  config = await postJson('/api/config', { smart_control: smart });
  await loadConfig();
}

async function resetDefaults() {
  config = await postJson('/api/reset-defaults');
  await loadConfig();
}

async function resetLearning() {
  config = await postJson('/api/reset-learning');
  drawChart();
}

$('save').addEventListener('click', saveConfig);
$('reconnect').addEventListener('click', () => postJson('/api/reconnect'));
$('reset-defaults').addEventListener('click', resetDefaults);
$('reset-learning').addEventListener('click', resetLearning);
function setAdvancedOpen(open) {
  $('advanced-backdrop').hidden = !open;
  document.body.classList.toggle('modal-open', open);
  if (open) $('advanced-close').focus();
  else $('advanced-open').focus();
}
$('advanced-open').addEventListener('click', () => setAdvancedOpen(true));
$('advanced-close').addEventListener('click', () => setAdvancedOpen(false));
$('advanced-backdrop').addEventListener('click', (event) => {
  if (event.target === event.currentTarget) setAdvancedOpen(false);
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !$('advanced-backdrop').hidden) setAdvancedOpen(false);
});

loadConfig();
refreshState();
setInterval(refreshState, 1000);
"""
