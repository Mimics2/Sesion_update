import os
import logging
import asyncio
import random
import qrcode
import json
from io import BytesIO
from datetime import datetime, timedelta
from typing import Dict, List, Set

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from aiogram import Bot, Dispatcher, Router, F
    from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardButton
    from aiogram.filters import Command, CommandStart
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardMarkup
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import (
        SessionPasswordNeededError, 
        PhoneCodeInvalidError,
        PhoneNumberInvalidError,
        FloodWaitError,
        PhoneCodeExpiredError
    )
except ImportError as e:
    print(f"❌ Missing dependencies: {e}")
    exit(1)

BOT_TOKEN = os.environ.get('BOT_TOKEN')
API_ID = int(os.environ.get('API_ID', '4'))  # Changed to 4 (Android)
API_HASH = os.environ.get('API_HASH', '014b35b6184100b085b0d0572f9b5103')  # Android hash

# Получаем ID админов из переменной окружения
ADMIN_IDS_STR = os.environ.get('ADMIN_IDS', '0')
ADMIN_IDS = set()

try:
    # Разделяем по запятой и преобразуем в числа
    if ADMIN_IDS_STR:
        admin_ids_list = [int(id_str.strip()) for id_str in ADMIN_IDS_STR.split(',') if id_str.strip()]
        ADMIN_IDS = set(admin_ids_list)
        logger.info(f"👑 Admin IDs loaded: {ADMIN_IDS}")
except ValueError as e:
    logger.error(f"❌ Error parsing ADMIN_IDS: {e}")
    ADMIN_IDS = set()

