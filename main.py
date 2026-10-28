import asyncio
import os
import sqlite3
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from openai import AsyncOpenAI
# ==================================================
# 1. CONFIGURATION
# ==================================================
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError(
        "TELEGRAM_BOT_TOKEN не найден в файле .env"
    )
MODEL_NAME = "qwen2.5:7b"
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
ai = AsyncOpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama"
)
# ==================================================
# 2. DATABASE
# ==================================================
db = sqlite3.connect("memory.db", timeout=30)
cursor = db.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    role TEXT,
    content TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    language TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    fact TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    task TEXT NOT NULL,
    is_completed INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    remind_at REAL NOT NULL,
    is_sent INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
# Migration for older users table schemas.
cursor.execute("PRAGMA table_info(users)")
columns = {row[1] for row in cursor.fetchall()}
if "username" not in columns:
    cursor.execute(
        "ALTER TABLE users ADD COLUMN username TEXT"
    )
if "first_name" not in columns:
    cursor.execute(
        "ALTER TABLE users ADD COLUMN first_name TEXT"
    )
if "language" not in columns:
    cursor.execute(
        "ALTER TABLE users ADD COLUMN language TEXT"
    )
if "created_at" not in columns:
    cursor.execute(
        "ALTER TABLE users ADD COLUMN created_at TIMESTAMP"
    )
if "name" in columns:
    cursor.execute("""
        UPDATE users
        SET first_name = name
        WHERE first_name IS NULL
    """)
cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id, id)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_facts_user_id ON facts(user_id, id)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id, is_completed, id)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(is_sent, remind_at)")
db.commit()
# ==================================================
# 3. USER PROFILE
# ==================================================
def save_user(user_id, username, first_name, language):
    cursor.execute(
        """
        INSERT INTO users (
            user_id,
            username,
            first_name,
            language
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            language = excluded.language
        """,
        (user_id, username, first_name, language)
    )
    db.commit()
