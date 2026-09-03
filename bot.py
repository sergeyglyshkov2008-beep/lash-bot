import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey, Float, inspect
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from aiohttp import web

# ---------- НАСТРОЙКИ ----------
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# ---------- БАЗА ДАННЫХ ----------
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    username = Column(String)
    full_name = Column(String)
    phone = Column(String)
    role = Column(String, default='client')
    created_at = Column(DateTime, default=datetime.utcnow)
    appointments = relationship('Appointment', back_populates='user')

class Service(Base):
    __tablename__ = 'services'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(String)
    price = Column(Float)
    duration_minutes = Column(Integer)
    prepayment_required = Column(Boolean, default=False)
    prepayment_amount = Column(Float, default=0)

class ScheduleSlot(Base):
    __tablename__ = 'schedule_slots'
    id = Column(Integer, primary_key=True)
    date = Column(String)   # формат YYYY.MM.DD
    time = Column(String)   # формат HH:MM
    is_booked = Column(Boolean, default=False)
    appointment = relationship('Appointment', uselist=False, back_populates='slot')

class Appointment(Base):
    __tablename__ = 'appointments'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    service_id = Column(Integer, ForeignKey('services.id'))
    schedule_id = Column(Integer, ForeignKey('schedule_slots.id'), unique=True)
    status = Column(String, default='pending')  # pending, confirmed, cancelled, completed
    prepayment_screenshot = Column(String)
    client_notes = Column(String)   # пожелания клиента
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship('User', back_populates='appointments')
    service = relationship('Service')
    slot = relationship('ScheduleSlot', back_populates='appointment')

class Setting(Base):
    __tablename__ = 'settings'
    key = Column(String, primary_key=True)
    value = Column(String)

engine = create_engine('sqlite:///bot.db', connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)

# Миграция: добавляем столбец client_notes, если его нет
inspector = inspect(engine)
columns = [col['name'] for col in inspector.get_columns('appointments')]
if 'client_notes' not in columns:
    with engine.connect() as conn:
        conn.execute("ALTER TABLE appointments ADD COLUMN client_notes VARCHAR")

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# ---------- ИНИЦИАЛИЗАЦИЯ БОТА ----------
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ---------- СОСТОЯНИЯ FSM ----------
class BookingStates(StatesGroup):
    choosing_service = State()
    choosing_intent = State()          # Мне нужно посоветоваться / Я знаю что хочу
    writing_wishes = State()           # клиент пишет пожелания
    choosing_date = State()
    choosing_time = State()
    waiting_screenshot = State()
    after_consult = State()            # после консультации, ждём нажатия "Я написала"

class AdminStates(StatesGroup):
    adding_slot_date = State()
    adding_slot_time = State()

    editing_slot_list = State()
    editing_slot_choose_action = State()
    editing_slot_new_date = State()
    editing_slot_new_time = State()

    managing_services = State()
    adding_service_name = State()
    adding_service_description = State()
    adding_service_price = State()
    adding_service_duration = State()
    adding_service_prepayment = State()
    editing_service_select = State()
    editing_service_field = State()
    editing_service_value = State()

    editing_any_text = State()         # выбор, какой текст редактировать
    editing_any_text_value = State()   # ввод нового текста

    entering_notes_select = State()
    entering_notes_value = State()

    confirming_delete_all = State()

# ---------- КЛАВИАТУРЫ ----------
def main_keyboard():
    buttons = [
        [types.KeyboardButton(text="📋 Услуги и цены")],
        [types.KeyboardButton(text="📅 Записаться")],
        [types.KeyboardButton(text="👤 Мои записи")],
        [types.KeyboardButton(text="❓ ЧаВо")],
        [types.KeyboardButton(text="📍 Адрес")],
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def admin_keyboard():
    buttons = [
        [types.KeyboardButton(text="➕ Добавить окна")],
        [types.KeyboardButton(text="📅 Редактировать окна")],
        [types.KeyboardButton(text="💼 Управление услугами")],
        [types.KeyboardButton(text="📝 Редактировать ЧаВо")],
        [types.KeyboardButton(text="📩 Сообщение с реквизитами")],
        [types.KeyboardButton(text="📨 Сообщение об успешной записи")],
        [types.KeyboardButton(text="📍 Адрес (текст)")],
        [types.KeyboardButton(text="💬 Текст консультации")],
        [types.KeyboardButton(text="📢 Текст пожеланий")],
        [types.KeyboardButton(text="✏️ Внести пожелания клиента")],
        [types.KeyboardButton(text="🗑 Удалить все записи")],
        [types.KeyboardButton(text="📋 Все записи")],
        [types.KeyboardButton(text="📊 Клиенты")],
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def inline_kb_3(buttons: list):
    """Создаёт инлайн-клавиатуру с кнопками по 3 в ряд."""
    kb = []
    for i in range(0, len(buttons), 3):
        kb.append(buttons[i:i+3])
    return types.InlineKeyboardMarkup(inline_keyboard=kb)

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def get_or_create_user(telegram_id, username, full_name):
    session = SessionLocal()
    user = session.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        user = User(telegram_id=telegram_id, username=username, full_name=full_name)
        session.add(user)
        session.commit()
    session.close()
    return user

def format_slot(slot):
    status = "занято" if slot.is_booked else "свободно"
    date_display = f"{slot.date[8:10]}.{slot.date[5:7]}.{slot.date[0:4]}"  # DD.MM.YYYY
    return f"{date_display} {slot.time} ({status})"

def format_date(date_str):  # YYYY.MM.DD -> DD.MM.YYYY
    return f"{date_str[8:10]}.{date_str[5:7]}.{date_str[0:4]}"

def parse_date_input(text):  # DD.MM.YYYY -> YYYY.MM.DD
    try:
        dt = datetime.strptime(text, "%d.%m.%Y")
        return dt.strftime("%Y.%m.%d")
    except ValueError:
        return None

# ---------- ВЕБ-СЕРВЕР ДЛЯ RENDER ----------
async def run_web_server():
    app = web.Application()
    app.router.add_get('/', lambda request: web.Response(text="OK"))
    port = int(os.getenv('PORT', 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"Web server started on port {port}")

# ---------- ОБРАБОТЧИКИ КОМАНД ----------
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    session = SessionLocal()
    user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
    session.close()
    if user.phone:
        await message.answer("С возвращением!", reply_markup=main_keyboard())
    else:
        keyboard = types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="📱 Отправить контакт", request_contact=True)]],
            resize_keyboard=True
        )
        await message.answer("Пожалуйста, отправьте ваш номер телефона для записи.", reply_markup=keyboard)

