import json
import logging
from typing import Dict, Any, Optional
from pyvkbot import Bot, Keyboard
from database import DatabaseManager
from config import config

logger = logging.getLogger(__name__)

class CampBot:
    def __init__(self, bot: Bot, db: DatabaseManager):
        self.bot = bot
        self.db = db
        self.states: Dict[int, Dict[str, Any]] = {}
        self._sync_admins()
        self._register_handlers()

    def _sync_admins(self):
        """Синхронизация админов из конфига при старте"""
        if config.INITIAL_SUPER_ADMIN:
            self.db.sync_admin_from_config(config.INITIAL_SUPER_ADMIN, 'super_admin')
        for aid in (config.ADMIN_IDS or []):
            self.db.sync_admin_from_config(aid, 'admin')
        logger.info("Admin sync complete")

    def _register_handlers(self):
        # 1. Обработка обычных текстовых сообщений и reply-кнопок
        @self.bot.message()
        def on_message(bot: Bot, message):
            self.handle_message(message)

        # 2. Обработка нажатий на inline-кнопки (галочки, листалки)
        try:
            self.bot.on("message_callback", self.handle_callback)
        except Exception as e:
            logger.error(f"Не удалось зарегистрировать обработчик callback-кнопок: {e}")

    # === Управление состояниями FSM ===
    def _set_state(self, uid: int, state: str, ctx: Optional[Dict] = None):
        self.states[uid] = {"state": state, "ctx": ctx or {}}

    def _clear_state(self, uid: int):
        self.states.pop(uid, None)

    # === Генерация клавиатуры ===
    def main_kb(self, role: str) -> Keyboard:
        kb = Keyboard(one_time=False, inline=False)
        if role in ("admin", "super_admin"):
            kb.add_button("Мероприятие", color="primary", payload=json.dumps({"cmd": "events"}))
            #kb.add_line()
            kb.add_button("Мини-курсы", color="primary", payload=json.dumps({"cmd": "mini_courses"}))
            kb.add_line()
            kb.add_button("Жалобы", color="primary", payload=json.dumps({"cmd": "complaints"}))
            kb.add_line()
            kb.add_button("Настройки", color="secondary", payload=json.dumps({"cmd": "settings"}))
        elif role == "participant":
            kb.add_button("Баланс", color="primary", payload=json.dumps({"cmd": "balance"}))
            kb.add_line()
            kb.add_button("Жалоба", color="negative", payload=json.dumps({"cmd": "complaint"}))
            kb.add_line()
            kb.add_button("Мероприятия", color="primary", payload=json.dumps({"cmd": "register_event"}))
            kb.add_line()
            kb.add_button("Мини-курсы", color="primary", payload=json.dumps({"cmd": "register_mini_course"}))
        else:
            kb.add_button("Войти", color="positive", payload=json.dumps({"cmd": "login"}))
        return kb

    def wait_kb(self) -> Keyboard:
        kb = Keyboard(one_time=True, inline=False)
        kb.add_button("Отмена", color="negative", payload=json.dumps({"cmd": "cancel"}))
        return kb

    def team_selection_kb(self, ctx: Dict) -> Keyboard:
        kb = Keyboard(inline=True)
        page = ctx.get("page", 0)
        users = ctx["users"]
        selected = ctx["selected"]
        per_page = 4
        start, end = page * per_page, (page + 1) * per_page

        # Каждый участник — отдельная строка
        for i, u in enumerate(users[start:end]):
            is_sel = u["user_id"] in selected
            emoji = "✅" if is_sel else "⬜"
            label = f"{emoji} {u['last_name']} {u['first_name']}"
            color = "positive" if is_sel else "secondary"
            kb.add_callback_button(
                label[:40],
                color=color,
                payload=json.dumps({"action": "toggle_user", "target_uid": u["user_id"]})
            )
            kb.add_line()

        # Кнопки навигации на одной строке
        if page > 0:
            kb.add_callback_button("⬅️", color="secondary", payload=json.dumps({"action": "team_page", "page": page - 1}))
        if end < len(users):
            kb.add_callback_button("➡️", color="secondary", payload=json.dumps({"action": "team_page", "page": page + 1}))
        if page > 0 or end < len(users):
            kb.add_line()

        # Кнопка подтверждения на отдельной строке
        kb.add_callback_button(
            f"Завершить ({len(selected)})",
            color="positive",
            payload=json.dumps({"action": "submit_team"})
        )
        return kb

    def team_selection_text(self, ctx: Dict) -> str:
        ev = self.db.get_event(ctx.get("target_id"))
        if not ev:
            return "Выберите участников:"
        selected_count = len(ctx.get("selected", []))
        return f"📅 {ev['name']}\n👥 Команда: от {ev['min_team_size']} до {ev['max_team_size']}\n✅ Выбрано: {selected_count}\n\nНажимайте на участников, чтобы добавить/убрать:"

    def participant_selection_kb(self, ctx: Dict) -> Keyboard:
        kb = Keyboard(inline=True)
        page = ctx.get("page", 0)
        participants = ctx["participants"]
        per_page = 4  # Уменьшено до 4
        start, end = page * per_page, (page + 1) * per_page

        # Добавляем кнопки участников
        for p in participants[start:end]:
            kb.add_callback_button(
                f"{p['last_name']} {p['first_name']}",
                color="primary",
                payload=json.dumps({"action": "select_participant", "participant_id": p['id']})
            )
            kb.add_line()

        # Кнопки навигации
        nav_buttons_added = False
        if page > 0:
            kb.add_callback_button("⬅️", color="secondary", payload=json.dumps({"action": "participant_page", "page": page - 1}))
            nav_buttons_added = True
        if end < len(participants):
            kb.add_callback_button("➡️", color="secondary", payload=json.dumps({"action": "participant_page", "page": page + 1}))
            nav_buttons_added = True
            
        if nav_buttons_added:
            kb.add_line()

        return kb

    # === Безопасное извлечение данных из объектов pyvkbot ===
    @staticmethod
    def _extract_message_data(message):
        if isinstance(message, dict):
            return (
                message.get("from_id") or message.get("user_id"),
                (message.get("text") or "").strip(),
                message.get("payload")
            )
        return None, "", None

    @staticmethod
    def _extract_callback_data(event):
        obj = getattr(event, 'object', event)
        if isinstance(obj, dict):
            return (
                obj.get("user_id"),
                obj.get("peer_id") or obj.get("user_id"),
                obj.get("conversation_message_id"),
                obj.get("payload", "{}")
            )
        return (
            getattr(obj, 'user_id', None),
            getattr(obj, 'peer_id', getattr(obj, 'user_id', None)),
            getattr(obj, 'conversation_message_id', None),
            getattr(obj, 'payload', "{}")
        )

    # === Главный обработчик сообщений ===
    def handle_message(self, message):
        try:
            uid, text, payload_str = self._extract_message_data(message)
            if not uid or uid < 0:
                return

            payload = json.loads(payload_str) if payload_str else {}
            cmd = payload.get("cmd") or self._text_to_cmd(text)

            p = self.db.get_participant_by_user_id(uid)
            role = p.get("role") if p else "unregistered"
            state_data = self.states.get(uid, {"state": "MAIN", "ctx": {}})
            state, ctx = state_data["state"], state_data["ctx"]

            # 1. Глобальные Назад/Отмена — перехватываем ДО всего
            if cmd in ("cancel", "back") or text.lower() in ("отмена", "назад"):
                self._clear_state(uid)
                self.bot.send_message(uid, "Возврат в главное меню.", keyboard=self.main_kb(role))
                return

            # 2. FSM Ввод текста в активном состоянии
            if state != "MAIN" and not cmd:
                self.process_fsm(uid, role, text, state, ctx)
                return

            # 3. Команды из кнопок или текста
            if cmd:
                self.route_command(uid, role, cmd, p)
                return

            self.bot.send_message(uid, "Используйте кнопки меню.", keyboard=self.main_kb(role))

        except Exception as e:
            logger.error("Msg error: %s", e, exc_info=True)

    # === Обработчик callback-кнопок (галочки, навигация) ===
    def handle_callback(self, event):
        try:
            uid, peer_id, cmid, payload_str = self._extract_callback_data(event)
            if not uid:
                return

            if isinstance(payload_str, str):
                payload = json.loads(payload_str)
            else:
                payload = payload_str or {}

            action = payload.get("action")
            p = self.db.get_participant_by_user_id(uid)
            role = p.get("role") if p else "unregistered"

            if action == "toggle_user":
                ctx = self.states.get(uid, {}).get("ctx", {})
                target = payload["target_uid"]
                if target in ctx.get("selected", []):
                    ctx["selected"].remove(target)
                else:
                    ctx["selected"].append(target)
                self.states[uid]["ctx"] = ctx
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": self.team_selection_text(ctx),
                    "keyboard": self.team_selection_kb(ctx).get_keyboard()
                })

            elif action == "team_page":
                ctx = self.states.get(uid, {}).get("ctx", {})
                ctx["page"] = payload["page"]
                self.states[uid]["ctx"] = ctx
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": self.team_selection_text(ctx),
                    "keyboard": self.team_selection_kb(ctx).get_keyboard()
                })

            elif action == "submit_team":
                ctx = self.states.get(uid, {}).get("ctx", {})
                ok, msg, errors = self.db.register_team_for_event(ctx.get("target_id"), uid, ctx.get("selected", []))
                if ok:
                    self._clear_state(uid)
                    self.bot.send_message(uid, f"🎉 {msg}", keyboard=self.main_kb(role))
                else:
                    error_msg = "\n".join(errors) if errors else msg
                    self.bot.send_message(uid, f"❌ {error_msg}", keyboard=self.team_selection_kb(ctx))

            elif action == "leave_team":
                if payload.get("target") == "event":
                    self.db.leave_event_team(payload["target_id"], uid)
                else:
                    self.db.leave_mini_course_team(payload["target_id"], 0, uid)
                self.bot.send_message(uid, "Вы вышли из команды.", keyboard=self.main_kb(role))

            elif action == "show_my_team":
                event_id = payload.get("event_id")
                mini_course_id = payload.get("mini_course_id")
                if event_id:
                    team = self.db.get_my_team_for_event(event_id, uid)
                    if team:
                        members = team.get('team_members', '') or ''
                        self.bot.send_message(uid, f"📅 Ваша команда:\n🏷 {members}", keyboard=self.main_kb(role))
                    else:
                        self.bot.send_message(uid, "Вы не состоите в команде.", keyboard=self.main_kb(role))
                elif mini_course_id:
                    self.bot.send_message(uid, "Вы записаны на этот мини-курс.", keyboard=self.main_kb(role))

            elif action == "start_fair":
                if role != "super_admin":
                    return self.bot.send_message(uid, "Эта команда доступна только супер-админу.", keyboard=self.main_kb(role))
                self._set_state(uid, "WAIT_FAIR_BUDGET", {"event_id": payload["event_id"]})
                self.bot.send_message(uid, "Введите начальный бюджет для каждой команды:", keyboard=self.wait_kb())

            elif action == "resolve_complaint":
                if role not in ("admin", "super_admin"):
                    return self.bot.send_message(uid, "Эта команда доступна только админу.", keyboard=self.main_kb(role))
                self.db.resolve_complaint(payload["complaint_id"])
                self.bot.send_message(uid, f"✅ Жалоба #{payload['complaint_id']} решена.", keyboard=self.main_kb(role))

            elif action == "select_participant":
                if role not in ("admin", "super_admin"):
                    return self.bot.send_message(uid, "Эта команда доступна только админу.", keyboard=self.main_kb(role))
                participant_id = payload["participant_id"]
                self._set_state(uid, "WAIT_TOP_UP_AMOUNT", {"participant_id": participant_id})
                self.bot.send_message(uid, "Введите сумму для пополнения баланса:", keyboard=self.wait_kb())

            elif action == "participant_page":
                if role not in ("admin", "super_admin"):
                    return self.bot.send_message(uid, "Эта команда доступна только админу.", keyboard=self.main_kb(role))
                ctx = self.states.get(uid, {}).get("ctx", {})
                ctx["page"] = payload["page"]
                self.states[uid]["ctx"] = ctx
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": "Выберите участника для пополнения баланса:",
                    "keyboard": self.participant_selection_kb(ctx).get_keyboard()
                })

            elif action == "select_event":
                event_id = payload["event_id"]
                ev = self.db.get_event(event_id)
                if not ev:
                    return self.bot.send_message(uid, "Мероприятие не найдено.", keyboard=self.main_kb(role))
                if not ev.get('is_active', 0):
                    return self.bot.send_message(uid, "Это мероприятие не активно.", keyboard=self.main_kb(role))

                # Проверяем, не состоит ли пользователь уже в команде
                my_team = self.db.get_my_team_for_event(event_id, uid)
                if my_team:
                    return self.bot.send_message(uid, "Вы уже состоите в команде для этого мероприятия.", keyboard=self.main_kb(role))

                # Получаем свободных участников (не в командах)
                free_participants = self.db.get_unregistered_participants_for_event(event_id, uid)
                if not free_participants:
                    return self.bot.send_message(uid, "Нет свободных участников для формирования команды.", keyboard=self.main_kb(role))

                # Текущий пользователь автоматически в команде
                self._set_state(uid, "SELECTING_TEAM", {
                    "target": "event",
                    "target_id": event_id,
                    "users": free_participants,
                    "selected": [uid],
                    "page": 0
                })

                ctx = self.states[uid]["ctx"]
                self.bot.send_message(
                    uid,
                    self.team_selection_text(ctx),
                    keyboard=self.team_selection_kb(ctx)
                )

            elif action == "select_mini_course":
                mini_course_id = payload["mini_course_id"]
                mc = self.db.get_mini_course(mini_course_id)
                if not mc:
                    return self.bot.send_message(uid, "Мини-курс не найден.", keyboard=self.main_kb(role))

                time_slots = self.db.get_time_slots(mini_course_id)
                if not time_slots:
                    return self.bot.send_message(uid, "Нет доступных временных слотов для этого курса.", keyboard=self.main_kb(role))

                kb = Keyboard(inline=True)
                for i, slot in enumerate(time_slots):
                    registered_count = self.db.get_mini_course_registrations(mini_course_id)
                    cnt = sum(1 for r in registered_count if r.get('time_slot_id') == slot['id'])
                    free = mc['max_participants'] - cnt
                    label = f"{slot['time']} (ост. {free})"
                    color = "secondary" if free <= 0 else "primary"
                    kb.add_callback_button(
                        label, color=color,
                        payload=json.dumps({"action": "select_time_slot", "mini_course_id": mini_course_id, "time_slot_id": slot['id']})
                    )
                    if i < len(time_slots) - 1:
                        kb.add_line()

                self.bot.send_message(uid, f"📚 {mc['name']}\n{mc['description']}\n\nВыберите время:", keyboard=kb)

            elif action == "select_time_slot":
                mini_course_id = payload["mini_course_id"]
                time_slot_id = payload["time_slot_id"]
                mc = self.db.get_mini_course(mini_course_id)
                if not mc:
                    return self.bot.send_message(uid, "Мини-курс не найден.", keyboard=self.main_kb(role))

                # Индивидуальная запись на мини-курс
                ok, msg = self.db.register_mini_course_individual(mini_course_id, time_slot_id, uid)
                self.bot.send_message(uid, "✅ " + msg if ok else "❌ " + msg, keyboard=self.main_kb(role))

        except Exception as e:
            logger.error("Callback error: %s", e, exc_info=True)

    # === Маппинг текста в команды ===
    @staticmethod
    def _text_to_cmd(t: str) -> Optional[str]:
        aliases = {
            "войти": "login", "баланс": "balance", "мероприятия": "events",
            "настройки": "settings", "назад": "back", "отмена": "cancel",
            "мини-курсы": "mini_courses", "жалоба": "complaint",
            "красная кнопка": "complaint", "участники": "participants",
        }
        return aliases.get(t.lower())

    # === Роутинг команд ===
    def route_command(self, uid: int, role: str, cmd: str, p: Optional[Dict]):
        if cmd == "login":
            if p:
                return self.bot.send_message(uid, f"Вы уже вошли как {p['first_name']}.", keyboard=self.main_kb(role))
            self._set_state(uid, "WAIT_CODE")
            self.bot.send_message(uid, "Введите персональный код:", keyboard=self.wait_kb())

        elif cmd == "balance":
            if not p:
                return self.bot.send_message(uid, "Сначала войдите.", keyboard=self.main_kb(role))
            balance = self.db.get_participant_balance(p['id'])
            history = self.db.get_balance_history(p['id']) if hasattr(self.db, 'get_balance_history') else []
            lines = [f"💰 Ваш баланс: {balance} баллов"]
            if history:
                lines.append("\n📜 История:")
                for item in history:
                    sign = "+" if item['amount'] > 0 else ""
                    lines.append(f"  {sign}{item['amount']}: {item['description']} ({item['created_at']})")
            self.bot.send_message(uid, "\n".join(lines), keyboard=self.main_kb(role))

        elif cmd == "register_event":
            if not p:
                return self.bot.send_message(uid, "Сначала войдите.", keyboard=self.main_kb(role))
            evts = self.db.get_active_events()
            if not evts:
                return self.bot.send_message(uid, "Нет активных мероприятий.", keyboard=self.main_kb(role))
            kb = Keyboard(inline=True)
            for i, e in enumerate(evts):
                my_team = self.db.get_my_team_for_event(e['id'], uid)
                if my_team:
                    members = [m.strip() for m in my_team['team_members'].split(',') if m.strip()]
                    names = ", ".join(members)
                    kb.add_callback_button(
                        e['name'], color="secondary",
                        payload=json.dumps({"action": "show_my_team", "event_id": e['id'], "names": names})
                    )
                    if self.db.can_cancel_registration(e['id'], uid):
                        kb.add_callback_button(
                            "Выйти", color="negative",
                            payload=json.dumps({"action": "leave_team", "target": "event", "target_id": e['id']})
                        )
                else:
                    kb.add_callback_button(
                        e['name'], color="primary",
                        payload=json.dumps({"action": "select_event", "event_id": e['id']})
                    )
                if i < len(evts) - 1:
                    kb.add_line()
            self.bot.send_message(uid, "Выберите мероприятие:", keyboard=kb)

        elif cmd == "register_mini_course":
            if not p:
                return self.bot.send_message(uid, "Сначала войдите.", keyboard=self.main_kb(role))
            mcs = self.db.get_published_mini_courses_with_stats()
            if not mcs:
                return self.bot.send_message(uid, "Нет опубликованных курсов.", keyboard=self.main_kb(role))

            lines = ["📚 Доступные мини-курсы:\n"]
            kb = Keyboard(inline=True)
            for i, mc in enumerate(mcs):
                slots_info = []
                for s in mc.get('slots', []):
                    free = mc['max_participants'] - s['registered']
                    slots_info.append(f"{s['time']} (ост. {free})")
                slots_str = ", ".join(slots_info)
                lines.append(f"🔹 {mc['name']}")
                lines.append(f"   {mc['description']}")
                lines.append(f"   👥 Записано: {mc['total_registered']}/{mc['max_participants']}")
                lines.append(f"   ⏰ {slots_str}\n")

                already = False
                for s in mc.get('slots', []):
                    if self.db.get_my_mini_course_team(mc['id'], s['id'], uid):
                        already = True
                        break

                if already:
                    kb.add_callback_button(
                        mc['name'], color="secondary",
                        payload=json.dumps({"action": "show_my_team", "mini_course_id": mc['id']})
                    )
                    kb.add_callback_button(
                        "Отменить", color="negative",
                        payload=json.dumps({"action": "leave_team", "target": "mini_course", "target_id": mc['id']})
                    )
                else:
                    kb.add_callback_button(
                        mc['name'], color="primary",
                        payload=json.dumps({"action": "select_mini_course", "mini_course_id": mc['id']})
                    )
                if i < len(mcs) - 1:
                    kb.add_line()

            self.bot.send_message(uid, "\n".join(lines), keyboard=kb)

        elif cmd == "complaint":
            if not p:
                return self.bot.send_message(uid, "Сначала войдите.", keyboard=self.main_kb(role))
            self._set_state(uid, "WAIT_COMPLAINT")
            self.bot.send_message(uid, "Опишите вашу жалобу:", keyboard=self.wait_kb())

        # === Админские команды ===
        elif role in ("admin", "super_admin"):
            if cmd == "events":
                kb = Keyboard(one_time=False, inline=False)
                kb.add_button("Создать", color="primary", payload=json.dumps({"cmd": "create_event"}))
                kb.add_line()
                kb.add_button("Список", payload=json.dumps({"cmd": "view_events"}))
                kb.add_line()
                kb.add_button("Команды", payload=json.dumps({"cmd": "view_event_teams"}))
                kb.add_line()
                kb.add_button("Назад", color="secondary", payload=json.dumps({"cmd": "back"}))
                self.bot.send_message(uid, "Меню мероприятий:", keyboard=kb)

            elif cmd == "mini_courses":
                kb = Keyboard(one_time=False, inline=False)
                kb.add_button("Создать", color="primary", payload=json.dumps({"cmd": "create_mini_course"}))
                kb.add_line()
                kb.add_button("Опубликовать", payload=json.dumps({"cmd": "publish_mini_courses"}))
                kb.add_line()
                kb.add_button("Неопубликованные", payload=json.dumps({"cmd": "view_unpublished"}))
                kb.add_line()
                kb.add_button("Опубликованные", payload=json.dumps({"cmd": "view_published"}))
                kb.add_line()
                kb.add_button("Назад", color="secondary", payload=json.dumps({"cmd": "back"}))
                self.bot.send_message(uid, "Меню мини-курсов:", keyboard=kb)

            elif cmd == "complaints":
                complaints = self.db.get_recent_complaints()
                if not complaints:
                    return self.bot.send_message(uid, "Новых жалоб нет.", keyboard=self.main_kb(role))
                lines = ["🚨 Жалобы:"]
                kb = Keyboard(inline=True)
                for i, c in enumerate(complaints):
                    lines.append(f"\n#{c['id']} | {c['first_name']} {c['last_name']}")
                    lines.append(f"   📝 {c['message']}")
                    lines.append(f"   ⏰ {c['created_at']}")
                    kb.add_callback_button(
                        f"Решить #{c['id']}", color="positive",
                        payload=json.dumps({"action": "resolve_complaint", "complaint_id": c['id']})
                    )
                    if i < len(complaints) - 1:
                        kb.add_line()
                self.bot.send_message(uid, "\n".join(lines), keyboard=kb)

            elif cmd == "settings":
                kb = Keyboard(one_time=False, inline=False)
                kb.add_button("Добавить админа", color="primary", payload=json.dumps({"cmd": "add_admin"}))
                kb.add_line()
                kb.add_button("Участники", payload=json.dumps({"cmd": "participants"}))
                kb.add_line()
                kb.add_button("Пополнить баланс", color="primary", payload=json.dumps({"cmd": "top_up_balance"}))
                kb.add_line()
                kb.add_button("Команды на мероприятиях", payload=json.dumps({"cmd": "admin_event_teams"}))
                kb.add_line()
                kb.add_button("Записи на мини-курсы", payload=json.dumps({"cmd": "admin_mini_course_regs"}))
                kb.add_line()
                if role == "super_admin":
                    kb.add_button("🏪 Запустить/Остановить ярмарку", color="primary", payload=json.dumps({"cmd": "toggle_fair"}))
                    kb.add_line()
                kb.add_button("Назад", color="secondary", payload=json.dumps({"cmd": "back"}))
                self.bot.send_message(uid, "Настройки:", keyboard=kb)

            elif cmd == "add_admin":
                invite_code = self.db.generate_admin_invite_code()
                self.bot.send_message(uid, f"🔑 Сгенерирован пригласительный код для админа:\n\n`{invite_code}`\n\nОтправьте этот код человеку, которого хотите сделать админом. Он должен нажать 'Войти' и ввести этот код.", keyboard=self.main_kb(role))

            elif cmd == "top_up_balance":
                participants = self.db.get_all_participants()
                if not participants:
                    return self.bot.send_message(uid, "Нет участников для пополнения баланса.", keyboard=self.main_kb(role))
                self._set_state(uid, "WAIT_SELECT_PARTICIPANT", {"participants": participants, "page": 0})
                self.bot.send_message(uid, "Выберите участника для пополнения баланса:", keyboard=self.participant_selection_kb({"participants": participants, "page": 0}))

            elif cmd == "create_event":
                self._set_state(uid, "WAIT_EV_NAME")
                self.bot.send_message(uid, "Введите название мероприятия:", keyboard=self.wait_kb())

            elif cmd == "view_events":
                evts = self.db.get_active_events()
                if not evts:
                    return self.bot.send_message(uid, "Нет мероприятий.", keyboard=self.main_kb(role))
                lines = ["📅 Активные мероприятия:"]
                for e in evts:
                    lines.append(f"\n🔹 {e['name']}")
                    lines.append(f"   📝 {e['description']}")
                    lines.append(f"   👥 Команда: от {e['min_team_size']} до {e['max_team_size']}")
                self.bot.send_message(uid, "\n".join(lines), keyboard=self.main_kb(role))

            elif cmd == "view_event_teams":
                evts = self.db.get_active_events()
                lines = ["📋 Команды на мероприятиях:"]
                for e in evts:
                    regs = self.db.get_event_teams_list(e['id'])
                    lines.append(f"\n🔹 {e['name']}:")
                    if not regs:
                        lines.append("   ⚪ Нет команд")
                    for r in regs:
                        members = r.get('team_members', '') or ''
                        lines.append(f"   👤 Капитан: {r['first_name']} {r['last_name']}")
                        lines.append(f"      🏷 {members}")
                self.bot.send_message(uid, "\n".join(lines), keyboard=self.main_kb(role))

            elif cmd == "create_mini_course":
                self._set_state(uid, "WAIT_MC_NAME")
                self.bot.send_message(uid, "Введите название мини-курса:", keyboard=self.wait_kb())

            elif cmd == "publish_mini_courses":
                count = self.db.publish_mini_courses() if hasattr(self.db, 'publish_mini_courses') else 0
                self.bot.send_message(uid, f"✅ Опубликовано: {count} курсов.", keyboard=self.main_kb(role))

            elif cmd == "view_unpublished":
                mcs = self.db.get_unpublished_mini_courses() if hasattr(self.db, 'get_unpublished_mini_courses') else []
                if not mcs:
                    return self.bot.send_message(uid, "Нет неопубликованных курсов.", keyboard=self.main_kb(role))
                lines = ["📚 Неопубликованные:"]
                for mc in mcs:
                    slots = self.db.get_time_slots(mc['id'])
                    slot_str = ", ".join([s['time'] for s in slots])
                    lines.append(f"\n🔸 {mc['name']}")
                    lines.append(f"   📝 {mc['description']}")
                    lines.append(f"   👥 Макс: {mc['max_participants']}")
                    lines.append(f"   ⏰ {slot_str}")
                self.bot.send_message(uid, "\n".join(lines), keyboard=self.main_kb(role))

            elif cmd == "view_published":
                mcs = self.db.get_published_mini_courses()
                if not mcs:
                    return self.bot.send_message(uid, "Нет опубликованных курсов.", keyboard=self.main_kb(role))
                lines = ["📚 Опубликованные:"]
                for mc in mcs:
                    slots = self.db.get_time_slots(mc['id'])
                    slot_str = ", ".join([s['time'] for s in slots])
                    regs = self.db.get_mini_course_registrations(mc['id']) if hasattr(self.db, 'get_mini_course_registrations') else []
                    lines.append(f"\n🔹 {mc['name']}")
                    lines.append(f"   📝 {mc['description']}")
                    lines.append(f"   👥 Записано: {len(regs)}/{mc['max_participants']}")
                    lines.append(f"   ⏰ {slot_str}")
                self.bot.send_message(uid, "\n".join(lines), keyboard=self.main_kb(role))

            elif cmd == "participants":
                kb = Keyboard(one_time=False, inline=False)
                kb.add_button("Пред-регистрация", color="primary", payload=json.dumps({"cmd": "pre_register"}))
                kb.add_line()
                kb.add_button("Список", payload=json.dumps({"cmd": "view_participants"}))
                kb.add_line()
                kb.add_button("Назад", color="secondary", payload=json.dumps({"cmd": "back"}))
                self.bot.send_message(uid, "Управление участниками:", keyboard=kb)

            elif cmd == "pre_register":
                self._set_state(uid, "WAIT_PARTICIPANT_NAMES")
                self.bot.send_message(
                    uid,
                    "Введите список участников через запятую:\nФамилия Имя, Фамилия Имя",
                    keyboard=self.wait_kb()
                )

            elif cmd == "view_participants":
                parts = self.db.get_pre_registered_participants()
                if not parts:
                    return self.bot.send_message(uid, "Список пуст.", keyboard=self.main_kb(role))
                lines = ["👥 Зарегистрированные:"]
                for pr in parts:
                    status = "✅" if pr['is_used'] else "⬜"
                    lines.append(f"{status} {pr['last_name']} {pr['first_name']}: `{pr['personal_code']}`")
                self.bot.send_message(uid, "\n".join(lines), keyboard=self.main_kb(role))

            elif cmd == "admin_event_teams":
                evts = self.db.get_active_events()
                if not evts:
                    return self.bot.send_message(uid, "Нет активных мероприятий.", keyboard=self.main_kb(role))
                lines = ["📋 Команды на мероприятиях:"]
                for e in evts:
                    teams = self.db.get_event_teams_list(e['id'])
                    lines.append(f"\n🔹 {e['name']}:")
                    if not teams:
                        lines.append("   ⚪ Нет команд")
                    for t in teams:
                        members = t.get('team_members', '') or ''
                        lines.append(f"   👤 Капитан: {t['first_name']} {t['last_name']}")
                        lines.append(f"      🏷 {members}")
                self.bot.send_message(uid, "\n".join(lines), keyboard=self.main_kb(role))

            elif cmd == "admin_mini_course_regs":
                mcs = self.db.get_published_mini_courses()
                if not mcs:
                    return self.bot.send_message(uid, "Нет опубликованных мини-курсов.", keyboard=self.main_kb(role))
                lines = ["📚 Записи на мини-курсы:"]
                for mc in mcs:
                    regs = self.db.get_mini_course_full_registrations(mc['id'])
                    lines.append(f"\n🔹 {mc['name']} ({len(regs)}/{mc['max_participants']}):")
                    if not regs:
                        lines.append("   ⚪ Нет записей")
                    for r in regs:
                        lines.append(f"   👤 {r['last_name']} {r['first_name']} — ⏰ {r['slot_time']}")
                self.bot.send_message(uid, "\n".join(lines), keyboard=self.main_kb(role))

            elif cmd == "toggle_fair":
                if self.db.is_fair_active():
                    self.db.stop_fair()
                    self.bot.send_message(uid, "🏪 Ярмарка остановлена.", keyboard=self.main_kb(role))
                else:
                    evts = self.db.get_active_events()
                    if not evts:
                        return self.bot.send_message(uid, "Нет активных мероприятий для запуска ярмарки.", keyboard=self.main_kb(role))

                    # Фильтруем только мероприятия, подходящие для ярмарки
                    fair_events = [e for e in evts if e.get('is_fair', 0) == 1]
                    if not fair_events:
                        return self.bot.send_message(uid, "Нет активных мероприятий, подходящих для ярмарки.", keyboard=self.main_kb(role))

                    kb = Keyboard(inline=True)
                    for i, e in enumerate(fair_events):
                        kb.add_callback_button(
                            e['name'], color="primary",
                            payload=json.dumps({"action": "start_fair", "event_id": e['id']})
                        )
                        if i < len(fair_events) - 1:
                            kb.add_line()
                    self.bot.send_message(uid, "Выберите мероприятие для запуска ярмарки:", keyboard=kb)

            else:
                self.bot.send_message(uid, "Команда не распознана.", keyboard=self.main_kb(role))
        else:
            self.bot.send_message(uid, "Эта команда недоступна.", keyboard=self.main_kb(role))

    # === Обработка FSM (текстовый ввод) ===
    def process_fsm(self, uid: int, role: str, text: str, state: str, ctx: Dict):
        p = self.db.get_participant_by_user_id(uid)

        if state == "WAIT_CODE":
            # Пробуем сначала как пригласительный код админа
            ok, msg, pd = self.db.login_with_admin_invite(uid, text.strip())
            if ok:
                self._clear_state(uid)
                new_role = pd.get("role") if pd else role
                self.bot.send_message(uid, msg, keyboard=self.main_kb(new_role))
                return
            # Если не получилось, пробуем как персональный код участника
            ok, msg, pd = self.db.login_with_personal_code(uid, text.strip())
            self._clear_state(uid)
            new_role = pd.get("role") if pd else role
            self.bot.send_message(uid, msg, keyboard=self.main_kb(new_role))

        elif state == "WAIT_COMPLAINT":
            if not p:
                self._clear_state(uid)
                return self.bot.send_message(uid, "Сначала войдите.", keyboard=self.main_kb(role))
            self.db.submit_complaint(p['id'], text)
            self._clear_state(uid)
            self.bot.send_message(uid, "✅ Жалоба отправлена. Спасибо!", keyboard=self.main_kb(role))

        elif state == "WAIT_ADMIN_ID":
            vk_id = text.strip().lstrip('@')
            if not vk_id.isdigit():
                return self.bot.send_message(uid, "❌ Введите числовой ID (например: 123456789)", keyboard=self.wait_kb())
            info = self.bot.send_api_method("users.get", {"user_ids": int(vk_id)})
            if not info or len(info) == 0:
                return self.bot.send_message(uid, "❌ Пользователь не найден в VK.", keyboard=self.wait_kb())
            ok, msg = self.db.add_admin_by_vk_id(int(vk_id), info[0]["first_name"], info[0]["last_name"])
            self._clear_state(uid)
            self.bot.send_message(uid, msg, keyboard=self.main_kb(role))

        elif state == "WAIT_PARTICIPANT_NAMES":
            names = [n.strip() for n in text.split(",") if n.strip()]
            results = self.db.pre_register_participants(names)
            lines = [f"📋 Обработано: {len(results)}"]
            for r in results:
                if r['success']:
                    lines.append(f"✅ {r['name']}: `{r['personal_code']}`")
                else:
                    lines.append(f"❌ {r['name']}: {r.get('error', 'Ошибка')}")
            self._clear_state(uid)
            self.bot.send_message(uid, "\n".join(lines), keyboard=self.main_kb(role))

        # === FSM создания мероприятия ===
        elif state == "WAIT_EV_NAME":
            ctx["name"] = text.strip()
            self._set_state(uid, "WAIT_EV_DESC", ctx)
            self.bot.send_message(uid, "Введите описание мероприятия:", keyboard=self.wait_kb())

        elif state == "WAIT_EV_DESC":
            ctx["desc"] = text.strip()
            self._set_state(uid, "WAIT_EV_MIN", ctx)
            self.bot.send_message(uid, "Введите МИНИМАЛЬНЫЙ размер команды:", keyboard=self.wait_kb())

        elif state == "WAIT_EV_MIN":
            try:
                val = int(text.strip())
                if val <= 0:
                    raise ValueError
                ctx["min"] = val
                self._set_state(uid, "WAIT_EV_MAX", ctx)
                self.bot.send_message(uid, "Введите МАКСИМАЛЬНЫЙ размер команды:", keyboard=self.wait_kb())
            except ValueError:
                self.bot.send_message(uid, "❌ Введите положительное целое число.", keyboard=self.wait_kb())

        elif state == "WAIT_EV_MAX":
            try:
                val = int(text.strip())
                if val < ctx.get("min", 1):
                    raise ValueError
                eid = self.db.create_event(ctx["name"], ctx["desc"], ctx["min"], val)
                self._clear_state(uid)
                self.bot.send_message(uid, f"✅ Мероприятие создано (ID: {eid})", keyboard=self.main_kb(role))
            except ValueError:
                self.bot.send_message(uid, "❌ Максимум должен быть >= минимума. Повторите:", keyboard=self.wait_kb())

        # === FSM создания мини-курса ===
        elif state == "WAIT_MC_NAME":
            ctx["name"] = text.strip()
            self._set_state(uid, "WAIT_MC_DESC", ctx)
            self.bot.send_message(uid, "Введите описание мини-курса:", keyboard=self.wait_kb())

        elif state == "WAIT_MC_DESC":
            ctx["desc"] = text.strip()
            self._set_state(uid, "WAIT_MC_MAX", ctx)
            self.bot.send_message(uid, "Введите максимальное количество участников:", keyboard=self.wait_kb())

        elif state == "WAIT_MC_MAX":
            try:
                val = int(text.strip())
                if val <= 0:
                    raise ValueError
                ctx["max"] = val
                self._set_state(uid, "WAIT_MC_SLOTS", ctx)
                self.bot.send_message(uid, "Введите временные слоты через запятую:\n11:00, 13:00, 15:00", keyboard=self.wait_kb())
            except ValueError:
                self.bot.send_message(uid, "❌ Введите положительное целое число.", keyboard=self.wait_kb())

        elif state == "WAIT_MC_SLOTS":
            slots = [s.strip() for s in text.split(",") if s.strip()]
            mc_id = self.db.create_mini_course(ctx["name"], ctx["desc"], ctx["max"])
            for slot in slots:
                self.db.add_time_slot(mc_id, slot)
            self._clear_state(uid)
            self.bot.send_message(
                uid,
                f"✅ Мини-курс создан (ID: {mc_id})\nСлоты: {', '.join(slots)}\n\nОпубликуйте через меню.",
                keyboard=self.main_kb(role)
            )

        # === FSM пополнения баланса ===
        elif state == "WAIT_TOP_UP_AMOUNT":
            try:
                amount = int(text.strip())
                if amount <= 0:
                    raise ValueError
                participant_id = ctx["participant_id"]
                participant = self.db.get_participant_by_user_id(participant_id)
                if not participant:
                    raise ValueError("Участник не найден")
                self.db.add_balance_to_participant(participant_id, amount, f"Пополнение от админа")
                self._clear_state(uid)
                self.bot.send_message(uid, f"✅ Баланс участника {participant['first_name']} {participant['last_name']} пополнен на {amount} баллов.", keyboard=self.main_kb(role))
            except ValueError as e:
                self.bot.send_message(uid, f"❌ {str(e) if str(e) != 'Участник не найден' else 'Введите положительное целое число.'}", keyboard=self.wait_kb())

        # === FSM запуска ярмарки ===
        elif state == "WAIT_FAIR_BUDGET":
            try:
                budget = int(text.strip())
                if budget <= 0:
                    raise ValueError
                self.db.start_fair(ctx["event_id"], budget)
                self._clear_state(uid)
                self.bot.send_message(uid, f"🏪 Ярмарка запущена! Начальный бюджет для каждой команды: {budget} монет.", keyboard=self.main_kb(role))
            except ValueError:
                self.bot.send_message(uid, "❌ Введите положительное целое число.", keyboard=self.wait_kb())

        else:
            self._clear_state(uid)
            self.bot.send_message(uid, "Сессия сброшена.", keyboard=self.main_kb(role))