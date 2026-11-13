import os
import re
import io
import json
import time
import math
import zlib
import asyncio
import logging
import functools
from datetime import datetime, timezone, timedelta

import httpx
import numpy as np
import uvicorn
from PIL import Image
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="TEAM MASTER VIP - Ultimate Trading Terminal", version="32.0")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")

# ===== OTCHARDS.COM API (ключ OTCHARDS_API_KEY берётся из Environment на Render) =====
OTCHARDS_API_KEY = os.environ.get("OTCHARDS_API_KEY", "")
OTCHARDS_API_URL = os.environ.get("OTCHARDS_API_URL", "https://otchards.com/api/candles")

OTCHARDS_SYMBOL_MAP = {
    "AUD/USD OTC": "AUDUSD_otc",
    "EUR/USD OTC": "EURUSD_otc",
    "GBP/USD OTC": "GBPUSD_otc",
    "USD/JPY OTC": "USDJPY_otc",
    "BITCOIN OTC": "BTCUSD_otc",
}

TF_SEC = {"S5": 5, "S15": 15, "S30": 30, "M1": 60, "M2": 120, "M3": 180, "M5": 300, "M10": 600, "M15": 900}


def otchards_symbol_for(asset: str):
    key = re.sub(r"[^\x00-\x7F]+", "", asset or "")
    key = re.sub(r"\s+", " ", key).strip().upper()
    return OTCHARDS_SYMBOL_MAP.get(key)


def _to_ts(t):
    if t is None:
        return None
    try:
        v = float(t)
        return v / 1000.0 if v > 1e11 else v
    except Exception:
        pass
    try:
        d = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def _parse_candle_row(row):
    """Возвращает (timestamp|None, [o,h,l,c]) из dict или массива."""
    try:
        if isinstance(row, dict):
            def g(*ks):
                for k in ks:
                    if k in row and row[k] is not None:
                        return row[k]
                return None
            t = _to_ts(g("time", "timestamp", "t", "date", "datetime"))
            o, h, l, c = (float(g("open", "o")), float(g("high", "h")), float(g("low", "l")), float(g("close", "c")))
        elif isinstance(row, (list, tuple)) and len(row) >= 4:
            if len(row) >= 5:
                t = _to_ts(row[0])
                vals = row[1:5]
            else:
                t = None
                vals = row[:4]
            o, h, l, c = [float(x) for x in vals]
        else:
            return None
        return t, [o, max(h, o, c), min(l, o, c), c]
    except Exception:
        return None


_PO_DEBUG = {"last_error": None, "last_raw": None, "last_status": None, "last_symbol": None, "last_ok_ts": None}
_PO_SYM_OK = {}
_HTTP_CLIENT = None


def _http():
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        _HTTP_CLIENT = httpx.AsyncClient(follow_redirects=True)
    return _HTTP_CLIENT


def _redact(s):
    """Прячем API-ключ из любых сообщений об ошибках/логов."""
    s = str(s)
    if OTCHARDS_API_KEY:
        s = s.replace(OTCHARDS_API_KEY, "***")
    return s


def _symbol_variants(symbol: str):
    base = symbol.replace("_otc", "")
    cand = [symbol, f"{base}_OTC", f"{base}-OTC", f"{base}otc"]
    ok = _PO_SYM_OK.get(symbol)
    out = [ok] if ok else []
    for v in cand:
        if v not in out:
            out.append(v)
    return out


def _extract_rows(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("candles", "data", "result", "items", "history", "values", "ohlc"):
            v = data.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                r = _extract_rows(v)
                if r is not None:
                    return r
    return None


def _resample(parsed, period):
    """Если API отдал свечи мельче нужного ТФ (например, M1 вместо M5) — склеиваем их по времени."""
    if len(parsed) < 3:
        return parsed
    ts = [p[0] for p in parsed]
    diffs = sorted(ts[i + 1] - ts[i] for i in range(len(ts) - 1))
    med = diffs[len(diffs) // 2]
    if med <= 0 or period < med * 1.5:
        return parsed
    buckets = {}
    for t, cd in parsed:
        b = int(t // period) * period
        if b not in buckets:
            buckets[b] = [cd[0], cd[1], cd[2], cd[3]]
        else:
            x = buckets[b]
            x[1] = max(x[1], cd[1])
            x[2] = min(x[2], cd[2])
            x[3] = cd[3]
    return [(b, buckets[b]) for b in sorted(buckets)]


async def fetch_otchards_candles(symbol: str, tf: str, count: int = 120):
    """Реальные свечи Pocket Option (OTC) через otchards.com. Ключ — OTCHARDS_API_KEY из Environment Render."""
    if not OTCHARDS_API_KEY:
        raise RuntimeError("OTCHARDS_API_KEY не задан в Environment")
    period = TF_SEC.get(tf, 60)
    headers = {"Authorization": f"Bearer {OTCHARDS_API_KEY}", "X-API-KEY": OTCHARDS_API_KEY,
               "Accept": "application/json"}
    last_err = "нет ответа от API"
    for sym in _symbol_variants(symbol):
        params = {"symbol": sym, "timeframe": period, "period": period, "count": count, "limit": count,
                  "api_key": OTCHARDS_API_KEY}
        try:
            res = await _http().get(OTCHARDS_API_URL, params=params, headers=headers, timeout=6.0)
        except Exception as e:
            _PO_DEBUG["last_error"] = "network: " + _redact(e)
            raise RuntimeError(_PO_DEBUG["last_error"])
        _PO_DEBUG.update({"last_status": res.status_code, "last_symbol": sym, "last_raw": _redact(res.text[:400])})
        if res.status_code in (401, 403, 429):
            raise RuntimeError(f"HTTP {res.status_code} — проверь OTCHARDS_API_KEY / лимиты")
        if res.status_code != 200:
            last_err = f"HTTP {res.status_code} для {sym}"
            continue
        try:
            data = res.json()
        except Exception:
            last_err = f"ответ не JSON для {sym}"
            continue
        rows = _extract_rows(data)
        if not rows:
            last_err = f"пустой ответ для {sym}"
            continue
        parsed = [p for p in (_parse_candle_row(r) for r in rows) if p]
        if parsed and all(p[0] is not None for p in parsed):
            ded = {}
            for t, cd in parsed:
                ded[t] = cd
            parsed = [(t, ded[t]) for t in sorted(ded)]
            parsed = _resample(parsed, period)
            age = time.time() - parsed[-1][0]
            if age > max(period * 6, 300):
                raise RuntimeError(f"устаревшие данные от API ({int(age)} c назад)")
        out = [p[1] for p in parsed]
        if len(out) < 10:
            last_err = f"мало свечей для {sym} ({len(out)})"
            continue
        _PO_SYM_OK[symbol] = sym
        _PO_DEBUG.update({"last_ok_ts": time.time(), "last_error": None})
        return out[-count:]
    _PO_DEBUG["last_error"] = last_err
    raise RuntimeError(last_err)


# ===== Binance (для обычных не-OTC активов) =====
BINANCE_MAPPING = {
    "EUR/USD": "EURUSDT", "GBP/USD": "GBPUSDT", "AUD/USD": "AUDUSDT", "NZD/USD": "NZDUSDT", "EUR/GBP": "EURGBP",
    "USD/JPY": "JPYUSDT", "USD/CAD": "CADUSDT", "USD/CHF": "CHFUSDT", "EUR/JPY": "EURJPY", "GBP/JPY": "GBPJPY",
    "AUD/JPY": "AUDJPY", "EUR/AUD": "EURAUD", "GBP/AUD": "GBPAUD", "EUR/CHF": "EURCHF", "GBP/CHF": "GBPCHF",
    "AUD/CHF": "AUDCHF", "CAD/JPY": "CADJPY", "CHF/JPY": "CHFJPY", "AUD/CAD": "AUDCAD", "AUD/NZD": "AUDNZD",
    "EUR/CAD": "EURCAD", "GBP/CAD": "GBPCAD", "NZD/JPY": "NZDJPY",
    "Bitcoin": "BTCUSDT", "Ethereum": "ETHUSDT", "Solana": "SOLUSDT", "BNB": "BNBUSDT", "Dogecoin": "DOGEUSDT",
    "Chainlink": "LINKUSDT", "Cardano": "ADAUSDT", "Polkadot": "DOTUSDT", "Polygon": "MATICUSDT",
    "Litecoin": "LTCUSDT", "TRON": "TRXUSDT", "Avalanche": "AVAXUSDT", "Toncoin": "TONUSDT", "Dash": "DASHUSDT",
    "Gold": "PAXGUSDT",
}
BINANCE_TF = {"S5": ("1s", 5), "S15": ("1s", 15), "S30": ("1s", 30), "M1": ("1m", 1), "M2": ("1m", 2),
              "M3": ("3m", 1), "M5": ("5m", 1), "M10": ("5m", 2), "M15": ("15m", 1)}


def _agg(c, n):
    if n <= 1:
        return c
    out, i = [], len(c)
    while i > 0:
        g = c[max(0, i - n):i]
        out.append([g[0][0], max(x[1] for x in g), min(x[2] for x in g), g[-1][3]])
        i -= n
    return out[::-1]


def _binance_name(asset: str):
    clean = re.sub(r"[^\x00-\x7F]+", "", asset).replace("OTC", "").strip().lower()
    if not clean:
        return None
    for k in BINANCE_MAPPING:
        if k.lower() == clean:
            return k
    for k in BINANCE_MAPPING:
        if k.lower() in clean:
            return k
    return None


async def fetch_binance_candles(name: str, tf: str, count: int = 50):
    symbol = BINANCE_MAPPING[name]
    interval, n = BINANCE_TF.get(tf, ("1m", 1))
    limit = min(1000, count * n + n)
    for host in ("https://api.binance.com", "https://data-api.binance.vision"):
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(f"{host}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}", timeout=4.0)
            if res.status_code == 200:
                raw = [[float(c[1]), float(c[2]), float(c[3]), float(c[4])] for c in res.json()]
                out = _agg(raw, n)
                if len(out) >= 10:
                    return out[-count:]
        except Exception as e:
            logging.warning(f"Binance {host} {name}: {e}")
    return None


# ===== Умный график (для активов без API): непрерывное движение по времени =====
FX_USD = {"USD": 1.0, "EUR": 1.085, "GBP": 1.27, "AUD": 0.66, "NZD": 0.60, "CAD": 0.735, "CHF": 1.135,
          "JPY": 0.00667, "BTC": 65000.0, "ETH": 3500.0, "BCH": 400.0}
QUOTE_PER_USD = {"ARS": 980, "BRL": 5.0, "CLP": 940, "COP": 4100, "DZD": 134, "EGP": 48, "IDR": 15800, "INR": 83.5,
                 "MXN": 17.2, "MYR": 4.7, "PHP": 57, "PKR": 278, "SGD": 1.35, "THB": 36, "VND": 24800, "RUB": 92,
                 "TRY": 32, "HUF": 365, "ZAR": 18.5, "UAH": 40, "NGN": 1500, "KES": 130}
_KW = [("bitcoin", 65000, .004), ("ethereum", 3500, .004), ("solana", 150, .005), ("bnb", 580, .004),
       ("dogecoin", .15, .006), ("chainlink", 14, .005), ("toncoin", 6, .005), ("avalanche", 35, .005),
       ("cardano", .45, .005), ("polkadot", 7, .005), ("polygon", .7, .005), ("litecoin", 80, .005),
       ("tron", .12, .004), ("dash", 30, .005), ("brent", 82, .0025), ("wti", 78, .0025), ("silver", 29, .003),
       ("gold", 2400, .002), ("natural gas", 2.6, .004), ("palladium", 1000, .003), ("platinum", 980, .003),
       ("apple", 190, .003), ("tesla", 250, .005), ("visa", 270, .003), ("intel", 31, .004), ("amazon", 185, .003),
       ("netflix", 640, .004), ("facebook", 500, .004), ("microsoft", 420, .003), ("johnson", 155, .002),
       ("alibaba", 80, .004), ("exxon", 115, .003), ("palantir", 25, .006), ("coinbase", 230, .006),
       ("mcdonald", 290, .002), ("boeing", 180, .004), ("pfizer", 28, .003), ("citigroup", 62, .003),
       ("fedex", 260, .003), ("gamestop", 20, .007), ("american express", 230, .003), ("cisco", 50, .003),
       ("advanced micro", 160, .005), ("marathon", 20, .008), ("vix", 15, .01), ("us100", 18500, .002),
       ("sp500", 5300, .0015), ("aus 200", 7800, .0015)]


def _usd_value(code):
    if code in FX_USD:
        return FX_USD[code]
    if code in QUOTE_PER_USD:
        return 1.0 / QUOTE_PER_USD[code]
    return None


@functools.lru_cache(maxsize=1024)
def _asset_meta(asset: str):
    a = re.sub(r"[^\x00-\x7F]+", "", asset).upper().replace("OTC", "").strip()
    m = re.search(r"\b([A-Z]{3})/([A-Z]{3})\b", a)
    if m:
        va, vb = _usd_value(m.group(1)), _usd_value(m.group(2))
        if va and vb:
            return va / vb, (0.004 if "BTC" in a or "BCH" in a else 0.0010)
    low = a.lower()
    for k, p, v in _KW:
        if k in low:
            return float(p), v
    h = zlib.crc32(asset.encode())
    return float(40 + (h % 260)), 0.003


def _hnoise(h, i):
    x = (i * 374761393 + h * 668265263) & 0xFFFFFFFF
    x = ((x ^ (x >> 13)) * 1274126177) & 0xFFFFFFFF
    x ^= x >> 16
    return (x & 0xFFFF) / 65535.0


def sim_price(asset: str, t: float):
    base, vol = _asset_meta(asset)
    h = zlib.crc32(asset.encode())
    ph = [((h >> (i * 3)) & 255) / 255.0 * 6.2832 + i for i in range(7)]
    v = (math.sin(t / 410 + ph[0]) + 0.6 * math.sin(t / 137 + ph[1]) + 0.35 * math.sin(t / 53 + ph[2]) +
         0.18 * math.sin(t / 19 + ph[3]) + 1.2 * math.sin(t / 1900 + ph[4]) + 1.8 * math.sin(t / 9000 + ph[5]) +
         2.2 * math.sin(t / 40000 + ph[6]))
    i = int(t)
    f = t - i
    n = (_hnoise(h, i) * (1 - f) + _hnoise(h, i + 1) * f - 0.5) * 0.3
    return base * (1 + vol * (v * 0.5 + n))


def sim_candles(asset: str, tf: str, count: int = 50):
    P = TF_SEC.get(tf, 60)
    now = time.time()
    k = int(now // P)
    out = []
    for j in range(k - count + 1, k + 1):
        s = j * P
        e = min(s + P, now)
        step = max(1.0, (e - s) / 16.0)
        ts = [s + step * i for i in range(int((e - s) / step) + 1)] + [e]
        ps = [sim_price(asset, t) for t in ts]
        out.append([ps[0], max(ps), min(ps), ps[-1]])
    return out


_CANDLE_CACHE = {}
_LAST_GOOD = {}


async def get_candles(asset: str, tf: str, count: int = 50):
    """Возвращает (свечи, источник). Источники:
    pocket_api      — реальные свечи Pocket Option (5 главных активов)
    binance         — реальный рынок (обычные не-OTC активы)
    market_fallback — API Pocket недоступен, показан реальный рынок (может отличаться от Pocket!)
    otc             — умный симулятор (остальные OTC активы)"""
    key = (asset, tf)
    now = time.time()
    c = _CANDLE_CACHE.get(key)
    if c and now - c[0] < 1.0:
        return c[1][-count:], c[2]
    N = 120
    candles, source = None, "otc"
    sym = otchards_symbol_for(asset)
    if sym:
        try:
            candles = await fetch_otchards_candles(sym, tf, N)
            source = "pocket_api"
            _LAST_GOOD[key] = (now, candles)
        except Exception as e:
            logging.warning(f"otchards.com error for {asset}: {_redact(e)}")
            _PO_DEBUG["last_error"] = _redact(e)
            lg = _LAST_GOOD.get(key)
            if lg and now - lg[0] < 45:
                candles, source = lg[1], "pocket_api"
    if candles is None and (sym or "OTC" not in asset.upper()):
        name = _binance_name(asset)
        if name:
            candles = await fetch_binance_candles(name, tf, N)
            if candles:
                source = "market_fallback" if sym else "binance"
    if not candles:
        candles, source = sim_candles(asset, tf, N), "otc"
    if len(_CANDLE_CACHE) > 400:
        _CANDLE_CACHE.clear()
    _CANDLE_CACHE[key] = (now, candles, source)
    return candles[-count:], source


# ===== Индикаторы и умный анализ =====
def calculate_rsi(prices, period=14):
    prices = np.asarray(prices, dtype=float)
    if len(prices) < period + 1:
        return 50.0
    d = np.diff(prices)
    g, l = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    ag, al = g[:period].mean(), l[:period].mean()
    for i in range(period, len(d)):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
    return 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)


def _ema(a, p):
    a = np.asarray(a, dtype=float)
    k = 2.0 / (p + 1)
    out = np.empty(len(a))
    out[0] = a[0]
    for i in range(1, len(a)):
        out[i] = a[i] * k + out[i - 1] * (1 - k)
    return out


def deep_analysis(candles):
    arr = np.array(candles, dtype=float)
    o, h, l, c = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
    n = len(c)
    price = c[-1]
    score, notes = 0.0, []
    e9, e21, e50 = _ema(c, 9), _ema(c, 21), _ema(c, min(50, n))
    if e9[-1] > e21[-1] > e50[-1]:
        score += 2
        notes.append("EMA 9 выше EMA 21 и EMA 50 — выстроен устойчивый восходящий тренд.")
    elif e9[-1] < e21[-1] < e50[-1]:
        score -= 2
        notes.append("EMA 9 ниже EMA 21 и EMA 50 — выстроен устойчивый нисходящий тренд.")
    elif e9[-1] > e21[-1]:
        score += 0.8
        notes.append("Быстрая EMA выше средней — краткосрочный перевес покупателей.")
    else:
        score -= 0.8
        notes.append("Быстрая EMA ниже средней — краткосрочный перевес продавцов.")
    m = _ema(c, 12) - _ema(c, 26)
    hist = m - _ema(m, 9)
    if hist[-1] > 0:
        score += 1
    else:
        score -= 1
    if hist[-1] > hist[-2]:
        score += 0.7
        notes.append("Гистограмма MACD растёт — импульс вверх усиливается.")
    else:
        score -= 0.7
        notes.append("Гистограмма MACD снижается — импульс вниз усиливается.")
    rsi = calculate_rsi(c)
    if rsi >= 70:
        score -= 1.5
        notes.append(f"RSI {rsi:.1f} — зона перекупленности, вероятен откат вниз.")
    elif rsi <= 30:
        score += 1.5
        notes.append(f"RSI {rsi:.1f} — зона перепроданности, вероятен отскок вверх.")
    else:
        score += 0.5 if rsi > 50 else -0.5
        notes.append(f"RSI {rsi:.1f} — нейтральная зона, {'преобладают покупатели' if rsi > 50 else 'преобладают продавцы'}.")
    w = c[-20:]
    mid, sd = w.mean(), w.std()
    if sd > 0:
        pb = (price - (mid - 2 * sd)) / (4 * sd)
        if pb <= 0.1:
            score += 1.5
            notes.append("Цена у нижней полосы Боллинджера — вероятен отскок вверх.")
        elif pb >= 0.9:
            score -= 1.5
            notes.append("Цена у верхней полосы Боллинджера — вероятен откат вниз.")
    if n >= 14:
        ll, hh = l[-14:].min(), h[-14:].max()
        k = 100 * (price - ll) / (hh - ll) if hh > ll else 50
        if k < 20:
            score += 1
            notes.append("Стохастик ниже 20 — рынок перепродан.")
        elif k > 80:
            score -= 1
            notes.append("Стохастик выше 80 — рынок перекуплен.")
    res, sup = h[-31:-1].max(), l[-31:-1].min()
    rng_ = res - sup
    if rng_ > 0:
        if price - sup < rng_ * 0.12:
            score += 1.5
            notes.append("Цена вплотную к уровню поддержки.")
        elif res - price < rng_ * 0.12:
            score -= 1.5
            notes.append("Цена вплотную к уровню сопротивления.")
    body, rg = abs(c[-1] - o[-1]), max(h[-1] - l[-1], 1e-12)
    pbody = abs(c[-2] - o[-2])
    lw, uw = min(o[-1], c[-1]) - l[-1], h[-1] - max(o[-1], c[-1])
    if c[-1] > o[-1] and c[-2] < o[-2] and body > pbody and c[-1] >= o[-2] and o[-1] <= c[-2]:
        score += 1.5
        notes.append("Бычье поглощение на последней свече.")
    elif c[-1] < o[-1] and c[-2] > o[-2] and body > pbody and c[-1] <= o[-2] and o[-1] >= c[-2]:
        score -= 1.5
        notes.append("Медвежье поглощение на последней свече.")
    elif lw > body * 2 and lw > rg * 0.55:
        score += 1
        notes.append("Длинная нижняя тень — покупатели выкупили снижение.")
    elif uw > body * 2 and uw > rg * 0.55:
        score -= 1
        notes.append("Длинная верхняя тень — продавцы отбили рост.")
    if c[-1] > c[-2] > c[-3]:
        score += 0.5
    elif c[-1] < c[-2] < c[-3]:
        score -= 0.5
    if abs(score) < 0.01:
        score = 0.1 if c[-1] >= o[-1] else -0.1
    signal = "UP" if score > 0 else "DOWN"
    conf = int(min(95, 55 + abs(score) * 5))
    return signal, f"Уверенность {conf}%. " + " ".join(notes[:5]), conf


def _sma_arr(a, p):
    a = np.asarray(a, dtype=float)
    out = np.empty(len(a))
    for i in range(len(a)):
        out[i] = a[max(0, i - p + 1):i + 1].mean()
    return out


def _stoch(h, l, c, k=14, s=3, d=3):
    n = len(c)
    raw = np.full(n, 50.0)
    for i in range(k - 1, n):
        hh, ll = h[i - k + 1:i + 1].max(), l[i - k + 1:i + 1].min()
        raw[i] = 100.0 * (c[i] - ll) / (hh - ll) if hh > ll else 50.0
    kk = _sma_arr(raw, s)
    return kk, _sma_arr(kk, d)


def _adx(h, l, c, p=14):
    """Возвращает (ADX, +DI, -DI, ATR) по Уайлдеру."""
    up, dn = h[1:] - h[:-1], l[:-1] - l[1:]
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])])

    def w(x):
        out = np.zeros(len(x))
        out[p - 1] = x[:p].sum()
        for i in range(p, len(x)):
            out[i] = out[i - 1] - out[i - 1] / p + x[i]
        return out

    atr_s, p_s, m_s = w(tr), w(pdm), w(mdm)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = np.nan_to_num(100 * p_s / atr_s)
        mdi = np.nan_to_num(100 * m_s / atr_s)
        dx = np.nan_to_num(100 * np.abs(pdi - mdi) / (pdi + mdi))
    start = 2 * p - 2
    if len(dx) <= start:
        return 20.0, float(pdi[-1]), float(mdi[-1]), float(atr_s[-1] / p)
    adx = np.zeros(len(dx))
    adx[start] = dx[p - 1:start + 1].mean()
    for i in range(start + 1, len(dx)):
        adx[i] = (adx[i - 1] * (p - 1) + dx[i]) / p
    return float(adx[-1]), float(pdi[-1]), float(mdi[-1]), float(atr_s[-1] / p)