def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь админом"""
    return user_id in ADMIN_IDS

class SessionStates(StatesGroup):
    ADD_USER = State()
    REMOVE_USER = State()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

class WhiteListManager:
    def __init__(self, filename: str = "whitelist.json"):
        self.filename = filename
        self.allowed_users: Set[int] = set()
        self.load()
    
    def load(self):
        """Загрузить белый список из файла"""
        try:
            if os.path.exists(self.filename):
                with open(self.filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.allowed_users = set(data.get('allowed_users', []))
                    logger.info(f"✅ Белый список загружен: {len(self.allowed_users)} пользователей")
            else:
                self.save()
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки белого списка: {e}")
            self.allowed_users = set()
    
    def save(self):
        """Сохранить белый список в файл"""
        try:
            data = {'allowed_users': list(self.allowed_users)}
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 Белый список сохранен: {len(self.allowed_users)} пользователей")
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения белого списка: {e}")
    
    def add_user(self, user_id: int) -> bool:
        """Добавить пользователя в белый список"""
        if user_id not in self.allowed_users:
            self.allowed_users.add(user_id)
            self.save()
            logger.info(f"➕ Добавлен пользователь {user_id} в белый список")
            return True
        return False
    
    def remove_user(self, user_id: int) -> bool:
        """Удалить пользователя из белого списка"""
        if user_id in self.allowed_users:
            self.allowed_users.remove(user_id)
            self.save()
            logger.info(f"➖ Удален пользователь {user_id} из белого списка")
            return True
        return False
    
    def get_all_users(self) -> List[int]:
        """Получить список всех пользователей"""
        return sorted(list(self.allowed_users))
    
    def is_allowed(self, user_id: int) -> bool:
        """Проверить, есть ли пользователь в белом списке"""
        return user_id in self.allowed_users
    
    def clear_all(self):
        """Очистить весь белый список"""
        self.allowed_users.clear()
        self.save()
        logger.info("🧹 Белый список очищен")

class WorkingSessionManager:
    def __init__(self, whitelist_manager: WhiteListManager):
        self.active_sessions = {}
        self.user_messages = {}  # Для хранения сообщений пользователей
        self.whitelist = whitelist_manager
    
    async def create_qr_session(self, user_id: int, message: Message):
        """Создание QR-сессии и немедленный старт отслеживания"""
        try:
            # Проверяем белый список
            if not self.whitelist.is_allowed(user_id) and not is_admin(user_id):
                return False, "❌ Доступ запрещен. Вы не в белом списке."
            
            # Закрываем старую сессию если есть
            if user_id in self.active_sessions:
                try:
                    await self.active_sessions[user_id]['client'].disconnect()
                except:
                    pass
            
            # Пробуем разные устройства и API
            devices = [
                {
                    "device_model": "Samsung SM-G991B",
                    "system_version": "Android 13",
                    "app_version": "10.0.0",
                    "api_id": API_ID,
                    "api_hash": API_HASH,
                },
                {
                    "device_model": "iPhone15,3", 
                    "system_version": "iOS 17.1.2",
                    "app_version": "10.0.0",
                    "api_id": API_ID,
                    "api_hash": API_HASH,
                }
            ]
            
            device = random.choice(devices)
            
            # Используем API из device конфига
            api_id = device.pop("api_id", API_ID)
            api_hash = device.pop("api_hash", API_HASH)
            
            logger.info(f"🔧 Using API: {api_id}, Device: {device['device_model']}")
            
            client = TelegramClient(StringSession(), api_id, api_hash, **device)
            await client.connect()
            
            # Создаем QR-логин
            qr_login = await client.qr_login()
            
            self.active_sessions[user_id] = {
                'client': client,
                'qr_login': qr_login,
                'created_at': datetime.now(),
                'message': message  # Сохраняем сообщение для ответа
            }
            
            # Сохраняем ID сообщения для обновления
            self.user_messages[user_id] = message
            
            return True, qr_login.url
            
        except Exception as e:
            logger.error(f"QR creation error: {e}")
            return False, f"❌ Ошибка создания QR: {str(e)}\n\nПопробуйте другие API данные в настройках."
    
    async def start_qr_monitoring(self, user_id: int):
        """Запуск мониторинга статуса QR-авторизации"""
        if user_id not in self.active_sessions:
            return
        
        data = self.active_sessions[user_id]
        message = data['message']
        
        try:
            # Отправляем сообщение о начале ожидания
            status_msg = await message.answer("⏳ Ожидаем сканирование QR-кода...")
            
            # Ждем сканирования с таймаутом 120 секунд
            logger.info(f"🔄 Начало ожидания QR для пользователя {user_id}")
            
            # Ждем завершения QR-логина
            await asyncio.wait_for(data['qr_login'].wait(), timeout=120)
            logger.info(f"✅ QR код отсканирован для пользователя {user_id}")
            
            # Обновляем статус
            await status_msg.edit_text("✅ QR-код отсканирован! Проверяем авторизацию...")
            
            # Даем время на подтверждение в приложении
            await asyncio.sleep(3)
            
            # ПРОВЕРЯЕМ АВТОРИЗАЦИЮ
            is_authorized = await data['client'].is_user_authorized()
            logger.info(f"🔐 Статус авторизации для {user_id}: {is_authorized}")
            
            if not is_authorized:
                await status_msg.edit_text("❌ Авторизация не завершена. Подтвердите вход в Telegram.")
                return
            
            # ✅ АВТОРИЗАЦИЯ УСПЕШНА - СОЗДАЕМ СЕССИЮ
            await status_msg.edit_text("✅ Авторизация успешна! Создаем сессию...")
            
            # Получаем строку сессии
            session_string = data['client'].session.save()
            logger.info(f"📦 Сессия создана для {user_id}")
            
            # Создаем файл сессии
            session_bytes = session_string.encode('utf-8')
            session_file = BufferedInputFile(session_bytes, filename="telegram_session.txt")
            
            # ✅ ОТПРАВЛЯЕМ СЕССИЮ ПОЛЬЗОВАТЕЛЮ
            await message.answer_document(
                document=session_file,
                caption="✅ **Сессия успешно создана!**\n\n"
                       "💾 Сохраните этот файл\n"
                       "🔒 Он дает полный доступ к аккаунту"
            )
            
            # Также отправляем текстовую версию
            await message.answer(f"📋 **Session String:**\n```\n{session_string}\n```")
            
            logger.info(f"🎉 Сессия отправлена пользователю {user_id}")
            
        except asyncio.TimeoutError:
            logger.warning(f"⏰ Таймаут QR для пользователя {user_id}")
            if user_id in self.user_messages:
                await self.user_messages[user_id].answer("❌ Время ожидания истекло. QR-код не был отсканирован.")
        except Exception as e:
            logger.error(f"❌ Ошибка мониторинга QR для {user_id}: {e}")
            if user_id in self.user_messages:
                await self.user_messages[user_id].answer(f"❌ Ошибка: {str(e)}")
        finally:
            # Всегда очищаем сессию
            await self.cleanup_session(user_id)
    
    async def cleanup_session(self, user_id: int):
        """Очистка сессии"""
        if user_id in self.active_sessions:
            try:
                await self.active_sessions[user_id]['client'].disconnect()
            except:
                pass
            del self.active_sessions[user_id]
        
        if user_id in self.user_messages:
            del self.user_messages[user_id]

# Инициализация менеджеров
whitelist_manager = WhiteListManager()
manager = WorkingSessionManager(whitelist_manager)

# ==============================================
# КОМАНДЫ ДЛЯ ВСЕХ ПОЛЬЗОВАТЕЛЕЙ
# ==============================================

@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    
    # Проверка доступа
    if not whitelist_manager.is_allowed(user_id) and not is_admin(user_id):
        await message.answer(
            "❌ **Доступ запрещен**\n\n"
            "Вы не находитесь в белом списке пользователей.\n"
            "Обратитесь к администратору."
        )
        return
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📷 Создать сессию через QR-код", callback_data="method_qr")
    builder.adjust(1)
    
    welcome_text = (
        "🔐 **Генератор сессий Telegram**\n\n"
        "Создайте сессию для вашего аккаунта через QR-код.\n"
        "После сканирования **сессия придет автоматически**.\n\n"
        "📋 **Команды:**\n"
        "/start - начать\n"
        "/qr - создать сессию\n"
        "/check - статус сессии\n"
        "/help - справка"
    )
    
    if is_admin(user_id):
        welcome_text += "\n\n👑 **Админ команды:**\n/admin - панель админа"
    
    await message.answer(welcome_text, reply_markup=builder.as_markup())

@router.message(Command("qr"))
async def cmd_qr(message: Message):
    """Создать сессию через QR"""
    user_id = message.from_user.id
    
    # Проверка белого списка
    if not is_admin(user_id) and not whitelist_manager.is_allowed(user_id):
        await message.answer("❌ **Доступ запрещен**\n\nВы не находитесь в белом списке.")
        return
    
    await message.answer("🔄 Создаем QR-код...")
    
    # Создаем QR-сессию и начинаем отслеживание
    success, qr_url = await manager.create_qr_session(user_id, message)
    
    if success:
        # Создаем QR-код изображение
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_url)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        bio = BytesIO()
        img.save(bio, 'PNG')
        bio.seek(0)
        
        qr_file = BufferedInputFile(bio.getvalue(), filename="qr_code.png")
        
        # Отправляем QR-код
        await message.answer_photo(
            photo=qr_file,
            caption="📷 **QR-код для подключения:**\n\n"
                   "1. Откройте Telegram → Настройки\n"
                   "2. Устройства → Подключить устройство\n"
                   "3. Отсканируйте этот QR-код\n"
                   "4. **Подтвердите вход** в приложении\n\n"
                   "⏳ Ожидаем 2 минуты...\n"
                   "✅ Сессия придет автоматически после подключения"
        )
        
        # ✅ НЕМЕДЛЕННО ЗАПУСКАЕМ МОНИТОРИНГ
        asyncio.create_task(manager.start_qr_monitoring(user_id))
        
    else:
        await message.answer(f"❌ {qr_url}")

@router.callback_query(F.data == "method_qr")
async def handle_qr_method(callback: CallbackQuery):
    await cmd_qr(callback.message)
    await callback.answer()

@router.message(Command("check"))
async def cmd_check(message: Message):
    """Проверка статуса сессии"""
    user_id = message.from_user.id
    
    # Проверка белого списка
    if not is_admin(user_id) and not whitelist_manager.is_allowed(user_id):
        await message.answer("❌ **Доступ запрещен**\n\nВы не находитесь в белом списке.")
        return
    
    if user_id in manager.active_sessions:
        created_time = manager.active_sessions[user_id]['created_at']
        time_passed = datetime.now() - created_time
        await message.answer(f"🔄 Сессия активна\n⏰ Прошло: {int(time_passed.total_seconds())} сек")
    else:
        await message.answer("❌ Нет активной сессии\n🔄 Используйте /qr")

@router.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "🔐 **Помощь по генератору сессий**\n\n"
        "Как использовать:\n"
        "1. Отправьте /qr\n"
        "2. Отсканируйте QR-код в Telegram\n"
        "3. **Обязательно подтвердите вход** в приложении\n"
        "4. **Сессия придет автоматически**\n\n"
        "Команды:\n"
        "/start - начать\n"
        "/qr - создать сессию\n"
        "/check - проверить статус\n"
        "/help - эта справка\n\n"
        "⚠️ **Важно:** После сканирования нужно нажать 'Подключить' в Telegram!"
    )
    await message.answer(help_text)

# ==============================================
# АДМИН КОМАНДЫ (только для админов)
# ==============================================

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    """Админ панель"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("❌ Нет доступа к админ панели")
        return
    
    admin_text = (
        "👑 **Панель администратора**\n\n"
        "📋 **Команды:**\n"
        "/add_user [ID] - добавить пользователя\n"
        "/remove_user [ID] - удалить пользователя\n"
        "/list_users - показать всех пользователей\n"
        "/clear_users - очистить весь список\n"
        "/stats - статистика\n"
        "/broadcast [текст] - сообщение всем\n\n"
        "Пример:\n"
        "/add_user 123456789\n"
        "/remove_user 123456789"
    )
    
    await message.answer(admin_text)

