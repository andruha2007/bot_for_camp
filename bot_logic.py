import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
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
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=20)
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
        with self._lock:
            self.states[uid] = {"state": state, "ctx": ctx or {}}

    def _clear_state(self, uid: int):
        with self._lock:
            self.states.pop(uid, None)

    def _get_state(self, uid: int) -> Dict[str, Any]:
        with self._lock:
            return self.states.get(uid, {"state": "MAIN", "ctx": {}}).copy()

    # === Генерация клавиатуры ===
    def main_kb(self, role: str) -> Keyboard:
        kb = Keyboard(one_time=False, inline=False)
        fair_active = self.db.is_fair_active() and not self.db.is_fair_completed()
        if role in ("admin", "super_admin"):
            kb.add_button("Мероприятие", color="primary", payload=json.dumps({"cmd": "events"}))
            kb.add_button("Мини-курсы", color="primary", payload=json.dumps({"cmd": "mini_courses"}))
            kb.add_line()
            kb.add_button("Жалобы", color="primary", payload=json.dumps({"cmd": "complaints"}))
            kb.add_line()
            kb.add_button("Настройки", color="secondary", payload=json.dumps({"cmd": "settings"}))
            if fair_active:
                kb.add_line()
                kb.add_button("🏪 Перейти к ярмарке", color="primary", payload=json.dumps({"cmd": "go_fair"}))
        elif role == "participant":
            kb.add_button("Баланс", color="primary", payload=json.dumps({"cmd": "balance"}))
            kb.add_line()
            kb.add_button("Жалоба", color="negative", payload=json.dumps({"cmd": "complaint"}))
            kb.add_line()
            kb.add_button("Мероприятия", color="primary", payload=json.dumps({"cmd": "register_event"}))
            kb.add_line()
            kb.add_button("Мини-курсы", color="primary", payload=json.dumps({"cmd": "register_mini_course"}))
            if fair_active:
                kb.add_line()
                kb.add_button("🏪 Перейти к ярмарке", color="primary", payload=json.dumps({"cmd": "go_fair"}))
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
        users = ctx.get("users", [])
        selected = ctx.get("selected", [])
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

    def balance_selection_kb(self, ctx: Dict) -> Keyboard:
        kb = Keyboard(inline=True)
        page = ctx.get("page", 0)
        participants = ctx.get("participants", [])
        selected = ctx.get("selected", [])
        per_page = 4
        start, end = page * per_page, (page + 1) * per_page

        for p in participants[start:end]:
            is_sel = p['id'] in selected
            emoji = "✅" if is_sel else "⬜"
            label = f"{emoji} {p['last_name']} {p['first_name']} ({p.get('balance', 0)} HSE-коинов)"
            color = "positive" if is_sel else "secondary"
            kb.add_callback_button(
                label[:40],
                color=color,
                payload=json.dumps({"action": "toggle_balance_user", "participant_db_id": p['id']})
            )
            kb.add_line()

        nav_added = False
        if page > 0:
            kb.add_callback_button("⬅️", color="secondary", payload=json.dumps({"action": "bal_page", "page": page - 1}))
            nav_added = True
        if end < len(participants):
            kb.add_callback_button("➡️", color="secondary", payload=json.dumps({"action": "bal_page", "page": page + 1}))
            nav_added = True
        if nav_added:
            kb.add_line()

        mode = ctx.get("mode", "topup")
        btn_label = "Завершить выбор"
        kb.add_callback_button(
            f"{btn_label} ({len(selected)})",
            color="positive",
            payload=json.dumps({"action": "submit_bal_sel"})
        )
        return kb

    def balance_selection_text(self, ctx: Dict) -> str:
        mode = ctx.get("mode", "topup")
        action_text = "пополнения" if mode == "topup" else "списания"
        selected_count = len(ctx.get("selected", []))
        return f"Выберите участников для {action_text} баланса:\n✅ Выбрано: {selected_count}\n\nНажимайте на участников, чтобы добавить/убрать:"

    # === Клавиатуры для админа (запись участников) ===
    def admin_participant_kb(self, participants, page=0, action_prefix="admin_reg_event_pick", page_target=""):
        kb = Keyboard(inline=True)
        per_page = 3
        start, end = page * per_page, (page + 1) * per_page

        for p in participants[start:end]:
            label = f"{p['last_name']} {p['first_name']}"
            kb.add_callback_button(
                label[:40], color="primary",
                payload=json.dumps({"action": action_prefix, "pid": p['id'], "page": page})
            )
            kb.add_line()

        nav_target = page_target or action_prefix
        has_prev = page > 0
        has_next = end < len(participants)
        if has_prev or has_next:
            if has_prev:
                kb.add_callback_button("⬅️", color="secondary", payload=json.dumps({"action": "admin_part_page", "page": page - 1, "target": nav_target, "prefix": action_prefix}))
            if has_next:
                kb.add_callback_button("➡️", color="secondary", payload=json.dumps({"action": "admin_part_page", "page": page + 1, "target": nav_target, "prefix": action_prefix}))
            kb.add_line()
        kb.add_callback_button("◀️ Назад", color="secondary", payload=json.dumps({"action": nav_target}))
        return kb

    def admin_event_selection_kb(self, events, pid):
        kb = Keyboard(inline=True)
        max_show = min(len(events), 5)
        for i in range(max_show):
            ev = events[i]
            label = ev['name']
            if ev.get('is_closed'):
                label = f"🔒 {label}"
            kb.add_callback_button(
                label[:40], color="secondary" if ev.get('is_closed') else "primary",
                payload=json.dumps({"action": "admin_reg_event_do", "pid": pid, "event_id": ev['id']})
            )
            kb.add_line()
        kb.add_callback_button("◀️ Назад", color="secondary", payload=json.dumps({"action": "admin_reg_event"}))
        return kb

    def admin_mc_selection_kb(self, courses, pid):
        kb = Keyboard(inline=True)
        max_show = min(len(courses), 5)
        for i in range(max_show):
            mc = courses[i]
            label = mc['name']
            if mc.get('is_closed'):
                label = f"🔒 {label}"
            kb.add_callback_button(
                label[:40], color="secondary" if mc.get('is_closed') else "primary",
                payload=json.dumps({"action": "admin_reg_mc_show_slots", "pid": pid, "mc_id": mc['id']})
            )
            kb.add_line()
        kb.add_callback_button("◀️ Назад", color="secondary", payload=json.dumps({"action": "admin_reg_mc"}))
        return kb

    def admin_mc_slot_selection_kb(self, mc, pid):
        kb = Keyboard(inline=True)
        slots = self.db.get_time_slots(mc['id'])
        max_show = min(len(slots), 5)
        for i in range(max_show):
            slot = slots[i]
            slot_max = slot['max_participants'] if slot['max_participants'] > 0 else mc['max_participants']
            cnt = self.db.get_mini_course_registrations(mc['id'])
            reg_count = sum(1 for r in cnt if r.get('time_slot_id') == slot['id'])
            free = slot_max - reg_count
            label = f"{slot['time']} (ост. {free}/{slot_max})"
            color = "secondary" if free <= 0 else "primary"
            kb.add_callback_button(
                label, color=color,
                payload=json.dumps({"action": "admin_reg_mc_do", "pid": pid, "mc_id": mc['id'], "ts_id": slot['id']})
            )
            kb.add_line()
        kb.add_callback_button("◀️ Назад", color="secondary", payload=json.dumps({"action": "admin_reg_mc_show_courses", "pid": pid}))
        return kb

    def admin_remove_event_kb(self, registrations, pid):
        """Показывает регистрации участника на мероприятия для удаления"""
        kb = Keyboard(inline=True)
        max_show = min(len(registrations), 5)
        for i in range(max_show):
            reg = registrations[i]
            ev = self.db.get_event(reg['event_id'])
            if not ev:
                continue
            is_captain = reg.get('captain_id') == reg['participant_id']
            label = f"{'👑' if is_captain else '👤'} {ev['name']}"
            kb.add_callback_button(
                label[:40], color="negative",
                payload=json.dumps({"action": "admin_unreg_event_do", "pid": pid, "event_id": ev['id']})
            )
            kb.add_line()
        kb.add_callback_button("◀️ Назад", color="secondary", payload=json.dumps({"action": "admin_remove_from_event"}))
        return kb

    def _admin_create_team_event_kb(self, events, page):
        kb = Keyboard(inline=True)
        per_page = 5
        start, end = page * per_page, (page + 1) * per_page
        for ev in events[start:end]:
            kb.add_callback_button(ev['name'][:40], color="primary",
                payload=json.dumps({"action": "admin_create_team_pick_event", "event_id": ev['id']}))
            kb.add_line()
        has_prev = page > 0
        has_next = end < len(events)
        if has_prev:
            kb.add_callback_button("⬅️", color="secondary", payload=json.dumps({"action": "admin_create_team_page", "page": page - 1}))
        if has_next:
            kb.add_callback_button("➡️", color="secondary", payload=json.dumps({"action": "admin_create_team_page", "page": page + 1}))
        if has_prev or has_next:
            kb.add_line()
        kb.add_callback_button("◀️ Назад", color="secondary", payload=json.dumps({"action": "show_admin_events"}))
        return kb

    def admin_remove_mc_kb(self, registrations, pid):
        """Показывает регистрации участника на мини-курсы для удаления"""
        kb = Keyboard(inline=True)
        max_show = min(len(registrations), 5)
        for i in range(max_show):
            reg = registrations[i]
            mc = self.db.get_mini_course(reg['mini_course_id'])
            if not mc:
                continue
            slot = self.db.get_time_slot(reg['time_slot_id'])
            time_str = f" ({slot['time']})" if slot else ""
            kb.add_callback_button(
                f"{mc['name']}{time_str}"[:40], color="negative",
                payload=json.dumps({"action": "admin_unreg_mc_do", "pid": pid, "mc_id": mc['id']})
            )
            kb.add_line()
        kb.add_callback_button("◀️ Назад", color="secondary", payload=json.dumps({"action": "admin_remove_from_mc"}))
        return kb

    # === Клавиатуры для ярмарки ===
    def _fair_participant_kb(self) -> Keyboard:
        kb = Keyboard(one_time=False, inline=False)
        kb.add_button("Моя команда", color="primary", payload=json.dumps({"cmd": "fair_my_team"}))
        kb.add_line()
        kb.add_button("Покупка", color="primary", payload=json.dumps({"cmd": "fair_buy"}))
        kb.add_line()
        kb.add_button("Выйти в главное меню", color="negative", payload=json.dumps({"cmd": "back"}))
        return kb

    def _fair_team_panel_kb(self) -> Keyboard:
        kb = Keyboard(one_time=False, inline=False)
        kb.add_button("Редактировать анкету", color="primary", payload=json.dumps({"cmd": "fair_edit_team"}))
        kb.add_line()
        kb.add_button("Товары", color="primary", payload=json.dumps({"cmd": "fair_items"}))
        kb.add_line()
        kb.add_button("Баланс", color="primary", payload=json.dumps({"cmd": "fair_balance"}))
        kb.add_line()
        kb.add_button("История операций", color="primary", payload=json.dumps({"cmd": "fair_history"}))
        kb.add_line()
        kb.add_button("Назад", color="negative", payload=json.dumps({"cmd": "fair_main"}))
        return kb

    def _fair_admin_kb(self) -> Keyboard:
        kb = Keyboard(one_time=False, inline=False)
        kb.add_button("Команды", color="primary", payload=json.dumps({"cmd": "fair_teams"}))
        kb.add_line()
        kb.add_button("Настройки", color="primary", payload=json.dumps({"cmd": "fair_settings"}))
        kb.add_line()
        kb.add_button("Выйти в главное меню", color="negative", payload=json.dumps({"cmd": "back"}))
        return kb

    def _fair_admin_settings_kb(self, role: str) -> Keyboard:
        kb = Keyboard(one_time=False, inline=False)
        kb.add_button("Уйти на обед", color="secondary", payload=json.dumps({"cmd": "fair_lunch"}))
        kb.add_line()
        kb.add_button("Выдать штраф команде", color="negative", payload=json.dumps({"cmd": "fair_fine"}))
        kb.add_line()
        paused = self.db.is_fair_paused()
        kb.add_button("Восстановить ярмарку" if paused else "Остановить ярмарку", color="secondary", payload=json.dumps({"cmd": "fair_pause"}))
        kb.add_line()
        if role == "super_admin":
            kb.add_button("Завершить ярмарку", color="negative", payload=json.dumps({"cmd": "fair_end"}))
            kb.add_line()
            kb.add_button("Установить кулдаун", color="secondary", payload=json.dumps({"cmd": "fair_cooldown"}))
            kb.add_line()
        kb.add_button("Назад", color="negative", payload=json.dumps({"cmd": "fair_main"}))
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
        self._executor.submit(self._handle_message_safe, message)

    def _handle_message_safe(self, message):
        try:
            uid, text, payload_str = self._extract_message_data(message)
            if not uid or uid < 0:
                return

            payload = json.loads(payload_str) if payload_str else {}
            cmd = payload.get("cmd") or self._text_to_cmd(text)

            p = self.db.get_participant_by_user_id(uid)
            role = p.get("role") if p else "unregistered"
            state_data = self._get_state(uid)
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
            try:
                uid, _, _ = self._extract_message_data(message)
                if uid and uid > 0:
                    self.bot.send_message(uid, "⚠️ Произошла внутренняя ошибка. Попробуйте ещё раз.")
            except Exception:
                pass

    # === Обработчик callback-кнопок (галочки, навигация) ===
    def handle_callback(self, event):
        self._executor.submit(self._handle_callback_safe, event)

    def _handle_callback_safe(self, event):
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
                selected = ctx.get("selected", [])
                if not selected:
                    return self.bot.send_message(uid, "❌ Выберите хотя бы одного участника.", keyboard=self.team_selection_kb(ctx))
                captain_uid = ctx.get("captain_uid", selected[0])
                if captain_uid not in selected:
                    captain_uid = selected[0]
                ok, msg, errors = self.db.register_team_for_event(ctx.get("target_id"), captain_uid, selected)
                if ok:
                    self._clear_state(uid)
                    self.bot.send_message(uid, f"🎉 {msg}", keyboard=self.main_kb(role))
                else:
                    error_msg = "\n".join(errors) if errors else msg
                    self.bot.send_message(uid, f"❌ {error_msg}", keyboard=self.team_selection_kb(ctx))

            elif action == "leave_team":
                if payload.get("target") == "event":
                    ok, msg, captain_uid, disbanded = self.db.leave_event_team(payload["target_id"], uid)
                    if ok and disbanded and captain_uid and captain_uid != uid:
                        self.bot.send_message(
                            captain_uid,
                            "⚠️ Ваша команда распалась, так как один из участников вышел, и в команде осталось меньше участников, чем требуется."
                        )
                else:
                    self.db.leave_mini_course_team(payload["target_id"], 0, uid)
                    ok, msg = True, "Вы вышли из записи на мини-курс."
                self.bot.send_message(uid, msg, keyboard=self.main_kb(role))

            elif action == "disband_team":
                ok, msg = self.db.disband_event_team(payload["event_id"], uid)
                self.bot.send_message(uid, msg, keyboard=self.main_kb(role))

            elif action == "admin_create_team_page":
                ctx = self.states.get(uid, {}).get("ctx", {})
                page = payload["page"]
                ctx["page"] = page
                self.states[uid]["ctx"] = ctx
                events = ctx.get("events", [])
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": "Выберите мероприятие для создания команды:",
                    "keyboard": self._admin_create_team_event_kb(events, page).get_keyboard()
                })

            elif action == "admin_create_team_pick_event":
                event_id = payload["event_id"]
                free_participants = self.db.get_unregistered_participants_for_event(event_id, 0)
                if not free_participants:
                    return self.bot.send_message(uid, "Нет свободных участников для этого мероприятия.", keyboard=self.main_kb(role))
                self._set_state(uid, "SELECTING_TEAM", {
                    "target": "event",
                    "target_id": event_id,
                    "users": free_participants,
                    "selected": [],
                    "page": 0
                })
                ctx2 = self.states[uid]["ctx"]
                self.bot.send_message(uid, "Выберите участников команды.\n👑 Первый выбранный станет капитаном:\n" + self.team_selection_text(ctx2), keyboard=self.team_selection_kb(ctx2))

            elif action == "show_my_team":
                event_id = payload.get("event_id")
                mini_course_id = payload.get("mini_course_id")
                if event_id:
                    team_info = self.db.get_team_info_for_event(event_id, uid)
                    if team_info:
                        members_lines = []
                        for m in team_info['members']:
                            prefix = "👑" if m['is_captain'] else "👤"
                            members_lines.append(f"{prefix} {m['last_name']} {m['first_name']}")

                        kb = Keyboard(inline=True)
                        if team_info['is_captain']:
                            kb.add_callback_button(
                                "Распустить команду", color="negative",
                                payload=json.dumps({"action": "disband_team", "event_id": event_id})
                            )
                            kb.add_line()
                        kb.add_callback_button(
                            "Выйти из команды", color="negative",
                            payload=json.dumps({"action": "leave_team", "target": "event", "target_id": event_id})
                        )

                        msg = (
                            f"📅 {team_info['event_name']}\n"
                            f"👥 Команда: {team_info['team_size']}/{team_info['max_team_size']}\n"
                            f"👑 Капитан: {team_info['captain_name']}\n\n"
                            f"Участники:\n" + "\n".join(members_lines)
                        )
                        self.bot.send_message(uid, msg, keyboard=kb)
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

            elif action == "toggle_balance_user":
                if role not in ("admin", "super_admin"):
                    return self.bot.send_message(uid, "Эта команда доступна только админу.", keyboard=self.main_kb(role))
                ctx = self.states.get(uid, {}).get("ctx", {})
                target_id = payload["participant_db_id"]
                if target_id in ctx.get("selected", []):
                    ctx["selected"].remove(target_id)
                else:
                    ctx["selected"].append(target_id)
                self.states[uid]["ctx"] = ctx
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": self.balance_selection_text(ctx),
                    "keyboard": self.balance_selection_kb(ctx).get_keyboard()
                })

            elif action == "bal_page":
                if role not in ("admin", "super_admin"):
                    return self.bot.send_message(uid, "Эта команда доступна только админу.", keyboard=self.main_kb(role))
                ctx = self.states.get(uid, {}).get("ctx", {})
                ctx["page"] = payload["page"]
                self.states[uid]["ctx"] = ctx
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": self.balance_selection_text(ctx),
                    "keyboard": self.balance_selection_kb(ctx).get_keyboard()
                })

            elif action == "submit_bal_sel":
                if role not in ("admin", "super_admin"):
                    return self.bot.send_message(uid, "Эта команда доступна только админу.", keyboard=self.main_kb(role))
                ctx = self.states.get(uid, {}).get("ctx", {})
                if not ctx.get("selected"):
                    return self.bot.send_message(uid, "❌ Вы не выбрали ни одного участника.", keyboard=self.main_kb(role))
                mode = ctx.get("mode", "topup")
                action_text = "пополнения" if mode == "topup" else "списания"
                self._set_state(uid, "WAIT_BALANCE_AMOUNT", {
                    "selected": ctx["selected"],
                    "mode": mode
                })
                self.bot.send_message(uid, f"Введите сумму для {action_text} баланса выбранным участникам ({len(ctx['selected'])} чел.):", keyboard=self.wait_kb())

            elif action == "select_event":
                event_id = payload["event_id"]
                ev = self.db.get_event(event_id)
                if not ev:
                    return self.bot.send_message(uid, "Мероприятие не найдено.", keyboard=self.main_kb(role))
                if not ev.get('is_active', 0):
                    return self.bot.send_message(uid, "Это мероприятие не активно.", keyboard=self.main_kb(role))
                if ev.get('is_closed'):
                    return self.bot.send_message(uid, "Регистрация на это мероприятие уже закрыта.", keyboard=self.main_kb(role))

                # Проверяем, не состоит ли пользователь уже в команде
                my_team = self.db.get_my_team_for_event(event_id, uid)
                if my_team:
                    return self.bot.send_message(uid, "Вы уже состоите в команде для этого мероприятия.", keyboard=self.main_kb(role))

                # Получаем свободных участников (не в командах)
                free_participants = self.db.get_unregistered_participants_for_event(event_id, uid)

                # Если нет свободных участников и min_team_size = 1 — регистрируем в одиночку
                if not free_participants and ev['min_team_size'] <= 1:
                    ok, msg, errors = self.db.register_team_for_event(event_id, uid, [uid])
                    if ok:
                        self.bot.send_message(uid, f"🎉 {msg}", keyboard=self.main_kb(role))
                    else:
                        self.bot.send_message(uid, f"❌ {msg}", keyboard=self.main_kb(role))
                    return

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
                if mc.get('is_closed'):
                    return self.bot.send_message(uid, "Регистрация на этот мини-курс уже закрыта.", keyboard=self.main_kb(role))

                time_slots = self.db.get_time_slots(mini_course_id)
                if not time_slots:
                    return self.bot.send_message(uid, "Нет доступных временных слотов для этого курса.", keyboard=self.main_kb(role))

                kb = Keyboard(inline=True)
                for i, slot in enumerate(time_slots):
                    registered_count = self.db.get_mini_course_registrations(mini_course_id)
                    slot_max = slot['max_participants'] if slot['max_participants'] > 0 else mc['max_participants']
                    cnt = sum(1 for r in registered_count if r.get('time_slot_id') == slot['id'])
                    free = slot_max - cnt
                    label = f"{slot['time']} (ост. {free}/{slot_max})"
                    color = "secondary" if free <= 0 else "primary"
                    kb.add_callback_button(
                        label, color=color,
                        payload=json.dumps({"action": "select_time_slot", "mini_course_id": mini_course_id, "time_slot_id": slot['id']})
                    )
                    if i < len(time_slots) - 1:
                        kb.add_line()

                date_str = f" ({mc['date']})" if mc.get('date') else ""
                self.bot.send_message(uid, f"📚 {mc['name']}{date_str}\n{mc['description']}\n\nВыберите время:", keyboard=kb)

            elif action == "select_time_slot":
                mini_course_id = payload["mini_course_id"]
                time_slot_id = payload["time_slot_id"]
                mc = self.db.get_mini_course(mini_course_id)
                if not mc:
                    return self.bot.send_message(uid, "Мини-курс не найден.", keyboard=self.main_kb(role))
                if mc.get('is_closed'):
                    return self.bot.send_message(uid, "Регистрация на этот мини-курс уже закрыта.", keyboard=self.main_kb(role))

                # Индивидуальная запись на мини-курс
                ok, msg = self.db.register_mini_course_individual(mini_course_id, time_slot_id, uid)
                self.bot.send_message(uid, "✅ " + msg if ok else "❌ " + msg, keyboard=self.main_kb(role))

            elif action == "confirm_delete_mini_course":
                if role not in ("admin", "super_admin"):
                    return
                mc_id = payload["mc_id"]
                mc = self.db.get_mini_course(mc_id)
                if mc and not mc.get('is_published'):
                    self.db.delete_mini_course(mc_id)
                    self.bot.send_message(uid, f"🗑 Мини-курс «{mc['name']}» удалён.", keyboard=self.main_kb(role))
                else:
                    self.bot.send_message(uid, "Этот мини-курс уже опубликован или не найден.", keyboard=self.main_kb(role))

            elif action == "confirm_delete_event":
                if role not in ("admin", "super_admin"):
                    return
                event_id = payload["event_id"]
                ev = self.db.get_event(event_id)
                if ev and not ev.get('is_published'):
                    self.db.delete_event(event_id)
                    self.bot.send_message(uid, f"🗑 Мероприятие «{ev['name']}» удалено.", keyboard=self.main_kb(role))
                else:
                    self.bot.send_message(uid, "Это мероприятие уже опубликовано или не найдено.", keyboard=self.main_kb(role))

            # === Админ: запись/удаление участников ===
            elif action == "admin_part_page":
                if role not in ("admin", "super_admin"):
                    return
                page = payload["page"]
                target = payload.get("target", "")
                prefix = payload.get("prefix", "admin_reg_event_pick")
                participants = self.db.get_all_participants()
                text_map = {
                    "admin_reg_event": "Выберите участника для регистрации на мероприятие:",
                    "admin_remove_from_event": "Выберите участника для удаления с мероприятия:",
                    "admin_reg_mc": "Выберите участника для записи на мини-курс:",
                    "admin_remove_from_mc": "Выберите участника для удаления с мини-курса:",
                }
                text = text_map.get(target, "Выберите участника:")
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": text,
                    "keyboard": self.admin_participant_kb(participants, page, prefix, target).get_keyboard()
                })

            elif action in ("admin_reg_event", "admin_reg_mc", "admin_remove_from_event", "admin_remove_from_mc"):
                if role not in ("admin", "super_admin"):
                    return
                participants = self.db.get_all_participants()
                text_map = {
                    "admin_reg_event": "Выберите участника для регистрации на мероприятие:",
                    "admin_remove_from_event": "Выберите участника для удаления с мероприятия:",
                    "admin_reg_mc": "Выберите участника для записи на мини-курс:",
                    "admin_remove_from_mc": "Выберите участника для удаления с мини-курса:",
                }
                prefix_map = {
                    "admin_reg_event": "admin_reg_event_pick",
                    "admin_remove_from_event": "admin_unreg_event_pick",
                    "admin_reg_mc": "admin_reg_mc_pick_part",
                    "admin_remove_from_mc": "admin_unreg_mc_pick",
                }
                text = text_map.get(action, "Выберите участника:")
                prefix = prefix_map.get(action, "admin_reg_event_pick")
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": text,
                    "keyboard": self.admin_participant_kb(participants, 0, prefix, action).get_keyboard()
                })

            elif action == "admin_reg_event_pick":
                if role not in ("admin", "super_admin"):
                    return
                pid = payload["pid"]
                p = self.db.get_participant_by_id(pid)
                if not p:
                    return self.bot.send_message(uid, "Участник не найден.", keyboard=self.main_kb(role))
                events = self.db.get_open_or_last_closed_events()
                if not events:
                    return self.bot.send_message(uid, "Нет доступных мероприятий.", keyboard=self.main_kb(role))
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": f"Выберите мероприятие для {p['last_name']} {p['first_name']}:",
                    "keyboard": self.admin_event_selection_kb(events, pid).get_keyboard()
                })

            elif action == "admin_reg_event_do":
                if role not in ("admin", "super_admin"):
                    return
                pid = payload["pid"]
                event_id = payload["event_id"]
                p = self.db.get_participant_by_id(pid)
                if not p:
                    return self.bot.send_message(uid, "Участник не найден.", keyboard=self.main_kb(role))
                ev = self.db.get_event(event_id)
                if not ev:
                    return self.bot.send_message(uid, "Мероприятие не найдено.", keyboard=self.main_kb(role))
                ok, msg, captain_uid = self.db.admin_register_participant_for_event(event_id, p['user_id'])
                if ok:
                    # Уведомить участника
                    try:
                        part_role = 'participant'
                        self.bot.send_message(
                            p['user_id'],
                            f"✅ Вы зарегистрированы на мероприятие «{ev['name']}» администратором."
                        )
                    except Exception:
                        pass
                    self.bot.send_message(uid, f"✅ {msg}", keyboard=self.main_kb(role))
                else:
                    self.bot.send_message(uid, f"❌ {msg}", keyboard=self.main_kb(role))

            elif action == "admin_unreg_event_pick":
                if role not in ("admin", "super_admin"):
                    return
                pid = payload["pid"]
                p = self.db.get_participant_by_id(pid)
                if not p:
                    return self.bot.send_message(uid, "Участник не найден.", keyboard=self.main_kb(role))
                rows = self.db.get_participant_event_registrations(pid)
                if not rows:
                    return self.bot.send_message(uid, "Участник не зарегистрирован ни на одно мероприятие.", keyboard=self.main_kb(role))
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": f"Выберите мероприятие для удаления {p['last_name']} {p['first_name']}:",
                    "keyboard": self.admin_remove_event_kb(rows, pid).get_keyboard()
                })

            elif action == "admin_unreg_event_do":
                if role not in ("admin", "super_admin"):
                    return
                pid = payload["pid"]
                event_id = payload["event_id"]
                p = self.db.get_participant_by_id(pid)
                if not p:
                    return self.bot.send_message(uid, "Участник не найден.", keyboard=self.main_kb(role))
                ev = self.db.get_event(event_id)
                ev_name = ev['name'] if ev else "?"
                ok, msg = self.db.admin_unregister_participant_from_event(event_id, p['user_id'])
                if ok:
                    try:
                        self.bot.send_message(
                            p['user_id'],
                            f"⚠️ Вы удалены с мероприятия «{ev_name}» администратором."
                        )
                    except Exception:
                        pass
                    self.bot.send_message(uid, f"✅ {msg}", keyboard=self.main_kb(role))
                else:
                    self.bot.send_message(uid, f"❌ {msg}", keyboard=self.main_kb(role))

            elif action == "admin_reg_mc_pick_part":
                if role not in ("admin", "super_admin"):
                    return
                pid = payload["pid"]
                p = self.db.get_participant_by_id(pid)
                if not p:
                    return self.bot.send_message(uid, "Участник не найден.", keyboard=self.main_kb(role))
                courses = self.db.get_open_or_last_closed_mini_courses()
                if not courses:
                    return self.bot.send_message(uid, "Нет доступных мини-курсов.", keyboard=self.main_kb(role))
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": f"Выберите мини-курс для {p['last_name']} {p['first_name']}:",
                    "keyboard": self.admin_mc_selection_kb(courses, pid).get_keyboard()
                })

            elif action == "admin_reg_mc_show_slots":
                if role not in ("admin", "super_admin"):
                    return
                pid = payload["pid"]
                mc_id = payload["mc_id"]
                p = self.db.get_participant_by_id(pid)
                if not p:
                    return self.bot.send_message(uid, "Участник не найден.", keyboard=self.main_kb(role))
                mc = self.db.get_mini_course(mc_id)
                if not mc:
                    return self.bot.send_message(uid, "Курс не найден.", keyboard=self.main_kb(role))
                date_str = f" ({mc['date']})" if mc.get('date') else ""
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": f"Выберите время для {p['last_name']} {p['first_name']} на курс «{mc['name']}»{date_str}:",
                    "keyboard": self.admin_mc_slot_selection_kb(mc, pid).get_keyboard()
                })

            elif action == "admin_reg_mc_show_courses":
                if role not in ("admin", "super_admin"):
                    return
                pid = payload["pid"]
                p = self.db.get_participant_by_id(pid)
                if not p:
                    return self.bot.send_message(uid, "Участник не найден.", keyboard=self.main_kb(role))
                courses = self.db.get_open_or_last_closed_mini_courses()
                if not courses:
                    return self.bot.send_message(uid, "Нет доступных мини-курсов.", keyboard=self.main_kb(role))
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": f"Выберите мини-курс для {p['last_name']} {p['first_name']}:",
                    "keyboard": self.admin_mc_selection_kb(courses, pid).get_keyboard()
                })

            elif action == "admin_reg_mc_do":
                if role not in ("admin", "super_admin"):
                    return
                pid = payload["pid"]
                mc_id = payload["mc_id"]
                ts_id = payload["ts_id"]
                p = self.db.get_participant_by_id(pid)
                if not p:
                    return self.bot.send_message(uid, "Участник не найден.", keyboard=self.main_kb(role))
                mc = self.db.get_mini_course(mc_id)
                if not mc:
                    return self.bot.send_message(uid, "Курс не найден.", keyboard=self.main_kb(role))
                ok, msg = self.db.admin_register_participant_for_mini_course(mc_id, ts_id, p['user_id'])
                if ok:
                    try:
                        slot = self.db.get_time_slot(ts_id)
                        time_str = f" ({slot['time']})" if slot else ""
                        self.bot.send_message(
                            p['user_id'],
                            f"✅ Вы записаны на мини-курс «{mc['name']}»{time_str} администратором."
                        )
                    except Exception:
                        pass
                    self.bot.send_message(uid, f"✅ {msg}", keyboard=self.main_kb(role))
                else:
                    self.bot.send_message(uid, f"❌ {msg}", keyboard=self.main_kb(role))

            elif action == "admin_unreg_mc_pick":
                if role not in ("admin", "super_admin"):
                    return
                pid = payload["pid"]
                p = self.db.get_participant_by_id(pid)
                if not p:
                    return self.bot.send_message(uid, "Участник не найден.", keyboard=self.main_kb(role))
                rows = self.db.get_participant_mini_course_registrations(pid)
                if not rows:
                    return self.bot.send_message(uid, "Участник не записан ни на один мини-курс.", keyboard=self.main_kb(role))
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": f"Выберите мини-курс для удаления {p['last_name']} {p['first_name']}:",
                    "keyboard": self.admin_remove_mc_kb(rows, pid).get_keyboard()
                })

            elif action == "admin_unreg_mc_do":
                if role not in ("admin", "super_admin"):
                    return
                pid = payload["pid"]
                mc_id = payload["mc_id"]
                p = self.db.get_participant_by_id(pid)
                if not p:
                    return self.bot.send_message(uid, "Участник не найден.", keyboard=self.main_kb(role))
                mc = self.db.get_mini_course(mc_id)
                mc_name = mc['name'] if mc else "?"
                ok, msg = self.db.admin_unregister_participant_from_mini_course(mc_id, p['user_id'])
                if ok:
                    try:
                        self.bot.send_message(
                            p['user_id'],
                            f"⚠️ Вы удалены с мини-курса «{mc_name}» администратором."
                        )
                    except Exception:
                        pass
                    self.bot.send_message(uid, f"✅ {msg}", keyboard=self.main_kb(role))
                else:
                    self.bot.send_message(uid, f"❌ {msg}", keyboard=self.main_kb(role))

            # === Закрытие регистрации ===
            elif action == "confirm_close_event":
                if role not in ("admin", "super_admin"):
                    return
                events = self.db.get_published_events()
                if not events:
                    return self.bot.send_message(uid, "Нет опубликованных мероприятий.", keyboard=self.main_kb(role))
                total_distributed = 0
                all_assignments = []
                all_registered_participants = []
                for ev in events:
                    result = self.db.close_event_registration_and_distribute(ev['id'])
                    if result:
                        total_distributed += result.get('distributed_count', 0)
                        assigned = result.get('new_assignments', [])
                        for a in assigned:
                            all_assignments.append((ev['name'], a))
                    # Собираем ВСЕХ зарегистрированных участников
                    ev_regs = self.db.get_event_teams_list(ev['id'])
                    for reg in ev_regs:
                        all_registered_participants.append((ev['name'], ev['id'], reg))
                msg_parts = ["✅ Регистрация на мероприятия закрыта."]

                # Уведомляем каждого зарегистрированного участника
                notified = set()
                for ev_name, ev_id, reg in all_registered_participants:
                    p_info = self.db.get_participant_by_id(reg['participant_id'])
                    if p_info and p_info['user_id']:
                        key = (ev_id, p_info['user_id'])
                        if key not in notified:
                            notified.add(key)
                            team_info = self.db.get_team_info_for_event(ev_id, p_info['user_id'])
                            if team_info:
                                try:
                                    self.bot.send_message(
                                        p_info['user_id'],
                                        f"📅 Мероприятие «{ev_name}» завершено.\n"
                                        f"👥 Ваша команда: {team_info['captain_name']}\n"
                                        f"👤 Участников: {team_info['team_size']}"
                                    )
                                except Exception:
                                    pass
                            else:
                                try:
                                    self.bot.send_message(
                                        p_info['user_id'],
                                        f"📅 Мероприятие «{ev_name}» завершено. Регистрация закрыта."
                                    )
                                except Exception:
                                    pass

                if all_assignments:
                    parts = []
                    for ev_name, a in all_assignments:
                        name = f"{a.get('first_name', '')} {a.get('last_name', '')}".strip()
                        team = a.get('captain_name', '')
                        parts.append(f"   {name} → команда «{team}»")
                    msg_parts.append(f"Распределено участников: {len(all_assignments)}")
                    msg_parts.append("\n📋 Отчёт по распределению:")
                    msg_parts.extend(parts)
                else:
                    msg_parts.append("Все участники уже были в командах.")
                self.bot.send_message(uid, "\n".join(msg_parts), keyboard=self.main_kb(role))

            elif action == "confirm_close_mini_courses":
                if role not in ("admin", "super_admin"):
                    return
                result = self.db.close_mini_courses_registration_and_distribute()
                if not result:
                    return self.bot.send_message(uid, "Нет опубликованных мини-курсов.", keyboard=self.main_kb(role))
                msg_parts = ["✅ Регистрация на мини-курсы закрыта."]
                assignments = result.get('new_assignments', [])

                # Уведомляем ВСЕХ зарегистрированных участников
                all_courses = result.get('courses', [])
                for course in all_courses:
                    regs = self.db.get_mini_course_full_registrations(course['id'])
                    for reg in regs:
                        p_info = self.db.get_participant_by_id(reg['participant_id'])
                        if p_info and p_info['user_id']:
                            try:
                                self.bot.send_message(
                                    p_info['user_id'],
                                    f"📚 Мини-курс «{course['name']}» завершён.\n"
                                    f"⏰ Ваше время: {reg.get('slot_time', '')}\n"
                                    f"Регистрация закрыта."
                                )
                            except Exception:
                                pass

                if assignments:
                    parts = []
                    for a in assignments:
                        name = f"{a.get('first_name', '')} {a.get('last_name', '')}".strip()
                        regs = a.get('registrations', [])
                        for r in regs:
                            parts.append(f"   {name} → {r.get('course_name', '')} ({r.get('slot_time', '')})")
                    msg_parts.append(f"Распределено участников: {len(assignments)}")
                    msg_parts.append("\n📋 Отчёт по распределению:")
                    msg_parts.extend(parts)
                else:
                    msg_parts.append("Все участники уже были записаны.")
                self.bot.send_message(uid, "\n".join(msg_parts), keyboard=self.main_kb(role))

            elif action == "show_admin_events":
                if role not in ("admin", "super_admin"):
                    return
                kb = Keyboard(one_time=False, inline=False)
                kb.add_button("Создать", color="primary", payload=json.dumps({"cmd": "create_event"}))
                kb.add_button("Опубликовать", payload=json.dumps({"cmd": "publish_events"}))
                kb.add_line()
                if self.db.has_open_registrations():
                    kb.add_button("Закрыть регистрацию", color="negative", payload=json.dumps({"cmd": "close_event_reg"}))
                    kb.add_line()
                kb.add_button("Список", payload=json.dumps({"cmd": "view_events"}))
                kb.add_button("Неопубликованные", payload=json.dumps({"cmd": "view_unpublished_events"}))
                kb.add_line()
                kb.add_button("Команды", payload=json.dumps({"cmd": "view_event_teams"}))
                kb.add_button("Создать команду", color="primary", payload=json.dumps({"cmd": "admin_create_team"}))
                kb.add_line()
                kb.add_button("Удалить", color="negative", payload=json.dumps({"cmd": "delete_event"}))
                kb.add_button("Последнее закрытое", payload=json.dumps({"cmd": "last_closed_event"}))
                kb.add_line()
                kb.add_button("Записать участника", color="primary", payload=json.dumps({"cmd": "admin_reg_event"}))
                kb.add_button("Удалить с мероприятия", color="negative", payload=json.dumps({"cmd": "admin_remove_from_event"}))
                kb.add_line()
                kb.add_button("Назад", color="secondary", payload=json.dumps({"cmd": "back"}))
                self.bot.send_message(uid, "Меню мероприятий:", keyboard=kb)

            elif action == "show_admin_mini_courses":
                if role not in ("admin", "super_admin"):
                    return
                kb = Keyboard(one_time=False, inline=False)
                kb.add_button("Создать", color="primary", payload=json.dumps({"cmd": "create_mini_course"}))
                kb.add_button("Опубликовать", payload=json.dumps({"cmd": "publish_mini_courses"}))
                kb.add_line()
                if self.db.has_open_registrations():
                    kb.add_button("Закрыть регистрацию", color="negative", payload=json.dumps({"cmd": "close_mini_course_reg"}))
                    kb.add_line()
                kb.add_button("Неопубликованные", payload=json.dumps({"cmd": "view_unpublished"}))
                kb.add_button("Опубликованные", payload=json.dumps({"cmd": "view_published"}))
                kb.add_line()
                kb.add_button("Удалить", color="negative", payload=json.dumps({"cmd": "delete_mini_course"}))
                kb.add_button("Последние закрытые", payload=json.dumps({"cmd": "last_closed_mini_courses"}))
                kb.add_line()
                kb.add_button("Записать участника", color="primary", payload=json.dumps({"cmd": "admin_reg_mc"}))
                kb.add_button("Удалить с мини-курса", color="negative", payload=json.dumps({"cmd": "admin_remove_from_mc"}))
                kb.add_line()
                kb.add_button("Назад", color="secondary", payload=json.dumps({"cmd": "back"}))
                self.bot.send_message(uid, "Меню мини-курсов:", keyboard=kb)

            # === Ярмарка: просмотр товара ===
            elif action == "fair_view_item":
                item = self.db.get_fair_item(payload["item_id"])
                if not item:
                    return self.bot.send_message(uid, "Товар не найден.", keyboard=self.main_kb(role))
                kb = Keyboard(inline=True)
                kb.add_callback_button(
                    "Изменить товар", color="primary",
                    payload=json.dumps({"action": "fair_edit_item_form", "item_id": item['id']})
                )
                kb.add_line()
                kb.add_callback_button(
                    "Изменить цену", color="primary",
                    payload=json.dumps({"action": "fair_change_price_form", "item_id": item['id']})
                )
                kb.add_line()
                kb.add_callback_button(
                    "Удалить товар", color="negative",
                    payload=json.dumps({"action": "fair_delete_item", "item_id": item['id']})
                )
                desc = f"\n{item['description']}" if item.get('description') else ""
                self.bot.send_message(uid, f"📦 {item['name']}\n💰 {item['price']} монет{desc}", keyboard=kb)

            elif action == "fair_edit_item_form":
                if self.db.is_fair_paused():
                    return self.bot.send_message(uid, "Ярмарка приостановлена.", keyboard=self.main_kb(role))
                item = self.db.get_fair_item(payload["item_id"])
                if not item:
                    return self.bot.send_message(uid, "Товар не найден.")
                my_team = self.db.get_fair_team_by_user_id(uid)
                if not my_team or my_team['id'] != item['team_id']:
                    return self.bot.send_message(uid, "Этот товар не принадлежит вашей команде.", keyboard=self._fair_participant_kb())
                self._set_state(uid, "WAIT_FAIR_EDIT_ITEM_NAME", {"item_id": payload["item_id"]})
                self.bot.send_message(uid, "Введите новое название товара:", keyboard=self.wait_kb())

            elif action == "fair_change_price_form":
                if self.db.is_fair_paused():
                    return self.bot.send_message(uid, "Ярмарка приостановлена.", keyboard=self.main_kb(role))
                item = self.db.get_fair_item(payload["item_id"])
                if not item:
                    return self.bot.send_message(uid, "Товар не найден.")
                my_team = self.db.get_fair_team_by_user_id(uid)
                if not my_team or my_team['id'] != item['team_id']:
                    return self.bot.send_message(uid, "Этот товар не принадлежит вашей команде.", keyboard=self._fair_participant_kb())
                self._set_state(uid, "WAIT_FAIR_CHANGE_PRICE", {"item_id": payload["item_id"]})
                self.bot.send_message(uid, "Введите новую цену (целое число):", keyboard=self.wait_kb())

            elif action == "fair_delete_item":
                if self.db.is_fair_paused():
                    return self.bot.send_message(uid, "Ярмарка приостановлена.", keyboard=self.main_kb(role))
                item = self.db.get_fair_item(payload["item_id"])
                if not item:
                    return self.bot.send_message(uid, "Товар не найден.")
                my_team = self.db.get_fair_team_by_user_id(uid)
                if not my_team or my_team['id'] != item['team_id']:
                    return self.bot.send_message(uid, "Этот товар не принадлежит вашей команде.", keyboard=self._fair_participant_kb())
                self.db.deactivate_fair_item(payload["item_id"])
                self.bot.send_message(uid, "🗑 Товар удалён.", keyboard=self._fair_participant_kb())

            # === Ярмарка: покупка ===
            elif action == "fair_select_team_buy":
                if self.db.is_fair_paused():
                    return self.bot.send_message(uid, "Ярмарка приостановлена.", keyboard=self.main_kb(role))
                team_id = payload["team_id"]
                items = self.db.get_team_items(team_id)
                if not items:
                    return self.bot.send_message(uid, "У этой команды нет активных товаров.", keyboard=self._fair_participant_kb())
                seller_team = self.db.get_fair_team(team_id)
                kb = Keyboard(inline=True)
                for i, item in enumerate(items):
                    kb.add_callback_button(
                        f"{item['name']} - {item['price']} монет",
                        color="primary",
                        payload=json.dumps({"action": "fair_select_item_buy", "item_id": item['id'], "seller_team_id": team_id})
                    )
                    if i < len(items) - 1:
                        kb.add_line()
                self.bot.send_message(uid, f"Товары команды {seller_team['team_name']}:", keyboard=kb)

            elif action == "fair_select_item_buy":
                if self.db.is_fair_paused():
                    return self.bot.send_message(uid, "Ярмарка приостановлена.", keyboard=self._fair_participant_kb())
                item_id = payload["item_id"]
                seller_team_id = payload["seller_team_id"]
                item = self.db.get_fair_item(item_id)
                seller_team = self.db.get_fair_team(seller_team_id)
                if not item or not seller_team:
                    return self.bot.send_message(uid, "Товар или команда не найдены.", keyboard=self._fair_participant_kb())
                my_team = self.db.get_fair_team_by_user_id(uid)
                if not my_team:
                    return self.bot.send_message(uid, "Вы не состоите в команде.", keyboard=self._fair_participant_kb())
                # Check cooldown
                cooldown = self.db.get_fair_cooldown()
                if cooldown > 0:
                    import datetime
                    last_tx = self.db.get_team_transactions(my_team['id'])
                    if last_tx and last_tx[0]['status'] in ('pending', 'approved'):
                        last_time = datetime.datetime.fromisoformat(last_tx[0]['created_at'].replace('T', ' '))
                        if (datetime.datetime.now() - last_time).total_seconds() < cooldown:
                            remain = int(cooldown - (datetime.datetime.now() - last_time).total_seconds())
                            return self.bot.send_message(uid, f"⏳ Подождите {remain} сек. перед следующей покупкой.", keyboard=self._fair_participant_kb())
                kb = Keyboard(inline=True)
                kb.add_callback_button(
                    "✅ Подтвердить покупку", color="positive",
                    payload=json.dumps({
                        "action": "fair_confirm_purchase",
                        "item_id": item_id,
                        "seller_team_id": seller_team_id,
                        "seller_team_name": seller_team['team_name'],
                        "item_name": item['name'],
                        "price": item['price'],
                        "my_team_id": my_team['id']
                    })
                )
                kb.add_line()
                kb.add_callback_button(
                    "❌ Отмена", color="negative",
                    payload=json.dumps({"action": "fair_cancel_purchase"})
                )
                desc = f"\n{item.get('description', '')}" if item.get('description') else ""
                self.bot.send_message(uid,
                    f"Подтверждение покупки:\n\n📦 {item['name']}{desc}\n💰 {item['price']} монет\n🏪 Продавец: {seller_team['team_name']}\n💰 Ваш баланс: {my_team['budget']} монет",
                    keyboard=kb)

            elif action == "fair_confirm_purchase":
                if self.db.is_fair_paused():
                    return self.bot.send_message(uid, "Ярмарка приостановлена.", keyboard=self.main_kb(role))
                item_id = payload["item_id"]
                seller_team_id = payload["seller_team_id"]
                price = payload["price"]
                my_team_id = payload["my_team_id"]
                buyer_team = self.db.get_fair_team(my_team_id)
                seller_team = self.db.get_fair_team(seller_team_id)
                if not buyer_team or not seller_team:
                    return self.bot.send_message(uid, "Ошибка: команда не найдена.", keyboard=self._fair_participant_kb())
                if buyer_team['budget'] < price:
                    return self.bot.send_message(uid, "❌ Ошибка списания средств: недостаточно монет на счету команды.", keyboard=self._fair_participant_kb())
                # Check admin availability BEFORE creating transaction
                event_id = self.db.get_active_fair_event_id()
                admin_uid = self.db.get_next_fair_admin(event_id) if event_id else None
                if not admin_uid:
                    return self.bot.send_message(uid, "❌ Нет доступных администраторов для обработки заявки.", keyboard=self._fair_participant_kb())
                # Create transaction
                p = self.db.get_participant_by_user_id(uid)
                seller_members = self.db.get_fair_team_members(seller_team_id)
                seller_uid = seller_members[0]['user_id'] if seller_members else 0
                tx_id = self.db.create_fair_transaction(
                    item_id, my_team_id, seller_team_id, price,
                    uid, seller_uid,
                    f"Покупка {payload.get('item_name', 'товара')} у {payload.get('seller_team_name', 'продавца')}"
                )
                self.db.assign_transaction_to_admin(tx_id, admin_uid)
                # Send notification to admin
                kb = Keyboard(inline=True)
                kb.add_callback_button(
                    "✅ Одобрить", color="positive",
                    payload=json.dumps({"action": "fair_approve_tx", "transaction_id": tx_id})
                )
                kb.add_callback_button(
                    "❌ Отклонить", color="negative",
                    payload=json.dumps({"action": "fair_reject_tx", "transaction_id": tx_id})
                )
                self.bot.send_message(admin_uid,
                    f"📋 Новая заявка на покупку!\n\n"
                    f"📦 {payload.get('item_name', 'Товар')}\n"
                    f"💰 Сумма: {price} монет\n"
                    f"🏪 Покупатель: {buyer_team['team_name']}\n"
                    f"🏪 Продавец: {payload.get('seller_team_name', 'Продавец')}\n"
                    f"👤 Покупатель: {p['last_name']} {p['first_name']}",
                    keyboard=kb)
                self.bot.send_message(uid, "✅ Заявка на покупку отправлена администратору. Ожидайте подтверждения.", keyboard=self._fair_participant_kb())

            elif action == "fair_cancel_purchase":
                self.bot.send_message(uid, "Покупка отменена.", keyboard=self._fair_participant_kb())

            # === Ярмарка: одобрение/отклонение транзакции админом ===
            elif action == "fair_approve_tx":
                if role not in ("admin", "super_admin"):
                    return
                tx_id = payload["transaction_id"]
                ok = self.db.approve_fair_transaction(tx_id)
                tx = self.db.get_fair_transaction(tx_id)
                if not tx:
                    return
                msg_text = f"✅ Транзакция #{tx_id} одобрена."
                empty_kb = Keyboard(inline=True)
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": msg_text,
                    "keyboard": empty_kb.get_keyboard()
                })
                if ok:
                    buyer_team = self.db.get_fair_team(tx['buyer_team_id'])
                    seller_team = self.db.get_fair_team(tx['seller_team_id'])
                    # Notify buyer team
                    buyer_members = self.db.get_fair_team_members(tx['buyer_team_id'])
                    for m in buyer_members:
                        mb = self.db.get_participant_by_user_id(m['user_id'])
                        r = mb.get('role', 'participant') if mb else 'participant'
                        self.bot.send_message(m['user_id'],
                            f"✅ Покупка одобрена! Списано: {tx['amount']} монет.\nПродавец: {seller_team['team_name'] if seller_team else '?'}",
                            keyboard=self.main_kb(r))
                    # Notify seller team
                    seller_members = self.db.get_fair_team_members(tx['seller_team_id'])
                    for m in seller_members:
                        mb = self.db.get_participant_by_user_id(m['user_id'])
                        r = mb.get('role', 'participant') if mb else 'participant'
                        self.bot.send_message(m['user_id'],
                            f"✅ Продажа подтверждена! Получено: {tx['amount']} монет.\nПокупатель: {buyer_team['team_name'] if buyer_team else '?'}",
                            keyboard=self.main_kb(r))
                else:
                    self.bot.send_message(uid, "❌ Не удалось одобрить транзакцию (недостаточно средств или уже обработана).")

            elif action == "fair_reject_tx":
                if role not in ("admin", "super_admin"):
                    return
                tx_id = payload["transaction_id"]
                self.db.reject_fair_transaction(tx_id)
                tx = self.db.get_fair_transaction(tx_id)
                empty_kb = Keyboard(inline=True)
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": f"❌ Транзакция #{tx_id} отклонена.",
                    "keyboard": empty_kb.get_keyboard()
                })
                if tx:
                    buyer_members = self.db.get_fair_team_members(tx['buyer_team_id'])
                    for m in buyer_members:
                        mb = self.db.get_participant_by_user_id(m['user_id'])
                        r = mb.get('role', 'participant') if mb else 'participant'
                        self.bot.send_message(m['user_id'],
                            "❌ Покупка отклонена администратором.",
                            keyboard=self.main_kb(r))

            # === Ярмарка: одобрение/отклонение заявок на изменения ===
            elif action == "fair_approve_change":
                if role not in ("admin", "super_admin"):
                    return
                req_id = payload["request_id"]
                req = self.db.approve_fair_change_request(req_id)
                if not req:
                    return self.bot.send_message(uid, "❌ Заявка уже обработана.", keyboard=self.main_kb(role))
                new_data = json.loads(req['new_data'])
                team = self.db.get_fair_team(req['team_id'])
                team_name = team['team_name'] if team else '?'
                ok = True
                result_msg = f"✅ Заявка #{req_id} одобрена."
                if req['request_type'] == 'add_item':
                    p = self.db.get_participant_by_user_id(req['user_id'])
                    pid = p['id'] if p else 0
                    self.db.add_fair_item(pid, req['team_id'], new_data['name'], new_data['price'], new_data.get('description', ''))
                    result_msg = f"✅ Товар «{new_data['name']}» добавлен (заявка #{req_id})."
                elif req['request_type'] == 'edit_item':
                    self.db.update_fair_item(req['item_id'], new_data['name'], new_data['price'], new_data.get('description', ''))
                    result_msg = f"✅ Товар «{new_data['name']}» обновлён (заявка #{req_id})."
                elif req['request_type'] == 'change_price':
                    self.db.update_fair_item_price(req['item_id'], new_data['price'])
                    result_msg = f"✅ Цена изменена на {new_data['price']} (заявка #{req_id})."
                elif req['request_type'] == 'edit_team':
                    self.db.update_fair_team_name(req['team_id'], new_data['name'])
                    result_msg = f"✅ Название команды изменено на «{new_data['name']}» (заявка #{req_id})."
                empty_kb = Keyboard(inline=True)
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": result_msg,
                    "keyboard": empty_kb.get_keyboard()
                })
                # Notify the requester
                if req['user_id']:
                    mb = self.db.get_participant_by_user_id(req['user_id'])
                    r = mb.get('role', 'participant') if mb else 'participant'
                    self.bot.send_message(req['user_id'],
                        f"✅ Ваша заявка одобрена!\n{result_msg}",
                        keyboard=self.main_kb(r) if r in ('admin', 'super_admin') else self._fair_participant_kb())

            elif action == "fair_reject_change":
                if role not in ("admin", "super_admin"):
                    return
                req_id = payload["request_id"]
                req = self.db.get_fair_change_request(req_id)
                if not req or req['status'] != 'pending':
                    return self.bot.send_message(uid, "❌ Заявка уже обработана.", keyboard=self.main_kb(role))
                self.db.reject_fair_change_request(req_id)
                empty_kb = Keyboard(inline=True)
                self.bot.send_api_method("messages.edit", {
                    "peer_id": peer_id,
                    "conversation_message_id": cmid,
                    "message": f"❌ Заявка #{req_id} отклонена.",
                    "keyboard": empty_kb.get_keyboard()
                })
                if req['user_id']:
                    mb = self.db.get_participant_by_user_id(req['user_id'])
                    r = mb.get('role', 'participant') if mb else 'participant'
                    self.bot.send_message(req['user_id'],
                        "❌ Ваша заявка отклонена администратором.",
                        keyboard=self.main_kb(r) if r in ('admin', 'super_admin') else self._fair_participant_kb())

            # === Ярмарка: штраф ===
            elif action == "fair_select_fine_team":
                if role not in ("admin", "super_admin"):
                    return
                self._set_state(uid, "WAIT_FAIR_FINE_AMOUNT", {"team_id": payload["team_id"]})
                self.bot.send_message(uid, "Введите сумму штрафа (целое число):", keyboard=self.wait_kb())

            # === Ярмарка: просмотр команды админом ===
            elif action == "fair_show_team_detail":
                if role not in ("admin", "super_admin"):
                    return
                team = self.db.get_fair_team(payload["team_id"])
                if not team:
                    return self.bot.send_message(uid, "Команда не найдена.", keyboard=self.main_kb(role))
                items = self.db.get_team_items(team['id'])
                txs = self.db.get_team_transactions(team['id'])
                lines = [f"🏪 Команда: {team['team_name']}"]
                lines.append(f"💰 Баланс: {team['budget']} монет")
                if items:
                    lines.append(f"\n📦 Товары ({len(items)}):")
                    for it in items:
                        lines.append(f"  • {it['name']} — {it['price']} монет")
                else:
                    lines.append("\n📦 Нет товаров")
                if txs:
                    lines.append(f"\n📜 История операций ({len(txs)}):")
                    for tx in txs[:10]:
                        se = "✅" if tx['status'] == 'approved' else ("❌" if tx['status'] == 'rejected' else "⏳")
                        if tx['seller_team_id'] == team['id']:
                            lines.append(f"  {se} +{tx['amount']} от {tx.get('buyer_team_name', '?')} ({tx.get('item_name', '?')})")
                        else:
                            lines.append(f"  {se} -{tx['amount']} к {tx.get('seller_team_name', '?')} ({tx.get('item_name', '?')})")
                else:
                    lines.append("\n📜 Нет операций")
                self.bot.send_message(uid, "\n".join(lines), keyboard=self._fair_admin_kb())

        except Exception as e:
            logger.error("Callback error: %s", e, exc_info=True)
            try:
                self.bot.send_message(uid, "⚠️ Произошла ошибка. Попробуйте снова.")
            except Exception:
                pass

    # === Маппинг текста в команды ===
    @staticmethod
    def _text_to_cmd(t: str) -> Optional[str]:
        aliases = {
            "войти": "login", "баланс": "balance", "мероприятия": "events",
            "настройки": "settings", "назад": "back", "отмена": "cancel",
            "мини-курсы": "mini_courses", "жалоба": "complaint",
            "красная кнопка": "complaint", "участники": "participants",
            "моя команда": "fair_my_team", "покупка": "fair_buy",
            "команды": "fair_teams", "товары": "fair_items",
            "список товаров": "fair_item_list", "добавить товар": "fair_add_item",
            "редактировать анкету": "fair_edit_team",
            "история операций": "fair_history",
            "уйти на обед": "fair_lunch",
            "выдать штраф команде": "fair_fine",
            "остановить ярмарку": "fair_pause",
            "восстановить ярмарку": "fair_pause",
            "завершить ярмарку": "fair_end",
            "установить кулдаун": "fair_cooldown",
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
            lines = [f"💰 Ваш баланс: {balance} HSE-коинов"]
            if history:
                lines.append("\n📜 История:")
                for item in history:
                    sign = "+" if item['amount'] > 0 else ""
                    lines.append(f"  {sign}{item['amount']}: {item['description']} ({item['created_at']})")
            self.bot.send_message(uid, "\n".join(lines), keyboard=self.main_kb(role))

        elif cmd == "register_event":
            if not p:
                return self.bot.send_message(uid, "Сначала войдите.", keyboard=self.main_kb(role))
            evts = self.db.get_published_events()
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

            self.bot.send_message(uid, "📚 Доступные мини-курсы:")
            for mc in mcs:
                slots_info = []
                for s in mc.get('slots', []):
                    slot_max = s['max_participants'] if s['max_participants'] > 0 else mc['max_participants']
                    free = slot_max - s['registered']
                    slots_info.append(f"{s['time']} (ост. {free}/{slot_max})")
                slots_str = ", ".join(slots_info)
                date_str = f" ({mc['date']})" if mc.get('date') else ""

                lines = [f"🔹 {mc['name']}{date_str}"]
                lines.append(f"📝 {mc['description']}")
                lines.append(f"👥 Всего записано: {mc['total_registered']}")
                if slots_str:
                    lines.append(f"⏰ {slots_str}")

                already = False
                for s in mc.get('slots', []):
                    if self.db.get_my_mini_course_team(mc['id'], s['id'], uid):
                        already = True
                        break

                kb = Keyboard(inline=True)
                if already:
                    kb.add_callback_button(
                        "✅ Вы уже записаны", color="secondary",
                        payload=json.dumps({"action": "show_my_team", "mini_course_id": mc['id']})
                    )
                    kb.add_line()
                    kb.add_callback_button(
                        "Отменить запись", color="negative",
                        payload=json.dumps({"action": "leave_team", "target": "mini_course", "target_id": mc['id']})
                    )
                else:
                    kb.add_callback_button(
                        "Записаться на этот мини-курс", color="primary",
                        payload=json.dumps({"action": "select_mini_course", "mini_course_id": mc['id']})
                    )

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
                kb.add_button("Опубликовать", payload=json.dumps({"cmd": "publish_events"}))
                kb.add_line()
                if self.db.has_open_registrations():
                    kb.add_button("Закрыть регистрацию", color="negative", payload=json.dumps({"cmd": "close_event_reg"}))
                    kb.add_line()
                kb.add_button("Список", payload=json.dumps({"cmd": "view_events"}))
                kb.add_button("Неопубликованные", payload=json.dumps({"cmd": "view_unpublished_events"}))
                kb.add_line()
                kb.add_button("Команды", payload=json.dumps({"cmd": "view_event_teams"}))
                kb.add_button("Создать команду", color="primary", payload=json.dumps({"cmd": "admin_create_team"}))
                kb.add_line()
                kb.add_button("Удалить", color="negative", payload=json.dumps({"cmd": "delete_event"}))
                kb.add_button("Последнее закрытое", payload=json.dumps({"cmd": "last_closed_event"}))
                kb.add_line()
                kb.add_button("Записать участника", color="primary", payload=json.dumps({"cmd": "admin_reg_event"}))
                kb.add_button("Удалить с мероприятия", color="negative", payload=json.dumps({"cmd": "admin_remove_from_event"}))
                kb.add_line()
                kb.add_button("Назад", color="secondary", payload=json.dumps({"cmd": "back"}))
                self.bot.send_message(uid, "Меню мероприятий:", keyboard=kb)

            elif cmd == "mini_courses":
                kb = Keyboard(one_time=False, inline=False)
                kb.add_button("Создать", color="primary", payload=json.dumps({"cmd": "create_mini_course"}))
                kb.add_button("Опубликовать", payload=json.dumps({"cmd": "publish_mini_courses"}))
                kb.add_line()
                if self.db.has_open_registrations():
                    kb.add_button("Закрыть регистрацию", color="negative", payload=json.dumps({"cmd": "close_mini_course_reg"}))
                    kb.add_line()
                kb.add_button("Неопубликованные", payload=json.dumps({"cmd": "view_unpublished"}))
                kb.add_button("Опубликованные", payload=json.dumps({"cmd": "view_published"}))
                kb.add_line()
                kb.add_button("Удалить", color="negative", payload=json.dumps({"cmd": "delete_mini_course"}))
                kb.add_button("Последние закрытые", payload=json.dumps({"cmd": "last_closed_mini_courses"}))
                kb.add_line()
                kb.add_button("Записать участника", color="primary", payload=json.dumps({"cmd": "admin_reg_mc"}))
                kb.add_button("Удалить с мини-курса", color="negative", payload=json.dumps({"cmd": "admin_remove_from_mc"}))
                kb.add_line()
                kb.add_button("Назад", color="secondary", payload=json.dumps({"cmd": "back"}))
                self.bot.send_message(uid, "Меню мини-курсов:", keyboard=kb)

            elif cmd == "close_event_reg":
                kb = Keyboard(inline=True)
                kb.add_callback_button("Да, закрыть", color="negative", payload=json.dumps({"action": "confirm_close_event"}))
                kb.add_callback_button("Нет", color="secondary", payload=json.dumps({"action": "show_admin_events"}))
                self.bot.send_message(uid, "Вы уверены, что хотите закрыть регистрацию на все опубликованные мероприятия?\nНезаписанные участники будут автоматически распределены по командам.", keyboard=kb)

            elif cmd == "close_mini_course_reg":
                kb = Keyboard(inline=True)
                kb.add_callback_button("Да, закрыть", color="negative", payload=json.dumps({"action": "confirm_close_mini_courses"}))
                kb.add_callback_button("Нет", color="secondary", payload=json.dumps({"action": "show_admin_mini_courses"}))
                self.bot.send_message(uid, "Вы уверены, что хотите закрыть регистрацию на все опубликованные мини-курсы?\nНезаписанные участники будут автоматически распределены.", keyboard=kb)

            elif cmd == "last_closed_event":
                ev = self.db.get_last_closed_event()
                if not ev:
                    return self.bot.send_message(uid, "Нет закрытых мероприятий.", keyboard=self.main_kb(role))
                participants = self.db.get_all_participants_with_registrations(ev['id'])
                lines = [f"📅 {ev['name']}\n"]
                if not participants:
                    lines.append("Нет зарегистрированных участников.")
                for p in participants:
                    team = p.get('team_name') or '—'
                    role_tag = "👑" if p.get('is_captain') else "👤"
                    lines.append(f"{role_tag} {p['first_name']} {p['last_name']} — {team}")
                lines.append(f"\nВсего участников: {len(participants)}")
                self.bot.send_message(uid, "\n".join(lines), keyboard=self.main_kb(role))

            elif cmd.startswith("last_closed_mini_courses"):
                courses = self.db.get_last_closed_mini_courses()
                if not courses:
                    return self.bot.send_message(uid, "Нет закрытых мини-курсов.", keyboard=self.main_kb(role))
                mc_ids = [c['id'] for c in courses]
                participants = self.db.get_all_participants_with_mini_course_regs(mc_ids)
                reg_by_mc = {}
                for p in participants:
                    mc_id = p['mini_course_id']
                    reg_by_mc.setdefault(mc_id, []).append(p)
                lines = []
                for c in courses:
                    lines.append(f"📚 {c['name']} ({c.get('date', '')[:10] if c.get('date') else ''})")
                    regs = reg_by_mc.get(c['id'], [])
                    if not regs:
                        lines.append("   Нет записей")
                    for r in regs:
                        lines.append(f"   👤 {r['first_name']} {r['last_name']} — {r.get('slot_time', '')}")
                    lines.append("")
                self.bot.send_message(uid, "\n".join(lines), keyboard=self.main_kb(role))

            elif cmd == "admin_reg_event":
                participants = self.db.get_all_participants()
                if not participants:
                    return self.bot.send_message(uid, "Нет участников.", keyboard=self.main_kb(role))
                self.bot.send_message(
                    uid,
                    "Выберите участника для регистрации на мероприятие:",
                    keyboard=self.admin_participant_kb(participants, 0, "admin_reg_event_pick", "admin_reg_event")
                )

            elif cmd == "admin_remove_from_event":
                participants = self.db.get_all_participants()
                if not participants:
                    return self.bot.send_message(uid, "Нет участников.", keyboard=self.main_kb(role))
                self.bot.send_message(
                    uid,
                    "Выберите участника для удаления с мероприятия:",
                    keyboard=self.admin_participant_kb(participants, 0, "admin_unreg_event_pick", "admin_remove_from_event")
                )

            elif cmd == "admin_reg_mc":
                participants = self.db.get_all_participants()
                if not participants:
                    return self.bot.send_message(uid, "Нет участников.", keyboard=self.main_kb(role))
                self.bot.send_message(
                    uid,
                    "Выберите участника для записи на мини-курс:",
                    keyboard=self.admin_participant_kb(participants, 0, "admin_reg_mc_pick_part", "admin_reg_mc")
                )

            elif cmd == "admin_remove_from_mc":
                participants = self.db.get_all_participants()
                if not participants:
                    return self.bot.send_message(uid, "Нет участников.", keyboard=self.main_kb(role))
                self.bot.send_message(
                    uid,
                    "Выберите участника для удаления с мини-курса:",
                    keyboard=self.admin_participant_kb(participants, 0, "admin_unreg_mc_pick", "admin_remove_from_mc")
                )

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
                kb.add_button("Снять HSE-коины со счета", color="negative", payload=json.dumps({"cmd": "deduct_balance"}))
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
                ctx = {"participants": participants, "selected": [], "page": 0, "mode": "topup"}
                self._set_state(uid, "WAIT_BALANCE_SELECT", ctx)
                self.bot.send_message(uid, self.balance_selection_text(ctx), keyboard=self.balance_selection_kb(ctx))

            elif cmd == "deduct_balance":
                participants = self.db.get_all_participants()
                if not participants:
                    return self.bot.send_message(uid, "Нет участников для списания HSE-коинов.", keyboard=self.main_kb(role))
                ctx = {"participants": participants, "selected": [], "page": 0, "mode": "deduct"}
                self._set_state(uid, "WAIT_BALANCE_SELECT", ctx)
                self.bot.send_message(uid, self.balance_selection_text(ctx), keyboard=self.balance_selection_kb(ctx))

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

            elif cmd == "admin_create_team":
                events = self.db.get_active_events()
                if not events:
                    return self.bot.send_message(uid, "Нет активных мероприятий.", keyboard=self.main_kb(role))
                self._set_state(uid, "ADMIN_CREATE_TEAM", {"events": events, "page": 0})
                self.bot.send_message(uid, "Выберите мероприятие для создания команды:", keyboard=self._admin_create_team_event_kb(events, 0))

            elif cmd == "publish_events":
                evts = self.db.publish_events()
                published_count = len(evts)
                self.bot.send_message(uid, f"✅ Опубликовано: {published_count} мероприятий.\nРегистрацию закроет администратор вручную.", keyboard=self.main_kb(role))
                if published_count > 0:
                    participant_ids = self.db.get_all_participant_user_ids()
                    participant_ids = [pid for pid in participant_ids if pid != uid]
                    for pid in participant_ids:
                        self.bot.send_message(
                            pid,
                            f"📅 Доступны новые мероприятия ({published_count} шт.)!\nЗарегистрироваться можно до закрытия регистрации администратором.\nНажмите «Мероприятия» в меню, чтобы записаться.",
                            keyboard=self.main_kb('participant')
                        )

            elif cmd == "view_unpublished_events":
                evts = self.db.get_unpublished_events()
                if not evts:
                    return self.bot.send_message(uid, "Нет неопубликованных мероприятий.", keyboard=self.main_kb(role))
                lines = ["📅 Неопубликованные мероприятия:"]
                for e in evts:
                    lines.append(f"\n🔸 {e['name']}")
                    lines.append(f"   📝 {e['description']}")
                    lines.append(f"   👥 Команда: от {e['min_team_size']} до {e['max_team_size']}")
                self.bot.send_message(uid, "\n".join(lines), keyboard=self.main_kb(role))

            elif cmd == "delete_event":
                evts = self.db.get_unpublished_events()
                if not evts:
                    return self.bot.send_message(uid, "Нет неопубликованных мероприятий для удаления.", keyboard=self.main_kb(role))
                kb = Keyboard(inline=True)
                for i, e in enumerate(evts):
                    kb.add_callback_button(
                        e['name'], color="negative",
                        payload=json.dumps({"action": "confirm_delete_event", "event_id": e['id']})
                    )
                    if i < len(evts) - 1:
                        kb.add_line()
                self.bot.send_message(uid, "Выберите мероприятие для удаления:", keyboard=kb)

            elif cmd == "delete_mini_course":
                mcs = self.db.get_unpublished_mini_courses()
                if not mcs:
                    return self.bot.send_message(uid, "Нет неопубликованных курсов для удаления.", keyboard=self.main_kb(role))
                kb = Keyboard(inline=True)
                for i, mc in enumerate(mcs):
                    kb.add_callback_button(
                        mc['name'], color="negative",
                        payload=json.dumps({"action": "confirm_delete_mini_course", "mc_id": mc['id']})
                    )
                    if i < len(mcs) - 1:
                        kb.add_line()
                self.bot.send_message(uid, "Выберите мини-курс для удаления:", keyboard=kb)

            elif cmd == "create_mini_course":
                self._set_state(uid, "WAIT_MC_NAME")
                self.bot.send_message(uid, "Введите название мини-курса:", keyboard=self.wait_kb())

            elif cmd == "publish_mini_courses":
                courses = self.db.publish_mini_courses()
                published_count = len(courses)
                self.bot.send_message(uid, f"✅ Опубликовано: {published_count} курсов.\nРегистрацию закроет администратор вручную.", keyboard=self.main_kb(role))

                # Уведомить всех участников о новых мини-курсах
                if published_count > 0:
                    participant_ids = self.db.get_all_participant_user_ids()
                    participant_ids = [pid for pid in participant_ids if pid != uid]
                    for pid in participant_ids:
                        self.bot.send_message(
                            pid,
                            f"📚 Доступны новые мини-курсы ({published_count} шт.)!\nЗарегистрироваться можно до закрытия регистрации администратором.\nНажмите «Мини-курсы» в меню, чтобы записаться.",
                            keyboard=self.main_kb('participant')
                        )

            elif cmd == "view_unpublished":
                mcs = self.db.get_unpublished_mini_courses() if hasattr(self.db, 'get_unpublished_mini_courses') else []
                if not mcs:
                    return self.bot.send_message(uid, "Нет неопубликованных курсов.", keyboard=self.main_kb(role))
                lines = ["📚 Неопубликованные:"]
                for mc in mcs:
                    slots = self.db.get_time_slots(mc['id'])
                    slot_str = ", ".join([s['time'] for s in slots])
                    date_str = f" ({mc['date']})" if mc.get('date') else ""
                    lines.append(f"\n🔸 {mc['name']}{date_str}")
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
                    date_str = f" ({mc['date']})" if mc.get('date') else ""
                    lines.append(f"\n🔹 {mc['name']}{date_str}")
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
                parts = self.db.get_all_participants()
                if not parts:
                    return self.bot.send_message(uid, "Список пуст.", keyboard=self.main_kb(role))
                lines = ["👥 Участники:"]
                for pr in parts:
                    lines.append(f"  {pr['last_name']} {pr['first_name']} — 💰 {pr['balance']} HSE-коинов")
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
                    date_str = f" ({mc['date']})" if mc.get('date') else ""
                    lines.append(f"\n🔹 {mc['name']}{date_str} ({len(regs)}/{mc['max_participants']}):")
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

                    fair_events = evts

                    kb = Keyboard(inline=True)
                    for i, e in enumerate(fair_events):
                        kb.add_callback_button(
                            e['name'], color="primary",
                            payload=json.dumps({"action": "start_fair", "event_id": e['id']})
                        )
                        if i < len(fair_events) - 1:
                            kb.add_line()
                    self.bot.send_message(uid, "Выберите мероприятие для запуска ярмарки:", keyboard=kb)

            # === Команды ярмарки ===
            elif cmd == "go_fair":
                if not self.db.is_fair_active() or self.db.is_fair_completed():
                    return self.bot.send_message(uid, "Ярмарка не активна или завершена.", keyboard=self.main_kb(role))
                event_id = self.db.get_active_fair_event_id()
                if not event_id:
                    return self.bot.send_message(uid, "Ошибка: ярмарка не настроена.", keyboard=self.main_kb(role))

                if role in ("admin", "super_admin"):
                    self.db.register_fair_admin(event_id, uid)
                    self.bot.send_message(uid, "🏪 Добро пожаловать на ярмарку! Вы зарегистрированы как администратор.", keyboard=self._fair_admin_kb())
                else:
                    team = self.db.get_fair_team_by_user(uid, event_id)
                    if not team:
                        return self.bot.send_message(uid, "Вы не состоите ни в одной команде на ярмарке.", keyboard=self.main_kb(role))
                    self.bot.send_message(uid, f"🏪 Добро пожаловать на ярмарку! Ваша команда: {team['team_name']}", keyboard=self._fair_participant_kb())

            elif cmd == "fair_main":
                event_id = self.db.get_active_fair_event_id()
                if role in ("admin", "super_admin"):
                    self.bot.send_message(uid, "🏪 Панель ярмарки:", keyboard=self._fair_admin_kb())
                else:
                    self.bot.send_message(uid, "🏪 Панель ярмарки:", keyboard=self._fair_participant_kb())

            elif cmd == "fair_my_team":
                team = self.db.get_fair_team_by_user_id(uid)
                if not team:
                    return self.bot.send_message(uid, "Команда не найдена.", keyboard=self.main_kb(role))
                members = self.db.get_fair_team_members(team['id'])
                member_lines = [f"👤 {m['last_name']} {m['first_name']}" for m in members]
                msg = f"Моя команда {team['team_name']}:\n" + "\n".join(member_lines)
                self.bot.send_message(uid, msg, keyboard=self._fair_team_panel_kb())

            elif cmd == "fair_edit_team":
                if self.db.is_fair_paused():
                    return self.bot.send_message(uid, "Ярмарка приостановлена. Изменение названия недоступно.", keyboard=self.main_kb(role))
                self._set_state(uid, "WAIT_FAIR_TEAM_NAME")
                self.bot.send_message(uid, "Введите новое название команды:", keyboard=self.wait_kb())

            elif cmd == "fair_items":
                if self.db.is_fair_paused():
                    return self.bot.send_message(uid, "Ярмарка приостановлена. Управление товарами недоступно.", keyboard=self.main_kb(role))
                kb = Keyboard(one_time=False, inline=False)
                kb.add_button("Список товаров", color="primary", payload=json.dumps({"cmd": "fair_item_list"}))
                kb.add_line()
                kb.add_button("Добавить товар", color="primary", payload=json.dumps({"cmd": "fair_add_item"}))
                kb.add_line()
                kb.add_button("Назад", color="negative", payload=json.dumps({"cmd": "fair_my_team"}))
                self.bot.send_message(uid, "Управление товарами:", keyboard=kb)

            elif cmd == "fair_item_list":
                team = self.db.get_fair_team_by_user_id(uid)
                if not team:
                    return self.bot.send_message(uid, "Команда не найдена.", keyboard=self.main_kb(role))
                items = self.db.get_team_items(team['id'])
                if not items:
                    return self.bot.send_message(uid, "У вашей команды нет товаров.", keyboard=self._fair_team_panel_kb())
                kb = Keyboard(inline=True)
                for i, item in enumerate(items):
                    kb.add_callback_button(
                        f"{item['name']} - {item['price']} монет",
                        color="primary",
                        payload=json.dumps({"action": "fair_view_item", "item_id": item['id']})
                    )
                    if i < len(items) - 1:
                        kb.add_line()
                self.bot.send_message(uid, "Ваши товары:", keyboard=kb)

            elif cmd == "fair_add_item":
                if self.db.is_fair_paused():
                    return self.bot.send_message(uid, "Ярмарка приостановлена. Добавление товаров недоступно.", keyboard=self.main_kb(role))
                team = self.db.get_fair_team_by_user_id(uid)
                if not team:
                    return self.bot.send_message(uid, "Команда не найдена.", keyboard=self.main_kb(role))
                self._set_state(uid, "WAIT_FAIR_ITEM_NAME", {"team_id": team['id']})
                self.bot.send_message(uid, "Введите название товара:", keyboard=self.wait_kb())

            elif cmd == "fair_balance":
                team = self.db.get_fair_team_by_user_id(uid)
                if not team:
                    return self.bot.send_message(uid, "Команда не найдена.", keyboard=self.main_kb(role))
                balance = self.db.get_team_balance(team['id'])
                self.bot.send_message(uid, f"💰 Баланс команды {team['team_name']}: {balance} монет.", keyboard=self._fair_team_panel_kb())

            elif cmd == "fair_history":
                team = self.db.get_fair_team_by_user_id(uid)
                if not team:
                    return self.bot.send_message(uid, "Команда не найдена.", keyboard=self.main_kb(role))
                txs = self.db.get_team_transactions(team['id'])
                if not txs:
                    self.bot.send_message(uid, "📜 История операций пуста.", keyboard=self._fair_team_panel_kb())
                else:
                    lines = ["📜 История операций:"]
                    for tx in txs:
                        status_emoji = {"approved": "✅", "rejected": "❌", "pending": "⏳"}
                        se = status_emoji.get(tx['status'], "❓")
                        if tx['seller_team_id'] == team['id']:
                            lines.append(f"{se} +{tx['amount']} от {tx['buyer_team_name']} ({tx['item_name']})")
                        else:
                            lines.append(f"{se} -{tx['amount']} к {tx['seller_team_name']} ({tx['item_name']})")
                        if tx['description']:
                            lines.append(f"   Причина: {tx['description']}")
                    self.bot.send_message(uid, "\n".join(lines), keyboard=self._fair_team_panel_kb())

            elif cmd == "fair_buy":
                if self.db.is_fair_paused():
                    return self.bot.send_message(uid, "Ярмарка приостановлена. Покупки недоступны.", keyboard=self.main_kb(role))
                event_id = self.db.get_active_fair_event_id()
                if not event_id:
                    return self.bot.send_message(uid, "Ярмарка не активна.", keyboard=self.main_kb(role))
                my_team = self.db.get_fair_team_by_user_id(uid)
                if not my_team:
                    return self.bot.send_message(uid, "Вы не состоите в команде.", keyboard=self.main_kb(role))
                teams = self.db.get_all_fair_teams(event_id)
                other_teams = [t for t in teams if t['id'] != my_team['id']]
                if not other_teams:
                    return self.bot.send_message(uid, "Нет других команд для покупки.", keyboard=self._fair_participant_kb())
                kb = Keyboard(inline=True)
                for i, t in enumerate(other_teams):
                    kb.add_callback_button(
                        t['team_name'], color="primary",
                        payload=json.dumps({"action": "fair_select_team_buy", "team_id": t['id']})
                    )
                    if i < len(other_teams) - 1:
                        kb.add_line()
                self.bot.send_message(uid, "Выберите команду, у которой хотите купить товар:", keyboard=kb)

            # === Админские команды ярмарки ===
            elif cmd == "fair_teams":
                if role not in ("admin", "super_admin"):
                    return self.bot.send_message(uid, "Доступно только админу.", keyboard=self.main_kb(role))
                event_id = self.db.get_active_fair_event_id()
                if not event_id:
                    return self.bot.send_message(uid, "Ярмарка не активна.", keyboard=self.main_kb(role))
                teams = self.db.get_all_fair_teams(event_id)
                if not teams:
                    return self.bot.send_message(uid, "Нет команд на ярмарке.", keyboard=self._fair_admin_kb())
                kb = Keyboard(inline=True)
                for i, t in enumerate(teams):
                    kb.add_callback_button(
                        t['team_name'], color="primary",
                        payload=json.dumps({"action": "fair_show_team_detail", "team_id": t['id']})
                    )
                    if i < len(teams) - 1:
                        kb.add_line()
                self.bot.send_message(uid, "Команды на ярмарке:", keyboard=kb)

            elif cmd == "fair_settings":
                if role not in ("admin", "super_admin"):
                    return self.bot.send_message(uid, "Доступно только админу.", keyboard=self.main_kb(role))
                self.bot.send_message(uid, "Настройки ярмарки:", keyboard=self._fair_admin_settings_kb(role))

            elif cmd == "fair_lunch":
                if role not in ("admin", "super_admin"):
                    return self.bot.send_message(uid, "Доступно только админу.", keyboard=self.main_kb(role))
                event_id = self.db.get_active_fair_event_id()
                if not event_id:
                    return self.bot.send_message(uid, "Ярмарка не активна.", keyboard=self.main_kb(role))
                is_registered = self.db.is_fair_admin_registered(event_id, uid)
                if not is_registered:
                    return self.bot.send_message(uid, "Вы не зарегистрированы как администратор ярмарки.", keyboard=self._fair_admin_kb())
                admin_info = None
                for a in self.db.get_fair_admins(event_id):
                    if a['admin_user_id'] == uid:
                        admin_info = a
                        break
                on_break = admin_info['is_on_break'] if admin_info else False
                self.db.set_fair_admin_break(event_id, uid, not on_break)
                status = "Вы ушли на обед. Заявки больше не приходят." if not on_break else "Вы вернулись с обеда. Заявки снова приходят."
                self.bot.send_message(uid, f"🍽 {status}", keyboard=self._fair_admin_settings_kb(role))

            elif cmd == "fair_fine":
                if role not in ("admin", "super_admin"):
                    return self.bot.send_message(uid, "Доступно только админу.", keyboard=self.main_kb(role))
                event_id = self.db.get_active_fair_event_id()
                if not event_id:
                    return self.bot.send_message(uid, "Ярмарка не активна.", keyboard=self.main_kb(role))
                teams = self.db.get_all_fair_teams(event_id)
                if not teams:
                    return self.bot.send_message(uid, "Нет команд.", keyboard=self._fair_admin_kb())
                kb = Keyboard(inline=True)
                for i, t in enumerate(teams):
                    kb.add_callback_button(
                        t['team_name'], color="negative",
                        payload=json.dumps({"action": "fair_select_fine_team", "team_id": t['id']})
                    )
                    if i < len(teams) - 1:
                        kb.add_line()
                self.bot.send_message(uid, "Выберите команду для штрафа:", keyboard=kb)

            elif cmd == "fair_pause":
                if role not in ("admin", "super_admin"):
                    return self.bot.send_message(uid, "Доступно только админу.", keyboard=self.main_kb(role))
                paused = self.db.is_fair_paused()
                self.db.set_fair_paused(not paused)
                status = "приостановлена" if not paused else "возобновлена"
                self.bot.send_message(uid, f"🏪 Ярмарка {status}.", keyboard=self._fair_admin_settings_kb(role))

            elif cmd == "fair_end":
                if role != "super_admin":
                    return self.bot.send_message(uid, "Доступно только супер-админу.", keyboard=self.main_kb(role))
                self._set_state(uid, "WAIT_FAIR_END_CONFIRM")
                self.bot.send_message(uid, "❗ Вы уверены, что хотите завершить ярмарку? Это действие необратимо.\nВведите ДА для подтверждения:", keyboard=self.wait_kb())

            elif cmd == "fair_cooldown":
                if role != "super_admin":
                    return self.bot.send_message(uid, "Доступно только супер-админу.", keyboard=self.main_kb(role))
                self._set_state(uid, "WAIT_FAIR_COOLDOWN")
                self.bot.send_message(uid, "Введите кулдаун между покупками (в секундах):", keyboard=self.wait_kb())

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

            # Уведомить всех админов о новой жалобе
            admin_ids = self.db.get_all_admin_user_ids()
            for aid in admin_ids:
                self.bot.send_message(
                    aid,
                    f"🚨 Новая жалоба от {p['first_name']} {p['last_name']}:\n{text}\n\nНажмите «Жалобы» в меню, чтобы просмотреть.",
                    keyboard=self.main_kb('admin')
                )

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
            name = text.strip()
            if not name:
                return self.bot.send_message(uid, "❌ Название не может быть пустым. Введите название:", keyboard=self.wait_kb())
            ctx["name"] = name
            self._set_state(uid, "WAIT_EV_DESC", ctx)
            self.bot.send_message(uid, "Введите описание мероприятия:", keyboard=self.wait_kb())

        elif state == "WAIT_EV_DESC":
            desc = text.strip()
            if not desc:
                return self.bot.send_message(uid, "❌ Описание не может быть пустым. Введите описание:", keyboard=self.wait_kb())
            ctx["desc"] = desc
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
                self.bot.send_message(uid, f"✅ Мероприятие создано (ID: {eid}). Опубликуйте через меню.", keyboard=self.main_kb(role))
            except ValueError:
                self.bot.send_message(uid, "❌ Максимум должен быть >= минимума. Повторите:", keyboard=self.wait_kb())

        # === FSM создания мини-курса ===
        elif state == "WAIT_MC_NAME":
            name = text.strip()
            if not name:
                return self.bot.send_message(uid, "❌ Название не может быть пустым. Введите название:", keyboard=self.wait_kb())
            ctx["name"] = name
            self._set_state(uid, "WAIT_MC_DESC", ctx)
            self.bot.send_message(uid, "Введите описание мини-курса:", keyboard=self.wait_kb())

        elif state == "WAIT_MC_DESC":
            desc = text.strip()
            if not desc:
                return self.bot.send_message(uid, "❌ Описание не может быть пустым. Введите описание:", keyboard=self.wait_kb())
            ctx["desc"] = desc
            self._set_state(uid, "WAIT_MC_DATE", ctx)
            self.bot.send_message(uid, "Введите дату мини-курса (например: 15.07.2024):", keyboard=self.wait_kb())

        elif state == "WAIT_MC_DATE":
            import re
            date_text = text.strip()
            if not re.match(r'^\d{2}\.\d{2}\.\d{4}$', date_text):
                return self.bot.send_message(uid, "❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ (пример: 15.07.2024):", keyboard=self.wait_kb())
            try:
                import datetime
                datetime.datetime.strptime(date_text, "%d.%m.%Y")
            except ValueError:
                return self.bot.send_message(uid, "❌ Некорректная дата. Повторите ввод:", keyboard=self.wait_kb())
            ctx["date"] = date_text
            self._set_state(uid, "WAIT_MC_SLOTS", ctx)
            self.bot.send_message(uid, "Введите временные слоты и количество мест через запятую:\n11:00/10, 13:00/15, 15:00/20", keyboard=self.wait_kb())

        elif state == "WAIT_MC_SLOTS":
            import re
            slots_raw = [s.strip() for s in text.split(",") if s.strip()]
            slots_data = []
            errors = []
            for s in slots_raw:
                if '/' in s:
                    time_part, max_part = s.split('/', 1)
                    time_part = time_part.strip()
                    max_part = max_part.strip()
                    if not re.match(r'^\d{2}:\d{2}$', time_part):
                        errors.append(f"❌ Неверный формат времени: '{s}' (используйте ЧЧ:ММ/количество)")
                        continue
                    try:
                        max_val = int(max_part)
                        if max_val <= 0:
                            errors.append(f"❌ Количество мест должно быть положительным: '{s}'")
                            continue
                    except ValueError:
                        errors.append(f"❌ Неверное количество мест: '{s}'")
                        continue
                    slots_data.append((time_part, max_val))
                else:
                    errors.append(f"❌ Неверный формат слота: '{s}' (используйте ЧЧ:ММ/количество)")
            if errors:
                return self.bot.send_message(uid, "\n".join(errors) + "\n\nПовторите ввод:", keyboard=self.wait_kb())
            if not slots_data:
                return self.bot.send_message(uid, "❌ Нет корректных слотов. Повторите ввод:", keyboard=self.wait_kb())
            mc_id = self.db.create_mini_course(ctx["name"], ctx["desc"], sum(m for _, m in slots_data), ctx.get("date", ""))
            for time_slot, max_p in slots_data:
                self.db.add_time_slot(mc_id, time_slot, max_p)
            self._clear_state(uid)
            slot_strs = [f"{t} ({m} мест)" if m > 0 else t for t, m in slots_data]
            self.bot.send_message(
                uid,
                f"✅ Мини-курс создан (ID: {mc_id})\nДата: {ctx.get('date', '')}\nСлоты: {', '.join(slot_strs)}\n\nОпубликуйте через меню.",
                keyboard=self.main_kb(role)
            )

        # === FSM пополнения / списания баланса ===
        elif state == "WAIT_BALANCE_AMOUNT":
            try:
                amount = int(text.strip())
                if amount <= 0:
                    raise ValueError
                selected = ctx["selected"]
                mode = ctx.get("mode", "topup")
                results = []
                for pid in selected:
                    participant = self.db.get_participant_by_id(pid)
                    if not participant:
                        continue
                    if mode == "topup":
                        self.db.add_balance_to_participant(pid, amount, f"Пополнение от админа")
                        results.append(f"✅ {participant['first_name']} {participant['last_name']}: +{amount}")
                    else:
                        ok = self.db.deduct_balance_from_participant(pid, amount, f"Списание админом")
                        if ok:
                            results.append(f"✅ {participant['first_name']} {participant['last_name']}: -{amount}")
                        else:
                            results.append(f"❌ {participant['first_name']} {participant['last_name']}: Не удалось списать средства")
                self._clear_state(uid)
                action_text = "пополнен" if mode == "topup" else "списан"
                self.bot.send_message(uid, f"{'✅' if mode == 'topup' else ''} Баланс {action_text}:\n" + "\n".join(results), keyboard=self.main_kb(role))
            except ValueError:
                self.bot.send_message(uid, "❌ Введите положительное целое число.", keyboard=self.wait_kb())

        # === FSM запуска ярмарки ===
        elif state == "WAIT_FAIR_BUDGET":
            try:
                budget = int(text.strip())
                if budget <= 0:
                    raise ValueError
                self.db.start_fair(ctx["event_id"], budget)
                count = self.db.create_fair_teams_for_event(ctx["event_id"], budget)
                self._clear_state(uid)
                self.bot.send_message(uid, f"🏪 Ярмарка запущена! Начальный бюджет: {budget} монет. Создано команд: {count}.", keyboard=self.main_kb(role))
                # Notify all participants
                participant_ids = self.db.get_all_participant_user_ids()
                for pid in participant_ids:
                    if pid == uid:
                        continue
                    part = self.db.get_participant_by_user_id(pid)
                    r = part.get('role', 'participant') if part else 'participant'
                    self.bot.send_message(pid, "🏪 Ярмарка открыта! Нажмите «Перейти к ярмарке» в меню.", keyboard=self.main_kb(r))
            except ValueError:
                self.bot.send_message(uid, "❌ Введите положительное целое число.", keyboard=self.wait_kb())

        # === FSM ярмарки: название команды ===
        elif state == "WAIT_FAIR_TEAM_NAME":
            new_name = text.strip()
            if len(new_name) < 1:
                return self.bot.send_message(uid, "Название не может быть пустым.", keyboard=self.wait_kb())
            team = self.db.get_fair_team_by_user_id(uid)
            if not team:
                self._clear_state(uid)
                return self.bot.send_message(uid, "Команда не найдена.", keyboard=self.main_kb(role))
            event_id = self.db.get_active_fair_event_id()
            if not event_id:
                self._clear_state(uid)
                return self.bot.send_message(uid, "Ярмарка не активна.", keyboard=self.main_kb(role))
            import json
            new_data = json.dumps({"name": new_name})
            old_data = json.dumps({"name": team['team_name']})
            req_id = self.db.create_fair_change_request(event_id, team['id'], 'edit_team', new_data, uid, 0, old_data)
            admin_uid = self.db.get_next_fair_admin(event_id)
            if admin_uid:
                self.db.assign_change_request_to_admin(req_id, admin_uid)
                kb = Keyboard(inline=True)
                kb.add_callback_button(
                    "✅ Одобрить", color="positive",
                    payload=json.dumps({"action": "fair_approve_change", "request_id": req_id})
                )
                kb.add_callback_button(
                    "❌ Отклонить", color="negative",
                    payload=json.dumps({"action": "fair_reject_change", "request_id": req_id})
                )
                self.bot.send_message(admin_uid,
                    f"📋 Новая заявка: Изменение названия команды\n\n"
                    f"Команда: {team['team_name']} → {new_name}",
                    keyboard=kb)
            self._clear_state(uid)
            self.bot.send_message(uid, "✅ Заявка на изменение названия отправлена администратору. Ожидайте подтверждения.", keyboard=self._fair_team_panel_kb())

        # === FSM ярмарки: добавление товара ===
        elif state == "WAIT_FAIR_ITEM_NAME":
            name = text.strip()
            if len(name) < 1:
                return self.bot.send_message(uid, "Название не может быть пустым.", keyboard=self.wait_kb())
            ctx["item_name"] = name
            self._set_state(uid, "WAIT_FAIR_ITEM_DESC", ctx)
            self.bot.send_message(uid, "Введите описание товара (или отправьте «-», чтобы пропустить):", keyboard=self.wait_kb())

        elif state == "WAIT_FAIR_ITEM_DESC":
            desc = "" if text.strip() == "-" else text.strip()
            ctx["item_desc"] = desc
            self._set_state(uid, "WAIT_FAIR_ITEM_PRICE", ctx)
            self.bot.send_message(uid, "Введите цену товара (целое число):", keyboard=self.wait_kb())

        elif state == "WAIT_FAIR_ITEM_PRICE":
            try:
                price = int(text.strip())
                if price <= 0:
                    raise ValueError
                team_id = ctx["team_id"]
                name = ctx["item_name"]
                desc = ctx.get("item_desc", "")
                team = self.db.get_fair_team(team_id)
                if not team:
                    self._clear_state(uid)
                    return self.bot.send_message(uid, "Команда не найдена.", keyboard=self.main_kb(role))
                event_id = self.db.get_active_fair_event_id()
                if not event_id:
                    self._clear_state(uid)
                    return self.bot.send_message(uid, "Ярмарка не активна.", keyboard=self.main_kb(role))
                import json
                new_data = json.dumps({"name": name, "price": price, "description": desc})
                req_id = self.db.create_fair_change_request(event_id, team_id, 'add_item', new_data, uid, 0, "")
                admin_uid = self.db.get_next_fair_admin(event_id)
                if admin_uid:
                    self.db.assign_change_request_to_admin(req_id, admin_uid)
                    kb = Keyboard(inline=True)
                    kb.add_callback_button(
                        "✅ Одобрить", color="positive",
                        payload=json.dumps({"action": "fair_approve_change", "request_id": req_id})
                    )
                    kb.add_callback_button(
                        "❌ Отклонить", color="negative",
                        payload=json.dumps({"action": "fair_reject_change", "request_id": req_id})
                    )
                    desc_line = f"\n📝 {desc}" if desc else ""
                    self.bot.send_message(admin_uid,
                        f"📋 Новая заявка: Добавление товара\n\n"
                        f"Команда: {team['team_name']}\n"
                        f"Товар: {name}\n"
                        f"💰 Цена: {price} монет{desc_line}",
                        keyboard=kb)
                self._clear_state(uid)
                self.bot.send_message(uid, "✅ Заявка на добавление товара отправлена администратору. Ожидайте подтверждения.", keyboard=self._fair_participant_kb())
            except ValueError:
                self.bot.send_message(uid, "❌ Введите положительное целое число.", keyboard=self.wait_kb())

        # === FSM ярмарки: редактирование товара ===
        elif state == "WAIT_FAIR_EDIT_ITEM_NAME":
            name = text.strip()
            if len(name) < 1:
                return self.bot.send_message(uid, "Название не может быть пустым.", keyboard=self.wait_kb())
            ctx["item_name"] = name
            self._set_state(uid, "WAIT_FAIR_EDIT_ITEM_DESC", ctx)
            self.bot.send_message(uid, "Введите новое описание товара (или «-», чтобы оставить без изменений):", keyboard=self.wait_kb())

        elif state == "WAIT_FAIR_EDIT_ITEM_DESC":
            desc = "" if text.strip() == "-" else text.strip()
            ctx["item_desc"] = desc
            self._set_state(uid, "WAIT_FAIR_EDIT_ITEM_PRICE", ctx)
            self.bot.send_message(uid, "Введите новую цену товара (целое число):", keyboard=self.wait_kb())

        elif state == "WAIT_FAIR_EDIT_ITEM_PRICE":
            try:
                price = int(text.strip())
                if price <= 0:
                    raise ValueError
                item_id = ctx.get("item_id")
                if not item_id:
                    self._clear_state(uid)
                    return self.bot.send_message(uid, "Ошибка: товар не найден.", keyboard=self.main_kb(role))
                item = self.db.get_fair_item(item_id)
                if not item:
                    self._clear_state(uid)
                    return self.bot.send_message(uid, "Товар не найден.", keyboard=self.main_kb(role))
                team = self.db.get_fair_team(item['team_id'])
                if not team:
                    self._clear_state(uid)
                    return self.bot.send_message(uid, "Команда не найдена.", keyboard=self.main_kb(role))
                event_id = self.db.get_active_fair_event_id()
                if not event_id:
                    self._clear_state(uid)
                    return self.bot.send_message(uid, "Ярмарка не активна.", keyboard=self.main_kb(role))
                import json
                new_data = json.dumps({"name": ctx["item_name"], "price": price, "description": ctx.get("item_desc", "")})
                old_data = json.dumps({"name": item['name'], "price": item['price'], "description": item.get('description', '')})
                req_id = self.db.create_fair_change_request(event_id, team['id'], 'edit_item', new_data, uid, item_id, old_data)
                admin_uid = self.db.get_next_fair_admin(event_id)
                if admin_uid:
                    self.db.assign_change_request_to_admin(req_id, admin_uid)
                    kb = Keyboard(inline=True)
                    kb.add_callback_button(
                        "✅ Одобрить", color="positive",
                        payload=json.dumps({"action": "fair_approve_change", "request_id": req_id})
                    )
                    kb.add_callback_button(
                        "❌ Отклонить", color="negative",
                        payload=json.dumps({"action": "fair_reject_change", "request_id": req_id})
                    )
                    self.bot.send_message(admin_uid,
                        f"📋 Новая заявка: Изменение товара\n\n"
                        f"Команда: {team['team_name']}\n"
                        f"Товар: {item['name']} → {ctx['item_name']}\n"
                        f"💰 Цена: {item['price']} → {price} монет",
                        keyboard=kb)
                self._clear_state(uid)
                self.bot.send_message(uid, "✅ Заявка на изменение товара отправлена администратору. Ожидайте подтверждения.", keyboard=self._fair_participant_kb())
            except ValueError:
                self.bot.send_message(uid, "❌ Введите положительное целое число.", keyboard=self.wait_kb())

        # === FSM ярмарки: изменение цены ===
        elif state == "WAIT_FAIR_CHANGE_PRICE":
            try:
                price = int(text.strip())
                if price <= 0:
                    raise ValueError
                item_id = ctx.get("item_id")
                item = self.db.get_fair_item(item_id)
                if not item:
                    self._clear_state(uid)
                    return self.bot.send_message(uid, "Товар не найден.", keyboard=self.main_kb(role))
                team = self.db.get_fair_team(item['team_id'])
                if not team:
                    self._clear_state(uid)
                    return self.bot.send_message(uid, "Команда не найдена.", keyboard=self.main_kb(role))
                event_id = self.db.get_active_fair_event_id()
                if not event_id:
                    self._clear_state(uid)
                    return self.bot.send_message(uid, "Ярмарка не активна.", keyboard=self.main_kb(role))
                import json
                new_data = json.dumps({"price": price})
                old_data = json.dumps({"price": item['price']})
                req_id = self.db.create_fair_change_request(event_id, team['id'], 'change_price', new_data, uid, item_id, old_data)
                admin_uid = self.db.get_next_fair_admin(event_id)
                if admin_uid:
                    self.db.assign_change_request_to_admin(req_id, admin_uid)
                    kb = Keyboard(inline=True)
                    kb.add_callback_button(
                        "✅ Одобрить", color="positive",
                        payload=json.dumps({"action": "fair_approve_change", "request_id": req_id})
                    )
                    kb.add_callback_button(
                        "❌ Отклонить", color="negative",
                        payload=json.dumps({"action": "fair_reject_change", "request_id": req_id})
                    )
                    self.bot.send_message(admin_uid,
                        f"📋 Новая заявка: Изменение цены\n\n"
                        f"Команда: {team['team_name']}\n"
                        f"Товар: {item['name']}\n"
                        f"💰 {item['price']} → {price} монет",
                        keyboard=kb)
                self._clear_state(uid)
                self.bot.send_message(uid, "✅ Заявка на изменение цены отправлена администратору. Ожидайте подтверждения.", keyboard=self._fair_participant_kb())
            except ValueError:
                self.bot.send_message(uid, "❌ Введите положительное целое число.", keyboard=self.wait_kb())

        # === FSM ярмарки: штраф ===
        elif state == "WAIT_FAIR_FINE_AMOUNT":
            try:
                amount = int(text.strip())
                if amount <= 0:
                    raise ValueError
                ctx["fine_amount"] = amount
                self._set_state(uid, "WAIT_FAIR_FINE_REASON", ctx)
                self.bot.send_message(uid, "Введите причину штрафа:", keyboard=self.wait_kb())
            except ValueError:
                self.bot.send_message(uid, "❌ Введите положительное целое число.", keyboard=self.wait_kb())

        elif state == "WAIT_FAIR_FINE_REASON":
            reason = text.strip()
            if len(reason) < 1:
                return self.bot.send_message(uid, "Причина не может быть пустой.", keyboard=self.wait_kb())
            team_id = ctx.get("team_id")
            amount = ctx.get("fine_amount", 0)
            team = self.db.get_fair_team(team_id)
            if not team:
                self._clear_state(uid)
                return self.bot.send_message(uid, "Команда не найдена.", keyboard=self.main_kb(role))
            self.db.fine_team_budget(team_id, amount)
            # Record in transactions
            event_id = self.db.get_active_fair_event_id()
            p = self.db.get_participant_by_user_id(uid)
            buyer_uid = p['id'] if p else 0
            tx_id = self.db.create_fair_transaction(
                None, team_id, team_id, amount,
                buyer_uid, buyer_uid,
                f"Штраф: {reason}"
            )
            # Mark fine as approved immediately (no admin approval needed)
            with self.db._get_conn() as conn:
                conn.execute("UPDATE fair_transactions SET status='approved' WHERE id=?", (tx_id,))
            self._clear_state(uid)
            # Notify team members
            members = self.db.get_fair_team_members(team_id)
            for m in members:
                mb = self.db.get_participant_by_user_id(m['user_id'])
                r = mb.get('role', 'participant') if mb else 'participant'
                self.bot.send_message(m['user_id'],
                    f"⚠️ Штраф! Команда {team['team_name']} оштрафована на {amount} монет.\nПричина: {reason}",
                    keyboard=self.main_kb(r))
            self.bot.send_message(uid, f"✅ Штраф {amount} монет выписан команде {team['team_name']}. Причина: {reason}", keyboard=self._fair_admin_kb())

        # === FSM ярмарки: кулдаун ===
        elif state == "WAIT_FAIR_COOLDOWN":
            try:
                seconds = int(text.strip())
                if seconds < 0:
                    raise ValueError
                self.db.set_fair_cooldown(seconds)
                self._clear_state(uid)
                self.bot.send_message(uid, f"✅ Кулдаун установлен: {seconds} сек.", keyboard=self._fair_admin_settings_kb(role))
            except ValueError:
                self.bot.send_message(uid, "❌ Введите неотрицательное целое число.", keyboard=self.wait_kb())

        # === FSM ярмарки: подтверждение завершения ===
        elif state == "WAIT_FAIR_END_CONFIRM":
            if text.strip().upper() == "ДА":
                event_id = self.db.get_active_fair_event_id()
                if not event_id:
                    self._clear_state(uid)
                    return self.bot.send_message(uid, "Ошибка: ярмарка не активна.", keyboard=self.main_kb(role))
                stats = self.db.get_fair_statistics(event_id)
                lines = ["📊 СТАТИСТИКА ЯРМАРКИ", "=" * 20]
                for t in stats['teams']:
                    lines.append(f"\n🏪 {t['team_name']}")
                    lines.append(f"💰 Баланс: {t['budget']} монет")
                    lines.append(f"📈 Заработано: {t['income']} монет")
                    lines.append(f"📉 Потрачено: {t['expenses']} монет")
                    lines.append(f"📦 Продано товаров: {t['items_sold']}")
                    lines.append(f"🛒 Куплено товаров: {t['items_bought']}")
                    if t['items']:
                        lines.append("  Товары в наличии:")
                        for it in t['items']:
                            lines.append(f"    • {it['name']} — {it['price']} монет")
                lines.append("\n\n🏆 ТОП ПРОДАВЦОВ:")
                for i, t in enumerate(stats['top_earners'], 1):
                    lines.append(f"{i}. {t['team_name']} — заработано {t['income']} монет")
                lines.append("\n🏆 ТОП ПО БЮДЖЕТУ:")
                for i, t in enumerate(stats['top_richest'], 1):
                    lines.append(f"{i}. {t['team_name']} — {t['budget']} монет")
                lines.append("\n\n📜 ВСЕ ТРАНЗАКЦИИ:")
                for tx in stats['transactions']:
                    se = "✅" if tx['status'] == 'approved' else ("❌" if tx['status'] == 'rejected' else "⏳")
                    lines.append(f"{se} {tx.get('buyer_team_name', '?')} → {tx.get('seller_team_name', '?')}: {tx['amount']} ({tx.get('item_name', '?')}) [{tx['status']}]")
                self.db.set_fair_completed()
                self._clear_state(uid)
                self.bot.send_message(uid, "\n".join(lines), keyboard=self.main_kb(role))
                # Notify all users
                all_ids = self.db.get_all_participant_user_ids() + self.db.get_all_admin_user_ids()
                for pid in set(all_ids):
                    if pid == uid:
                        continue
                    part = self.db.get_participant_by_user_id(pid)
                    r = part.get('role', 'participant') if part else 'participant'
                    self.bot.send_message(pid, "🏪 Ярмарка завершена! Спасибо за участие!", keyboard=self.main_kb(r))
            else:
                self._clear_state(uid)
                self.bot.send_message(uid, "Завершение ярмарки отменено.", keyboard=self._fair_admin_kb())

        else:
            self._clear_state(uid)
            self.bot.send_message(uid, "Сессия сброшена.", keyboard=self.main_kb(role))