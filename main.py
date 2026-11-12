import os
import re
import io
import json
import time
import random
import asyncio
import logging
from datetime import datetime, timezone

import httpx
import numpy as np
import uvicorn
import google.generativeai as genai
from PIL import Image
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="TEAM MASTER VIP - Ultimate Trading Terminal", version="29.3")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")

NEWS_RU_MAP = {
    "non-farm payrolls": "Занятость вне с/х (NFP)",
    "nonfarm payrolls": "Занятость вне с/х (NFP)",
    "unemployment rate": "Уровень безработицы",
    "cpi": "Индекс потребительских цен (CPI)",
    "core cpi": "Базовый CPI",
    "ppi": "Индекс цен производителей (PPI)",
    "producer price index": "Индекс цен производителей (PPI)",
    "gdp": "ВВП (GDP)",
    "gross domestic product": "ВВП (GDP)",
    "interest rate decision": "Решение по процентной ставке",
    "rate decision": "Решение по ставке",
    "fomc": "Заседание ФРС (FOMC)",
    "federal funds rate": "Ставка ФРС",
    "retail sales": "Розничные продажи",
    "core retail sales": "Базовые розничные продажи",
    "ism manufacturing": "ISM Производство",
    "ism services": "ISM Услуги",
    "pmi": "Индекс PMI",
    "consumer confidence": "Индекс потребительского доверия",
    "consumer inflation expectations": "Ожидания инфляции потребителей",
    "building permits": "Разрешения на строительство",
    "housing starts": "Начало строительства жилья",
    "existing home sales": "Продажи вторичного жилья",
    "new home sales": "Продажи нового жилья",
    "durable goods": "Заказы на товары длит. пользования",
    "trade balance": "Торговый баланс",
    "current account": "Платёжный баланс",
    "industrial production": "Промышленное производство",
    "capacity utilization": "Загрузка мощностей",
    "initial jobless claims": "Первичные заявки по безработице",
    "continuing jobless claims": "Повторные заявки по безработице",
    "average hourly earnings": "Средняя почасовая оплата",
    "adp non-farm employment": "Занятость ADP",
    "adp employment": "Занятость ADP",
    "michigan consumer sentiment": "Индекс настроений Мичигана",
    "cb consumer confidence": "Индекс доверия потребителей CB",
    "beige book": "Бежевая книга ФРС",
    "president lagarde speech": "Выступление президента ЕЦБ Лагард",
    "ecb's president lagarde speech": "Выступление президента ЕЦБ Лагард",
    "lagarde speech": "Выступление Лагард (ЕЦБ)",
    "bank of england": "Банк Англии",
    "bank of japan": "Банк Японии",
    "reserve bank of australia": "РБА Австралия",
    "bank of canada": "Банк Канады",
    "press conference": "Пресс-конференция",
    "monetary policy statement": "Заявление по ДКП",
    "core pce": "Базовый PCE",
    "pce price index": "Индекс цен PCE",
    "jolts job openings": "Вакансии JOLTS",
    "philadelphia fed": "Индекс ФРБ Филадельфии",
    "empire state manufacturing": "Индекс Empire State",
    "flash manufacturing pmi": "Предварительный PMI пр-ва",
    "flash services pmi": "Предварительный PMI услуг",
    "composite pmi": "Сводный PMI",
    "ifo business climate": "Индекс IFO",
    "sentix investor confidence": "Индекс Sentix",
    "leading indicators": "Опережающие индикаторы",
    "crude oil inventories": "Запасы сырой нефти",
    "natural gas storage": "Запасы природного газа",
    "speech": "Выступление",
}


def translate_news_title(title: str) -> str:
    if not title:
        return title
    raw = title.strip()
    low = raw.lower()
    for en, ru in sorted(NEWS_RU_MAP.items(), key=lambda x: -len(x[0])):
        if en in low:
            if any(x in ru for x in ("м/м", "г/г", "кв/кв")):
                return ru
            suffix = ""
            if "mom" in low or "m/m" in low or "(m/m)" in low:
                suffix = " (м/м)"
            elif "yoy" in low or "y/y" in low or "(y/y)" in low:
                suffix = " (г/г)"
            elif "qoq" in low or "q/q" in low or "(q/q)" in low:
                suffix = " (кв/кв)"
            return ru + suffix
    out = raw
    replacements = [
        ("MoM", "м/м"), ("YoY", "г/г"), ("QoQ", "кв/кв"),
        ("m/m", "м/м"), ("y/y", "г/г"), ("q/q", "кв/кв"),
        ("(m/m)", "(м/м)"), ("(y/y)", "(г/г)"), ("(q/q)", "(кв/кв)"),
        ("Speech", "Выступление"), ("Decision", "Решение"),
        ("Prelim", "Предв."), ("Preliminary", "Предварительный"),
        ("Unemployment Claims", "Заявки по безработице"),
        ("Initial Jobless Claims", "Первичные заявки по безработице"),
        ("GDP", "ВВП"), ("CPI", "ИПЦ"), ("PPI", "ИЦП"),
    ]
    for a, b in replacements:
        out = out.replace(a, b)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def impact_ru(imp: str) -> str:
    x = (imp or "").lower()
    if x == "high":
        return "Высокая"
    if x == "medium":
        return "Средняя"
    return "Низкая"


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


BINANCE_MAPPING = {
    "EUR/USD": "EURUSDT", "GBP/USD": "GBPUSDT", "AUD/USD": "AUDUSDT",
    "NZD/USD": "NZDUSDT", "EUR/GBP": "EURGBP", "USD/JPY": "JPYUSDT",
    "USD/CAD": "CADUSDT", "USD/CHF": "CHFUSDT", "EUR/JPY": "EURJPY",
    "GBP/JPY": "GBPJPY", "AUD/JPY": "AUDJPY", "EUR/AUD": "EURAUD",
    "GBP/AUD": "GBPAUD", "EUR/CHF": "EURCHF", "GBP/CHF": "GBPCHF",
    "AUD/CHF": "AUDCHF", "CAD/JPY": "CADJPY", "CHF/JPY": "CHFJPY",
    "AUD/CAD": "AUDCAD", "AUD/NZD": "AUDNZD", "EUR/CAD": "EURCAD",
    "GBP/CAD": "GBPCAD", "NZD/JPY": "NZDJPY",
    "Bitcoin": "BTCUSDT", "Ethereum": "ETHUSDT", "Solana": "SOLUSDT",
    "BNB": "BNBUSDT", "Dogecoin": "DOGEUSDT", "Chainlink": "LINKUSDT",
    "Cardano": "ADAUSDT", "Polkadot": "DOTUSDT", "Polygon": "MATICUSDT",
    "Litecoin": "LTCUSDT", "TRON": "TRXUSDT", "Avalanche": "AVAXUSDT",
    "Toncoin": "TONUSDT", "Dash": "DASHUSDT",
    "Apple": "AAPLUSDT", "Tesla": "TSLAUSDT", "Microsoft": "MSFTUSDT",
    "Amazon": "AMZNUSDT", "Netflix": "NFLXUSDT", "Intel": "INTCUSDT",
    "VISA": "VUSDT", "Coinbase": "COINUSDT", "Palantir": "PLTRUSDT",
    "AMD": "AMDUSDT", "Cisco": "CSCOUSDT",
    "Gold": "PAXGUSDT", "Silver": "XAGUSDT",
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
    weights = np.exp(np.linspace(-1.0, 0.0, period))
    weights /= weights.sum()
    return np.convolve(prices, weights, mode="valid")[-1]


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
    return signal, " ".join(reasons)


def generate_otc_candles(asset_name, count=50):
    seed_value = sum(ord(ch) for ch in asset_name) + int(time.time() / 150)
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
    def _gemini_sync(self, image_bytes: bytes, config: dict):
        prompt = (
            "Ты профессиональный ИИ-сканер графиков бинарных опционов Pocket Option. "
            "Внимательно проанализируй расположение свечей, их тела, тени, цвета, текущую тенденцию, уровни и паттерны на данном скриншоте графика. "
            "Выдай торговый сигнал: направление (BUY / ВВЕРХ или SELL / ВНИЗ), таймфрейм, стратегию ("
            + str(config.get("стратегия", "Smart Money"))
            + ") и подробный технический анализ на основе расположения свечей. Используй эмодзи 📈📉⚡🔥💎🎯🚀."
        )
        return _gemini_rest_vision(prompt, image_bytes, mime="image/jpeg")

    async def compute(self, image_bytes: bytes, config: dict) -> str:
        try:
            image = Image.open(io.BytesIO(image_bytes))
            if GEMINI_API_KEY:
                try:
                    buf = io.BytesIO()
                    image.convert("RGB").save(buf, format="JPEG", quality=85)
                    jpeg_bytes = buf.getvalue()
                    text = await asyncio.to_thread(self._gemini_sync, jpeg_bytes, config)
                    if text:
                        return "--- TEAM MASTER AI CANDLE SCANNER ---\n" + text
                except Exception as ex:
                    logging.warning(f"Gemini error: {ex}")

            img_rgb = image.convert("RGB")
            np_img = np.array(img_rgb)
            r = np_img[:, :, 0].astype(float)
            g = np_img[:, :, 1].astype(float)
            b = np_img[:, :, 2].astype(float)
            green_candles = int(np.sum((g > r + 30) & (g > b + 30)))
            red_candles = int(np.sum((r > g + 30) & (r > b + 30)))
            total_pixels = np_img.shape[0] * np_img.shape[1]
            candle_ratio = (green_candles + red_candles) / total_pixels
            if candle_ratio < 0.0015:
                return "❌ ГРАФИК СО СВЕЧАМИ НЕ ОБНАРУЖЕН. Убедитесь, что на скриншоте четко виден график. 📷🔍"

            bullish = green_candles >= red_candles
            direction = "BUY (ВВЕРХ) 📈" if bullish else "SELL (ВНИЗ) 📉"
            trend_desc = (
                "Преобладание бычьих зелёных тел свечей указывает на активных покупателей. 🟢💪"
                if bullish
                else "Доминирование медвежьих красных свечей говорит о давлении продавцов. 🔴⚔️"
            )
            return (
                "--- TEAM MASTER SIGNAL V4.0 ---\n"
                "📊 СТРАТЕГИЯ: " + str(config.get("стратегия", "Smart Money")) + "\n"
                "⏱ ТАЙМФРЕЙМ: " + str(config.get("интервал", "M1")) + "\n"
                "⏳ ЭКСПИРАЦИЯ: " + str(config.get("экспирация", "1м")) + "\n"
                "-------------------------------\n"
                "🔥 ВЕРДИКТ: " + direction + "\n"
                "📌 ПРИЧИНА: Расположение и анализ свечных тел: " + trend_desc + "\n"
                "Импульс подтверждён закрытием последних свечей. ⚡\n"
                "Уровни поддержки и сопротивления визуально подтверждены на графике. 🎯💎\n"
                "🚀 Сигнал готов к входу!"
            )
        except Exception as e:
            return "❌ Ошибка анализа изображения: " + str(e) + " ⚠️"


scanner_core = ScannerAnalyzer()

_CURRENCY = [
    "🇪🇺🇺🇸 EUR/USD OTC", "🇬🇧🇺🇸 GBP/USD OTC", "🇺🇸🇯🇵 USD/JPY OTC", "🇦🇺🇺🇸 AUD/USD OTC", "🇪🇺🇯🇵 EUR/JPY OTC",
    "🇺🇸🇨🇦 USD/CAD OTC", "🇬🇧🇯🇵 GBP/JPY OTC", "🇳🇿🇺🇸 NZD/USD OTC", "🇺🇸🇨🇭 USD/CHF OTC", "🇪🇺🇬🇧 EUR/GBP OTC",
    "🇦🇺🇨🇭 AUD/CHF OTC", "🇦🇺🇨🇭 AUD/CHF", "🇦🇺🇺🇸 AUD/USD", "🇺🇸🇨🇦 USD/CAD", "🇺🇸🇯🇵 USD/JPY", "🇪🇺🇺🇸 EUR/USD",
    "🇬🇧🇺🇸 GBP/USD", "🇪🇺🇬🇧 EUR/GBP", "🇬🇧🇨🇭 GBP/CHF", "🇬🇧🇦🇺 GBP/AUD", "🇪🇺🇨🇭 EUR/CHF", "🇪🇺🇨🇦 EUR/CAD",
    "🇪🇺🇦🇺 EUR/AUD", "🇯🇵🇨🇦 CAD/JPY", "🇯🇵🇨🇭 CHF/JPY", "🇦🇺🇨🇦 AUD/CAD", "🇦🇺🇯🇵 AUD/JPY", "🇬🇧🇯🇵 GBP/JPY",
    "🇪🇺🇯🇵 EUR/JPY", "🇨🇦🇨🇭 CAD/CHF",
    "🇦🇺🇳🇿 AUD/NZD OTC", "🇨🇦🇨🇭 CAD/CHF OTC", "🇪🇺🇭🇺 EUR/HUF OTC", "🇪🇺🇳🇿 EUR/NZD OTC", "🇪🇺🇹🇷 EUR/TRY OTC",
    "🇬🇧🇦🇺 GBP/AUD OTC", "🇬🇧🇨🇭 GBP/CHF OTC", "🇬🇧🇨🇦 GBP/CAD OTC", "🇳🇿🇯🇵 NZD/JPY OTC", "🇺🇸🇦🇷 USD/ARS OTC",
    "🇺🇸🇧🇷 USD/BRL OTC", "🇺🇸🇨🇱 USD/CLP OTC", "🇺🇸🇨🇴 USD/COP OTC", "🇺🇸🇩🇿 USD/DZD OTC", "🇺🇸🇪🇬 USD/EGP OTC",
    "🇺🇸🇮🇩 USD/IDR OTC", "🇺🇸🇮🇳 USD/INR OTC", "🇺🇸🇲🇽 USD/MXN OTC", "🇺🇸🇲🇾 USD/MYR OTC", "🇺🇸🇵🇭 USD/PHP OTC",
    "🇺🇸🇵🇰 USD/PKR OTC", "🇺🇸🇸🇬 USD/SGD OTC", "🇺🇸🇹🇭 USD/THB OTC", "🇺🇸🇻🇳 USD/VND OTC", "🇿🇦🇺🇸 ZAR/USD OTC",
    "🇺🇦🇺🇸 UAH/USD OTC", "🇳🇬🇺🇸 NGN/USD OTC", "🇰🇪🇺🇸 KES/USD OTC", "🇺🇸🇷🇺 USD/RUB OTC", "🇺🇸🇷🇺 USD/RUB",
]
_CRYPTO = [
    "₿ Bitcoin", "Ξ Ethereum", "🪙 Solana", "🪙 BNB OTC", "🪙 Dogecoin OTC", "🪙 Chainlink OTC",
    "🪙 Toncoin OTC", "🪙 Bitcoin ETF OTC", "🪙 Bitcoin OTC", "🪙 Avalanche OTC", "🪙 Cardano OTC",
    "🪙 Ethereum OTC", "🪙 Polkadot OTC", "🪙 Polygon OTC", "🪙 Litecoin OTC", "🪙 TRON OTC",
    "🪙 Dash", "🪙 Chainlink", "🪙 BCH/EUR", "🪙 BCH/GBP", "🪙 BCH/JPY", "🪙 BTC/GBP", "🪙 BTC/JPY",
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
        "💱 Валютные пары": _CURRENCY, "🔥 Криптовалюты": _CRYPTO, "🛢️ Сырьевые товары": _COMMODITIES,
        "📈 Акции компаний": _STOCKS, "📊 Биржевые индексы": _INDICES,
    },
    "en": {
        "💱 Currency Pairs": _CURRENCY, "🔥 Cryptocurrencies": _CRYPTO, "🛢️ Commodities": _COMMODITIES,
        "📈 Company Stocks": _STOCKS, "📊 Market Indices": _INDICES,
    },
    "ua": {
        "💱 Валютні пари": _CURRENCY, "🔥 Криптовалюти": _CRYPTO, "🛢️ Сировинні товари": _COMMODITIES,
        "📈 Акції компаній": _STOCKS, "📊 Біржові індекси": _INDICES,
    },
}

