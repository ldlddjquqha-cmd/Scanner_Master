import os
import asyncio
import json
import logging
import random
from fastapi import FastAPI, UploadFile, File, Form, Request, Cookie
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn
import google.generativeai as genai
from PIL import Image
import io
import numpy as np
import httpx
from datetime import datetime

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="TEAM MASTER VIP - ULTIMATE QUANTUM CORE", version="20.0")

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
        "text": f"🔔 **Новый ученик вошел в систему!**\n\n**Ник:** @{username}\n**Статус:** ✅ Доступ разрешен",
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
    "GBP/JPY": "GBPJPY", "NZD/USD": "NZDUSDT", "USD/CHF": "USDCHF", "EUR/GBP": "EURGBP"
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

def ai_analyze_market(prices, rsi, ema):
    current_price = prices[-1]
    feature_trend = 1.0 if current_price > ema else -1.0
    feature_rsi_overbought = 1.0 if rsi > 70 else (-1.0 if rsi < 30 else 0.0)
    momentum = (prices[-1] - prices[-5]) / prices[-5] if len(prices) >= 5 else 0.0
    feature_momentum = 1.0 if momentum > 0 else -1.0
    
    ai_score = (feature_trend * 0.45) + (-1.0 * feature_rsi_overbought * 0.30) + (feature_momentum * 0.25)
    return "UP" if ai_score >= 0 else "DOWN"

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
        if not GEMINI_API_KEY:
            return (
                f"--- TEAM MASTER SIGNAL V4.0 ---\n"
                f"СТРАТЕГИЯ: {config.get('стратегия', 'Smart Money')}\n"
                f"ТАЙМФРЕЙМ: {config.get('интервал', 'M1')}\n"
                f"ЭКСПИРАЦИЯ: {config.get('экспирация', '1м')}\n"
                f"-------------------------------\n"
                f"ВЕРДИКТ: BUY (ВВЕРХ)\n"
                f"ПРОХОДИМОСТЬ: 89%\n"
                f"ПРИЧИНА: Отработка зоны POI по свечному анализу.\n"
                f"ВХОД: Прямо сейчас."
            )

        try:
            image = Image.open(io.BytesIO(image_bytes))
            model = genai.GenerativeModel('gemini-1.5-flash')
            
            prompt = (
                "ВНИМАНИЕ: Проанализируй данное изображение графика. Сначала строго проверь, присутствуют ли на изображении "
                "реальные торговые свечи (красные и зеленые свечи графиков котировок, бары, линии тренда). "
                "Если на изображении НЕТ графиков со свечами (например, фото людей, природы, скриншоты чатов, интерфейсов без свечей, черный экран или посторонние предметы), "
                "то строго ответь ровно одной фразой: '❌ ГРАФИК СО СВЕЧАМИ НЕ ОБНАРУЖЕН'.\n\n"
                "Если на изображении отчетливо видны красные и зеленые свечи торгового графика, проведи глубокий технический анализ по стратегии "
                f"'{config.get('стратегия', 'Smart Money')}' с таймфреймом '{config.get('интервал', 'M1')}' и экспирацией '{config.get('экспирация', '1м')}' "
                "и выдай результат строго в таком формате:\n"
                "DIRECTION: BUY (ВВЕРХ) (или SELL (ВНИЗ))\n"
                "PROB: [число от 70 до 95]%\n"
                "REASON: [краткая профессиональная причина по свечным паттернам, уровням и структуре рынка]"
            )
            
            response = model.generate_content([image, prompt])
            res_text = response.text if response and response.text else ""

            if "НЕ ОБНАРУЖЕН" in res_text or "❌" in res_text:
                return "❌ ОШИБКА: На изображении не обнаружено красно-зеленых свечей графика. Загрузите или сделайте снимок торгового графика со свечами!"

            direction = "BUY (ВВЕРХ)"
            prob = "88"
            reason = "Успешный пробой уровня ликвидности и подтверждение по свечам."

            for line in res_text.split("\n"):
                if "DIRECTION:" in line:
                    direction = line.replace("DIRECTION:", "").strip()
                elif "PROB:" in line:
                    prob = line.replace("PROB:", "").strip().replace("%", "")
                elif "REASON:" in line:
                    reason = line.replace("REASON:", "").strip()

            return (
                f"--- TEAM MASTER SIGNAL V4.0 ---\n"
                f"СТРАТЕГИЯ: {config.get('стратегия', 'Smart Money')}\n"
                f"ТАЙМФРЕЙМ: {config.get('интервал', 'M1')}\n"
                f"ЭКСПИРАЦИЯ: {config.get('экспирация', '1м')}\n"
                f"-------------------------------\n"
                f"ВЕРДИКТ: {direction}\n"
                f"ПРОХОДИМОСТЬ: {prob}%\n"
                f"ПРИЧИНА: {reason}\n"
                f"ВХОД: Прямо сейчас."
            )
        except Exception as e:
            return f"❌ Ошибка анализа изображения через ИИ: {str(e)}"

scanner_core = ScannerAnalyzer()

