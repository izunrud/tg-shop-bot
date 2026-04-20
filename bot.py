import os, logging, sqlite3, time, threading
from datetime import datetime
from telebot import TeleBot, types
from flask import Flask

BOT_TOKEN = os.getenv("BOT_TOKEN", "8311930730:AAFguCCuRXlOcGaTK76rZx5NwuompnGYdOw")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6697402034"))
DB = "shop.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bot")

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, joined_at TEXT, balance REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY, name TEXT, emoji TEXT DEFAULT '📦');
        CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY, cat_id INTEGER, name TEXT, desc TEXT, price REAL, is_active BOOLEAN DEFAULT 1);
        CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, prod_id INTEGER, key_data TEXT, status TEXT DEFAULT 'available');
        CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, prod_id INTEGER, key_sent TEXT, amount REAL, created_at TEXT);
        CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, status TEXT DEFAULT 'open', created_at TEXT);
        CREATE TABLE IF NOT EXISTS ticket_msgs (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER, sender TEXT, text TEXT, created_at TEXT);
    """)
    conn.commit()
    conn.close()

bot = TeleBot(BOT_TOKEN, parse_mode="HTML")
states = {}

def main_kb(admin=False):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton("🛍 Каталог"), types.KeyboardButton("👤 Профиль"))
    kb.add(types.KeyboardButton("🎫 Поддержка"))
    if admin:
        kb.add(types.KeyboardButton("📊 Админка"))
    return kb

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    states.pop(msg.from_user.id, None)
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO users (id, username, joined_at) VALUES (?, ?, ?)",
                 (msg.from_user.id, msg.from_user.username, datetime.now().isoformat()))
    conn.commit()
    conn.close()    bot.send_message(msg.chat.id, "🏠 <b>Главное меню</b>\nВыберите раздел:",
                     reply_markup=main_kb(msg.from_user.id == ADMIN_ID))

@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def show_profile(msg):
    conn = get_db()
    row = conn.execute("SELECT balance, joined_at FROM users WHERE id=?", (msg.from_user.id,)).fetchone()
    conn.close()
    if not row:
        return cmd_start(msg)
    conn = get_db()
    cnt = conn.execute("SELECT count() FROM orders WHERE user_id=?", (msg.from_user.id,)).fetchone()[0]
    conn.close()
    txt = f"👤 <b>Профиль</b>\n💳 Баланс: <code>{row['balance']}₽</code>\n📦 Заказов: <code>{cnt}</code>\n📅 {row['joined_at'][:10]}"
    bot.send_message(msg.chat.id, txt, reply_markup=main_kb(msg.from_user.id == ADMIN_ID))

@bot.message_handler(func=lambda m: m.text == "🛍 Каталог")
def show_categories(msg):
    conn = get_db()
    cats = conn.execute("SELECT id, name, emoji FROM categories").fetchall()
    conn.close()
    if not cats:
        return bot.send_message(msg.chat.id, "⚠️ Каталог пуст", reply_markup=main_kb(msg.from_user.id == ADMIN_ID))
    kb = types.InlineKeyboardMarkup(row_width=1)
    for c in cats:
        kb.add(types.InlineKeyboardButton(f"{c['emoji']} {c['name']}", callback_data=f"cat_{c['id']}"))
    bot.send_message(msg.chat.id, "📂 <b>Категории</b>", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("cat_"))
def show_products(call):
    cid = int(call.data.split("_")[1])
    conn = get_db()
    prods = conn.execute("SELECT id, name, price FROM products WHERE cat_id=? AND is_active=1", (cid,)).fetchall()
    conn.close()
    if not prods:
        return bot.answer_callback_query(call.id, "Товаров нет", show_alert=True)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for p in prods:
        conn = get_db()
        stock = conn.execute("SELECT count() FROM inventory WHERE prod_id=? AND status='available'", (p['id'],)).fetchone()[0]
        conn.close()
        if stock > 0:
            kb.add(types.InlineKeyboardButton(f"🛒 {p['name']} | {p['price']}₽ ({stock})", callback_data=f"buy_{p['id']}"))
        else:
            kb.add(types.InlineKeyboardButton(f"❌ {p['name']} | {p['price']}₽", callback_data="noop"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_menu"))
    bot.edit_message_text("📦 <b>Товары</b>", call.message.chat.id, call.message.message_id, reply_markup=kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "back_menu")def back_menu(call):
    bot.delete_message(call.message.chat.id, call.message.message_id)
    cmd_start(call)

@bot.callback_query_handler(func=lambda c: c.data.startswith("buy_"))
def process_buy(call):
    uid = call.from_user.id
    pid = int(call.data.split("_")[1])
    conn = get_db()
    prod = conn.execute("SELECT name, price FROM products WHERE id=?", (pid,)).fetchone()
    if not prod:
        conn.close()
        return bot.answer_callback_query(call.id, "Товар удалён", show_alert=True)
    item = conn.execute("SELECT id, key_data FROM inventory WHERE prod_id=? AND status='available' LIMIT 1", (pid,)).fetchone()
    if not item:
        conn.close()
        return bot.answer_callback_query(call.id, "❌ Нет в наличии", show_alert=True)
    conn.execute("UPDATE inventory SET status='sold' WHERE id=?", (item['id'],))
    conn.execute("INSERT INTO orders (user_id, prod_id, key_sent, amount, created_at) VALUES (?, ?, ?, ?, ?)",
                 (uid, pid, item['key_data'], prod['price'], datetime.now().isoformat()))
    conn.commit()
    conn.close()
    bot.send_message(uid, f"✅ <b>Успешно!</b>\n📦 {prod['name']}\n💰 -{prod['price']}₽\n🔑 <code>{item['key_data']}</code>\n<i>Сохрани ключ!</i>")
    bot.answer_callback_query(call.id, "Выдано")

@bot.message_handler(func=lambda m: m.text == "🎫 Поддержка")
def open_support(msg):
    states[msg.from_user.id] = "support"
    bot.send_message(msg.chat.id, "🎫 <b>Поддержка</b>\nОпишите проблему.\nДля выхода: /start")

@bot.message_handler(func=lambda m: states.get(m.from_user.id) == "support")
def handle_support(msg):
    conn = get_db()
    tid = conn.execute("INSERT INTO tickets (user_id, created_at) VALUES (?,?)",
                       (msg.from_user.id, datetime.now().isoformat())).lastrowid
    conn.execute("INSERT INTO ticket_msgs (ticket_id, sender, text, created_at) VALUES (?,?,?,?)",
                 (tid, "user", msg.text, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    bot.send_message(ADMIN_ID, f"🎫 <b>Тикет #{tid}</b>\n👤 {msg.from_user.id}\n💬 {msg.text}\n<i>Ответ: /reply {msg.from_user.id} текст</i>")
    bot.send_message(msg.chat.id, "✅ Отправлено. Ожидайте.\n/start для возврата")
    states.pop(msg.from_user.id, None)

@bot.message_handler(func=lambda m: m.text == "📊 Админка" and m.from_user.id == ADMIN_ID)
def admin_panel(msg):
    conn = get_db()
    u = conn.execute("SELECT count() FROM users").fetchone()[0]
    s = conn.execute("SELECT count() FROM orders").fetchone()[0]
    r = conn.execute("SELECT sum(amount) FROM orders").fetchone()[0] or 0
    conn.close()    bot.send_message(msg.chat.id, f"📊 <b>Аналитика</b>\n👥 Юзеров: <code>{u}</code>\n💰 Продаж: <code>{s}</code>\n💵 Выручка: <code>{r}₽</code>\n\n<i>Команды: /addcat, /addprod, /addkeys</i>")

@bot.message_handler(commands=["addcat"])
def add_category(msg):
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        return bot.send_message(msg.chat.id, "❌ /addcat Название Эмодзи")
    conn = get_db()
    conn.execute("INSERT INTO categories (name, emoji) VALUES (?,?)", (parts[1], parts[2]))
    conn.commit()
    conn.close()
    bot.send_message(msg.chat.id, f"✅ Категория '{parts[1]}' создана")

@bot.message_handler(commands=["addprod"])
def add_product(msg):
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.split(maxsplit=4)
    if len(parts) < 5:
        return bot.send_message(msg.chat.id, "❌ /addprod CatID Название Цена Описание")
    try:
        conn = get_db()
        conn.execute("INSERT INTO products (cat_id, name, price, desc) VALUES (?,?,?,?)",
                     (int(parts[1]), parts[2], float(parts[3]), parts[4]))
        conn.commit()
        conn.close()
        bot.send_message(msg.chat.id, f"✅ Товар '{parts[2]}' добавлен")
    except ValueError:
        bot.send_message(msg.chat.id, "❌ Цена должна быть числом")

@bot.message_handler(commands=["addkeys"])
def start_add_keys(msg):
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.split()
    if len(parts) < 2:
        return bot.send_message(msg.chat.id, "❌ /addkeys ProdID")
    states[msg.from_user.id] = {"prod_id": int(parts[1])}
    bot.send_message(msg.chat.id, "📥 Скинь ключи (каждый с новой строки):")

@bot.message_handler(func=lambda m: isinstance(states.get(m.from_user.id), dict) and "prod_id" in states[m.from_user.id])
def save_keys(msg):
    data = states[msg.from_user.id]
    keys = [k.strip() for k in msg.text.split("\n") if k.strip()]
    conn = get_db()
    for k in keys:
        conn.execute("INSERT INTO inventory (prod_id, key_data) VALUES (?,?)", (data["prod_id"], k))
    conn.commit()    conn.close()
    bot.send_message(msg.chat.id, f"✅ Загружено {len(keys)} ключей")
    states.pop(msg.from_user.id, None)

@bot.message_handler(commands=["reply"])
def admin_reply(msg):
    if msg.from_user.id != ADMIN_ID:
        return
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        return bot.send_message(msg.chat.id, "❌ /reply UserID Текст")
    try:
        bot.send_message(int(parts[1]), f"📩 <b>Поддержка:</b>\n{parts[2]}")
        bot.send_message(msg.chat.id, "✅ Ответ отправлен")
    except Exception as e:
        bot.send_message(msg.chat.id, f"❌ {e}")

app = Flask(__name__)

@app.route("/")
def keep_alive():
    return "Bot is running"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

if __name__ == "__main__":
    logger.info("🚀 Запуск бота...")
    init_db()
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("✅ Polling...")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
