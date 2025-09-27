"""
parse_schedule.py — МПТ
=======================

Скрипт **полностью** парсит страницу <https://mpt.ru/raspisanie/>:

* определяет тип недели (Числитель/Знаменатель и т.д.);
* извлекает список специальностей и групп;
* для **каждой группы** заносит в SQLite‑БД все пары по дням недели
  (аудитория, предмет, преподаватель, номер пары, пометка «ПРАКТИКА» — если есть).

CLI‑режимы
----------
```
python parse_schedule.py             # сохранить расписание в schedule.db
python parse_schedule.py -d mpt.db   # в указанную БД
python parse_schedule.py --list-groups  # вывести группы и выйти
```
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional
import time

import requests
from bs4 import BeautifulSoup, Tag

LIVE_URL = "https://mpt.ru/raspisanie/"
DEFAULT_DB_FILENAME = "schedule.db" # Только имя файла по умолчанию
PARSING_INTERVAL_SECONDS = 86400

logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

# --- Определение пути по умолчанию для БД ---
SCRIPT_DIR = Path(__file__).resolve().parent

PROJECT_BASE_DIR = SCRIPT_DIR.parent # Это будет .../navigator/ 

# 3. Полный путь к директории 'data' по умолчанию
DEFAULT_DATA_DIR = PROJECT_BASE_DIR / "data"

# 4. Полный путь к файлу БД по умолчанию
CONSTRUCTED_DEFAULT_DB_PATH = DEFAULT_DATA_DIR / DEFAULT_DB_FILENAME

###############################################################################
# База данных
###############################################################################
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS specializations (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    specialization_id INTEGER NOT NULL,
    UNIQUE(name, specialization_id),
    FOREIGN KEY (specialization_id) REFERENCES specializations(id)
);
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    day_of_week TEXT NOT NULL,
    location TEXT,
    week_type TEXT,
    period_number TEXT,
    subject TEXT,
    teacher TEXT,
    raw_data TEXT,
    FOREIGN KEY (group_id) REFERENCES groups(id)
);
"""

def init_db(path: Path) -> sqlite3.Connection:
    # Убедимся, что родительская директория для файла БД существует
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"Директория для БД обеспечена/существует: {path.parent}")
    except OSError as e:
        logger.error(f"Не удалось создать директорию {path.parent} для БД: {e}")
        sys.exit(1) # Выход, если не удалось создать директорию

    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    logger.info(f"Соединение с БД установлено: {path}")
    return conn

###############################################################################
# CRUD‑helpers
###############################################################################

