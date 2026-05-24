# vk_server.py
import json
import logging
import random
import time
from typing import Any, Callable, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import config

logger = logging.getLogger(__name__)


class VKLongPollServer:
    def __init__(self, token: str, group_id: int, api_version: str = "5.131"):
        self.token = token
        self.group_id = group_id
        self.api_version = api_version
        self.server_url: Optional[str] = None
        self.server_key: Optional[str] = None
        self.server_ts: Optional[str] = None
        self.session = requests.Session()
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _api_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"https://api.vk.ru/method/{method}"
        params = {
            **params,
            "access_token": self.token,
            "v": self.api_version,
        }
        try:
            response = self.session.post(url, params=params, timeout=(5, 15))
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as exc:
            logger.warning("VK API request %s failed: %s", method, exc.__class__.__name__)
            return {"error": {"error_msg": f"network error: {exc.__class__.__name__}"}}
        except ValueError:
            logger.warning("VK API request %s returned invalid JSON", method)
            return {"error": {"error_msg": "invalid json"}}

    def _get_longpoll_server(self) -> bool:
        response = self._api_request("groups.getLongPollServer", {"group_id": self.group_id})
        data = response.get("response")
        if not data:
            logger.error("Long Poll server error: %s", response)
            return False

        self.server_url = data["server"]
        self.server_key = data["key"]
        self.server_ts = data["ts"]
        logger.info("Connected to VK Long Poll")
        return True

    def send_message(self, user_id: int, text: str, keyboard: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "user_id": user_id,
            "message": text,
            "random_id": random.randint(1, 2**31 - 1),
        }
        if keyboard:
            params["keyboard"] = keyboard if isinstance(keyboard, str) else json.dumps(keyboard, ensure_ascii=False)

        response = self._api_request("messages.send", params)
        if "error" in response:
            logger.error("Failed to send message to %s: %s", user_id, response["error"])
        return response

    def get_user_info(self, user_id: int) -> Dict[str, str]:
        response = self._api_request("users.get", {"user_ids": user_id, "fields": "screen_name"})
        data = response.get("response") or []
        if not data:
            return {}
        user = data[0]
        first = user.get("first_name") or ""
        last = user.get("last_name") or ""
        return {
            "display_name": " ".join(part for part in (first, last) if part).strip(),
            "screen_name": user.get("screen_name") or f"id{user_id}",
        }

    def start_polling(
        self,
        message_handler: Callable[..., None],
        callback_handler: Optional[Callable[[int, Dict[str, Any]], None]] = None,
    ):
        retry_delay = 3
        while not self._get_longpoll_server():
            logger.warning("Cannot connect to VK Long Poll. Retrying in %s sec.", retry_delay)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)

        logger.info("Bot started. Waiting for events...")
        retry_delay = 3

        while True:
            try:
                response = self.session.get(
                    self.server_url,
                    params={
                        "act": "a_check",
                        "key": self.server_key,
                        "ts": self.server_ts,
                        "wait": config.LP_WAIT_TIME,
                        "v": self.api_version,
                    },
                    timeout=config.LP_WAIT_TIME + 5,
                )
                response.raise_for_status()
                data = response.json()

                if "failed" in data:
                    logger.warning("Long Poll failed: %s", data.get("failed"))
                    time.sleep(2)
                    while not self._get_longpoll_server():
                        logger.warning("Cannot refresh Long Poll server. Retrying in %s sec.", retry_delay)
                        time.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, 60)
                    retry_delay = 3
                    continue

                self.server_ts = data["ts"]

                for event in data.get("updates", []):
                    event_type = event.get("type")

                    if event_type == "message_new":
                        message = event.get("object", {}).get("message", {})
                        user_id = message.get("from_id")
                        if not user_id:
                            continue
                        text = message.get("text", "").strip()
                        payload = message.get("payload")
                        try:
                            message_handler(user_id, text, payload, self.get_user_info(user_id))
                        except Exception as exc:
                            logger.error("Message handler failed for %s: %s", user_id, exc, exc_info=True)
                            self.send_message(user_id, "Произошла ошибка. Напишите /help или попробуйте позже.")

                    elif event_type == "message_event" and callback_handler:
                        obj = event.get("object", {})
                        user_id = obj.get("user_id")
                        payload = obj.get("payload", {})
                        if isinstance(payload, str):
                            try:
                                payload = json.loads(payload)
                            except json.JSONDecodeError:
                                logger.error("Cannot parse callback payload: %s", payload)
                                payload = {}
                        if user_id:
                            try:
                                callback_handler(user_id, payload)
                            except Exception as exc:
                                logger.error("Callback handler failed for %s: %s", user_id, exc, exc_info=True)
                                self.send_message(user_id, "Не удалось обработать кнопку. Попробуйте открыть меню заново.")

            except requests.exceptions.Timeout:
                continue
            except requests.exceptions.RequestException as exc:
                logger.warning("Connection error: %s. Retrying in %s sec.", exc.__class__.__name__, retry_delay)
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
                while not self._get_longpoll_server():
                    logger.warning("Cannot refresh Long Poll server. Retrying in %s sec.", retry_delay)
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60)
                retry_delay = 3
            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                break
            except Exception as exc:
                logger.error("Critical Long Poll loop error: %s", exc, exc_info=True)
                time.sleep(5)
