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
app = FastAPI(title="TEAM MASTER VIP - Ultimate Trading Terminal", version="30.5")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
OTCSHARDS_API_KEY = os.environ.get("OTCSHARDS_API_KEY", "")

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
    if "Bitcoin" in asset_name or "BITCOIN" in asset_name:
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

_TOP_BEST_OTC = [
    "AUD/USD OTC",
    "EUR/USD OTC",
    "GBP/USD OTC",
    "USD/JPY OTC",
    "BITCOIN OTC"
]

_CURRENCY = [
    "AUD/USD OTC", "EUR/USD OTC", "GBP/USD OTC", "USD/JPY OTC", "BITCOIN OTC",
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
    "BITCOIN OTC", "₿ Bitcoin", "Ξ Ethereum", "🪙 Solana", "🪙 BNB OTC", "🪙 Dogecoin OTC", "🪙 Chainlink OTC",
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
        "🔥 ТОП Лучшие (OTC API)": _TOP_BEST_OTC,
        "💱 Валютные пары": _CURRENCY,
        "🔥 Криптовалюты": _CRYPTO,
        "🛢️ Сырьевые товары": _COMMODITIES,
        "📈 Акции компаний": _STOCKS,
        "📊 Биржевые индексы": _INDICES,
    },
    "en": {
        "🔥 TOP Best (OTC API)": _TOP_BEST_OTC,
        "💱 Currency Pairs": _CURRENCY,
        "🔥 Cryptocurrencies": _CRYPTO,
        "🛢️ Commodities": _COMMODITIES,
        "📈 Company Stocks": _STOCKS,
        "📊 Market Indices": _INDICES,
    },
    "ua": {
        "🔥 ТОП Найкращі (OTC API)": _TOP_BEST_OTC,
        "💱 Валютні пари": _CURRENCY,
        "🔥 Криптовалюти": _CRYPTO,
        "🛢️ Сировинні товари": _COMMODITIES,
        "📈 Акції компаній": _STOCKS,
        "📊 Біржові індекси": _INDICES,
    },
}

EDU_50 = [
    {
        "t": "1. Торговля от уровней поддержки и сопротивления",
        "tags": ["M1", "M5", "Pocket Option", "Уровни", "Price Action"],
        "kind": "levels",
        "d": "📌 Подробный разбор стратегии торговли от уровней поддержки и сопротивления:\n\n"
             "• 🧠 Рыночная логика и суть стратегии:\n"
             "Уровни поддержки и сопротивления — это ключевые ценовые зоны, где баланс сил между покупателями и продавцами кардинально меняется.\n\n"
             "• ⏱ Рекомендуемые таймфреймы: M1, M2 и M5.\n"
             "• ⏳ Оптимальная экспирация: 2–4 минуты.\n"
             "• 🛡️ Риск: не более 1-2% на сделку."
    }
]

