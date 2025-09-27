import asyncio
import multiprocessing
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.exceptions import TelegramBadRequest

from navigator.bot.user_db import init_user_db, get_user_group, set_user_group
from navigator.bot.schedule_db import (
    get_specializations, get_groups_by_spec, get_schedule, 
    get_replacements, get_current_week_type
)
from navigator.parser.pars import run_parser as run_changes_parser
from navigator.parser.dynamic_parser import run_parser as run_schedule_parser


# --- Настройки ---
API_TOKEN = '8091157857:AAGvuAq0q6PNmSzJj-3qw7FAwvyx02rDxBk'

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- Состояния FSM ---
class Reg(StatesGroup):
    choosing_spec = State()
    choosing_group = State()

# --- Словарь для смены недели ---
WEEK_FLIP = {
    "ЧИСЛИТЕЛЬ": "ЗНАМЕНАТЕЛЬ",
    "ЗНАМЕНАТЕЛЬ": "ЧИСЛИТЕЛЬ"
}

# --- Клавиатуры ---
def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Расписание на сегодня"), KeyboardButton(text="Расписание на завтра")],
            [KeyboardButton(text="Расписание на неделю")],
            [KeyboardButton(text="Профиль")]
        ],
        resize_keyboard=True
    )

def profile_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сменить группу", callback_data="change_group")]
    ])

def specs_keyboard(specs):
    buttons = [InlineKeyboardButton(text=spec, callback_data=f"spec_{spec}") for spec in specs]
    keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def groups_keyboard(groups):
    buttons = [InlineKeyboardButton(text=group, callback_data=f"group_{group}") for group in groups]
    keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# --- Регистрация и команда /start ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    if await get_user_group(message.from_user.id):
        await message.answer("С возвращением!", reply_markup=main_keyboard())
    else:
        await message.answer("Добро пожаловать! Давайте настроим ваш профиль.")
        await start_registration(message, state)

async def start_registration(message_or_call: types.Message | types.CallbackQuery, state: FSMContext):
    specs = await get_specializations()
    if not specs:
        await message_or_call.answer("Не удалось загрузить список специальностей. Пожалуйста, подождите, парсеры еще не завершили работу.")
        return

    await state.set_state(Reg.choosing_spec)
    keyboard = specs_keyboard(specs)
    text = "Шаг 1: Выберите вашу специальность"
    if isinstance(message_or_call, types.Message):
        await message_or_call.answer(text, reply_markup=keyboard)
    else:
        await message_or_call.message.edit_text(text, reply_markup=keyboard)

@dp.callback_query(StateFilter(Reg.choosing_spec), F.data.startswith('spec_'))
async def process_spec_choice(callback: types.CallbackQuery, state: FSMContext):
    spec_name = callback.data.split('_', 1)[1]
    groups = await get_groups_by_spec(spec_name)
    if not groups:
        await callback.message.edit_text("Не удалось найти группы. Попробуйте выбрать другую специальность.")
        return

    await state.set_state(Reg.choosing_group)
    await callback.message.edit_text("Шаг 2: Выберите вашу группу", reply_markup=groups_keyboard(groups))
    await callback.answer()

@dp.callback_query(StateFilter(Reg.choosing_group), F.data.startswith('group_'))
async def process_group_choice(callback: types.CallbackQuery, state: FSMContext):
    group_name = callback.data.split('_', 1)[1]
    await set_user_group(callback.from_user.id, group_name)
    await state.clear()
    await callback.message.delete()
    await callback.message.answer(f"Отлично! Ваша группа: {group_name}", reply_markup=main_keyboard())
    await callback.answer("Профиль настроен!")

# --- Профиль ---
@dp.message(F.text == "Профиль")
async def show_profile(message: types.Message, state: FSMContext):
    group = await get_user_group(message.from_user.id)
    if not group:
        await message.answer("Вы еще не выбрали группу. Давайте это исправим.")
        await start_registration(message, state)
        return
    await message.answer(f"Ваша группа: {group}", reply_markup=profile_keyboard())

