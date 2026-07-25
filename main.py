import hashlib
import aiohttp
from aiogram import types, F, Bot, Dispatcher
import asyncio

# Твои данные из партнерки
API_TOKEN = "Zc4X9zu0EMrqbPuLy3tN"
PARTNER_ID = "850173" 

# Токен твоего Telegram-бота (не забудь заменить на свой, если еще не вписан)
BOT_TOKEN = "ТОКЕН_ТВОГО_ТЕЛЕГРАМ_БОТА"
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

async def verify_pocket_option_user(user_id: str) -> bool:
    global PARTNER_ID
    
    # Формируем хэш строго по инструкции API
    raw_hash_string = f"{user_id}:{PARTNER_ID}:{API_TOKEN}"
    api_hash = hashlib.md5(raw_hash_string.encode('utf-8')).hexdigest()
    
    url = f"https://affiliate.pocketoption.com/api/user-info/{user_id}/{PARTNER_ID}/{api_hash}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as response:
                if response.status != 200:
                    return False
                
                data = await response.json()
                print(f"Ответ API для ID {user_id}: {data}")
                
                if not data or "error" in data or data.get("status") == "error":
                    return False
                
                return True
                
    except Exception as e:
        print(f"Ошибка при запросе к API Pocket Option: {e}")
        return False

@dp.message(F.text)
async def handle_user_id_check(message: types.Message):
    user_input = message.text.strip()
    
    if not user_input.isdigit():
        await message.answer("❌ Неверный формат! ID должен состоять только из цифр.")
        return
    
    processing_msg = await message.answer("⏳ Проверяю твой ID в системе...")
    
    is_valid = await verify_pocket_option_user(user_input)
    
    try:
        await processing_msg.delete()
    except:
        pass
    
    if is_valid:
        await message.answer("✅ ID успешно подтвержден! Доступ к боту и сигналам открыт 🎉")
    else:
        await message.answer("❌ Доступ запрещен!\n\nID не найден в партнерской программе или не выполнены условия по депозиту.")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
