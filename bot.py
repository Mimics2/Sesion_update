import os
import logging
import asyncio
import random
import qrcode
import json
import re
from io import BytesIO
from datetime import datetime, timedelta
from typing import Dict, List, Set

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

try:
    from aiogram import Bot, Dispatcher, Router, F
    from aiogram.types import Message, CallbackQuery, BufferedInputFile
    from aiogram.filters import Command, CommandStart
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.utils.keyboard import InlineKeyboardBuilder
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
API_ID = int(os.environ.get('API_ID', '4'))
API_HASH = os.environ.get('API_HASH', '014b35b6184100b085b0d0572f9b5103')

# ==============================================
# ФИКС ДЛЯ ПАРСИНГА ADMIN_IDS
# ==============================================
ADMIN_IDS = set()

# Получаем строку с ID админов
admin_ids_raw = os.environ.get('ADMIN_IDS', '')
logger.info(f"📋 Raw ADMIN_IDS from env: '{admin_ids_raw}'")

if admin_ids_raw:
    try:
        # Очищаем строку
        cleaned = admin_ids_raw.strip().replace('"', '').replace("'", "")
        cleaned = re.sub(r'\s+', '', cleaned)
        
        logger.info(f"🧹 Cleaned ADMIN_IDS: '{cleaned}'")
        
        if cleaned:
            id_strings = cleaned.split(',')
            for id_str in id_strings:
                if id_str:
                    admin_id = int(id_str)
                    ADMIN_IDS.add(admin_id)
            
            logger.info(f"✅ Admin IDs parsed: {ADMIN_IDS}")
    except Exception as e:
        logger.error(f"❌ Error parsing ADMIN_IDS: {e}")
        # Пробуем альтернативный метод
        try:
            numbers = re.findall(r'\d+', admin_ids_raw)
            for num in numbers:
                ADMIN_IDS.add(int(num))
            logger.info(f"✅ Admin IDs parsed with regex: {ADMIN_IDS}")
        except:
            pass

# Если все еще пусто, попробуем старый формат
if not ADMIN_IDS:
    admin_id_single = os.environ.get('ADMIN_ID', '')
    if admin_id_single:
        try:
            admin_id = int(admin_id_single.strip())
            ADMIN_IDS.add(admin_id)
            logger.info(f"✅ Using single ADMIN_ID: {ADMIN_IDS}")
        except:
            pass

# Если все еще пусто, хардкод ID админов
if not ADMIN_IDS:
    ADMIN_IDS.update({6646433980, 931124646})
    logger.warning(f"⚠️ Using hardcoded admin IDs: {ADMIN_IDS}")

logger.info(f"👑 Final Admin IDs: {ADMIN_IDS}")

