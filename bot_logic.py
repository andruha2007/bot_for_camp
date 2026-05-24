import json
import logging
from typing import Any, Callable, Dict, List, Optional
from database import DatabaseManager
from config import config

logger = logging.getLogger(__name__)

CURRENCY = "баллов"

S = {
    "MAIN": "MAIN",
    "WAIT_PERSONAL_CODE": "WAIT_PERSONAL_CODE",
    "WAIT_ADMIN_VK_TAG": "WAIT_ADMIN_VK_TAG",
    "WAIT_PARTICIPANT_NAMES": "WAIT_PARTICIPANT_NAMES",
    "WAIT_EVENT_NAME": "WAIT_EVENT_NAME",
    "WAIT_EVENT_DESCRIPTION": "WAIT_EVENT_DESCRIPTION",
    "WAIT_EVENT_TEAM_SIZE": "WAIT_EVENT_TEAM_SIZE",
    "WAIT_EVENT_TEAM_MEMBERS": "WAIT_EVENT_TEAM_MEMBERS",
    "WAIT_MINI_COURSE_NAME": "WAIT_MINI_COURSE_NAME",
    "WAIT_MINI_COURSE_DESCRIPTION": "WAIT_MINI_COURSE_DESCRIPTION",
    "WAIT_MINI_COURSE_MAX_PARTICIPANTS": "WAIT_MINI_COURSE_MAX_PARTICIPANTS",
    "WAIT_MINI_COURSE_TIME_SLOTS": "WAIT_MINI_COURSE_TIME_SLOTS",
    "WAIT_BALANCE_AMOUNT": "WAIT_BALANCE_AMOUNT",
    "WAIT_BALANCE_DESCRIPTION": "WAIT_BALANCE_DESCRIPTION",
    "WAIT_COMPLAINT_MESSAGE": "WAIT_COMPLAINT_MESSAGE",
    "WAIT_SINGLE_PARTICIPANT_NAME": "WAIT_SINGLE_PARTICIPANT_NAME",
    "WAIT_REMOVE_PARTICIPANT_CODE": "WAIT_REMOVE_PARTICIPANT_CODE",
}

