import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey, Float
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
    choosing_date = State()
    choosing_time = State()
    waiting_screenshot = State()

class AdminStates(StatesGroup):
    # Добавление слотов
    adding_slot_date = State()
    adding_slot_time = State()

    # Редактирование слотов
    editing_slot_list = State()
    editing_slot_choose_action = State()
    editing_slot_new_date = State()
    editing_slot_new_time = State()

    # Управление услугами
    managing_services = State()
    adding_service_name = State()
    adding_service_description = State()
    adding_service_price = State()
    adding_service_duration = State()
    adding_service_prepayment = State()
    editing_service_select = State()
    editing_service_field = State()
    editing_service_value = State()

    # Редактирование ЧаВо
    managing_faq = State()
    editing_faq_text = State()

    # Редактирование сообщения подтверждения
    managing_confirmation = State()
    editing_confirmation_text = State()

# ---------- КЛАВИАТУРЫ ----------
def main_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="📋 Услуги и цены")],
            [types.KeyboardButton(text="📅 Записаться")],
            [types.KeyboardButton(text="👤 Мои записи")],
            [types.KeyboardButton(text="❓ ЧаВо")],
        ],
        resize_keyboard=True
    )

def admin_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="➕ Добавить окна")],
            [types.KeyboardButton(text="📅 Редактировать окна")],
            [types.KeyboardButton(text="💼 Управление услугами")],
            [types.KeyboardButton(text="📝 Редактировать ЧаВо")],
            [types.KeyboardButton(text="📩 Сообщение подтверждения")],
            [types.KeyboardButton(text="📋 Все записи")],
            [types.KeyboardButton(text="📊 Клиенты")],
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

def format_slot(slot):
    status = "занято" if slot.is_booked else "свободно"
    return f"{slot.date} {slot.time} ({status})"

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
    if faq:
        await message.answer(faq.value)
    else:
        await message.answer("Раздел пока не заполнен.")

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
        text += f"📅 {slot.date} в {slot.time} — {service.name}, статус: {app.status}\n"
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
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=f"{s.name} - {s.price}₽", callback_data=f"service_{s.id}")]
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
        # Читаем текст сообщения подтверждения из базы
        confirmation = session.query(Setting).filter(Setting.key == 'confirmation_text').first()
        if confirmation:
            text = confirmation.value.format(
                service_name=service.name,
                date=data['date'],
                time=slot.time,
                prepayment_amount=service.prepayment_amount
            )
        else:
            # Fallback на старый текст
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
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, f"🆕 Новая запись!\nКлиент: {user.full_name} (@{user.username})\nУслуга: {service.name}\nДата: {data['date']}\nВремя: {slot.time}")
            except Exception as e:
                logging.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")
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
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS пуст, скриншот не отправлен")
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(admin_id, file_id, caption=f"🧾 Предоплата от {user.full_name} (@{user.username})\nУслуга: {service.name}\nДата: {data['date']}\nВремя: {slot.time}\nСумма: {service.prepayment_amount} руб.")
        except Exception as e:
            logging.error(f"Не удалось отправить скриншот админу {admin_id}: {e}")
    await state.clear()
    session.close()

# ---------- АДМИН-ПАНЕЛЬ ----------
# ---------- ДОБАВЛЕНИЕ ОКОН ----------
@dp.message(F.text == "➕ Добавить окна")
async def add_slots_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав.")
        return
    await message.answer("Введите дату в формате ГГГГ.ММ.ДД (например, 2025.05.20):")
    await state.set_state(AdminStates.adding_slot_date)

@dp.message(AdminStates.adding_slot_date)
async def add_slot_date(message: types.Message, state: FSMContext):
    date_text = message.text.strip()
    try:
        datetime.strptime(date_text, "%Y.%m.%d")
    except ValueError:
        await message.answer("Неверный формат даты. Введите ещё раз (ГГГГ.ММ.ДД):")
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
    await message.answer("Готово!")
    await state.clear()