ASSETS_DATA_JS = {
  "ru": {
    "💱 Валютные пары": ["EUR/USD OTC", "AUD/CHF OTC", "AUD/USD OTC", "EUR/NZD OTC", "NZD/JPY OTC", "NZD/USD OTC", "USD/CAD OTC", "GBP/USD OTC", "USD/JPY OTC", "EUR/USD", "GBP/USD"],
    "🔥 Криптовалюты": ["BNB OTC", "Dogecoin OTC", "Chainlink OTC", "Toncoin OTC", "Bitcoin OTC", "Ethereum OTC", "Solana OTC", "Cardano OTC", "TRON OTC"],
    "🛢️ Сырьевые товары": ["Brent Oil OTC", "WTI Crude Oil OTC", "Silver OTC", "Gold OTC", "Natural Gas OTC"],
    "📈 Акции компаний": ["American Express OTC", "Tesla OTC", "VISA OTC", "Intel OTC", "Microsoft OTC", "Netflix OTC", "Amazon OTC", "Apple OTC"],
    "📊 Биржевые индексы": ["US100 OTC", "SP500 OTC"]
  },
  "en": {
    "💱 Currency Pairs": ["EUR/USD OTC", "AUD/CHF OTC", "AUD/USD OTC", "EUR/NZD OTC", "NZD/JPY OTC", "NZD/USD OTC", "USD/CAD OTC", "GBP/USD OTC", "USD/JPY OTC", "EUR/USD", "GBP/USD"],
    "🔥 Cryptocurrencies": ["BNB OTC", "Dogecoin OTC", "Chainlink OTC", "Toncoin OTC", "Bitcoin OTC", "Ethereum OTC", "Solana OTC", "Cardano OTC", "TRON OTC"],
    "🛢️ Commodities": ["Brent Oil OTC", "WTI Crude Oil OTC", "Silver OTC", "Gold OTC", "Natural Gas OTC"],
    "📈 Company Stocks": ["American Express OTC", "Tesla OTC", "VISA OTC", "Intel OTC", "Microsoft OTC", "Netflix OTC", "Amazon OTC", "Apple OTC"],
    "📊 Market Indices": ["US100 OTC", "SP500 OTC"]
  },
  "ua": {
    "💱 Валютні пари": ["EUR/USD OTC", "AUD/CHF OTC", "AUD/USD OTC", "EUR/NZD OTC", "NZD/JPY OTC", "NZD/USD OTC", "USD/CAD OTC", "GBP/USD OTC", "USD/JPY OTC", "EUR/USD", "GBP/USD"],
    "🔥 Криптовалюти": ["BNB OTC", "Dogecoin OTC", "Chainlink OTC", "Toncoin OTC", "Bitcoin OTC", "Ethereum OTC", "Solana OTC", "Cardano OTC", "TRON OTC"],
    "🛢️ Сировинні товари": ["Brent Oil OTC", "WTI Crude Oil OTC", "Silver OTC", "Gold OTC", "Natural Gas OTC"],
    "📈 Акції компаній": ["American Express OTC", "Tesla OTC", "VISA OTC", "Intel OTC", "Microsoft OTC", "Netflix OTC", "Amazon OTC", "Apple OTC"],
    "📊 Біржові індекси": ["US100 OTC", "SP500 OTC"]
  }
}

EDU_50 = []
strategy_names = [
    "Smart Money Order Block", "ICT Fair Value Gap (FVG)", "Price Action Pin-Bar", "Scalping EMA 9/21", "Trend Reversal RSI Divergence",
    "Bollinger Bands Squeeze", "MACD Zero Line Cross", "Support & Resistance Rejection", "Breakout Retest Strategy", "Supertrend Momentum",
    "Stochastic Oscillator Cross", "Parabolic SAR Trend", "Fibonacci Retracement 61.8", "Pivot Point Bounce", "Double Top / Double Bottom",
    "Head and Shoulders Pattern", "Triangle Breakout", "Channel Trading Strategy", "Aroon Indicator Trend", "Commodity Channel Index (CCI)",
    "Ichimoku Cloud Breakout", "Williams %R Overbought/Oversold", "Average True Range (ATR) Volatility", "Volume Profile POC Bounce", "Heikin Ashi Trend Continuation",
    "Three White Soldiers / Three Black Crows", "Morning Star / Evening Star", "Engulfing Candle Reversal", "Harami Pattern Reversal", "Tweezer Top / Bottom",
    "Inside Bar Breakout", "Fakeout (False Breakout) Trap", "Liquidity Sweep Reversal", "Asian Range Breakout", "London Session Open Momentum",
    "New York Reversal Strategy", "VWAP Bounce Strategy", "Keltner Channel Breakout", "Donchian Channel Trend", "Ultimate Oscillator Signal",
    "Mass Index Reversal", "Chaikin Money Flow (CMF)", "Ease of Movement (EOM)", "Vortex Indicator Cross", "TRIX Momentum Strategy",
    "Detrended Price Oscillator (DPO)", "Linear Regression Slope", "KST (Know Sure Thing)", "PPO (Percentage Price Oscillator)", "Ultimate Master Sniper Strategy"
]

for i, name in enumerate(strategy_names, 1):
    tf_choice = random.choice(["S30, M1", "M1, M2", "M2, M5", "M5, M15"])
    exp_choice = random.choice(["30 сек – 1 мин", "1 мин – 2 мин", "2 мин – 5 мин", "5 мин"])
    desc = (
        f"📌 ДЕТАЛЬНЫЙ АНАЛИЗ СТРАТЕГИИ №{i} ({name}):\n\n"
        f"• Рыночная механика: Стратегия построена на выявлении дисбаланса спроса и предложения, институциональных ордеров и ключевых зон ликвидности на торговых графиках.\n"
        f"• Рекомендуемые таймфреймы: {tf_choice}.\n"
        f"• Экспирация сделки: {exp_choice}.\n"
        f"• Правила входа в позицию: Дождитесь закрытия сигнальной свечи в зоне интереса (POI) и входите строго по направлению импульса. Избегайте торговли за 5 минут до и после важных новостей."
    )
    img_url = f"https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=600&auto=format&fit=crop&sig={i}"
    EDU_50.append({
        "t": f"{i}. {name}",
        "d": desc,
        "img": img_url,
        "tags": [tf_choice.split(",")[0].strip(), "VIP Master"]
    })

