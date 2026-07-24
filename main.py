import os
import logging
import asyncio
from threading import Thread
from flask import Flask
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery

TOKEN = os.getenv("TOKEN", "7960762468:AAEu1rItSoIL9Q7cHtY-zA5kCr3UmlDWSLQ")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

users_status = {}

# --- Flask server для обхода ошибки портов на Render ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
# ------------------------------------------------------

def get_ban_keyboard(user_id: int, is_banned: bool) -> InlineKeyboardMarkup:
    text = "🟢 Разблокировать" if is_banned else "🔴 Заблокировать"
    callback_data = f"unban_{user_id}" if is_banned else f"ban_{user_id}"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=callback_data)]])

@dp.message(Command("notify"))
async def notify_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("Формат: /notify <user_id> <UID>")
        return
    
    target_user_id = int(args[1])
    uid = args[2]
    
    users_status[target_user_id] = False
    
    await bot.send_message(
        ADMIN_ID,
        f"🚨 Ученик прошел верификацию на сайте!\n\n🆔 ID: `{target_user_id}`\n🔗 UID: `{uid}`",
        reply_markup=get_ban_keyboard(target_user_id, is_banned=False),
        parse_mode="Markdown"
    )
    await message.answer("Уведомление отправлено.")

@dp.callback_query(F.data.startswith("ban_") | F.data.startswith("unban_"))
async def toggle_ban(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет прав", show_alert=True)
        return

    action, user_id_str = callback.data.split("_")
    target_user_id = int(user_id_str)
    is_banned = (action == "ban")
    
    users_status[target_user_id] = is_banned
    
    await callback.message.edit_reply_markup(reply_markup=get_ban_keyboard(target_user_id, is_banned))
    await callback.answer("Статус обновлен")

async def main():
    # Запускаем Flask в отдельном потоке, чтобы занимать порт для Render
    Thread(target=run_flask).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