# ---------- РЕДАКТИРОВАНИЕ ОКОН ----------
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
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=format_slot(slot), callback_data=f"editslot_{slot.id}")]
        for slot in slots
    ])
    await message.answer("Список окон (нажмите для редактирования):", reply_markup=keyboard)
    await state.set_state(AdminStates.editing_slot_list)

@dp.callback_query(AdminStates.editing_slot_list, F.data.startswith("editslot_"))
async def edit_slot_selected(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    slot_id = int(callback.data.split("_")[1])
    await state.update_data(editing_slot_id=slot_id)
    session = SessionLocal()
    slot = session.query(ScheduleSlot).get(slot_id)
    session.close()
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📅 Изменить дату", callback_data="action_changedate")],
        [types.InlineKeyboardButton(text="🕒 Изменить время", callback_data="action_changetime")],
        [types.InlineKeyboardButton(text="🗑 Удалить слот", callback_data="action_delete")],
        [types.InlineKeyboardButton(text="Отмена", callback_data="action_cancel")]
    ])
    await callback.message.edit_text(f"Слот: {slot.date} {slot.time}\nВыберите действие:", reply_markup=keyboard)
    await state.set_state(AdminStates.editing_slot_choose_action)

@dp.callback_query(AdminStates.editing_slot_choose_action, F.data == "action_changedate")
async def change_slot_date_prompt(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Введите новую дату в формате ГГГГ.ММ.ДД (например, 2025.06.01):")
    await state.set_state(AdminStates.editing_slot_new_date)

@dp.message(AdminStates.editing_slot_new_date)
async def change_slot_date(message: types.Message, state: FSMContext):
    new_date = message.text.strip()
    try:
        datetime.strptime(new_date, "%Y.%m.%d")
    except ValueError:
        await message.answer("Неверный формат даты. Введите ещё раз (ГГГГ.ММ.ДД):")
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
    slot.date = new_date
    session.commit()
    # Если слот забронирован, уведомим клиента (опционально)
    if slot.is_booked:
        appt = session.query(Appointment).filter(Appointment.schedule_id == slot_id).first()
        if appt:
            client = session.query(User).get(appt.user_id)
            try:
                await bot.send_message(client.telegram_id, f"⚠️ Ваша запись перенесена на новую дату: {new_date} {slot.time}")
            except Exception as e:
                logging.error(f"Не удалось уведомить клиента {client.telegram_id}: {e}")
    session.close()
    await message.answer("Готово!")
    await state.clear()

@dp.callback_query(AdminStates.editing_slot_choose_action, F.data == "action_changetime")
async def change_slot_time_prompt(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Введите новое время в формате ЧЧ:ММ (например, 12:00):")
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
                await bot.send_message(client.telegram_id, f"⚠️ Время вашей записи изменено: {slot.date} {new_time}")
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
        # Если слот забронирован, удалим связанную запись и уведомим клиента
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

# ---------- УПРАВЛЕНИЕ УСЛУГАМИ ----------
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
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="➕ Добавить услугу", callback_data="svc_add")],
        [types.InlineKeyboardButton(text="✏️ Редактировать услугу", callback_data="svc_edit")],
        [types.InlineKeyboardButton(text="🗑 Удалить услугу", callback_data="svc_delete")],
    ])
    await message.answer(text, reply_markup=keyboard)
    await state.set_state(AdminStates.managing_services)

# Добавление услуги
@dp.callback_query(AdminStates.managing_services, F.data == "svc_add")
async def add_service_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Введите название услуги:")
    await state.set_state(AdminStates.adding_service_name)

@dp.message(AdminStates.adding_service_name)
async def add_service_name(message: types.Message, state: FSMContext):
    await state.update_data(svc_name=message.text.strip())
    await message.answer("Введите описание (или отправьте '-', если нет):")
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
        # Используем то же состояние для ввода суммы, но лучше отдельное
        await state.set_state(AdminStates.adding_service_prepayment)  # временно
        # Для простоты допустим, что пользователь введёт сумму
        # Но мы не будем реализовывать ввод суммы в этом состоянии,
        # а завершим создание без предоплаты и попросим настроить через редактирование
        prepayment_required = False
        await message.answer("Ввод суммы предоплаты пока не реализован. Услуга будет создана без предоплаты, вы сможете настроить её через редактирование.")
    # Сохраняем услугу
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

