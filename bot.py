import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

# ---------- НАСТРОЙКИ ----------
BOT_TOKEN = "8612365391:AAHktx_X-KLGi0WzdfsqExa2PNrDU0A5LFg"     
ADMIN_CHAT_ID = 1904488883           

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
    date = Column(String)   # формат YYYY-MM-DD
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
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship('User', back_populates='appointments')
    service = relationship('Service')
    slot = relationship('ScheduleSlot', back_populates='appointment')

# Подключение к SQLite
engine = create_engine('sqlite:///bot.db', connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# ---------- ИНИЦИАЛИЗАЦИЯ БОТА ----------
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ---------- СОСТОЯНИЯ FSM ----------
class BookingStates(StatesGroup):
    choosing_service = State()
    choosing_date = State()
    choosing_time = State()
    waiting_screenshot = State()

class AdminStates(StatesGroup):
    adding_slot_date = State()
    adding_slot_time = State()

# ---------- КЛАВИАТУРЫ ----------
def main_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="?? Услуги и цены")],
            [types.KeyboardButton(text="?? Записаться")],
            [types.KeyboardButton(text="?? Мои записи")],
            [types.KeyboardButton(text="? FAQ")],
        ],
        resize_keyboard=True
    )

def admin_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="? Добавить окна")],
            [types.KeyboardButton(text="?? Все записи")],
            [types.KeyboardButton(text="?? Клиенты")],
        ],
        resize_keyboard=True
    )

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

# ---------- ОБРАБОТЧИКИ КОМАНД ----------
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    # Проверяем, есть ли телефон
    session = SessionLocal()
    user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
    session.close()
    if user.phone:
        await message.answer("С возвращением!", reply_markup=main_keyboard())
    else:
        # Просим номер телефона
        keyboard = types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="?? Отправить контакт", request_contact=True)]],
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
    if message.from_user.id != ADMIN_CHAT_ID:
        await message.answer("У вас нет прав администратора.")
        return
    await message.answer("Админ-панель:", reply_markup=admin_keyboard())

# ---------- ОСНОВНОЕ МЕНЮ ----------
@dp.message(F.text == "?? Услуги и цены")
async def show_services(message: types.Message):
    session = SessionLocal()
    services = session.query(Service).all()
    if not services:
        await message.answer("Услуги пока не добавлены.")
        session.close()
        return
    text = "Наши услуги:\n\n"
    for s in services:
        text += f"?? {s.name} — {s.price} руб. ({s.duration_minutes} мин)\n"
        if s.description:
            text += f"   {s.description}\n"
        if s.prepayment_required:
            text += f"   Предоплата: {s.prepayment_amount} руб.\n"
        text += "\n"
    await message.answer(text)
    session.close()

@dp.message(F.text == "? FAQ")
async def show_faq(message: types.Message):
    await message.answer(
        "Частые вопросы:\n\n"
        "? Как подготовиться?\n— Приходите без макияжа глаз.\n\n"
        "? Сколько держатся ресницы?\n— 2–4 недели.\n\n"
        "? Можно ли мочить глаза?\n— В первые 24 часа не рекомендуется."
    )

@dp.message(F.text == "?? Мои записи")
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
        text += f"?? {slot.date} в {slot.time} — {service.name}, статус: {app.status}\n"
    await message.answer(text)
    session.close()

# ---------- ЗАПИСЬ ----------
@dp.message(F.text == "?? Записаться")
async def start_booking(message: types.Message, state: FSMContext):
    session = SessionLocal()
    services = session.query(Service).all()
    if not services:
        await message.answer("Нет доступных услуг.")
        session.close()
        return
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=f"{s.name} - {s.price}?", callback_data=f"service_{s.id}")]
        for s in services
    ])
    await message.answer("Выберите услугу:", reply_markup=keyboard)
    await state.set_state(BookingStates.choosing_service)
    session.close()

@dp.callback_query(BookingStates.choosing_service)
async def service_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    service_id = int(callback.data.split("_")[1])
    await state.update_data(service_id=service_id)
    session = SessionLocal()
    dates = session.query(ScheduleSlot.date).filter(ScheduleSlot.is_booked == False).distinct().order_by(ScheduleSlot.date).limit(10).all()
    if not dates:
        await callback.message.answer("Свободных окон пока нет.")
        await state.clear()
        session.close()
        return
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=d[0], callback_data=f"date_{d[0]}")] for d in dates
    ])
    await callback.message.edit_text("Выберите дату:", reply_markup=keyboard)
    await state.set_state(BookingStates.choosing_date)
    session.close()

@dp.callback_query(BookingStates.choosing_date)
async def date_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    date = callback.data.split("_")[1]
    await state.update_data(date=date)
    session = SessionLocal()
    slots = session.query(ScheduleSlot).filter(ScheduleSlot.date == date, ScheduleSlot.is_booked == False).order_by(ScheduleSlot.time).all()
    if not slots:
        await callback.message.answer("На эту дату нет свободных окон.")
        await state.clear()
        session.close()
        return
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=slot.time, callback_data=f"time_{slot.id}")] for slot in slots
    ])
    await callback.message.edit_text("Выберите время:", reply_markup=keyboard)
    await state.set_state(BookingStates.choosing_time)
    session.close()

