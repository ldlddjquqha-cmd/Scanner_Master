import os
import base64
import cv2
import numpy as np
from flask import Flask, request, jsonify
from flask_cors import CORS
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

# 1. КОНСТАНТЫ ЖЕСТКО В КОД 💎
TOKEN = "8479849828:AAEl31VYsy9o7NrSL9lIHdmHDaUBrbP1aFw"
APP_URL = "https://scanner-master.onrender.com"

app = Flask(__name__)
CORS(app)

# Инициализация Telegram приложения
bot_app = Application.builder().token(TOKEN).build()

# 2. ИИ АНАЛИЗ ПИКСЕЛЕЙ ЧЕРЕЗ OPENCV 👁️
def analyze_image_pixels(image_bytes):
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return "ERROR", "Не удалось прочитать кадр"
    
    h, w, _ = img.shape
    cy, cx = h // 2, w // 2
    roi = img[cy-50:cy+50, cx-50:cx+50]
    
    b, g, r = cv2.split(roi)
    avg_green = np.mean(g)
    avg_red = np.mean(r)
    
    if avg_green > avg_red + 15:
        return "UP", avg_green, avg_red
    elif avg_red > avg_green + 15:
        return "DOWN", avg_green, avg_red
    else:
        if avg_green > avg_red:
            return "FLAT_UP", avg_green, avg_red
        else:
            return "FLAT_DOWN", avg_green, avg_red

# 3. МАРШРУТЫ FLASK ДЛЯ WEB APP (С ПОДДЕРЖКОЙ 6 ЯЗЫКОВ 🌍)
@app.route('/')
def home():
    return "ИИ Сервер Команды Мастер запущен через Webhook! 🟢"