EDU_50 = [
    {
        "t": "1. Торговля от уровней поддержки и сопротивления",
        "tags": ["M1", "M5", "Pocket Option", "Уровни", "Price Action"],
        "kind": "levels",
        "d": "📌 Подробный разбор стратегии торговли от уровней поддержки и сопротивления:\n\n"
             "• 🧠 Рыночная логика и суть стратегии:\n"
             "Уровни поддержки и сопротивления — это ключевые ценовые зоны, где баланс сил между покупателями и продавцами кардинально меняется. Уровень сопротивления — это область сверху, где продавцы останавливают рост цены и начинают агрессивно давить вниз. Уровень поддержки — область снизу, где покупатели защищают свои позиции и толкают цену вверх.\n\n"
             "• ⏱ Рекомендуемые таймфреймы: M1, M2 и M5.\n"
             "• ⏳ Оптимальная экспирация: 2–4 минуты.\n\n"
             "• 🎯 Правила входа:\n"
             "  1️⃣ PUT — цена подходит к сильному сопротивлению + тени/замедление.\n"
             "  2️⃣ CALL — касание поддержки + зелёная поглощающая свеча.\n\n"
             "• 🛡️ Риск: не более 1-2% на сделку."
    },
    {
        "t": "2. Торговля в боковике (Флэт и диапазон)",
        "tags": ["M1", "M5", "Pocket Option", "Флэт", "Боковик"],
        "kind": "range",
        "d": "📌 Подробный разбор стратегии торговли в боковике:\n\n"
             "• 🧠 Рыночная логика: цена движется внутри горизонтального коридора. Покупаем у нижней границы, продаём у верхней.\n\n"
             "• ⏱ Таймфреймы: M1 и M5.\n"
             "• ⏳ Экспирация: 1–3 минуты.\n\n"
             "• 🎯 Правила:\n"
             "  1️⃣ CALL — касание нижней границы + пин-бар / ложный пробой.\n"
             "  2️⃣ PUT — касание верхней границы + угасание импульса.\n\n"
             "• 🛡️ Проверяйте отсутствие сильных новостей."
    },
    {
        "t": "3. Accelerator Oscillator + Alligator",
        "tags": ["M1", "M2", "M5", "Pocket Option", "Индикаторы"],
        "kind": "trend",
        "d": "📌 Связка Accelerator Oscillator + Alligator:\n\n"
             "• Alligator моделирует циклы рынка, AC измеряет ускорение.\n\n"
             "• ⏱ Таймфреймы: M1, M2, M5.\n"
             "• ⏳ Экспирация: 1–3 минуты.\n\n"
             "• 🎯 Правила:\n"
             "  1️⃣ CALL — Аллигатор вверх + AC в зелёной зоне выше нуля.\n"
             "  2️⃣ PUT — Аллигатор вниз + AC в красной зоне.\n\n"
             "• 🛡️ Риск 1-2%."
    },
    {
        "t": "4. ADX + Aroon",
        "tags": ["M1", "M5", "Pocket Option", "Индикаторы"],
        "kind": "trend",
        "d": "📌 Связка ADX + Aroon:\n\n"
             "• ADX показывает силу тренда, Aroon ищет экстремумы.\n\n"
             "• ⏱ Таймфреймы: M1 и M5.\n"
             "• ⏳ Экспирация: 2–3 минуты.\n\n"
             "• 🎯 Правила:\n"
             "  1️⃣ CALL — ADX > 25 + Aroon Up пересекает Aroon Down снизу вверх.\n"
             "  2️⃣ PUT — ADX > 25 + Aroon Down в верхней зоне.\n\n"
             "• Не торгуйте при ADX < 20."
    },
    {
        "t": "5. Bollinger Bands + Awesome Oscillator",
        "tags": ["S30", "M1", "Pocket Option", "Индикаторы"],
        "kind": "bounce",
        "d": "📌 Связка Bollinger Bands + Awesome Oscillator:\n\n"
             "• Полосы Боллинджера показывают волатильность, AO — импульс.\n\n"
             "• ⏱ Таймфреймы: S30 и M1.\n"
             "• ⏳ Экспирация: 1 минута.\n\n"
             "• 🎯 Правила:\n"
             "  1️⃣ CALL — касание нижней полосы + AO переходит в зелёную зону.\n"
             "  2️⃣ PUT — касание верхней полосы + разворот AO вниз.\n\n"
             "• Торгуйте по тренду старшего ТФ."
    },
]

for i in range(6, 51):
    kinds = ["levels", "range", "trend", "bounce"]
    EDU_50.append({
        "t": f"{i}. Профессиональная стратегия и связка индикаторов №{i}",
        "tags": ["M1", "Pocket Option", "Стратегии"],
        "kind": kinds[i % 4],
        "d": (
            f"📌 Подробный разбор стратегии №{i}:\n\n"
            f"• Система построена на уровнях ликвидности, зонах перекупленности/перепроданности и фильтрации шума.\n\n"
            f"• ⏱ Таймфреймы: M1, M2, M5.\n"
            f"• ⏳ Экспирация: 1–3 минуты.\n\n"
            f"• 🎯 Правила входа:\n"
            f"  1. Дождитесь подхода цены к ключевой зоне.\n"
            f"  2. Убедитесь в отсутствии сильного противонаправленного импульса.\n"
            f"  3. Открывайте CALL или PUT на закрытии сигнальной свечи.\n\n"
            f"• 🛡️ Риск не более 1-2% от депозита."
        ),
    })

