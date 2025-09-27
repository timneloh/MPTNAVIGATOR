import os
from datetime import datetime
import re
import aiohttp
from bs4 import BeautifulSoup

# Импортируем пул соединений из user_db, чтобы использовать единый пул
from .user_db import POOL

# --- Настройки ---
LIVE_URL = "https://mpt.ru/raspisanie/"
WEEK_RE = re.compile(r"Неделя\s*[:\-]\s*(?P<type>[\wА-Яа-я]+)", re.I)

# --- Функции для работы с расписанием ---

async def get_current_week_type() -> str | None:
    """Асинхронно получает тип текущей недели (Числитель/Знаменатель) с сайта."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(LIVE_URL, timeout=30) as response:
                response.raise_for_status()
                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")
                if m := WEEK_RE.search(soup.get_text(" ", strip=True)):
                    return m.group("type").upper()
    except Exception as e:
        print(f"Error fetching current week type: {e}")
        return None
    return "НЕИЗВЕСТНО"

async def get_specializations() -> list[str]:
    """Асинхронно получает список всех специальностей."""
    async with POOL.acquire() as conn:
        rows = await conn.fetch("SELECT name FROM specializations ORDER BY name")
        return [row['name'] for row in rows]

async def get_groups_by_spec(specialization_name: str) -> list[str]:
    """Асинхронно получает список групп для указанной специальности."""
    async with POOL.acquire() as conn:
        rows = await conn.fetch("""
            SELECT g.name 
            FROM groups g
            JOIN specializations s ON g.specialization_id = s.id
            WHERE s.name = $1
            ORDER BY g.name
        """, specialization_name)
        return [row['name'] for row in rows]

async def get_schedule(group_name: str, date: datetime.date, week_type: str) -> list:
    """Асинхронно получает расписание для группы на указанную дату и тип недели."""
    day_of_week = date.strftime('%A').upper()
    async with POOL.acquire() as conn:
        return await conn.fetch("""
            SELECT s.period_number, s.subject, s.teacher 
            FROM schedules s
            JOIN groups g ON s.group_id = g.id
            WHERE g.name = $1 AND s.day_of_week = $2 AND UPPER(s.week_type) = $3
        """, group_name, day_of_week, week_type.upper())

async def get_replacements(group_name: str, date: datetime.date) -> list:
    """Асинхронно получает замены для группы на указанную дату."""
    async with POOL.acquire() as conn:
        # Проверяем существование таблицы, а не файла
        table_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'changes'
            );
        """)
        if not table_exists:
            return []
            
        return await conn.fetch("""
            SELECT lesson_number, replacement_subject, replacement_teacher
            FROM changes
            WHERE group_name = $1 AND change_date = $2
        """, group_name, date)
