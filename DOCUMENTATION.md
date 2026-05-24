# Bot for Camp - Comprehensive Documentation

## 📁 Project Structure

```
bot_for_camp/
├── config.py          # Configuration management
├── database.py        # Database operations and models
├── bot_logic.py       # Main bot logic and state management
├── main.py            # Entry point and server integration
├── vk_server.py       # VK API integration (from bot_for_fair)
├── requirements.txt    # Python dependencies
├── DOCUMENTATION.md   # This file
└── README.md           # Project overview
```

## 📋 File Documentation

### `config.py`

**Purpose**: Manages application configuration and environment variables

**Key Functions**:

| Function | Parameters | Returns | Description |
|----------|------------|---------|-------------|
| `validate()` | None | `List[str]` | Validates configuration, returns list of errors |
| `load_env()` | None | None | Loads environment variables from .env file |

**Configuration Variables**:
- `VK_BOT_TOKEN`: VK API access token
- `VK_GROUP_ID`: VK group ID
- `INITIAL_SUPER_ADMIN`: Initial super admin user ID
- `ADMIN_IDS`: List of admin user IDs
- `DB_PATH`: Database file path
- `LP_VERSION`: VK API version

---

### `database.py`

**Purpose**: Database operations using SQLite with comprehensive data models

**Database Tables**:
- `settings`: Key-value configuration storage
- `participants`: User data with roles and personal codes
- `pre_registered_participants`: Pre-registered participants
- `events`: Event information
- `event_registrations`: Team registrations for events
- `mini_courses`: Mini-course information
- `time_slots`: Time slots for mini-courses
- `mini_course_registrations`: Participant registrations for mini-courses
- `balance_history`: Participant balance transactions
- `complaints`: User complaints system

**Key Functions**:

| Function | Parameters | Returns | Description |
|----------|------------|---------|-------------|
| `generate_personal_code()` | None | `str` | Generates unique personal code (format: a123bc) |
| `register_participant(user_id, first_name, last_name)` | `int, str, str` | `Tuple[bool, str, Optional[str]]` | Registers new participant |
| `get_participant_by_user_id(user_id)` | `int` | `Optional[Dict[str, Any]]` | Gets participant by VK user ID |
| `get_participant_by_personal_code(personal_code)` | `str` | `Optional[Dict[str, Any]]` | Gets participant by personal code |
| `update_balance(participant_id, amount, description, created_by)` | `int, int, str, Optional[int]` | `bool` | Updates participant balance |
| `create_event(name, description, team_size)` | `str, str, int` | `int` | Creates new event |
| `register_team_for_event(event_id, participant_id, team_members)` | `int, int, List[str]` | `Tuple[bool, str]` | Registers team for event |
| `create_mini_course(name, description, max_participants)` | `str, str, int` | `int` | Creates new mini-course |
| `register_for_mini_course(mini_course_id, participant_id, time_slot_id)` | `int, int, int` | `Tuple[bool, str]` | Registers participant for mini-course |
| `pre_register_participants(names_list)` | `List[str]` | `List[Dict[str, Any]]` | Pre-registers participants from name list |
| `login_with_personal_code(user_id, personal_code)` | `int, str` | `Tuple[bool, str, Optional[Dict[str, Any]]]` | Logs in participant using personal code |
| `add_admin_by_vk_tag(super_admin_id, vk_tag, role)` | `int, str, str` | `Tuple[bool, str]` | Adds admin by VK tag (@username or user ID) |
| `get_event_registration_details(event_id)` | `int` | `List[Dict[str, Any]]` | Gets detailed event registration info |
| `get_mini_course_registration_details(mini_course_id)` | `int` | `List[Dict[str, Any]]` | Gets detailed mini-course registration info |

---

### `bot_logic.py`

**Purpose**: Main bot logic with state management and command handling

**States**:
- `MAIN`: Default state
- `WAIT_PERSONAL_CODE`: Waiting for personal code input
- `WAIT_ADMIN_VK_TAG`: Waiting for admin VK tag input
- `WAIT_PARTICIPANT_NAMES`: Waiting for participant names input
- `WAIT_EVENT_NAME`: Waiting for event name input
- `WAIT_EVENT_DESCRIPTION`: Waiting for event description input
- `WAIT_EVENT_TEAM_SIZE`: Waiting for event team size input
- `WAIT_EVENT_TEAM_MEMBERS`: Waiting for event team members input
- `WAIT_MINI_COURSE_NAME`: Waiting for mini-course name input
- `WAIT_MINI_COURSE_DESCRIPTION`: Waiting for mini-course description input
- `WAIT_MINI_COURSE_MAX_PARTICIPANTS`: Waiting for mini-course max participants input
- `WAIT_MINI_COURSE_TIME_SLOTS`: Waiting for mini-course time slots input
- `WAIT_BALANCE_AMOUNT`: Waiting for balance amount input
- `WAIT_BALANCE_DESCRIPTION`: Waiting for balance description input
- `WAIT_COMPLAINT_MESSAGE`: Waiting for complaint message input

**Key Functions**:

| Function | Parameters | Returns | Description |
|----------|------------|---------|-------------|
| `handle_message(user_id, text, payload, user_info)` | `int, str, Optional[Any], Optional[Dict]` | `None` | Main message handler |
| `handle_callback(user_id, payload)` | `int, Dict[str, Any]` | `None` | Callback button handler |
| `_main_kb(role, participant)` | `str, Optional[Dict]` | `Dict[str, Any]` | Generates main keyboard |
| `_reply_kb(rows, one_time)` | `List[List[Dict]], bool` | `Dict[str, Any]` | Generates reply keyboard |
| `_inline_kb(buttons)` | `List[Dict[str, Any]]` | `Dict[str, Any]` | Generates inline keyboard |
| `_view_event_teams(user_id, role)` | `int, str` | `None` | Shows event teams to admin |
| `_view_mini_course_participants(user_id, role)` | `int, str` | `None` | Shows mini-course participants to admin |