@dp.message(F.contact)
async def contact_received(message: types.Message):
    session = SessionLocal()
    user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
    if user:
        user.phone = message.contact.phone_number
        session.commit()
    session.close()
    await message.answer("Спасибо! Теперь вы можете записаться.", reply_markup=main_keyboard())

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав администратора.")
        return
    await message.answer("Админ-панель:", reply_markup=admin_keyboard())

# ---------- ОСНОВНОЕ МЕНЮ (клиент) ----------
@dp.message(F.text == "📋 Услуги и цены")
async def show_services(message: types.Message):
    session = SessionLocal()
    services = session.query(Service).all()
    if not services:
        await message.answer("Услуги пока не добавлены.")
        session.close()
        return
    text = "Наши услуги:\n\n"
    for s in services:
        text += f"🔹 {s.name} — {s.price} руб. ({s.duration_minutes} мин)\n"
        if s.description:
            text += f"   {s.description}\n"
        if s.prepayment_required:
            text += f"   Предоплата: {s.prepayment_amount} руб.\n"
        text += "\n"
    await message.answer(text)
    session.close()

@dp.message(F.text == "❓ ЧаВо")
async def show_faq(message: types.Message):
    session = SessionLocal()
    faq = session.query(Setting).filter(Setting.key == 'faq_text').first()
    session.close()
    await message.answer(faq.value if faq else "Раздел пока не заполнен.")

@dp.message(F.text == "📍 Адрес")
async def show_address(message: types.Message):
    session = SessionLocal()
    addr = session.query(Setting).filter(Setting.key == 'address_text').first()
    session.close()
    await message.answer(addr.value if addr else "Адрес не указан.")

@dp.message(F.text == "👤 Мои записи")
async def show_my_appointments(message: types.Message):
    session = SessionLocal()
    user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start.")
        session.close()
        return
    appointments = session.query(Appointment).filter(Appointment.user_id == user.id).all()
    if not appointments:
        await message.answer("У вас нет записей.")
        session.close()
        return
    text = "Ваши записи:\n\n"
    for app in appointments:
        service = session.query(Service).get(app.service_id)
        slot = session.query(ScheduleSlot).get(app.schedule_id)
        date_display = format_date(slot.date)
        text += f"📅 {date_display} в {slot.time} — {service.name}\n"
    await message.answer(text)
    session.close()

# ---------- ЗАПИСЬ ----------
@dp.message(F.text == "📅 Записаться")
async def start_booking(message: types.Message, state: FSMContext):
    session = SessionLocal()
    services = session.query(Service).all()
    if not services:
        await message.answer("Нет доступных услуг.")
        session.close()
        return
    buttons = [types.InlineKeyboardButton(text=f"{s.name} - {s.price}₽", callback_data=f"service_{s.id}") for s in services]
    await message.answer("Выберите услугу:", reply_markup=inline_kb_3(buttons))
    await state.set_state(BookingStates.choosing_service)
    session.close()

@dp.callback_query(BookingStates.choosing_service)
async def service_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    service_id = int(callback.data.split("_")[1])
    await state.update_data(service_id=service_id)
    kb = inline_kb_3([
        types.InlineKeyboardButton(text="💬 Мне нужно посоветоваться", callback_data="intent_consult"),
        types.InlineKeyboardButton(text="✅ Я знаю что хочу", callback_data="intent_book")
    ])
    await callback.message.edit_text("Что вам нужно?", reply_markup=kb)
    await state.set_state(BookingStates.choosing_intent)

@dp.callback_query(BookingStates.choosing_intent, F.data == "intent_consult")
async def intent_consult(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    session = SessionLocal()
    consult_text = session.query(Setting).filter(Setting.key == 'consultation_text').first()
    session.close()
    await callback.message.answer(consult_text.value if consult_text else "Для консультации напишите администратору.")
    # Уведомляем админа
    user = get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
    admin_msg = f"📞 Клиент хочет консультацию:\nИмя: {user.full_name}\nUsername: @{user.username}"
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_msg)
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")
    # Показываем кнопку "Я написала"
    kb = inline_kb_3([types.InlineKeyboardButton(text="✅ Я написала", callback_data="i_wrote")])
    await callback.message.answer("После того как напишете администратору, нажмите кнопку ниже, чтобы продолжить запись.", reply_markup=kb)
    await state.set_state(BookingStates.after_consult)

