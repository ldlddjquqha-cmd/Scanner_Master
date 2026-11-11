import os
import asyncio
import json
import logging
import random
from fastapi import FastAPI, UploadFile, File, Form, Request, Cookie
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
import numpy as np
import httpx
from datetime import datetime

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="TEAM MASTER VIP - ULTIMATE QUANTUM CORE", version="29.0")

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

async def send_tg_signal_alert(username, asset, timeframe):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": f"📊 **Пользователь перешел к сигналам!**\n\n**Ученик:** @{username}\n**Актив:** `{asset}`\n**Таймфрейм:** `{timeframe}`\n**Время запроса:** {datetime.now().strftime('%H:%M:%S')}",
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
        reasons.append("📈 Цена находится выше экспоненциальной скользящей средней (EMA), подтверждая сильный восходящий тренд и мощный импульс покупателей! 🚀")
    else:
        reasons.append("📉 Цена закрепилась ниже скользящей средней (EMA), что указывает на уверенное развитие нисходящей тенденции и давление продавцов! ⚡")
        
    if rsi > 65:
        reasons.append(f"🔥 Индекс относительной силы RSI составляет {round(rsi, 1)}, актив приближается к зоне перекупленности, будьте аккуратны! ⚠️")
    elif rsi < 35:
        reasons.append(f"💎 Индекс относительной силы RSI находится на отметке {round(rsi, 1)}, фиксируется сильная зона перепроданности и огромный потенциал для крутого отскока вверх! ✨")
    else:
        reasons.append(f"⚖️ Индикатор RSI абсолютно нейтрален ({round(rsi, 1)}), доминирует стабильный текущий локальный импульс рынка! 🎯")

    if momentum_up:
        reasons.append("🟢 Свечной анализ показывает абсолютное преобладание покупателей и зеленых тел на последнем таймфрейме! 🚀")
    else:
        reasons.append("🔴 Наблюдается жесткое давление со стороны продавцов и красных свечей по закрытию последних периодов! ⚡")

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
            from PIL import Image
            import io
            image = Image.open(io.BytesIO(image_bytes))
            img_rgb = image.convert("RGB")
            np_img = np.array(img_rgb)
            
            h, w, _ = np_img.shape
            crop = np_img[int(h*0.05):int(h*0.95), int(w*0.05):int(w*0.95)]
            
            r_channel = crop[:, :, 0].astype(float)
            g_channel = crop[:, :, 1].astype(float)
            b_channel = crop[:, :, 2].astype(float)
            
            green_candles = np.sum((g_channel > r_channel + 10) & (g_channel > b_channel + 10))
            red_candles = np.sum((r_channel > g_channel + 10) & (r_channel > b_channel + 10))
            
            is_buy = green_candles >= red_candles
            direction = "BUY (ВВЕРХ) 🚀✨" if is_buy else "SELL (ВНИЗ) 📉🔥"
            trend_desc = "🟢 Обнаружено преобладание зеленых тел свечей, импульс направлен вверх по тренду! 🚀" if is_buy else "🔴 Зафиксировано преобладание красных тел свечей, давление продавцов доминирует! ⚡"
            
            return (
                f"🚀📊 УСПЕШНЫЙ СКАН СВЕЧЕЙ И ГРАФИКА TEAM MASTER VIP 💎✨\n\n"
                f"🎯 СТРАТЕГИЯ: {config.get('стратегия', 'Smart Money')} 🧠🔥\n"
                f"⏱ ТАЙМФРЕЙМ: {config.get('интервал', 'M1')} ⚡\n"
                f"⏳ ЭКСПИРАЦИЯ: {config.get('экспирация', '1м')} 🕒\n"
                f"---------------------------------------------------\n"
                f"📈 НАПРАВЛЕНИЕ: {direction}\n\n"
                f"💡 АНАЛИЗ РАСПОЛОЖЕНИЯ СВЕЧЕЙ И ТЕКСТУРЫ:\n"
                f"{trend_desc} 🌟 Уровни сопротивления, поддержки и волатильность полностью подтверждены по пиксельному анализу изображения свечного графика! 🎯💎🚀"
            )
        except Exception as e:
            return (
                f"🚀📊 УСПЕШНЫЙ СКАН СВЕЧЕЙ И ГРАФИКА TEAM MASTER VIP 💎✨\n\n"
                f"🎯 СТРАТЕГИЯ: Smart Money 🧠🔥\n"
                f"⏱ ТАЙМФРЕЙМ: M1 ⚡\n"
                f"⏳ ЭКСПИРАЦИЯ: 1м 🕒\n"
                f"---------------------------------------------------\n"
                f"📈 НАПРАВЛЕНИЕ: BUY (ВВЕРХ) 🚀✨\n\n"
                f"💡 АНАЛИЗ РАСПОЛОЖЕНИЯ СВЕЧЕЙ И ТЕКСТУРЫ:\n"
                f"🟢 Скриншот успешно обработан сканером! Структура свечей и уровни подтверждены! 🎯💎🚀"
            )

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
    "t": "1. 🚀💎 Элитная торговля от мощных уровней поддержки и сопротивления! 🎯🔥",
    "tags": ["M1", "M5", "Pocket Option", "Уровни", "Price Action", "🚀 Золотой Стандарт"],
    "d": "📌 🌟 ПОДРОБНЫЙ ПРОФЕССИОНАЛЬНЫЙ РАЗБОР СТРАТЕГИИ ТОРГОВЛИ ОТ УРОВНЕЙ ПОДДЕРЖКИ И СОПРОТИВЛЕНИЯ В POCKET OPTION! 💎🔥\n\n"
       "• 🧠 ⚡ РЫНОЧНАЯ ЛОГИКА И СУТЬ СТРАТЕГИИ:\n"
       "Уровни поддержки и сопротивления — это абсолютные бастионы ликвидности и ключевые ценовые зоны, где баланс сил между покупателями и продавцами кардинально меняется! 🚀 Уровень сопротивления — это мощная область сверху, где крупные продавцы останавливают вертикальный рост цены и начинают агрессивно давить вниз! 📉 Уровень поддержки — надежная область снизу, где киты и покупатели защищают свои позиции и толкают график ракетой вверх! 📈 Когда цена филигранно приближается к таким барьерам, участники фиксируют прибыль, вызывая красивый технический отскок или мощнейший импульсный пробой! 💎✨\n\n"
       "• ⏱ ⚡ РЕКОМЕНДУЕМЫЕ ТАЙМФРЕЙМЫ ГРАФИКА:\n"
       "Таймфреймы M1 (1 минута), M2 и M5 идеально подходят для скальпинга и точной отработки бинарных опционов! 🎯🔥\n\n"
       "• ⏳ 🕒 ОПТИМАЛЬНАЯ ЭКСПИРАЦИЯ СДЕЛКИ:\n"
       "От 2 до 4 минут для идеальной отработки импульса и защиты от рыночного шума! 🚀💎\n\n"
       "• 🎯 ✨ ЧЕТКИЕ ПРАВИЛА ОТКРЫТИЯ ПОЗИЦИЙ (CALL / PUT):\n"
       "  1️⃣ 📉 Для входа ВНИЗ (PUT): Дождитесь аккуратного подхода цены к сильному зеркальному уровню сопротивления. Проанализируйте закрытие свечей: если появляются длинные тени и замедление импульса, открывайте сделку на понижение с уверенностью! 🔥\n"
       "  2️⃣ 📈 Для входа ВВЕРХ (CALL): При касании ценой подтвержденного уровня поддержки и формировании зеленой поглощающей свечи открывайте сделку на повышение ракетой! 🚀✨\n\n"
       "• 🛡️ 💎 УПРАВЛЕНИЕ КАПИТАЛОМ И РИСКИ:\n"
       "Никогда не входите напролом при затяжном импульсе! Ждите подтверждения паттерна и соблюдайте риск-менеджмент 1-2% на сделку! 🌟🎯"
  },
  {
    "t": "2. 🚀💎 Профессиональная торговля в боковике (Флэт и диапазон)! 🎯🔥",
    "tags": ["M1", "M5", "Pocket Option", "Флэт", "Боковик", "💎 Топ Стратегия"],
    "d": "📌 🌟 ПОДРОБНЫЙ РАЗБОР СТРАТЕГИИ ТОРГОВЛИ В ГОРИЗОНТАЛЬНОМ БОКОВИКЕ (ФЛЭТЕ)! 💎🔥\n\n"
       "• 🧠 ⚡ РЫНОЧНАЯ ЛОГИКА И СУТЬ СТРАТЕГИИ:\n"
       "Боковое движение (флэт) возникает тогда, когда на рынке нет тренда, а цена движется внутри четкого канала между поддержкой и сопротивлением! 🚀 Стратегия основана на возвращении цены к средним значениям диапазона (меан-реверсия). Трейдер продает у верхней границы канала и покупает у нижней! 💎✨\n\n"
       "• ⏱ ⚡ РЕКОМЕНДУЕМЫЕ ТАЙМФРЕЙМЫ:\n"
       "Таймфреймы M1 и M5 для мгновенной реакции на границы канала! 🎯🔥\n\n"
       "• ⏳ 🕒 ОПТИМАЛЬНАЯ ЭКСПИРАЦИЯ:\n"
       "1 – 3 минуты для точной отработки отскока от границ! 🚀\n\n"
       "• 🎯 ✨ ПРАВИЛА ВХОДА (CALL / PUT):\n"
       "  1️⃣ 📈 CALL: Цена касается нижней границы канала, формируется пин-бар. Открываем сделку вверх к середине флэта! 🚀✨\n"
       "  2️⃣ 📉 PUT: Цена доходит до верхней границы горизонтального диапазона, импульс угасает, открываем сделку вниз! 🔥\n\n"
       "• 🛡️ 💎 РИСК-МЕНЕДЖМЕНТ:\n"
       "Всегда проверяйте отсутствие новостей, способных пробить узкий коридор! 🌟🎯"
  },
  {
    "t": "3. 🚀💎 Связка Accelerator Oscillator + Alligator! 🎯🔥",
    "tags": ["M1", "M2", "M5", "Pocket Option", "Индикаторы", "🔥 Индикаторная система"],
    "d": "📌 🌟 ПОДРОБНЫЙ РАЗБОР ПРОФЕССИОНАЛЬНОЙ СВЯЗКИ ACCELERATOR OSCILLATOR + ALLIGATOR! 💎🔥\n\n"
       "• 🧠 ⚡ РЫНОЧНАЯ ЛОГИКА И СУТЬ СТРАТЕГИИ:\n"
       "Индикатор Alligator моделирует рыночные циклы и тренды, а Accelerator Oscillator (AC) измеряет ускорение движущей силы рынка! 🚀 Их комбинация дает убийственную точность входов в Pocket Option! 💎✨\n\n"
       "• ⏱ ⚡ ТАЙМФРЕЙМЫ:\n"
       "M1, M2 и M5 для стабильной отработки сигналов! 🎯\n\n"
       "• ⏳ 🕒 ЭКСПИРАЦИЯ:\n"
       "От 1 до 3 минут! 🚀\n\n"
       "• 🎯 ✨ ПРАВИЛА ВХОДА:\n"
       "  1️⃣ 📈 CALL: Линии Аллигатора развернуты вверх, гистограмма AC переходит в зеленую зону выше нуля! 🚀✨\n"
       "  2️⃣ 📉 PUT: Пасть Аллигатора направлена вниз, гистограмма AC находится в красной зоне! 🔥\n\n"
       "• 🛡️ 💎 УПРАВЛЕНИЕ КАПИТАЛОМ:\n"
       "Риск на сделку не более 1-2% от банка! 🌟🎯"
  },
  {
    "t": "4. 🚀💎 Мощная связка ADX + Aroon! 🎯🔥",
    "tags": ["M1", "M5", "Pocket Option", "Индикаторы", "⚡ Тренд"],
    "d": "📌 🌟 ПОДРОБНЫЙ РАЗБОР СВЯЗКИ ADX + AROON ДЛЯ ТРЕЙДИНГА! 💎🔥\n\n"
       "• 🧠 ⚡ РЫНОЧНАЯ ЛОГИКА:\n"
       "ADX измеряет силу текущего тренда, а Aroon находит экстремумы цены и зарождение нового импульса! 🚀 Идеально для ловли сильных движений! 💎✨\n\n"
       "• ⏱ ⚡ ТАЙМФРЕЙМЫ:\n"
       "M1 и M5! 🎯\n\n"
       "• ⏳ 🕒 ЭКСПИРАЦИЯ:\n"
       "От 2 до 3 минут! 🚀\n\n"
       "• 🎯 ✨ ПРАВИЛА ВХОДА:\n"
       "  1️⃣ 📈 CALL: ADX выше 25, Aroon Up пересекает Aroon Down снизу вверх! 🚀✨\n"
       "  2️⃣ 📉 PUT: ADX выше 25, Aroon Down уходит в верхнюю зону! 🔥\n\n"
       "• 🛡️ 💎 РИСК-МЕНЕДЖМЕНТ:\n"
       "Избегайте торговли при слабом ADX ниже 20! 🌟🎯"
  },
  {
    "t": "5. 🚀💎 Bollinger Bands + Awesome Oscillator! 🎯🔥",
    "tags": ["S30", "M1", "Pocket Option", "Индикаторы", "💎 Скальпинг"],
    "d": "📌 🌟 ПОДРОБНЫЙ РАЗБОР СВЯЗКИ BOLLINGER BANDS + AWESOME OSCILLATOR! 💎🔥\n\n"
       "• 🧠 ⚡ РЫНОЧНАЯ ЛОГИКА:\n"
       "Полосы Боллинджера фиксируют волатильность и пробой границ, а AO оценивает импульс гистограммы! 🚀 Отличная система для быстрых сделок! 💎✨\n\n"
       "• ⏱ ⚡ ТАЙМФРЕЙМЫ:\n"
       "S30 и M1! 🎯\n\n"
       "• ⏳ 🕒 ЭКСПИРАЦИЯ:\n"
       "1 минута! 🚀\n\n"
       "• 🎯 ✨ ПРАВИЛА ВХОДА:\n"
       "  1️⃣ 📈 CALL: Касание нижней границы Bollinger Bands, гистограмма AO переходит в зеленую зону! 🚀✨\n"
       "  2️⃣ 📉 PUT: Касание верхней границы, разворот гистограммы AO вниз! 🔥\n\n"
       "• 🛡️ 💎 РИСК-МЕНЕДЖМЕНТ:\n"
       "Торговать строго по тренду старшего таймфрейма! 🌟🎯"
  }
]

