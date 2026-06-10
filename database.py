# database.py
import logging, random, sqlite3, string
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error("DB Error: %s", e, exc_info=True)
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER UNIQUE,
                    first_name TEXT, last_name TEXT, personal_code TEXT UNIQUE,
                    role TEXT DEFAULT 'participant', balance INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS pre_registered_participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, first_name TEXT, last_name TEXT,
                    personal_code TEXT UNIQUE, is_used BOOLEAN DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS admin_invites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invite_code TEXT UNIQUE,
                    role TEXT DEFAULT 'admin',
                    is_used BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, description TEXT,
                    min_team_size INTEGER, max_team_size INTEGER, is_active BOOLEAN DEFAULT 1,
                    is_fair BOOLEAN DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS event_registrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER, participant_id INTEGER,
                    team_members TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(event_id, participant_id)
                );

                CREATE TABLE IF NOT EXISTS mini_courses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, description TEXT,
                    max_participants INTEGER, is_published BOOLEAN DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS time_slots (id INTEGER PRIMARY KEY AUTOINCREMENT, mini_course_id INTEGER, time TEXT);

                CREATE TABLE IF NOT EXISTS mini_course_registrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, mini_course_id INTEGER,
                    participant_id INTEGER, time_slot_id INTEGER, UNIQUE(mini_course_id, participant_id)
                );

                CREATE TABLE IF NOT EXISTS balance_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, participant_id INTEGER, amount INTEGER, description TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS complaints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, participant_id INTEGER, message TEXT, is_resolved BOOLEAN DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS fair_settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS fair_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES participants(user_id),
                    team_name TEXT NOT NULL,
                    name TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS fair_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER,
                    buyer_id INTEGER,
                    seller_user_id INTEGER,
                    amount INTEGER,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

            """)
            # Миграция: добавить created_at в event_registrations, если колонки нет
            cols = [c[1] for c in conn.execute("PRAGMA table_info(event_registrations)").fetchall()]
            if 'created_at' not in cols:
                conn.execute("ALTER TABLE event_registrations ADD COLUMN created_at TIMESTAMP")
                conn.execute("UPDATE event_registrations SET created_at = datetime('now') WHERE created_at IS NULL")
            cols = [c[1] for c in conn.execute("PRAGMA table_info(mini_course_registrations)").fetchall()]
            if 'created_at' not in cols:
                conn.execute("ALTER TABLE mini_course_registrations ADD COLUMN created_at TIMESTAMP")
                conn.execute("UPDATE mini_course_registrations SET created_at = datetime('now') WHERE created_at IS NULL")
            conn.execute("""INSERT OR IGNORE INTO fair_settings (key, value) VALUES
                    ('is_active', '0'),
                    ('active_event_id', '');
            """)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection):
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
        if "team_size" in cols and "min_team_size" not in cols:
            conn.execute("ALTER TABLE events ADD COLUMN min_team_size INTEGER DEFAULT 3")
            conn.execute("ALTER TABLE events ADD COLUMN max_team_size INTEGER DEFAULT 5")
            conn.execute("UPDATE events SET min_team_size = team_size, max_team_size = team_size WHERE team_size IS NOT NULL")
        if "is_fair" not in cols:
            conn.execute("ALTER TABLE events ADD COLUMN is_fair BOOLEAN DEFAULT 0")

    def generate_personal_code(self) -> str:
        while True:
            code = f"{random.choice(string.ascii_lowercase)}{''.join(random.choices(string.digits, k=3))}{''.join(random.choices(string.ascii_lowercase, k=2))}"
            with self._get_conn() as conn:
                if not conn.execute("SELECT 1 FROM participants WHERE personal_code=?", (code,)).fetchone():
                    return code

    def generate_admin_invite_code(self, role: str = 'admin') -> str:
        while True:
            code = f"{random.choice(string.ascii_uppercase)}{''.join(random.choices(string.digits, k=4))}{''.join(random.choices(string.ascii_uppercase, k=2))}"
            with self._get_conn() as conn:
                if not conn.execute("SELECT 1 FROM admin_invites WHERE invite_code=?", (code,)).fetchone():
                    conn.execute("INSERT INTO admin_invites (invite_code, role) VALUES (?, ?)", (code, role))
                    return code

    def sync_admin_from_config(self, user_id: int, role: str):
        with self._get_conn() as conn:
            ex = conn.execute("SELECT * FROM participants WHERE user_id=?", (user_id,)).fetchone()
            if ex:
                if ex['role'] != role:
                    conn.execute("UPDATE participants SET role=? WHERE user_id=?", (role, user_id))
            else:
                conn.execute("INSERT INTO participants (user_id, first_name, last_name, personal_code, role) VALUES (?, 'Admin', 'Config', ?, ?)",
                             (user_id, self.generate_personal_code(), role))

    def add_admin_by_vk_id(self, user_id: int, first_name: str, last_name: str, role: str = 'admin') -> Tuple[bool, str]:
        with self._get_conn() as conn:
            ex = conn.execute("SELECT * FROM participants WHERE user_id=?", (user_id,)).fetchone()
            if ex:
                if ex['role'] != role:
                    conn.execute("UPDATE participants SET role=? WHERE user_id=?", (role, user_id))
                return True, f"{first_name} {last_name} уже в базе. Роль обновлена."
            conn.execute("INSERT INTO participants (user_id, first_name, last_name, personal_code, role) VALUES (?, ?, ?, ?, ?)",
                         (user_id, first_name, last_name, self.generate_personal_code(), role))
            return True, f"{first_name} {last_name} назначен {role}."

    def get_participant_by_user_id(self, user_id: int) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM participants WHERE user_id=?", (user_id,)).fetchone()
            return dict(row) if row else None

    def get_all_participants(self) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT id, user_id, first_name, last_name FROM participants WHERE role='participant' AND user_id IS NOT NULL ORDER BY last_name").fetchall()]

    def get_unregistered_participants_for_event(self, event_id: int, current_user_id: int) -> List[Dict]:
        """Получить участников, НЕ входящих ни в одну команду мероприятия (кроме текущего)"""
        with self._get_conn() as conn:
            current_p = conn.execute("SELECT id FROM participants WHERE user_id=?", (current_user_id,)).fetchone()
            current_id = current_p['id'] if current_p else -1

            free = conn.execute("""
                SELECT p.id, p.user_id, p.first_name, p.last_name
                FROM participants p
                WHERE p.role='participant'
                  AND p.user_id IS NOT NULL
                  AND p.id != ?
                  AND p.id NOT IN (
                    SELECT participant_id FROM event_registrations WHERE event_id = ?
                  )
                ORDER BY p.last_name
            """, (current_id, event_id)).fetchall()
            return [dict(r) for r in free]

    def get_my_team_for_event(self, event_id: int, user_id: int) -> Optional[Dict]:
        """Проверяет, состоит ли пользователь в какой-либо команде мероприятия"""
        with self._get_conn() as conn:
            p = conn.execute("SELECT id FROM participants WHERE user_id=?", (user_id,)).fetchone()
            if not p:
                return None
            # Своя строка в event_registrations — пользователь в команде (капитан или участник)
            my_row = conn.execute("""
                SELECT er.*, p2.first_name, p2.last_name
                FROM event_registrations er
                JOIN participants p2 ON p2.id = er.participant_id
                WHERE er.event_id = ? AND er.participant_id = ?
            """, (event_id, p['id'])).fetchone()
            return dict(my_row) if my_row else None

    def get_my_mini_course_team(self, mc_id: int, ts_id: int, user_id: int) -> Optional[Dict]:
        """Получить информацию о команде пользователя на мини-курс"""
        with self._get_conn() as conn:
            p = conn.execute("SELECT id FROM participants WHERE user_id=?", (user_id,)).fetchone()
            if not p:
                return None
            reg = conn.execute("""
                SELECT mcr.*, p2.first_name, p2.last_name
                FROM mini_course_registrations mcr
                JOIN participants p2 ON p2.id = mcr.participant_id
                WHERE mcr.mini_course_id = ? AND mcr.time_slot_id = ? AND mcr.participant_id = ?
            """, (mc_id, ts_id, p['id'])).fetchone()
            return dict(reg) if reg else None

    def can_cancel_registration(self, event_id: int, user_id: int) -> bool:
        """Проверка, можно ли отменить регистрацию (в течение 2 часов)"""
        with self._get_conn() as conn:
            p = conn.execute("SELECT id FROM participants WHERE user_id=?", (user_id,)).fetchone()
            if not p:
                return False
            reg = conn.execute("""
                SELECT created_at
                FROM event_registrations
                WHERE event_id = ? AND participant_id = ?
            """, (event_id, p['id'])).fetchone()
            if not reg:
                return False

            # Проверка 2-х часового лимита
            import datetime
            created_at = datetime.datetime.fromisoformat(reg['created_at'].replace('T', ' '))
            return (datetime.datetime.now() - created_at).total_seconds() < 7200

    def can_cancel_mini_course_registration(self, mc_id: int, ts_id: int, user_id: int) -> bool:
        """Проверка, можно ли отменить регистрацию на курс (в течение 2 часов)"""
        with self._get_conn() as conn:
            p = conn.execute("SELECT id FROM participants WHERE user_id=?", (user_id,)).fetchone()
            if not p:
                return False
            reg = conn.execute("""
                SELECT created_at
                FROM mini_course_registrations
                WHERE mini_course_id = ? AND time_slot_id = ? AND participant_id = ?
            """, (mc_id, ts_id, p['id'])).fetchone()
            if not reg:
                return False

            import datetime
            created_at = datetime.datetime.fromisoformat(reg['created_at'].replace('T', ' '))
            return (datetime.datetime.now() - created_at).total_seconds() < 7200

    def auto_distribute_participants(self, event_id: int):
        """Равномерное распределение участников без команды по существующим командам"""
        import random
        with self._get_conn() as conn:
            ev = self.get_event(event_id)
            if not ev:
                return 0

            unregistered = conn.execute("""
                SELECT p.id, p.first_name, p.last_name
                FROM participants p
                WHERE p.role='participant'
                AND p.id NOT IN (
                    SELECT participant_id FROM event_registrations WHERE event_id = ?
                )
            """, (event_id,)).fetchall()

            if not unregistered:
                return 0

            teams = conn.execute("""
                SELECT er.participant_id, er.team_members
                FROM event_registrations er
                WHERE er.event_id = ?
            """, (event_id,)).fetchall()

            if not teams:
                return 0

            # Считаем текущий размер каждой команды
            team_sizes = []
            for t in teams:
                members = [m.strip() for m in (t['team_members'] or '').split(',') if m.strip()]
                team_sizes.append({'participant_id': t['participant_id'], 'size': len(members)})

            distributed = 0
            shuffled = list(unregistered)
            random.shuffle(shuffled)

            for participant in shuffled:
                team_sizes.sort(key=lambda x: x['size'])
                best = None
                for ts in team_sizes:
                    if ts['size'] < ev['max_team_size']:
                        best = ts
                        break
                if not best:
                    break

                # Создаём строку участника в event_registrations
                conn.execute(
                    "INSERT OR IGNORE INTO event_registrations (event_id, participant_id, team_members) VALUES (?, ?, ?)",
                    (event_id, participant['id'], "")
                )

                # Обновляем team_members у капитана (первая запись в team_sizes)
                cap_row = conn.execute(
                    "SELECT team_members FROM event_registrations WHERE event_id=? AND participant_id=?",
                    (event_id, best['participant_id'])
                ).fetchone()
                old = (cap_row['team_members'] or '').strip()
                new_members = (f"{old}, {participant['last_name']} {participant['first_name']}").lstrip(', ')
                conn.execute(
                    "UPDATE event_registrations SET team_members=? WHERE event_id=? AND participant_id=?",
                    (new_members, event_id, best['participant_id'])
                )
                best['size'] += 1
                distributed += 1

            return distributed

    def auto_distribute_mini_course_participants(self, mc_id: int, ts_id: int):
        """Автоматическое распределение участников на мини-курс"""
        with self._get_conn() as conn:
            mc = self.get_mini_course(mc_id)
            if not mc:
                return 0

            # Участники, не записанные на этот курс и слот
            unregistered = conn.execute("""
                SELECT p.id, p.first_name, p.last_name
                FROM participants p
                WHERE p.role='participant'
                AND p.id NOT IN (
                    SELECT participant_id FROM mini_course_registrations WHERE mini_course_id = ? AND time_slot_id = ?
                )
            """, (mc_id, ts_id)).fetchall()

            if not unregistered:
                return 0

            # Существующие команды
            teams = conn.execute("""
                SELECT participant_id
                FROM mini_course_registrations
                WHERE mini_course_id = ? AND time_slot_id = ?
            """, (mc_id, ts_id)).fetchall()

            if not teams:
                return 0

            # Распределение по командам с свободными местами
            distributed = 0
            for participant in unregistered:
                # Ищем команду с минимальным количеством участников
                best_team = None
                min_count = float('inf')

                for team in teams:
                    # Подсчитываем участников в этой команде
                    count = conn.execute("""
                        SELECT COUNT(*) as cnt
                        FROM mini_course_registrations
                        WHERE mini_course_id = ? AND time_slot_id = ? AND participant_id = ?
                    """, (mc_id, ts_id, team['participant_id'])).fetchone()['cnt']

                    if count < mc['max_participants'] and count < min_count:
                        min_count = count
                        best_team = team

                if best_team:
                    # Записываем участника в команду
                    conn.execute("""
                        INSERT OR IGNORE INTO mini_course_registrations (mini_course_id, participant_id, time_slot_id)
                        VALUES (?, ?, ?)
                    """, (mc_id, participant['id'], ts_id))
                    distributed += 1

            return distributed

    def get_participant_balance(self, pid: int) -> int:
        with self._get_conn() as conn:
            row = conn.execute("SELECT balance FROM participants WHERE id=?", (pid,)).fetchone()
            return row['balance'] if row else 0

    def add_balance_to_participant(self, participant_id: int, amount: int, description: str = "Пополнение баланса"):
        with self._get_conn() as conn:
            conn.execute("UPDATE participants SET balance = balance + ? WHERE id=?", (amount, participant_id))
            conn.execute("INSERT INTO balance_history (participant_id, amount, description) VALUES (?, ?, ?)",
                         (participant_id, amount, description))

    def create_event(self, name: str, desc: str, min_size: int, max_size: int, is_fair: bool = False) -> int:
        with self._get_conn() as conn:
            return conn.execute("INSERT INTO events (name, description, min_team_size, max_team_size, is_fair) VALUES (?, ?, ?, ?, ?)",
                               (name, desc, min_size, max_size, 1 if is_fair else 0)).lastrowid

    def get_active_events(self) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM events WHERE is_active=1").fetchall()]

    def get_event(self, eid: int) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
            return dict(row) if row else None

    def get_my_event_team(self, event_id: int, user_id: int) -> Optional[List[Dict]]:
        with self._get_conn() as conn:
            p = conn.execute("SELECT id FROM participants WHERE user_id=?", (user_id,)).fetchone()
            if not p:
                return None
            reg = conn.execute("SELECT team_members FROM event_registrations WHERE event_id=? AND participant_id=?", (event_id, p['id'])).fetchone()
            if not reg:
                return None
            members = [m.strip() for m in (reg['team_members'] or '').split(',')]
            return [dict(r) for r in conn.execute("SELECT first_name, last_name FROM participants WHERE CONCAT(last_name, ' ', first_name) IN ({})".format(','.join(['?']*len(members))), members).fetchall()]

    def leave_event_team(self, event_id: int, user_id: int) -> bool:
        with self._get_conn() as conn:
            p = conn.execute("SELECT id FROM participants WHERE user_id=?", (user_id,)).fetchone()
            if not p:
                return False
            conn.execute("DELETE FROM event_registrations WHERE event_id=? AND participant_id=?", (event_id, p['id']))
            return True

    def leave_mini_course_team(self, mc_id: int, ts_id: int, user_id: int) -> bool:
        with self._get_conn() as conn:
            p = conn.execute("SELECT id FROM participants WHERE user_id=?", (user_id,)).fetchone()
            if not p:
                return False
            conn.execute("DELETE FROM mini_course_registrations WHERE mini_course_id=? AND participant_id=?", (mc_id, p['id']))
            return True

    def register_team_for_event(self, event_id: int, captain_user_id: int, selected_user_ids: List[int]) -> Tuple[bool, str, List[str]]:
        ev = self.get_event(event_id)
        if not ev:
            return False, "Мероприятие не найдено", []
        if not (ev['min_team_size'] <= len(selected_user_ids) <= ev['max_team_size']):
            return False, f"Нужно от {ev['min_team_size']} до {ev['max_team_size']} человек.", []

        errors = []
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cap = conn.execute("SELECT id, first_name, last_name FROM participants WHERE user_id=?", (captain_user_id,)).fetchone()
                if not cap:
                    return False, "Вы не зарегистрированы", errors

                # Проверяем каждого выбранного участника в одной транзакции
                member_participant_ids = []
                for vk_uid in selected_user_ids:
                    p = conn.execute("SELECT id, first_name, last_name FROM participants WHERE user_id=?", (vk_uid,)).fetchone()
                    if not p:
                        errors.append(f"Участник с ID {vk_uid} не найден")
                        continue
                    existing = conn.execute(
                        "SELECT 1 FROM event_registrations WHERE event_id=? AND participant_id=?",
                        (event_id, p['id'])
                    ).fetchone()
                    if existing:
                        errors.append(f"{p['last_name']} {p['first_name']} уже состоит в другой команде")
                        continue
                    member_participant_ids.append(p['id'])

                if errors:
                    conn.rollback()
                    return False, "Некоторые участники не могут быть добавлены", errors

                # Удаляем старую регистрацию капитана (если была)
                conn.execute("DELETE FROM event_registrations WHERE event_id=? AND participant_id=?", (event_id, cap['id']))

                # Создаём запись — капитан регистрирует команду
                members_names = []
                for pid in member_participant_ids:
                    p = conn.execute("SELECT first_name, last_name FROM participants WHERE id=?", (pid,)).fetchone()
                    if p:
                        members_names.append(f"{p['last_name']} {p['first_name']}")
                        conn.execute(
                            "INSERT OR IGNORE INTO event_registrations (event_id, participant_id, team_members) VALUES (?, ?, ?)",
                            (event_id, pid, "")
                        )
                # Обновляем team_members у капитана (храним список имён)
                conn.execute(
                    "UPDATE event_registrations SET team_members=? WHERE event_id=? AND participant_id=?",
                    (", ".join(members_names), event_id, cap['id'])
                )

                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e

        # Автораспределение (уже вне критической секции)
        self.auto_distribute_participants(event_id)
        return True, "Команда зарегистрирована!", []

    def register_mini_course_individual(self, mc_id: int, ts_id: int, user_id: int) -> Tuple[bool, str]:
        """Индивидуальная запись участника на мини-курс"""
        mc = self.get_mini_course(mc_id)
        if not mc:
            return False, "Курс не найден"

        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                p = conn.execute("SELECT id FROM participants WHERE user_id=?", (user_id,)).fetchone()
                if not p:
                    return False, "Вы не зарегистрированы"

                # Проверяем, не записан ли уже на этот курс+слот
                already = conn.execute(
                    "SELECT 1 FROM mini_course_registrations WHERE mini_course_id=? AND time_slot_id=? AND participant_id=?",
                    (mc_id, ts_id, p['id'])
                ).fetchone()
                if already:
                    return False, "Вы уже записаны на этот курс в данное время"

                # Проверяем количество мест
                current = conn.execute(
                    "SELECT COUNT(*) as cnt FROM mini_course_registrations WHERE mini_course_id=? AND time_slot_id=?",
                    (mc_id, ts_id)
                ).fetchone()['cnt']
                if current >= mc['max_participants']:
                    return False, "Нет свободных мест на этот временной слот"

                conn.execute(
                    "INSERT INTO mini_course_registrations (mini_course_id, participant_id, time_slot_id) VALUES (?, ?, ?)",
                    (mc_id, p['id'], ts_id)
                )
                conn.commit()
                return True, f"Вы записаны на курс '{mc['name']}'!"
            except Exception as e:
                conn.rollback()
                raise e

    def get_published_mini_courses_with_stats(self) -> List[Dict]:
        """Возвращает опубликованные мини-курсы с количеством свободных мест по каждому слоту"""
        with self._get_conn() as conn:
            courses = [dict(r) for r in conn.execute("SELECT * FROM mini_courses WHERE is_published=1").fetchall()]
            for mc in courses:
                slots = conn.execute("""
                    SELECT ts.*,
                           (SELECT COUNT(*) FROM mini_course_registrations mcr WHERE mcr.time_slot_id=ts.id) as registered
                    FROM time_slots ts
                    WHERE ts.mini_course_id=?
                """, (mc['id'],)).fetchall()
                mc['slots'] = [dict(s) for s in slots]
                mc['total_registered'] = conn.execute(
                    "SELECT COUNT(*) FROM mini_course_registrations WHERE mini_course_id=?", (mc['id'],)
                ).fetchone()[0]
            return courses

    def get_event_teams_list(self, event_id: int) -> List[Dict]:
        """Список команд мероприятия для админа"""
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("""
                SELECT er.*, p.first_name, p.last_name
                FROM event_registrations er
                JOIN participants p ON p.id = er.participant_id
                WHERE er.event_id = ?
                ORDER BY p.last_name
            """, (event_id,)).fetchall()]

    def get_mini_course_full_registrations(self, mc_id: int) -> List[Dict]:
        """Все регистрации на мини-курс со слотами для админа"""
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("""
                SELECT mcr.*, p.first_name, p.last_name, ts.time as slot_time
                FROM mini_course_registrations mcr
                JOIN participants p ON p.id = mcr.participant_id
                JOIN time_slots ts ON ts.id = mcr.time_slot_id
                WHERE mcr.mini_course_id = ?
                ORDER BY ts.time, p.last_name
            """, (mc_id,)).fetchall()]

    def submit_complaint(self, pid: int, msg: str):
        with self._get_conn() as conn:
            conn.execute("INSERT INTO complaints (participant_id, message) VALUES (?, ?)", (pid, msg))

    def get_recent_complaints(self) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT c.*, p.first_name, p.last_name, p.personal_code FROM complaints c JOIN participants p ON p.id=c.participant_id WHERE c.is_resolved=0 ORDER BY c.created_at DESC LIMIT 10").fetchall()]

    def resolve_complaint(self, cid: int):
        with self._get_conn() as conn:
            conn.execute("UPDATE complaints SET is_resolved=1 WHERE id=?", (cid,))

    def pre_register_participants(self, names: List[str]) -> List[Dict]:
        res = []
        with self._get_conn() as conn:
            for n in names:
                parts = n.strip().split()
                if len(parts) < 2:
                    res.append({'success': False, 'name': n, 'error': 'Формат'})
                    continue
                ln, fn = parts[0], ' '.join(parts[1:])
                pc = self.generate_personal_code()
                try:
                    conn.execute("INSERT INTO pre_registered_participants (first_name, last_name, personal_code) VALUES (?, ?, ?)", (fn, ln, pc))
                    res.append({'success': True, 'name': f"{ln} {fn}", 'personal_code': pc})
                except:
                    res.append({'success': False, 'name': f"{ln} {fn}", 'error': 'Уже есть'})
        return res

    def login_with_personal_code(self, uid: int, pc: str) -> Tuple[bool, str, Optional[Dict]]:
        with self._get_conn() as conn:
            pr = conn.execute("SELECT * FROM pre_registered_participants WHERE personal_code=? AND is_used=0", (pc,)).fetchone()
            if not pr:
                return False, "Код не найден", None
            ex = conn.execute("SELECT * FROM participants WHERE user_id=?", (uid,)).fetchone()
            if ex:
                return False, f"Вы уже {ex['first_name']}", ex
            conn.execute("INSERT INTO participants (user_id, first_name, last_name, personal_code) VALUES (?, ?, ?, ?)",
                        (uid, pr['first_name'], pr['last_name'], pc))
            conn.execute("UPDATE pre_registered_participants SET is_used=1 WHERE id=?", (pr['id'],))
            p = conn.execute("SELECT * FROM participants WHERE user_id=?", (uid,)).fetchone()
            return True, f"Привет, {p['first_name']}!", dict(p)

    def login_with_admin_invite(self, uid: int, invite_code: str) -> Tuple[bool, str, Optional[Dict]]:
        with self._get_conn() as conn:
            invite = conn.execute("SELECT * FROM admin_invites WHERE invite_code=? AND is_used=0", (invite_code,)).fetchone()
            if not invite:
                return False, "Пригласительный код не найден или уже использован", None
            ex = conn.execute("SELECT * FROM participants WHERE user_id=?", (uid,)).fetchone()
            if ex:
                return False, f"Вы уже {ex['first_name']}", ex
            conn.execute("INSERT INTO participants (user_id, first_name, last_name, personal_code, role) VALUES (?, 'Admin', 'User', ?, ?)",
                        (uid, self.generate_personal_code(), invite['role']))
            conn.execute("UPDATE admin_invites SET is_used=1 WHERE id=?", (invite['id'],))
            p = conn.execute("SELECT * FROM participants WHERE user_id=?", (uid,)).fetchone()
            return True, f"Привет, Админ! Ваша роль: {p['role']}", dict(p)

    def is_fair_active(self) -> bool:
        with self._get_conn() as conn:
            row = conn.execute("SELECT value FROM fair_settings WHERE key='is_active'").fetchone()
            return row and row['value'] == '1'

    def get_active_fair_event_id(self) -> Optional[int]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT value FROM fair_settings WHERE key='active_event_id'").fetchone()
            return int(row['value']) if row else None

    def start_fair(self, event_id: int, budget: int):
        with self._get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO fair_settings (key, value) VALUES ('is_active', '1')")
            conn.execute("INSERT OR REPLACE INTO fair_settings (key, value) VALUES ('active_event_id', ?)", (str(event_id),))
            conn.execute("INSERT OR REPLACE INTO fair_settings (key, value) VALUES ('team_budget', ?)", (str(budget),))

    def stop_fair(self):
        with self._get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO fair_settings (key, value) VALUES ('is_active', '0')")

    def get_fair_teams(self, event_id: int) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("""
                SELECT er.id, er.team_members, p.user_id as captain_uid, p.first_name, p.last_name
                FROM event_registrations er
                JOIN participants p ON p.id = er.participant_id
                WHERE er.event_id = ?
            """, (event_id,)).fetchall()]

    def find_fair_participant(self, user_id: int, event_id: int) -> Optional[Dict]:
        p = self.get_participant_by_user_id(user_id)
        if not p:
            return None
        user_full_name = f"{p['last_name']} {p['first_name']}"

        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT er.id, er.team_members, t.name as team_name
                FROM event_registrations er
                JOIN events t ON t.id = er.event_id
                WHERE er.event_id = ?
            """, (event_id,)).fetchall()

            for row in rows:
                members = [m.strip() for m in (row['team_members'] or '').split(',')]
                if user_full_name in members:
                    return {
                        'reg_id': row['id'],
                        'team_name': row['team_name'],
                        'members': members
                    }
        return None

    def add_fair_item(self, user_id: int, team_name: str, name: str, price: int) -> int:
        with self._get_conn() as conn:
            return conn.execute("INSERT INTO fair_items (user_id, team_name, name, price) VALUES (?, ?, ?, ?)",
                               (user_id, team_name, name, price)).lastrowid

    def get_fair_items(self, user_id: int) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM fair_items WHERE user_id=? AND is_active=1", (user_id,)).fetchall()]

    def get_team_budget(self) -> int:
        with self._get_conn() as conn:
            row = conn.execute("SELECT value FROM fair_settings WHERE key='team_budget'").fetchone()
            return int(row['value']) if row else 0

    def get_event_registration_details(self, event_id: int) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("""
                SELECT er.*, p.first_name, p.last_name
                FROM event_registrations er
                JOIN participants p ON p.id = er.participant_id
                WHERE er.event_id = ?
            """, (event_id,)).fetchall()]

    def get_mini_course_registrations(self, mc_id: int) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("""
                SELECT mcr.*, p.first_name, p.last_name
                FROM mini_course_registrations mcr
                JOIN participants p ON p.id = mcr.participant_id
                WHERE mcr.mini_course_id = ?
            """, (mc_id,)).fetchall()]

    def get_unpublished_mini_courses(self) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM mini_courses WHERE is_published=0").fetchall()]

    def publish_mini_courses(self) -> int:
        with self._get_conn() as conn:
            conn.execute("UPDATE mini_courses SET is_published=1 WHERE is_published=0")
            return conn.execute("SELECT COUNT(*) FROM mini_courses WHERE is_published=1").fetchone()[0]

    def get_balance_history(self, pid: int) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM balance_history WHERE participant_id=? ORDER BY created_at DESC", (pid,)).fetchall()]

    def get_pre_registered_participants(self) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM pre_registered_participants ORDER BY last_name").fetchall()]

    def get_mini_course(self, mc_id: int) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM mini_courses WHERE id=?", (mc_id,)).fetchone()
            return dict(row) if row else None

    def get_time_slot(self, ts_id: int) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM time_slots WHERE id=?", (ts_id,)).fetchone()
            return dict(row) if row else None

    def get_time_slots(self, mc_id: int) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM time_slots WHERE mini_course_id=?", (mc_id,)).fetchall()]

    def get_published_mini_courses(self) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM mini_courses WHERE is_published=1").fetchall()]

    def create_mini_course(self, name: str, desc: str, max_p: int) -> int:
        with self._get_conn() as conn:
            return conn.execute("INSERT INTO mini_courses (name, description, max_participants) VALUES (?, ?, ?)", (name, desc, max_p)).lastrowid

    def add_time_slot(self, mc_id: int, time: str):
        with self._get_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO time_slots (mini_course_id, time) VALUES (?, ?)", (mc_id, time))
