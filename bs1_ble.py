from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

try:
    from bleak import BleakClient, BleakScanner
except Exception:  # pragma: no cover - bleak is optional until installed.
    BleakClient = None
    BleakScanner = None

SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"

@dataclass
class BS1Status:
    current_rpm: int = 0
    target_rpm: int = 0
    mode: int = 0
    work_mode: str = ""
    gear_setting: str = ""
    max_gear: str = ""
    selected_gear: str = ""


def make_frame(cmd: int, payload: bytes = b"") -> bytes:
    length = 2 + len(payload)
    checksum = (cmd + length + sum(payload)) & 0xFF
    return bytes([0x5A, 0xA5, cmd & 0xFF, length & 0xFF]) + payload + bytes([checksum])


ENTER_DYNAMIC = make_frame(0x46, b"\x01")
HEARTBEAT_1 = make_frame(0x23)
HEARTBEAT_2 = make_frame(0x45)


def rpm_frame(rpm: int) -> bytes:
    rpm = max(0, min(5000, int(rpm)))
    return make_frame(0x21, bytes([rpm & 0xFF, (rpm >> 8) & 0xFF]))


def parse_status_notify(data: bytes | bytearray) -> BS1Status | None:
    raw = bytes(data)
    if len(raw) < 9 or raw[0:2] != b"\x5A\xA5" or raw[2] != 0xEF:
        return None
    gear = raw[4]
    mode = raw[5]
    current = int.from_bytes(raw[7:9], "little")
    target = int.from_bytes(raw[9:11], "little") if len(raw) >= 11 else 0
    max_gear, selected = decode_gear_setting(gear)
    return BS1Status(
        current_rpm=current,
        target_rpm=target,
        mode=mode,
        work_mode=decode_work_mode(mode),
        gear_setting=f"0x{gear:02X}",
        max_gear=max_gear,
        selected_gear=selected,
    )


def decode_work_mode(mode: int) -> str:
    return "auto/realtime" if mode & 0x01 else "gear"


def decode_gear_setting(value: int) -> tuple[str, str]:
    max_map = {0x2: "standard", 0x4: "performance", 0x6: "extreme"}
    selected_map = {0x8: "quiet", 0xA: "standard", 0xC: "performance", 0xE: "extreme"}
    return (
        max_map.get((value >> 4) & 0x0F, f"unknown(0x{(value >> 4) & 0x0F:X})"),
        selected_map.get(value & 0x0F, f"unknown(0x{value & 0x0F:X})"),
    )


class BS1BleClient:
    def __init__(
        self,
        status_callback: Callable[[BS1Status], None],
        disconnect_callback: Callable[[str], None],
    ) -> None:
        self.status_callback = status_callback
        self.disconnect_callback = disconnect_callback
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.client = None
        self.device_name = ""
        self.device_address = ""
        self._heartbeat_task: asyncio.Task | None = None
        self._lock = threading.RLock()

    def start(self) -> None:
        if self.loop:
            return
        ready = threading.Event()

        def runner() -> None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=runner, name="bs1-ble-loop", daemon=True)
        self.thread.start()
        ready.wait(timeout=3)

    def stop(self) -> None:
        if not self.loop:
            return
        try:
            self.disconnect(timeout=5)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)

    def is_connected(self) -> bool:
        with self._lock:
            return bool(self.client and self.client.is_connected)

    def connect(self, timeout: float = 15) -> bool:
        return bool(self.call(self._connect(), timeout=timeout))

    def set_target_rpm(self, rpm: int, timeout: float = 5) -> bool:
        return bool(self.call(self._set_target_rpm(rpm), timeout=timeout))

    def disconnect(self, timeout: float = 3) -> None:
        self.call(self._disconnect(), timeout=timeout)

    def call(self, coro, timeout: float):
        self.start()
        if not self.loop:
            raise RuntimeError("BLE loop unavailable")
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=timeout)

    async def _connect(self) -> bool:
        if BleakScanner is None or BleakClient is None:
            raise RuntimeError("Missing dependency: install bleak first")
        if self.client and self.client.is_connected:
            return True
        devices = await BleakScanner.discover(timeout=8)
        target = None
        for dev in devices:
            name = dev.name or ""
            if "BS1" in name:
                target = dev
                break
            if not target and "Flydigi" in name:
                target = dev
        if not target:
            raise RuntimeError("BS1 BLE device not found")
        client = BleakClient(target, disconnected_callback=self._on_disconnect)
        await client.connect()
        await client.start_notify(NOTIFY_UUID, self._on_notify)
        with self._lock:
            self.client = client
            self.device_name = target.name or "Flydigi BS1"
            self.device_address = str(target.address)
        if not self._heartbeat_task or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        return True

    async def _disconnect(self) -> None:
        with self._lock:
            client = self.client
            self.client = None
        heartbeat_task = self._heartbeat_task
        if heartbeat_task and heartbeat_task is not asyncio.current_task():
            heartbeat_task.cancel()
            self._heartbeat_task = None
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def _write(self, frame: bytes) -> None:
        with self._lock:
            client = self.client
        if not client or not client.is_connected:
            raise RuntimeError("BS1 is not connected")
        try:
            await client.write_gatt_char(WRITE_UUID, frame, response=False)
        except Exception:
            await client.write_gatt_char(WRITE_UUID, frame, response=True)

    async def _set_target_rpm(self, rpm: int) -> bool:
        await self._write(ENTER_DYNAMIC)
        await asyncio.sleep(0.05)
        await self._write(rpm_frame(rpm))
        return True

    async def _heartbeat_loop(self) -> None:
        index = 0
        while True:
            await asyncio.sleep(3)
            frame = HEARTBEAT_1 if index % 2 == 0 else HEARTBEAT_2
            index += 1
            try:
                await self._write(frame)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.disconnect_callback(str(exc))
                await self._disconnect()
                return

    def _on_notify(self, _sender, data: bytearray) -> None:
        status = parse_status_notify(data)
        if status:
            self.status_callback(status)

    def _on_disconnect(self, _client) -> None:
        with self._lock:
            self.client = None
        self.disconnect_callback("BS1 BLE disconnected")