@dp.callback_query(F.data == "change_group")
async def change_group(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Начинаем смену группы...")
    await start_registration(callback, state)

# --- Расписание ---
def format_schedule(schedule, replacements):
    if not schedule and not replacements:
        return "На этот день расписание и замены не найдены."

    schedule_dict = {item['period_number']: (item['subject'], item['teacher']) for item in schedule}

    # Применяем замены
    for rep in replacements:
        original_lesson = schedule_dict.get(rep['lesson_number'])
        replacement_text = f"ЗАМЕНА: {rep['replacement_subject']}"
        
        if original_lesson:
            original_subject, _ = original_lesson
            replacement_text += f" (было: {original_subject})"
        
        schedule_dict[rep['lesson_number']] = (replacement_text, rep['replacement_teacher'])

    if not schedule_dict:
        return "На этот день расписание не найдено."

    # Формируем итоговый текст
    formatted_lines = []
    for lesson_num, (subj, teacher) in sorted(schedule_dict.items()):
        # Проверяем, является ли номер пары числом для корректной сортировки
        try:
            sort_key = int(lesson_num)
        except (ValueError, TypeError):
            sort_key = float('inf') # Помещаем нечисловые пары (напр. "ПРАКТИКА") в конец
        formatted_lines.append((sort_key, f"{lesson_num} пара: {subj} ({teacher})"))
    
    # Сортируем строки по числовому номеру пары
    formatted_lines.sort(key=lambda x: x[0])
    
    return "\n".join([line for _, line in formatted_lines])

async def send_schedule(message: types.Message, date: datetime.date, day_text: str):
    user_id = message.from_user.id
    group = await get_user_group(user_id)
    if not group:
        await message.answer("Сначала выберите группу через /start или Профиль.")
        return

    current_week_type = await get_current_week_type()
    if not current_week_type or current_week_type == "НЕИЗВЕСТНО":
        await message.answer("Не удалось определить тип текущей недели. Попробуйте позже.")
        return

    today = datetime.now().date()
    target_week_type = current_week_type
    if today.isocalendar().week != date.isocalendar().week:
        target_week_type = WEEK_FLIP.get(current_week_type, current_week_type)

    schedule_data = await get_schedule(group, date, target_week_type)
    replacements_data = await get_replacements(group, date)
    
    formatted = format_schedule(schedule_data, replacements_data)
    await message.answer(f"Расписание на {day_text} для группы {group} ({target_week_type.capitalize()}):\n{formatted}")

@dp.message(F.text == "Расписание на сегодня")
async def schedule_today(message: types.Message):
    await send_schedule(message, datetime.now().date(), "сегодня")

@dp.message(F.text == "Расписание на завтра")
async def schedule_tomorrow(message: types.Message):
    await send_schedule(message, datetime.now().date() + timedelta(days=1), "завтра")

# --- Новая логика для расписания на неделю ---

async def get_weekly_schedule_text_and_keyboard(group: str, week_type: str):
    """Готовит текст расписания на неделю и клавиатуру для переключения."""
    days = ["ПОНЕДЕЛЬНИК", "ВТОРНИК", "СРЕДА", "ЧЕТВЕРГ", "ПЯТНИЦА", "СУББОТА"]
    full_schedule_text = []
    
    today = datetime.now()
    base_date = today - timedelta(days=today.weekday())

    for i, day_name in enumerate(days):
        date_for_day = base_date + timedelta(days=i)
        # Для расписания на неделю замены не ищем, чтобы не перегружать
        schedule_data = await get_schedule(group, date_for_day, week_type)
        if schedule_data:
            # Используем format_schedule без замен
            formatted = format_schedule(schedule_data, [])
            full_schedule_text.append(f"**{day_name.capitalize()}**:\n{formatted}")
    
    if not full_schedule_text:
        response = f"Для группы {group} не найдено расписание на неделю ({week_type.capitalize()})."
    else:
        response = f"**Расписание на неделю ({week_type.capitalize()}) для группы {group}**:\n\n" + "\n\n".join(full_schedule_text)

    other_week = WEEK_FLIP.get(week_type, "ЧИСЛИТЕЛЬ")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Показать {other_week.lower()}", callback_data=f"show_week_{other_week}")]
    ])
    
    return response, keyboard

@dp.message(F.text == "Расписание на неделю")
async def weekly_schedule_entry(message: types.Message):
    """Обрабатывает нажатие кнопки 'Расписание на неделю'."""
    group = await get_user_group(message.from_user.id)
    if not group:
        await message.answer("Сначала выберите группу в Профиле.")
        return

    current_week = await get_current_week_type()
    if not current_week or current_week == "НЕИЗВЕСТНО":
        await message.answer("Не удалось определить тип текущей недели. Попробуйте позже.")
        return
        
    text, keyboard = await get_weekly_schedule_text_and_keyboard(group, current_week)
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("show_week_"))
async def weekly_schedule_switch(callback: types.CallbackQuery):
    """Обрабатывает переключение недели в расписании на неделю."""
    week_type = callback.data.split('_', 2)[2]
    group = await get_user_group(callback.from_user.id)
    if not group:
        await callback.message.edit_text("Сначала выберите группу в Профиле.")
        await callback.answer()
        return

    text, keyboard = await get_weekly_schedule_text_and_keyboard(group, week_type)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except TelegramBadRequest:
        # Это исключение возникает, если текст сообщения не изменился. Просто игнорируем.
        pass
    await callback.answer()

# --- Запуск бота ---
async def main():
    # Инициализация БД для бота
    await init_user_db()

    # Запуск парсеров в отдельных процессах
    print("Запуск фоновых парсеров...")
    p1 = multiprocessing.Process(target=run_changes_parser, daemon=True)
    p2 = multiprocessing.Process(target=run_schedule_parser, daemon=True)
    p1.start()
    p2.start()
    print("Парсеры запущены.")

    # Запуск бота
    print("Запуск бота...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    # На Windows для multiprocessing может потребоваться эта обертка
    multiprocessing.freeze_support()
    asyncio.run(main())