**Command Aliases**:

| Text Command | Internal Command | Description |
|--------------|------------------|-------------|
| "войти" | "login" | Login command |
| "отмена" | "cancel" | Cancel current operation |
| "назад" | "back" | Go back to previous menu |
| "баланс" | "balance" | Check balance |
| "мероприятия" | "events" | Events menu |
| "мини-курсы" | "mini_courses" | Mini-courses menu |
| "жалоба" | "complaint" | File complaint |
| "красная кнопка" | "complaint" | File complaint (red button) |
| "настройки" | "settings" | Admin settings |
| "участники" | "participants" | Participants management |
| "предварительная регистрация" | "pre_register" | Pre-register participants |
| "посмотреть зарегистрированных" | "view_participants" | View pre-registered participants |
| "добавить админа" | "add_admin" | Add new admin |
| "создать мероприятие" | "create_event" | Create new event |
| "посмотреть активные мероприятия" | "view_events" | View active events |
| "создать мини-курс" | "create_mini_course" | Create new mini-course |
| "опубликовать мини-курсы" | "publish_mini_courses" | Publish mini-courses |
| "посмотреть неопубликованные" | "view_unpublished" | View unpublished mini-courses |
| "посмотреть опубликованные" | "view_published" | View published mini-courses |
| "посмотреть команды на мероприятиях" | "view_event_teams" | View event teams |
| "посмотреть участников мини-курсов" | "view_mini_course_participants" | View mini-course participants |

---

### `main.py`

**Purpose**: Entry point and server integration

**Key Functions**:

| Function | Parameters | Returns | Description |
|----------|------------|---------|-------------|
| `setup_logging(level)` | `str` | `None` | Sets up logging configuration |
| `main()` | None | `None` | Main application entry point |

**Workflow**:
1. Load configuration and validate
2. Initialize database
3. Initialize VK server
4. Set up message callback
5. Initialize bot logic
6. Start polling for messages

---

### `vk_server.py`

**Purpose**: VK API integration and long polling server

**Key Functions**:

| Function | Parameters | Returns | Description |
|----------|------------|---------|-------------|
| `send_message(user_id, text, keyboard)` | `int, str, Optional[Dict]` | `None` | Sends message to user |
| `start_polling(message_handler, callback_handler)` | `Callable, Callable` | `None` | Starts long polling |

---

## 🎯 User Roles and Permissions

| Role | Permissions |
|------|-------------|
| **unregistered** | Can only login using personal code |
| **participant** | Can check balance, register for events/mini-courses, file complaints |
| **admin** | Can manage participants, create events/mini-courses, view registrations, handle complaints |
| **super_admin** | All admin permissions + can add/remove admins |

---

## 🔄 Workflow Examples

### Participant Registration Flow
1. Admin pre-registers participants: "предварительная регистрация" → enters names
2. System generates personal codes (format: a123bc)
3. Participant logs in: "войти" → enters personal code
4. Participant can now access all features

### Event Creation Flow
1. Admin: "мероприятия" → "создать мероприятие"
2. Enter event name, description, team size
3. Event is created and available for registration
4. Participants: "мероприятия" → select event → enter team members
5. Admin can view teams: "посмотреть команды на мероприятиях"

### Mini-Course Flow
1. Admin: "мини-курсы" → "создать мини-курс"
2. Enter mini-course name, description, max participants, time slots
3. Admin: "опубликовать мини-курсы" to make available
4. Participants: "мини-курсы" → select course → select time slot
5. Admin can view participants: "посмотреть участников мини-курсов"

---

## 📊 Database Schema

```sql
-- Participants
CREATE TABLE participants (
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

-- Pre-registered participants
CREATE TABLE pre_registered_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    personal_code TEXT UNIQUE NOT NULL,
    is_used BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Events
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    team_size INTEGER NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Event registrations
CREATE TABLE event_registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER REFERENCES events(id),
    participant_id INTEGER REFERENCES participants(id),
    team_members TEXT NOT NULL,
    registration_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_frozen BOOLEAN DEFAULT 0,
    UNIQUE(event_id, participant_id)
);

-- Mini-courses
CREATE TABLE mini_courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    max_participants INTEGER NOT NULL,
    is_published BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Time slots
CREATE TABLE time_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mini_course_id INTEGER REFERENCES mini_courses(id),
    time TEXT NOT NULL,
    UNIQUE(mini_course_id, time)
);

-- Mini-course registrations
CREATE TABLE mini_course_registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mini_course_id INTEGER REFERENCES mini_courses(id),
    participant_id INTEGER REFERENCES participants(id),
    time_slot_id INTEGER REFERENCES time_slots(id),
    registration_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_frozen BOOLEAN DEFAULT 0,
    UNIQUE(mini_course_id, participant_id)
);
```

---

## 🚀 Deployment

1. Install dependencies: `pip install -r requirements.txt`
2. Create `.env` file with configuration
3. Run bot: `python main.py`

## 📝 Notes

- Personal codes are generated in format: 1 letter + 3 digits + 2 letters (e.g., a123bc)
- All database operations use transactions with proper error handling
- The system supports both @username and numeric user IDs for admin addition
- Comprehensive logging is implemented throughout the application