# Редактирование услуги
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
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=f"{s.name} (id {s.id})", callback_data=f"editsvc_{s.id}")]
        for s in services
    ])
    await callback.message.edit_text("Выберите услугу для редактирования:", reply_markup=keyboard)
    await state.set_state(AdminStates.editing_service_select)

@dp.callback_query(AdminStates.editing_service_select, F.data.startswith("editsvc_"))
async def edit_service_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    service_id = int(callback.data.split("_")[1])
    await state.update_data(editing_service_id=service_id)
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Название", callback_data="field_name")],
        [types.InlineKeyboardButton(text="Описание", callback_data="field_description")],
        [types.InlineKeyboardButton(text="Цена", callback_data="field_price")],
        [types.InlineKeyboardButton(text="Длительность", callback_data="field_duration")],
        [types.InlineKeyboardButton(text="Предоплата", callback_data="field_prepayment")],
    ])
    await callback.message.edit_text("Какое поле редактировать?", reply_markup=keyboard)
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

# Удаление услуги
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
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=f"{s.name} (id {s.id})", callback_data=f"delsvc_{s.id}")]
        for s in services
    ])
    await callback.message.edit_text("Выберите услугу для удаления:", reply_markup=keyboard)
    await state.set_state(AdminStates.managing_services)  # остаёмся, ловим delsvc_ ниже

@dp.callback_query(F.data.startswith("delsvc_"))
async def delete_service(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    service_id = int(callback.data.split("_")[1])
    session = SessionLocal()
    service = session.query(Service).get(service_id)
    if service:
        # Проверим, есть ли связанные записи
        has_appointments = session.query(Appointment).filter(Appointment.service_id == service_id).count() > 0
        if has_appointments:
            await callback.message.answer("Нельзя удалить услугу, так как есть записи. Сначала удалите или перенесите записи.")
        else:
            session.delete(service)
            session.commit()
            await callback.message.answer("Готово!")
    else:
        await callback.message.answer("Услуга не найдена.")
    session.close()
    await state.clear()

# ---------- РЕДАКТИРОВАНИЕ ЧАВО ----------
@dp.message(F.text == "📝 Редактировать ЧаВо")
async def edit_faq_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав.")
        return
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✏️ Редактировать", callback_data="faq_edit")],
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="faq_cancel")],
    ])
    await message.answer("Выберите действие:", reply_markup=keyboard)
    await state.set_state(AdminStates.managing_faq)

@dp.callback_query(AdminStates.managing_faq, F.data == "faq_edit")
async def faq_edit_prompt(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Введите новый текст ЧаВо:")
    await state.set_state(AdminStates.editing_faq_text)

@dp.callback_query(AdminStates.managing_faq, F.data == "faq_cancel")
async def faq_cancel(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("Отменено.")
    await state.clear()

@dp.message(AdminStates.editing_faq_text)
async def faq_save(message: types.Message, state: FSMContext):
    new_text = message.text.strip()
    if not new_text:
        await message.answer("Текст не может быть пустым. Попробуйте ещё раз:")
        return
    session = SessionLocal()
    setting = session.query(Setting).filter(Setting.key == 'faq_text').first()
    if setting:
        setting.value = new_text
    else:
        session.add(Setting(key='faq_text', value=new_text))
    session.commit()
    session.close()
    await message.answer("Готово!")
    await state.clear()

# ---------- РЕДАКТИРОВАНИЕ СООБЩЕНИЯ ПОДТВЕРЖДЕНИЯ ----------
@dp.message(F.text == "📩 Сообщение подтверждения")
async def edit_confirmation_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав.")
        return
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✏️ Редактировать", callback_data="conf_edit")],
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="conf_cancel")],
    ])
    await message.answer("Выберите действие:", reply_markup=keyboard)
    await state.set_state(AdminStates.managing_confirmation)

