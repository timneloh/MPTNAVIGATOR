import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

from navigator.bot.user_db import init_user_db, get_user_group, set_user_group
from navigator.bot.schedule_db import get_specializations, get_groups_by_spec, get_schedule, get_replacements

# --- Настройки ---
API_TOKEN = '8091157857:AAGvuAq0q6PNmSzJj-3qw7FAwvyx02rDxBk'

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- Состояния FSM для регистрации ---
class Registration(StatesGroup):
    choosing_spec = State()
    choosing_group = State()

# --- Клавиатуры ---
def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Расписание на сегодня"), KeyboardButton(text="Расписание на завтра")],
            [KeyboardButton(text="Профиль")]
        ],
        resize_keyboard=True
    )

def profile_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сменить группу", callback_data="change_group")],
            [InlineKeyboardButton(text="Назад", callback_data="to_main_menu")]
        ]
    )

def specs_keyboard(specs):
    buttons = [InlineKeyboardButton(text=spec, callback_data=f"spec_{spec}") for spec in specs]
    # Группируем кнопки по 2 в ряд
    keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def groups_keyboard(groups):
    buttons = [InlineKeyboardButton(text=group, callback_data=f"group_{group}") for group in groups]
    # Группируем кнопки по 2 в ряд
    keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# --- Регистрация и команда /start ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    group = get_user_group(user_id)
    if group:
        await message.answer(f"С возвращением! Ваша группа: {group}", reply_markup=main_keyboard())
    else:
        await message.answer("Добро пожаловать! Давайте настроим ваш профиль.")
        await start_registration(message, state)

async def start_registration(message_or_call: types.Message | types.CallbackQuery, state: FSMContext):
    specs = get_specializations()
    if not specs:
        await message_or_call.answer("Не удалось загрузить список специальностей. Попробуйте позже.")
        return

    await state.set_state(Registration.choosing_spec)
    keyboard = specs_keyboard(specs)
    
    if isinstance(message_or_call, types.Message):
        await message_or_call.answer("Шаг 1: Выберите вашу специальность", reply_markup=keyboard)
    else: # CallbackQuery
        await message_or_call.message.edit_text("Шаг 1: Выберите вашу специальность", reply_markup=keyboard)


@dp.callback_query(StateFilter(Registration.choosing_spec), F.data.startswith('spec_'))
async def process_spec_choice(callback: types.CallbackQuery, state: FSMContext):
    spec_name = callback.data.split('_')[1]
    await state.update_data(spec=spec_name)
    
    groups = get_groups_by_spec(spec_name)
    if not groups:
        await callback.message.edit_text("Не удалось найти группы для этой специальности. Попробуйте выбрать другую.")
        return

    await state.set_state(Registration.choosing_group)
    keyboard = groups_keyboard(groups)
    await callback.message.edit_text("Шаг 2: Выберите вашу группу", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(StateFilter(Registration.choosing_group), F.data.startswith('group_'))
async def process_group_choice(callback: types.CallbackQuery, state: FSMContext):
    group_name = callback.data.split('_')[1]
    user_id = callback.from_user.id
    
    set_user_group(user_id, group_name)
    await state.clear()
    
    await callback.message.delete() # Удаляем inline клавиатуру
    await callback.message.answer(
        f"Отлично! Ваша группа: {group_name}",
        reply_markup=main_keyboard()
    )
    await callback.answer("Профиль настроен!")

# --- Профиль ---
@dp.message(F.text == "Профиль")
async def show_profile(message: types.Message):
    group = get_user_group(message.from_user.id)
    if not group:
        await message.answer("Вы еще не выбрали группу. Давайте это исправим.")
        await start_registration(message, FSMContext(storage=dp.storage, key=dp.key_builder.build(message.chat.id, message.from_user.id)))
        return
        
    await message.answer(f"Ваш профиль:
- Группа: {group}", reply_markup=profile_keyboard())

@dp.callback_query(F.data == "change_group")
async def change_group(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Начинаем смену группы...")
    await start_registration(callback, state)

@dp.callback_query(F.data == "to_main_menu")
async def back_to_main_menu(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.answer()

# --- Расписание ---
def format_schedule(schedule, replacements):
    if not schedule and not replacements:
        return "На этот день расписание и замены не найдены."

    # Применяем замены к расписанию
    schedule_dict = {item[0]: (item[1], item[2]) for item in schedule}
    for rep_num, rep_subj, rep_teacher in replacements:
        schedule_dict[rep_num] = (f"ЗАМЕНА: {rep_subj}", rep_teacher)

    if not schedule_dict:
        return "На этот день расписание не найдено."

    formatted = []
    for lesson_num, (subj, teacher) in sorted(schedule_dict.items()):
        formatted.append(f" пара: {subj} ({teacher})")

    return "\n".join(formatted)

async def send_schedule(message: types.Message, date: datetime.date, day_text: str):
    user_id = message.from_user.id
    group = get_user_group(user_id)
    if not group:
        await message.answer("Пожалуйста, сначала выберите группу, используя команду /start.")
        return

    schedule_data = get_schedule(group, date)
    replacements_data = get_replacements(group, date)
    
    formatted_schedule = format_schedule(schedule_data, replacements_data)
    await message.answer(f"Расписание на {day_text} для группы {group}:\n{formatted_schedule}")

@dp.message(F.text == "Расписание на сегодня")
async def schedule_today(message: types.Message):
    await send_schedule(message, datetime.now().date(), "сегодня")

@dp.message(F.text == "Расписание на завтра")
async def schedule_tomorrow(message: types.Message):
    tomorrow = datetime.now().date() + timedelta(days=1)
    await send_schedule(message, tomorrow, "завтра")

# --- Запуск бота ---
async def main():
    init_user_db()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
