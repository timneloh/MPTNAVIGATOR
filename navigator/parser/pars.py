import requests
from bs4 import BeautifulSoup
import psycopg2
from datetime import datetime
import time
import signal
import sys
import os
import configparser

# --- Настройки ---
URL = 'https://mpt.ru/izmeneniya-v-raspisanii/'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}
UPDATE_INTERVAL = 300  # 5 минут в секундах

# --- Конфигурация БД ---
def get_db_config():
    config = configparser.ConfigParser()
    # Путь к config.ini относительно текущего скрипта
    config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config.ini')
    config.read(config_path)
    return config['postgresql']

# --- Инициализация БД ---
def init_db():
    db_config = get_db_config()
    conn = psycopg2.connect(**db_config)
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS changes (
        id SERIAL PRIMARY KEY,
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
                
                lesson_number = cols[0].text.strip()
                original_full = cols[1].text.strip().rsplit(' ', 1)
                original_subject = original_full[0] if len(original_full) > 1 else original_full[0]
                original_teacher = original_full[1] if len(original_full) > 1 else ''
                
                replacement_full = cols[2].text.strip().rsplit(' ', 1)
                replacement_subject = replacement_full[0] if len(replacement_full) > 1 else replacement_full[0]
                replacement_teacher = replacement_full[1] if len(replacement_full) > 1 else ''
                
                updated_at_str = cols[3].text.strip()
                try:
                    updated_at = datetime.strptime(updated_at_str, '%d.%m.%Y %H:%M:%S')
                    change_date = updated_at.date()
                except ValueError:
                    continue
                
                cursor.execute('''
                    SELECT 1 FROM changes 
                    WHERE group_name = %s 
                    AND lesson_number = %s 
                    AND change_date = %s
                ''', (group_name, lesson_number, change_date))
                
                if cursor.fetchone():
                    continue
                
                cursor.execute('''
                    INSERT INTO changes (
                        group_name, lesson_number, original_subject, original_teacher,
                        replacement_subject, replacement_teacher, updated_at, change_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    group_name, lesson_number, original_subject, original_teacher,
                    replacement_subject, replacement_teacher, updated_at, change_date
                ))
        
        conn.commit()
        print(f"[{datetime.now()}] Данные успешно обновлены")

    except requests.exceptions.RequestException as e:
        print(f"[{datetime.now()}] Ошибка подключения: {str(e)}")
    except psycopg2.Error as e:
        print(f"[{datetime.now()}] Ошибка БД: {str(e)}")
        conn.rollback()
    except Exception as e:
        print(f"[{datetime.now()}] Ошибка: {str(e)}")

# --- Обработчик завершения ---
def signal_handler(sig, frame):
    print("\nПолучен сигнал завершения. Закрываем соединение с БД...")
    if 'conn' in globals() and conn:
        conn.close()
    sys.exit(0)

# --- Основной цикл ---
if __name__ == "__main__":
    conn = init_db()
    signal.signal(signal.SIGINT, signal_handler)
    
    print("Запущен парсер изменений расписания. Нажмите Ctrl+C для остановки.")
    while True:
        parse_schedule(conn)
        time.sleep(UPDATE_INTERVAL)
