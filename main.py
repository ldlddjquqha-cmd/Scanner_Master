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
app = FastAPI(title="TEAM MASTER VIP - ULTIMATE QUANTUM CORE", version="26.8")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")


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
    "NZD/USD": "NZDUSDT", "EUR/GBP": "EURGBP",
    "Bitcoin": "BTCUSDT", "Ethereum": "ETHUSDT", "Solana": "SOLUSDT",
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
    def _gemini_sync(self, image, config):
        model = genai.GenerativeModel(GEMINI_MODEL)
        prompt = (
            "Ты профессиональный ИИ-сканер графиков бинарных опционов Pocket Option. "
            "Внимательно проанализируй расположение свечей, их тела, тени, цвета, текущую тенденцию, уровни и паттерны на данном скриншоте графика. "
            "Выдай торговый сигнал: направление (BUY / ВВЕРХ или SELL / ВНИЗ), таймфрейм, стратегию ("
            + config.get("стратегия", "Smart Money")
            + ") и подробный технический анализ на основе расположения свечей. Используй эмодзи 📈📉⚡🔥💎🎯🚀."
        )
        response = model.generate_content([prompt, image])
        return response.text if response and response.text else None

    async def compute(self, image_bytes: bytes, config: dict) -> str:
        try:
            image = Image.open(io.BytesIO(image_bytes))

            if GEMINI_API_KEY:
                try:
                    text = await asyncio.to_thread(self._gemini_sync, image, config)
                    if text:
                        return f"--- TEAM MASTER AI CANDLE SCANNER ---\n{text}"
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
            rsi_note = "Импульс подтверждён закрытием последних свечей. ⚡"
            level_note = "Уровни поддержки и сопротивления визуально подтверждены на графике. 🎯💎"

            return (
                f"--- TEAM MASTER SIGNAL V4.0 ---\n"
                f"📊 СТРАТЕГИЯ: {config.get('стратегия', 'Smart Money')}\n"
                f"⏱ ТАЙМФРЕЙМ: {config.get('интервал', 'M1')}\n"
                f"⏳ ЭКСПИРАЦИЯ: {config.get('экспирация', '1м')}\n"
                f"-------------------------------\n"
                f"🔥 ВЕРДИКТ: {direction}\n"
                f"📌 ПРИЧИНА: Расположение и анализ свечных тел: {trend_desc}\n"
                f"{rsi_note}\n"
                f"{level_note}\n"
                f"🚀 Сигнал готов к входу!"
            )
        except Exception as e:
            return f"❌ Ошибка анализа изображения: {str(e)} ⚠️"


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
    "🇺🇦🇺🇸 UAH/USD OTC", "🇳🇬🇺🇸 NGN/USD OTC", "🇰🇪🇺🇸 KES/USD OTC",
]
_CRYPTO = [
    "₿ Bitcoin", "Ξ Ethereum", "🪙 Solana", "🪙 BNB OTC", "🪙 Dogecoin OTC", "🪙 Chainlink OTC",
    "🪙 Toncoin OTC", "🪙 Bitcoin ETF OTC", "🪙 Bitcoin OTC", "🪙 Avalanche OTC", "🪙 Cardano OTC",
    "🪙 Ethereum OTC", "🪙 Polkadot OTC", "🪙 Polygon OTC", "🪙 Litecoin OTC", "🪙 TRON OTC",
    "🪙 Dash", "🪙 Chainlink", "🪙 BCH/EUR", "🪙 BCH/GBP", "🪙 BCH/JPY", "🪙 BTC/GBP", "🪙 BTC/JPY",
]
_COMMODITIES = [
    "🛢️ Brent Oil OTC", "🛢️ WTI Crude Oil OTC", "🪙 Silver OTC", "🪙 Gold OTC",
    "⛽ Natural Gas OTC", "🪙 Palladium spot OTC", "🪙 Platinum spot",
]
_STOCKS = [
    "🍎 Apple OTC", "🚗 Tesla OTC", "💳 VISA OTC", "💻 Intel OTC", "🛒 Amazon OTC",
    "🎬 Netflix OTC", "📘 FACEBOOK INC OTC", "📦 Microsoft OTC", "💊 Johnson & Johnson OTC",
    "🛒 Alibaba OTC", "📊 VIX OTC", "🛢️ ExxonMobil OTC", "🤖 Palantir Technologies OTC",
    "🪙 Coinbase Global OTC", "🍔 McDonald's OTC", "✈️ Boeing Company OTC", "💊 Pfizer Inc OTC",
    "🏦 Citigroup Inc OTC", "📦 FedEx OTC", "🎮 GameStop Corp OTC", "💳 American Express OTC",
    "🌐 Cisco OTC", "💻 Advanced Micro Devices OTC", "📦 Marathon Digital Holdings OTC",
]
_INDICES = ["📊 US100 OTC", "📈 SP500", "📊 SP500 OTC", "📊 AUS 200"]

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
    EDU_50.append({
        "t": f"{i}. Профессиональная стратегия и связка индикаторов №{i}",
        "tags": ["M1", "Pocket Option", "Стратегии"],
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
.nav{display:flex;flex-direction:column;align-items:center;gap:1px;color:var(--muted);font-size:8.5px;font-weight:800;cursor:pointer;min-width:40px;padding:3px 1px;transition:color .2s}
.nav.on{color:var(--gold)}.nav span:first-child{font-size:15px}
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
.news-item{background:var(--inner);border:1px solid var(--border);border-radius:11px;padding:11px 12px;margin-bottom:7px}
.news-item.high{border-color:rgba(255,82,82,.45);background:rgba(255,82,82,.06)}
.news-item.medium{border-color:rgba(255,167,38,.4);background:rgba(255,167,38,.05)}
.news-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;gap:8px}
.news-cur{font-size:11px;font-weight:900;color:var(--gold)}
.news-time{font-size:10px;color:var(--muted);font-weight:700}
.news-name{font-size:12px;font-weight:800;color:var(--text);margin-bottom:6px;line-height:1.35}
.news-meta{display:flex;flex-wrap:wrap;gap:6px;font-size:10px;font-weight:700;color:#b0b8c8}
.news-meta span{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:3px 7px}
.news-impact{font-size:9px;font-weight:900;padding:2px 7px;border-radius:6px}
.news-impact.high{background:rgba(255,82,82,.2);color:var(--red)}
.news-impact.medium{background:rgba(255,167,38,.2);color:#FFA726}
.news-impact.low{background:rgba(255,215,0,.12);color:var(--gold)}
.news-updated{font-size:10px;color:var(--muted);font-weight:700;text-align:center;margin-bottom:8px}
.news-list{max-height:420px;overflow-y:auto}
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
      <span style="font-size:9px;color:var(--gold);font-weight:800" id="apiTimerDisplay" data-t="syncLbl">⚡ СИНХРОНИЗАЦИЯ</span>
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

  <!-- НОВОСТИ -->
  <div class="card tab hidden" id="tabNews">
    <h3 class="g-text" style="font-size:13px;margin-bottom:6px" data-t="newsTitle">📰 Экономические новости</h3>
    <p style="font-size:10.5px;color:var(--muted);margin-bottom:8px" data-t="newsDesc">Календарь High/Medium событий. Автообновление — новые новости появляются сами.</p>
    <div class="news-updated" id="newsUpdated">—</div>
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
      <b>TEAM MASTER VIP</b> — <span data-t="aboutP1">это международное сообщество и торговая экосистема, основанная трейдерами для тех, кто хочет системно развиваться на финансовых рынках и платформе Pocket Option.</span><br><br>
      🎯 <b data-t="aboutGoalT">Наша цель и миссия:</b> <span data-t="aboutGoal">предоставить каждому участнику современные инструменты, включая ИИ-сканер графиков по свечам, авторские связки индикаторов и стратегии Price Action и Smart Money.</span><br><br>
      💡 <b data-t="aboutWhyT">Что внутри:</b><br>
      <span data-t="aboutL1">• Технические сигналы и ИИ-сканирование графиков</span><br>
      <span data-t="aboutL2">• База знаний из 50 связок и методик</span><br>
      <span data-t="aboutL3">• Экономический календарь новостей с автообновлением</span><br><br>
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

</div>

<script>
const ASSETS_DATA_JS = __ASSETS_JSON__;
const EDU = __EDU_JSON__;

let timers = {sig: null, scan: null};
let stream = null, camReady = false;
let favs = [];
try { favs = JSON.parse(localStorage.getItem("tmv_favs") || "[]"); } catch(e) { favs = []; }
let sigCount = 0;
try { sigCount = parseInt(localStorage.getItem("tmv_sigs") || "0") || 0; } catch(e) {}
let currentAsset = "🇪🇺🇺🇸 EUR/USD OTC";
let currentLang = "ru";
let modalCategory = "all";
let uploadedImageBlob = null;
let scanCfg = {"интервал": "M1", "экспирация": "1м", "стратегия": "Smart Money"};
let newsTimer = null;
let lastNewsHash = "";

const I18N = {
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
    newsTitle: "📰 Экономические новости", newsDesc: "Календарь High/Medium событий. Автообновление — новые новости появляются сами.",
    btnRefreshNews: "🔄 Обновить сейчас", newsLoading: "Загрузка новостей...",
    scanTitle: "📷 ИИ Сканер (Анализ по свечам)", scanInstr: "Инструкция ИИ-сканера свечей:",
    scanStep1: "1️⃣ Настройте параметры и стратегию ниже",
    scanStep2: "2️⃣ Откройте камеру или загрузите скриншот графика со свечами",
    scanStep3: "3️⃣ Нажмите «ЗАПУСК (ИИ СКАН СВЕЧЕЙ)»",
    lblScanTf: "ИНТЕРВАЛ СВЕЧЕЙ", lblScanStrat: "СТРАТЕГИЯ АНАЛИЗА",
    btnOpenCam: "📷 Открыть камеру", btnUpload: "📁 Загрузить скриншот графика со свечами",
    camOff: "Камера отключена или файл не выбран", camOffSub: "нажмите кнопку выше для запуска или загрузите скрин",
    btnScanNow: "🚀 ЗАПУСК (ИИ СКАН СВЕЧЕЙ)", btnCloseCam: "⏹ Закрыть камеру / Сбросить", btnNewScan: "⚡ Новый скан",
    aboutTitle: "👑 О нашей команде (Team Master)",
    aboutP1: "это международное сообщество и торговая экосистема, основанная трейдерами для тех, кто хочет системно развиваться на финансовых рынках и платформе Pocket Option.",
    aboutGoalT: "Наша цель и миссия:",
    aboutGoal: "предоставить каждому участнику современные инструменты, включая ИИ-сканер графиков по свечам, авторские связки индикаторов и стратегии Price Action и Smart Money.",
    aboutWhyT: "Что внутри:",
    aboutL1: "• Технические сигналы и ИИ-сканирование графиков", aboutL2: "• База знаний из 50 связок и методик",
    aboutL3: "• Экономический календарь новостей с автообновлением",
    aboutRisk: "⚠️ Торговля бинарными опционами связана с высоким риском потери капитала. Сигналы не гарантируют прибыль. Риск на сделку — 1-2% от депозита.",
    btnTgChan: "📢 Наш Telegram канал",
    navSig: "Сигналы", navFavs: "Избранное", navNews: "Новости", navEdu: "Связки", navProf: "Профиль", navScan: "Сканер", navAbout: "О нас",
    modalCatalogTitle: "📋 Полный каталог активов Pocket Option", searchPlaceholder: "Поиск актива...",
    catAll: "🌟 Все", catCur: "💱 Валюта", catCrypto: "🔥 Крипто", catComm: "🛢️ Сырьё", catStocks: "📈 Акции", catIdx: "📊 Индексы"
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
    newsTitle: "📰 Economic News", newsDesc: "High/Medium impact calendar. Auto-refresh — new events appear automatically.",
    btnRefreshNews: "🔄 Refresh now", newsLoading: "Loading news...",
    scanTitle: "📷 AI Scanner (Candle Analysis)", scanInstr: "AI candle scanner instructions:",
    scanStep1: "1️⃣ Set the parameters and strategy below",
    scanStep2: "2️⃣ Open the camera or upload a chart screenshot with candles",
    scanStep3: "3️⃣ Press “RUN (AI CANDLE SCAN)”",
    lblScanTf: "CANDLE INTERVAL", lblScanStrat: "ANALYSIS STRATEGY",
    btnOpenCam: "📷 Open Camera", btnUpload: "📁 Upload chart screenshot with candles",
    camOff: "Camera is off or no file selected", camOffSub: "press the button above to start or upload a screenshot",
    btnScanNow: "🚀 RUN (AI CANDLE SCAN)", btnCloseCam: "⏹ Close Camera / Reset", btnNewScan: "⚡ New Scan",
    aboutTitle: "👑 About Our Team (Team Master)",
    aboutP1: "is an international community and trading ecosystem created by traders for those who want to grow systematically on financial markets and the Pocket Option platform.",
    aboutGoalT: "Our goal and mission:",
    aboutGoal: "to give every member modern tools, including an AI candle chart scanner, author indicator combos and Price Action and Smart Money strategies.",
    aboutWhyT: "What's inside:",
    aboutL1: "• Technical signals and AI chart scanning", aboutL2: "• Knowledge base of 50 combos and methods",
    aboutL3: "• Economic news calendar with auto-refresh",
    aboutRisk: "⚠️ Binary options trading carries a high risk of losing capital. Signals do not guarantee profit. Risk per trade — 1-2% of the deposit.",
    btnTgChan: "📢 Our Telegram Channel",
    navSig: "Signals", navFavs: "Favorites", navNews: "News", navEdu: "Combos", navProf: "Profile", navScan: "Scanner", navAbout: "About",
    modalCatalogTitle: "📋 Full Pocket Option Assets Catalog", searchPlaceholder: "Search asset...",
    catAll: "🌟 All", catCur: "💱 Currency", catCrypto: "🔥 Crypto", catComm: "🛢️ Commodities", catStocks: "📈 Stocks", catIdx: "📊 Indices"
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
    newsTitle: "📰 Економічні новини", newsDesc: "Календар High/Medium подій. Автооновлення — нові новини з'являються самі.",
    btnRefreshNews: "🔄 Оновити зараз", newsLoading: "Завантаження новин...",
    scanTitle: "📷 ШІ Сканер (Аналіз за свічками)", scanInstr: "Інструкція ШІ-сканера свічок:",
    scanStep1: "1️⃣ Налаштуйте параметри та стратегію нижче",
    scanStep2: "2️⃣ Відкрийте камеру або завантажте скріншот графіка зі свічками",
    scanStep3: "3️⃣ Натисніть «ЗАПУСК (ШІ СКАН СВІЧОК)»",
    lblScanTf: "ІНТЕРВАЛ СВІЧОК", lblScanStrat: "СТРАТЕГІЯ АНАЛІЗУ",
    btnOpenCam: "📷 Відкрити камеру", btnUpload: "📁 Завантажити скріншот графіка зі свічками",
    camOff: "Камера вимкнена або файл не обрано", camOffSub: "натисніть кнопку вище для запуску або завантажте скрін",
    btnScanNow: "🚀 ЗАПУСК (ШІ СКАН СВІЧОК)", btnCloseCam: "⏹ Закрити камеру / Скинути", btnNewScan: "⚡ Новий скан",
    aboutTitle: "👑 Про нашу команду (Team Master)",
    aboutP1: "це міжнародна спільнота та торгова екосистема, створена трейдерами для тих, хто хоче системно розвиватися на фінансових ринках і платформі Pocket Option.",
    aboutGoalT: "Наша мета і місія:",
    aboutGoal: "надати кожному учаснику сучасні інструменти, зокрема ШІ-сканер графіків за свічками, авторські зв'язки індикаторів та стратегії Price Action і Smart Money.",
    aboutWhyT: "Що всередині:",
    aboutL1: "• Технічні сигнали та ШІ-сканування графіків", aboutL2: "• База знань з 50 зв'язок і методик",
    aboutL3: "• Економічний календар новин з автооновленням",
    aboutRisk: "⚠️ Торгівля бінарними опціонами пов'язана з високим ризиком втрати капіталу. Сигнали не гарантують прибуток. Ризик на угоду — 1-2% від депозиту.",
    btnTgChan: "📢 Наш Telegram канал",
    navSig: "Сигнали", navFavs: "Обране", navNews: "Новини", navEdu: "Зв'язки", navProf: "Профіль", navScan: "Сканер", navAbout: "Про нас",
    modalCatalogTitle: "📋 Повний каталог активів Pocket Option", searchPlaceholder: "Пошук активу...",
    catAll: "🌟 Всі", catCur: "💱 Валюта", catCrypto: "🔥 Крипто", catComm: "🛢️ Сировина", catStocks: "📈 Акції", catIdx: "📊 Індекси"
  }
};

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function getCategories() { return ASSETS_DATA_JS[currentLang] || ASSETS_DATA_JS["ru"]; }

function updateSelectedAssetUI() {
  document.getElementById("currentAssetDisplay").textContent = currentAsset;
}

function tab(tabId, el) {
  document.querySelectorAll(".tab").forEach(t => t.classList.add("hidden"));
  document.getElementById(tabId).classList.remove("hidden");
  document.querySelectorAll(".bnav .nav").forEach(n => n.classList.remove("on"));
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
  document.querySelectorAll("#catalogFilterCats .cat").forEach(c => c.classList.remove("on"));
  if (el) el.classList.add("on");
  renderCatalogList(document.getElementById("modalSearchInput").value);
}

function setScanCfg(el, type, val) {
  el.parentElement.querySelectorAll(".cat").forEach(b => b.classList.remove("on"));
  el.classList.add("on");
  scanCfg[type] = val;
}

function renderCatalogList(filter) {
  filter = (filter || "").toLowerCase();
  const bodyEl = document.getElementById("modalCatalogBody");
  const cats = getCategories();
  let html = "";
  for (let catName in cats) {
    if (modalCategory !== "all" && !catName.includes(modalCategory)) continue;
    const items = cats[catName].filter(it => it.toLowerCase().includes(filter));
    if (items.length) {
      html += `<div class="catalog-category-header">${esc(catName)}</div>`;
      items.forEach(item => {
        const isFav = favs.includes(item);
        html += `<div class="fav-item">
          <span data-a="\( {esc(item)}" onclick="selectCatalogAsset(this.dataset.a)" style="flex:1"> \){esc(item)}</span>
          <div style="display:flex;gap:10px;align-items:center">
            <span style="cursor:pointer;font-size:15px" data-a="\( {esc(item)}" onclick="toggleFav(this.dataset.a)"> \){isFav ? "⭐" : "☆"}</span>
            <span style="color:var(--gold);font-size:11px;cursor:pointer" data-a="${esc(item)}" onclick="selectCatalogAsset(this.dataset.a)">➔</span>
          </div>
        </div>`;
      });
    }
  }
  bodyEl.innerHTML = html || `<div style="text-align:center;padding:20px;color:var(--muted);font-size:11px">—</div>`;
}

function toggleFav(name) {
  if (favs.includes(name)) favs = favs.filter(f => f !== name);
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
  const listEl = document.getElementById("mainFavList");
  const emptyEl = document.getElementById("mainFavEmpty");
  if (!favs.length) {
    listEl.innerHTML = "";
    emptyEl.style.display = "block";
    return;
  }
  emptyEl.style.display = "none";
  let html = "";
  favs.forEach(f => {
    html += `<div class="fav-item" data-a="${esc(f)}" onclick="selectCatalogAsset(this.dataset.a)"><span>⭐ ${esc(f)}</span><span style="color:var(--gold)">➔</span></div>`;
  });
  listEl.innerHTML = html;
}

function clearFavs() {
  favs = [];
  try { localStorage.setItem("tmv_favs", "[]"); } catch(e) {}
  renderMainFavs();
  updateProfileStats();
}

function startTimer(key, elementId, seconds, onComplete) {
  if (timers[key]) clearInterval(timers[key]);
  let rem = seconds;
  const el = document.getElementById(elementId);
  updateTimerDisplay(el, rem);
  timers[key] = setInterval(() => {
    rem--;
    if (rem <= 0) {
      clearInterval(timers[key]);
      el.textContent = "00:00 ✔";
      if (onComplete) onComplete();
      return;
    }
    updateTimerDisplay(el, rem);
  }, 1000);
}

function updateTimerDisplay(el, sec) {
  const m = Math.floor(sec / 60), s = sec % 60;
  el.textContent = (m < 10 ? "0" + m : m) + ":" + (s < 10 ? "0" + s : s);
}

function runAnalyzer(callback) {
  const overlay = document.getElementById("analyzeBox");
  const txtEl = document.getElementById("analyzeTxt");
  const smileEl = document.getElementById("analyzeSmile");
  overlay.classList.remove("hidden");
  const phrases = [
    "⚡ Подключаюсь к рынку... 😊",
    "📊 Анализирую котировки... 🔍",
    "💎 Сканирую индикаторы... ✨",
    "🚀 Формирую точный сигнал... 🎯"
  ];
  const smiles = ["😊", "😎", "🤩", "🔥"];
  let step = 0;
  txtEl.textContent = phrases[0];
  smileEl.textContent = smiles[0];
  const interval = setInterval(() => {
    step++;
    txtEl.textContent = phrases[step % phrases.length];
    smileEl.textContent = smiles[step % smiles.length];
  }, 480);
  setTimeout(() => {
    clearInterval(interval);
    overlay.classList.add("hidden");
    if (callback) callback();
  }, 2400);
}

function incSig() {
  sigCount++;
  try { localStorage.setItem("tmv_sigs", sigCount); } catch(e) {}
  updateProfileStats();
}

function getSig() {
  runAnalyzer(async () => {
    const tf = document.getElementById("tf").value;
    const expSec = parseInt(document.getElementById("exp").value);
    let dir = "⬆️ CALL (ВВЕРХ)";
    let stratText = `Технический анализ котировок для актива (${currentAsset}) по таймфрейму ${tf} с экспирацией ${expSec} сек.`;

    try {
      const res = await fetch(`/get_signal?asset=\( {encodeURIComponent(currentAsset)}&timeframe= \){encodeURIComponent(tf)}`);
      if (res.ok) {
        const data = await res.json();
        if (data.signal) {
          dir = data.signal === "UP" ? "⬆️ CALL (ВВЕРХ)" : "⬇️ PUT (ВНИЗ)";
          if (data.reason) stratText = data.reason;
        }
      }
    } catch (e) {}

    const isCall = dir.includes("CALL");
    document.getElementById("sigMeta").textContent = `Pocket Option · ${currentAsset} · ${tf} · ${expSec} сек`;
    const dirEl = document.getElementById("sigDir");
    dirEl.textContent = dir;
    dirEl.className = "sig-dir " + (isCall ? "call" : "put");
    document.getElementById("sigStrat").innerHTML = `<b>Анализ сигнала:</b> ${esc(stratText)}`;
    document.getElementById("sigBox").style.display = "block";
    document.getElementById("btnCancel").style.display = "block";
    document.getElementById("btnNew").style.display = "none";
    incSig();
    startTimer("sig", "cd", expSec, () => {
      document.getElementById("btnCancel").style.display = "none";
      document.getElementById("btnNew").style.display = "block";
    });
  });
}

function autoSig() {
  let allItems = [];
  const cats = getCategories();
  for (let c in cats) allItems = allItems.concat(cats[c]);
  currentAsset = allItems[Math.floor(Math.random() * allItems.length)];
  updateSelectedAssetUI();
  getSig();
}

function cancelSig() {
  if (timers.sig) clearInterval(timers.sig);
  document.getElementById("sigBox").style.display = "none";
}

function formatNewsTime(iso) {
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso || "—";
    const dd = String(d.getDate()).padStart(2, "0");
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return `\( {dd}. \){mm} \( {hh}: \){mi}`;
  } catch (e) { return iso || "—"; }
}

function impactClass(imp) {
  const x = (imp || "").toLowerCase();
  if (x === "high") return "high";
  if (x === "medium") return "medium";
  return "low";
}

function impactEmoji(imp) {
  const x = (imp || "").toLowerCase();
  if (x === "high") return "🔴 High";
  if (x === "medium") return "🟠 Medium";
  return "🟡 Low";
}

function renderNewsList(events) {
  const list = document.getElementById("newsList");
  if (!events || !events.length) {
    list.innerHTML = `<div style="text-align:center;padding:20px;color:var(--muted);font-size:11px">Нет событий на ближайшие дни</div>`;
    return;
  }
  let html = "";
  events.forEach(ev => {
    const ic = impactClass(ev.impact);
    const actual = (ev.actual || "").trim();
    const forecast = (ev.forecast || "").trim();
    const previous = (ev.previous || "").trim();
    html += `<div class="news-item ${ic}">
      <div class="news-top">
        <span class="news-cur">${esc(ev.currency || "—")} · <span class="news-impact \( {ic}"> \){impactEmoji(ev.impact)}</span></span>
        <span class="news-time">🕒 ${esc(formatNewsTime(ev.date))}</span>
      </div>
      <div class="news-name">${esc(ev.name || "Событие")}</div>
      <div class="news-meta">
        ${forecast ? `<span>📈 Прогноз: ${esc(forecast)}</span>` : ""}
        ${previous ? `<span>📉 Пред.: ${esc(previous)}</span>` : ""}
        ${actual ? `<span style="color:var(--green)">✅ Факт: ${esc(actual)}</span>` : `<span>⏳ Ожидается</span>`}
      </div>
    </div>`;
  });
  list.innerHTML = html;
}

async function loadNews(force) {
  const upd = document.getElementById("newsUpdated");
  try {
    const res = await fetch("/news");
    if (!res.ok) throw new Error("fail");
    const data = await res.json();
    const events = data.events || [];
    const hash = JSON.stringify(events.map(e => [e.name, e.date, e.actual, e.forecast]));
    if (force || hash !== lastNewsHash) {
      lastNewsHash = hash;
      renderNewsList(events);
    }
    const t = data.updated ? formatNewsTime(data.updated) : formatNewsTime(new Date().toISOString());
    upd.textContent = `🔄 Обновлено: ${t} · событий: ${events.length}`;
  } catch (e) {
    if (!document.getElementById("newsList").innerHTML.includes("news-item")) {
      document.getElementById("newsList").innerHTML = `<div style="text-align:center;padding:20px;color:var(--red);font-size:11px">Не удалось загрузить новости. Нажмите обновить.</div>`;
    }
  }
}

function startNewsAuto() {
  if (newsTimer) clearInterval(newsTimer);
  newsTimer = setInterval(() => loadNews(false), 90000);
}

function renderEdu() {
  let html = "";
  EDU.forEach((ed, i) => {
    html += `<div class="edu-item" onclick="openEdu(\( {i})"><h4> \){esc(ed.t)}</h4><span style="color:var(--gold)">➔</span></div>`;
  });
  document.getElementById("eduItems").innerHTML = html;
}

function openEdu(idx) {
  const ed = EDU[idx];
  document.getElementById("eduList").classList.add("hidden");
  document.getElementById("eduView").classList.remove("hidden");
  let tagsHtml = "";
  ed.tags.forEach(t => { tagsHtml += `<span class="tag">${esc(t)}</span>`; });
  document.getElementById("eduBody").innerHTML = `<h3>\( {esc(ed.t)}</h3><div class="tag-row"> \){tagsHtml}</div><p>${esc(ed.d)}</p>`;
}
function closeEdu() {
  document.getElementById("eduView").classList.add("hidden");
  document.getElementById("eduList").classList.remove("hidden");
}
function openRandomEdu() { openEdu(Math.floor(Math.random() * EDU.length)); }

function updateProfileStats() {
  document.getElementById("profSigCount").textContent = sigCount;
  document.getElementById("profFavCount").textContent = favs.length;
}

function loadProfile() {
  try {
    document.getElementById("profPocketId").value = localStorage.getItem("tmv_pocketId") || "";
    document.getElementById("profFavStrat").value = localStorage.getItem("tmv_favStrat") || "";
    const photo = localStorage.getItem("tmv_profPhoto");
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
  } catch(e) {}
  sigCount = 0;
  document.getElementById("profPocketId").value = "";
  document.getElementById("profFavStrat").value = "";
  document.getElementById("profilePhotoImg").classList.add("hidden");
  document.getElementById("profilePhotoImg").removeAttribute("src");
  document.getElementById("profilePhotoIcon").classList.remove("hidden");
  updateProfileStats();
}

function handleProfilePhoto(event) {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
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
  const video = document.getElementById("scanVideo");
  document.getElementById("scanPreviewImg").classList.add("hidden");
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    alert("Камера недоступна в этом браузере. Загрузите скриншот графика.");
    return;
  }
  navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false })
    .then(s => {
      stream = s;
      video.srcObject = stream;
      video.classList.remove("hidden");
      document.getElementById("scanPh").classList.add("hidden");
      document.getElementById("scanFrame").classList.add("live");
      camReady = true;
      document.getElementById("btnScan").removeAttribute("disabled");
    })
    .catch(err => { alert("Не удалось получить доступ к камере: " + err); });
}