def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь админом"""
    result = user_id in ADMIN_IDS
    return result

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
        self.user_messages = {}
        self.whitelist = whitelist_manager
    
    async def create_qr_session(self, user_id: int, message: Message):
        """Создание QR-сессии"""
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
            
            # Используем разные API на случай если одно не работает
            api_configs = [
                {"api_id": 4, "api_hash": "014b35b6184100b085b0d0572f9b5103"},
                {"api_id": 2040, "api_hash": "b18441a1ff607e10a989891a5462e627"},
                {"api_id": 2834, "api_hash": "68875f756c9fe3e3097b6f72a8b68f93"},
            ]
            
            # Пробуем все конфиги по очереди
            last_error = None
            for config in api_configs:
                try:
                    device = {
                        "device_model": "Samsung SM-G991B",
                        "system_version": "Android 13",
                        "app_version": "10.0.0",
                    }
                    
                    client = TelegramClient(StringSession(), config["api_id"], config["api_hash"], **device)
                    await client.connect()
                    
                    qr_login = await client.qr_login()
                    
                    self.active_sessions[user_id] = {
                        'client': client,
                        'qr_login': qr_login,
                        'created_at': datetime.now(),
                        'message': message
                    }
                    
                    self.user_messages[user_id] = message
                    
                    logger.info(f"✅ QR created with API {config['api_id']} for user {user_id}")
                    return True, qr_login.url
                    
                except Exception as e:
                    last_error = e
                    continue
            
            # Если все конфиги не сработали
            return False, f"❌ Ошибка создания QR: {str(last_error)}"
            
        except Exception as e:
            logger.error(f"QR creation error: {e}")
            return False, f"❌ Ошибка создания QR: {str(e)}"
    
    async def start_qr_monitoring(self, user_id: int):
        """Запуск мониторинга статуса QR-авторизации"""
        if user_id not in self.active_sessions:
            return
        
        data = self.active_sessions[user_id]
        message = data['message']
        
        try:
            status_msg = await message.answer("⏳ Ожидаем сканирование QR-кода...")
            
            # Ждем сканирования с таймаутом 120 секунд
            await asyncio.wait_for(data['qr_login'].wait(), timeout=120)
            
            await status_msg.edit_text("✅ QR-код отсканирован! Проверяем авторизацию...")
            await asyncio.sleep(3)
            
            # Проверяем авторизацию
            is_authorized = await data['client'].is_user_authorized()
            
            if not is_authorized:
                await status_msg.edit_text("❌ Авторизация не завершена. Подтвердите вход в Telegram.")
                return
            
            # Авторизация успешна
            await status_msg.edit_text("✅ Авторизация успешна! Создаем сессию...")
            
            # Получаем строку сессии
            session_string = data['client'].session.save()
            
            # Создаем файл сессии
            session_bytes = session_string.encode('utf-8')
            session_file = BufferedInputFile(session_bytes, filename="telegram_session.txt")
            
            # Отправляем сессию пользователю
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
        
        # Запускаем мониторинг
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
    
    if not is_admin(user_id) and not whitelist_manager.is_allowed(user_id):
        await message.answer("❌ **Доступ запрещен**")
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
        "⚠️ **Важно:** После сканирования нажмите 'Подключить' в Telegram!"
    )
    await message.answer(help_text)

# ==============================================
# АДМИН КОМАНДЫ
# ==============================================

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    """Админ панель"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer(f"❌ Нет доступа к админ панели\nВаш ID: {user_id}")
        return
    
    admin_text = (
        f"👑 **Панель администратора**\n\n"
        f"🆔 **Ваш ID:** `{user_id}`\n"
        f"📊 **Всего админов:** {len(ADMIN_IDS)}\n\n"
        f"📋 **Команды:**\n"
        f"/myid - показать мой ID\n"
        f"/add_user [ID] - добавить пользователя\n"
        f"/remove_user [ID] - удалить пользователя\n"
        f"/list_users - показать всех пользователей\n"
        f"/clear_users - очистить весь список\n"
        f"/stats - статистика\n"
        f"/debug - отладочная информация\n\n"
        f"Пример:\n"
        f"`/add_user 123456789`\n"
        f"`/remove_user 123456789`"
    )
    
    await message.answer(admin_text)

@router.message(Command("myid"))
async def cmd_myid(message: Message):
    """Показать мой ID и статус админа"""
    user_id = message.from_user.id
    username = message.from_user.username or "нет username"
    first_name = message.from_user.first_name or ""
    last_name = message.from_user.last_name or ""
    
    admin_status = is_admin(user_id)
    whitelist_status = whitelist_manager.is_allowed(user_id)
    
    text = (
        f"👤 **Ваши данные:**\n"
        f"🆔 **ID:** `{user_id}`\n"
        f"📛 **Имя:** {first_name} {last_name}\n"
        f"🔗 **Username:** @{username}\n"
        f"👑 **Статус админа:** {'✅ ДА' if admin_status else '❌ НЕТ'}\n"
        f"📋 **В белом списке:** {'✅ ДА' if whitelist_status else '❌ НЕТ'}\n\n"
        f"📊 **Admin IDs в системе:** {sorted(list(ADMIN_IDS))}"
    )
    
    await message.answer(text)

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
        f"🔧 API ID: `{API_ID}`\n"
        f"📁 Файл белого списка: `{whitelist_manager.filename}`"
    )
    
    await message.answer(stats_text)

@router.message(Command("debug"))
async def cmd_debug(message: Message):
    """Отладочная информация"""
    user_id = message.from_user.id
    
    if not is_admin(user_id):
        await message.answer("❌ Нет доступа")
        return
    
    # Проверяем переменные окружения
    env_vars = {
        'BOT_TOKEN': '✅ Set' if os.environ.get('BOT_TOKEN') else '❌ Not set',
        'API_ID': os.environ.get('API_ID', '❌ Not set'),
        'API_HASH': '✅ Set' if os.environ.get('API_HASH') else '❌ Not set',
        'ADMIN_IDS': os.environ.get('ADMIN_IDS', '❌ Not set'),
    }
    
    text = "🔧 **Отладочная информация:**\n\n"
    text += "**Переменные окружения:**\n"
    for key, value in env_vars.items():
        text += f"{key}: {value}\n"
    
    text += f"\n👑 **Admin IDs:** {sorted(list(ADMIN_IDS))}\n"
    text += f"👤 **Your ID:** {user_id}\n"
    text += f"🔍 **Is admin:** {is_admin(user_id)}\n"
    text += f"📋 **In whitelist:** {whitelist_manager.is_allowed(user_id)}"
    
    await message.answer(text)

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
    logger.info(f"👑 Admin IDs: {sorted(list(ADMIN_IDS))}")
    logger.info(f"🔧 Using API_ID: {API_ID}")
    logger.info(f"👥 Users in whitelist: {len(whitelist_manager.get_all_users())}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
