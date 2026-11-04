import logging
from logging.handlers import RotatingFileHandler

from .bot import build_app
from .config import PROJECT_ROOT, load_settings


def main() -> None:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(log_dir / "fa_bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    settings = load_settings()
    build_app(settings).run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
