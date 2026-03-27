#!/usr/bin/env python3
"""ailm Control Panel — system tray widget for managing ailm service and LLM model.

Usage: python contrib/ailm-control.py
  - Left click: start/stop ailm
  - Right click: menu (model switch, restart, event count)
  - Green dot = running, grey dot = stopped
"""

import re
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

from PySide6.QtCore import QTimer, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

CONFIG_PATH = Path.home() / ".config" / "ailm" / "config.toml"
DB_PATH = Path.home() / ".local" / "share" / "ailm" / "ailm.db"
SERVICE = "ailm"


def _run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, timeout=10).stdout.strip()


def _is_running() -> bool:
    r = subprocess.run(["systemctl", "--user", "is-active", SERVICE],
                       capture_output=True, text=True)
    return r.stdout.strip() == "active"


def _current_model() -> str:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f).get("llm", {}).get("model", "qwen3.5:9b")
    return "qwen3.5:9b"


def _available_models() -> list[str]:
    try:
        out = _run(["ollama", "list"])
        return sorted(
            line.split()[0] for line in out.splitlines()[1:]
            if line.split() and "embed" not in line.split()[0]
        )
    except Exception:
        return []


def _set_model(model: str) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        text = CONFIG_PATH.read_text()
        if re.search(r'^model\s*=', text, re.MULTILINE):
            text = re.sub(r'^model\s*=\s*"[^"]*"', f'model = "{model}"', text, flags=re.MULTILINE)
        else:
            text = f'[llm]\nmodel = "{model}"\n' + text
        CONFIG_PATH.write_text(text)
    else:
        CONFIG_PATH.write_text(f'[llm]\nmodel = "{model}"\ntimeout = 120\n')


def _event_count() -> int:
    try:
        if DB_PATH.exists():
            conn = sqlite3.connect(str(DB_PATH))
            n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            conn.close()
            return n
    except Exception:
        pass
    return 0


def _make_icon(running: bool) -> QIcon:
    px = QPixmap(64, 64)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor("#4CAF50") if running else QColor("#9E9E9E")
    p.setBrush(color)
    p.setPen(color.darker(120))
    p.drawEllipse(4, 4, 56, 56)
    p.end()
    return QIcon(px)


class AilmControl(QSystemTrayIcon):
    def __init__(self):
        super().__init__()
        self._running = False
        self._build_menu()
        self._refresh()

        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(5000)
        self.activated.connect(self._on_click)

    def _build_menu(self):
        menu = QMenu()
        self._status_action = menu.addAction("ailm: ...")
        self._status_action.setEnabled(False)
        menu.addSeparator()

        self._toggle_action = menu.addAction("Start")
        self._toggle_action.triggered.connect(self._toggle)

        restart = menu.addAction("Restart")
        restart.triggered.connect(self._restart)
        menu.addSeparator()

        self._model_menu = menu.addMenu("Model")
        self._fill_models()
        menu.addSeparator()

        self._events_action = menu.addAction("Events: ...")
        self._events_action.setEnabled(False)
        menu.addSeparator()

        menu.addAction("Quit Control Panel").triggered.connect(QApplication.quit)
        self.setContextMenu(menu)

    def _fill_models(self):
        self._model_menu.clear()
        current = _current_model()
        for m in _available_models():
            a = self._model_menu.addAction(f"* {m}" if m == current else m)
            if m == current:
                a.setEnabled(False)
            else:
                a.triggered.connect(lambda _, model=m: self._switch(model))

    @Slot()
    def _refresh(self):
        self._running = _is_running()
        model = _current_model()
        status = "running" if self._running else "stopped"
        self.setIcon(_make_icon(self._running))
        self.setToolTip(f"ailm [{status}] {model}")
        self._status_action.setText(f"ailm: {status} | {model}")
        self._toggle_action.setText("Stop" if self._running else "Start")
        self._events_action.setText(f"Events: {_event_count()}")

    @Slot()
    def _toggle(self):
        _run(["systemctl", "--user", "stop" if self._running else "start", SERVICE])
        QTimer.singleShot(1500, self._refresh)

    @Slot()
    def _restart(self):
        _run(["systemctl", "--user", "restart", SERVICE])
        QTimer.singleShot(1500, self._refresh)

    def _switch(self, model: str):
        _set_model(model)
        self._fill_models()
        if self._running:
            _run(["systemctl", "--user", "restart", SERVICE])
        QTimer.singleShot(1500, self._refresh)

    @Slot()
    def _on_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    tray = AilmControl()
    tray.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
