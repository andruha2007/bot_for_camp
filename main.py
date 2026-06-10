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
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("bot.log", encoding="utf-8")
        ]
    )

    logging.getLogger("pyvkbot").setLevel(logging.INFO)
    logger = logging.getLogger(__name__)

    if not config.VK_BOT_TOKEN or config.VK_GROUP_ID == 0:
        logger.error("❌ Заполните VK_BOT_TOKEN и VK_GROUP_ID в .env")
        sys.exit(1)

    try:
        db = DatabaseManager(config.DB_PATH)
        logger.info("✅ База данных подключена")
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}", exc_info=True)
        sys.exit(1)

    try:
        # Инициализация нативного бота pyvkbot
        bot = Bot(token=config.VK_BOT_TOKEN, group_id=config.VK_GROUP_ID)
        CampBot(bot, db)

        logger.info("🚀 Бот успешно запущен!")
        bot.start_polling()
    except Exception as e:
        logger.error(f"💥 Критическая ошибка при запуске: {e}", exc_info=True)
        logger.error("💡 Если ошибка связана с 'getaddrinfo failed' или 'api.vk.com', проверьте настройки прокси в .env или запустите бота на VPS-сервере.")
        sys.exit(1)

if __name__ == "__main__":
    main()