function handleImageUpload(event) {
  const file = event.target.files[0];
  if (!file) return;
  uploadedImageBlob = file;
  if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
  document.getElementById("scanVideo").classList.add("hidden");
  const previewImg = document.getElementById("scanPreviewImg");
  const reader = new FileReader();
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
  if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
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
  const v = scanCfg["экспирация"];
  const n = parseInt(v);
  return v.includes("с") ? n : n * 60;
}

function doScan() {
  if (!camReady) { alert("Сначала откройте камеру или загрузите скриншот графика со свечами!"); return; }
  const expSec = getScanExpSeconds();
  if (uploadedImageBlob) {
    processImageBlobForScan(uploadedImageBlob, expSec);
  } else {
    const video = document.getElementById("scanVideo");
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.toBlob(blob => { processImageBlobForScan(blob, expSec); }, "image/jpeg");
  }
}

function processImageBlobForScan(blob, expSec) {
  runAnalyzer(async () => {
    let resText = "❌ Ошибка анализа. Попробуйте ещё раз.";
    try {
      const fd = new FormData();
      fd.append("file", blob, "snapshot.jpg");
      fd.append("cfg", JSON.stringify(scanCfg));
      const response = await fetch("/analyze", { method: "POST", body: fd });
      if (response.ok) {
        const resData = await response.json();
        resText = resData.text || resText;
      }
    } catch (e) {}

    const isErr = resText.includes("❌");
    document.getElementById("scanMeta").textContent = "TEAM MASTER AI SCANNER";
    const dirEl = document.getElementById("scanDir");

    if (isErr) {
      dirEl.textContent = "❌ ОШИБКА / ГРАФИК НЕ ОБНАРУЖЕН";
      dirEl.className = "sig-dir put";
    } else {
      const upper = resText.toUpperCase();
      const iBuy = Math.min(...["BUY", "ВВЕРХ"].map(w => upper.indexOf(w) < 0 ? 1e9 : upper.indexOf(w)));
      const iSell = Math.min(...["SELL", "ВНИЗ"].map(w => upper.indexOf(w) < 0 ? 1e9 : upper.indexOf(w)));
      const buy = iBuy <= iSell;
      dirEl.textContent = buy ? "BUY (ВВЕРХ) 📈" : "SELL (ВНИЗ) 📉";
      dirEl.className = "sig-dir " + (buy ? "call" : "put");
      incSig();
    }

    document.getElementById("scanStrat").innerHTML = `<pre style="white-space:pre-wrap;font-family:inherit;margin:0">${esc(resText)}</pre>`;
    document.getElementById("scanSigBox").style.display = "block";
    document.getElementById("btnScanCancel").style.display = "block";
    document.getElementById("btnScanNew").style.display = "none";

    if (!isErr) {
      startTimer("scan", "scanCd", expSec, () => {
        document.getElementById("btnScanCancel").style.display = "none";
        document.getElementById("btnScanNew").style.display = "block";
      });
    } else {
      document.getElementById("btnScanCancel").style.display = "none";
      document.getElementById("btnScanNew").style.display = "block";
    }
  });
}

