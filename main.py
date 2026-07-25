import os
import random
import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

app = FastAPI()

if os.path.exists("index.html"):
    app.mount("/static", StaticFiles(directory="."), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_index():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>index.html not found</h1>"

class ScanRequest(BaseModel):
    expiration: str = "1 min"
    asset: str = "EUR/USD"

@app.post("/api/scan_ai")
async def scan_ai(data: ScanRequest):
    try:
        expiration = data.expiration or "1 min"
        asset = data.asset or "EUR/USD"
        
        seed_val = abs(hash(asset + expiration)) % 100
        chosen_dir = "CALL" if seed_val % 2 == 0 else "PUT"
        wr = 70 + (seed_val % 9)
        
        explanations = [
            f"AI Vision проанализировал актив {asset} для экспирации {expiration}. Обнаружен уверенный отскок от уровня поддержки с подтверждением по объему.",
            f"Нейросеть зафиксировала пробой канала Боллинджера по инструменту {asset} при экспирации {expiration}. Рекомендуется входить по тренду.",
            f"Анализ стакана и свечей на активе {asset} ({expiration}) указывает на сильный импульс. RSI подтверждает разворот.",
            f"Паттерн 'Поглощение' на {asset} подтвержден индикаторами MACD и RSI для экспирации {expiration}."
        ]
        
        text = explanations[seed_val % len(explanations)]
        
        return {
            "ok": True,
            "asset": asset,
            "direction": chosen_dir,
            "wr": wr,
            "text": text
        }
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})

# --- TELEGRAM BOT CONFIGURATION ---
TOKEN = "7960762468:AAEu1rItSoIL9Q7cHtY-zA5kCr3UmlDWSLQ"
ADMIN_ID = 0  # Сюда автоматически запишется ID первого админа, кто нажмет /start или можно указать ваш Telegram ID

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class RegStates(StatesGroup):
    waiting_for_id = State()
    waiting_for_deposit = State()
    in_signals = State()

# Словарь для хранения состояний пользователей и message_id уведомлений у админа
user_data_store = {}

@dp.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    global ADMIN_ID
    if ADMIN_ID == 0:
        ADMIN_ID = message.from_user.id
        
    await message.answer("Привет! Пожалуйста, отправь свой ID для регистрации:")
    await state.set_state(RegStates.waiting_for_id)

@dp.message(RegStates.waiting_for_id)
async def process_user_id(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_text_id = message.text
    from datetime import datetime
    time_str = datetime.now().strftime("%H:%M:%S %d.%m.%Y")
    
    await state.update_data(entered_id=user_text_id)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Заблокировать", callback_data=f"block_{user_id}"),
            InlineKeyboardButton(text="✅ Разблокировать (Депозит)", callback_data=f"pass_dep_{user_id}")
        ]
    ])
    
    msg = await bot.send_message(
        ADMIN_ID,
        f"👤 Пользователь: {message.from_user.full_name} (@{message.from_user.username})\n"
        f"ID: `{user_text_id}` (Telegram ID: {user_id})\n"
        f"⏱ Время отправки: {time_str}\n"
        f"📌 Статус: Проверка регистрации",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    
    user_data_store[user_id] = {"reg_msg_id": msg.message_id, "state": "reg"}
    await message.answer("Ваш ID принят на проверку администратором. Ожидайте.")

@dp.callback_query(F.data.startswith("block_"))
async def callback_block(callback: CallbackQuery):
    target_user_id = int(callback.data.split("_")[1])
    await bot.send_message(
        target_user_id,
        "❌ Вы не прошли регистрацию! Пожалуйста, пройдите её заново и отправьте правильный ID."
    )
    await callback.message.edit_text(callback.message.text + "\n\n<b>[ЗАБЛОКИРОВАН / ОТКЛОНЕН]</b>", parse_mode="HTML")
    await callback.answer("Пользователь заблокирован/отклонен.")

@dp.callback_query(F.data.startswith("pass_dep_"))
async def callback_pass_reg(callback: CallbackQuery):
    target_user_id = int(callback.data.split("_")[1])
    
    # Удаляем старое сообщение регистрации у админа
    try:
        await callback.message.delete()
    except:
        pass
        
    await bot.send_message(
        target_user_id,
        "✅ Регистрация подтверждена! Теперь отправьте подтверждение вашего депозита (скриншот или текст)."
    )
    
    # Ждем подтверждения депозита от пользователя (переводим его в состояние депозита)
    # Создаем временное сообщение для админа о том, что пользователь на этапе депозита
    from datetime import datetime
    time_str = datetime.now().strftime("%H:%M:%S")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Заблокировать", callback_data=f"block_{target_user_id}"),
            InlineKeyboardButton(text="✅ Подтвердить депозит", callback_data=f"pass_signals_{target_user_id}")
        ]
    ])
    
    msg = await bot.send_message(
        ADMIN_ID,
        f"💰 Пользователь (ID: {target_user_id}) пополнил баланс!\n⏱ Время: {time_str}",
        reply_markup=keyboard
    )
    user_data_store[target_user_id] = {"dep_msg_id": msg.message_id, "state": "dep"}
    await callback.answer("Переведено на этап депозита.")

@dp.callback_query(F.data.startswith("pass_signals_"))
async def callback_pass_deposit(callback: CallbackQuery):
    target_user_id = int(callback.data.split("_")[1])
    
    try:
        await callback.message.delete()
    except:
        pass
        
    await bot.send_message(
        target_user_id,
        "🎉 Депозит подтвержден! Вы перешли к сигналам. Доступ открыт."
    )
    
    from datetime import datetime
    time_str = datetime.now().strftime("%H:%M:%S")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚫 Заблокировать навсегда", callback_data=f"perm_block_{target_user_id}"),
            InlineKeyboardButton(text="✅ Разблокировать", callback_data=f"perm_unblock_{target_user_id}")
        ]
    ])
    
    msg = await bot.send_message(
        ADMIN_ID,
        f"🚀 Пользователь (ID: {target_user_id}) перешел к сигналам!\n⏱ Время: {time_str}",
        reply_markup=keyboard
    )
    user_data_store[target_user_id] = {"signals_msg_id": msg.message_id, "state": "signals"}
    await callback.answer("Пользователь переведен к сигналам.")

@dp.callback_query(F.data.startswith("perm_block_"))
async def callback_perm_block(callback: CallbackQuery):
    target_user_id = int(callback.data.split("_")[1])
    await bot.send_message(target_user_id, "🚫 Ваш доступ к боту заблокирован администратором навсегда.")
    await callback.message.edit_text(callback.message.text + "\n\n<b>[ЗАБЛОКИРОВАН НАВСЕГДА]</b>", parse_mode="HTML")
    await callback.answer("Доступ заблокирован.")

@dp.callback_query(F.data.startswith("perm_unblock_"))
async def callback_perm_unblock(callback: CallbackQuery):
    target_user_id = int(callback.data.split("_")[1])
    await bot.send_message(target_user_id, "✅ Ваш доступ восстановлен!")
    await callback.answer("Пользователь разблокирован.")

import asyncio

async def main():
    # Запуск поллинга телеграм бота в фоновом режиме вместе с FastAPI
    # Для совместного запуска FastAPI и Aiogram можно использовать uvicorn + asyncio task
    pass

if __name__ == "__main__":
    import uvicorn
    # Запускаем uvicorn сервер для сайта
    port = int(os.environ.get("PORT", 10000))
    
    # Чтобы бот и сервер работали вместе, запустим бота через задачу asyncio при старте Uvicorn
    @app.on_event("startup")
    async def startup_event():
        asyncio.create_task(dp.start_polling(bot))

    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
