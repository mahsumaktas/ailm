"""ailm CLI entry point."""

import argparse
import asyncio
import logging
import signal
import sys

from ailm import __version__

logger = logging.getLogger(__name__)


async def run_headless(config) -> None:
    """Run ailm without GUI — blocks until interrupted."""
    from ailm.app import Application

    app = Application(config)
    await app.start()
    await app.maybe_insert_welcome()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    # SIGHUP → config reload without restart
    loop.add_signal_handler(
        signal.SIGHUP,
        lambda: asyncio.ensure_future(app.reload_config()),
    )

    try:
        await stop_event.wait()
    finally:
        await app.stop()


def run_with_ui(config) -> None:
    """Run ailm with PySide6 system tray UI."""
    from PySide6.QtWidgets import QApplication

    from ailm.ui import AilmTray, AsyncioBridge, FeedPopup

    qt_app = QApplication(sys.argv)
    bridge = AsyncioBridge()
    tray = AilmTray()
    popup = FeedPopup()
    popup.resize(config.ui.popup_width, config.ui.popup_height)

    # Wire tray → popup
    tray.show_feed_requested.connect(popup.show_near_tray)

    # Wire bus events → feed UI
    def on_event_received(event):
        popup.add_event(event)

    bridge.event_received.connect(on_event_received)

    # Wire status changes → tray icon
    def on_status_changed(status_str):
        from ailm.core.models import SystemStatus
        try:
            tray.set_status(SystemStatus(status_str))
        except ValueError:
            pass

    bridge.status_changed.connect(on_status_changed)

    tray.show()
    bridge.start()

    # Application lifecycle in asyncio thread
    app_ref = [None]  # mutable ref for shutdown closure

    async def start_app():
        from ailm.app import Application
        app = Application(config)
        app_ref[0] = app
        await app.start()
        await app.maybe_insert_welcome()

        # Forward events to Qt
        app.bus.subscribe(None, lambda e: bridge.event_received.emit(e))

        # Forward status changes to Qt
        app.status_tracker.on_status_change(
            lambda old, new: bridge.status_changed.emit(new.value)
        )

        # Load recent events into feed
        if app.repo:
            events = await app.repo.get_recent_events(limit=50)
            for event in reversed(events):
                bridge.event_received.emit(event)

        logger.info("Application started with UI")

    async def stop_app():
        if app_ref[0] is not None:
            await app_ref[0].stop()
            app_ref[0] = None
        bridge.stop_loop()

    # Wire quit — no coroutine leak (lambda creates coro only when submitted)
    tray.quit_requested.connect(lambda: bridge.submit(stop_app()))

    # SIGTERM → graceful shutdown via Qt
    signal.signal(signal.SIGTERM, lambda *_: tray.quit_requested.emit())

    bridge.submit(start_app())
    sys.exit(qt_app.exec())


def main() -> None:
    """Parse CLI arguments, load configuration, and launch the selected mode."""
    parser = argparse.ArgumentParser(
        prog="ailm",
        description="AI-powered Linux system companion",
    )
    parser.add_argument("--version", action="version", version=f"ailm {__version__}")
    parser.add_argument(
        "--dump-config",
        action="store_true",
        help="Print current config and exit",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Run headless without GUI",
    )
    args = parser.parse_args()

    from ailm.config import dump_config, load_config
    from ailm.core.logging import setup_logging

    setup_logging()
    config = load_config()

    if args.dump_config:
        print(dump_config(config))
        sys.exit(0)

    if args.no_ui:
        asyncio.run(run_headless(config))
    else:
        run_with_ui(config)


if __name__ == "__main__":
    main()
