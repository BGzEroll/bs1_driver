from __future__ import annotations

import sys
from pathlib import Path

from autostart import is_autostart_enabled
from config_store import ConfigStore
from controller import Controller
from defaults import WEB_PORT
from ui_window import LocalWindow
from web_server import WebServer


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main() -> None:
    store = ConfigStore(app_dir())
    config = store.load()
    config["autostart"] = is_autostart_enabled()
    store.save(config)

    controller = Controller(store, config)
    controller.start()

    web = WebServer(controller, port=WEB_PORT)
    web.start()

    window = LocalWindow(controller, web)
    window.run()


if __name__ == "__main__":
    main()