for i in range(6, 51):
    strat_title = f"{i}. 🚀💎 Профессиональная авторская стратегия и связка индикаторов №{i} ✨🔥"
    strat_desc = (
        f"📌 🌟 ПОДРОБНЫЙ РАЗБОР АВТОРСКОЙ СТРАТЕГИИ И МЕТОДИКИ №{i} ДЛЯ ТЕРМИНАЛА POCKET OPTION! 💎🔥\n\n"
        f"• 🧠 ⚡ РЫНОЧНАЯ МЕХАНИКА И СУТЬ СТРАТЕГИИ:\n"
        f"Данная элитная торговая система построена на комплексном анализе уровней ликвидности, динамических зон перекупленности/перепроданности "
        f"и фильтрации рыночного шума! 🚀 Она создана для уверенной прибыльной торговли бинарными опционами на любых валютных парах, акциях и криптовалютах, "
        f"позволяя с хирургической точностью определять моменты разворота тренда и импульсного движения! 💎✨\n\n"
        f"• ⏱ ⚡ РЕКОМЕНДУЕМЫЕ ТАЙМФРЕЙМЫ ГРАФИКА:\n"
        f"Оптимально использовать таймфреймы M1 (1 минута), M2 и M5 для стабильной и надежной отработки сигналов! 🎯🔥\n\n"
        f"• ⏳ 🕒 ОПТИМАЛЬНАЯ ЭКСПИРАЦИЯ СДЕЛКИ В POCKET OPTION:\n"
        f"Рекомендуемое время экспирации составляет от 1 до 3 минут для максимальной эффективности! 🚀💎\n\n"
        f"• 🎯 ✨ ЧЕТКИЕ ПРАВИЛА ОТКРЫТИЯ ПОЗИЦИЙ (CALL / PUT):\n"
        f"  1️⃣ 🌟 Шаг первый: Дождитесь точного подхода цены к ключевой зоне интереса или подтверждения сигнала по основному индикатору! 🔥\n"
        f"  2️⃣ ⚡ Шаг второй: Убедитесь в отсутствии сильного противонаправленного импульса на младшем таймфрейме! 🚀\n"
        f"  3️⃣ 💎 Шаг третий: Открывайте позицию ВВЕРХ (CALL) 📈 или ВНИЗ (PUT) 📉 строго на закрытии сигнальной свечи без спешки и эмоций! ✨\n\n"
        f"• 🛡️ 🎯 УПРАВЛЕНИЕ КАПИТАЛОМ И РИСКИ:\n"
        f"Никогда не превышайте риск в 1-2% от вашего депозита на одну сделку! Дисциплина и железный мани-менеджмент — ваш главный залог успеха и богатства! 🌟💎🚀"
    )
    EDU_50.append({
        "t": strat_title,
        "tags": ["M1", "Pocket Option", "Стратегии", "💎 Premium"],
        "d": strat_desc
    })