@router.message(Command("add_user"))
async def cmd_add_user(message: Message):
    """Добавить пользователя в белый список"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("❌ Нет доступа")
        return
    
    args = message.text.split()
    
    if len(args) < 2:
        await message.answer("❌ Укажите ID пользователя\nПример: `/add_user 123456789`")
        return
    
    try:
        user_to_add = int(args[1])
        
        if user_to_add in ADMIN_IDS:
            await message.answer("❌ Нельзя добавить администратора")
        elif whitelist_manager.add_user(user_to_add):
            await message.answer(f"✅ Пользователь `{user_to_add}` добавлен в белый список")
        else:
            await message.answer(f"ℹ️ Пользователь `{user_to_add}` уже в белом списке")
            
    except ValueError:
        await message.answer("❌ Неверный формат ID. Используйте числовой ID")

@router.message(Command("remove_user"))
async def cmd_remove_user(message: Message):
    """Удалить пользователя из белого списка"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("❌ Нет доступа")
        return
    
    args = message.text.split()
    
    if len(args) < 2:
        await message.answer("❌ Укажите ID пользователя\nПример: `/remove_user 123456789`")
        return
    
    try:
        user_to_remove = int(args[1])
        
        if whitelist_manager.remove_user(user_to_remove):
            await message.answer(f"✅ Пользователь `{user_to_remove}` удален из белого списка")
        else:
            await message.answer(f"❌ Пользователь `{user_to_remove}` не найден в белом списке")
            
    except ValueError:
        await message.answer("❌ Неверный формат ID. Используйте числовой ID")