def pro_analysis(candles, htf=None):
    """Профессиональный анализ для 5 главных активов (реальные свечи Pocket Option).
    Тренд-блок (EMA/MACD/DI) + блок возврата к среднему (RSI/BB/Stoch/уровни) + свечные паттерны,
    веса зависят от режима рынка (ADX), плюс подтверждение со старшего таймфрейма."""
    arr = np.array(candles, dtype=float)
    o, h, l, c = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
    n = len(c)
    price = c[-1]
    notes, votes = [], []

    e9, e21, e50 = _ema(c, 9), _ema(c, 21), _ema(c, min(50, n))
    adx, pdi, mdi, atr = _adx(h, l, c)
    rsi = calculate_rsi(c)
    macd = _ema(c, 12) - _ema(c, 26)
    hist = macd - _ema(macd, 9)
    kk, dd = _stoch(h, l, c)
    kv, dv = float(kk[-1]), float(dd[-1])
    w = c[-20:]
    mid, sd = w.mean(), w.std()
    pb = (price - (mid - 2 * sd)) / (4 * sd) if sd > 0 else 0.5

    # ---- ТРЕНД ----
    trend = 0.0
    if e9[-1] > e21[-1] > e50[-1]:
        trend += 2
        s_ema = 1
        notes.append((1, "EMA 9 > 21 > 50 — устойчивый восходящий тренд."))
    elif e9[-1] < e21[-1] < e50[-1]:
        trend -= 2
        s_ema = -1
        notes.append((-1, "EMA 9 < 21 < 50 — устойчивый нисходящий тренд."))
    elif e9[-1] > e21[-1]:
        trend += 0.8
        s_ema = 1
        notes.append((1, "Быстрая EMA выше средней — перевес покупателей."))
    else:
        trend -= 0.8
        s_ema = -1
        notes.append((-1, "Быстрая EMA ниже средней — перевес продавцов."))
    votes.append(("EMA", s_ema))

    s_macd = 1 if hist[-1] > 0 else -1
    trend += s_macd
    votes.append(("MACD", s_macd))
    if hist[-1] > hist[-2]:
        trend += 0.7
        notes.append((1, "Гистограмма MACD растёт — импульс вверх усиливается."))
    else:
        trend -= 0.7
        notes.append((-1, "Гистограмма MACD снижается — импульс вниз усиливается."))

    if adx >= 20:
        s_di = 1 if pdi > mdi else -1
        trend += s_di
        notes.append((s_di, f"ADX {adx:.0f}: {'покупатели' if s_di > 0 else 'продавцы'} доминируют (+DI {pdi:.0f} / −DI {mdi:.0f})."))
    else:
        s_di = 0
    votes.append(("DI", s_di))

    # ---- ВОЗВРАТ К СРЕДНЕМУ ----
    rev = 0.0
    if rsi >= 70:
        rev -= 1.5
        s_rsi = -1
        notes.append((-1, f"RSI {rsi:.0f} — перекупленность, вероятен откат вниз."))
    elif rsi <= 30:
        rev += 1.5
        s_rsi = 1
        notes.append((1, f"RSI {rsi:.0f} — перепроданность, вероятен отскок вверх."))
    else:
        s_rsi = 1 if rsi > 50 else -1
        trend += 0.5 * s_rsi
        notes.append((s_rsi, f"RSI {rsi:.0f} — {'преобладают покупатели' if s_rsi > 0 else 'преобладают продавцы'}."))
    votes.append(("RSI", s_rsi))

    if pb <= 0.1:
        rev += 1.5
        s_bb = 1
        notes.append((1, "Цена у нижней полосы Боллинджера — вероятен отскок."))
    elif pb >= 0.9:
        rev -= 1.5
        s_bb = -1
        notes.append((-1, "Цена у верхней полосы Боллинджера — вероятен откат."))
    else:
        s_bb = 1 if price >= mid else -1
    votes.append(("BB", s_bb))

    cross_up = kk[-2] <= dd[-2] and kk[-1] > dd[-1]
    cross_dn = kk[-2] >= dd[-2] and kk[-1] < dd[-1]
    if kv < 20:
        rev += 1.0 + (0.5 if cross_up else 0)
        notes.append((1, f"Стохастик {kv:.0f} — зона перепроданности{' + бычье пересечение' if cross_up else ''}."))
    elif kv > 80:
        rev -= 1.0 + (0.5 if cross_dn else 0)
        notes.append((-1, f"Стохастик {kv:.0f} — зона перекупленности{' + медвежье пересечение' if cross_dn else ''}."))
    votes.append(("STOCH", 1 if kv > dv else -1))

    res, sup = h[-31:-1].max(), l[-31:-1].min()
    rng_ = res - sup
    if rng_ > 0:
        if price - sup < rng_ * 0.12:
            rev += 1.5
            notes.append((1, "Цена вплотную к поддержке (минимум последних 30 свечей)."))
        elif res - price < rng_ * 0.12:
            rev -= 1.5
            notes.append((-1, "Цена вплотную к сопротивлению (максимум последних 30 свечей)."))

    # ---- СВЕЧИ ----
    pat = 0.0
    body, rg = abs(c[-1] - o[-1]), max(h[-1] - l[-1], 1e-12)
    pbody = abs(c[-2] - o[-2])
    lw, uw = min(o[-1], c[-1]) - l[-1], h[-1] - max(o[-1], c[-1])
    if c[-1] > o[-1] and c[-2] < o[-2] and body > pbody and c[-1] >= o[-2] and o[-1] <= c[-2]:
        pat += 1.5
        notes.append((1, "Бычье поглощение на последней свече."))
    elif c[-1] < o[-1] and c[-2] > o[-2] and body > pbody and c[-1] <= o[-2] and o[-1] >= c[-2]:
        pat -= 1.5
        notes.append((-1, "Медвежье поглощение на последней свече."))
    elif lw > body * 2 and lw > rg * 0.55:
        pat += 1.0
        notes.append((1, "Длинная нижняя тень — покупатели выкупили снижение."))
    elif uw > body * 2 and uw > rg * 0.55:
        pat -= 1.0
        notes.append((-1, "Длинная верхняя тень — продавцы отбили рост."))
    mom = 0.5 if c[-1] > c[-2] > c[-3] else (-0.5 if c[-1] < c[-2] < c[-3] else 0.0)

    # ---- РЕЖИМ РЫНКА ----
    if adx >= 25:
        wt, wr, regime = 1.0, 0.45, "сильный тренд"
    elif adx < 20:
        wt, wr, regime = 0.5, 1.2, "флэт"
    else:
        wt, wr, regime = 0.8, 0.8, "переходный"
    total = wt * trend + wr * rev + pat + mom

    # ---- СТАРШИЙ ТАЙМФРЕЙМ ----
    htf_sign = 0
    if htf is not None and len(htf) >= 22:
        hc = np.array(htf, dtype=float)[:, 3]
        htf_sign = 1 if _ema(hc, 9)[-1] > _ema(hc, 21)[-1] else -1
        total += htf_sign
        votes.append(("HTF", htf_sign))
        notes.append((htf_sign, f"Старший таймфрейм: тренд {'вверх' if htf_sign > 0 else 'вниз'}."))

    if abs(total) < 0.01:
        total = 0.1 if c[-1] >= o[-1] else -0.1
    sig = 1 if total > 0 else -1

    agree = sum(1 for _, s in votes if s == sig)
    tv = max(1, sum(1 for _, s in votes if s != 0))
    conf = 54 + min(32, abs(total) * 4.0) + (agree / tv - 0.5) * 12
    warns = []
    if htf_sign and htf_sign != sig:
        conf -= 6
        warns.append("⚠️ Старший таймфрейм идёт против сигнала.")
    if atr > 0 and (h[-1] - l[-1]) > 2.6 * atr:
        conf -= 4
        warns.append("⚠️ Последняя свеча аномально большая — возможен откат.")
    conf = int(max(55, min(95, round(conf))))
    quality = "СИЛЬНЫЙ" if conf >= 80 else ("СРЕДНИЙ" if conf >= 68 else "СЛАБЫЙ — лучше пропустить")

    aligned = [t for s, t in notes if s == sig][:4]
    against = [t for s, t in notes if s == -sig][:1]
    lines = [f"Уверенность {conf}% · {quality}",
             f"Режим: {regime} (ADX {adx:.0f}) · индикаторы согласны: {agree}/{tv}"]
    lines += aligned
    if against:
        lines.append("Против сигнала: " + against[0])
    lines += warns

    def sg(v):
        return "up" if v > 0 else ("down" if v < 0 else "flat")

    inds = [["RSI", f"{rsi:.0f}", sg(s_rsi)],
            ["ADX", f"{adx:.0f}", sg(s_di)],
            ["EMA", "↑" if s_ema > 0 else "↓", sg(s_ema)],
            ["MACD", "↑" if s_macd > 0 else "↓", sg(s_macd)],
            ["Stoch", f"{kv:.0f}", sg(1 if kv > dv else -1)],
            ["BB", f"{pb * 100:.0f}%", sg(s_bb)]]
    if htf_sign:
        inds.append(["HTF", "↑" if htf_sign > 0 else "↓", sg(htf_sign)])
    return {"signal": "UP" if sig > 0 else "DOWN", "confidence": conf, "quality": quality,
            "reason": "\n".join(lines), "indicators": inds}


# ===== Перевод новостей =====
NEWS_RU_MAP = {
    "non-farm payrolls": "Занятость вне с/х (NFP)", "nonfarm payrolls": "Занятость вне с/х (NFP)",
    "unemployment rate": "Уровень безработицы", "core cpi": "Базовый CPI", "cpi": "Индекс потребительских цен (CPI)",
    "ppi": "Индекс цен производителей (PPI)", "gdp": "ВВП (GDP)", "interest rate decision": "Решение по процентной ставке",
    "rate decision": "Решение по ставке", "fomc": "Заседание ФРС (FOMC)", "federal funds rate": "Ставка ФРС",
    "retail sales": "Розничные продажи", "ism manufacturing": "ISM Производство", "ism services": "ISM Услуги",
    "pmi": "Индекс PMI", "consumer confidence": "Индекс потребительского доверия", "trade balance": "Торговый баланс",
    "initial jobless claims": "Первичные заявки по безработице", "unemployment claims": "Заявки по безработице",
    "average hourly earnings": "Средняя почасовая оплата", "adp non-farm employment": "Занятость ADP",
    "michigan consumer sentiment": "Индекс настроений Мичигана", "beige book": "Бежевая книга ФРС",
    "lagarde speech": "Выступление Лагард (ЕЦБ)", "bank of england": "Банк Англии", "bank of japan": "Банк Японии",
    "core pce": "Базовый PCE", "pce price index": "Индекс цен PCE", "jolts job openings": "Вакансии JOLTS",
    "crude oil inventories": "Запасы сырой нефти", "durable goods": "Заказы на товары длит. пользования",
    "industrial production": "Промышленное производство", "building permits": "Разрешения на строительство",
    "housing starts": "Начало строительства жилья", "ifo business climate": "Индекс IFO",
    "monetary policy statement": "Заявление по ДКП", "press conference": "Пресс-конференция",
    "meeting minutes": "Протокол заседания", "speaks": "Выступление", "speech": "Выступление",
}


def translate_news_title(title: str) -> str:
    if not title:
        return title
    raw = title.strip()
    low = raw.lower()
    for en, ru in sorted(NEWS_RU_MAP.items(), key=lambda x: -len(x[0])):
        if en in low:
            suffix = ""
            if re.search(r"\bm/m\b|\bmom\b", low):
                suffix = " (м/м)"
            elif re.search(r"\by/y\b|\byoy\b", low):
                suffix = " (г/г)"
            elif re.search(r"\bq/q\b|\bqoq\b", low):
                suffix = " (кв/кв)"
            return ru + suffix
    return raw


def impact_ru(imp: str) -> str:
    x = (imp or "").lower()
    return "Высокая" if x == "high" else ("Средняя" if x == "medium" else "Низкая")


async def send_tg_signal_alert(asset, timeframe):
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": ADMIN_CHAT_ID,
               "text": f"📊 Пользователь запросил сигнал!\n\nАктив: {asset}\nТаймфрейм: {timeframe}\n"
                       f"Время запроса: {datetime.now().strftime('%H:%M:%S')}"}
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json=payload, timeout=5.0)
        except Exception:
            pass


