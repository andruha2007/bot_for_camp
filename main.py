# main.py
import logging
import sys
from config import config
from database import DatabaseManager
from bot_logic import CampBotLogic
from vk_server import VKLongPollServer

def setup_logging(level: str):
    """Настраивает логирование"""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("bot.log", encoding="utf-8", mode="a")
        ]
    )

def main():
    # 1. Настройка логирования
    setup_logging(config.LOG_LEVEL)
    logger = logging.getLogger(__name__)

    # 2. Валидация конфигурации
    errors = config.validate()
    if errors:
        logger.error("Конфигурация некорректна:")
        for err in errors:
            logger.error(f"   {err}")
        if "VK_BOT_TOKEN" in str(errors) or "VK_GROUP_ID" in str(errors):
            logger.error("Заполните файл .env и перезапустите бота")
            sys.exit(1)

    logger.info("Инициализация компонентов...")
    logger.info(f"База данных: {config.DB_PATH}")
    logger.info(f"Группа VK: {config.VK_GROUP_ID}")

    # 3. Инициализация БД
    try:
        db = DatabaseManager(config.DB_PATH)
        logger.info("База данных подключена")
    except Exception as e:
        logger.error(f"Ошибка подключения к БД: {e}")
        sys.exit(1)

    # 4. Инициализация сервера VK
    vk_server = VKLongPollServer(
        token=config.VK_BOT_TOKEN,
        group_id=config.VK_GROUP_ID,
        api_version=config.LP_VERSION
    )

    # 5. Callback для отправки сообщений
    def send_callback(user_id: int, text: str, keyboard_payload: dict = None):
        if text:  # Не отправляем пустые сообщения
            vk_server.send_message(user_id, text, keyboard_payload)

    # 6. Инициализация бизнес-логики
    bot = CampBotLogic(db, send_callback)

    # 7. Запуск
    logger.info("Все компоненты готовы. Запуск бота...")
    logger.info("Нажмите Ctrl+C для остановки")

    try:
        vk_server.start_polling(
            message_handler=bot.handle_message,
            callback_handler=bot.handle_callback
        )
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()