def get_or_create_specialization(conn: sqlite3.Connection, name: str) -> int:
    cur = conn.execute("SELECT id FROM specializations WHERE name = ?", (name,))
    if row := cur.fetchone():
        return row[0]
    cur = conn.execute("INSERT INTO specializations(name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def get_or_create_group(conn: sqlite3.Connection, name: str, spec_id: int) -> int:
    cur = conn.execute(
        "SELECT id FROM groups WHERE name = ? AND specialization_id = ?",
        (name, spec_id),
    )
    if row := cur.fetchone():
        return row[0]
    cur = conn.execute(
        "INSERT INTO groups(name, specialization_id) VALUES (?, ?)",
        (name, spec_id),
    )
    conn.commit()
    return cur.lastrowid

###############################################################################
# Загрузка HTML
###############################################################################

def fetch_html() -> str:
    logger.info("GET %s", LIVE_URL)
    r = requests.get(LIVE_URL, timeout=30)
    r.raise_for_status()
    return r.text

###############################################################################
# Парсер: регулярки и утилиты
###############################################################################
DAY_NAMES = (
    "ПОНЕДЕЛЬНИК", "ВТОРНИК", "СРЕДА", "ЧЕТВЕРГ", "ПЯТНИЦА", "СУББОТА", "ВОСКРЕСЕНЬЕ",
)
DAY_RE = re.compile(r"^(?P<day>" + "|".join(DAY_NAMES) + ")", re.I)
WEEK_RE = re.compile(r"Неделя\s*[:\-]\s*(?P<type>[\wА-Яа-я]+)", re.I)
SPEC_CODE_RE = re.compile(r"\d{2}\.\d{2}\.\d{2}")

class ParseError(RuntimeError):
    pass


def find_main_container(soup: BeautifulSoup) -> Tag:
    """Найти DIV, внутри которого лежат вкладки спец‑ций (ul.nav-tabs)."""
    for ul in soup.select("ul.nav-tabs[role=tablist]"):
        a_tags = ul.select("li > a[role=tab]")
        if not a_tags:
            continue
        if sum(1 for a in a_tags if SPEC_CODE_RE.search(a.text)) >= len(a_tags) // 2:
            return ul.parent
    raise ParseError("Не нашёл контейнер с вкладками специализаций")


def extract_week_type(soup: BeautifulSoup) -> str:
    if m := WEEK_RE.search(soup.get_text(" ", strip=True)):
        return m.group("type")
    return "Не указана"

###############################################################################
# Публичная функция — перечисление групп
###############################################################################

def list_groups(html: str) -> Dict[str, List[str]]:
    soup = BeautifulSoup(html, "html.parser")
    container = find_main_container(soup)

    spec_tabs = container.select("ul.nav-tabs[role=tablist] > li > a[role=tab]")
    spec_panes = {p["id"]: p for p in container.select("div.tab-content > div.tab-pane[role=tabpanel]")}

    result: Dict[str, List[str]] = {}
    for a_spec in spec_tabs:
        spec_name = a_spec.text.strip()
        pane = spec_panes.get(a_spec["href"].lstrip("#"))
        if not pane:
            continue
        groups: List[str] = []
        for a_grp in pane.select("ul.nav-tabs[role=tablist] > li > a[role=tab]"):
            gpane = pane.select_one(f"div#{a_grp['href'].lstrip('#')}")
            gname = (gpane.find("h3").text.replace("Группа ", "") if gpane and gpane.find("h3") else a_grp.text).strip()
            groups.append(gname)
        result[spec_name] = groups
    return result

###############################################################################
# Основной парсинг расписания -> БД
###############################################################################

def parse_and_store(conn: sqlite3.Connection, html: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    week_type = extract_week_type(soup)
    logger.info("Тип недели: %s", week_type)

    container = find_main_container(soup)
    spec_tabs = container.select("ul.nav-tabs[role=tablist] > li > a[role=tab]")
    spec_panes = {p["id"]: p for p in container.select("div.tab-content > div.tab-pane[role=tabpanel]")}

    for a_spec in spec_tabs:
        spec_name = a_spec.text.strip()
        pane = spec_panes.get(a_spec["href"].lstrip("#"))
        if not pane:
            continue
        spec_id = get_or_create_specialization(conn, spec_name)
        logger.info("Спец‑ция: %s", spec_name)

        
        for h3 in pane.find_all("h3"):
            txt = h3.get_text(" ", strip=True)
            if not txt.upper().startswith("ГРУПП"):
                continue
            grp_name = txt.replace("Группа ", "").strip()
            grp_id = get_or_create_group(conn, grp_name, spec_id)
            logger.info("   Группа: %s", grp_name)

            sibling = h3.next_sibling
            while sibling:
                if isinstance(sibling, Tag) and sibling.name == "h3":
                    break 
                if isinstance(sibling, Tag) and sibling.name == "table" and \
                   set(sibling.get("class", [])) & {"table", "table-striped"}:
                    _parse_group_table(conn, sibling, grp_id, week_type)
                sibling = sibling.next_sibling

    conn.commit()

def _parse_group_table(conn: sqlite3.Connection, tbl: Tag, grp_id: int, week_type: str) -> None:
    """Разобрать одну таблицу расписания и вставить строки."""
    header_tag = tbl.find("thead").find(["h4", "th", "td"])
    if not header_tag:
        return
    head_txt = header_tag.get_text(" ", strip=True)
    md = DAY_RE.match(head_txt)
    if not md:
        return
    day = md.group("day").upper()
    loc = (
        header_tag.find("span").text.strip()
        if header_tag.find("span") else head_txt[len(md.group(0)):].strip() or "Не указано"
    )

    for tr in tbl.select("tbody > tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        if len(tds) == 1 and "ПРАКТИКА" in tds[0].text.upper():
            conn.execute(
                """INSERT INTO schedules (group_id, day_of_week, location, week_type,
                                          period_number, subject, teacher, raw_data) 
                   VALUES (?,?,?,?,?,?,?,?)""",
                (grp_id, day, loc, week_type, "ПРАКТИКА", "ПРАКТИКА", "ПРАКТИКА", tds[0].text.strip()),
            )
            continue
        if len(tds) >= 3:
            num = tds[0].text.strip()
            subj = " / ".join(x.text.strip() for x in tds[1].select("div.label")) or tds[1].text.strip()
            teacher = " / ".join(x.text.strip() for x in tds[2].select("div.label")) or tds[2].text.strip()
            conn.execute(
                """INSERT INTO schedules (group_id, day_of_week, location, week_type,
                                          period_number, subject, teacher) 
                   VALUES (?,?,?,?,?,?,?)""",
                (grp_id, day, loc, week_type, num, subj, teacher),
            )
        else:
            raw = " | ".join(td.text.strip() for td in tds)
            conn.execute(
                "INSERT INTO schedules (group_id, day_of_week, location, week_type, raw_data) VALUES (?,?,?,?,?)",
                (grp_id, day, loc, week_type, raw),
            )

###############################################################################
# CLI
###############################################################################

def main(argv: Optional[List[str]] | None = None) -> None:
    # logger.debug(f"Скрипт запущен из: {SCRIPT_DIR}")
    # logger.debug(f"Базовая директория проекта (предполагаемая): {PROJECT_BASE_DIR}")
    # logger.debug(f"Директория для данных по умолчанию: {DEFAULT_DATA_DIR}")
    # logger.debug(f"Полный путь к БД по умолчанию: {CONSTRUCTED_DEFAULT_DB_PATH}")

    ap = argparse.ArgumentParser(description="Парсит расписание МПТ в SQLite")
    ap.add_argument(
        "-d", "--db",
        type=Path,
        default=CONSTRUCTED_DEFAULT_DB_PATH,
        help=f"файл SQLite (по умолчанию: {CONSTRUCTED_DEFAULT_DB_PATH})"
    )
    # Аргумент для интервала убран, используется константа PARSING_INTERVAL_SECONDS
    ap.add_argument("-l", "--list-groups", action="store_true", help="только вывести группы и выйти")
    args = ap.parse_args(argv)

    if args.list_groups:
        try:
            html = fetch_html()
            for spec, groups in list_groups(html).items():
                print(spec)
                for g in groups:
                    print(f"   • {g}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка сети при получении списка групп: {e}")
            sys.exit(2)
        except ParseError as err:
            logger.error("Ошибка разбора при получении списка групп: %s", err)
            sys.exit(3)
        except Exception as e:
            logger.error(f"Непредвиденная ошибка при получении списка групп: {e}", exc_info=True)
            sys.exit(1)
        return

    logger.info(f"Парсер запущен в циклическом режиме с интервалом {PARSING_INTERVAL_SECONDS} секунд.")
    logger.info("Для остановки нажмите Ctrl+C.")

    try:
        while True:
            logger.info("--- Начало цикла парсинга ---")
            conn = None 
            try:
                html = fetch_html()
                conn = init_db(args.db)
                parse_and_store(conn, html)
                logger.info("Данные успешно обработаны и сохранены в %s", args.db.resolve())
            except requests.exceptions.RequestException as e:
                logger.error(f"Ошибка сети при получении HTML: {e}. Следующая попытка через {PARSING_INTERVAL_SECONDS} сек.")
            except ParseError as err:
                logger.error(f"Ошибка разбора HTML: {err}. Следующая попытка через {PARSING_INTERVAL_SECONDS} сек.")
            except sqlite3.Error as e: # Более специфичная ошибка для БД
                logger.error(f"Ошибка базы данных: {e}. Следующая попытка через {PARSING_INTERVAL_SECONDS} сек.", exc_info=True)
            except Exception as e: 
                logger.error(f"Непредвиденная ошибка в цикле обработки: {e}. Следующая попытка через {PARSING_INTERVAL_SECONDS} сек.", exc_info=True)
            finally:
                if conn:
                    conn.close()
                    logger.info("Соединение с БД закрыто.")

            logger.info(f"--- Конец цикла парсинга. Ожидание {PARSING_INTERVAL_SECONDS} секунд... ---")
            time.sleep(PARSING_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("\nПарсер остановлен пользователем (Ctrl+C). Завершение...")
    finally:
        logger.info("Парсер завершил свою работу.")


def run_parser():
    main()

if __name__ == "__main__":
    run_parser()