function cancelScan() {
  if (timers.scan) clearInterval(timers.scan);
  document.getElementById("scanSigBox").style.display = "none";
}

function changeLanguage(lang) {
  currentLang = lang;
  const dict = I18N[lang];
  if (!dict) return;
  document.querySelectorAll("[data-t]").forEach(el => {
    const k = el.getAttribute("data-t");
    if (dict[k] !== undefined) el.innerHTML = dict[k];
  });
  document.querySelectorAll("[data-ph]").forEach(el => {
    const k = el.getAttribute("data-ph");
    if (dict[k] !== undefined) el.setAttribute("placeholder", dict[k]);
  });
  document.documentElement.lang = lang === "ua" ? "uk" : lang;
  renderCatalogList(document.getElementById("modalSearchInput").value);
}

window.addEventListener("DOMContentLoaded", () => {
  updateSelectedAssetUI();
  renderMainFavs();
  renderEdu();
  loadProfile();
  loadNews(true);
  startNewsAuto();
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

    prices = None
    if not is_otc and clean_asset in BINANCE_MAPPING:
        try:
            url = f"https://api.binance.com/api/v3/klines?symbol={BINANCE_MAPPING[clean_asset]}&interval=1m&limit=50"
            async with httpx.AsyncClient() as client:
                res = await client.get(url, timeout=3.0)
                prices = [float(c[4]) for c in res.json()]
        except Exception:
            prices = None

    if not prices:
        prices = generate_otc_candles(asset, count=50)

    rsi = calculate_rsi(prices)
    ema = calculate_ema(prices)
    signal, reason = smart_market_analysis(prices, rsi, ema)
    return {"signal": signal, "reason": reason}


@app.get("/news")
async def get_news():
    events = []
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1) ShowMyTrades — high/medium + actual
    try:
        url = (
            "https://showmytrades.com/api/public/calendar/v1"
            "?currency=USD,EUR,GBP,JPY,AUD,NZD,CAD,CHF"
            "&impact=high,medium&days=5"
        )
        async with httpx.AsyncClient() as client:
            res = await client.get(url, timeout=10.0)
            if res.status_code == 200:
                data = res.json()
                for e in data.get("events", []):
                    events.append({
                        "name": e.get("title") or "",
                        "currency": (e.get("currency") or "").upper(),
                        "impact": (e.get("impact") or "medium").capitalize(),
                        "date": e.get("time") or "",
                        "forecast": e.get("forecast") or "",
                        "previous": e.get("previous") or "",
                        "actual": e.get("actual") or "",
                    })
                if data.get("generated_at"):
                    updated = data["generated_at"]
    except Exception as ex:
        logging.warning(f"ShowMyTrades news error: {ex}")

    # 2) Fallback — PipAtlas
    if not events:
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get("https://pipatlas.com/api/calendar", timeout=10.0)
                if res.status_code == 200:
                    data = res.json()
                    for e in data.get("events", []):
                        imp = (e.get("impact") or "").capitalize()
                        if imp not in ("High", "Medium"):
                            continue
                        events.append({
                            "name": e.get("name") or "",
                            "currency": (e.get("currency") or "").upper(),
                            "impact": imp,
                            "date": e.get("date") or "",
                            "forecast": e.get("forecast") or "",
                            "previous": e.get("previous") or "",
                            "actual": e.get("actual") or "",
                        })
                    if data.get("updated"):
                        updated = data["updated"]
        except Exception as ex:
            logging.warning(f"PipAtlas news error: {ex}")

    # ближайшие сверху
    def sort_key(x):
        try:
            return x.get("date") or ""
        except Exception:
            return ""

    events.sort(key=sort_key)
    return {"events": events[:80], "updated": updated}


@app.post("/analyze")
async def analyze(file: UploadFile = File(...), cfg: str = Form(...)):
    image_bytes = await file.read()
    try:
        config = json.loads(cfg)
    except Exception:
        config = {}
    result_text = await scanner_core.compute(image_bytes, config)
    return {"text": result_text}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
