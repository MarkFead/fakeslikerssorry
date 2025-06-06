from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
import os
from werkzeug.utils import secure_filename
import logging
from contextlib import contextmanager
from functools import wraps
import shutil
import time

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24))

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# Конфигурация
class Config:
    UPLOAD_FOLDER = 'static/uploads'
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
    DATABASE_PATH = 'shop.db'


app.config.from_object(Config)
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)


# Функция для создания соединения с БД
def create_connection():
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


# Контекстный менеджер для работы с БД
@contextmanager
def get_db_connection():
    conn = None
    try:
        conn = create_connection()
        yield conn
    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        if conn:
            conn.commit()
            conn.close()


# Декоратор для обработки ошибок
def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {e}")
            flash('Произошла ошибка. Попробуйте снова.', 'error')
            return redirect(url_for('home'))

    return decorated_function


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS


def check_column_exists(conn, table_name, column_name):
    """Проверяет существование колонки в таблице"""
    try:
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = [column[1] for column in cursor.fetchall()]
        return column_name in columns
    except sqlite3.Error as e:
        logger.warning(f"Error checking column existence: {e}")
        return False


def migrate_database():
    """Выполняет миграции базы данных"""
    conn = None
    try:
        conn = create_connection()
        c = conn.cursor()

        # Проверяем и добавляем недостающие колонки
        migrations = [
            {
                'table': 'categories',
                'column': 'created_at',
                'sql': 'ALTER TABLE categories ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
            },
            {
                'table': 'currencies',
                'column': 'symbol',
                'sql': 'ALTER TABLE currencies ADD COLUMN symbol TEXT DEFAULT ""'
            },
            {
                'table': 'currencies',
                'column': 'is_active',
                'sql': 'ALTER TABLE currencies ADD COLUMN is_active BOOLEAN DEFAULT TRUE'
            },
            {
                'table': 'items',
                'column': 'created_at',
                'sql': 'ALTER TABLE items ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
            },
            {
                'table': 'items',
                'column': 'updated_at',
                'sql': 'ALTER TABLE items ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
            },
            {
                'table': 'item_images',
                'column': 'is_primary',
                'sql': 'ALTER TABLE item_images ADD COLUMN is_primary BOOLEAN DEFAULT FALSE'
            },
            {
                'table': 'carts',
                'column': 'created_at',
                'sql': 'ALTER TABLE carts ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
            }
        ]

        for migration in migrations:
            if not check_column_exists(conn, migration['table'], migration['column']):
                try:
                    c.execute(migration['sql'])
                    logger.info(f"Added column {migration['column']} to {migration['table']}")
                except sqlite3.Error as e:
                    logger.warning(f"Failed to add column {migration['column']} to {migration['table']}: {e}")

        # Обновляем символы валют если они пустые
        c.execute("UPDATE currencies SET symbol = '₽' WHERE name = 'RUB' AND (symbol = '' OR symbol IS NULL)")
        c.execute("UPDATE currencies SET symbol = 'Br' WHERE name = 'BYN' AND (symbol = '' OR symbol IS NULL)")

        conn.commit()
        logger.info("Database migration completed")
    except sqlite3.Error as e:
        logger.error(f"Error during database migration: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def init_db():
    """Инициализация базы данных с улучшенной структурой"""
    conn = None
    try:
        conn = create_connection()
        c = conn.cursor()

        # Создание таблиц (только если не существуют)
        tables = [
            '''CREATE TABLE IF NOT EXISTS categories
               (
                   id
                   INTEGER
                   PRIMARY
                   KEY
                   AUTOINCREMENT,
                   name
                   TEXT
                   NOT
                   NULL
                   UNIQUE,
                   image_path
                   TEXT,
                   folder_name
                   TEXT
                   UNIQUE
               )''',
            '''CREATE TABLE IF NOT EXISTS items
            (
                id
                INTEGER
                PRIMARY
                KEY
                AUTOINCREMENT,
                category_id
                INTEGER
                NOT
                NULL,
                name
                TEXT
                NOT
                NULL,
                description
                TEXT,
                sizes
                TEXT
                NOT
                NULL,
                stock_quantity
                INTEGER
                DEFAULT
                0,
                FOREIGN
                KEY
               (
                category_id
               ) REFERENCES categories
               (
                   id
               ) ON DELETE CASCADE
                )''',
            '''CREATE TABLE IF NOT EXISTS item_images
            (
                id
                INTEGER
                PRIMARY
                KEY
                AUTOINCREMENT,
                item_id
                INTEGER
                NOT
                NULL,
                image_path
                TEXT
                NOT
                NULL,
                is_primary
                BOOLEAN
                DEFAULT
                FALSE,
                FOREIGN
                KEY
               (
                item_id
               ) REFERENCES items
               (
                   id
               ) ON DELETE CASCADE
                )''',
            '''CREATE TABLE IF NOT EXISTS currencies
               (
                   id
                   INTEGER
                   PRIMARY
                   KEY
                   AUTOINCREMENT,
                   name
                   TEXT
                   NOT
                   NULL
                   UNIQUE,
                   rate
                   REAL
                   NOT
                   NULL,
                   symbol
                   TEXT
                   DEFAULT
                   ""
               )''',
            '''CREATE TABLE IF NOT EXISTS item_prices
            (
                id
                INTEGER
                PRIMARY
                KEY
                AUTOINCREMENT,
                item_id
                INTEGER
                NOT
                NULL,
                currency_id
                INTEGER
                NOT
                NULL,
                price
                REAL
                NOT
                NULL,
                FOREIGN
                KEY
               (
                item_id
               ) REFERENCES items
               (
                   id
               ) ON DELETE CASCADE,
                FOREIGN KEY
               (
                   currency_id
               ) REFERENCES currencies
               (
                   id
               ),
                UNIQUE
               (
                   item_id,
                   currency_id
               )
                )''',
            '''CREATE TABLE IF NOT EXISTS carts
            (
                user_id
                INTEGER
                NOT
                NULL,
                item_id
                INTEGER
                NOT
                NULL,
                size
                TEXT
                NOT
                NULL,
                quantity
                INTEGER
                DEFAULT
                1,
                FOREIGN
                KEY
               (
                item_id
               ) REFERENCES items
               (
                   id
               ) ON DELETE CASCADE
                )''',
            '''CREATE TABLE IF NOT EXISTS user_preferences
            (
                user_id
                INTEGER
                PRIMARY
                KEY,
                currency_id
                INTEGER,
                FOREIGN
                KEY
               (
                currency_id
               ) REFERENCES currencies
               (
                   id
               )
                )''',
            '''CREATE TABLE IF NOT EXISTS orders
               (
                   id
                   INTEGER
                   PRIMARY
                   KEY
                   AUTOINCREMENT,
                   user_id
                   INTEGER
                   NOT
                   NULL,
                   order_data
                   TEXT
                   NOT
                   NULL,
                   total_price
                   REAL
                   NOT
                   NULL,
                   currency_code
                   TEXT
                   NOT
                   NULL,
                   status
                   TEXT
                   DEFAULT
                   'pending',
                   created_at
                   TIMESTAMP
                   DEFAULT
                   CURRENT_TIMESTAMP
               )'''
        ]

        for table in tables:
            c.execute(table)

        conn.commit()

        # Проверяем есть ли валюты
        c.execute("SELECT COUNT(*) FROM currencies")
        if c.fetchone()[0] == 0:
            currencies = [
                ('RUB', 1.0, '₽'),
                ('BYN', 0.037, 'Br')
            ]
            c.executemany("INSERT INTO currencies (name, rate, symbol) VALUES (?, ?, ?)", currencies)
            conn.commit()
            logger.info("Currencies initialized")

        # Создание индексов для оптимизации
        indexes = [
            'CREATE INDEX IF NOT EXISTS idx_items_category ON items(category_id)',
            'CREATE INDEX IF NOT EXISTS idx_item_images_item ON item_images(item_id)',
            'CREATE INDEX IF NOT EXISTS idx_item_prices_item ON item_prices(item_id)',
            'CREATE INDEX IF NOT EXISTS idx_carts_user ON carts(user_id)'
        ]

        for index in indexes:
            try:
                c.execute(index)
            except sqlite3.Error as e:
                logger.warning(f"Failed to create index: {e}")

        conn.commit()
        logger.info("Database initialized successfully")
    except sqlite3.Error as e:
        logger.error(f"Error during database initialization: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

    # Выполняем миграции отдельно
    migrate_database()


def create_placeholder_if_needed():
    """Создает placeholder изображение если его нет"""
    placeholder_path = os.path.join(Config.UPLOAD_FOLDER, 'placeholder.jpg')
    if not os.path.exists(placeholder_path):
        try:
            # Создаем простое placeholder изображение
            from PIL import Image, ImageDraw, ImageFont

            # Создаем изображение 400x300 пикселей
            img = Image.new('RGB', (400, 300), color='#f3f4f6')
            draw = ImageDraw.Draw(img)

            # Добавляем текст
            try:
                # Пытаемся использовать системный шрифт
                font = ImageFont.truetype("arial.ttf", 24)
            except:
                # Если не получается, используем стандартный
                font = ImageFont.load_default()

            text = "Нет изображения"
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]

            x = (400 - text_width) // 2
            y = (300 - text_height) // 2

            draw.text((x, y), text, fill='#9ca3af', font=font)

            # Сохраняем изображение
            img.save(placeholder_path, 'JPEG')
            logger.info(f"Created placeholder image at {placeholder_path}")

        except ImportError:
            logger.warning("PIL not available, cannot create placeholder image")
        except Exception as e:
            logger.warning(f"Failed to create placeholder image: {e}")


class DatabaseService:
    """Сервис для работы с базой данных"""

    @staticmethod
    def get_categories():
        conn = None
        try:
            conn = create_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM categories ORDER BY name")
            return cursor.fetchall()
        finally:
            if conn:
                conn.close()

    @staticmethod
    def get_category_by_id(category_id):
        conn = None
        try:
            conn = create_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM categories WHERE id = ?", (category_id,))
            return cursor.fetchone()
        finally:
            if conn:
                conn.close()

    @staticmethod
    def get_item_by_id(item_id):
        conn = None
        try:
            conn = create_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM items WHERE id = ?", (item_id,))
            return cursor.fetchone()
        finally:
            if conn:
                conn.close()

    @staticmethod
    def get_item_images(item_id):
        conn = None
        try:
            conn = create_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM item_images WHERE item_id = ? ORDER BY is_primary DESC", (item_id,))
            return cursor.fetchall()
        finally:
            if conn:
                conn.close()

    @staticmethod
    def get_item_prices(item_id):
        conn = None
        try:
            conn = create_connection()
            cursor = conn.cursor()
            cursor.execute("""
                           SELECT ip.*, c.name as currency_name, c.symbol
                           FROM item_prices ip
                                    JOIN currencies c ON ip.currency_id = c.id
                           WHERE ip.item_id = ?
                           """, (item_id,))
            return cursor.fetchall()
        finally:
            if conn:
                conn.close()

    @staticmethod
    def get_items_by_category(category_id, currency_code='RUB'):
        conn = None
        try:
            conn = create_connection()
            cursor = conn.cursor()
            query = '''
                    SELECT i.*,
                           GROUP_CONCAT(ii.image_path ORDER BY ii.is_primary DESC) as images,
                           COALESCE(ip.price, 0)                                   as price
                    FROM items i
                             LEFT JOIN item_images ii ON i.id = ii.item_id
                             LEFT JOIN item_prices ip ON i.id = ip.item_id
                             LEFT JOIN currencies c ON ip.currency_id = c.id AND c.name = ?
                    WHERE i.category_id = ?
                    GROUP BY i.id
                    ORDER BY i.name \
                    '''
            cursor.execute(query, (currency_code, category_id))
            items = cursor.fetchall()
            result = []
            for item in items:
                item_dict = dict(item)
                if item_dict['images']:
                    image_paths = item_dict['images'].split(',')
                    valid_images = []
                    for img_path in image_paths:
                        if img_path and img_path.strip():
                            full_path = os.path.join('static', img_path.replace('/', os.sep))
                            if os.path.exists(full_path):
                                valid_images.append(img_path.strip())
                            else:
                                logger.warning(f"Image not found: {full_path}")
                    item_dict['images'] = ','.join(valid_images) if valid_images else None
                result.append(item_dict)
            return result
        finally:
            if conn:
                conn.close()

    @staticmethod
    def get_currencies():
        conn = None
        try:
            conn = create_connection()
            cursor = conn.cursor()
            try:
                # Пробуем запрос с is_active
                cursor.execute("SELECT * FROM currencies WHERE is_active = 1 ORDER BY name")
                currencies = cursor.fetchall()
                if currencies:
                    return currencies
                # Если нет активных валют, возвращаем все
                cursor.execute("SELECT * FROM currencies ORDER BY name")
                return cursor.fetchall()
            except sqlite3.OperationalError:
                # Если колонки is_active нет, используем базовый запрос
                cursor.execute("SELECT * FROM currencies ORDER BY name")
                return cursor.fetchall()
        finally:
            if conn:
                conn.close()


class FileService:
    """Сервис для работы с файлами"""

    @staticmethod
    def get_next_folder_name(base_path, prefix='ct'):
        if not os.path.exists(base_path):
            os.makedirs(base_path, exist_ok=True)
            return f'{prefix}1'

        existing_folders = [f for f in os.listdir(base_path)
                            if f.startswith(prefix) and f[len(prefix):].isdigit()]
        next_number = max([int(f[len(prefix):]) for f in existing_folders] + [0]) + 1
        return f'{prefix}{next_number}'

    @staticmethod
    def save_uploaded_file(file, folder_path):
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            name, ext = os.path.splitext(filename)
            filename = f"{name}_{int(time.time())}{ext}"
            clean_folder_path = folder_path.replace('static' + os.sep, '').replace('static/', '')
            file_path = os.path.join('static', clean_folder_path, filename)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            logger.info(f"Saving file to: {file_path}")
            file.save(file_path)
            if os.path.exists(file_path):
                logger.info(f"File saved successfully: {file_path}")
                relative_path = os.path.join(clean_folder_path, filename).replace(os.sep, '/')
                return relative_path
            else:
                logger.error(f"Failed to save file: {file_path}")
        logger.warning(f"File not saved: {file.filename if file else 'No file'}")
        return None

    @staticmethod
    def delete_file_safe(file_path):
        try:
            full_path = os.path.join('static', file_path.replace('/', os.sep))
            if os.path.exists(full_path):
                os.remove(full_path)
                logger.info(f"Deleted file: {file_path}")
        except OSError as e:
            logger.warning(f"Failed to delete file {file_path}: {e}")

# Маршруты
@app.route('/')
@handle_errors
def home():
    selected_currency = session.get('currency', 'RUB')
    categories = DatabaseService.get_categories()
    currencies = DatabaseService.get_currencies()
    return render_template('index.html',
                           categories=categories,
                           currencies=currencies,
                           selected_currency=selected_currency)


@app.route('/category/<int:category_id>')
@handle_errors
def category(category_id):
    selected_currency = session.get('currency', 'RUB')
    category = DatabaseService.get_category_by_id(category_id)

    if not category:
        flash('Категория не найдена', 'error')
        return redirect(url_for('home'))

    items = DatabaseService.get_items_by_category(category_id, selected_currency)
    currencies = DatabaseService.get_currencies()

    return render_template('category.html',
                           items=items,
                           category=category,
                           currencies=currencies,
                           selected_currency=selected_currency)


@app.route('/edit_item/<int:item_id>', methods=['GET', 'POST'])
@handle_errors
def edit_item(item_id):
    item = DatabaseService.get_item_by_id(item_id)
    if not item:
        flash('Товар не найден', 'error')
        return redirect(url_for('home'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        category_id = request.form.get('category_id')
        description = request.form.get('description', '').strip()
        sizes = request.form.get('sizes', '').strip()
        stock_quantity = request.form.get('stock_quantity', 0)

        if not name or not category_id or not sizes:
            flash('Заполните все обязательные поля', 'error')
            return redirect(url_for('edit_item', item_id=item_id))

        try:
            category_id = int(category_id)
            stock_quantity = int(stock_quantity)
        except ValueError:
            flash('Некорректные данные', 'error')
            return redirect(url_for('edit_item', item_id=item_id))

        conn = None
        try:
            conn = create_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE items SET category_id = ?, name = ?, description = ?, sizes = ?, stock_quantity = ? WHERE id = ?",
                (category_id, name, description, sizes, stock_quantity, item_id)
            )

            # Обновляем цены
            currencies = DatabaseService.get_currencies()
            for currency in currencies:
                price_field = f'price_{currency["id"]}'
                price = request.form.get(price_field)
                if price:
                    try:
                        price = float(price)
                        cursor.execute(
                            "INSERT OR REPLACE INTO item_prices (item_id, currency_id, price) VALUES (?, ?, ?)",
                            (item_id, currency['id'], price)
                        )
                    except ValueError:
                        logger.warning(f"Invalid price for currency {currency['name']}: {price}")

            # Обрабатываем новые изображения
            uploaded_files = request.files.getlist('images')
            if uploaded_files and any(f.filename for f in uploaded_files):
                category = DatabaseService.get_category_by_id(category_id)
                category_folder = category['folder_name']
                if not category_folder:
                    flash('Ошибка: у категории нет папки для файлов', 'error')
                    return redirect(url_for('add_item'))
                    category_path = os.path.join('uploads', category_folder)
                    os.makedirs(os.path.join('static', category_path), exist_ok=True)

                    primary_image_index = request.form.get('primary_image', '0')
                    try:
                        primary_image_index = int(primary_image_index)
                    except (ValueError, TypeError):
                        primary_image_index = 0

                    if request.form.get('replace_images') == 'true':
                        old_images = DatabaseService.get_item_images(item_id)
                        for img in old_images:
                            FileService.delete_file_safe(img['image_path'])
                        cursor.execute("DELETE FROM item_images WHERE item_id = ?", (item_id,))

                    for i, file in enumerate(uploaded_files):
                        if file and file.filename and allowed_file(file.filename):
                            relative_path = FileService.save_uploaded_file(file, category_path, i)
                            if relative_path:
                                is_primary = (i == primary_image_index)
                                cursor.execute(
                                    "INSERT INTO item_images (item_id, image_path, is_primary) VALUES (?, ?, ?)",
                                    (item_id, relative_path, is_primary)
                                )

            conn.commit()
            flash('Товар успешно обновлен!', 'success')
        finally:
            if conn:
                conn.close()

        return redirect(url_for('category', category_id=item['category_id']))

    categories = DatabaseService.get_categories()
    currencies = DatabaseService.get_currencies()
    item_images = DatabaseService.get_item_images(item_id)
    item_prices = DatabaseService.get_item_prices(item_id)
    selected_currency = session.get('currency', 'RUB')

    prices_dict = {}
    for price in item_prices:
        prices_dict[price['currency_id']] = price['price']

    return render_template('edit_item.html',
                           item=item,
                           categories=categories,
                           currencies=currencies,
                           item_images=item_images,
                           prices_dict=prices_dict,
                           selected_currency=selected_currency)


@app.route('/add_category', methods=['GET', 'POST'])
@handle_errors
def add_category():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()

        if not name or any(c in name for c in '<>:"/\\|?*'):
            flash('Недопустимое название категории', 'error')
            return redirect(url_for('add_category'))

        folder_name = FileService.get_next_folder_name(Config.UPLOAD_FOLDER)
        category_path = os.path.join('uploads', folder_name)
        os.makedirs(os.path.join('static', category_path), exist_ok=True)
        image_path = None
        if 'image' in request.files:
            image_path = FileService.save_uploaded_file(request.files['image'], category_path)

        conn = None
        try:
            conn = create_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO categories (name, image_path, folder_name) VALUES (?, ?, ?)",
                           (name, image_path, folder_name))
            conn.commit()
            flash('Категория успешно добавлена', 'success')
        finally:
            if conn:
                conn.close()

        return redirect(url_for('home'))

    currencies = DatabaseService.get_currencies()
    selected_currency = session.get('currency', 'RUB')
    return render_template('add_category.html',
                           currencies=currencies,
                           selected_currency=selected_currency)


@app.route('/edit_category/<int:category_id>', methods=['GET', 'POST'])
@handle_errors
def edit_category(category_id):
    category = DatabaseService.get_category_by_id(category_id)
    if not category:
        flash('Категория не найдена', 'error')
        return redirect(url_for('home'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()

        if not name or any(c in name for c in '<>:"/\\|?*'):
            flash('Недопустимое название категории', 'error')
            return redirect(url_for('edit_category', category_id=category_id))

        image_path = category['image_path']

        if 'image' in request.files and request.files['image'].filename:
            if image_path:
                FileService.delete_file_safe(image_path)
            folder_name = category['folder_name']
            category_path = os.path.join('uploads', folder_name)
            os.makedirs(os.path.join('static', category_path), exist_ok=True)
            image_path = FileService.save_uploaded_file(request.files['image'], category_path)

        conn = None
        try:
            conn = create_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE categories SET name = ?, image_path = ? WHERE id = ?",
                           (name, image_path, category_id))
            conn.commit()
            flash('Категория успешно обновлена', 'success')
        finally:
            if conn:
                conn.close()

        return redirect(url_for('home'))

    currencies = DatabaseService.get_currencies()
    selected_currency = session.get('currency', 'RUB')
    return render_template('edit_category.html',
                           category=category,
                           currencies=currencies,
                           selected_currency=selected_currency)


@app.route('/add_item', methods=['GET', 'POST'])
@handle_errors
def add_item():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        category_id = request.form.get('category_id')
        description = request.form.get('description', '').strip()
        sizes = request.form.get('sizes', '').strip()
        stock_quantity = request.form.get('stock_quantity', 0)
        if not name or not category_id or not sizes:
            flash('Заполните все обязательные поля', 'error')
            return redirect(url_for('add_item'))
        try:
            category_id = int(category_id)
            stock_quantity = int(stock_quantity)
        except ValueError:
            flash('Некорректные данные', 'error')
            return redirect(url_for('add_item'))
        category = DatabaseService.get_category_by_id(category_id)
        if not category:
            flash('Выбранная категория не существует', 'error')
            return redirect(url_for('add_item'))
        category_folder = category['folder_name']
        if not category_folder:
            flash('Ошибка: у категории нет папки для файлов', 'error')
            return redirect(url_for('add_item'))
        category_path = os.path.join('uploads', category_folder)
        os.makedirs(os.path.join('static', category_path), exist_ok=True)
        conn = None
        try:
            conn = create_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO items (category_id, name, description, sizes, stock_quantity) VALUES (?, ?, ?, ?, ?)",
                (category_id, name, description, sizes, stock_quantity)
            )
            item_id = cursor.lastrowid
            currencies = DatabaseService.get_currencies()
            for currency in currencies:
                price_field = f'price_{currency[1].lower()}'  # Соответствует шаблону
                price = request.form.get(price_field)
                if price:
                    try:
                        price = float(price)
                        cursor.execute(
                            "INSERT INTO item_prices (item_id, currency_id, price) VALUES (?, ?, ?)",
                            (item_id, currency[0], price)  # currency[0] — ID валюты
                        )
                    except ValueError:
                        logger.warning(f"Invalid price for currency {currency[1]}: {price}")
            uploaded_files = request.files.getlist('images')
            primary_image_index = request.form.get('primary_image', '0')
            try:
                primary_image_index = int(primary_image_index)
            except (ValueError, TypeError):
                primary_image_index = 0
            for i, file in enumerate(uploaded_files):
                if file and file.filename and allowed_file(file.filename):
                    relative_path = FileService.save_uploaded_file(file, category_path)
                    is_primary = (i == primary_image_index)
                    cursor.execute(
                        "INSERT INTO item_images (item_id, image_path, is_primary) VALUES (?, ?, ?)",
                        (item_id, relative_path, is_primary)
                    )
            conn.commit()
            flash('Товар успешно добавлен!', 'success')
            return redirect(url_for('home'))
        finally:
            if conn:
                conn.close()
    categories = DatabaseService.get_categories()
    currencies = DatabaseService.get_currencies()
    selected_currency = session.get('currency', 'RUB')
    return render_template('add_item.html', categories=categories, currencies=currencies, selected_currency=selected_currency)


@app.route('/about')
@handle_errors
def about():
    currencies = DatabaseService.get_currencies()
    selected_currency = session.get('currency', 'RUB')
    return render_template('about.html',
                           currencies=currencies,
                           selected_currency=selected_currency)


@app.route('/set_currency/<code>')
def set_currency(code):
    session['currency'] = code
    return redirect(request.referrer or url_for('home'))

@app.route('/manage_bans')
def manage_bans():
    cursors = get_db_connection.cursor()
    cursors.execute('SELECT user_id, banned_at FROM banned_users')
    banned_users = cursors.fetchall()
    cursors.execute('SELECT id, name FROM items WHERE is_blocked = 1')
    blocked_items = cursors.fetchall()
    return render_template('manage_bans.html', banned_users=banned_users, blocked_items=blocked_items)

@app.route('/api/categories')
def api_categories():
    """API endpoint для получения категорий"""
    categories = DatabaseService.get_categories()
    return jsonify([dict(cat) for cat in categories])


# Обработчик ошибок
@app.errorhandler(404)
def not_found(error):
    currencies = DatabaseService.get_currencies()
    selected_currency = session.get('currency', 'RUB')
    return render_template('error.html',
                           error_code=404,
                           error_message="Страница не найдена",
                           currencies=currencies,
                           selected_currency=selected_currency), 404


@app.errorhandler(500)
def internal_error(error):
    currencies = DatabaseService.get_currencies()
    selected_currency = session.get('currency', 'RUB')
    return render_template('error.html',
                           error_code=500,
                           error_message="Внутренняя ошибка сервера",
                           currencies=currencies,
                           selected_currency=selected_currency), 500


if __name__ == '__main__':
    init_db()
    create_placeholder_if_needed()
    app.run(debug=True, host='0.0.0.0', port=5000)
