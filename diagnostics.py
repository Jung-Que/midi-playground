import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import threading
from paths import user_data_root


_installed = False
_log_path = None


def log_directory() -> Path:
    return user_data_root() / "logs"


def install_exception_logging() -> Path:
    """Install one rotating crash log for source and packaged runs."""
    global _installed, _log_path
    if _installed:
        return _log_path

    directory = log_directory()
    directory.mkdir(parents=True, exist_ok=True)
    _log_path = directory / "midi-playground.log"
    handler = RotatingFileHandler(
        _log_path,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    original_hook = sys.excepthook

    def log_unhandled(exc_type, exc_value, exc_traceback):
        logging.getLogger("midi_playground.crash").critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        original_hook(exc_type, exc_value, exc_traceback)

    sys.excepthook = log_unhandled

    if hasattr(threading, "excepthook"):
        original_thread_hook = threading.excepthook

        def log_thread_exception(args):
            logging.getLogger("midi_playground.crash").critical(
                "Unhandled thread exception in %s",
                args.thread.name if args.thread else "unknown",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            original_thread_hook(args)

        threading.excepthook = log_thread_exception

    _installed = True
    logging.getLogger("midi_playground").info("Diagnostics started: %s", _log_path)
    return _log_path
