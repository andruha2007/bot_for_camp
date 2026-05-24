# config.py
import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

# Загружаем переменные из .env (ищет файл в корневой директории проекта)
# Находим корень проекта относительно этого файла
BASE_DIR = Path(__file__).resolve().parent.parent
env_path = BASE_DIR / ".env" if (BASE_DIR / ".env").exists() else Path(__file__).parent / ".env"
load_dotenv(env_path)

def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]

@dataclass
class Config:
    """
    Конфигурация приложения.
    Все значения загружаются из переменных окружения или .env файла.
    """

    # === VK API ===
    VK_BOT_TOKEN: str = os.getenv("VK_BOT_TOKEN", "")
    VK_GROUP_ID: int = int(os.getenv("VK_GROUP_ID", "0"))

    # === База данных ===
    DB_PATH: str = os.getenv("DB_PATH", "camp.db")

    # === Администраторы ===
    INITIAL_SUPER_ADMIN: int = int(os.getenv("INITIAL_SUPER_ADMIN", "0"))
    ADMIN_TAGS: list[str] = None
    ADMIN_IDS: list[int] = None

    # === Long Poll ===
    LP_WAIT_TIME: int = int(os.getenv("LP_WAIT_TIME", "25"))
    LP_VERSION: str = os.getenv("LP_VERSION", "5.131")

    # === Логирование ===
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    def validate(self) -> list[str]:
        """Проверяет обязательные настройки. Возвращает список ошибок."""
        errors = []
        if not self.VK_BOT_TOKEN or self.VK_BOT_TOKEN.startswith("vk1.a.ВАШ"):
            errors.append("VK_BOT_TOKEN не настроен в .env")
        if self.VK_GROUP_ID == 0:
            errors.append("VK_GROUP_ID не настроен в .env")
        if self.INITIAL_SUPER_ADMIN == 0:
            errors.append("INITIAL_SUPER_ADMIN не настроен — админы не будут созданы автоматически")
        return errors

    def __post_init__(self):
        """Авто-валидация при создании"""
        self.ADMIN_TAGS = _parse_csv(os.getenv("ADMIN_TAGS", ""))
        self.ADMIN_IDS = []
        for value in _parse_csv(os.getenv("ADMIN_IDS", "")):
            try:
                self.ADMIN_IDS.append(int(value))
            except ValueError:
                pass
        errors = self.validate()
        if errors:
            import logging
            logger = logging.getLogger(__name__)
            for err in errors:
                logger.warning(err)

# Глобальный экземпляр конфигурации
config = Config()