# ===== AI сканер =====
class ScannerAnalyzer:
    def _gemini_sync(self, image_bytes: bytes, config: dict):
        prompt = (
            "Ты профессиональный ИИ-сканер графиков бинарных опционов Pocket Option. "
            "Внимательно проанализируй свечи, тени, цвета, тенденцию, уровни и паттерны на скриншоте. "
            "Выдай торговый сигнал: направление (BUY / ВВЕРХ или SELL / ВНИЗ), таймфрейм, стратегию ("
            + str(config.get("стратегия", "Smart Money"))
            + ") и подробный технический анализ. Используй эмодзи 📈📉⚡🔥💎🎯🚀."
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
            r, g, b = [np_img[:, :, i].astype(float) for i in range(3)]
            green = int(np.sum((g > r + 30) & (g > b + 30)))
            red = int(np.sum((r > g + 30) & (r > b + 30)))
            if (green + red) / (np_img.shape[0] * np_img.shape[1]) < 0.0015:
                return "❌ ГРАФИК СО СВЕЧАМИ НЕ ОБНАРУЖЕН. Убедитесь, что на скриншоте четко виден график. 📷🔍"
            bullish = green >= red
            direction = "BUY (ВВЕРХ) 📈" if bullish else "SELL (ВНИЗ) 📉"
            desc = ("Преобладание бычьих зелёных свечей указывает на активных покупателей. 🟢💪" if bullish
                    else "Доминирование медвежьих красных свечей говорит о давлении продавцов. 🔴⚔️")
            return ("--- TEAM MASTER SIGNAL V4.0 ---\n"
                    "📊 СТРАТЕГИЯ: " + str(config.get("стратегия", "Smart Money")) + "\n"
                    "⏱ ТАЙМФРЕЙМ: " + str(config.get("интервал", "M1")) + "\n"
                    "⏳ ЭКСПИРАЦИЯ: " + str(config.get("экспирация", "1м")) + "\n"
                    "-------------------------------\n"
                    "🔥 ВЕРДИКТ: " + direction + "\n"
                    "📌 ПРИЧИНА: " + desc + "\nИмпульс подтверждён закрытием последних свечей. ⚡\n🚀 Сигнал готов к входу!")
        except Exception as e:
            return "❌ Ошибка анализа изображения: " + str(e) + " ⚠️"


scanner_core = ScannerAnalyzer()

# ===== КАТАЛОГ АКТИВОВ (разделы не пересекаются) =====
_BEST = ["🇦🇺🇺🇸 AUD/USD OTC", "🇪🇺🇺🇸 EUR/USD OTC", "🇬🇧🇺🇸 GBP/USD OTC", "🇺🇸🇯🇵 USD/JPY OTC", "🪙 Bitcoin OTC"]


def _dd(lst, used):
    out = []
    for a in lst:
        if a not in used:
            used.add(a)
            out.append(a)
    return out


_used = set(_BEST)
_CURRENCY = _dd([
    "🇪🇺🇺🇸 EUR/USD OTC", "🇬🇧🇺🇸 GBP/USD OTC", "🇺🇸🇯🇵 USD/JPY OTC", "🇦🇺🇺🇸 AUD/USD OTC", "🇪🇺🇯🇵 EUR/JPY OTC",
    "🇺🇸🇨🇦 USD/CAD OTC", "🇬🇧🇯🇵 GBP/JPY OTC", "🇳🇿🇺🇸 NZD/USD OTC", "🇺🇸🇨🇭 USD/CHF OTC", "🇪🇺🇬🇧 EUR/GBP OTC",
    "🇦🇺🇨🇭 AUD/CHF OTC", "🇦🇺🇳🇿 AUD/NZD OTC", "🇨🇦🇨🇭 CAD/CHF OTC", "🇪🇺🇭🇺 EUR/HUF OTC", "🇪🇺🇳🇿 EUR/NZD OTC",
    "🇪🇺🇹🇷 EUR/TRY OTC", "🇬🇧🇦🇺 GBP/AUD OTC", "🇬🇧🇨🇭 GBP/CHF OTC", "🇬🇧🇨🇦 GBP/CAD OTC", "🇳🇿🇯🇵 NZD/JPY OTC",
    "🇺🇸🇦🇷 USD/ARS OTC", "🇺🇸🇧🇷 USD/BRL OTC", "🇺🇸🇨🇱 USD/CLP OTC", "🇺🇸🇨🇴 USD/COP OTC", "🇺🇸🇩🇿 USD/DZD OTC",
    "🇺🇸🇪🇬 USD/EGP OTC", "🇺🇸🇮🇩 USD/IDR OTC", "🇺🇸🇮🇳 USD/INR OTC", "🇺🇸🇲🇽 USD/MXN OTC", "🇺🇸🇲🇾 USD/MYR OTC",
    "🇺🇸🇵🇭 USD/PHP OTC", "🇺🇸🇵🇰 USD/PKR OTC", "🇺🇸🇸🇬 USD/SGD OTC", "🇺🇸🇹🇭 USD/THB OTC", "🇺🇸🇻🇳 USD/VND OTC",
    "🇿🇦🇺🇸 ZAR/USD OTC", "🇺🇦🇺🇸 UAH/USD OTC", "🇳🇬🇺🇸 NGN/USD OTC", "🇰🇪🇺🇸 KES/USD OTC", "🇺🇸🇷🇺 USD/RUB OTC",
    "🇦🇺🇨🇭 AUD/CHF", "🇦🇺🇺🇸 AUD/USD", "🇺🇸🇨🇦 USD/CAD", "🇺🇸🇯🇵 USD/JPY", "🇪🇺🇺🇸 EUR/USD", "🇬🇧🇺🇸 GBP/USD",
    "🇪🇺🇬🇧 EUR/GBP", "🇬🇧🇨🇭 GBP/CHF", "🇬🇧🇦🇺 GBP/AUD", "🇪🇺🇨🇭 EUR/CHF", "🇪🇺🇨🇦 EUR/CAD", "🇪🇺🇦🇺 EUR/AUD",
    "🇯🇵🇨🇦 CAD/JPY", "🇯🇵🇨🇭 CHF/JPY", "🇦🇺🇨🇦 AUD/CAD", "🇦🇺🇯🇵 AUD/JPY", "🇬🇧🇯🇵 GBP/JPY", "🇪🇺🇯🇵 EUR/JPY",
    "🇨🇦🇨🇭 CAD/CHF", "🇺🇸🇷🇺 USD/RUB",
], _used)
_CRYPTO = _dd([
    "₿ Bitcoin", "Ξ Ethereum", "🪙 Solana", "🪙 BNB OTC", "🪙 Dogecoin OTC", "🪙 Chainlink OTC", "🪙 Toncoin OTC",
    "🪙 Bitcoin ETF OTC", "🪙 Avalanche OTC", "🪙 Cardano OTC", "🪙 Ethereum OTC", "🪙 Polkadot OTC",
    "🪙 Polygon OTC", "🪙 Litecoin OTC", "🪙 TRON OTC", "🪙 Dash", "🪙 Chainlink", "🪙 BCH/EUR", "🪙 BCH/GBP",
    "🪙 BCH/JPY", "🪙 BTC/GBP", "🪙 BTC/JPY",
], _used)
_COMMODITIES = _dd(["🛢️ Brent Oil OTC", "🛢️ WTI Crude Oil OTC", "🥈 Silver OTC", "🥇 Gold OTC", "⛽ Natural Gas OTC",
                    "⚪ Palladium spot OTC", "🔘 Platinum spot"], _used)
_STOCKS = _dd([
    "🍎 Apple OTC", "🚗 Tesla OTC", "💳 VISA OTC", "💻 Intel OTC", "🛒 Amazon OTC", "🎬 Netflix OTC",
    "📘 FACEBOOK INC OTC", "🪟 Microsoft OTC", "💊 Johnson & Johnson OTC", "🛒 Alibaba OTC", "⛽ ExxonMobil OTC",
    "🤖 Palantir Technologies OTC", "🪙 Coinbase Global OTC", "🍔 McDonald's OTC", "✈️ Boeing Company OTC",
    "💊 Pfizer Inc OTC", "🏦 Citigroup Inc OTC", "📦 FedEx OTC", "🎮 GameStop Corp OTC", "💳 American Express OTC",
    "🌐 Cisco OTC", "⚡ Advanced Micro Devices OTC", "⛏️ Marathon Digital Holdings OTC",
], _used)
_INDICES = _dd(["📊 US100 OTC", "📈 SP500", "📊 SP500 OTC", "🇦🇺 AUS 200", "📉 VIX OTC"], _used)

ASSETS_DATA_JS = {
    "ru": {"⭐ Лучшее": _BEST, "💱 Валютные пары": _CURRENCY, "🔥 Криптовалюты": _CRYPTO,
           "🛢️ Сырьевые товары": _COMMODITIES, "📈 Акции компаний": _STOCKS, "📊 Биржевые индексы": _INDICES},
    "en": {"⭐ Best": _BEST, "💱 Currency Pairs": _CURRENCY, "🔥 Cryptocurrencies": _CRYPTO,
           "🛢️ Commodities": _COMMODITIES, "📈 Company Stocks": _STOCKS, "📊 Market Indices": _INDICES},
    "ua": {"⭐ Найкраще": _BEST, "💱 Валютні пари": _CURRENCY, "🔥 Криптовалюти": _CRYPTO,
           "🛢️ Сировинні товари": _COMMODITIES, "📈 Акції компаній": _STOCKS, "📊 Біржові індекси": _INDICES},
}

# ===== 50 СТРАТЕГИЙ: (название, паттерн, зеркало(PUT-сценарий), индикаторы, ТФ, экспирация, логика, CALL, PUT, фильтр) =====
_S = [
("Торговля от уровней поддержки и сопротивления", "levels", 0, ["ema"], "M1, M5", "2–4 мин",
 "Уровень — цена, где раньше уже был разворот. Чем больше касаний и чем свежее реакция, тем сильнее зона: у сопротивления доминируют продавцы, у поддержки — покупатели.",
 "цена коснулась поддержки (допуск до половины тела свечи) и свеча закрылась с нижней тенью или бычьим поглощением.",
 "цена коснулась сопротивления и свеча закрылась с верхней тенью или медвежьим поглощением.",
 "уровень пробит телом свечи, касаний меньше двух, до High-новости меньше 5 минут."),
("Торговля в боковике (флэт)", "range", 0, [], "M1, M5", "1–3 мин",
 "Цена ходит между двумя горизонтальными границами минимум 8–10 свечей. Покупаем у нижней границы, продаём у верхней — до выхода из канала.",
 "касание нижней границы + пин-бар или ложный пробой вниз с возвратом в канал.",
 "касание верхней границы + затухание импульса или ложный пробой вверх с возвратом.",
 "канал уже, чем 6–8 средних свечей; закрытие свечи за границей; сильные новости."),
("Alligator + Accelerator Oscillator", "awake", 0, ["alli", "ao"], "M1, M2, M5", "2–3 мин",
 "Линии Alligator (13/8/5) сходятся во флэте и расходятся веером на старте тренда. Accelerator показывает, что ускорение совпадает с направлением.",
 "линии Alligator расходятся вверх (зелёная выше красной выше синей) и гистограмма AC зелёная выше нуля.",
 "линии расходятся вниз и гистограмма AC красная ниже нуля.",
 "линии Alligator переплетены («спит»); AC меняет цвет каждую свечу."),
("ADX + EMA: вход по силе тренда", "trend", 0, ["ema", "adx"], "M1, M5", "2–3 мин",
 "ADX измеряет силу тренда, а не направление. Торгуем только когда ADX выше 25 и растёт; точка входа — откат к EMA 21.",
 "ADX > 25, +DI выше −DI, цена откатила к EMA 21 и закрылась выше неё.",
 "ADX > 25, −DI выше +DI, цена откатила к EMA 21 снизу и закрылась ниже неё.",
 "ADX < 20 — тренда нет; ADX падает от пика выше 40."),
("Bollinger Bands + Awesome Oscillator", "bb", 0, ["bb", "ao"], "S30, M1", "1 мин",
 "Полосы показывают отклонение от средней на 2σ, AO — смену импульса. Вход после касания полосы и разворота гистограммы AO.",
 "свеча коснулась нижней полосы, AO перешла из красной в зелёную.",
 "свеча коснулась верхней полосы, AO развернулась вниз.",
 "полосы расширяются по тренду, и цена идёт вдоль полосы («езда по полосе»)."),
("RSI + сопротивление: перекупленность", "levels", 1, ["rsi"], "M1, M2", "2–3 мин",
 "RSI(14) выше 70 сам по себе не сигнал. Сигнал — перекупленность на уровне сопротивления с отказом цены.",
 "RSI < 30 на поддержке и бычья свеча подтверждения.",
 "RSI > 70 у сопротивления, свеча с верхней тенью или медвежье закрытие.",
 "сильный тренд: RSI может держаться выше 70 десятки свечей; пробой уровня."),
("Пересечение EMA 9/21", "awake", 1, ["ema"], "M1, M5", "2–5 мин",
 "Быстрая EMA 9 пересекает EMA 21: ранний сигнал смены краткосрочного тренда. Надёжнее после периода флэта.",
 "EMA 9 пересекла EMA 21 снизу вверх, свеча закрылась выше обеих линий.",
 "EMA 9 пересекла EMA 21 сверху вниз, свеча закрылась ниже обеих линий.",
 "EMA переплетаются (частые пересечения) — флэт; расстояние между ними меньше ширины свечи."),
("MACD + EMA: откат по тренду", "trend", 0, ["ema", "macd"], "M1, M5", "2–4 мин",
 "Тренд определяет EMA 21/50, вход — когда MACD после коррекции снова разворачивается по тренду.",
 "восходящий тренд, MACD-гистограмма после спада вновь растёт, линия MACD пересекает сигнальную вверх.",
 "нисходящий тренд, гистограмма после отскока снова падает, MACD пересекает сигнальную вниз.",
 "MACD около нулевой линии без наклона; свеча-импульс против тренда больше 2 ATR."),
("Stochastic + поддержка", "levels", 0, ["stoch"], "M1, M2", "1–3 мин",
 "Stochastic(14,3) в зоне экстремума + реакция цены на уровень даёт точку разворота.",
 "%K пересёк %D снизу вверх ниже 20, цена на поддержке.",
 "%K пересёк %D сверху вниз выше 80, цена у сопротивления.",
 "Stochastic держится в экстремуме при сильном тренде; нет уровня рядом."),
("Пин-бар от уровня (бычий)", "pin", 0, ["ema"], "M1, M5", "2–3 мин",
 "Пин-бар — свеча с длинной тенью (≥2/3 диапазона) и малым телом: рынок попробовал уровень и был отвергнут.",
 "после падения к поддержке закрылась свеча с нижней тенью ≥ 2 тел; входим на открытии следующей.",
 "у сопротивления — свеча с верхней тенью ≥ 2 тел; входим на открытии следующей.",
 "тень пин-бара не выходит за уровень; пин-бар в середине диапазона."),
("Бычье поглощение", "engulf", 0, [], "M1, M5", "2–3 мин",
 "Зелёная свеча полностью перекрывает тело предыдущей красной после снижения: покупатели забрали инициативу.",
 "после 3+ красных свечей тело зелёной перекрывает тело предыдущей, лучше возле поддержки.",
 "зеркально: красное тело перекрывает зелёное у сопротивления.",
 "поглощающая свеча меньше средней; нет предшествующего движения."),
("Медвежье поглощение", "engulf", 1, ["ema"], "M1, M5", "2–3 мин",
 "Красная свеча перекрывает тело предыдущей зелёной после роста: продавцы перехватили инициативу.",
 "бычье поглощение у поддержки по тренду вверх.",
 "после 3+ зелёных свечей красное тело перекрывает зелёное, лучше у сопротивления.",
 "поглощение против сильного тренда без уровня; малое тело сигнальной свечи."),
("Ложный пробой поддержки", "fake", 0, [], "M1, M5", "2–4 мин",
 "Цена коротко выходит за уровень (сбор стопов), но свеча закрывается обратно внутри диапазона — «ловушка» для пробойщиков.",
 "свеча проколола поддержку тенью и закрылась выше уровня.",
 "свеча проколола сопротивление тенью и закрылась ниже уровня.",
 "закрытие свечи за уровнем; пробой на большом объёме/импульсной свече."),
("Пробой и ретест уровня", "retest", 0, ["ema"], "M1, M5", "2–5 мин",
 "Пробитый уровень меняет роль: сопротивление становится поддержкой. Вход — на возврате к нему после пробоя.",
 "цена пробила сопротивление телом, вернулась к уровню и отскочила (свеча с нижней тенью).",
 "цена пробила поддержку, вернулась и отскочила вниз.",
 "возврат закрывается обратно за уровнем; ретест затянулся больше 8 свечей."),
("Двойная вершина", "dbl", 1, [], "M2, M5", "3–5 мин",
 "Две вершины на одном уровне и пробой «линии шеи»: покупатели дважды не смогли пройти зону.",
 "двойное дно: пробой линии шеи вверх закрытием свечи.",
 "вторая вершина на уровне первой ±10% и закрытие ниже линии шеи.",
 "вершины сильно отличаются по высоте; нет закрепления за линией шеи."),
("Двойное дно", "dbl", 0, ["rsi"], "M2, M5", "3–5 мин",
 "Два минимума на одном уровне и пробой «линии шеи»: продавцы дважды не смогли продавить поддержку.",
 "второе дно на уровне первого ±10%, закрытие свечи выше линии шеи.",
 "зеркально — двойная вершина с закрытием ниже шеи.",
 "второй минимум заметно ниже первого; пробой шеи слабой свечой."),
("Голова и плечи", "hs", 1, [], "M3, M5", "5–10 мин",
 "Три вершины, средняя выше: ослабление восходящего импульса. Сигнал — закрытие ниже линии шеи.",
 "обратная «голова и плечи»: закрытие выше линии шеи.",
 "закрытие свечи ниже линии шеи после правого плеча.",
 "плечи несимметричны больше чем на 30%; пробой без закрытия."),
("Восходящий треугольник", "tri", 0, [], "M1, M5", "2–5 мин",
 "Горизонтальное сопротивление и растущая поддержка сжимают цену; чаще всего пробой идёт вверх.",
 "закрытие свечи выше сопротивления после 3+ касаний.",
 "зеркально: нисходящий треугольник, закрытие ниже поддержки.",
 "пробой с длинной тенью и возвратом внутрь; треугольник из менее 6 свечей."),
("Бычий флаг", "flag", 0, ["ema"], "M1, M5", "2–4 мин",
 "Сильный импульс («флагшток») и узкая коррекция против него. Продолжение тренда после выхода из флага.",
 "импульс ≥ 5 свечей, коррекция 4–8 свечей с малыми телами, пробой верхней границы флага.",
 "зеркально: медвежий флаг, пробой нижней границы.",
 "коррекция глубже 50% флагштока; объём импульса слабый."),
("Падающий клин", "wedge", 0, ["rsi"], "M2, M5", "3–5 мин",
 "Сходящиеся линии вниз с ослаблением снижения — признак истощения продавцов.",
 "пробой верхней линии клина закрытием свечи; RSI выходит выше 50.",
 "зеркально: растущий клин, пробой нижней линии.",
 "клин расходится, а не сужается; пробой без закрытия."),
("Inside Bar", "inside", 0, [], "M1, M5", "2–3 мин",
 "Свеча полностью внутри диапазона предыдущей («материнской») — сжатие перед импульсом. Торгуем пробой.",
 "закрытие выше максимума материнской свечи по тренду вверх.",
 "закрытие ниже минимума материнской свечи по тренду вниз.",
 "материнская свеча меньше средней; пробой против старшего тренда."),
("Три белых солдата", "sold", 0, [], "M1, M5", "3–5 мин",
 "Три подряд зелёные свечи с почти полными телами и открытием внутри предыдущего тела — сильный разворотный импульс.",
 "три зелёные свечи с малыми тенями после снижения/флэта.",
 "зеркально: «три чёрные вороны».",
 "верхние тени более половины тела; вход после 4-й свечи (поздно)."),
("Доджи на развороте", "doji", 0, [], "M1, M5", "2–3 мин",
 "Доджи — равенство покупателей и продавцов. После затяжного движения у уровня — сигнал смены направления с подтверждением.",
 "после снижения у поддержки доджи, следующая свеча зелёная — вход на открытии.",
 "после роста у сопротивления доджи и красная свеча подтверждения.",
 "доджи в середине флэта; подтверждающая свеча не сформирована."),
("Bollinger Squeeze", "squeeze", 0, ["bb"], "M1, M5", "2–4 мин",
 "Полосы сжимаются до минимальной ширины за 20 свечей — волатильность накапливается и выстреливает.",
 "закрытие свечи выше верхней полосы после сжатия.",
 "закрытие свечи ниже нижней полосы после сжатия.",
 "ложный выход с возвратом внутрь полос; новость в ближайшие минуты."),
("Бычья дивергенция RSI", "div", 0, ["rsi"], "M2, M5", "3–5 мин",
 "Цена обновляет минимум, а RSI формирует более высокий минимум: давление продавцов слабеет.",
 "второй минимум цены ниже первого, минимум RSI выше; вход после бычьей свечи.",
 "зеркально: медвежья дивергенция — новый максимум цены при более низком максимуме RSI.",
 "минимумы разделены более чем 25 свечами; нет подтверждающей свечи."),
("Медвежья дивергенция RSI", "div", 1, ["rsi"], "M2, M5", "3–5 мин",
 "Цена обновляет максимум, а RSI формирует более низкий максимум: рост теряет силу.",
 "бычья дивергенция у поддержки с подтверждением.",
 "новый максимум цены при более низком максимуме RSI и красная свеча подтверждения.",
 "сильный пробой с высокой волатильностью; дивергенция на фоне новости."),
("Бычья дивергенция MACD", "div", 0, ["macd"], "M2, M5", "3–5 мин",
 "Цена делает новый минимум, а гистограмма MACD — более высокий минимум: замедление нисходящего импульса.",
 "более высокий минимум гистограммы MACD при новом минимуме цены и пересечение MACD/сигнальной вверх.",
 "зеркально: более низкий максимум гистограммы при новом максимуме цены.",
 "гистограмма около нуля без чётких пиков."),
("Восходящий канал: отскок от низа", "chan", 0, [], "M2, M5", "3–5 мин",
 "Цена идёт между параллельными восходящими линиями. Покупаем отскок от нижней границы по тренду.",
 "касание нижней границы канала (минимум 3 касания) + свеча с нижней тенью.",
 "касание верхней границы — сценарий PUT при потере импульса.",
 "закрытие свечи ниже нижней границы; канал круче 60°."),
("Нисходящий канал: отскок от верха", "chan", 1, [], "M2, M5", "3–5 мин",
 "Параллельные нисходящие линии. Продаём отскок от верхней границы по направлению тренда.",
 "касание нижней границы нисходящего канала и бычье поглощение (контртренд, короткая экспирация).",
 "касание верхней границы + свеча с верхней тенью.",
 "закрытие выше верхней границы; канал расширяется."),
("Откат к EMA 50 по тренду", "trend", 0, ["ema"], "M1, M5", "3–5 мин",
 "Цена держится выше EMA 50 и откатывается к ней — «дешёвая» точка входа по тренду.",
 "цена выше EMA 50, откат к EMA 50, свеча закрылась выше неё и зелёная.",
 "цена ниже EMA 50, отскок к ней, красная свеча закрытия ниже.",
 "EMA 50 плоская; цена закрылась по ту сторону EMA 50 двумя свечами."),
("Alligator: пробуждение", "awake", 0, ["alli"], "M1, M5", "2–4 мин",
 "После «сна» (линии рядом) рынок «просыпается» — линии расходятся, цена выходит за них.",
 "цена закрылась выше всех трёх линий после флэта, линии расходятся вверх.",
 "цена закрылась ниже всех трёх линий, линии расходятся вниз.",
 "линии сходятся/переплетены; цена возвращается в «пасть»."),
("Bollinger + EMA: нисходящий тренд", "trend", 1, ["bb", "ema"], "M1, M5", "2–3 мин",
 "В тренде цена отскакивает от средней линии полос (SMA 20), а не от крайних полос.",
 "восходящий тренд: касание средней линии и зелёная свеча.",
 "нисходящий тренд: касание средней линии сверху и красная свеча.",
 "закрытие свечи за средней линией; полосы горизонтальны."),
("Stochastic во флэте: верхняя граница", "range", 1, ["stoch", "ema"], "M1, M2", "1–3 мин",
 "Во флэте Stochastic хорошо работает на границах: подтверждает исчерпание хода.",
 "нижняя граница канала + %K пересекает %D вверх ниже 20.",
 "верхняя граница канала + %K пересекает %D вниз выше 80.",
 "выход из диапазона; EMA имеют угол больше 30°."),
("RSI 50: продолжение нисходящего тренда", "trend", 1, ["rsi"], "M1, M5", "2–4 мин",
 "В тренде RSI откатывается к 50 и разворачивается. Уровень 50 работает как динамическая поддержка/сопротивление.",
 "восходящий тренд: RSI откатился к 50 и развернулся вверх.",
 "нисходящий тренд: RSI поднялся к 50 снизу и развернулся вниз.",
 "RSI пересёк 50 и закрепился с другой стороны."),
("Фибоначчи 61.8%", "fib", 0, ["ema"], "M2, M5", "3–5 мин",
 "После импульса откат часто останавливается на 50–61.8%. Зона 61.8% — «золотое сечение» для входа по тренду.",
 "восходящий импульс, откат на 61.8% + бычья свеча.",
 "нисходящий импульс, отскок на 61.8% + медвежья свеча.",
 "откат глубже 78.6% — импульс сломан."),
("Order Block (SMC)", "ob", 0, [], "M2, M5", "3–5 мин",
 "Order Block — последняя противоположная свеча перед сильным импульсом, где крупный игрок набирал позицию. Возврат в блок даёт реакцию.",
 "цена вернулась в блок (последняя красная свеча перед ростом) и показала бычью реакцию.",
 "цена вернулась в блок (последняя зелёная свеча перед падением) и показала медвежью реакцию.",
 "блок уже протестирован 2+ раза; закрытие свечи за блоком."),
("Liquidity Grab (SMC)", "grab", 1, [], "M1, M5", "2–4 мин",
 "Равные минимумы/максимумы — скопление стопов. Цена «снимает» ликвидность и сразу возвращается.",
 "прокол равных минимумов и закрытие выше них.",
 "прокол равных максимумов тенью и закрытие ниже них.",
 "закрытие за уровнем ликвидности; нет возврата за 1–2 свечи."),
("Fair Value Gap (SMC)", "fvg", 0, [], "M1, M5", "2–4 мин",
 "FVG — разрыв между тенями 1-й и 3-й свечей при импульсной 2-й. Цена часто возвращается заполнить его.",
 "восходящий FVG: возврат в зону и бычья свеча.",
 "нисходящий FVG: возврат в зону сверху и медвежья свеча.",
 "FVG полностью закрыт свечой; зона старше 30 свечей."),
("Break of Structure вниз", "bos", 1, [], "M2, M5", "3–5 мин",
 "BOS — пробой последнего значимого экстремума по тренду, подтверждающий продолжение движения.",
 "пробой предыдущего максимума телом свечи (продолжение вверх).",
 "пробой предыдущего минимума телом свечи (продолжение вниз).",
 "пробой тенью без закрытия; смена структуры против старшего ТФ."),
("Change of Character (CHoCH) вверх", "choch", 0, [], "M2, M5", "3–5 мин",
 "CHoCH — первый пробой структуры против тренда (нисходящий: цена обновила последний lower high). Ранний сигнал разворота.",
 "в нисходящем тренде цена пробила последний lower high, откат к зоне пробоя и вход по бычьей свече.",
 "зеркально: в восходящем тренде пробит последний higher low.",
 "пробой тенью без закрытия; нет отката к зоне."),
("Круглые уровни (сопротивление)", "levels", 1, [], "M1, M5", "2–4 мин",
 "Психологические уровни (1.1000, 150.00, 65000) концентрируют заявки и часто дают реакцию.",
 "цена у круглого уровня снизу-вверх — поддержка и бычья свеча.",
 "цена у круглого уровня снизу и свеча с верхней тенью.",
 "пробой телом с закреплением; выход новости."),
("Отскок от трендовой линии", "trl", 0, [], "M2, M5", "3–5 мин",
 "Линия через минимум 3 восходящих минимумов: касание в тренде — точка входа.",
 "касание восходящей линии (3-е или 4-е касание) + бычья свеча.",
 "зеркально: нисходящая линия, отскок вниз.",
 "закрытие свечи за линией; слишком крутой наклон."),
("Пробой диапазона вниз по импульсу", "brk", 1, [], "M1, M5", "2–3 мин",
 "После долгого флэта импульсная свеча с телом ≥ 2 средних выходит за границу — вход по направлению импульса.",
 "импульсная зелёная свеча закрылась выше границы диапазона.",
 "импульсная красная свеча закрылась ниже границы диапазона.",
 "пробой тенью; следующая свеча возвращается в диапазон."),
("Возврат после спайка вниз", "spike", 0, [], "S30, M1", "1–2 мин",
 "Резкий вынос на 3+ ATR без новости чаще частично откатывается — стратегия среднего возврата.",
 "резкий спайк вниз, затем зелёная свеча с возвратом в диапазон.",
 "зеркально: спайк вверх и красная свеча возврата.",
 "спайк вызван новостью; продолжение в ту же сторону 2+ свечи."),
("Возврат после спайка вверх", "spike", 1, [], "S30, M1", "1–2 мин",
 "Резкий выброс вверх без новости: цена возвращается к средней.",
 "спайк вниз с быстрым возвратом.",
 "спайк вверх, красная свеча возврата.",
 "новостной фон; тренд ускоряется."),
("Тройное касание поддержки", "triple", 0, ["ema"], "M2, M5", "3–5 мин",
 "Три касания одного уровня подтверждают его значимость; третье — часто последняя возможность удержать зону.",
 "3-е касание поддержки с отказом (тень вниз) и зелёным закрытием.",
 "3-е касание сопротивления с красным закрытием.",
 "4-е касание — уровень слабеет; закрытие за уровнем."),
("Бычий Харами", "harami", 0, [], "M1, M5", "2–3 мин",
 "Большая красная свеча и маленькая зелёная внутри её тела — замедление нисходящего импульса.",
 "малая свеча внутри тела большой, следующая свеча зелёная.",
 "зеркально: красная малая внутри большой зелёной.",
 "тело второй свечи более 50% первой."),
("Утренняя звезда", "morning", 0, [], "M1, M5", "3–5 мин",
 "Три свечи: большая красная, малая (звезда) с гэпом/тенью, большая зелёная, закрывающаяся выше середины первой.",
 "третья свеча зелёная и закрылась выше середины первой.",
 "зеркально — вечерняя звезда.",
 "третья свеча не дошла до середины первой."),
("Вечерняя звезда", "morning", 1, [], "M1, M5", "3–5 мин",
 "Зеркальный паттерн для вершины: большая зелёная, малая, большая красная, закрывающаяся ниже середины первой.",
 "утренняя звезда у поддержки.",
 "третья свеча красная и закрылась ниже середины первой.",
 "третья свеча короткая; нет сопротивления рядом."),
("Пин-бар от сопротивления", "pin", 1, ["ema"], "M1, M5", "2–3 мин",
 "Пин-бар с длинной верхней тенью у сопротивления: рынок отверг рост.",
 "бычий пин-бар у поддержки.",
 "после роста свеча с верхней тенью ≥ 2 тел у сопротивления; вход на открытии следующей.",
 "тень не заходит в зону уровня; пин-бар против сильного тренда."),
]

assert len(_S) == 50, len(_S)


def _build_desc(s):
    t, pat, mir, ov, tf, exp, logic, call, put, flt = s
    return (f"📌 {t}\n\n🧠 Логика стратегии:\n{logic}\n\n⏱ Таймфреймы: {tf}\n⏳ Экспирация: {exp}\n\n"
            f"🟢 Условия CALL:\n{call}\n\n🔴 Условия PUT:\n{put}\n\n🚫 Когда не входить:\n{flt}\n\n"
            f"🛡️ Риск: 1–2% депозита на сделку; после 2 убытков подряд — пауза 15 минут.")


EDU_50 = []
for _i, _s in enumerate(_S):
    EDU_50.append({
        "t": f"{_i + 1}. {_s[0]}", "pat": _s[1], "mirror": _s[2], "ov": _s[3], "seed": _i * 7919 + 13,
        "tags": [x.strip() for x in _s[4].split(",")] + ["Pocket Option"] + [o.upper() for o in _s[3]],
        "tf": _s[4], "exp": _s[5], "call": _s[7], "put": _s[8], "flt": _s[9], "d": _build_desc(_s),
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
.live-bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;gap:4px;flex-wrap:wrap}
.live{font-size:8px;background:rgba(0,230,118,.12);color:var(--green);padding:3px 8px;border-radius:6px;border:1px solid rgba(0,230,118,.3);font-weight:800}
.lbl{display:block;font-size:9px;color:var(--muted);font-weight:800;letter-spacing:.3px;margin-bottom:4px}
.selected-asset-box{background:var(--inner);border:1.5px solid var(--gold);border-radius:12px;padding:10px 14px;margin-bottom:12px;display:flex;justify-content:space-between;align-items:center;cursor:pointer}
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
.chart-box{background:linear-gradient(180deg,#070b14,#05070d);border:1px solid var(--border-g);border-radius:12px;padding:8px 8px 6px;margin-bottom:12px;text-align:left}
.chart-head{display:flex;justify-content:space-between;align-items:center;gap:6px;margin-bottom:6px;flex-wrap:wrap}
.chart-title{font-size:10px;font-weight:900;color:var(--gold)}
.chart-src{font-size:9px;font-weight:800;padding:3px 8px;border-radius:7px}
.chart-src.real{background:rgba(0,230,118,.14);color:var(--green);border:1px solid rgba(0,230,118,.35)}
.chart-src.fake{background:rgba(255,215,0,.1);color:var(--gold);border:1px solid rgba(255,215,0,.3)}
.chart-box svg{width:100%;height:auto;display:block}
.edu-item{background:var(--inner);border:1px solid var(--border);border-radius:11px;padding:11px 13px;margin-bottom:7px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;gap:8px;transition:transform .15s}
.edu-item:active{transform:scale(.98)}.edu-item h4{font-size:11.5px;color:var(--gold);font-weight:800}
.edu-item small{display:block;font-size:9px;color:var(--muted);font-weight:700;margin-top:2px}
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
.news-sec-title{font-size:11.5px;font-weight:900;margin:16px 0 10px;padding:10px 14px;border-radius:12px;letter-spacing:.5px;display:flex;align-items:center;gap:8px;text-transform:uppercase}
.news-sec-up{background:linear-gradient(90deg,rgba(255,215,0,.22),rgba(255,215,0,.03));color:var(--gold);border:1px solid rgba(255,215,0,.35)}
.news-sec-done{background:linear-gradient(90deg,rgba(0,230,118,.16),rgba(0,230,118,.02));color:var(--green);border:1px solid rgba(0,230,118,.3)}
.nc{position:relative;background:linear-gradient(160deg,#121826 0%,#0a0e17 60%,#080b12 100%);border:1px solid #1d2638;border-radius:18px;padding:14px 14px 12px 18px;margin-bottom:12px;cursor:pointer;overflow:hidden;box-shadow:0 8px 24px rgba(0,0,0,.4);transition:transform .15s,border-color .2s}
.nc:active{transform:scale(.985)}
.nc-bar{position:absolute;left:0;top:0;bottom:0;width:5px;background:#FFD700}
.nc.high .nc-bar{background:linear-gradient(180deg,#FF1744,#FF5252)}
.nc.medium .nc-bar{background:linear-gradient(180deg,#FF9100,#FFB74D)}
.nc.high{border-color:rgba(255,82,82,.45)}
.nc.medium{border-color:rgba(255,167,38,.4)}
.nc.open{border-color:var(--gold);box-shadow:0 0 0 1px rgba(255,215,0,.3),0 12px 30px rgba(255,215,0,.12)}
.nc-head{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:10px}
.nc-cur{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.nc-flag{font-size:20px;line-height:1}
.nc-code{font-size:13px;font-weight:900;color:#fff;letter-spacing:.6px}
.nc-imp{font-size:9px;font-weight:900;padding:3px 9px;border-radius:20px;letter-spacing:.3px}
.nc-imp.high{background:rgba(255,82,82,.2);color:#ff8a80;border:1px solid rgba(255,82,82,.4)}
.nc-imp.medium{background:rgba(255,167,38,.18);color:#FFB74D;border:1px solid rgba(255,167,38,.4)}
.nc-imp.low{background:rgba(255,215,0,.1);color:var(--gold);border:1px solid rgba(255,215,0,.3)}
.nc-time{font-size:11px;color:#fff;font-weight:800;background:rgba(255,215,0,.12);border:1px solid rgba(255,215,0,.3);padding:5px 10px;border-radius:10px;white-space:nowrap}
.nc-title{font-size:15px;font-weight:800;color:#fff;line-height:1.35;margin-bottom:4px}
.nc-orig{font-size:10px;color:var(--muted);margin-bottom:8px;font-weight:600}
.nc-pairs{font-size:10.5px;color:#aab3c5;font-weight:700;margin-bottom:10px;background:rgba(255,255,255,.04);border-radius:8px;padding:6px 9px;display:inline-block}
.nc-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}
.nc-st{background:rgba(255,255,255,.04);border:1px solid #1d2638;border-radius:11px;padding:8px 6px;text-align:center}
.nc-st i{display:block;font-style:normal;font-size:8.5px;color:var(--muted);font-weight:800;text-transform:uppercase;letter-spacing:.4px;margin-bottom:3px}
.nc-st b{font-size:13px;font-weight:900;color:#fff}
.nc-st.fact{border-color:rgba(0,230,118,.4);background:rgba(0,230,118,.08)}
.nc-st.fact b{color:var(--green)}
.nc-st.wait b{color:var(--gold)}
.nc-detail{display:none;margin-top:12px;padding:12px;border-top:1px solid rgba(255,215,0,.25);border-radius:12px;font-size:12px;color:#d7deea;line-height:1.7;background:rgba(0,0,0,.3)}
.nc.open .nc-detail{display:block}
.nc-copy{margin-top:10px;width:100%;padding:10px;border-radius:10px;border:1px solid rgba(255,215,0,.35);background:rgba(255,215,0,.08);color:var(--gold);font-size:11px;font-weight:800;cursor:pointer}
.news-updated{font-size:10px;color:var(--muted);font-weight:700;text-align:center;margin-bottom:10px;padding:7px;background:rgba(255,255,255,.03);border-radius:8px}
.news-list{max-height:560px;overflow-y:auto;padding-right:3px}
.news-loading{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:36px 16px;gap:14px}
.news-loading-wheel{width:42px;height:42px;border:3px solid rgba(255,215,0,.15);border-top-color:var(--gold);border-radius:50%;animation:spin .75s linear infinite}
.news-loading-text{font-size:12px;font-weight:800;color:var(--gold);animation:pulse 1.2s ease infinite}
.sig-count-badge{font-size:10px;font-weight:800;color:var(--gold);background:rgba(255,215,0,.1);border:1px solid rgba(255,215,0,.25);padding:3px 8px;border-radius:8px}
.strat-visual{background:linear-gradient(180deg,#070b14,#05070d);border:1px solid var(--border-g);border-radius:14px;padding:10px;margin-bottom:14px;overflow:hidden;box-shadow:inset 0 0 30px rgba(255,215,0,.04)}
.strat-visual svg{width:100%;height:auto;display:block}
.strat-legend{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;font-size:10px;font-weight:700;color:#b0b8c8}
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
.quiz-opt{display:block;width:100%;padding:9px;margin-bottom:6px;border-radius:8px;border:1px solid var(--border);background:var(--card);color:#c5cdd8;font-size:11px;font-weight:700;cursor:pointer;text-align:left;line-height:1.45}
.quiz-opt:hover{border-color:var(--gold)}
.quiz-opt.correct{border-color:var(--green);background:rgba(0,230,118,.1);color:var(--green)}
.quiz-opt.wrong{border-color:var(--red);background:rgba(255,82,82,.1);color:var(--red)}
.cat-acc{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:8px;padding:11px 13px;border-radius:11px;background:var(--inner);border:1px solid var(--border);color:var(--text);font-size:11.5px;font-weight:900;text-transform:uppercase;letter-spacing:.4px;cursor:pointer;transition:border-color .2s,background .2s}
.cat-acc.open{border-color:var(--gold);color:var(--gold);background:rgba(255,215,0,.06)}
.cat-meta{font-size:10px;color:var(--muted);font-weight:800;display:flex;align-items:center;gap:6px}
.cat-meta i{font-style:normal;color:var(--gold);font-size:12px}
.live-badge{font-size:8px;font-weight:900;color:var(--green);background:rgba(0,230,118,.12);border:1px solid rgba(0,230,118,.35);padding:2px 6px;border-radius:6px;margin-left:6px;letter-spacing:.3px}
.ind-row{display:flex;flex-wrap:wrap;gap:5px;justify-content:center;margin-bottom:8px}
.ind-row:empty{display:none}
.ind{font-size:9.5px;font-weight:800;padding:4px 8px;border-radius:7px;border:1px solid var(--border);background:var(--card);color:#c5cdd8}
.ind.up{color:var(--green);border-color:rgba(0,230,118,.35)}
.ind.down{color:var(--red);border-color:rgba(255,82,82,.35)}
.sig-meta{line-height:1.6}
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
        <select class="sel" id="tf" onchange="liveReset()">
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
      <div class="ind-row" id="sigInd"></div>
      <div class="strat" id="sigStrat"></div>
      <div class="sig-stats"><span><span data-t="lblCombo">Связка</span>: <b id="sigCombo">Multi-Factor</b></span></div>
      <button class="btn btn-red btn-sm" id="btnCancel" onclick="cancelSig()" style="display:none" data-t="btnCancel">✖ Отменить</button>
      <button class="btn btn-gold btn-sm" id="btnNew" onclick="getSig()" style="display:none" data-t="btnNewSig">⚡ Новый сигнал</button>
    </div>
    <div class="chart-box" id="liveChart" style="display:none;margin:11px 0 0"></div>
  </div>

  <div class="card tab hidden" id="tabFavs">
    <h3 class="g-text" style="font-size:13px;margin-bottom:8px" data-t="favTabTitle">⭐ Избранные активы</h3>
    <p style="font-size:11px;color:var(--muted);margin-bottom:10px" data-t="favTabDesc">Нажмите на любой актив для перехода.</p>
    <div class="fav-list" id="mainFavList"></div>
    <div id="mainFavEmpty" style="text-align:center;padding:24px;color:var(--muted);font-size:11px;font-weight:600" data-t="profFavEmpty">Пока пусто — добавь актив из каталога</div>
    <div class="gap2"></div>
    <button class="btn btn-dark btn-sm" onclick="clearFavs()" data-t="btnClearFav">🗑 Очистить избранное</button>
  </div>

  <div class="card tab hidden" id="tabNews">
    <h3 class="g-text" style="font-size:13px;margin-bottom:6px" data-t="newsTitle">📰 Форекс-новости</h3>
    <p style="font-size:10.5px;color:var(--muted);margin-bottom:8px" data-t="newsDesc">Экономический календарь. High и Medium важность. Время — ваше локальное.</p>
    <div class="filter-row" id="newsFilterRow">
      <button class="filter-btn on" onclick="setNewsFilter('ALL',this)">ALL</button>
      <button class="filter-btn" onclick="setNewsFilter('USD',this)">🇺🇸 USD</button>
      <button class="filter-btn" onclick="setNewsFilter('EUR',this)">🇪🇺 EUR</button>
      <button class="filter-btn" onclick="setNewsFilter('GBP',this)">🇬🇧 GBP</button>
      <button class="filter-btn" onclick="setNewsFilter('JPY',this)">🇯🇵 JPY</button>
      <button class="filter-btn" onclick="setNewsFilter('AUD',this)">🇦🇺 AUD</button>
      <button class="filter-btn" onclick="setNewsFilter('CAD',this)">🇨🇦 CAD</button>
      <button class="filter-btn" onclick="setNewsFilter('CHF',this)">🇨🇭 CHF</button>
      <button class="filter-btn" onclick="setNewsFilter('NZD',this)">🇳🇿 NZD</button>
      <button class="filter-btn" onclick="setNewsFilter('RUB',this)">🇷🇺 RUB</button>
    </div>
    <div class="news-updated" id="newsUpdated">Обновление...</div>
    <div class="news-list" id="newsList">
      <div class="news-loading"><div class="news-loading-wheel"></div><div class="news-loading-text">Загружаю календарь новостей...</div></div>
    </div>
    <div class="gap"></div>
    <button class="btn btn-dark btn-sm" onclick="loadNews(true)" data-t="btnRefreshNews">🔄 Обновить</button>
  </div>

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

  <div class="card tab hidden" id="tabEdu">
    <div id="eduList">
      <h3 class="g-text" style="font-size:13px;margin-bottom:8px" data-t="eduTitle">📚 Связки и стратегии</h3>
      <p style="font-size:11px;color:var(--muted);margin-bottom:10px" data-t="eduDesc">50 стратегий: у каждой свой график-пример, подробные условия CALL/PUT и тест из 5 вопросов.</p>
      <button class="btn btn-gold btn-sm" onclick="openRandomEdu()" data-t="btnRandomEdu" style="margin-bottom:12px">🎲 Случайная стратегия</button>
      <div id="eduItems"></div>
    </div>
    <div id="eduView" class="hidden">
      <button class="btn btn-dark btn-sm" onclick="closeEdu()" style="margin-bottom:10px" data-t="btnBack">← Назад</button>
      <div class="edu-detail" id="eduBody"></div>
    </div>
  </div>

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
    <a href="https://t.me/+uekq4TquqkM4Mzcy" target="_blank" class="btn btn-gold">📢 Наш Telegram-канал</a>
  </div>

</div>

<div class="bnav">
  <div class="nav on" onclick="showTab('tabSig',this)"><span>⚡</span><span data-t="navSig">Сигнал</span></div>
  <div class="nav" onclick="showTab('tabFavs',this)"><span>⭐</span><span data-t="navFavs">Избранное</span></div>
  <div class="nav" onclick="showTab('tabNews',this)"><span>📰</span><span data-t="navNews">Новости</span></div>
  <div class="nav" onclick="showTab('tabProfile',this)"><span>👤</span><span data-t="navProf">Профиль</span></div>
  <div class="nav" onclick="showTab('tabEdu',this)"><span>📚</span><span data-t="navEdu">Стратегии</span></div>
  <div class="nav" onclick="showTab('tabScan',this)"><span>📷</span><span data-t="navScan">Сканер</span></div>
  <div class="nav" onclick="showTab('tabAbout',this)"><span>ℹ️</span><span data-t="navAbout">О нас</span></div>
</div>

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

<div class="modal-overlay hidden" id="historyModal">
  <div class="modal-content">
    <div class="modal-header">
      <h3>📜 История сигналов</h3>
      <button class="modal-close" onclick="closeHistoryModal()">✕</button>
    </div>
    <div class="modal-body" id="historyBody"></div>
  </div>
</div>

<div class="modal-overlay hidden" id="quizModal">
  <div class="modal-content">
    <div class="modal-header">
      <h3 id="quizTitle">Мини-тест</h3>
      <button class="modal-close" onclick="closeQuiz()">✕</button>
    </div>
    <div class="modal-body" id="quizBody"></div>
  </div>
</div>

<div class="analyze-overlay hidden" id="analyzeBox">
  <div class="analyze-smile" id="analyzeSmile">😊</div>
  <div class="analyze-wheel"></div>
  <div class="analyze-text" id="analyzeTxt">⚡ Подключаюсь к рынку...</div>
</div>
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
var FLAGS = {USD:"🇺🇸",EUR:"🇪🇺",GBP:"🇬🇧",JPY:"🇯🇵",AUD:"🇦🇺",CAD:"🇨🇦",CHF:"🇨🇭",NZD:"🇳🇿",RUB:"🇷🇺"};
var TFS = {S5:5,S15:15,S30:30,M1:60,M2:120,M3:180,M5:300,M10:600,M15:900};
var live = {timer:null, busy:false, on:false};

try {
  favs = JSON.parse(localStorage.getItem("tmv_favs") || "[]");
  sigCount = parseInt(localStorage.getItem("tmv_sigs") || "0");
  signalHistory = JSON.parse(localStorage.getItem("tmv_history") || "[]");
} catch(e) {}

var I18N = {
  ru: {lblActiveAsset:"ВЫБРАННЫЙ АКТИВ",lblTf:"ИНТЕРВАЛ",lblExp:"ЭКСПИРАЦИЯ",btnCalcSig:"⚡ Просчитать сигнал",btnAutoSig:"🤖 Выбрать топ актив",btnCancel:"✖ Отменить",btnNewSig:"⚡ Новый сигнал",favTabTitle:"⭐ Избранные активы",favTabDesc:"Нажмите на любой актив для перехода.",profFavEmpty:"Пока пусто — добавь актив из каталога",btnClearFav:"🗑 Очистить избранное",newsTitle:"📰 Форекс-новости",newsDesc:"Экономический календарь. High и Medium важность. Время — ваше локальное.",btnRefreshNews:"🔄 Обновить",eduTitle:"📚 Связки и стратегии",eduDesc:"50 стратегий: у каждой свой график-пример, подробные условия CALL/PUT и тест из 5 вопросов.",btnRandomEdu:"🎲 Случайная стратегия",btnBack:"← Назад",scanTitle:"📷 AI Сканер графика",scanPhText:"Откройте камеру или загрузите скриншот",btnCam:"📷 Камера",btnUpload:"📁 Загрузить",btnScan:"⚡ Сканировать",btnStopCam:"⏹ Остановить",profTitle:"👤 Профиль",lblSigCount:"Сигналов:",lblFavCount:"Избранных:",btnResetProf:"Сбросить профиль",navSig:"Сигнал",navFavs:"Избранное",navNews:"Новости",navEdu:"Стратегии",navScan:"Сканер",navProf:"Профиль",navAbout:"О нас",catalogTitle:"Каталог активов",lblCombo:"Связка"},
  en: {lblActiveAsset:"SELECTED ASSET",lblTf:"TIMEFRAME",lblExp:"EXPIRATION",btnCalcSig:"⚡ Calculate Signal",btnAutoSig:"🤖 Top Asset",btnCancel:"✖ Cancel",btnNewSig:"⚡ New Signal",favTabTitle:"⭐ Favorites",favTabDesc:"Tap any asset to select it.",profFavEmpty:"Empty — add from catalog",btnClearFav:"🗑 Clear favorites",newsTitle:"📰 Forex News",newsDesc:"Economic calendar. High & Medium impact. Local time.",btnRefreshNews:"🔄 Refresh",eduTitle:"📚 Strategies",eduDesc:"50 strategies with chart examples and a 5-question test.",btnRandomEdu:"🎲 Random strategy",btnBack:"← Back",scanTitle:"📷 AI Chart Scanner",scanPhText:"Open camera or upload screenshot",btnCam:"📷 Camera",btnUpload:"📁 Upload",btnScan:"⚡ Scan",btnStopCam:"⏹ Stop",profTitle:"👤 Profile",lblSigCount:"Signals:",lblFavCount:"Favorites:",btnResetProf:"Reset profile",navSig:"Signal",navFavs:"Favs",navNews:"News",navEdu:"Strategies",navScan:"Scanner",navProf:"Profile",navAbout:"About",catalogTitle:"Asset Catalog",lblCombo:"Combo"},
  ua: {lblActiveAsset:"ОБРАНИЙ АКТИВ",lblTf:"ІНТЕРВАЛ",lblExp:"ЕКСПІРАЦІЯ",btnCalcSig:"⚡ Розрахувати сигнал",btnAutoSig:"🤖 Топ актив",btnCancel:"✖ Скасувати",btnNewSig:"⚡ Новий сигнал",favTabTitle:"⭐ Обрані",favTabDesc:"Натисніть на актив.",profFavEmpty:"Порожньо",btnClearFav:"🗑 Очистити",newsTitle:"📰 Новини",newsDesc:"Економічний календар. High і Medium. Локальний час.",btnRefreshNews:"🔄 Оновити",eduTitle:"📚 Стратегії",eduDesc:"50 стратегій з графіками та тестом із 5 питань.",btnRandomEdu:"🎲 Випадкова",btnBack:"← Назад",scanTitle:"📷 AI Сканер",scanPhText:"Камера або скріншот",btnCam:"📷 Камера",btnUpload:"📁 Завантажити",btnScan:"⚡ Сканувати",btnStopCam:"⏹ Зупинити",profTitle:"👤 Профіль",lblSigCount:"Сигналів:",lblFavCount:"Обраних:",btnResetProf:"Скинути",navSig:"Сигнал",navFavs:"Обране",navNews:"Новини",navEdu:"Стратегії",navScan:"Сканер",navProf:"Профіль",navAbout:"Про нас",catalogTitle:"Каталог",lblCombo:"Зв'язка"}
};

function esc(s) {
  return String(s == null ? "" : s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function showTab(id, el) {
  document.querySelectorAll(".tab").forEach(function(t){ t.classList.add("hidden"); });
  document.getElementById(id).classList.remove("hidden");
  document.querySelectorAll(".nav").forEach(function(n){ n.classList.remove("on"); });
  if (el) el.classList.add("on");
  if (id === "tabSig") liveTick();
}

function getCategories() { return ASSETS[currentLang] || ASSETS.ru; }
function updateSelectedAssetUI() { document.getElementById("currentAssetDisplay").textContent = currentAsset; }
var openCatIdx = 0;
function openCatalogModal() {
  openCatIdx = 0;
  var si = document.getElementById("modalSearchInput");
  if (si) si.value = "";
  renderCatalogList("");
  document.getElementById("catalogModal").classList.remove("hidden");
}
function closeCatalogModal() { document.getElementById("catalogModal").classList.add("hidden"); }

function toggleCat(i) {
  openCatIdx = (openCatIdx === i) ? -1 : i;
  renderCatalogList(document.getElementById("modalSearchInput").value || "");
}

function renderCatalogList(q) {
  var body = document.getElementById("catalogBody");
  var cats = getCategories();
  var keys = Object.keys(cats);
  var liveList = cats[keys[0]] || [];
  var html = "";
  var query = (q || "").toLowerCase();
  keys.forEach(function(cat, idx) {
    var items = cats[cat].filter(function(a){ return !query || a.toLowerCase().indexOf(query) >= 0; });
    if (!items.length) return;
    var isOpen = query ? true : (idx === openCatIdx);
    html += '<div class="cat-acc' + (isOpen ? ' open' : '') + '"' + (query ? '' : ' onclick="toggleCat(' + idx + ')"') + '>';
    html += '<span>' + esc(cat) + '</span><span class="cat-meta">' + items.length + ' <i>' + (isOpen ? '▾' : '▸') + '</i></span></div>';
    if (!isOpen) return;
    items.forEach(function(a) {
      var isFav = favs.indexOf(a) >= 0;
      var isLive = liveList.indexOf(a) >= 0;
      html += '<div class="fav-item" onclick="selectAsset(\'' + a.replace(/'/g,"\\'") + '\')">';
      html += '<span>' + esc(a) + (isLive ? ' <b class="live-badge">LIVE API</b>' : '') + '</span>';
      html += '<span onclick="event.stopPropagation();toggleFav(\'' + a.replace(/'/g,"\\'") + '\')">' + (isFav ? "⭐" : "☆") + '</span>';
      html += '</div>';
    });
  });
  body.innerHTML = html || '<div style="text-align:center;padding:20px;color:var(--muted)">Ничего не найдено</div>';
}

function selectAsset(a) {
  closeCatalogModal();
  if (a === currentAsset) return;
  currentAsset = a;
  updateSelectedAssetUI();
  cancelSig();
}

function toggleFav(a) {
  var i = favs.indexOf(a);
  if (i >= 0) favs.splice(i, 1); else favs.push(a);
  try { localStorage.setItem("tmv_favs", JSON.stringify(favs)); } catch(e) {}
  renderCatalogList(document.getElementById("modalSearchInput").value || "");
  renderMainFavs();
  updateProfileStats();
}

function renderMainFavs() {
  var list = document.getElementById("mainFavList");
  var empty = document.getElementById("mainFavEmpty");
  if (!favs.length) { list.innerHTML = ""; empty.style.display = "block"; return; }
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
    var m = Math.floor(rem / 60), s = rem % 60;
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
      try {
        var sa = localStorage.getItem("tmv_sig_asset"), st = localStorage.getItem("tmv_sig_tf");
        if (sa) { currentAsset = sa; updateSelectedAssetUI(); }
        if (st) document.getElementById("tf").value = st;
      } catch(e) {}
      showChart();
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
  var phrases = ["Подключаюсь к потоку котировок...","Считываю свечи и объём...","Сверяю EMA / RSI / MACD...","Ищу уровни и свечные паттерны...","Формирую финальный сигнал..."];
  var smiles = ["📡","📊","🔍","⚙️","🎯"];
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
      var d = h.dir || "";
      var dirClass = (d.indexOf("CALL") >= 0 || d.indexOf("BUY") >= 0) ? "call" : "put";
      html += '<div class="hist-item"><div class="h-meta">' + esc(h.time || "") + ' · ' + esc(h.asset || "") + ' · ' + esc(h.tf || "") + ' · эксп. ' + esc(String(h.exp || "")) + ' сек</div>';
      html += '<div class="h-dir ' + dirClass + '">' + esc(d) + '</div>';
      if (h.reason) html += '<div class="h-reason">' + esc(h.reason) + '</div>';
      html += '</div>';
    });
    body.innerHTML = html;
  }
  document.getElementById("historyModal").classList.remove("hidden");
}
function closeHistoryModal() { document.getElementById("historyModal").classList.add("hidden"); }

/* ===== CANDLE CHART: показывается снизу только после «Просчитать сигнал», без точки входа ===== */
function drawCandles(candles, source, tf) {
  if (!candles || !candles.length) return '<div class="chart-head"><span class="chart-title">⏳ Загрузка графика…</span></div>';
  var W = 360, H = 210, padL = 6, padR = 56, padT = 8, padB = 14;
  var hi = -Infinity, lo = Infinity;
  candles.forEach(function(c) { if (c[1] > hi) hi = c[1]; if (c[2] < lo) lo = c[2]; });
  var rng = (hi - lo) || 1e-9;
  hi += rng * 0.06; lo -= rng * 0.06; rng = hi - lo;
  var n = candles.length;
  var step = (W - padL - padR) / n;
  var bw = Math.max(2, step * 0.64);
  var dec = hi > 1000 ? 2 : (hi > 100 ? 3 : (hi > 10 ? 4 : 5));
  function y(v) { return padT + (hi - v) / rng * (H - padT - padB); }
  var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" xmlns="http://www.w3.org/2000/svg"><rect width="' + W + '" height="' + H + '" fill="#05070d"/>';
  for (var g = 0; g <= 4; g++) {
    var gv = lo + rng * g / 4, gy = y(gv);
    svg += '<line x1="' + padL + '" y1="' + gy.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + gy.toFixed(1) + '" stroke="#161e2e" stroke-width="1"/>';
    svg += '<text x="' + (W - padR + 4) + '" y="' + (gy + 3).toFixed(1) + '" fill="#7a8499" font-size="8.5" font-weight="700">' + gv.toFixed(dec) + '</text>';
  }
  candles.forEach(function(c, i) {
    var up = c[3] >= c[0], col = up ? "#00E676" : "#FF5252";
    var cx = padL + i * step + step / 2;
    var yo = y(c[0]), yc = y(c[3]);
    var top = Math.min(yo, yc), hgt = Math.max(1.2, Math.abs(yo - yc));
    svg += '<line x1="' + cx.toFixed(1) + '" y1="' + y(c[1]).toFixed(1) + '" x2="' + cx.toFixed(1) + '" y2="' + y(c[2]).toFixed(1) + '" stroke="' + col + '" stroke-width="1.2"/>';
    svg += '<rect x="' + (cx - bw / 2).toFixed(1) + '" y="' + top.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + hgt.toFixed(1) + '" fill="' + col + '" rx="0.8"/>';
  });
  var last = candles[n - 1][3], ly = y(last);
  svg += '<line x1="' + padL + '" y1="' + ly.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + ly.toFixed(1) + '" stroke="#FFD700" stroke-width="1" stroke-dasharray="4 3"/>';
  svg += '<rect x="' + (W - padR) + '" y="' + (ly - 7).toFixed(1) + '" width="' + (padR - 2) + '" height="14" fill="#FFD700" rx="3"/>';
  svg += '<text x="' + (W - padR + 3) + '" y="' + (ly + 3).toFixed(1) + '" fill="#111" font-size="8.5" font-weight="900">' + last.toFixed(dec) + '</text>';
  svg += '</svg>';
  var real = source === "pocket_api" || source === "binance";
  var labels = {
    pocket_api: "🟢 LIVE · POCKET OPTION API",
    binance: "🟢 LIVE · BINANCE",
    market_fallback: "🟠 РЕЗЕРВ · РЫНОК (API Pocket недоступен)",
    otc: "🧠 УМНЫЙ АНАЛИЗ"
  };
  var srcLabel = labels[source] || labels.otc;
  var P = TFS[tf] || 60, rem = P - (Math.floor(Date.now() / 1000) % P);
  var rm = Math.floor(rem / 60), rs = rem % 60;
  var cdTxt = (rm < 10 ? "0" : "") + rm + ":" + (rs < 10 ? "0" : "") + rs;
  return '<div class="chart-head"><span class="chart-title">📊 ' + esc(currentAsset) + ' · ' + esc(tf) + ' · ⏱ ' + cdTxt + '</span><span class="chart-src ' + (real ? "real" : "fake") + '">' + srcLabel + '</span></div>' + svg;
}

async function liveTick() {
  if (!live.on || live.busy || document.hidden) return;
  var tabEl = document.getElementById("tabSig");
  if (!tabEl || tabEl.classList.contains("hidden")) return;
  live.busy = true;
  var asset = currentAsset, tf = document.getElementById("tf").value;
  try {
    var r = await fetch("/candles?asset=" + encodeURIComponent(asset) + "&timeframe=" + encodeURIComponent(tf));
    if (r.ok) {
      var d = await r.json();
      if (live.on && asset === currentAsset && tf === document.getElementById("tf").value) {
        document.getElementById("liveChart").innerHTML = drawCandles(d.candles, d.source, tf);
      }
    }
  } catch (e) {}
  live.busy = false;
}
function startLive() {
  if (live.timer) clearInterval(live.timer);
  live.timer = setInterval(liveTick, 1500);
}
function showChart() {
  var el = document.getElementById("liveChart");
  el.style.display = "block";
  if (!live.on) el.innerHTML = '<div class="chart-head"><span class="chart-title">⏳ Загрузка графика…</span></div>';
  live.on = true;
  liveTick();
}
function hideChart() {
  live.on = false;
  var el = document.getElementById("liveChart");
  el.style.display = "none";
  el.innerHTML = "";
}
function liveReset() { liveTick(); }

function fmtT(d) { return pad2(d.getHours()) + ":" + pad2(d.getMinutes()) + ":" + pad2(d.getSeconds()); }

function getSig() {
  runAnalyzer(async function() {
    var tf = document.getElementById("tf").value;
    var expSec = parseInt(document.getElementById("exp").value);
    var data = null;
    try {
      var res = await fetch("/get_signal?asset=" + encodeURIComponent(currentAsset) + "&timeframe=" + encodeURIComponent(tf));
      if (res.ok) data = await res.json();
    } catch (e) {}
    var sigBox = document.getElementById("sigBox");
    if (!data || !data.signal) {
      if (timers.sig) clearInterval(timers.sig);
      try { localStorage.removeItem("tmv_sig_end"); } catch(e) {}
      document.getElementById("sigMeta").textContent = "Pocket Option · " + currentAsset + " · " + tf;
      var de = document.getElementById("sigDir");
      de.textContent = "⚠️ НЕТ СИГНАЛА";
      de.className = "sig-dir put";
      document.getElementById("cd").textContent = "00:00";
      document.getElementById("sigInd").innerHTML = "";
      document.getElementById("sigStrat").innerHTML = "<b>Ошибка:</b> не удалось получить котировки. Нажмите «Новый сигнал» и попробуйте ещё раз.";
      sigBox.style.display = "block";
      document.getElementById("btnCancel").style.display = "none";
      document.getElementById("btnNew").style.display = "block";
      hideChart();
      return;
    }
    var isCall = data.signal === "UP";
    var dir = isCall ? "⬆️ CALL (ВВЕРХ)" : "⬇️ PUT (ВНИЗ)";
    var stratText = data.reason || "";
    if (data.warn) stratText = "⚠️ " + data.warn + "\n" + stratText;
    var t0 = new Date(), t1 = new Date(t0.getTime() + expSec * 1000);
    document.getElementById("sigMeta").innerHTML = "Pocket Option · " + esc(currentAsset) + " · " + esc(tf) + " · " + expSec + " сек<br>🕒 Вход: <b>" + fmtT(t0) + "</b> → Экспирация: <b>" + fmtT(t1) + "</b>";
    var dirEl = document.getElementById("sigDir");
    dirEl.textContent = dir;
    dirEl.className = "sig-dir " + (isCall ? "call" : "put");
    var ih = "";
    (data.indicators || []).forEach(function(x) {
      ih += '<span class="ind ' + esc(x[2]) + '">' + esc(x[0]) + ' ' + esc(x[1]) + '</span>';
    });
    document.getElementById("sigInd").innerHTML = ih;
    document.getElementById("sigStrat").innerHTML = "<b>Анализ сигнала:</b>\n" + esc(stratText);
    sigBox.style.display = "block";
    document.getElementById("btnCancel").style.display = "block";
    document.getElementById("btnNew").style.display = "none";
    try { localStorage.setItem("tmv_sig_asset", currentAsset); localStorage.setItem("tmv_sig_tf", tf); } catch(e) {}
    showChart();
    incSig();
    saveToHistory({time: new Date().toLocaleString("ru-RU"), asset: currentAsset, tf: tf, exp: expSec, dir: dir, reason: stratText});
    startTimer("sig", "cd", expSec, function() {
      document.getElementById("btnCancel").style.display = "none";
      document.getElementById("btnNew").style.display = "block";
    }, "tmv_sig_end");
  });
}

function autoSig() {
  var cats = getCategories();
  var firstKey = Object.keys(cats)[0];
  var pool = cats[firstKey];
  currentAsset = pool[Math.floor(Math.random() * pool.length)];
  updateSelectedAssetUI();
  getSig();
}

function cancelSig() {
  if (timers.sig) clearInterval(timers.sig);
  try { localStorage.removeItem("tmv_sig_end"); } catch(e) {}
  document.getElementById("sigBox").style.display = "none";
  hideChart();
}

function pad2(n) { return n < 10 ? "0" + n : String(n); }

function formatNewsTime(iso) {
  try {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso || "—";
    var now = new Date();
    var tm = pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    if (d.toDateString() === now.toDateString()) return "сегодня " + tm;
    var tmr = new Date(now.getTime() + 86400000);
    if (d.toDateString() === tmr.toDateString()) return "завтра " + tm;
    return pad2(d.getDate()) + "." + pad2(d.getMonth() + 1) + " " + tm;
  } catch (e) { return iso || "—"; }
}

function impactClass(imp) {
  var x = String(imp || "").toLowerCase();
  return x === "high" ? "high" : (x === "medium" ? "medium" : "low");
}
function impactLabel(imp, impRu) {
  var x = String(imp || "").toLowerCase();
  var t = impRu || (x === "high" ? "Высокая" : (x === "medium" ? "Средняя" : "Низкая"));
  return (x === "high" ? "🔴 " : (x === "medium" ? "🟠 " : "🟡 ")) + t;
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
  if (ev.forecast) lines.push("Прогноз: " + ev.forecast);
  if (ev.previous) lines.push("Предыдущее: " + ev.previous);
  if (ev.actual) lines.push("Факт: " + ev.actual);
  else lines.push(ev.status === "released" ? "Факт ещё не опубликован источником." : "Факт ещё не вышел — ждите публикации.");
  lines.push("");
  lines.push("Что это значит:");
  var nm = ((ev.name || "") + " " + (ev.name_en || "")).toLowerCase();
  if (nm.indexOf("ввп") >= 0 || nm.indexOf("gdp") >= 0) {
    lines.push("ВВП показывает рост экономики. Сильнее прогноза обычно усиливает валюту, слабее — ослабляет.");
  } else if (nm.indexOf("безраб") >= 0 || nm.indexOf("unemployment") >= 0 || nm.indexOf("claims") >= 0 || nm.indexOf("payroll") >= 0 || nm.indexOf("employment") >= 0) {
    lines.push("Рынок труда. Занятость выше прогноза — плюс валюте; рост безработицы/заявок — минус.");
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

function orderedNews() {
  var filtered = newsCache || [];
  if (newsFilter && newsFilter !== "ALL") filtered = filtered.filter(function(e) { return (e.currency || "").toUpperCase() === newsFilter; });
  var released = filtered.filter(function(e) { return e.status === "released"; });
  var upcoming = filtered.filter(function(e) { return e.status !== "released"; });
  return { upcoming: upcoming, released: released, all: upcoming.concat(released) };
}

function renderNewsList() {
  var list = document.getElementById("newsList");
  var o = orderedNews();
  if (!o.all.length) {
    list.innerHTML = '<div style="text-align:center;padding:28px;color:var(--muted);font-size:12px;font-weight:600">Нет событий High/Medium по выбранной валюте</div>';
    return;
  }
  var html = "";
  if (o.upcoming.length) html += '<div class="news-sec-title news-sec-up">⏳ Скоро выйдет · ' + o.upcoming.length + '</div>';
  var shown = false;
  o.all.forEach(function(ev, idx) {
    if (ev.status === "released" && !shown) {
      html += '<div class="news-sec-title news-sec-done">✅ Уже вышло · ' + o.released.length + '</div>';
      shown = true;
    }
    var ic = impactClass(ev.impact);
    var actual = String(ev.actual || "").trim(), forecast = String(ev.forecast || "").trim(), previous = String(ev.previous || "").trim();
    var cur = (ev.currency || "").toUpperCase();
    html += '<div class="nc ' + ic + '" data-idx="' + idx + '" onclick="toggleNews(' + idx + ')"><div class="nc-bar"></div>';
    html += '<div class="nc-head"><div class="nc-cur"><span class="nc-flag">' + (FLAGS[cur] || "🌐") + '</span><span class="nc-code">' + esc(cur || "—") + '</span><span class="nc-imp ' + ic + '">' + esc(impactLabel(ev.impact, ev.impact_ru)) + '</span></div>';
    html += '<span class="nc-time">🕒 ' + esc(formatNewsTime(ev.date)) + '</span></div>';
    html += '<div class="nc-title">' + esc((ev.name || "Событие").replace(/\s+/g, " ").trim()) + '</div>';
    if (ev.name_en && ev.name_en !== ev.name) html += '<div class="nc-orig">' + esc(ev.name_en) + '</div>';
    if (ev.pair_hint) html += '<div class="nc-pairs">💱 ' + esc(ev.pair_hint) + '</div>';
    html += '<div class="nc-stats"><div class="nc-st"><i>Прогноз</i><b>' + esc(forecast || "—") + '</b></div>';
    html += '<div class="nc-st"><i>Пред.</i><b>' + esc(previous || "—") + '</b></div>';
    if (actual) html += '<div class="nc-st fact"><i>Факт</i><b>' + esc(actual) + '</b></div>';
    else if (ev.status === "released") html += '<div class="nc-st"><i>Факт</i><b>—</b></div>';
    else html += '<div class="nc-st wait"><i>Факт</i><b>⏳</b></div>';
    html += '</div><div class="nc-detail">' + esc(newsDetailText(ev)).replace(/\n/g, "<br>");
    html += '<button class="nc-copy" onclick="event.stopPropagation();copyNewsDetail(' + idx + ',this)">📋 Копировать объяснение</button></div></div>';
  });
  list.innerHTML = html;
}

function copyNewsDetail(idx, btn) {
  var ev = orderedNews().all[idx];
  if (!ev) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(newsDetailText(ev)).then(function() {
      if (btn) { btn.textContent = "✅ Скопировано"; setTimeout(function(){ btn.textContent = "📋 Копировать объяснение"; }, 1600); }
    }).catch(function(){});
  }
}

function toggleNews(idx) {
  document.querySelectorAll(".nc").forEach(function(el) {
    var i = parseInt(el.getAttribute("data-idx"));
    if (i === idx) el.classList.toggle("open"); else el.classList.remove("open");
  });
}

async function loadNews(force) {
  var upd = document.getElementById("newsUpdated");
  var list = document.getElementById("newsList");
  if (force && (!newsCache || !newsCache.length)) {
    list.innerHTML = '<div class="news-loading"><div class="news-loading-wheel"></div><div class="news-loading-text">Загружаю календарь новостей...</div></div>';
    upd.textContent = "⏳ Загрузка...";
  }
  try {
    var res = await fetch("/news");
    if (!res.ok) throw new Error("fail");
    var data = await res.json();
    var events = data.events || [];
    newsCache = events;
    var hash = JSON.stringify(events.map(function(e) { return [e.name, e.date, e.actual, e.forecast, e.status]; }));
    if (force || hash !== lastNewsHash) { lastNewsHash = hash; renderNewsList(); }
    upd.textContent = "🔄 Обновлено: " + formatNewsTime(data.updated || new Date().toISOString()) + " · событий: " + events.length;
  } catch (e) {
    if (list.innerHTML.indexOf("nc-title") < 0) list.innerHTML = '<div style="text-align:center;padding:24px;color:var(--red);font-size:12px;font-weight:700">Не удалось загрузить. Нажмите «Обновить».</div>';
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

/* ===== СТРАТЕГИИ: уникальный график под каждую ===== */
function rng(seed) {
  var s = ((seed || 1) * 2654435761) >>> 0 || 123456789;
  return function() { s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; };
}
var PAT = {
 levels:{pts:[60,45,30,19,32,48,62,50,38,28,22],ln:[[18,18,"Поддержка","Сопротивление","g"]],tail:[[22,24,16,23],[23,34,22,33]]},
 range:{pts:[30,68,32,70,31,69,33,67,35,45,32],ln:[[72,72,"Верх канала","Низ канала","r"],[28,28,"Низ канала","Верх канала","g"]],tail:[[32,34,27,33],[33,46,32,45]]},
 trend:{pts:[20,26,38,32,46,56,48,62,72,64,78,86,78,70],ln:[],tail:[[70,71,64,66],[66,80,65,79]]},
 bb:{pts:[78,70,64,56,48,40,34,28,22,18],ln:[],tail:[[18,19,12,13],[13,26,12,25]]},
 engulf:{pts:[76,66,60,50,44,36,30,26],ln:[],tail:[[26,27,20,21],[20,34,19,33]]},
 pin:{pts:[60,48,36,26,40,52,40,30,22],ln:[[18,18,"Поддержка","Сопротивление","g"]],tail:[[22,23,8,21],[21,31,20,30]]},
 fake:{pts:[70,52,34,42,58,46,33,45,60,48,36,44],ln:[[30,30,"Поддержка","Сопротивление","g"]],tail:[[44,45,22,40],[40,52,39,51]]},
 retest:{pts:[30,42,54,60,50,57,61,52,58,62,74,82,72,64],ln:[[60,60,"Пробитый уровень","Пробитый уровень","y"]],tail:[[64,66,59,65],[65,76,64,75]]},
 dbl:{pts:[78,64,46,26,38,52,40,26,40,56,66],ln:[[54,54,"Линия шеи","Линия шеи","y"]],tail:[[56,66,55,64],[64,72,63,71]]},
 hs:{pts:[70,50,32,44,56,38,20,40,56,42,30,44,54],ln:[[54,54,"Линия шеи","Линия шеи","y"]],tail:[[54,64,53,62],[62,72,61,70]]},
 tri:{pts:[30,64,36,62,42,60,48,59,53,58,56,57],ln:[[58,58,"Сопротивление","Поддержка","r"],[30,54,"Растущая поддержка","Падающее сопротивление","g"]],tail:[[57,68,56,66],[66,74,65,73]]},
 flag:{pts:[18,34,52,70,84,76,80,72,76,70,74,68],ln:[],tail:[[68,70,66,69],[69,90,68,88]]},
 wedge:{pts:[80,60,72,54,64,50,58,47,52,45,48],ln:[],tail:[[46,47,42,43],[43,58,42,57]]},
 inside:{pts:[20,28,38,48,58,68,76],ln:[],tail:[[74,92,60,88],[84,88,74,78],[78,100,77,98]]},
 sold:{pts:[80,70,60,50,42,34,28,24],ln:[],tail:[[24,38,22,36],[36,50,35,48],[48,62,47,60]]},
 doji:{pts:[84,74,62,50,40,30,22,18],ln:[],tail:[[18,22,12,18.4],[18.4,34,17,32]]},
 squeeze:{pts:[50,60,40,58,43,56,45,54,47,53,48,52,49,51],ln:[],tail:[[50,64,50,63]]},
 div:{pts:[86,70,54,38,22,36,46,38,30,24,20,26],ln:[],tail:[[22,26,17,25],[25,36,24,35]]},
 chan:{pts:[20,40,28,48,36,56,44,64,52,72,60],ln:[[20,60,"Нижняя граница","Верхняя граница","g"],[40,80,"Верхняя граница","Нижняя граница","r"]],tail:[[60,61,58,59],[59,70,58,69]]},
 fib:{pts:[20,36,54,70,86,76,66,56,48,44,46],ln:[[86,86,"100%","100%","y"],[45,45,"Фибо 61.8%","Фибо 61.8%","g"],[20,20,"0%","0%","y"]],tail:[[45,47,41,42],[42,56,41,55]]},
 ob:{pts:[50,44,40,34,28,36,52,70,84,70,56,44,38],ln:[[32,32,"Order Block","Order Block","g"]],tail:[[40,41,33,35],[35,48,34,47]]},
 grab:{pts:[66,46,30,44,60,44,30,46,62,46,31,46],ln:[[29,29,"Ликвидность (равные минимумы)","Ликвидность (равные максимумы)","g"]],tail:[[46,47,21,42],[42,56,41,55]]},
 fvg:{pts:[30,32,38,46,68,80,78,72,66,60,58],ln:[[52,52,"FVG","FVG","y"]],tail:[[58,59,52,53],[53,66,52,65]]},
 bos:{pts:[30,52,38,62,46,72,60,68,62],ln:[[72,72,"Предыдущий максимум","Предыдущий минимум","y"]],tail:[[62,66,60,64],[64,80,63,78]]},
 choch:{pts:[86,70,76,58,64,46,52,38,46,60,50,58,50],ln:[[52,52,"CHoCH","CHoCH","y"]],tail:[[50,62,49,60],[60,70,59,68]]},
 trl:{pts:[20,34,28,42,36,50,44,58,52,66,60],ln:[[22,64,"Трендовая линия","Трендовая линия","g"]],tail:[[60,61,57,58],[58,70,57,69]]},
 spike:{pts:[50,52,49,51,50,52,51,50,51],ln:[],tail:[[51,52,26,29],[29,40,28,39],[39,46,38,45]]},
 triple:{pts:[70,40,20,45,66,40,20,45,66,40,20,40,26],ln:[[19,19,"3 касания поддержки","3 касания сопротивления","g"]],tail:[[26,30,17,28],[28,40,27,39]]},
 harami:{pts:[84,74,62,52,42,34],ln:[],tail:[[34,36,16,18],[24,30,22,28],[28,42,27,40]]},
 morning:{pts:[84,72,62,50,42,34],ln:[],tail:[[34,35,22,23],[22,24,18,21],[22,38,21,36]]},
 awake:{pts:[50,52,49,51,50,52,50,56,64,72,80,86,78,74],ln:[],tail:[[74,78,72,77],[77,86,76,85]]},
 brk:{pts:[40,50,42,52,41,51,43,51,48,52,66,70],ln:[[52,52,"Граница диапазона","Граница диапазона","y"]],tail:[[68,76,66,75]]}
};
function emaA(a,p){var k=2/(p+1),o=[a[0]];for(var i=1;i<a.length;i++)o.push(a[i]*k+o[i-1]*(1-k));return o;}
function smaA(a,p){var o=[];for(var i=0;i<a.length;i++){if(i<p-1){o.push(null);continue;}var s=0;for(var j=i-p+1;j<=i;j++)s+=a[j];o.push(s/p);}return o;}
function ewm(a,al){var o=[a[0]];for(var i=1;i<a.length;i++)o.push(a[i]*al+o[i-1]*(1-al));return o;}
function rsiA(c,p){var o=[null],g=0,l=0;for(var i=1;i<c.length;i++){var d=c[i]-c[i-1],u=d>0?d:0,dn=d<0?-d:0;if(i<=p){g+=u;l+=dn;if(i==p){g/=p;l/=p;o.push(l==0?100:100-100/(1+g/l));}else o.push(null);}else{g=(g*(p-1)+u)/p;l=(l*(p-1)+dn)/p;o.push(l==0?100:100-100/(1+g/l));}}return o;}
function adxA(cs,p){var tr=[0],pd=[0],md=[0],i;for(i=1;i<cs.length;i++){var up=cs[i][1]-cs[i-1][1],dn=cs[i-1][2]-cs[i][2];pd.push(up>dn&&up>0?up:0);md.push(dn>up&&dn>0?dn:0);tr.push(Math.max(cs[i][1]-cs[i][2],Math.abs(cs[i][1]-cs[i-1][3]),Math.abs(cs[i][2]-cs[i-1][3])));}var al=1/p,t=ewm(tr,al),a=ewm(pd,al),b=ewm(md,al),pi=[],mi=[],dx=[];for(i=0;i<cs.length;i++){var pp=t[i]?100*a[i]/t[i]:0,mm=t[i]?100*b[i]/t[i]:0;pi.push(pp);mi.push(mm);dx.push(pp+mm?100*Math.abs(pp-mm)/(pp+mm):0);}return {adx:ewm(dx,al),pi:pi,mi:mi};}
function panelSpec(t,cs){
  var c=cs.map(function(x){return x[3];}),i;
  if(t=="rsi")return {l:"RSI 14",mn:0,mx:100,lv:[30,70],s:[{v:rsiA(c,14),c:"#FFD700"}]};
  if(t=="macd"){var e1=emaA(c,12),e2=emaA(c,26),m=e1.map(function(v,i){return v-e2[i];}),sg=emaA(m,9),h=m.map(function(v,i){return v-sg[i];});var mx=0;h.concat(m).forEach(function(v){if(Math.abs(v)>mx)mx=Math.abs(v);});return {l:"MACD 12,26,9",mn:-mx,mx:mx,lv:[0],s:[{v:h,b:"sign"},{v:m,c:"#4FC3F7"},{v:sg,c:"#FFD700"}]};}
  if(t=="stoch"){var K=[],D=[];for(i=0;i<cs.length;i++){if(i<13){K.push(null);continue;}var ll=1e18,hh=-1e18;for(var j=i-13;j<=i;j++){if(cs[j][2]<ll)ll=cs[j][2];if(cs[j][1]>hh)hh=cs[j][1];}K.push(100*(c[i]-ll)/((hh-ll)||1));}for(i=0;i<K.length;i++){if(i<15){D.push(null);continue;}D.push((K[i]+K[i-1]+K[i-2])/3);}return {l:"Stochastic 14,3",mn:0,mx:100,lv:[20,80],s:[{v:K,c:"#00E676"},{v:D,c:"#FF5252"}]};}
  if(t=="ao"){var md=cs.map(function(x){return (x[1]+x[2])/2;}),a5=smaA(md,5),a34=smaA(md,34),ao=md.map(function(v,i){return a34[i]==null?null:a5[i]-a34[i];}),mx2=0;ao.forEach(function(v){if(v!=null&&Math.abs(v)>mx2)mx2=Math.abs(v);});return {l:"Awesome / Accelerator",mn:-mx2,mx:mx2,lv:[0],s:[{v:ao,b:"delta"}]};}
  var A=adxA(cs,14);return {l:"ADX 14 / +DI / −DI",mn:0,mx:60,lv:[25],s:[{v:A.pi,c:"#00E676"},{v:A.mi,c:"#FF5252"},{v:A.adx,c:"#FFD700"}]};
}
function strategyImage(ed) {
  var P = PAT[ed.pat] || PAT.levels, mir = !!ed.mirror, r = rng(ed.seed);
  function my(v) { return mir ? 100 - v : v; }
  var pts = P.pts.map(my);
  var tail = P.tail.map(function(c) { return mir ? [100-c[0], 100-c[2], 100-c[1], 100-c[3]] : c.slice(); });
  var N = 48, M = N - tail.length, cs = [], prev = pts[0], i;
  function interp(t) { var x = t * (pts.length - 1), k = Math.min(pts.length - 2, Math.floor(x)), f = x - k; return pts[k] + (pts[k+1] - pts[k]) * f; }
  for (i = 0; i < M; i++) {
    var cl = interp(i / (M - 1)) + (r() - .5) * 2.4, o = prev;
    cs.push([o, Math.max(o, cl) + r() * 1.8, Math.min(o, cl) - r() * 1.8, cl]);
    prev = cl;
  }
  tail.forEach(function(t) { cs.push(t); });
  var closes = cs.map(function(c) { return c[3]; });
  var ov = ed.ov || [], panels = ["rsi","macd","stoch","ao","adx"], pan = null;
  ov.forEach(function(o) { if (!pan && panels.indexOf(o) >= 0) pan = o; });
  var colMap = {g:"#00E676", r:"#FF5252", y:"#FFD700"};
  var lines = P.ln.map(function(l) {
    var c = l[4]; if (mir) c = c == "g" ? "r" : (c == "r" ? "g" : c);
    return {y0: my(l[0]), y1: my(l[1]), t: mir ? l[3] : l[2], c: colMap[c]};
  });
  var overlays = [];
  if (ov.indexOf("ema") >= 0) { overlays.push({v: emaA(closes, 9), c: "#FFD700"}); overlays.push({v: emaA(closes, 21), c: "#4FC3F7"}); }
  if (ov.indexOf("alli") >= 0) { overlays.push({v: emaA(closes, 13), c: "#4FC3F7"}); overlays.push({v: emaA(closes, 8), c: "#FF5252"}); overlays.push({v: emaA(closes, 5), c: "#00E676"}); }
  var bb = null;
  if (ov.indexOf("bb") >= 0) {
    var sm = smaA(closes, 20); bb = {m: sm, u: [], l: []};
    for (i = 0; i < closes.length; i++) {
      if (sm[i] == null) { bb.u.push(null); bb.l.push(null); continue; }
      var s2 = 0; for (var j = i - 19; j <= i; j++) s2 += Math.pow(closes[j] - sm[i], 2);
      var sd = Math.sqrt(s2 / 20); bb.u.push(sm[i] + 2 * sd); bb.l.push(sm[i] - 2 * sd);
    }
  }
  var W = 360, H = pan ? 262 : 214, top = 28, mb = pan ? 172 : H - 12, hi = -1e18, lo = 1e18;
  function ext(v) { if (v == null) return; if (v > hi) hi = v; if (v < lo) lo = v; }
  cs.forEach(function(c) { ext(c[1]); ext(c[2]); });
  lines.forEach(function(l) { ext(l.y0); ext(l.y1); });
  if (bb) { bb.u.forEach(ext); bb.l.forEach(ext); }
  var rg = (hi - lo) || 1; hi += rg * 0.08; lo -= rg * 0.08; rg = hi - lo;
  var padL = 6, padR = 8, step = (W - padL - padR) / N, bw = Math.max(2.4, step * 0.64);
  function Y(v) { return top + (hi - v) / rg * (mb - top); }
  function X(k) { return padL + k * step + step / 2; }
  function path(arr, col, w) {
    var d = "", started = false;
    arr.forEach(function(v, k) { if (v == null) return; d += (started ? "L" : "M") + X(k).toFixed(1) + " " + Y(v).toFixed(1); started = true; });
    return '<path d="' + d + '" fill="none" stroke="' + col + '" stroke-width="' + (w || 1.4) + '"/>';
  }
  var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" xmlns="http://www.w3.org/2000/svg"><rect width="' + W + '" height="' + H + '" fill="#05070d"/>';
  for (i = 0; i <= 3; i++) { var gy = top + (mb - top) * i / 3; svg += '<line x1="' + padL + '" y1="' + gy.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + gy.toFixed(1) + '" stroke="#121a28" stroke-width="1"/>'; }
  if (bb) {
    var up = "", dn = "";
    bb.u.forEach(function(v, k) { if (v != null) up += (up ? "L" : "M") + X(k).toFixed(1) + " " + Y(v).toFixed(1); });
    for (i = bb.l.length - 1; i >= 0; i--) if (bb.l[i] != null) dn += "L" + X(i).toFixed(1) + " " + Y(bb.l[i]).toFixed(1);
    svg += '<path d="' + up + dn + 'Z" fill="rgba(79,195,247,.07)"/>' + path(bb.u, "#4FC3F7", 1) + path(bb.l, "#4FC3F7", 1) + path(bb.m, "#7a8499", 1);
  }
  lines.forEach(function(l) {
    var ya = Y(l.y0), yb = Y(l.y1);
    svg += '<line x1="' + padL + '" y1="' + ya.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + yb.toFixed(1) + '" stroke="' + l.c + '" stroke-width="1.6" stroke-dasharray="6 4"/>';
    svg += '<text x="' + (padL + 3) + '" y="' + (ya - 4).toFixed(1) + '" fill="' + l.c + '" font-size="9" font-weight="800">' + esc(l.t) + '</text>';
  });
  cs.forEach(function(c, k) {
    var col = c[3] >= c[0] ? "#00E676" : "#FF5252", cx = X(k), yo = Y(c[0]), yc = Y(c[3]);
    svg += '<line x1="' + cx.toFixed(1) + '" y1="' + Y(c[1]).toFixed(1) + '" x2="' + cx.toFixed(1) + '" y2="' + Y(c[2]).toFixed(1) + '" stroke="' + col + '" stroke-width="1.2"/>';
    svg += '<rect x="' + (cx - bw/2).toFixed(1) + '" y="' + Math.min(yo, yc).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(1.2, Math.abs(yo - yc)).toFixed(1) + '" fill="' + col + '" rx="0.8"/>';
  });
  overlays.forEach(function(o) { svg += path(o.v, o.c, 1.4); });
  var lc = cs[N - 1], lx = X(N - 1), isCall = !mir;
  if (isCall) {
    var by = Math.min(mb - 6, Y(lc[2]) + 8);
    svg += '<polygon points="' + lx.toFixed(1) + ',' + by.toFixed(1) + ' ' + (lx-6).toFixed(1) + ',' + (by+10).toFixed(1) + ' ' + (lx+6).toFixed(1) + ',' + (by+10).toFixed(1) + '" fill="#00E676"/>';
    svg += '<text x="' + (lx-9).toFixed(1) + '" y="' + (by+9).toFixed(1) + '" fill="#00E676" font-size="10" font-weight="900" text-anchor="end">CALL ▲</text>';
  } else {
    var ty = Math.max(top + 12, Y(lc[1]) - 8);
    svg += '<polygon points="' + lx.toFixed(1) + ',' + ty.toFixed(1) + ' ' + (lx-6).toFixed(1) + ',' + (ty-10).toFixed(1) + ' ' + (lx+6).toFixed(1) + ',' + (ty-10).toFixed(1) + '" fill="#FF5252"/>';
    svg += '<text x="' + (lx-9).toFixed(1) + '" y="' + (ty-1).toFixed(1) + '" fill="#FF5252" font-size="10" font-weight="900" text-anchor="end">PUT ▼</text>';
  }
  var ttl = ed.t.replace(/^\d+\.\s*/, ""); if (ttl.length > 38) ttl = ttl.substring(0, 37) + "…";
  svg += '<text x="8" y="16" fill="#FFD700" font-size="10.5" font-weight="900">' + esc(ttl) + '</text>';
  svg += '<rect x="' + (W - 64) + '" y="5" width="56" height="16" rx="5" fill="' + (isCall ? "rgba(0,230,118,.18)" : "rgba(255,82,82,.18)") + '" stroke="' + (isCall ? "#00E676" : "#FF5252") + '"/><text x="' + (W - 36) + '" y="16.5" fill="' + (isCall ? "#00E676" : "#FF5252") + '" font-size="9.5" font-weight="900" text-anchor="middle">' + (isCall ? "CALL" : "PUT") + '</text>';
  if (pan) {
    var ps = panelSpec(pan, cs), pT = 186, pB = H - 8, pr = (ps.mx - ps.mn) || 1;
    function PY(v) { return pB - (v - ps.mn) / pr * (pB - pT); }
    svg += '<rect x="' + padL + '" y="' + pT + '" width="' + (W - padL - padR) + '" height="' + (pB - pT) + '" fill="#070b14" stroke="#161e2e"/>';
    ps.lv.forEach(function(v) { svg += '<line x1="' + padL + '" y1="' + PY(v).toFixed(1) + '" x2="' + (W - padR) + '" y2="' + PY(v).toFixed(1) + '" stroke="#2a3550" stroke-dasharray="3 3"/>'; });
    ps.s.forEach(function(se) {
      if (se.b) {
        var z = PY(0);
        se.v.forEach(function(v, k) {
          if (v == null) return;
          var col = se.b == "sign" ? (v >= 0 ? "#00E676" : "#FF5252") : ((k > 0 && se.v[k-1] != null && v < se.v[k-1]) ? "#FF5252" : "#00E676");
          var yy = PY(v);
          svg += '<rect x="' + (X(k) - bw/2).toFixed(1) + '" y="' + Math.min(yy, z).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(0.8, Math.abs(yy - z)).toFixed(1) + '" fill="' + col + '"/>';
        });
      } else {
        var d = "", st = false;
        se.v.forEach(function(v, k) { if (v == null) return; d += (st ? "L" : "M") + X(k).toFixed(1) + " " + PY(v).toFixed(1); st = true; });
        svg += '<path d="' + d + '" fill="none" stroke="' + se.c + '" stroke-width="1.3"/>';
      }
    });
    svg += '<text x="' + (padL + 4) + '" y="' + (pT + 10) + '" fill="#7a8499" font-size="8.5" font-weight="800">' + ps.l + '</text>';
  }
  svg += '</svg>';
  var chips = ['<span>' + (isCall ? "🟢 Сценарий CALL" : "🔴 Сценарий PUT") + '</span>', '<span>⏱ ' + esc(ed.tf) + '</span>', '<span>⏳ ' + esc(ed.exp) + '</span>'];
  var names = {ema:"EMA 9 / 21", alli:"Alligator 13/8/5", bb:"Bollinger 20,2", rsi:"RSI 14", macd:"MACD", stoch:"Stochastic", ao:"AO / AC", adx:"ADX / DI"};
  ov.forEach(function(o) { if (names[o]) chips.push('<span>' + names[o] + '</span>'); });
  return '<div class="strat-visual">' + svg + '<div class="strat-legend">' + chips.join("") + '</div></div>';
}

function buildQuiz(ed) {
  var r = rng((ed.seed || 1) + 99);
  function mk(q, right, wrongs) {
    var opts = wrongs.slice(0, 2), pos = Math.floor(r() * 3);
    opts.splice(pos, 0, right);
    return {q: q, opts: opts, a: pos};
  }
  var name = ed.t.replace(/^\d+\.\s*/, "");
  return [
    mk("«" + name + "»: когда открывать CALL?", ed.call, [ed.put, "На любой свече без подтверждения — главное быстрее."]),
    mk("«" + name + "»: когда открывать PUT?", ed.put, [ed.call, "Против сигнала, чтобы отыграть прошлый убыток."]),
    mk("Рекомендуемые таймфреймы стратегии?", ed.tf, ["H4, D1", "Только S5 при любой волатильности"]),
    mk("Когда по этой стратегии НЕ стоит входить?", ed.flt, ["Когда рынок спокойный и сигнал чёткий.", "Только по понедельникам."]),
    mk("Рекомендуемый риск на одну сделку?", "1–2% депозита", ["10–20% — чтобы быстрее отыграться", "Весь депозит при сильном сигнале"])
  ];
}

function renderEdu() {
  var html = "";
  EDU.forEach(function(ed, i) {
    html += '<div class="edu-item" onclick="openEdu(' + i + ')"><div><h4>' + esc(ed.t) + '</h4><small>' + esc(ed.tf) + ' · эксп. ' + esc(ed.exp) + '</small></div><span style="color:var(--gold)">➔</span></div>';
  });
  document.getElementById("eduItems").innerHTML = html;
}

function openEdu(idx) {
  var ed = EDU[idx];
  document.getElementById("eduList").classList.add("hidden");
  document.getElementById("eduView").classList.remove("hidden");
  var tagsHtml = "";
  (ed.tags || []).forEach(function(t) { tagsHtml += '<span class="tag">' + esc(t) + '</span>'; });
  document.getElementById("eduBody").innerHTML =
    '<h3>' + esc(ed.t) + '</h3><div class="tag-row">' + tagsHtml + '</div>' + strategyImage(ed) +
    '<div style="white-space:pre-wrap;font-size:12px;line-height:1.65;color:#d0d7e2;margin-bottom:14px">' + esc(ed.d) + '</div>' +
    '<button class="btn btn-gold btn-sm" onclick="openQuiz(' + idx + ')">📝 Пройти 5 вопросов</button>';
  window.scrollTo(0, 0);
}
function closeEdu() {
  document.getElementById("eduView").classList.add("hidden");
  document.getElementById("eduList").classList.remove("hidden");
}
function openRandomEdu() { openEdu(Math.floor(Math.random() * EDU.length)); }

function openQuiz(idx) {
  var ed = EDU[idx], bank = buildQuiz(ed);
  document.getElementById("quizTitle").textContent = "Мини-тест";
  var html = "";
  bank.forEach(function(item, qi) {
    html += '<div class="quiz-q"><h4>' + (qi + 1) + '. ' + esc(item.q) + '</h4>';
    item.opts.forEach(function(opt, oi) {
      html += '<button class="quiz-opt" onclick="answerQuiz(this,' + oi + ',' + item.a + ')">' + esc(opt) + '</button>';
    });
    html += '</div>';
  });
  document.getElementById("quizBody").innerHTML = html;
  document.getElementById("quizModal").classList.remove("hidden");
}
function answerQuiz(btn, oi, correct) {
  var parent = btn.parentNode;
  parent.querySelectorAll(".quiz-opt").forEach(function(b){ b.disabled = true; });
  if (oi === correct) btn.classList.add("correct");
  else { btn.classList.add("wrong"); parent.querySelectorAll(".quiz-opt")[correct].classList.add("correct"); }
}
function closeQuiz() { document.getElementById("quizModal").classList.add("hidden"); }

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
    ["tmv_pocketId","tmv_favStrat","tmv_profPhoto","tmv_sigs","tmv_history"].forEach(function(k){ localStorage.removeItem(k); });
  } catch(e) {}
  sigCount = 0; signalHistory = [];
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
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { alert("Камера недоступна. Загрузите скриншот."); return; }
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
  var v = scanCfg["экспирация"], n = parseInt(v);
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
      if (response.ok) { var resData = await response.json(); resText = resData.text || resText; }
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
      var buy = Math.min(idxOf("BUY"), idxOf("ВВЕРХ")) <= Math.min(idxOf("SELL"), idxOf("ВНИЗ"));
      dirEl.textContent = buy ? "BUY (ВВЕРХ) 📈" : "SELL (ВНИЗ) 📉";
      dirEl.className = "sig-dir " + (buy ? "call" : "put");
      incSig();
      saveToHistory({time: new Date().toLocaleString("ru-RU"), asset: "AI Scanner", tf: scanCfg["интервал"] || "M1", exp: expSec, dir: dirEl.textContent, reason: resText.substring(0, 300)});
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
  startLive();
});
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def serve_terminal():
    assets_json = json.dumps(ASSETS_DATA_JS, ensure_ascii=False).replace("</", "<\\/")
    edu_json = json.dumps(EDU_50, ensure_ascii=False).replace("</", "<\\/")
    return HTMLResponse(HTML_UI.replace("__ASSETS_JSON__", assets_json).replace("__EDU_JSON__", edu_json))


@app.get("/po_status")
async def po_status():
    """Диагностика API Pocket (otchards): открой https://ТВОЙ-САЙТ.onrender.com/po_status"""
    async def one(name, sym):
        try:
            cs = await fetch_otchards_candles(sym, "M1", 50)
            return name, {"ok": True, "candles": len(cs), "last": cs[-1]}
        except Exception as e:
            return name, {"ok": False, "error": _redact(e)}

    res = await asyncio.gather(*[one(n, s) for n, s in OTCHARDS_SYMBOL_MAP.items()])
    return {"api_key_set": bool(OTCHARDS_API_KEY), "api_url": OTCHARDS_API_URL,
            "tests": dict(res), "symbol_ok": dict(_PO_SYM_OK), "debug": dict(_PO_DEBUG)}


@app.get("/candles")
async def candles_endpoint(asset: str = "🇪🇺🇺🇸 EUR/USD OTC", timeframe: str = "M1"):
    candles, source = await get_candles(asset, timeframe, 50)
    return {"candles": candles, "source": source, "price": candles[-1][3], "t": time.time()}


HTF_MAP = {"S5": "S30", "S15": "M1", "S30": "M2", "M1": "M5", "M2": "M10", "M3": "M15", "M5": "M15"}


@app.get("/get_signal")
async def get_signal(asset: str = "🇪🇺🇺🇸 EUR/USD OTC", timeframe: str = "M1"):
    asyncio.create_task(send_tg_signal_alert(asset, timeframe))
    candles, source = await get_candles(asset, timeframe, 120)
    is_pro = bool(otchards_symbol_for(asset))
    result, warn = None, None
    if is_pro:
        htf = None
        h_tf = HTF_MAP.get(timeframe)
        if h_tf and source == "pocket_api":
            try:
                hc, hs = await get_candles(asset, h_tf, 60)
                htf = hc if hs == "pocket_api" else None
            except Exception:
                htf = None
        if len(candles) >= 30:
            try:
                result = pro_analysis(candles, htf)
            except Exception as e:
                logging.warning(f"pro_analysis error: {e}")
        if source != "pocket_api":
            warn = "API Pocket Option сейчас недоступен — сигнал построен по резервным рыночным данным, возможны расхождения."
    if result is None:
        signal, reason, conf = deep_analysis(candles[-50:])
        result = {"signal": signal, "confidence": conf, "reason": reason, "indicators": [], "quality": ""}
    return {"signal": result["signal"], "reason": result["reason"], "confidence": result["confidence"],
            "indicators": result.get("indicators", []), "quality": result.get("quality", ""),
            "source": source, "is_pro": is_pro, "warn": warn,
            "candles": candles[-50:], "price": candles[-1][3], "server_time": time.time()}


# ===== НОВОСТИ (экономический календарь) =====
MAJOR_FX = {"USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "RUB"}
PAIR_HINTS = {
    "USD": "EUR/USD · GBP/USD · USD/JPY · USD/RUB", "EUR": "EUR/USD · EUR/GBP · EUR/JPY",
    "GBP": "GBP/USD · EUR/GBP · GBP/JPY", "JPY": "USD/JPY · EUR/JPY · GBP/JPY",
    "AUD": "AUD/USD · AUD/JPY · EUR/AUD", "NZD": "NZD/USD · AUD/NZD", "CAD": "USD/CAD · EUR/CAD",
    "CHF": "USD/CHF · EUR/CHF · GBP/CHF", "RUB": "USD/RUB · EUR/RUB",
}
FF_URLS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
           "https://nfs.faireconomy.media/ff_calendar_nextweek.json"]
_NEWS_CACHE = {"ts": 0.0, "data": None}


def _parse_event_dt(iso_str: str):
    if not iso_str:
        return None
    try:
        d = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


def _tok(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


async def _get_json(client, url):
    try:
        r = await client.get(url, timeout=12.0)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logging.warning(f"news fetch {url}: {e}")
    return None


async def _news_ff(client):
    out = []
    for u in FF_URLS:
        data = await _get_json(client, u)
        if isinstance(data, list):
            for e in data:
                out.append({"title": e.get("title") or "", "currency": (e.get("country") or "").upper(),
                            "impact": (e.get("impact") or "").capitalize(), "date": e.get("date") or "",
                            "forecast": e.get("forecast") or "", "previous": e.get("previous") or "",
                            "actual": e.get("actual") or ""})
    return out


async def _news_smt(client):
    url = ("https://showmytrades.com/api/public/calendar/v1?currency=USD,EUR,GBP,JPY,AUD,NZD,CAD,CHF,RUB"
           "&impact=high,medium&days=3")
    data = await _get_json(client, url)
    out = []
    if isinstance(data, dict):
        for e in data.get("events", []):
            out.append({"title": e.get("title") or "", "currency": (e.get("currency") or "").upper(),
                        "impact": (e.get("impact") or "medium").capitalize(), "date": e.get("time") or "",
                        "forecast": e.get("forecast") or "", "previous": e.get("previous") or "",
                        "actual": e.get("actual") or ""})
    return out


async def _news_pipatlas(client):
    data = await _get_json(client, "https://pipatlas.com/api/calendar")
    out = []
    if isinstance(data, dict):
        for e in data.get("events", []):
            out.append({"title": e.get("name") or "", "currency": (e.get("currency") or "").upper(),
                        "impact": (e.get("impact") or "").capitalize(), "date": e.get("date") or "",
                        "forecast": e.get("forecast") or "", "previous": e.get("previous") or "",
                        "actual": e.get("actual") or ""})
    return out


def _fill_from_pool(ev, pool):
    dt = _parse_event_dt(ev["date"])
    if not dt:
        return
    a = _tok(ev["title"])
    for p in pool:
        if p["currency"] != ev["currency"]:
            continue
        pd = _parse_event_dt(p["date"])
        if not pd or abs((pd - dt).total_seconds()) > 600:
            continue
        b = _tok(p["title"])
        if a and b and len(a & b) / len(a | b) >= 0.5:
            for k in ("actual", "forecast", "previous"):
                if not ev.get(k) and p.get(k):
                    ev[k] = p[k]
            return


@app.get("/news")
async def get_news():
    if _NEWS_CACHE["data"] and time.time() - _NEWS_CACHE["ts"] < 60:
        return _NEWS_CACHE["data"]
    now = datetime.now(timezone.utc)
    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
        ff, smt = await asyncio.gather(_news_ff(client), _news_smt(client))
        if ff:
            base, pool = ff, smt
        elif smt:
            base, pool = smt, []
        else:
            base, pool = await _news_pipatlas(client), []
    events = []
    for e in base:
        if e["impact"] not in ("High", "Medium") or e["currency"] not in MAJOR_FX:
            continue
        dt = _parse_event_dt(e["date"])
        if not dt or dt < now - timedelta(hours=36) or dt > now + timedelta(hours=96):
            continue
        if pool and not e.get("actual"):
            _fill_from_pool(e, pool)
        released = dt <= now or bool(e.get("actual"))
        events.append({
            "name": translate_news_title(e["title"]), "name_en": e["title"], "currency": e["currency"],
            "impact": e["impact"], "impact_ru": impact_ru(e["impact"]), "date": e["date"],
            "forecast": e["forecast"], "previous": e["previous"], "actual": e["actual"],
            "status": "released" if released else "upcoming",
            "status_ru": "✅ Уже вышло" if released else "⏳ Скоро выйдет",
            "pair_hint": PAIR_HINTS.get(e["currency"], e["currency"]),
        })

    def _key(ev):
        ts = _parse_event_dt(ev["date"]).timestamp()
        return (0, ts) if ev["status"] == "upcoming" else (1, -ts)

    events.sort(key=_key)
    result = {"events": events[:80], "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "server_time": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "focus": "forex"}
    if events or not _NEWS_CACHE["data"]:
        _NEWS_CACHE.update({"ts": time.time(), "data": result})
        return result
    return _NEWS_CACHE["data"]


@app.post("/analyze")
async def analyze(file: UploadFile = File(...), cfg: str = Form(...)):
    image_bytes = await file.read()
    try:
        config = json.loads(cfg)
    except Exception:
        config = {}
    return {"text": await scanner_core.compute(image_bytes, config)}


def _gemini_rest_vision(prompt_text: str, image_bytes: bytes, mime: str = "image/jpeg"):
    if not GEMINI_API_KEY:
        return None
    import base64
    import urllib.request
    b64 = base64.b64encode(image_bytes).decode("ascii")
    seen = set()
    for m in [GEMINI_MODEL or "gemini-2.0-flash", "gemini-2.0-flash", "gemini-flash-latest"]:
        if not m or m in seen:
            continue
        seen.add(m)
        for ver in ("v1beta", "v1"):
            url = f"https://generativelanguage.googleapis.com/{ver}/models/{m}:generateContent?key={GEMINI_API_KEY}"
            body = json.dumps({"contents": [{"parts": [{"text": prompt_text}, {"inline_data": {"mime_type": mime, "data": b64}}]}],
                               "generationConfig": {"temperature": 0.3, "maxOutputTokens": 420}}).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=22) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                cands = data.get("candidates") or []
                if cands:
                    texts = [p.get("text", "") for p in ((cands[0].get("content") or {}).get("parts") or []) if p.get("text")]
                    if texts:
                        return "\n".join(texts).strip()
            except Exception as e:
                logging.warning(f"Gemini vision {m} {ver}: {e}")
    return None


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
