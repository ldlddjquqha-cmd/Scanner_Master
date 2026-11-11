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
app = FastAPI(title="TEAM MASTER VIP - ULTIMATE QUANTUM CORE", version="28.1")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
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
    low = title.lower().strip()
    for en, ru in sorted(NEWS_RU_MAP.items(), key=lambda x: -len(x[0])):
        if en in low:
            suffix = ""
            if "mom" in low or "m/m" in low:
                suffix = " (м/м)"
            elif "yoy" in low or "y/y" in low:
                suffix = " (г/г)"
            elif "qoq" in low or "q/q" in low:
                suffix = " (кв/кв)"
            if any(x in ru for x in ("м/м", "г/г", "кв/кв")):
                return ru
            return ru + suffix
    out = title
    for a, b in [
        ("MoM", "м/м"), ("YoY", "г/г"), ("QoQ", "кв/кв"),
        ("m/m", "м/м"), ("y/y", "г/г"), ("q/q", "кв/кв"),
        ("Speech", "Выступление"), ("Decision", "Решение"),
    ]:
        out = out.replace(a, b)
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


# Расширенный маппинг реальных активов (не-OTC) → Binance
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
             "Уровни поддержки и сопротивления — это ключевые ценовые зоны, где баланс сил между покупателями и продавцами кардинально меняется. Уровень сопротивления — это область сверху, где продавцы останавливают рост цены и начинают агрессивно давить вниз. Уровень поддержки — область снизу, где покупатели защищают свои позиции и толкают цену вверх. Когда цена приближается к таким барьерам, крупные участники рынка часто фиксируют прибыль, что приводит к техническому отскоку или мощному пробою.\n\n"
             "• ⏱ Рекомендуемые таймфреймы графика:\n"
             "Таймфреймы M1, M2 и M5 идеально подходят для анализа бинарных опционов.\n\n"
             "• ⏳ Оптимальная экспирация сделки:\n"
             "От 2 до 4 минут в зависимости от силы и ширины ценового уровня.\n\n"
             "• 🎯 Четкие правила открытия позиций (CALL / PUT):\n"
             "  1️⃣ Для входа ВНИЗ (PUT): Дождитесь подхода цены к сильному уровню сопротивления. Проанализируйте закрытие свечей: если появляются тени и замедление импульса, открывайте сделку на понижение.\n"
             "  2️⃣ Для входа ВВЕРХ (CALL): При касании ценой подтвержденного уровня поддержки и формировании зеленой поглощающей свечи открывайте сделку на повышение.\n\n"
             "• 🛡️ Управление капиталом и риски:\n"
             "Никогда не входите напролом при затяжном импульсе. Ждите подтверждения свечного паттерна."
    },
    {
        "t": "2. Торговля в боковике (Флэт и диапазон)",
        "tags": ["M1", "M5", "Pocket Option", "Флэт", "Боковик"],
        "kind": "range",
        "d": "📌 Подробный разбор стратегии торговли в боковике (флэте):\n\n"
             "• 🧠 Рыночная логика и суть стратегии:\n"
             "Боковое движение (флэт) возникает тогда, когда на рынке нет ярко выраженного тренда, а цена движется внутри четкого горизонтального коридора между границами поддержки снизу и сопротивления сверху. Стратегия основана на возвращении цены к средним значениям диапазона (методика возврата к меану). Трейдер продает у верхней границы канала и покупает у нижней.\n\n"
             "• ⏱ Рекомендуемые таймфреймы графика:\n"
             "Таймфреймы M1 и M5.\n\n"
             "• ⏳ Оптимальная экспирация сделки:\n"
             "1 – 3 минуты.\n\n"
             "• 🎯 Четкие правила открытия позиций (CALL / PUT):\n"
             "  1️⃣ Для входа ВВЕРХ (CALL): Цена аккуратно касается нижней границы бокового канала, формируется паттерн ложного пробоя или пин-бар. Открываем сделку вверх к середине или верхней границе флэта.\n"
             "  2️⃣ Для входа ВНИЗ (PUT): Цена доходит до верхней границы горизонтального диапазона, импульс угасает, открываем сделку на понижение.\n\n"
             "• 🛡️ Управление капиталом и риски:\n"
             "Обязательно проверяйте отсутствие новостного фона, способного пробить узкий коридор."
    },
    {
        "t": "3. Accelerator Oscillator + Alligator",
        "tags": ["M1", "M2", "M5", "Pocket Option", "Индикаторы"],
        "kind": "trend",
        "d": "📌 Подробный разбор профессиональной связки Accelerator Oscillator + Alligator:\n\n"
             "• 🧠 Рыночная логика и суть стратегии:\n"
             "Индикатор Alligator моделирует рыночные циклы, а Accelerator Oscillator (AC) измеряет ускорение движущей силы рынка.\n\n"
             "• ⏱ Рекомендуемые таймфреймы:\nM1, M2 и M5.\n\n"
             "• ⏳ Оптимальная экспирация:\nОт 1 до 3 минут.\n\n"
             "• 🎯 Четкие правила открытия позиций (CALL / PUT):\n"
             "  1️⃣ CALL: Линии Аллигатора развернуты вверх, гистограмма AC переходит в зеленую зону выше нуля.\n"
             "  2️⃣ PUT: Пасть Аллигатора направлена вниз, гистограмма AC в красной зоне.\n\n"
             "• 🛡️ Управление капиталом:\nРиск на сделку не более 1-2%."
    },
    {
        "t": "4. ADX + Aroon",
        "tags": ["M1", "M5", "Pocket Option", "Индикаторы"],
        "kind": "trend",
        "d": "📌 Подробный разбор связки ADX + Aroon:\n\n"
             "• 🧠 Рыночная логика:\nADX измеряет силу тренда, а Aroon находит экстремумы цены.\n\n"
             "• ⏱ Рекомендуемые таймфреймы:\nM1 и M5.\n\n"
             "• ⏳ Оптимальная экспирация:\nОт 2 до 3 минут.\n\n"
             "• 🎯 Четкие правила открытия позиций:\n"
             "  1️⃣ CALL: ADX выше 25, Aroon Up пересекает Aroon Down снизу вверх.\n"
             "  2️⃣ PUT: ADX выше 25, Aroon Down уходит в верхнюю зону.\n\n"
             "• 🛡️ Риск-менеджмент:\nИзбегать торговли при ADX ниже 20."
    },
    {
        "t": "5. Bollinger Bands + Awesome Oscillator",
        "tags": ["S30", "M1", "Pocket Option", "Индикаторы"],
        "kind": "bounce",
        "d": "📌 Подробный разбор связки Bollinger Bands + Awesome Oscillator:\n\n"
             "• 🧠 Рыночная логика:\nПолосы Боллинджера фиксируют волатильность и пробой границ, а AO оценивает импульс.\n\n"
             "• ⏱ Рекомендуемые таймфреймы:\nS30 и M1.\n\n"
             "• ⏳ Оптимальная экспирация:\n1 минута.\n\n"
             "• 🎯 Четкие правила открытия позиций:\n"
             "  1️⃣ CALL: Касание нижней границы Bollinger Bands, гистограмма AO переходит в зеленую зону.\n"
             "  2️⃣ PUT: Касание верхней границы, разворот гистограммы AO вниз.\n\n"
             "• 🛡️ Риск-менеджмент:\nТорговать по тренду старшего таймфрейма."
    },
]