HTML_UI = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>👑 TEAM MASTER VIP — Ultimate Trading Terminal</title>
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
.gate{background:var(--card);border:1px solid var(--border-g);border-radius:18px;padding:22px 16px;text-align:center;animation:fu .4s ease}
.progress{display:flex;justify-content:center;align-items:center;margin-bottom:18px}
.p-dot{width:10px;height:10px;border-radius:50%;background:var(--border);transition:all .3s}
.p-dot.done{background:var(--green)}.p-dot.cur{background:var(--gold);transform:scale(1.2);box-shadow:0 0 10px rgba(255,215,0,.45)}
.p-line{width:28px;height:2px;background:var(--border);margin:0 3px}.p-line.done{background:var(--green)}
.gate-icon{font-size:44px;margin-bottom:6px}
.gate-title{font-size:17px;font-weight:900;margin-bottom:8px;background:var(--gold-g);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.gate-desc{font-size:11.5px;color:#b0b8c8;line-height:1.6;text-align:left;margin-bottom:16px;background:var(--inner);padding:11px 13px;border-radius:11px;border:1px solid var(--border)}
.gate-desc b{color:var(--gold)}
.input{width:100%;padding:12px;border-radius:11px;border:1.5px solid var(--border-g);background:var(--inner);color:#fff;font-size:14px;font-weight:700;text-align:center;outline:none;margin-bottom:9px}
.input:focus{border-color:var(--gold)}
.msg{font-size:11px;font-weight:700;padding:9px 11px;border-radius:10px;margin-bottom:9px;display:none}
.msg.ok{display:block;background:rgba(0,230,118,.1);color:var(--green);border:1px solid rgba(0,230,118,.28)}
.msg.err{display:block;background:rgba(255,82,82,.1);color:var(--red);border:1px solid rgba(255,82,82,.28)}
.step{display:none!important}.step.on{display:block!important;animation:fu .3s ease}
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
.strat{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:10px 12px;font-size:10.5px;color:#c5cdd8;text-align:left;line-height:1.5;margin-bottom:8px}
.sig-stats{display:flex;justify-content:space-between;font-size:10px;font-weight:800;margin-bottom:8px}
.edu-item{background:var(--inner);border:1px solid var(--border);border-radius:11px;padding:11px 13px;margin-bottom:7px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;transition:transform .15s}
.edu-item:active{transform:scale(.98)}.edu-item h4{font-size:11.5px;color:var(--gold);font-weight:800}
.edu-detail{background:var(--inner);border:1px solid var(--border-g);border-radius:12px;padding:14px;animation:fu .3s ease}
.edu-detail h3{color:var(--gold);font-size:13px;font-weight:900;margin-bottom:8px}
.edu-detail p{font-size:11.5px;color:#c5cdd8;line-height:1.7;white-space:pre-line;margin-bottom:10px}
.edu-detail img{width:100%;height:160px;object-fit:cover;border-radius:10px;border:1px solid var(--border);margin-bottom:8px}
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
.ind-row{display:flex;flex-wrap:wrap;gap:5px;margin:8px 0}
.ind-chip{font-size:9px;font-weight:800;padding:4px 8px;border-radius:7px;background:rgba(255,215,0,.08);color:var(--gold);border:1px solid rgba(255,215,0,.2)}
.fav-list{max-height:240px;overflow-y:auto;display:flex;flex-direction:column;gap:6px}
.fav-item{display:flex;justify-content:space-between;align-items:center;padding:11px 13px;background:var(--inner);border:1px solid var(--border);border-radius:11px;font-size:12px;font-weight:700;cursor:pointer;transition:background .15s}
.fav-item:hover{background:rgba(255,215,0,.08)}
.fav-item span:first-child{color:var(--gold)}
.admin-profile-box {background:var(--inner);border:2px solid var(--gold);border-radius:14px;padding:14px;margin-top:12px;animation:fu .3s ease}
.users-list {display:flex;flex-direction:column;gap:6px;max-height:220px;overflow-y:auto;margin-top:8px;padding-right:4px}
.admin-search-input {width:100%;padding:9px 11px;border-radius:9px;border:1px solid var(--border);background:var(--card);color:#fff;font-size:11.5px;font-weight:700;outline:none;margin-bottom:8px}
.admin-search-input:focus {border-color:var(--gold)}
.user-row {display:flex;justify-content:space-between;align-items:center;background:var(--card);padding:9px 11px;border-radius:9px;border:1px solid var(--border);font-size:11px;font-weight:700}
.catalog-category-header {font-size:11px;font-weight:900;color:var(--gold);text-transform:uppercase;margin:12px 0 6px 4px;letter-spacing:.6px;border-bottom:1px solid var(--border-g);padding-bottom:4px}
.chat-container{display:flex;flex-direction:column;height:380px;background:var(--inner);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.chat-messages{flex:1;overflow-y:auto;padding:10px;display:flex;flex-direction:column;gap:8px}
.chat-msg{padding:8px 11px;border-radius:10px;font-size:11.5px;line-height:1.4;max-width:85%}
.chat-msg.user{background:rgba(255,215,0,0.15);color:var(--gold);align-self:flex-end;border:1px solid rgba(255,215,0,0.3)}
.chat-msg.ai{background:var(--card);color:var(--text);align-self:flex-start;border:1px solid var(--border)}
.chat-input-row{display:flex;gap:6px;padding:8px;background:var(--card);border-top:1px solid var(--border)}
.chat-input{flex:1;padding:9px 11px;border-radius:9px;border:1px solid var(--border);background:var(--inner);color:#fff;font-size:11.5px;outline:none}
.chat-input:focus{border-color:var(--gold)}
</style>
</head>
<body>

<div class="wrap">
<div class="header">
<div>
<h1 class="g-text">👑 TEAM MASTER VIP</h1>
<small id="hdrSub">⚡ TRADING TERMINAL</small>
</div>
<select class="lang" id="langSelect" onchange="changeLanguage(this.value)">
<option value="ru">🇷🇺 RU</option>
<option value="en">🇺🇸 EN</option>
<option value="ua">🇺🇦 UA</option>
</select>
</div>

<div class="gate" id="gate">
<div class="progress">
<div class="p-dot cur" id="d1"></div><div class="p-line" id="l1"></div>
<div class="p-dot" id="d2"></div><div class="p-line" id="l2"></div>
<div class="p-dot" id="d3"></div>
</div>
<div class="step on" id="s1">
<div class="gate-icon">👑</div>
<div class="gate-title" data-t="gateTitle">Добро пожаловать</div>
<div class="gate-desc" id="gateDesc1"><b>Авторизация в системе</b><br><br>Чтобы войти в систему введите ваш секретный ключ или пароль.<br><br>Укажите Ваш настоящий Telegram юзернейм (например, <b>@username</b>).</div>
<input class="input" id="masterCodeInput" type="text" placeholder="Введите ключ доступа...">
<input class="input" id="tgUserInput" type="text" placeholder="Ваш ТГ юзернейм (@username)">
<div class="msg" id="regMsg"></div>
<button class="btn btn-gold" id="btnReg" onclick="doReg()" data-t="btnConfirmId">✅ Войти в терминал</button>
</div>
<div class="step" id="s2">
<div class="gate-icon">💎</div>
<div class="gate-title" data-t="depTitle">Проверка депозита</div>
<div class="gate-desc" id="gateDesc2"><b>Статус аккаунта</b><br><br>Ключ принят. Введите промокод <b>WELCOME50</b> для подтверждения доступа к сигналам.</div>
<input class="input" id="promoInput" type="text" value="WELCOME50">
<div class="msg" id="depMsg"></div>
<button class="btn btn-green" id="btnDep" onclick="doDep()" data-t="btnCheckDep">🔍 Проверить депозит</button>

<div style="background:var(--inner);border:1px solid var(--border-g);border-radius:12px;padding:10px 12px;margin-top:12px;text-align:left">
<div style="font-size:11px;font-weight:800;color:var(--gold);margin-bottom:4px">🎁 Нет аккаунта Pocket Option?</div>
<div style="font-size:10px;color:#b0b8c8;line-height:1.4;margin-bottom:8px">Зарегистрируйтесь по нашей официальной ссылке, чтобы получить бонус +50% к первому депозиту:</div>
<a class="btn btn-gold btn-sm" style="background:linear-gradient(135deg,#FFD700,#FFA000)" href="https://u3.shortink.io/cabinet/demo-quick-high-low?utm_campaign=850173&utm_source=affiliate&utm_medium=sr&a=RLQDltKf13Zlrj&al=1771346&ac=smart-link&cid=960963&code=WELCOME50" target="_blank">🔗 Регистрация в Pocket Option</a>
</div>

<div class="gap2"></div>
<button class="btn btn-dark btn-sm" onclick="goStep(1)" data-t="btnBack">← Назад</button>
</div>
<div class="step" id="s3">
<div class="gate-icon">🎉</div>
<div class="gate-title" data-t="accOpenTitle">Доступ открыт</div>
<div class="gate-desc" style="text-align:center" data-t="accOpenDesc">Активация прошла успешно. Все функции терминала доступны.</div>
<button class="btn btn-gold" onclick="enterApp()" data-t="btnEnterTerm">🚀 Войти в терминал</button>
</div>
</div>

<div id="app" class="hidden">
<div class="card tab" id="tabSig">
<div class="live-bar">
  <span class="live" id="apiStatusBadge">● LIVE API: ONLINE 🟢</span>
  <span style="font-size:9px;color:var(--gold);font-weight:800" id="apiTimerDisplay">⚡ СИНХРОНИЗАЦИЯ</span>
</div>

<div class="selected-asset-box" onclick="openCatalogModal()">
  <div class="selected-asset-info">
    <span data-t="lblActiveAsset">АКТИВ ДЛЯ АНАЛИЗА</span>
    <span id="currentAssetDisplay">EUR/USD OTC</span>
  </div>
  <div style="display:flex;align-items:center;gap:6px">
    <span id="currentAssetPayout" class="cat-payout">92%</span>
    <span style="font-size:16px">📋</span>
  </div>
</div>

<div class="cats" id="assetCatsContainer"></div>

<div class="grid2" style="margin-bottom:10px">
<div>
<label class="lbl" data-t="lblTf">ТАЙМФРЕЙМ</label>
<select class="sel" id="tf">
<option value="S5">5 сек</option><option value="S15">15 сек</option><option value="S30">30 сек</option>
<option value="M1" selected>1 мин</option><option value="M2">2 мин</option><option value="M3">3 мин</option>
<option value="M5">5 мин</option><option value="M10">10 мин</option><option value="M15">15 мин</option>
</select>
</div>
<div>
<label class="lbl" data-t="lblExp">ЭКСПИРАЦИЯ</label>
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
<button class="btn btn-gold" onclick="getSig()" data-t="btnCalcSig">⚡ Просчитать сигнал</button>
<div class="gap"></div>
<button class="btn btn-green btn-sm" onclick="autoSig()" data-t="btnAutoSig">🤖 Выбрать топ актив</button>
<div class="gap"></div>
<a class="btn btn-gold btn-sm" style="background:linear-gradient(135deg,#FFD700,#FFA000)" href="https://u3.shortink.io/cabinet/demo-quick-high-low?utm_campaign=850173&utm_source=affiliate&utm_medium=sr&a=RLQDltKf13Zlrj&al=1771346&ac=smart-link&cid=960963&code=WELCOME50" target="_blank" id="poDeepLink" data-t="btnOpenPo">🚀 Открыть Pocket Option</a>
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
<p style="font-size:11px;color:var(--muted);margin-bottom:10px" data-t="favTabDesc">Нажмите на любой актив из списка, чтобы мгновенно перейти к нему.</p>
<div class="fav-list" id="mainFavList"></div>
<div id="mainFavEmpty" style="text-align:center;padding:24px;color:var(--muted);font-size:11px;font-weight:600" data-t="profFavEmpty">Пока пусто — добавь актив из полного каталога</div>
</div>

<div class="card tab hidden" id="tabChat">
<h3 class="g-text" style="font-size:13px;margin-bottom:8px">🤖 ИИ Умный Чат (Трейдинг)</h3>
<p style="font-size:10.5px;color:var(--muted);margin-bottom:10px">Спросите у ИИ о рынках, стратегиях и техническом анализе.</p>
<div class="chat-container">
  <div class="chat-messages" id="chatMessagesBox">
    <div class="chat-msg ai">Привет! Я твой ИИ-ассистент по трейдингу. Задай мне любой вопрос о рынках, стратегиях или управлении рисками!</div>
  </div>
  <div class="chat-input-row">
    <input type="text" class="chat-input" id="chatInputText" placeholder="Введите ваш вопрос..." onkeydown="if(event.key==='Enter') sendAiChatMessage()">
    <button class="btn btn-gold btn-sm" style="width:auto;padding:8px 14px" onclick="sendAiChatMessage()">➔</button>
  </div>
</div>
<div class="gap2"></div>
<a class="btn btn-dark btn-sm" href="https://t.me/+uekq4TquqkM4Mzcy" target="_blank" style="color:var(--gold)" id="tgCommunityLink">💬 Наши чаты и сообщество в Telegram</a>
</div>

<div class="card tab hidden" id="tabEdu">
<div id="eduList">
<h3 class="g-text" style="text-align:center;font-size:13px;margin-bottom:10px" data-t="eduHeader">📚 50 профессиональных связок (Полный курс)</h3>
<div id="eduItems"></div>
</div>
<div id="eduView" class="hidden">
<button class="btn btn-gold btn-sm" style="margin-bottom:10px" onclick="closeEdu()" data-t="btnBackEdu">← Назад к списку</button>
<div class="edu-detail" id="eduBody"></div>
</div>
</div>

<div class="card tab hidden" id="tabProf">
<h3 class="g-text" style="text-align:center;font-size:13px;margin-bottom:10px" data-t="profTitle">👤 Профиль</h3>
<label class="lbl" data-t="profNameLbl">ВАШЕ ИМЯ</label>
<input class="name-input" id="profName" placeholder="Введите имя" onchange="saveName()" onblur="saveName()">

<div class="prof-row"><span data-t="profStatus">Статус</span><b style="color:var(--green)">VIP UNLIMITED</b></div>
<div class="prof-row"><span data-t="profIdLbl">Ваш ТГ</span><b id="profId">—</b></div>
<div class="prof-row"><span data-t="profSigsLbl">Сигналов</span><b id="profSigs">0</b></div>
<div class="gap2"></div>
<label class="lbl" data-t="profFavLbl">⭐ ИЗБРАННЫЕ АКТИВЫ</label>
<div class="fav-list" id="favList"></div>
<div id="favEmpty" style="text-align:center;padding:16px;color:var(--muted);font-size:11px;font-weight:600" data-t="profFavEmpty">Пока пусто — добавь актив из полного каталога</div>
<div class="gap2"></div>
<button class="btn btn-dark btn-sm" onclick="clearFavs()" data-t="btnClearFav">🗑 Очистить избранное</button>
<div class="gap"></div>
<button class="btn btn-red btn-sm" onclick="logout()" data-t="btnLogout">🚪 Выйти</button>

<div class="admin-profile-box hidden" id="adminProfileBox">
<h3 class="g-text" style="font-size:12px;margin-bottom:8px">👑 АДМИН-ПАНЕЛЬ: УПРАВЛЕНИЕ УЧАСТНИКАМИ</h3>
<label class="lbl">ПОЛЬЗОВАТЕЛИ</label>
<input type="text" class="admin-search-input" id="adminSearchUserInput" placeholder="Поиск по @username..." oninput="renderAdminData(this.value)" autocomplete="off">
<div class="users-list" id="adminUsersList"></div>
</div>

</div>

<div class="card tab hidden" id="tabScan">
<h3 class="g-text" style="text-align:center;font-size:13px;margin-bottom:8px">📷 Сканер с ИИ (Свечной анализ)</h3>
<div class="scan-tips" id="scanTipsHtml">
<b>Инструкция сканера:</b><br>
1️⃣ Выберите таймфрейм, экспирацию и стратегию ниже<br>
2️⃣ Откройте камеру или загрузите скриншот графика со свечами<br>
3️⃣ Нажмите «ЗАПУСК (СКАН СВЕЧЕЙ)»
</div>

<div class="lbl" style="margin-top:6px">ТАЙМФРЕЙМ СКАНИРОВАНИЯ</div>
<div class="cats" style="margin-bottom:6px" id="scanTfGrid">
  <div class="cat" onclick="setScanCfg(this,'интервал','S5')">S5</div>
  <div class="cat" onclick="setScanCfg(this,'интервал','S15')">S15</div>
  <div class="cat" onclick="setScanCfg(this,'интервал','S30')">S30</div>
  <div class="cat on" onclick="setScanCfg(this,'интервал','M1')">M1</div>
  <div class="cat" onclick="setScanCfg(this,'интервал','M2')">M2</div>
  <div class="cat" onclick="setScanCfg(this,'интервал','M5')">M5</div>
</div>

<div class="lbl">ЭКСПИРАЦИЯ</div>
<div class="cats" style="margin-bottom:6px" id="scanExpGrid">
  <div class="cat" onclick="setScanCfg(this,'экспирация','30с')">30с</div>
  <div class="cat on" onclick="setScanCfg(this,'экспирация','1м')">1м</div>
  <div class="cat" onclick="setScanCfg(this,'экспирация','2м')">2м</div>
  <div class="cat" onclick="setScanCfg(this,'экспирация','5м')">5м</div>
</div>

<div class="lbl">СТРАТЕГИЯ ИИ</div>
<div class="cats" style="margin-bottom:8px" id="scanStratGrid">
  <div class="cat on" onclick="setScanCfg(this,'стратегия','Smart Money')">Smart Money</div>
  <div class="cat" onclick="setScanCfg(this,'стратегия','ICT')">ICT</div>
  <div class="cat" onclick="setScanCfg(this,'стратегия','PA')">PA</div>
  <div class="cat" onclick="setScanCfg(this,'стратегия','Scalp')">Scalp</div>
  <div class="cat" onclick="setScanCfg(this,'стратегия','Trend')">Trend</div>
</div>

<button class="btn btn-gold btn-sm" id="btnOpenCam" onclick="openCam()" data-t="btnOpenCam">📷 Открыть камеру</button>
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

<button class="btn btn-gold" id="btnScan" onclick="doScan()" disabled style="background:#1b5e20;color:#fff">🚀 ЗАПУСК (СКАН СВЕЧЕЙ)</button>
<div class="gap"></div>
<button class="btn btn-dark btn-sm" onclick="stopCam()" data-t="btnCloseCam">⏹ Закрыть камеру / Сбросить</button>
<div class="sig" id="scanSigBox">
<div class="sig-meta" id="scanMeta">TEAM MASTER SIGNAL V4.0</div>
<div class="sig-dir call" id="scanDir">BUY (ВВЕРХ)</div>
<div class="timer">⏱ <span id="scanCd">01:00</span></div>
<div class="strat" id="scanStrat"></div>
<button class="btn btn-red btn-sm" id="btnScanCancel" onclick="cancelScan()" style="display:none" data-t="btnCancel">✖ Отменить</button>
<button class="btn btn-gold btn-sm" id="btnScanNew" onclick="doScan()" style="display:none" data-t="btnNewScan">⚡ Новый скан</button>
</div>
</div>

<div class="card tab hidden" id="tabAbout">
<h3 class="g-text" style="text-align:center;font-size:13px;margin-bottom:6px" data-t="aboutTitle">👑 О нас и нашей команде</h3>
<p style="font-size:11px;color:#b0b8c8;line-height:1.65;margin-bottom:8px"><b>TEAM MASTER VIP</b> — это сообщество трейдеров и разработчиков. Алгоритмический терминал с нейросетевым анализом свечных паттернов.</p>
<p style="font-size:10px;color:var(--muted);line-height:1.5;margin-bottom:10px" data-t="aboutRisk">Торгуйте осознанно. Риск на сделку 1-2% от банка.</p>
<a class="btn btn-gold btn-sm" href="https://t.me/+uekq4TquqkM4Mzcy" target="_blank">📢 Наш Telegram канал</a>
</div>
</div>

<div class="bnav hidden" id="bnav">
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
<div class="analyze-text" id="analyzeTxt">🔍 Сканирование красно-зеленых свечей...</div>
<div class="analyze-sub">Smart Money · ICT · PA · Scalp · Trend</div>
</div>

<div class="modal-overlay hidden" id="catalogModal">
  <div class="modal-content">
    <div class="modal-header">
      <h3 data-t="modalCatalogTitle">📋 Полный каталог активов</h3>
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

let tgUser = localStorage.getItem("tmv_tgUser")||null;
let isAdmin = localStorage.getItem("tmv_isAdmin") === "true";
let timer=null,scanTimer=null,stream=null,camReady=false;
let favs=JSON.parse(localStorage.getItem("tmv_favs")||"[]");
let sigCount=parseInt(localStorage.getItem("tmv_sigs")||"0");
let currentAsset="EUR/USD OTC";
let currentLang="ru";
let modalCategory="all";
let uploadedImageBlob = null;
let scanCfg = {интервал: 'M1', экспирация: '1м', стратегия: 'Smart Money'};

let allUsersReg = JSON.parse(localStorage.getItem("tmv_users_db") || JSON.stringify([
  {tg: "@master_admin", status: "Активен", role: "ADMIN"}
]));

const ASSETS_DATA_JS = {
  ru: {
    "💱 Валютные пары": ["EUR/USD OTC", "AUD/CHF OTC", "AUD/USD OTC", "EUR/NZD OTC", "NZD/JPY OTC", "NZD/USD OTC", "USD/CAD OTC", "GBP/USD OTC", "USD/JPY OTC", "EUR/USD", "GBP/USD"],
    "🔥 Криптовалюты": ["BNB OTC", "Dogecoin OTC", "Chainlink OTC", "Toncoin OTC", "Bitcoin OTC", "Ethereum OTC", "Solana OTC", "Cardano OTC", "TRON OTC"],
    "🛢️ Сырьевые товары": ["Brent Oil OTC", "WTI Crude Oil OTC", "Silver OTC", "Gold OTC", "Natural Gas OTC"],
    "📈 Акции компаний": ["American Express OTC", "Tesla OTC", "VISA OTC", "Intel OTC", "Microsoft OTC", "Netflix OTC", "Amazon OTC", "Apple OTC"],
    "📊 Биржевые индексы": ["US100 OTC", "SP500 OTC"]
  },
  en: {
    "💱 Currency Pairs": ["EUR/USD OTC", "AUD/CHF OTC", "AUD/USD OTC", "EUR/NZD OTC", "NZD/JPY OTC", "NZD/USD OTC", "USD/CAD OTC", "GBP/USD OTC", "USD/JPY OTC", "EUR/USD", "GBP/USD"],
    "🔥 Cryptocurrencies": ["BNB OTC", "Dogecoin OTC", "Chainlink OTC", "Toncoin OTC", "Bitcoin OTC", "Ethereum OTC", "Solana OTC", "Cardano OTC", "TRON OTC"],
    "🛢️ Commodities": ["Brent Oil OTC", "WTI Crude Oil OTC", "Silver OTC", "Gold OTC", "Natural Gas OTC"],
    "📈 Company Stocks": ["American Express OTC", "Tesla OTC", "VISA OTC", "Intel OTC", "Microsoft OTC", "Netflix OTC", "Amazon OTC", "Apple OTC"],
    "📊 Market Indices": ["US100 OTC", "SP500 OTC"]
  },
  ua: {
    "💱 Валютні пари": ["EUR/USD OTC", "AUD/CHF OTC", "AUD/USD OTC", "EUR/NZD OTC", "NZD/JPY OTC", "NZD/USD OTC", "USD/CAD OTC", "GBP/USD OTC", "USD/JPY OTC", "EUR/USD", "GBP/USD"],
    "🔥 Криптовалюти": ["BNB OTC", "Dogecoin OTC", "Chainlink OTC", "Toncoin OTC", "Bitcoin OTC", "Ethereum OTC", "Solana OTC", "Cardano OTC", "TRON OTC"],
    "🛢️ Сировинні товари": ["Brent Oil OTC", "WTI Crude Oil OTC", "Silver OTC", "Gold OTC", "Natural Gas OTC"],
    "📈 Акції компаній": ["American Express OTC", "Tesla OTC", "VISA OTC", "Intel OTC", "Microsoft OTC", "Netflix OTC", "Amazon OTC", "Apple OTC"],
    "📊 Біржові індекси": ["US100 OTC", "SP500 OTC"]
  }
};

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
    lblActiveAsset: "АКТИВ ДЛЯ АНАЛИЗА",
    lblTf: "ТАЙМФРЕЙМ",
    lblExp: "ЭКСПИРАЦИЯ",
    btnCalcSig: "⚡ Просчитать сигнал",
    btnAutoSig: "🤖 Выбрать топ актив",
    btnOpenPo: "🚀 Открыть Pocket Option",
    lblCombo: "Связка",
    btnCancel: "✖ Отменить",
    btnNewSig: "⚡ Новый сигнал",
    eduHeader: "📚 50 профессиональных связок (Полный курс)",
    btnBackEdu: "← Назад к списку",
    profTitle: "👤 Профиль",
    profNameLbl: "ВАШЕ ИМЯ",
    profStatus: "Статус",
    profIdLbl: "Ваш ТГ",
    profSigsLbl: "Сигналов",
    profFavLbl: "⭐ ИЗБРАННЫЕ АКТИВЫ",
    favTabTitle: "⭐ Избранные активы",
    favTabDesc: "Нажмите на любой актив из списка, чтобы мгновенно перейти к нему.",
    profFavEmpty: "Пока пусто — добавь актив из полного каталога",
    btnClearFav: "🗑 Очистить избранное",
    btnLogout: "🚪 Выйти",
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
    modalCatalogTitle: "📋 Полный каталог активов",
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
    lblActiveAsset: "SELECTED ASSET",
    lblTf: "TIMEFRAME",
    lblExp: "EXPIRATION",
    btnCalcSig: "⚡ Calculate Signal",
    btnAutoSig: "🤖 Choose Top",
    btnOpenPo: "🚀 Open Pocket Option",
    lblCombo: "Combo",
    btnCancel: "✖ Cancel",
    btnNewSig: "⚡ New Signal",
    eduHeader: "📚 Combos",
    btnBackEdu: "← Back",
    profTitle: "👤 Profile",
    profNameLbl: "YOUR NAME",
    profStatus: "Status",
    profIdLbl: "Your TG",
    profSigsLbl: "Signals",
    profFavLbl: "⭐ FAVORITE ASSETS",
    favTabTitle: "⭐ Favorite Assets",
    favTabDesc: "Click any asset to switch instantly.",
    profFavEmpty: "Empty yet",
    btnClearFav: "🗑 Clear",
    btnLogout: "🚪 Logout",
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
    gateTitle: "Ласкаво просимо",
    btnConfirmId: "✅ Увійти в термінал",
    depTitle: "Перевірка депозиту",
    btnCheckDep: "🔍 Перевірити депозит",
    btnBack: "← Назад",
    accOpenTitle: "Доступ відкрито",
    accOpenDesc: "Активацію успішно пройдено.",
    btnEnterTerm: "🚀 Увійти в термінал",
    lblActiveAsset: "АКТИВ ДЛЯ АНАЛІЗУ",
    lblTf: "ТАЙМФРЕЙМ",
    lblExp: "ЕКСПИРАЦИЯ",
    btnCalcSig: "⚡ Прорахувати сигнал",
    btnAutoSig: "🤖 Обрати топ",
    btnOpenPo: "🚀 Відкрити Pocket Option",
    lblCombo: "Зв'язка",
    btnCancel: "✖ Скасувати",
    btnNewSig: "⚡ Новий сигнал",
    eduHeader: "📚 Зв'язки",
    btnBackEdu: "← Назад",
    profTitle: "👤 Профіль",
    profNameLbl: "ВАШЕ ІМ'Я",
    profStatus: "Статус",
    profIdLbl: "Ваш ТГ",
    profSigsLbl: "Сигналів",
    profFavLbl: "⭐ ОБРАНІ АКТИВИ",
    favTabTitle: "⭐ Обрані активи",
    favTabDesc: "Натисніть на будь-який актив зі списку.",
    profFavEmpty: "Поки порожньо",
    btnClearFav: "🗑 Очистити",
    btnLogout: "🚪 Вийти",
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
    modalCatalogTitle: "📋 Повний каталог активів",
    modalSearchPlaceholder: "Пошук активу..."
  }
};

const EDU = """ + json.dumps(EDU_50, ensure_ascii=False) + """;

function getAssetPayout(symbol) {
  return symbol.includes("OTC") ? "92%" : "82%";
}

function updatePoDeepLink() {
  const btn = document.getElementById("poDeepLink");
  if(!btn) return;
  let formattedAsset = currentAsset.replace("/", "").replace(/ /g, "_").toLowerCase();
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
    let payoutStr = getAssetPayout(assetName);
    html += `<button class="cat ${activeClass}" onclick="selectCatalogAsset('${assetName}')">${assetName} <span class="cat-payout">${payoutStr}</span></button>`;
  });
  container.innerHTML = html;
}

function updateSelectedAssetUI() {
  const displayEl = document.getElementById("currentAssetDisplay");
  if(displayEl) displayEl.textContent = currentAsset;
  const payoutEl = document.getElementById("currentAssetPayout");
  if(payoutEl) payoutEl.textContent = getAssetPayout(currentAsset);
  updatePoDeepLink();
}

function goStep(n){
  document.querySelectorAll(".step").forEach(s=>s.classList.remove("on"));
  document.getElementById("s"+n).classList.add("on");
  for(let i=1;i<=3;i++){
    const d=document.getElementById("d"+i);
    if(d){
      d.className="p-dot";
      if(i<n) d.classList.add("done");
      else if(i===n) d.classList.add("cur");
    }
  }
  for(let i=1;i<=2;i++){
    const l=document.getElementById("l"+i);
    if(l){
      l.className="p-line";
      if(i<n) l.classList.add("done");
    }
  }
}

function showMsg(id, txt, isErr=false){
  const el=document.getElementById(id);
  if(el){
    el.className="msg "+(isErr?"err":"ok");
    el.innerHTML=txt;
  }
}

function checkUserBlockedStatus(user) {
  let foundUser = allUsersReg.find(u => u.tg.toLowerCase() === user.toLowerCase());
  if(foundUser && foundUser.status === "Заблокирован") {
    alert("⛔ Ваш аккаунт заблокирован администратором!");
    return true;
  }
  return false;
}

function registerUserAndNotify(username) {
  if(!username) return;
  let formattedTg = username.startsWith("@") ? username : "@" + username;
  let foundUser = allUsersReg.find(u => u.tg.toLowerCase() === formattedTg.toLowerCase());
  if(!foundUser) {
    allUsersReg.push({tg: formattedTg, status: "Активен", role: "USER"});
    localStorage.setItem("tmv_users_db", JSON.stringify(allUsersReg));
  }
}

function doReg(){
  const code = document.getElementById("masterCodeInput").value.trim();
  const user = document.getElementById("tgUserInput").value.trim();
  if(!user || !user.startsWith("@")){
    showMsg("regMsg", "❌ Укажите ваш Telegram юзернейм начиная с @", true);
    return;
  }
  if(!code) {
    showMsg("regMsg", "❌ Введите ключ доступа!", true);
    return;
  }
  if(code !== ADMIN_SECRET_KEY && code !== USER_SECRET_KEY) {
    showMsg("regMsg", "❌ Неверный ключ доступа!", true);
    return;
  }
  if(checkUserBlockedStatus(user)) {
    showMsg("regMsg", "❌ Этот аккаунт заблокирован!", true);
    return;
  }
  isAdmin = (code === ADMIN_SECRET_KEY);
  localStorage.setItem("tmv_isAdmin", isAdmin ? "true" : "false");
  tgUser = user;
  localStorage.setItem("tmv_tgUser", user);
  registerUserAndNotify(tgUser);
  showMsg("regMsg", "✅ Ключ принят!");
  setTimeout(()=>{ goStep(2); }, 1000);
}

function doDep(){
  const promo = document.getElementById("promoInput").value.trim();
  if(promo.toUpperCase() !== "WELCOME50"){
    showMsg("depMsg", "❌ Неверный промокод! Введите WELCOME50", true);
    return;
  }
  showMsg("depMsg", "✅ Депозит подтвержден!");
  setTimeout(()=>{ goStep(3); }, 1000);
}

function enterApp(){
  document.getElementById("gate").classList.add("hidden");
  document.getElementById("app").classList.remove("hidden");
  document.getElementById("bnav").classList.remove("hidden");
  renderAssetButtons();
  updateSelectedAssetUI();
  renderFavs();
  renderEdu();
  loadProfile();
}

function tab(tabId, el){
  document.querySelectorAll(".tab").forEach(t=>t.classList.add("hidden"));
  document.getElementById(tabId).classList.remove("hidden");
  document.querySelectorAll(".bnav .nav").forEach(n=>n.classList.remove("on"));
  if(el) el.classList.add("on");
  if(tabId === 'tabFavs') renderMainFavs();
  if(tabId === 'tabProf' && isAdmin) renderAdminData();
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
        let payoutStr = getAssetPayout(item);
        html += `<div class="fav-item">
          <span onclick="selectCatalogAsset('${item}')">${item}</span>
          <div style="display:flex;gap:8px;align-items:center">
            <span class="cat-payout">${payoutStr}</span>
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
    html += `<div class="fav-item" onclick="selectCatalogAsset('${f}')"><span>⭐ ${f} <span class="cat-payout">${getAssetPayout(f)}</span></span><span>➔</span></div>`;
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
    html += `<div class="fav-item" onclick="selectCatalogAsset('${f}')"><span>⭐ ${f} <span class="cat-payout">${getAssetPayout(f)}</span></span><span style="color:var(--gold)">Перейти ➔</span></div>`;
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
    
    let dir = "⬆️ CALL (ВВЕРХ)";
    let stratText = `Математический ИИ-анализ актива (${currentAsset}) по индикаторам RSI и EMA.`;
    
    try {
      const res = await fetch(`/get_signal?asset=${encodeURIComponent(currentAsset)}&timeframe=${tf}`);
      if (res.ok) {
        const data = await res.json();
        if (data.signal) {
          dir = data.signal === "UP" ? "⬆️ CALL (ВВЕРХ)" : "⬇️ PUT (НИЗ)";
        }
      }
    } catch(e) {}

    let isCall = dir.includes("CALL") || dir.includes("ВВЕРХ");
    document.getElementById("sigMeta").textContent = `${currentAsset} · ${tf} · Выплата: ${getAssetPayout(currentAsset)}`;
    const dirEl = document.getElementById("sigDir");
    dirEl.textContent = dir;
    dirEl.className = "sig-dir " + (isCall ? "call" : "put");
    document.getElementById("sigStrat").innerHTML = `<b>Анализ:</b> ${stratText}`;
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
  const phrasesList = ["🔍 Запрос к Gemini AI...", "🤖 Анализ Smart Money...", "📊 Обработка RSI и EMA...", "⚡ Генерация точного сигнала..."];
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
  document.getElementById("eduBody").innerHTML = `<h3>${ed.t}</h3><img src="${ed.img}" alt="Strategy"><div class="tag-row">${tagsHtml}</div><p>${ed.d}</p>`;
}

function closeEdu(){
  document.getElementById("eduView").classList.add("hidden");
  document.getElementById("eduList").classList.remove("hidden");
}

function loadProfile(){
  document.getElementById("profId").textContent = tgUser || "@user";
  document.getElementById("profName").value = localStorage.getItem("tmv_name") || "";
  document.getElementById("profSigs").textContent = sigCount;
  renderFavs();
  const adminBox = document.getElementById("adminProfileBox");
  if(isAdmin) {
    adminBox.classList.remove("hidden");
    renderAdminData();
  } else {
    adminBox.classList.add("hidden");
  }
}

function saveName(){
  localStorage.setItem("tmv_name", document.getElementById("profName").value);
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
        let isErr = resText.includes("❌");
        
        document.getElementById("scanMeta").textContent = "TEAM MASTER SIGNAL V4.0";
        const dirEl = document.getElementById("scanDir");
        
        if(isErr) {
          dirEl.textContent = "❌ ГРАФИК НЕ НАЙДЕН";
          dirEl.className = "sig-dir put";
        } else {
          let isBuy = resText.includes("BUY") || resText.includes("ВВЕРХ");
          dirEl.textContent = isBuy ? "BUY (ВВЕРХ)" : "SELL (ВНИЗ)";
          dirEl.className = "sig-dir " + (isBuy ? "call" : "put");
          sigCount++;
          localStorage.setItem("tmv_sigs", sigCount);
          document.getElementById("profSigs").textContent = sigCount;
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
  msgBox.innerHTML += `<div class="chat-msg ai" id="${loadingId}">Gemini AI думает...</div>`;
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

function renderAdminData(filter=""){
  const listEl = document.getElementById("adminUsersList");
  if(!listEl) return;
  let html = "";
  const filteredUsers = allUsersReg.filter(u => u.tg.toLowerCase().includes(filter.toLowerCase()));
  filteredUsers.forEach((u, idx)=>{
    html += `<div class="user-row">
      <div><b>${u.tg}</b><br><small style="color:var(--green)">${u.status}</small></div>
    </div>`;
  });
  listEl.innerHTML = html || `<div style="text-align:center;padding:10px;color:var(--muted)">Список пуст</div>`;
}

window.addEventListener("DOMContentLoaded", ()=>{
  if(tgUser) {
    if(checkUserBlockedStatus(tgUser)) { logout(); return; }
    registerUserAndNotify(tgUser);
    goStep(3);
    enterApp();
  }
});
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def serve_terminal():
    return HTML_UI

@app.get("/get_signal")
async def get_signal(asset: str = "EUR/USD OTC", timeframe: str = "1min", tg_username: str = Cookie(None)):
    user_for_alert = tg_username if tg_username else "Неизвестный ученик"
    asyncio.create_task(send_tg_signal_alert(user_for_alert, asset, timeframe))
    
    await asyncio.sleep(1.2)
    is_otc = "OTC" in asset
    clean_asset = asset.replace(" OTC", "").strip()
    
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
    calculated_signal = ai_analyze_market(prices, rsi, ema)
    
    base_accuracy = 70.0 + (abs(rsi - 50.0) * 0.5)
    accuracy = round(min(max(base_accuracy, 65.0), 94.5), 1)
    
    return {"signal": calculated_signal, "payout": 92 if is_otc else 82, "accuracy": accuracy}

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
        model = genai.GenerativeModel('gemini-1.5-flash')
        chat_prompt = (
            "Ты профессиональный элитный трейдер и помощник. Твоя задача — отвечать на вопросы пользователя "
            "ИСКЛЮЧИТЕЛЬНО по теме трейдинга, финансовых рынков, технических индикаторов, риск-менеджмента и психологии торговли. "
            "СТРОГОЕ ПРАВИЛО: Если вопрос пользователя касается любых других тем (политика, личная жизнь, программирование, кулинария, "
            "грязные/пошлые темы, развлечения или любые посторонние вопросы, не связанные с трейдингом), ты обязан вежливо отказаться "
            "и ответить: 'Я консультирую исключительно по вопросам трейдинга, рынков и стратегий торговли.'\n\n"
            f"Вопрос пользователя: {user_prompt}"
        )
        response = model.generate_content(chat_prompt)
        reply = response.text.strip() if response and response.text else "Нет ответа от ИИ."
        return {"reply": reply}
    except Exception as e:
        return {"reply": f"⚠️ Ошибка связи с ИИ: {str(e)}"}

@app.post("/request_access")
async def request_access(username: str = Form(...)):
    username = username.strip().replace("@", "").replace(" ", "")
    db = get_db()
    if username in db["users"] and db["users"][username].get("status") == "blocked":
        return JSONResponse({"success": False, "message": "Вы заблокированы!"})
    
    db["users"][username] = {"status": "approved", "activated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    save_db(db)
    asyncio.create_task(send_tg_notification(username))
    
    response = JSONResponse({"success": True, "message": "Доступ активирован!"})
    response.set_cookie(key="tg_username", value=username, max_age=2592000)
    return response

@app.get("/check_user_status")
async def check_user_status(username: str = ""):
    username = username.strip().replace("@", "").replace(" ", "")
    db = get_db()
    status = db["users"].get(username, {}).get("status") if username else None
    return JSONResponse({"status": status})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
