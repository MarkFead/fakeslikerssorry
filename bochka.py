import asyncio
import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional, Tuple

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
class Config:
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    ADMIN_IDS = [int(admin_id) for admin_id in os.getenv('ADMIN_ID', '').split(',') if admin_id]
    ORDERS_CHANNEL_ID = os.getenv('ORDERS_CHANNEL_ID')
    NOTIFICATIONS_CHANNEL_ID = os.getenv('NOTIFICATIONS_CHANNEL_ID')
    DATABASE_PATH = 'shop.db'
    STATIC_PATH = 'static'

# Проверка конфигурации
if not Config.BOT_TOKEN:
    logger.error("BOT_TOKEN is not set in .env")
    raise ValueError("BOT_TOKEN is not set in .env")

# Инициализация бота
bot = Bot(token=Config.BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

# Состояния
class OrderStates(StatesGroup):
    SELECT_SIZE = State()
    SELECT_CURRENCY = State()
    SELECT_ORDER_ITEMS = State()
    CONFIRM_ORDER = State()

# Контекстный менеджер для работы с БД
@asynccontextmanager
async def get_db():
    conn = None
    try:
        conn = sqlite3.connect(Config.DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        yield conn
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.commit()
            conn.close()

# Сервис уведомлений
class NotificationService:
    """Сервис для отправки уведомлений в Telegram"""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.orders_channel_id = Config.ORDERS_CHANNEL_ID
        self.notifications_channel_id = Config.NOTIFICATIONS_CHANNEL_ID
        self.admin_ids = Config.ADMIN_IDS

    async def send_order_notification(self, user_info, order_items, total_price, currency_code, order_id=None):
        """Отправляет уведомление о новом заказе в канал и админам"""
        order_number = f"#{order_id}" if order_id else f"#{datetime.now().strftime('%Y%m%d%H%M%S')}"
        current_time = datetime.now().strftime("%d.%m.%Y %H:%M")

        message = (
            f"🛍️ <b>НОВЫЙ ЗАКАЗ</b> {order_number}\n\n"
            f"👤 <b>Клиент:</b>\n"
        )

        user_id = user_info.get('id', 'Неизвестно')
        username = user_info.get('username', '')
        full_name = user_info.get('full_name', 'Неизвестно')

        message += f"   • Имя: {full_name}\n"
        if username:
            message += f"   • Username: @{username}\n"
        message += f"   • ID: {user_id}\n"

        message += "\n"

        message += f"📦 <b>Товары:</b>\n"
        for i, item in enumerate(order_items, 1):
            item_name = str(item.get('name', 'Неизвестный товар'))
            item_size = str(item.get('size', ''))
            item_price = item.get('price', 0)

            message += f"   {i}. {item_name}\n"
            if item_size:
                message += f"      • Размер: {item_size}\n"
            message += f"      • Цена: {item_price:.2f} {currency_code}\n"

            if i < len(order_items):
                message += "\n"

        message += f"\n💰 <b>Итого:</b> {total_price:.2f} {currency_code}"
        message += f"\n🕐 <b>Время заказа:</b> {current_time}\n\n"
        message += f"📞 Свяжитесь с клиентом для подтверждения заказа"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_order_{user_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_order_{user_id}")
            ],
            [InlineKeyboardButton(text="📞 Связаться", url=f"tg://user?id={user_id}")]
        ])

        channel_sent = False
        if self.orders_channel_id:
            try:
                await self.bot.send_message(
                    chat_id=self.orders_channel_id,
                    text=message,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
                channel_sent = True
                logger.info(f"Уведомление о заказе отправлено в канал {self.orders_channel_id}")
            except TelegramBadRequest as e:
                logger.error(f"Не удалось отправить уведомление в канал заказов: {e}")

        if not channel_sent and self.admin_ids:
            for admin_id in self.admin_ids:
                try:
                    await self.bot.send_message(
                        chat_id=admin_id,
                        text=message + "\n\n⚠️ Это сообщение отправлено вам, так как отправка в канал не удалась.",
                        parse_mode="HTML",
                        reply_markup=keyboard
                    )
                    logger.info(f"Уведомление о заказе отправлено админу {admin_id}")
                except TelegramBadRequest as e:
                    logger.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")

    async def send_bot_started_notification(self):
        """Отправляет уведомление о запуске бота"""
        message = "🟢 <b>Бот запущен</b>\n\nМагазин одежды готов к работе!"
        if self.notifications_channel_id:
            try:
                await self.bot.send_message(
                    chat_id=self.notifications_channel_id,
                    text=message,
                    parse_mode="HTML"
                )
                logger.info(f"Bot started notification sent to channel {self.notifications_channel_id}")
            except TelegramBadRequest as e:
                logger.error(f"Failed to send bot started notification: {e}")

# Создаем экземпляр сервиса уведомлений
notification_service = NotificationService(bot)

# Инициализация базы данных
def init_db():
    logger.info("Starting init_db")
    try:
        conn = sqlite3.connect('shop.db')
        c = conn.cursor()
        c.execute("PRAGMA foreign_keys = ON")
        logger.info("Connected to database")

        tables = [
            '''CREATE TABLE IF NOT EXISTS categories
               (id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                image_path TEXT,
                folder_name TEXT)''',
            '''CREATE TABLE IF NOT EXISTS items
               (id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER,
                name TEXT NOT NULL,
                description TEXT,
                sizes TEXT NOT NULL,
                stock_quantity INTEGER DEFAULT 0,
                FOREIGN KEY (category_id) REFERENCES categories(id))''',
            '''CREATE TABLE IF NOT EXISTS item_images
               (id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER,
                image_path TEXT NOT NULL,
                FOREIGN KEY (item_id) REFERENCES items(id))''',
            '''CREATE TABLE IF NOT EXISTS carts
               (user_id INTEGER,
                item_id INTEGER,
                size TEXT,
                quantity INTEGER DEFAULT 1,
                FOREIGN KEY (item_id) REFERENCES items(id))''',
            '''CREATE TABLE IF NOT EXISTS currencies
               (id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                rate REAL NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS user_preferences
               (user_id INTEGER PRIMARY KEY,
                currency_id INTEGER,
                FOREIGN KEY (currency_id) REFERENCES currencies(id))''',
            '''CREATE TABLE IF NOT EXISTS item_prices
               (id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER,
                currency_id INTEGER,
                price REAL NOT NULL,
                FOREIGN KEY (item_id) REFERENCES items(id),
                FOREIGN KEY (currency_id) REFERENCES currencies(id))''',
            '''CREATE TABLE IF NOT EXISTS orders
               (id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                order_data TEXT NOT NULL,
                total_price REAL NOT NULL,
                currency_code TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''',
            '''CREATE TABLE IF NOT EXISTS banned_users
               (user_id INTEGER PRIMARY KEY,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)'''
        ]

        for table in tables:
            c.execute(table)

        c.execute("SELECT COUNT(*) FROM currencies")
        if c.fetchone()[0] == 0:
            c.execute("DELETE FROM item_prices")
            c.execute("DELETE FROM user_preferences")
            c.execute("DELETE FROM currencies")
            logger.info("Cleared item_prices, user_preferences, currencies")

            currencies = [('RUB', 1.0), ('BYN', 0.037)]
            c.executemany("INSERT OR IGNORE INTO currencies (name, rate) VALUES (?, ?)", currencies)
            logger.info("Inserted currencies")

        conn.commit()
        logger.info("init_db completed successfully")
    except sqlite3.Error as e:
        logger.error(f"Database error in init_db: {e}")
        raise
    finally:
        conn.close()

# Сервисы для работы с данными
class DatabaseService:
    @staticmethod
    async def is_user_banned(user_id: int) -> bool:
        """Проверить, забанен ли пользователь"""
        async with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
            return bool(cursor.fetchone())

    @staticmethod
    async def ban_user(user_id: int) -> bool:
        """Забанить пользователя"""
        async with get_db() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("INSERT INTO banned_users (user_id) VALUES (?)", (user_id,))
                return True
            except sqlite3.IntegrityError:
                return False  # Пользователь уже забанен

    @staticmethod
    async def unban_user(user_id: int) -> bool:
        """Разбанить пользователя"""
        async with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
            return cursor.rowcount > 0

    @staticmethod
    async def get_user_currency(user_id: int) -> Tuple[str, float]:
        """Получить валюту пользователя"""
        async with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                           SELECT currencies.name, currencies.rate
                           FROM user_preferences
                                    JOIN currencies ON user_preferences.currency_id = currencies.id
                           WHERE user_preferences.user_id = ?
                           ''', (user_id,))
            result = cursor.fetchone()
            return (result['name'], result['rate']) if result else ('BYN', 0.037)

    @staticmethod
    async def get_categories() -> List[sqlite3.Row]:
        """Получить все категории"""
        async with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, image_path FROM categories ORDER BY name")
            return cursor.fetchall()

    @staticmethod
    async def get_category_by_id(category_id: int) -> Optional[sqlite3.Row]:
        """Получить категорию по ID"""
        async with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM categories WHERE id = ?", (category_id,))
            return cursor.fetchone()

    @staticmethod
    async def get_items_by_category(category_id: int) -> List[sqlite3.Row]:
        """Получить товары по категории"""
        async with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM items WHERE category_id = ?", (category_id,))
            return cursor.fetchall()

    @staticmethod
    async def get_item_by_id(item_id: int) -> Optional[sqlite3.Row]:
        """Получить товар по ID"""
        async with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name, description, stock_quantity, sizes, category_id FROM items WHERE id = ?",
                (item_id,)
            )
            return cursor.fetchone()

    @staticmethod
    async def get_item_images(item_id: int) -> List[str]:
        """Получить изображения товара"""
        async with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT image_path FROM item_images WHERE item_id = ? ORDER BY id",
                (item_id,)
            )
            return [row['image_path'] for row in cursor.fetchall()]

    @staticmethod
    async def get_item_price(item_id: int, currency_code: str) -> float:
        """Получить цену товара в указанной валюте"""
        async with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                           SELECT price
                           FROM item_prices
                           WHERE item_id = ?
                             AND currency_id = (SELECT id
                                                FROM currencies
                                                WHERE name = ?)
                           ''', (item_id, currency_code))
            result = cursor.fetchone()
            return result['price'] if result else 0.0

    @staticmethod
    async def add_to_cart(user_id: int, item_id: int, size: str):
        """Добавить товар в корзину"""
        async with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO carts (user_id, item_id, size) VALUES (?, ?, ?)",
                (user_id, item_id, size)
            )

    @staticmethod
    async def get_cart_items(user_id: int) -> List[sqlite3.Row]:
        """Получить товары из корзины"""
        async with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                           SELECT items.id, items.name, carts.size
                           FROM carts
                                    JOIN items ON carts.item_id = items.id
                           WHERE carts.user_id = ?
                           ''', (user_id,))
            return cursor.fetchall()

    @staticmethod
    async def clear_cart(user_id: int):
        """Очистить корзину"""
        async with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM carts WHERE user_id = ?", (user_id,))

    @staticmethod
    async def remove_from_cart(user_id: int, item_id: int, size: str):
        """Удалить конкретный товар из корзины"""
        async with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM carts WHERE user_id = ? AND item_id = ? AND size = ? LIMIT 1",
                (user_id, item_id, size)
            )

    @staticmethod
    async def get_currencies() -> List[sqlite3.Row]:
        """Получить все валюты"""
        async with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM currencies ORDER BY name")
            return cursor.fetchall()

    @staticmethod
    async def set_user_currency(user_id: int, currency_id: int):
        """Установить валюту пользователя"""
        async with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO user_preferences (user_id, currency_id) VALUES (?, ?)",
                (user_id, currency_id)
            )

    @staticmethod
    async def get_currency_name(currency_id: int) -> str:
        """Получить название валюты по ID"""
        async with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM currencies WHERE id = ?", (currency_id,))
            result = cursor.fetchone()
            return result['name'] if result else 'Unknown'

    @staticmethod
    async def create_order(user_id: int, order_items: List[dict], total_price: float, currency_code: str) -> int:
        """Создать заказ"""
        async with get_db() as conn:
            cursor = conn.cursor()
            order_data = {
                "items": order_items,
                "user_id": user_id,
                "timestamp": datetime.now().isoformat()
            }
            cursor.execute('''
                           INSERT INTO orders (user_id, order_data, total_price, currency_code, status, created_at)
                           VALUES (?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP)
                           ''', (user_id, json.dumps(order_data, ensure_ascii=False), total_price, currency_code))
            return cursor.lastrowid

# Утилиты для работы с сообщениями
class MessageManager:
    message_ids = {}  # Хранилище ID сообщений по chat_id

    @staticmethod
    async def update_message_ids(chat_id: int, message_id: int):
        """Обновить список ID сообщений для чата"""
        if chat_id not in MessageManager.message_ids:
            MessageManager.message_ids[chat_id] = []
        MessageManager.message_ids[chat_id].append(message_id)
        logger.debug(f"Updated message IDs for chat {chat_id}: {MessageManager.message_ids[chat_id]}")

    @staticmethod
    async def delete_previous_messages(chat_id: int, exclude_ids: List[int] = None):
        """Удалить предыдущие сообщения, кроме исключенных"""
        if chat_id not in MessageManager.message_ids:
            return
        for msg_id in MessageManager.message_ids[chat_id]:
            if exclude_ids and msg_id in exclude_ids:
                continue
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                logger.debug(f"Deleted message {msg_id} in chat {chat_id}")
            except TelegramBadRequest as e:
                logger.warning(f"Failed to delete message {msg_id}: {e}")
        MessageManager.message_ids[chat_id] = [msg_id for msg_id in MessageManager.message_ids[chat_id] if exclude_ids and msg_id in exclude_ids]
        logger.debug(f"Remaining message IDs for chat {chat_id}: {MessageManager.message_ids[chat_id]}")

    @staticmethod
    async def safe_answer_callback(callback: types.CallbackQuery):
        """Безопасно ответить на callback"""
        try:
            await callback.answer()
        except TelegramBadRequest as e:
            if "query is too old" not in str(e):
                logger.warning(f"Failed to answer callback query: {e}")

    @staticmethod
    async def safe_edit_message(
            callback: types.CallbackQuery,
            text: str,
            reply_markup: Optional[InlineKeyboardMarkup] = None
    ) -> Optional[types.Message]:
        """Безопасно редактировать сообщение"""
        try:
            await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
            return callback.message
        except TelegramBadRequest as e:
            logger.warning(f"Failed to edit message: {e}")
            return await bot.send_message(
                chat_id=callback.message.chat.id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )

    @staticmethod
    async def safe_delete_message(chat_id: int, message_id: int):
        """Безопасно удалить сообщение"""
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramBadRequest as e:
            logger.warning(f"Failed to delete message {message_id}: {e}")

    @staticmethod
    def get_valid_images(images: List[str]) -> List[str]:
        """Получить список существующих изображений"""
        valid_images = []
        for img in images:
            if img and img.strip():
                full_path = os.path.join(Config.STATIC_PATH, img.replace('/', os.sep))
                if os.path.exists(full_path):
                    valid_images.append(img)
                else:
                    logger.warning(f"Image not found: {full_path}")
        return valid_images

# Клавиатуры
class Keyboards:
    @staticmethod
    def main_menu() -> InlineKeyboardMarkup:
        """Главное меню"""
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛍 Каталог", callback_data='catalog')],
            [InlineKeyboardButton(text="🛒 Корзина", callback_data='cart')],
            [InlineKeyboardButton(text="👤 Профиль", callback_data='profile')],
            [InlineKeyboardButton(text="💱 Выбрать валюту", callback_data='select_currency')]
        ])

    @staticmethod
    def categories_menu(categories: List[sqlite3.Row]) -> InlineKeyboardMarkup:
        """Меню категорий"""
        buttons = [
            [InlineKeyboardButton(text=cat['name'], callback_data=f'category_{cat["id"]}')]
            for cat in categories
        ]
        buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data='main')])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def items_menu(items: List[sqlite3.Row], category_id: int) -> InlineKeyboardMarkup:
        """Меню товаров"""
        buttons = [
            [InlineKeyboardButton(text=item['name'], callback_data=f'item_{item["id"]}')]
            for item in items
        ]
        buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data='catalog')])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def sizes_menu(sizes: List[str], item_id: int, category_id: int) -> InlineKeyboardMarkup:
        """Меню размеров"""
        buttons = [
            [InlineKeyboardButton(text=f"📏 {size}", callback_data=f'size_{item_id}_{size}')]
            for size in sizes
        ]
        buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f'category_{category_id}')])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def cart_menu(has_items: bool = False) -> InlineKeyboardMarkup:
        """Меню корзины"""
        buttons = []
        if has_items:
            buttons.extend([
                [InlineKeyboardButton(text="✅ Оформить заказ", callback_data='checkout')],
                [InlineKeyboardButton(text="🗑 Очистить корзину", callback_data='clear_cart')]
            ])
        buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data='main')])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def currencies_menu(currencies: List[sqlite3.Row]) -> InlineKeyboardMarkup:
        """Меню валют"""
        buttons = [
            [InlineKeyboardButton(text=curr['name'], callback_data=f'currency_{curr["id"]}')]
            for curr in currencies
        ]
        buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data='main')])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def back_to_main() -> InlineKeyboardMarkup:
        """Кнопка назад в главное меню"""
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 В главное меню", callback_data='main')]
        ])

    @staticmethod
    def confirm_order() -> InlineKeyboardMarkup:
        """Меню подтверждения заказа"""
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить заказ", callback_data='confirm_order')],
            [InlineKeyboardButton(text="❌ Отменить", callback_data='cart')],
            [InlineKeyboardButton(text="🔙 Назад", callback_data='main')]
        ])

    @staticmethod
    def order_success() -> InlineKeyboardMarkup:
        """Меню после успешного заказа"""
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛍 Продолжить покупки", callback_data='catalog')],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data='main')]
        ])

# Обработчики команд
@router.message(Command('start'))
async def start_command(message: types.Message, state: FSMContext):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    logger.info(f"User {user_id} started the bot")

    if await DatabaseService.is_user_banned(user_id):
        await message.answer("🚫 Ваш аккаунт заблокирован. Обратитесь к администратору.")
        return

    await state.clear()
    keyboard = Keyboards.main_menu()

    sent_message = await message.answer(
        "🎉 Добро пожаловать в магазин одежды!\n\n"
        "Выберите действие из меню ниже:",
        reply_markup=keyboard
    )

    await state.update_data(last_message_id=sent_message.message_id)

# Обработчики callback'ов
@router.callback_query(F.data == 'main')
async def main_menu_handler(callback: types.CallbackQuery, state: FSMContext):
    """Главное меню"""
    await MessageManager.safe_answer_callback(callback)
    await state.clear()

    keyboard = Keyboards.main_menu()
    await MessageManager.safe_edit_message(
        callback,
        "🏠 Главное меню\n\nВыберите действие:",
        keyboard
    )

@router.callback_query(F.data == 'catalog')
async def catalog_handler(callback: types.CallbackQuery, state: FSMContext):
    """Каталог товаров"""
    await MessageManager.safe_answer_callback(callback)

    categories = await DatabaseService.get_categories()

    if not categories:
        keyboard = Keyboards.back_to_main()
        await MessageManager.safe_edit_message(
            callback,
            "📂 Категории отсутствуют\n\nПока что в магазине нет категорий товаров.",
            keyboard
        )
        return

    keyboard = Keyboards.categories_menu(categories)
    await MessageManager.safe_edit_message(
        callback,
        "📂 Выберите категорию:",
        keyboard
    )

@router.callback_query(F.data.startswith('category_'))
async def category_handler(callback: types.CallbackQuery, state: FSMContext):
    """Просмотр категории с изображением"""
    await MessageManager.safe_answer_callback(callback)

    category_id = int(callback.data.split('_')[1])
    category = await DatabaseService.get_category_by_id(category_id)

    if not category:
        await MessageManager.safe_edit_message(
            callback,
            "❌ Категория не найдена",
            Keyboards.back_to_main()
        )
        return

    category_name = category['name']
    category_image = category['image_path']
    full_image_path = os.path.join(Config.STATIC_PATH, category_image.replace('/', os.sep)) if category_image else None
    image_exists = os.path.exists(full_image_path) if full_image_path else False
    logger.debug(f"Category {category_name} image path: {category_image}, exists: {image_exists}, full path: {full_image_path}")

    items = await DatabaseService.get_items_by_category(category_id)
    currency_code, _ = await DatabaseService.get_user_currency(callback.from_user.id)

    if not items:
        keyboard = Keyboards.back_to_main()
        text = f"📂 Категория: {category_name}\n\n📦 В этой категории пока нет товаров."
    else:
        keyboard = Keyboards.items_menu(items, category_id)
        items_text = []
        for item in items:
            price = await DatabaseService.get_item_price(item['id'], currency_code)
            items_text.append(f"• {item['name']} - {price:.2f} {currency_code}")
        text = f"📂 Категория: {category_name}\n\n" + "\n".join(items_text)

    await MessageManager.safe_delete_message(callback.message.chat.id, callback.message.message_id)

    if category_image and image_exists:
        try:
            sent_message = await bot.send_photo(
                chat_id=callback.message.chat.id,
                photo=FSInputFile(full_image_path),
                caption=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await MessageManager.update_message_ids(callback.message.chat.id, sent_message.message_id)
            await MessageManager.delete_previous_messages(callback.message.chat.id, [sent_message.message_id])
        except TelegramBadRequest as e:
            logger.warning(f"Failed to send category photo {category_image}: {e}")
            sent_message = await bot.send_message(
                chat_id=callback.message.chat.id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await MessageManager.update_message_ids(callback.message.chat.id, sent_message.message_id)
            await MessageManager.delete_previous_messages(callback.message.chat.id, [sent_message.message_id])
    else:
        sent_message = await bot.send_message(
            chat_id=callback.message.chat.id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await MessageManager.update_message_ids(callback.message.chat.id, sent_message.message_id)
        await MessageManager.delete_previous_messages(callback.message.chat.id, [sent_message.message_id])

@router.callback_query(F.data.startswith('item_'))
async def item_handler(callback: types.CallbackQuery, state: FSMContext):
    """Просмотр товара"""
    await MessageManager.safe_answer_callback(callback)

    item_id = int(callback.data.split('_')[1])
    item = await DatabaseService.get_item_by_id(item_id)

    if not item:
        sent_message = await bot.send_message(
            chat_id=callback.message.chat.id,
            text="❌ Товар не найден",
            reply_markup=Keyboards.back_to_main(),
            parse_mode="HTML"
        )
        await MessageManager.update_message_ids(callback.message.chat.id, sent_message.message_id)
        await MessageManager.delete_previous_messages(callback.message.chat.id, [sent_message.message_id])
        return

    currency_code, _ = await DatabaseService.get_user_currency(callback.from_user.id)
    price = await DatabaseService.get_item_price(item_id, currency_code)
    images = await DatabaseService.get_item_images(item_id)
    valid_images = MessageManager.get_valid_images(images)

    sizes = [s.strip() for s in item['sizes'].split(',') if s.strip()]

    text = (
        f"🏷️ {item['name']}\n"
        f"💰 Цена: {price:.2f} {currency_code}\n"
        f"📝 {item['description'] or 'Нет описания'}\n\n"
        f"📦 В наличии: {item['stock_quantity']}\n\n"
        f"👕 Выберите размер:"
    )

    keyboard = Keyboards.sizes_menu(sizes, item_id, item['category_id'])

    await MessageManager.safe_delete_message(callback.message.chat.id, callback.message.message_id)

    if valid_images:
        try:
            sent_message = await bot.send_photo(
                chat_id=callback.message.chat.id,
                photo=FSInputFile(os.path.join(Config.STATIC_PATH, valid_images[0])),
                caption=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await MessageManager.update_message_ids(callback.message.chat.id, sent_message.message_id)
            await MessageManager.delete_previous_messages(callback.message.chat.id, [sent_message.message_id])

            if len(valid_images) > 1:
                media = []
                for img in valid_images[1:4]:
                    try:
                        media.append(InputMediaPhoto(
                            media=FSInputFile(os.path.join(Config.STATIC_PATH, img))
                        ))
                    except Exception as e:
                        logger.warning(f"Failed to add image {img}: {e}")

                if media:
                    try:
                        media_messages = await bot.send_media_group(
                            chat_id=callback.message.chat.id,
                            media=media
                        )
                        for msg in media_messages:
                            await MessageManager.update_message_ids(callback.message.chat.id, msg.message_id)
                    except TelegramBadRequest as e:
                        logger.warning(f"Failed to send media group: {e}")

        except TelegramBadRequest as e:
            logger.warning(f"Failed to send item photo {valid_images[0]}: {e}")
            sent_message = await bot.send_message(
                chat_id=callback.message.chat.id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await MessageManager.update_message_ids(callback.message.chat.id, sent_message.message_id)
            await MessageManager.delete_previous_messages(callback.message.chat.id, [sent_message.message_id])
    else:
        sent_message = await bot.send_message(
            chat_id=callback.message.chat.id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await MessageManager.update_message_ids(callback.message.chat.id, sent_message.message_id)
        await MessageManager.delete_previous_messages(callback.message.chat.id, [sent_message.message_id])

@router.callback_query(F.data.startswith('size_'))
async def size_handler(callback: types.CallbackQuery, state: FSMContext):
    """Выбор размера и добавление в корзину"""
    await MessageManager.safe_answer_callback(callback)

    parts = callback.data.split('_', 2)
    if len(parts) < 3:
        return

    item_id = int(parts[1])
    size = parts[2]
    user_id = callback.from_user.id

    try:
        await DatabaseService.add_to_cart(user_id, item_id, size)

        await MessageManager.safe_edit_message(
            callback,
            f"✅ Товар добавлен в корзину!\n📏 Размер: {size}\n\n"
            "Что делаем дальше?",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛍 Продолжить покупки", callback_data='catalog')],
                [InlineKeyboardButton(text="🛒 Перейти в корзину", callback_data='cart')],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data='main')]
            ])
        )

    except Exception as e:
        logger.error(f"Failed to add item to cart: {e}")
        await MessageManager.safe_edit_message(
            callback,
            "❌ Ошибка при добавлении товара в корзину",
            Keyboards.back_to_main()
        )

@router.callback_query(F.data == 'cart')
async def cart_handler(callback: types.CallbackQuery, state: FSMContext):
    """Корзина"""
    await MessageManager.safe_answer_callback(callback)

    user_id = callback.from_user.id
    cart_items = await DatabaseService.get_cart_items(user_id)
    currency_code, _ = await DatabaseService.get_user_currency(user_id)

    if not cart_items:
        keyboard = Keyboards.cart_menu(False)
        text = "🛒 Ваша корзина пуста\n\nДобавьте товары из каталога!"
    else:
        total = 0.0
        items_text = ["🛒 Ваша корзина:\n"]

        for item in cart_items:
            price = await DatabaseService.get_item_price(item['id'], currency_code)
            total += price
            items_text.append(
                f"• {item['name']} (📏 {item['size']}) - {price:.2f} {currency_code}"
            )

        items_text.append(f"\n💰 Итого: {total:.2f} {currency_code}")
        text = "\n".join(items_text)
        keyboard = Keyboards.cart_menu(True)

    await MessageManager.safe_edit_message(callback, text, keyboard)

@router.callback_query(F.data == 'clear_cart')
async def clear_cart_handler(callback: types.CallbackQuery, state: FSMContext):
    """Очистить корзину"""
    await MessageManager.safe_answer_callback(callback)

    try:
        await DatabaseService.clear_cart(callback.from_user.id)
        await MessageManager.safe_edit_message(
            callback,
            "🗑 Корзина очищена",
            Keyboards.back_to_main()
        )
    except Exception as e:
        logger.error(f"Failed to clear cart: {e}")
        await MessageManager.safe_edit_message(
            callback,
            "❌ Ошибка при очистке корзины",
            Keyboards.back_to_main()
        )

@router.callback_query(F.data == 'select_currency')
async def select_currency_handler(callback: types.CallbackQuery, state: FSMContext):
    """Выбор валюты"""
    await MessageManager.safe_answer_callback(callback)

    currencies = await DatabaseService.get_currencies()
    keyboard = Keyboards.currencies_menu(currencies)

    await MessageManager.safe_edit_message(
        callback,
        "💱 Выберите валюту:",
        keyboard
    )

@router.callback_query(F.data.startswith('currency_'))
async def currency_handler(callback: types.CallbackQuery, state: FSMContext):
    """Установка валюты"""
    await MessageManager.safe_answer_callback(callback)

    currency_id = int(callback.data.split('_')[1])
    user_id = callback.from_user.id

    try:
        await DatabaseService.set_user_currency(user_id, currency_id)
        currency_name = await DatabaseService.get_currency_name(currency_id)

        await MessageManager.safe_edit_message(
            callback,
            f"✅ Валюта изменена на {currency_name}!",
            Keyboards.back_to_main()
        )

    except Exception as e:
        logger.error(f"Failed to set currency: {e}")
        await MessageManager.safe_edit_message(
            callback,
            "❌ Ошибка при изменении валюты",
            Keyboards.back_to_main()
        )

@router.callback_query(F.data == 'profile')
async def profile_handler(callback: types.CallbackQuery, state: FSMContext):
    """Профиль пользователя"""
    await MessageManager.safe_answer_callback(callback)

    user = callback.from_user
    currency_code, _ = await DatabaseService.get_user_currency(user.id)
    is_banned = await DatabaseService.is_user_banned(user.id)

    text = (
        f"👤 Ваш профиль\n\n"
        f"🆔 ID: {user.id}\n"
        f"👤 Имя: {user.first_name or 'Не указано'}\n"
        f"💱 Валюта: {currency_code}\n"
        f"🚫 Статус: {'Заблокирован' if is_banned else 'Активен'}\n"
    )

    if user.username:
        text += f"📱 Username: @{user.username}\n"

    await MessageManager.safe_edit_message(
        callback,
        text,
        Keyboards.back_to_main()
    )

# === СИСТЕМА ОФОРМЛЕНИЯ ЗАКАЗОВ ===
@router.callback_query(F.data == 'checkout')
async def checkout_handler(callback: types.CallbackQuery, state: FSMContext):
    """Начало оформления заказа"""
    await MessageManager.safe_answer_callback(callback)

    user_id = callback.from_user.id
    cart_items = await DatabaseService.get_cart_items(user_id)

    if not cart_items:
        await MessageManager.safe_edit_message(
            callback,
            "🛒 Ваша корзина пуста!\n\nДобавьте товары для оформления заказа.",
            Keyboards.back_to_main()
        )
        return

    currency_code, _ = await DatabaseService.get_user_currency(user_id)
    total = 0.0
    order_details = ["📋 Подтверждение заказа:\n"]

    for item in cart_items:
        price = await DatabaseService.get_item_price(item['id'], currency_code)
        total += price
        order_details.append(
            f"• {item['name']} (📏 {item['size']}) - {price:.2f} {currency_code}"
        )

    order_details.append(f"\n💰 Итого: {total:.2f} {currency_code}")
    order_details.append("\n✅ Подтвердите заказ:")

    text = "\n".join(order_details)

    await state.update_data(
        cart_items=cart_items,
        total_price=total,
        currency_code=currency_code
    )
    await state.set_state(OrderStates.CONFIRM_ORDER)

    await MessageManager.safe_edit_message(
        callback,
        text,
        Keyboards.confirm_order()
    )

@router.callback_query(F.data == 'confirm_order', OrderStates.CONFIRM_ORDER)
async def confirm_order_handler(callback: types.CallbackQuery, state: FSMContext):
    """Подтверждение заказа"""
    await MessageManager.safe_answer_callback(callback)

    try:
        data = await state.get_data()
        user_id = callback.from_user.id
        cart_items = data['cart_items']
        total_price = data['total_price']
        currency_code = data['currency_code']

        order_items = []
        for item in cart_items:
            price = await DatabaseService.get_item_price(item['id'], currency_code)
            order_items.append({
                'name': item['name'],
                'size': item['size'],
                'price': price
            })

        order_id = await DatabaseService.create_order(
            user_id, order_items, total_price, currency_code
        )

        await DatabaseService.clear_cart(user_id)

        user_info = {
            'id': user_id,
            'username': callback.from_user.username,
            'full_name': callback.from_user.full_name
        }

        await notification_service.send_order_notification(
            user_info, order_items, total_price, currency_code, order_id
        )

        success_text = (
            f"✅ Заказ #{order_id} успешно оформлен!\n\n"
            f"💰 Сумма заказа: {total_price:.2f} {currency_code}\n\n"
            "🚚 Ожидайте подтверждения заказа менеджером!"
        )

        await MessageManager.safe_edit_message(
            callback,
            success_text,
            Keyboards.order_success()
        )

        await state.clear()

    except Exception as e:
        logger.error(f"Failed to create order: {e}")
        await MessageManager.safe_edit_message(
            callback,
            "❌ Ошибка при оформлении заказа\n\nПопробуйте еще раз или обратитесь к администратору.",
            Keyboards.back_to_main()
        )
        await state.clear()

# Обработчики для админов
@router.callback_query(F.data.startswith("accept_order_"))
async def accept_order(callback: types.CallbackQuery):
    if callback.from_user.id not in Config.ADMIN_IDS:
        await callback.answer("У вас нет прав для выполнения этого действия", show_alert=True)
        return

    user_id = callback.data.split("_")[-1]

    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ <b>Заказ принят</b> администратором",
            parse_mode="HTML"
        )

        try:
            await bot.send_message(
                chat_id=user_id,
                text="✅ Ваш заказ принят! Наш менеджер скоро свяжется с вами для уточнения деталей."
            )
            logger.info(f"Уведомление о принятии заказа отправлено пользователю {user_id}")
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

        await callback.answer("Заказ принят! Уведомление отправлено клиенту.")
    except Exception as e:
        logger.error(f"Ошибка при принятии заказа: {e}")
        await callback.answer("Произошла ошибка при обработке заказа", show_alert=True)

@router.callback_query(F.data.startswith("reject_order_"))
async def reject_order(callback: types.CallbackQuery):
    if callback.from_user.id not in Config.ADMIN_IDS:
        await callback.answer("У вас нет прав для выполнения этого действия", show_alert=True)
        return

    user_id = callback.data.split("_")[-1]

    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n❌ <b>Заказ отклонен</b> администратором",
            parse_mode="HTML"
        )

        try:
            await bot.send_message(
                chat_id=user_id,
                text="❌ К сожалению, ваш заказ отклонен. Пожалуйста, свяжитесь с нами для уточнения деталей."
            )
            logger.info(f"Уведомление об отклонении заказа отправлено пользователю {user_id}")
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

        await callback.answer("Заказ отклонен! Уведомление отправлено клиенту.")
    except Exception as e:
        logger.error(f"Ошибка при отклонении заказа: {e}")
        await callback.answer("Произошла ошибка при обработке заказа", show_alert=True)

@router.message(Command('ban'), lambda message: message.chat.id == int(Config.NOTIFICATIONS_CHANNEL_ID))
async def ban_user_command(message: types.Message):
    """Команда для бана пользователя в приватном канале"""
    if message.from_user.id not in Config.ADMIN_IDS:
        await message.answer("🚫 У вас нет прав для выполнения этой команды.")
        return

    try:
        user_id = int(message.text.split()[1])
    except (IndexError, ValueError):
        await message.answer("❌ Использование: /ban <user_id>")
        return

    if await DatabaseService.ban_user(user_id):
        await message.answer(f"✅ Пользователь {user_id} заблокирован.")
        try:
            await bot.send_message(
                chat_id=user_id,
                text="🚫 Ваш аккаунт заблокирован. Обратитесь к администратору."
            )
            logger.info(f"Уведомление о бане отправлено пользователю {user_id}")
        except TelegramBadRequest as e:
            logger.warning(f"Не удалось уведомить пользователя {user_id} о бане: {e}")
    else:
        await message.answer(f"⚠️ Пользователь {user_id} уже заблокирован.")

@router.message(Command('unban'), lambda message: message.chat.id == int(Config.NOTIFICATIONS_CHANNEL_ID))
async def unban_user_command(message: types.Message):
    """Команда для разбана пользователя в приватном канале"""
    if message.from_user.id not in Config.ADMIN_IDS:
        await message.answer("🚫 У вас нет прав для выполнения этой команды.")
        return

    try:
        user_id = int(message.text.split()[1])
    except (IndexError, ValueError):
        await message.answer("❌ Использование: /unban <user_id>")
        return

    if await DatabaseService.unban_user(user_id):
        await message.answer(f"✅ Пользователь {user_id} разблокирован.")
        try:
            await bot.send_message(
                chat_id=user_id,
                text="✅ Ваш аккаунт разблокирован. Вы снова можете пользоваться ботом."
            )
            logger.info(f"Уведомление о разбане отправлено пользователю {user_id}")
        except TelegramBadRequest as e:
            logger.warning(f"Не удалось уведомить пользователя {user_id} о разбане: {e}")
    else:
        await message.answer(f"⚠️ Пользователь {user_id} не был заблокирован.")

# Обработчик неизвестных callback'ов
@router.callback_query()
async def unknown_callback_handler(callback: types.CallbackQuery, state: FSMContext):
    """Обработчик неизвестных callback'ов"""
    await MessageManager.safe_answer_callback(callback)
    logger.warning(f"Unknown callback data: {callback.data}")

    await MessageManager.safe_edit_message(
        callback,
        "❌ Неизвестная команда",
        Keyboards.back_to_main()
    )

# Обработчик текстовых сообщений
@router.message()
async def text_message_handler(message: types.Message, state: FSMContext):
    """Обработчик текстовых сообщений"""
    current_state = await state.get_state()

    if current_state in [OrderStates.CONFIRM_ORDER]:
        return

    await message.answer(
        "🤖 Используйте кнопки меню для навигации",
        reply_markup=Keyboards.main_menu()
    )

# Подключение роутера
dp.include_router(router)

# Основная функция
async def main():
    """Запуск бота"""
    logger.info("Starting Telegram bot")
    logger.info(f"Admin IDs: {Config.ADMIN_IDS}")
    logger.info(f"Orders Channel ID: {Config.ORDERS_CHANNEL_ID}")
    logger.info(f"Notifications Channel ID: {Config.NOTIFICATIONS_CHANNEL_ID}")
    init_db()
    logger.info("Database initialized")

    await notification_service.send_bot_started_notification()

    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Error in main: {e}")
        raise
    finally:
        await bot.session.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise