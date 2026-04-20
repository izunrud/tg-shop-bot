import os
import logging
import sqlite3
import time
import threading
from datetime import datetime
from telebot import TeleBot, types
from flask import Flask

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8311930730:AAFguCCuRXlOcGaTK76rZx5NwuompnGYdOw")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6697402034"))
DB_NAME = "shop.db"
RATE_LIMIT_SEC = 3

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("shop_bot")

# ================= DATABASE =================
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            joined_at TEXT,
            balance REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY,
            name TEXT,
            emoji TEXT DEFAULT '📦'
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY,
            cat_id INTEGER,
            name TEXT,
            desc TEXT,
            price REAL,
            is_active BOOLEAN DEFAULT 1
        );        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prod_id INTEGER,
            key_data TEXT,
            status TEXT DEFAULT 'available'
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            prod_id INTEGER,
            key_sent TEXT,
            amount REAL,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            status TEXT DEFAULT 'open',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS ticket_msgs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER,
            sender TEXT,
            text TEXT,
            created_at TEXT
        );
    """)
    conn.commit()
    conn.close()

# ================= RATE LIMIT =================
user_last_action = {}

def check_rate_limit(user_id):
    now = time.time()
    last = user_last_action.get(user_id, 0)
    if now - last < RATE_LIMIT_SEC:
        return False
    user_last_action[user_id] = now
    return True

# ================= BOT INIT =================
bot = TeleBot(BOT_TOKEN, parse_mode="HTML")
user_states = {}

def is_admin(uid):
    return uid == ADMIN_ID

def bottom_keyboard(admin=False):    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("🛍 Каталог"),
        types.KeyboardButton("👤 Профиль")
    )
    kb.add(types.KeyboardButton("🎫 Поддержка"))
    if admin:
        kb.add(types.KeyboardButton("📊 Админка"))
    return kb

# ================= HANDLERS: MAIN MENU =================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid = message.from_user.id
    uname = message.from_user.username
    user_states.pop(uid, None)
    
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO users (id, username, joined_at) VALUES (?, ?, ?)",
        (uid, uname, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    
    bot.send_message(
        uid,
        "🏠 <b>Главное меню</b>\n\nВыберите раздел:",
        reply_markup=bottom_keyboard(is_admin(uid))
    )

@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def show_profile(message):
    if not check_rate_limit(message.from_user.id):
        return
    
    conn = get_db()
    user = conn.execute(
        "SELECT balance, joined_at FROM users WHERE id=?",
        (message.from_user.id,)
    ).fetchone()
    conn.close()
    
    if not user:
        return cmd_start(message)
    
    conn = get_db()
    order_count = conn.execute(
        "SELECT count() FROM orders WHERE user_id=?",
        (message.from_user.id,)    ).fetchone()[0]
    conn.close()
    
    text = (
        f"👤 <b>Личный кабинет</b>\n\n"
        f"💳 Баланс: <code>{user['balance']}₽</code>\n"
        f"📦 Заказов: <code>{order_count}</code>\n"
        f"📅 Регистрация: {user['joined_at'][:10]}"
    )
    bot.send_message(
        message.chat.id,
        text,
        reply_markup=bottom_keyboard(is_admin(message.from_user.id))
    )

# ================= HANDLERS: CATALOG =================
@bot.message_handler(func=lambda m: m.text == "🛍 Каталог")
def show_categories(message):
    conn = get_db()
    categories = conn.execute(
        "SELECT id, name, emoji FROM categories"
    ).fetchall()
    conn.close()
    
    if not categories:
        return bot.send_message(
            message.chat.id,
            "⚠️ Каталог пока пуст.",
            reply_markup=bottom_keyboard(is_admin(message.from_user.id))
        )
    
    kb = types.InlineKeyboardMarkup(row_width=1)
    for cat in categories:
        kb.add(
            types.InlineKeyboardButton(
                f"{cat['emoji']} {cat['name']}",
                callback_data=f"cat_{cat['id']}"
            )
        )
    
    bot.send_message(
        message.chat.id,
        "📂 <b>Категории:</b>",
        reply_markup=kb
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("cat_"))
def show_products(callback):
    cid = int(callback.data.split("_")[1])
        conn = get_db()
    products = conn.execute(
        "SELECT id, name, price FROM products WHERE cat_id=? AND is_active=1",
        (cid,)
    ).fetchall()
    conn.close()
    
    if not products:
        return bot.answer_callback_query(
            callback.id,
            "Товаров нет",
            show_alert=True
        )
    
    kb = types.InlineKeyboardMarkup(row_width=1)
    for prod in products:
        pid = prod['id']
        conn = get_db()
        stock = conn.execute(
            "SELECT count() FROM inventory WHERE prod_id=? AND status='available'",
            (pid,)
        ).fetchone()[0]
        conn.close()
        
        if stock > 0:
            btn_text = f"🛒 {prod['name']} | {prod['price']}₽ ({stock} шт.)"
            kb.add(types.InlineKeyboardButton(btn_text, callback_data=f"buy_{pid}"))
        else:
            btn_text = f"❌ {prod['name']} | {prod['price']}₽ (Нет)"
            kb.add(types.InlineKeyboardButton(btn_text, callback_data="no_stock"))
    
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_menu"))
    
    bot.edit_message_text(
        "📦 <b>Товары:</b>",
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        reply_markup=kb
    )
    bot.answer_callback_query(callback.id)

@bot.callback_query_handler(func=lambda c: c.data == "back_menu")
def back_to_menu(callback):
    bot.delete_message(callback.message.chat.id, callback.message.message_id)
    cmd_start(callback)

@bot.callback_query_handler(func=lambda c: c.data == "no_stock")
def no_stock_alert(callback):
    bot.answer_callback_query(
        callback.id,        "Товара нет на складе",
        show_alert=True
    )

# ================= HANDLERS: BUY & AUTO-DELIVERY =================
@bot.callback_query_handler(func=lambda c: c.data.startswith("buy_"))
def process_purchase(callback):
    uid = callback.from_user.id
    pid = int(callback.data.split("_")[1])
    
    if not check_rate_limit(uid):
        return bot.answer_callback_query(
            callback.id,
            "Подождите немного...",
            show_alert=True
        )
    
    conn = get_db()
    
    # Get product info
    product = conn.execute(
        "SELECT name, price FROM products WHERE id=?",
        (pid,)
    ).fetchone()
    if not product:
        conn.close()
        return bot.answer_callback_query(
            callback.id,
            "Товар не найден",
            show_alert=True
        )
    
    # Get available key
    item = conn.execute(
        "SELECT id, key_data FROM inventory WHERE prod_id=? AND status='available' LIMIT 1",
        (pid,)
    ).fetchone()
    if not item:
        conn.close()
        return bot.answer_callback_query(
            callback.id,
            "❌ Товар закончился!",
            show_alert=True
        )
    
    key_id = item['id']
    key_data = item['key_data']
    
    # Atomic transaction: mark key as sold + create order
    conn.execute("UPDATE inventory SET status='sold' WHERE id=?", (key_id,))    conn.execute(
        "INSERT INTO orders (user_id, prod_id, key_sent, amount, created_at) VALUES (?, ?, ?, ?, ?)",
        (uid, pid, key_data, product['price'], datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    
    # Send key to user
    bot.send_message(
        uid,
        f"✅ <b>Покупка успешна!</b>\n\n"
        f"📦 Товар: {product['name']}\n"
        f"💰 Списано: {product['price']}₽\n\n"
        f"🔑 <b>Твой ключ / Данные:</b>\n"
        f"<code>{key_data}</code>\n\n"
        f"<i>Сохрани это сообщение!</i>"
    )
    bot.answer_callback_query(callback.id, "Выдано!")

# ================= HANDLERS: SUPPORT =================
@bot.message_handler(func=lambda m: m.text == "🎫 Поддержка")
def open_support(message):
    user_states[message.from_user.id] = "support"
    bot.send_message(
        message.chat.id,
        "🎫 <b>Техподдержка</b>\n\n"
        "Опишите вашу проблему.\n"
        "Для возврата в меню: /start",
        reply_markup=bottom_keyboard(is_admin(message.from_user.id))
    )

@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "support")
def handle_support_message(message):
    conn = get_db()
    
    # Create ticket
    ticket_id = conn.execute(
        "INSERT INTO tickets (user_id, created_at) VALUES (?, ?)",
        (message.from_user.id, datetime.now().isoformat())
    ).lastrowid
    
    # Save message
    conn.execute(
        "INSERT INTO ticket_msgs (ticket_id, sender, text, created_at) VALUES (?, ?, ?, ?)",
        (ticket_id, "user", message.text, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    
    # Notify admin    bot.send_message(
        ADMIN_ID,
        f"🎫 <b>Новый тикет #{ticket_id}</b>\n"
        f"👤 Пользователь: {message.from_user.id}\n"
        f"💬 Сообщение:\n{message.text}\n\n"
        f"<i>Ответить: /reply {message.from_user.id} ваш_текст</i>"
    )
    
    # Confirm to user
    bot.send_message(
        message.chat.id,
        "✅ Сообщение отправлено в поддержку.\n"
        "Ожидайте ответа.\n"
        "Для возврата: /start",
        reply_markup=bottom_keyboard(is_admin(message.from_user.id))
    )
    user_states.pop(message.from_user.id, None)

# ================= HANDLERS: ADMIN =================
@bot.message_handler(func=lambda m: m.text == "📊 Админка" and is_admin(m.from_user.id))
def admin_dashboard(message):
    conn = get_db()
    users_count = conn.execute("SELECT count() FROM users").fetchone()[0]
    orders_count = conn.execute("SELECT count() FROM orders").fetchone()[0]
    revenue = conn.execute("SELECT sum(amount) FROM orders").fetchone()[0] or 0
    conn.close()
    
    text = (
        f"📊 <b>Аналитика</b>\n\n"
        f"👥 Пользователей: <code>{users_count}</code>\n"
        f"💰 Завершённых продаж: <code>{orders_count}</code>\n"
        f"💵 Общая выручка: <code>{revenue}₽</code>\n\n"
        f"<i>Команды управления:</i>\n"
        f"<code>/addcat Название Эмодзи</code>\n"
        f"<code>/addprod CatID Название Цена Описание</code>\n"
        f"<code>/addkeys ProdID</code> (затем скинь ключи списком)\n"
        f"<code>/reply UserID Текст</code> (ответить в тикет)"
    )
    bot.send_message(message.chat.id, text)

@bot.message_handler(commands=["addcat"])
def add_category_command(message):
    if not is_admin(message.from_user.id):
        return
    
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        return bot.send_message(
            message.chat.id,
            "❌ Формат: <code>/addcat Название Эмодзи</code>\n"            "Пример: <code>/addcat Игры 🎮</code>",
            parse_mode="HTML"
        )
    
    name, emoji = parts[1], parts[2]
    conn = get_db()
    conn.execute(
        "INSERT INTO categories (name, emoji) VALUES (?, ?)",
        (name, emoji)
    )
    conn.commit()
    conn.close()
    
    bot.send_message(message.chat.id, f"✅ Категория '<b>{name}</b>' создана!")

@bot.message_handler(commands=["addprod"])
def add_product_command(message):
    if not is_admin(message.from_user.id):
        return
    
    parts = message.text.split(maxsplit=4)
    if len(parts) < 5:
        return bot.send_message(
            message.chat.id,
            "❌ Формат: <code>/addprod CatID Название Цена Описание</code>\n"
            "Пример: <code>/addprod 1 SteamKey 150 Ключ активации</code>",
            parse_mode="HTML"
        )
    
    try:
        cat_id = int(parts[1])
        name = parts[2]
        price = float(parts[3])
        desc = parts[4]
        
        conn = get_db()
        conn.execute(
            "INSERT INTO products (cat_id, name, price, desc) VALUES (?, ?, ?, ?)",
            (cat_id, name, price, desc)
        )
        conn.commit()
        conn.close()
        
        bot.send_message(message.chat.id, f"✅ Товар '<b>{name}</b>' добавлен в категорию #{cat_id}!")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Цена должна быть числом (например, 150 или 99.99)")

@bot.message_handler(commands=["addkeys"])
def start_add_keys_command(message):
    if not is_admin(message.from_user.id):        return
    
    parts = message.text.split()
    if len(parts) < 2:
        return bot.send_message(
            message.chat.id,
            "❌ Формат: <code>/addkeys ProdID</code>\n"
            "Пример: <code>/addkeys 1</code>",
            parse_mode="HTML"
        )
    
    prod_id = int(parts[1])
    user_states[message.from_user.id] = {"state": "addkeys", "prod_id": prod_id}
    
    bot.send_message(
        message.chat.id,
        "📥 <b>Загрузка ключей</b>\n\n"
        "Отправь список ключей/данных.\n"
        "Каждый ключ с новой строки:\n"
        "<code>KEY-AAAA-BBBB\nKEY-CCCC-DDDD</code>",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda m: isinstance(user_states.get(m.from_user.id), dict) and user_states[m.from_user.id].get("state") == "addkeys")
def save_keys_batch(message):
    state = user_states[message.from_user.id]
    prod_id = state["prod_id"]
    
    # Parse keys: split by newlines, strip whitespace, filter empty
    keys = [k.strip() for k in message.text.split("\n") if k.strip()]
    
    conn = get_db()
    for key in keys:
        conn.execute(
            "INSERT INTO inventory (prod_id, key_data) VALUES (?, ?)",
            (prod_id, key)
        )
    conn.commit()
    conn.close()
    
    bot.send_message(
        message.chat.id,
        f"✅ Загружено <b>{len(keys)}</b> ключей для товара #{prod_id}!"
    )
    user_states.pop(message.from_user.id, None)

@bot.message_handler(commands=["reply"])
def admin_reply_to_ticket(message):
    if not is_admin(message.from_user.id):
        return    
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        return bot.send_message(
            message.chat.id,
            "❌ Формат: <code>/reply UserID Текст_ответа</code>\n"
            "Пример: <code>/reply 123456 Ваш вопрос решён!</code>",
            parse_mode="HTML"
        )
    
    try:
        target_uid = int(parts[1])
        reply_text = parts[2]
        
        bot.send_message(
            target_uid,
            f"📩 <b>Ответ от поддержки:</b>\n{reply_text}"
        )
        bot.send_message(message.chat.id, "✅ Ответ отправлен пользователю.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")

# ================= FLASK KEEP-ALIVE (for Render) =================
app = Flask(__name__)

@app.route("/")
def health_check():
    return "Bot is running"

def run_flask_server():
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ================= GRACEFUL SHUTDOWN =================
def handle_shutdown(signum, frame):
    logger.info("🛑 Получен сигнал остановки. Завершаю работу...")
    # DB connection is auto-closed by context
    import sys
    sys.exit(0)

import signal
signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)

# ================= MAIN ENTRY POINT =================
if __name__ == "__main__":
    logger.info("🚀 Запуск PRO-бота...")
    
    # Initialize database
    init_db()    logger.info("✅ База данных готова.")
    
    # Start Flask server in background thread (for Render keep-alive)
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    logger.info("🌐 Flask-сервер запущен (порт 8080).")
    
    # Start Telegram polling
    logger.info("✅ Polling Telegram API...")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