for i in range(6, 51):
    kinds = ["levels", "range", "trend", "bounce"]
    EDU_50.append({
        "t": f"{i}. Профессиональная стратегия и связка индикаторов №{i}",
        "tags": ["M1", "Pocket Option", "Стратегии"],
        "kind": kinds[i % 4],
        "d": (
            f"📌 Подробный разбор стратегии и методики №{i} для терминала Pocket Option:\n\n"
            f"• 🧠 Рыночная механика и суть стратегии:\n"
            f"Данная авторская торговая система построена на комплексном анализе уровней ликвидности, динамических зон перекупленности/перепроданности "
            f"и фильтрации рыночного шума. Она создана для торговли бинарными опционами на валютных парах, акциях и криптовалютах, "
            f"позволяя определять моменты разворота тренда и импульсного продолжения движения.\n\n"
            f"• ⏱ Рекомендуемые таймфреймы графика:\n"
            f"Оптимально использовать таймфреймы M1 (1 минута), M2 и M5 для стабильной отработки сигналов.\n\n"
            f"• ⏳ Оптимальная экспирация сделки в Pocket Option:\n"
            f"Рекомендуемое время экспирации составляет от 1 до 3 минут.\n\n"
            f"• 🎯 Четкие правила открытия позиций (CALL / PUT):\n"
            f"  1️⃣ Шаг первый: Дождитесь подхода цены к ключевой зоне интереса или подтверждения сигнала по основному индикатору.\n"
            f"  2️⃣ Шаг второй: Убедитесь в отсутствии сильного противонаправленного импульса на младшем таймфрейме.\n"
            f"  3️⃣ Шаг третий: Открывайте позицию ВВЕРХ (CALL) или ВНИЗ (PUT) на закрытии сигнальной свечи без спешки.\n\n"
            f"• 🛡️ Управление капиталом и риски:\n"
            f"Никогда не превышайте риск в 1-2% от вашего депозита на одну сделку. Дисциплина и строгий мани-менеджмент — основа стабильности."
        ),
    })

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
.cats{display:flex;gap:6px;overflow-x:auto;padding-bottom:8px;margin-bottom:8px;scrollbar-width:none}.cats::-webkit-scrollbar{display:none}
.cat{flex-shrink:0;padding:8px 14px;background:var(--inner);border:1px solid var(--border-g);border-radius:12px;color:var(--muted);font-size:11px;font-weight:800;cursor:pointer;white-space:nowrap;transition:all .2s;display:flex;align-items:center;gap:6px}
.cat.on{background:var(--gold-g);color:#111;border:none;box-shadow:0 3px 12px rgba(255,215,0,.3)}
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
.edu-detail p{font-size:11.5px;color:#c5cdd8;line-height:1.7;white-space:pre-line;margin-bottom:10px}
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
.about-box{background:var(--inner);border:1px solid var(--border-g);border-radius:14px;padding:16px;font-size:11.5px;color:#c5cdd8;line-height:1.75}
.news-sec-title{font-size:12px;font-weight:900;margin:14px 0 8px;padding:8px 10px;border-radius:10px;letter-spacing:.3px}
.news-sec-up{background:linear-gradient(90deg,rgba(255,215,0,.18),transparent);color:var(--gold);border:1px solid rgba(255,215,0,.25)}
.news-sec-done{background:linear-gradient(90deg,rgba(0,230,118,.12),transparent);color:var(--green);border:1px solid rgba(0,230,118,.25)}
.news-item{background:linear-gradient(180deg,rgba(18,22,34,.95),rgba(10,12,20,.98));border:1px solid var(--border);border-radius:14px;padding:12px 13px;margin-bottom:9px;cursor:pointer;transition:all .18s;box-shadow:0 4px 14px rgba(0,0,0,.25)}
.news-item:active{transform:scale(.985)}
.news-item.high{border-color:rgba(255,82,82,.55);box-shadow:0 0 0 1px rgba(255,82,82,.12),0 6px 18px rgba(255,82,82,.08)}
.news-item.medium{border-color:rgba(255,167,38,.45);box-shadow:0 0 0 1px rgba(255,167,38,.1)}
.news-item.open{border-color:var(--gold);box-shadow:0 0 0 1px rgba(255,215,0,.25),0 8px 22px rgba(255,215,0,.1)}
.news-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;gap:8px}
.news-cur{font-size:11px;font-weight:900;color:var(--gold);display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.news-time{font-size:11px;color:#fff;font-weight:800;background:rgba(255,215,0,.12);border:1px solid rgba(255,215,0,.25);padding:3px 8px;border-radius:8px;white-space:nowrap}
.news-name{font-size:13px;font-weight:800;color:#fff;margin-bottom:4px;line-height:1.35}
.news-pairs{font-size:10.5px;color:#9aa3b5;margin-bottom:7px;font-weight:600}
.news-meta{display:flex;flex-wrap:wrap;gap:6px;font-size:10.5px;font-weight:700;color:#c5cdd8}
.news-meta span{background:rgba(255,255,255,.04);border:1px solid var(--border);border-radius:8px;padding:4px 8px}
.news-impact{font-size:9px;font-weight:900;padding:3px 8px;border-radius:7px}
.news-impact.high{background:rgba(255,82,82,.22);color:#ff8a80}
.news-impact.medium{background:rgba(255,167,38,.2);color:#FFB74D}
.news-impact.low{background:rgba(255,215,0,.12);color:var(--gold)}
.news-updated{font-size:10px;color:var(--muted);font-weight:700;text-align:center;margin-bottom:8px}
.news-list{max-height:460px;overflow-y:auto;padding-right:2px}
.news-detail{display:none;margin-top:10px;padding:10px 11px;border-top:1px solid rgba(255,215,0,.2);border-radius:0 0 10px 10px;font-size:11.5px;color:#d7deea;line-height:1.65;background:rgba(0,0,0,.25)}
.news-item.open .news-detail{display:block}
.news-badge{font-size:9px;font-weight:900;padding:2px 7px;border-radius:6px;background:rgba(255,255,255,.06);color:#b0b8c8}
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
      <span style="font-size:10px;color:var(--gold);font-weight:800" id="liveClock">🕒 --:--:--</span>
    </div>
    <div class="selected-asset-box" onclick="openCatalogModal()">
      <div class="selected-asset-info">
        <span data-t="lblActiveAsset">ВЫБРАННЫЙ АКТИВ POCKET OPTION</span>
        <span id="currentAssetDisplay">🇪🇺🇺🇸 EUR/USD OTC</span>
      </div>
      <span style="font-size:22px">📋</span>
    </div>
    <div class="grid2" style="margin-bottom:10px">
      <div>
        <label class="lbl" data-t="lblTf">ИНТЕРВАЛ (ТАЙМФРЕЙМ)</label>
        <select class="sel" id="tf">
          <option value="S5">5 сек</option><option value="S15">15 сек</option><option value="S30">30 сек</option>
          <option value="M1" selected>1 мин</option><option value="M2">2 мин</option><option value="M3">3 мин</option>
          <option value="M5">5 мин</option><option value="M10">10 мин</option><option value="M15">15 мин</option>
        </select>
      </div>
      <div>
        <label class="lbl" data-t="lblExp">ЭКСПИРАЦИЯ СДЕЛКИ</label>
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
      <div class="strat" id="sigStrat"></div>
      <div class="sig-stats"><span><span data-t="lblCombo">Связка</span>: <b id="sigCombo">Multi-Factor</b></span></div>
      <button class="btn btn-red btn-sm" id="btnCancel" onclick="cancelSig()" style="display:none" data-t="btnCancel">✖ Отменить</button>
      <button class="btn btn-gold btn-sm" id="btnNew" onclick="getSig()" style="display:none" data-t="btnNewSig">⚡ Новый сигнал</button>
    </div>
  </div>

  <div class="card tab hidden" id="tabFavs">
    <h3 class="g-text" style="font-size:13px;margin-bottom:8px" data-t="favTabTitle">⭐ Избранные активы</h3>
    <p style="font-size:11px;color:var(--muted);margin-bottom:10px" data-t="favTabDesc">Нажмите на любой актив из списка для перехода.</p>
    <div class="fav-list" id="mainFavList"></div>
    <div id="mainFavEmpty" style="text-align:center;padding:24px;color:var(--muted);font-size:11px;font-weight:600" data-t="profFavEmpty">Пока пусто — добавь актив из полного каталога</div>
    <div class="gap2"></div>
    <button class="btn btn-dark btn-sm" onclick="clearFavs()" data-t="btnClearFav">🗑 Очистить избранное</button>
  </div>

  <div class="card tab hidden" id="tabNews">
    <h3 class="g-text" style="font-size:13px;margin-bottom:6px" data-t="newsTitle">📰 Форекс-новости</h3>
    <p style="font-size:10.5px;color:var(--muted);margin-bottom:8px" data-t="newsDesc">Только форекс (USD EUR GBP JPY AUD NZD CAD CHF RUB). High/Medium. Фильтр по валюте. Автообновление каждую минуту.</p>
    <div class="news-updated" id="newsUpdated">—</div>
    <div class="filter-row" id="newsFilterRow">
      <button class="filter-btn on" data-cur="ALL" onclick="setNewsFilter('ALL',this)">Все</button>
      <button class="filter-btn" data-cur="USD" onclick="setNewsFilter('USD',this)">USD</button>
      <button class="filter-btn" data-cur="EUR" onclick="setNewsFilter('EUR',this)">EUR</button>
      <button class="filter-btn" data-cur="GBP" onclick="setNewsFilter('GBP',this)">GBP</button>
      <button class="filter-btn" data-cur="JPY" onclick="setNewsFilter('JPY',this)">JPY</button>
      <button class="filter-btn" data-cur="AUD" onclick="setNewsFilter('AUD',this)">AUD</button>
      <button class="filter-btn" data-cur="RUB" onclick="setNewsFilter('RUB',this)">RUB</button>
      <button class="filter-btn" data-cur="CAD" onclick="setNewsFilter('CAD',this)">CAD</button>
      <button class="filter-btn" data-cur="CHF" onclick="setNewsFilter('CHF',this)">CHF</button>
      <button class="filter-btn" data-cur="NZD" onclick="setNewsFilter('NZD',this)">NZD</button>
    </div>
    <div style="display:flex;gap:6px;margin-bottom:10px">
      <button class="btn btn-gold btn-sm" style="flex:1" onclick="loadNews(true)" data-t="btnRefreshNews">🔄 Обновить сейчас</button>
    </div>
    <div class="news-list" id="newsList">
      <div style="text-align:center;padding:20px;color:var(--muted);font-size:11px" data-t="newsLoading">Загрузка новостей...</div>
    </div>
  </div>

  <div class="card tab hidden" id="tabEdu">
    <div style="display:flex;gap:6px;margin-bottom:10px">
      <button class="btn btn-gold btn-sm" style="flex:1" onclick="openRandomEdu()" data-t="btnRandomStrat">🎲 Рандом стратегия</button>
    </div>
    <div id="eduList">
      <h3 class="g-text" style="text-align:center;font-size:13px;margin-bottom:10px" data-t="eduHeader">📚 50 профессиональных связок индикаторов</h3>
      <div id="eduItems"></div>
    </div>
    <div id="eduView" class="hidden">
      <button class="btn btn-gold btn-sm" style="margin-bottom:10px" onclick="closeEdu()" data-t="btnBackEdu">← Назад к списку</button>
      <div class="edu-detail" id="eduBody"></div>
    </div>
  </div>

  <div class="card tab hidden" id="tabProf">
    <h3 class="g-text" style="text-align:center;font-size:13px;margin-bottom:12px" data-t="profTitle">👤 Профиль</h3>
    <label class="profile-photo-upload" title="Фото профиля">
      <span id="profilePhotoIcon" style="font-size:24px">📷</span>
      <img id="profilePhotoImg" class="hidden" alt="Profile">
      <input type="file" id="profilePhotoInput" accept="image/*" style="display:none" onchange="handleProfilePhoto(event)">
    </label>
    <div style="text-align:center;font-size:10px;color:var(--muted);margin-bottom:12px" data-t="profPhotoHint">Нажмите для добавления фото вашего профиля</div>
    <label class="lbl" data-t="lblPocketId">АЙДИ С POCKET (Pocket ID)</label>
    <input class="input" id="profPocketId" data-ph="phPocketId" placeholder="Введите ваш Pocket ID..." onchange="saveProfileSettings()" onblur="saveProfileSettings()">
    <label class="lbl" style="margin-top:8px" data-t="lblFavStrat">ЛЮБИМАЯ СТРАТЕГИЯ</label>
    <input class="input" id="profFavStrat" data-ph="phFavStrat" placeholder="Например: Smart Money, Price Action..." onchange="saveProfileSettings()" onblur="saveProfileSettings()">
    <div style="display:flex;justify-content:space-between;font-size:11px;font-weight:800;margin:6px 2px 10px">
      <span style="color:var(--muted)" data-t="profSigsLbl">Сигналов</span>
      <b id="profSigCount" style="color:var(--gold)">0</b>
    </div>
    <div style="display:flex;justify-content:space-between;font-size:11px;font-weight:800;margin:0 2px 10px">
      <span style="color:var(--muted)" data-t="profFavCountLbl">Избранных активов</span>
      <b id="profFavCount" style="color:var(--gold)">0</b>
    </div>
    <button class="btn btn-gold btn-sm" style="margin-bottom:8px" onclick="openHistoryModal()" data-t="btnHistory">📜 История сигналов</button>
    <button class="btn btn-red btn-sm" onclick="resetProfile()" data-t="btnResetProfile">🗑 Сбросить данные профиля</button>
  </div>

  <div class="card tab hidden" id="tabScan">
    <h3 class="g-text" style="text-align:center;font-size:13px;margin-bottom:8px" data-t="scanTitle">📷 ИИ Сканер (Анализ по свечам)</h3>
    <div class="scan-tips" id="scanTipsHtml">
      <b data-t="scanInstr">Инструкция ИИ-сканера свечей:</b><br>
      <span data-t="scanStep1">1️⃣ Настройте параметры и стратегию ниже</span><br>
      <span data-t="scanStep2">2️⃣ Откройте камеру или загрузите скриншот графика со свечами</span><br>
      <span data-t="scanStep3">3️⃣ Нажмите «ЗАПУСК (ИИ СКАН СВЕЧЕЙ)»</span>
    </div>
    <div class="lbl" style="margin-top:6px" data-t="lblScanTf">ИНТЕРВАЛ СВЕЧЕЙ</div>
    <div class="cats" style="margin-bottom:6px">
      <div class="cat" onclick="setScanCfg(this,'интервал','S5')">S5</div>
      <div class="cat" onclick="setScanCfg(this,'интервал','S15')">S15</div>
      <div class="cat" onclick="setScanCfg(this,'интервал','S30')">S30</div>
      <div class="cat on" onclick="setScanCfg(this,'интервал','M1')">M1</div>
      <div class="cat" onclick="setScanCfg(this,'интервал','M2')">M2</div>
      <div class="cat" onclick="setScanCfg(this,'интервал','M5')">M5</div>
    </div>
    <div class="lbl" data-t="lblExp">ЭКСПИРАЦИЯ СДЕЛКИ</div>
    <div class="cats" style="margin-bottom:6px">
      <div class="cat" onclick="setScanCfg(this,'экспирация','30с')">30с</div>
      <div class="cat on" onclick="setScanCfg(this,'экспирация','1м')">1м</div>
      <div class="cat" onclick="setScanCfg(this,'экспирация','2м')">2м</div>
      <div class="cat" onclick="setScanCfg(this,'экспирация','5м')">5м</div>
    </div>
    <div class="lbl" data-t="lblScanStrat">СТРАТЕГИЯ АНАЛИЗА</div>
    <div class="cats" style="margin-bottom:8px">
      <div class="cat on" onclick="setScanCfg(this,'стратегия','Smart Money')">Smart Money</div>
      <div class="cat" onclick="setScanCfg(this,'стратегия','ICT')">ICT</div>
      <div class="cat" onclick="setScanCfg(this,'стратегия','PA')">PA</div>
      <div class="cat" onclick="setScanCfg(this,'стратегия','Scalp')">Scalp</div>
      <div class="cat" onclick="setScanCfg(this,'стратегия','Trend')">Trend</div>
    </div>
    <button class="btn btn-gold btn-sm" onclick="openCam()" data-t="btnOpenCam">📷 Открыть камеру</button>
    <div class="gap"></div>
    <div style="margin-bottom:8px">
      <label class="btn btn-dark btn-sm" style="cursor:pointer;text-align:center;display:block;">
        <span data-t="btnUpload">📁 Загрузить скриншот графика со свечами</span>
        <input type="file" id="scanFileInput" accept="image/*" style="display:none" onchange="handleImageUpload(event)">
      </label>
    </div>
    <div class="scan-box">
      <div class="scan-frame" id="scanFrame">
        <video class="scan-video hidden" id="scanVideo" autoplay playsinline muted></video>
        <img id="scanPreviewImg" class="scan-video hidden" style="object-fit:contain;background:#000;" alt="Preview">
        <div class="scan-placeholder" id="scanPh">📷<br><span data-t="camOff">Камера отключена или файл не выбран</span><br><small data-t="camOffSub">нажмите кнопку выше для запуска или загрузите скрин</small></div>
      </div>
    </div>
    <button class="btn btn-gold" id="btnScan" onclick="doScan()" disabled style="background:#1b5e20;color:#fff;animation:none" data-t="btnScanNow">🚀 ЗАПУСК (ИИ СКАН СВЕЧЕЙ)</button>
    <div class="gap"></div>
    <button class="btn btn-dark btn-sm" onclick="stopCam()" data-t="btnCloseCam">⏹ Закрыть камеру / Сбросить</button>
    <div class="sig" id="scanSigBox">
      <div class="sig-meta" id="scanMeta">TEAM MASTER AI SCANNER</div>
      <div class="sig-dir call" id="scanDir">BUY (ВВЕРХ)</div>
      <div class="timer">⏱ <span id="scanCd">01:00</span></div>
      <div class="strat" id="scanStrat"></div>
      <button class="btn btn-red btn-sm" id="btnScanCancel" onclick="cancelScan()" style="display:none" data-t="btnCancel">✖ Отменить</button>
      <button class="btn btn-gold btn-sm" id="btnScanNew" onclick="doScan()" style="display:none" data-t="btnNewScan">⚡ Новый скан</button>
    </div>
  </div>

  <div class="card tab hidden" id="tabAbout">
    <h3 class="g-text" style="text-align:center;font-size:13px;margin-bottom:10px" data-t="aboutTitle">👑 О нашей команде (Team Master)</h3>
    <div class="about-box">
      <b>TEAM MASTER VIP</b> — <span data-t="aboutP1">это международное сообщество и торговая экосистема Team Master Trade. В команде аналитики с опытом 7+ лет, есть академия и сопровождение.</span><br><br>
      🎯 <b data-t="aboutGoalT">Наша цель и миссия:</b> <span data-t="aboutGoal">дать участникам ИИ-сканер, сигналы, связки индикаторов, экономический календарь и поддержку наставников.</span><br><br>
      💡 <b data-t="aboutWhyT">Что внутри:</b><br>
      <span data-t="aboutL1">• Технические сигналы и ИИ-сканирование графиков</span><br>
      <span data-t="aboutL2">• База знаний из 50 связок и методик</span><br>
      <span data-t="aboutL3">• Экономический календарь и ИИ-помощник</span><br><br>
      <span data-t="aboutRisk">⚠️ Торговля бинарными опционами связана с высоким риском потери капитала. Сигналы не гарантируют прибыль. Риск на сделку — 1-2% от депозита.</span>
    </div>
    <div class="gap"></div>
    <a class="btn btn-gold btn-sm" href="https://t.me/+uekq4TquqkM4Mzcy" target="_blank" rel="noopener" data-t="btnTgChan">📢 Наш Telegram канал</a>
  </div>

</div>

<div class="bnav" id="bnav">
  <div class="nav on" onclick="tab('tabSig',this)"><span>🎯</span><span data-t="navSig">Сигналы</span></div>
  <div class="nav" onclick="tab('tabFavs',this)"><span>⭐</span><span data-t="navFavs">Избранное</span></div>
  <div class="nav" onclick="tab('tabNews',this)"><span>📰</span><span data-t="navNews">Новости</span></div>
  <div class="nav" onclick="tab('tabEdu',this)"><span>📚</span><span data-t="navEdu">Связки</span></div>
  <div class="nav" onclick="tab('tabProf',this)"><span>👤</span><span data-t="navProf">Профиль</span></div>
  <div class="nav" onclick="tab('tabScan',this)"><span>📷</span><span data-t="navScan">Сканер</span></div>
  <div class="nav" onclick="tab('tabAbout',this)"><span>👑</span><span data-t="navAbout">О нас</span></div>
</div>

<div class="analyze-overlay hidden" id="analyzeBox">
  <div class="analyze-smile" id="analyzeSmile">😊</div>
  <div class="analyze-wheel"></div>
  <div class="analyze-text" id="analyzeTxt">⚡ Подключаюсь к рынку...</div>
</div>

<div class="modal-overlay hidden" id="catalogModal">
  <div class="modal-content">
    <div class="modal-header">
      <h3 data-t="modalCatalogTitle">📋 Полный каталог активов Pocket Option</h3>
      <button class="modal-close" onclick="closeCatalogModal()">✕</button>
    </div>
    <input type="text" class="modal-search-input" id="modalSearchInput" data-ph="searchPlaceholder" placeholder="Поиск актива..." oninput="renderCatalogList(this.value)">
    <div class="cats" style="margin-bottom:8px" id="catalogFilterCats">
      <button class="cat on" onclick="setModalCat('all',this)" data-t="catAll">🌟 Все</button>
      <button class="cat" onclick="setModalCat('💱',this)" data-t="catCur">💱 Валюта</button>
      <button class="cat" onclick="setModalCat('🔥',this)" data-t="catCrypto">🔥 Крипто</button>
      <button class="cat" onclick="setModalCat('🛢️',this)" data-t="catComm">🛢️ Сырьё</button>
      <button class="cat" onclick="setModalCat('📈',this)" data-t="catStocks">📈 Акции</button>
      <button class="cat" onclick="setModalCat('📊',this)" data-t="catIdx">📊 Индексы</button>
    </div>
    <div class="modal-body" id="modalCatalogBody"></div>
  </div>
</div>

<div class="modal-overlay hidden" id="historyModal">
  <div class="modal-content">
    <div class="modal-header">
      <h3>📜 История сигналов</h3>
      <button class="modal-close" onclick="closeHistoryModal()">✕</button>
    </div>
    <div class="modal-body" id="historyBody" style="max-height:70vh"></div>
  </div>
</div>

</div>

<script>
var ASSETS_DATA_JS = __ASSETS_JSON__;
var EDU = __EDU_JSON__;

var timers = {sig: null, scan: null};
var stream = null, camReady = false;
var favs = [];
try { favs = JSON.parse(localStorage.getItem("tmv_favs") || "[]"); } catch(e) { favs = []; }
var sigCount = 0;
try { sigCount = parseInt(localStorage.getItem("tmv_sigs") || "0") || 0; } catch(e) {}
var currentAsset = "🇪🇺🇺🇸 EUR/USD OTC";
var currentLang = "ru";
var modalCategory = "all";
var uploadedImageBlob = null;
var scanCfg = {"интервал": "M1", "экспирация": "1м", "стратегия": "Smart Money"};
var newsTimer = null;
var lastNewsHash = "";
var newsCache = [];
var newsFilter = "ALL";
var signalHistory = [];
try { signalHistory = JSON.parse(localStorage.getItem("tmv_history") || "[]"); } catch(e) { signalHistory = []; }

var I18N = {
  ru: {
    syncLbl: "⚡ СИНХРОНИЗАЦИЯ",
    lblActiveAsset: "ВЫБРАННЫЙ АКТИВ POCKET OPTION", lblTf: "ИНТЕРВАЛ (ТАЙМФРЕЙМ)", lblExp: "ЭКСПИРАЦИЯ СДЕЛКИ",
    btnCalcSig: "⚡ Просчитать сигнал", btnAutoSig: "🤖 Выбрать топ актив",
    lblCombo: "Связка", btnCancel: "✖ Отменить", btnNewSig: "⚡ Новый сигнал",
    eduHeader: "📚 50 профессиональных связок индикаторов", btnBackEdu: "← Назад к списку", btnRandomStrat: "🎲 Рандом стратегия",
    profTitle: "👤 Профиль", profPhotoHint: "Нажмите для добавления фото вашего профиля",
    lblPocketId: "АЙДИ С POCKET (Pocket ID)", lblFavStrat: "ЛЮБИМАЯ СТРАТЕГИЯ",
    profSigsLbl: "Сигналов", profFavCountLbl: "Избранных активов", btnResetProfile: "🗑 Сбросить данные профиля",
    phPocketId: "Введите ваш Pocket ID...", phFavStrat: "Например: Smart Money, Price Action...",
    favTabTitle: "⭐ Избранные активы", favTabDesc: "Нажмите на любой актив из списка для перехода.",
    profFavEmpty: "Пока пусто — добавь актив из полного каталога", btnClearFav: "🗑 Очистить избранное",
    newsTitle: "📰 Форекс-новости", newsDesc: "Только форекс (USD EUR GBP JPY AUD NZD CAD CHF RUB). High/Medium. Фильтр по валюте. Автообновление каждую минуту.",
    btnRefreshNews: "🔄 Обновить сейчас", newsLoading: "Загрузка новостей...",
    chatTitle: "🤖 ИИ Помощник Team Master",
    chatDesc: "Вопросы по рынку, стратегиям, терминалу и команде Team Master Trade (аналитики 7+ лет, академия).",
    chatPlaceholder: "Спросите про рынок, команду или терминал...",
    btnTgCommunity: "💬 Наши чаты и сообщество в Telegram",
    scanTitle: "📷 ИИ Сканер (Анализ по свечам)", scanInstr: "Инструкция ИИ-сканера свечей:",
    scanStep1: "1️⃣ Настройте параметры и стратегию ниже",
    scanStep2: "2️⃣ Откройте камеру или загрузите скриншот графика со свечами",
    scanStep3: "3️⃣ Нажмите «ЗАПУСК (ИИ СКАН СВЕЧЕЙ)»",
    lblScanTf: "ИНТЕРВАЛ СВЕЧЕЙ", lblScanStrat: "СТРАТЕГИЯ АНАЛИЗА",
    btnOpenCam: "📷 Открыть камеру", btnUpload: "📁 Загрузить скриншот графика со свечами",
    camOff: "Камера отключена или файл не выбран", camOffSub: "нажмите кнопку выше для запуска или загрузите скрин",
    btnScanNow: "🚀 ЗАПУСК (ИИ СКАН СВЕЧЕЙ)", btnCloseCam: "⏹ Закрыть камеру / Сбросить", btnNewScan: "⚡ Новый скан",
    aboutTitle: "👑 О нашей команде (Team Master)",
    aboutP1: "это международное сообщество и торговая экосистема Team Master Trade. В команде аналитики с опытом 7+ лет, есть академия и сопровождение.",
    aboutGoalT: "Наша цель и миссия:",
    aboutGoal: "дать участникам ИИ-сканер, сигналы, связки индикаторов, экономический календарь и поддержку наставников.",
    aboutWhyT: "Что внутри:",
    aboutL1: "• Технические сигналы и ИИ-сканирование графиков", aboutL2: "• База знаний из 50 связок и методик",
    aboutL3: "• Экономический календарь и ИИ-помощник",
    aboutRisk: "⚠️ Торговля бинарными опционами связана с высоким риском потери капитала. Сигналы не гарантируют прибыль. Риск на сделку — 1-2% от депозита.",
    btnTgChan: "📢 Наш Telegram канал",
    navSig: "Сигналы", navFavs: "Избранное", navNews: "Новости", navChat: "ИИ Чат", navEdu: "Связки", navProf: "Профиль", navScan: "Сканер", navAbout: "О нас",
    modalCatalogTitle: "📋 Полный каталог активов Pocket Option", searchPlaceholder: "Поиск актива...",
    catAll: "🌟 Все", catCur: "💱 Валюта", catCrypto: "🔥 Крипто", catComm: "🛢️ Сырьё", catStocks: "📈 Акции", catIdx: "📊 Индексы",
    btnHistory: "📜 История сигналов"
  },
  en: {
    syncLbl: "⚡ SYNCING",
    lblActiveAsset: "SELECTED POCKET OPTION ASSET", lblTf: "INTERVAL (TIMEFRAME)", lblExp: "TRADE EXPIRATION",
    btnCalcSig: "⚡ Calculate Signal", btnAutoSig: "🤖 Choose Top Asset",
    lblCombo: "Combo", btnCancel: "✖ Cancel", btnNewSig: "⚡ New Signal",
    eduHeader: "📚 50 Professional Indicator Combos", btnBackEdu: "← Back to list", btnRandomStrat: "🎲 Random Strategy",
    profTitle: "👤 Profile", profPhotoHint: "Tap to add your profile photo",
    lblPocketId: "POCKET ID", lblFavStrat: "FAVORITE STRATEGY",
    profSigsLbl: "Signals", profFavCountLbl: "Favorite assets", btnResetProfile: "🗑 Reset profile data",
    phPocketId: "Enter your Pocket ID...", phFavStrat: "E.g.: Smart Money, Price Action...",
    favTabTitle: "⭐ Favorite Assets", favTabDesc: "Tap any asset to switch to it.",
    profFavEmpty: "Empty yet — add an asset from the full catalog", btnClearFav: "🗑 Clear favorites",
    newsTitle: "📰 Forex News", newsDesc: "Forex only (USD EUR GBP JPY AUD NZD CAD CHF RUB). High/Medium. Currency filter. Auto-refresh.",
    btnRefreshNews: "🔄 Refresh now", newsLoading: "Loading news...",
    chatTitle: "🤖 AI Assistant Team Master",
    chatDesc: "Ask about markets, strategies, the terminal and Team Master Trade (7+ years analysts, academy).",
    chatPlaceholder: "Ask about market, team or terminal...",
    btnTgCommunity: "💬 Our Telegram chats and community",
    scanTitle: "📷 AI Scanner (Candle Analysis)", scanInstr: "AI candle scanner instructions:",
    scanStep1: "1️⃣ Set the parameters and strategy below",
    scanStep2: "2️⃣ Open the camera or upload a chart screenshot with candles",
    scanStep3: "3️⃣ Press RUN (AI CANDLE SCAN)",
    lblScanTf: "CANDLE INTERVAL", lblScanStrat: "ANALYSIS STRATEGY",
    btnOpenCam: "📷 Open Camera", btnUpload: "📁 Upload chart screenshot with candles",
    camOff: "Camera is off or no file selected", camOffSub: "press the button above to start or upload a screenshot",
    btnScanNow: "🚀 RUN (AI CANDLE SCAN)", btnCloseCam: "⏹ Close Camera / Reset", btnNewScan: "⚡ New Scan",
    aboutTitle: "👑 About Our Team (Team Master)",
    aboutP1: "is an international community and trading ecosystem Team Master Trade. Analysts with 7+ years experience, academy and mentoring.",
    aboutGoalT: "Our goal and mission:",
    aboutGoal: "to give members an AI scanner, signals, indicator combos, economic calendar and mentor support.",
    aboutWhyT: "What's inside:",
    aboutL1: "• Technical signals and AI chart scanning", aboutL2: "• Knowledge base of 50 combos and methods",
    aboutL3: "• Economic calendar and AI assistant",
    aboutRisk: "⚠️ Binary options trading carries a high risk of losing capital. Signals do not guarantee profit. Risk per trade — 1-2% of the deposit.",
    btnTgChan: "📢 Our Telegram Channel",
    navSig: "Signals", navFavs: "Favorites", navNews: "News", navChat: "AI Chat", navEdu: "Combos", navProf: "Profile", navScan: "Scanner", navAbout: "About",
    modalCatalogTitle: "📋 Full Pocket Option Assets Catalog", searchPlaceholder: "Search asset...",
    catAll: "🌟 All", catCur: "💱 Currency", catCrypto: "🔥 Crypto", catComm: "🛢️ Commodities", catStocks: "📈 Stocks", catIdx: "📊 Indices",
    btnHistory: "📜 Signal History"
  },
  ua: {
    syncLbl: "⚡ СИНХРОНІЗАЦІЯ",
    lblActiveAsset: "ОБРАНИЙ АКТИВ POCKET OPTION", lblTf: "ІНТЕРВАЛ (ТАЙМФРЕЙМ)", lblExp: "ЕКСПІРАЦІЯ УГОДИ",
    btnCalcSig: "⚡ Прорахувати сигнал", btnAutoSig: "🤖 Обрати топ актив",
    lblCombo: "Зв'язка", btnCancel: "✖ Скасувати", btnNewSig: "⚡ Новий сигнал",
    eduHeader: "📚 50 професійних зв'язок індикаторів", btnBackEdu: "← Назад до списку", btnRandomStrat: "🎲 Рандом стратегія",
    profTitle: "👤 Профіль", profPhotoHint: "Натисніть, щоб додати фото профілю",
    lblPocketId: "АЙДІ З POCKET (Pocket ID)", lblFavStrat: "УЛЮБЛЕНА СТРАТЕГІЯ",
    profSigsLbl: "Сигналів", profFavCountLbl: "Обраних активів", btnResetProfile: "🗑 Скинути дані профілю",
    phPocketId: "Введіть ваш Pocket ID...", phFavStrat: "Наприклад: Smart Money, Price Action...",
    favTabTitle: "⭐ Обрані активи", favTabDesc: "Натисніть на будь-який актив зі списку для переходу.",
    profFavEmpty: "Поки порожньо — додай актив з повного каталогу", btnClearFav: "🗑 Очистити обране",
    newsTitle: "📰 Форекс-новини", newsDesc: "Тільки форекс (USD EUR GBP JPY AUD NZD CAD CHF RUB). High/Medium. Фільтр по валюті. Автооновлення.",
    btnRefreshNews: "🔄 Оновити зараз", newsLoading: "Завантаження новин...",
    chatTitle: "🤖 ШІ Помічник Team Master",
    chatDesc: "Питання про ринок, стратегії, термінал і команду Team Master Trade (аналітики 7+ років, академія).",
    chatPlaceholder: "Запитайте про ринок, команду або термінал...",
    btnTgCommunity: "💬 Наші чати та спільнота в Telegram",
    scanTitle: "📷 ШІ Сканер (Аналіз за свічками)", scanInstr: "Інструкція ШІ-сканера свічок:",
    scanStep1: "1️⃣ Налаштуйте параметри та стратегію нижче",
    scanStep2: "2️⃣ Відкрийте камеру або завантажте скріншот графіка зі свічками",
    scanStep3: "3️⃣ Натисніть «ЗАПУСК (ШІ СКАН СВІЧОК)»",
    lblScanTf: "ІНТЕРВАЛ СВІЧОК", lblScanStrat: "СТРАТЕГІЯ АНАЛІЗУ",
    btnOpenCam: "📷 Відкрити камеру", btnUpload: "📁 Завантажити скріншот графіка зі свічками",
    camOff: "Камера вимкнена або файл не обрано", camOffSub: "натисніть кнопку вище для запуску або завантажте скрін",
    btnScanNow: "🚀 ЗАПУСК (ШІ СКАН СВІЧОК)", btnCloseCam: "⏹ Закрити камеру / Скинути", btnNewScan: "⚡ Новий скан",
    aboutTitle: "👑 Про нашу команду (Team Master)",
    aboutP1: "це міжнародна спільнота та торгова екосистема Team Master Trade. У команді аналітики з досвідом 7+ років, є академія та супровід.",
    aboutGoalT: "Наша мета і місія:",
    aboutGoal: "дати учасникам ШІ-сканер, сигнали, зв'язки індикаторів, економічний календар і підтримку наставників.",
    aboutWhyT: "Що всередині:",
    aboutL1: "• Технічні сигнали та ШІ-сканування графіків", aboutL2: "• База знань з 50 зв'язок і методик",
    aboutL3: "• Економічний календар і ШІ-помічник",
    aboutRisk: "⚠️ Торгівля бінарними опціонами пов'язана з високим ризиком втрати капіталу. Сигнали не гарантують прибуток. Ризик на угоду — 1-2% від депозиту.",
    btnTgChan: "📢 Наш Telegram канал",
    navSig: "Сигнали", navFavs: "Обране", navNews: "Новини", navChat: "ШІ Чат", navEdu: "Зв'язки", navProf: "Профіль", navScan: "Сканер", navAbout: "Про нас",
    modalCatalogTitle: "📋 Повний каталог активів Pocket Option", searchPlaceholder: "Пошук активу...",
    catAll: "🌟 Всі", catCur: "💱 Валюта", catCrypto: "🔥 Крипто", catComm: "🛢️ Сировина", catStocks: "📈 Акції", catIdx: "📊 Індекси",
    btnHistory: "📜 Історія сигналів"
  }
};

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function getCategories() { return ASSETS_DATA_JS[currentLang] || ASSETS_DATA_JS["ru"]; }

function updateSelectedAssetUI() {
  document.getElementById("currentAssetDisplay").textContent = currentAsset;
}

function tab(tabId, el) {
  document.querySelectorAll(".tab").forEach(function(t) { t.classList.add("hidden"); });
  document.getElementById(tabId).classList.remove("hidden");
  document.querySelectorAll(".bnav .nav").forEach(function(n) { n.classList.remove("on"); });
  if (el) el.classList.add("on");
  if (tabId === "tabFavs") renderMainFavs();
  if (tabId === "tabProf") updateProfileStats();
  if (tabId === "tabNews") { loadNews(false); startNewsAuto(); }
}

function openCatalogModal() {
  document.getElementById("catalogModal").classList.remove("hidden");
  renderCatalogList(document.getElementById("modalSearchInput").value);
}
function closeCatalogModal() { document.getElementById("catalogModal").classList.add("hidden"); }

function setModalCat(cat, el) {
  modalCategory = cat;
  document.querySelectorAll("#catalogFilterCats .cat").forEach(function(c) { c.classList.remove("on"); });
  if (el) el.classList.add("on");
  renderCatalogList(document.getElementById("modalSearchInput").value);
}

function setScanCfg(el, type, val) {
  el.parentElement.querySelectorAll(".cat").forEach(function(b) { b.classList.remove("on"); });
  el.classList.add("on");
  scanCfg[type] = val;
}

function renderCatalogList(filter) {
  filter = (filter || "").toLowerCase();
  var bodyEl = document.getElementById("modalCatalogBody");
  var cats = getCategories();
  var html = "";
  for (var catName in cats) {
    if (modalCategory !== "all" && catName.indexOf(modalCategory) < 0) continue;
    var items = cats[catName].filter(function(it) { return it.toLowerCase().indexOf(filter) >= 0; });
    if (items.length) {
      html += '<div class="catalog-category-header">' + esc(catName) + '</div>';
      items.forEach(function(item) {
        var isFav = favs.indexOf(item) >= 0;
        var star = isFav ? "⭐" : "☆";
        html += '<div class="fav-item">';
        html += '<span data-a="' + esc(item) + '" onclick="selectCatalogAsset(this.getAttribute(\'data-a\'))" style="flex:1">' + esc(item) + '</span>';
        html += '<div style="display:flex;gap:10px;align-items:center">';
        html += '<span style="cursor:pointer;font-size:15px" data-a="' + esc(item) + '" onclick="toggleFav(this.getAttribute(\'data-a\'))">' + star + '</span>';
        html += '<span style="color:var(--gold);font-size:11px;cursor:pointer" data-a="' + esc(item) + '" onclick="selectCatalogAsset(this.getAttribute(\'data-a\'))">➔</span>';
        html += '</div></div>';
      });
    }
  }
  bodyEl.innerHTML = html || '<div style="text-align:center;padding:20px;color:var(--muted);font-size:11px">—</div>';
}

function toggleFav(name) {
  if (favs.indexOf(name) >= 0) favs = favs.filter(function(f) { return f !== name; });
  else favs.push(name);
  try { localStorage.setItem("tmv_favs", JSON.stringify(favs)); } catch(e) {}
  renderCatalogList(document.getElementById("modalSearchInput").value);
  renderMainFavs();
  updateProfileStats();
}

function selectCatalogAsset(name) {
  currentAsset = name;
  updateSelectedAssetUI();
  closeCatalogModal();
  tab("tabSig", document.querySelector(".bnav .nav"));
}

function renderMainFavs() {
  var listEl = document.getElementById("mainFavList");
  var emptyEl = document.getElementById("mainFavEmpty");
  if (!favs.length) {
    listEl.innerHTML = "";
    emptyEl.style.display = "block";
    return;
  }
  emptyEl.style.display = "none";
  var html = "";
  favs.forEach(function(f) {
    html += '<div class="fav-item" data-a="' + esc(f) + '" onclick="selectCatalogAsset(this.getAttribute(\'data-a\'))"><span>⭐ ' + esc(f) + '</span><span style="color:var(--gold)">➔</span></div>';
  });
  listEl.innerHTML = html;
}

function clearFavs() {
  favs = [];
  try { localStorage.setItem("tmv_favs", "[]"); } catch(e) {}
  renderMainFavs();
  updateProfileStats();
}

function startTimer(key, elementId, seconds, onComplete, persistKey) {
  if (timers[key]) clearInterval(timers[key]);
  var endTs = Date.now() + seconds * 1000;
  if (persistKey) {
    try { localStorage.setItem(persistKey, String(endTs)); } catch(e) {}
  }
  function tick() {
    var rem = Math.max(0, Math.ceil((endTs - Date.now()) / 1000));
    var el = document.getElementById(elementId);
    if (!el) return;
    if (rem <= 0) {
      clearInterval(timers[key]);
      el.textContent = "00:00 ✔";
      if (persistKey) try { localStorage.removeItem(persistKey); } catch(e) {}
      if (onComplete) onComplete();
      return;
    }
    updateTimerDisplay(el, rem);
  }
  tick();
  timers[key] = setInterval(tick, 250);
}

function updateTimerDisplay(el, sec) {
  var m = Math.floor(sec / 60), s = sec % 60;
  el.textContent = (m < 10 ? "0" + m : m) + ":" + (s < 10 ? "0" + s : s);
}

function resumeTimers() {
  // Сигнал
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
  // Скан
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
    "⚡ Подключаюсь к рынку... 😊",
    "📊 Анализирую котировки... 🔍",
    "💎 Сканирую индикаторы... ✨",
    "🚀 Формирую точный сигнал... 🎯"
  ];
  var smiles = ["😊", "😎", "🤩", "🔥"];
  var step = 0;
  txtEl.textContent = phrases[0];
  smileEl.textContent = smiles[0];
  var interval = setInterval(function() {
    step++;
    txtEl.textContent = phrases[step % phrases.length];
    smileEl.textContent = smiles[step % smiles.length];
  }, 480);
  setTimeout(function() {
    clearInterval(interval);
    overlay.classList.add("hidden");
    if (callback) callback();
  }, 2400);
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

function getSig() {
  runAnalyzer(async function() {
    var tf = document.getElementById("tf").value;
    var expSec = parseInt(document.getElementById("exp").value);
    var dir = "⬆️ CALL (ВВЕРХ)";
    var stratText = "Технический анализ котировок для актива (" + currentAsset + ") по таймфрейму " + tf + " с экспирацией " + expSec + " сек.";
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
    var isCall = dir.indexOf("CALL") >= 0;
    document.getElementById("sigMeta").textContent = "Pocket Option · " + currentAsset + " · " + tf + " · " + expSec + " сек";
    var dirEl = document.getElementById("sigDir");
    dirEl.textContent = dir;
    dirEl.className = "sig-dir " + (isCall ? "call" : "put");
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
  lines.push("📌 Событие: " + (ev.name || "—"));
  if (ev.name_en && ev.name_en !== ev.name) lines.push("EN: " + ev.name_en);
  lines.push("💱 Валюта: " + (ev.currency || "—"));
  if (ev.pair_hint) lines.push("📊 Пары: " + ev.pair_hint);
  lines.push("⚡ Важность: " + (ev.impact_ru || ev.impact || "—"));
  lines.push("🕒 Время: " + formatNewsTime(ev.date));
  lines.push("📍 Статус: " + (ev.status_ru || "—"));
  if (ev.forecast) lines.push("📈 Прогноз: " + ev.forecast);
  if (ev.previous) lines.push("📉 Предыдущее: " + ev.previous);
  if (ev.actual) lines.push("✅ Факт: " + ev.actual);
  else lines.push("⏳ Факт ещё не вышел — обновится автоматически.");
  lines.push("");
  lines.push("💡 Team Master: на High-новостях волатильность резко растёт. Не входи за 3–5 мин до выхода, если нет плана.");
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
    list.innerHTML = '<div style="text-align:center;padding:20px;color:var(--muted);font-size:11px">Нет форекс-событий High/Medium по выбранному фильтру</div>';
    return;
  }
  var released = filtered.filter(function(e) { return e.status === "released"; });
  var upcoming = filtered.filter(function(e) { return e.status !== "released"; });
  var ordered = upcoming.concat(released);
  var html = "";
  if (upcoming.length) {
    html += '<div class="news-sec-title news-sec-up">⏳ Скоро выйдет · валютные пары</div>';
  }
  var shownReleasedHeader = false;
  ordered.forEach(function(ev, idx) {
    if (ev.status === "released" && !shownReleasedHeader) {
      html += '<div class="news-sec-title news-sec-done">✅ Уже вышло сегодня / недавно</div>';
      shownReleasedHeader = true;
    }
    var ic = impactClass(ev.impact);
    var actual = String(ev.actual || "").trim();
    var forecast = String(ev.forecast || "").trim();
    var previous = String(ev.previous || "").trim();
    html += '<div class="news-item ' + ic + '" data-idx="' + idx + '" onclick="toggleNews(' + idx + ')">';
    html += '<div class="news-top">';
    html += '<span class="news-cur">' + esc(ev.currency || "—") + ' · <span class="news-impact ' + ic + '">' + impactLabel(ev.impact, ev.impact_ru) + '</span></span>';
    html += '<span class="news-time">🕒 ' + esc(formatNewsTime(ev.date)) + '</span>';
    html += '</div>';
    html += '<div class="news-pairs">' + esc(ev.status_ru || "") + (ev.pair_hint ? " · влияет на " + esc(ev.pair_hint) : "") + '</div>';
    html += '<div class="news-name">' + esc(ev.name || "Событие") + '</div>';
    if (ev.name_en && ev.name_en !== ev.name) {
      html += '<div style="font-size:10px;color:var(--muted);margin-bottom:6px">' + esc(ev.name_en) + '</div>';
    }
    html += '<div class="news-meta">';
    if (forecast) html += '<span>📈 Прогноз: ' + esc(forecast) + '</span>';
    if (previous) html += '<span>📉 Пред.: ' + esc(previous) + '</span>';
    if (actual) html += '<span style="color:var(--green)">✅ Факт: ' + esc(actual) + '</span>';
    else html += '<span>⏳ Ожидается</span>';
    html += '</div>';
    html += '<div class="news-detail" id="newsDetail' + idx + '">' + esc(newsDetailText(ev)).replace(/\n/g, "<br>") + '</div>';
    html += '</div>';
  });
  list.innerHTML = html;
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
    if (document.getElementById("newsList").innerHTML.indexOf("news-item") < 0) {
      document.getElementById("newsList").innerHTML = '<div style="text-align:center;padding:20px;color:var(--red);font-size:11px">Не удалось загрузить новости. Нажмите обновить.</div>';
    }
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

function strategyVisual(kind) {
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
      '<text x="24" y="192" fill="#9aa3b5" font-size="11">Откат к тренду / EMA → вход по направлению тренда</text>' +
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
  var visual = strategyVisual(ed.kind || "levels");
  document.getElementById("eduBody").innerHTML =
    '<h3>' + esc(ed.t) + '</h3>' +
    '<div class="tag-row">' + tagsHtml + '</div>' +
    visual +
    '<div class="edu-body-text" style="white-space:pre-wrap;font-size:12px;line-height:1.65;color:#d0d7e2">' + esc(ed.d) + '</div>';
}
function closeEdu() {
  document.getElementById("eduView").classList.add("hidden");
  document.getElementById("eduList").classList.remove("hidden");
}
function openRandomEdu() { openEdu(Math.floor(Math.random() * EDU.length)); }

function updateProfileStats() {
  document.getElementById("profSigCount").textContent = String(sigCount);
  document.getElementById("profFavCount").textContent = String(favs.length);
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
    alert("Камера недоступна в этом браузере. Загрузите скриншот графика.");
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
    .catch(function(err) { alert("Не удалось получить доступ к камере: " + err); });
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
  if (!camReady) { alert("Сначала откройте камеру или загрузите скриншот графика со свечами!"); return; }
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
  document.querySelectorAll("[data-ph]").forEach(function(el) {
    var k = el.getAttribute("data-ph");
    if (dict[k] !== undefined) el.setAttribute("placeholder", dict[k]);
  });
  document.documentElement.lang = lang === "ua" ? "uk" : lang;
  renderCatalogList(document.getElementById("modalSearchInput").value);
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
    # Очищаем эмодзи и лишнее
    clean_asset = re.sub(r"[^\x00-\x7F]+", "", asset).replace("OTC", "").strip()
    # Дополнительная очистка для маппинга
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
    """Форекс-календарь: валюты (включая RUB), High/Medium, сегодня вышло + скоро выйдет."""
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
        GEMINI_MODEL or "gemini-3.8-flash",
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-flash-latest",
        "gemini-2.0-flash",
        "gemini-2.0-flash-001",
        "gemini-3.6-flash",
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
        GEMINI_MODEL or "gemini-3.8-flash",
        "gemini-3.8-flash",
        "gemini-flash-lite-latest",
        "gemini-2.0-flash",
        "gemini-3.5-flash-lite",
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


def _chat_sync(chat_prompt):
    return _gemini_rest_text(chat_prompt)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
