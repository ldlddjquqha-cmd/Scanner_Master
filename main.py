import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message

# Замените на токен вашего бота
TOKEN = "YOUR_BOT_TOKEN_HERE"

# Инициализация диспетчера и бота
dp = Dispatcher()


@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
  """Этот хендлер срабатывает на команду /start."""
  user_name = message.from_user.first_name if message.from_user else "Пользователь"
  await message.answer(
      f"Привет, {html.bold(html.quote(user_name))}! Бот успешно запущен и готов к работе."
  )


async def main() -> None:
  # Инициализация бота с настройками по умолчанию (HTML-разметка)
  bot = Bot(
      token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)
  )

  # Запуск поллинга обновлений
  await dp.start_polling(bot)


if __name__ == "__main__":
  logging.basicConfig(level=logging.INFO, stream=sys.stdout)
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    print("Бот остановлен.")
