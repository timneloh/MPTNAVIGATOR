import asyncpg
import os
import configparser

# Глобальный пул соединений
POOL = None

def get_db_config():
    config = configparser.ConfigParser()
    config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config.ini')
    config.read(config_path)
    return config['postgresql']

async def init_user_db():
    """Создает и инициализирует пул соединений и таблицу пользователей."""
    global POOL
    if POOL is not None:
        return
        
    db_config = get_db_config()
    POOL = await asyncpg.create_pool(**db_config)
    
    async with POOL.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                group_name TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
        """)

async def get_user_group(user_id: int) -> str | None:
    """Асинхронно получает имя группы пользователя по его ID."""
    async with POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT group_name FROM users WHERE user_id = $1", user_id)
        return row['group_name'] if row else None

async def set_user_group(user_id: int, group_name: str):
    """Асинхронно добавляет или обновляет группу для пользователя."""
    async with POOL.acquire() as conn:
        # INSERT ... ON CONFLICT (user_id) DO UPDATE ... - идиоматичный способ для PostgreSQL
        await conn.execute("""
            INSERT INTO users (user_id, group_name) VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET group_name = $2
        """, user_id, group_name)
