import asyncio
import aiosqlite
import time
import secrets
import logging
import aiohttp
import io
import os
from PIL import Image, ImageDraw, ImageFont
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    ReplyKeyboardMarkup, KeyboardButton, 
    LabeledPrice, PreCheckoutQuery, BufferedInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

# ======================== НАСТРОЙКИ ========================
TOKEN = "1780246047:Uvi0GNiae3TqSKX8EfEcQe2jbyLsjw0D3uN"
DB_NAME = "free_stars_bot.db"
TASK_CREATE_FEE = 50
MIN_TASK_REWARD = 35
MAX_TASK_REWARD = 500
MIN_TASK_USERS = 5
GRAM_RATE = 5000
REFERRAL_PERCENT = 2
ADMIN_IDS = {1780245734, 1780243200} 
BOT_USERNAME = "Free_Stars_Bot"

CHANNEL_ID = "@NewsFreeStars"               
CHAT_ID_FOR_LOGS = "@NewsFreeStars"         
SUPPORT_LINK = "https://staticgram.top/FreeStarsSupport" 
NEWS_LINK = "https://staticgram.top/NewsFreeStars"    

logging.basicConfig(level=logging.INFO)

custom_api = TelegramAPIServer.from_base('https://api.staticgram.top')
session = AiohttpSession(api=custom_api)
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()

# ======================== СОСТОЯНИЯ ========================
class UserStates(StatesGroup):
    withdraw_amount = State()
    deposit_stars_amount = State()
    deposit_gram_amount = State()
    task_title = State()
    task_target = State()
    task_reward = State()
    task_count = State()
    task_proof = State()
    promo_redeem = State()
    promo_create_code = State()
    promo_create_reward = State()
    promo_create_limit = State()

class AdminStates(StatesGroup):
    grant_amount = State()
    mass_grant = State()
    broadcast = State()
    