@dp.callback_query(AdminStates.managing_confirmation, F.data == "conf_edit")
async def confirmation_edit_prompt(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "Введите новый текст сообщения подтверждения.\n"
        "Можно использовать переменные:\n"
        "{service_name} — название услуги\n"
        "{date} — дата\n"
        "{time} — время\n"
        "{prepayment_amount} — сумма предоплаты\n\n"
        "Пример:\n"
        "Услуга: {service_name}\nДата: {date}\nВремя: {time}\nПредоплата: {prepayment_amount} руб.\nПереведите на карту 1234 5678 9012 3456 и отправьте скриншот."
    )
    await state.set_state(AdminStates.editing_confirmation_text)

@dp.callback_query(AdminStates.managing_confirmation, F.data == "conf_cancel")
async def confirmation_cancel(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("Отменено.")
    await state.clear()

@dp.message(AdminStates.editing_confirmation_text)
async def confirmation_save(message: types.Message, state: FSMContext):
    new_text = message.text.strip()
    if not new_text:
        await message.answer("Текст не может быть пустым. Попробуйте ещё раз:")
        return
    session = SessionLocal()
    setting = session.query(Setting).filter(Setting.key == 'confirmation_text').first()
    if setting:
        setting.value = new_text
    else:
        session.add(Setting(key='confirmation_text', value=new_text))
    session.commit()
    session.close()
    await message.answer("Готово!")
    await state.clear()

# ---------- ПРОСМОТР ЗАПИСЕЙ ----------
@dp.message(F.text == "📋 Все записи")
async def show_all_appointments(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав.")
        return
    session = SessionLocal()
    today = datetime.now().date()
    end_date = today + timedelta(days=7)
    appointments = session.query(Appointment).join(ScheduleSlot).filter(
        ScheduleSlot.date >= today.strftime("%Y.%m.%d"),
        ScheduleSlot.date <= end_date.strftime("%Y.%m.%d")
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
        text += f"📅 {slot.date} в {slot.time} — {user.full_name} ({service.name}), статус: {app.status}\n"
    await message.answer(text)
    session.close()

# ---------- ПРОСМОТР КЛИЕНТОВ ----------
@dp.message(F.text == "📊 Клиенты")
async def show_clients(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав.")
        return
    session = SessionLocal()
    users = session.query(User).filter(User.role == 'client').all()
    if not users:
        await message.answer("Клиентов пока нет.")
        session.close()
        return
    text = "База клиентов:\n\n"
    for u in users:
        text += f"👤 {u.full_name} (@{u.username}) — {u.phone}\n"
    await message.answer(text)
    session.close()

# ---------- ЗАПУСК ----------
async def main():
    session = SessionLocal()
    # Добавляем дефолтные настройки, если их нет
    if session.query(Setting).filter(Setting.key == 'faq_text').first() is None:
        default_faq = (
            "Часто задаваемые вопросы:\n\n"
            "❓ Как подготовиться?\n— Приходите без макияжа глаз.\n\n"
            "❓ Сколько держатся ресницы?\n— 2–4 недели.\n\n"
            "❓ Можно ли мочить глаза?\n— В первые 24 часа не рекомендуется."
        )
        session.add(Setting(key='faq_text', value=default_faq))
        session.commit()
    if session.query(Setting).filter(Setting.key == 'confirmation_text').first() is None:
        default_confirmation = (
            "Вы выбрали:\n"
            "Услуга: {service_name}\n"
            "Дата: {date}\n"
            "Время: {time}\n\n"
            "Для подтверждения записи необходимо внести предоплату {prepayment_amount} руб.\n"
            "Переведите на карту 1234 5678 9012 3456 и отправьте скриншот чека."
        )
        session.add(Setting(key='confirmation_text', value=default_confirmation))
        session.commit()
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