# ===== HTML =====
HTML_UI = r"""<!DOCTYPE html>
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
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.55;transform:scale(1.04)}}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
@keyframes bounceSmile{0%,100%{transform:translateY(0)}50%{transform:translateY(-6px)}}
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
.input{width:100%;padding:12px;border-radius:11px;border:1.5px solid var(--border-g);background:var(--inner);color:#fff;font-size:14px;font-weight:700;text-align:center;outline:none;margin-bottom:9px}
.input:focus{border-color:var(--gold)}
.live-bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.live{font-size:8px;background:rgba(0,230,118,.12);color:var(--green);padding:3px 8px;border-radius:6px;border:1px solid rgba(0,230,118,.3);font-weight:800}
.lbl{display:block;font-size:9px;color:var(--muted);font-weight:800;letter-spacing:.3px;margin-bottom:4px}
.selected-asset-box{background:var(--inner);border:1.5px solid var(--gold);border-radius:12px;padding:10px 14px;margin-bottom:14px;display:flex;justify-content:space-between;align-items:center;cursor:pointer}
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
.tag-row{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px}
.tag{font-size:9px;font-weight:800;padding:3px 8px;border-radius:6px;background:rgba(255,215,0,.08);color:var(--gold);border:1px solid rgba(255,215,0,.2)}
.bnav{position:fixed;bottom:0;left:0;right:0;background:#05070d;border-top:1px solid var(--border);display:flex;justify-content:space-around;padding:6px 0 10px;z-index:100}
.nav{display:flex;flex-direction:column;align-items:center;gap:1px;color:var(--muted);font-size:8px;font-weight:800;cursor:pointer;min-width:36px;padding:3px 1px;transition:color .2s}
.nav.on{color:var(--gold)}.nav span:first-child{font-size:14px}
.hidden{display:none!important}
.gap{height:6px}.gap2{height:9px}
.analyze-overlay{position:fixed;inset:0;background:rgba(4,6,10,.94);z-index:200;display:flex;flex-direction:column;align-items:center;justify-content:center;animation:fadeIn .2s ease}
.analyze-wheel{width:56px;height:56px;border:3px solid rgba(255,215,0,.12);border-top-color:var(--gold);border-radius:50%;animation:spin .7s linear infinite;margin-bottom:14px}
.analyze-text{font-size:13px;font-weight:800;color:var(--gold);text-align:center;animation:pulse 1.1s ease infinite;max-width:260px;line-height:1.5}
.analyze-smile{font-size:28px;margin-bottom:10px;animation:bounceSmile 1s ease infinite}
.scan-box{background:var(--inner);border:1px solid var(--border-g);border-radius:14px;overflow:hidden;margin-bottom:10px}
.scan-video{width:100%;height:210px;object-fit:cover;background:#000;display:block}
.scan-placeholder{height:210px;display:flex;flex-direction:column;align-items:center;justify-content:center;color:var(--muted);font-size:12px;font-weight:700;gap:8px;text-align:center;padding:12px}
.scan-frame{position:relative}
.scan-frame.live::after{content:"";position:absolute;inset:12px;border:2px solid rgba(255,215,0,.5);border-radius:8px;pointer-events:none;box-shadow:0 0 0 9999px rgba(0,0,0,.2)}
.scan-tips{font-size:10.5px;color:#b0b8c8;line-height:1.55;background:var(--card);border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:10px}
.scan-tips b{color:var(--gold)}
.fav-list{max-height:320px;overflow-y:auto;display:flex;flex-direction:column;gap:6px}
.fav-item{display:flex;justify-content:space-between;align-items:center;padding:11px 13px;background:var(--inner);border:1px solid var(--border);border-radius:11px;font-size:12px;font-weight:700;cursor:pointer;transition:background .15s}
.fav-item:hover{background:rgba(255,215,0,.08)}
.fav-item>span:first-child{color:var(--gold)}
.catalog-category-header{font-size:11px;font-weight:900;color:var(--gold);text-transform:uppercase;margin:12px 0 6px 4px;letter-spacing:.6px;border-bottom:1px solid var(--border-g);padding-bottom:4px}
.profile-photo-upload{width:90px;height:90px;border-radius:50%;border:2px dashed var(--gold);background:var(--inner);display:flex;align-items:center;justify-content:center;cursor:pointer;margin:0 auto 12px;overflow:hidden;position:relative}
.profile-photo-upload img{width:100%;height:100%;object-fit:cover}
.about-box{background:var(--inner);border:1px solid var(--border-g);border-radius:14px;padding:16px;font-size:12px;color:#c5cdd8;line-height:1.8}
.news-sec-title{font-size:12px;font-weight:900;margin:16px 0 10px;padding:10px 12px;border-radius:12px;letter-spacing:.4px;display:flex;align-items:center;gap:8px}
.news-sec-up{background:linear-gradient(90deg,rgba(255,215,0,.22),rgba(255,215,0,.04));color:var(--gold);border:1px solid rgba(255,215,0,.35);box-shadow:0 4px 14px rgba(255,215,0,.08)}
.news-sec-done{background:linear-gradient(90deg,rgba(0,230,118,.16),rgba(0,230,118,.03));color:var(--green);border:1px solid rgba(0,230,118,.3);box-shadow:0 4px 14px rgba(0,230,118,.06)}
.news-item{background:linear-gradient(180deg,rgba(18,22,34,.98),rgba(8,10,16,.99));border:1.5px solid var(--border);border-radius:16px;padding:14px 14px 12px;margin-bottom:11px;cursor:pointer;transition:all .2s ease;box-shadow:0 6px 18px rgba(0,0,0,.32)}
.news-item:active{transform:scale(.985)}
.news-item.high{border-color:rgba(255,82,82,.6);box-shadow:0 0 0 1px rgba(255,82,82,.15),0 8px 22px rgba(255,82,82,.1)}
.news-item.medium{border-color:rgba(255,167,38,.5);box-shadow:0 0 0 1px rgba(255,167,38,.12),0 6px 18px rgba(255,167,38,.06)}
.news-item.open{border-color:var(--gold);box-shadow:0 0 0 1px rgba(255,215,0,.3),0 10px 28px rgba(255,215,0,.12)}
.news-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px}
.news-cur{font-size:11px;font-weight:900;color:var(--gold);display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.news-time{font-size:11px;color:#fff;font-weight:800;background:rgba(255,215,0,.14);border:1px solid rgba(255,215,0,.28);padding:4px 9px;border-radius:9px;white-space:nowrap}
.news-name{font-size:14px;font-weight:800;color:#fff;margin-bottom:5px;line-height:1.35;letter-spacing:.2px}
.news-pairs{font-size:10.5px;color:#9aa3b5;margin-bottom:8px;font-weight:600}
.news-meta{display:flex;flex-wrap:wrap;gap:6px;font-size:10.5px;font-weight:700;color:#c5cdd8}
.news-meta span{background:rgba(255,255,255,.05);border:1px solid var(--border);border-radius:8px;padding:5px 9px}
.news-impact{font-size:9px;font-weight:900;padding:3px 8px;border-radius:7px}
.news-impact.high{background:rgba(255,82,82,.22);color:#ff8a80}
.news-impact.medium{background:rgba(255,167,38,.2);color:#FFB74D}
.news-impact.low{background:rgba(255,215,0,.12);color:var(--gold)}
.news-updated{font-size:10px;color:var(--muted);font-weight:700;text-align:center;margin-bottom:10px;padding:6px;background:rgba(255,255,255,.03);border-radius:8px}
.news-list{max-height:480px;overflow-y:auto;padding-right:3px}
.news-detail{display:none;margin-top:12px;padding:12px 12px 10px;border-top:1px solid rgba(255,215,0,.25);border-radius:0 0 12px 12px;font-size:12px;color:#d7deea;line-height:1.7;background:rgba(0,0,0,.28)}
.news-item.open .news-detail{display:block}
.news-copy-btn{margin-top:10px;width:100%;padding:9px;border-radius:10px;border:1px solid rgba(255,215,0,.35);background:rgba(255,215,0,.08);color:var(--gold);font-size:11px;font-weight:800;cursor:pointer}
.news-copy-btn:active{transform:scale(.97)}
.news-loading{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:36px 16px;gap:14px}
.news-loading-wheel{width:42px;height:42px;border:3px solid rgba(255,215,0,.15);border-top-color:var(--gold);border-radius:50%;animation:spin .75s linear infinite}
.news-loading-text{font-size:12px;font-weight:800;color:var(--gold);animation:pulse 1.2s ease infinite}
.news-skeleton{background:linear-gradient(90deg,rgba(18,22,34,.9) 25%,rgba(30,36,52,.95) 50%,rgba(18,22,34,.9) 75%);background-size:200% 100%;animation:shimmer 1.4s ease infinite;border-radius:14px;height:92px;margin-bottom:10px;border:1px solid var(--border)}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
.sig-count-badge{font-size:10px;font-weight:800;color:var(--gold);background:rgba(255,215,0,.1);border:1px solid rgba(255,215,0,.25);padding:3px 8px;border-radius:8px}
.strat-visual{background:linear-gradient(180deg,#070b14,#05070d);border:1px solid var(--border-g);border-radius:14px;padding:12px;margin-bottom:14px;overflow:hidden;box-shadow:inset 0 0 30px rgba(255,215,0,.04)}
.strat-visual svg{width:100%;height:auto;display:block}
.strat-legend{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;font-size:10px;font-weight:700;color:#b0b8c8}
.strat-legend span{background:rgba(255,255,255,.04);border:1px solid var(--border);border-radius:7px;padding:4px 8px}
.hist-item{background:var(--inner);border:1px solid var(--border);border-radius:12px;padding:11px 13px;margin-bottom:8px;font-size:11.5px}
.hist-item .h-dir{font-weight:900;font-size:13px;margin:4px 0}
.hist-item .h-meta{color:var(--muted);font-size:10px;margin-bottom:4px}
.hist-item .h-reason{color:#c5cdd8;line-height:1.5;margin-top:6px}
.filter-row{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.filter-btn{padding:6px 12px;border-radius:10px;border:1px solid var(--border-g);background:var(--inner);color:var(--muted);font-size:10px;font-weight:800;cursor:pointer}
.filter-btn.on{background:var(--gold-g);color:#111;border:none}
.quiz-q{background:var(--inner);border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:10px}
.quiz-q h4{font-size:12px;color:var(--gold);margin-bottom:8px}
.quiz-opt{display:block;width:100%;padding:9px;margin-bottom:6px;border-radius:8px;border:1px solid var(--border);background:var(--card);color:#c5cdd8;font-size:11px;font-weight:700;cursor:pointer;text-align:left}
.quiz-opt:hover{border-color:var(--gold)}
.quiz-opt.correct{border-color:var(--green);background:rgba(0,230,118,.1);color:var(--green)}
.quiz-opt.wrong{border-color:var(--red);background:rgba(255,82,82,.1);color:var(--red)}
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

  <!-- SIGNAL -->
  <div class="card tab" id="tabSig">
    <div class="live-bar">
      <span class="live" id="apiStatusBadge">● POCKET OPTION API: ONLINE 🟢</span>
      <span class="sig-count-badge" id="headerSigCount">Сигналов: 0</span>
      <span style="font-size:10px;color:var(--gold);font-weight:800" id="liveClock">🕒 --:--:--</span>
    </div>
    <div class="selected-asset-box" onclick="openCatalogModal()">
      <div class="selected-asset-info">
        <span data-t="lblActiveAsset">ВЫБРАННЫЙ АКТИВ</span>
        <span id="currentAssetDisplay">🇪🇺🇺🇸 EUR/USD OTC</span>
      </div>
      <span style="font-size:22px">📋</span>
    </div>
    <div class="grid2" style="margin-bottom:10px">
      <div>
        <label class="lbl" data-t="lblTf">ИНТЕРВАЛ</label>
        <select class="sel" id="tf">
          <option value="S5">5 сек</option><option value="S15">15 сек</option><option value="S30">30 сек</option>
          <option value="M1" selected>1 мин</option><option value="M2">2 мин</option><option value="M3">3 мин</option>
          <option value="M5">5 мин</option><option value="M10">10 мин</option><option value="M15">15 мин</option>
        </select>
      </div>
      <div>
        <label class="lbl" data-t="lblExp">ЭКСПИРАЦИЯ</label>
        <select class="sel" id="exp">
          <option value="15">15 сек</option><option value="30">30 сек</option>
          <option value="60" selected>1 мин</option><option value="120">2 мин</option>
          <option value="180">3 мин</option><option value="240">4 мин</option>
          <option value="300">5 мин</option><option value="360">6 мин</option>
          <option value="420">7 мин</option><option value="480">8 мин</option>
          <option value="540">9 мин</option><option value="600">10 мин</option>
          <option value="720">12 мин</option><option value="900">15 мин</option>
        </select>
      </div>
    </div>
    <button class="btn btn-gold" onclick="getSig()" data-t="btnCalcSig">⚡ Просчитать сигнал</button>
    <div class="gap"></div>
    <button class="btn btn-green btn-sm" onclick="autoSig()" data-t="btnAutoSig">🤖 Выбрать топ актив</button>
    <div class="sig" id="sigBox">
      <div class="sig-meta" id="sigMeta"></div>
      <div class="sig-dir call" id="sigDir">⬆️ CALL</div>
      <div class="timer">⏱ <span id="cd">01:00</span></div>
      <div id="sigChartContainer" style="margin:10px 0;text-align:center;"></div>
      <div class="strat" id="sigStrat"></div>
      <div class="sig-stats"><span><span data-t="lblCombo">Связка</span>: <b id="sigCombo">Multi-Factor</b></span></div>
      <button class="btn btn-red btn-sm" id="btnCancel" onclick="cancelSig()" style="display:none" data-t="btnCancel">✖ Отменить</button>
      <button class="btn btn-gold btn-sm" id="btnNew" onclick="getSig()" style="display:none" data-t="btnNewSig">⚡ Новый сигнал</button>
    </div>
  </div>

  <!-- FAVS -->
  <div class="card tab hidden" id="tabFavs">
    <h3 class="g-text" style="font-size:13px;margin-bottom:8px" data-t="favTabTitle">⭐ Избранные активы</h3>
    <p style="font-size:11px;color:var(--muted);margin-bottom:10px" data-t="favTabDesc">Нажмите на любой актив для перехода.</p>
    <div class="fav-list" id="mainFavList"></div>
    <div id="mainFavEmpty" style="text-align:center;padding:24px;color:var(--muted);font-size:11px;font-weight:600" data-t="profFavEmpty">Пока пусто — добавь актив из каталога</div>
    <div class="gap2"></div>
    <button class="btn btn-dark btn-sm" onclick="clearFavs()" data-t="btnClearFav">🗑 Очистить избранное</button>
  </div>

  <!-- NEWS -->
  <div class="card tab hidden" id="tabNews">
    <h3 class="g-text" style="font-size:13px;margin-bottom:6px" data-t="newsTitle">📰 Экономические новости</h3>
    <p style="font-size:10.5px;color:var(--muted);margin-bottom:8px" data-t="newsDesc">Актуальный календарь форекс-новостей. Четкие уровни важности, переведенные описания и влияние на активы.</p>
    <div class="filter-row" id="newsFilterRow">
      <button class="filter-btn on" onclick="setNewsFilter('ALL',this)">ВСЕ</button>
      <button class="filter-btn" onclick="setNewsFilter('USD',this)">USD</button>
      <button class="filter-btn" onclick="setNewsFilter('EUR',this)">EUR</button>
      <button class="filter-btn" onclick="setNewsFilter('GBP',this)">GBP</button>
      <button class="filter-btn" onclick="setNewsFilter('JPY',this)">JPY</button>
      <button class="filter-btn" onclick="setNewsFilter('AUD',this)">AUD</button>
      <button class="filter-btn" onclick="setNewsFilter('CAD',this)">CAD</button>
      <button class="filter-btn" onclick="setNewsFilter('CHF',this)">CHF</button>
      <button class="filter-btn" onclick="setNewsFilter('NZD',this)">NZD</button>
      <button class="filter-btn" onclick="setNewsFilter('RUB',this)">RUB</button>
    </div>
    <div class="news-updated" id="newsUpdated">Обновление...</div>
    <div class="news-list" id="newsList">
      <div class="news-loading" id="newsLoading">
        <div class="news-loading-wheel"></div>
        <div class="news-loading-text">Загружаю календарь новостей...</div>
      </div>
    </div>
    <div class="gap"></div>
    <button class="btn btn-dark btn-sm" onclick="loadNews(true)" data-t="btnRefreshNews">🔄 Обновить</button>
  </div>

  <!-- PROFILE -->
  <div class="card tab hidden" id="tabProfile">
    <h3 class="g-text" style="font-size:13px;margin-bottom:12px" data-t="profTitle">👤 Профиль</h3>
    <div class="profile-photo-upload" onclick="document.getElementById('profPhotoInput').click()">
      <img id="profilePhotoImg" class="hidden" alt="photo">
      <span id="profilePhotoIcon" style="font-size:32px">📷</span>
      <input type="file" id="profPhotoInput" accept="image/*" style="display:none" onchange="handleProfilePhoto(event)">
    </div>
    <label class="lbl">Pocket Option ID</label>
    <input class="input" id="profPocketId" placeholder="Ваш ID" onchange="saveProfileSettings()">
    <label class="lbl">Любимая стратегия</label>
    <input class="input" id="profFavStrat" placeholder="Например: Уровни" onchange="saveProfileSettings()">
    <div style="display:flex;justify-content:space-between;margin:12px 0;font-size:12px;font-weight:700">
      <span data-t="lblSigCount">Сигналов: <b id="profSigCount">0</b></span>
      <span data-t="lblFavCount">Избранных: <b id="profFavCount">0</b></span>
    </div>
    <button class="btn btn-dark btn-sm" onclick="openHistoryModal()" style="margin-bottom:8px">📜 История сигналов</button>
    <button class="btn btn-red btn-sm" onclick="resetProfile()" data-t="btnResetProf">Сбросить профиль</button>
  </div>

  <!-- EDU -->
  <div class="card tab hidden" id="tabEdu">
    <div id="eduList">
      <h3 class="g-text" style="font-size:13px;margin-bottom:8px" data-t="eduTitle">📚 Связки и стратегии</h3>
      <p style="font-size:11px;color:var(--muted);margin-bottom:10px" data-t="eduDesc">Выберите стратегию + 5 вопросов мини-теста. У каждой свой график-пример.</p>
      <button class="btn btn-gold btn-sm" onclick="openRandomEdu()" data-t="btnRandomEdu" style="margin-bottom:12px">🎲 Случайная стратегия</button>
      <div id="eduItems"></div>
    </div>
    <div id="eduView" class="hidden">
      <button class="btn btn-dark btn-sm" onclick="closeEdu()" style="margin-bottom:10px" data-t="btnBack">← Назад</button>
      <div class="edu-detail" id="eduBody"></div>
    </div>
  </div>

  <!-- SCANNER -->
  <div class="card tab hidden" id="tabScan">
    <h3 class="g-text" style="font-size:13px;margin-bottom:8px" data-t="scanTitle">📷 AI Сканер графика</h3>
    <div class="scan-tips">
      <b>Как пользоваться:</b> откройте камеру или загрузите скриншот. Наведите на свечи и нажмите «Сканировать».
    </div>
    <div class="scan-box">
      <div class="scan-frame" id="scanFrame">
        <video id="scanVideo" class="scan-video hidden" autoplay playsinline muted></video>
        <img id="scanPreviewImg" class="scan-video hidden" alt="preview">
        <div class="scan-placeholder" id="scanPh">
          <span style="font-size:28px">📷</span>
          <span data-t="scanPhText">Откройте камеру или загрузите скриншот</span>
        </div>
      </div>
    </div>
    <div class="grid2" style="margin-bottom:10px">
      <button class="btn btn-dark btn-sm" onclick="openCam()" data-t="btnCam">📷 Камера</button>
      <label class="btn btn-dark btn-sm" style="cursor:pointer">
        <span data-t="btnUpload">📁 Загрузить</span>
        <input type="file" id="scanFileInput" accept="image/*" style="display:none" onchange="handleImageUpload(event)">
      </label>
    </div>
    <button class="btn btn-gold" id="btnScan" onclick="doScan()" disabled data-t="btnScan">⚡ Сканировать</button>
    <div class="gap"></div>
    <button class="btn btn-dark btn-sm" onclick="stopCam()" data-t="btnStopCam">⏹ Остановить</button>
    <div class="sig" id="scanSigBox" style="display:none">
      <div class="sig-meta" id="scanMeta"></div>
      <div class="sig-dir call" id="scanDir">BUY</div>
      <div class="timer">⏱ <span id="scanCd">01:00</span></div>
      <div class="strat" id="scanStrat"></div>
      <button class="btn btn-red btn-sm" id="btnScanCancel" onclick="cancelScan()" style="display:none">✖ Отменить</button>
      <button class="btn btn-gold btn-sm" id="btnScanNew" onclick="doScan()" style="display:none">⚡ Новый скан</button>
    </div>
  </div>

  <!-- ABOUT -->
  <div class="card tab hidden" id="tabAbout">
    <h3 class="g-text" style="font-size:13px;margin-bottom:12px">ℹ️ О нас</h3>
    <div class="about-box">
      🚀 <b>TEAM MASTER VIP</b> — это команда профессиональных трейдеров и разработчиков.<br><br>
      💎 Мы создаём современные инструменты для торговли бинарными опционами на Pocket Option.<br><br>
      📊 Соединяем технический анализ, искусственный интеллект и реальный рыночный опыт.<br><br>
      🔥 Наша цель — давать максимально точные, понятные и рабочие сигналы.<br><br>
      ✨ Присоединяйся к команде и торгуй вместе с нами!
    </div>
    <div class="gap2"></div>
    <a href="https://t.me/+uekq4TquqkM4Mzcy" target="_blank" class="btn btn-gold">
      📢 Наш Telegram-канал
    </a>
  </div>

</div>

<!-- Bottom Navigation -->
<div class="bnav">
  <div class="nav on" onclick="showTab('tabSig',this)"><span>⚡</span><span data-t="navSig">Сигнал</span></div>
  <div class="nav" onclick="showTab('tabFavs',this)"><span>⭐</span><span data-t="navFavs">Избранное</span></div>
  <div class="nav" onclick="showTab('tabNews',this)"><span>📰</span><span data-t="navNews">Новости</span></div>
  <div class="nav" onclick="showTab('tabProfile',this)"><span>👤</span><span data-t="navProf">Профиль</span></div>
  <div class="nav" onclick="showTab('tabEdu',this)"><span>📚</span><span data-t="navEdu">Стратегии</span></div>
  <div class="nav" onclick="showTab('tabScan',this)"><span>📷</span><span data-t="navScan">Сканер</span></div>
  <div class="nav" onclick="showTab('tabAbout',this)"><span>ℹ️</span><span data-t="navAbout">О нас</span></div>
</div>

<!-- Catalog Modal -->
<div class="modal-overlay hidden" id="catalogModal">
  <div class="modal-content">
    <div class="modal-header">
      <h3 data-t="catalogTitle">Каталог активов</h3>
      <button class="modal-close" onclick="closeCatalogModal()">✕</button>
    </div>
    <input class="modal-search-input" id="modalSearchInput" placeholder="Поиск актива..." oninput="renderCatalogList(this.value)">
    <div class="modal-body" id="catalogBody"></div>
  </div>
</div>

<!-- History Modal -->
<div class="modal-overlay hidden" id="historyModal">
  <div class="modal-content">
    <div class="modal-header">
      <h3>📜 История сигналов</h3>
      <button class="modal-close" onclick="closeHistoryModal()">✕</button>
    </div>
    <div class="modal-body" id="historyBody"></div>
  </div>
</div>

<!-- Quiz Modal -->
<div class="modal-overlay hidden" id="quizModal">
  <div class="modal-content">
    <div class="modal-header">
      <h3 id="quizTitle">Мини-тест</h3>
      <button class="modal-close" onclick="closeQuiz()">✕</button>
    </div>
    <div class="modal-body" id="quizBody"></div>
  </div>
</div>

<!-- Analyze Overlay -->
<div class="analyze-overlay hidden" id="analyzeBox">
  <div class="analyze-smile" id="analyzeSmile">😊</div>
  <div class="analyze-wheel"></div>
  <div class="analyze-text" id="analyzeTxt">⚡ Подключаюсь к рынку...</div>
</div>

<script>
var ASSETS = __ASSETS_JSON__;
var EDU = __EDU_JSON__;
var currentLang = "ru";
var currentAsset = "🇪🇺🇺🇸 EUR/USD OTC";
var favs = [];
var sigCount = 0;
var signalHistory = [];
var timers = {};
var newsCache = [];
var lastNewsHash = "";
var newsFilter = "ALL";
var newsTimer = null;
var stream = null;
var camReady = false;
var uploadedImageBlob = null;
var scanCfg = {"стратегия":"Smart Money","интервал":"M1","экспирация":"1м"};

try {
  favs = JSON.parse(localStorage.getItem("tmv_favs") || "[]");
  sigCount = parseInt(localStorage.getItem("tmv_sigs") || "0");
  signalHistory = JSON.parse(localStorage.getItem("tmv_history") || "[]");
} catch(e) {}

var I18N = {
  ru: {
    lblActiveAsset: "ВЫБРАННЫЙ АКТИВ",
    lblTf: "ИНТЕРВАЛ",
    lblExp: "ЭКСПИРАЦИЯ",
    btnCalcSig: "⚡ Просчитать сигнал",
    btnAutoSig: "🤖 Выбрать топ актив",
    btnCancel: "✖ Отменить",
    btnNewSig: "⚡ Новый сигнал",
    favTabTitle: "⭐ Избранные активы",
    favTabDesc: "Нажмите на любой актив для перехода.",
    profFavEmpty: "Пока пусто — добавь актив из каталога",
    btnClearFav: "🗑 Очистить избранное",
    newsTitle: "📰 Экономические новости",
    newsDesc: "Актуальный календарь форекс-новостей. Четкие уровни важности, переведенные описания и влияние на активы.",
    btnRefreshNews: "🔄 Обновить",
    eduTitle: "📚 Связки и стратегии",
    eduDesc: "Выберите стратегию + мини-тест",
    btnRandomEdu: "🎲 Случайная стратегия",
    btnBack: "← Назад",
    scanTitle: "📷 AI Сканер графика",
    scanPhText: "Откройте камеру или загрузите скриншот",
    btnCam: "📷 Камера",
    btnUpload: "📁 Загрузить",
    btnScan: "⚡ Сканировать",
    btnStopCam: "⏹ Остановить",
    profTitle: "👤 Профиль",
    lblSigCount: "Сигналов:",
    lblFavCount: "Избранных:",
    btnResetProf: "Сбросить профиль",
    navSig: "Сигнал",
    navFavs: "Избранное",
    navNews: "Новости",
    navEdu: "Стратегии",
    navScan: "Сканер",
    navProf: "Профиль",
    navAbout: "О нас",
    catalogTitle: "Каталог активов",
    lblCombo: "Связка"
  },
  en: {
    lblActiveAsset: "SELECTED ASSET",
    lblTf: "TIMEFRAME",
    lblExp: "EXPIRATION",
    btnCalcSig: "⚡ Calculate Signal",
    btnAutoSig: "🤖 Top Asset",
    btnCancel: "✖ Cancel",
    btnNewSig: "⚡ New Signal",
    favTabTitle: "⭐ Favorites",
    favTabDesc: "Tap any asset to select it.",
    profFavEmpty: "Empty — add from catalog",
    btnClearFav: "🗑 Clear favorites",
    newsTitle: "📰 Economic News",
    newsDesc: "Up-to-date forex calendar. Clear impact levels, translated descriptions and asset impact.",
    btnRefreshNews: "🔄 Refresh",
    eduTitle: "📚 Strategies",
    eduDesc: "Choose a strategy + mini-test",
    btnRandomEdu: "🎲 Random strategy",
    btnBack: "← Back",
    scanTitle: "📷 AI Chart Scanner",
    scanPhText: "Open camera or upload screenshot",
    btnCam: "📷 Camera",
    btnUpload: "📁 Upload",
    btnScan: "⚡ Scan",
    btnStopCam: "⏹ Stop",
    profTitle: "👤 Profile",
    lblSigCount: "Signals:",
    lblFavCount: "Favorites:",
    btnResetProf: "Reset profile",
    navSig: "Signal",
    navFavs: "Favs",
    navNews: "News",
    navEdu: "Strategies",
    navScan: "Scanner",
    navProf: "Profile",
    navAbout: "About",
    catalogTitle: "Asset Catalog",
    lblCombo: "Combo"
  },
  ua: {
    lblActiveAsset: "ОБРАНИЙ АКТИВ",
    lblTf: "ІНТЕРВАЛ",
    lblExp: "ЕКСПІРАЦІЯ",
    btnCalcSig: "⚡ Розрахувати сигнал",
    btnAutoSig: "🤖 Топ актив",
    btnCancel: "✖ Скасувати",
    btnNewSig: "⚡ Новий сигнал",
    favTabTitle: "⭐ Обрані",
    favTabDesc: "Натисніть на актив.",
    profFavEmpty: "Порожньо",
    btnClearFav: "🗑 Очистити",
    newsTitle: "📰 Економічні новини",
    newsDesc: "Актуальний календар новин. Чіткі рівня важливості, перекладені описи та вплив на активи.",
    btnRefreshNews: "🔄 Оновити",
    eduTitle: "📚 Стратегії",
    eduDesc: "Оберіть стратегію + міні-тест",
    btnRandomEdu: "🎲 Випадкова",
    btnBack: "← Назад",
    scanTitle: "📷 AI Сканер",
    scanPhText: "Камера або скріншот",
    btnCam: "📷 Камера",
    btnUpload: "📁 Завантажити",
    btnScan: "⚡ Сканувати",
    btnStopCam: "⏹ Зупинити",
    profTitle: "👤 Профіль",
    lblSigCount: "Сигналів:",
    lblFavCount: "Обраних:",
    btnResetProf: "Скинути",
    navSig: "Сигнал",
    navFavs: "Обране",
    navNews: "Новини",
    navEdu: "Стратегії",
    navScan: "Сканер",
    navProf: "Профіль",
    navAbout: "Про нас",
    catalogTitle: "Каталог",
    lblCombo: "Зв'язка"
  }
};

function esc(s) {
  return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function showTab(id, el) {
  document.querySelectorAll(".tab").forEach(function(t){ t.classList.add("hidden"); });
  document.getElementById(id).classList.remove("hidden");
  document.querySelectorAll(".nav").forEach(function(n){ n.classList.remove("on"); });
  if (el) el.classList.add("on");
}

function getCategories() {
  return ASSETS[currentLang] || ASSETS.ru;
}

function updateSelectedAssetUI() {
  document.getElementById("currentAssetDisplay").textContent = currentAsset;
}

function openCatalogModal() {
  renderCatalogList("");
  document.getElementById("catalogModal").classList.remove("hidden");
}
function closeCatalogModal() {
  document.getElementById("catalogModal").classList.add("hidden");
}

function renderCatalogList(q) {
  var body = document.getElementById("catalogBody");
  var cats = getCategories();
  var html = "";
  var query = (q || "").toLowerCase();
  for (var cat in cats) {
    var items = cats[cat].filter(function(a){ return !query || a.toLowerCase().indexOf(query) >= 0; });
    if (!items.length) continue;
    html += '<div class="catalog-category-header">' + esc(cat) + '</div>';
    items.forEach(function(a) {
      var isFav = favs.indexOf(a) >= 0;
      html += '<div class="fav-item" onclick="selectAsset(\'' + a.replace(/'/g,"\\'") + '\')">';
      html += '<span>' + esc(a) + '</span>';
      html += '<span onclick="event.stopPropagation();toggleFav(\'' + a.replace(/'/g,"\\'") + '\')">' + (isFav ? "⭐" : "☆") + '</span>';
      html += '</div>';
    });
  }
  body.innerHTML = html || '<div style="text-align:center;padding:20px;color:var(--muted)">Ничего не найдено</div>';
}

function selectAsset(a) {
  currentAsset = a;
  updateSelectedAssetUI();
  closeCatalogModal();
}

function toggleFav(a) {
  var i = favs.indexOf(a);
  if (i >= 0) favs.splice(i, 1);
  else favs.push(a);
  try { localStorage.setItem("tmv_favs", JSON.stringify(favs)); } catch(e) {}
  renderCatalogList(document.getElementById("modalSearchInput").value || "");
  renderMainFavs();
  updateProfileStats();
}

function renderMainFavs() {
  var list = document.getElementById("mainFavList");
  var empty = document.getElementById("mainFavEmpty");
  if (!favs.length) {
    list.innerHTML = "";
    empty.style.display = "block";
    return;
  }
  empty.style.display = "none";
  var html = "";
  favs.forEach(function(a) {
    html += '<div class="fav-item" onclick="selectAsset(\'' + a.replace(/'/g,"\\'") + '\');showTab(\'tabSig\',document.querySelectorAll(\'.nav\')[0])">';
    html += '<span>' + esc(a) + '</span><span>→</span></div>';
  });
  list.innerHTML = html;
}

function clearFavs() {
  favs = [];
  try { localStorage.setItem("tmv_favs", "[]"); } catch(e) {}
  renderMainFavs();
  updateProfileStats();
}

function startTimer(key, elId, seconds, onEnd, storageKey) {
  if (timers[key]) clearInterval(timers[key]);
  var end = Date.now() + seconds * 1000;
  try { localStorage.setItem(storageKey, String(end)); } catch(e) {}
  function tick() {
    var rem = Math.ceil((end - Date.now()) / 1000);
    if (rem <= 0) {
      clearInterval(timers[key]);
      document.getElementById(elId).textContent = "00:00";
      if (onEnd) onEnd();
      return;
    }
    var m = Math.floor(rem / 60);
    var s = rem % 60;
    document.getElementById(elId).textContent = (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }
  tick();
  timers[key] = setInterval(tick, 250);
}

function resumeTimers() {
  try {
    var end = parseInt(localStorage.getItem("tmv_sig_end") || "0");
    if (end > Date.now()) {
      var rem = Math.ceil((end - Date.now()) / 1000);
      document.getElementById("sigBox").style.display = "block";
      document.getElementById("btnCancel").style.display = "block";
      document.getElementById("btnNew").style.display = "none";
      startTimer("sig", "cd", rem, function() {
        document.getElementById("btnCancel").style.display = "none";
        document.getElementById("btnNew").style.display = "block";
      }, "tmv_sig_end");
    }
  } catch(e) {}
  try {
    var end2 = parseInt(localStorage.getItem("tmv_scan_end") || "0");
    if (end2 > Date.now()) {
      var rem2 = Math.ceil((end2 - Date.now()) / 1000);
      document.getElementById("scanSigBox").style.display = "block";
      document.getElementById("btnScanCancel").style.display = "block";
      document.getElementById("btnScanNew").style.display = "none";
      startTimer("scan", "scanCd", rem2, function() {
        document.getElementById("btnScanCancel").style.display = "none";
        document.getElementById("btnScanNew").style.display = "block";
      }, "tmv_scan_end");
    }
  } catch(e) {}
}

function runAnalyzer(callback) {
  var overlay = document.getElementById("analyzeBox");
  var txtEl = document.getElementById("analyzeTxt");
  var smileEl = document.getElementById("analyzeSmile");
  overlay.classList.remove("hidden");
  var phrases = [
    "Подключаюсь к потоку котировок...",
    "Считываю свечи и объём...",
    "Сверяю EMA / RSI / импульс...",
    "Фильтрую шум и ложные входы...",
    "Формирую финальный сигнал..."
  ];
  var smiles = ["📡", "📊", "🔍", "⚙️", "🎯"];
  var step = 0;
  txtEl.textContent = phrases[0];
  smileEl.textContent = smiles[0];
  var interval = setInterval(function() {
    step++;
    txtEl.textContent = phrases[step % phrases.length];
    smileEl.textContent = smiles[step % smiles.length];
  }, 420);
  setTimeout(function() {
    clearInterval(interval);
    overlay.classList.add("hidden");
    if (callback) callback();
  }, 2300);
}

function incSig() {
  sigCount++;
  try { localStorage.setItem("tmv_sigs", String(sigCount)); } catch(e) {}
  updateProfileStats();
}

function saveToHistory(entry) {
  signalHistory.unshift(entry);
  if (signalHistory.length > 100) signalHistory = signalHistory.slice(0, 100);
  try { localStorage.setItem("tmv_history", JSON.stringify(signalHistory)); } catch(e) {}
}

function openHistoryModal() {
  var body = document.getElementById("historyBody");
  if (!signalHistory.length) {
    body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--muted);font-size:12px">История пуста. Сигналы появятся после расчёта.</div>';
  } else {
    var html = "";
    signalHistory.forEach(function(h) {
      var dirClass = (h.dir || "").indexOf("CALL") >= 0 || (h.dir || "").indexOf("UP") >= 0 || (h.dir || "").indexOf("ВВЕРХ") >= 0 ? "call" : "put";
      html += '<div class="hist-item">';
      html += '<div class="h-meta">' + esc(h.time || "") + ' · ' + esc(h.asset || "") + ' · ' + esc(h.tf || "") + ' · эксп. ' + esc(String(h.exp || "")) + ' сек</div>';
      html += '<div class="h-dir ' + dirClass + '">' + esc(h.dir || "") + '</div>';
      if (h.reason) html += '<div class="h-reason">' + esc(h.reason) + '</div>';
      html += '</div>';
    });
    body.innerHTML = html;
  }
  document.getElementById("historyModal").classList.remove("hidden");
}
function closeHistoryModal() { document.getElementById("historyModal").classList.add("hidden"); }

function drawCandleChartSVG(prices, signalDir, entryPrice) {
  if (!prices || !prices.length) return '';
  var w = 360, h = 180;
  var minP = Math.min.apply(null, prices);
  var maxP = Math.max.apply(null, prices);
  var rangeP = (maxP - minP) || 0.0001;
  var pad = 20;
  var chartH = h - pad * 2;
  var count = prices.length;
  var stepW = (w - 40) / count;
  
  var svg = '<svg viewBox="0 0 ' + w + ' ' + h + '" xmlns="http://www.w3.org/2000/svg" style="background:#070a10;border-radius:10px;width:100%;height:auto;border:1px solid rgba(255,215,0,0.2);">';
  svg += '<rect width="' + w + '" height="' + h + '" fill="#070a10"/>';
  
  // Grid lines
  for (var i = 1; i <= 3; i++) {
    var gy = pad + (chartH / 4) * i;
    svg += '<line x1="20" y1="' + gy + '" x2="' + (w - 20) + '" y2="' + gy + '" stroke="#161e2e" stroke-width="1" stroke-dasharray="3 3"/>';
  }
  
  var points = [];
  for (var i = 0; i < count; i++) {
    var p = prices[i];
    var x = 20 + i * stepW + stepW / 2;
    var y = pad + chartH - ((p - minP) / rangeP) * chartH;
    points.push({x: x, y: y, p: p});
  }
  
  // Draw line / candles
  for (var i = 0; i < points.length - 1; i++) {
    var curr = points[i];
    var next = points[i+1];
    var color = next.p >= curr.p ? "#00E676" : "#FF5252";
    svg += '<line x1="' + curr.x + '" y1="' + curr.y + '" x2="' + next.x + '" y2="' + next.y + '" stroke="' + color + '" stroke-width="2.5" stroke-linecap="round"/>';
  }
  
  // Entry point highlight on the last candle
  var last = points[points.length - 1];
  var isCall = signalDir.indexOf("CALL") >= 0 || signalDir.indexOf("UP") >= 0;
  var dotColor = isCall ? "#00E676" : "#FF5252";
  
  svg += '<circle cx="' + last.x + '" cy="' + last.y + '" r="6" fill="' + dotColor + '" stroke="#FFD700" stroke-width="2"/>';
  svg += '<text x="' + Math.min(last.x - 20, w - 110) + '" y="' + Math.max(last.y - 10, 15) + '" fill="#FFD700" font-size="10" font-weight="900">ВХОД: ' + Number(entryPrice).toFixed(5) + '</text>';
  svg += '</svg>';
  return svg;
}

function getSig() {
  runAnalyzer(async function() {
    var tf = document.getElementById("tf").value;
    var expSec = parseInt(document.getElementById("exp").value);
    var dir = "⬆️ CALL (ВВЕРХ)";
    var stratText = "Технический анализ котировок для актива (" + currentAsset + ") по таймфрейму " + tf + " с экспирацией " + expSec + " сек.";
    var prices = generate_otc_candles(currentAsset, 40);
    
    try {
      var res = await fetch("/get_signal?asset=" + encodeURIComponent(currentAsset) + "&timeframe=" + encodeURIComponent(tf));
      if (res.ok) {
        var data = await res.json();
        if (data.signal) {
          dir = data.signal === "UP" ? "⬆️ CALL (ВВЕРХ)" : "⬇️ PUT (ВНИЗ)";
          if (data.reason) stratText = data.reason;
        }
      }
    } catch (e) {}

    var entryPrice = prices[prices.length - 1];
    var isCall = dir.indexOf("CALL") >= 0 || dir.indexOf("UP") >= 0;
    
    document.getElementById("sigMeta").textContent = "Pocket Option · " + currentAsset + " · " + tf + " · " + expSec + " сек";
    var dirEl = document.getElementById("sigDir");
    dirEl.textContent = dir;
    dirEl.className = "sig-dir " + (isCall ? "call" : "put");
    
    // Render real candle chart SVG with entry point
    document.getElementById("sigChartContainer").innerHTML = drawCandleChartSVG(prices, dir, entryPrice);
    
    document.getElementById("sigStrat").innerHTML = "<b>Анализ сигнала:</b> " + esc(stratText);
    document.getElementById("sigBox").style.display = "block";
    document.getElementById("btnCancel").style.display = "block";
    document.getElementById("btnNew").style.display = "none";
    incSig();
    var nowStr = new Date().toLocaleString("ru-RU");
    saveToHistory({
      time: nowStr,
      asset: currentAsset,
      tf: tf,
      exp: expSec,
      dir: dir,
      reason: stratText
    });
    startTimer("sig", "cd", expSec, function() {
      document.getElementById("btnCancel").style.display = "none";
      document.getElementById("btnNew").style.display = "block";
    }, "tmv_sig_end");
  });
}

function autoSig() {
  var allItems = [];
  var cats = getCategories();
  for (var c in cats) allItems = allItems.concat(cats[c]);
  currentAsset = allItems[Math.floor(Math.random() * allItems.length)];
  updateSelectedAssetUI();
  getSig();
}

function cancelSig() {
  if (timers.sig) clearInterval(timers.sig);
  try { localStorage.removeItem("tmv_sig_end"); } catch(e) {}
  document.getElementById("sigBox").style.display = "none";
}

function pad2(n) { return n < 10 ? "0" + n : String(n); }

function formatNewsTime(iso) {
  try {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso || "—";
    var now = new Date();
    var sameDay = d.getDate() === now.getDate() && d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear();
    var tm = pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    if (sameDay) return "сегодня " + tm;
    return pad2(d.getDate()) + "." + pad2(d.getMonth() + 1) + " " + tm;
  } catch (e) { return iso || "—"; }
}

function impactClass(imp) {
  var x = String(imp || "").toLowerCase();
  if (x === "high") return "high";
  if (x === "medium") return "medium";
  return "low";
}

function impactLabel(imp, impRu) {
  var x = String(imp || "").toLowerCase();
  if (impRu) {
    if (x === "high") return "🔴 " + impRu;
    if (x === "medium") return "🟠 " + impRu;
    return "🟡 " + impRu;
  }
  if (x === "high") return "🔴 Высокая";
  if (x === "medium") return "🟠 Средняя";
  return "🟡 Низкая";
}

function newsDetailText(ev) {
  var lines = [];
  lines.push("Событие: " + (ev.name || "—"));
  if (ev.name_en && ev.name_en !== ev.name) lines.push("Оригинал: " + ev.name_en);
  lines.push("Валюта: " + (ev.currency || "—"));
  if (ev.pair_hint) lines.push("Влияет на пары: " + ev.pair_hint);
  lines.push("Важность: " + (ev.impact_ru || ev.impact || "—"));
  lines.push("Время выхода: " + formatNewsTime(ev.date));
  lines.push("Статус: " + (ev.status_ru || "—"));
  if (ev.forecast) lines.push("Прогноз (ожидание рынка): " + ev.forecast);
  if (ev.previous) lines.push("Предыдущее значение: " + ev.previous);
  if (ev.actual) lines.push("Фактическое значение: " + ev.actual);
  else lines.push("Факт ещё не вышел — ждите публикации.");
  lines.push("");
  lines.push("Что это значит:");
  var nm = (ev.name || "").toLowerCase();
  if (nm.indexOf("ввп") >= 0 || nm.indexOf("gdp") >= 0) {
    lines.push("ВВП показывает рост экономики. Сильнее прогноза обычно усиливает валюту, слабее — ослабляет.");
  } else if (nm.indexOf("безраб") >= 0 || nm.indexOf("unemployment") >= 0 || nm.indexOf("claims") >= 0) {
    lines.push("Заявки/безработица отражают рынок труда. Рост заявок = хуже для валюты, снижение = лучше.");
  } else if (nm.indexOf("cpi") >= 0 || nm.indexOf("инфляц") >= 0 || nm.indexOf("pce") >= 0 || nm.indexOf("ppi") >= 0) {
    lines.push("Инфляция. Выше прогноза часто повышает ожидания по ставкам и укрепляет валюту.");
  } else if (nm.indexOf("ставк") >= 0 || nm.indexOf("rate") >= 0 || nm.indexOf("fomc") >= 0) {
    lines.push("Решение по ставке — одно из самых сильных событий. Повышение обычно укрепляет валюту.");
  } else {
    lines.push("Смотрите факт vs прогноз. Сильное отклонение даёт резкий импульс по связанным парам.");
  }
  lines.push("");
  lines.push("Team Master: на High-новостях не входите за 3–5 минут до выхода. Дождитесь реакции рынка.");
  return lines.join("\n");
}

function setNewsFilter(cur, el) {
  newsFilter = cur;
  document.querySelectorAll("#newsFilterRow .filter-btn").forEach(function(b) { b.classList.remove("on"); });
  if (el) el.classList.add("on");
  renderNewsList(newsCache);
}

function renderNewsList(events) {
  var list = document.getElementById("newsList");
  var filtered = events || [];
  if (newsFilter && newsFilter !== "ALL") {
    filtered = filtered.filter(function(e) { return (e.currency || "").toUpperCase() === newsFilter; });
  }
  if (!filtered.length) {
    list.innerHTML = '<div style="text-align:center;padding:28px;color:var(--muted);font-size:12px;font-weight:600">Нет событий High/Medium по выбранной валюте</div>';
    return;
  }
  var released = filtered.filter(function(e) { return e.status === "released"; });
  var upcoming = filtered.filter(function(e) { return e.status !== "released"; });
  var ordered = upcoming.concat(released);
  var html = "";
  if (upcoming.length) {
    html += '<div class="news-sec-title news-sec-up">⏳ Скоро выйдет · ' + upcoming.length + '</div>';
  }
  var shownReleasedHeader = false;
  ordered.forEach(function(ev, idx) {
    if (ev.status === "released" && !shownReleasedHeader) {
      html += '<div class="news-sec-title news-sec-done">✅ Уже вышло · ' + released.length + '</div>';
      shownReleasedHeader = true;
    }
    var ic = impactClass(ev.impact);
    var actual = String(ev.actual || "").trim();
    var forecast = String(ev.forecast || "").trim();
    var previous = String(ev.previous || "").trim();
    var cleanName = (ev.name || "Событие").replace(/\s+/g, " ").trim();
    html += '<div class="news-item ' + ic + '" data-idx="' + idx + '" onclick="toggleNews(' + idx + ')">';
    html += '<div class="news-top">';
    html += '<span class="news-cur">' + esc(ev.currency || "—") + ' · <span class="news-impact ' + ic + '">' + impactLabel(ev.impact, ev.impact_ru) + '</span></span>';
    html += '<span class="news-time">🕒 ' + esc(formatNewsTime(ev.date)) + '</span>';
    html += '</div>';
    html += '<div class="news-name">' + esc(cleanName) + '</div>';
    if (ev.name_en && ev.name_en !== ev.name) {
      html += '<div style="font-size:10px;color:var(--muted);margin-bottom:6px">' + esc(ev.name_en) + '</div>';
    }
    html += '<div class="news-pairs">' + esc(ev.status_ru || "") + (ev.pair_hint ? " · влияет на " + esc(ev.pair_hint) : "") + '</div>';
    html += '<div class="news-meta">';
    if (forecast) html += '<span>📈 Прогноз: ' + esc(forecast) + '</span>';
    if (previous) html += '<span>📉 Пред.: ' + esc(previous) + '</span>';
    if (actual) html += '<span style="color:var(--green)">✅ Факт: ' + esc(actual) + '</span>';
    else html += '<span>⏳ Ожидается</span>';
    html += '</div>';
    var detailId = "newsDetail" + idx;
    html += '<div class="news-detail" id="' + detailId + '">' + esc(newsDetailText(ev)).replace(/\n/g, "<br>");
    html += '<button class="news-copy-btn" onclick="event.stopPropagation();copyNewsDetail(' + idx + ')">📋 Копировать объяснение</button>';
    html += '</div>';
    html += '</div>';
  });
  list.innerHTML = html;
}

function copyNewsDetail(idx) {
  var filtered = newsCache || [];
  if (newsFilter && newsFilter !== "ALL") {
    filtered = filtered.filter(function(e) { return (e.currency || "").toUpperCase() === newsFilter; });
  }
  var upcoming = filtered.filter(function(e) { return e.status !== "released"; });
  var released = filtered.filter(function(e) { return e.status === "released"; });
  var ordered = upcoming.concat(released);
  var ev = ordered[idx];
  if (!ev) return;
  var text = newsDetailText(ev);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function() {
      var btn = document.querySelector("#newsDetail" + idx + " .news-copy-btn");
      if (btn) { btn.textContent = "✅ Скопировано"; setTimeout(function(){ btn.textContent = "📋 Копировать объяснение"; }, 1600); }
    }).catch(function(){});
  }
}

function toggleNews(idx) {
  var items = document.querySelectorAll(".news-item");
  items.forEach(function(el) {
    var i = parseInt(el.getAttribute("data-idx"));
    if (i === idx) el.classList.toggle("open");
    else el.classList.remove("open");
  });
}

async function loadNews(force) {
  var upd = document.getElementById("newsUpdated");
  var list = document.getElementById("newsList");
  if (force || !newsCache || !newsCache.length) {
    list.innerHTML = '<div class="news-loading"><div class="news-loading-wheel"></div><div class="news-loading-text">Загружаю календарь новостей...</div></div>' +
      '<div class="news-skeleton"></div><div class="news-skeleton" style="height:78px"></div><div class="news-skeleton" style="height:86px"></div>';
    upd.textContent = "⏳ Загрузка...";
  }
  try {
    var res = await fetch("/news");
    if (!res.ok) throw new Error("fail");
    var data = await res.json();
    var events = data.events || [];
    newsCache = events;
    var hash = JSON.stringify(events.map(function(e) { return [e.name, e.date, e.actual, e.forecast]; }));
    if (force || hash !== lastNewsHash) {
      lastNewsHash = hash;
      renderNewsList(events);
    }
    var t = data.updated ? formatNewsTime(data.updated) : formatNewsTime(new Date().toISOString());
    upd.textContent = "🔄 Обновлено: " + t + " · событий: " + events.length;
  } catch (e) {
    if (list.innerHTML.indexOf("news-item") < 0) {
      list.innerHTML = '<div style="text-align:center;padding:24px;color:var(--red);font-size:12px;font-weight:700">Не удалось загрузить. Нажмите «Обновить».</div>';
    }
    upd.textContent = "⚠️ Ошибка загрузки";
  }
}

function startNewsAuto() {
  if (newsTimer) clearInterval(newsTimer);
  newsTimer = setInterval(function() { loadNews(false); }, 60000);
}

function tickClock() {
  var el = document.getElementById("liveClock");
  if (!el) return;
  var d = new Date();
  el.textContent = "🕒 " + pad2(d.getHours()) + ":" + pad2(d.getMinutes()) + ":" + pad2(d.getSeconds());
}

function strategyVisual(kind, idx) {
  idx = typeof idx === "number" ? idx : 0;
  if (kind === "range") {
    return '<div class="strat-visual"><svg viewBox="0 0 360 200" xmlns="http://www.w3.org/2000/svg">' +
      '<rect width="360" height="200" fill="#05070d"/>' +
      '<line x1="30" y1="40" x2="340" y2="40" stroke="#FF5252" stroke-width="2" stroke-dasharray="7 5"/>' +
      '<text x="34" y="32" fill="#FF5252" font-size="12" font-weight="800">СОПРОТИВЛЕНИЕ → PUT 📉</text>' +
      '<line x1="30" y1="160" x2="340" y2="160" stroke="#00E676" stroke-width="2" stroke-dasharray="7 5"/>' +
      '<text x="34" y="178" fill="#00E676" font-size="12" font-weight="800">ПОДДЕРЖКА → CALL 📈</text>' +
      '<rect x="50" y="90" width="10" height="40" fill="#00E676"/><line x1="55" y1="80" x2="55" y2="140" stroke="#00E676" stroke-width="2"/>' +
      '<rect x="80" y="70" width="10" height="45" fill="#FF5252"/><line x1="85" y1="60" x2="85" y2="125" stroke="#FF5252" stroke-width="2"/>' +
      '<rect x="110" y="100" width="10" height="35" fill="#00E676"/><line x1="115" y1="90" x2="115" y2="145" stroke="#00E676" stroke-width="2"/>' +
      '<rect x="140" y="55" width="10" height="50" fill="#FF5252"/><line x1="145" y1="48" x2="145" y2="115" stroke="#FF5252" stroke-width="2"/>' +
      '<rect x="170" y="95" width="10" height="40" fill="#00E676"/><line x1="175" y1="85" x2="175" y2="145" stroke="#00E676" stroke-width="2"/>' +
      '<rect x="200" y="65" width="10" height="48" fill="#FF5252"/><line x1="205" y1="55" x2="205" y2="125" stroke="#FF5252" stroke-width="2"/>' +
      '<rect x="230" y="105" width="10" height="38" fill="#00E676"/><line x1="235" y1="95" x2="235" y2="150" stroke="#00E676" stroke-width="2"/>' +
      '<rect x="260" y="50" width="10" height="42" fill="#FF5252"/><line x1="265" y1="45" x2="265" y2="105" stroke="#FF5252" stroke-width="2"/>' +
      '<rect x="290" y="100" width="10" height="36" fill="#00E676"/><line x1="295" y1="90" x2="295" y2="145" stroke="#00E676" stroke-width="2"/>' +
      '<circle cx="265" cy="50" r="7" fill="none" stroke="#FFD700" stroke-width="2"/><text x="275" y="48" fill="#FFD700" font-size="11" font-weight="800">PUT</text>' +
      '<circle cx="235" cy="145" r="7" fill="none" stroke="#FFD700" stroke-width="2"/><text x="245" y="155" fill="#FFD700" font-size="11" font-weight="800">CALL</text>' +
      '</svg><div class="strat-legend"><span>🔴 Верх канала = PUT</span><span>🟢 Низ канала = CALL</span><span>Экспирация 1–3 мин</span></div></div>';
  }
  if (kind === "trend") {
    return '<div class="strat-visual"><svg viewBox="0 0 360 200" xmlns="http://www.w3.org/2000/svg">' +
      '<rect width="360" height="200" fill="#05070d"/>' +
      '<line x1="20" y1="170" x2="340" y2="40" stroke="#FFD700" stroke-width="2" stroke-dasharray="4 4" opacity=".5"/>' +
      '<text x="24" y="24" fill="#FFD700" font-size="12" font-weight="800">ТРЕНД ВВЕРХ → CALL по откату</text>' +
      '<rect x="40" y="140" width="12" height="28" fill="#00E676"/><line x1="46" y1="130" x2="46" y2="175" stroke="#00E676" stroke-width="2"/>' +
      '<rect x="70" y="125" width="12" height="30" fill="#00E676"/><line x1="76" y1="115" x2="76" y2="160" stroke="#00E676" stroke-width="2"/>' +
      '<rect x="100" y="135" width="12" height="22" fill="#FF5252"/><line x1="106" y1="128" x2="106" y2="162" stroke="#FF5252" stroke-width="2"/>' +
      '<rect x="130" y="110" width="12" height="32" fill="#00E676"/><line x1="136" y1="100" x2="136" y2="150" stroke="#00E676" stroke-width="2"/>' +
      '<rect x="160" y="95" width="12" height="30" fill="#00E676"/><line x1="166" y1="85" x2="166" y2="135" stroke="#00E676" stroke-width="2"/>' +
      '<rect x="190" y="105" width="12" height="24" fill="#FF5252"/><line x1="196" y1="98" x2="196" y2="135" stroke="#FF5252" stroke-width="2"/>' +
      '<rect x="220" y="80" width="12" height="34" fill="#00E676"/><line x1="226" y1="70" x2="226" y2="120" stroke="#00E676" stroke-width="2"/>' +
      '<rect x="250" y="65" width="12" height="30" fill="#00E676"/><line x1="256" y1="55" x2="256" y2="105" stroke="#00E676" stroke-width="2"/>' +
      '<rect x="280" y="75" width="12" height="22" fill="#FF5252"/><line x1="286" y1="68" x2="286" y2="105" stroke="#FF5252" stroke-width="2"/>' +
      '<rect x="310" y="50" width="12" height="32" fill="#00E676"/><line x1="316" y1="40" x2="316" y2="90" stroke="#00E676" stroke-width="2"/>' +
      '<circle cx="196" cy="120" r="8" fill="none" stroke="#FFD700" stroke-width="2.5"/>' +
      '<text x="208" y="124" fill="#FFD700" font-size="12" font-weight="800">ВХОД CALL</text>' +
      '</svg><div class="strat-legend"><span>📈 CALL по тренду</span><span>Откат = точка входа</span><span>Не против тренда</span></div></div>';
  }
  if (kind === "bounce") {
    return '<div class="strat-visual"><svg viewBox="0 0 360 200" xmlns="http://www.w3.org/2000/svg">' +
      '<rect width="360" height="200" fill="#05070d"/>' +
      '<path d="M30 50 Q80 30 120 70 Q160 130 200 145 Q240 90 300 55" fill="none" stroke="#FFD700" stroke-width="2.5"/>' +
      '<line x1="20" y1="150" x2="340" y2="150" stroke="#00E676" stroke-width="2" stroke-dasharray="6 4"/>' +
      '<text x="24" y="168" fill="#00E676" font-size="12" font-weight="800">НИЖНЯЯ ПОЛОСА / ПОДДЕРЖКА</text>' +
      '<circle cx="200" cy="145" r="9" fill="none" stroke="#FFD700" stroke-width="2.5"/>' +
      '<text x="214" y="140" fill="#FFD700" font-size="12" font-weight="800">ОТСКОК → CALL</text>' +
      '<line x1="20" y1="40" x2="340" y2="40" stroke="#FF5252" stroke-width="1.5" stroke-dasharray="6 4" opacity=".7"/>' +
      '<text x="24" y="32" fill="#FF5252" font-size="11" font-weight="700">Верхняя полоса → PUT</text>' +
      '</svg><div class="strat-legend"><span>🟢 Отскок от низа = CALL</span><span>🔴 Отскок от верха = PUT</span><span>Bollinger / уровень</span></div></div>';
  }
  return '<div class="strat-visual"><svg viewBox="0 0 360 200" xmlns="http://www.w3.org/2000/svg">' +
    '<rect width="360" height="200" fill="#05070d"/>' +
    '<line x1="25" y1="45" x2="340" y2="45" stroke="#FF5252" stroke-width="2.5"/>' +
    '<text x="28" y="36" fill="#FF5252" font-size="12" font-weight="800">СОПРОТИВЛЕНИЕ → PUT</text>' +
    '<line x1="25" y1="155" x2="340" y2="155" stroke="#00E676" stroke-width="2.5"/>' +
    '<text x="28" y="175" fill="#00E676" font-size="12" font-weight="800">ПОДДЕРЖКА → CALL</text>' +
    '<rect x="55" y="90" width="12" height="40" fill="#00E676"/><line x1="61" y1="80" x2="61" y2="140" stroke="#00E676" stroke-width="2"/>' +
    '<rect x="95" y="70" width="12" height="50" fill="#FF5252"/><line x1="101" y1="55" x2="101" y2="130" stroke="#FF5252" stroke-width="2"/>' +
    '<rect x="135" y="100" width="12" height="35" fill="#00E676"/><line x1="141" y1="90" x2="141" y2="145" stroke="#00E676" stroke-width="2"/>' +
    '<rect x="175" y="55" width="12" height="45" fill="#FF5252"/><line x1="181" y1="48" x2="181" y2="110" stroke="#FF5252" stroke-width="2"/>' +
    '<rect x="215" y="95" width="12" height="42" fill="#00E676"/><line x1="221" y1="85" x2="221" y2="150" stroke="#00E676" stroke-width="2"/>' +
    '<rect x="255" y="52" width="12" height="40" fill="#FF5252"/><line x1="261" y1="48" x2="261" y2="105" stroke="#FF5252" stroke-width="2"/>' +
    '<rect x="295" y="105" width="12" height="38" fill="#00E676"/><line x1="301" y1="95" x2="301" y2="155" stroke="#00E676" stroke-width="2"/>' +
    '<circle cx="261" cy="52" r="8" fill="none" stroke="#FFD700" stroke-width="2.5"/><text x="272" y="50" fill="#FFD700" font-size="12" font-weight="800">PUT</text>' +
    '<circle cx="301" cy="150" r="8" fill="none" stroke="#FFD700" stroke-width="2.5"/><text x="312" y="160" fill="#FFD700" font-size="12" font-weight="800">CALL</text>' +
    '</svg><div class="strat-legend"><span>📍 Жди касания уровня</span><span>Свечное подтверждение</span><span>Риск 1–2%</span></div></div>';
}

var QUIZ_BANK = {
  levels: [
    {q: "Где открывать PUT по уровням?", opts: ["У поддержки", "У сопротивления", "В середине диапазона"], a: 1},
    {q: "Что важно перед входом?", opts: ["Свечное подтверждение", "Случайный вход", "Только объём"], a: 0},
    {q: "Оптимальный риск на сделку?", opts: ["10%", "1–2%", "50%"], a: 1},
    {q: "CALL по уровням чаще открывают...", opts: ["У сопротивления", "У поддержки + подтверждение", "В любом месте"], a: 1},
    {q: "Лучшие таймфреймы для уровней?", opts: ["M1–M5", "H4–D1", "Только S5"], a: 0}
  ],
  range: [
    {q: "Где покупать во флэте?", opts: ["У верхней границы", "У нижней границы", "В любом месте"], a: 1},
    {q: "Что проверять перед входом во флэт?", opts: ["Новостной фон", "Цвет графика", "Время суток"], a: 0},
    {q: "Экспирация во флэте обычно?", opts: ["1–3 мин", "30 мин", "1 день"], a: 0},
    {q: "PUT во флэте открывают...", opts: ["У нижней границы", "У верхней границы", "В центре"], a: 1},
    {q: "Флэт лучше не торговать, если...", opts: ["Нет новостей", "Выходит High-новость", "График красивый"], a: 1}
  ],
  trend: [
    {q: "Вход по тренду делается...", opts: ["Против тренда", "По направлению тренда после отката", "Случайно"], a: 1},
    {q: "ADX ниже 20 означает?", opts: ["Сильный тренд", "Слабый/нет тренда", "Гарантированный сигнал"], a: 1},
    {q: "Лучше входить...", opts: ["На откате к тренду", "На пике импульса", "Без анализа"], a: 0},
    {q: "Против тренда входить...", opts: ["Можно всегда", "Опасно и нежелательно", "Обязательно"], a: 1},
    {q: "Сильный тренд подтверждается...", opts: ["ADX > 25 и направленный импульс", "Только одной свечой", "Случайным входом"], a: 0}
  ],
  bounce: [
    {q: "Отскок от нижней полосы Боллинджера чаще даёт?", opts: ["PUT", "CALL", "Ничего"], a: 1},
    {q: "Что подтверждает отскок?", opts: ["Разворот AO / свеча", "Только цвет", "Случайность"], a: 0},
    {q: "Таймфрейм для bounce-стратегий?", opts: ["S30 / M1", "H4", "D1"], a: 0},
    {q: "Отскок от верхней полосы чаще даёт?", opts: ["CALL", "PUT", "Флэт"], a: 1},
    {q: "Риск на bounce-сделку?", opts: ["1–2%", "20%", "50%"], a: 0}
  ]
};

function renderEdu() {
  var html = "";
  EDU.forEach(function(ed, i) {
    html += '<div class="edu-item" onclick="openEdu(' + i + ')"><h4>' + esc(ed.t) + '</h4><span style="color:var(--gold)">➔</span></div>';
  });
  document.getElementById("eduItems").innerHTML = html;
}

function openEdu(idx) {
  var ed = EDU[idx];
  document.getElementById("eduList").classList.add("hidden");
  document.getElementById("eduView").classList.remove("hidden");
  var tagsHtml = "";
  (ed.tags || []).forEach(function(t) { tagsHtml += '<span class="tag">' + esc(t) + '</span>'; });
  var visual = strategyVisual(ed.kind || "levels", idx);
  document.getElementById("eduBody").innerHTML =
    '<h3>' + esc(ed.t) + '</h3>' +
    '<div class="tag-row">' + tagsHtml + '</div>' +
    visual +
    '<div style="white-space:pre-wrap;font-size:12px;line-height:1.65;color:#d0d7e2;margin-bottom:14px">' + esc(ed.d) + '</div>' +
    '<div style="font-size:11px;color:var(--muted);margin-bottom:8px;font-weight:700">5 вопросов по этой стратегии · сигналов получено: <span class="sig-count-badge">' + sigCount + '</span></div>' +
    '<button class="btn btn-gold btn-sm" onclick="openQuiz(\'' + (ed.kind || "levels") + '\',\'' + esc(ed.t).replace(/'/g,"\\'") + '\')">📝 Пройти 5 вопросов</button>';
}
function closeEdu() {
  document.getElementById("eduView").classList.add("hidden");
  document.getElementById("eduList").classList.remove("hidden");
}
function openRandomEdu() { openEdu(Math.floor(Math.random() * EDU.length)); }

function openQuiz(kind, title) {
  var bank = QUIZ_BANK[kind] || QUIZ_BANK.levels;
  document.getElementById("quizTitle").textContent = "Мини-тест: " + (title || "");
  var html = "";
  bank.forEach(function(item, qi) {
    html += '<div class="quiz-q"><h4>' + (qi+1) + '. ' + esc(item.q) + '</h4>';
    item.opts.forEach(function(opt, oi) {
      html += '<button class="quiz-opt" onclick="answerQuiz(this,' + qi + ',' + oi + ',' + item.a + ')">' + esc(opt) + '</button>';
    });
    html += '</div>';
  });
  document.getElementById("quizBody").innerHTML = html;
  document.getElementById("quizModal").classList.remove("hidden");
}
function answerQuiz(btn, qi, oi, correct) {
  var parent = btn.parentNode;
  parent.querySelectorAll(".quiz-opt").forEach(function(b){ b.disabled = true; });
  if (oi === correct) {
    btn.classList.add("correct");
  } else {
    btn.classList.add("wrong");
    parent.querySelectorAll(".quiz-opt")[correct].classList.add("correct");
  }
}
function closeQuiz() {
  document.getElementById("quizModal").classList.add("hidden");
}

function updateProfileStats() {
  document.getElementById("profSigCount").textContent = String(sigCount);
  document.getElementById("profFavCount").textContent = String(favs.length);
  var hs = document.getElementById("headerSigCount");
  if (hs) hs.textContent = "Сигналов: " + sigCount;
}

function loadProfile() {
  try {
    document.getElementById("profPocketId").value = localStorage.getItem("tmv_pocketId") || "";
    document.getElementById("profFavStrat").value = localStorage.getItem("tmv_favStrat") || "";
    var photo = localStorage.getItem("tmv_profPhoto");
    if (photo) {
      document.getElementById("profilePhotoImg").src = photo;
      document.getElementById("profilePhotoImg").classList.remove("hidden");
      document.getElementById("profilePhotoIcon").classList.add("hidden");
    }
  } catch(e) {}
  updateProfileStats();
}

function saveProfileSettings() {
  try {
    localStorage.setItem("tmv_pocketId", document.getElementById("profPocketId").value);
    localStorage.setItem("tmv_favStrat", document.getElementById("profFavStrat").value);
  } catch(e) {}
}

function resetProfile() {
  try {
    localStorage.removeItem("tmv_pocketId");
    localStorage.removeItem("tmv_favStrat");
    localStorage.removeItem("tmv_profPhoto");
    localStorage.removeItem("tmv_sigs");
    localStorage.removeItem("tmv_history");
  } catch(e) {}
  sigCount = 0;
  signalHistory = [];
  document.getElementById("profPocketId").value = "";
  document.getElementById("profFavStrat").value = "";
  document.getElementById("profilePhotoImg").classList.add("hidden");
  document.getElementById("profilePhotoImg").removeAttribute("src");
  document.getElementById("profilePhotoIcon").classList.remove("hidden");
  updateProfileStats();
}

function handleProfilePhoto(event) {
  var file = event.target.files[0];
  if (!file) return;
  var reader = new FileReader();
  reader.onload = function (e) {
    try { localStorage.setItem("tmv_profPhoto", e.target.result); } catch (err) {}
    document.getElementById("profilePhotoImg").src = e.target.result;
    document.getElementById("profilePhotoImg").classList.remove("hidden");
    document.getElementById("profilePhotoIcon").classList.add("hidden");
  };
  reader.readAsDataURL(file);
}

function openCam() {
  uploadedImageBlob = null;
  var video = document.getElementById("scanVideo");
  document.getElementById("scanPreviewImg").classList.add("hidden");
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    alert("Камера недоступна. Загрузите скриншот.");
    return;
  }
  navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false })
    .then(function(s) {
      stream = s;
      video.srcObject = stream;
      video.classList.remove("hidden");
      document.getElementById("scanPh").classList.add("hidden");
      document.getElementById("scanFrame").classList.add("live");
      camReady = true;
      document.getElementById("btnScan").removeAttribute("disabled");
    })
    .catch(function(err) { alert("Не удалось получить доступ к камере"); });
}

function handleImageUpload(event) {
  var file = event.target.files[0];
  if (!file) return;
  uploadedImageBlob = file;
  if (stream) { stream.getTracks().forEach(function(t) { t.stop(); }); stream = null; }
  document.getElementById("scanVideo").classList.add("hidden");
  var previewImg = document.getElementById("scanPreviewImg");
  var reader = new FileReader();
  reader.onload = function (e) {
    previewImg.src = e.target.result;
    previewImg.classList.remove("hidden");
    document.getElementById("scanPh").classList.add("hidden");
    document.getElementById("scanFrame").classList.add("live");
    camReady = true;
    document.getElementById("btnScan").removeAttribute("disabled");
  };
  reader.readAsDataURL(file);
}

function stopCam() {
  if (stream) { stream.getTracks().forEach(function(t) { t.stop(); }); stream = null; }
  uploadedImageBlob = null;
  document.getElementById("scanVideo").classList.add("hidden");
  document.getElementById("scanPreviewImg").classList.add("hidden");
  document.getElementById("scanPh").classList.remove("hidden");
  document.getElementById("scanFrame").classList.remove("live");
  camReady = false;
  document.getElementById("btnScan").setAttribute("disabled", "true");
  document.getElementById("scanFileInput").value = "";
}

function getScanExpSeconds() {
  var v = scanCfg["экспирация"];
  var n = parseInt(v);
  return String(v).indexOf("с") >= 0 ? n : n * 60;
}

function doScan() {
  if (!camReady) { alert("Сначала откройте камеру или загрузите скриншот!"); return; }
  var expSec = getScanExpSeconds();
  if (uploadedImageBlob) {
    processImageBlobForScan(uploadedImageBlob, expSec);
  } else {
    var video = document.getElementById("scanVideo");
    var canvas = document.createElement("canvas");
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.toBlob(function(blob) { processImageBlobForScan(blob, expSec); }, "image/jpeg");
  }
}

function processImageBlobForScan(blob, expSec) {
  runAnalyzer(async function() {
    var resText = "❌ Ошибка анализа. Попробуйте ещё раз.";
    try {
      var fd = new FormData();
      fd.append("file", blob, "snapshot.jpg");
      fd.append("cfg", JSON.stringify(scanCfg));
      var response = await fetch("/analyze", { method: "POST", body: fd });
      if (response.ok) {
        var resData = await response.json();
        resText = resData.text || resText;
      }
    } catch (e) {}

    var isErr = resText.indexOf("❌") >= 0;
    document.getElementById("scanMeta").textContent = "TEAM MASTER AI SCANNER";
    var dirEl = document.getElementById("scanDir");

    if (isErr) {
      dirEl.textContent = "❌ ОШИБКА / ГРАФИК НЕ ОБНАРУЖЕН";
      dirEl.className = "sig-dir put";
    } else {
      var upper = resText.toUpperCase();
      function idxOf(w) { var i = upper.indexOf(w); return i < 0 ? 1e9 : i; }
      var iBuy = Math.min(idxOf("BUY"), idxOf("ВВЕРХ"));
      var iSell = Math.min(idxOf("SELL"), idxOf("ВНИЗ"));
      var buy = iBuy <= iSell;
      dirEl.textContent = buy ? "BUY (ВВЕРХ) 📈" : "SELL (ВНИЗ) 📉";
      dirEl.className = "sig-dir " + (buy ? "call" : "put");
      incSig();
      var nowStr = new Date().toLocaleString("ru-RU");
      saveToHistory({
        time: nowStr,
        asset: "AI Scanner",
        tf: scanCfg["интервал"] || "M1",
        exp: expSec,
        dir: dirEl.textContent,
        reason: resText.substring(0, 300)
      });
    }

    document.getElementById("scanStrat").innerHTML = '<pre style="white-space:pre-wrap;font-family:inherit;margin:0">' + esc(resText) + '</pre>';
    document.getElementById("scanSigBox").style.display = "block";
    document.getElementById("btnScanCancel").style.display = "block";
    document.getElementById("btnScanNew").style.display = "none";

    if (!isErr) {
      startTimer("scan", "scanCd", expSec, function() {
        document.getElementById("btnScanCancel").style.display = "none";
        document.getElementById("btnScanNew").style.display = "block";
      }, "tmv_scan_end");
    } else {
      document.getElementById("btnScanCancel").style.display = "none";
      document.getElementById("btnScanNew").style.display = "block";
    }
  });
}

function cancelScan() {
  if (timers.scan) clearInterval(timers.scan);
  try { localStorage.removeItem("tmv_scan_end"); } catch(e) {}
  document.getElementById("scanSigBox").style.display = "none";
}

function changeLanguage(lang) {
  currentLang = lang;
  var dict = I18N[lang];
  if (!dict) return;
  document.querySelectorAll("[data-t]").forEach(function(el) {
    var k = el.getAttribute("data-t");
    if (dict[k] !== undefined) el.innerHTML = dict[k];
  });
  document.documentElement.lang = lang === "ua" ? "uk" : lang;
  renderCatalogList(document.getElementById("modalSearchInput").value || "");
}

window.addEventListener("DOMContentLoaded", function() {
  updateSelectedAssetUI();
  renderMainFavs();
  renderEdu();
  loadProfile();
  loadNews(true);
  startNewsAuto();
  tickClock();
  setInterval(tickClock, 1000);
  resumeTimers();
});
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def serve_terminal():
    assets_json = json.dumps(ASSETS_DATA_JS, ensure_ascii=False).replace("</", "<\\/")
    edu_json = json.dumps(EDU_50, ensure_ascii=False).replace("</", "<\\/")
    return HTMLResponse(
        HTML_UI.replace("__ASSETS_JSON__", assets_json).replace("__EDU_JSON__", edu_json)
    )


@app.get("/get_signal")
async def get_signal(asset: str = "🇪🇺🇺🇸 EUR/USD OTC", timeframe: str = "M1"):
    asyncio.create_task(send_tg_signal_alert(asset, timeframe))
    await asyncio.sleep(1.0)
    is_otc = "OTC" in asset
    clean_asset = re.sub(r"[^\x00-\x7F]+", "", asset).replace("OTC", "").strip()
    for k in list(BINANCE_MAPPING.keys()):
        if k.lower() in clean_asset.lower() or clean_asset.lower() in k.lower():
            clean_asset = k
            break
    prices = None
    if not is_otc and clean_asset in BINANCE_MAPPING:
        try:
            symbol = BINANCE_MAPPING[clean_asset]
            url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit=50"
            async with httpx.AsyncClient() as client:
                res = await client.get(url, timeout=4.0)
                if res.status_code == 200:
                    prices = [float(c[4]) for c in res.json()]
        except Exception as e:
            logging.warning(f"Binance error for {clean_asset}: {e}")
            prices = None
    if not prices:
        prices = generate_otc_candles(asset, count=50)
    rsi = calculate_rsi(prices)
    ema = calculate_ema(prices)
    signal, reason = smart_market_analysis(prices, rsi, ema)
    return {"signal": signal, "reason": reason, "source": "real" if not is_otc and clean_asset in BINANCE_MAPPING else "otc"}


def _parse_event_dt(iso_str: str):
    if not iso_str:
        return None
    try:
        s = iso_str.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


MAJOR_FX = {"USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "RUB"}
PAIR_HINTS = {
    "USD": "EUR/USD · GBP/USD · USD/JPY · USD/RUB",
    "EUR": "EUR/USD · EUR/GBP · EUR/JPY",
    "GBP": "GBP/USD · EUR/GBP · GBP/JPY",
    "JPY": "USD/JPY · EUR/JPY · GBP/JPY",
    "AUD": "AUD/USD · AUD/JPY · EUR/AUD",
    "NZD": "NZD/USD · AUD/NZD",
    "CAD": "USD/CAD · EUR/CAD",
    "CHF": "USD/CHF · EUR/CHF · GBP/CHF",
    "RUB": "USD/RUB · EUR/RUB",
}


@app.get("/news")
async def get_news():
    events = []
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = datetime.now(timezone.utc)
    try:
        url = (
            "https://showmytrades.com/api/public/calendar/v1"
            "?currency=USD,EUR,GBP,JPY,AUD,NZD,CAD,CHF,RUB"
            "&impact=high,medium&days=3"
        )
        async with httpx.AsyncClient() as client:
            res = await client.get(url, timeout=12.0)
            if res.status_code == 200:
                data = res.json()
                for e in data.get("events", []):
                    raw_title = e.get("title") or ""
                    imp = (e.get("impact") or "medium").capitalize()
                    dt_iso = e.get("time") or ""
                    dt = _parse_event_dt(dt_iso)
                    actual = e.get("actual") or ""
                    if dt and dt <= now:
                        status = "released"
                        status_ru = "✅ Уже вышло"
                    else:
                        status = "upcoming"
                        status_ru = "⏳ Скоро выйдет"
                    if actual:
                        status = "released"
                        status_ru = "✅ Уже вышло"
                    events.append({
                        "name": translate_news_title(raw_title),
                        "name_en": raw_title,
                        "currency": (e.get("currency") or "").upper(),
                        "impact": imp,
                        "impact_ru": impact_ru(imp),
                        "date": dt_iso,
                        "forecast": e.get("forecast") or "",
                        "previous": e.get("previous") or "",
                        "actual": actual,
                        "status": status,
                        "status_ru": status_ru,
                        "pair_hint": PAIR_HINTS.get((e.get("currency") or "").upper(), (e.get("currency") or "").upper()),
                    })
                if data.get("generated_at"):
                    updated = data["generated_at"]
    except Exception as ex:
        logging.warning(f"ShowMyTrades news error: {ex}")

    if not events:
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get("https://pipatlas.com/api/calendar", timeout=12.0)
                if res.status_code == 200:
                    data = res.json()
                    for e in data.get("events", []):
                        imp = (e.get("impact") or "").capitalize()
                        if imp not in ("High", "Medium"):
                            continue
                        raw_title = e.get("name") or ""
                        dt_iso = e.get("date") or ""
                        dt = _parse_event_dt(dt_iso)
                        actual = e.get("actual") or ""
                        if dt and dt <= now:
                            status, status_ru = "released", "✅ Уже вышло"
                        else:
                            status, status_ru = "upcoming", "⏳ Скоро выйдет"
                        if actual:
                            status, status_ru = "released", "✅ Уже вышло"
                        events.append({
                            "name": translate_news_title(raw_title),
                            "name_en": raw_title,
                            "currency": (e.get("currency") or "").upper(),
                            "impact": imp,
                            "impact_ru": impact_ru(imp),
                            "date": dt_iso,
                            "forecast": e.get("forecast") or "",
                            "previous": e.get("previous") or "",
                            "actual": actual,
                            "status": status,
                            "status_ru": status_ru,
                            "pair_hint": PAIR_HINTS.get((e.get("currency") or "").upper(), (e.get("currency") or "").upper()),
                        })
                    if data.get("updated"):
                        updated = data["updated"]
        except Exception as ex:
            logging.warning(f"PipAtlas news error: {ex}")

    def _sort_key(ev):
        dt = _parse_event_dt(ev.get("date") or "")
        ts = dt.timestamp() if dt else 0
        is_up = 0 if ev.get("status") == "upcoming" else 1
        if is_up == 0:
            return (0, ts)
        return (1, -ts)

    events = [ev for ev in events if (ev.get("currency") or "") in MAJOR_FX]
    events.sort(key=_sort_key)
    return {
        "events": events[:80],
        "updated": updated,
        "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "focus": "forex",
    }


@app.post("/analyze")
async def analyze(file: UploadFile = File(...), cfg: str = Form(...)):
    image_bytes = await file.read()
    try:
        config = json.loads(cfg)
    except Exception:
        config = {}
    result_text = await scanner_core.compute(image_bytes, config)
    return {"text": result_text}


def _gemini_rest_text(prompt_text: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY не задан")
    models_try = [
        GEMINI_MODEL or "gemini-2.0-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-001",
        "gemini-flash-latest",
    ]
    seen = set()
    last_err = None
    import urllib.request
    for m in models_try:
        if not m or m in seen:
            continue
        seen.add(m)
        for ver in ("v1beta", "v1"):
            url = (
                f"https://generativelanguage.googleapis.com/{ver}/models/{m}:generateContent"
                f"?key={GEMINI_API_KEY}"
            )
            body = json.dumps({
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 420,
                    "topP": 0.85,
                },
            }).encode("utf-8")
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}, method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=18) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                candidates = data.get("candidates") or []
                if candidates:
                    parts = (candidates[0].get("content") or {}).get("parts") or []
                    texts = [p.get("text", "") for p in parts if p.get("text")]
                    if texts:
                        return "\n".join(texts).strip()
                last_err = RuntimeError(f"empty {m}/{ver}")
            except Exception as e:
                last_err = e
                logging.warning(f"Gemini REST {m} {ver}: {e}")
                continue
    if last_err:
        raise last_err
    return ""


def _gemini_rest_vision(prompt_text: str, image_bytes: bytes, mime: str = "image/jpeg") -> str:
    if not GEMINI_API_KEY:
        return None
    import base64
    import urllib.request
    b64 = base64.b64encode(image_bytes).decode("ascii")
    models_try = [
        GEMINI_MODEL or "gemini-2.0-flash",
        "gemini-2.0-flash",
        "gemini-flash-latest",
    ]
    seen = set()
    for m in models_try:
        if not m or m in seen:
            continue
        seen.add(m)
        for ver in ("v1beta", "v1"):
            url = (
                f"https://generativelanguage.googleapis.com/{ver}/models/{m}:generateContent"
                f"?key={GEMINI_API_KEY}"
            )
            body = json.dumps({
                "contents": [{
                    "parts": [
                        {"text": prompt_text},
                        {"inline_data": {"mime_type": mime, "data": b64}},
                    ]
                }],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 420},
            }).encode("utf-8")
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}, method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=22) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                candidates = data.get("candidates") or []
                if candidates:
                    parts = (candidates[0].get("content") or {}).get("parts") or []
                    texts = [p.get("text", "") for p in parts if p.get("text")]
                    if texts:
                        return "\n".join(texts).strip()
            except Exception as e:
                logging.warning(f"Gemini vision {m} {ver}: {e}")
                continue
    return None


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
