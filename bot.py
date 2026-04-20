import os, sys, signal, logging, sqlite3, time, threading
from datetime import datetime
from telebot import TeleBot, types
from flask import Flask

BOT_TOKEN = os.getenv("BOT_TOKEN", "8311930730:AAFguCCuRXlOcGaTK76rZx5NwuompnGYdOw")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6697402034"))
DB_PATH = "shop.db"
RATE_LIMIT_SEC = 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("shop_bot")

class Database:
    _conn = None
    @classmethod
    def get(cls):
        if cls._conn is None:
            cls._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            cls._conn.row_factory = sqlite3.Row
            cls._init_schema()
        return cls._conn
    @classmethod
    def _init_schema(cls):
        c = cls._conn.cursor()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, joined_at TEXT, balance REAL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY, name TEXT, emoji TEXT DEFAULT '📦');
            CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY, cat_id INTEGER, name TEXT, desc TEXT, price REAL, is_active BOOLEAN DEFAULT 1);
            CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, prod_id INTEGER, key_data TEXT, status TEXT DEFAULT 'available');
            CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, prod_id INTEGER, key_sent TEXT, amount REAL, created_at TEXT);
            CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, status TEXT DEFAULT 'open', created_at TEXT);
            CREATE TABLE IF NOT EXISTS ticket_msgs (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER, sender TEXT, text TEXT, created_at TEXT);
        """)
        cls._conn.commit()
    @classmethod
    def execute(cls, query, params=(), fetch=False, fetchall=False):
        conn = cls.get()
        c = conn.cursor()
        c.execute(query, params)
        conn.commit()
        if fetch: return c.fetchone()
        if fetchall: return c.fetchall()
        return c.lastrowid
    @classmethod
    def close(cls):
        if cls._conn: cls._conn.close(); cls._conn = None

user_last_action = {}
def check_rate_limit(user_id):    now = time.time()
    last = user_last_action.get(user_id, 0)
    if now - last < RATE_LIMIT_SEC: return False
    user_last_action[user_id] = now
    return True

bot = TeleBot(BOT_TOKEN, parse_mode="HTML")
user_states = {}
def is_admin(uid): return uid == ADMIN_ID
def bottom_kb(admin=False):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton("🛍 Каталог"), types.KeyboardButton("👤 Профиль"))
    kb.add(types.KeyboardButton("🎫 Поддержка"))
    if admin: kb.add(types.KeyboardButton("📊 Админка"))
    return kb

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    uid, uname = msg.from_user.id, msg.from_user.username
    Database.execute("INSERT OR IGNORE INTO users (id, username, joined_at) VALUES (?,?,?)", (uid, uname, datetime.now().isoformat()))
    user_states.pop(uid, None)
    bot.send_message(uid, "🏠 <b>Главное меню</b>\nВыберите раздел:", reply_markup=bottom_kb(is_admin(uid)))

@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def show_profile(msg):
    if not check_rate_limit(msg.from_user.id): return
    row = Database.execute("SELECT balance, joined_at FROM users WHERE id=?", (msg.from_user.id,), fetch=True)
    if not row: return cmd_start(msg)
    cnt = Database.execute("SELECT count() FROM orders WHERE user_id=?", (msg.from_user.id,), fetch=True)[0]
    txt = f"👤 <b>Личный кабинет</b>\n💳 Баланс: <code>{row['balance']}₽</code>\n📦 Заказов: <code>{cnt}</code>\n📅 Регистрация: {row['joined_at'][:10]}"
    bot.send_message(msg.chat.id, txt, reply_markup=bottom_kb(is_admin(msg.from_user.id)))

@bot.message_handler(func=lambda m: m.text == "🛍 Каталог")
def show_cats(msg):
    cats = Database.execute("SELECT id, name, emoji FROM categories", fetchall=True)
    if not cats: return bot.send_message(msg.chat.id, "⚠️ Каталог пуст", reply_markup=bottom_kb(is_admin(msg.from_user.id)))
    kb = types.InlineKeyboardMarkup(row_width=1)
    for c in cats: kb.add(types.InlineKeyboardButton(f"{c['emoji']} {c['name']}", callback_data=f"cat_{c['id']}"))
    bot.send_message(msg.chat.id, "📂 <b>Категории</b>", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("cat_"))
def show_prods(call):
    cid = int(call.data.split("_")[1])
    prods = Database.execute("SELECT id, name, price FROM products WHERE cat_id=? AND is_active=1", (cid,), fetchall=True)
    if not prods: return bot.answer_callback_query(call.id, "Товаров нет", show_alert=True)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for p in prods:
        stock = Database.execute("SELECT count() FROM inventory WHERE prod_id=? AND status='available'", (p['id'],), fetch=True)[0]
        txt = f"🛒 {p['name']} | {p['price']}₽ ({stock})" if stock > 0 else f"❌ {p['name']} | {p['price']}₽"
        kb.add(types.InlineKeyboardButton(txt, callback_data=f"buy_{p['id']}" if stock > 0 else "noop"))    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_menu"))
    bot.edit_message_text("📦 <b>Товары</b>", call.message.chat.id, call.message.message_id, reply_markup=kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "back_menu")
def back(call):
    bot.delete_message(call.message.chat.id, call.message.message_id)
    cmd_start(call)

@bot.callback_query_handler(func=lambda c: c.data.startswith("buy_"))
def process_buy(call):
    uid, pid = call.from_user.id, int(call.data.split("_")[1])
    if not check_rate_limit(uid): return bot.answer_callback_query(call.id, "Подождите...", show_alert=True)
    prod = Database.execute("SELECT name, price FROM products WHERE id=?", (pid,), fetch=True)
    if not prod: return bot.answer_callback_query(call.id, "Товар удалён", show_alert=True)
    item = Database.execute("SELECT id, key_data FROM inventory WHERE prod_id=? AND status='available' LIMIT 1", (pid,), fetch=True)
    if not item: return bot.answer_callback_query(call.id, "❌ Нет в наличии", show_alert=True)
    Database.execute("UPDATE inventory SET status='sold' WHERE id=?", (item['id'],))
    Database.execute("INSERT INTO orders (user_id, prod_id, key_sent, amount, created_at) VALUES (?,?,?,?,?)", (uid, pid, item['key_data'], prod['price'], datetime.now().isoformat()))
    bot.send_message(uid, f"✅ <b>Успешно!</b>\n📦 {prod['name']}\n💰 -{prod['price']}₽\n🔑 <code>{item['key_data']}</code>\n<i>Сохрани ключ!</i>")
    bot.answer_callback_query(call.id, "Выдано")

@bot.message_handler(func=lambda m: m.text == "🎫 Поддержка")
def open_support(msg):
    user_states[msg.from_user.id] = "support"
    bot.send_message(msg.chat.id, "🎫 <b>Поддержка</b>\nОпишите проблему.\nДля выхода: /start")

@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "support")
def handle_support(msg):
    tid = Database.execute("INSERT INTO tickets (user_id, created_at) VALUES (?,?)", (msg.from_user.id, datetime.now().isoformat()))
    Database.execute("INSERT INTO ticket_msgs (ticket_id, sender, text, created_at) VALUES (?,?,?,?)", (tid, "user", msg.text, datetime.now().isoformat()))
    bot.send_message(ADMIN_ID, f"🎫 <b>Тикет #{tid}</b>\n👤 {msg.from_user.id}\n💬 {msg.text}\n<i>Ответ: /reply {msg.from_user.id} текст</i>")
    bot.send_message(msg.chat.id, "✅ Отправлено. Ожидайте.\n/start для возврата")
    user_states.pop(msg.from_user.id, None)

@bot.message_handler(func=lambda m: m.text == "📊 Админка" and is_admin(m.from_user.id))
def admin_panel(msg):
    u = Database.execute("SELECT count() FROM users", fetch=True)[0]
    s = Database.execute("SELECT count() FROM orders", fetch=True)[0]
    rev = Database.execute("SELECT sum(amount) FROM orders", fetch=True)[0] or 0
    bot.send_message(msg.chat.id, f"📊 <b>Аналитика</b>\n👥 Юзеров: <code>{u}</code>\n💰 Продаж: <code>{s}</code>\n💵 Выручка: <code>{rev}₽</code>\n\n<i>Команды: /addcat, /addprod, /addkeys</i>")

@bot.message_handler(commands=["addcat"])
def add_cat(msg):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split(maxsplit=2)
    if len(p) < 3: return bot.send_message(msg.chat.id, "❌ /addcat Название Эмодзи")
    Database.execute("INSERT INTO categories (name, emoji) VALUES (?,?)", (p[1], p[2]))
    bot.send_message(msg.chat.id, f"✅ Категория '{p[1]}' создана")
@bot.message_handler(commands=["addprod"])
def add_prod(msg):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split(maxsplit=4)
    if len(p) < 5: return bot.send_message(msg.chat.id, "❌ /addprod CatID Название Цена Описание")
    try:
        Database.execute("INSERT INTO products (cat_id, name, price, desc) VALUES (?,?,?,?)", (int(p[1]), p[2], float(p[3]), p[4]))
        bot.send_message(msg.chat.id, f"✅ Товар '{p[2]}' добавлен")
    except ValueError:
        bot.send_message(msg.chat.id, "❌ Цена должна быть числом")

@bot.message_handler(commands=["addkeys"])
def start_add_keys(msg):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split()
    if len(p) < 2: return bot.send_message(msg.chat.id, "❌ /addkeys ProdID")
    user_states[msg.from_user.id] = {"state": "addkeys", "prod_id": int(p[1])}
    bot.send_message(msg.chat.id, "📥 Скинь ключи (каждый с новой строки):")

@bot.message_handler(func=lambda m: isinstance(user_states.get(m.from_user.id), dict) and user_states[m.from_user.id]["state"] == "addkeys")
def save_keys(msg):
    st = user_states[msg.from_user.id]
    keys = [k.strip() for k in msg.text.split("\n") if k.strip()]
    for k in keys: Database.execute("INSERT INTO inventory (prod_id, key_data) VALUES (?,?)", (st["prod_id"], k))
    bot.send_message(msg.chat.id, f"✅ Загружено {len(keys)} ключей")
    user_states.pop(msg.from_user.id, None)

@bot.message_handler(commands=["reply"])
def admin_reply(msg):
    if not is_admin(msg.from_user.id): return
    p = msg.text.split(maxsplit=2)
    if len(p) < 3: return bot.send_message(msg.chat.id, "❌ /reply UserID Текст")
    try:
        bot.send_message(int(p[1]), f"📩 <b>Поддержка:</b>\n{p[2]}")
        bot.send_message(msg.chat.id, "✅ Ответ отправлен")
    except Exception as e:
        bot.send_message(msg.chat.id, f"❌ {e}")

# 🌐 KEEP-ALIVE СЕРВЕР (чтобы Render не спал)
app = Flask(__name__)
@app.route("/")
def alive(): return "Bot is running"
def run_server(): app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

if __name__ == "__main__":
    logger.info("🚀 Запуск PRO-бота на Render...")
    Database.get()
    threading.Thread(target=run_server, daemon=True).start()
    logger.info("✅ База готова. Polling...")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)