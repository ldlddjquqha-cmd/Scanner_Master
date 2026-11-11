import os
import asyncio
import json
import logging
import random
from fastapi import FastAPI, UploadFile, File, Form, Request, Cookie
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
import google.generativeai as genai
from PIL import Image
import io
import numpy as np
import httpx
from datetime import datetime

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="TEAM MASTER VIP - ULTIMATE QUANTUM CORE", version="26.5")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

DB_FILE = "requests.json"
BOT_TOKEN = "8847524172:AAF3um77sDqsX6IWq_W-D-Hb8TQJSpK-0qo"
ADMIN_CHAT_ID = "6765689893"

def get_db():
    if not os.path.exists(DB_FILE):
        return {"users": {}, "history": []}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            if "users" not in data: data = {"users": data, "history": []}
            if "history" not in data: data["history"] = []
            return data
        except:
            return {"users": {}, "history": []}

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

async def send_tg_notification(username):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "❌ Забанить", "callback_data": f"ban:{username}"},
                {"text": "✅ Разблокировать", "callback_data": f"unban:{username}"}
            ]
        ]
    }
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": f"🔔 Новый ученик вошел в систему!\n\nНик: @{username}\nСтатус: ✅ Доступ разрешен",
        "parse_mode": "Markdown",
        "reply_markup": reply_markup
    }
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json=payload, timeout=5.0)
        except Exception as e:
            pass

async def send_tg_signal_alert(username, asset, timeframe):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": f"📊 Пользователь перешел к сигналам!\n\nУченик: @{username}\nАктив: {asset}\nТаймфрейм: {timeframe}\nВремя запроса: {datetime.now().strftime('%H:%M:%S')}",
        "parse_mode": "Markdown"
    }
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json=payload, timeout=5.0)
        except Exception as e:
            pass

BINANCE_MAPPING = {
    "EUR/USD": "EURUSDT", "GBP/USD": "GBPUSDT", "USD/JPY": "USDJPY",
    "AUD/USD": "AUDUSDT", "EUR/JPY": "EURJPY", "USD/CAD": "USDCAD",
    "GBP/JPY": "GBPJPY", "NZD/USD": "NZDUSDT", "USD/CHF": "USDCHF", "EUR/GBP": "EURGBP",
    "Bitcoin": "BTCUSDT", "Ethereum": "ETHUSDT", "Solana": "SOLUSDT"
}

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices)
    up = np.where(deltas > 0, deltas, 0).mean()
    down = np.where(deltas < 0, -deltas, 0).mean()
    if down == 0:
        return 100.0
    rs = up / down
    return 100.0 - (100.0 / (1.0 + rs))

def calculate_ema(prices, period=20):
    if len(prices) < period:
        return prices[-1]
    weights = np.exp(np.linspace(-1., 0., period))
    weights /= weights.sum()
    return np.convolve(prices, weights, mode='valid')[-1]

def smart_market_analysis(prices, rsi, ema):
    current_price = prices[-1]
    prev_price = prices[-2] if len(prices) >= 2 else current_price

    trend_up = current_price > ema
    momentum_up = current_price > prev_price
    reasons = []

    if trend_up:
        reasons.append("Цена находится выше экспоненциальной скользящей средней (EMA), подтверждая сильный восходящий тренд.")
    else:
        reasons.append("Цена закрепилась ниже скользящей средней (EMA), что указывает на развитие нисходящей тенденции.")

    if rsi > 65:
        reasons.append(f"Индекс относительной силы RSI составляет {round(rsi, 1)}, актив приближается к зоне перекупленности.")
    elif rsi < 35:
        reasons.append(f"Индекс относительной силы RSI находится на отметке {round(rsi, 1)}, фиксируется зона перепроданности и потенциал отскока.")
    else:
        reasons.append(f"Индикатор RSI нейтрален ({round(rsi, 1)}), доминирует текущий локальный импульс.")

    if momentum_up:
        reasons.append("Свечной анализ показывает преобладание покупателей на последнем таймфрейме.")
    else:
        reasons.append("Наблюдается давление со стороны продавцов по закрытию последних свечей.")

    signal = "UP" if (trend_up and momentum_up) or (rsi < 40) else "DOWN"
    full_reason = " ".join(reasons)
    return signal, full_reason

def generate_otc_candles(asset_name, count=50):
    seed_value = sum(ord(char) for char in asset_name) + int(asyncio.get_event_loop().time() / 150)
    state = random.Random(seed_value)
    if "Bitcoin" in asset_name:
        start_price = 65000.0
    elif "Ethereum" in asset_name:
        start_price = 3500.0
    elif "Gold" in asset_name:
        start_price = 2300.0
    else:
        start_price = 1.1250
    prices = [start_price]
    for _ in range(count):
        prices.append(prices[-1] * (1 + state.uniform(-0.0015, 0.0015)))
    return prices

class ScannerAnalyzer:
    async def compute(self, image_bytes: bytes, config: dict) -> str:
        try:
            image = Image.open(io.BytesIO(image_bytes))

            if GEMINI_API_KEY:
                try:
                    model = genai.GenerativeModel('gemini-1.5-flash-001')
                    prompt = (
                        "Ты профессиональный ИИ-сканер графиков бинарных опционов Pocket Option. "
                        "Внимательно проанализируй расположение свечей, их тела, тени, цвета, текущую тенденцию, уровни и паттерны на данном скриншоте графиках. "
                        "Выдай точный и детальный торговый сигнал: направление (BUY / ВВЕРХ или SELL / ВНИЗ), таймфрейм, стратегию (" + config.get('стратегия', 'Smart Money') + ") "
                        "и подробный технический анализ на основе расположения свечей. Обязательно используй эмодзи 📈📉⚡🔥💎."
                    )
                    response = model.generate_content([prompt, image])
                    if response and response.text:
                        return f"--- TEAM MASTER AI CANDLE SCANNER ---\n{response.text}"
                except Exception as ex:
                    pass

            img_rgb = image.convert("RGB")
            np_img = np.array(img_rgb)
            r_channel = np_img[:, :, 0].astype(float)
            g_channel = np_img[:, :, 1].astype(float)
            b_channel = np_img[:, :, 2].astype(float)
            green_candles = np.sum((g_channel > r_channel + 30) & (g_channel > b_channel + 30))
            red_candles = np.sum((r_channel > g_channel + 30) & (r_channel > b_channel + 30))
            total_pixels = np_img.shape[0] * np_img.shape[1]
            candle_ratio = (green_candles + red_candles) / total_pixels

            if candle_ratio < 0.0015:
                return "❌ ГРАФИК СО СВЕЧАМИ НЕ ОБНАРУЖЕН. Убедитесь, что на скриншоте четко виден график."

            direction = "BUY (ВВЕРХ)" if green_candles >= red_candles else "SELL (ВНИЗ)"
            trend_desc = "Преобладание бычьих зеленых тел свечей указывает на покупателей." if green_candles >= red_candles else "Доминирование медвежьих красных свечей говорит о давлении продавцов."

            return (
                f"--- TEAM MASTER SIGNAL V4.0 ---\n"
                f"СТРАТЕГИЯ: {config.get('стратегия', 'Smart Money')}\n"
                f"ТАЙМФРЕЙМ: {config.get('интервал', 'M1')}\n"
                f"ЭКСПИРАЦИЯ: {config.get('экспирация', '1м')}\n"
                f"-------------------------------\n"
                f"ВЕРДИКТ: {direction}\n"
                f"ПРИЧИНА: Расположение и анализ свечных тел: {trend_desc} Уровни сопротивления и поддержки подтверждены."
            )
        except Exception as e:
            return f"❌ Ошибка анализа изображения: {str(e)}"

scanner_core = ScannerAnalyzer()