def get_user(user_id):
    cursor.execute(
        """
        SELECT
            user_id,
            username,
            first_name,
            language,
            created_at
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )
    return cursor.fetchone()
def get_profile_context(user_id):
    user = get_user(user_id)
    if not user:
        return ""
    (
        _user_id,
        username,
        first_name,
        language,
        _created_at
    ) = user
    username_text = (
        f"@{username}" if username else "не установлен"
    )
    first_name_text = (
        first_name if first_name else "не указано"
    )
    return f"""
Профиль пользователя Telegram:
Имя: {first_name_text}
Username: {username_text}
Язык Telegram: {language}
Если пользователь спрашивает своё имя,
используй имя из Telegram-профиля.
Не выдумывай дополнительные сведения.
"""
# ==================================================
# 4. CHAT HISTORY
# ==================================================
def save_message(user_id, role, content):
    cursor.execute(
        """
        INSERT INTO messages (user_id, role, content)
        VALUES (?, ?, ?)
        """,
        (user_id, role, content)
    )
    db.commit()
def get_history(user_id):
    cursor.execute(
        """
        SELECT role, content
        FROM messages
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 10
        """,
        (user_id,)
    )
    rows = cursor.fetchall()
    rows.reverse()
    return [
        {"role": role, "content": content}
        for role, content in rows
    ]
def clear_history(user_id):
    cursor.execute(
        """
        DELETE FROM messages
        WHERE user_id = ?
        """,
        (user_id,)
    )
    db.commit()
# ==================================================
# 5. LONG-TERM MEMORY
# ==================================================
def save_fact(user_id, fact):
    cursor.execute(
        """
        SELECT id
        FROM facts
        WHERE user_id = ?
          AND LOWER(TRIM(fact)) = LOWER(TRIM(?))
        LIMIT 1
        """,
        (user_id, fact)
    )
    if cursor.fetchone():
        return False
    cursor.execute(
        """
        INSERT INTO facts (user_id, fact)
        VALUES (?, ?)
        """,
        (user_id, fact)
    )
    db.commit()
    return True
def update_fact(user_id, old_fact, new_fact):
    cursor.execute(
        """
        UPDATE facts
        SET fact = ?
        WHERE user_id = ?
          AND LOWER(TRIM(fact)) = LOWER(TRIM(?))
        """,
        (new_fact, user_id, old_fact)
    )
    db.commit()
    return cursor.rowcount > 0
def get_facts(user_id):
    cursor.execute(
        """
        SELECT fact
        FROM facts
        WHERE user_id = ?
        ORDER BY id ASC
        """,
        (user_id,)
    )
    return [row[0] for row in cursor.fetchall()]
def clear_facts(user_id):
    cursor.execute(
        "DELETE FROM facts WHERE user_id = ?",
        (user_id,)
    )
    db.commit()
def get_memory_context(user_id):
    facts = get_facts(user_id)
    if not facts:
        return "Долгосрочная память пока пуста."
    facts_text = "\n".join(
        f"- {fact}" for fact in facts
    )
    return f"""
Долгосрочная память о пользователе:
{facts_text}
Используй эти сведения, если они относятся к вопросу.
Не выдумывай факты.
"""
# ==================================================
# 6. TASK MANAGEMENT
# ==================================================
def add_task(user_id, task):
    cursor.execute(
        """
        INSERT INTO tasks (user_id, task)
        VALUES (?, ?)
        """,
        (user_id, task)
    )
    db.commit()
    return cursor.lastrowid
def get_tasks(user_id, include_completed=True):
    if include_completed:
        cursor.execute(
            """
            SELECT id, task, is_completed, created_at
            FROM tasks
            WHERE user_id = ?
            ORDER BY is_completed ASC, id DESC
            """,
            (user_id,)
        )
    else:
        cursor.execute(
            """
            SELECT id, task, is_completed, created_at
            FROM tasks
            WHERE user_id = ?
              AND is_completed = 0
            ORDER BY id DESC
            """,
            (user_id,)
        )
    return cursor.fetchall()
def complete_task(user_id, task_id):
    cursor.execute(
        """
        UPDATE tasks
        SET is_completed = 1
        WHERE id = ?
          AND user_id = ?
          AND is_completed = 0
        """,
        (task_id, user_id)
    )
    db.commit()
    return cursor.rowcount > 0
def delete_task(user_id, task_id):
    cursor.execute(
        """
        DELETE FROM tasks
        WHERE id = ?
          AND user_id = ?
        """,
        (task_id, user_id)
    )
    db.commit()
    return cursor.rowcount > 0
# ==================================================
# REMINDER STORAGE
# ==================================================
def add_reminder(user_id, text, remind_at):
    cursor.execute(
        """
        INSERT INTO reminders (user_id, text, remind_at)
        VALUES (?, ?, ?)
        """,
        (user_id, text, remind_at.timestamp())
    )
    db.commit()
    return cursor.lastrowid
def get_reminders(user_id):
    cursor.execute(
        """
        SELECT id, text, remind_at, is_sent
        FROM reminders
        WHERE user_id = ? AND is_sent = 0
        ORDER BY remind_at ASC
        """,
        (user_id,)
    )
    return cursor.fetchall()
def delete_reminder(user_id, reminder_id):
    cursor.execute(
        "DELETE FROM reminders WHERE id = ? AND user_id = ? AND is_sent = 0",
        (reminder_id, user_id)
    )
    db.commit()
    return cursor.rowcount > 0
async def reminder_worker():
    """Отправляет наступившие напоминания; просроченные остаются в базе до отправки."""
    while True:
        try:
            cursor.execute(
                """
                SELECT id, user_id, text, remind_at
                FROM reminders
                WHERE is_sent = 0 AND remind_at <= ?
                ORDER BY remind_at ASC
                """,
                (time.time(),)
            )
            due_reminders = cursor.fetchall()
            for reminder_id, user_id, reminder_text, remind_at in due_reminders:
                try:
                    await bot.send_message(
                        user_id,
                        "Напоминание!\n\n" + reminder_text
                    )
                    cursor.execute(
                        "UPDATE reminders SET is_sent = 1 WHERE id = ? AND user_id = ?",
                        (reminder_id, user_id)
                    )
                    db.commit()
                    print(f"Напоминание #{reminder_id} отправлено.")
                except Exception as error:
                    print(f"Не удалось отправить напоминание #{reminder_id}: {error}")
        except Exception as error:
            print(f"Ошибка фоновой проверки напоминаний: {error}")
        await asyncio.sleep(5)
# ==================================================
# 7. AI SYSTEM PROMPT
# ==================================================
SYSTEM_PROMPT = """
Ты — AI-ассистент внутри Telegram-бота.
ЯЗЫК:
Определи язык последнего сообщения пользователя
и отвечай на этом языке.
Если пользователь пишет по-русски,
отвечай по-русски.
Если пользователь пишет по-английски,
отвечай по-английски.
Не используй китайские, японские или корейские
символы, если пользователь не просит об этом.
Отвечай естественно, грамотно и понятно.
ТЕБЕ ДОСТУПНЫ:
1. Telegram-профиль пользователя.
2. Долгосрочная память.
3. Недавняя история переписки.
Используй эту информацию для персонализации.
Не выдумывай факты о пользователе.
Если информация неизвестна, честно сообщи об этом.
Перед отправкой проверь язык и естественность ответа.
"""
def contains_chinese(text):
    for char in text:
        code = ord(char)
        if (
            0x4E00 <= code <= 0x9FFF
            or 0x3400 <= code <= 0x4DBF
            or 0x20000 <= code <= 0x2A6DF
        ):
            return True
    return False
# ==================================================
# 8. MEMORY EXTRACTION
# ==================================================
async def extract_memory_action(user_id, user_message):
    current_facts = get_facts(user_id)
    if current_facts:
        facts_text = "\n".join(
            f"- {fact}" for fact in current_facts
        )
    else:
        facts_text = "Память пока пуста."
    response = await ai.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {
                "role": "system",
                "content": f"""
Ты — модуль долгосрочной памяти.
Текущая память:
{facts_text}
Определи, нужно ли изменить память.
NEW: новый устойчивый факт о пользователе.
Формат: NEW: конкретный факт
UPDATE: пользователь явно изменил существующий факт.
Формат: UPDATE: СТАРЫЙ ФАКТ => НОВЫЙ ФАКТ
NOTHING: ничего сохранять не нужно.
Примеры:
Пользователь: "Я изучаю Python."
Ответ: NEW: Пользователь изучает Python
Если в памяти есть факт:
"Пользователь изучает Python"
Пользователь: "Я больше не изучаю Python, теперь я изучаю Go."
Ответ: UPDATE: Пользователь изучает Python => Пользователь изучает Go
Не сохраняй приветствия, вопросы, временные действия
и случайные события.
Сохраняй только устойчивые факты о пользователе.
Не создавай категории вроде "интересы" или "цели".
Если нет уверенности, отвечай NOTHING.
Верни только одну строку на русском языке.
"""
            },
            {
                "role": "user",
                "content": user_message
            }
        ],
        temperature=0
    )
    result = (
        response.choices[0].message.content or ""
    ).strip()
    return result
# ==================================================
# 9. /start AND /help
# ==================================================
COMMANDS_TEXT = (
    "Команды:\n"
    "/start — запустить бота\n"
    "/help — список команд\n"
    "/profile — твой профиль\n"
    "/memory — долгосрочная память\n"
    "/clear — очистить историю чата\n"
    "/clear_memory — очистить память\n"
    "/tasks — список задач\n"
    "/addtask Текст — добавить задачу\n"
    "/done НОМЕР — выполнить задачу\n"
    "/deltask НОМЕР — удалить задачу\n"
    "/remind 2 Текст — напомнить через N минут\n"
    "/remind ГГГГ-ММ-ДД ЧЧ:ММ Текст — напомнить в дату и время\n"
    "/reminders — список напоминаний\n"
    "/delreminder НОМЕР — удалить напоминание"
)


@dp.message(Command("start"))
async def start_command(message: Message):
    user = message.from_user
    first_name = user.first_name or "Пользователь"
    save_user(
        user.id,
        user.username,
        first_name,
        user.language_code or "unknown"
    )
    await message.answer(
        f"Привет, {first_name}!\n\n"
        "Я AI-ассистент с памятью, задачами и напоминаниями.\n\n"
        + COMMANDS_TEXT
    )


@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(COMMANDS_TEXT)

# ==================================================
# 10. /profile
# ==================================================
@dp.message(Command("profile"))
async def profile_command(message: Message):
    user = message.from_user
    save_user(
        user.id,
        user.username,
        user.first_name or "Пользователь",
        user.language_code or "unknown"
    )
    profile = get_user(user.id)
    if not profile:
        await message.answer("Профиль не найден.")
        return
    (
        user_id,
        username,
        first_name,
        language,
        created_at
    ) = profile
    username_text = (
        f"@{username}" if username else "не установлен"
    )
    await message.answer(
        "Профиль:\n\n"
        f"Имя: {first_name or 'не указано'}\n"
        f"Username: {username_text}\n"
        f"Язык: {language or 'неизвестен'}\n"
        f"Дата создания записи: {created_at or 'неизвестна'}"
    )
# ==================================================
# 11. /memory
# ==================================================
@dp.message(Command("memory"))
async def memory_command(message: Message):
    facts = get_facts(message.from_user.id)
    if not facts:
        await message.answer(
            "Я пока ничего не запомнил о тебе."
        )
        return
    lines = ["Что я помню о тебе:\n"]
    for fact in facts:
        lines.append(f"• {fact}")
    await message.answer("\n".join(lines))
# ==================================================
# 12. /clear
# ==================================================
@dp.message(Command("clear"))
async def clear_command(message: Message):
    clear_history(message.from_user.id)
    await message.answer(
        "История переписки очищена.\n"
        "Профиль, задачи и долгосрочная память сохранены."
    )
# ==================================================
# 13. /clear_memory
# ==================================================
@dp.message(Command("clear_memory"))
async def clear_memory_command(message: Message):
    clear_facts(message.from_user.id)
    await message.answer(
        "Долгосрочная память очищена.\n"
        "Профиль, задачи и история сохранены."
    )
# ==================================================
# 14. /tasks
# ==================================================
@dp.message(Command("tasks"))
async def tasks_command(message: Message):
    tasks = get_tasks(message.from_user.id)
    if not tasks:
        await message.answer(
            "Список задач пуст.\n\n"
            "Добавить задачу:\n"
            "/addtask Изучить Python"
        )
        return
    lines = ["Твои задачи:\n"]
    for task_id, task, is_completed, created_at in tasks:
        status = "✅" if is_completed else "⬜"
        lines.append(f"{status} {task_id}. {task}")
    lines.extend([
        "",
        "Отметить выполненной: /done НОМЕР",
        "Удалить задачу: /deltask НОМЕР"
    ])
    await message.answer("\n".join(lines))
# ==================================================
# 15. /addtask
# ==================================================
@dp.message(Command("addtask"))
async def addtask_command(message: Message):
    task_text = (
        (message.text or "").partition(" ")[2].strip()
    )
    if not task_text:
        await message.answer(
            "Укажи описание задачи.\n"
            "Пример: /addtask Изучить FastAPI"
        )
        return
    if any(line.lstrip().startswith("/") for line in task_text.splitlines()[1:]):
        await message.answer(
            "Похоже, ты вставил несколько команд одним сообщением.\n"
            "Отправляй каждую команду отдельным сообщением.\n"
            "Задача не создана."
        )
        return
    if len(task_text) > 500:
        await message.answer(
            "Описание слишком длинное. "
            "Максимум — 500 символов."
        )
        return
    task_id = add_task(
        message.from_user.id,
        task_text
    )
    await message.answer(
        "Задача добавлена.\n\n"
        f"№ {task_id}: {task_text}\n\n"
        "Список задач: /tasks"
    )
# ==================================================
# 16. /done
# ==================================================
@dp.message(Command("done"))
async def done_command(message: Message):
    argument = (
        (message.text or "").partition(" ")[2].strip()
    )
    if not argument.isdigit():
        await message.answer(
            "Укажи номер задачи.\n"
            "Пример: /done 3"
        )
        return
    task_id = int(argument)
    success = complete_task(
        message.from_user.id,
        task_id
    )
    if success:
        await message.answer(
            f"Задача № {task_id} отмечена выполненной."
        )
    else:
        await message.answer(
            "Невыполненная задача с таким номером не найдена."
        )
# ==================================================
# 17. /deltask
# ==================================================
@dp.message(Command("deltask"))
async def deltask_command(message: Message):
    argument = (
        (message.text or "").partition(" ")[2].strip()
    )
    if not argument.isdigit():
        await message.answer(
            "Укажи номер задачи.\n"
            "Пример: /deltask 3"
        )
        return
    task_id = int(argument)
    success = delete_task(
        message.from_user.id,
        task_id
    )
    if success:
        await message.answer(
            f"Задача № {task_id} удалена."
        )
    else:
        await message.answer(
            "Задача с таким номером не найдена."
        )
# ==================================================
# REMINDER COMMANDS
# ==================================================
@dp.message(Command("remind"))
async def remind_command(message: Message):
    argument = (message.text or "").partition(" ")[2].strip()
    if not argument:
        await message.answer(
            "Укажи время и текст напоминания.\n\n"
            "Через несколько минут: /remind 2 Проверить бота\n"
            "В конкретное время: /remind 2026-12-31 18:00 Изучить FastAPI"
        )
        return
    parts = argument.split(maxsplit=2)
    try:
        if len(parts) >= 2 and parts[0].isdigit():
            minutes = int(parts[0])
            reminder_text = " ".join(parts[1:]).strip()
            if minutes < 1 or minutes > 525600:
                raise ValueError
            remind_at = datetime.now().astimezone() + timedelta(minutes=minutes)
        elif len(parts) == 3:
            date_text, time_text, reminder_text = parts
            remind_at = datetime.strptime(
                f"{date_text} {time_text}", "%Y-%m-%d %H:%M"
            ).astimezone()
            if remind_at <= datetime.now().astimezone():
                await message.answer("Это время уже прошло. Укажи будущее время.")
                return
        else:
            raise ValueError
    except ValueError:
        await message.answer(
            "Не удалось разобрать команду. Используй один из форматов:\n"
            "/remind 2 Проверить бота\n"
            "/remind 2026-12-31 18:00 Изучить FastAPI"
        )
        return
    if any(line.lstrip().startswith("/") for line in reminder_text.splitlines()[1:]):
        await message.answer(
            "Похоже, ты вставил несколько команд одним сообщением.\n"
            "Отправляй каждую команду отдельным сообщением.\n"
            "Напоминание не создано."
        )
        return
    if not reminder_text.strip():
        await message.answer("Добавь текст напоминания после времени.")
        return
    if len(reminder_text) > 500:
        await message.answer("Текст слишком длинный. Максимум — 500 символов.")
        return
    reminder_id = add_reminder(message.from_user.id, reminder_text, remind_at)
    await message.answer(
        f"Напоминание № {reminder_id} сохранено.\n"
        f"Когда: {remind_at:%Y-%m-%d %H:%M %Z}\n"
        f"Текст: {reminder_text}\n\n"
        "Посмотреть напоминания: /reminders"
    )
@dp.message(Command("reminders"))
async def reminders_command(message: Message):
    reminders = get_reminders(message.from_user.id)
    if not reminders:
        await message.answer("Активных напоминаний нет.\nПример: /remind 2 Проверить бота")
        return
    lines = ["Твои активные напоминания:\n"]
    for reminder_id, reminder_text, remind_at, is_sent in reminders:
        due = datetime.fromtimestamp(remind_at).astimezone()
        lines.append(f"⏰ {reminder_id}. {due:%Y-%m-%d %H:%M} — {reminder_text}")
    lines.extend(["", "Удалить: /delreminder НОМЕР"])
    await message.answer("\n".join(lines))
@dp.message(Command("delreminder"))
async def delreminder_command(message: Message):
    argument = (message.text or "").partition(" ")[2].strip()
    if not argument.isdigit():
        await message.answer("Укажи номер напоминания. Пример: /delreminder 3")
        return
    reminder_id = int(argument)
    if delete_reminder(message.from_user.id, reminder_id):
        await message.answer(f"Напоминание № {reminder_id} удалено.")
    else:
        await message.answer("Активное напоминание с таким номером не найдено.")
# ==================================================
# 18. GENERAL AI MESSAGE HANDLER
# ==================================================
@dp.message()
async def handle_message(message: Message):
    if not message.text:
        return
    user = message.from_user
    user_id = user.id
    user_message = message.text
    try:
        save_user(
            user_id,
            user.username,
            user.first_name or "Пользователь",
            user.language_code or "unknown"
        )
        save_message(
            user_id,
            "user",
            user_message
        )
        history = get_history(user_id)
        profile_context = get_profile_context(user_id)
        memory_context = get_memory_context(user_id)
        response = await ai.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "system",
                    "content": profile_context
                },
                {
                    "role": "system",
                    "content": memory_context
                },
                *history
            ],
            temperature=0.2
        )
        answer = (
            response.choices[0].message.content or ""
        ).strip()
        if not answer:
            answer = "Модель вернула пустой ответ. Попробуй повторить сообщение."
        # Retry if the answer contains Chinese characters.
        if contains_chinese(answer):
            retry_response = await ai.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Перепиши ответ только на русском языке. "
                            "Не используй китайские, японские или "
                            "корейские символы. Сохрани смысл. "
                            "Верни только исправленный ответ."
                        )
                    },
                    {
                        "role": "user",
                        "content": answer
                    }
                ],
                temperature=0
            )
            answer = (
                retry_response.choices[0].message.content or ""
            ).strip()
        if contains_chinese(answer):
            answer = (
                "Я не смог корректно сформировать ответ. "
                "Попробуй задать вопрос ещё раз."
            )
        # Memory is useful, but a memory-model failure must not discard
        # an otherwise successful chat response.
        try:
            memory_action = await extract_memory_action(
                user_id,
                user_message
            )
            if memory_action.startswith("NEW:"):
                fact = memory_action[4:].strip()
                if fact:
                    save_fact(user_id, fact)
            elif memory_action.startswith("UPDATE:"):
                update_text = memory_action[7:].strip()
                if "=>" in update_text:
                    old_fact, new_fact = update_text.split("=>", 1)
                    old_fact = old_fact.strip()
                    new_fact = new_fact.strip()
                    if old_fact and new_fact:
                        updated = update_fact(user_id, old_fact, new_fact)
                        if not updated:
                            save_fact(user_id, new_fact)
        except Exception as memory_error:
            print(f"Ошибка обновления долгосрочной памяти: {memory_error}")
        save_message(
            user_id,
            "assistant",
            answer
        )
        await message.answer(answer)
    except Exception as error:
        print(f"Ошибка при обработке сообщения: {error}")
        await message.answer(
            "Произошла ошибка при обработке сообщения. "
            "Проверь, запущена ли Ollama и доступна ли модель."
        )
# ==================================================
# 19. START BOT
# ==================================================
async def main():
    print("Бот запущен!")
    worker = asyncio.create_task(reminder_worker())
    try:
        await dp.start_polling(bot)
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        await ai.close()
        await bot.session.close()
        db.close()
if __name__ == "__main__":
    asyncio.run(main())