@router.message(Command("list_users"))
async def cmd_list_users(message: Message):
    """Показать всех пользователей в белом списке"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("❌ Нет доступа")
        return
    
    users = whitelist_manager.get_all_users()
    
    if not users:
        text = "📭 **Белый список пуст**\n\nНет пользователей в белом списке"
    else:
        text = f"👥 **Пользователи в белом списке** ({len(users)}):\n\n"
        for i, user_id in enumerate(users, 1):
            text += f"{i}. `{user_id}`\n"
    
    await message.answer(text)

@router.message(Command("clear_users"))
async def cmd_clear_users(message: Message):
    """Очистить весь белый список"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("❌ Нет доступа")
        return
    
    # Подтверждение
    await message.answer(
        "⚠️ **Очистка всего белого списка**\n\n"
        "Вы уверены, что хотите удалить ВСЕХ пользователей?\n"
        "Это действие нельзя отменить!\n\n"
        "Отправьте `/confirm_clear` для подтверждения"
    )

@router.message(Command("confirm_clear"))
async def cmd_confirm_clear(message: Message):
    """Подтверждение очистки белого списка"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("❌ Нет доступа")
        return
    
    whitelist_manager.clear_all()
    await message.answer("✅ Белый список очищен!")

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Статистика системы"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("❌ Нет доступа")
        return
    
    users = whitelist_manager.get_all_users()
    active_sessions = len(manager.active_sessions)
    
    stats_text = (
        f"📊 **Статистика системы**\n\n"
        f"👥 Пользователей в белом списке: {len(users)}\n"
        f"🔄 Активных сессий: {active_sessions}\n"
        f"👑 Админов: {len(ADMIN_IDS)}\n"
        f"📁 Файл белого списка: `{whitelist_manager.filename}`\n\n"
        f"🔧 API ID: `{API_ID}`"
    )
    
    await message.answer(stats_text)

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    """Отправить сообщение всем пользователям"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("❌ Нет доступа")
        return
    
    args = message.text.split(maxsplit=1)
    
    if len(args) < 2:
        await message.answer("❌ Укажите текст сообщения\nПример: `/broadcast Привет всем!`")
        return
    
    broadcast_text = args[1]
    
    # Здесь должна быть реализация рассылки
    # Для этого нужно хранить ID всех пользователей, которые использовали бота
    await message.answer("📢 Функция рассылки будет реализована в будущем")

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    """Отмена текущего действия"""
    current_state = await state.get_state()
    if current_state is None:
        return
    
    await state.clear()
    await message.answer("❌ Действие отменено")

async def main():
    logger.info("🚀 Starting Working QR Session Bot...")
    logger.info(f"👑 Admin IDs: {ADMIN_IDS}")
    logger.info(f"🔧 Using API_ID: {API_ID}")
    logger.info(f"👥 Users in whitelist: {len(whitelist_manager.get_all_users())}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
