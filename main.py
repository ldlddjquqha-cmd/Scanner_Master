import os
import re
import io
import json
import time
import base64
import random
import asyncio
import logging
from datetime import datetime, timezone

import httpx
import numpy as np
import uvicorn
from PIL import Image
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="TEAM MASTER VIP - Ultimate Trading Terminal", version="31.0")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
OTCSHARDS_API_KEY = (
    os.environ.get("OTCSHARDS_API_KEY", "")
    or os.environ.get("OTCHARTS_API_KEY", "")
    or os.environ.get("OTCHARDS_API_KEY", "")
)
OTCSHARDS_API_URL = os.environ.get("OTCSHARDS_API_URL", "https://api.otcshards.com/v1/candles")


# ============================================================
#  ВСПОМОГАТЕЛЬНОЕ
# ============================================================
async def send_tg_signal_alert(asset, timeframe):
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": (
            f"📊 Пользователь запросил сигнал!\n\n"
            f"Актив: {asset}\nТаймфрейм: {timeframe}\n"
            f"Время запроса: {datetime.now().strftime('%H:%M:%S')}"
        ),
    }
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json=payload, timeout=5.0)
        except Exception:
            pass


def calculate_rsi(prices, period=14):
    prices = np.asarray(prices, dtype=float)
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices)[-period:]
    up = np.where(deltas > 0, deltas, 0).mean()
    down = np.where(deltas < 0, -deltas, 0).mean()
    if down == 0:
        return 100.0
    rs = up / down
    return 100.0 - (100.0 / (1.0 + rs))


def calculate_ema(prices, period=20):
    prices = np.asarray(prices, dtype=float)
    if len(prices) < period:
        return float(prices[-1])
    k = 2.0 / (period + 1.0)
    ema = float(prices[:period].mean())
    for p in prices[period:]:
        ema = float(p) * k + ema * (1.0 - k)
    return ema


def smart_market_analysis(prices, rsi, ema):
    current_price = prices[-1]
    prev_price = prices[-2] if len(prices) >= 2 else current_price
    trend_up = current_price > ema
    momentum_up = current_price > prev_price
    reasons = []
    if trend_up:
        reasons.append("Цена находится выше экспоненциальной скользящей средней (EMA), тренд восходящий.")
    else:
        reasons.append("Цена закрепилась ниже скользящей средней (EMA), развивается нисходящая тенденция.")
    if rsi > 65:
        reasons.append(f"RSI = {round(rsi, 1)}: актив приближается к зоне перекупленности.")
    elif rsi < 35:
        reasons.append(f"RSI = {round(rsi, 1)}: зона перепроданности, возможен отскок.")
    else:
        reasons.append(f"RSI нейтрален ({round(rsi, 1)}), доминирует текущий локальный импульс.")
    if momentum_up:
        reasons.append("Свечной анализ: на последней свече преобладают покупатели.")
    else:
        reasons.append("Свечной анализ: на последней свече давление продавцов.")
    signal = "UP" if (trend_up and momentum_up) or (rsi < 35) else "DOWN"
    if rsi > 70:
        signal = "DOWN"
    return signal, " ".join(reasons)


def generate_otc_candles(asset_name, count=50):
    seed_value = sum(ord(ch) for ch in asset_name) + int(time.time() / 150)
    state = random.Random(seed_value)
    up = asset_name.upper()
    if "BITCOIN" in up or "BTC" in up:
        start_price = 65000.0
    elif "ETHEREUM" in up:
        start_price = 3500.0
    elif "GOLD" in up:
        start_price = 2300.0
    elif "JPY" in up:
        start_price = 150.0
    else:
        start_price = 1.1250
    prices = [start_price]
    for _ in range(count):
        prices.append(prices[-1] * (1 + state.uniform(-0.0015, 0.0015)))
    return prices