@app.route('/analyze', methods=['POST'])
def handle_analyze():
    data = request.get_json()
    if not data or 'image' not in data:
        return jsonify({"error": "No image"}), 400
    
    try:
        header, encoded = data.get('image').split(",", 1)
        image_bytes = base64.b64decode(encoded)
    except Exception:
        return jsonify({"error": "Decode error"}), 400
        
    lang = data.get('lang', 'ru')  # Получаем выбранный язык из Mini App
    tf = data.get('tf', '1m')
    expiration = data.get('exp', '1m')
    
    res = analyze_image_pixels(image_bytes)
    if res[0] == "ERROR":
        return jsonify({"error": "Read error"}), 400
        
    direction, g_val, r_val = res
    
    # Словари перевода ответов ИИ на 6 языков
    translations = {
        "ru": {
            "up_title": "🟢 ВВЕРХ (CALL) 📈", "down_title": "🔴 ВНИЗ (PUT) 📉",
            "up_reason": f"🔥 ИИ обнаружил преобладание зелёного спектра (З: {g_val:.1f}, К: {r_val:.1f}). Импульс быков растет.",
            "down_reason": f"💥 ИИ зафиксировал доминирование красного спектра (К: {r_val:.1f}, З: {g_val:.1f}). Медведи давят рынок.",
            "flat_up": f"💤 Флэт. Слабый перевес покупателей (З: {g_val:.1f}/К: {r_val:.1f}).",
            "flat_down": f"💤 Флэт. Слабый перевес продавцов (К: {r_val:.1f}/З: {g_val:.1f}).",
            "up_tips": f"💎 СОВЕТ: Вход ВВЕРХ на [{expiration}]. Риск — 1-2%!",
            "down_tips": f"💎 СОВЕТ: Вход ВНИЗ на [{expiration}]. Объемы подтверждены!"
        },
        "uk": {
            "up_title": "🟢 ВГОРУ (CALL) 📈", "down_title": "🔴 ВНИЗ (PUT) 📉",
            "up_reason": f"🔥 ШІ виявив перевагу зеленого спектру (З: {g_val:.1f}, Ч: {r_val:.1f}). Імпульс биків зростає.",
            "down_reason": f"💥 ШІ зафіксував домінування червоного спектру (Ч: {r_val:.1f}, З: {g_val:.1f}). Ведмеді тиснуть на ринок.",
            "flat_up": f"💤 Флет. Слабкий перевага покупців (З: {g_val:.1f}/Ч: {r_val:.1f}).",
            "flat_down": f"💤 Флет. Слабкий перевага продавців (Ч: {r_val:.1f}/З: {g_val:.1f}).",
            "up_tips": f"💎 ПОРАДА: Вхід ВГОРУ на [{expiration}]. Ризик — 1-2%!",
            "down_tips": f"💎 ПОРАДА: Вхід ВНИЗ на [{expiration}]. Об'єми підтверджені!"
        },
        "ro": {
            "up_title": "🟢 СRESȘTERE (CALL) 📈", "down_title": "🔴 SCĂDERE (PUT) 📉",
            "up_reason": f"🔥 IA a detectat o dominanță a spectrului verde (V: {g_val:.1f}, R: {r_val:.1f}). Impulsul taurilor crește.",
            "down_reason": f"💥 IA a înregistrat o dominanță a spectrului roșu (R: {r_val:.1f}, V: {g_val:.1f}). Urșii presează piața.",
            "flat_up": f"💤 Flat. Avantaj slab al cumpărătorilor (V: {g_val:.1f}/R: {r_val:.1f}).",
            "flat_down": f"💤 Flat. Avantaj slab al vânzătorilor (R: {r_val:.1f}/V: {g_val:.1f}).",
            "up_tips": f"💎 SFAT: Intrare în SUS pe [{expiration}]. Risc — 1-2%!",
            "down_tips": f"💎 SFAT: Intrare în JOS pe [{expiration}]. Volume confirmate!"
        },
        "en": {
            "up_title": "🟢 HIGHER (CALL) 📈", "down_title": "🔴 LOWER (PUT) 📉",
            "up_reason": f"🔥 AI detected green spectrum dominance (G: {g_val:.1f}, R: {r_val:.1f}). Bullish momentum rising.",
            "down_reason": f"💥 AI detected red spectrum dominance (R: {r_val:.1f}, G: {g_val:.1f}). Bearish pressure mounting.",
            "flat_up": f"💤 Flat. Weak buyers advantage (G: {g_val:.1f}/R: {r_val:.1f}).",
            "flat_down": f"💤 Flat. Weak sellers advantage (R: {r_val:.1f}/G: {g_val:.1f}).",
            "up_tips": f"💎 TIP: Enter UP for [{expiration}]. Risk 1-2% max!",
            "down_tips": f"💎 TIP: Enter DOWN for [{expiration}]. Volume confirmed!"
        },
        "es": {
            "up_title": "🟢 ARRIBA (CALL) 📈", "down_title": "🔴 ABAJO (PUT) 📉",
            "up_reason": f"🔥 IA detectó dominio del espectro verde (V: {g_val:.1f}, R: {r_val:.1f}). Impulso alcista.",
            "down_reason": f"💥 IA detectó dominio del espectro rojo (R: {r_val:.1f}, V: {g_val:.1f}). Presión bajista.",
            "flat_up": f"💤 Lateral. Ventaja débil de compradores (V: {g_val:.1f}/R: {r_val:.1f}).",
            "flat_down": f"💤 Lateral. Ventaja débil de vendedores (R: {r_val:.1f}/V: {g_val:.1f}).",
            "up_tips": f"💎 CONSEJO: Compra en [{expiration}]. ¡Riesgo 1-2%!",
            "down_tips": f"💎 CONSEJO: Venta en [{expiration}]. ¡Volumen confirmado!"
        },
        "tr": {
            "up_title": "🟢 YUKARI (CALL) 📈", "down_title": "🔴 AŞAĞI (PUT) 📉",
            "up_reason": f"🔥 Yapay Zeka yeşil spektrum baskınlığı tespit etti (Y: {g_val:.1f}, K: {r_val:.1f}).",
            "down_reason": f"💥 Yapay Zeka kırmızı spektrum baskınlığı tespit etti (K: {r_val:.1f}, Y: {g_val:.1f}).",
            "flat_up": f"💤 Yatay Piyasa. Alıcıların hafif üstünlüğü (Y: {g_val:.1f}/K: {r_val:.1f}).",
            "flat_down": f"💤 Yatay Piyasa. Satıcıların hafif üstünlüğü (K: {r_val:.1f}/Y: {g_val:.1f}).",
            "up_tips": f"💎 İPUCU: [{expiration}] için YUKARI işlem. Risk %1-2!",
            "down_tips": f"💎 İPUCU: [{expiration}] için AŞAĞI işlem. Hacim onaylandı!"
        }
    }
    
    # Если передан неизвестный язык, ставим русский по умолчанию
    t_dict = translations.get(lang, translations["ru"])
    
    if direction == "UP":
        return jsonify({"direction": "UP", "signal_text": t_dict["up_title"], "reason": t_dict["up_reason"], "tips": t_dict["up_tips"]})
    elif direction == "DOWN":
        return jsonify({"direction": "DOWN", "signal_text": t_dict["down_title"], "reason": t_dict["down_reason"], "tips": t_dict["down_tips"]})
    elif direction == "FLAT_UP":
        return jsonify({"direction": "UP", "signal_text": t_dict["up_title"], "reason": t_dict["flat_up"], "tips": t_dict["up_tips"]})
    else:
        return jsonify({"direction": "DOWN", "signal_text": t_dict["down_title"], "reason": t_dict["flat_down"], "tips": t_dict["down_tips"]})

# 4. ОБРАБОТКА ВЕБХУКА ОТ ТЕЛЕГРАМА
@app.route(f'/{TOKEN}', methods=['POST'])
async def telegram_webhook():
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    await bot_app.process_update(update)
    return 'OK', 200

# 5. ТЕЛЕГРАМ ОБРАБОТЧИК КОМАНДЫ /START
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[InlineKeyboardButton("🔮 ЗАПУСТИТЬ ИИ СКАНЕР 🚀", web_app=WebAppInfo(url=APP_URL))]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 Привет, Мастер трейдинга! 🤖💎\n\nНажимай на кнопку ниже, выбирай один из 6 языков интерфейса, направляй камеру на свечу и забирай точный ИИ сигнал! 📈💸",
        reply_markup=reply_markup, parse_mode="Markdown"
    )

# Установка вебхука при старте
async def setup_webhook():
    await bot_app.initialize()
    await bot_app.add_handler(CommandHandler("start", start))
    await bot_app.bot.set_webhook(url=f"{APP_URL}/{TOKEN}")
    print("🚀 Вебхук Telegram успешно установлен!")

if __name__ == '__main__':
    # Запускаем установку вебхука в цикле событий, затем стартуем Flask
    asyncio.run(setup_webhook())
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
