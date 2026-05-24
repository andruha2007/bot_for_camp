import logging
import random
import sqlite3
import string
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta

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
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.error("DB transaction failed: %s", exc, exc_info=True)
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE,
                    first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL,
                    personal_code TEXT UNIQUE NOT NULL,
                    role TEXT CHECK(role IN ('admin', 'super_admin', 'participant')) DEFAULT 'participant',
                    balance INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS pre_registered_participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL,
                    personal_code TEXT UNIQUE NOT NULL,
                    is_used BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    team_size INTEGER NOT NULL,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS event_registrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER REFERENCES events(id),
                    participant_id INTEGER REFERENCES participants(id),
                    team_members TEXT NOT NULL,
                    registration_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_frozen BOOLEAN DEFAULT 0,
                    UNIQUE(event_id, participant_id)
                );

                CREATE TABLE IF NOT EXISTS mini_courses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    max_participants INTEGER NOT NULL,
                    is_published BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS time_slots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mini_course_id INTEGER REFERENCES mini_courses(id),
                    time TEXT NOT NULL,
                    UNIQUE(mini_course_id, time)
                );

                CREATE TABLE IF NOT EXISTS mini_course_registrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mini_course_id INTEGER REFERENCES mini_courses(id),
                    participant_id INTEGER REFERENCES participants(id),
                    time_slot_id INTEGER REFERENCES time_slots(id),
                    registration_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_frozen BOOLEAN DEFAULT 0,
                    UNIQUE(mini_course_id, participant_id)
                );

                CREATE TABLE IF NOT EXISTS balance_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    participant_id INTEGER REFERENCES participants(id),
                    amount INTEGER NOT NULL,
                    description TEXT,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS complaints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    participant_id INTEGER REFERENCES participants(id),
                    message TEXT NOT NULL,
                    is_resolved BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                INSERT OR IGNORE INTO settings (key, value) VALUES
                    ('registration_freeze_time', '2'),
                    ('event_team_size', '5');
                """
            )
            self._migrate(conn)
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_participants_user_id ON participants(user_id);
                CREATE INDEX IF NOT EXISTS idx_participants_personal_code ON participants(personal_code);
                CREATE INDEX IF NOT EXISTS idx_event_registrations_event ON event_registrations(event_id);
                CREATE INDEX IF NOT EXISTS idx_mini_course_registrations_course ON mini_course_registrations(mini_course_id);
                CREATE INDEX IF NOT EXISTS idx_balance_history_participant ON balance_history(participant_id);
                """
            )

    def _migrate(self, conn: sqlite3.Connection):
        def columns(table: str) -> set[str]:
            return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}

    def generate_personal_code(self) -> str:
        """Generate a unique personal code in format: 1 letter, 3 digits, 2 letters (e.g., a123bc)"""
        while True:
            letter1 = random.choice(string.ascii_lowercase)
            digits = ''.join(random.choices(string.digits, k=3))
            letters = ''.join(random.choices(string.ascii_lowercase, k=2))
            code = f"{letter1}{digits}{letters}"

            with self._get_conn() as conn:
                existing = conn.execute("SELECT 1 FROM participants WHERE personal_code=?", (code,)).fetchone()
                if not existing:
                    return code

    def register_participant(self, user_id: int, first_name: str, last_name: str) -> Tuple[bool, str, Optional[str]]:
        """Register a new participant with a unique personal code"""
        if not first_name or not last_name:
            return False, "Имя и фамилия обязательны", None

        personal_code = self.generate_personal_code()

        with self._get_conn() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO participants (user_id, first_name, last_name, personal_code, role)
                    VALUES (?, ?, ?, ?, 'participant')
                    """,
                    (user_id, first_name.strip(), last_name.strip(), personal_code)
                )
                return True, f"Вы успешно зарегистрированы! Ваш персональный код: {personal_code}", personal_code
            except sqlite3.IntegrityError:
                # User already exists
                existing = conn.execute("SELECT personal_code FROM participants WHERE user_id=?", (user_id,)).fetchone()
                if existing:
                    return True, f"Вы уже зарегистрированы. Ваш персональный код: {existing['personal_code']}", existing['personal_code']
                return False, "Ошибка регистрации. Пожалуйста, попробуйте позже.", None

    def get_participant_by_user_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM participants WHERE user_id=?", (user_id,)).fetchone()
            return dict(row) if row else None

    def get_participant_by_personal_code(self, personal_code: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM participants WHERE personal_code=?", (personal_code,)).fetchone()
            return dict(row) if row else None

    def get_participant_by_name(self, first_name: str, last_name: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM participants WHERE first_name=? AND last_name=?",
                (first_name.strip(), last_name.strip())
            ).fetchone()
            return dict(row) if row else None

    def get_participant_balance(self, participant_id: int) -> int:
        with self._get_conn() as conn:
            row = conn.execute("SELECT balance FROM participants WHERE id=?", (participant_id,)).fetchone()
            return row['balance'] if row else 0

    def get_balance_history(self, participant_id: int) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM balance_history WHERE participant_id=? ORDER BY created_at DESC",
                    (participant_id,)
                ).fetchall()
            ]

    def update_balance(self, participant_id: int, amount: int, description: str, created_by: Optional[int] = None) -> bool:
        with self._get_conn() as conn:
            # Update participant balance
            conn.execute(
                "UPDATE participants SET balance = balance + ? WHERE id=?",
                (amount, participant_id)
            )

            # Add to history
            conn.execute(
                """
                INSERT INTO balance_history (participant_id, amount, description, created_by)
                VALUES (?, ?, ?, ?)
                """,
                (participant_id, amount, description, created_by)
            )
            return True

    def create_event(self, name: str, description: str, team_size: int) -> int:
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO events (name, description, team_size, is_active) VALUES (?, ?, ?, 1)",
                (name.strip(), description.strip(), team_size)
            )
            return cur.lastrowid

    def get_active_events(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM events WHERE is_active=1 ORDER BY created_at DESC"
                ).fetchall()
            ]

    def get_event(self, event_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            return dict(row) if row else None

    def register_team_for_event(self, event_id: int, participant_id: int, team_members: List[str]) -> Tuple[bool, str]:
        """Register a team for an event"""
        event = self.get_event(event_id)
        if not event:
            return False, "Мероприятие не найдено"

        participant = self.get_participant_by_user_id(participant_id)
        if not participant:
            return False, "Участник не найден"

        # Validate team members format and existence
        parsed_members = []
        for member in team_members:
            parts = member.strip().split()
            if len(parts) < 2:
                return False, f"Некорректный формат участника: {member}. Используйте 'Фамилия Имя'"

            first_name = ' '.join(parts[1:])  # Handle multiple first names
            last_name = parts[0]

            # Check if participant exists
            existing_participant = self.get_participant_by_name(first_name, last_name)
            if not existing_participant:
                return False, f"Участник не найден: {last_name} {first_name}"

            # Check if participant is already registered for this event
            with self._get_conn() as conn:
                existing_registration = conn.execute(
                    """
                    SELECT 1 FROM event_registrations er
                    JOIN participants p ON p.id = er.participant_id
                    WHERE er.event_id=? AND p.id=?
                    """,
                    (event_id, existing_participant['id'])
                ).fetchone()

                if existing_registration:
                    return False, f"Участник уже зарегистрирован на это мероприятие: {last_name} {first_name}"

                parsed_members.append(f"{last_name} {first_name}")

        # Check team size
        if len(parsed_members) != event['team_size']:
            return False, f"Количество участников должно быть ровно {event['team_size']}"

        with self._get_conn() as conn:
            # Check if this participant already registered a team for this event
            existing_registration = conn.execute(
                "SELECT 1 FROM event_registrations WHERE event_id=? AND participant_id=?",
                (event_id, participant_id)
            ).fetchone()

            if existing_registration:
                return False, "Вы уже зарегистрировали команду на это мероприятие"

            # Register the team
            conn.execute(
                """
                INSERT INTO event_registrations (event_id, participant_id, team_members)
                VALUES (?, ?, ?)
                """,
                (event_id, participant_id, ", ".join(parsed_members))
            )
            return True, "Команда успешно зарегистрирована на мероприятие!"

    def get_event_registrations(self, event_id: int) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT er.*, p.first_name, p.last_name, p.personal_code
                    FROM event_registrations er
                    JOIN participants p ON p.id = er.participant_id
                    WHERE er.event_id=?
                    ORDER BY er.registration_time
                    """,
                    (event_id,)
                ).fetchall()
            ]

    def get_event_registration_details(self, event_id: int) -> List[Dict[str, Any]]:
        """Get detailed event registration information including team members"""
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT er.*, p.first_name, p.last_name, p.personal_code, er.team_members
                    FROM event_registrations er
                    JOIN participants p ON p.id = er.participant_id
                    WHERE er.event_id=?
                    ORDER BY er.registration_time
                    """,
                    (event_id,)
                ).fetchall()
            ]

    def get_mini_course_registration_details(self, mini_course_id: int) -> List[Dict[str, Any]]:
        """Get detailed mini-course registration information"""
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT mcr.*, p.first_name, p.last_name, p.personal_code, ts.time
                    FROM mini_course_registrations mcr
                    JOIN participants p ON p.id = mcr.participant_id
                    JOIN time_slots ts ON ts.id = mcr.time_slot_id
                    WHERE mcr.mini_course_id=?
                    ORDER BY ts.time, p.last_name, p.first_name
                    """,
                    (mini_course_id,)
                ).fetchall()
            ]

    def create_mini_course(self, name: str, description: str, max_participants: int) -> int:
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO mini_courses (name, description, max_participants, is_published) VALUES (?, ?, ?, 0)",
                (name.strip(), description.strip(), max_participants)
            )
            return cur.lastrowid

    def add_time_slot(self, mini_course_id: int, time: str) -> bool:
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO time_slots (mini_course_id, time) VALUES (?, ?)",
                    (mini_course_id, time.strip())
                )
                return True
            except sqlite3.IntegrityError:
                return False  # Time slot already exists

    def get_mini_course(self, mini_course_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM mini_courses WHERE id=?", (mini_course_id,)).fetchone()
            return dict(row) if row else None

    def get_time_slots(self, mini_course_id: int) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM time_slots WHERE mini_course_id=? ORDER BY time",
                    (mini_course_id,)
                ).fetchall()
            ]

    def get_unpublished_mini_courses(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM mini_courses WHERE is_published=0 ORDER BY created_at DESC"
                ).fetchall()
            ]

    def get_published_mini_courses(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM mini_courses WHERE is_published=1 ORDER BY created_at DESC"
                ).fetchall()
            ]

    def publish_mini_courses(self) -> int:
        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE mini_courses SET is_published=1 WHERE is_published=0"
            )
            return cur.rowcount

    def register_for_mini_course(self, mini_course_id: int, participant_id: int, time_slot_id: int) -> Tuple[bool, str]:
        """Register a participant for a mini course"""
        mini_course = self.get_mini_course(mini_course_id)
        if not mini_course:
            return False, "Мини-курс не найден"

        if not mini_course['is_published']:
            return False, "Этот мини-курс еще не опубликован"

        participant = self.get_participant_by_user_id(participant_id)
        if not participant:
            return False, "Участник не найден"

        time_slot = self.get_time_slot(time_slot_id)
        if not time_slot or time_slot['mini_course_id'] != mini_course_id:
            return False, "Слот времени не найден"

        with self._get_conn() as conn:
            # Check if participant already registered for this mini course
            existing = conn.execute(
                "SELECT 1 FROM mini_course_registrations WHERE mini_course_id=? AND participant_id=?",
                (mini_course_id, participant_id)
            ).fetchone()

            if existing:
                return False, "Вы уже зарегистрированы на этот мини-курс"

            # Check if mini course is full
            current_count = conn.execute(
                "SELECT COUNT(*) FROM mini_course_registrations WHERE mini_course_id=? AND time_slot_id=?",
                (mini_course_id, time_slot_id)
            ).fetchone()[0]

            if current_count >= mini_course['max_participants']:
                return False, "На этот мини-курс в выбранное время нет свободных мест"

            # Register participant
            conn.execute(
                """
                INSERT INTO mini_course_registrations (mini_course_id, participant_id, time_slot_id)
                VALUES (?, ?, ?)
                """,
                (mini_course_id, participant_id, time_slot_id)
            )
            return True, "Вы успешно зарегистрированы на мини-курс!"

    def get_time_slot(self, time_slot_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM time_slots WHERE id=?", (time_slot_id,)).fetchone()
            return dict(row) if row else None

    def get_mini_course_registrations(self, mini_course_id: int, time_slot_id: Optional[int] = None) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            query = """
                SELECT mcr.*, p.first_name, p.last_name, ts.time
                FROM mini_course_registrations mcr
                JOIN participants p ON p.id = mcr.participant_id
                JOIN time_slots ts ON ts.id = mcr.time_slot_id
                WHERE mcr.mini_course_id=?
            """
            params = [mini_course_id]

            if time_slot_id:
                query += " AND mcr.time_slot_id=?"
                params.append(time_slot_id)

            query += " ORDER BY mcr.registration_time"

            return [
                dict(row)
                for row in conn.execute(query, params).fetchall()
            ]

    def freeze_registrations(self, event_id: Optional[int] = None, mini_course_id: Optional[int] = None) -> int:
        """Freeze registrations for events or mini courses"""
        with self._get_conn() as conn:
            if event_id:
                cur = conn.execute(
                    "UPDATE event_registrations SET is_frozen=1 WHERE event_id=? AND is_frozen=0",
                    (event_id,)
                )
                return cur.rowcount
            elif mini_course_id:
                cur = conn.execute(
                    "UPDATE mini_course_registrations SET is_frozen=1 WHERE mini_course_id=? AND is_frozen=0",
                    (mini_course_id,)
                )
                return cur.rowcount
            return 0

    def submit_complaint(self, participant_id: int, message: str) -> bool:
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO complaints (participant_id, message)
                VALUES (?, ?)
                """,
                (participant_id, message.strip())
            )
            return True

    def get_recent_complaints(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT c.*, p.first_name, p.last_name, p.personal_code
                    FROM complaints c
                    JOIN participants p ON p.id = c.participant_id
                    WHERE c.is_resolved=0
                    ORDER BY c.created_at DESC
                    LIMIT ?
                    """,
                    (limit,)
                ).fetchall()
            ]

    def resolve_complaint(self, complaint_id: int) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE complaints SET is_resolved=1 WHERE id=?",
                (complaint_id,)
            )
            return cur.rowcount > 0

    def get_participant_events(self, participant_id: int) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT e.id, e.name, e.description, er.team_members, er.registration_time
                    FROM event_registrations er
                    JOIN events e ON e.id = er.event_id
                    WHERE er.participant_id=?
                    ORDER BY er.registration_time DESC
                    """,
                    (participant_id,)
                ).fetchall()
            ]

    def get_participant_mini_courses(self, participant_id: int) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT mc.id, mc.name, mc.description, ts.time, mcr.registration_time
                    FROM mini_course_registrations mcr
                    JOIN mini_courses mc ON mc.id = mcr.mini_course_id
                    JOIN time_slots ts ON ts.id = mcr.time_slot_id
                    WHERE mcr.participant_id=?
                    ORDER BY mcr.registration_time DESC
                    """,
                    (participant_id,)
                ).fetchall()
            ]

    def get_setting(self, key: str, default: str = "") -> str:
        with self._get_conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self._get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))

    def get_admins(self) -> List[int]:
        with self._get_conn() as conn:
            return [
                row["user_id"]
                for row in conn.execute("SELECT user_id FROM participants WHERE role IN ('admin', 'super_admin') AND user_id IS NOT NULL ORDER BY user_id").fetchall()
            ]

    def set_admin_role(self, user_id: int, role: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE participants SET role=? WHERE user_id=?",
                (role, user_id)
            )
            return cur.rowcount > 0

    def get_participant_info(self, participant_id: int) -> Optional[Dict[str, Any]]:
        participant = self.get_participant_by_user_id(participant_id)
        if not participant:
            return None

        info = dict(participant)
        info['balance_history'] = self.get_balance_history(participant_id)
        info['events'] = self.get_participant_events(participant_id)
        info['mini_courses'] = self.get_participant_mini_courses(participant_id)
        return info

    def pre_register_participants(self, names_list: List[str]) -> List[Dict[str, Any]]:
        """Pre-register participants from a list of names (format: LastName FirstName)"""
        results = []
        with self._get_conn() as conn:
            for name_item in names_list:
                parts = name_item.strip().split()
                if len(parts) < 2:
                    results.append({
                        'success': False,
                        'name': name_item,
                        'error': 'Некорректный формат. Используйте "Фамилия Имя"'
                    })
                    continue

                last_name = parts[0]
                first_name = ' '.join(parts[1:])

                # Generate unique personal code
                while True:
                    personal_code = self.generate_personal_code()
                    existing = conn.execute("SELECT 1 FROM pre_registered_participants WHERE personal_code=?", (personal_code,)).fetchone()
                    if not existing:
                        break

                try:
                    conn.execute(
                        """
                        INSERT INTO pre_registered_participants (first_name, last_name, personal_code)
                        VALUES (?, ?, ?)
                        """,
                        (first_name.strip(), last_name.strip(), personal_code)
                    )
                    results.append({
                        'success': True,
                        'name': f"{last_name} {first_name}",
                        'personal_code': personal_code
                    })
                except sqlite3.IntegrityError:
                    results.append({
                        'success': False,
                        'name': f"{last_name} {first_name}",
                        'error': 'Участник с такими данными уже существует'
                    })

        return results

    def add_single_participant(self, first_name: str, last_name: str) -> Tuple[bool, str, Optional[str]]:
        """Add a single participant to pre-registered list"""
        if not first_name or not last_name:
            return False, "Имя и фамилия обязательны", None

        # Generate unique personal code
        personal_code = self.generate_personal_code()

        with self._get_conn() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO pre_registered_participants (first_name, last_name, personal_code)
                    VALUES (?, ?, ?)
                    """,
                    (first_name.strip(), last_name.strip(), personal_code)
                )
                return True, f"Участник {last_name} {first_name} добавлен с кодом: {personal_code}", personal_code
            except sqlite3.IntegrityError:
                return False, f"Участник {last_name} {first_name} уже существует", None

    def remove_pre_registered_participant(self, personal_code: str) -> Tuple[bool, str]:
        """Remove a participant from pre-registered list by personal code"""
        with self._get_conn() as conn:
            # Check if participant exists and is not used
            participant = conn.execute(
                "SELECT * FROM pre_registered_participants WHERE personal_code=?",
                (personal_code,)
            ).fetchone()

            if not participant:
                return False, "Участник с таким персональным кодом не найден"

            if participant['is_used']:
                return False, "Нельзя удалить участника, который уже использовал свой код для регистрации"

            conn.execute(
                "DELETE FROM pre_registered_participants WHERE personal_code=?",
                (personal_code,)
            )
            return True, f"Участник {participant['last_name']} {participant['first_name']} удален"

    def get_pre_registered_participants(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM pre_registered_participants ORDER BY last_name, first_name"
                ).fetchall()
            ]

    def get_unused_pre_registered_participants(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM pre_registered_participants WHERE is_used=0 ORDER BY last_name, first_name"
                ).fetchall()
            ]

    def login_with_personal_code(self, user_id: int, personal_code: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Login participant using their personal code"""
        with self._get_conn() as conn:
            # Check if this personal code exists in pre-registered participants
            pre_reg = conn.execute(
                "SELECT * FROM pre_registered_participants WHERE personal_code=? AND is_used=0",
                (personal_code,)
            ).fetchone()

            if not pre_reg:
                return False, "Персональный код не найден или уже использован", None

            # Check if this user is already registered
            existing_participant = conn.execute(
                "SELECT * FROM participants WHERE user_id=?",
                (user_id,)
            ).fetchone()

            if existing_participant:
                return False, f"Вы уже зарегистрированы как {existing_participant['first_name']} {existing_participant['last_name']}", existing_participant

            # Register the participant
            conn.execute(
                """
                INSERT INTO participants (user_id, first_name, last_name, personal_code, role)
                VALUES (?, ?, ?, ?, 'participant')
                """,
                (user_id, pre_reg['first_name'], pre_reg['last_name'], personal_code)
            )

            # Mark the pre-registered code as used
            conn.execute(
                "UPDATE pre_registered_participants SET is_used=1 WHERE id=?",
                (pre_reg['id'],)
            )

            # Get the newly registered participant
            participant = conn.execute(
                "SELECT * FROM participants WHERE user_id=?",
                (user_id,)
            ).fetchone()

            return True, f"Добро пожаловать, {participant['first_name']} {participant['last_name']}!", dict(participant)

    def add_admin_by_vk_tag(self, super_admin_id: int, vk_tag: str, role: str = 'admin') -> Tuple[bool, str]:
        """Add admin by VK tag (e.g., @username or user ID)"""
        if role not in ('admin', 'super_admin'):
            return False, "Некорректная роль"

        # Check if super admin exists
        super_admin = self.get_participant_by_user_id(super_admin_id)
        if not super_admin or super_admin.get('role') != 'super_admin':
            return False, "Только суперадмин может добавлять админов"

        # Clean the vk_tag - remove @ symbol if present
        vk_tag = vk_tag.strip()
        if vk_tag.startswith('@'):
            vk_tag = vk_tag[1:]

        # Check if it's a numeric user ID
        if vk_tag.isdigit():
            admin_user_id = int(vk_tag)
        else:
            # For non-numeric tags (usernames), we'll use a placeholder ID
            # In a real implementation, we would resolve the username to user ID via VK API
            admin_user_id = -1  # Placeholder for username-based admins

        # Check if this user is already registered
        existing_participant = self.get_participant_by_user_id(admin_user_id) if admin_user_id > 0 else None
        if existing_participant:
            # Update role if needed
            if existing_participant.get('role') != role:
                self.set_admin_role(admin_user_id, role)
            return True, f"Пользователь {vk_tag} уже зарегистрирован и назначен {role}"

        # For new admins, we need their name. In a real implementation, we would get this from VK API
        # For now, we'll use a placeholder
        with self._get_conn() as conn:
            # Generate a personal code for the admin
            personal_code = self.generate_personal_code()

            conn.execute(
                """
                INSERT INTO participants (user_id, first_name, last_name, personal_code, role)
                VALUES (?, ?, ?, ?, ?)
                """,
                (admin_user_id, "Admin", "User", personal_code, role)
            )

        return True, f"Пользователь {vk_tag} назначен {role}"

    def get_pre_registered_by_code(self, personal_code: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM pre_registered_participants WHERE personal_code=?",
                (personal_code,)
            ).fetchone()
            return dict(row) if row else None
