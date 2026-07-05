import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging() -> None:
    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "voice-bridge.log"

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(formatter)

    root = logging.getLogger()
    if not any(
        isinstance(existing, RotatingFileHandler)
        and getattr(existing, "baseFilename", "") == str(log_file.resolve())
        for existing in root.handlers
    ):
        root.addHandler(handler)
    root.setLevel(logging.INFO)

    logging.getLogger("call_hermes").info("logging initialized: %s", log_file)
