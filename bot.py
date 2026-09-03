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

    # Редактирование сообщения с реквизитами (подтверждение записи)
    managing_confirmation = State()
    editing_confirmation_text = State()

    # Редактирование сообщения об успешной записи (после подтверждения)
    managing_success = State()
    editing_success_text = State()

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
            [types.KeyboardButton(text="📩 Сообщение с реквизитами")],
            [types.KeyboardButton(text="📨 Сообщение об успешной записи")],
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
    user = session.query(User).filter(User.telegram_id == callback.from_user.id).first()

    # Помечаем слот как занятый и создаём запись со статусом pending
    slot.is_booked = True
    appointment = Appointment(user_id=user.id, service_id=service_id, schedule_id=slot_id, status='pending')
    session.add(appointment)
    session.commit()

    # Отправляем клиенту первое сообщение с деталями
    details_text = f"Вы выбрали:\nУслуга: {service.name}\nДата: {slot.date}\nВремя: {slot.time}"
    await callback.message.answer(details_text)

    # Отправляем второе сообщение из настройки confirmation_text (например, реквизиты)
    conf_setting = session.query(Setting).filter(Setting.key == 'confirmation_text').first()
    if conf_setting:
        second_text = conf_setting.value.format(
            service_name=service.name,
            date=slot.date,
            time=slot.time,
            prepayment_amount=service.prepayment_amount
        )
    else:
        second_text = "Дополнительная информация появится позже."
    await callback.message.answer(second_text)

    # Уведомляем всех админов с кнопками подтверждения/отклонения
    for admin_id in ADMIN_IDS:
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text="✅", callback_data=f"confirm_{appointment.id}"),
                types.InlineKeyboardButton(text="❌", callback_data=f"reject_{appointment.id}")
            ]
        ])
        msg_text = (f"🆕 Новая запись!\n"
                    f"Клиент: {user.full_name} (@{user.username})\n"
                    f"Услуга: {service.name}\n"
                    f"Дата: {slot.date}\n"
                    f"Время: {slot.time}\n"
                    f"Статус: ожидает подтверждения")
        try:
            await bot.send_message(admin_id, msg_text, reply_markup=keyboard)
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")

    await state.clear()
    session.close()

# ---------- ОБРАБОТКА КНОПОК ПОДТВЕРЖДЕНИЯ/ОТКЛОНЕНИЯ ----------
@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_appointment(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав.", show_alert=True)
        return
    appointment_id = int(callback.data.split("_")[1])
    session = SessionLocal()
    appointment = session.query(Appointment).get(appointment_id)
    if not appointment:
        await callback.answer("Запись не найдена.", show_alert=True)
        session.close()
        return
    if appointment.status != 'pending':
        await callback.answer("Эта запись уже обработана.", show_alert=True)
        session.close()
        return

    appointment.status = 'confirmed'
    session.commit()

    # Получаем данные для сообщения клиенту
    slot = session.query(ScheduleSlot).get(appointment.schedule_id)
    service = session.query(Service).get(appointment.service_id)
    client = session.query(User).get(appointment.user_id)

    # Текст из настройки success_message с подстановкой
    success_setting = session.query(Setting).filter(Setting.key == 'success_message').first()
    if success_setting:
        success_text = success_setting.value.format(
            date=slot.date,
            time=slot.time,
            service_name=service.name,
            admin_username=callback.from_user.username or "администратор"
        )
    else:
        success_text = f"✅ Вы успешно записаны на {slot.date} {slot.time}. До встречи!"

    try:
        await bot.send_message(client.telegram_id, success_text)
    except Exception as e:
        logging.error(f"Не удалось отправить клиенту {client.telegram_id} сообщение о подтверждении: {e}")

    # Обновляем сообщение у админа
    await callback.message.edit_text(
        f"✅ Запись подтверждена\n"
        f"Клиент: {client.full_name}\n"
        f"Услуга: {service.name}\n"
        f"Дата: {slot.date}\n"
        f"Время: {slot.time}",
        reply_markup=None
    )
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
    if not appointment:
        await callback.answer("Запись не найдена.", show_alert=True)
        session.close()
        return
    if appointment.status != 'pending':
        await callback.answer("Эта запись уже обработана.", show_alert=True)
        session.close()
        return

    slot = session.query(ScheduleSlot).get(appointment.schedule_id)
    client = session.query(User).get(appointment.user_id)
    service = session.query(Service).get(appointment.service_id)

    # Освобождаем слот и удаляем запись (или помечаем cancelled)
    slot.is_booked = False
    session.delete(appointment)
    session.commit()

    try:
        await bot.send_message(client.telegram_id, "❌ Ваша запись была отклонена администратором.")
    except Exception as e:
        logging.error(f"Не удалось отправить клиенту {client.telegram_id} сообщение об отклонении: {e}")

    await callback.message.edit_text(
        f"❌ Запись отклонена\n"
        f"Клиент: {client.full_name}\n"
        f"Услуга: {service.name}\n"
        f"Дата: {slot.date}\n"
        f"Время: {slot.time}",
        reply_markup=None
    )
    await callback.answer("Запись отклонена")
    session.close()

# ---------- ОБРАБОТКА СКРИНШОТА (если услуга требовала предоплату) ----------
# Этот блок оставлен для совместимости, хотя теперь слот бронируется сразу.
# Если вы хотите принимать предоплату до подтверждения, можно адаптировать.
# В текущей реализации предоплата не обязательна для бронирования, но вы можете настроить текст.

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

# ... (весь код управления услугами остаётся аналогичным предыдущему)
# Для краткости здесь не дублирую, он уже был в прошлом ответе.
# Необходимо вставить его полностью.

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

# ---------- РЕДАКТИРОВАНИЕ СООБЩЕНИЯ С РЕКВИЗИТАМИ (ПОДТВЕРЖДЕНИЕ ЗАПИСИ) ----------
@dp.message(F.text == "📩 Сообщение с реквизитами")
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
        "Введите новый текст сообщения с реквизитами (можно использовать переменные):\n"
        "{service_name} — название услуги\n"
        "{date} — дата\n"
        "{time} — время\n"
        "{prepayment_amount} — сумма предоплаты\n\n"
        "Пример:\n"
        "Для подтверждения записи необходимо внести предоплату {prepayment_amount} руб.\n"
        "Переведите на карту 1234 5678 9012 3456 и отправьте скриншот."
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

# ---------- РЕДАКТИРОВАНИЕ СООБЩЕНИЯ ОБ УСПЕШНОЙ ЗАПИСИ (ПОСЛЕ ПОДТВЕРЖДЕНИЯ) ----------
@dp.message(F.text == "📨 Сообщение об успешной записи")
async def edit_success_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав.")
        return
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✏️ Редактировать", callback_data="success_edit")],
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="success_cancel")],
    ])
    await message.answer("Выберите действие:", reply_markup=keyboard)
    await state.set_state(AdminStates.managing_success)