@dp.callback_query(BookingStates.choosing_time)
async def time_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    slot_id = int(callback.data.split("_")[1])
    await state.update_data(slot_id=slot_id)
    data = await state.get_data()
    service_id = data['service_id']
    session = SessionLocal()
    service = session.query(Service).get(service_id)
    slot = session.query(ScheduleSlot).get(slot_id)
    if service.prepayment_required:
        text = (f"Вы выбрали:\n"
                f"Услуга: {service.name}\n"
                f"Дата: {data['date']}\n"
                f"Время: {slot.time}\n\n"
                f"Для подтверждения записи необходимо внести предоплату {service.prepayment_amount} руб.\n"
                f"Переведите на карту 1234 5678 9012 3456 и отправьте скриншот чека.")
        await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="Я отправил(а) скриншот", callback_data="sent_screenshot")]
        ]))
        await state.set_state(BookingStates.waiting_screenshot)
    else:
        user = session.query(User).filter(User.telegram_id == callback.from_user.id).first()
        appointment = Appointment(user_id=user.id, service_id=service_id, schedule_id=slot_id, status='pending')
        slot.is_booked = True
        session.add(appointment)
        session.commit()
        await callback.message.edit_text("Запись создана! Ожидайте подтверждения мастера.")
        await bot.send_message(ADMIN_CHAT_ID, f"?? Новая запись!\nКлиент: {user.full_name} (@{user.username})\nУслуга: {service.name}\nДата: {data['date']}\nВремя: {slot.time}")
        await state.clear()
    session.close()

@dp.callback_query(BookingStates.waiting_screenshot, F.data == "sent_screenshot")
async def ask_for_screenshot(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.answer("Пожалуйста, отправьте фото скриншота перевода.")

@dp.message(BookingStates.waiting_screenshot, F.photo)
async def screenshot_received(message: types.Message, state: FSMContext):
    data = await state.get_data()
    service_id = data['service_id']
    slot_id = data['slot_id']
    session = SessionLocal()
    user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
    service = session.query(Service).get(service_id)
    slot = session.query(ScheduleSlot).get(slot_id)
    file_id = message.photo[-1].file_id
    appointment = Appointment(user_id=user.id, service_id=service_id, schedule_id=slot_id, status='pending', prepayment_screenshot=file_id)
    slot.is_booked = True
    session.add(appointment)
    session.commit()
    await message.answer("Спасибо! Ваша запись ожидает подтверждения мастера.")
    await bot.send_photo(ADMIN_CHAT_ID, file_id, caption=f"?? Предоплата от {user.full_name} (@{user.username})\nУслуга: {service.name}\nДата: {data['date']}\nВремя: {slot.time}\nСумма: {service.prepayment_amount} руб.")
    await state.clear()
    session.close()

# ---------- АДМИН-ПАНЕЛЬ ----------
@dp.message(F.text == "? Добавить окна")
async def add_slots_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_CHAT_ID:
        return
    await message.answer("Введите дату в формате ГГГГ-ММ-ДД (например, 2025-05-20):")
    await state.set_state(AdminStates.adding_slot_date)

@dp.message(AdminStates.adding_slot_date)
async def add_slot_date(message: types.Message, state: FSMContext):
    date_text = message.text.strip()
    try:
        datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError:
        await message.answer("Неверный формат. Введите ещё раз:")
        return
    await state.update_data(date=date_text)
    await message.answer("Теперь введите время через запятую (например: 10:00, 11:30, 15:00):")
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
    await message.answer(f"Добавлено окон: {len(times)} на {date}")
    await state.clear()

@dp.message(F.text == "?? Все записи")
async def show_all_appointments(message: types.Message):
    if message.from_user.id != ADMIN_CHAT_ID:
        return
    session = SessionLocal()
    today = datetime.now().date()
    end_date = today + timedelta(days=7)
    appointments = session.query(Appointment).join(ScheduleSlot).filter(
        ScheduleSlot.date >= today.strftime("%Y-%m-%d"),
        ScheduleSlot.date <= end_date.strftime("%Y-%m-%d")
    ).all()
    if not appointments:
        await message.answer("На ближайшую неделю записей нет.")
        session.close()
        return
    text = "Записи на неделю:\n\n"
    for app in appointments:
        user = session.query(User).get(app.user_id)
        service = session.query(Service).get(app.service_id)
        slot = session.query(ScheduleSlot).get(app.schedule_id)
        text += f"?? {slot.date} в {slot.time} — {user.full_name} ({service.name}), статус: {app.status}\n"
    await message.answer(text)
    session.close()

@dp.message(F.text == "?? Клиенты")
async def show_clients(message: types.Message):
    if message.from_user.id != ADMIN_CHAT_ID:
        return
    session = SessionLocal()
    users = session.query(User).filter(User.role == 'client').all()
    if not users:
        await message.answer("Клиентов пока нет.")
        session.close()
        return
    text = "База клиентов:\n\n"
    for u in users:
        text += f"?? {u.full_name} (@{u.username}) — {u.phone}\n"
    await message.answer(text)
    session.close()

# ---------- ЗАПУСК ----------
async def main():
    # Добавим тестовые услуги, если база пустая
    session = SessionLocal()
    if session.query(Service).count() == 0:
        services = [
            Service(name="Классическое наращивание", price=2500, duration_minutes=120, prepayment_required=True, prepayment_amount=500),
            Service(name="2D наращивание", price=3000, duration_minutes=150, prepayment_required=True, prepayment_amount=500),
            Service(name="Коррекция", price=1800, duration_minutes=90, prepayment_required=False)
        ]
        session.add_all(services)
        session.commit()
    session.close()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())