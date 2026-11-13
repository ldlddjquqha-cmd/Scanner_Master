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
from PIL import Image
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="TEAM MASTER VIP", version="32.2")

# ===================== ENV =====================
OTCHARTS_API_KEY = os.environ.get("OTCHARTS_API_KEY", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# ===================== REAL OTC (otcharts.com) =====================
async def get_real_otc_candles(po_symbol: str, count: int = 50):
    if not OTCHARTS_API_KEY:
        logging.warning("OTCHARTS_API_KEY не задан")
        return None
    try:
        from otcharts import Client
        otc = Client(api_key=OTCHARTS_API_KEY)
        bars = otc.candles("otc", po_symbol, tf=60, limit=count)
        if bars and len(bars) >= 10:
            out = []
            for b in bars:
                out.append({
                    "o": round(float(getattr(b, "open", getattr(b, "o", 0))), 5),
                    "h": round(float(getattr(b, "high", getattr(b, "h", 0))), 5),
                    "l": round(float(getattr(b, "low", getattr(b, "l", 0))), 5),
                    "c": round(float(getattr(b, "close", getattr(b, "c", 0))), 5),
                })
            return out
    except ImportError:
        logging.info("otcharts не установлен, пробуем REST")
    except Exception as e:
        logging.warning(f"otcharts client: {e}")

    try:
        url = f"https://otcharts.com/v1/candles?venue=otc&symbol={po_symbol}&tf=60&limit={count}"
        headers = {"Authorization": f"Bearer {OTCHARTS_API_KEY}"}
        async with httpx.AsyncClient(timeout=12.0) as client:
            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                raw = data.get("candles") or data.get("bars") or data.get("data") or []
                out = []
                for c in raw:
                    if not isinstance(c, dict):
                        continue
                    o = float(c.get("open", c.get("o", 0)))
                    h = float(c.get("high", c.get("h", 0)))
                    l = float(c.get("low", c.get("l", 0)))
                    cl = float(c.get("close", c.get("c", 0)))
                    if cl > 0:
                        out.append({"o": round(o, 5), "h": round(h, 5), "l": round(l, 5), "c": round(cl, 5)})
                if len(out) >= 10:
                    return out
            else:
                logging.warning(f"otcharts REST {res.status_code}: {res.text[:180]}")
    except Exception as e:
        logging.warning(f"otcharts REST: {e}")
    return None

# ===================== NEWS =====================
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
    for a, b in [
        ("MoM", "м/м"), ("YoY", "г/г"), ("QoQ", "кв/кв"),
        ("m/m", "м/м"), ("y/y", "г/г"), ("q/q", "кв/кв"),
        ("(m/m)", "(м/м)"), ("(y/y)", "(г/г)"), ("(q/q)", "(кв/кв)"),
        ("Speech", "Выступление"), ("Decision", "Решение"),
        ("Prelim", "Предв."), ("Preliminary", "Предварительный"),
        ("Unemployment Claims", "Заявки по безработице"),
        ("Initial Jobless Claims", "Первичные заявки по безработице"),
        ("GDP", "ВВП"), ("CPI", "ИПЦ"), ("PPI", "ИЦП"),
    ]:
        out = out.replace(a, b)
    return re.sub(r"\s+", " ", out).strip()

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
        "text": f"📊 Сигнал запрошен\nАктив: {asset}\nТФ: {timeframe}\n{datetime.now().strftime('%H:%M:%S')}",
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
    return float(np.convolve(prices, weights, mode="valid")[-1])

def smart_market_analysis(prices, rsi, ema):
    current_price = prices[-1]
    prev_price = prices[-2] if len(prices) >= 2 else current_price
    trend_up = current_price > ema
    momentum_up = current_price > prev_price
    reasons = []
    if trend_up:
        reasons.append("Цена выше EMA — восходящий тренд.")
    else:
        reasons.append("Цена ниже EMA — нисходящий тренд.")
    if rsi > 65:
        reasons.append(f"RSI {round(rsi, 1)} — зона перекупленности.")
    elif rsi < 35:
        reasons.append(f"RSI {round(rsi, 1)} — зона перепроданности, возможен отскок.")
    else:
        reasons.append(f"RSI нейтральный ({round(rsi, 1)}).")
    if momentum_up:
        reasons.append("Последние свечи показывают давление покупателей.")
    else:
        reasons.append("Последние свечи показывают давление продавцов.")
    signal = "UP" if (trend_up and momentum_up) or (rsi < 40) else "DOWN"
    return signal, " ".join(reasons)

def generate_otc_ohlc(asset_name, count=50):
    seed_value = sum(ord(ch) for ch in asset_name) + int(time.time() / 90)
    state = random.Random(seed_value)
    if "Bitcoin" in asset_name or "BTC" in asset_name.upper():
        start = 65000.0
    elif "Ethereum" in asset_name or "ETH" in asset_name:
        start = 3500.0
    elif "Gold" in asset_name:
        start = 2650.0
    elif "Oil" in asset_name:
        start = 78.0
    else:
        start = 1.0850
    candles = []
    price = start
    for _ in range(count):
        change = state.uniform(-0.0022, 0.0022)
        open_p = price
        close_p = price * (1 + change)
        high_p = max(open_p, close_p) * (1 + state.uniform(0, 0.0012))
        low_p = min(open_p, close_p) * (1 - state.uniform(0, 0.0012))
        candles.append({
            "o": round(open_p, 5),
            "h": round(high_p, 5),
            "l": round(low_p, 5),
            "c": round(close_p, 5)
        })
        price = close_p
    return candles

class ScannerAnalyzer:
    async def compute(self, image_bytes: bytes, config: dict) -> str:
        try:
            image = Image.open(io.BytesIO(image_bytes))
            img_rgb = image.convert("RGB")
            np_img = np.array(img_rgb)
            r = np_img[:, :, 0].astype(float)
            g = np_img[:, :, 1].astype(float)
            b = np_img[:, :, 2].astype(float)
            green = int(np.sum((g > r + 30) & (g > b + 30)))
            red = int(np.sum((r > g + 30) & (r > b + 30)))
            total = np_img.shape[0] * np_img.shape[1]
            if (green + red) / total < 0.0015:
                return "❌ ГРАФИК СО СВЕЧАМИ НЕ ОБНАРУЖЕН."
            bullish = green >= red
            direction = "BUY (ВВЕРХ) 📈" if bullish else "SELL (ВНИЗ) 📉"
            trend = "Преобладание зелёных свечей — покупатели." if bullish else "Преобладание красных свечей — продавцы."
            return (
                "--- TEAM MASTER AI SCANNER ---\n"
                f"📊 Стратегия: {config.get('стратегия', 'Smart Money')}\n"
                f"⏱ ТФ: {config.get('интервал', 'M1')}\n"
                f"⏳ Экспирация: {config.get('экспирация', '1м')}\n"
                f"🔥 Вердикт: {direction}\n"
                f"📌 {trend}\n🚀 Сигнал готов!"
            )
        except Exception as e:
            return f"❌ Ошибка: {e}"

scanner_core = ScannerAnalyzer()

_BEST = [
    "🇦🇺🇺🇸 AUD/USD OTC",
    "🇪🇺🇺🇸 EUR/USD OTC",
    "🇬🇧🇺🇸 GBP/USD OTC",
    "🇺🇸🇯🇵 USD/JPY OTC",
    "₿ BITCOIN OTC",
]

_CURRENCY = [
    "🇦🇺🇺🇸 AUD/USD OTC", "🇪🇺🇺🇸 EUR/USD OTC", "🇬🇧🇺🇸 GBP/USD OTC", "🇺🇸🇯🇵 USD/JPY OTC",
    "🇪🇺🇯🇵 EUR/JPY OTC", "🇺🇸🇨🇦 USD/CAD OTC", "🇬🇧🇯🇵 GBP/JPY OTC", "🇳🇿🇺🇸 NZD/USD OTC",
    "🇺🇸🇨🇭 USD/CHF OTC", "🇪🇺🇬🇧 EUR/GBP OTC", "🇦🇺🇨🇭 AUD/CHF OTC", "🇦🇺🇺🇸 AUD/USD",
    "🇺🇸🇨🇦 USD/CAD", "🇺🇸🇯🇵 USD/JPY", "🇪🇺🇺🇸 EUR/USD", "🇬🇧🇺🇸 GBP/USD",
    "🇪🇺🇬🇧 EUR/GBP", "🇬🇧🇨🇭 GBP/CHF", "🇬🇧🇦🇺 GBP/AUD", "🇪🇺🇨🇭 EUR/CHF",
    "🇪🇺🇨🇦 EUR/CAD", "🇪🇺🇦🇺 EUR/AUD", "🇯🇵🇨🇦 CAD/JPY", "🇯🇵🇨🇭 CHF/JPY",
    "🇦🇺🇨🇦 AUD/CAD", "🇦🇺🇯🇵 AUD/JPY", "🇬🇧🇯🇵 GBP/JPY", "🇪🇺🇯🇵 EUR/JPY",
    "🇨🇦🇨🇭 CAD/CHF", "🇦🇺🇳🇿 AUD/NZD OTC", "🇨🇦🇨🇭 CAD/CHF OTC",
    "🇪🇺🇳🇿 EUR/NZD OTC", "🇬🇧🇦🇺 GBP/AUD OTC", "🇬🇧🇨🇭 GBP/CHF OTC",
    "🇬🇧🇨🇦 GBP/CAD OTC", "🇳🇿🇯🇵 NZD/JPY OTC", "🇺🇸🇷🇺 USD/RUB OTC", "🇺🇸🇷🇺 USD/RUB",
]

_CRYPTO = [
    "₿ BITCOIN OTC", "₿ Bitcoin", "Ξ Ethereum", "🪙 Solana", "🪙 BNB OTC",
    "🪙 Dogecoin OTC", "🪙 Chainlink OTC", "🪙 Toncoin OTC", "🪙 Avalanche OTC",
    "🪙 Cardano OTC", "🪙 Ethereum OTC", "🪙 Polkadot OTC", "🪙 Litecoin OTC",
    "🪙 TRON OTC", "🪙 Dash", "🪙 Chainlink",
]

_COMMODITIES = [
    "🛢️ Brent Oil OTC", "🛢️ WTI Crude Oil OTC", "🥈 Silver OTC", "🥇 Gold OTC",
    "⛽ Natural Gas OTC", "⚪ Palladium spot OTC", "🔘 Platinum spot",
]

_STOCKS = [
    "🍎 Apple OTC", "🚗 Tesla OTC", "💳 VISA OTC", "💻 Intel OTC", "🛒 Amazon OTC",
    "🎬 Netflix OTC", "🪟 Microsoft OTC", "🤖 Palantir Technologies OTC",
    "🪙 Coinbase Global OTC", "🌐 Cisco OTC", "⚡ Advanced Micro Devices OTC",
]

_INDICES = ["📊 US100 OTC", "📈 SP500", "📊 SP500 OTC", "🇦🇺 AUS 200"]

ASSETS_DATA_JS = {
    "ru": {
        "⭐ Лучшее": _BEST,
        "💱 Валютные пары": _CURRENCY,
        "🔥 Криптовалюты": _CRYPTO,
        "🛢️ Сырьевые товары": _COMMODITIES,
        "📈 Акции компаний": _STOCKS,
        "📊 Биржевые индексы": _INDICES,
    },
    "en": {
        "⭐ Best": _BEST,
        "💱 Currency Pairs": _CURRENCY,
        "🔥 Cryptocurrencies": _CRYPTO,
        "🛢️ Commodities": _COMMODITIES,
        "📈 Company Stocks": _STOCKS,
        "📊 Market Indices": _INDICES,
    },
    "ua": {
        "⭐ Найкраще": _BEST,
        "💱 Валютні пари": _CURRENCY,
        "🔥 Криптовалюти": _CRYPTO,
        "🛢️ Сировинні товари": _COMMODITIES,
        "📈 Акції компаній": _STOCKS,
        "📊 Біржові індекси": _INDICES,
    },
}

HTML_UI = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>TEAM MASTER VIP</title>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
:root{--gold:#FFD700;--gold-g:linear-gradient(135deg,#BF953F,#FCF6BA,#B38728,#FBF5B7,#AA771C);--bg:#04060a;--card:#0b0f17;--inner:#070a10;--green:#00E676;--red:#FF5252;--border:#161e2e;--border-g:rgba(212,175,55,.45);--muted:#7a8499;--text:#e8ecf4}
*{box-sizing:border-box;margin:0;padding:0;font-family:Montserrat,sans-serif;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--text);min-height:100vh;display:flex;justify-content:center;padding:10px 10px 90px}
.wrap{width:100%;max-width:440px;display:flex;flex-direction:column;gap:10px}
.g-text{background:var(--gold-g);background-size:200% auto;-webkit-background-clip:text;-webkit-text-fill-color:transparent;animation:sh 3s linear infinite;font-weight:900}
@keyframes sh{to{background-position:200% center}}
@keyframes fu{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.btn{width:100%;padding:13px;border:none;border-radius:12px;font-weight:800;font-size:12px;letter-spacing:.3px;text-transform:uppercase;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px;transition:transform .15s}
.btn:active{transform:scale(.97)}.btn:disabled{opacity:.4;pointer-events:none}
.btn-gold{background:var(--gold-g);background-size:200% auto;animation:sh 3s linear infinite;color:#111;box-shadow:0 5px 18px rgba(191,149,63,.3)}
.btn-green{background:linear-gradient(135deg,#00C853,#00E676);color:#063}
.btn-dark{background:var(--inner);color:var(--muted);border:1px solid var(--border)}
.btn-red{background:linear-gradient(135deg,#FF1744,#FF5252);color:#fff}
.btn-sm{padding:10px;font-size:11px}
.header{background:var(--card);border:1px solid var(--border-g);border-radius:14px;padding:14px 16px;display:flex;justify-content:space-between;align-items:center}
.header h1{font-size:15px}.header small{font-size:9px;color:var(--gold);font-weight:700}
.lang{background:var(--inner);color:#fff;border:1px solid var(--border);padding:5px 8px;border-radius:8px;font-weight:700;font-size:10px;outline:none}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px;box-shadow:0 3px 16px rgba(0,0,0,.35);animation:fu .3s ease}
.selected-asset-box{background:var(--inner);border:1.5px solid var(--gold);border-radius:12px;padding:10px 14px;margin-bottom:12px;display:flex;justify-content:space-between;align-items:center;cursor:pointer}
.selected-asset-info span:first-child{font-size:9px;color:var(--muted);display:block;font-weight:700}
.selected-asset-info span:last-child{font-size:13px;color:var(--gold);font-weight:900}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.sel{width:100%;padding:11px 12px;border-radius:10px;border:1px solid var(--border);background:var(--inner);color:#fff;font-size:12px;font-weight:700;outline:none;cursor:pointer}
.sig{display:none;margin-top:12px;background:var(--inner);border:2px solid var(--gold);border-radius:14px;padding:14px;text-align:center;animation:fu .3s ease}
.sig-meta{font-size:10px;color:var(--muted);font-weight:700}
.sig-dir{font-size:20px;font-weight:900;padding:12px;border-radius:11px;margin:8px 0}
.call{background:rgba(0,230,118,.12);color:var(--green);border:1px solid rgba(0,230,118,.35)}
.put{background:rgba(255,82,82,.12);color:var(--red);border:1px solid rgba(255,82,82,.35)}
.entry-box{background:rgba(255,215,0,.08);border:1px dashed var(--gold);border-radius:10px;padding:10px;margin:8px 0;font-size:13px;font-weight:800;color:var(--gold)}
.chart-wrap{background:#05070d;border-radius:10px;padding:8px;margin:10px 0;overflow:hidden}
#candleCanvas{width:100%;height:180px;display:block}
.strat{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:10px 12px;font-size:11px;color:#c5cdd8;text-align:left;line-height:1.55;margin-bottom:8px}
.timer{font-size:13px;font-weight:900;color:var(--gold);background:rgba(255,215,0,.06);border:1px dashed var(--gold);border-radius:10px;padding:8px;margin-bottom:8px}
.bnav{position:fixed;bottom:0;left:0;right:0;background:#05070d;border-top:1px solid var(--border);display:flex;justify-content:space-around;padding:8px 4px 12px;z-index:100}
.bnav button{background:none;border:none;color:var(--muted);font-size:10px;font-weight:700;display:flex;flex-direction:column;align-items:center;gap:3px;cursor:pointer;padding:4px 8px}
.bnav button.active{color:var(--gold)}
.page{display:none}.page.active{display:block;animation:fu .25s ease}
.modal-overlay{position:fixed;inset:0;background:rgba(4,6,10,.9);z-index:300;display:none;align-items:center;justify-content:center;padding:16px}
.modal-overlay.show{display:flex}
.modal-content{background:var(--card);border:1.5px solid var(--border-g);border-radius:16px;width:100%;max-width:410px;max-height:85vh;display:flex;flex-direction:column;padding:16px}
.modal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.modal-header h3{font-size:14px;color:var(--gold)}
.modal-close{background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer;font-weight:900}
.modal-search{width:100%;padding:10px 12px;border-radius:10px;border:1.5px solid var(--border-g);background:var(--inner);color:#fff;font-size:12px;font-weight:700;outline:none;margin-bottom:10px}
.cat-title{font-size:11px;color:var(--gold);font-weight:800;margin:10px 0 6px;padding-left:2px}
.asset-item{background:var(--inner);border:1px solid var(--border);border-radius:10px;padding:11px 13px;margin-bottom:6px;cursor:pointer;font-size:13px;font-weight:700;display:flex;justify-content:space-between;align-items:center}
.asset-item:active{background:rgba(255,215,0,.08)}
.news-item{background:var(--inner);border:1px solid var(--border);border-radius:14px;padding:14px;margin-bottom:10px;position:relative;overflow:hidden}
.news-item.high{border-left:4px solid var(--red);box-shadow:0 0 12px rgba(255,82,82,.08)}
.news-item.medium{border-left:4px solid #FFB300}
.news-top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:8px}
.news-name{font-size:13.5px;font-weight:800;color:#fff;line-height:1.35}
.news-impact{font-size:9px;font-weight:800;padding:4px 8px;border-radius:8px;white-space:nowrap}
.news-impact.high{background:rgba(255,82,82,.18);color:var(--red)}
.news-impact.medium{background:rgba(255,179,0,.18);color:#FFB300}
.news-meta{font-size:10.5px;color:var(--muted);display:flex;flex-wrap:wrap;gap:10px;margin-top:6px;align-items:center}
.news-status{font-size:10.5px;font-weight:700;padding:3px 8px;border-radius:6px}
.news-status.up{background:rgba(255,215,0,.1);color:var(--gold)}
.news-status.rel{background:rgba(0,230,118,.1);color:var(--green)}
.news-vals{display:flex;gap:12px;margin-top:8px;font-size:11px;flex-wrap:wrap}
.news-vals span{background:var(--card);padding:4px 8px;border-radius:6px;border:1px solid var(--border)}
.news-pair{margin-top:8px;font-size:10.5px;color:var(--gold);font-weight:700}
.session-box{background:var(--inner);border:1.5px solid var(--border-g);border-radius:14px;padding:14px;margin-top:12px}
.session-stats{display:flex;justify-content:space-around;text-align:center;margin:12px 0}
.session-stats div{font-size:11px;color:var(--muted)}
.session-stats strong{display:block;font-size:18px;font-weight:900;color:#fff;margin-top:2px}
.winrate-bar{height:10px;background:#1a2233;border-radius:6px;overflow:hidden;margin:8px 0}
.winrate-fill{height:100%;background:linear-gradient(90deg,#00C853,#00E676);border-radius:6px;transition:width .4s}
.session-btns{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}
.session-btns .btn{padding:12px;font-size:14px}
.lbl{display:block;font-size:9px;color:var(--muted);font-weight:800;margin-bottom:4px}
.live{font-size:8px;background:rgba(0,230,118,.12);color:var(--green);padding:3px 8px;border-radius:6px;border:1px solid rgba(0,230,118,.3);font-weight:800}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div>
      <h1 class="g-text">TEAM MASTER VIP</h1>
      <small id="headerSigCount">Сигналов: 0</small>
    </div>
    <select class="lang" onchange="changeLanguage(this.value)">
      <option value="ru">RU</option>
      <option value="en">EN</option>
      <option value="ua">UA</option>
    </select>
  </div>

  <div id="page-signal" class="page active">
    <div class="card">
      <div class="selected-asset-box" onclick="openAssetModal()">
        <div class="selected-asset-info">
          <span>Выбранный актив</span>
          <span id="selectedAssetText">🇪🇺🇺🇸 EUR/USD OTC</span>
        </div>
        <span style="color:var(--gold);font-size:18px">▾</span>
      </div>
      <div class="grid2" style="margin-bottom:10px">
        <div>
          <label class="lbl">Таймфрейм</label>
          <select class="sel" id="tfSelect">
            <option value="M1">M1</option>
            <option value="M2">M2</option>
            <option value="M5">M5</option>
          </select>
        </div>
        <div>
          <label class="lbl">Экспирация</label>
          <select class="sel" id="expSelect">
            <option value="60">1 мин</option>
            <option value="120">2 мин</option>
            <option value="180">3 мин</option>
            <option value="300">5 мин</option>
          </select>
        </div>
      </div>
      <button class="btn btn-gold" id="btnGetSignal" onclick="getSignal()">⚡ ПОЛУЧИТЬ СИГНАЛ</button>
      <div class="sig" id="sigBox">
        <div class="sig-meta" id="sigMeta">—</div>
        <div class="sig-dir call" id="sigDir">—</div>
        <div class="entry-box" id="entryBox">Точка входа: —</div>
        <div class="chart-wrap"><canvas id="candleCanvas" width="400" height="180"></canvas></div>
        <div class="timer" id="sigTimer">⏱ —</div>
        <div class="strat" id="sigReason">—</div>
        <button class="btn btn-dark btn-sm" onclick="document.getElementById('sigBox').style.display='none'">Закрыть</button>
      </div>
    </div>
  </div>

  <div id="page-news" class="page">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
        <h3 class="g-text" style="font-size:15px">📅 Экономический календарь</h3>
        <span class="live" id="newsUpdated">обновление...</span>
      </div>
      <div id="newsList">Загрузка...</div>
    </div>
  </div>

  <div id="page-profile" class="page">
    <div class="card">
      <div style="display:flex;align-items:center;gap:14px;margin-bottom:14px">
        <div style="width:56px;height:56px;border-radius:50%;background:var(--inner);border:2px solid var(--gold);display:flex;align-items:center;justify-content:center;font-size:24px">👤</div>
        <div>
          <div class="g-text" style="font-size:16px">Трейдер</div>
          <div style="font-size:11px;color:var(--muted);margin-top:2px">
            Винрейт: <strong id="profWinrate" style="color:var(--gold)">0%</strong>
            &nbsp;·&nbsp; +<span id="profWins">0</span> / −<span id="profLosses">0</span>
          </div>
        </div>
      </div>
      <div class="session-box">
        <div style="font-size:12px;font-weight:800;color:var(--gold);margin-bottom:4px">📊 Текущая сессия</div>
        <div class="session-stats">
          <div>Победы<br><strong id="sessWins" style="color:var(--green)">0</strong></div>
          <div>Поражения<br><strong id="sessLosses" style="color:var(--red)">0</strong></div>
          <div>Винрейт<br><strong id="sessWinrate">0%</strong></div>
        </div>
        <div class="winrate-bar"><div class="winrate-fill" id="winrateFill" style="width:0%"></div></div>
        <div class="session-btns">
          <button class="btn btn-green" onclick="sessionPlus()">＋ Победа</button>
          <button class="btn btn-red" onclick="sessionMinus()">− Поражение</button>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px">
          <button class="btn btn-dark btn-sm" onclick="startSession()">Начать сессию</button>
          <button class="btn btn-dark btn-sm" onclick="resetSession()">Сбросить</button>
        </div>
      </div>
      <div style="margin-top:14px;font-size:11px;color:var(--muted);text-align:center">
        Всего сигналов: <strong id="profSigCount" style="color:#fff">0</strong>
      </div>
    </div>
  </div>
</div>

<div class="bnav">
  <button class="active" onclick="showPage('signal',this)">⚡<span>Сигнал</span></button>
  <button onclick="showPage('news',this)">📅<span>Новости</span></button>
  <button onclick="showPage('profile',this)">👤<span>Профиль</span></button>
</div>

<div class="modal-overlay" id="assetModal">
  <div class="modal-content">
    <div class="modal-header">
      <h3>Выбор актива</h3>
      <button class="modal-close" onclick="closeAssetModal()">×</button>
    </div>
    <input class="modal-search" id="assetSearch" placeholder="Поиск актива..." oninput="renderAssets(this.value)">
    <div style="overflow-y:auto;flex:1" id="assetList"></div>
  </div>
</div>

<script>
var ASSETS = __ASSETS_JSON__;
var currentLang = "ru";
var selectedAsset = "🇪🇺🇺🇸 EUR/USD OTC";
var sigCount = parseInt(localStorage.getItem("tmv_sigs") || "0");
var sessWins = parseInt(localStorage.getItem("tmv_sess_w") || "0");
var sessLosses = parseInt(localStorage.getItem("tmv_sess_l") || "0");

function esc(s){ return String(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

function showPage(name, btn){
  document.querySelectorAll(".page").forEach(p=>p.classList.remove("active"));
  document.getElementById("page-"+name).classList.add("active");
  document.querySelectorAll(".bnav button").forEach(b=>b.classList.remove("active"));
  if(btn) btn.classList.add("active");
  if(name==="news") loadNews();
  if(name==="profile") updateProfileUI();
}

function openAssetModal(){ document.getElementById("assetModal").classList.add("show"); renderAssets(""); }
function closeAssetModal(){ document.getElementById("assetModal").classList.remove("show"); }

function renderAssets(q){
  q = (q||"").toLowerCase();
  var data = ASSETS[currentLang] || ASSETS.ru;
  var html = "";
  Object.keys(data).forEach(function(cat){
    var items = data[cat].filter(function(a){ return !q || a.toLowerCase().indexOf(q)>=0; });
    if(!items.length) return;
    html += '<div class="cat-title">'+esc(cat)+'</div>';
    items.forEach(function(a){
      html += '<div class="asset-item" onclick="selectAsset(\''+a.replace(/'/g,"\\'")+'\')">'+esc(a)+'<span style="color:var(--gold)">›</span></div>';
    });
  });
  document.getElementById("assetList").innerHTML = html || '<div style="color:var(--muted);padding:20px;text-align:center">Ничего не найдено</div>';
}

function selectAsset(a){
  selectedAsset = a;
  document.getElementById("selectedAssetText").textContent = a;
  closeAssetModal();
}

function changeLanguage(lang){
  currentLang = lang;
  renderAssets(document.getElementById("assetSearch").value||"");
}

function drawCandles(candles, signal){
  var canvas = document.getElementById("candleCanvas");
  var ctx = canvas.getContext("2d");
  var W = canvas.width, H = canvas.height;
  ctx.clearRect(0,0,W,H);
  ctx.fillStyle = "#05070d";
  ctx.fillRect(0,0,W,H);
  if(!candles || !candles.length) return;
  var n = candles.length;
  var highs = candles.map(c=>c.h), lows = candles.map(c=>c.l);
  var maxP = Math.max.apply(null, highs);
  var minP = Math.min.apply(null, lows);
  var range = maxP - minP || 1;
  var pad = 12;
  var chartH = H - pad*2;
  var candleW = Math.max(3, (W - 20) / n - 2);
  function y(p){ return pad + (maxP - p) / range * chartH; }
  ctx.strokeStyle = "#161e2e";
  ctx.lineWidth = 1;
  for(var i=0;i<5;i++){
    var gy = pad + (chartH/4)*i;
    ctx.beginPath(); ctx.moveTo(0,gy); ctx.lineTo(W,gy); ctx.stroke();
  }
  candles.forEach(function(c, i){
    var x = 10 + i * ((W-20)/n) + candleW/2;
    var openY = y(c.o), closeY = y(c.c), highY = y(c.h), lowY = y(c.l);
    var up = c.c >= c.o;
    ctx.strokeStyle = up ? "#00E676" : "#FF5252";
    ctx.fillStyle = up ? "#00E676" : "#FF5252";
    ctx.beginPath();
    ctx.moveTo(x, highY);
    ctx.lineTo(x, lowY);
    ctx.stroke();
    var bodyTop = Math.min(openY, closeY);
    var bodyH = Math.max(1, Math.abs(closeY - openY));
    ctx.fillRect(x - candleW/2, bodyTop, candleW, bodyH);
  });
  var last = candles[n-1];
  var lx = 10 + (n-1)*((W-20)/n) + candleW/2;
  var ly = y(last.c);
  ctx.beginPath();
  ctx.arc(lx, ly, 5, 0, Math.PI*2);
  ctx.fillStyle = "#FFD700";
  ctx.fill();
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.fillStyle = signal === "UP" ? "#00E676" : "#FF5252";
  ctx.beginPath();
  if(signal === "UP"){
    ctx.moveTo(lx, ly-18); ctx.lineTo(lx-6, ly-8); ctx.lineTo(lx+6, ly-8);
  } else {
    ctx.moveTo(lx, ly+18); ctx.lineTo(lx-6, ly+8); ctx.lineTo(lx+6, ly+8);
  }
  ctx.fill();
}

async function getSignal(){
  var btn = document.getElementById("btnGetSignal");
  btn.disabled = true;
  btn.textContent = "Анализ...";
  try{
    var tf = document.getElementById("tfSelect").value;
    var exp = parseInt(document.getElementById("expSelect").value) || 60;
    var url = "/get_signal?asset=" + encodeURIComponent(selectedAsset) + "&timeframe=" + encodeURIComponent(tf);
    var res = await fetch(url);
    var data = await res.json();
    var isUp = data.signal === "UP";
    var srcLabel = data.source === "pocket" ? "Pocket OTC (реал)" : data.source === "real" ? "Binance (реал)" : "OTC sim";
    document.getElementById("sigMeta").textContent = selectedAsset + " · " + tf + " · " + srcLabel;
    var dirEl = document.getElementById("sigDir");
    dirEl.textContent = isUp ? "CALL (ВВЕРХ) 📈" : "PUT (ВНИЗ) 📉";
    dirEl.className = "sig-dir " + (isUp ? "call" : "put");
    document.getElementById("entryBox").textContent = "📍 Точка входа: " + (data.entry || "—");
    document.getElementById("sigReason").textContent = data.reason || "";
    if(data.candles && data.candles.length) drawCandles(data.candles, data.signal);
    var left = exp;
    var tEl = document.getElementById("sigTimer");
    tEl.textContent = "⏱ " + left + " сек";
    if(window._sigTimer) clearInterval(window._sigTimer);
    window._sigTimer = setInterval(function(){
      left--;
      tEl.textContent = left > 0 ? "⏱ " + left + " сек" : "⏱ Время вышло";
      if(left <= 0) clearInterval(window._sigTimer);
    }, 1000);
    document.getElementById("sigBox").style.display = "block";
    sigCount++;
    localStorage.setItem("tmv_sigs", String(sigCount));
    document.getElementById("headerSigCount").textContent = "Сигналов: " + sigCount;
    document.getElementById("profSigCount").textContent = sigCount;
  }catch(e){
    alert("Ошибка получения сигнала");
  }
  btn.disabled = false;
  btn.textContent = "⚡ ПОЛУЧИТЬ СИГНАЛ";
}

async function loadNews(){
  var box = document.getElementById("newsList");
  box.innerHTML = '<div style="text-align:center;color:var(--muted);padding:24px">Загрузка календаря...</div>';
  try{
    var res = await fetch("/news");
    var data = await res.json();
    document.getElementById("newsUpdated").textContent = "обновлено " + new Date().toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"});
    if(!data.events || !data.events.length){
      box.innerHTML = '<div style="text-align:center;color:var(--muted);padding:24px">Нет важных событий</div>';
      return;
    }
    var html = "";
    data.events.slice(0,45).forEach(function(ev){
      var impClass = (ev.impact||"").toLowerCase() === "high" ? "high" : "medium";
      var stClass = ev.status === "upcoming" ? "up" : "rel";
      html += '<div class="news-item '+impClass+'">';
      html += '<div class="news-top"><div class="news-name">'+esc(ev.name)+'</div>';
      html += '<span class="news-impact '+impClass+'">'+esc(ev.impact_ru||ev.impact)+'</span></div>';
      html += '<div class="news-meta">';
      html += '<span style="font-weight:800;color:#fff">'+esc(ev.currency||"")+'</span>';
      if(ev.date){
        try{
          var d = new Date(ev.date);
          html += '<span>'+d.toLocaleString("ru-RU",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"})+'</span>';
        }catch(e){}
      }
      html += '<span class="news-status '+stClass+'">'+esc(ev.status_ru||"")+'</span>';
      html += '</div>';
      if(ev.forecast || ev.previous || ev.actual){
        html += '<div class="news-vals">';
        if(ev.previous) html += '<span>Пред: <b>'+esc(ev.previous)+'</b></span>';
        if(ev.forecast) html += '<span>Прогноз: <b>'+esc(ev.forecast)+'</b></span>';
        if(ev.actual) html += '<span style="color:var(--green)">Факт: <b>'+esc(ev.actual)+'</b></span>';
        html += '</div>';
      }
      if(ev.pair_hint) html += '<div class="news-pair">⚡ Влияние: '+esc(ev.pair_hint)+'</div>';
      html += '</div>';
    });
    box.innerHTML = html;
  }catch(e){
    box.innerHTML = '<div style="text-align:center;color:var(--red);padding:24px">Ошибка загрузки</div>';
  }
}

function updateProfileUI(){
  var total = sessWins + sessLosses;
  var wr = total ? Math.round(sessWins / total * 100) : 0;
  document.getElementById("sessWins").textContent = sessWins;
  document.getElementById("sessLosses").textContent = sessLosses;
  document.getElementById("sessWinrate").textContent = wr + "%";
  document.getElementById("winrateFill").style.width = wr + "%";
  document.getElementById("profWinrate").textContent = wr + "%";
  document.getElementById("profWins").textContent = sessWins;
  document.getElementById("profLosses").textContent = sessLosses;
  document.getElementById("profSigCount").textContent = sigCount;
  document.getElementById("headerSigCount").textContent = "Сигналов: " + sigCount;
}
function startSession(){
  sessWins = 0; sessLosses = 0;
  localStorage.setItem("tmv_sess_w","0");
  localStorage.setItem("tmv_sess_l","0");
  updateProfileUI();
}
function resetSession(){
  sessWins = 0; sessLosses = 0;
  localStorage.setItem("tmv_sess_w","0");
  localStorage.setItem("tmv_sess_l","0");
  updateProfileUI();
}
function sessionPlus(){
  sessWins++;
  localStorage.setItem("tmv_sess_w", String(sessWins));
  updateProfileUI();
}
function sessionMinus(){
  sessLosses++;
  localStorage.setItem("tmv_sess_l", String(sessLosses));
  updateProfileUI();
}
document.getElementById("headerSigCount").textContent = "Сигналов: " + sigCount;
updateProfileUI();
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def serve_terminal():
    assets_json = json.dumps(ASSETS_DATA_JS, ensure_ascii=False).replace("</", "<\\/")
    return HTMLResponse(HTML_UI.replace("__ASSETS_JSON__", assets_json))

@app.get("/get_signal")
async def get_signal(asset: str = "🇪🇺🇺🇸 EUR/USD OTC", timeframe: str = "M1"):
    asyncio.create_task(send_tg_signal_alert(asset, timeframe))
    await asyncio.sleep(0.35)

    candles = None
    source = "otc"
    prices = None

    po_symbol = None
    clean = re.sub(r"[^\x00-\x7F]+", "", asset).upper().replace("OTC", "").replace(" ", "").replace("/", "")
    if "AUDUSD" in clean or "AUD/USD" in asset:
        po_symbol = "AUDUSD_otc"
    elif "EURUSD" in clean or "EUR/USD" in asset:
        po_symbol = "EURUSD_otc"
    elif "GBPUSD" in clean or "GBP/USD" in asset:
        po_symbol = "GBPUSD_otc"
    elif "USDJPY" in clean or "USD/JPY" in asset:
        po_symbol = "USDJPY_otc"
    elif "BITCOIN" in clean or "BTC" in clean:
        po_symbol = "BTCUSD_otc"

    if po_symbol:
        real = await get_real_otc_candles(po_symbol, 50)
        if real:
            candles = real
            prices = [c["c"] for c in candles]
            source = "pocket"

    if not candles:
        is_otc = "OTC" in asset.upper()
        clean_asset = re.sub(r"[^\x00-\x7F]+", "", asset).replace("OTC", "").strip()
        for k in list(BINANCE_MAPPING.keys()):
            if k.lower() in clean_asset.lower() or clean_asset.lower() in k.lower():
                clean_asset = k
                break
        if not is_otc and clean_asset in BINANCE_MAPPING:
            try:
                symbol = BINANCE_MAPPING[clean_asset]
                url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit=50"
                async with httpx.AsyncClient() as client:
                    res = await client.get(url, timeout=5.0)
                    if res.status_code == 200:
                        raw = res.json()
                        candles = [{"o": float(c[1]), "h": float(c[2]), "l": float(c[3]), "c": float(c[4])} for c in raw]
                        prices = [c["c"] for c in candles]
                        source = "real"
            except Exception as e:
                logging.warning(f"Binance: {e}")

    if not candles:
        candles = generate_otc_ohlc(asset, 50)
        prices = [c["c"] for c in candles]
        source = "otc"

    rsi = calculate_rsi(prices)
    ema = calculate_ema(prices)
    signal, reason = smart_market_analysis(prices, rsi, ema)
    entry = round(prices[-1], 5) if prices else None

    return {
        "signal": signal,
        "reason": reason,
        "entry": entry,
        "candles": candles,
        "rsi": round(rsi, 1),
        "source": source,
    }

def _parse_event_dt(iso_str: str):
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
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
        url = "https://showmytrades.com/api/public/calendar/v1?currency=USD,EUR,GBP,JPY,AUD,NZD,CAD,CHF,RUB&impact=high,medium&days=3"
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
                        status, status_ru = "released", "✅ Уже вышло"
                    else:
                        status, status_ru = "upcoming", "⏳ Скоро выйдет"
                    if actual:
                        status, status_ru = "released", "✅ Уже вышло"
                    events.append({
                        "name": translate_news_title(raw_title),
                        "currency": (e.get("currency") or "").upper(),
                        "impact": imp,
                        "impact_ru": impact_ru(imp),
                        "date": dt_iso,
                        "forecast": e.get("forecast") or "",
                        "previous": e.get("previous") or "",
                        "actual": actual,
                        "status": status,
                        "status_ru": status_ru,
                        "pair_hint": PAIR_HINTS.get((e.get("currency") or "").upper(), ""),
                    })
                if data.get("generated_at"):
                    updated = data["generated_at"]
    except Exception as ex:
        logging.warning(f"News: {ex}")

    def _sort_key(ev):
        dt = _parse_event_dt(ev.get("date") or "")
        ts = dt.timestamp() if dt else 0
        is_up = 0 if ev.get("status") == "upcoming" else 1
        return (is_up, ts if is_up == 0 else -ts)

    events = [ev for ev in events if (ev.get("currency") or "") in MAJOR_FX]
    events.sort(key=_sort_key)
    return {"events": events[:60], "updated": updated}

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
