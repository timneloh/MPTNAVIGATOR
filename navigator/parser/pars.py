import requests
from bs4 import BeautifulSoup
import sqlite3
from datetime import datetime
import time
import signal
import sys
import os

# --- Настройки ---
URL = 'https://mpt.ru/izmeneniya-v-raspisanii/'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}
UPDATE_INTERVAL = 300  # 5 минут в секундах

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NAVIGATOR_PARENT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_FOLDER_NAME = 'data'
DB_NAME = 'replacements.db'
DATABASE_DIR = os.path.join(NAVIGATOR_PARENT_DIR, DATA_FOLDER_NAME)
DATABASE_PATH = os.path.join(DATABASE_DIR, DB_NAME) #делаем базу данных в /data

# --- Инициализация БД ---
def init_db():
    try:
        os.makedirs(DATABASE_DIR, exist_ok=True)
    except OSError as e:
        print(f"Ошибка! Не могу создать папку для БД: {DATABASE_DIR}. Детали: {e}")
        sys.exit(1)

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Обновленная структура таблицы с полем change_date
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_name TEXT NOT NULL,
        lesson_number TEXT NOT NULL,
        original_subject TEXT NOT NULL,
        original_teacher TEXT NOT NULL,
        replacement_subject TEXT NOT NULL,
        replacement_teacher TEXT NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        change_date DATE NOT NULL,
        UNIQUE(group_name, lesson_number, change_date)
    )
    ''')
    conn.commit()
    return conn

# --- Функция парсинга ---
def parse_schedule(conn):
    cursor = conn.cursor()
    try:
        response = requests.get(URL, headers=HEADERS, timeout=10)
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')
        
        for table in soup.find_all('table', class_='table'):
            caption = table.find('caption')
            if not caption:
                continue
            
            group_name = caption.text.split(':')[-1].strip()
            
            for row in table.find_all('tr')[1:]:
                cols = row.find_all('td')
                if len(cols) < 4:
                    continue
                
                # Парсим данные
                lesson_number = cols[0].text.strip()
                
                # Обработка исходных данных
                original_full = cols[1].text.strip().rsplit(' ', 1)
                original_subject = original_full[0] if len(original_full) > 1 else original_full[0]
                original_teacher = original_full[1] if len(original_full) > 1 else ''
                
                # Обработка замены
                replacement_full = cols[2].text.strip().rsplit(' ', 1)
                replacement_subject = replacement_full[0] if len(replacement_full) > 1 else replacement_full[0]
                replacement_teacher = replacement_full[1] if len(replacement_full) > 1 else ''
                
                # Парсим дату и время
                updated_at_str = cols[3].text.strip()
                try:
                    updated_at = datetime.strptime(updated_at_str, '%d.%m.%Y %H:%M:%S')
                    change_date = updated_at.date()  # Извлекаем дату без времени
                except ValueError:
                    continue
                
                # Проверяем существование записи
                cursor.execute('''
                    SELECT 1 FROM changes 
                    WHERE group_name = ? 
                    AND lesson_number = ? 
                    AND change_date = ?
                ''', (group_name, lesson_number, change_date))
                
                if cursor.fetchone():
                    continue  # Пропускаем существующую запись
                
                # Вставляем новую запись
                cursor.execute('''
                    INSERT INTO changes (
                        group_name,
                        lesson_number,
                        original_subject,
                        original_teacher,
                        replacement_subject,
                        replacement_teacher,
                        updated_at,
                        change_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    group_name,
                    lesson_number,
                    original_subject,
                    original_teacher,
                    replacement_subject,
                    replacement_teacher,
                    updated_at_str,
                    change_date
                ))
        
        conn.commit()
        print(f"[{datetime.now()}] Данные успешно обновлены")

    except requests.exceptions.RequestException as e:
        print(f"[{datetime.now()}] Ошибка подключения: {str(e)}")
    except Exception as e:
        print(f"[{datetime.now()}] Ошибка: {str(e)}")

# --- Обработчик завершения ---
def signal_handler(sig, frame):
    print("\nПолучен сигнал завершения. Закрываем соединение с БД...")
    conn.close()
    sys.exit(0)

# --- Основной цикл ---
if __name__ == "__main__":
    conn = init_db()
    signal.signal(signal.SIGINT, signal_handler)
    
    print("Запущен парсер расписания. Нажмите Ctrl+C для остановки.")
    while True:
        parse_schedule(conn)
        time.sleep(UPDATE_INTERVAL)