# ======================== БАЗА ДАННЫХ ========================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, balance INTEGER DEFAULT 0, earned INTEGER DEFAULT 0, deposited INTEGER DEFAULT 0, referred_by INTEGER DEFAULT NULL, ref_paid INTEGER DEFAULT 0, is_blocked INTEGER DEFAULT 0, reg_date INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER NOT NULL, title TEXT NOT NULL, target TEXT NOT NULL, reward INTEGER NOT NULL, total INTEGER NOT NULL, approved INTEGER DEFAULT 0, status TEXT DEFAULT 'active', created INTEGER NOT NULL, is_sub BOOLEAN DEFAULT 0);
        CREATE TABLE IF NOT EXISTS applications (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, user_id INTEGER NOT NULL, proof TEXT DEFAULT '', status TEXT DEFAULT 'pending', created INTEGER NOT NULL, UNIQUE(task_id, user_id));
        CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL, amount INTEGER NOT NULL, created INTEGER NOT NULL, details TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS promos (code TEXT PRIMARY KEY, owner INTEGER NOT NULL, reward INTEGER NOT NULL, total INTEGER NOT NULL, used INTEGER DEFAULT 0, created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS promo_users (code TEXT, user_id INTEGER, PRIMARY KEY(code, user_id));
        CREATE TABLE IF NOT EXISTS payments (payload TEXT PRIMARY KEY, user_id INTEGER NOT NULL, amount INTEGER NOT NULL, kind TEXT NOT NULL, status TEXT DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS receipts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, file_id TEXT NOT NULL, action TEXT NOT NULL, amount INTEGER NOT NULL, created INTEGER NOT NULL);
        """)
        await db.commit()

async def get_user(uid):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id=?", (uid,)) as c: 
            user = await c.fetchone()
            if not user:
                await db.execute("INSERT INTO users(user_id, reg_date) VALUES (?, ?)", (uid, int(time.time())))
                await db.commit()
                async with db.execute("SELECT * FROM users WHERE user_id=?", (uid,)) as c2:
                    user = await c2.fetchone()
            return user

async def change_balance(uid, amt, kind="", det=""):
    async with aiosqlite.connect(DB_NAME, timeout=10) as db:
        await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amt, uid))
        if amt > 0 and kind in ("Награда", "Промокод"):
            await db.execute("UPDATE users SET earned=earned+? WHERE user_id=?", (amt, uid))
        if amt > 0 and kind in ("Пополнение", "Пополнение GRAM"):
            await db.execute("UPDATE users SET deposited=deposited+? WHERE user_id=?", (amt, uid))
        await db.execute("INSERT INTO history(user_id, kind, amount, created, details) VALUES (?, ?, ?, ?, ?)", (uid, kind, amt, int(time.time()), det))
        await db.commit()

def btn(text, data): return InlineKeyboardButton(text=text, callback_data=data)
def btn_url(text, url): return InlineKeyboardButton(text=text, url=url)
def inline(rows): return InlineKeyboardMarkup(inline_keyboard=rows)

def main_kb(user_id):
    kb = [
        [KeyboardButton(text="📋 Задания"), KeyboardButton(text="➕ Создать задание")],
        [KeyboardButton(text="📁 Мои задания"), KeyboardButton(text="💰 Мой баланс")],
        [KeyboardButton(text="⭐ Пополнить звёздами"), KeyboardButton(text="💎 Пополнить GRAM")],
        [KeyboardButton(text="🎟 Промокод"), KeyboardButton(text="🎫 Создать промокод")],
        [KeyboardButton(text="🆘 Поддержка"), KeyboardButton(text="📢 Наш канал")]
    ]
    if user_id in ADMIN_IDS: kb.append([KeyboardButton(text="🛠 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

async def check_sub(user_id):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status not in ['left', 'kicked']
    except Exception:
        return True

def sub_kb():
    return inline([
        [btn_url("📢 Подписаться на канал", f"https://t.me/{CHANNEL_ID.replace('@', '')}")],
        [btn("✅ Проверить подписку", "check_sub")]
    ])
    
def create_receipt_image(user_id, amount, credited, kind, action="ПОПОЛНЕНИЕ"):
    img = Image.new('RGB', (600, 800), color='#1a1a1a')
    draw = ImageDraw.Draw(img)
    font_paths = ["/system/fonts/Roboto-Regular.ttf", "/system/fonts/NotoSans-Regular.ttf", "/system/fonts/DroidSans.ttf"]
    font_bold, font_reg = None, None
    for path in font_paths:
        if os.path.exists(path):
            try:
                font_reg = ImageFont.truetype(path, 30)
                bold_path = path.replace("Regular", "Bold")
                if os.path.exists(bold_path): font_bold = ImageFont.truetype(bold_path, 70)
                break
            except: pass
    if not font_reg: font_reg = ImageFont.load_default()
    if not font_bold: font_bold = font_reg

    color = '#ffcc00' if action == "ПОПОЛНЕНИЕ" else '#00ff00'
    draw.rounded_rectangle([40, 100, 560, 700], radius=30, fill='#2b2b2b', outline=color, width=4)
    draw.text((300, 170), f"ЧЕК: {action}", font=font_reg, fill=color, anchor="mm")
    draw.line([(80, 230), (520, 230)], fill='#555555', width=2)
    date_str = time.strftime('%d.%m.%Y %H:%M')
    draw.text((80, 280), "ID:", font=font_reg, fill='#aaaaaa')
    draw.text((80, 350), "Способ:", font=font_reg, fill='#aaaaaa')
    draw.text((80, 420), "Дата:", font=font_reg, fill='#aaaaaa')
    draw.text((520, 280), str(user_id), font=font_reg, fill='white', anchor="ra")
    draw.text((520, 350), kind, font=font_reg, fill='white', anchor="ra")
    draw.text((520, 420), date_str, font=font_reg, fill='white', anchor="ra")
    draw.line([(80, 490), (520, 490)], fill='#555555', width=2)
    if action == "ПОПОЛНЕНИЕ":
        draw.text((300, 540), "Зачислено на баланс:", font=font_reg, fill='#aaaaaa', anchor="mm")
        draw.text((300, 630), f"+{credited} STAR", font=font_bold, fill='#00ff00', anchor="mm")
    else:
        draw.text((300, 540), "Выведено с баланса:", font=font_reg, fill='#aaaaaa', anchor="mm")
        draw.text((300, 630), f"-{credited} STAR", font=font_bold, fill='#ff3333', anchor="mm")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf

@dp.message(CommandStart())
async def cmd_start(msg: types.Message):
    ref = None
    if msg.text and " " in msg.text:
        p = msg.text.split(" ")[1]
        if p.startswith("ref") and p[3:].isdigit(): ref = int(p[3:])
    user = await get_user(msg.from_user.id)
    async with aiosqlite.connect(DB_NAME) as db:
        if user['referred_by'] is None and ref:
            await db.execute("UPDATE users SET referred_by=? WHERE user_id=?", (ref, msg.from_user.id))
            await db.commit()
    if not await check_sub(msg.from_user.id):
        return await msg.answer("🔒 <b>Для использования бота подпишись на наш канал!</b>", parse_mode="HTML", reply_markup=sub_kb())
    await msg.answer(f"👋 <b>Привет, {msg.from_user.first_name}!</b>\n\nДобро пожаловать в <b>Free Stars</b>!", parse_mode="HTML", reply_markup=main_kb(msg.from_user.id))

@dp.message(Command("cancel"))
async def cmd_cancel(msg: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return await msg.answer("🤷‍♂️ Нечего отменять. Вы в главном меню.", reply_markup=main_kb(msg.from_user.id))
    
    await state.clear()
    await msg.answer("❌ <b>Действие отменено.</b>\nВозвращаю в главное меню.", parse_mode="HTML", reply_markup=main_kb(msg.from_user.id))        

@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(cb: types.CallbackQuery):
    if await check_sub(cb.from_user.id):
        await cb.message.delete()
        await cb.message.answer("✅ Подписка подтверждена!", reply_markup=main_kb(cb.from_user.id))
    else:
        await cb.answer("❌ Вы не подписаны!", show_alert=True)

@dp.message(F.text == "🆘 Поддержка")
async def msg_support(msg: types.Message):
    await msg.answer(f"🆘 <b>Поддержка:</b> {SUPPORT_LINK}", parse_mode="HTML")

@dp.message(F.text == "📢 Наш канал")
async def msg_news(msg: types.Message):
    await msg.answer(f"📢 <b>Канал:</b> {NEWS_LINK}", parse_mode="HTML")

@dp.message(F.text == "💰 Мой баланс")
async def msg_balance(msg: types.Message):
    user = await get_user(msg.from_user.id)
    link = f"https://staticgram.top/{BOT_USERNAME}?start=ref{user['user_id']}"
    txt = (f"👤 <b>Профиль</b>\n\n🆔 ID: <code>{user['user_id']}</code>\n"
           f"💵 Доступно: <b>{user['balance']} ⭐</b>\n"
           f"📈 Заработано (с заданий): <b>{user['earned']} ⭐</b>\n"
           f"💳 Пополнено: <b>{user['deposited']} ⭐</b>\n\n"
           f"🔗 Ссылка:\n<code>{link}</code>")
    await msg.answer(txt, parse_mode="HTML", reply_markup=inline([
        [btn("📤 Вывести", "withdraw"), btn("📜 История", "history:0")],
        [btn("🧾 Мои чеки", "receipts:0")]
    ]))

@dp.callback_query(F.data.startswith("history:"))
async def cb_history(cb: types.CallbackQuery):
    page = int(cb.data.split(":")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM history WHERE user_id=? ORDER BY id DESC LIMIT 10 OFFSET ?", (cb.from_user.id, page*10)) as c: rows = await c.fetchall()
    if not rows: return await cb.answer("История пуста.", show_alert=True)
    txt = "📜 <b>История операций</b>\n\n"
    for r in rows:
        date = time.strftime('%d.%m.%Y %H:%M', time.localtime(r['created']))
        sign = "🟢 +" if r['amount'] > 0 else "🔴 "
        txt += f"{sign}<b>{r['amount']} ⭐</b> | {r['kind']}\n<i>{date}</i>\n\n"
    await cb.message.edit_text(txt, parse_mode="HTML")

@dp.callback_query(F.data.startswith("receipts:"))
async def cb_receipts(cb: types.CallbackQuery):
    page = int(cb.data.split(":")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM receipts WHERE user_id=? ORDER BY id DESC LIMIT 5 OFFSET ?", (cb.from_user.id, page*5)) as c: rows = await c.fetchall()
    if not rows: return await cb.answer("Чеков нет.", show_alert=True)
    for r in rows:
        date = time.strftime('%d.%m.%Y %H:%M', time.localtime(r['created']))
        caption = f"🧾 <b>{r['action']}</b>\nСумма: {r['amount']} ⭐\nДата: {date}"
        await cb.message.answer_photo(photo=r['file_id'], caption=caption, parse_mode="HTML")
    await cb.answer()
    
@dp.message(F.text == "⭐ Пополнить звёздами")
async def msg_dep_stars(msg: types.Message):
    kb = inline([
        [btn("100 ⭐", "buy_stars:100"), btn("500 ⭐", "buy_stars:500")], 
        [btn("1000 ⭐", "buy_stars:1000"), btn("5000 ⭐", "buy_stars:5000")],
        [btn("✏️ Другая сумма", "custom_stars")]
    ])
    await msg.answer("⭐ <b>Пополнение звёздами</b>\n\nКомиссия: 10%", parse_mode="HTML", reply_markup=kb)

@dp.message(F.text == "💎 Пополнить GRAM")
async def msg_dep_gram(msg: types.Message):
    kb = inline([
        [btn("1 GRAM", "buy_gram:1"), btn("5 GRAM", "buy_gram:5")],
        [btn("✏️ Другая сумма", "custom_gram")]
    ])
    await msg.answer(f"💎 <b>Пополнение GRAM</b>\n\nКурс: 1 GRAM = {GRAM_RATE} ⭐", parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "custom_stars")
async def cb_custom_stars(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("⭐ <b>Введи сумму:</b>")
    await state.set_state(UserStates.deposit_stars_amount)
    await cb.answer()

@dp.message(UserStates.deposit_stars_amount)
async def process_custom_stars(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit(): return await msg.answer("❌ Целое число.")
    amount = int(msg.text)
    if not (1 <= amount <= 100000): return await msg.answer("❌ От 1 до 100 000.")
    await state.clear()
    await create_stars_invoice(msg.from_user.id, amount, msg)

@dp.callback_query(F.data == "custom_gram")
async def cb_custom_gram(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("💎 <b>Введи сумму GRAM:</b>")
    await state.set_state(UserStates.deposit_gram_amount)
    await cb.answer()

@dp.message(UserStates.deposit_gram_amount)
async def process_custom_gram(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit(): return await msg.answer("❌ Целое число.")
    amount = int(msg.text)
    if not (1 <= amount <= 10000): return await msg.answer("❌ От 1 до 10 000.")
    await state.clear()
    await create_gram_invoice(msg.from_user.id, amount, msg)

@dp.callback_query(F.data.startswith("buy_stars:"))
async def cb_buy_stars(cb: types.CallbackQuery):
    amount = int(cb.data.split(":")[1])
    await create_stars_invoice(cb.from_user.id, amount, cb.message)
    await cb.answer()

async def create_stars_invoice(user_id, amount, message_obj):
    payload = f"stars_{user_id}_{secrets.token_hex(4)}"
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO payments VALUES (?, ?, ?, 'stars', 'pending')", (payload, user_id, amount))
        await db.commit()
    try:
        await bot.send_invoice(chat_id=user_id, title="Пополнение", description=f"На {amount} звёзд", payload=payload, provider_token="", currency="XTR", prices=[LabeledPrice(label="Звёзды", amount=amount)])
    except Exception as e:
        await message_obj.answer(f"❌ Ошибка Stars: {e}")

@dp.callback_query(F.data.startswith("buy_gram:"))
async def cb_buy_gram(cb: types.CallbackQuery):
    amount = int(cb.data.split(":")[1])
    await create_gram_invoice(cb.from_user.id, amount, cb.message)
    await cb.answer()

async def create_gram_invoice(user_id, amount, message_obj):
    payload = f"gram_{user_id}_{secrets.token_hex(4)}"
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO payments VALUES (?, ?, ?, 'gram', 'pending')", (payload, user_id, amount))
        await db.commit()
    url = f"https://api.staticgram.top/bot{TOKEN}/sendGramInvoice"
    data = {"chat_id": user_id, "title": "Пополнение через GRAM", "description": f"На {amount} GRAM.", "payload": payload, "amount": str(amount)}
    try:
        async with aiohttp.ClientSession() as session_http:
            async with session_http.post(url, json=data) as resp:
                result = await resp.json()
                if not result.get("ok"): await message_obj.answer(f"❌ Ошибка: {result.get('description')}")
    except Exception as e:
        await message_obj.answer(f"❌ Ошибка сети: {e}")

@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def success_pay(msg: types.Message):
    await process_successful_payment(msg.successful_payment.invoice_payload)

async def process_successful_payment(payload):
    async with aiosqlite.connect(DB_NAME, timeout=10) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM payments WHERE payload=? AND status='pending'", (payload,)) as c: row = await c.fetchone()
        if not row: return
        uid, amt, kind = row['user_id'], row['amount'], row['kind']
        await db.execute("UPDATE payments SET status='paid' WHERE payload=?", (payload,))
        await db.commit()
    if kind == 'stars':
        credited = int(amt * 0.9)
        await change_balance(uid, credited, "Пополнение", f"Оплачено {amt}")
        method_name, public_amount = "Telegram Stars", f"{amt} ⭐"
    elif kind == 'gram':
        credited = amt * GRAM_RATE
        await change_balance(uid, credited, "Пополнение GRAM", f"GRAM {amt}")
        method_name, public_amount = "GRAM", f"{amt} GRAM"

    try:
        user = await get_user(uid)
        display_name = f"@{user['username']}" if user and user['username'] else f"ID: {uid}"
        channel_text = (f"💳 <b>Новое пополнение!</b>\n👤 {display_name}\n💵 {public_amount}\n💎 {method_name}")
        await bot.send_message(CHAT_ID_FOR_LOGS, channel_text, parse_mode="HTML")
    except: pass

    try:
        img_buf = create_receipt_image(uid, amt, credited, method_name, "ПОПОЛНЕНИЕ")
        photo = BufferedInputFile(img_buf.read(), filename="receipt.png")
        res = await bot.send_photo(uid, photo=photo, caption=f"🧾 <b>Чек об оплате</b>\n💰 Зачислено: <b>+{credited} ⭐</b>", parse_mode="HTML")
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT INTO receipts(user_id, file_id, action, amount, created) VALUES (?, ?, 'ПОПОЛНЕНИЕ', ?, ?)", (uid, res.photo[-1].file_id, credited, int(time.time())))
            await db.commit()
    except: pass

    async with aiosqlite.connect(DB_NAME, timeout=10) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT referred_by, ref_paid FROM users WHERE user_id=?", (uid,)) as c: u = await c.fetchone()
        if u and u['referred_by'] and not u['ref_paid']:
            bonus = int(credited * REFERRAL_PERCENT / 100)
            await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (bonus, u['referred_by']))
            await db.execute("UPDATE users SET ref_paid=1 WHERE user_id=?", (uid,))
            await db.commit()
            await bot.send_message(u['referred_by'], f"🎉 <b>Реферальный бонус!</b>\n+{bonus} ⭐", parse_mode="HTML")
            
@dp.callback_query(F.data == "withdraw")
async def cb_withdraw(cb: types.CallbackQuery, state: FSMContext):
    user = await get_user(cb.from_user.id)
    if user['balance'] <= 0: return await cb.answer("Нет средств.", show_alert=True)
    await cb.message.answer(f"📤 <b>Введи сумму для вывода:</b>\nДоступно: {user['balance']} ⭐", parse_mode="HTML")
    await state.set_state(UserStates.withdraw_amount)
    await cb.answer()

@dp.message(UserStates.withdraw_amount)
async def process_withdraw(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit(): return await msg.answer("❌ Введи целое число.")
    amount = int(msg.text)
    if amount < 1: return await msg.answer("❌ Минимум 1 ⭐.")
    
    user = await get_user(msg.from_user.id)
    if time.time() - user['reg_date'] < 3600:
        return await msg.answer("🛡 <b>Антифрод:</b> Ваш аккаунт слишком новый для вывода.", parse_mode="HTML")

    if user['balance'] < amount: return await msg.answer("❌ Недостаточно средств.")
    
    wait_msg = await msg.answer("⏳ <i>Обрабатываем вывод...</i>", parse_mode="HTML")
    url = f"https://api.staticgram.top/bot{TOKEN}/withdrawStarBalance"
    request_id = f"wd_{msg.from_user.id}_{secrets.token_hex(4)}"
    data = {"user_id": msg.from_user.id, "star_count": amount, "request_id": request_id}
    
    try:
        async with aiohttp.ClientSession() as session_http:
            async with session_http.post(url, json=data) as resp:
                result = await resp.json()
                if result.get("ok"):
                    async with aiosqlite.connect(DB_NAME) as db:
                        await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, msg.from_user.id))
                        await db.execute("INSERT INTO history(user_id, kind, amount, created, details) VALUES (?, 'Вывод', ?, ?, ?)", (msg.from_user.id, -amount, int(time.time()), "Авто-вывод"))
                        await db.commit()
                    await wait_msg.delete()
                    
                    try:
                        full_name = msg.from_user.full_name
                        username = f" (@{msg.from_user.username})" if msg.from_user.username else ""
                        await bot.send_message(CHAT_ID_FOR_LOGS, f"📤 <b>Новый вывод!</b>\n👤 {full_name}{username}\n🆔 ID: <code>{msg.from_user.id}</code>\n💵 Сумма: <b>{amount} ⭐</b>", parse_mode="HTML")
                    except: pass

                    img_buf = create_receipt_image(msg.from_user.id, amount, amount, "Telegram Stars", "ВЫВОД")
                    photo = BufferedInputFile(img_buf.read(), filename="receipt_out.png")
                    res = await msg.answer_photo(photo=photo, caption=f"✅ <b>Успешный вывод!</b>\nСумма: <b>{amount} ⭐</b>", parse_mode="HTML", reply_markup=main_kb(msg.from_user.id))
                    async with aiosqlite.connect(DB_NAME) as db:
                        await db.execute("INSERT INTO receipts(user_id, file_id, action, amount, created) VALUES (?, ?, 'ВЫВОД', ?, ?)", (msg.from_user.id, res.photo[-1].file_id, amount, int(time.time())))
                        await db.commit()
                else:
                    await wait_msg.edit_text(f"❌ <b>Ошибка:</b> {result.get('description')}", parse_mode="HTML")
    except Exception as e:
        await wait_msg.edit_text(f"❌ Ошибка сети: {e}")
    await state.clear()

@dp.message(F.text == "📋 Задания")
async def msg_tasks(msg: types.Message):
    await show_tasks_page(msg.from_user.id, msg, 0)

async def show_tasks_page(user_id, msg_obj, page):
    offset = page * 5
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tasks WHERE status='active' AND owner!=? AND approved < total LIMIT 5 OFFSET ?", (user_id, offset)) as c: tasks = await c.fetchall()
        async with db.execute("SELECT COUNT(*) FROM tasks WHERE status='active' AND owner!=? AND approved < total", (user_id,)) as c: total = (await c.fetchone())[0]

    if not tasks: return await msg_obj.answer("📋 <b>На этой странице заданий нет.</b>", parse_mode="HTML")
    kb = [[btn(f"📌 {t['title']} | {t['reward']} ⭐", f"task_view:{t['id']}")] for t in tasks]
    nav = []
    if page > 0: nav.append(btn("⬅️ Назад", f"tasks_page:{page-1}"))
    if offset + 5 < total: nav.append(btn("Вперед ➡️", f"tasks_page:{page+1}"))
    if nav: kb.append(nav)
    await msg_obj.answer(f"📋 <b>Задания (Стр. {page+1}):</b>", parse_mode="HTML", reply_markup=inline(kb))

@dp.callback_query(F.data.startswith("tasks_page:"))
async def cb_tasks_page(cb: types.CallbackQuery):
    page = int(cb.data.split(":")[1])
    await cb.message.delete()
    await show_tasks_page(cb.from_user.id, cb.message, page)
    await cb.answer()

@dp.callback_query(F.data.startswith("task_view:"))
async def cb_task_view(cb: types.CallbackQuery):
    tid = int(cb.data.split(":")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tasks WHERE id=?", (tid,)) as c: t = await c.fetchone()
    txt = (f"📋 <b>{t['title']}</b>\n\n🔗 <b>Ссылка:</b> {t['target']}\n💸 <b>Награда:</b> {t['reward']} ⭐\n👥 <b>Выполнено:</b> {t['approved']} из {t['total']}")
    kb = [
        [btn("▶️ Выполнить", f"task_apply:{tid}")],
        [btn("⚠️ Пожаловаться", f"task_report:{tid}")]
    ]
    await cb.message.edit_text(txt, parse_mode="HTML", reply_markup=inline(kb))

@dp.callback_query(F.data.startswith("task_report:"))
async def cb_task_report(cb: types.CallbackQuery):
    tid = int(cb.data.split(":")[1])
    for adm in ADMIN_IDS:
        await bot.send_message(adm, f"⚠️ <b>Жалоба на задание #{tid}!</b>\nПользователь: {cb.from_user.id}", parse_mode="HTML")
    await cb.answer("✅ Жалоба отправлена администратору.", show_alert=True)

@dp.callback_query(F.data.startswith("task_apply:"))
async def cb_task_apply(cb: types.CallbackQuery, state: FSMContext):
    tid = int(cb.data.split(":")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tasks WHERE id=?", (tid,)) as c: task = await c.fetchone()

    if task['owner'] == cb.from_user.id:
        return await cb.answer("❌ Нельзя выполнять свои задания.", show_alert=True)

    if task['is_sub'] == 1:
        chat_username = task['target'].split("t.me/")[1]
        try:
            member = await bot.get_chat_member(chat_id=f"@{chat_username}", user_id=cb.from_user.id)
            if member.status in ['left', 'kicked']:
                return await cb.answer("❌ Вы не подписаны на канал!", show_alert=True)
        except:
            return await cb.answer("❌ Ошибка проверки подписки.", show_alert=True)
        
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM applications WHERE task_id=? AND user_id=?", (tid, cb.from_user.id)) as c: existing = await c.fetchone()
            if existing: return await cb.answer("Вы уже выполняли.", show_alert=True)
            await db.execute("INSERT INTO applications(task_id, user_id, proof, status, created) VALUES (?, ?, 'Авто', 'approved', ?)", (tid, cb.from_user.id, int(time.time())))
            await db.execute("UPDATE tasks SET approved=approved+1 WHERE id=?", (tid,))
            await db.commit()
        await change_balance(cb.from_user.id, task['reward'], "Награда", f"Авто-задание #{tid}")
        await cb.message.edit_text("✅ <b>Подписка подтверждена!</b> Награда зачислена.", parse_mode="HTML")
        return

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO applications(task_id, user_id, status, created) VALUES (?, ?, 'pending', ?)", (tid, cb.from_user.id, int(time.time())))
        await db.commit()
    await cb.message.answer("📸 <b>Отправь скриншот:</b>", parse_mode="HTML")
    await state.update_data(tid=tid)
    await state.set_state(UserStates.task_proof)
    await cb.answer()

@dp.message(UserStates.task_proof, F.photo | F.text)
async def process_task_proof(msg: types.Message, state: FSMContext):
    tid = (await state.get_data()).get('tid')
    proof = msg.photo[-1].file_id if msg.photo else msg.text
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("UPDATE applications SET proof=? WHERE task_id=? AND user_id=?", (proof, tid, msg.from_user.id))
        async with db.execute("SELECT owner, title FROM tasks WHERE id=?", (tid,)) as c: task = await c.fetchone()
        await db.commit()
    await state.clear()
    await msg.answer("✅ <b>Отправлено!</b>", parse_mode="HTML", reply_markup=main_kb(msg.from_user.id))
    kb = inline([[btn("👁 Заявки", "my_tasks")]])
    if msg.photo: await bot.send_photo(task['owner'], proof, caption=f"📥 Заявка: {task['title']}", reply_markup=kb)
    else: await bot.send_message(task['owner'], f"📥 Заявка: {task['title']}\n💬 {proof}", reply_markup=kb)
    
@dp.message(F.text == "➕ Создать задание")
async def msg_create_task(msg: types.Message, state: FSMContext):
    await msg.answer("📝 <b>Введи название задания:</b>", parse_mode="HTML")
    await state.set_state(UserStates.task_title)

@dp.message(UserStates.task_title)
async def task_title(msg: types.Message, state: FSMContext):
    await state.update_data(title=msg.text)
    await msg.answer("🔗 <b>Отправь ссылку (t.me/... или staticgram.top/...):</b>", parse_mode="HTML")
    await state.set_state(UserStates.task_target)

@dp.message(UserStates.task_target)
async def task_target(msg: types.Message, state: FSMContext):
    target = msg.text.strip()
    is_sub = 0
    chat_username = None
    if "t.me/" in target:
        chat_username = target.split("t.me/")[1].split("/")[0].split("?")[0]
    elif "staticgram.top/" in target:
        chat_username = target.split("staticgram.top/")[1].split("/")[0].split("?")[0]
    elif target.startswith("@"):
        chat_username = target.replace("@", "").split("/")[0]
        
    if chat_username:
        chat_username = chat_username.strip()
        is_sub = 1
        try:
            await bot.get_chat(f"@{chat_username}")
            target = f"https://t.me/{chat_username}"
        except Exception:
            return await msg.answer("❌ <b>Ошибка:</b> Бот не может найти этот канал. Убедитесь, что он существует и бот в нём админ.", parse_mode="HTML")

    await state.update_data(target=target, is_sub=is_sub)
    await msg.answer(f"⭐ <b>Введи награду</b> ({MIN_TASK_REWARD}-{MAX_TASK_REWARD}):", parse_mode="HTML")
    await state.set_state(UserStates.task_reward)

@dp.message(UserStates.task_reward)
async def task_reward(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit() or not (MIN_TASK_REWARD <= int(msg.text) <= MAX_TASK_REWARD): return await msg.answer("❌ Неверная сумма.")
    await state.update_data(reward=int(msg.text))
    await msg.answer(f"👥 <b>Сколько человек нужно?</b> (мин. {MIN_TASK_USERS})", parse_mode="HTML")
    await state.set_state(UserStates.task_count)

@dp.message(UserStates.task_count)
async def task_count(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit() or int(msg.text) < MIN_TASK_USERS: return await msg.answer("❌ Мало людей.")
    count = int(msg.text)
    data = await state.get_data()
    cost = TASK_CREATE_FEE + data['reward'] * count
    user = await get_user(msg.from_user.id)
    if user['balance'] < cost:
        await state.clear()
        return await msg.answer(f"❌ <b>Недостаточно средств.</b>\nНужно: {cost} ⭐", parse_mode="HTML", reply_markup=main_kb(msg.from_user.id))
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (cost, msg.from_user.id))
        await db.execute("INSERT INTO tasks(owner, title, target, reward, total, status, created, is_sub) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)", (msg.from_user.id, data['title'], data['target'], data['reward'], count, int(time.time()), data.get('is_sub', 0)))
        await db.execute("INSERT INTO history(user_id, kind, amount, created, details) VALUES (?, 'Создание задания', ?, ?, ?)", (msg.from_user.id, -cost, int(time.time()), data['title']))
        await db.commit()
    await state.clear()
    await msg.answer("🎉 <b>Задание опубликовано!</b>", parse_mode="HTML", reply_markup=main_kb(msg.from_user.id))

@dp.message(F.text == "📁 Мои задания")
async def msg_my_tasks(msg: types.Message):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tasks WHERE owner=?", (msg.from_user.id,)) as c: tasks = await c.fetchall()
    if not tasks: return await msg.answer("📁 <b>У тебя пока нет своих заданий.</b>", parse_mode="HTML")
    txt = "📁 <b>Твои задания:</b>\n\n"
    for t in tasks: txt += f"📌 {t['title']}\n👥 {t['approved']}/{t['total']}\n\n"
    await msg.answer(txt, parse_mode="HTML")

@dp.callback_query(F.data == "my_tasks")
async def cb_my_tasks(cb: types.CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT a.*, t.title FROM applications a JOIN tasks t ON t.id=a.task_id WHERE t.owner=? AND a.status='pending'", (cb.from_user.id,)) as c: apps = await c.fetchall()
    if not apps: return await cb.answer("Нет заявок.", show_alert=True)
    for a in apps:
        kb = inline([[btn("✅ Одобрить", f"app_ok:{a['id']}"), btn("❌ Отклонить", f"app_no:{a['id']}")]])
        txt = f"📥 <b>Заявка:</b> {a['title']}\n\n💬 <b>Ответ:</b> {a['proof']}"
        await bot.send_message(cb.from_user.id, txt, parse_mode="HTML", reply_markup=kb)
    await cb.answer()

@dp.callback_query(F.data.startswith("app_ok:"))
async def cb_app_ok(cb: types.CallbackQuery):
    aid = int(cb.data.split(":")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT a.*, t.reward, t.id as tid FROM applications a JOIN tasks t ON t.id=a.task_id WHERE a.id=? AND a.status='pending'", (aid,)) as c: app = await c.fetchone()
        if not app: return await cb.answer("Обработано.", show_alert=True)
        await db.execute("UPDATE applications SET status='approved' WHERE id=?", (aid,))
        await db.execute("UPDATE tasks SET approved=approved+1 WHERE id=?", (app['tid'],))
        await db.commit()
    await change_balance(app['user_id'], app['reward'], "Награда", f"Задание #{app['tid']}")
    await cb.message.edit_text("✅ <b>Заявка одобрена.</b>", parse_mode="HTML")
    await bot.send_message(app['user_id'], f"🎉 <b>Задание одобрено!</b>\nНачислено: <b>+{app['reward']} ⭐</b>", parse_mode="HTML")

@dp.callback_query(F.data.startswith("app_no:"))
async def cb_app_no(cb: types.CallbackQuery):
    aid = int(cb.data.split(":")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT a.*, t.title FROM applications a JOIN tasks t ON t.id=a.task_id WHERE a.id=?", (aid,)) as c: app = await c.fetchone()
        await db.execute("UPDATE applications SET status='rejected' WHERE id=?", (aid,))
        await db.commit()
    await cb.message.edit_text("❌ <b>Заявка отклонена.</b>", parse_mode="HTML")
    await bot.send_message(app['user_id'], f"😔 <b>Заявка отклонена.</b>\nЗадание: {app['title']}", parse_mode="HTML")

# ======================== ПРОМОКОДЫ ========================
@dp.message(F.text == "🎟 Промокод")
async def msg_promo(msg: types.Message, state: FSMContext):
    await msg.answer("🎟 <b>Введи промокод:</b>", parse_mode="HTML")
    await state.set_state(UserStates.promo_redeem)

@dp.message(UserStates.promo_redeem)
async def process_promo(msg: types.Message, state: FSMContext):
    code = msg.text.strip().upper()
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM promos WHERE code=?", (code,)) as c: p = await c.fetchone()
        if not p or p['used'] >= p['total']:
            await state.clear()
            return await msg.answer("❌ <b>Промокод не найден.</b>", parse_mode="HTML")
        async with db.execute("SELECT * FROM promo_users WHERE code=? AND user_id=?", (code, msg.from_user.id)) as c:
            if await c.fetchone():
                await state.clear()
                return await msg.answer("❌ <b>Ты уже активировал его.</b>", parse_mode="HTML")
        await db.execute("INSERT INTO promo_users(code, user_id) VALUES (?, ?)", (code, msg.from_user.id))
        await db.execute("UPDATE promos SET used=used+1 WHERE code=?", (code,))
        await db.commit()
    await change_balance(msg.from_user.id, p['reward'], "Промокод", code)
    await state.clear()
    await msg.answer(f"✅ <b>Активирован!</b>\nНачислено: <b>+{p['reward']} ⭐</b>", parse_mode="HTML", reply_markup=main_kb(msg.from_user.id))

@dp.message(F.text == "🎫 Создать промокод")
async def msg_create_promo(msg: types.Message, state: FSMContext):
    await msg.answer("🎫 <b>Введи название (латиницей):</b>", parse_mode="HTML")
    await state.set_state(UserStates.promo_create_code)

@dp.message(UserStates.promo_create_code)
async def promo_code(msg: types.Message, state: FSMContext):
    code = msg.text.strip().upper()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT code FROM promos WHERE code=?", (code,)) as c:
            if await c.fetchone(): return await msg.answer("❌ Занято.")
    await state.update_data(code=code)
    await msg.answer("⭐ <b>Введи награду:</b>", parse_mode="HTML")
    await state.set_state(UserStates.promo_create_reward)

@dp.message(UserStates.promo_create_reward)
async def promo_reward(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit(): return await msg.answer("❌ Число.")
    await state.update_data(reward=int(msg.text))
    await msg.answer("🔢 <b>Кол-во активаций:</b>", parse_mode="HTML")
    await state.set_state(UserStates.promo_create_limit)

@dp.message(UserStates.promo_create_limit)
async def promo_limit(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit(): return await msg.answer("❌ Число.")
    limit = int(msg.text)
    data = await state.get_data()
    cost = data['reward'] * limit
    user = await get_user(msg.from_user.id)
    if user['balance'] < cost:
        await state.clear()
        return await msg.answer(f"❌ Нужно {cost} ⭐.")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (cost, msg.from_user.id))
        await db.execute("INSERT INTO promos(code, owner, reward, total, created) VALUES (?, ?, ?, ?, ?)", (data['code'], msg.from_user.id, data['reward'], limit, int(time.time())))
        await db.execute("INSERT INTO history(user_id, kind, amount, created, details) VALUES (?, 'Создание промо', ?, ?, ?)", (msg.from_user.id, -cost, int(time.time()), data['code']))
        await db.commit()
    await state.clear()
    await msg.answer(f"✅ <b>{data['code']} создан!</b>", parse_mode="HTML", reply_markup=main_kb(msg.from_user.id))

# ======================== АДМИН-ПАНЕЛЬ ========================
async def show_admin_panel(uid):
    kb = inline([
        [btn("📊 Статистика", "admin_stats")], 
        [btn("👥 Юзеры", "admin_users"), btn("📋 Задания", "admin_tasks")],
        [btn("➕ Выдать", "admin_grant"), btn("🎁 Всем", "admin_mass")], 
        [btn("📢 Рассылка", "admin_broadcast")],
        [btn("🏠 В меню", "admin_home")]
    ])
    await bot.send_message(uid, "🛠 <b>АДМИН-ПАНЕЛЬ</b>", parse_mode="HTML", reply_markup=kb)

@dp.message(F.text == "🛠 Админ-панель")
async def msg_admin_panel(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS: return
    await show_admin_panel(msg.from_user.id)

@dp.callback_query(F.data == "admin_home")
async def cb_admin_home(cb: types.CallbackQuery):
    await cb.message.delete()
    await cb.message.answer("🏠 <b>Главное меню:</b>", parse_mode="HTML", reply_markup=main_kb(cb.from_user.id))

@dp.callback_query(F.data == "admin_panel_back")
async def cb_admin_back(cb: types.CallbackQuery):
    await cb.message.delete()
    await show_admin_panel(cb.from_user.id)

@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(cb: types.CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*), SUM(balance) FROM users") as c: count, bal = await c.fetchone()
        async with db.execute("SELECT SUM(amount) FROM history WHERE kind IN ('Пополнение', 'Пополнение GRAM')") as c: deps = (await c.fetchone())[0] or 0
        async with db.execute("SELECT SUM(amount) FROM history WHERE kind='Вывод'") as c: wds = abs((await c.fetchone())[0] or 0)
        async with db.execute("SELECT COUNT(*) FROM tasks WHERE status='active'") as c: active = (await c.fetchone())[0]
    txt = (f"📊 <b>Статистика</b>\n\n👥 Юзеров: <b>{count}</b>\n💰 Баланс: <b>{bal or 0} ⭐</b>\n\n"
           f"💳 Пополнено: <b>{deps} ⭐</b>\n📤 Выведено: <b>{wds} ⭐</b>\n📋 Заданий: <b>{active}</b>")
    await cb.message.edit_text(txt, parse_mode="HTML", reply_markup=inline([[btn("⬅️ Назад", "admin_panel_back")]]))

@dp.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("📢 <b>Отправь фото (или текст) для рассылки:</b>", parse_mode="HTML")
    await state.set_state(AdminStates.broadcast)
    await cb.answer()

@dp.message(AdminStates.broadcast)
async def process_broadcast(msg: types.Message, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users WHERE is_blocked=0") as c: users = await c.fetchall()
    success, fail = 0, 0
    status_msg = await msg.answer(f"📢 Рассылка для {len(users)} чел...")
    for (uid,) in users:
        try:
            if msg.photo: await bot.send_photo(uid, msg.photo[-1].file_id, caption=msg.caption or "", parse_mode="HTML")
            else: await bot.send_message(uid, msg.text, parse_mode="HTML")
            success += 1
            await asyncio.sleep(0.05)
        except: fail += 1
    await status_msg.edit_text(f"✅ <b>Готово!</b>\nУспешно: {success}\nОшибок: {fail}", parse_mode="HTML")

@dp.callback_query(F.data == "admin_grant")
async def cb_admin_grant(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("➕ Введи ID и сумму (через пробел):")
    await state.set_state(AdminStates.grant_amount)
    await cb.answer()

@dp.message(AdminStates.grant_amount)
async def process_grant(msg: types.Message, state: FSMContext):
    try: uid, amt = map(int, msg.text.split())
    except: return await msg.answer("❌ Формат: ID СУММА")
    await change_balance(uid, amt, "Выдача админом", str(msg.from_user.id))
    await state.clear()
    await msg.answer(f"✅ Выдано {amt} ⭐ юзеру {uid}.", reply_markup=main_kb(msg.from_user.id))

@dp.callback_query(F.data == "admin_mass")
async def cb_admin_mass(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("🎁 Введи сумму для выдачи ВСЕМ:")
    await state.set_state(AdminStates.mass_grant)
    await cb.answer()

@dp.message(AdminStates.mass_grant)
async def process_mass(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit(): return await msg.answer("❌ Число.")
    amt = int(msg.text)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance+? WHERE is_blocked=0", (amt,))
        await db.commit()
    await state.clear()
    await msg.answer(f"✅ Выдано по {amt} ⭐ всем.", reply_markup=main_kb(msg.from_user.id))

async def main():
    await init_db()
    print("Бот успешно запущен! 🚀")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())