@dp.callback_query(BookingStates.after_consult, F.data == "i_wrote")
async def after_consult(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    # Переходим к выбору даты (пожелания не запрашиваем, т.к. клиент уже проконсультировался)
    session = SessionLocal()
    dates = session.query(ScheduleSlot.date).filter(ScheduleSlot.is_booked == False).distinct().order_by(ScheduleSlot.date).limit(20).all()
    session.close()
    if not dates:
        await callback.message.answer("Свободных окон пока нет.")
        await state.clear()
        return
    buttons = [types.InlineKeyboardButton(text=format_date(d[0]), callback_data=f"date_{d[0]}") for d in dates]
    await callback.message.answer("Выберите дату:", reply_markup=inline_kb_3(buttons))
    await state.set_state(BookingStates.choosing_date)

@dp.callback_query(BookingStates.choosing_intent, F.data == "intent_book")
async def intent_book(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    session = SessionLocal()
    wishes_text = session.query(Setting).filter(Setting.key == 'wishes_text').first()
    session.close()
    await callback.message.answer(wishes_text.value if wishes_text else "Напишите параметры ресниц или отправьте фото.")
    await state.set_state(BookingStates.writing_wishes)

@dp.message(BookingStates.writing_wishes, F.photo | F.text)
async def receive_wishes(message: types.Message, state: FSMContext):
    notes = ""
    photo_id = None
    if message.photo:
        photo_id = message.photo[-1].file_id
        notes = "[Фото]"
    else:
        notes = message.text
    await state.update_data(client_notes=notes, client_photo=photo_id)
    session = SessionLocal()
    dates = session.query(ScheduleSlot.date).filter(ScheduleSlot.is_booked == False).distinct().order_by(ScheduleSlot.date).limit(20).all()
    session.close()
    if not dates:
        await message.answer("Свободных окон пока нет.")
        await state.clear()
        return
    buttons = [types.InlineKeyboardButton(text=format_date(d[0]), callback_data=f"date_{d[0]}") for d in dates]
    await message.answer("Выберите дату:", reply_markup=inline_kb_3(buttons))
    await state.set_state(BookingStates.choosing_date)

@dp.callback_query(BookingStates.choosing_date)
async def date_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    date = callback.data.split("_")[1]
    await state.update_data(date=date)
    session = SessionLocal()
    slots = session.query(ScheduleSlot).filter(ScheduleSlot.date == date, ScheduleSlot.is_booked == False).order_by(ScheduleSlot.time).all()
    session.close()
    if not slots:
        await callback.message.answer("На эту дату нет свободных окон.")
        await state.clear()
        return
    buttons = [types.InlineKeyboardButton(text=s.time, callback_data=f"time_{s.id}") for s in slots]
    await callback.message.edit_text("Выберите время:", reply_markup=inline_kb_3(buttons))
    await state.set_state(BookingStates.choosing_time)

@dp.callback_query(BookingStates.choosing_time)
async def time_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    slot_id = int(callback.data.split("_")[1])
    data = await state.get_data()
    service_id = data['service_id']
    client_notes = data.get('client_notes', '')
    client_photo = data.get('client_photo')
    session = SessionLocal()
    service = session.query(Service).get(service_id)
    slot = session.query(ScheduleSlot).get(slot_id)
    user = session.query(User).filter(User.telegram_id == callback.from_user.id).first()

    slot.is_booked = True
    appointment = Appointment(user_id=user.id, service_id=service_id, schedule_id=slot_id, status='pending', client_notes=client_notes)
    session.add(appointment)
    session.commit()

    date_display = format_date(slot.date)
    await callback.message.answer(f"Вы выбрали:\nУслуга: {service.name}\nДата: {date_display}\nВремя: {slot.time}")

    if service.prepayment_required:
        conf_text = session.query(Setting).filter(Setting.key == 'confirmation_text').first()
        text = conf_text.value.format(service_name=service.name, date=date_display, time=slot.time, prepayment_amount=service.prepayment_amount) if conf_text else "Внесите предоплату."
        await callback.message.answer(text)
        await state.update_data(appointment_id=appointment.id)
        await state.set_state(BookingStates.waiting_screenshot)
    else:
        for admin_id in ADMIN_IDS:
            kb = inline_kb_3([
                types.InlineKeyboardButton(text="✅", callback_data=f"confirm_{appointment.id}"),
                types.InlineKeyboardButton(text="❌", callback_data=f"reject_{appointment.id}")
            ])
            msg = (f"🆕 Новая запись\nКлиент: {user.full_name} (@{user.username})\n"
                   f"Услуга: {service.name}\nДата: {date_display} {slot.time}\n"
                   f"Пожелания: {client_notes if client_notes else 'нет'}")
            try:
                await bot.send_message(admin_id, msg, reply_markup=kb)
            except Exception as e:
                logging.error(f"Не удалось отправить админу {admin_id}: {e}")
        await state.clear()
    session.close()

@dp.message(BookingStates.waiting_screenshot, F.photo)
async def screenshot_received(message: types.Message, state: FSMContext):
    data = await state.get_data()
    appointment_id = data['appointment_id']
    session = SessionLocal()
    appointment = session.query(Appointment).get(appointment_id)
    if not appointment:
        await message.answer("Запись не найдена.")
        session.close()
        await state.clear()
        return
    file_id = message.photo[-1].file_id
    appointment.prepayment_screenshot = file_id
    session.commit()
    slot = session.query(ScheduleSlot).get(appointment.schedule_id)
    service = session.query(Service).get(appointment.service_id)
    user = session.query(User).get(appointment.user_id)
    date_display = format_date(slot.date)
    await message.answer("Спасибо! Ваша запись ожидает подтверждения мастера.")
    for admin_id in ADMIN_IDS:
        kb = inline_kb_3([
            types.InlineKeyboardButton(text="✅", callback_data=f"confirm_{appointment.id}"),
            types.InlineKeyboardButton(text="❌", callback_data=f"reject_{appointment.id}")
        ])
        caption = (f"🧾 Предоплата от {user.full_name}\nУслуга: {service.name}\n"
                   f"Дата: {date_display} {slot.time}")
        try:
            await bot.send_photo(admin_id, file_id, caption=caption, reply_markup=kb)
        except Exception as e:
            logging.error(f"Не удалось отправить скриншот админу {admin_id}: {e}")
    await state.clear()
    session.close()

# ---------- ПОДТВЕРЖДЕНИЕ/ОТКЛОНЕНИЕ ----------
@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_appointment(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав.", show_alert=True)
        return
    appointment_id = int(callback.data.split("_")[1])
    session = SessionLocal()
    appointment = session.query(Appointment).get(appointment_id)
    if not appointment or appointment.status != 'pending':
        await callback.answer("Запись уже обработана.", show_alert=True)
        session.close()
        return
    appointment.status = 'confirmed'
    session.commit()
    slot = session.query(ScheduleSlot).get(appointment.schedule_id)
    service = session.query(Service).get(appointment.service_id)
    client = session.query(User).get(appointment.user_id)
    date_display = format_date(slot.date)
    success_text = session.query(Setting).filter(Setting.key == 'success_message').first()
    text = success_text.value.format(date=date_display, time=slot.time, service_name=service.name, admin_username=callback.from_user.username or "администратор") if success_text else f"✅ Вы успешно записаны на {date_display} {slot.time}."
    try:
        await bot.send_message(client.telegram_id, text)
    except Exception as e:
        logging.error(f"Не удалось отправить клиенту {client.telegram_id}: {e}")
    await callback.message.edit_text(f"✅ Запись подтверждена\nКлиент: {client.full_name}\nУслуга: {service.name}\nДата: {date_display} {slot.time}", reply_markup=None)
    await callback.answer("Запись подтверждена")
    session.close()

@dp.callback_query(F.data.startswith("reject_"))
async def reject_appointment(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав.", show_alert=True)
        return
    appointment_id = int(callback.data.split("_")[1])
    session = SessionLocal()
    appointment = session.query(Appointment).get(appointment_id)
    if not appointment or appointment.status != 'pending':
        await callback.answer("Запись уже обработана.", show_alert=True)
        session.close()
        return
    slot = session.query(ScheduleSlot).get(appointment.schedule_id)
    client = session.query(User).get(appointment.user_id)
    service = session.query(Service).get(appointment.service_id)
    slot.is_booked = False
    session.delete(appointment)
    session.commit()
    try:
        await bot.send_message(client.telegram_id, "❌ Ваша запись была отклонена администратором.")
    except Exception as e:
        logging.error(f"Не удалось отправить клиенту {client.telegram_id}: {e}")
    await callback.message.edit_text(f"❌ Запись отклонена\nКлиент: {client.full_name}\nУслуга: {service.name}", reply_markup=None)
    await callback.answer("Запись отклонена")
    session.close()

# ---------- АДМИН-ПАНЕЛЬ ----------
# Добавление окон
@dp.message(F.text == "➕ Добавить окна")
async def add_slots_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав.")
        return
    await message.answer("Введите дату в формате ДД.ММ.ГГГГ (например, 05.06.2025):")
    await state.set_state(AdminStates.adding_slot_date)

@dp.message(AdminStates.adding_slot_date)
async def add_slot_date(message: types.Message, state: FSMContext):
    parsed = parse_date_input(message.text.strip())
    if not parsed:
        await message.answer("Неверный формат. Введите ещё раз (ДД.ММ.ГГГГ):")
        return
    await state.update_data(date=parsed)
    await message.answer("Введите время через запятую (например: 10:00, 11:30):")
    await state.set_state(AdminStates.adding_slot_time)

@dp.message(AdminStates.adding_slot_time)
async def add_slot_time(message: types.Message, state: FSMContext):
    data = await state.get_data()
    date = data['date']
    times = [t.strip() for t in message.text.split(',')]
    session = SessionLocal()
    for t in times:
        slot = ScheduleSlot(date=date, time=t, is_booked=False)
        session.add(slot)
    session.commit()
    session.close()
    await message.answer("Готово!")
    await state.clear()

# Редактирование окон
@dp.message(F.text == "📅 Редактировать окна")
async def edit_slots_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав.")
        return
    session = SessionLocal()
    slots = session.query(ScheduleSlot).order_by(ScheduleSlot.date, ScheduleSlot.time).all()
    session.close()
    if not slots:
        await message.answer("Нет ни одного окна.")
        return
    buttons = [types.InlineKeyboardButton(text=format_slot(slot), callback_data=f"editslot_{slot.id}") for slot in slots]
    await message.answer("Список окон:", reply_markup=inline_kb_3(buttons))
    await state.set_state(AdminStates.editing_slot_list)

@dp.callback_query(AdminStates.editing_slot_list, F.data.startswith("editslot_"))
async def edit_slot_selected(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    slot_id = int(callback.data.split("_")[1])
    await state.update_data(editing_slot_id=slot_id)
    session = SessionLocal()
    slot = session.query(ScheduleSlot).get(slot_id)
    session.close()
    kb = inline_kb_3([
        types.InlineKeyboardButton(text="📅 Дата", callback_data="action_changedate"),
        types.InlineKeyboardButton(text="🕒 Время", callback_data="action_changetime"),
        types.InlineKeyboardButton(text="🗑 Удалить", callback_data="action_delete"),
        types.InlineKeyboardButton(text="Отмена", callback_data="action_cancel"),
    ])
    await callback.message.edit_text(f"Слот: {format_slot(slot)}\nЧто сделать?", reply_markup=kb)
    await state.set_state(AdminStates.editing_slot_choose_action)

@dp.callback_query(AdminStates.editing_slot_choose_action, F.data == "action_changedate")
async def change_slot_date_prompt(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Введите новую дату (ДД.ММ.ГГГГ):")
    await state.set_state(AdminStates.editing_slot_new_date)

@dp.message(AdminStates.editing_slot_new_date)
async def change_slot_date(message: types.Message, state: FSMContext):
    parsed = parse_date_input(message.text.strip())
    if not parsed:
        await message.answer("Неверный формат. Введите ещё раз (ДД.ММ.ГГГГ):")
        return
    data = await state.get_data()
    slot_id = data['editing_slot_id']
    session = SessionLocal()
    slot = session.query(ScheduleSlot).get(slot_id)
    if not slot:
        await message.answer("Слот не найден.")
        session.close()
        await state.clear()
        return
    slot.date = parsed
    session.commit()
    if slot.is_booked:
        appt = session.query(Appointment).filter(Appointment.schedule_id == slot_id).first()
        if appt:
            client = session.query(User).get(appt.user_id)
            try:
                await bot.send_message(client.telegram_id, f"⚠️ Ваша запись перенесена на {format_date(parsed)} {slot.time}")
            except Exception as e:
                logging.error(f"Не удалось уведомить клиента {client.telegram_id}: {e}")
    session.close()
    await message.answer("Готово!")
    await state.clear()

@dp.callback_query(AdminStates.editing_slot_choose_action, F.data == "action_changetime")
async def change_slot_time_prompt(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Введите новое время (ЧЧ:ММ):")
    await state.set_state(AdminStates.editing_slot_new_time)

@dp.message(AdminStates.editing_slot_new_time)
async def change_slot_time(message: types.Message, state: FSMContext):
    new_time = message.text.strip()
    try:
        datetime.strptime(new_time, "%H:%M")
    except ValueError:
        await message.answer("Неверный формат времени. Введите ещё раз (ЧЧ:ММ):")
        return
    data = await state.get_data()
    slot_id = data['editing_slot_id']
    session = SessionLocal()
    slot = session.query(ScheduleSlot).get(slot_id)
    if not slot:
        await message.answer("Слот не найден.")
        session.close()
        await state.clear()
        return
    slot.time = new_time
    session.commit()
    if slot.is_booked:
        appt = session.query(Appointment).filter(Appointment.schedule_id == slot_id).first()
        if appt:
            client = session.query(User).get(appt.user_id)
            try:
                await bot.send_message(client.telegram_id, f"⚠️ Время вашей записи изменено на {new_time}")
            except Exception as e:
                logging.error(f"Не удалось уведомить клиента {client.telegram_id}: {e}")
    session.close()
    await message.answer("Готово!")
    await state.clear()

@dp.callback_query(AdminStates.editing_slot_choose_action, F.data == "action_delete")
async def delete_slot(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    slot_id = data['editing_slot_id']
    session = SessionLocal()
    slot = session.query(ScheduleSlot).get(slot_id)
    if slot:
        if slot.is_booked:
            appt = session.query(Appointment).filter(Appointment.schedule_id == slot_id).first()
            if appt:
                client = session.query(User).get(appt.user_id)
                try:
                    await bot.send_message(client.telegram_id, "⚠️ Ваша запись была отменена администратором.")
                except Exception as e:
                    logging.error(f"Не удалось уведомить клиента {client.telegram_id}: {e}")
                session.delete(appt)
        session.delete(slot)
        session.commit()
        await callback.message.edit_text("Готово!")
    else:
        await callback.message.edit_text("Слот не найден.")
    session.close()
    await state.clear()

@dp.callback_query(AdminStates.editing_slot_choose_action, F.data == "action_cancel")
async def cancel_slot_edit(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("Редактирование отменено.")
    await state.clear()

# Управление услугами
@dp.message(F.text == "💼 Управление услугами")
async def manage_services(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав.")
        return
    session = SessionLocal()
    services = session.query(Service).all()
    session.close()
    if not services:
        text = "Нет услуг. Добавьте новую."
    else:
        text = "Текущие услуги:\n"
        for s in services:
            text += f"🆔 {s.id}: {s.name} — {s.price}₽, {s.duration_minutes} мин\n"
    kb = inline_kb_3([
        types.InlineKeyboardButton(text="➕ Добавить", callback_data="svc_add"),
        types.InlineKeyboardButton(text="✏️ Редактировать", callback_data="svc_edit"),
        types.InlineKeyboardButton(text="🗑 Удалить", callback_data="svc_delete"),
    ])
    await message.answer(text, reply_markup=kb)
    await state.set_state(AdminStates.managing_services)

@dp.callback_query(AdminStates.managing_services, F.data == "svc_add")
async def add_service_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Введите название услуги:")
    await state.set_state(AdminStates.adding_service_name)

@dp.message(AdminStates.adding_service_name)
async def add_service_name(message: types.Message, state: FSMContext):
    await state.update_data(svc_name=message.text.strip())
    await message.answer("Введите описание (или '-' если нет):")
    await state.set_state(AdminStates.adding_service_description)

@dp.message(AdminStates.adding_service_description)
async def add_service_description(message: types.Message, state: FSMContext):
    desc = message.text.strip()
    if desc == "-":
        desc = None
    await state.update_data(svc_description=desc)
    await message.answer("Введите цену (число):")
    await state.set_state(AdminStates.adding_service_price)

@dp.message(AdminStates.adding_service_price)
async def add_service_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text.strip())
    except ValueError:
        await message.answer("Цена должна быть числом. Попробуйте ещё раз:")
        return
    await state.update_data(svc_price=price)
    await message.answer("Введите длительность в минутах (целое число):")
    await state.set_state(AdminStates.adding_service_duration)

@dp.message(AdminStates.adding_service_duration)
async def add_service_duration(message: types.Message, state: FSMContext):
    try:
        duration = int(message.text.strip())
    except ValueError:
        await message.answer("Длительность должна быть целым числом. Попробуйте ещё раз:")
        return
    await state.update_data(svc_duration=duration)
    await message.answer("Требуется ли предоплата? (да/нет):")
    await state.set_state(AdminStates.adding_service_prepayment)

@dp.message(AdminStates.adding_service_prepayment)
async def add_service_prepayment(message: types.Message, state: FSMContext):
    answer = message.text.strip().lower()
    prepayment_required = answer in ["да", "yes", "1", "y"]
    if prepayment_required:
        await message.answer("Введите сумму предоплаты (число):")
        await state.update_data(svc_prepayment_required=True)
        await state.set_state(AdminStates.adding_service_prepayment)  # временно
        prepayment_required = False
        await message.answer("Ввод суммы предоплаты пока не реализован. Услуга будет создана без предоплаты, вы сможете настроить её через редактирование.")
    data = await state.get_data()
    session = SessionLocal()
    new_service = Service(
        name=data['svc_name'],
        description=data.get('svc_description'),
        price=data['svc_price'],
        duration_minutes=data['svc_duration'],
        prepayment_required=False,
        prepayment_amount=0
    )
    session.add(new_service)
    session.commit()
    session.close()
    await message.answer("Готово!")
    await state.clear()

@dp.callback_query(AdminStates.managing_services, F.data == "svc_edit")
async def edit_service_select(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    session = SessionLocal()
    services = session.query(Service).all()
    session.close()
    if not services:
        await callback.message.answer("Нет услуг для редактирования.")
        await state.clear()
        return
    buttons = [types.InlineKeyboardButton(text=f"{s.name} (id {s.id})", callback_data=f"editsvc_{s.id}") for s in services]
    await callback.message.edit_text("Выберите услугу:", reply_markup=inline_kb_3(buttons))
    await state.set_state(AdminStates.editing_service_select)

@dp.callback_query(AdminStates.editing_service_select, F.data.startswith("editsvc_"))
async def edit_service_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    service_id = int(callback.data.split("_")[1])
    await state.update_data(editing_service_id=service_id)
    kb = inline_kb_3([
        types.InlineKeyboardButton(text="Название", callback_data="field_name"),
        types.InlineKeyboardButton(text="Описание", callback_data="field_description"),
        types.InlineKeyboardButton(text="Цена", callback_data="field_price"),
        types.InlineKeyboardButton(text="Длительность", callback_data="field_duration"),
        types.InlineKeyboardButton(text="Предоплата", callback_data="field_prepayment"),
    ])
    await callback.message.edit_text("Какое поле редактировать?", reply_markup=kb)
    await state.set_state(AdminStates.editing_service_field)

@dp.callback_query(AdminStates.editing_service_field)
async def edit_service_field(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    field = callback.data
    await state.update_data(editing_field=field)
    prompts = {
        "field_name": "Введите новое название:",
        "field_description": "Введите новое описание (или '-' для пустого):",
        "field_price": "Введите новую цену (число):",
        "field_duration": "Введите новую длительность (минут):",
        "field_prepayment": "Введите сумму предоплаты (0, если не нужна):",
    }
    await callback.message.answer(prompts[field])
    await state.set_state(AdminStates.editing_service_value)

@dp.message(AdminStates.editing_service_value)
async def edit_service_value(message: types.Message, state: FSMContext):
    data = await state.get_data()
    service_id = data['editing_service_id']
    field = data['editing_field']
    session = SessionLocal()
    service = session.query(Service).get(service_id)
    if not service:
        await message.answer("Услуга не найдена.")
        session.close()
        await state.clear()
        return
    value = message.text.strip()
    if field == "field_name":
        service.name = value
    elif field == "field_description":
        service.description = value if value != "-" else None
    elif field == "field_price":
        try:
            service.price = float(value)
        except ValueError:
            await message.answer("Неверное число. Отменено.")
            session.close()
            await state.clear()
            return
    elif field == "field_duration":
        try:
            service.duration_minutes = int(value)
        except ValueError:
            await message.answer("Неверное число. Отменено.")
            session.close()
            await state.clear()
            return
    elif field == "field_prepayment":
        try:
            amount = float(value)
            if amount > 0:
                service.prepayment_required = True
                service.prepayment_amount = amount
            else:
                service.prepayment_required = False
                service.prepayment_amount = 0
        except ValueError:
            await message.answer("Неверное число. Отменено.")
            session.close()
            await state.clear()
            return
    session.commit()
    session.close()
    await message.answer("Готово!")
    await state.clear()

@dp.callback_query(AdminStates.managing_services, F.data == "svc_delete")
async def delete_service_select(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    session = SessionLocal()
    services = session.query(Service).all()
    session.close()
    if not services:
        await callback.message.answer("Нет услуг для удаления.")
        await state.clear()
        return
    buttons = [types.InlineKeyboardButton(text=f"{s.name} (id {s.id})", callback_data=f"delsvc_{s.id}") for s in services]
    await callback.message.edit_text("Выберите услугу для удаления:", reply_markup=inline_kb_3(buttons))
    await state.set_state(AdminStates.managing_services)

@dp.callback_query(F.data.startswith("delsvc_"))
async def delete_service(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    service_id = int(callback.data.split("_")[1])
    session = SessionLocal()
    service = session.query(Service).get(service_id)
    if service:
        has_appointments = session.query(Appointment).filter(Appointment.service_id == service_id).count() > 0
        if has_appointments:
            await callback.message.answer("Нельзя удалить услугу, так как есть записи.")
        else:
            session.delete(service)
            session.commit()
            await callback.message.answer("Готово!")
    else:
        await callback.message.answer("Услуга не найдена.")
    session.close()
    await state.clear()

# Редактирование текстов
@dp.message(F.text.in_(["📝 Редактировать ЧаВо", "📩 Сообщение с реквизитами", "📨 Сообщение об успешной записи", "📍 Адрес (текст)", "💬 Текст консультации", "📢 Текст пожеланий"]))
async def edit_text_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав.")
        return
    mapping = {
        "📝 Редактировать ЧаВо": ("faq_text", "Введите новый текст ЧаВо:"),
        "📩 Сообщение с реквизитами": ("confirmation_text", "Введите текст сообщения с реквизитами (можно использовать {service_name}, {date}, {time}, {prepayment_amount}):"),
        "📨 Сообщение об успешной записи": ("success_message", "Введите текст сообщения об успешной записи (можно использовать {date}, {time}, {service_name}, {admin_username}):"),
        "📍 Адрес (текст)": ("address_text", "Введите текст адреса:"),
        "💬 Текст консультации": ("consultation_text", "Введите текст консультации для клиента:"),
        "📢 Текст пожеланий": ("wishes_text", "Введите текст запроса пожеланий (при выборе 'Я знаю что хочу'):"),
    }
    key, prompt = mapping[message.text]
    await state.update_data(edit_key=key)
    await message.answer(prompt)
    await state.set_state(AdminStates.editing_any_text_value)

@dp.message(AdminStates.editing_any_text_value)
async def save_any_text(message: types.Message, state: FSMContext):
    new_text = message.text.strip()
    if not new_text:
        await message.answer("Текст не может быть пустым. Попробуйте ещё раз:")
        return
    data = await state.get_data()
    key = data['edit_key']
    session = SessionLocal()
    setting = session.query(Setting).filter(Setting.key == key).first()
    if setting:
        setting.value = new_text
    else:
        session.add(Setting(key=key, value=new_text))
    session.commit()
    session.close()
    await message.answer("Готово!")
    await state.clear()

# Внесение пожеланий клиента
@dp.message(F.text == "✏️ Внести пожелания клиента")
async def enter_notes_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав.")
        return
    session = SessionLocal()
    appointments = session.query(Appointment).all()
    session.close()
    if not appointments:
        await message.answer("Нет записей.")
        return
    buttons = []
    for app in appointments:
        slot = session.query(ScheduleSlot).get(app.schedule_id)
        client = session.query(User).get(app.user_id)
        date_display = format_date(slot.date)
        buttons.append(types.InlineKeyboardButton(text=f"{date_display} {slot.time} - {client.full_name}", callback_data=f"notes_{app.id}"))
    await message.answer("Выберите запись:", reply_markup=inline_kb_3(buttons))
    await state.set_state(AdminStates.entering_notes_select)

@dp.callback_query(AdminStates.entering_notes_select, F.data.startswith("notes_"))
async def enter_notes_select(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    app_id = int(callback.data.split("_")[1])
    await state.update_data(app_id=app_id)
    await callback.message.answer("Введите пожелания клиента:")
    await state.set_state(AdminStates.entering_notes_value)

@dp.message(AdminStates.entering_notes_value)
async def save_client_notes(message: types.Message, state: FSMContext):
    data = await state.get_data()
    app_id = data['app_id']
    notes = message.text.strip()
    session = SessionLocal()
    app = session.query(Appointment).get(app_id)
    if app:
        app.client_notes = notes
        session.commit()
        await message.answer("Готово!")
    else:
        await message.answer("Запись не найдена.")
    session.close()
    await state.clear()

# Удаление всех окон
@dp.message(F.text == "🗑 Удалить все записи")
async def delete_all_appointments(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав.")
        return
    kb = inline_kb_3([
        types.InlineKeyboardButton(text="✅ Да, удалить все окна", callback_data="delall_yes"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data="delall_no"),
    ])
    await message.answer("Вы уверены, что хотите удалить ВСЕ ОКНА и связанные записи?", reply_markup=kb)
    await state.set_state(AdminStates.confirming_delete_all)

@dp.callback_query(AdminStates.confirming_delete_all, F.data == "delall_yes")
async def delete_all_confirm(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    session = SessionLocal()
    # Удаляем все записи
    session.query(Appointment).delete()
    # Удаляем все слоты
    session.query(ScheduleSlot).delete()
    session.commit()
    session.close()
    await callback.message.edit_text("Все окна и записи удалены.")
    await state.clear()

@dp.callback_query(AdminStates.confirming_delete_all, F.data == "delall_no")
async def delete_all_cancel(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("Отменено.")
    await state.clear()

# Просмотр записей (кнопки)
@dp.message(F.text == "📋 Все записи")
async def show_appointments_admin(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав.")
        return
    session = SessionLocal()
    appointments = session.query(Appointment).all()
    session.close()
    if not appointments:
        await message.answer("Записей нет.")
        return
    buttons = []
    for app in appointments:
        slot = session.query(ScheduleSlot).get(app.schedule_id)
        client = session.query(User).get(app.user_id)
        date_display = format_date(slot.date)
        buttons.append(types.InlineKeyboardButton(text=f"{date_display} {slot.time} - {client.full_name}", callback_data=f"app_{app.id}"))
    await message.answer("Записи (нажмите для деталей):", reply_markup=inline_kb_3(buttons))

@dp.callback_query(F.data.startswith("app_"))
async def show_appointment_detail(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав.", show_alert=True)
        return
    app_id = int(callback.data.split("_")[1])
    session = SessionLocal()
    app = session.query(Appointment).get(app_id)
    if not app:
        await callback.answer("Запись не найдена.", show_alert=True)
        session.close()
        return
    slot = session.query(ScheduleSlot).get(app.schedule_id)
    client = session.query(User).get(app.user_id)
    service = session.query(Service).get(app.service_id)
    date_display = format_date(slot.date)
    text = (f"📋 Запись #{app.id}\n"
            f"Клиент: {client.full_name} (@{client.username})\n"
            f"Телефон: {client.phone}\n"
            f"Услуга: {service.name}\n"
            f"Дата: {date_display} {slot.time}\n"
            f"Пожелания: {app.client_notes or 'нет'}\n")
    if app.prepayment_screenshot:
        await callback.message.answer(text)
        await callback.message.answer_photo(app.prepayment_screenshot)
    else:
        await callback.message.answer(text)
    session.close()
    await callback.answer()

# Просмотр клиентов
@dp.message(F.text == "📊 Клиенты")
async def show_clients(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав.")
        return
    session = SessionLocal()
    users = session.query(User).filter(User.role == 'client').all()
    session.close()
    if not users:
        await message.answer("Клиентов пока нет.")
        return
    text = "База клиентов:\n\n"
    for u in users:
        text += f"👤 {u.full_name} (@{u.username}) — {u.phone}\n"
    await message.answer(text)

# ---------- ЗАПУСК ----------
async def main():
    session = SessionLocal()
    defaults = {
        'faq_text': "Часто задаваемые вопросы:\n\n❓ Как подготовиться?\n— Приходите без макияжа глаз.\n\n❓ Сколько держатся ресницы?\n— 2–4 недели.\n\n❓ Можно ли мочить глаза?\n— В первые 24 часа не рекомендуется.",
        'confirmation_text': "Для подтверждения записи необходимо внести предоплату {prepayment_amount} руб.\nПереведите на карту 1234 5678 9012 3456 и отправьте скриншот.",
        'success_message': "✅ Вы успешно записаны на {date} {time}.\nУслуга: {service_name}\nЕсли возникнут вопросы, обратитесь к @{admin_username}.",
        'address_text': "📍 Наш адрес: г. Москва, ул. Примерная, д. 1",
        'consultation_text': "💬 Для консультации напишите @username",
        'wishes_text': "✍️ Напишите параметры ресниц или отправьте фото.",
    }
    for key, value in defaults.items():
        if session.query(Setting).filter(Setting.key == key).first() is None:
            session.add(Setting(key=key, value=value))
    if session.query(Service).count() == 0:
        services = [
            Service(name="Классическое наращивание", price=2500, duration_minutes=120, prepayment_required=True, prepayment_amount=500),
            Service(name="2D наращивание", price=3000, duration_minutes=150, prepayment_required=True, prepayment_amount=500),
            Service(name="Коррекция", price=1800, duration_minutes=90, prepayment_required=False)
        ]
        session.add_all(services)
    session.commit()
    session.close()

    await asyncio.gather(
        run_web_server(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    asyncio.run(main())