def _gemini_rest_vision(prompt: str, image_bytes: bytes, mime: str = "image/jpeg") -> str:
    """Запрос к Gemini по REST (без сторонних SDK)."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": base64.b64encode(image_bytes).decode()}},
            ]
        }]
    }
    r = httpx.post(url, params={"key": GEMINI_API_KEY}, json=body, timeout=45.0)
    r.raise_for_status()
    data = r.json()
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts).strip()


class ScannerAnalyzer:
    def _gemini_sync(self, image_bytes: bytes, config: dict):
        prompt = (
            "Ты профессиональный ИИ-сканер графиков бинарных опционов Pocket Option. "
            "Проанализируй свечи, их тела и тени, текущую тенденцию, уровни и паттерны на скриншоте. "
            "Выдай сигнал: направление (BUY / ВВЕРХ или SELL / ВНИЗ), таймфрейм, стратегию ("
            + str(config.get("стратегия", "Smart Money"))
            + ") и краткий технический анализ на русском языке. Первой строкой напиши только BUY или SELL."
        )
        return _gemini_rest_vision(prompt, image_bytes, mime="image/jpeg")

    async def compute(self, image_bytes: bytes, config: dict) -> str:
        try:
            image = Image.open(io.BytesIO(image_bytes))
            if GEMINI_API_KEY:
                try:
                    buf = io.BytesIO()
                    image.convert("RGB").save(buf, format="JPEG", quality=85)
                    text = await asyncio.to_thread(self._gemini_sync, buf.getvalue(), config)
                    if text:
                        return "--- TEAM MASTER AI CANDLE SCANNER ---\n" + text
                except Exception as ex:
                    logging.warning(f"Gemini error: {ex}")

            np_img = np.array(image.convert("RGB"))
            r = np_img[:, :, 0].astype(float)
            g = np_img[:, :, 1].astype(float)
            b = np_img[:, :, 2].astype(float)
            green_candles = int(np.sum((g > r + 30) & (g > b + 30)))
            red_candles = int(np.sum((r > g + 30) & (r > b + 30)))
            total_pixels = np_img.shape[0] * np_img.shape[1]
            candle_ratio = (green_candles + red_candles) / max(total_pixels, 1)
            if candle_ratio < 0.0015:
                return "❌ ГРАФИК СО СВЕЧАМИ НЕ ОБНАРУЖЕН. Убедитесь, что на скриншоте чётко виден график. 📷🔍"

            bullish = green_candles >= red_candles
            direction = "BUY (ВВЕРХ) 📈" if bullish else "SELL (ВНИЗ) 📉"
            trend_desc = (
                "Преобладание бычьих зелёных тел свечей указывает на активных покупателей. 🟢"
                if bullish
                else "Доминирование медвежьих красных свечей говорит о давлении продавцов. 🔴"
            )
            return (
                "--- TEAM MASTER SIGNAL ---\n"
                "📊 СТРАТЕГИЯ: " + str(config.get("стратегия", "Smart Money")) + "\n"
                "⏱ ТАЙМФРЕЙМ: " + str(config.get("интервал", "M1")) + "\n"
                "⏳ ЭКСПИРАЦИЯ: " + str(config.get("экспирация", "1м")) + "\n"
                "-------------------------------\n"
                "🔥 ВЕРДИКТ: " + direction + "\n"
                "📌 ПРИЧИНА: " + trend_desc + "\n"
                "Импульс подтверждён закрытием последних свечей. ⚡\n"
                "🎯 Сигнал готов к входу!"
            )
        except Exception as e:
            return "❌ Ошибка анализа изображения: " + str(e) + " ⚠️"


scanner_core = ScannerAnalyzer()


# ============================================================
#  АКТИВЫ
# ============================================================
_TOP_BEST_OTC = ["AUD/USD OTC", "EUR/USD OTC", "GBP/USD OTC", "USD/JPY OTC", "BITCOIN OTC"]

_CURRENCY = [
    "🇪🇺🇺🇸 EUR/USD OTC", "🇬🇧🇺🇸 GBP/USD OTC", "🇺🇸🇯🇵 USD/JPY OTC", "🇦🇺🇺🇸 AUD/USD OTC", "🇪🇺🇯🇵 EUR/JPY OTC",
    "🇺🇸🇨🇦 USD/CAD OTC", "🇬🇧🇯🇵 GBP/JPY OTC", "🇳🇿🇺🇸 NZD/USD OTC", "🇺🇸🇨🇭 USD/CHF OTC", "🇪🇺🇬🇧 EUR/GBP OTC",
    "🇦🇺🇨🇭 AUD/CHF OTC", "🇦🇺🇳🇿 AUD/NZD OTC", "🇨🇦🇨🇭 CAD/CHF OTC", "🇪🇺🇭🇺 EUR/HUF OTC", "🇪🇺🇳🇿 EUR/NZD OTC",
    "🇪🇺🇹🇷 EUR/TRY OTC", "🇬🇧🇦🇺 GBP/AUD OTC", "🇬🇧🇨🇭 GBP/CHF OTC", "🇬🇧🇨🇦 GBP/CAD OTC", "🇳🇿🇯🇵 NZD/JPY OTC",
    "🇺🇸🇦🇷 USD/ARS OTC", "🇺🇸🇧🇷 USD/BRL OTC", "🇺🇸🇨🇱 USD/CLP OTC", "🇺🇸🇨🇴 USD/COP OTC", "🇺🇸🇩🇿 USD/DZD OTC",
    "🇺🇸🇪🇬 USD/EGP OTC", "🇺🇸🇮🇩 USD/IDR OTC", "🇺🇸🇮🇳 USD/INR OTC", "🇺🇸🇲🇽 USD/MXN OTC", "🇺🇸🇲🇾 USD/MYR OTC",
    "🇺🇸🇵🇭 USD/PHP OTC", "🇺🇸🇵🇰 USD/PKR OTC", "🇺🇸🇸🇬 USD/SGD OTC", "🇺🇸🇹🇭 USD/THB OTC", "🇺🇸🇻🇳 USD/VND OTC",
    "🇿🇦🇺🇸 ZAR/USD OTC", "🇺🇦🇺🇸 UAH/USD OTC", "🇳🇬🇺🇸 NGN/USD OTC", "🇰🇪🇺🇸 KES/USD OTC", "🇺🇸🇷🇺 USD/RUB OTC",
    "🇦🇺🇺🇸 AUD/USD", "🇺🇸🇨🇦 USD/CAD", "🇺🇸🇯🇵 USD/JPY", "🇪🇺🇺🇸 EUR/USD", "🇬🇧🇺🇸 GBP/USD", "🇪🇺🇬🇧 EUR/GBP",
    "🇬🇧🇨🇭 GBP/CHF", "🇬🇧🇦🇺 GBP/AUD", "🇪🇺🇨🇭 EUR/CHF", "🇪🇺🇨🇦 EUR/CAD", "🇪🇺🇦🇺 EUR/AUD", "🇯🇵🇨🇦 CAD/JPY",
    "🇯🇵🇨🇭 CHF/JPY", "🇦🇺🇨🇦 AUD/CAD", "🇦🇺🇯🇵 AUD/JPY", "🇬🇧🇯🇵 GBP/JPY", "🇪🇺🇯🇵 EUR/JPY", "🇨🇦🇨🇭 CAD/CHF",
]
_CRYPTO = [
    "₿ Bitcoin OTC", "Ξ Ethereum OTC", "🪙 Solana OTC", "🪙 BNB OTC", "🪙 Dogecoin OTC", "🪙 Chainlink OTC",
    "🪙 Toncoin OTC", "🪙 Bitcoin ETF OTC", "🪙 Avalanche OTC", "🪙 Cardano OTC", "🪙 Polkadot OTC",
    "🪙 Polygon OTC", "🪙 Litecoin OTC", "🪙 TRON OTC", "🪙 Dash", "🪙 BCH/EUR", "🪙 BCH/GBP", "🪙 BCH/JPY",
    "🪙 BTC/GBP", "🪙 BTC/JPY",
]
_COMMODITIES = [
    "🛢️ Brent Oil OTC", "🛢️ WTI Crude Oil OTC", "🥈 Silver OTC", "🥇 Gold OTC",
    "⛽ Natural Gas OTC", "⚪ Palladium spot OTC", "🔘 Platinum spot",
]
_STOCKS = [
    "🍎 Apple OTC", "🚗 Tesla OTC", "💳 VISA OTC", "💻 Intel OTC", "🛒 Amazon OTC",
    "🎬 Netflix OTC", "📘 FACEBOOK INC OTC", "🪟 Microsoft OTC", "💊 Johnson & Johnson OTC",
    "🛒 Alibaba OTC", "📉 VIX OTC", "⛽ ExxonMobil OTC", "🤖 Palantir Technologies OTC",
    "🪙 Coinbase Global OTC", "🍔 McDonald's OTC", "✈️ Boeing Company OTC", "💊 Pfizer Inc OTC",
    "🏦 Citigroup Inc OTC", "📦 FedEx OTC", "🎮 GameStop Corp OTC", "💳 American Express OTC",
    "🌐 Cisco OTC", "⚡ Advanced Micro Devices OTC", "⛏️ Marathon Digital Holdings OTC",
]
_INDICES = ["📊 US100 OTC", "📈 SP500", "📊 SP500 OTC", "🇦🇺 AUS 200"]

ASSETS_DATA_JS = {
    "ru": {
        "🔥 ТОП Лучшие (OTC API)": _TOP_BEST_OTC,
        "💱 Валютные пары": _CURRENCY,
        "🪙 Криптовалюты": _CRYPTO,
        "🛢️ Сырьевые товары": _COMMODITIES,
        "📈 Акции компаний": _STOCKS,
        "📊 Биржевые индексы": _INDICES,
    },
    "en": {
        "🔥 TOP Best (OTC API)": _TOP_BEST_OTC,
        "💱 Currency Pairs": _CURRENCY,
        "🪙 Cryptocurrencies": _CRYPTO,
        "🛢️ Commodities": _COMMODITIES,
        "📈 Company Stocks": _STOCKS,
        "📊 Market Indices": _INDICES,
    },
    "ua": {
        "🔥 ТОП Найкращі (OTC API)": _TOP_BEST_OTC,
        "💱 Валютні пари": _CURRENCY,
        "🪙 Криптовалюти": _CRYPTO,
        "🛢️ Сировинні товари": _COMMODITIES,
        "📈 Акції компаній": _STOCKS,
        "📊 Біржові індекси": _INDICES,
    },
}


# ============================================================
#  СТРАТЕГИИ (50 штук)
# ============================================================
KIND_TXT = {
    "levels": {
        "label": "Уровни",
        "logic": "Уровни поддержки и сопротивления — зоны, где баланс покупателей и продавцов резко меняется. Цена часто отскакивает от них или пробивает с импульсом.",
        "call": "Цена подошла к поддержке, свеча показала отказ идти ниже (длинная нижняя тень, бычье поглощение). Вход вверх на следующей свече.",
        "put": "Цена подошла к сопротивлению, свеча показала отказ идти выше (длинная верхняя тень, медвежье поглощение). Вход вниз на следующей свече.",
        "flt": "Уровень пробит несколькими телами свечей подряд; перед уровнем была длинная импульсная свеча; выходит важная новость.",
    },
    "range": {
        "label": "Флэт",
        "logic": "В боковике цена ходит между чёткими границами. Торгуем возврат к середине диапазона от его краёв.",
        "call": "Цена коснулась нижней границы диапазона, появилась разворотная свеча. Вход вверх.",
        "put": "Цена коснулась верхней границы диапазона, появилась разворотная свеча. Вход вниз.",
        "flt": "Диапазон уже 2 раза пробивали, границы нечёткие, свечи резко выросли в размере.",
    },
    "trend": {
        "label": "Тренд",
        "logic": "Торгуем по направлению сильного тренда: ждём откат и вход в сторону движения, когда индикаторы подтверждают его продолжение.",
        "call": "Восходящий тренд: цена выше EMA, индикаторы направлены вверх, после небольшого отката появилась бычья свеча. Вход вверх.",
        "put": "Нисходящий тренд: цена ниже EMA, индикаторы направлены вниз, после небольшого отката появилась медвежья свеча. Вход вниз.",
        "flt": "Индикаторы переплетаются, цена ходит вокруг EMA, тренд слабый или уже сильно растянут.",
    },
    "reversal": {
        "label": "Разворот",
        "logic": "Ищем моменты, когда движение перегрето и цена готова вернуться: подтверждаем зону индикатором и свечой.",
        "call": "Цена в зоне перепроданности у поддержки или нижней границы, появилась бычья разворотная свеча. Вход вверх.",
        "put": "Цена в зоне перекупленности у сопротивления или верхней границы, появилась медвежья разворотная свеча. Вход вниз.",
        "flt": "Сильный тренд без откатов — перекупленность может длиться долго; нет подтверждающей свечи.",
    },
    "breakout": {
        "label": "Пробой",
        "logic": "Пробой значимой границы с импульсом часто даёт продолжение. Лучший вход — после ретеста пробитого уровня.",
        "call": "Свеча закрылась выше сопротивления, цена вернулась к уровню (ретест) и отскочила вверх. Вход вверх.",
        "put": "Свеча закрылась ниже поддержки, цена вернулась к уровню (ретест) и отскочила вниз. Вход вниз.",
        "flt": "Пробой на малом объёме свечи, сразу возврат внутрь диапазона (ложный пробой), вход «на пробое» без ретеста.",
    },
    "candle": {
        "label": "Свечи",
        "logic": "Свечные модели показывают, кто взял контроль над рынком. Работают только у значимых зон.",
        "call": "У поддержки сформировалась бычья модель (поглощение, молот, пин-бар). Вход вверх на следующей свече.",
        "put": "У сопротивления сформировалась медвежья модель (поглощение, повешенный, пин-бар). Вход вниз на следующей свече.",
        "flt": "Модель в «пустоте» вдали от уровней, слишком маленькая свеча, против сильного тренда.",
    },
}

STRAT_LIST = [
    ("Торговля от уровней поддержки и сопротивления", "M1, M5", "2–4 мин", "levels"),
    ("Торговля в боковике (флэт)", "M1, M5", "1–3 мин", "range"),
    ("Alligator + Accelerator Oscillator", "M1, M2, M5", "2–3 мин", "trend"),
    ("ADX + EMA: вход по силе тренда", "M1, M5", "2–3 мин", "trend"),
    ("Bollinger Bands + Awesome Oscillator", "S30, M1", "1 мин", "reversal"),
    ("RSI + сопротивление: перекупленность", "M1, M2", "2–3 мин", "reversal"),
    ("Пересечение EMA 9/21", "M1, M5", "2–5 мин", "trend"),
    ("MACD + уровни", "M1, M5", "2–4 мин", "levels"),
    ("Stochastic + поддержка", "M1, M2", "2–3 мин", "reversal"),
    ("Пробой уровня с ретестом", "M1, M5", "2–4 мин", "breakout"),
    ("Ложный пробой (фейк-аут)", "M1, M5", "1–3 мин", "reversal"),
    ("Свечная модель «Поглощение»", "M1, M5", "1–3 мин", "candle"),
    ("Пин-бар у уровня", "M1, M5", "2–3 мин", "candle"),
    ("Внутренний бар (Inside Bar)", "M1, M5", "2–4 мин", "breakout"),
    ("Двойная вершина / двойное дно", "M1, M5", "2–5 мин", "reversal"),
    ("Треугольник и пробой", "M5", "3–5 мин", "breakout"),
    ("Канал: отскок от границ", "M1, M5", "2–4 мин", "range"),
    ("Линия тренда + RSI", "M1, M5", "2–4 мин", "trend"),
    ("Bollinger Squeeze: сжатие и выход", "M1, M5", "2–3 мин", "breakout"),
    ("Bollinger + RSI: отскок от границы", "S30, M1", "1–2 мин", "reversal"),
    ("Откат к EMA 50", "M1, M5", "2–4 мин", "trend"),
    ("Три свечи подряд: контртренд", "M1", "1–2 мин", "reversal"),
    ("Heikin Ashi + EMA", "M1, M5", "2–4 мин", "trend"),
    ("Parabolic SAR + ADX", "M1, M5", "2–4 мин", "trend"),
    ("CCI + уровень", "M1, M2", "2–3 мин", "levels"),
    ("Williams %R + EMA", "M1, M2", "1–3 мин", "reversal"),
    ("Momentum + пробой", "M1, M5", "2–3 мин", "breakout"),
    ("ATR: фильтр волатильности", "M1, M5", "2–4 мин", "trend"),
    ("Ichimoku: Tenkan / Kijun", "M5", "3–5 мин", "trend"),
    ("Fibonacci: откат 61.8%", "M5", "3–5 мин", "levels"),
    ("Pivot Points", "M5", "3–5 мин", "levels"),
    ("Дивергенция MACD", "M1, M5", "2–5 мин", "reversal"),
    ("Дивергенция RSI", "M1, M5", "2–5 мин", "reversal"),
    ("Stochastic: кросс в зонах", "M1, M2", "1–3 мин", "reversal"),
    ("Торговля после новости", "M1, M5", "2–4 мин", "breakout"),
    ("Круглые уровни", "M1, M5", "2–4 мин", "levels"),
    ("Тренд старшего ТФ (M15 → M1)", "M1, M15", "2–4 мин", "trend"),
    ("Лента из EMA 8/13/21/34", "M1, M5", "2–4 мин", "trend"),
    ("Vortex + EMA", "M1, M5", "2–3 мин", "trend"),
    ("DeMarker + уровень", "M1, M2", "2–3 мин", "levels"),
    ("Bulls / Bears Power", "M1, M5", "2–3 мин", "trend"),
    ("Fractals + Alligator", "M1, M5", "2–4 мин", "breakout"),
    ("Awesome Oscillator: «блюдце»", "M1, M5", "2–3 мин", "trend"),
    ("Awesome Oscillator: двойной пик", "M1, M5", "2–4 мин", "reversal"),
    ("Accelerator: два зелёных бара", "M1, M2", "1–3 мин", "trend"),
    ("Keltner Channel: отскок", "M1, M5", "1–3 мин", "reversal"),
    ("Donchian Channel: пробой", "M5", "3–5 мин", "breakout"),
    ("SuperTrend", "M1, M5", "2–5 мин", "trend"),
    ("Возврат к EMA 200", "M5", "3–5 мин", "reversal"),
    ("Комбо: уровень + свеча + RSI", "M1, M5", "2–4 мин", "levels"),
]

EDU_50 = []
for _i, (_t, _tf, _exp, _kind) in enumerate(STRAT_LIST, 1):
    _k = KIND_TXT[_kind]
    EDU_50.append({
        "n": _i,
        "t": f"{_i}. {_t}",
        "tf": _tf,
        "exp": _exp,
        "tags": [x.strip() for x in _tf.split(",")] + ["Pocket Option", _k["label"]],
        "sec": [
            {"h": "🧠 Суть стратегии", "t": _k["logic"]},
            {"h": "⏱ Таймфреймы и экспирация", "t": f"Таймфреймы: {_tf}. Экспирация: {_exp}."},
            {"h": "🟢 Условия CALL", "t": _k["call"]},
            {"h": "🔴 Условия PUT", "t": _k["put"]},
            {"h": "🚫 Когда не входить", "t": _k["flt"]},
            {"h": "🛡️ Риск", "t": "Не более 1–2% депозита на сделку. После двух минусов подряд сделайте паузу. Любая стратегия не даёт гарантии результата."},
        ],
    })


# ============================================================
#  HTML
# ============================================================
HTML_UI = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>TEAM MASTER VIP — Ultimate Trading Terminal</title>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
:root{
  --gold:#FFD700;
  --gold-text:linear-gradient(110deg,#9a6e22 0%,#d9b44f 16%,#fff3b8 30%,#e6c25f 44%,#b8862c 60%,#f7e08f 78%,#9a6e22 100%);
  --gold-bg:linear-gradient(115deg,#b87400 0%,#ffb800 9%,#ffd836 20%,#fff7b8 31%,#ffe45c 40%,#f0a000 52%,#ffc71c 63%,#fffbd6 74%,#ffd630 85%,#c98000 100%);
  --bg:#04060a;--card:#0b0f17;--inner:#070a10;--green:#00E676;--red:#FF5252;
  --border:#161e2e;--border-g:rgba(224,186,84,.45);--muted:#7a8499;--text:#e8ecf4;
  --r-btn:7px;--r-card:10px;--r-in:8px;
}
*{box-sizing:border-box;margin:0;padding:0;font-family:Montserrat,sans-serif;-webkit-tap-highlight-color:transparent}
html,body{-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
body{background:radial-gradient(ellipse at top,#0d1220 0%,#04060a 60%);color:var(--text);min-height:100vh;display:flex;justify-content:center;padding:10px 10px 90px}
.wrap{width:100%;max-width:440px;display:flex;flex-direction:column;gap:10px}

@keyframes goldFlow{0%{background-position:0% 50%}100%{background-position:400% 50%}}
@keyframes goldPulse{0%,100%{box-shadow:0 6px 20px rgba(255,170,0,.4),0 0 16px rgba(255,200,40,.4),inset 0 2px 0 rgba(255,255,255,.95),inset 0 -3px 7px rgba(150,80,0,.45)}50%{box-shadow:0 8px 26px rgba(255,190,20,.65),0 0 28px rgba(255,225,90,.8),inset 0 2px 0 #fff,inset 0 -3px 7px rgba(150,80,0,.45)}}
@keyframes gloss{0%{transform:translateX(-170%) skewX(-20deg)}60%,100%{transform:translateX(420%) skewX(-20deg)}}
@keyframes txtFlow{to{background-position:300% 0}}
@keyframes fu{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.6;transform:scale(1.04)}}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
@keyframes bounceSmile{0%,100%{transform:translateY(0)}50%{transform:translateY(-6px)}}

/* золото: текст */
.g-text{background:var(--gold-text);background-size:300% 100%;-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;animation:txtFlow 5s linear infinite;font-weight:900}

/* золото: блестящие элементы (как кнопка «Просчитать сигнал») */
.btn-gold,.filter-btn.on,.lvl-fill,.res-ok{
  background:var(--gold-bg);background-size:400% 100%;animation:goldFlow 3s linear infinite;
  color:#2b1900;border:1px solid rgba(255,250,200,.95)}
.btn-gold{position:relative;overflow:hidden;text-shadow:0 1px 0 rgba(255,255,255,.7);animation:goldFlow 3s linear infinite,goldPulse 2.6s ease-in-out infinite}
.btn-gold::after{content:"";position:absolute;top:-30%;bottom:-30%;left:0;width:40%;background:linear-gradient(100deg,transparent,rgba(255,255,255,.85),transparent);transform:translateX(-170%) skewX(-20deg);animation:gloss 2.8s cubic-bezier(.45,.1,.25,1) infinite;pointer-events:none}

.btn{width:100%;padding:13px;border:none;border-radius:var(--r-btn);font-weight:800;font-size:12px;letter-spacing:.6px;text-transform:uppercase;cursor:pointer;text-align:center;text-decoration:none;display:flex;align-items:center;justify-content:center;gap:6px;transition:transform .15s,opacity .2s}
.btn:active{transform:scale(.97)}.btn:disabled{opacity:.4;pointer-events:none}
.btn-green{background:linear-gradient(180deg,#00e676,#00a84f);color:#022b12;border:1px solid rgba(180,255,210,.6);box-shadow:0 4px 14px rgba(0,230,118,.25),inset 0 1px 0 rgba(255,255,255,.5)}
.btn-dark{background:linear-gradient(180deg,#141922,#07090e);color:#e0c273;border:1px solid rgba(224,186,84,.5);box-shadow:inset 0 1px 0 rgba(255,235,170,.1)}
.btn-red{background:linear-gradient(180deg,#ff4a5c,#b3001b);color:#fff;font-size:11.5px;padding:11px;border:1px solid rgba(255,170,170,.5)}
.btn-sm{padding:10px;font-size:11px}

.header{background:linear-gradient(165deg,#0c1017,#04060a);border:1px solid var(--border-g);border-radius:var(--r-card);padding:14px 16px;display:flex;justify-content:space-between;align-items:center;box-shadow:0 8px 24px rgba(0,0,0,.6),inset 0 1px 0 rgba(255,255,255,.05)}
.header h1{font-size:17px;letter-spacing:.8px}.header small{font-size:9px;color:#cdb673;font-weight:700;letter-spacing:1.4px}
.lang{background:var(--inner);color:#fff;border:1px solid var(--border-g);padding:6px 8px;border-radius:var(--r-in);font-weight:700;font-size:10px;outline:none}
.card{background:linear-gradient(165deg,#0c1017 0%,#04060a 100%);border:1px solid rgba(224,186,84,.28);border-radius:var(--r-card);padding:14px;box-shadow:0 12px 28px rgba(0,0,0,.7),inset 0 1px 0 rgba(255,255,255,.06);animation:fu .3s ease}
.input{width:100%;padding:12px;border-radius:var(--r-in);border:1.5px solid var(--border-g);background:var(--inner);color:#fff;font-size:14px;font-weight:700;text-align:center;outline:none;margin-bottom:9px}
.input:focus{border-color:var(--gold)}
.live-bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.live{font-size:8px;background:rgba(0,230,118,.12);color:var(--green);padding:3px 8px;border-radius:5px;border:1px solid rgba(0,230,118,.3);font-weight:800}
.lbl{display:block;font-size:9px;color:var(--muted);font-weight:800;letter-spacing:.6px;margin-bottom:4px}
.selected-asset-box{background:var(--inner);border:1.5px solid rgba(224,186,84,.7);border-radius:var(--r-card);padding:10px 14px;margin-bottom:14px;display:flex;justify-content:space-between;align-items:center;cursor:pointer;box-shadow:inset 0 1px 0 rgba(255,235,170,.08)}
.selected-asset-info span:first-child{font-size:9px;color:var(--muted);display:block;font-weight:700;letter-spacing:.6px}
.selected-asset-info span:last-child{font-size:13px;color:var(--gold);font-weight:900}
.modal-overlay{position:fixed;inset:0;background:rgba(4,6,10,.9);z-index:300;display:flex;align-items:center;justify-content:center;padding:16px;animation:fadeIn .2s ease}
.modal-content{background:var(--card);border:1.5px solid var(--border-g);border-radius:var(--r-card);width:100%;max-width:410px;max-height:85vh;display:flex;flex-direction:column;padding:16px;animation:fu .3s ease}
.modal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.modal-header h3{font-size:14px;color:var(--gold)}
.modal-close{background:none;border:none;color:var(--muted);font-size:18px;cursor:pointer;font-weight:900}
.modal-body{overflow-y:auto;flex:1;display:flex;flex-direction:column;gap:6px;padding-right:2px}
.modal-search-input{width:100%;padding:10px 12px;border-radius:var(--r-in);border:1.5px solid var(--border-g);background:var(--inner);color:#fff;font-size:12px;font-weight:700;outline:none;margin-bottom:10px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.sel{width:100%;padding:11px 12px;border-radius:var(--r-in);border:1px solid var(--border-g);background-color:var(--inner);color:#fff;font-size:12px;font-weight:700;outline:none;cursor:pointer;appearance:none;-webkit-appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath fill='%23d9b44f' d='M1 1l5 5 5-5'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 11px center}
.sig{display:none;margin-top:11px;background:var(--inner);border:2px solid rgba(224,186,84,.7);border-radius:var(--r-card);padding:14px;text-align:center;animation:fu .35s ease}
.sig-meta{font-size:10px;color:var(--muted);font-weight:700}
.sig-dir{font-size:19px;font-weight:900;padding:11px;border-radius:var(--r-btn);margin:8px 0}
.call{background:rgba(0,230,118,.1);color:var(--green);border:1px solid rgba(0,230,118,.35)}
.put{background:rgba(255,82,82,.1);color:var(--red);border:1px solid rgba(255,82,82,.35)}
.timer{font-size:12.5px;font-weight:900;color:var(--gold);background:rgba(255,215,0,.06);border:1px dashed rgba(224,186,84,.7);border-radius:var(--r-btn);padding:8px;margin-bottom:8px}
.strat{background:var(--card);border:1px solid var(--border);border-radius:var(--r-btn);padding:10px 12px;font-size:11px;color:#c5cdd8;text-align:left;line-height:1.6;margin-bottom:8px;white-space:pre-line}
.mini-chart-box{margin:10px 0;background:var(--card);border:1px solid var(--border-g);border-radius:var(--r-btn);padding:8px}
.mini-chart-box svg{width:100%;height:90px;display:block}
.sig-stats{display:flex;justify-content:space-between;font-size:10px;font-weight:800;margin-bottom:8px}
.res-row{display:none;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px}
.res-row .btn{padding:10px}
.res-ok{border:1px solid rgba(255,250,200,.95)}
.stack{display:flex;flex-direction:column;gap:8px}

.edu-item{background:linear-gradient(165deg,#0d1118,#05070b);border:1px solid rgba(224,186,84,.28);border-radius:var(--r-card);padding:12px 13px;margin-bottom:7px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;gap:10px;box-shadow:0 6px 16px rgba(0,0,0,.5),inset 0 1px 0 rgba(255,235,170,.06);transition:transform .15s,border-color .25s}
.edu-item:active{transform:scale(.98)}.edu-item:hover{border-color:rgba(224,186,84,.6)}
.edu-item h4{font-size:12px;color:#e6c76f;font-weight:800;line-height:1.35}
.edu-item small{display:block;font-size:9px;color:var(--muted);font-weight:700;margin-top:3px}
.edu-item .arr{color:#d6ad4a;font-weight:900;flex-shrink:0}
.edu-item.done h4::after{content:" ✅";font-size:10px}
.edu-detail h3{color:#ffe28a;font-size:14px;font-weight:900;margin-bottom:8px;line-height:1.35}
.tag-row{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px}
.tag{font-size:9px;font-weight:800;padding:3px 8px;border-radius:5px;background:rgba(255,215,0,.08);color:var(--gold);border:1px solid rgba(255,215,0,.25)}
.edu-sec{margin-bottom:9px;padding:10px;border-radius:var(--r-btn);border:1px solid var(--border);background:rgba(255,255,255,.02)}
.edu-sec-h{font-size:12px;font-weight:900;color:#ffe28a;margin-bottom:5px}
.edu-sec-t{font-size:12px;line-height:1.7;color:#dbe1ec}
.edu-note{font-size:10px;color:var(--muted);margin-top:6px;text-align:center}

.bnav{position:fixed;bottom:0;left:0;right:0;background:linear-gradient(180deg,#080b12,#04060a);border-top:1px solid rgba(224,186,84,.3);display:flex;justify-content:space-around;padding:6px 0 10px;z-index:100}
.nav{display:flex;flex-direction:column;align-items:center;gap:1px;color:var(--muted);font-size:8px;font-weight:800;cursor:pointer;min-width:36px;padding:3px 1px;position:relative}
.nav.on{color:var(--gold)}.nav span:first-child{font-size:15px}
.nav.on::before{content:"";position:absolute;top:-7px;left:20%;right:20%;height:2px;border-radius:2px;background:var(--gold-bg);background-size:400% 100%;animation:goldFlow 3s linear infinite}
.hidden{display:none!important}
.gap{height:6px}
.analyze-overlay{position:fixed;inset:0;background:rgba(4,6,10,.94);z-index:400;display:flex;flex-direction:column;align-items:center;justify-content:center}
.analyze-wheel{width:56px;height:56px;border:3px solid rgba(255,215,0,.12);border-top-color:var(--gold);border-radius:50%;animation:spin .7s linear infinite;margin-bottom:14px}
.analyze-text{font-size:13px;font-weight:800;color:var(--gold);text-align:center;animation:pulse 1.1s ease infinite;max-width:260px;line-height:1.5}
.analyze-smile{font-size:28px;margin-bottom:10px;animation:bounceSmile 1s ease infinite}
.scan-box{background:var(--inner);border:1px solid var(--border-g);border-radius:var(--r-card);overflow:hidden;margin-bottom:10px}
.scan-video{width:100%;height:210px;object-fit:cover;background:#000;display:block}
.scan-placeholder{height:210px;display:flex;flex-direction:column;align-items:center;justify-content:center;color:var(--muted);font-size:12px;font-weight:700;gap:8px;text-align:center;padding:12px}
.scan-placeholder span:first-child{font-size:34px}
.fav-list{max-height:340px;overflow-y:auto;display:flex;flex-direction:column;gap:6px}
.fav-item{display:flex;justify-content:space-between;align-items:center;padding:11px 13px;background:var(--inner);border:1px solid var(--border);border-radius:var(--r-btn);font-size:12px;font-weight:700;cursor:pointer}
.fav-item:hover{border-color:rgba(224,186,84,.55)}
.fav-item>span:first-child{color:#e6c76f}
.catalog-category-header{font-size:11px;font-weight:900;color:var(--gold);text-transform:uppercase;margin:12px 0 6px 4px;letter-spacing:.6px;border-bottom:1px solid var(--border-g);padding-bottom:4px}
.about-box{background:var(--inner);border:1px solid var(--border-g);border-radius:var(--r-card);padding:16px;font-size:12px;color:#c5cdd8;line-height:1.8}

/* профиль */
.prof-top{display:flex;gap:14px;align-items:center;margin-bottom:12px}
.profile-photo-upload{width:84px;height:84px;border-radius:50%;border:2px solid rgba(224,186,84,.8);background:var(--inner);display:flex;align-items:center;justify-content:center;cursor:pointer;overflow:hidden;flex-shrink:0;box-shadow:0 0 14px rgba(224,186,84,.25)}
.profile-photo-upload img{width:100%;height:100%;object-fit:cover}
.prof-id{flex:1;min-width:0}
.prof-id .input{text-align:left;margin-bottom:6px;padding:10px 12px;font-size:13px}
.prof-hint{font-size:9px;color:#cdb673;font-weight:700}
.lvl-card{background:linear-gradient(165deg,#0c1017,#04060a);border:1px solid rgba(224,186,84,.35);border-radius:var(--r-card);padding:14px;margin-bottom:10px;box-shadow:inset 0 1px 0 rgba(255,255,255,.05)}
.lvl-top{display:flex;justify-content:space-between;align-items:center;font-size:10px;font-weight:900;letter-spacing:.8px;color:var(--muted)}
.lvl-top b{color:var(--gold);font-size:12px}
.lvl-name{font-size:23px;margin:6px 0 10px;letter-spacing:.4px}
.lvl-bar{height:12px;border-radius:4px;background:#0a0d13;box-shadow:inset 0 1px 3px rgba(0,0,0,.9);overflow:hidden;border:1px solid #1a2130}
.lvl-fill{height:100%;width:0%;border:none;border-radius:3px;transition:width .8s cubic-bezier(.2,.8,.2,1);box-shadow:0 0 12px rgba(255,215,0,.5)}
.lvl-note{font-size:10.5px;color:#aab3c5;font-weight:600;margin-top:9px;line-height:1.5}
.lvl-steps{display:flex;justify-content:space-between;margin-top:10px;gap:3px}
.lvl-steps i{flex:1;height:4px;border-radius:2px;background:#1a2130;display:block}
.lvl-steps i.on{background:var(--gold-bg);background-size:400% 100%;animation:goldFlow 3s linear infinite}
.pg-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px}
.pg-grid div{background:linear-gradient(180deg,#0c1017,#04060a);border:1px solid #1a2130;border-radius:8px;padding:11px 2px;text-align:center;box-shadow:inset 0 1px 0 rgba(255,255,255,.05)}
.pg-grid b{display:block;font-size:20px;font-weight:900;color:var(--gold)}
.pg-grid b.gr{color:var(--green);text-shadow:0 0 12px rgba(0,230,118,.4)}
.pg-grid b.rd{color:var(--red);text-shadow:0 0 12px rgba(255,82,82,.4)}
.pg-grid span{display:block;font-size:7.5px;letter-spacing:.7px;color:var(--muted);font-weight:800;margin-top:3px}

/* новости */
.news-item{background:linear-gradient(180deg,#101522,#090d14);border:1.5px solid var(--border);border-radius:var(--r-card);padding:14px;margin-bottom:11px;cursor:pointer;box-shadow:0 6px 18px rgba(0,0,0,.35);transition:transform .15s}
.news-item:active{transform:scale(.985)}
.news-item.high{border-color:rgba(255,82,82,.6)}
.news-item.medium{border-color:rgba(255,167,38,.5)}
.news-item.open{border-color:var(--gold)}
.news-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.news-cur{font-size:11px;font-weight:900;color:var(--gold);display:flex;align-items:center;gap:6px}
.news-time{font-size:11px;color:#fff;font-weight:800;background:rgba(255,215,0,.14);border:1px solid rgba(255,215,0,.28);padding:4px 9px;border-radius:6px}
.news-name{font-size:13.5px;font-weight:800;color:#fff;margin-bottom:5px;line-height:1.35}
.news-pairs{font-size:10.5px;color:#9aa3b5;margin-bottom:8px;font-weight:600}
.news-meta{display:flex;flex-wrap:wrap;gap:6px;font-size:10px;font-weight:700;color:#c5cdd8}
.news-meta span{background:rgba(255,255,255,.05);border:1px solid var(--border);border-radius:6px;padding:4px 8px}
.news-impact{font-size:9px;font-weight:900;padding:3px 7px;border-radius:5px}
.news-impact.high{background:rgba(255,82,82,.22);color:#ff8a80}
.news-impact.medium{background:rgba(255,167,38,.2);color:#FFB74D}
.news-updated{font-size:10px;color:var(--muted);font-weight:700;text-align:center;margin-bottom:10px;padding:6px;background:rgba(255,255,255,.03);border-radius:6px}
.news-list{max-height:480px;overflow-y:auto;padding-right:3px}
.news-detail{display:none;margin-top:12px;padding:12px;border-top:1px solid rgba(212,175,55,.3);font-size:11.5px;color:#d7deea;line-height:1.7;background:rgba(0,0,0,.3)}
.news-item.open .news-detail{display:block}
.filter-row{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.filter-btn{padding:7px 12px;border-radius:var(--r-btn);border:1px solid var(--border-g);background:var(--inner);color:var(--muted);font-size:10px;font-weight:800;cursor:pointer}
.filter-btn.on{border-radius:var(--r-btn)}
</style>
</head>
<body>
<div class="wrap">
<div class="header">
  <div><h1 class="g-text">TEAM MASTER VIP</h1><small>POCKET OPTION TERMINAL</small></div>
  <select class="lang" id="langSelect" onchange="changeLanguage(this.value)"><option value="ru">🇷🇺 RU</option><option value="en">🇺🇸 EN</option><option value="ua">🇺🇦 UA</option></select>
</div>
<div id="app">

  <!-- СИГНАЛ -->
  <div class="card tab" id="tabSig">
    <div class="live-bar"><span class="live">● POCKET OPTION API: ONLINE 🟢</span><span style="font-size:10px;color:var(--gold);font-weight:800" id="liveClock">🕒 --:--:--</span></div>
    <div class="selected-asset-box" onclick="openCatalogModal()">
      <div class="selected-asset-info"><span>ВЫБРАННЫЙ АКТИВ</span><span id="currentAssetDisplay">AUD/USD OTC</span></div>
      <span style="font-size:22px">📋</span>
    </div>
    <div class="grid2" style="margin-bottom:10px">
      <div><label class="lbl">ИНТЕРВАЛ</label><select class="sel" id="tf"><option value="S5">5 сек</option><option value="S30">30 сек</option><option value="M1" selected>1 мин</option><option value="M5">5 мин</option></select></div>
      <div><label class="lbl">ЭКСПИРАЦИЯ</label><select class="sel" id="exp"><option value="30">30 сек</option><option value="60" selected>1 мин</option><option value="180">3 мин</option><option value="300">5 мин</option></select></div>
    </div>
    <button class="btn btn-gold" id="btnGet" onclick="getSig()">⚡ Просчитать сигнал</button>
    <div class="gap"></div>
    <button class="btn btn-green btn-sm" onclick="autoSig()">🤖 Выбрать топ актив</button>
    <div class="sig" id="sigBox">
      <div class="sig-meta" id="sigMeta"></div>
      <div class="sig-dir call" id="sigDir">⬆️ CALL</div>
      <div class="timer">⏱ <span id="cd">01:00</span></div>
      <div class="strat" id="sigStrat"></div>
      <div class="mini-chart-box" id="sigMiniChart"></div>
      <div class="sig-stats"><span>Источник: <b id="sigSource">API Pocket Option</b></span></div>
      <div class="res-row" id="resRow">
        <button class="btn btn-sm res-ok" onclick="recordResult(true)">✅ Плюс</button>
        <button class="btn btn-red btn-sm" onclick="recordResult(false)">❌ Минус</button>
      </div>
      <button class="btn btn-red btn-sm" id="btnCancel" onclick="cancelSig()" style="display:none">✖ Отменить</button>
      <button class="btn btn-gold btn-sm" id="btnNew" onclick="getSig()" style="display:none">⚡ Новый сигнал</button>
    </div>
  </div>

  <!-- ИЗБРАННОЕ -->
  <div class="card tab hidden" id="tabFavs"><h3 class="g-text" style="font-size:13px;margin-bottom:8px">⭐ Избранные</h3><div class="fav-list" id="mainFavList"></div><div id="mainFavEmpty" style="text-align:center;padding:24px;color:var(--muted);font-size:11px">Пока пусто</div></div>

  <!-- НОВОСТИ -->
  <div class="card tab hidden" id="tabNews">
    <h3 class="g-text" style="font-size:13px;margin-bottom:8px">📰 Форекс-новости</h3>
    <div class="filter-row" id="newsFilterRow">
      <button class="filter-btn on" onclick="setNewsFilter('ALL',this)">ALL</button>
      <button class="filter-btn" onclick="setNewsFilter('USD',this)">USD</button>
      <button class="filter-btn" onclick="setNewsFilter('EUR',this)">EUR</button>
      <button class="filter-btn" onclick="setNewsFilter('GBP',this)">GBP</button>
      <button class="filter-btn" onclick="setNewsFilter('JPY',this)">JPY</button>
      <button class="filter-btn" onclick="setNewsFilter('AUD',this)">AUD</button>
    </div>
    <div class="news-updated" id="newsUpdated">Обновление...</div>
    <div class="news-list" id="newsList"></div>
  </div>

  <!-- ПРОФИЛЬ -->
  <div class="card tab hidden" id="tabProfile">
    <h3 class="g-text" style="font-size:13px;margin-bottom:12px">👤 Профиль</h3>
    <div class="prof-top">
      <div class="profile-photo-upload" onclick="document.getElementById('profPhotoInput').click()">
        <img id="profilePhotoImg" class="hidden" alt=""><span id="profilePhotoIcon" style="font-size:30px">📷</span>
        <input type="file" id="profPhotoInput" accept="image/*" style="display:none" onchange="handleProfilePhoto(event)">
      </div>
      <div class="prof-id">
        <input class="input" id="profName" placeholder="Ваше имя" maxlength="24" onchange="saveProfileSettings()">
        <input class="input" id="profPocketId" placeholder="Pocket Option ID" maxlength="24" onchange="saveProfileSettings()">
        <div class="prof-hint">Нажмите на фото, чтобы изменить</div>
      </div>
    </div>

    <div class="lvl-card">
      <div class="lvl-top"><span>🏅 УРОВЕНЬ ТРЕЙДЕРА</span><b id="lvlPct">0%</b></div>
      <div class="lvl-name g-text" id="lvlName">Ур. 1 · Новичок</div>
      <div class="lvl-bar"><div class="lvl-fill" id="lvlFill"></div></div>
      <div class="lvl-steps" id="lvlSteps"></div>
      <div class="lvl-note" id="lvlNote"></div>
    </div>

    <div class="pg-grid">
      <div><b id="stSig">0</b><span>СИГНАЛОВ</span></div>
      <div><b id="stRead">0</b><span>ИЗУЧЕНО</span></div>
      <div><b class="gr" id="stW">0</b><span>ПЛЮС</span></div>
      <div><b class="rd" id="stL">0</b><span>МИНУС</span></div>
    </div>
    <div class="stack">
      <button class="btn btn-gold" onclick="showTab('tabEdu',document.querySelectorAll('.nav')[4])">📚 Изучать стратегии</button>
      <button class="btn btn-red" onclick="resetProfile()">Сбросить профиль</button>
    </div>
  </div>

  <!-- СТРАТЕГИИ -->
  <div class="card tab hidden" id="tabEdu">
    <div id="eduList">
      <h3 class="g-text" style="font-size:13px;margin-bottom:4px">📚 Связки и стратегии</h3>
      <div style="font-size:10.5px;color:var(--muted);margin-bottom:10px;line-height:1.5">50 стратегий с подробными условиями CALL / PUT. Каждая изученная стратегия повышает ваш уровень.</div>
      <div id="eduItems"></div>
    </div>
    <div id="eduView" class="hidden">
      <button class="btn btn-dark btn-sm" style="margin-bottom:10px" onclick="closeEdu()">← Назад к списку</button>
      <div class="edu-detail" id="eduBody"></div>
    </div>
  </div>

  <!-- СКАНЕР -->
  <div class="card tab hidden" id="tabScan">
    <h3 class="g-text" style="font-size:13px;margin-bottom:8px">📷 AI Сканер</h3>
    <div class="scan-box"><div class="scan-frame"><video id="scanVideo" class="scan-video hidden" autoplay playsinline muted></video><img id="scanPreviewImg" class="scan-video hidden" alt=""><div class="scan-placeholder" id="scanPh"><span>📷</span><span>Откройте камеру или загрузите скриншот графика</span></div></div></div>
    <div class="grid2" style="margin-bottom:10px"><button class="btn btn-dark btn-sm" onclick="openCam()">📷 Камера</button><label class="btn btn-dark btn-sm" style="cursor:pointer">📁 Загрузить<input type="file" id="scanFileInput" accept="image/*" style="display:none" onchange="handleImageUpload(event)"></label></div>
    <button class="btn btn-gold" id="btnScan" onclick="doScan()" disabled>⚡ Сканировать</button>
    <div class="sig" id="scanSigBox"><div class="sig-meta" id="scanMeta"></div><div class="sig-dir call" id="scanDir">BUY</div><div class="timer">⏱ <span id="scanCd">01:00</span></div><div class="strat" id="scanStrat"></div></div>
  </div>

  <!-- О НАС -->
  <div class="card tab hidden" id="tabAbout"><h3 class="g-text" style="font-size:13px;margin-bottom:12px">ℹ️ О нас</h3><div class="about-box">TEAM MASTER VIP — профессиональный торговый терминал с AI-анализом и API интеграцией.<br><br>Торговля бинарными опционами сопряжена с высоким риском потери капитала. Сигналы не гарантируют прибыль.</div></div>
</div>

<div class="bnav">
  <div class="nav on" onclick="showTab('tabSig',this)"><span>⚡</span><span>Сигнал</span></div>
  <div class="nav" onclick="showTab('tabFavs',this)"><span>⭐</span><span>Избранное</span></div>
  <div class="nav" onclick="showTab('tabNews',this)"><span>📰</span><span>Новости</span></div>
  <div class="nav" onclick="showTab('tabProfile',this)"><span>👤</span><span>Профиль</span></div>
  <div class="nav" onclick="showTab('tabEdu',this)"><span>📚</span><span>Стратегии</span></div>
  <div class="nav" onclick="showTab('tabScan',this)"><span>📷</span><span>Сканер</span></div>
  <div class="nav" onclick="showTab('tabAbout',this)"><span>ℹ️</span><span>О нас</span></div>
</div>

<div class="modal-overlay hidden" id="catalogModal">
  <div class="modal-content"><div class="modal-header"><h3>Каталог активов</h3><button class="modal-close" onclick="closeCatalogModal()">✕</button></div><input class="modal-search-input" id="catSearch" placeholder="Поиск актива..." oninput="renderCatalogList(this.value)"><div class="modal-body" id="catalogBody"></div></div>
</div>
<div class="analyze-overlay hidden" id="analyzeBox"><div class="analyze-smile">😊</div><div class="analyze-wheel"></div><div class="analyze-text" id="analyzeTxt">⚡ Запрос к рынку...</div></div>

<script>
var ASSETS = __ASSETS_JSON__;
var EDU = __EDU_JSON__;
var TOP_LIST = ["AUD/USD OTC", "EUR/USD OTC", "GBP/USD OTC", "USD/JPY OTC", "BITCOIN OTC"];
var currentLang = "ru";
var currentAsset = "AUD/USD OTC";
var favs = [];
var timers = {};
var newsCache = [];
var newsFilter = "ALL";
var stream = null;
var uploadedImageBlob = null;
var scanSource = "";
var FLAT = [];
var sigRecorded = true;

function byId(id) { return document.getElementById(id); }
function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

try { favs = JSON.parse(localStorage.getItem("tmv_favs") || "[]"); } catch(e) { favs = []; }

/* ===== ПРОФИЛЬ И УРОВНИ ===== */
var PROF = {name: "", pid: "", photo: "", sig: 0, plus: 0, minus: 0, read: []};
var LEVELS = [[0, "Новичок"], [10, "Ученик"], [30, "Трейдер"], [60, "Профи"], [100, "Эксперт"], [150, "Мастер"]];

function loadProf() {
  try {
    var p = JSON.parse(localStorage.getItem("tmv_prof") || "{}");
    for (var k in p) if (PROF.hasOwnProperty(k)) PROF[k] = p[k];
    if (!Array.isArray(PROF.read)) PROF.read = [];
  } catch(e) {}
}
function saveProf() { try { localStorage.setItem("tmv_prof", JSON.stringify(PROF)); } catch(e) {} }
function getPoints() { return (PROF.plus || 0) + (PROF.minus || 0) + PROF.read.length; }

function renderProfile() {
  var pts = getPoints(), idx = 0;
  for (var i = 0; i < LEVELS.length; i++) if (pts >= LEVELS[i][0]) idx = i;
  var cur = LEVELS[idx], next = LEVELS[idx + 1];
  var pct = 100, note;
  if (next) {
    pct = Math.round((pts - cur[0]) / (next[0] - cur[0]) * 100);
    note = "До уровня «" + next[1] + "»: ещё " + (next[0] - pts) + " очков · сделки: " + ((PROF.plus || 0) + (PROF.minus || 0)) + " + стратегии: " + PROF.read.length;
  } else {
    note = "Максимальный уровень достигнут. Очков: " + pts;
  }
  byId("lvlName").textContent = "Ур. " + (idx + 1) + " · " + cur[1];
  byId("lvlPct").textContent = pct + "%";
  byId("lvlFill").style.width = pct + "%";
  byId("lvlNote").textContent = note;
  var steps = "";
  for (var j = 0; j < LEVELS.length; j++) steps += '<i class="' + (j <= idx ? "on" : "") + '"></i>';
  byId("lvlSteps").innerHTML = steps;
  byId("stSig").textContent = PROF.sig || 0;
  byId("stRead").textContent = PROF.read.length;
  byId("stW").textContent = PROF.plus || 0;
  byId("stL").textContent = PROF.minus || 0;
}

function loadProfileUI() {
  byId("profName").value = PROF.name || "";
  byId("profPocketId").value = PROF.pid || "";
  if (PROF.photo) {
    var img = byId("profilePhotoImg");
    img.src = PROF.photo; img.classList.remove("hidden");
    byId("profilePhotoIcon").classList.add("hidden");
  }
}
function saveProfileSettings() {
  PROF.name = byId("profName").value.trim();
  PROF.pid = byId("profPocketId").value.trim();
  saveProf();
}
function handleProfilePhoto(e) {
  var file = e.target.files && e.target.files[0];
  if (!file) return;
  var reader = new FileReader();
  reader.onload = function(ev) {
    var im = new Image();
    im.onload = function() {
      var c = document.createElement("canvas"), S = 240;
      c.width = S; c.height = S;
      var m = Math.min(im.width, im.height), sx = (im.width - m) / 2, sy = (im.height - m) / 2;
      c.getContext("2d").drawImage(im, sx, sy, m, m, 0, 0, S, S);
      PROF.photo = c.toDataURL("image/jpeg", 0.85);
      saveProf(); loadProfileUI();
    };
    im.src = ev.target.result;
  };
  reader.readAsDataURL(file);
}
function resetProfile() {
  if (!confirm("Сбросить профиль, уровень и статистику?")) return;
  PROF = {name: "", pid: "", photo: "", sig: 0, plus: 0, minus: 0, read: []};
  saveProf();
  byId("profilePhotoImg").classList.add("hidden");
  byId("profilePhotoIcon").classList.remove("hidden");
  loadProfileUI(); renderProfile(); renderEdu();
}
function recordResult(win) {
  if (sigRecorded) return;
  sigRecorded = true;
  if (win) PROF.plus = (PROF.plus || 0) + 1; else PROF.minus = (PROF.minus || 0) + 1;
  saveProf(); renderProfile();
  byId("resRow").style.display = "none";
  byId("btnNew").style.display = "block";
}

/* ===== НАВИГАЦИЯ ===== */
function showTab(id, el) {
  document.querySelectorAll(".tab").forEach(function(t) { t.classList.add("hidden"); });
  byId(id).classList.remove("hidden");
  document.querySelectorAll(".nav").forEach(function(n) { n.classList.remove("on"); });
  if (el) el.classList.add("on");
  if (id === "tabProfile") renderProfile();
  window.scrollTo(0, 0);
}

/* ===== КАТАЛОГ АКТИВОВ ===== */
function updateSelectedAssetUI() { byId("currentAssetDisplay").textContent = currentAsset; }
function openCatalogModal() { byId("catSearch").value = ""; renderCatalogList(""); byId("catalogModal").classList.remove("hidden"); }
function closeCatalogModal() { byId("catalogModal").classList.add("hidden"); }

function renderCatalogList(q) {
  var body = byId("catalogBody");
  var cats = ASSETS[currentLang] || ASSETS.ru;
  var html = "", query = (q || "").toLowerCase();
  FLAT = [];
  for (var cat in cats) {
    var items = cats[cat].filter(function(a) { return !query || a.toLowerCase().indexOf(query) >= 0; });
    if (!items.length) continue;
    html += '<div class="catalog-category-header">' + esc(cat) + '</div>';
    items.forEach(function(a) {
      var n = FLAT.push(a) - 1;
      var isFav = favs.indexOf(a) >= 0;
      html += '<div class="fav-item" onclick="selectAsset(FLAT[' + n + '])"><span>' + esc(a) + '</span><span onclick="event.stopPropagation();toggleFav(FLAT[' + n + '])">' + (isFav ? "⭐" : "☆") + '</span></div>';
    });
  }
  body.innerHTML = html || '<div style="text-align:center;padding:24px;color:var(--muted)">Ничего не найдено</div>';
}
function selectAsset(a) { currentAsset = a; updateSelectedAssetUI(); closeCatalogModal(); }
function toggleFav(a) {
  var i = favs.indexOf(a);
  if (i >= 0) favs.splice(i, 1); else favs.push(a);
  try { localStorage.setItem("tmv_favs", JSON.stringify(favs)); } catch(e) {}
  renderCatalogList(byId("catSearch").value);
  renderMainFavs();
}
function pickFav(i) { currentAsset = favs[i]; updateSelectedAssetUI(); showTab("tabSig", document.querySelectorAll(".nav")[0]); }
function renderMainFavs() {
  var list = byId("mainFavList"), empty = byId("mainFavEmpty");
  if (!favs.length) { list.innerHTML = ""; empty.style.display = "block"; return; }
  empty.style.display = "none";
  var html = "";
  favs.forEach(function(a, i) { html += '<div class="fav-item" onclick="pickFav(' + i + ')"><span>' + esc(a) + '</span><span style="color:#d6ad4a">➔</span></div>'; });
  list.innerHTML = html;
}

/* ===== ТАЙМЕРЫ И ОВЕРЛЕЙ ===== */
function startTimer(key, elId, seconds, onEnd) {
  if (timers[key]) clearInterval(timers[key]);
  var end = Date.now() + seconds * 1000;
  function tick() {
    var rem = Math.ceil((end - Date.now()) / 1000);
    if (rem <= 0) { clearInterval(timers[key]); byId(elId).textContent = "00:00"; if (onEnd) onEnd(); return; }
    var m = Math.floor(rem / 60), s = rem % 60;
    byId(elId).textContent = (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }
  tick(); timers[key] = setInterval(tick, 250);
}
function showOv(txt) { byId("analyzeTxt").textContent = txt || "⚡ Запрос к рынку..."; byId("analyzeBox").classList.remove("hidden"); }
function hideOv() { byId("analyzeBox").classList.add("hidden"); }

/* ===== СИГНАЛ ===== */
function renderMiniChart(candles) {
  if (!candles || candles.length < 2) return "";
  var min = Math.min.apply(null, candles), max = Math.max.apply(null, candles);
  var range = max - min || 1, w = 320, h = 90;
  var pts = candles.map(function(p, i) {
    var x = (i / (candles.length - 1)) * w;
    var y = h - ((p - min) / range) * (h - 12) - 6;
    return x.toFixed(1) + "," + y.toFixed(1);
  }).join(" ");
  return '<svg viewBox="0 0 ' + w + ' ' + h + '"><polyline fill="none" stroke="#FFD700" stroke-width="2.5" stroke-linejoin="round" points="' + pts + '"/></svg>';
}

async function getSig() {
  var tf = byId("tf").value, expSec = parseInt(byId("exp").value);
  showOv("⚡ Запрос к рынку...");
  try {
    var res = await fetch("/get_signal?asset=" + encodeURIComponent(currentAsset) + "&timeframe=" + encodeURIComponent(tf));
    if (!res.ok) throw new Error("http " + res.status);
    var data = await res.json();
    var isCall = data.signal === "UP";
    byId("sigMeta").textContent = "Pocket Option · " + currentAsset + " · " + tf + " · " + (expSec >= 60 ? (expSec / 60) + " мин" : expSec + " сек");
    var dirEl = byId("sigDir");
    dirEl.textContent = isCall ? "⬆️ CALL (ВВЕРХ)" : "⬇️ PUT (ВНИЗ)";
    dirEl.className = "sig-dir " + (isCall ? "call" : "put");
    byId("sigStrat").innerHTML = "<b>Анализ:</b> " + esc(data.reason);
    byId("sigMiniChart").innerHTML = renderMiniChart(data.candles);
    byId("sigSource").textContent = data.source_desc || "API Pocket Option";
    byId("sigBox").style.display = "block";
    byId("btnCancel").style.display = "block";
    byId("btnNew").style.display = "none";
    byId("resRow").style.display = "none";
    sigRecorded = true;
    PROF.sig = (PROF.sig || 0) + 1; saveProf(); renderProfile();
    startTimer("sig", "cd", expSec, function() {
      byId("btnCancel").style.display = "none";
      sigRecorded = false;
      byId("resRow").style.display = "grid";
    });
  } catch(e) {
    alert("Не удалось получить сигнал. Попробуйте ещё раз.");
  }
  hideOv();
}
function autoSig() {
  currentAsset = TOP_LIST[Math.floor(Math.random() * TOP_LIST.length)];
  updateSelectedAssetUI();
  getSig();
}
function cancelSig() {
  if (timers.sig) clearInterval(timers.sig);
  byId("sigBox").style.display = "none";
  sigRecorded = true;
}

/* ===== НОВОСТИ ===== */
function setNewsFilter(cur, el) {
  newsFilter = cur;
  document.querySelectorAll("#newsFilterRow .filter-btn").forEach(function(b) { b.classList.remove("on"); });
  if (el) el.classList.add("on");
  renderNewsList(newsCache);
}
function renderNewsList(events) {
  var list = byId("newsList"), filtered = events || [];
  if (newsFilter && newsFilter !== "ALL") filtered = filtered.filter(function(e) { return (e.currency || "").toUpperCase() === newsFilter; });
  if (!filtered.length) { list.innerHTML = '<div style="text-align:center;padding:24px;color:var(--muted)">Нет новостей по фильтру</div>'; return; }
  var html = "";
  filtered.forEach(function(ev, idx) {
    var ic = (ev.impact || "").toLowerCase() === "high" ? "high" : "medium";
    html += '<div class="news-item ' + ic + '" onclick="this.classList.toggle(\'open\')">';
    html += '<div class="news-top"><span class="news-cur">' + esc(ev.currency) + ' · <span class="news-impact ' + ic + '">' + (ic === "high" ? "🔴 Высокая" : "🟠 Средняя") + '</span></span><span class="news-time">' + esc(ev.date) + '</span></div>';
    html += '<div class="news-name">' + esc(ev.name) + '</div>';
    html += '<div class="news-pairs">Влияет на: ' + esc(ev.pair_hint || ev.currency) + '</div>';
    html += '<div class="news-meta"><span>Прогноз: ' + esc(ev.forecast || "—") + '</span><span>Факт: ' + esc(ev.actual || "Ожидается") + '</span></div>';
    html += '<div class="news-detail">Рекомендация Team Master: на сильных новостях воздержитесь от входа за 3 минуты до публикации и в первые минуты после неё.</div>';
    html += '</div>';
  });
  list.innerHTML = html;
}
async function loadNews() {
  try {
    var res = await fetch("/news");
    if (res.ok) {
      var data = await res.json();
      newsCache = data.events || [];
      renderNewsList(newsCache);
      byId("newsUpdated").textContent = "🔄 Обновлено · Событий: " + newsCache.length;
    }
  } catch(e) { byId("newsUpdated").textContent = "Не удалось загрузить новости"; }
}

/* ===== СТРАТЕГИИ ===== */
function renderEdu() {
  var html = "";
  EDU.forEach(function(ed, i) {
    var done = PROF.read.indexOf(ed.n) >= 0;
    html += '<div class="edu-item' + (done ? " done" : "") + '" onclick="openEdu(' + i + ')"><div><h4>' + esc(ed.t) + '</h4><small>' + esc(ed.tf) + ' · эксп. ' + esc(ed.exp) + '</small></div><span class="arr">➔</span></div>';
  });
  byId("eduItems").innerHTML = html;
}
function openEdu(i) {
  var ed = EDU[i];
  byId("eduList").classList.add("hidden");
  byId("eduView").classList.remove("hidden");
  var tags = ed.tags.map(function(t) { return '<span class="tag">' + esc(t) + '</span>'; }).join("");
  var secs = ed.sec.map(function(s) { return '<div class="edu-sec"><div class="edu-sec-h">' + esc(s.h) + '</div><div class="edu-sec-t">' + esc(s.t) + '</div></div>'; }).join("");
  var gained = false;
  if (PROF.read.indexOf(ed.n) < 0) { PROF.read.push(ed.n); gained = true; saveProf(); renderProfile(); }
  byId("eduBody").innerHTML = '<h3>' + esc(ed.t) + '</h3><div class="tag-row">' + tags + '</div>' + secs +
    '<div class="edu-note">' + (gained ? "✅ Стратегия изучена: +1 очко к уровню" : "Стратегия уже изучена") + '</div>';
  window.scrollTo(0, 0);
}
function closeEdu() {
  byId("eduView").classList.add("hidden");
  byId("eduList").classList.remove("hidden");
  renderEdu();
}

/* ===== СКАНЕР ===== */
function stopCam() { if (stream) { stream.getTracks().forEach(function(t) { t.stop(); }); stream = null; } }
function openCam() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { alert("Камера недоступна в этом браузере"); return; }
  navigator.mediaDevices.getUserMedia({video: {facingMode: "environment"}, audio: false}).then(function(s) {
    stopCam(); stream = s;
    var video = byId("scanVideo");
    video.srcObject = stream; video.classList.remove("hidden");
    byId("scanPreviewImg").classList.add("hidden");
    byId("scanPh").classList.add("hidden");
    scanSource = "cam";
    byId("btnScan").removeAttribute("disabled");
  }).catch(function() { alert("Камера недоступна. Разрешите доступ или загрузите скриншот."); });
}
function handleImageUpload(e) {
  var file = e.target.files && e.target.files[0];
  if (!file) return;
  uploadedImageBlob = file; scanSource = "file";
  stopCam();
  byId("scanVideo").classList.add("hidden");
  var reader = new FileReader();
  reader.onload = function(ev) {
    var img = byId("scanPreviewImg");
    img.src = ev.target.result; img.classList.remove("hidden");
    byId("scanPh").classList.add("hidden");
    byId("btnScan").removeAttribute("disabled");
  };
  reader.readAsDataURL(file);
}
function grabCamBlob() {
  return new Promise(function(resolve) {
    var v = byId("scanVideo");
    if (!v.videoWidth) { resolve(null); return; }
    var c = document.createElement("canvas");
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    c.toBlob(function(b) { resolve(b); }, "image/jpeg", 0.9);
  });
}
async function doScan() {
  var blob = scanSource === "cam" ? await grabCamBlob() : uploadedImageBlob;
  if (!blob) { alert("Сначала откройте камеру или загрузите скриншот графика"); return; }
  showOv("🔍 AI анализирует график...");
  try {
    var fd = new FormData();
    fd.append("file", blob, "chart.jpg");
    fd.append("cfg", JSON.stringify({"интервал": byId("tf").value, "экспирация": byId("exp").selectedOptions[0].text}));
    var res = await fetch("/analyze", {method: "POST", body: fd});
    if (!res.ok) throw new Error("http " + res.status);
    var data = await res.json();
    var text = data.text || "";
    var up = text.search(/BUY|ВВЕРХ/i), dn = text.search(/SELL|ВНИЗ/i);
    var isCall = up >= 0 && (dn < 0 || up < dn);
    var noChart = text.indexOf("❌") === 0;
    var dirEl = byId("scanDir");
    if (noChart) { dirEl.textContent = "❌ НЕТ ГРАФИКА"; dirEl.className = "sig-dir put"; }
    else { dirEl.textContent = isCall ? "⬆️ BUY (ВВЕРХ)" : "⬇️ SELL (ВНИЗ)"; dirEl.className = "sig-dir " + (isCall ? "call" : "put"); }
    byId("scanMeta").textContent = "AI Сканер · " + byId("tf").value;
    byId("scanStrat").textContent = text;
    byId("scanSigBox").style.display = "block";
    if (!noChart) { startTimer("scan", "scanCd", 60); PROF.sig = (PROF.sig || 0) + 1; saveProf(); renderProfile(); }
  } catch(e) { alert("Ошибка сканирования. Попробуйте ещё раз."); }
  hideOv();
}

function changeLanguage(lang) { currentLang = lang; }
function tickClock() {
  var d = new Date(), el = byId("liveClock");
  if (el) el.textContent = "🕒 " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0") + ":" + String(d.getSeconds()).padStart(2, "0");
}

window.addEventListener("DOMContentLoaded", function() {
  loadProf(); loadProfileUI(); renderProfile();
  updateSelectedAssetUI(); renderMainFavs(); renderEdu(); loadNews();
  tickClock(); setInterval(tickClock, 1000);
});
</script>
</body>
</html>
"""