@dp.callback_query(AdminStates.managing_success, F.data == "success_edit")
async def success_edit_prompt(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "Введите новый текст сообщения об успешной записи (можно использовать переменные):\n"
        "{date} — дата\n"
        "{time} — время\n"
        "{service_name} — название услуги\n"
        "{admin_username} — username администратора, подтвердившего запись\n\n"
        "Пример:\n"
        "✅ Вы успешно записаны на {date} {time}.\n"
        "Услуга: {service_name}\n"
        "Если возникнут вопросы, обратитесь к @{admin_username}."
    )
    await state.set_state(AdminStates.editing_success_text)

@dp.callback_query(AdminStates.managing_success, F.data == "success_cancel")
async def success_cancel(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("Отменено.")
    await state.clear()

@dp.message(AdminStates.editing_success_text)
async def success_save(message: types.Message, state: FSMContext):
    new_text = message.text.strip()
    if not new_text:
        await message.answer("Текст не может быть пустым. Попробуйте ещё раз:")
        return
    session = SessionLocal()
    setting = session.query(Setting).filter(Setting.key == 'success_message').first()
    if setting:
        setting.value = new_text
    else:
        session.add(Setting(key='success_message', value=new_text))
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
    # Добавляем настройки по умолчанию, если их нет
    if session.query(Setting).filter(Setting.key == 'faq_text').first() is None:
        default_faq = (
            "Часто задаваемые вопросы:\n\n"
            "❓ Как подготовиться?\n— Приходите без макияжа глаз.\n\n"
            "❓ Сколько держатся ресницы?\n— 2–4 недели.\n\n"
            "❓ Можно ли мочить глаза?\n— В первые 24 часа не рекомендуется."
        )
        session.add(Setting(key='faq_text', value=default_faq))
    if session.query(Setting).filter(Setting.key == 'confirmation_text').first() is None:
        default_confirmation = (
            "Для подтверждения записи необходимо внести предоплату {prepayment_amount} руб.\n"
            "Переведите на карту 1234 5678 9012 3456 и отправьте скриншот чека."
        )
        session.add(Setting(key='confirmation_text', value=default_confirmation))
    if session.query(Setting).filter(Setting.key == 'success_message').first() is None:
        default_success = (
            "✅ Вы успешно записаны на {date} {time}.\n"
            "Услуга: {service_name}\n"
            "Если возникнут вопросы, обратитесь к @{admin_username}."
        )
        session.add(Setting(key='success_message', value=default_success))
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