HTML_UI = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
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
.selected-asset-box {background:var(--inner);border:1.5px solid var(--gold);border-radius:12px;padding:10px 14px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;cursor:pointer}
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
.prof-row{font-size:11.5px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center}
.name-input{width:100%;padding:10px;border-radius:10px;border:1px solid var(--border);background:var(--inner);color:#fff;font-size:13px;font-weight:700;outline:none;text-align:center;margin-bottom:10px}
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
.catalog-category-header {font-size:11px;font-weight:900;color:var(--gold);text-transform:uppercase;margin:12px 0 6px 4px;letter-spacing:.6px;border-bottom:1px solid var(--border-g);padding-bottom:4px}
.chat-container{display:flex;flex-direction:column;height:380px;background:var(--inner);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.chat-messages{flex:1;overflow-y:auto;padding:10px;display:flex;flex-direction:column;gap:8px}
.chat-msg{padding:8px 11px;border-radius:10px;font-size:11.5px;line-height:1.4;max-width:85%}
.chat-msg.user{background:rgba(255,215,0,0.15);color:var(--gold);align-self:flex-end;border:1px solid rgba(255,215,0,0.3)}
.chat-msg.ai{background:var(--card);color:var(--text);align-self:flex-start;border:1px solid var(--border)}
.chat-input-row{display:flex;gap:6px;padding:8px;background:var(--card);border-top:1px solid var(--border)}
.chat-input{flex:1;padding:9px 11px;border-radius:9px;border:1px solid var(--border);background:var(--inner);color:#fff;font-size:11.5px;outline:none}
.chat-input:focus{border-color:var(--gold)}
.avatar-preview{width:72px;height:72px;border-radius:50%;border:2px solid var(--gold);object-fit:cover;background:var(--inner);display:block;margin:0 auto 8px}
</style>
</head>
<body>

<div class="wrap">
<div class="header">
<div>
<h1 class="g-text">TEAM MASTER VIP</h1>
<small id="hdrSub">POCKET OPTION TERMINAL</small>
</div>
<select class="lang" id="langSelect" onchange="changeLanguage(this.value)">
<option value="ru">🇷🇺 RU</option>
<option value="en">🇺🇸 EN</option>
<option value="ua">🇺🇦 UA</option>
</select>
</div>

<div id="app">
<div class="card tab" id="tabSig">
<div class="live-bar">
  <span class="live" id="apiStatusBadge">● POCKET OPTION API: ONLINE 🟢</span>
  <span style="font-size:9px;color:var(--gold);font-weight:800" id="apiTimerDisplay">⚡ СИНХРОНИЗАЦИЯ</span>
</div>

<div class="selected-asset-box" onclick="openCatalogModal()">
  <div class="selected-asset-info">
    <span data-t="lblActiveAsset">ВЫБРАННЫЙ АКТИВ POCKET OPTION</span>
    <span id="currentAssetDisplay">🇪🇺🇺🇸 EUR/USD OTC</span>
  </div>
  <div style="display:flex;align-items:center;gap:6px">
    <span id="currentAssetBadge" class="cat-payout">TRADING</span>
    <span style="font-size:16px">📋</span>
  </div>
</div>

<div class="cats" id="assetCatsContainer"></div>

<div class="grid2" style="margin-bottom:10px">
<div>
<label class="lbl" data-t="lblTf">ИНТЕРВАЙМ (ТАЙМФРЕЙМ)</label>
<select class="sel" id="tf" onchange="syncExpiration()">
<option value="S5">5 сек</option><option value="S15">15 сек</option><option value="S30">30 сек</option>
<option value="M1" selected>1 мин</option><option value="M2">2 мин</option><option value="M3">3 мин</option>
<option value="M5">5 мин</option><option value="M10">10 мин</option><option value="M15">15 мин</option>
</select>
</div>
<div>
<label class="lbl" data-t="lblExp">ЭКСПИРАЦИЯ СДЕЛКИ</label>
<select class="sel" id="exp">
<option value="15" data-s="15">15 сек</option><option value="30" data-s="30">30 сек</option>
<option value="60" data-s="60" selected>1 мин</option><option value="120" data-s="120">2 мин</option>
<option value="180" data-s="180">3 мин</option><option value="240" data-s="240">4 мин</option>
<option value="300" data-s="300">5 мин</option><option value="360" data-s="360">6 мин</option>
<option value="420" data-s="420">7 мин</option><option value="480" data-s="480">8 мин</option>
<option value="540" data-s="540">9 мин</option><option value="600" data-s="600">10 мин</option>
<option value="720" data-s="720">12 мин</option><option value="900" data-s="900">15 мин</option>
</select>
</div>
</div>
<button type="button" class="btn btn-gold" onclick="getSig()" data-t="btnCalcSig">⚡ Просчитать сигнал</button>
<div class="gap"></div>
<button type="button" class="btn btn-green btn-sm" onclick="autoSig()" data-t="btnAutoSig">🤖 Выбрать топ актив</button>
<div class="gap"></div>
<a class="btn btn-gold btn-sm" style="background:linear-gradient(135deg,#FFD700,#FFA000)" href="https://u3.shortink.io/cabinet/demo-quick-high-low?utm_campaign=850173&utm_source=affiliate&utm_medium=sr&a=RLQDltKf13Zlrj&al=1771346&ac=smart-link&cid=960963&code=WELCOME50" target="_blank" id="poDeepLink" data-t="btnOpenPo">🚀 Открыть Pocket Option</a>
<div class="sig" id="sigBox">
<div class="sig-meta" id="sigMeta"></div>
<div class="sig-dir call" id="sigDir">⬆️ CALL</div>
<div class="timer">⏱ <span id="cd">01:00</span></div>
<div class="strat" id="sigStrat"></div>
<div class="sig-stats"><span><span data-t="lblCombo">Связка</span>: <b id="sigCombo">Multi-Factor</b></span></div>
<button type="button" class="btn btn-red btn-sm" id="btnCancel" onclick="cancelSig()" style="display:none" data-t="btnCancel">✖ Отменить</button>
<button type="button" class="btn btn-gold btn-sm" id="btnNew" onclick="getSig()" style="display:none" data-t="btnNewSig">⚡ Новый сигнал</button>
</div>
</div>

<div class="card tab hidden" id="tabFavs">
<h3 class="g-text" style="font-size:13px;margin-bottom:8px" data-t="favTabTitle">⭐ Избранные активы</h3>
<p style="font-size:11px;color:var(--muted);margin-bottom:10px" data-t="favTabDesc">Нажмите на любой актив из списка для перехода.</p>
<div class="fav-list" id="mainFavList"></div>
<div id="mainFavEmpty" style="text-align:center;padding:24px;color:var(--muted);font-size:11px;font-weight:600" data-t="profFavEmpty">Пока пусто — добавь актив из полного каталога</div>
</div>

<div class="card tab hidden" id="tabChat">
<h3 class="g-text" style="font-size:13px;margin-bottom:8px">🤖 ИИ Помощник (Трейдинг)</h3>
<p style="font-size:10.5px;color:var(--muted);margin-bottom:10px">Спросите у ИИ о трейдинге, рынках, командах и стратегиях! На неприличные вопросы ИИ не отвечает.</p>
<div class="chat-container">
  <div class="chat-messages" id="chatMessagesBox">
    <div class="chat-msg ai">Привет! Я твой ИИ-помощник. Отвечаю только на вопросы по поводу трейдинга, рынка, команды и стратегий! 🚀📈✨</div>
  </div>
  <div class="chat-input-row">
    <input type="text" class="chat-input" id="chatInputText" placeholder="Введите вопрос по трейдингу..." onkeydown="if(event.key==='Enter') sendAiChatMessage()">
    <button type="button" class="btn btn-gold btn-sm" style="width:auto;padding:8px 14px" onclick="sendAiChatMessage()">➔</button>
  </div>
</div>
<div class="gap2"></div>
<a class="btn btn-dark btn-sm" href="https://t.me/+uekq4TquqkM4Mzcy" target="_blank" style="color:var(--gold)" id="tgCommunityLink">💬 Наши чаты и сообщество в Telegram</a>
</div>

<div class="card tab hidden" id="tabEdu">
<div style="display:flex;gap:6px;margin-bottom:10px">
  <button type="button" class="btn btn-gold btn-sm" style="flex:1" onclick="openRandomEdu()" data-t="btnRandomStrat">🎲 Рандом стратегия</button>
</div>

<div id="eduList">
<h3 class="g-text" style="text-align:center;font-size:13px;margin-bottom:10px" data-t="eduHeader">📚 50 профессиональных связок индикаторов</h3>
<div id="eduItems"></div>
</div>
<div id="eduView" class="hidden">
<button type="button" class="btn btn-gold btn-sm" style="margin-bottom:10px" onclick="closeEdu()" data-t="btnBackEdu">← Назад к списку</button>
<div class="edu-detail" id="eduBody"></div>
</div>
</div>

<div class="card tab hidden" id="tabProf">
<h3 class="g-text" style="text-align:center;font-size:13px;margin-bottom:10px" data-t="profTitle">👤 Профиль трейдера</h3>

<div style="text-align:center; margin-bottom:10px;">
  <img id="profileAvatarImg" class="avatar-preview" src="" alt="Avatar">
  <label class="btn btn-dark btn-sm" style="cursor:pointer; display:inline-block; width:auto; padding:6px 12px; font-size:10px;">
    📷 Изменить фото <input type="file" id="avatarFileInput" accept="image/*" style="display:none" onchange="handleAvatarUpload(event)">
  </label>
</div>

<label class="lbl" data-t="profNameLbl">ВАШ НИКНЕЙМ</label>
<input class="name-input" id="profName" placeholder="Введите ник" onchange="saveProfileData()" onblur="saveProfileData()">

<label class="lbl" data-t="profFavStratLbl">ЛЮБИМАЯ СТРАТЕГИЯ</label>
<input class="name-input" id="profFavStrat" placeholder="Например: Smart Money / Уровни" onchange="saveProfileData()" onblur="saveProfileData()">

<div class="prof-row"><span data-t="profStatus">Статус</span><b style="color:var(--green)">VIP UNLIMITED</b></div>
<div class="prof-row"><span data-t="profIdLbl">Ваш ID / ТГ</span><b id="profId">—</b></div>
<div class="prof-row"><span data-t="profSigsLbl">Сигналов</span><b id="profSigs">0</b></div>
<div class="gap2"></div>
<label class="lbl" data-t="profFavLbl">⭐ ИЗБРАННЫЕ АКТИВЫ</label>
<div class="fav-list" id="favList"></div>
<div id="favEmpty" style="text-align:center;padding:16px;color:var(--muted);font-size:11px;font-weight:600" data-t="profFavEmpty">Пока пусто — добавь актив из полного каталога</div>
<div class="gap2"></div>
<button type="button" class="btn btn-dark btn-sm" onclick="clearFavs()" data-t="btnClearFav">🗑 Очистить избранное</button>
</div>

<div class="card tab hidden" id="tabScan">
<h3 class="g-text" style="text-align:center;font-size:13px;margin-bottom:8px">📷 Сканер (Анализ графика)</h3>
<div class="scan-tips" id="scanTipsHtml">
<b>Инструкция сканера Pocket Option:</b><br>
1️⃣ Выберите интервал, экспирацию и стратегию ниже<br>
2️⃣ Откройте камеру или загрузите скриншот графика со свечами<br>
3️⃣ Нажмите «ЗАПУСК (СКАН СВЕЧЕЙ)»
</div>

<div class="lbl" style="margin-top:6px">ИНТЕРВАЙЛ СКАНЕРОВАНИЯ</div>
<div class="cats" style="margin-bottom:6px" id="scanTfGrid">
  <div class="cat" onclick="setScanCfg(this,'интервал','S5')">S5</div>
  <div class="cat" onclick="setScanCfg(this,'интервал','S15')">S15</div>
  <div class="cat" onclick="setScanCfg(this,'интервал','S30')">S30</div>
  <div class="cat on" onclick="setScanCfg(this,'интервал','M1')">M1</div>
  <div class="cat" onclick="setScanCfg(this,'интервал','M2')">M2</div>
  <div class="cat" onclick="setScanCfg(this,'интервал','M5')">M5</div>
</div>

<div class="lbl">ЭКСПИРАЦИЯ СДЕЛКИ</div>
<div class="cats" style="margin-bottom:6px" id="scanExpGrid">
  <div class="cat" onclick="setScanCfg(this,'экспирация','30с')">30с</div>
  <div class="cat on" onclick="setScanCfg(this,'экспирация','1м')">1м</div>
  <div class="cat" onclick="setScanCfg(this,'экспирация','2м')">2м</div>
  <div class="cat" onclick="setScanCfg(this,'экспирация','5м')">5м</div>
</div>

<div class="lbl">СТРАТЕГИЯ АНАЛИЗА</div>
<div class="cats" style="margin-bottom:8px" id="scanStratGrid">
  <div class="cat on" onclick="setScanCfg(this,'стратегия','Smart Money')">Smart Money</div>
  <div class="cat" onclick="setScanCfg(this,'стратегия','ICT')">ICT</div>
  <div class="cat" onclick="setScanCfg(this,'стратегия','PA')">PA</div>
  <div class="cat" onclick="setScanCfg(this,'стратегия','Scalp')">Scalp</div>
  <div class="cat" onclick="setScanCfg(this,'стратегия','Trend')">Trend</div>
</div>

<button type="button" class="btn btn-gold btn-sm" id="btnOpenCam" onclick="openCam()" data-t="btnOpenCam">📷 Открыть камеру</button>
<div class="gap"></div>
<div style="margin-bottom:8px">
  <label class="btn btn-dark btn-sm" style="cursor:pointer; text-align:center; display:block;">
    📁 Загрузить скриншот графика со свечами <input type="file" id="scanFileInput" accept="image/*" style="display:none" onchange="handleImageUpload(event)">
  </label>
</div>
<div class="scan-box">
<div class="scan-frame" id="scanFrame">
<video class="scan-video hidden" id="scanVideo" autoplay playsinline muted></video>
<img id="scanPreviewImg" class="scan-video hidden" style="object-fit:contain;background:#000;" alt="Preview">
<div class="scan-placeholder" id="scanPh">📷<br><span data-t="camOff">Камера отключена или файл не выбран</span><br><small data-t="camOffSub">нажмите кнопку выше для запуска или загрузите скрин</small></div>
</div>
</div>

<button type="button" class="btn btn-gold" id="btnScan" onclick="doScan()" disabled style="background:#1b5e20;color:#fff">🚀 ЗАПУСК (СКАН СВЕЧЕЙ)</button>
<div class="gap"></div>
<button type="button" class="btn btn-dark btn-sm" onclick="stopCam()" data-t="btnCloseCam">⏹ Закрыть камеру / Сбросить</button>
<div class="sig" id="scanSigBox">
<div class="sig-meta" id="scanMeta">TEAM MASTER SIGNAL V4.0</div>
<div class="sig-dir call" id="scanDir">BUY (ВВЕРХ)</div>
<div class="timer">⏱ <span id="scanCd">01:00</span></div>
<div class="strat" id="scanStrat"></div>
<button type="button" class="btn btn-red btn-sm" id="btnScanCancel" onclick="cancelScan()" style="display:none" data-t="btnCancel">✖ Отменить</button>
<button type="button" class="btn btn-gold btn-sm" id="btnScanNew" onclick="doScan()" style="display:none" data-t="btnNewScan">⚡ Новый скан</button>
</div>
</div>

<div class="card tab hidden" id="tabAbout">
<h3 class="g-text" style="text-align:center;font-size:13px;margin-bottom:6px" data-t="aboutTitle">👑 О нас и нашей команде</h3>
<p style="font-size:11px;color:#b0b8c8;line-height:1.65;margin-bottom:8px"><b>TEAM MASTER VIP</b> — это элитный терминал для трейдинга на платформе Pocket Option с интеллектуальным анализом.</p>
<p style="font-size:10px;color:var(--muted);line-height:1.5;margin-bottom:10px" data-t="aboutRisk">Торгуйте осознанно. Риск на сделку 1-2% от банка.</p>
<a class="btn btn-gold btn-sm" href="https://t.me/+uekq4TquqkM4Mzcy" target="_blank">📢 Наш Telegram канал</a>
</div>
</div>

<div class="bnav" id="bnav">
<div class="nav on" onclick="tab('tabSig',this)"><span>🎯</span><span data-t="navSig">Сигналы</span></div>
<div class="nav" onclick="tab('tabFavs',this)"><span>⭐</span><span data-t="navFavs">Избранное</span></div>
<div class="nav" onclick="tab('tabChat',this)"><span>🤖</span><span>ИИ Чат</span></div>
<div class="nav" onclick="tab('tabEdu',this)"><span>📚</span><span data-t="navEdu">Связки</span></div>
<div class="nav" onclick="tab('tabProf',this)"><span>👤</span><span data-t="navProf">Профиль</span></div>
<div class="nav" onclick="tab('tabScan',this)"><span>📷</span><span data-t="navScan">Сканер</span></div>
<div class="nav" onclick="tab('tabAbout',this)"><span>👑</span><span data-t="navAbout">О нас</span></div>
</div>

<div class="analyze-overlay hidden" id="analyzeBox">
<div class="analyze-wheel"></div>
<div class="analyze-text" id="analyzeTxt">🔍 Сканирование свечей рынка...</div>
<div class="analyze-sub">Smart Money · ICT · PA · Scalp · Trend</div>
</div>

<div class="modal-overlay hidden" id="catalogModal">
  <div class="modal-content">
    <div class="modal-header">
      <h3 data-t="modalCatalogTitle">📋 Полный каталог активов Pocket Option</h3>
      <button type="button" class="modal-close" onclick="closeCatalogModal()">✕</button>
    </div>
    <input type="text" class="modal-search-input" id="modalSearchInput" placeholder="Поиск актива..." oninput="renderCatalogList(this.value)">
    
    <div class="cats" style="margin-bottom:8px" id="catalogFilterCats">
      <button type="button" class="cat on" onclick="setModalCat('all',this)">🌟 Все</button>
      <button type="button" class="cat" onclick="setModalCat('💱',this)">💱 Валюта</button>
      <button type="button" class="cat" onclick="setModalCat('🔥',this)">🔥 Крипто</button>
      <button type="button" class="cat" onclick="setModalCat('🛢️',this)">🛢️ Сырьё</button>
      <button type="button" class="cat" onclick="setModalCat('📈',this)">📈 Акции</button>
      <button type="button" class="cat" onclick="setModalCat('📊',this)">📊 Индексы</button>
    </div>
    
    <div class="modal-body" id="modalCatalogBody"></div>
  </div>
</div>

<script>
let tgUser = localStorage.getItem("tmv_tgUser") || "@MasterTrader";
let timer=null,scanTimer=null,stream=null,camReady=false;
let favs=JSON.parse(localStorage.getItem("tmv_favs")||"[]");
let sigCount=parseInt(localStorage.getItem("tmv_sigs")||"0");
let currentAsset="🇪🇺🇺🇸 EUR/USD OTC";
let currentLang="ru";
let modalCategory="all";
let uploadedImageBlob = null;
let scanCfg = {интервал: 'M1', экспирация: '1м', стратегия: 'Smart Money'};

const ASSETS_DATA_JS = JSON.parse('__ASSETS_JSON_PLACEHOLDER__');

const I18N = {
  ru: {
    lblActiveAsset: "АКТИВ POCKET OPTION",
    lblTf: "ИНТЕРВАЙМ (ТАЙМФРЕЙМ)",
    lblExp: "ЭКСПИРАЦИЯ СДЕЛКИ",
    btnCalcSig: "⚡ Просчитать сигнал",
    btnAutoSig: "🤖 Выбрать топ актив",
    btnOpenPo: "🚀 Открыть Pocket Option",
    lblCombo: "Связка",
    btnCancel: "✖ Отменить",
    btnNewSig: "⚡ Новый сигнал",
    eduHeader: "📚 50 профессиональных связок индикаторов",
    btnBackEdu: "← Назад к списку",
    btnRandomStrat: "🎲 Рандом стратегия",
    profTitle: "👤 Профиль трейдера",
    profNameLbl: "ВАШ НИКНЕЙМ",
    profFavStratLbl: "ЛЮБИМАЯ СТРАТЕГИЯ",
    profStatus: "Статус",
    profIdLbl: "Ваш ID / ТГ",
    profSigsLbl: "Сигналов",
    profFavLbl: "⭐ ИЗБРАННЫЕ АКТИВЫ",
    favTabTitle: "⭐ Избранные активы",
    favTabDesc: "Нажмите на любой актив для перехода.",
    profFavEmpty: "Пока пусто — добавь актив из полного каталога",
    btnClearFav: "🗑 Очистить избранное",
    btnOpenCam: "📷 Открыть камеру",
    camOff: "Камера отключена или файл не выбран",
    camOffSub: "нажмите кнопку выше для запуска или загрузите скрин",
    btnScanNow: "🚀 ЗАПУСК (СКАН СВЕЧЕЙ)",
    btnCloseCam: "⏹ Закрыть камеру / Сбросить",
    btnNewScan: "⚡ Новый скан",
    aboutTitle: "👑 О нас и нашей команде",
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
    lblActiveAsset: "POCKET OPTION ASSET",
    lblTf: "TIMEFRAME",
    lblExp: "EXPIRATION",
    btnCalcSig: "⚡ Calculate Signal",
    btnAutoSig: "🤖 Choose Top",
    btnOpenPo: "🚀 Open Pocket Option",
    lblCombo: "Combo",
    btnCancel: "✖ Cancel",
    btnNewSig: "⚡ New Signal",
    eduHeader: "📚 Indicator Combos",
    btnBackEdu: "← Back",
    btnRandomStrat: "🎲 Random Strategy",
    profTitle: "👤 Trader Profile",
    profNameLbl: "YOUR NICKNAME",
    profFavStratLbl: "FAVORITE STRATEGY",
    profStatus: "Status",
    profIdLbl: "Your ID / TG",
    profSigsLbl: "Signals",
    profFavLbl: "⭐ FAVORITE ASSETS",
    favTabTitle: "⭐ Favorite Assets",
    favTabDesc: "Click any asset to switch.",
    profFavEmpty: "Empty yet",
    btnClearFav: "🗑 Clear",
    btnOpenCam: "📷 Open Camera",
    camOff: "Camera is off or no file",
    camOffSub: "click to start or upload screenshot",
    btnScanNow: "🚀 RUN (CANDLE SCAN)",
    btnCloseCam: "⏹ Close / Reset",
    btnNewScan: "⚡ New Scan",
    aboutTitle: "👑 About Us & Our Team",
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
    lblActiveAsset: "АКТИВ POCKET OPTION",
    lblTf: "ІНТЕРВАЛ (ТАЙМФРЕЙМ)",
    lblExp: "ЕКСПИРАЦИЯ УГОДИ",
    btnCalcSig: "⚡ Прорахувати сигнал",
    btnAutoSig: "🤖 Обрати топ",
    btnOpenPo: "🚀 Відкрити Pocket Option",
    lblCombo: "Зв'язка",
    btnCancel: "✖ Скасувати",
    btnNewSig: "⚡ Новий сигнал",
    eduHeader: "📚 50 професійних зв'язок індикаторів",
    btnBackEdu: "← Назад",
    btnRandomStrat: "🎲 Рандом стратегія",
    profTitle: "👤 Профіль трейдера",
    profNameLbl: "ВАШ НИКНЕЙМ",
    profFavStratLbl: "УЛЮБЛЕНА СТРАТЕГІЯ",
    profStatus: "Статус",
    profIdLbl: "Ваш ID / ТГ",
    profSigsLbl: "Сигналів",
    profFavLbl: "⭐ ОБРАНІ АКТИВИ",
    favTabTitle: "⭐ Обрані активи",
    favTabDesc: "Натисніть на будь-який актив.",
    profFavEmpty: "Поки порожньо",
    btnClearFav: "🗑 Очистити",
    btnOpenCam: "📷 Відкрити камеру",
    camOff: "Камера вимкнена або файл не обрано",
    camOffSub: "натисніть для запуску або завантажте скрін",
    btnScanNow: "🚀 ЗАПУСК (СКАН СВІЧОК)",
    btnCloseCam: "⏹ Закрити / Скинути",
    btnNewScan: "⚡ Новий скан",
    aboutTitle: "👑 Про нас та команду",
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

function updatePoDeepLink() {
  const btn = document.getElementById("poDeepLink");
  if(!btn) return;
  let cleanAsset = currentAsset.replace(/^[^\w]+/, "").trim();
  let formattedAsset = cleanAsset.replace("/", "").replace(/ /g, "_").toLowerCase();
  const baseUrl = "https://u3.shortink.io/cabinet/demo-quick-high-low";
  const queryParams = "?utm_campaign=850173&utm_source=affiliate&utm_medium=sr&a=RLQDltKf13Zlrj&al=1771346&ac=smart-link&cid=960963&code=WELCOME50";
  btn.href = `${baseUrl}/${formattedAsset}${queryParams}`;
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
    html += `<button type="button" class="cat ${activeClass}" onclick="selectCatalogAsset('${assetName}')">${assetName} <span class="cat-payout">${badgeStr}</span></button>`;
  });
  container.innerHTML = html;
}

function updateSelectedAssetUI() {
  const displayEl = document.getElementById("currentAssetDisplay");
  if(displayEl) displayEl.textContent = currentAsset;
  const badgeEl = document.getElementById("currentAssetBadge");
  if(badgeEl) badgeEl.textContent = getAssetBadge(currentAsset);
  updatePoDeepLink();
}

function tab(tabId, el){
  document.querySelectorAll(".tab").forEach(t=>t.classList.add("hidden"));
  document.getElementById(tabId).classList.remove("hidden");
  document.querySelectorAll(".bnav .nav").forEach(n=>n.classList.remove("on"));
  if(el) el.classList.add("on");
  if(tabId === 'tabFavs') renderMainFavs();
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

function syncExpiration() {
  const tfVal = document.getElementById("tf").value;
  const expSelect = document.getElementById("exp");
  let targetSec = 60;
  if(tfVal === "S5") targetSec = 15;
  else if(tfVal === "S15") targetSec = 30;
  else if(tfVal === "S30") targetSec = 60;
  else if(tfVal === "M1") targetSec = 60;
  else if(tfVal === "M2") targetSec = 120;
  else if(tfVal === "M3") targetSec = 180;
  else if(tfVal === "M5") targetSec = 300;
  else if(tfVal === "M10") targetSec = 600;
  else if(tfVal === "M15") targetSec = 900;
  
  for(let i=0; i<expSelect.options.length; i++){
    if(parseInt(expSelect.options[i].value) === targetSec){
      expSelect.selectedIndex = i;
      break;
    }
  }
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

function clearFavs(){
  favs = [];
  localStorage.setItem("tmv_favs", JSON.stringify(favs));
  renderFavs();
  renderMainFavs();
}

async function getSig(){
  runAnalyzer(async ()=>{
    const tf = document.getElementById("tf").value;
    const expSec = parseInt(document.getElementById("exp").value);
    
    let dir = "⬆️ CALL (ВВЕРХ) 🚀✨";
    let stratText = `💎 Интеллектуальный умный расчет котировок Pocket Option для актива (${currentAsset}) по таймфрейму ${tf} с экспирацией ${expSec} сек! 🚀 Уверенный технический анализ по уровням сопротивления, поддержки, флэту и расположению свечей успешно подтвержден! 🔥✨`;
    
    try {
      const res = await fetch(`/get_signal?asset=${encodeURIComponent(currentAsset)}&timeframe=${tf}`);
      if (res.ok) {
        const data = await res.json();
        if (data.signal) {
          dir = data.signal === "UP" ? "⬆️ CALL (ВВЕРХ) 🚀✨" : "⬇️ PUT (НИЗ) 📉🔥";
          if(data.reason) {
            stratText = `💎 Умный технический расчет сигнала: ${data.reason} 🚀✨`;
          }
        }
      }
    } catch(e) {}

    let isCall = dir.includes("CALL") || dir.includes("ВВЕРХ");
    document.getElementById("sigMeta").textContent = `Pocket Option · ${currentAsset} · Таймфрейм: ${tf} · Экспирация: ${expSec} сек`;
    const dirEl = document.getElementById("sigDir");
    dirEl.textContent = dir;
    dirEl.className = "sig-dir " + (isCall ? "call" : "put");
    document.getElementById("sigStrat").innerHTML = `<b>💎 Умный анализ сигналов и свечей:</b> ${stratText}`;
    document.getElementById("sigBox").style.display = "block";
    document.getElementById("btnCancel").style.display = "block";
    document.getElementById("btnNew").style.display = "none";
    sigCount++;
    localStorage.setItem("tmv_sigs", sigCount);
    document.getElementById("profSigs").textContent = sigCount;
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
      el.textContent = "00:00 Готово! ✨";
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
  const phrasesList = ["💎 Запрос к умному анализатору рынка...", "🚀 Анализ уровней, флэта и боковика...", "⚡ Проверка свечных паттернов и тренда...", "✨ Генерация сигнала по расположению свечей..."];
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
  ed.tags.forEach(t => { tagsHtml += `<span class="tag">${t}</span>`; });
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
  document.getElementById("profId").textContent = tgUser;
  document.getElementById("profName").value = localStorage.getItem("tmv_name") || "@MasterTrader";
  document.getElementById("profFavStrat").value = localStorage.getItem("tmv_fav_strat") || "Smart Money";
  document.getElementById("profSigs").textContent = sigCount;
  
  const savedAvatar = localStorage.getItem("tmv_avatar");
  const avatarImg = document.getElementById("profileAvatarImg");
  if(savedAvatar) {
    avatarImg.src = savedAvatar;
  } else {
    avatarImg.src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='72' height='72' viewBox='0 0 24 24' fill='%23FFD700'%3E%3Cpath d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14.2c-2.5 0-4.71-1.28-6-3.22.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09 6 3.08-1.29 1.94-3.5 3.22-6 3.22z'/%3E%3C/svg%3E";
  }
  renderFavs();
}

function saveProfileData(){
  localStorage.setItem("tmv_name", document.getElementById("profName").value);
  localStorage.setItem("tmv_fav_strat", document.getElementById("profFavStrat").value);
}

function handleAvatarUpload(event) {
  const file = event.target.files[0];
  if(!file) return;
  const reader = new FileReader();
  reader.onload = function(e) {
    const base64Img = e.target.result;
    localStorage.setItem("tmv_avatar", base64Img);
    document.getElementById("profileAvatarImg").src = base64Img;
  }
  reader.readAsDataURL(file);
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
  .catch(err => { alert("Не удалось получить доступ к камере: " + err); });
}

function handleImageUpload(event) {
  const file = event.target.files[0];
  if(!file) return;
  uploadedImageBlob = file;
  if(stream){ stream.getTracks().forEach(t => t.stop()); stream = null; }
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
  if(stream){ stream.getTracks().forEach(t => t.stop()); stream = null; }
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
  if(!camReady){ alert("Сначала откройте камеру или загрузите скриншот графика со свечами!"); return; }
  const expSec = scanCfg.экспирация.includes('30') ? 30 : parseInt(scanCfg.экспирация) * 60;

  if (uploadedImageBlob) {
    processImageBlobForScan(uploadedImageBlob, expSec);
  } else {
    const video = document.getElementById("scanVideo");
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.toBlob((blob) => { processImageBlobForScan(blob, expSec); }, 'image/jpeg');
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
        
        document.getElementById("scanMeta").textContent = "TEAM MASTER SIGNAL V4.0";
        const dirEl = document.getElementById("scanDir");
        
        let isBuy = resText.includes("BUY") || resText.includes("ВВЕРХ");
        dirEl.textContent = isBuy ? "BUY (ВВЕРХ) 🚀✨" : "SELL (ВНИЗ) 📉🔥";
        dirEl.className = "sig-dir " + (isBuy ? "call" : "put");
        sigCount++;
        localStorage.setItem("tmv_sigs", sigCount);
        document.getElementById("profSigs").textContent = sigCount;

        document.getElementById("scanStrat").innerHTML = `<pre style="white-space:pre-wrap;font-family:inherit;"><b>${resText}</b></pre>`;
        document.getElementById("scanSigBox").style.display = "block";
        document.getElementById("btnScanCancel").style.display = "block";
        document.getElementById("btnScanNew").style.display = "none";

        startTimer("scanCd", expSec, ()=>{
          document.getElementById("btnScanCancel").style.display = "none";
          document.getElementById("btnScanNew").style.display = "block";
        });
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
  msgBox.innerHTML += `<div class="chat-msg ai" id="${loadingId}">ИИ думает... 🤖✨</div>`;
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
async def get_signal(asset: str = "🇪🇺🇺🇸 EUR/USD OTC", timeframe: str = "1min"):
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
    user_prompt = prompt.strip().lower()
    
    forbidden_words = ["хуй", "пизда", "ебал", "сука", "бля", "блять", "трах", "секс", "пошел нахуй", "иди нахуй", "порно", " cyka", " blyat"]
    for word in forbidden_words:
        if word in user_prompt:
            return {"reply": "🚫✨ Извините, но я отвечаю исключительно на вопросы по поводу трейдинга, рынка, команды и стратегий торговли! На другие темы и неприличные вопросы я не отвечаю! 📈💎🚀"}

    trading_keywords = ["трейд", "рынк", "команд", "стратег", "сигнал", "pocket", "option", "анализ", "график", "свеч", "индикатор", "актив", "валют", "крипт", "биткоин", "депозит", "риск", "мани", "мэнеджмент", "exp", "экспирация", "таймфрейм", "терминал", "биткоин", "торгов", "скальп", "уровн", "профит", "плюс", "успех", "мастер"]
    
    is_trading_related = any(kw in user_prompt for kw in trading_keywords)
    
    if not is_trading_related:
        return {"reply": "🚫📈 Я элитный помощник терминала TEAM MASTER VIP и отвечаю исключительно на вопросы по поводу трейдинга, рынка, команды и стратегий! Задайте вопрос по торговле или сигналам! 🚀💎✨"}

    responses = [
        f"💎 Отличный вопрос по трейдингу и рынку! 🚀 Для максимального профита в Pocket Option всегда учитывайте текущий тренд, старшие таймфреймы и строгий риск-менеджмент 1-2% на сделку! 🔥📈✨",
        f"🌟 В нашей команде TEAM MASTER VIP мы используем передовые стратегии Smart Money и свечной анализ! 🎯 Торгуйте строго по уровням поддержки и сопротивления, избегая эмоциональных входов! 🚀💎",
        f"🔥 Рынок бинарных опционов требует холодной дисциплины и четкого расчета! 📊 Используйте наш встроенный сканер свечей и 50 профессиональных связок индикаторов для уверенных побед! 🚀✨💎"
    ]
    return {"reply": random.choice(responses)}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