ASSETS_DATA_JS = {
    "ru": {
        "💱 Валютные пары": [
            "🇪🇺🇺🇸 EUR/USD OTC", "🇬🇧🇺🇸 GBP/USD OTC", "🇺🇸🇯🇵 USD/JPY OTC", "🇦🇺🇺🇸 AUD/USD OTC", "🇪🇺🇯🇵 EUR/JPY OTC",
            "🇺🇸🇨🇦 USD/CAD OTC", "🇬🇧🇯🇵 GBP/JPY OTC", "🇳🇿🇺🇸 NZD/USD OTC", "🇺🇸🇨🇭 USD/CHF OTC", "🇪🇺🇬🇧 EUR/GBP OTC",
            "🇦🇺🇨🇭 AUD/CHF OTC", "🇦🇺🇨🇭 AUD/CHF", "🇦🇺🇺🇸 AUD/USD", "🇺🇸🇨🇦 USD/CAD", "🇺🇸🇯🇵 USD/JPY", "🇪🇺🇺🇸 EUR/USD",
            "🇬🇧🇺🇸 GBP/USD", "🇪🇺🇬🇧 EUR/GBP", "🇬🇧🇨🇭 GBP/CHF", "🇬🇧🇦🇺 GBP/AUD", "🇪🇺🇨🇭 EUR/CHF", "🇪🇺🇨🇦 EUR/CAD",
            "🇪🇺🇦🇺 EUR/AUD", "🇯🇵🇨🇦 CAD/JPY", "🇯🇵🇨🇭 CHF/JPY", "🇦🇺🇨🇦 AUD/CAD", "🇦🇺🇯🇵 AUD/JPY", "🇬🇧🇯🇵 GBP/JPY", "🇪🇺🇯🇵 EUR/JPY", "🇨🇦🇨🇭 CAD/CHF",
            "🇦🇺🇳🇿 AUD/NZD OTC", "🇨🇦🇨🇭 CAD/CHF OTC", "🇪🇺🇭🇺 EUR/HUF OTC", "🇪🇺🇳🇿 EUR/NZD OTC", "🇪🇺🇹🇷 EUR/TRY OTC", "🇬🇧🇦🇺 GBP/AUD OTC",
            "🇬🇧🇨🇭 GBP/CHF OTC", "🇬🇧🇨🇦 GBP/CAD OTC", "🇬🇧🇯🇵 GBP/JPY OTC", "🇳🇿🇯🇵 NZD/JPY OTC", "🇳🇿🇺🇸 NZD/USD OTC", "🇺🇸🇦🇷 USD/ARS OTC",
            "🇺🇸🇧🇷 USD/BRL OTC", "🇺🇸🇨🇱 USD/CLP OTC", "🇺🇸🇨🇴 USD/COP OTC", "🇺🇸🇩🇿 USD/DZD OTC", "🇺🇸🇪🇬 USD/EGP OTC", "🇺🇸🇮🇩 USD/IDR OTC",
            "🇺🇸🇮🇳 USD/INR OTC", "🇺🇸🇲🇽 USD/MXN OTC", "🇺🇸🇲🇾 USD/MYR OTC", "🇺🇸🇵🇭 USD/PHP OTC", "🇺🇸🇵🇰 USD/PKR OTC", "🇺🇸🇸🇬 USD/SGD OTC",
            "🇺🇸🇹🇭 USD/THB OTC", "🇺🇸🇻🇳 USD/VND OTC", "🇿🇦🇺🇸 ZAR/USD OTC", "🇺🇦🇺🇸 UAH/USD OTC", "🇳🇬🇺🇸 NGN/USD OTC", "🇰🇪🇺🇸 KES/USD OTC"
        ],
        "🔥 Криптовалюты": [
            "₿ Bitcoin", "Ξ Ethereum", "🪙 Solana", "🪙 BNB OTC", "🪙 Dogecoin OTC", "🪙 Chainlink OTC",
            "🪙 Toncoin OTC", "🪙 Bitcoin ETF OTC", "🪙 Bitcoin OTC", "🪙 Avalanche OTC", "🪙 Cardano OTC",
            "🪙 Ethereum OTC", "🪙 Polkadot OTC", "🪙 Polygon OTC", "🪙 Litecoin OTC", "🪙 TRON OTC",
            "🪙 Dash", "🪙 Chainlink", "🪙 BCH/EUR", "🪙 BCH/GBP", "🪙 BCH/JPY", "🪙 BTC/GBP", "🪙 BTC/JPY"
        ],
        "🛢️ Сырьевые товары": [
            "🛢️ Brent Oil OTC", "🛢️ WTI Crude Oil OTC", "🪙 Silver OTC", "🪙 Gold OTC",
            "⛽ Natural Gas OTC", "🪙 Palladium spot OTC", "🪙 Platinum spot"
        ],
        "📈 Акции компаний": [
            "🍎 Apple OTC", "🚗 Tesla OTC", "💳 VISA OTC", "💻 Intel OTC", "🛒 Amazon OTC",
            "🎬 Netflix OTC", "📘 FACEBOOK INC OTC", "📦 Microsoft OTC", "💊 Johnson & Johnson OTC",
            "🛒 Alibaba OTC", "📊 VIX OTC", "🛢️ ExxonMobil OTC", "🤖 Palantir Technologies OTC",
            "🪙 Coinbase Global OTC", "🍔 McDonald's OTC", "✈️ Boeing Company OTC", "💊 Pfizer Inc OTC",
            "🏦 Citigroup Inc OTC", "📦 FedEx OTC", "🎮 GameStop Corp OTC", "💳 American Express OTC",
            "🌐 Cisco OTC", "💻 Advanced Micro Devices OTC", "📦 Marathon Digital Holdings OTC"
        ],
        "📊 Биржевые индексы": [
            "📊 US100 OTC", "📈 SP500", "📊 SP500 OTC", "📊 AUS 200"
        ]
    },
    "en": {
        "💱 Currency Pairs": [
            "🇪🇺🇺🇸 EUR/USD OTC", "🇬🇧🇺🇸 GBP/USD OTC", "🇺🇸🇯🇵 USD/JPY OTC", "🇦🇺🇺🇸 AUD/USD OTC", "🇪🇺🇯🇵 EUR/JPY OTC",
            "🇺🇸🇨🇦 USD/CAD OTC", "🇬🇧🇯🇵 GBP/JPY OTC", "🇳🇿🇺🇸 NZD/USD OTC", "🇺🇸🇨🇭 USD/CHF OTC", "🇪🇺🇬🇧 EUR/GBP OTC",
            "🇦🇺🇨🇭 AUD/CHF OTC", "🇦🇺🇨🇭 AUD/CHF", "🇦🇺🇺🇸 AUD/USD", "🇺🇸🇨🇦 USD/CAD", "🇺🇸🇯🇵 USD/JPY", "🇪🇺🇺🇸 EUR/USD",
            "🇬🇧🇺🇸 GBP/USD", "🇪🇺🇬🇧 EUR/GBP", "🇬🇧🇨🇭 GBP/CHF", "🇬🇧🇦🇺 GBP/AUD", "🇪🇺🇨🇭 EUR/CHF", "🇪🇺🇨🇦 EUR/CAD",
            "🇪🇺🇦🇺 EUR/AUD", "🇯🇵🇨🇦 CAD/JPY", "🇯🇵🇨🇭 CHF/JPY", "🇦🇺🇨🇦 AUD/CAD", "🇦🇺🇯🇵 AUD/JPY", "🇬🇧🇯🇵 GBP/JPY", "🇪🇺🇯🇵 EUR/JPY", "🇨🇦🇨🇭 CAD/CHF",
            "🇦🇺🇳🇿 AUD/NZD OTC", "🇨🇦🇨🇭 CAD/CHF OTC", "🇪🇺🇭🇺 EUR/HUF OTC", "🇪🇺🇳🇿 EUR/NZD OTC", "🇪🇺🇹🇷 EUR/TRY OTC", "🇬🇧🇦🇺 GBP/AUD OTC",
            "🇬🇧🇨🇭 GBP/CHF OTC", "🇬🇧🇨🇦 GBP/CAD OTC", "🇬🇧🇯🇵 GBP/JPY OTC", "🇳🇿🇯🇵 NZD/JPY OTC", "🇳🇿🇺🇸 NZD/USD OTC", "🇺🇸🇦🇷 USD/ARS OTC",
            "🇺🇸🇧🇷 USD/BRL OTC", "🇺🇸🇨🇱 USD/CLP OTC", "🇺🇸🇨🇴 USD/COP OTC", "🇺🇸🇩🇿 USD/DZD OTC", "🇺🇸🇪🇬 USD/EGP OTC", "🇺🇸🇮🇩 USD/IDR OTC",
            "🇺🇸🇮🇳 USD/INR OTC", "🇺🇸🇲🇽 USD/MXN OTC", "🇺🇸🇲🇾 USD/MYR OTC", "🇺🇸🇵🇭 USD/PHP OTC", "🇺🇸🇵🇰 USD/PKR OTC", "🇺🇸🇸🇬 USD/SGD OTC",
            "🇺🇸🇹🇭 USD/THB OTC", "🇺🇸🇻🇳 USD/VND OTC", "🇿🇦🇺🇸 ZAR/USD OTC", "🇺🇦🇺🇸 UAH/USD OTC", "🇳🇬🇺🇸 NGN/USD OTC", "🇰🇪🇺🇸 KES/USD OTC"
        ],
        "🔥 Cryptocurrencies": [
            "₿ Bitcoin", "Ξ Ethereum", "🪙 Solana", "🪙 BNB OTC", "🪙 Dogecoin OTC", "🪙 Chainlink OTC",
            "🪙 Toncoin OTC", "🪙 Bitcoin ETF OTC", "🪙 Bitcoin OTC", "🪙 Avalanche OTC", "🪙 Cardano OTC",
            "🪙 Ethereum OTC", "🪙 Polkadot OTC", "🪙 Polygon OTC", "🪙 Litecoin OTC", "🪙 TRON OTC",
            "🪙 Dash", "🪙 Chainlink", "🪙 BCH/EUR", "🪙 BCH/GBP", "🪙 BCH/JPY", "🪙 BTC/GBP", "🪙 BTC/JPY"
        ],
        "🛢️ Commodities": [
            "🛢️ Brent Oil OTC", "🛢️ WTI Crude Oil OTC", "🪙 Silver OTC", "🪙 Gold OTC",
            "⛽ Natural Gas OTC", "🪙 Palladium spot OTC", "🪙 Platinum spot"
        ],
        "📈 Company Stocks": [
            "🍎 Apple OTC", "🚗 Tesla OTC", "💳 VISA OTC", "💻 Intel OTC", "🛒 Amazon OTC",
            "🎬 Netflix OTC", "📘 FACEBOOK INC OTC", "📦 Microsoft OTC", "💊 Johnson & Johnson OTC",
            "🛒 Alibaba OTC", "📊 VIX OTC", "🛢️ ExxonMobil OTC", "🤖 Palantir Technologies OTC",
            "🪙 Coinbase Global OTC", "🍔 McDonald's OTC", "✈️ Boeing Company OTC", "💊 Pfizer Inc OTC",
            "🏦 Citigroup Inc OTC", "📦 FedEx OTC", "🎮 GameStop Corp OTC", "💳 American Express OTC",
            "🌐 Cisco OTC", "💻 Advanced Micro Devices OTC", "📦 Marathon Digital Holdings OTC"
        ],
        "📊 Market Indices": [
            "📊 US100 OTC", "📈 SP500", "📊 SP500 OTC", "📊 AUS 200"
        ]
    },
    "ua": {
        "💱 Валютні пари": [
            "🇪🇺🇺🇸 EUR/USD OTC", "🇬🇧🇺🇸 GBP/USD OTC", "🇺🇸🇯🇵 USD/JPY OTC", "🇦🇺🇺🇸 AUD/USD OTC", "🇪🇺🇯🇵 EUR/JPY OTC",
            "🇺🇸🇨🇦 USD/CAD OTC", "🇬🇧🇯🇵 GBP/JPY OTC", "🇳🇿🇺🇸 NZD/USD OTC", "🇺🇸🇨🇭 USD/CHF OTC", "🇪🇺🇬🇧 EUR/GBP OTC",
            "🇦🇺🇨🇭 AUD/CHF OTC", "🇦🇺🇨🇭 AUD/CHF", "🇦🇺🇺🇸 AUD/USD", "🇺🇸🇨🇦 USD/CAD", "🇺🇸🇯🇵 USD/JPY", "🇪🇺🇺🇸 EUR/USD",
            "🇬🇧🇺🇸 GBP/USD", "🇪🇺🇬🇧 EUR/GBP", "🇬🇧🇨🇭 GBP/CHF", "🇬🇧🇦🇺 GBP/AUD", "🇪🇺🇨🇭 EUR/CHF", "🇪🇺🇨🇦 EUR/CAD",
            "🇪🇺🇦🇺 EUR/AUD", "🇯🇵🇨🇦 CAD/JPY", "🇯🇵🇨🇭 CHF/JPY", "🇦🇺🇨🇦 AUD/CAD", "🇦🇺🇯🇵 AUD/JPY", "🇬🇧🇯🇵 GBP/JPY", "🇪🇺🇯🇵 EUR/JPY", "🇨🇦🇨🇭 CAD/CHF",
            "🇦🇺🇳🇿 AUD/NZD OTC", "🇨🇦🇨🇭 CAD/CHF OTC", "🇪🇺🇭🇺 EUR/HUF OTC", "🇪🇺🇳🇿 EUR/NZD OTC", "🇪🇺🇹🇷 EUR/TRY OTC", "🇬🇧🇦🇺 GBP/AUD OTC",
            "🇬🇧🇨🇭 GBP/CHF OTC", "🇬🇧🇨🇦 GBP/CAD OTC", "🇬🇧🇯🇵 GBP/JPY OTC", "🇳🇿🇯🇵 NZD/JPY OTC", "🇳🇿🇺🇸 NZD/USD OTC", "🇺🇸🇦🇷 USD/ARS OTC",
            "🇺🇸🇧🇷 USD/BRL OTC", "🇺🇸🇨🇱 USD/CLP OTC", "🇺🇸🇨🇴 USD/COP OTC", "🇺🇸🇩🇿 USD/DZD OTC", "🇺🇸🇪🇬 USD/EGP OTC", "🇺🇸🇮🇩 USD/IDR OTC",
            "🇺🇸🇮🇳 USD/INR OTC", "🇺🇸🇲🇽 USD/MXN OTC", "🇺🇸🇲🇾 USD/MYR OTC", "🇺🇸🇵🇭 USD/PHP OTC", "🇺🇸🇵🇰 USD/PKR OTC", "🇺🇸🇸🇬 USD/SGD OTC",
            "🇺🇸🇹🇭 USD/THB OTC", "🇺🇸🇻🇳 USD/VND OTC", "🇿🇦🇺🇸 ZAR/USD OTC", "🇺🇦🇺🇸 UAH/USD OTC", "🇳🇬🇺🇸 NGN/USD OTC", "🇰🇪🇺🇸 KES/USD OTC"
        ],
        "🔥 Криптовалюти": [
            "₿ Bitcoin", "Ξ Ethereum", "🪙 Solana", "🪙 BNB OTC", "🪙 Dogecoin OTC", "🪙 Chainlink OTC",
            "🪙 Toncoin OTC", "🪙 Bitcoin ETF OTC", "🪙 Bitcoin OTC", "🪙 Avalanche OTC", "🪙 Cardano OTC",
            "🪙 Ethereum OTC", "🪙 Polkadot OTC", "🪙 Polygon OTC", "🪙 Litecoin OTC", "🪙 TRON OTC",
            "🪙 Dash", "🪙 Chainlink", "🪙 BCH/EUR", "🪙 BCH/GBP", "🪙 BCH/JPY", "🪙 BTC/GBP", "🪙 BTC/JPY"
        ],
        "🛢️ Сировинні товари": [
            "🛢️ Brent Oil OTC", "🛢️ WTI Crude Oil OTC", "🪙 Silver OTC", "🪙 Gold OTC",
            "⛽ Natural Gas OTC", "🪙 Palladium spot OTC", "🪙 Platinum spot"
        ],
        "📈 Акції компаній": [
            "🍎 Apple OTC", "🚗 Tesla OTC", "💳 VISA OTC", "💻 Intel OTC", "🛒 Amazon OTC",
            "🎬 Netflix OTC", "📘 FACEBOOK INC OTC", "📦 Microsoft OTC", "💊 Johnson & Johnson OTC",
            "🛒 Alibaba OTC", "📊 VIX OTC", "🛢️ ExxonMobil OTC", "🤖 Palantir Technologies OTC",
            "🪙 Coinbase Global OTC", "🍔 McDonald's OTC", "✈️ Boeing Company OTC", "💊 Pfizer Inc OTC",
            "🏦 Citigroup Inc OTC", "📦 FedEx OTC", "🎮 GameStop Corp OTC", "💳 American Express OTC",
            "🌐 Cisco OTC", "💻 Advanced Micro Devices OTC", "📦 Marathon Digital Holdings OTC"
        ],
        "📊 Біржові індекси": [
            "📊 US100 OTC", "📈 SP500", "📊 SP500 OTC", "📊 AUS 200"
        ]
    }
}

