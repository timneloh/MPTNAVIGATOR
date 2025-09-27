import sqlite3
import os

# --- Настройки ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = 'users.db'
DATABASE_PATH = os.path.join(SCRIPT_DIR, '..', 'data', DB_NAME)

# --- Инициализация БД ---
def init_user_db():
    """Создает и инициализирует базу данных для пользователей."""
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            group_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

# --- Функции для работы с пользователями ---
def get_user_group(user_id: int) -> str | None:
    """Получает имя группы пользователя по его ID."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT group_name FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def set_user_group(user_id: int, group_name: str):
    """Добавляет или обновляет группу для пользователя."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO users (user_id, group_name) VALUES (?, ?)",
        (user_id, group_name)
    )
    conn.commit()
    conn.close()
