import os
import sys
import logging
import sqlite3
import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# ==========================================
# 1. НАСТРОЙКИ (ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ИЛИ ДЕФОЛТНЫЕ)
# ==========================================
TOKEN = os.getenv("TOKEN", "7891234567:AAFxExampleTokenForAdminNotification123")
ADMIN_ID = int(os.getenv("ADMIN_ID", "987654321"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("TradingApp")

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI(title="TEAM MASTER VIP Security Core", version="10.8")

# ==========================================
# 2. БАЗА ДАННЫХ (SQLite)
# ==========================================
DB_FILE = "vip_terminal.db"

def init_db():
    logger.info("Инициализация локальной базы данных SQLite...")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            pocket_id TEXT,
            is_verified BOOLEAN DEFAULT 0,
            is_banned BOOLEAN DEFAULT 0,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

def db_exec(query, params=(), fetchone=False, fetchall=False):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(query, params)
    res = None
    if fetchone:
        res = cursor.fetchone()
    elif fetchall:
        res = cursor.fetchall()
    else:
        conn.commit()
    conn.close()
    return res

# ==========================================
# 3. PYDANTIC МОДЕЛИ ДАННЫХ
# ==========================================
class VerifyPayload(BaseModel):
    pocket_id: str
    telegram_id: int = None

# ==========================================
# 4. API МАРШРУТЫ (FASTAPI)
# ==========================================
@app.post("/api/v1/verify")
async def api_verify(payload: VerifyPayload, request: Request):
    pocket_id = payload.pocket_id
    client_ip = request.client.host

    if not pocket_id:
        raise HTTPException(status_code=400, detail="Не указан Pocket Option ID")

    # Ищем пользователя в БД по pocket_id
    user_row = db_exec("SELECT telegram_id, is_banned FROM users WHERE pocket_id = ?", (pocket_id,), fetchone=True)
    
    if user_row and user_row[1] == 1:
        raise HTTPException(status_code=403, detail="Доступ заблокирован администратором.")

    # Сохраняем или обновляем запись
    db_exec(
        "INSERT OR REPLACE INTO users (pocket_id, is_verified, is_banned) VALUES (?, 1, 0)",
        (pocket_id,)
    )

    # Отправляем уведомление администратору в Telegram с кнопками управления
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"block_{pocket_id}")],
        [InlineKeyboardButton(text="✅ Разблокировать", callback_data=f"unblock_{pocket_id}")]
    ])
    
    try:
        await bot.send_message(
            ADMIN_ID,
            f"🚨 *НОВЫЙ ПОЛЬЗОВАТЕЛЬ ПРОШЕЛ ВЕРИФИКАЦИЮ!*\n\n"
            f"👤 *ID Pocket Option:* `{pocket_id}`\n"
            f"🌐 *IP Адрес:* `{client_ip}`\n"
            f"🛡️ *Статус:* Доступ открыт",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление администратору: {e}")

    return {"status": "success", "message": "Верификация успешно пройдена"}

@app.get("/api/v1/status/{pocket_id}")
async def api_status(pocket_id: str):
    row = db_exec("SELECT is_verified, is_banned FROM users WHERE pocket_id = ?", (pocket_id,), fetchone=True)
    if not row:
        return {"access": False, "banned": False}
    return {"access": bool(row[0]), "banned": bool(row[1])}

# Отдача фронтенда (index.html должен лежать в той же папке)
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>404: Файл index.html не найден на сервере</h1>"

# ==========================================
# 5. TELEGRAM БОТ И КОЛЛБЭКИ АДМИНА
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👑 *TEAM MASTER VIP Terminal Bot*\n\n"
        "Бот запущен и обслуживает систему авторизации веб-терминала.",
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("block_") | F.data.startswith("unblock_"))
async def handle_admin_action(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ У вас нет прав администратора.", show_alert=True)
        return

    action, pocket_id = callback.data.split("_")
    is_banned = 1 if action == "block" else 0

    db_exec("UPDATE users SET is_banned = ? WHERE pocket_id = ?", (is_banned, pocket_id))

    status_text = "ЗАБЛОКИРОВАН 🔴" if is_banned == 1 else "РАЗБЛОКИРОВАН 🟢"
    
    # Меняем инлайн-кнопки местами / обновляем интерфейс у админа
    opposite_action = "unblock" if is_banned == 1 else "block"
    opposite_label = "✅ Разблокировать" if is_banned == 1 else "🚫 Заблокировать"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=opposite_label, callback_data=f"{opposite_action}_{pocket_id}")]
    ])
    
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception:
        pass

    await callback.answer(f"Статус для ID {pocket_id} изменен: {status_text}")

async def start_telegram_polling():
    logger.info("Запуск фонового пуллинга Telegram бота...")
    await dp.start_polling(bot, skip_updates=True)

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(start_telegram_polling())

# ==========================================
# 6. ЗАПУСК СЕРВЕРА
# ==========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
