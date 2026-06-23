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
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
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
                    buyer_team_id INTEGER,
                    seller_team_id INTEGER,
                    buyer_user_id INTEGER,
                    seller_user_id INTEGER,
                    amount INTEGER,
                    description TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    assigned_admin_user_id INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS fair_teams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL,
                    captain_participant_id INTEGER NOT NULL,
                    team_name TEXT NOT NULL,
                    budget INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(event_id, captain_participant_id)
                );

                CREATE TABLE IF NOT EXISTS fair_admin_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL,
                    admin_user_id INTEGER NOT NULL,
                    is_on_break BOOLEAN DEFAULT 0,
                    UNIQUE(event_id, admin_user_id)
                );

                CREATE TABLE IF NOT EXISTS fair_change_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL,
                    team_id INTEGER NOT NULL,
                    request_type TEXT NOT NULL,
                    user_id INTEGER DEFAULT 0,
                    item_id INTEGER DEFAULT 0,
                    old_data TEXT DEFAULT '',
                    new_data TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    assigned_admin_user_id INTEGER DEFAULT 0,
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
                    ('active_event_id', ''),
                    ('is_paused', '0'),
                    ('is_completed', '0'),
                    ('admin_queue_index', '0'),
                    ('cooldown_seconds', '0');
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

        fair_items_cols = {row["name"] for row in conn.execute("PRAGMA table_info(fair_items)")}
        if "team_id" not in fair_items_cols:
            conn.execute("ALTER TABLE fair_items ADD COLUMN team_id INTEGER DEFAULT 0")
        if "description" not in fair_items_cols:
            conn.execute("ALTER TABLE fair_items ADD COLUMN description TEXT DEFAULT ''")

        ft_cols = {row["name"] for row in conn.execute("PRAGMA table_info(fair_transactions)")}
        if "buyer_team_id" not in ft_cols:
            conn.execute("ALTER TABLE fair_transactions ADD COLUMN buyer_team_id INTEGER DEFAULT 0")
        if "seller_team_id" not in ft_cols:
            conn.execute("ALTER TABLE fair_transactions ADD COLUMN seller_team_id INTEGER DEFAULT 0")
        if "description" not in ft_cols:
            conn.execute("ALTER TABLE fair_transactions ADD COLUMN description TEXT DEFAULT ''")
        if "assigned_admin_user_id" not in ft_cols:
            conn.execute("ALTER TABLE fair_transactions ADD COLUMN assigned_admin_user_id INTEGER DEFAULT 0")
        if "buyer_user_id" not in ft_cols:
            conn.execute("ALTER TABLE fair_transactions ADD COLUMN buyer_user_id INTEGER DEFAULT 0")
        if "seller_user_id" not in ft_cols:
            conn.execute("ALTER TABLE fair_transactions ADD COLUMN seller_user_id INTEGER DEFAULT 0")

        fcr_cols = {row["name"] for row in conn.execute("PRAGMA table_info(fair_change_requests)")}
        if "user_id" not in fcr_cols:
            conn.execute("ALTER TABLE fair_change_requests ADD COLUMN user_id INTEGER DEFAULT 0")

        evt_cols = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
        if "is_published" not in evt_cols:
            conn.execute("ALTER TABLE events ADD COLUMN is_published BOOLEAN DEFAULT 0")
            # Existing events become published automatically
            conn.execute("UPDATE events SET is_published=1")

        mc_cols = {row["name"] for row in conn.execute("PRAGMA table_info(mini_courses)")}
        if "date" not in mc_cols:
            conn.execute("ALTER TABLE mini_courses ADD COLUMN date TEXT DEFAULT ''")

        ts_cols = {row["name"] for row in conn.execute("PRAGMA table_info(time_slots)")}
        if "max_participants" not in ts_cols:
            conn.execute("ALTER TABLE time_slots ADD COLUMN max_participants INTEGER DEFAULT 0")

        evt_cols2 = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
        if "published_at" not in evt_cols2:
            conn.execute("ALTER TABLE events ADD COLUMN published_at TIMESTAMP")

        mc_cols2 = {row["name"] for row in conn.execute("PRAGMA table_info(mini_courses)")}
        if "published_at" not in mc_cols2:
            conn.execute("ALTER TABLE mini_courses ADD COLUMN published_at TIMESTAMP")

        evt_cols3 = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
        if "is_closed" not in evt_cols3:
            conn.execute("ALTER TABLE events ADD COLUMN is_closed BOOLEAN DEFAULT 0")

        mc_cols3 = {row["name"] for row in conn.execute("PRAGMA table_info(mini_courses)")}
        if "is_closed" not in mc_cols3:
            conn.execute("ALTER TABLE mini_courses ADD COLUMN is_closed BOOLEAN DEFAULT 0")

        reg_cols = {row["name"] for row in conn.execute("PRAGMA table_info(event_registrations)")}
        if "captain_id" not in reg_cols:
            conn.execute("ALTER TABLE event_registrations ADD COLUMN captain_id INTEGER DEFAULT 0")
            # Бэкап: для существующих записей капитанов (team_members != '') ставим captain_id = participant_id
            conn.execute("UPDATE event_registrations SET captain_id = participant_id WHERE team_members != ''")
            # Для остальных — пытаемся найти капитана по составу команды
            for row in conn.execute("SELECT id, event_id, participant_id FROM event_registrations WHERE captain_id = 0").fetchall():
                pid = row['participant_id']
                ev_id = row['event_id']
                p = conn.execute("SELECT last_name, first_name FROM participants WHERE id=?", (pid,)).fetchone()
                if p:
                    full_name = f"{p['last_name']} {p['first_name']}"
                    cap = conn.execute(
                        "SELECT participant_id FROM event_registrations WHERE event_id=? AND team_members LIKE ? LIMIT 1",
                        (ev_id, f"%{full_name}%")
                    ).fetchone()
                    if cap:
                        conn.execute("UPDATE event_registrations SET captain_id=? WHERE id=?", (cap['participant_id'], row['id']))

        # Indexes for performance
        existing_indexes = {r[2] for r in conn.execute("SELECT * FROM sqlite_master WHERE type='index'")}
        fair_indexes = {
            "idx_fair_items_team_id": "CREATE INDEX IF NOT EXISTS idx_fair_items_team_id ON fair_items(team_id)",
            "idx_fair_items_is_active": "CREATE INDEX IF NOT EXISTS idx_fair_items_is_active ON fair_items(is_active)",
            "idx_fair_transactions_status": "CREATE INDEX IF NOT EXISTS idx_fair_transactions_status ON fair_transactions(status)",
            "idx_fair_transactions_buyer": "CREATE INDEX IF NOT EXISTS idx_fair_transactions_buyer ON fair_transactions(buyer_team_id)",
            "idx_fair_transactions_seller": "CREATE INDEX IF NOT EXISTS idx_fair_transactions_seller ON fair_transactions(seller_team_id)",
            "idx_fair_teams_event_id": "CREATE INDEX IF NOT EXISTS idx_fair_teams_event_id ON fair_teams(event_id)",
            "idx_event_registrations_event": "CREATE INDEX IF NOT EXISTS idx_event_registrations_event ON event_registrations(event_id)",
            "idx_event_registrations_participant": "CREATE INDEX IF NOT EXISTS idx_event_registrations_participant ON event_registrations(participant_id)",
            "idx_participants_user_id": "CREATE INDEX IF NOT EXISTS idx_participants_user_id ON participants(user_id)",
            "idx_fair_admin_queue_event": "CREATE INDEX IF NOT EXISTS idx_fair_admin_queue_event ON fair_admin_queue(event_id)",
        }
        for name, ddl in fair_indexes.items():
            if name not in existing_indexes:
                conn.execute(ddl)

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
            return [dict(r) for r in conn.execute("SELECT id, user_id, first_name, last_name, balance FROM participants WHERE role='participant' AND user_id IS NOT NULL ORDER BY last_name").fetchall()]

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

    def get_team_info_for_event(self, event_id: int, user_id: int) -> Optional[Dict]:
        """Полная информация о команде пользователя на мероприятии"""
        with self._get_conn() as conn:
            ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if not ev:
                return None
            p = conn.execute("SELECT id FROM participants WHERE user_id=?", (user_id,)).fetchone()
            if not p:
                return None
            my_reg = conn.execute(
                "SELECT * FROM event_registrations WHERE event_id=? AND participant_id=?",
                (event_id, p['id'])
            ).fetchone()
            if not my_reg:
                return None

            captain_id = my_reg['captain_id']
            if captain_id == 0:
                return None

            captain = conn.execute("SELECT * FROM participants WHERE id=?", (captain_id,)).fetchone()
            if not captain:
                return None

            team_regs = conn.execute("""
                SELECT er.*, p.first_name, p.last_name, p.user_id
                FROM event_registrations er
                JOIN participants p ON p.id = er.participant_id
                WHERE er.event_id=? AND er.captain_id=?
            """, (event_id, captain_id)).fetchall()

            members = []
            for reg in team_regs:
                members.append({
                    "id": reg['participant_id'],
                    "user_id": reg['user_id'],
                    "first_name": reg['first_name'],
                    "last_name": reg['last_name'],
                    "is_captain": reg['participant_id'] == captain_id
                })

            return {
                "event_name": ev['name'],
                "event_id": event_id,
                "captain_id": captain_id,
                "captain_user_id": captain['user_id'],
                "captain_name": f"{captain['last_name']} {captain['first_name']}",
                "members": members,
                "team_size": len(members),
                "min_team_size": ev['min_team_size'],
                "max_team_size": ev['max_team_size'],
                "is_captain": p['id'] == captain_id,
                "my_id": p['id']
            }

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

    def auto_distribute_participants(self, event_id: int) -> int:
        """Равномерное распределение участников без команды по командам.
        Если команд нет — создаёт их автоматически."""
        import random
        with self._get_conn() as conn:
            ev = self.get_event(event_id)
            if not ev or not ev['max_team_size']:
                return 0

            # Все участники системы
            all_participants = conn.execute("""
                SELECT p.id, p.first_name, p.last_name
                FROM participants p
                WHERE p.role='participant' AND p.user_id IS NOT NULL
                ORDER BY p.last_name
            """).fetchall()

            # Уже зарегистрированные
            registered_ids = {
                r['participant_id'] for r in conn.execute(
                    "SELECT participant_id FROM event_registrations WHERE event_id=?", (event_id,)
                ).fetchall()
            }

            unregistered = [p for p in all_participants if p['id'] not in registered_ids]
            if not unregistered:
                return 0

            # Капитаны существующих команд (captain_id == participant_id)
            captains = conn.execute("""
                SELECT DISTINCT er.participant_id, er.team_members
                FROM event_registrations er
                WHERE er.event_id=? AND er.captain_id = er.participant_id
            """, (event_id,)).fetchall()

            # Если команд нет — создаём их
            if not captains:
                random.shuffle(all_participants)
                max_size = ev['max_team_size']
                min_size = ev.get('min_team_size', 1) or 1
                num_teams = max(1, (len(all_participants) + max_size - 1) // max_size)

                for i in range(num_teams):
                    team_members = all_participants[i::num_teams]
                    if not team_members:
                        continue
                    captain = team_members[0]
                    member_names = [f"{m['last_name']} {m['first_name']}" for m in team_members[1:]]
                    conn.execute(
                        "INSERT OR IGNORE INTO event_registrations (event_id, participant_id, team_members, captain_id) VALUES (?, ?, ?, ?)",
                        (event_id, captain['id'], ", ".join(member_names), captain['id'])
                    )
                    for m in team_members[1:]:
                        conn.execute(
                            "INSERT OR IGNORE INTO event_registrations (event_id, participant_id, team_members, captain_id) VALUES (?, ?, ?, ?)",
                            (event_id, m['id'], "", captain['id'])
                        )
                return len(all_participants)

            # Распределяем по существующим командам
            team_sizes = []
            for cap in captains:
                members = [m.strip() for m in (cap['team_members'] or '').split(',') if m.strip()]
                team_sizes.append({'participant_id': cap['participant_id'], 'size': len(members)})

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

                conn.execute(
                    "INSERT OR IGNORE INTO event_registrations (event_id, participant_id, team_members, captain_id) VALUES (?, ?, ?, ?)",
                    (event_id, participant['id'], "", best['participant_id'])
                )

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

    def auto_distribute_mini_course_participants(self, mc_id: int, ts_id: int) -> int:
        """Автоматическое распределение участников на конкретный мини-курс и слот"""
        import random
        with self._get_conn() as conn:
            mc = self.get_mini_course(mc_id)
            if not mc:
                return 0

            slot = conn.execute("SELECT * FROM time_slots WHERE id=?", (ts_id,)).fetchone()
            if not slot:
                return 0

            slot_max = slot['max_participants'] if slot['max_participants'] > 0 else mc['max_participants']
            current_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM mini_course_registrations WHERE time_slot_id=?", (ts_id,)
            ).fetchone()['cnt']
            free = slot_max - current_count
            if free <= 0:
                return 0

            # Участники, ещё не записанные на этот курс
            unregistered = conn.execute("""
                SELECT p.id, p.first_name, p.last_name
                FROM participants p
                WHERE p.role='participant'
                  AND p.user_id IS NOT NULL
                  AND p.id NOT IN (
                    SELECT participant_id FROM mini_course_registrations WHERE mini_course_id=?
                  )
            """, (mc_id,)).fetchall()

            if not unregistered:
                return 0

            random.shuffle(unregistered)
            distributed = 0
            for participant in unregistered:
                if distributed >= free:
                    break
                # Проверяем, нет ли временного конфликта
                conflict = conn.execute("""
                    SELECT 1 FROM mini_course_registrations mcr
                    JOIN time_slots ts2 ON ts2.id = mcr.time_slot_id
                    JOIN mini_courses mc2 ON mc2.id = mcr.mini_course_id
                    WHERE mcr.participant_id = ?
                      AND mc2.date = ?
                      AND ts2.time = ?
                """, (participant['id'], mc.get('date', ''), slot['time'])).fetchone()
                if conflict:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO mini_course_registrations (mini_course_id, participant_id, time_slot_id) VALUES (?, ?, ?)",
                    (mc_id, participant['id'], ts_id)
                )
                distributed += 1

            return distributed

    def auto_distribute_all_mini_courses(self) -> int:
        """Распределяет всех участников, не записанных на мини-курсы,
        по доступным курсам. Гарантирует, что каждый участник записан
        на все опубликованные мини-курсы на текущую дату.
        Если 2 курса в один слот времени — участник получает один из них случайно."""
        import random
        from collections import defaultdict
        total_distributed = 0
        with self._get_conn() as conn:
            courses = conn.execute(
                "SELECT * FROM mini_courses WHERE is_published=1 ORDER BY date"
            ).fetchall()
            if not courses:
                return 0

            all_participants = conn.execute(
                "SELECT id, first_name, last_name FROM participants WHERE role='participant' AND user_id IS NOT NULL"
            ).fetchall()
            if not all_participants:
                return 0

            # Группируем курсы по дате
            by_date = defaultdict(list)
            for mc in courses:
                by_date[mc['date']].append(mc)

            for date, mc_list in by_date.items():
                mc_ids = [mc['id'] for mc in mc_list]
                placeholders = ','.join(['?'] * len(mc_ids))

                # Все слоты для этой даты
                all_slots = conn.execute(f"""
                    SELECT ts.*, mc.id as mc_id, mc.name as course_name,
                           mc.max_participants as course_max
                    FROM time_slots ts
                    JOIN mini_courses mc ON mc.id = ts.mini_course_id
                    WHERE ts.mini_course_id IN ({placeholders})
                    ORDER BY ts.time
                """, mc_ids).fetchall()

                # Группируем слоты по времени
                slots_by_time = defaultdict(list)
                for s in all_slots:
                    slots_by_time[s['time']].append(dict(s))

                for time, slots in slots_by_time.items():
                    for participant in all_participants:
                        # Проверяем, записан ли участник уже на какой-то курс в это время
                        already = conn.execute("""
                            SELECT 1 FROM mini_course_registrations mcr
                            JOIN time_slots ts2 ON ts2.id = mcr.time_slot_id
                            JOIN mini_courses mc2 ON mc2.id = mcr.mini_course_id
                            WHERE mcr.participant_id = ?
                              AND mc2.date = ?
                              AND ts2.time = ?
                        """, (participant['id'], date, time)).fetchone()
                        if already:
                            continue

                        # Ищем слоты со свободными местами
                        available = []
                        for slot in slots:
                            current = conn.execute(
                                "SELECT COUNT(*) as cnt FROM mini_course_registrations WHERE time_slot_id=?",
                                (slot['id'],)
                            ).fetchone()['cnt']
                            slot_max = slot['max_participants'] if slot['max_participants'] > 0 else slot['course_max']
                            if current < slot_max:
                                available.append(slot)

                        if available:
                            chosen = random.choice(available)
                            conn.execute(
                                "INSERT OR IGNORE INTO mini_course_registrations (mini_course_id, participant_id, time_slot_id) VALUES (?, ?, ?)",
                                (chosen['mc_id'], participant['id'], chosen['id'])
                            )
                            total_distributed += 1

            return total_distributed

    def is_event_registration_open(self, event_id: int) -> bool:
        """Регистрация открыта, если мероприятие опубликовано и не закрыто"""
        with self._get_conn() as conn:
            row = conn.execute("SELECT is_published, is_closed FROM events WHERE id=?", (event_id,)).fetchone()
            if not row:
                return False
            return bool(row['is_published']) and not bool(row['is_closed'])

    def is_mini_course_registration_open(self, mc_id: int) -> bool:
        """Регистрация на мини-курс открыта, если курс опубликован и не закрыт"""
        with self._get_conn() as conn:
            row = conn.execute("SELECT is_published, is_closed FROM mini_courses WHERE id=?", (mc_id,)).fetchone()
            if not row:
                return False
            return bool(row['is_published']) and not bool(row['is_closed'])

    def has_open_registrations(self) -> bool:
        """Есть ли хотя бы одно опубликованное мероприятие или мини-курс с открытой регистрацией"""
        with self._get_conn() as conn:
            ev = conn.execute("SELECT 1 FROM events WHERE is_published=1 AND (is_closed IS NULL OR is_closed=0)").fetchone()
            if ev:
                return True
            mc = conn.execute("SELECT 1 FROM mini_courses WHERE is_published=1 AND (is_closed IS NULL OR is_closed=0)").fetchone()
            return mc is not None

    def close_event_registration_and_distribute(self, event_id: int) -> Dict:
        """Закрыть регистрацию на мероприятие и распределить участников без команды.
        Возвращает словарь с результатами."""
        ev = self.get_event(event_id)
        if not ev:
            return {'event_name': '', 'distributed_count': 0, 'new_assignments': []}

        # Собираем незарегистрированных ДО закрытия
        with self._get_conn() as conn:
            unregistered = [dict(r) for r in conn.execute("""
                SELECT p.id, p.user_id, p.first_name, p.last_name
                FROM participants p
                WHERE p.role='participant'
                  AND p.user_id IS NOT NULL
                  AND p.id NOT IN (
                    SELECT participant_id FROM event_registrations WHERE event_id=?
                  )
            """, (event_id,)).fetchall()]

        # Закрываем
        with self._get_conn() as conn:
            conn.execute("UPDATE events SET is_closed=1 WHERE id=?", (event_id,))

        # Распределяем
        count = self.auto_distribute_participants(event_id)

        # Собираем информацию о новых назначениях
        new_assignments = []
        if count > 0:
            with self._get_conn() as conn:
                for p in unregistered:
                    reg = conn.execute("""
                        SELECT er.captain_id, (p2.last_name || ' ' || p2.first_name) as captain_name,
                               p2.user_id as captain_user_id
                        FROM event_registrations er
                        JOIN participants p2 ON p2.id = er.captain_id
                        WHERE er.event_id=? AND er.participant_id=?
                    """, (event_id, p['id'])).fetchone()
                    if reg:
                        new_assignments.append({
                            'participant_id': p['id'],
                            'user_id': p['user_id'],
                            'first_name': p['first_name'],
                            'last_name': p['last_name'],
                            'captain_name': reg['captain_name']
                        })

        return {
            'event_name': ev['name'],
            'distributed_count': count,
            'new_assignments': new_assignments
        }

    def close_mini_courses_registration_and_distribute(self) -> Dict:
        """Закрыть регистрацию на все опубликованные мини-курсы и распределить
        незаписавшихся участников.
        Возвращает словарь с результатами."""
        with self._get_conn() as conn:
            courses = [dict(r) for r in conn.execute(
                "SELECT * FROM mini_courses WHERE is_published=1 AND (is_closed IS NULL OR is_closed=0)"
            ).fetchall()]

        if not courses:
            return {'courses': [], 'distributed_count': 0, 'new_assignments': []}

        # Собираем незарегистрированных ДО закрытия (на любой из курсов)
        all_mc_ids = [mc['id'] for mc in courses]
        placeholders = ','.join(['?'] * len(all_mc_ids))
        with self._get_conn() as conn:
            unregistered = [dict(r) for r in conn.execute(f"""
                SELECT p.id, p.user_id, p.first_name, p.last_name
                FROM participants p
                WHERE p.role='participant'
                  AND p.user_id IS NOT NULL
                  AND p.id NOT IN (
                    SELECT DISTINCT mcr.participant_id
                    FROM mini_course_registrations mcr
                    WHERE mcr.mini_course_id IN ({placeholders})
                  )
            """, all_mc_ids).fetchall()]

        # Закрываем
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE mini_courses SET is_closed=1 WHERE is_published=1 AND (is_closed IS NULL OR is_closed=0)"
            )

        # Распределяем
        count = self.auto_distribute_all_mini_courses()

        # Собираем информацию о новых назначениях
        new_assignments = []
        if count > 0:
            with self._get_conn() as conn:
                for p in unregistered:
                    regs = [dict(r) for r in conn.execute("""
                        SELECT mcr.mini_course_id, mcr.time_slot_id,
                               mc.name as course_name, ts.time as slot_time
                        FROM mini_course_registrations mcr
                        JOIN mini_courses mc ON mc.id = mcr.mini_course_id
                        JOIN time_slots ts ON ts.id = mcr.time_slot_id
                        WHERE mcr.participant_id=?
                          AND mcr.mini_course_id IN ({placeholders})
                    """.format(placeholders=placeholders), [p['id']] + all_mc_ids).fetchall()]
                    if regs:
                        new_assignments.append({
                            'participant_id': p['id'],
                            'user_id': p['user_id'],
                            'first_name': p['first_name'],
                            'last_name': p['last_name'],
                            'registrations': [{
                                'course_name': r['course_name'],
                                'slot_time': r['slot_time']
                            } for r in regs]
                        })

        return {
            'courses': [{'id': mc['id'], 'name': mc['name']} for mc in courses],
            'distributed_count': count,
            'new_assignments': new_assignments
        }

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
            return conn.execute("INSERT INTO events (name, description, min_team_size, max_team_size, is_fair, is_published) VALUES (?, ?, ?, ?, ?, 0)",
                                (name, desc, min_size, max_size, 1 if is_fair else 0)).lastrowid

    def get_active_events(self) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM events WHERE is_active=1").fetchall()]

    def get_event(self, eid: int) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
            return dict(row) if row else None

    def get_published_events(self) -> List[Dict]:
        """Только открытые для регистрации (опубликованные и не закрытые)"""
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM events WHERE is_active=1 AND is_published=1 AND (is_closed IS NULL OR is_closed=0)").fetchall()]

    def get_unpublished_events(self) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM events WHERE is_active=1 AND (is_published IS NULL OR is_published=0)").fetchall()]

    def publish_events(self) -> List[Dict]:
        with self._get_conn() as conn:
            events = [dict(r) for r in conn.execute("SELECT * FROM events WHERE is_active=1 AND (is_published IS NULL OR is_published=0)").fetchall()]
            conn.execute("UPDATE events SET is_published=1, published_at=datetime('now') WHERE is_active=1 AND (is_published IS NULL OR is_published=0)")
            return events

    def delete_mini_course(self, mc_id: int) -> bool:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM mini_course_registrations WHERE mini_course_id=?", (mc_id,))
            conn.execute("DELETE FROM time_slots WHERE mini_course_id=?", (mc_id,))
            conn.execute("DELETE FROM mini_courses WHERE id=?", (mc_id,))
            return True

    def delete_event(self, event_id: int) -> bool:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM event_registrations WHERE event_id=?", (event_id,))
            conn.execute("DELETE FROM events WHERE id=?", (event_id,))
            return True

    def get_my_event_team(self, event_id: int, user_id: int) -> Optional[List[Dict]]:
        with self._get_conn() as conn:
            p = conn.execute("SELECT id FROM participants WHERE user_id=?", (user_id,)).fetchone()
            if not p:
                return None
            reg = conn.execute("SELECT team_members FROM event_registrations WHERE event_id=? AND participant_id=?", (event_id, p['id'])).fetchone()
            if not reg:
                return None
            members = [m.strip() for m in (reg['team_members'] or '').split(',') if m.strip()]
            if not members:
                return []
            return [dict(r) for r in conn.execute("SELECT first_name, last_name, user_id FROM participants WHERE (last_name || ' ' || first_name) IN ({})".format(','.join(['?']*len(members))), members).fetchall()]

    def leave_event_team(self, event_id: int, user_id: int) -> Tuple[bool, str, Optional[int], bool]:
        """
        Выход участника из команды.
        Возвращает: (success, message, captain_user_id_to_notify, team_was_disbanded)
        """
        with self._get_conn() as conn:
            p = conn.execute("SELECT * FROM participants WHERE user_id=?", (user_id,)).fetchone()
            if not p:
                return False, "Вы не зарегистрированы", None, False

            my_reg = conn.execute(
                "SELECT * FROM event_registrations WHERE event_id=? AND participant_id=?",
                (event_id, p['id'])
            ).fetchone()
            if not my_reg:
                return False, "Вы не состоите в команде", None, False

            ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if not ev:
                return False, "Мероприятие не найдено", None, False

            captain_id = my_reg['captain_id']
            if captain_id == 0:
                return False, "Ошибка: команда не определена", None, False

            is_captain = (p['id'] == captain_id)
            if is_captain:
                # Капитан выходит — вся команда распадается
                # Собираем user_id участников для уведомления (капитан уже уведомлён)
                conn.execute(
                    "DELETE FROM event_registrations WHERE event_id=? AND captain_id=?",
                    (event_id, captain_id)
                )
                return True, "Вы вышли из команды. Команда распущена.", None, True

            # Обычный участник выходит
            # Удаляем его имя из team_members капитана
            captain_reg = conn.execute(
                "SELECT team_members FROM event_registrations WHERE event_id=? AND participant_id=?",
                (event_id, captain_id)
            ).fetchone()
            if captain_reg:
                members = [m.strip() for m in (captain_reg['team_members'] or '').split(',') if m.strip()]
                leaver_name = f"{p['last_name']} {p['first_name']}"
                members = [m for m in members if m != leaver_name]
                conn.execute(
                    "UPDATE event_registrations SET team_members=? WHERE event_id=? AND participant_id=?",
                    (", ".join(members), event_id, captain_id)
                )

            # Удаляем регистрацию участника
            conn.execute(
                "DELETE FROM event_registrations WHERE event_id=? AND participant_id=?",
                (event_id, p['id'])
            )

            # Проверяем, не стало ли в команде меньше min_team_size
            remaining = conn.execute(
                "SELECT COUNT(*) as cnt FROM event_registrations WHERE event_id=? AND captain_id=?",
                (event_id, captain_id)
            ).fetchone()['cnt']

            if remaining < ev['min_team_size']:
                # Команда распадается
                conn.execute(
                    "DELETE FROM event_registrations WHERE event_id=? AND captain_id=?",
                    (event_id, captain_id)
                )
                cap_info = conn.execute(
                    "SELECT user_id FROM participants WHERE id=?", (captain_id,)
                ).fetchone()
                captain_uid = cap_info['user_id'] if cap_info else None
                return True, "Вы вышли из команды. Команда распалась (недостаточно участников).", captain_uid, True

            return True, "Вы вышли из команды.", None, False

    def disband_event_team(self, event_id: int, user_id: int) -> Tuple[bool, str]:
        """Капитан распускает команду"""
        with self._get_conn() as conn:
            p = conn.execute("SELECT id FROM participants WHERE user_id=?", (user_id,)).fetchone()
            if not p:
                return False, "Вы не зарегистрированы"

            my_reg = conn.execute(
                "SELECT * FROM event_registrations WHERE event_id=? AND participant_id=?",
                (event_id, p['id'])
            ).fetchone()
            if not my_reg:
                return False, "Вы не состоите в команде"

            captain_id = my_reg['captain_id']
            if p['id'] != captain_id:
                return False, "Только капитан может распустить команду"

            conn.execute(
                "DELETE FROM event_registrations WHERE event_id=? AND captain_id=?",
                (event_id, captain_id)
            )
            return True, "Команда распущена. Все участники исключены."

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
                    conn.rollback()
                    return False, "Капитан не зарегистрирован", errors

                cap_existing = conn.execute(
                    "SELECT 1 FROM event_registrations WHERE event_id=? AND participant_id=?",
                    (event_id, cap['id'])
                ).fetchone()
                if cap_existing:
                    conn.rollback()
                    return False, "Капитан уже состоит в команде на этом мероприятии", errors

                member_participant_ids = []
                members_names = []
                for vk_uid in selected_user_ids:
                    p = conn.execute("SELECT id, first_name, last_name FROM participants WHERE user_id=?", (vk_uid,)).fetchone()
                    if not p:
                        errors.append(f"Участник с ID {vk_uid} не найден")
                        continue
                    if p['id'] != cap['id']:
                        existing = conn.execute(
                            "SELECT 1 FROM event_registrations WHERE event_id=? AND participant_id=?",
                            (event_id, p['id'])
                        ).fetchone()
                        if existing:
                            errors.append(f"{p['last_name']} {p['first_name']} уже состоит в другой команде")
                            continue
                    member_participant_ids.append(p['id'])
                    members_names.append(f"{p['last_name']} {p['first_name']}")

                if errors:
                    conn.rollback()
                    return False, "Некоторые участники не могут быть добавлены", errors

                conn.execute(
                    "INSERT INTO event_registrations (event_id, participant_id, team_members, captain_id) VALUES (?, ?, ?, ?)",
                    (event_id, cap['id'], ", ".join(members_names), cap['id'])
                )

                for pid in member_participant_ids:
                    if pid != cap['id']:
                        conn.execute(
                            "INSERT INTO event_registrations (event_id, participant_id, team_members, captain_id) VALUES (?, ?, ?, ?)",
                            (event_id, pid, "", cap['id'])
                        )

                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e

        return True, "Команда зарегистрирована!", []

    def register_mini_course_individual(self, mc_id: int, ts_id: int, user_id: int) -> Tuple[bool, str]:
        """Индивидуальная запись участника на мини-курс"""
        mc = self.get_mini_course(mc_id)
        if not mc:
            return False, "Курс не найден"

        slot = self.get_time_slot(ts_id)
        if not slot:
            return False, "Временной слот не найден"

        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                p = conn.execute("SELECT id FROM participants WHERE user_id=?", (user_id,)).fetchone()
                if not p:
                    return False, "Вы не зарегистрированы"

                # Проверяем, не записан ли уже на этот курс (любой слот)
                already = conn.execute(
                    "SELECT 1 FROM mini_course_registrations WHERE mini_course_id=? AND participant_id=?",
                    (mc_id, p['id'])
                ).fetchone()
                if already:
                    return False, "Вы уже записаны на этот курс"

                # Проверяем пересечение по времени: нет ли другого курса в то же время и дату
                conflict = conn.execute("""
                    SELECT 1 FROM mini_course_registrations mcr
                    JOIN time_slots ts2 ON ts2.id = mcr.time_slot_id
                    JOIN mini_courses mc2 ON mc2.id = mcr.mini_course_id
                    WHERE mcr.participant_id = ?
                      AND mc2.date = ?
                      AND ts2.time = ?
                      AND mcr.mini_course_id != ?
                """, (p['id'], mc.get('date', ''), slot['time'], mc_id)).fetchone()
                if conflict:
                    return False, "Вы уже записаны на другой курс в это же время"

                # Определяем максимальное количество мест на слот
                slot_max = slot['max_participants'] if slot['max_participants'] > 0 else mc['max_participants']

                # Проверяем количество мест на слот
                current = conn.execute(
                    "SELECT COUNT(*) as cnt FROM mini_course_registrations WHERE time_slot_id=?",
                    (ts_id,)
                ).fetchone()['cnt']
                if current >= slot_max:
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
        """Возвращает опубликованные и не закрытые мини-курсы с количеством свободных мест"""
        with self._get_conn() as conn:
            courses = [dict(r) for r in conn.execute("SELECT * FROM mini_courses WHERE is_published=1 AND (is_closed IS NULL OR is_closed=0)").fetchall()]
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

    def get_participant_by_id(self, pid: int) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
            return dict(row) if row else None

    def deduct_balance_from_participant(self, participant_id: int, amount: int, description: str = "Списание со счета") -> bool:
        with self._get_conn() as conn:
            row = conn.execute("SELECT balance FROM participants WHERE id=?", (participant_id,)).fetchone()
            if not row or row['balance'] < amount:
                return False
            conn.execute("UPDATE participants SET balance = balance - ? WHERE id=?", (amount, participant_id))
            conn.execute("INSERT INTO balance_history (participant_id, amount, description) VALUES (?, ?, ?)",
                         (participant_id, -amount, description))
            return True

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

    def get_all_participant_user_ids(self) -> List[int]:
        with self._get_conn() as conn:
            return [r['user_id'] for r in conn.execute("SELECT user_id FROM participants WHERE role='participant' AND user_id IS NOT NULL").fetchall()]

    def get_all_admin_user_ids(self) -> List[int]:
        with self._get_conn() as conn:
            return [r['user_id'] for r in conn.execute("SELECT user_id FROM participants WHERE role IN ('admin', 'super_admin') AND user_id IS NOT NULL").fetchall()]

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
                except Exception:
                    res.append({'success': False, 'name': f"{ln} {fn}", 'error': 'Уже есть'})
        return res

    def login_with_personal_code(self, uid: int, pc: str) -> Tuple[bool, str, Optional[Dict]]:
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                pr = conn.execute("SELECT * FROM pre_registered_participants WHERE personal_code=? AND is_used=0", (pc,)).fetchone()
                if not pr:
                    conn.rollback()
                    return False, "Код не найден", None
                ex = conn.execute("SELECT * FROM participants WHERE user_id=?", (uid,)).fetchone()
                if ex:
                    conn.rollback()
                    return False, f"Вы уже {ex['first_name']}", ex
                # Mark code as used first
                cur = conn.execute("UPDATE pre_registered_participants SET is_used=1 WHERE id=?", (pr['id'],))
                if cur.rowcount == 0:
                    conn.rollback()
                    return False, "Код уже использован", None
                conn.execute("INSERT INTO participants (user_id, first_name, last_name, personal_code) VALUES (?, ?, ?, ?)",
                            (uid, pr['first_name'], pr['last_name'], pc))
                p = conn.execute("SELECT * FROM participants WHERE user_id=?", (uid,)).fetchone()
                conn.commit()
                return True, f"Привет, {p['first_name']}!", dict(p)
            except Exception as e:
                conn.rollback()
                raise e

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

    def create_fair_teams_for_event(self, event_id: int, budget: int) -> int:
        """Create fair_teams for all teams in an event, returns count."""
        with self._get_conn() as conn:
            captains = conn.execute("""
                SELECT DISTINCT captain_id FROM event_registrations
                WHERE event_id=? AND captain_id>0
            """, (event_id,)).fetchall()
            count = 0
            for i, row in enumerate(captains):
                team_name = f"Команда {i + 1}"
                conn.execute(
                    "INSERT OR IGNORE INTO fair_teams (event_id, captain_participant_id, team_name, budget) VALUES (?, ?, ?, ?)",
                    (event_id, row['captain_id'], team_name, budget)
                )
                count += 1
            return count

    def get_fair_team_by_user(self, user_id: int, event_id: int) -> Optional[Dict]:
        p = self.get_participant_by_user_id(user_id)
        if not p:
            return None
        with self._get_conn() as conn:
            reg = conn.execute(
                "SELECT captain_id FROM event_registrations WHERE event_id=? AND participant_id=?",
                (event_id, p['id'])
            ).fetchone()
            if not reg:
                return None
            team = conn.execute(
                "SELECT * FROM fair_teams WHERE event_id=? AND captain_participant_id=?",
                (event_id, reg['captain_id'])
            ).fetchone()
            return dict(team) if team else None

    def get_fair_team(self, team_id: int) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM fair_teams WHERE id=?", (team_id,)).fetchone()
            return dict(row) if row else None

    def get_all_fair_teams(self, event_id: int) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM fair_teams WHERE event_id=?", (event_id,)).fetchall()]

    def get_fair_team_members(self, team_id: int) -> List[Dict]:
        team = self.get_fair_team(team_id)
        if not team:
            return []
        with self._get_conn() as conn:
            regs = conn.execute("""
                SELECT p.user_id, p.first_name, p.last_name
                FROM event_registrations er
                JOIN participants p ON p.id = er.participant_id
                WHERE er.event_id=? AND er.captain_id=?
            """, (team['event_id'], team['captain_participant_id'])).fetchall()
            return [dict(r) for r in regs]

    def update_fair_team_name(self, team_id: int, new_name: str):
        with self._get_conn() as conn:
            conn.execute("UPDATE fair_teams SET team_name=? WHERE id=?", (new_name, team_id))

    def get_team_balance(self, team_id: int) -> int:
        with self._get_conn() as conn:
            row = conn.execute("SELECT budget FROM fair_teams WHERE id=?", (team_id,)).fetchone()
            return row['budget'] if row else 0

    def add_team_budget(self, team_id: int, amount: int, description: str = ""):
        with self._get_conn() as conn:
            conn.execute("UPDATE fair_teams SET budget = budget + ? WHERE id=?", (amount, team_id))

    def deduct_team_budget(self, team_id: int, amount: int) -> bool:
        with self._get_conn() as conn:
            row = conn.execute("SELECT budget FROM fair_teams WHERE id=?", (team_id,)).fetchone()
            if not row or row['budget'] < amount:
                return False
            conn.execute("UPDATE fair_teams SET budget = budget - ? WHERE id=?", (amount, team_id))
            return True

    def fine_team_budget(self, team_id: int, amount: int):
        """Apply a fine - deducts even if budget goes negative."""
        with self._get_conn() as conn:
            conn.execute("UPDATE fair_teams SET budget = budget - ? WHERE id=?", (amount, team_id))

    def add_fair_item(self, user_id: int, team_id: int, name: str, price: int, description: str = "") -> int:
        with self._get_conn() as conn:
            team = conn.execute("SELECT team_name FROM fair_teams WHERE id=?", (team_id,)).fetchone()
            team_name = team['team_name'] if team else ""
            return conn.execute(
                "INSERT INTO fair_items (user_id, team_id, team_name, name, price, description) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, team_id, team_name, name, price, description)
            ).lastrowid

    def get_team_items(self, team_id: int) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM fair_items WHERE team_id=? AND is_active=1 ORDER BY created_at", (team_id,)).fetchall()]

    def get_fair_item(self, item_id: int) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM fair_items WHERE id=?", (item_id,)).fetchone()
            return dict(row) if row else None

    def update_fair_item(self, item_id: int, name: str, price: int, description: str = ""):
        with self._get_conn() as conn:
            conn.execute("UPDATE fair_items SET name=?, price=?, description=? WHERE id=?", (name, price, description, item_id))

    def update_fair_item_price(self, item_id: int, price: int):
        with self._get_conn() as conn:
            conn.execute("UPDATE fair_items SET price=? WHERE id=?", (price, item_id))

    def deactivate_fair_item(self, item_id: int):
        with self._get_conn() as conn:
            conn.execute("UPDATE fair_items SET is_active=0 WHERE id=?", (item_id,))

    def get_team_active_items(self, team_id: int) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM fair_items WHERE team_id=? AND is_active=1 ORDER BY created_at", (team_id,)).fetchall()]

    def get_all_active_items_for_event(self, event_id: int) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("""
                SELECT fi.*, ft.team_name, ft.id as team_id
                FROM fair_items fi
                JOIN fair_teams ft ON ft.id = fi.team_id
                WHERE ft.event_id=? AND fi.is_active=1
                ORDER BY ft.team_name, fi.name
            """, (event_id,)).fetchall()]

    def create_fair_transaction(self, item_id: Optional[int], buyer_team_id: int, seller_team_id: int,
                                amount: int, buyer_user_id: int, seller_user_id: int,
                                description: str = "") -> int:
        with self._get_conn() as conn:
            return conn.execute("""
                INSERT INTO fair_transactions (item_id, buyer_team_id, seller_team_id, buyer_user_id, seller_user_id, amount, description, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (item_id, buyer_team_id, seller_team_id, buyer_user_id, seller_user_id, amount, description)).lastrowid

    def approve_fair_transaction(self, transaction_id: int) -> bool:
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                tx = conn.execute("SELECT * FROM fair_transactions WHERE id=? AND status='pending'", (transaction_id,)).fetchone()
                if not tx:
                    conn.rollback()
                    return False
                buyer = conn.execute("SELECT * FROM fair_teams WHERE id=?", (tx['buyer_team_id'],)).fetchone()
                seller = conn.execute("SELECT * FROM fair_teams WHERE id=?", (tx['seller_team_id'],)).fetchone()
                if not buyer or not seller or buyer['budget'] < tx['amount']:
                    conn.rollback()
                    return False
                conn.execute("UPDATE fair_teams SET budget = budget - ? WHERE id=?", (tx['amount'], tx['buyer_team_id']))
                conn.execute("UPDATE fair_teams SET budget = budget + ? WHERE id=?", (tx['amount'], tx['seller_team_id']))
                conn.execute("UPDATE fair_transactions SET status='approved' WHERE id=?", (transaction_id,))
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
        return True

    def reject_fair_transaction(self, transaction_id: int):
        with self._get_conn() as conn:
            conn.execute("UPDATE fair_transactions SET status='rejected' WHERE id=?", (transaction_id,))

    def get_fair_transaction(self, transaction_id: int) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM fair_transactions WHERE id=?", (transaction_id,)).fetchone()
            return dict(row) if row else None

    def get_pending_transactions(self, event_id: int) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("""
                SELECT ftx.*, fi.name as item_name
                FROM fair_transactions ftx
                LEFT JOIN fair_items fi ON fi.id = ftx.item_id
                JOIN fair_teams ft ON ft.id = ftx.buyer_team_id
                WHERE ft.event_id=? AND ftx.status='pending'
                ORDER BY ftx.created_at ASC
            """, (event_id,)).fetchall()]

    def get_team_transactions(self, team_id: int) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("""
                SELECT ftx.*, fi.name as item_name,
                       (SELECT team_name FROM fair_teams WHERE id=ftx.buyer_team_id) as buyer_team_name,
                       (SELECT team_name FROM fair_teams WHERE id=ftx.seller_team_id) as seller_team_name
                FROM fair_transactions ftx
                LEFT JOIN fair_items fi ON fi.id = ftx.item_id
                WHERE ftx.buyer_team_id=? OR ftx.seller_team_id=?
                ORDER BY ftx.created_at DESC
            """, (team_id, team_id)).fetchall()]

    def get_team_income(self, team_id: int) -> int:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM fair_transactions WHERE seller_team_id=? AND status='approved'",
                (team_id,)
            ).fetchone()
            return row[0] if row else 0

    def get_team_expenses(self, team_id: int) -> int:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM fair_transactions WHERE buyer_team_id=? AND status='approved'",
                (team_id,)
            ).fetchone()
            return row[0] if row else 0

    def get_all_transactions_for_event(self, event_id: int) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("""
                SELECT ftx.*, fi.name as item_name,
                       (SELECT team_name FROM fair_teams WHERE id=ftx.buyer_team_id) as buyer_team_name,
                       (SELECT team_name FROM fair_teams WHERE id=ftx.seller_team_id) as seller_team_name
                FROM fair_transactions ftx
                LEFT JOIN fair_items fi ON fi.id = ftx.item_id
                JOIN fair_teams ft ON ft.id = ftx.buyer_team_id
                WHERE ft.event_id=?
                ORDER BY ftx.created_at DESC
            """, (event_id,)).fetchall()]

    def assign_transaction_to_admin(self, transaction_id: int, admin_user_id: int):
        with self._get_conn() as conn:
            conn.execute("UPDATE fair_transactions SET assigned_admin_user_id=? WHERE id=?", (admin_user_id, transaction_id))

    # === Change requests for fair (add/edit item, edit team) ===
    def create_fair_change_request(self, event_id: int, team_id: int, request_type: str,
                                    new_data: str, user_id: int = 0, item_id: int = 0, old_data: str = "") -> int:
        with self._get_conn() as conn:
            return conn.execute("""
                INSERT INTO fair_change_requests (event_id, team_id, request_type, user_id, item_id, old_data, new_data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (event_id, team_id, request_type, user_id, item_id, old_data, new_data)).lastrowid

    def get_fair_change_request(self, request_id: int) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM fair_change_requests WHERE id=?", (request_id,)).fetchone()
            return dict(row) if row else None

    def approve_fair_change_request(self, request_id: int) -> Optional[Dict]:
        req = self.get_fair_change_request(request_id)
        if not req or req['status'] != 'pending':
            return None
        with self._get_conn() as conn:
            conn.execute("UPDATE fair_change_requests SET status='approved' WHERE id=?", (request_id,))
        return req

    def reject_fair_change_request(self, request_id: int):
        with self._get_conn() as conn:
            conn.execute("UPDATE fair_change_requests SET status='rejected' WHERE id=?", (request_id,))

    def assign_change_request_to_admin(self, request_id: int, admin_user_id: int):
        with self._get_conn() as conn:
            conn.execute("UPDATE fair_change_requests SET assigned_admin_user_id=? WHERE id=?", (admin_user_id, request_id))

    def register_fair_admin(self, event_id: int, admin_user_id: int):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO fair_admin_queue (event_id, admin_user_id) VALUES (?, ?)",
                (event_id, admin_user_id)
            )

    def unregister_fair_admin(self, event_id: int, admin_user_id: int):
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM fair_admin_queue WHERE event_id=? AND admin_user_id=?",
                (event_id, admin_user_id)
            )

    def is_fair_admin_registered(self, event_id: int, admin_user_id: int) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM fair_admin_queue WHERE event_id=? AND admin_user_id=?",
                (event_id, admin_user_id)
            ).fetchone()
            return row is not None

    def set_fair_admin_break(self, event_id: int, admin_user_id: int, on_break: bool):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE fair_admin_queue SET is_on_break=? WHERE event_id=? AND admin_user_id=?",
                (1 if on_break else 0, event_id, admin_user_id)
            )

    def get_fair_admins(self, event_id: int) -> List[Dict]:
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM fair_admin_queue WHERE event_id=? ORDER BY id", (event_id,)
            ).fetchall()]

    def get_next_fair_admin(self, event_id: int) -> Optional[int]:
        admins = self.get_fair_admins(event_id)
        available = [a for a in admins if not a['is_on_break']]
        if not available:
            return None
        with self._get_conn() as conn:
            row = conn.execute("SELECT value FROM fair_settings WHERE key='admin_queue_index'").fetchone()
            idx = int(row['value']) if row else 0
            if idx >= len(available):
                idx = 0
            next_admin = available[idx]
            new_idx = (idx + 1) % len(available)
            conn.execute("INSERT OR REPLACE INTO fair_settings (key, value) VALUES ('admin_queue_index', ?)", (str(new_idx),))
            return next_admin['admin_user_id']

    def is_fair_paused(self) -> bool:
        with self._get_conn() as conn:
            row = conn.execute("SELECT value FROM fair_settings WHERE key='is_paused'").fetchone()
            return row and row['value'] == '1'

    def set_fair_paused(self, paused: bool):
        with self._get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO fair_settings (key, value) VALUES ('is_paused', ?)", ('1' if paused else '0'))

    def is_fair_completed(self) -> bool:
        with self._get_conn() as conn:
            row = conn.execute("SELECT value FROM fair_settings WHERE key='is_completed'").fetchone()
            return row and row['value'] == '1'

    def set_fair_completed(self):
        with self._get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO fair_settings (key, value) VALUES ('is_completed', '1')")
            conn.execute("INSERT OR REPLACE INTO fair_settings (key, value) VALUES ('is_active', '0')")

    def get_fair_cooldown(self) -> int:
        with self._get_conn() as conn:
            row = conn.execute("SELECT value FROM fair_settings WHERE key='cooldown_seconds'").fetchone()
            return int(row['value']) if row else 0

    def set_fair_cooldown(self, seconds: int):
        with self._get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO fair_settings (key, value) VALUES ('cooldown_seconds', ?)", (str(seconds),))

    def get_fair_statistics(self, event_id: int) -> Dict:
        with self._get_conn() as conn:
            teams_data = []
            teams = self.get_all_fair_teams(event_id)
            for t in teams:
                income = conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM fair_transactions WHERE seller_team_id=? AND status='approved'",
                    (t['id'],)
                ).fetchone()[0]
                expenses = conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM fair_transactions WHERE buyer_team_id=? AND status='approved'",
                    (t['id'],)
                ).fetchone()[0]
                items_sold = conn.execute(
                    "SELECT COUNT(*) FROM fair_transactions WHERE seller_team_id=? AND status='approved'",
                    (t['id'],)
                ).fetchone()[0]
                items_bought = conn.execute(
                    "SELECT COUNT(*) FROM fair_transactions WHERE buyer_team_id=? AND status='approved'",
                    (t['id'],)
                ).fetchone()[0]
                item_list = [dict(r) for r in conn.execute(
                    "SELECT name, price FROM fair_items WHERE team_id=? AND is_active=1", (t['id'],)
                ).fetchall()]
                teams_data.append({
                    "team_name": t['team_name'],
                    "budget": t['budget'],
                    "income": income,
                    "expenses": expenses,
                    "items_sold": items_sold,
                    "items_bought": items_bought,
                    "items": item_list,
                })
            all_txs = self.get_all_transactions_for_event(event_id)
            top_earners = sorted(teams_data, key=lambda x: x['income'], reverse=True)
            top_richest = sorted(teams_data, key=lambda x: x['budget'], reverse=True)
            return {
                "teams": teams_data,
                "transactions": all_txs,
                "top_earners": top_earners,
                "top_richest": top_richest,
            }

    def get_fair_team_by_user_id(self, user_id: int) -> Optional[Dict]:
        """Find which fair team a user belongs to (uses active event)."""
        event_id = self.get_active_fair_event_id()
        if not event_id:
            return None
        return self.get_fair_team_by_user(user_id, event_id)

    def get_participant_event_registrations(self, participant_id: int) -> List[Dict]:
        """Все регистрации участника на мероприятия"""
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("""
                SELECT er.* FROM event_registrations er
                WHERE er.participant_id = ?
            """, (participant_id,)).fetchall()]

    def get_participant_mini_course_registrations(self, participant_id: int) -> List[Dict]:
        """Все регистрации участника на мини-курсы"""
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("""
                SELECT mcr.* FROM mini_course_registrations mcr
                WHERE mcr.participant_id = ?
            """, (participant_id,)).fetchall()]

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

    def publish_mini_courses(self) -> List[Dict]:
        with self._get_conn() as conn:
            courses = [dict(r) for r in conn.execute("SELECT * FROM mini_courses WHERE is_published=0").fetchall()]
            conn.execute("UPDATE mini_courses SET is_published=1, published_at=datetime('now') WHERE is_published=0")
            return courses

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
        """Только открытые для регистрации (опубликованные и не закрытые)"""
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM mini_courses WHERE is_published=1 AND (is_closed IS NULL OR is_closed=0)").fetchall()]

    def get_last_closed_event(self) -> Optional[Dict]:
        """Последнее закрытое мероприятие (для админа)"""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM events
                WHERE is_closed=1
                ORDER BY id DESC LIMIT 1
            """).fetchone()
            return dict(row) if row else None

    def get_last_closed_mini_courses(self) -> List[Dict]:
        """Последние закрытые мини-курсы (для админа, по убыванию id)"""
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("""
                SELECT * FROM mini_courses
                WHERE is_closed=1
                ORDER BY id DESC LIMIT 10
            """).fetchall()]

    def get_open_or_last_closed_events(self) -> List[Dict]:
        """Открытые мероприятия + последнее закрытое (для админа)"""
        with self._get_conn() as conn:
            open_evs = [dict(r) for r in conn.execute(
                "SELECT * FROM events WHERE is_published=1 AND (is_closed IS NULL OR is_closed=0) ORDER BY id DESC"
            ).fetchall()]
            last_closed = conn.execute(
                "SELECT * FROM events WHERE is_closed=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if last_closed:
                open_evs.append(dict(last_closed))
            return open_evs

    def get_open_or_last_closed_mini_courses(self) -> List[Dict]:
        """Открытые мини-курсы + последние закрытые (для админа)"""
        with self._get_conn() as conn:
            open_mcs = [dict(r) for r in conn.execute(
                "SELECT * FROM mini_courses WHERE is_published=1 AND (is_closed IS NULL OR is_closed=0) ORDER BY id DESC"
            ).fetchall()]
            last_closed = [dict(r) for r in conn.execute(
                "SELECT * FROM mini_courses WHERE is_closed=1 ORDER BY id DESC LIMIT 10"
            ).fetchall()]
            closed_ids = {mc['id'] for mc in open_mcs}
            for mc in last_closed:
                if mc['id'] not in closed_ids:
                    open_mcs.append(mc)
            return open_mcs

    def admin_register_participant_for_event(self, event_id: int, participant_user_id: int) -> Tuple[bool, str, Optional[int]]:
        """Админ принудительно регистрирует участника на мероприятие.
        Возвращает (success, message, captain_user_id)"""
        with self._get_conn() as conn:
            p = conn.execute("SELECT id, last_name, first_name FROM participants WHERE user_id=?", (participant_user_id,)).fetchone()
            if not p:
                return False, "Участник не найден", None

            existing = conn.execute(
                "SELECT 1 FROM event_registrations WHERE event_id=? AND participant_id=?",
                (event_id, p['id'])
            ).fetchone()
            if existing:
                return False, "Участник уже зарегистрирован на это мероприятие", None

            ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if not ev:
                return False, "Мероприятие не найдено", None

            # Ищем команды с местом
            captains = conn.execute("""
                SELECT DISTINCT er.participant_id, er.team_members
                FROM event_registrations er
                WHERE er.event_id=? AND er.captain_id = er.participant_id
            """, (event_id,)).fetchall()

            if not captains:
                # Нет команд — создаём новую с этим участником как капитаном
                conn.execute(
                    "INSERT INTO event_registrations (event_id, participant_id, team_members, captain_id) VALUES (?, ?, '', ?)",
                    (event_id, p['id'], p['id'])
                )
                return True, f"{p['last_name']} {p['first_name']} назначен капитаном новой команды", participant_user_id

            best = None
            best_size = float('inf')
            for cap in captains:
                members = [m.strip() for m in (cap['team_members'] or '').split(',') if m.strip()]
                if len(members) < ev['max_team_size'] and len(members) < best_size:
                    best = cap
                    best_size = len(members)

            if not best:
                return False, "Нет свободных мест в командах", None

            # Добавляем в лучшую команду
            conn.execute(
                "INSERT INTO event_registrations (event_id, participant_id, team_members, captain_id) VALUES (?, ?, '', ?)",
                (event_id, p['id'], best['participant_id'])
            )

            cap_row = conn.execute(
                "SELECT team_members FROM event_registrations WHERE event_id=? AND participant_id=?",
                (event_id, best['participant_id'])
            ).fetchone()
            old = (cap_row['team_members'] or '').strip()
            new_members = (f"{old}, {p['last_name']} {p['first_name']}").lstrip(', ')
            conn.execute(
                "UPDATE event_registrations SET team_members=? WHERE event_id=? AND participant_id=?",
                (new_members, event_id, best['participant_id'])
            )

            cap_info = conn.execute(
                "SELECT user_id FROM participants WHERE id=?", (best['participant_id'],)
            ).fetchone()
            captain_uid = cap_info['user_id'] if cap_info else None

            return True, f"{p['last_name']} {p['first_name']} добавлен в команду", captain_uid

    def admin_register_participant_for_mini_course(self, mc_id: int, ts_id: int, participant_user_id: int) -> Tuple[bool, str]:
        """Админ принудительно записывает участника на мини-курс"""
        p = self.get_participant_by_user_id(participant_user_id)
        if not p:
            return False, "Участник не найден"

        mc = self.get_mini_course(mc_id)
        if not mc:
            return False, "Курс не найден"

        slot = self.get_time_slot(ts_id)
        if not slot:
            return False, "Временной слот не найден"

        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                already = conn.execute(
                    "SELECT 1 FROM mini_course_registrations WHERE mini_course_id=? AND participant_id=?",
                    (mc_id, p['id'])
                ).fetchone()
                if already:
                    return False, "Участник уже записан на этот курс"

                slot_max = slot['max_participants'] if slot['max_participants'] > 0 else mc['max_participants']
                current = conn.execute(
                    "SELECT COUNT(*) as cnt FROM mini_course_registrations WHERE time_slot_id=?",
                    (ts_id,)
                ).fetchone()['cnt']
                if current >= slot_max:
                    return False, "Нет свободных мест на этот временной слот"

                conn.execute(
                    "INSERT INTO mini_course_registrations (mini_course_id, participant_id, time_slot_id) VALUES (?, ?, ?)",
                    (mc_id, p['id'], ts_id)
                )
                conn.commit()
                return True, f"{p['last_name']} {p['first_name']} записан на '{mc['name']}' ({slot['time']})"
            except Exception as e:
                conn.rollback()
                raise e

    def admin_unregister_participant_from_event(self, event_id: int, participant_user_id: int) -> Tuple[bool, str]:
        """Админ удаляет участника с мероприятия"""
        with self._get_conn() as conn:
            p = conn.execute("SELECT id, last_name, first_name FROM participants WHERE user_id=?", (participant_user_id,)).fetchone()
            if not p:
                return False, "Участник не найден"

            my_reg = conn.execute(
                "SELECT * FROM event_registrations WHERE event_id=? AND participant_id=?",
                (event_id, p['id'])
            ).fetchone()
            if not my_reg:
                return False, "Участник не состоит в команде на этом мероприятии"

            captain_id = my_reg['captain_id']
            if captain_id == p['id']:
                # Капитан — удаляем всю команду
                conn.execute(
                    "DELETE FROM event_registrations WHERE event_id=? AND captain_id=?",
                    (event_id, captain_id)
                )
                return True, f"Команда капитана {p['last_name']} {p['first_name']} распущена"
            else:
                # Обычный участник — убираем из team_members капитана
                cap_reg = conn.execute(
                    "SELECT team_members FROM event_registrations WHERE event_id=? AND participant_id=?",
                    (event_id, captain_id)
                ).fetchone()
                if cap_reg:
                    members = [m.strip() for m in (cap_reg['team_members'] or '').split(',') if m.strip()]
                    leaver_name = f"{p['last_name']} {p['first_name']}"
                    members = [m for m in members if m != leaver_name]
                    conn.execute(
                        "UPDATE event_registrations SET team_members=? WHERE event_id=? AND participant_id=?",
                        (", ".join(members), event_id, captain_id)
                    )
                conn.execute(
                    "DELETE FROM event_registrations WHERE event_id=? AND participant_id=?",
                    (event_id, p['id'])
                )
                return True, f"{p['last_name']} {p['first_name']} удалён с мероприятия"

    def admin_unregister_participant_from_mini_course(self, mc_id: int, participant_user_id: int) -> Tuple[bool, str]:
        """Админ удаляет участника с мини-курса"""
        p = self.get_participant_by_user_id(participant_user_id)
        if not p:
            return False, "Участник не найден"

        mc = self.get_mini_course(mc_id)
        if not mc:
            return False, "Курс не найден"

        with self._get_conn() as conn:
            reg = conn.execute(
                "SELECT 1 FROM mini_course_registrations WHERE mini_course_id=? AND participant_id=?",
                (mc_id, p['id'])
            ).fetchone()
            if not reg:
                return False, "Участник не записан на этот курс"

            conn.execute(
                "DELETE FROM mini_course_registrations WHERE mini_course_id=? AND participant_id=?",
                (mc_id, p['id'])
            )
            return True, f"{p['last_name']} {p['first_name']} удалён с курса '{mc['name']}'"

    def get_all_participants_with_registrations(self, event_id: int) -> List[Dict]:
        """Все участники мероприятия с информацией о команде (для админа)"""
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute("""
                SELECT p.id, p.user_id, p.first_name, p.last_name,
                       er.captain_id,
                       CASE WHEN er.captain_id = er.participant_id THEN 1 ELSE 0 END as is_captain,
                       (SELECT (p3.last_name || ' ' || p3.first_name) FROM participants p3 WHERE p3.id = er.captain_id) as captain_name,
                       (SELECT (p3.last_name || ' ' || p3.first_name) FROM participants p3 WHERE p3.id = er.captain_id) as team_name,
                       er.team_members
                FROM participants p
                LEFT JOIN event_registrations er ON er.event_id=? AND er.participant_id = p.id
                WHERE p.role='participant' AND p.user_id IS NOT NULL
                ORDER BY p.last_name
            """, (event_id,)).fetchall()]

    def get_all_participants_with_mini_course_regs(self, mc_ids: List[int]) -> List[Dict]:
        """Все участники с информацией о регистрации на указанные мини-курсы (для админа)"""
        if not mc_ids:
            return []
        placeholders = ','.join(['?'] * len(mc_ids))
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute(f"""
                SELECT p.id, p.user_id, p.first_name, p.last_name,
                       mcr.mini_course_id, mcr.time_slot_id,
                       mc.name as course_name, ts.time as slot_time
                FROM participants p
                LEFT JOIN mini_course_registrations mcr ON mcr.participant_id = p.id AND mcr.mini_course_id IN ({placeholders})
                LEFT JOIN mini_courses mc ON mc.id = mcr.mini_course_id
                LEFT JOIN time_slots ts ON ts.id = mcr.time_slot_id
                WHERE p.role='participant' AND p.user_id IS NOT NULL
                ORDER BY p.last_name
            """, mc_ids).fetchall()]

    def create_mini_course(self, name: str, desc: str, max_p: int, date: str = '') -> int:
        with self._get_conn() as conn:
            return conn.execute("INSERT INTO mini_courses (name, description, max_participants, date) VALUES (?, ?, ?, ?)", (name, desc, max_p, date)).lastrowid

    def add_time_slot(self, mc_id: int, time: str, max_participants: int = 0):
        with self._get_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO time_slots (mini_course_id, time, max_participants) VALUES (?, ?, ?)", (mc_id, time, max_participants))