class CampBotLogic:
    def __init__(self, db: DatabaseManager, send_callback: Callable[[int, str, Optional[Dict]], None]):
        self.db = db
        self.send = send_callback
        self.user_states: Dict[int, Dict[str, Any]] = {}
        self._ensure_initial_admin()

    def _ensure_initial_admin(self):
        allowed_ids = list(config.ADMIN_IDS or [])
        if config.INITIAL_SUPER_ADMIN:
            allowed_ids.append(config.INITIAL_SUPER_ADMIN)

        # Sync admins from config
        for admin_id in allowed_ids:
            self.db.set_admin_role(admin_id, 'admin')

        # Set super admin if configured
        if config.INITIAL_SUPER_ADMIN:
            self.db.set_admin_role(config.INITIAL_SUPER_ADMIN, 'super_admin')

        logger.info("Admin sync complete")

    def _set_state(self, user_id: int, state: str, ctx: Optional[Dict[str, Any]] = None):
        self.user_states[user_id] = {"state": state, "ctx": ctx or {}}

    def _get_state(self, user_id: int) -> Dict[str, Any]:
        return self.user_states.get(user_id, {"state": S["MAIN"], "ctx": {}})

    def _clear_state(self, user_id: int):
        self.user_states.pop(user_id, None)

    @staticmethod
    def _payload(cmd: str) -> str:
        return json.dumps({"cmd": cmd}, ensure_ascii=False)

    @staticmethod
    def _action_payload(payload: Dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _money(value: float) -> str:
        return f"{int(value)} {CURRENCY}" if float(value).is_integer() else f"{value:.1f} {CURRENCY}"

    def _reply_kb(self, rows: List[List[Dict[str, str]]], one_time: bool = False) -> Dict[str, Any]:
        return {
            "one_time": one_time,
            "buttons": [
                [
                    {
                        "action": {"type": "text", "label": btn["label"], "payload": self._payload(btn["cmd"])},
                        "color": btn.get("color", "secondary"),
                    }
                    for btn in row
                ]
                for row in rows
            ],
        }

    def _inline_kb(self, buttons: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "inline": True,
            "buttons": [
                [
                    {
                        "action": {
                            "type": "callback",
                            "label": btn["label"][:40],
                            "payload": self._action_payload(btn["payload"]),
                        },
                        "color": btn.get("color", "secondary"),
                    }
                ]
                for btn in buttons
            ],
        }

    def _wait_kb(self) -> Dict[str, Any]:
        return self._reply_kb([[{"label": "Отмена", "cmd": "cancel", "color": "negative"}]], one_time=True)

    def _main_kb(self, role: str, participant: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if role == "admin" or role == "super_admin":
            return self._reply_kb(
                [
                    [{"label": "Мероприятия", "cmd": "events", "color": "primary"}],
                    [{"label": "Мини-курсы", "cmd": "mini_courses", "color": "primary"}],
                    [{"label": "Жалобы", "cmd": "complaints", "color": "primary"}],
                    [{"label": "Настройки", "cmd": "settings"}],
                ]
            )

        if role == "participant":
            buttons = []
            buttons.append([{"label": "Проверить баланс", "cmd": "balance", "color": "primary"}])
            buttons.append([{"label": "Записаться на мероприятие", "cmd": "register_event", "color": "primary"}])
            buttons.append([{"label": "Записаться на мини-курс", "cmd": "register_mini_course", "color": "primary"}])
            buttons.append([{"label": "Информация о мини-курсах", "cmd": "mini_course_info", "color": "primary"}])
            buttons.append([{"label": "Красная кнопка", "cmd": "complaint", "color": "negative"}])
            return self._reply_kb(buttons)

        # Unregistered user - only show login option
        return self._reply_kb(
            [
                [{"label": "Войти", "cmd": "login", "color": "positive"}],
            ]
        )

    def _is_super_admin_user(self, user_id: int) -> bool:
        participant = self.db.get_participant_by_user_id(user_id)
        return participant and participant.get("role") == "super_admin"

    def handle_message(self, user_id: int, text: str, payload: Optional[Any] = None, user_info: Optional[Dict[str, str]] = None) -> None:
        try:
            text = (text or "").strip()
            payload_dict = self._parse_payload(payload)
            participant = self.db.get_participant_by_user_id(user_id)

            role = participant.get("role") if participant else "unregistered"
            state_data = self._get_state(user_id)
            state = state_data["state"]
            ctx = state_data["ctx"]

            cmd = payload_dict.get("cmd") if payload_dict else None
            if not cmd:
                cmd = self._command_from_text(text)

            # Handle state input first (if user is in a multi-step process)
            if state != S["MAIN"] and not cmd:
                return self._handle_state_input(user_id, role, text, state, ctx)

            # Handle commands second
            if cmd:
                return self._handle_command(user_id, role, cmd, participant)

            # Handle special text commands (cancel, back)
            if cmd in ("cancel", "back") or text.lower() in ("отмена", "назад", "/cancel", "/back"):
                self._clear_state(user_id)
                return self.send(user_id, "Действие отменено.", self._main_kb(role, participant))

            return self.send(user_id, "Не понял команду. Используйте кнопки меню или напишите «Помощь».", self._main_kb(role, participant))
        except Exception as exc:
            logger.error("Message handler failed for %s: %s", user_id, exc, exc_info=True)
            self.send(user_id, "Произошла ошибка. Я вернулся в главное меню.", self._main_kb("unregistered", None))

    @staticmethod
    def _parse_payload(payload: Optional[Any]) -> Dict[str, Any]:
        if not payload:
            return {}
        if isinstance(payload, dict):
            return payload
        try:
            return json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _command_from_text(text: str) -> Optional[str]:
        aliases = {
            "войти": "login",
            "отмена": "cancel",
            "назад": "back",
            "баланс": "balance",
            "мероприятия": "events",
            "мини-курсы": "mini_courses",
            "жалоба": "complaint",
            "красная кнопка": "complaint",
            "настройки": "settings",
            "участники": "participants",
            "предварительная регистрация": "pre_register",
            "посмотреть зарегистрированных": "view_participants",
            "добавить админа": "add_admin",
            "создать мероприятие": "create_event",
            "посмотреть активные мероприятия": "view_events",
            "создать мини-курс": "create_mini_course",
            "опубликовать мини-курсы": "publish_mini_courses",
            "посмотреть неопубликованные": "view_unpublished",
            "посмотреть опубликованные": "view_published",
            "посмотреть команды на мероприятиях": "view_event_teams",
            "посмотреть участников мини-курсов": "view_mini_course_participants",
            "добавить участника": "add_single_participant",
            "удалить участника": "remove_participant",
        }
        return aliases.get(text.lower())

    def handle_callback(self, user_id: int, payload: Dict[str, Any]) -> None:
        try:
            payload = self._parse_payload(payload)
            participant = self.db.get_participant_by_user_id(user_id)
            role = participant.get("role") if participant else "unregistered"
            action = payload.get("action")

            if action == "select_event":
                return self._select_event(user_id, role, int(payload["event_id"]))
            if action == "select_mini_course":
                return self._select_mini_course(user_id, role, int(payload["mini_course_id"]))
            if action == "select_time_slot":
                return self._select_time_slot(user_id, role, int(payload["mini_course_id"]), int(payload["time_slot_id"]))
            if action == "view_participant":
                return self._view_participant_info(user_id, role, int(payload["participant_id"]))
            if action == "resolve_complaint":
                return self._resolve_complaint(user_id, role, int(payload["complaint_id"]))
            if action == "remove_admin":
                return self._remove_admin(user_id, role, int(payload["admin_id"]))

            self.send(user_id, "Действие больше не актуально. Откройте меню заново.", self._main_kb(role, participant))
        except Exception as exc:
            logger.error("Callback failed for %s: %s", user_id, exc, exc_info=True)
            self.send(user_id, "Не удалось обработать кнопку. Попробуйте открыть меню заново.")

    def _handle_command(self, user_id: int, role: str, cmd: str, participant: Optional[Dict[str, Any]]):
        if cmd == "login":
            if participant:
                return self.send(user_id, f"Вы уже вошли в систему как {participant['first_name']} {participant['last_name']}.", self._main_kb(role, participant))

            self._set_state(user_id, S["WAIT_PERSONAL_CODE"])
            return self.send(user_id, "Пожалуйста, введите ваш персональный код:", self._wait_kb())

        if cmd == "balance":
            if not participant:
                return self.send(user_id, "Вы должны сначала войти в систему.", self._main_kb(role, participant))

            balance = self.db.get_participant_balance(participant['id'])
            history = self.db.get_balance_history(participant['id'])

            lines = [f"Ваш текущий баланс: {self._money(balance)}"]
            if history:
                lines.append("\nИстория операций:")
                for item in history:
                    amount_sign = "+" if item['amount'] > 0 else ""
                    lines.append(f"- {amount_sign}{self._money(item['amount'])}: {item['description']} ({item['created_at']})")

            return self.send(user_id, "\n".join(lines), self._main_kb(role, participant))

        if cmd == "register_event":
            if not participant:
                return self.send(user_id, "Вы должны сначала войти в систему.", self._main_kb(role, participant))

            events = self.db.get_active_events()
            if not events:
                return self.send(user_id, "В настоящее время нет активных мероприятий.", self._main_kb(role, participant))

            buttons = [
                {
                    "label": event['name'],
                    "payload": {"action": "select_event", "event_id": event['id']},
                    "color": "primary",
                }
                for event in events
            ]

            return self.send(user_id, "Выберите мероприятие для регистрации:", self._inline_kb(buttons))

        if cmd == "register_mini_course":
            if not participant:
                return self.send(user_id, "Вы должны сначала войти в систему.", self._main_kb(role, participant))

            mini_courses = self.db.get_published_mini_courses()
            if not mini_courses:
                return self.send(user_id, "В настоящее время нет опубликованных мини-курсов.", self._main_kb(role, participant))

            buttons = [
                {
                    "label": f"{mc['name']} (свободных мест: {mc['max_participants']})",
                    "payload": {"action": "select_mini_course", "mini_course_id": mc['id']},
                    "color": "primary",
                }
                for mc in mini_courses
            ]

            return self.send(user_id, "Выберите мини-курс для регистрации:", self._inline_kb(buttons))

        if cmd == "mini_course_info":
            mini_courses = self.db.get_published_mini_courses()
            if not mini_courses:
                return self.send(user_id, "В настоящее время нет опубликованных мини-курсов.", self._main_kb(role, participant))

            lines = ["Информация о доступных мини-курсах:"]
            for mc in mini_courses:
                time_slots = self.db.get_time_slots(mc['id'])
                slot_info = ", ".join([ts['time'] for ts in time_slots])
                lines.append(f"\n{mc['name']}:")
                lines.append(f"Описание: {mc['description']}")
                lines.append(f"Время проведения: {slot_info}")
                lines.append(f"Максимальное количество участников: {mc['max_participants']}")

            return self.send(user_id, "\n".join(lines), self._main_kb(role, participant))

        if cmd == "complaint":
            if not participant:
                return self.send(user_id, "Вы должны сначала войти в систему.", self._main_kb(role, participant))

            self._set_state(user_id, S["WAIT_COMPLAINT_MESSAGE"])
            return self.send(user_id, "Пожалуйста, опишите вашу жалобу:", self._wait_kb())

        if role == "admin" or role == "super_admin":
            if cmd == "participants":
                return self._send_participants_menu(user_id, role)
            if cmd == "events":
                return self._send_events_menu(user_id, role)
            if cmd == "mini_courses":
                return self._send_mini_courses_menu(user_id, role)
            if cmd == "complaints":
                return self._send_complaints(user_id, role)
            if cmd == "settings":
                return self._send_settings_menu(user_id, role)
            if cmd == "add_admin":
                if not self._is_super_admin_user(user_id):
                    return self.send(user_id, "Только суперадмин может добавлять админов.", self._main_kb(role, participant))
                self._set_state(user_id, S["WAIT_ADMIN_VK_TAG"])
                return self.send(user_id, "Введите VK тег нового админа (например, @username):", self._wait_kb())
            if cmd == "pre_register":
                self._set_state(user_id, S["WAIT_PARTICIPANT_NAMES"])
                return self.send(user_id, "Введите список участников через запятую в формате: Фамилия Имя, Фамилия Имя", self._wait_kb())
            if cmd == "view_participants":
                return self._view_participants(user_id, role)
            if cmd == "create_event":
                self._set_state(user_id, S["WAIT_EVENT_NAME"])
                return self.send(user_id, "Введите название мероприятия:", self._wait_kb())
            if cmd == "view_events":
                return self._view_events(user_id, role)
            if cmd == "create_mini_course":
                self._set_state(user_id, S["WAIT_MINI_COURSE_NAME"])
                return self.send(user_id, "Введите название мини-курса:", self._wait_kb())
            if cmd == "publish_mini_courses":
                count = self.db.publish_mini_courses()
                return self.send(user_id, f"Опубликовано {count} мини-курсов.", self._main_kb(role, participant))
            if cmd == "view_unpublished":
                return self._view_unpublished_mini_courses(user_id, role)
            if cmd == "view_published":
                return self._view_published_mini_courses(user_id, role)
            if cmd == "view_event_teams":
                return self._view_event_teams(user_id, role)
            if cmd == "view_mini_course_participants":
                return self._view_mini_course_participants(user_id, role)
            if cmd == "add_single_participant":
                self._set_state(user_id, S["WAIT_SINGLE_PARTICIPANT_NAME"])
                return self.send(user_id, "Введите фамилию и имя участника в формате: Фамилия Имя", self._wait_kb())
            if cmd == "remove_participant":
                self._set_state(user_id, S["WAIT_REMOVE_PARTICIPANT_CODE"])
                return self.send(user_id, "Введите персональный код участника для удаления:", self._wait_kb())

        self.send(user_id, "Эта команда недоступна для вашей роли.", self._main_kb(role, participant))

    def _handle_state_input(self, user_id: int, role: str, text: str, state: str, ctx: Dict[str, Any]) -> None:
        participant = self.db.get_participant_by_user_id(user_id)

        if state == S["WAIT_PERSONAL_CODE"]:
            success, msg, participant_data = self.db.login_with_personal_code(user_id, text.strip())
            self._clear_state(user_id)
            if success:
                new_role = participant_data.get("role", "participant")
                return self.send(user_id, msg, self._main_kb(new_role, participant_data))
            else:
                return self.send(user_id, msg, self._main_kb(role, participant))

        if state == S["WAIT_ADMIN_VK_TAG"] and (role == "admin" or role == "super_admin"):
            success, msg = self.db.add_admin_by_vk_tag(user_id, text.strip())
            self._clear_state(user_id)
            return self.send(user_id, msg, self._main_kb(role, participant))

        if state == S["WAIT_PARTICIPANT_NAMES"] and (role == "admin" or role == "super_admin"):
            names_list = [name.strip() for name in text.split(",") if name.strip()]
            results = self.db.pre_register_participants(names_list)

            success_count = sum(1 for r in results if r['success'])
            error_messages = [r['error'] for r in results if not r['success'] and 'error' in r]

            lines = [f"Обработано {len(results)} участников, успешно: {success_count}"]
            if error_messages:
                lines.append("\nОшибки:")
                lines.extend(error_messages)

            # Show the generated codes
            lines.append("\nСгенерированные коды:")
            for result in results:
                if result['success']:
                    lines.append(f"{result['name']}: {result['personal_code']}")

            self._clear_state(user_id)
            return self.send(user_id, "\n".join(lines), self._main_kb(role, participant))

        if state == S["WAIT_SINGLE_PARTICIPANT_NAME"] and (role == "admin" or role == "super_admin"):
            parts = text.strip().split()
            if len(parts) < 2:
                return self.send(user_id, "Некорректный формат. Используйте 'Фамилия Имя'", self._wait_kb())

            last_name = parts[0]
            first_name = ' '.join(parts[1:])

            success, msg, personal_code = self.db.add_single_participant(first_name, last_name)
            self._clear_state(user_id)

            if success:
                return self.send(user_id, msg, self._main_kb(role, participant))
            else:
                return self.send(user_id, msg, self._main_kb(role, participant))

        if state == S["WAIT_REMOVE_PARTICIPANT_CODE"] and (role == "admin" or role == "super_admin"):
            success, msg = self.db.remove_pre_registered_participant(text.strip())
            self._clear_state(user_id)

            if success:
                return self.send(user_id, msg, self._main_kb(role, participant))
            else:
                return self.send(user_id, msg, self._main_kb(role, participant))

        if state == S["WAIT_COMPLAINT_MESSAGE"]:
            if not participant:
                return self.send(user_id, "Вы должны сначала войти в систему.", self._main_kb(role, participant))

            success = self.db.submit_complaint(participant['id'], text)
            self._clear_state(user_id)

            if success:
                return self.send(user_id, "Ваша жалоба отправлена администратору. Спасибо!", self._main_kb(role, participant))
            else:
                return self.send(user_id, "Не удалось отправить жалобу. Пожалуйста, попробуйте позже.", self._main_kb(role, participant))

        if role == "admin" or role == "super_admin":
            return self._handle_admin_state(user_id, role, text, state, ctx)

        self._clear_state(user_id)
        self.send(user_id, "Действие сброшено.", self._main_kb(role, participant))

    def _view_events(self, user_id: int, role: str):
        events = self.db.get_active_events()
        if not events:
            return self.send(user_id, "Активных мероприятий пока нет.", self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

        lines = ["Список активных мероприятий:"]
        for event in events:
            lines.append(f"\n{event['name']}:")
            lines.append(f"Описание: {event['description']}")
            lines.append(f"Размер команды: {event['team_size']}")

        return self.send(user_id, "\n".join(lines), self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

    def _view_unpublished_mini_courses(self, user_id: int, role: str):
        mini_courses = self.db.get_unpublished_mini_courses()
        if not mini_courses:
            return self.send(user_id, "Неопубликованных мини-курсов пока нет.", self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

        lines = ["Список неопубликованных мини-курсов:"]
        for mc in mini_courses:
            time_slots = self.db.get_time_slots(mc['id'])
            slot_info = ", ".join([ts['time'] for ts in time_slots])
            lines.append(f"\n{mc['name']}:")
            lines.append(f"Описание: {mc['description']}")
            lines.append(f"Макс. участников: {mc['max_participants']}")
            lines.append(f"Слоты времени: {slot_info}")

        return self.send(user_id, "\n".join(lines), self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

    def _view_published_mini_courses(self, user_id: int, role: str):
        mini_courses = self.db.get_published_mini_courses()
        if not mini_courses:
            return self.send(user_id, "Опубликованных мини-курсов пока нет.", self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

        lines = ["Список опубликованных мини-курсов:"]
        for mc in mini_courses:
            time_slots = self.db.get_time_slots(mc['id'])
            slot_info = ", ".join([ts['time'] for ts in time_slots])
            registrations = self.db.get_mini_course_registrations(mc['id'])
            lines.append(f"\n{mc['name']}:")
            lines.append(f"Описание: {mc['description']}")
            lines.append(f"Макс. участников: {mc['max_participants']}")
            lines.append(f"Слоты времени: {slot_info}")
            lines.append(f"Зарегистрировано: {len(registrations)} участников")

        return self.send(user_id, "\n".join(lines), self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

    def _handle_admin_state(self, user_id: int, role: str, text: str, state: str, ctx: Dict[str, Any]) -> None:
        participant = self.db.get_participant_by_user_id(user_id)

        if state == S["WAIT_EVENT_NAME"]:
            ctx["name"] = text.strip()
            self._set_state(user_id, S["WAIT_EVENT_DESCRIPTION"], ctx)
            return self.send(user_id, "Введите описание мероприятия:", self._wait_kb())

        if state == S["WAIT_EVENT_DESCRIPTION"]:
            ctx["description"] = text.strip()
            self._set_state(user_id, S["WAIT_EVENT_TEAM_SIZE"], ctx)
            return self.send(user_id, "Введите размер команды (количество участников):", self._wait_kb())

        if state == S["WAIT_EVENT_TEAM_SIZE"]:
            try:
                team_size = int(text.strip())
                if team_size <= 0:
                    raise ValueError("Team size must be positive")
            except ValueError:
                return self.send(user_id, "Пожалуйста, введите целое положительное число.", self._wait_kb())

            event_id = self.db.create_event(
                ctx["name"],
                ctx["description"],
                team_size
            )
            self._clear_state(user_id)
            return self.send(user_id, f"Мероприятие создано с ID: {event_id}", self._main_kb(role, participant))

        if state == S["WAIT_MINI_COURSE_NAME"]:
            ctx["name"] = text.strip()
            self._set_state(user_id, S["WAIT_MINI_COURSE_DESCRIPTION"], ctx)
            return self.send(user_id, "Введите описание мини-курса:", self._wait_kb())

        if state == S["WAIT_MINI_COURSE_DESCRIPTION"]:
            ctx["description"] = text.strip()
            self._set_state(user_id, S["WAIT_MINI_COURSE_MAX_PARTICIPANTS"], ctx)
            return self.send(user_id, "Введите максимальное количество участников:", self._wait_kb())

        if state == S["WAIT_MINI_COURSE_MAX_PARTICIPANTS"]:
            try:
                max_participants = int(text.strip())
                if max_participants <= 0:
                    raise ValueError("Max participants must be positive")
            except ValueError:
                return self.send(user_id, "Пожалуйста, введите целое положительное число.", self._wait_kb())

            ctx["max_participants"] = max_participants
            self._set_state(user_id, S["WAIT_MINI_COURSE_TIME_SLOTS"], ctx)
            return self.send(user_id, "Введите слоты времени через запятую (например: 11:00, 13:00):", self._wait_kb())

        if state == S["WAIT_MINI_COURSE_TIME_SLOTS"]:
            time_slots = [slot.strip() for slot in text.split(",") if slot.strip()]

            mini_course_id = self.db.create_mini_course(
                ctx["name"],
                ctx["description"],
                ctx["max_participants"]
            )

            for time_slot in time_slots:
                self.db.add_time_slot(mini_course_id, time_slot)

            self._clear_state(user_id)
            return self.send(user_id, f"Мини-курс создан с ID: {mini_course_id}. Вы можете опубликовать его в меню мини-курсов.", self._main_kb(role, participant))

        if state == S["WAIT_BALANCE_AMOUNT"]:
            try:
                amount = int(text.strip())
            except ValueError:
                return self.send(user_id, "Пожалуйста, введите целое число.", self._wait_kb())

            ctx["amount"] = amount
            self._set_state(user_id, S["WAIT_BALANCE_DESCRIPTION"], ctx)
            return self.send(user_id, "Введите описание операции:", self._wait_kb())

        if state == S["WAIT_BALANCE_DESCRIPTION"]:
            success = self.db.update_balance(
                ctx["participant_id"],
                ctx["amount"],
                text.strip(),
                user_id
            )
            self._clear_state(user_id)

            if success:
                return self.send(user_id, "Баланс участника успешно обновлен.", self._main_kb(role, participant))
            else:
                return self.send(user_id, "Не удалось обновить баланс.", self._main_kb(role, participant))

        self._clear_state(user_id)
        self.send(user_id, "Действие сброшено.", self._main_kb(role, participant))

    def _select_event(self, user_id: int, role: str, event_id: int):
        participant = self.db.get_participant_by_user_id(user_id)
        if not participant:
            return self.send(user_id, "Вы должны сначала войти в систему.", self._main_kb(role, participant))

        event = self.db.get_event(event_id)
        if not event:
            return self.send(user_id, "Мероприятие не найдено.", self._main_kb(role, participant))

        self._set_state(user_id, S["WAIT_EVENT_TEAM_MEMBERS"], {"event_id": event_id})
        return self.send(user_id, f"Мероприятие: {event['name']}\nОписание: {event['description']}\nРазмер команды: {event['team_size']}\n\nВведите участников команды через запятую в формате: Фамилия Имя, Фамилия Имя", self._wait_kb())

    def _select_mini_course(self, user_id: int, role: str, mini_course_id: int):
        participant = self.db.get_participant_by_user_id(user_id)
        if not participant:
            return self.send(user_id, "Вы должны сначала войти в систему.", self._main_kb(role, participant))

        mini_course = self.db.get_mini_course(mini_course_id)
        if not mini_course:
            return self.send(user_id, "Мини-курс не найден.", self._main_kb(role, participant))

        time_slots = self.db.get_time_slots(mini_course_id)
        if not time_slots:
            return self.send(user_id, "Для этого мини-курса нет доступных слотов времени.", self._main_kb(role, participant))

        buttons = [
            {
                "label": f"Записаться на {ts['time']}",
                "payload": {"action": "select_time_slot", "mini_course_id": mini_course_id, "time_slot_id": ts['id']},
                "color": "primary",
            }
            for ts in time_slots
        ]

        return self.send(user_id, f"Мини-курс: {mini_course['name']}\nОписание: {mini_course['description']}\n\nВыберите слот времени:", self._inline_kb(buttons))

    def _select_time_slot(self, user_id: int, role: str, mini_course_id: int, time_slot_id: int):
        participant = self.db.get_participant_by_user_id(user_id)
        if not participant:
            return self.send(user_id, "Вы должны сначала войти в систему.", self._main_kb(role, participant))

        success, msg = self.db.register_for_mini_course(mini_course_id, participant['id'], time_slot_id)
        if success:
            return self.send(user_id, msg, self._main_kb(role, participant))
        else:
            return self.send(user_id, msg, self._main_kb(role, participant))

    def _send_participants_menu(self, user_id: int, role: str):
        buttons = [
            [{"label": "Предварительная регистрация", "cmd": "pre_register", "color": "primary"}],
            [{"label": "Добавить участника", "cmd": "add_single_participant", "color": "primary"}],
            [{"label": "Удалить участника", "cmd": "remove_participant", "color": "primary"}],
            [{"label": "Посмотреть зарегистрированных", "cmd": "view_participants", "color": "primary"}],
            [{"label": "Назад", "cmd": "back"}],
        ]
        return self.send(user_id, "Меню управления участниками:", self._reply_kb(buttons))

    def _view_participants(self, user_id: int, role: str):
        participants = self.db.get_pre_registered_participants()
        if not participants:
            return self.send(user_id, "Участников пока нет.", self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

        lines = ["Список предварительно зарегистрированных участников:"]
        for p in participants:
            status = "ИСПОЛЬЗОВАН" if p['is_used'] else "ДОСТУПЕН"
            lines.append(f"{p['last_name']} {p['first_name']}: {p['personal_code']} [{status}]")

        return self.send(user_id, "\n".join(lines), self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

    def _send_events_menu(self, user_id: int, role: str):
        buttons = [
            [{"label": "Создать мероприятие", "cmd": "create_event", "color": "primary"}],
            [{"label": "Посмотреть активные мероприятия", "cmd": "view_events", "color": "primary"}],
            [{"label": "Назад", "cmd": "back"}],
        ]
        return self.send(user_id, "Меню мероприятий:", self._reply_kb(buttons))

    def _send_mini_courses_menu(self, user_id: int, role: str):
        buttons = [
            [{"label": "Создать мини-курс", "cmd": "create_mini_course", "color": "primary"}],
            [{"label": "Опубликовать мини-курсы", "cmd": "publish_mini_courses", "color": "primary"}],
            [{"label": "Посмотреть неопубликованные", "cmd": "view_unpublished", "color": "primary"}],
            [{"label": "Посмотреть опубликованные", "cmd": "view_published", "color": "primary"}],
            [{"label": "Назад", "cmd": "back"}],
        ]
        return self.send(user_id, "Меню мини-курсов:", self._reply_kb(buttons))

    def _send_settings_menu(self, user_id: int, role: str):
        buttons = [
            [{"label": "Добавить админа", "cmd": "add_admin", "color": "primary"}],
            [{"label": "Управление участниками", "cmd": "participants", "color": "primary"}],
            [{"label": "Назад", "cmd": "back"}],
        ]
        return self.send(user_id, "Настройки админа:", self._reply_kb(buttons))

    def _send_complaints(self, user_id: int, role: str):
        complaints = self.db.get_recent_complaints(10)
        if not complaints:
            return self.send(user_id, "Новых жалоб нет.", self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

        lines = ["Последние жалобы:"]
        buttons = []

        for complaint in complaints:
            lines.append(f"\nЖалоба #{complaint['id']} от {complaint['first_name']} {complaint['last_name']} ({complaint['personal_code']}):")
            lines.append(f"Сообщение: {complaint['message']}")
            lines.append(f"Время: {complaint['created_at']}")

            buttons.append({
                "label": f"Пометить как решенную #{complaint['id']}",
                "payload": {"action": "resolve_complaint", "complaint_id": complaint['id']},
                "color": "positive",
            })

        return self.send(user_id, "\n".join(lines), self._inline_kb(buttons))

    def _resolve_complaint(self, user_id: int, role: str, complaint_id: int):
        if role != "admin" and role != "super_admin":
            return self.send(user_id, "Недостаточно прав.", self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

        success = self.db.resolve_complaint(complaint_id)
        if success:
            return self.send(user_id, "Жалоба помечена как решенная.", self._main_kb(role, self.db.get_participant_by_user_id(user_id)))
        else:
            return self.send(user_id, "Не удалось обновить статус жалобы.", self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

    def _remove_admin(self, user_id: int, role: str, admin_id: int):
        if not self._is_super_admin_user(user_id):
            return self.send(user_id, "Удалять админов может только суперадмин.", self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

        if user_id == admin_id:
            return self.send(user_id, "Вы не можете удалить себя.", self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

        # Check if this is the last super admin
        super_admins = [p for p in self.db.get_admins() if self.db.get_participant_by_user_id(p).get('role') == 'super_admin']
        if len(super_admins) <= 1 and self.db.get_participant_by_user_id(admin_id).get('role') == 'super_admin':
            return self.send(user_id, "Это последний суперадмин. Нельзя удалить.", self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

        success = self.db.set_admin_role(admin_id, 'participant')
        if success:
            return self.send(user_id, f"Админ {admin_id} понижен до участника.", self._main_kb(role, self.db.get_participant_by_user_id(user_id)))
        else:
            return self.send(user_id, "Не удалось удалить админа.", self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

    def _view_event_teams(self, user_id: int, role: str):
        events = self.db.get_active_events()
        if not events:
            return self.send(user_id, "Активных мероприятий пока нет.", self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

        lines = ["Список команд на мероприятиях:"]
        for event in events:
            registrations = self.db.get_event_registration_details(event['id'])
            if not registrations:
                lines.append(f"\n{event['name']}: нет зарегистрированных команд")
                continue

            lines.append(f"\n{event['name']} (размер команды: {event['team_size']}):")
            for reg in registrations:
                team_members = reg['team_members'].split(', ')
                lines.append(f"\n  Команда от {reg['first_name']} {reg['last_name']} ({reg['personal_code']}):")
                for member in team_members:
                    lines.append(f"    - {member}")

        return self.send(user_id, "\n".join(lines), self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

    def _view_mini_course_participants(self, user_id: int, role: str):
        mini_courses = self.db.get_published_mini_courses()
        if not mini_courses:
            return self.send(user_id, "Опубликованных мини-курсов пока нет.", self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

        lines = ["Список участников на мини-курсах:"]
        for mc in mini_courses:
            registrations = self.db.get_mini_course_registration_details(mc['id'])
            if not registrations:
                lines.append(f"\n{mc['name']}: нет зарегистрированных участников")
                continue

            lines.append(f"\n{mc['name']}:")
            for reg in registrations:
                lines.append(f"  {reg['first_name']} {reg['last_name']} ({reg['personal_code']}) - слот {reg['time']}")

        return self.send(user_id, "\n".join(lines), self._main_kb(role, self.db.get_participant_by_user_id(user_id)))

    def _help(self, role: str) -> str:
        if role == "admin" or role == "super_admin":
            return (
                "Бот для лагеря управляет регистрацией участников, мероприятиями и мини-курсами.\n\n"
                "Админ может создавать мероприятия, мини-курсы, управлять участниками и обрабатывать жалобы."
            )
        else:
            return (
                "Бот для лагеря позволяет участникам регистрироваться на мероприятия и мини-курсы.\n\n"
                "Для начала работы нажмите 'Войти' и введите ваш персональный код."
            )