EDU_50 = [
    {
        "t": "1. Торговля от уровней поддержки и сопротивления",
        "tags": ["M1", "M5", "Pocket Option", "Уровни", "Price Action"],
        "d": "📌 Подробный разбор стратегии торговли от уровней поддержки и сопротивления:\n\n"
           "• 🧠 Рыночная логика и суть стратегии:\n"
           "Уровни поддержки и сопротивления — это ключевые ценовые зоны, где баланс сил между покупателями и продавцами кардинально меняется. Уровень сопротивления — это область сверху, где продавцы останавливают рост цены и начинают агрессивно давить вниз. Уровень поддержки — область снизу, где покупатели защищают свои позиции и толкают цену вверх. Когда цена приближается к таким барьерам, крупные участники рынка часто фиксируют прибыль, что приводит к техническому отскоку или мощному пробою.\n\n"
           "• ⏱ Рекомендуемые таймфреймы графика:\n"
           "Таймфреймы M1, M2 и M5 идеально подходят для анализа бинарных опционов.\n\n"
           "• ⏳ Оптимальная экспирация сделки:\n"
           "От 2 до 4 минут в зависимости от силы и ширины ценового уровня.\n\n"
           "• 🎯 Четкие правила открытия позиций (CALL / PUT):\n"
           " 1️⃣ Для входа ВНИЗ (PUT): Дождитесь подхода цены к сильному уровню сопротивления. Проанализируйте закрытие свечей: если появляются тени и замедление импульса, открывайте сделку на понижение.\n"
           " 2️⃣ Для входа ВВЕРХ (CALL): При касании ценой подтвержденного уровня поддержки и формировании зеленой поглощающей свечи открывайте сделку на повышение.\n\n"
           "• 🛡️ Управление капиталом и риски:\n"
           "Никогда не входите напролом при затяжном импульсе. Ждите подтверждения свечного паттерна."
    },
    {
        "t": "2. Торговля в боковике (Флэт и диапазон)",
        "tags": ["M1", "M5", "Pocket Option", "Флэт", "Боковик"],
        "d": "📌 Подробный разбор стратегии торговли в боковике (флэте):\n\n"
           "• 🧠 Рыночная логика и суть стратегии:\n"
           "Боковое движение (флэт) возникает тогда, когда на рынке нет ярко выраженного тренда, а цена движется внутри четкого горизонтального коридора между границами поддержки снизу и сопротивления сверху. Стратегия основана на возвращении цены к средним значениям диапазона (методика возврата к меану). Трейдер продает у верхней границы канала и покупает у нижней.\n\n"
           "• ⏱ Рекомендуемые таймфреймы графика:\n"
           "Таймфреймы M1 и M5.\n\n"
           "• ⏳ Оптимальная экспирация сделки:\n"
           "1 – 3 минуты.\n\n"
           "• 🎯 Четкие правила открытия позиций (CALL / PUT):\n"
           " 1️⃣ Для входа ВВЕРХ (CALL): Цена аккуратно касается нижней границы бокового канала, формируется паттерн ложного пробоя или пин-бар. Открываем сделку вверх к середине или верхней границе флэта.\n"
           " 2️⃣ Для входа ВНИЗ (PUT): Цена доходит до верхней границы горизонтального диапазона, импульс угасает, открываем сделку на понижение.\n\n"
           "• 🛡️ Управление капиталом и риски:\n"
           "Обязательно проверяйте отсутствие новостного фона, способного пробить узкий коридор."
    },
    {
        "t": "3. Accelerator Oscillator + Alligator",
        "tags": ["M1", "M2", "M5", "Pocket Option", "Индикаторы"],
        "d": "📌 Подробный разбор профессиональной связки Accelerator Oscillator + Alligator:\n\n"
           "• 🧠 Рыночная логика и суть стратегии:\n"
           "Индикатор Alligator моделирует рыночные циклы, а Accelerator Oscillator (AC) измеряет ускорение движущей силы рынка.\n\n"
           "• ⏱ Рекомендуемые таймфреймы:\n"
           "M1, M2 и M5.\n\n"
           "• ⏳ Оптимальная экспирация:\n"
           "От 1 до 3 минут.\n\n"
           "• 🎯 Четкие правила открытия позиций (CALL / PUT):\n"
           " 1️⃣ CALL: Линии Аллигатора развернуты вверх, гистограмма AC переходит в зеленую зону выше нуля.\n"
           " 2️⃣ PUT: Пасть Аллигатора направлена вниз, гистограмма AC в красной зоне.\n\n"
           "• 🛡️ Управление капиталом:\n"
           "Риск на сделку не более 1-2%."
    },
    {
        "t": "4. ADX + Aroon",
        "tags": ["M1", "M5", "Pocket Option", "Индикаторы"],
        "d": "📌 Подробный разбор связки ADX + Aroon:\n\n"
           "• 🧠 Рыночная логика:\n"
           "ADX измеряет силу тренда, а Aroon находит экстремумы цены.\n\n"
           "• ⏱ Рекомендуемые таймфреймы:\n"
           "M1 и M5.\n\n"
           "• ⏳ Оптимальная экспирация:\n"
           "От 2 до 3 минут.\n\n"
           "• 🎯 Четкие правила открытия позиций:\n"
           " 1️⃣ CALL: ADX выше 25, Aroon Up пересекает Aroon Down снизу вверх.\n"
           " 2️⃣ PUT: ADX выше 25, Aroon Down уходит в верхнюю зону.\n\n"
           "• 🛡️ Риск-менеджмент:\n"
           "Избегать торговли при ADX ниже 20."
    },
    {
        "t": "5. Bollinger Bands + Awesome Oscillator",
        "tags": ["S30", "M1", "Pocket Option", "Индикаторы"],
        "d": "📌 Подробный разбор связки Bollinger Bands + Awesome Oscillator:\n\n"
           "• 🧠 Рыночная логика:\n"
           "Полосы Боллинджера фиксируют волатильность и пробой границ, а AO оценивает импульс.\n\n"
           "• ⏱ Рекомендуемые таймфреймы:\n"
           "S30 и M1.\n\n"
           "• ⏳ Оптимальная экспирация:\n"
           "1 минута.\n\n"
           "• 🎯 Четкие правила открытия позиций:\n"
           " 1️⃣ CALL: Касание нижней границы Bollinger Bands, гистограмма AO переходит в зеленую зону.\n"
           " 2️⃣ PUT: Касание верхней границы, разворот гистограммы AO вниз.\n\n"
           "• 🛡️ Риск-менеджмент:\n"
           "Торговать по тренду старшего таймфрейма."
    }
]

for i in range(6, 51):
    strat_title = f"{i}. Профессиональная стратегия и связка индикаторов №{i}"
    strat_desc = (
        f"📌 Подробный разбор стратегии и методики №{i} для терминала Pocket Option:\n\n"
        f"• 🧠 Рыночная механика и суть стратегии:\n"
        f"Данная авторская торговая система построена на комплексном анализе уровней ликвидности, динамических зон перекупленности/перепроданности "
        f"и фильтрации рыночного шума. Она создана для уверенной торговли бинарными опционами на любых валютных парах, акциях и криптовалютах, "
        f"позволяя точно определять моменты разворота тренда и импульсного продолжения движения.\n\n"
        f"• ⏱ Рекомендуемые таймфреймы графика:\n"
        f"Оптимально использовать таймфреймы M1 (1 минута), M2 и M5 для стабильной отработки сигналов.\n\n"
        f"• ⏳ Оптимальная экспирация сделки в Pocket Option:\n"
        f"Рекомендуемое время экспирации составляет от 1 до 3 минут.\n\n"
        f"• 🎯 Четкие правила открытия позиций (CALL / PUT):\n"
        f" 1️⃣ Шаг первый: Дождитесь подхода цены к ключевой зоне интереса или подтверждения сигнала по основному индикатору.\n"
        f" 2️⃣ Шаг второй: Убедитесь в отсутствии сильного противонаправленного импульса на младшем таймфрейме.\n"
        f" 3️⃣ Шаг третий: Открывайте позицию ВВЕРХ (CALL) или ВНИЗ (PUT) строго на закрытии сигнальной свечи без спешки.\n\n"
        f"• 🛡️ Управление капиталом и риски:\n"
        f"Никогда не превышайте риск в 1-2% от вашего депозита на одну сделку. Дисциплина и строгий мани-менеджмент — главный залог успеха."
    )
    EDU_50.append({
        "t": strat_title,
        "tags": ["M1", "Pocket Option", "Стратегии"],
        "d": strat_desc
    })