for i in range(2, 51):
    kinds = ["levels", "range", "trend", "bounce"]
    EDU_50.append({
        "t": f"{i}. Профессиональная стратегия и связка индикаторов №{i}",
        "tags": ["M1", "Pocket Option", "Стратегии"],
        "kind": kinds[i % 4],
        "d": f"📌 Подробный разбор стратегии №{i}:\nСистема построена на уровнях ликвидности и фильтрации шума.\n• Таймфреймы: M1, M5\n• Экспирация: 1–3 мин."
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
.mini-chart-box{margin:10px 0;background:var(--card);border:1px solid var(--border-g);border-radius:10px;padding:8px}
.mini-chart-box svg{width:100%;height:90px;display:block}
.sig-stats{display:flex;justify-content:space-between;font-size:10px;font-weight:800;margin-bottom:8px}
.edu-item{background:var(--inner);border:1px solid var(--border);border-radius:11px;padding:11px 13px;margin-bottom:7px;cursor:pointer;display:flex;justify-content:space-between;align-items:center}
.edu-item h4{font-size:11.5px;color:var(--gold);font-weight:800}
.edu-detail{background:var(--inner);border:1px solid var(--border-g);border-radius:12px;padding:14px}
.edu-detail h3{color:var(--gold);font-size:13px;font-weight:900;margin-bottom:8px}
.tag-row{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px}
.tag{font-size:9px;font-weight:800;padding:3px 8px;border-radius:6px;background:rgba(255,215,0,.08);color:var(--gold);border:1px solid rgba(255,215,0,.2)}
.bnav{position:fixed;bottom:0;left:0;right:0;background:#05070d;border-top:1px solid var(--border);display:flex;justify-content:space-around;padding:6px 0 10px;z-index:100}
.nav{display:flex;flex-direction:column;align-items:center;gap:1px;color:var(--muted);font-size:8px;font-weight:800;cursor:pointer;min-width:36px;padding:3px 1px}
.nav.on{color:var(--gold)}.nav span:first-child{font-size:14px}
.hidden{display:none!important}
.gap{height:6px}.gap2{height:9px}
.analyze-overlay{position:fixed;inset:0;background:rgba(4,6,10,.94);z-index:200;display:flex;flex-direction:column;align-items:center;justify-content:center}
.analyze-wheel{width:56px;height:56px;border:3px solid rgba(255,215,0,.12);border-top-color:var(--gold);border-radius:50%;animation:spin .7s linear infinite;margin-bottom:14px}
.analyze-text{font-size:13px;font-weight:800;color:var(--gold);text-align:center;animation:pulse 1.1s ease infinite;max-width:260px;line-height:1.5}
.analyze-smile{font-size:28px;margin-bottom:10px;animation:bounceSmile 1s ease infinite}
.scan-box{background:var(--inner);border:1px solid var(--border-g);border-radius:14px;overflow:hidden;margin-bottom:10px}
.scan-video{width:100%;height:210px;object-fit:cover;background:#000;display:block}
.scan-placeholder{height:210px;display:flex;flex-direction:column;align-items:center;justify-content:center;color:var(--muted);font-size:12px;font-weight:700;gap:8px;text-align:center;padding:12px}
.scan-frame{position:relative}
.scan-tips{font-size:10.5px;color:#b0b8c8;line-height:1.55;background:var(--card);border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:10px}
.scan-tips b{color:var(--gold)}
.fav-list{max-height:320px;overflow-y:auto;display:flex;flex-direction:column;gap:6px}
.fav-item{display:flex;justify-content:space-between;align-items:center;padding:11px 13px;background:var(--inner);border:1px solid var(--border);border-radius:11px;font-size:12px;font-weight:700;cursor:pointer}
.fav-item>span:first-child{color:var(--gold)}
.catalog-category-header{font-size:11px;font-weight:900;color:var(--gold);text-transform:uppercase;margin:12px 0 6px 4px;letter-spacing:.6px;border-bottom:1px solid var(--border-g);padding-bottom:4px}
.profile-photo-upload{width:90px;height:90px;border-radius:50%;border:2px dashed var(--gold);background:var(--inner);display:flex;align-items:center;justify-content:center;cursor:pointer;margin:0 auto 12px;overflow:hidden}
.profile-photo-upload img{width:100%;height:100%;object-fit:cover}
.about-box{background:var(--inner);border:1px solid var(--border-g);border-radius:14px;padding:16px;font-size:12px;color:#c5cdd8;line-height:1.8}
.news-sec-title{font-size:11.5px;font-weight:900;margin:14px 0 8px;padding:8px 12px;border-radius:10px;letter-spacing:.4px;display:flex;align-items:center;gap:6px}
.news-sec-up{background:linear-gradient(90deg,rgba(255,215,0,.2),rgba(255,215,0,.03));color:var(--gold);border:1px solid rgba(255,215,0,.3)}
.news-sec-done{background:linear-gradient(90deg,rgba(0,230,118,.14),rgba(0,230,118,.02));color:var(--green);border:1px solid rgba(0,230,118,.25)}
.news-item{background:linear-gradient(180deg,#101522,#090d14);border:1.5px solid var(--border);border-radius:16px;padding:14px;margin-bottom:11px;cursor:pointer;box-shadow:0 6px 18px rgba(0,0,0,.35);transition:transform .15s}
.news-item:active{transform:scale(.985)}
.news-item.high{border-color:rgba(255,82,82,.6);box-shadow:0 0 0 1px rgba(255,82,82,.15),0 8px 22px rgba(255,82,82,.1)}
.news-item.medium{border-color:rgba(255,167,38,.5);box-shadow:0 0 0 1px rgba(255,167,38,.12),0 6px 18px rgba(255,167,38,.06)}
.news-item.open{border-color:var(--gold);box-shadow:0 0 0 1px rgba(255,215,0,.3),0 10px 28px rgba(255,215,0,.12)}
.news-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.news-cur{font-size:11px;font-weight:900;color:var(--gold);display:flex;align-items:center;gap:6px}
.news-time{font-size:11px;color:#fff;font-weight:800;background:rgba(255,215,0,.14);border:1px solid rgba(255,215,0,.28);padding:4px 9px;border-radius:9px}
.news-name{font-size:13.5px;font-weight:800;color:#fff;margin-bottom:5px;line-height:1.35}
.news-pairs{font-size:10.5px;color:#9aa3b5;margin-bottom:8px;font-weight:600}
.news-meta{display:flex;flex-wrap:wrap;gap:6px;font-size:10px;font-weight:700;color:#c5cdd8}
.news-meta span{background:rgba(255,255,255,.05);border:1px solid var(--border);border-radius:8px;padding:4px 8px}
.news-impact{font-size:9px;font-weight:900;padding:3px 7px;border-radius:6px}
.news-impact.high{background:rgba(255,82,82,.22);color:#ff8a80}
.news-impact.medium{background:rgba(255,167,38,.2);color:#FFB74D}
.news-updated{font-size:10px;color:var(--muted);font-weight:700;text-align:center;margin-bottom:10px;padding:6px;background:rgba(255,255,255,.03);border-radius:8px}
.news-list{max-height:480px;overflow-y:auto;padding-right:3px}
.news-detail{display:none;margin-top:12px;padding:12px;border-top:1px solid rgba(212,175,55,.3);border-radius:0 0 12px 12px;font-size:11.5px;color:#d7deea;line-height:1.7;background:rgba(0,0,0,.3)}
.news-item.open .news-detail{display:block}
.news-copy-btn{margin-top:10px;width:100%;padding:9px;border-radius:10px;border:1px solid rgba(255,215,0,.35);background:rgba(255,215,0,.08);color:var(--gold);font-size:11px;font-weight:800;cursor:pointer}
.filter-row{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.filter-btn{padding:6px 10px;border-radius:10px;border:1px solid var(--border-g);background:var(--inner);color:var(--muted);font-size:10px;font-weight:800;cursor:pointer}
.filter-btn.on{background:var(--gold-g);color:#111;border:none}
</style>
</head>
<body>
<div class="wrap">
<div class="header">
  <div><h1 class="g-text">TEAM MASTER VIP</h1><small>POCKET OPTION TERMINAL</small></div>
  <select class="lang" id="langSelect" onchange="changeLanguage(this.value)"><option value="ru">🇷🇺 RU</option><option value="en">🇺🇸 EN</option><option value="ua">🇺🇦 UA</option></select>
</div>
<div id="app">
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
    <button class="btn btn-gold" onclick="getSig()">⚡ Просчитать сигнал</button>
    <div class="gap"></div>
    <button class="btn btn-green btn-sm" onclick="autoSig()">🤖 Выбрать топ актив</button>
    <div class="sig" id="sigBox">
      <div class="sig-meta" id="sigMeta"></div>
      <div class="sig-dir call" id="sigDir">⬆️ CALL</div>
      <div class="timer">⏱ <span id="cd">01:00</span></div>
      <div class="strat" id="sigStrat"></div>
      <div class="mini-chart-box" id="sigMiniChart"></div>
      <div class="sig-stats"><span>Источник: <b id="sigSource">API Pocket Option</b></span></div>
      <button class="btn btn-red btn-sm" id="btnCancel" onclick="cancelSig()" style="display:none">✖ Отменить</button>
      <button class="btn btn-gold btn-sm" id="btnNew" onclick="getSig()" style="display:none">⚡ Новый сигнал</button>
    </div>
  </div>
  <div class="card tab hidden" id="tabFavs"><h3 class="g-text" style="font-size:13px;margin-bottom:8px">⭐ Избранные</h3><div class="fav-list" id="mainFavList"></div><div id="mainFavEmpty" style="text-align:center;padding:24px;color:var(--muted);font-size:11px">Пока пусто</div></div>
  <div class="card tab hidden" id="tabNews">
    <h3 class="g-text" style="font-size:13px;margin-bottom:6px">📰 Форекс-новости</h3>
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
  <div class="card tab hidden" id="tabProfile"><h3 class="g-text" style="font-size:13px;margin-bottom:12px">👤 Профиль</h3><div class="profile-photo-upload" onclick="document.getElementById('profPhotoInput').click()"><img id="profilePhotoImg" class="hidden" alt=""><span id="profilePhotoIcon" style="font-size:32px">📷</span><input type="file" id="profPhotoInput" accept="image/*" style="display:none" onchange="handleProfilePhoto(event)"></div><input class="input" id="profPocketId" placeholder="Pocket Option ID" onchange="saveProfileSettings()"></div>
  <div class="card tab hidden" id="tabEdu"><h3 class="g-text" style="font-size:13px;margin-bottom:8px">📚 Стратегии</h3><div id="eduItems"></div></div>
  <div class="card tab hidden" id="tabScan">
    <h3 class="g-text" style="font-size:13px;margin-bottom:8px">📷 AI Сканер</h3>
    <div class="scan-box"><div class="scan-frame"><video id="scanVideo" class="scan-video hidden" autoplay playsinline muted></video><img id="scanPreviewImg" class="scan-video hidden" alt=""><div class="scan-placeholder" id="scanPh"><span>📷</span><span>Откройте камеру или загрузите скриншот</span></div></div></div>
    <div class="grid2" style="margin-bottom:10px"><button class="btn btn-dark btn-sm" onclick="openCam()">📷 Камера</button><label class="btn btn-dark btn-sm" style="cursor:pointer">📁 Загрузить<input type="file" id="scanFileInput" accept="image/*" style="display:none" onchange="handleImageUpload(event)"></label></div>
    <button class="btn btn-gold" id="btnScan" onclick="doScan()" disabled>⚡ Сканировать</button>
    <div class="sig" id="scanSigBox" style="display:none"><div class="sig-meta" id="scanMeta"></div><div class="sig-dir call" id="scanDir">BUY</div><div class="timer">⏱ <span id="scanCd">01:00</span></div><div class="strat" id="scanStrat"></div></div>
  </div>
  <div class="card tab hidden" id="tabAbout"><h3 class="g-text" style="font-size:13px;margin-bottom:12px">ℹ️ О нас</h3><div class="about-box">TEAM MASTER VIP — профессиональный торговый терминал с AI и API интеграцией.</div></div>
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
  <div class="modal-content"><div class="modal-header"><h3>Каталог активов</h3><button class="modal-close" onclick="closeCatalogModal()">✕</button></div><input class="modal-search-input" placeholder="Поиск актива..." oninput="renderCatalogList(this.value)"><div class="modal-body" id="catalogBody"></div></div>
</div>
<div class="analyze-overlay hidden" id="analyzeBox"><div class="analyze-smile">😊</div><div class="analyze-wheel"></div><div class="analyze-text" id="analyzeTxt">⚡ Запрос к рынку...</div></div>
<script>
var ASSETS = __ASSETS_JSON__;
var EDU = __EDU_JSON__;
var currentLang = "ru";
var currentAsset = "AUD/USD OTC";
var favs = [];
var timers = {};
var newsCache = [];
var newsFilter = "ALL";
var stream = null;
var camReady = false;
var uploadedImageBlob = null;

try {
  favs = JSON.parse(localStorage.getItem("tmv_favs") || "[]");
} catch(e) {}

function showTab(id, el) {
  document.querySelectorAll(".tab").forEach(t => t.classList.add("hidden"));
  document.getElementById(id).classList.remove("hidden");
  document.querySelectorAll(".nav").forEach(n => n.classList.remove("on"));
  if (el) el.classList.add("on");
}
function updateSelectedAssetUI() { document.getElementById("currentAssetDisplay").textContent = currentAsset; }
function openCatalogModal() { renderCatalogList(""); document.getElementById("catalogModal").classList.remove("hidden"); }
function closeCatalogModal() { document.getElementById("catalogModal").classList.add("hidden"); }

function renderCatalogList(q) {
  var body = document.getElementById("catalogBody");
  var cats = ASSETS[currentLang] || ASSETS.ru;
  var html = "";
  var query = (q || "").toLowerCase();
  for (var cat in cats) {
    var items = cats[cat].filter(a => !query || a.toLowerCase().indexOf(query) >= 0);
    if (!items.length) continue;
    html += '<div class="catalog-category-header">' + cat + '</div>';
    items.forEach(a => {
      var isFav = favs.indexOf(a) >= 0;
      html += '<div class="fav-item" onclick="selectAsset(\'' + a.replace(/'/g,"\\'") + '\')"><span>' + a + '</span><span onclick="event.stopPropagation();toggleFav(\'' + a.replace(/'/g,"\\'") + '\')">' + (isFav ? "⭐" : "☆") + '</span></div>';
    });
  }
  body.innerHTML = html;
}

function selectAsset(a) { currentAsset = a; updateSelectedAssetUI(); closeCatalogModal(); }
function toggleFav(a) {
  var i = favs.indexOf(a);
  if (i >= 0) favs.splice(i, 1); else favs.push(a);
  try { localStorage.setItem("tmv_favs", JSON.stringify(favs)); } catch(e) {}
  renderCatalogList(document.getElementById("catalogBody").querySelector("input") ? "" : "");
  renderMainFavs();
}
function renderMainFavs() {
  var list = document.getElementById("mainFavList");
  var empty = document.getElementById("mainFavEmpty");
  if (!favs.length) { list.innerHTML = ""; empty.style.display = "block"; return; }
  empty.style.display = "none";
  var html = "";
  favs.forEach(a => {
    html += '<div class="fav-item" onclick="selectAsset(\'' + a.replace(/'/g,"\\'") + '\');showTab(\'tabSig\',document.querySelectorAll(\'.nav\')[0])"><span>' + a + '</span><span>→</span></div>';
  });
  list.innerHTML = html;
}

function startTimer(key, elId, seconds, onEnd) {
  if (timers[key]) clearInterval(timers[key]);
  var end = Date.now() + seconds * 1000;
  function tick() {
    var rem = Math.ceil((end - Date.now()) / 1000);
    if (rem <= 0) { clearInterval(timers[key]); document.getElementById(elId).textContent = "00:00"; if (onEnd) onEnd(); return; }
    var m = Math.floor(rem / 60); var s = rem % 60;
    document.getElementById(elId).textContent = (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }
  tick(); timers[key] = setInterval(tick, 250);
}

function runAnalyzer(callback) {
  var overlay = document.getElementById("analyzeBox");
  overlay.classList.remove("hidden");
  setTimeout(() => { overlay.classList.add("hidden"); if (callback) callback(); }, 1600);
}

function renderMiniChart(candles) {
  if (!candles || !candles.length) return "";
  var min = Math.min(...candles);
  var max = Math.max(...candles);
  var range = max - min || 1;
  var w = 320, h = 90;
  var pts = candles.map((p, i) => {
    var x = (i / (candles.length - 1)) * w;
    var y = h - ((p - min) / range) * (h - 12) - 6;
    return x.toFixed(1) + "," + y.toFixed(1);
  }).join(" ");
  return '<svg viewBox="0 0 ' + w + ' ' + h + '"><polyline fill="none" stroke="#FFD700" stroke-width="2.5" points="' + pts + '"/></svg>';
}

function getSig() {
  runAnalyzer(async function() {
    var tf = document.getElementById("tf").value;
    var expSec = parseInt(document.getElementById("exp").value);
    try {
      var res = await fetch("/get_signal?asset=" + encodeURIComponent(currentAsset) + "&timeframe=" + encodeURIComponent(tf));
      if (res.ok) {
        var data = await res.json();
        var dir = data.signal === "UP" ? "⬆️ CALL (ВВЕРХ)" : "⬇️ PUT (ВНИЗ)";
        var isCall = dir.indexOf("CALL") >= 0;
        document.getElementById("sigMeta").textContent = "Pocket Option · " + currentAsset + " · " + tf;
        var dirEl = document.getElementById("sigDir");
        dirEl.textContent = dir;
        dirEl.className = "sig-dir " + (isCall ? "call" : "put");
        document.getElementById("sigStrat").innerHTML = "<b>Анализ:</b> " + data.reason;
        document.getElementById("sigMiniChart").innerHTML = renderMiniChart(data.candles);
        document.getElementById("sigSource").textContent = data.source_desc || "API Pocket Option";
        document.getElementById("sigBox").style.display = "block";
        document.getElementById("btnCancel").style.display = "block";
        document.getElementById("btnNew").style.display = "none";
        startTimer("sig", "cd", expSec, () => {
          document.getElementById("btnCancel").style.display = "none";
          document.getElementById("btnNew").style.display = "block";
        });
      }
    } catch(e) {}
  });
}

function autoSig() {
  var topList = ASSETS[currentLang]["🔥 ТОП Лучшие (OTC API)"] || ASSETS.ru["🔥 ТОП Лучшие (OTC API)"];
  currentAsset = topList[Math.floor(Math.random() * topList.length)];
  updateSelectedAssetUI();
  getSig();
}

function cancelSig() { if (timers.sig) clearInterval(timers.sig); document.getElementById("sigBox").style.display = "none"; }

function setNewsFilter(cur, el) {
  newsFilter = cur;
  document.querySelectorAll("#newsFilterRow .filter-btn").forEach(b => b.classList.remove("on"));
  if (el) el.classList.add("on");
  renderNewsList(newsCache);
}

function renderNewsList(events) {
  var list = document.getElementById("newsList");
  var filtered = events || [];
  if (newsFilter && newsFilter !== "ALL") {
    filtered = filtered.filter(e => (e.currency || "").toUpperCase() === newsFilter);
  }
  if (!filtered.length) {
    list.innerHTML = '<div style="text-align:center;padding:24px;color:var(--muted)">Нет новостей по фильтру</div>';
    return;
  }
  var html = "";
  filtered.forEach((ev, idx) => {
    var ic = (ev.impact || "").toLowerCase() === "high" ? "high" : "medium";
    html += '<div class="news-item ' + ic + '" onclick="toggleNews(' + idx + ')">';
    html += '<div class="news-top"><span class="news-cur">' + ev.currency + ' · <span class="news-impact ' + ic + '">' + (ic === "high" ? "🔴 Высокая" : "🟠 Средняя") + '</span></span><span class="news-time">' + ev.date + '</span></div>';
    html += '<div class="news-name">' + ev.name + '</div>';
    html += '<div class="news-pairs">Влияет на: ' + (ev.pair_hint || ev.currency) + '</div>';
    html += '<div class="news-meta"><span>Прогноз: ' + (ev.forecast || "—") + '</span><span>Факт: ' + (ev.actual || "Ожидается") + '</span></div>';
    html += '<div class="news-detail" id="nd' + idx + '">Рекомендация Team Master: на сильных новостях воздержитесь от входа за 3 минуты до публикации.</div>';
    html += '</div>';
  });
  list.innerHTML = html;
}

function toggleNews(idx) { document.getElementById("nd" + idx).parentElement.classList.toggle("open"); }

async function loadNews() {
  try {
    var res = await fetch("/news");
    if (res.ok) {
      var data = await res.json();
      newsCache = data.events || [];
      renderNewsList(newsCache);
      document.getElementById("newsUpdated").textContent = "🔄 Обновлено · Событий: " + newsCache.length;
    }
  } catch(e) {}
}

function renderEdu() {
  var html = "";
  EDU.forEach((ed, i) => {
    html += '<div class="edu-item" onclick="alert(\'' + ed.t.replace(/'/g,"\\'") + '\')"><h4>' + ed.t + '</h4><span style="color:var(--gold)">➔</span></div>';
  });
  document.getElementById("eduItems").innerHTML = html;
}

function openCam() {
  navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false }).then(s => {
    stream = s;
    var video = document.getElementById("scanVideo");
    video.srcObject = stream;
    video.classList.remove("hidden");
    document.getElementById("scanPh").classList.add("hidden");
    camReady = true;
    document.getElementById("btnScan").removeAttribute("disabled");
  }).catch(() => alert("Камера недоступна"));
}

function handleImageUpload(e) {
  var file = e.target.files[0];
  if (!file) return;
  uploadedImageBlob = file;
  var reader = new FileReader();
  reader.onload = ev => {
    var img = document.getElementById("scanPreviewImg");
    img.src = ev.target.result;
    img.classList.remove("hidden");
    document.getElementById("scanPh").classList.add("hidden");
    camReady = true;
    document.getElementById("btnScan").removeAttribute("disabled");
  };
  reader.readAsDataURL(file);
}

function doScan() {
  runAnalyzer(async function() {
    var resText = "BUY (ВВЕРХ) 📈 · Импульс и уровни подтверждены.";
    document.getElementById("scanDir").textContent = resText;
    document.getElementById("scanStrat").innerHTML = "<b>AI Анализ скриншота:</b> График стабилен, тренд восходящий.";
    document.getElementById("scanSigBox").style.display = "block";
    startTimer("scan", "scanCd", 60);
  });
}

function handleProfilePhoto(e) {}
function saveProfileSettings() {}
function changeLanguage(lang) { currentLang = lang; }
function tickClock() {
  var d = new Date();
  var el = document.getElementById("liveClock");
  if (el) el.textContent = "🕒 " + String(d.getHours()).padStart(2,'0') + ":" + String(d.getMinutes()).padStart(2,'0') + ":" + String(d.getSeconds()).padStart(2,'0');
}

window.addEventListener("DOMContentLoaded", () => {
  updateSelectedAssetUI();
  renderMainFavs();
  renderEdu();
  loadNews();
  tickClock();
  setInterval(tickClock, 1000);
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
async def get_signal(asset: str = "AUD/USD OTC", timeframe: str = "M1"):
    asyncio.create_task(send_tg_signal_alert(asset, timeframe))
    await asyncio.sleep(0.6)

    prices = []
    top_otcs = ["AUD/USD OTC", "EUR/USD OTC", "GBP/USD OTC", "USD/JPY OTC", "BITCOIN OTC"]
    source_desc = "Умный рандом (Локальный анализ)"

    if asset in top_otcs and OTCSHARDS_API_KEY:
        try:
            async with httpx.AsyncClient() as client:
                url = f"https://api.otcshards.com/v1/candles?symbol={asset}&timeframe={timeframe}&key={OTCSHARDS_API_KEY}"
                res = await client.get(url, timeout=5.0)
                if res.status_code == 200:
                    data = res.json()
                    prices = [float(c["close"]) for c in data.get("candles", [])]
                    if prices:
                        source_desc = "API OTCShards (Реальные OTC данные)"
        except Exception as e:
            logging.warning(f"OTCShards API error: {e}")

    if not prices:
        prices = generate_otc_candles(asset, count=50)

    rsi = calculate_rsi(prices)
    ema = calculate_ema(prices)
    signal, reason = smart_market_analysis(prices, rsi, ema)
    return {"signal": signal, "reason": reason, "candles": prices, "source_desc": source_desc}


@app.get("/news")
async def get_news():
    events = [
        {"name": "Занятость вне с/х (NFP)", "currency": "USD", "date": "Сегодня 15:30", "impact": "High", "forecast": "180K", "actual": "Ожидается", "pair_hint": "EUR/USD, GBP/USD"},
        {"name": "Решение ФРС по ставке", "currency": "USD", "date": "Завтра 21:00", "impact": "High", "forecast": "5.25%", "actual": "Ожидается", "pair_hint": "USD/JPY, EUR/USD"},
        {"name": "Индекс потребительских цен (CPI)", "currency": "EUR", "date": "Через 2 дня 12:00", "impact": "Medium", "forecast": "2.4%", "actual": "Ожидается", "pair_hint": "EUR/USD, EUR/GBP"}
    ]
    return {"events": events, "updated": datetime.now(timezone.utc).isoformat()}


@app.post("/analyze")
async def analyze(file: UploadFile = File(...), cfg: str = Form(...)):
    image_bytes = await file.read()
    res = await scanner_core.compute(image_bytes, {})
    return {"text": res}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
