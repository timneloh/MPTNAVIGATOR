import sqlite3
import os
from datetime import datetime

# --- Настройки ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEDULE_DB_PATH = os.path.join(SCRIPT_DIR, '..', 'data', 'schedule.db')
REPLACEMENTS_DB_PATH = os.path.join(SCRIPT_DIR, '..', 'data', 'replacements.db')

# --- Функции для работы с расписанием ---

def get_specializations():
    """Получает список всех специальностей."""
    conn = sqlite3.connect(SCHEDULE_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM specializations ORDER BY name")
    result = [row[0] for row in cursor.fetchall()]
    conn.close()
    return result

def get_groups_by_spec(specialization_name: str):
    """Получает список групп для указанной специальности."""
    conn = sqlite3.connect(SCHEDULE_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT g.name 
        FROM groups g
        JOIN specializations s ON g.specialization_id = s.id
        WHERE s.name = ?
        ORDER BY g.name
    """, (specialization_name,))
    result = [row[0] for row in cursor.fetchall()]
    conn.close()
    return result

def get_schedule(group_name: str, date: datetime.date):
    """Получает расписание для группы на указанную дату."""
    day_of_week = date.strftime('%A').upper()
    conn = sqlite3.connect(SCHEDULE_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.period_number, s.subject, s.teacher 
        FROM schedules s
        JOIN groups g ON s.group_id = g.id
        WHERE g.name = ? AND s.day_of_week = ?
    """, (group_name, day_of_week))
    schedule = cursor.fetchall()
    conn.close()
    return schedule

def get_replacements(group_name: str, date: datetime.date):
    """Получает замены для группы на указанную дату."""
    # replacements.db может не существовать, если парсер еще не запускался
    if not os.path.exists(REPLACEMENTS_DB_PATH):
        return []
    conn = sqlite3.connect(REPLACEMENTS_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT lesson_number, replacement_subject, replacement_teacher
        FROM changes
        WHERE group_name = ? AND change_date = ?
    """, (group_name, date.strftime('%Y-%m-%d')))
    replacements = cursor.fetchall()
    conn.close()
    return replacements