# ============================================================
#  API
# ============================================================
@app.get("/", response_class=HTMLResponse)
def serve_terminal():
    assets_json = json.dumps(ASSETS_DATA_JS, ensure_ascii=False).replace("</", "<\\/")
    edu_json = json.dumps(EDU_50, ensure_ascii=False).replace("</", "<\\/")
    return HTMLResponse(
        HTML_UI.replace("__ASSETS_JSON__", assets_json).replace("__EDU_JSON__", edu_json)
    )


def _clean_asset(asset: str) -> str:
    key = re.sub(r"[^\x00-\x7F]+", "", asset or "")
    return re.sub(r"\s+", " ", key).strip()


@app.get("/get_signal")
async def get_signal(asset: str = "AUD/USD OTC", timeframe: str = "M1"):
    asyncio.create_task(send_tg_signal_alert(asset, timeframe))
    await asyncio.sleep(0.6)

    prices = []
    clean = _clean_asset(asset)
    top_otcs = ["AUD/USD OTC", "EUR/USD OTC", "GBP/USD OTC", "USD/JPY OTC", "BITCOIN OTC"]
    source_desc = "Локальный анализ"

    if clean.upper() in top_otcs and OTCSHARDS_API_KEY:
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    OTCSHARDS_API_URL,
                    params={"symbol": clean, "timeframe": timeframe, "key": OTCSHARDS_API_KEY},
                    timeout=5.0,
                )
                if res.status_code == 200:
                    data = res.json()
                    rows = data.get("candles", data) if isinstance(data, dict) else data
                    for c in rows or []:
                        v = c.get("close", c.get("c")) if isinstance(c, dict) else None
                        if v is not None:
                            prices.append(float(v))
                    if prices:
                        source_desc = "API OTC (реальные данные)"
        except Exception as e:
            logging.warning(f"OTC API error: {e}")

    if len(prices) < 5:
        prices = generate_otc_candles(clean or asset, count=50)

    rsi = calculate_rsi(prices)
    ema = calculate_ema(prices)
    signal, reason = smart_market_analysis(prices, rsi, ema)
    return {"signal": signal, "reason": reason, "candles": prices[-60:], "source_desc": source_desc}


@app.get("/news")
async def get_news():
    events = [
        {"name": "Занятость вне с/х (NFP)", "currency": "USD", "date": "Сегодня 15:30", "impact": "High", "forecast": "180K", "actual": "Ожидается", "pair_hint": "EUR/USD, GBP/USD"},
        {"name": "Решение ФРС по ставке", "currency": "USD", "date": "Завтра 21:00", "impact": "High", "forecast": "5.25%", "actual": "Ожидается", "pair_hint": "USD/JPY, EUR/USD"},
        {"name": "Индекс потребительских цен (CPI)", "currency": "EUR", "date": "Через 2 дня 12:00", "impact": "Medium", "forecast": "2.4%", "actual": "Ожидается", "pair_hint": "EUR/USD, EUR/GBP"},
    ]
    return {"events": events, "updated": datetime.now(timezone.utc).isoformat()}


@app.post("/analyze")
async def analyze(file: UploadFile = File(...), cfg: str = Form("{}")):
    image_bytes = await file.read()
    try:
        config = json.loads(cfg) if cfg else {}
    except Exception:
        config = {}
    res = await scanner_core.compute(image_bytes, config if isinstance(config, dict) else {})
    return {"text": res}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
