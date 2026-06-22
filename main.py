# main.py
import os
import sys
import logging

# 1. Загружаем конфиг
from config import config

# 2. ВАЖНО: Проксируем трафик ДО импорта pyvkbot и requests, если прокси указан в .env
if config.HTTPS_PROXY:
    os.environ["HTTPS_PROXY"] = config.HTTPS_PROXY
    os.environ["HTTP_PROXY"] = config.HTTPS_PROXY
    print(f"🌐 Используется прокси для обхода блокировки: {config.HTTPS_PROXY}")

from pyvkbot import Bot
from database import DatabaseManager
from bot_logic import CampBot

def main():
    class SafeStreamHandler(logging.StreamHandler):
        def emit(self, record):
            try:
                super().emit(record)
            except UnicodeEncodeError:
                msg = self.format(record)
                try:
                    sys.stdout.buffer.write((msg + self.terminator).encode("utf-8", errors="replace"))
                    sys.stdout.buffer.flush()
                except Exception:
                    pass

    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            SafeStreamHandler(sys.stdout),
            logging.FileHandler("bot.log", encoding="utf-8")
        ]
    )

    logging.getLogger("pyvkbot").setLevel(logging.INFO)
    logger = logging.getLogger(__name__)

    if not config.VK_BOT_TOKEN or config.VK_GROUP_ID == 0:
        logger.error("Zapolnite VK_BOT_TOKEN i VK_GROUP_ID v .env")
        sys.exit(1)

    try:
        db = DatabaseManager(config.DB_PATH)
        logger.info("Baza dannyh podklyuchena")
    except Exception as e:
        logger.error(f"Oshibka BD: {e}", exc_info=True)
        sys.exit(1)

    try:
        bot = Bot(token=config.VK_BOT_TOKEN, group_id=config.VK_GROUP_ID)
        CampBot(bot, db)

        logger.info("Bot uspeshno zapushchen!")
        bot.start_polling()
    except Exception as e:
        logger.error(f"Kriticheskaya oshibka pri zapuske: {e}", exc_info=True)
        logger.error("Esli oshibka svyazana s 'getaddrinfo failed' ili 'api.vk.com', proverte nastrojki proksi v .env ili zapustite bota na VPS-servere.")
        sys.exit(1)

if __name__ == "__main__":
    main()