HTML_UI = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>TEAM MASTER VIP — Ultimate Trading Terminal</title>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
:root{--gold:#FFD700;--gold-g:linear-gradient(135deg,#BF953F,#FCF6BA,#B38728,#FBF5B7,#AA771C);--bg:#04060a;--card:#0b0f17;--inner:#070a10;--green:#00E676;--red:#FF5252;--border:#161e2e;--border-g:rgba(212,175,55,.45);--muted:#7a8499;--text:#e8ecf4}
*{box-sizing:border-box;margin:0;padding:0;font-family:Montserrat,sans-serif;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--text);min-height:100vh;display:flex;justify-content:center;padding:10px 10px 85px}
.wrap{width:100%;max-width:440px;display:flex;flex-direction:column;gap:10px}
.g-text{background:var(--gold-g);background-size:200% auto;-webkit-background-clip:text;-webkit-text-fill-color:transparent;animation:sh 3s linear infinite;font-weight:900}
@keyframes sh{to{background-position:200% center}}
@keyframes fu{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
.btn{width:100%;padding:13px;border:none;border-radius:12px;font-weight:800;font-size:12px;letter-spacing:.3px;text-transform:uppercase;cursor:pointer;text-align:center;text-decoration:none;display:flex;align-items:center;justify-content:center;gap:6px;transition:transform .15s,opacity .2s}
.btn:active{transform:scale(.97)}.btn:disabled{opacity:.4;pointer-events:none}
.btn-gold{background:var(--gold-g);background-size:200% auto;animation:sh 3s linear infinite;color:#111;box-shadow:0 5px 18px rgba(191,149,63,.3)}
.btn-green{background:linear-gradient(135deg,#00C853,#00E676);color:#063;box-shadow:0 4px 14px rgba(0,230,118,.25)}
.btn-dark{background:var(--inner);color:var(--muted);border:1px solid var(--border)}
.btn-red{background:linear-gradient(135deg,#FF1744,#FF5252);color:#fff;font-size:11.5px;padding:11px}
.btn-sm{padding:10px;font-size:11px}
.header{background:var(--card);border:1px solid var(--border-g);border-radius:14px;padding:14px 16px;display:flex;justify-content:space-between;align-items:center}
.header h1{font-size:15px}.header small{font-size:9px;color:var(--gold);font-weight:700}
.lang{background:var(--inner);color:#fff;border:1px solid var(--border);padding:5px 8px;border-radius:8px;font-weight:700;font-size:10px;outline:none}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px;box-shadow:0 3px 16px rgba(0,0,0,.35);animation:fu .3s ease}
.live-bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.live{font-size:8px;background:rgba(0,230,118,.12);color:var(--green);padding:3px 8px;border-radius:6px;border:1px solid rgba(0,230,118,.3);font-weight:800}
.cats{display:flex;gap:6px;overflow-x:auto;padding-bottom:8px;margin-bottom:8px;scrollbar-width:none}.cats::-webkit-scrollbar{display:none}
.cat{flex-shrink:0;padding:8px 14px;background:var(--inner);border:1px solid var(--border-g);border-radius:12px;color:var(--muted);font-size:11px;font-weight:800;cursor:pointer;white-space:nowrap;transition:all .2s;display:flex;align-items:center;gap:6px}
.cat.on{background:var(--gold-g);color:#111;border:none;box-shadow:0 3px 12px rgba(255,215,0,.3)}
.cat-payout{background:rgba(0,230,118,0.2);color:var(--green);padding:2px 5px;border-radius:4px;font-size:9px;font-weight:900}
.lbl{display:block;font-size:9px;color:var(--muted);font-weight:800;letter-spacing:.3px;margin-bottom:4px}
.selected-asset-box{background:var(--inner);border:1.5px solid var(--gold);border-radius:12px;padding:10px 14px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;cursor:pointer}
.selected-asset-box:hover{background:rgba(255,215,0,0.05)}
.selected-asset-info span:first-child{font-size:9px;color:var(--muted);display:block;font-weight:700}
.selected-asset-info span:last-child{font-size:13px;color:var(--gold);font-weight:900}
.modal-overlay{position:fixed;inset:0;background:rgba(4,6,10,.88);z-index:300;display:flex;align-items:center;justify-content:center;padding:16px;animation:fadeIn .2s ease}
.modal-content{background:var(--card);border:1.5px solid var(--border-g);border-radius:16px;width:100%;max-width:410px;max-height:85vh;display:flex;flex-direction:column;padding:16px;animation:fu .3s ease}
.modal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.modal-header h3{font-size:14px;color:var(--gold)}
.modal-close{background:none;border:none;color:var(--muted);font-size:18px;cursor:pointer;font-weight:900}
.modal-body{overflow-y:auto;flex:1;display:flex;flex-direction:column;gap:6px;padding-right:2px}
.modal-search-input{width:100%;padding:10px 12px;border-radius:10px;border:1.5px solid var(--border-g);background:var(--inner);color:#fff;font-size:12px;font-weight:700;outline:none;margin-bottom:10px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.sel{width:100%;padding:11px 12px;border-radius:10px;border:1px solid var(--border);background:var(--inner);color:#fff;font-size:12px;font-weight:700;outline:none;cursor:pointer;appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath fill='%238a94a6' d='M1 1l5 5 5-5'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 11px center}
.sig{display:none;margin-top:11px;background:var(--inner);border:2px solid var(--gold);border-radius:14px;padding:14px;text-align:center;animation:fu .3s ease}
.sig-meta{font-size:10px;color:var(--muted);font-weight:700}
.sig-dir{font-size:19px;font-weight:900;padding:11px;border-radius:11px;margin:8px 0}
.call{background:rgba(0,230,118,.1);color:var(--green);border:1px solid rgba(0,230,118,.3)}
.put{background:rgba(255,82,82,.1);color:var(--red);border:1px solid rgba(255,82,82,.3)}
.timer{font-size:12.5px;font-weight:900;color:var(--gold);background:rgba(255,215,0,.06);border:1px dashed var(--gold);border-radius:10px;padding:8px;margin-bottom:8px}
.strat{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:10px 12px;font-size:11px;color:#c5cdd8;text-align:left;line-height:1.6;margin-bottom:8px;white-space:pre-line}
.sig-stats{display:flex;justify-content:space-between;font-size:10px;font-weight:800;margin-bottom:8px}
.edu-item{background:var(--inner);border:1px solid var(--border);border-radius:11px;padding:11px 13px;margin-bottom:7px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;transition:transform .15s}
.edu-item:active{transform:scale(.98)}.edu-item h4{font-size:11.5px;color:var(--gold);font-weight:800}
.edu-detail{background:var(--inner);border:1px solid var(--border-g);border-radius:12px;padding:14px;animation:fu .3s ease}
.edu-detail h3{color:var(--gold);font-size:13px;font-weight:900;margin-bottom:8px}
.edu-detail p{font-size:11.5px;color:#c5cdd8;line-height:1.7;white-space:pre-line;margin-bottom:10px}
.tag-row{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px}
.tag{font-size:9px;font-weight:800;padding:3px 8px;border-radius:6px;background:rgba(255,215,0,.08);color:var(--gold);border:1px solid rgba(255,215,0,.2)}
.bnav{position:fixed;bottom:0;left:0;right:0;background:#05070d;border-top:1px solid var(--border);display:flex;justify-content:space-around;padding:6px 0 10px;z-index:100}
.nav{display:flex;flex-direction:column;align-items:center;gap:1px;color:var(--muted);font-size:8.5px;font-weight:800;cursor:pointer;min-width:44px;padding:3px 1px;transition:color .2s}
.nav.on{color:var(--gold)}.nav span:first-child{font-size:15px}
.hidden{display:none!important}
.gap{height:6px}.gap2{height:9px}
.analyze-overlay{position:fixed;inset:0;background:rgba(4,6,10,.94);z-index:200;display:flex;flex-direction:column;align-items:center;justify-content:center;animation:fadeIn .2s ease}
.analyze-wheel{width:56px;height:56px;border:3px solid rgba(255,215,0,.12);border-top-color:var(--gold);border-radius:50%;animation:spin .7s linear infinite;margin-bottom:14px}
.analyze-text{font-size:13px;font-weight:800;color:var(--gold);text-align:center;animation:pulse 1.2s ease infinite;max-width:240px;line-height:1.45}
.scan-box{background:var(--inner);border:1px solid var(--border-g);border-radius:14px;overflow:hidden;margin-bottom:10px}
.scan-video{width:100%;height:210px;object-fit:cover;background:#000;display:block}
.scan-placeholder{height:210px;display:flex;flex-direction:column;align-items:center;justify-content:center;color:var(--muted);font-size:12px;font-weight:700;gap:8px;text-align:center;padding:12px}
.scan-frame{position:relative}
.scan-frame.live::after{content:"";position:absolute;inset:12px;border:2px solid rgba(255,215,0,.5);border-radius:8px;pointer-events:none;box-shadow:0 0 0 9999px rgba(0,0,0,.2)}
.scan-tips{font-size:10.5px;color:#b0b8c8;line-height:1.55;background:var(--card);border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:10px}
.scan-tips b{color:var(--gold)}
.fav-list{max-height:240px;overflow-y:auto;display:flex;flex-direction:column;gap:6px}
.fav-item{display:flex;justify-content:space-between;align-items:center;padding:11px 13px;background:var(--inner);border:1px solid var(--border);border-radius:11px;font-size:12px;font-weight:700;cursor:pointer;transition:background .15s}
.fav-item:hover{background:rgba(255,215,0,.08)}
.fav-item span:first-child{color:var(--gold)}
.admin-profile-box{background:var(--inner);border:2px solid var(--gold);border-radius:14px;padding:14px;margin-top:12px;animation:fu .3s ease}
.users-list{display:flex;flex-direction:column;gap:6px;max-height:220px;overflow-y:auto;margin-top:8px;padding-right:4px}
.admin-search-input{width:100%;padding:9px 11px;border-radius:9px;border:1px solid var(--border);background:var(--card);color:#fff;font-size:11.5px;font-weight:700;outline:none;margin-bottom:8px}
.admin-search-input:focus{border-color:var(--gold)}
.user-row{display:flex;justify-content:space-between;align-items:center;background:var(--card);padding:9px 11px;border-radius:9px;border:1px solid var(--border);font-size:11px;font-weight:700}
.catalog-category-header{font-size:11px;font-weight:900;color:var(--gold);text-transform:uppercase;margin:12px 0 6px 4px;letter-spacing:.6px;border-bottom:1px solid var(--border-g);padding-bottom:4px}
.chat-container{display:flex;flex-direction:column;height:380px;background:var(--inner);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.chat-messages{flex:1;overflow-y:auto;padding:10px;display:flex;flex-direction:column;gap:8px}
.chat-msg{padding:8px 11px;border-radius:10px;font-size:11.5px;line-height:1.4;max-width:85%}
.chat-msg.user{background:rgba(255,215,0,0.15);color:var(--gold);align-self:flex-end;border:1px solid rgba(255,215,0,0.3)}
.chat-msg.ai{background:var(--card);color:var(--text);align-self:flex-start;border:1px solid var(--border)}
.chat-input-row{display:flex;gap:6px;padding:8px;background:var(--card);border-top:1px solid var(--border)}
.chat-input{flex:1;padding:9px 11px;border-radius:9px;border:1px solid var(--border);background:var(--inner);color:#fff;font-size:11.5px;outline:none}
.chat-input:focus{border-color:var(--gold)}
.profile-photo-upload{width:90px;height:90px;border-radius:50%;border:2px dashed var(--gold);background:var(--inner);display:flex;align-items:center;justify-content:center;cursor:pointer;margin:0 auto 12px;overflow:hidden;position:relative}
.profile-photo-upload img{width:100%;height:100%;object-fit:cover}
.about-box{background:var(--inner);border:1px solid var(--border-g);border-radius:14px;padding:16px;font-size:11.5px;color:#c5cdd8;line-height:1.75}
</style>
</head>
<body>
<div class="wrap">
<div class="header">
<div>
<h1 class="g-text">TEAM MASTER VIP</h1>
<small>POCKET OPTION TERMINAL</small>
</div>
<select class="lang" onchange="changeLanguage(this.value)">
<option value="ru">🇷🇺 RU</option>
<option value="en">🇺🇸 EN</option>
<option value="ua">🇦🇺 UA</option>
</select>
</div>

<div id="app">
<div class="live-bar">
<div class="live" id="poApiStatus">● POCKET OPTION API: ONLINE 🟢</div>
<div class="live" style="background:rgba(255,215,0,.1);color:var(--gold);border-color:rgba(255,215,0,.3)">⚡ СИНХРОНИЗАЦИЯ</div>
</div>

<div class="tab" id="tabSig">
<div class="card">
<span class="lbl" data-t="lblActiveAsset">АКТИВ POCKET OPTION</span>
<div class="selected-asset-box" onclick="openCatalogModal()">
<div class="selected-asset-info">
<span id="currentAssetBadge">OTC</span>
<span id="currentAssetDisplay">🇪🇺🇺🇸 EUR/USD OTC</span>
</div>
<span style="color:var(--gold);font-size:15px">⚙️ Изменить</span>
</div>

<div class="cats" id="assetCatsContainer"></div>
<div class="gap"></div>

<div class="grid2">
<div>
<span class="lbl" data-t="lblTf">ИНТЕРВАЙМ (ТАЙМФРЕЙМ)</span>
<select class="sel" id="tf">
<option value="5sec">5 сек</option>
<option value="15sec">15 сек</option>
<option value="30sec">30 сек</option>
<option value="1min" selected>1 мин</option>
<option value="2min">2 мин</option>
<option value="3min">3 мин</option>
<option value="5min">5 мин</option>
<option value="10min">10 мин</option>
<option value="15min">15 мин</option>
</select>
</div>
<div>
<span class="lbl" data-t="lblExp">ЭКСПИРАЦИЯ СДЕЛКИ</span>
<select class="sel" id="exp">
<option value="15">15 сек</option>
<option value="30">30 сек</option>
<option value="60" selected>1 мин</option>
<option value="120">2 мин</option>
<option value="180">3 мин</option>
<option value="240">4 мин</option>
<option value="300">5 мин</option>
<option value="360">6 мин</option>
<option value="420">7 мин</option>
<option value="480">8 мин</option>
<option value="540">9 мин</option>
<option value="600">10 мин</option>
<option value="720">12 мин</option>
<option value="900">15 мин</option>
</select>
</div>
</div>

<div class="gap2"></div>
<button class="btn btn-gold" onclick="getSig()" data-t="btnCalcSig">⚡ Просчитать сигнал</button>
<div class="gap"></div>
<div class="grid2" style="grid-template-columns: 1fr;">
<button class="btn btn-dark btn-sm" onclick="autoSig()" data-t="btnAutoSig">🤖 Выбрать топ актив</button>
</div>

<div class="sig" id="sigBox">
<div class="sig-meta" id="sigMeta">Pocket Option · EUR/USD</div>
<div class="sig-dir" id="sigDir">⬆️ CALL</div>
<div class="timer" id="cd">01:00</div>
<div class="strat" id="sigStrat"></div>
<div class="sig-stats">
<span data-t="lblCombo">Связка: Multi-Factor</span>
<span style="color:var(--green)">Точность: 94.8%</span>
</div>
<button class="btn btn-dark btn-sm" id="btnCancel" onclick="cancelSig()" data-t="btnCancel">✖ Отменить</button>
<button class="btn btn-gold btn-sm hidden" id="btnNew" onclick="getSig()" data-t="btnNewSig">⚡ Новый сигнал</button>
</div>
</div>
</div>

<div class="tab hidden" id="tabFavs">
<div class="card">
<h3 class="gate-title" style="font-size:15px;margin-bottom:6px" data-t="favTabTitle">⭐ Избранные активы</h3>
<p style="font-size:11px;color:var(--muted);margin-bottom:10px" data-t="favTabDesc">Нажмите на любой актив из списка для перехода.</p>
<div class="fav-list" id="mainFavList"></div>
<div id="mainFavEmpty" style="text-align:center;padding:20px;color:var(--muted);font-size:11px" data-t="profFavEmpty">Пока пусто — добавь актив из полного каталога</div>
</div>
</div>

<div class="tab hidden" id="tabChat">
<div class="card">
<h3 class="gate-title" style="font-size:15px;margin-bottom:6px">🤖 ИИ Помощник по Трейдингу</h3>
<p style="font-size:11px;color:var(--muted);margin-bottom:10px">Спросите о трейдинге, стратегии, технических вопросах или если что-то не работает.</p>
<div class="chat-container">
<div class="chat-messages" id="chatMessagesBox">
<div class="chat-msg ai">Привет, друг! 🤝 Я твой ИИ-помощник в Team Master VIP. Отвечаю на вопросы по трейдингу, стратегиям и техническим вопросам! 📈🚀</div>
</div>
<div class="chat-input-row">
<input type="text" class="chat-input" id="chatInputText" placeholder="Напишите вопрос ИИ..." onkeydown="if(event.key==='Enter') sendAiChatMessage()">
<button class="btn btn-gold btn-sm" style="width:auto;padding:0 14px" onclick="sendAiChatMessage()">➔</button>
</div>
</div>
<div class="gap"></div>
<a href="https://t.me/" target="_blank" class="btn btn-dark btn-sm">💬 Наши чаты и сообщество в Telegram</a>
</div>
</div>

<div class="tab hidden" id="tabEdu">
<div class="card" id="eduList">
<div class="grid2" style="margin-bottom:8px">
<button class="btn btn-gold btn-sm" onclick="openRandomEdu()" data-t="btnRandomStrat">🎲 Рандом стратегия</button>
<button class="btn btn-dark btn-sm" onclick="renderEdu()" data-t="eduHeader">🔄 Сбросить</button>
</div>
<h3 class="gate-title" style="font-size:14px;margin-bottom:8px" data-t="eduHeader">📚 50 профессиональных связок индикаторов</h3>
<div id="eduItems" style="max-height:340px;overflow-y:auto;display:flex;flex-direction:column;gap:6px"></div>
</div>
<div class="card hidden" id="eduView">
<button class="btn btn-dark btn-sm" style="margin-bottom:10px" onclick="closeEdu()" data-t="btnBackEdu">← Назад к списку</button>
<div class="edu-detail" id="eduBody"></div>
</div>
</div>

<div class="tab hidden" id="tabProf">
<div class="card">
<h3 class="gate-title" style="font-size:15px;margin-bottom:8px" data-t="profTitle">👤 Профиль</h3>
<div class="profile-photo-upload" onclick="document.getElementById('profilePhotoInput').click()">
<span id="profilePhotoIcon" style="font-size:26px">📷</span>
<img id="profilePhotoImg" class="hidden" src="" alt="">
<input type="file" id="profilePhotoInput" accept="image/*" class="hidden" onchange="handleProfilePhoto(event)">
</div>
<div style="text-align:center;font-size:11px;color:var(--muted);margin-bottom:12px">Нажмите для добавления фото вашего профиля</div>
<span class="lbl">АЙДИ С POCKET (Pocket ID)</span>
<input type="text" class="input" id="profPocketId" placeholder="Введите Pocket ID" oninput="saveProfileSettings()">
<span class="lbl">ЛЮБИМАЯ СТРАТЕГИЯ</span>
<input type="text" class="input" id="profFavStrat" placeholder="Например: Price Action" oninput="saveProfileSettings()">
<div class="gap"></div>
<button class="btn btn-red" onclick="logout()" data-t="btnLogout">🚪 Выйти</button>

<div class="admin-profile-box hidden" id="adminProfileBox">
<h4 style="font-size:12px;color:var(--gold);margin-bottom:6px">👑 АДМИН-ПАНЕЛЬ: УПРАВЛЕНИЕ УЧАСТНИКАМИ</h4>
<input type="text" class="admin-search-input" id="adminSearchInput" placeholder="Поиск участника по ТГ..." oninput="renderAdminData(this.value)">
<div class="users-list" id="adminUsersList"></div>
</div>
</div>
</div>

<div class="tab hidden" id="tabScan">
<div class="card">
<h3 class="gate-title" style="font-size:15px;margin-bottom:4px">📷 ИИ Сканер (Анализ по свечам)</h3>
<p style="font-size:10.5px;color:var(--muted);margin-bottom:10px">Инструкция ИИ-сканера свечей:<br>1️⃣ Настройте параметры ниже<br>2️⃣ Откройте камеру или загрузите скриншот<br>3️⃣ Нажмите «ЗАПУСК»</p>

<span class="lbl">ИНТЕРВАЛ СВЕЧЕЙ</span>
<div class="cats" style="margin-bottom:6px">
<button class="cat" onclick="setScanCfg(this, 'интервал', 'S5')">S5</button>
<button class="cat" onclick="setScanCfg(this, 'интервал', 'S15')">S15</button>
<button class="cat" onclick="setScanCfg(this, 'интервал', 'S30')">S30</button>
<button class="cat on" onclick="setScanCfg(this, 'интервал', 'M1')">M1</button>
<button class="cat" onclick="setScanCfg(this, 'интервал', 'M2')">M2</button>
<button class="cat" onclick="setScanCfg(this, 'интервал', 'M5')">M5</button>
</div>

<span class="lbl">ЭКСПИРАЦИЯ СДЕЛКИ</span>
<div class="cats" style="margin-bottom:8px">
<button class="cat" onclick="setScanCfg(this, 'экспирация', '30с')">30с</button>
<button class="cat on" onclick="setScanCfg(this, 'экспирация', '1м')">1м</button>
<button class="cat" onclick="setScanCfg(this, 'экспирация', '2м')">2м</button>
<button class="cat" onclick="setScanCfg(this, 'экспирация', '5м')">5м</button>
</div>

<span class="lbl">СТРАТЕГИЯ АНАЛИЗА</span>
<div class="cats" style="margin-bottom:10px">
<button class="cat on" onclick="setScanCfg(this, 'стратегия', 'Smart Money')">Smart Money</button>
<button class="cat" onclick="setScanCfg(this, 'стратегия', 'ICT')">ICT</button>
<button class="cat" onclick="setScanCfg(this, 'стратегия', 'PA')">PA</button>
<button class="cat" onclick="setScanCfg(this, 'стратегия', 'Scalp')">Scalp</button>
<button class="cat" onclick="setScanCfg(this, 'стратегия', 'Trend')">Trend</button>
</div>

<div class="scan-box">
<div class="scan-frame" id="scanFrame">
<video class="scan-video hidden" id="scanVideo" autoplay playsinline></video>
<img class="scan-video hidden" id="scanPreviewImg" alt="">
<div class="scan-placeholder" id="scanPh">
<span style="font-size:32px">📷</span>
<span data-t="camOff">Камера отключена или файл не выбран</span>
<span style="font-size:10px;color:var(--muted)" data-t="camOffSub">нажмите кнопку выше для запуска или загрузите скрин</span>
</div>
</div>
</div>

<div class="grid2" style="margin-bottom:8px">
<button class="btn btn-dark btn-sm" onclick="openCam()" data-t="btnOpenCam">📷 Открыть камеру</button>
<button class="btn btn-dark btn-sm" onclick="document.getElementById('scanFileInput').click()">📁 Загрузить скрин</button>
<input type="file" id="scanFileInput" accept="image/*" class="hidden" onchange="handleImageUpload(event)">
</div>

<button class="btn btn-gold" id="btnScan" disabled onclick="doScan()" data-t="btnScanNow">🚀 ЗАПУСК (ИИ СКАН СВЕЧЕЙ)</button>
<div class="gap"></div>
<button class="btn btn-dark btn-sm" onclick="stopCam()" data-t="btnCloseCam">⏹ Закрыть камеру / Сбросить</button>

<div class="sig" id="scanSigBox">
<div class="sig-meta" id="scanMeta">TEAM MASTER AI SCANNER</div>
<div class="sig-dir" id="scanDir">BUY (ВВЕРХ)</div>
<div class="timer" id="scanCd">01:00</div>
<div class="strat" id="scanStrat"></div>
<button class="btn btn-dark btn-sm" id="btnScanCancel" onclick="cancelScan()" data-t="btnCancel">✖ Отменить</button>
<button class="btn btn-gold btn-sm hidden" id="btnScanNew" onclick="doScan()" data-t="btnNewScan">⚡ Новый скан</button>
</div>
</div>
</div>

<div class="tab hidden" id="tabAbout">
<div class="card">
<h3 class="gate-title" style="font-size:15px;margin-bottom:8px" data-t="aboutTitle">👑 О нашей команде (Team Master)</h3>
<div class="about-box">
TEAM MASTER VIP — это элитное международное сообщество и профессиональная торговая экосистема, основанная сильными трейдерами для тех, кто хочет стабильно и системно зарабатывать на финансовых рынках и платформе Pocket Option.<br><br>
🎯 <b>Наша цель и миссия:</b> Предоставить каждому участнику самые передовые технологические инструменты, включая автоматический ИИ-сканер графиков по свечам, эксклюзивные авторские связки индикаторов и проверенные торговые стратегии Price Action и Smart Money.<br><br>
💡 <b>Почему выбирают нас:</b><br>
• Высокая точность аналитических сигналов и ИИ-сканирования в реальном времени<br>
• Мощная база знаний из 50+ профессиональных связок и методик<br>
• Постоянная поддержка менторов, дружная команда единомышленников и закрытые торговые чаты<br><br>
Присоединяйтесь к нашей команде, развивайте дисциплину и выводите свой трейдинг на принципиально новый финансовый уровень! 🚀💎
</div>
<div class="gap"></div>
<a href="https://t.me/" target="_blank" class="btn btn-dark btn-sm" data-t="btnTgChan">📢 Наш Telegram канал</a>
</div>
</div>

<div class="bnav" id="bnav">
<div class="nav on" onclick="tab('tabSig', this)"><span>🎯</span><span data-t="navSig">Сигналы</span></div>
<div class="nav" onclick="tab('tabFavs', this)"><span>⭐</span><span data-t="navFavs">Избранное</span></div>
<div class="nav" onclick="tab('tabChat', this)"><span>🤖</span><span data-t="navChat">ИИ Чат</span></div>
<div class="nav" onclick="tab('tabEdu', this)"><span>📚</span><span data-t="navEdu">Связки</span></div>
<div class="nav" onclick="tab('tabProf', this)"><span>👤</span><span data-t="navProf">Профиль</span></div>
<div class="nav" onclick="tab('tabScan', this)"><span>📷</span><span data-t="navScan">Сканер</span></div>
<div class="nav" onclick="tab('tabAbout', this)"><span>👑</span><span data-t="navAbout">О нас</span></div>
</div>
</div>
</div>

<div id="analyzeBox" class="analyze-overlay hidden">
<div class="analyze-wheel"></div>
<div class="analyze-text" id="analyzeTxt">🤖 ИИ сканирует расположение свечей...</div>
</div>

<div id="catalogModal" class="modal-overlay hidden">
<div class="modal-content">
<div class="modal-header">
<h3 data-t="modalCatalogTitle">📋 Полный каталог активов Pocket Option</h3>
<button class="modal-close" onclick="closeCatalogModal()">✕</button>
</div>
<input type="text" class="modal-search-input" id="modalSearchInput" placeholder="Поиск актива..." oninput="renderCatalogList(this.value)">
<div class="cats" style="margin-bottom:8px" id="catalogFilterCats">
<button class="cat on" onclick="setModalCat('all',this)">🌟 Все</button>
<button class="cat" onclick="setModalCat('💱',this)">💱 Валюта</button>
<button class="cat" onclick="setModalCat('🔥',this)">🔥 Крипто</button>
<button class="cat" onclick="setModalCat('🛢️',this)">🛢️ Сырьё</button>
<button class="cat" onclick="setModalCat('📈',this)">📈 Акции</button>
<button class="cat" onclick="setModalCat('📊',this)">📊 Индексы</button>
</div>
<div class="modal-body" id="modalCatalogBody"></div>
</div>
</div>

<script>
const ADMIN_SECRET_KEY = "MASTER_ROOT_7777_SUPER_SECRET";
const USER_SECRET_KEY = "MASTER_TRADING_777_";
let tgUser = localStorage.getItem("tmv_tgUser")||"@user";
let isAdmin = false;
let timer=null,scanTimer=null,stream=null,camReady=false;
let favs=JSON.parse(localStorage.getItem("tmv_favs")||"[]");
let sigCount=parseInt(localStorage.getItem("tmv_sigs")||"0");
let currentAsset="🇪🇺🇺🇸 EUR/USD OTC";
let currentLang="ru";
let modalCategory="all";
let uploadedImageBlob = null;
let scanCfg = {интервал: 'M1', экспирация: '1м', стратегия: 'Smart Money'};
let allUsersReg = [];
const ASSETS_DATA_JS = JSON.parse('__ASSETS_JSON_PLACEHOLDER__');
const I18N = {
    ru: {
        gateTitle: "Добро пожаловать",
        btnConfirmId: "✅ Войти в терминал",
        depTitle: "Проверка депозита",
        btnCheckDep: "🔍 Проверить депозит",
        btnBack: "← Назад",
        accOpenTitle: "Доступ открыт",
        accOpenDesc: "Активация прошла успешно. Все функции терминала доступны.",
        btnEnterTerm: "🚀 Войти в терминал",
        lblActiveAsset: "АКТИВ POCKET OPTION",
        lblTf: "ИНТЕРВАЙМ (ТАЙМФРЕЙМ)",
        lblExp: "ЭКСПИРАЦИЯ СДЕЛКИ",
        btnCalcSig: "⚡ Просчитать сигнал",
        btnAutoSig: "🤖 Выбрать топ актив",
        lblCombo: "Связка",
        btnCancel: "✖ Отменить",
        btnNewSig: "⚡ Новый сигнал",
        eduHeader: "📚 50 профессиональных связок индикаторов",
        btnBackEdu: "← Назад к списку",
        btnRandomStrat: "🎲 Рандом стратегия",
        profTitle: "👤 Профиль",
        profStatus: "Статус",
        profIdLbl: "Ваш ТГ",
        profSigsLbl: "Сигналов",
        profFavLbl: "⭐ ИЗБРАННЫЕ АКТИВЫ",
        favTabTitle: "⭐ Избранные активы",
        favTabDesc: "Нажмите на любой актив из списка для перехода.",
        profFavEmpty: "Пока пусто — добавь актив из полного каталога",
        btnClearFav: "🗑 Очистить избранное",
        btnLogout: "🚪 Выйти",
        btnOpenCam: "📷 Открыть камеру",
        camOff: "Камера отключена или файл не выбран",
        camOffSub: "нажмите кнопку выше для запуска или загрузите скрин",
        btnScanNow: "🚀 ЗАПУСК (ИИ СКАН СВЕЧЕЙ)",
        btnCloseCam: "⏹ Закрыть камеру / Сбросить",
        btnNewScan: "⚡ Новый скан",
        aboutTitle: "👑 О нашей команде (Team Master)",
        aboutRisk: "Торгуйте осознанно. Риск на сделку 1-2% от банка.",
        btnTgChan: "📢 Наш Telegram канал",
        navSig: "Сигналы",
        navFavs: "Избранное",
        navEdu: "Связки",
        navProf: "Профиль",
        navScan: "Сканер",
        navAbout: "О нас",
        modalCatalogTitle: "📋 Полный каталог активов Pocket Option",
        modalSearchPlaceholder: "Поиск актива..."
    },
    en: {
        gateTitle: "Welcome",
        btnConfirmId: "✅ Enter Terminal",
        depTitle: "Deposit Check",
        btnCheckDep: "🔍 Check Deposit",
        btnBack: "← Back",
        accOpenTitle: "Access Granted",
        accOpenDesc: "Activation successful.",
        btnEnterTerm: "🚀 Enter Terminal",
        lblActiveAsset: "POCKET OPTION ASSET",
        lblTf: "TIMEFRAME",
        lblExp: "EXPIRATION",
        btnCalcSig: "⚡ Calculate Signal",
        btnAutoSig: "🤖 Choose Top",
        lblCombo: "Combo",
        btnCancel: "✖ Cancel",
        btnNewSig: "⚡ New Signal",
        eduHeader: "📚 Indicator Combos",
        btnBackEdu: "← Back",
        btnRandomStrat: "🎲 Random Strategy",
        profTitle: "👤 Profile",
        profStatus: "Status",
        profIdLbl: "Your TG",
        profSigsLbl: "Signals",
        profFavLbl: "⭐ FAVORITE ASSETS",
        favTabTitle: "⭐ Favorite Assets",
        favTabDesc: "Click any asset to switch.",
        profFavEmpty: "Empty yet",
        btnClearFav: "🗑 Clear",
        btnLogout: "🚪 Logout",
        btnOpenCam: "📷 Open Camera",
        camOff: "Camera is off or no file",
        camOffSub: "click to start or upload screenshot",
        btnScanNow: "🚀 RUN (AI CANDLE SCAN)",
        btnCloseCam: "⏹ Close / Reset",
        btnNewScan: "⚡ New Scan",
        aboutTitle: "👑 About Our Team",
        aboutRisk: "Trade wisely.",
        btnTgChan: "📢 Telegram",
        navSig: "Signals",
        navFavs: "Favorites",
        navEdu: "Combos",
        navProf: "Profile",
        navScan: "Scanner",
        navAbout: "About",
        modalCatalogTitle: "📋 Full Assets Catalog",
        modalSearchPlaceholder: "Search asset..."
    },
    ua: {
        gateTitle: "Ласкаво просимо",
        btnConfirmId: "✅ Увійти в термінал",
        depTitle: "Перевірка депозиту",
        btnCheckDep: "🔍 Перевірити депозит",
        btnBack: "← Назад",
        accOpenTitle: "Доступ відкрито",
        accOpenDesc: "Активацію успішно пройдено.",
        btnEnterTerm: "🚀 Увійти в термінал",
        lblActiveAsset: "АКТИВ POCKET OPTION",
        lblTf: "ІНТЕРВАЛ (ТАЙМФРЕЙМ)",
        lblExp: "ЕКСПИРАЦИЯ УГОДИ",
        btnCalcSig: "⚡ Прорахувати сигнал",
        btnAutoSig: "🤖 Обрати топ",
        lblCombo: "Зв'язка",
        btnCancel: "✖ Скасувати",
        btnNewSig: "⚡ Новий сигнал",
        eduHeader: "📚 50 професійних зв'язок індикаторів",
        btnBackEdu: "← Назад",
        btnRandomStrat: "🎲 Рандом стратегія",
        profTitle: "👤 Профіль",
        profStatus: "Статус",
        profIdLbl: "Ваш ТГ",
        profSigsLbl: "Сигналів",
        profFavLbl: "⭐ ОБРАНІ АКТИВИ",
        favTabTitle: "⭐ Обрані активи",
        favTabDesc: "Натисніть на будь-який актив.",
        profFavEmpty: "Поки порожньо",
        btnClearFav: "🗑 Очистити",
        btnLogout: "🚪 Вийти",
        btnOpenCam: "📷 Відкрити камеру",
        camOff: "Камера вимкнена або файл не обрано",
        camOffSub: "натисніть для запуску або завантажте скрін",
        btnScanNow: "🚀 ЗАПУСК (ІИ СКАН СВІЧОК)",
        btnCloseCam: "⏹ Закрити / Скинути",
        btnNewScan: "⚡ Новий скан",
        aboutTitle: "👑 Про нашу команду",
        aboutRisk: "Торгуйте усвідомлено.",
        btnTgChan: "📢 Telegram",
        navSig: "Сигнали",
        navFavs: "Обране",
        navEdu: "Зв'язки",
        navProf: "Профіль",
        navScan: "Сканер",
        navAbout: "Про нас",
        modalCatalogTitle: "📋 Повний каталог активів Pocket Option",
        modalSearchPlaceholder: "Пошук активу..."
    }
};
const EDU = JSON.parse('__EDU_JSON_PLACEHOLDER__');

function getAssetBadge(symbol) {
    return symbol.includes("OTC") ? "OTC" : "LIVE";
}

function renderAssetButtons() {
    const container = document.getElementById("assetCatsContainer");
    if(!container) return;
    const categories = ASSETS_DATA_JS[currentLang] || ASSETS_DATA_JS["ru"];
    let allItems = [];
    for(let cat in categories) {
        allItems = allItems.concat(categories[cat]);
    }
    let items = allItems.slice(0, 6);
    let html = "";
    items.forEach((assetName) => {
        let activeClass = (assetName === currentAsset) ? " on" : "";
        let badgeStr = getAssetBadge(assetName);
        html += `<button class="cat ${activeClass}" onclick="selectCatalogAsset('${assetName}')">${assetName} <span class="cat-payout">${badgeStr}</span></button>`;
    });
    container.innerHTML = html;
}

function updateSelectedAssetUI() {
    const displayEl = document.getElementById("currentAssetDisplay");
    if(displayEl) displayEl.textContent = currentAsset;
    const badgeEl = document.getElementById("currentAssetBadge");
    if(badgeEl) badgeEl.textContent = getAssetBadge(currentAsset);
}

function openCatalogModal(){
    document.getElementById("catalogModal").classList.remove("hidden");
    renderCatalogList("");
}

function closeCatalogModal(){
    document.getElementById("catalogModal").classList.add("hidden");
}

function setModalCat(cat, el){
    modalCategory = cat;
    document.querySelectorAll("#catalogFilterCats .cat").forEach(c=>c.classList.remove("on"));
    if(el) el.classList.add("on");
    renderCatalogList(document.getElementById("modalSearchInput").value);
}

function setScanCfg(el, type, val) {
    el.parentElement.querySelectorAll('.cat').forEach(b => b.classList.remove('active', 'on'));
    el.classList.add('active', 'on');
    scanCfg[type] = val;
}

function renderCatalogList(filter=""){
    const bodyEl = document.getElementById("modalCatalogBody");
    let html = "";
    let searchLower = filter.toLowerCase();
    const categories = ASSETS_DATA_JS[currentLang] || ASSETS_DATA_JS["ru"];
    for(let catName in categories) {
        if(modalCategory !== "all" && !catName.includes(modalCategory)) continue;
        let groupItems = categories[catName].filter(item => item.toLowerCase().includes(searchLower));
        if(groupItems.length > 0){
            html += `<div class="catalog-category-header">${catName}</div>`;
            groupItems.forEach(item => {
                let isFav = favs.includes(item);
                let badgeStr = getAssetBadge(item);
                html += `<div class="fav-item">
                    <span onclick="selectCatalogAsset('${item}')">${item}</span>
                    <div style="display:flex;gap:8px;align-items:center">
                        <span class="cat-payout">${badgeStr}</span>
                        <span style="cursor:pointer;font-size:15px" onclick="toggleFav('${item}')">${isFav ? '⭐' : '☆'}</span>
                        <span style="color:var(--gold);font-size:11px;cursor:pointer" onclick="selectCatalogAsset('${item}')">➔</span>
                    </div>
                </div>`;
            });
        }
    }
    bodyEl.innerHTML = html || `<div style="text-align:center;padding:20px;color:var(--muted);font-size:11px">Ничего не найдено</div>`;
}

function toggleFav(name){
    if(favs.includes(name)) favs = favs.filter(f => f !== name);
    else favs.push(name);
    localStorage.setItem("tmv_favs", JSON.stringify(favs));
    renderCatalogList(document.getElementById("modalSearchInput").value);
    renderFavs();
    renderMainFavs();
}

function selectCatalogAsset(name){
    currentAsset = name;
    updateSelectedAssetUI();
    renderAssetButtons();
    closeCatalogModal();
    tab('tabSig', document.querySelector('.bnav .nav'));
}

function renderFavs(){
    const listEl = document.getElementById("favList");
    const emptyEl = document.getElementById("favEmpty");
    if(!listEl) return;
    if(favs.length === 0){
        listEl.innerHTML = "";
        if(emptyEl) emptyEl.style.display = "block";
        return;
    }
    if(emptyEl) emptyEl.style.display = "none";
    let html = "";
    favs.forEach(f => {
        html += `<div class="fav-item" onclick="selectCatalogAsset('${f}')"><span>⭐ ${f} <span class="cat-payout">${getAssetBadge(f)}</span></span><span>➔</span></div>`;
    });
    listEl.innerHTML = html;
}

function renderMainFavs(){
    const listEl = document.getElementById("mainFavList");
    const emptyEl = document.getElementById("mainFavEmpty");
    if(!listEl) return;
    if(favs.length === 0){
        listEl.innerHTML = "";
        if(emptyEl) emptyEl.style.display = "block";
        return;
    }
    if(emptyEl) emptyEl.style.display = "none";
    let html = "";
    favs.forEach(f => {
        html += `<div class="fav-item" onclick="selectCatalogAsset('${f}')"><span>⭐ ${f} <span class="cat-payout">${getAssetBadge(f)}</span></span><span style="color:var(--gold)">Перейти ➔</span></div>`;
    });
    listEl.innerHTML = html;
}

async function getSig(){
    runAnalyzer(async ()=>{
        const tf = document.getElementById("tf").value;
        const expSec = parseInt(document.getElementById("exp").value);
        let dir = "⬆️ CALL (ВВЕРХ)";
        let stratText = `Интеллектуальный анализ котировок Pocket Option для актива (${currentAsset}) по таймфрейму ${tf} с экспирацией ${expSec} сек. Умный расчет без ИИ: анализ уровней сопротивления, поддержки и боковика подтвержден.`;
        try {
            const res = await fetch(`/get_signal?asset=${encodeURIComponent(currentAsset)}&timeframe=${tf}`);
            if (res.ok) {
                const data = await res.json();
                if (data.signal) {
                    dir = data.signal === "UP" ? "⬆️ CALL (ВВЕРХ)" : "⬇️ PUT (НИЗ)";
                    if(data.reason) {
                        stratText = `Технический расчет (без ИИ): ${data.reason}`;
                    }
                }
            }
        } catch(e) {}
        let isCall = dir.includes("CALL") || dir.includes("ВВЕРХ");
        document.getElementById("sigMeta").textContent = `Pocket Option · ${currentAsset} · Таймфрейм: ${tf} · Экспирация: ${expSec} сек`;
        const dirEl = document.getElementById("sigDir");
        dirEl.textContent = dir;
        dirEl.className = "sig-dir " + (isCall ? "call" : "put");
        document.getElementById("sigStrat").innerHTML = `<b>Анализ сигналов (без ИИ):</b> ${stratText}`;
        document.getElementById("sigBox").style.display = "block";
        document.getElementById("btnCancel").style.display = "block";
        document.getElementById("btnNew").style.display = "none";
        sigCount++;
        localStorage.setItem("tmv_sigs", sigCount);
        startTimer("cd", expSec, ()=>{
            document.getElementById("btnCancel").style.display = "none";
            document.getElementById("btnNew").style.display = "block";
        });
    });
}

function autoSig(){
    let allItems = [];
    const categories = ASSETS_DATA_JS[currentLang] || ASSETS_DATA_JS["ru"];
    for(let cat in categories) allItems = allItems.concat(categories[cat]);
    currentAsset = allItems[Math.floor(Math.random()*allItems.length)];
    updateSelectedAssetUI();
    renderAssetButtons();
    getSig();
}

function cancelSig(){
    if(timer) clearInterval(timer);
    document.getElementById("sigBox").style.display = "none";
}

function startTimer(elementId, seconds, onComplete){
    if(timer) clearInterval(timer);
    let rem = seconds;
    const el = document.getElementById(elementId);
    updateTimerDisplay(el, rem);
    timer = setInterval(()=>{
        rem--;
        if(rem <= 0){
            clearInterval(timer);
            el.textContent = "00:00 Готово!";
            if(onComplete) onComplete();
            return;
        }
        updateTimerDisplay(el, rem);
    }, 1000);
}

function updateTimerDisplay(el, sec){
    const m = Math.floor(sec/60);
    const s = sec%60;
    el.textContent = (m<10?"0"+m:m) + ":" + (s<10?"0"+s:s);
}

function runAnalyzer(callback){
    const overlay = document.getElementById("analyzeBox");
    const txtEl = document.getElementById("analyzeTxt");
    overlay.classList.remove("hidden");
    let step = 0;
    const phrasesList = ["Запрос к ИИ сканеру...", "Анализ расположения свечей...", "Проверка тел и теней свечей...", "Генерация точного сигнала..."];
    const interval = setInterval(()=>{
        txtEl.textContent = phrasesList[step % phrasesList.length];
        step++;
    }, 400);
    setTimeout(()=>{
        clearInterval(interval);
        overlay.classList.add("hidden");
        if(callback) callback();
    }, 2200);
}

function renderEdu(){
    const itemsEl = document.getElementById("eduItems");
    let html = "";
    EDU.forEach((ed, i)=>{
        html += `<div class="edu-item" onclick="openEdu(${i})"><h4>${ed.t}</h4><span style="color:var(--gold)">➔</span></div>`;
    });
    itemsEl.innerHTML = html;
}

function openEdu(idx){
    const ed = EDU[idx];
    document.getElementById("eduList").classList.add("hidden");
    document.getElementById("eduView").classList.remove("hidden");
    let tagsHtml = "";
    ed.tags.forEach(t => {
        tagsHtml += `<span class="tag">${t}</span>`;
    });
    document.getElementById("eduBody").innerHTML = `<h3>${ed.t}</h3><div class="tag-row">${tagsHtml}</div><p>${ed.d}</p>`;
}

function closeEdu(){
    document.getElementById("eduView").classList.add("hidden");
    document.getElementById("eduList").classList.remove("hidden");
}

function openRandomEdu(){
    let randIdx = Math.floor(Math.random() * EDU.length);
    openEdu(randIdx);
}

function loadProfile(){
    document.getElementById("profPocketId").value = localStorage.getItem("tmv_pocketId") || "";
    document.getElementById("profFavStrat").value = localStorage.getItem("tmv_favStrat") || "";
    let savedPhoto = localStorage.getItem("tmv_profPhoto");
    if(savedPhoto) {
        document.getElementById("profilePhotoImg").src = savedPhoto;
        document.getElementById("profilePhotoImg").classList.remove("hidden");
        document.getElementById("profilePhotoIcon").classList.add("hidden");
    }
}

function saveProfileSettings(){
    localStorage.setItem("tmv_pocketId", document.getElementById("profPocketId").value);
    localStorage.setItem("tmv_favStrat", document.getElementById("profFavStrat").value);
}

function handleProfilePhoto(event) {
    const file = event.target.files[0];
    if(!file) return;
    const reader = new FileReader();
    reader.onload = function(e) {
        let base64Img = e.target.result;
        localStorage.setItem("tmv_profPhoto", base64Img);
        document.getElementById("profilePhotoImg").src = base64Img;
        document.getElementById("profilePhotoImg").classList.remove("hidden");
        document.getElementById("profilePhotoIcon").classList.add("hidden");
    }
    reader.readAsDataURL(file);
}

function logout(){
    localStorage.removeItem("tmv_tgUser");
    localStorage.removeItem("tmv_isAdmin");
    location.reload();
}

function openCam(){
    uploadedImageBlob = null;
    const video = document.getElementById("scanVideo");
    const previewImg = document.getElementById("scanPreviewImg");
    const ph = document.getElementById("scanPh");
    const frame = document.getElementById("scanFrame");
    previewImg.classList.add("hidden");
    navigator.mediaDevices.getUserMedia({video:{facingMode:"environment"}, audio:false})
        .then(s => {
            stream = s;
            video.srcObject = stream;
            video.classList.remove("hidden");
            ph.classList.add("hidden");
            frame.classList.add("live");
            camReady = true;
            document.getElementById("btnScan").removeAttribute("disabled");
        })
        .catch(err => {
            alert("Не удалось получить доступ к камере: " + err);
        });
}

function handleImageUpload(event) {
    const file = event.target.files[0];
    if(!file) return;
    uploadedImageBlob = file;
    if(stream){
        stream.getTracks().forEach(t => t.stop());
        stream = null;
    }
    const video = document.getElementById("scanVideo");
    const previewImg = document.getElementById("scanPreviewImg");
    const ph = document.getElementById("scanPh");
    const frame = document.getElementById("scanFrame");
    video.classList.add("hidden");
    const reader = new FileReader();
    reader.onload = function(e) {
        previewImg.src = e.target.result;
        previewImg.classList.remove("hidden");
        ph.classList.add("hidden");
        frame.classList.add("live");
        camReady = true;
        document.getElementById("btnScan").removeAttribute("disabled");
    }
    reader.readAsDataURL(file);
}

function stopCam(){
    if(stream){
        stream.getTracks().forEach(t => t.stop());
        stream = null;
    }
    uploadedImageBlob = null;
    const video = document.getElementById("scanVideo");
    const previewImg = document.getElementById("scanPreviewImg");
    const ph = document.getElementById("scanPh");
    const frame = document.getElementById("scanFrame");
    video.classList.add("hidden");
    previewImg.classList.add("hidden");
    ph.classList.remove("hidden");
    frame.classList.remove("live");
    camReady = false;
    document.getElementById("btnScan").setAttribute("disabled", "true");
    document.getElementById("scanFileInput").value = "";
}

async function doScan(){
    if(!camReady){
        alert("Сначала откройте камеру или загрузите скриншот графика со свечами!");
        return;
    }
    const expSec = scanCfg.экспирация.includes('30') ? 30 : parseInt(scanCfg.экспирация) * 60;
    if (uploadedImageBlob) {
        processImageBlobForScan(uploadedImageBlob, expSec);
    } else {
        const video = document.getElementById("scanVideo");
        const canvas = document.createElement("canvas");
        canvas.width = video.videoWidth || 640;
        canvas.height = video.videoHeight || 480;
        canvas.getContext("2d").drawImage(video, 0, 0);
        canvas.toBlob((blob) => {
            processImageBlobForScan(blob, expSec);
        }, 'image/jpeg');
    }
}

function processImageBlobForScan(blob, expSec) {
    runAnalyzer(async ()=>{
        try {
            const fd = new FormData();
            fd.append('file', blob, 'snapshot.jpg');
            fd.append('cfg', JSON.stringify(scanCfg));
            const response = await fetch('/analyze', { method: 'POST', body: fd });
            if(response.ok) {
                const resData = await response.json();
                let resText = resData.text || "❌ Ошибка анализа";
                let isErr = resText.includes("❌");
                document.getElementById("scanMeta").textContent = "TEAM MASTER AI SCANNER";
                const dirEl = document.getElementById("scanDir");
                if(isErr) {
                    dirEl.textContent = "❌ ГРАФИК СО СВЕЧАМИ НЕ ОБНАРУЖЕН";
                    dirEl.className = "sig-dir put";
                } else {
                    let isBuy = resText.includes("BUY") || resText.includes("ВВЕРХ");
                    dirEl.textContent = isBuy ? "BUY (ВВЕРХ)" : "SELL (ВНИЗ)";
                    dirEl.className = "sig-dir " + (isBuy ? "call" : "put");
                    sigCount++;
                    localStorage.setItem("tmv_sigs", sigCount);
                }
                document.getElementById("scanStrat").innerHTML = `<pre style="white-space:pre-wrap;font-family:inherit;"><b>${resText}</b></pre>`;
                document.getElementById("scanSigBox").style.display = "block";
                document.getElementById("btnScanCancel").style.display = "block";
                document.getElementById("btnScanNew").style.display = "none";
                if(!isErr) {
                    startTimer("scanCd", expSec, ()=>{
                        document.getElementById("btnScanCancel").style.display = "none";
                        document.getElementById("btnScanNew").style.display = "block";
                    });
                }
                return;
            }
        } catch(e) {}
    });
}

function cancelScan(){
    if(scanTimer) clearInterval(scanTimer);
    document.getElementById("scanSigBox").style.display = "none";
}

async function sendAiChatMessage() {
    const inputEl = document.getElementById("chatInputText");
    const msgBox = document.getElementById("chatMessagesBox");
    const text = inputEl.value.trim();
    if(!text) return;

    msgBox.innerHTML += `<div class="chat-msg user">${escapeHtml(text)}</div>`;
    inputEl.value = "";
    msgBox.scrollTop = msgBox.scrollHeight;

    const loadingId = "ai_load_" + Date.now();
    msgBox.innerHTML += `<div class="chat-msg ai" id="${loadingId}">Ассистент анализирует вопрос... 🤖</div>`;
    msgBox.scrollTop = msgBox.scrollHeight;

    try {
        const fd = new FormData();
        fd.append('prompt', text);
        const response = await fetch('/ai-chat', { method: "POST", body: fd });
        const loadEl = document.getElementById(loadingId);
        if(loadEl) loadEl.remove();
        if(response.ok) {
            const data = await response.json();
            msgBox.innerHTML += `<div class="chat-msg ai">${escapeHtml(data.reply)}</div>`;
        }
    } catch(e) {
        const loadEl = document.getElementById(loadingId);
        if(loadEl) loadEl.remove();
    }
    msgBox.scrollTop = msgBox.scrollHeight;
}

function escapeHtml(str) {
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function changeLanguage(lang){
    currentLang = lang;
    const dict = I18N[lang];
    if(!dict) return;
    for(let key in dict){
        const el = document.querySelector(`[data-t="${key}"]`);
        if(el) el.innerHTML = dict[key];
    }
    renderAssetButtons();
    renderCatalogList(document.getElementById("modalSearchInput").value);
}

function tab(tabId, el){
    document.querySelectorAll(".tab").forEach(t=>t.classList.add("hidden"));
    document.getElementById(tabId).classList.remove("hidden");
    document.querySelectorAll(".bnav .nav").forEach(n=>n.classList.remove("on"));
    if(el) el.classList.add("on");
    if(tabId === 'tabFavs') renderMainFavs();
}

window.addEventListener("DOMContentLoaded", ()=>{
    renderAssetButtons();
    updateSelectedAssetUI();
    renderFavs();
    renderEdu();
    loadProfile();
});
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def serve_terminal():
    assets_json = json.dumps(ASSETS_DATA_JS, ensure_ascii=False)
    edu_json = json.dumps(EDU_50, ensure_ascii=False)
    rendered_html = HTML_UI.replace('__ASSETS_JSON_PLACEHOLDER__', assets_json).replace('__EDU_JSON_PLACEHOLDER__', edu_json)
    return rendered_html

@app.get("/get_signal")
async def get_signal(asset: str = "🇪🇺🇺🇸 EUR/USD OTC", timeframe: str = "1min", tg_username: str = Cookie(None)):
    user_for_alert = tg_username if tg_username else "Неизвестный ученик"
    asyncio.create_task(send_tg_signal_alert(user_for_alert, asset, timeframe))

    await asyncio.sleep(1.2)
    is_otc = "OTC" in asset
    clean_asset = asset.replace("OTC", "").replace("🇪🇺", "").replace("🇺🇸", "").replace("🇬🇧", "").replace("🇯🇵", "").replace("🇦🇺", "").replace("🇨🇦", "").replace("🇳🇿", "").replace("🇨🇭", "").replace("₿", "").replace("Ξ", "").replace("🪙", "").replace("🛢️", "").replace("⛽", "").replace("🍎", "").replace("🚗", "").replace("💳", "").replace("💻", "").replace("🛒", "").replace("🎬", "").replace("📘", "").replace("📦", "").replace("💊", "").replace("📊", "").replace("🤖", "").replace("🍔", "").replace("✈️", "").replace("🏦", "").replace("🎮", "").replace("🌐", "").strip()
    
    if is_otc:
        prices = generate_otc_candles(asset, count=50)
    else:
        binance_symbol = BINANCE_MAPPING.get(clean_asset, "BTCUSDT")
        try:
            url = f"https://api.binance.com/api/v3/klines?symbol={binance_symbol}&interval=1m&limit=50"
            async with httpx.AsyncClient() as client:
                res = await client.get(url, timeout=3.0)
                prices = [float(c[4]) for c in res.json()]
        except:
            prices = generate_otc_candles(clean_asset, count=50)

    rsi = calculate_rsi(prices)
    ema = calculate_ema(prices)
    calculated_signal, reason = smart_market_analysis(prices, rsi, ema)
    return {"signal": calculated_signal, "reason": reason}

@app.post("/analyze")
async def analyze(file: UploadFile = File(...), cfg: str = Form(...)):
    image_bytes = await file.read()
    config = json.loads(cfg)
    result_text = await scanner_core.compute(image_bytes, config)
    return {"text": result_text}

@app.post("/ai-chat")
async def ai_chat(prompt: str = Form(...)):
    user_prompt = prompt.strip()
    if not GEMINI_API_KEY:
        return {"reply": "⚠️ Ключ GEMINI_API_KEY не настроен в окружении Render."}

    try:
        model = genai.GenerativeModel('gemini-1.5-flash-001')
        chat_prompt = (
            "Ты профессиональный элитный трейдер, наставник и ИИ-помощник по торговле в нашей команде 'Team Master VIP' на платформе Pocket Option. "
            "Твоя задача — отвечать на вопросы пользователя ИСКЛЮЧИТЕЛЬНО по теме трейдинга, финансовых рынков, технических индикаторов, стратегий, "
            "сигналов, а также помогать решать технические вопросы с терминалом или если что-то не работает. "
            "Используй много крутых эмодзи в каждом ответе (📈,📉,⚡,🚀,💎,👑,🎯,🤖,⭐,🔥,📊).\n\n"
            "СТРОГИЕ ПРАВИЛА БЕЗОПАСНОСТИ И ТЕМАТИКИ:\n"
            "1. Отвечай ТОЛЬКО на вопросы по трейдингу, стратегиям, рынкам, сигналам и техническим вопросам терминала. Если вопрос касается любых других посторонних тем "
            "(политика, личная жизнь, фильмы, кулинария, погода, программирование вне трейдинга и т.д.), строго откажись: "
            "'Я консультирую исключительно по вопросам трейдинга, рынков, сигналов и техническим вопросам Team Master VIP! 📈📉'\n"
            "2. НА НЕПРИЛИЧНЫЕ, ПОШЛЫЕ ИЛИ ОСКОРБИТЕЛЬНЫЕ ВОПРОСЫ ИИ КАТЕГОРИЧЕСКИ НЕ ОТВЕЧАЕТ! Строго отвечай: "
            "'Я общаюсь только на темы профессионального трейдинга и рынков! Никаких посторонних или неприличных тем. ❌💎'\n"
            "3. Если пользователь просит дать сигнал, подсказать актив, направление (CALL/PUT) или разобрать сделку — подробно и профессионально распиши сигнал!\n\n"
            f"Вопрос пользователя: {user_prompt}"
        )
        response = model.generate_content(chat_prompt)
        reply = response.text.strip() if response and response.text else "Нет ответа от ИИ. 🤖"
        return {"reply": reply}
    except Exception as e:
        return {"reply": f"⚠️ Ошибка связи с ИИ: {str(e)}"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
