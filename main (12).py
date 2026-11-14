import os
import re
import io
import json
import time
import math
import random
import zlib
import asyncio
import logging
import functools
from contextlib import asynccontextmanager
from collections import deque
from datetime import datetime, timezone, timedelta

import httpx
import numpy as np
import uvicorn
from PIL import Image
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse

logging.basicConfig(level=logging.INFO)
@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(signal_bot_loop())   # бот сканирует рынок Binance
    try:
        yield
    finally:
        task.cancel()
        for _c in list(_BOT.values()):
            _t = _c.get("task")
            if _t and not _t.done():
                _t.cancel()


app = FastAPI(title="TEAM MASTER VIP - Ultimate Trading Terminal", version="37.0", lifespan=lifespan)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")

# ===== OTCHARTS.COM API (Pocket Option OTC). Ключ берётся из Environment на Render =====
def _find_otc_key():
    """Ищем ключ otc_live_... в Environment: по известным именам, по префиксу OTCHA*, по значению."""
    for n in ("OTCHARTS_API_KEY", "OTCHARDS_API_KEY", "OTCHARTS_KEY", "OTCHARDS_KEY", "OTCHARTS_TOKEN",
              "OTCHARDS_TOKEN", "OTCHARTS", "OTCHARDS", "OTCHARTS_API", "OTCHARDS_API"):
        v = (os.environ.get(n) or "").strip()
        if v and not v.lower().startswith("http"):
            return v
    for k, v in os.environ.items():
        v = (v or "").strip()
        if v.startswith("otc_live_") or v.startswith("otc_"):
            return v
    for k, v in os.environ.items():
        v = (v or "").strip()
        if k.upper().startswith("OTCHA") and "URL" not in k.upper() and v and not v.lower().startswith("http"):
            return v
    return ""


OTCHARDS_API_KEY = _find_otc_key()
OTCHARDS_API_URL = "https://otcharts.com/v1/candles"
_u = (os.environ.get("OTCHARTS_API_URL") or os.environ.get("OTCHARDS_API_URL") or "").strip()
if _u.startswith("http") and "otcharts" in _u:
    OTCHARDS_API_URL = _u
OTC_VENUE = os.environ.get("OTCHARTS_VENUE", "otc")   # otc = Pocket Option

OTCHARDS_SYMBOL_MAP = {
    "AUD/USD OTC": "AUDUSD_otc",
    "EUR/USD OTC": "EURUSD_otc",
    "GBP/USD OTC": "GBPUSD_otc",
    "USD/JPY OTC": "USDJPY_otc",
    "BITCOIN OTC": "BTCUSD_otc",
}

TF_SEC = {"S5": 5, "S15": 15, "S30": 30, "M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300, "M10": 600, "M15": 900}


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


_RAW_CACHE = {}      # (symbol, base_tf) -> (ts, limit, parsed)
_RAW_LOCKS = {}


async def _otc_request(sym: str, base: int, limit: int):
    """Один запрос к otcharts.com. Бросает RuntimeError (наружу пользователю не показывается)."""
    headers = {"Authorization": f"Bearer {OTCHARDS_API_KEY}", "Accept": "application/json"}
    params = {"venue": OTC_VENUE, "symbol": sym, "tf": base, "limit": limit}
    try:
        res = await _http().get(OTCHARDS_API_URL, params=params, headers=headers, timeout=8.0)
    except Exception as e:
        _PO_DEBUG["last_error"] = "network: " + _redact(e)
        raise RuntimeError(_PO_DEBUG["last_error"])
    _PO_DEBUG.update({"last_status": res.status_code, "last_symbol": sym, "last_raw": _redact(res.text[:400])})
    if res.status_code in (401, 402, 403, 429, 503):
        raise RuntimeError(f"HTTP {res.status_code} (ключ/тариф/лимит) для {sym}")
    if res.status_code != 200:
        raise LookupError(f"HTTP {res.status_code} для {sym}")
    try:
        data = res.json()
    except Exception:
        raise LookupError(f"ответ не JSON для {sym}")
    rows = _extract_rows(data)
    if not rows:
        raise LookupError(f"пустой ответ для {sym}")
    parsed = [p for p in (_parse_candle_row(r) for r in rows) if p]
    if not parsed:
        raise LookupError(f"не удалось разобрать свечи для {sym}")
    if all(p[0] is not None for p in parsed):
        ded = {}
        for t, cd in parsed:
            ded[t] = cd
        parsed = [(t, ded[t]) for t in sorted(ded)]
        # Pocket Option штампует время на 2 часа вперёд от UTC — выравниваем
        if parsed[-1][0] - time.time() > 3000:
            parsed = [(t - 7200, cd) for t, cd in parsed]
    else:
        parsed = [(float(i), cd) for i, (_, cd) in enumerate(parsed)]
    if len(parsed) < 10:
        raise LookupError(f"мало свечей для {sym} ({len(parsed)})")
    return parsed


async def _otc_raw(symbol: str, base: int, limit: int):
    key = (symbol, base)
    ttl = 10.0 if base >= 60 else 2.0
    c = _RAW_CACHE.get(key)
    if c and time.time() - c[0] < ttl and c[1] >= limit:
        return c[2]
    lock = _RAW_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        c = _RAW_CACHE.get(key)
        if c and time.time() - c[0] < ttl and c[1] >= limit:
            return c[2]
        last_err = "нет ответа от API"
        for sym in _symbol_variants(symbol):
            try:
                parsed = await _otc_request(sym, base, limit)
            except LookupError as e:
                last_err = str(e)
                continue
            _PO_SYM_OK[symbol] = sym
            _PO_DEBUG.update({"last_ok_ts": time.time(), "last_error": None})
            _RAW_CACHE[key] = (time.time(), limit, parsed)
            return parsed
        _PO_DEBUG["last_error"] = last_err
        raise RuntimeError(last_err)


async def fetch_otchards_candles(symbol: str, tf: str, count: int = 120):
    """Реальные свечи Pocket Option (OTC) через otcharts.com. Ключ — из Environment Render."""
    if not OTCHARDS_API_KEY:
        raise RuntimeError("ключ otcharts не найден в Environment")
    period = TF_SEC.get(tf, 60)
    base = 60 if period >= 60 else period
    mult = max(1, period // base)
    limit = min(1500, max(60, count * mult + 5))
    parsed = await _otc_raw(symbol, base, limit)
    if period > base:
        parsed = _resample(parsed, period)
    out = [p[1] for p in parsed]
    if len(out) < 10:
        raise RuntimeError("мало свечей после сжатия ТФ")
    return out[-count:]


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
    "Ripple": "XRPUSDT", "Shiba Inu": "SHIBUSDT", "Pepe": "PEPEUSDT", "NEAR": "NEARUSDT", "Cosmos": "ATOMUSDT",
    "Uniswap": "UNIUSDT", "Aptos": "APTUSDT", "Arbitrum": "ARBUSDT", "Optimism": "OPUSDT", "Sui": "SUIUSDT",
    "Injective": "INJUSDT", "Filecoin": "FILUSDT", "Ethereum Classic": "ETCUSDT", "Bitcoin Cash": "BCHUSDT",
    "Stellar": "XLMUSDT", "Hedera": "HBARUSDT",
}
BINANCE_TF = {"S5": ("1s", 5), "S15": ("1s", 15), "S30": ("1s", 30), "M1": ("1m", 1), "M2": ("1m", 2),
              "M3": ("3m", 1), "M4": ("1m", 4), "M5": ("5m", 1), "M10": ("5m", 2), "M15": ("15m", 1)}


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
    """Возвращает (свечи, источник). Никогда не бросает исключений:
    5 главных активов — реальные свечи Pocket Option с otcharts; если API не отвечает — тихо
    подхватывается запасной источник и всё продолжает работать."""
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
            logging.warning(f"otcharts error for {asset}: {_redact(e)}")
            _PO_DEBUG["last_error"] = _redact(e)
            lg = _LAST_GOOD.get(key)
            if lg and now - lg[0] < 180:
                candles, source = lg[1], "pocket_api"
    if candles is None and (sym or "OTC" not in asset.upper()):
        try:
            name = _binance_name(asset)
            if name:
                candles = await fetch_binance_candles(name, tf, N)
                if candles:
                    source = "market_fallback" if sym else "binance"
        except Exception as e:
            logging.warning(f"fallback market error for {asset}: {e}")
            candles = None
    if not candles:
        candles, source = sim_candles(asset, tf, N), "otc"
    if len(_CANDLE_CACHE) > 400:
        _CANDLE_CACHE.clear()
    _CANDLE_CACHE[key] = (now, candles, source)
    return candles[-count:], source


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
    quality = "СИЛЬНЫЙ" if conf >= 80 else ("СРЕДНИЙ" if conf >= 68 else "СЛАБЫЙ — лучше пропустить")
    return signal, "\n".join(notes[:5]), conf


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
    lines = []
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
            "reason": "\n".join(lines), "indicators": inds,
            "agree": agree, "tv": tv, "regime": "", "points": aligned,
            "against": against[0] if against else "", "warns": warns}


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
    NOT_CHART = ("❌ ЭТО НЕ ГРАФИК. На изображении не найден торговый график со свечами или линией цены.\n"
                 "📷 Наведи камеру на график (экран терминала) так, чтобы были видны свечи, и сканируй снова. Сигнал не выдан.")

    def _gemini_sync(self, image_bytes: bytes, config: dict):
        prompt = (
            "Ты профессиональный ИИ-сканер графиков бинарных опционов Pocket Option. "
            "СНАЧАЛА проверь: есть ли на изображении торговый график (свечи или линия цены с осями). "
            "Если это НЕ график (фото предметов, людей, тела, комнаты, текст, интерфейс без графика) — ответь ТОЛЬКО одним словом: НЕ_ГРАФИК. "
            "Если график есть — внимательно проанализируй свечи, тени, цвета, тенденцию, уровни и паттерны именно на графике. "
            "Выдай торговый сигнал: направление (BUY / ВВЕРХ или SELL / ВНИЗ), таймфрейм, стратегию ("
            + str(config.get("стратегия", "Smart Money"))
            + ") и подробный технический анализ. Используй эмодзи 📈📉⚡🔥💎🎯🚀."
        )
        return _gemini_rest_vision(prompt, image_bytes, mime="image/jpeg")

    def _gemini_is_chart(self, image_bytes: bytes):
        t = _gemini_rest_vision("Это скриншот или фото экрана с торговым графиком (свечи или линия цены, оси цены/времени)? "
                                "Ответь одним словом: ДА или НЕТ.", image_bytes, mime="image/jpeg")
        if not t:
            return None
        t = t.strip().upper()
        if t.startswith("ДА") or t.startswith("YES"):
            return True
        if t.startswith("НЕТ") or t.startswith("NO"):
            return False
        return None

    @staticmethod
    def _masks(image):
        im = image.convert("RGB")
        im.thumbnail((480, 480))
        a = np.asarray(im).astype(np.int16)
        r, g, b = a[..., 0], a[..., 1], a[..., 2]
        green = (g > r + 50) & (g > b + 30) & (g > 110)
        red = (r > g + 60) & (r > b + 40) & (r > 150)
        return a, green, red

    def _chart_heuristic(self, image):
        a, green, red = self._masks(image)
        h, w = green.shape
        gray = (a[..., 0] * 299 + a[..., 1] * 587 + a[..., 2] * 114) // 1000
        grad = np.abs(np.diff(gray, axis=1))[:-1, :] + np.abs(np.diff(gray, axis=0))[:, :-1]
        flat = float(np.mean(grad < 8))                       # у графика большие ровные области
        col = green | red
        cols = int(np.sum(col.sum(axis=0) >= 5))              # столбцы со «свечами»
        frac = float(col.mean())
        return flat >= 0.5 and cols >= 10 and frac >= 0.002

    def _fallback_direction(self, image):
        a, green, red = self._masks(image)
        h, w = green.shape
        col = green | red
        ys, xs = np.where(col)
        late = ys[xs > w * 0.8]
        mid = ys[(xs > w * 0.55) & (xs <= w * 0.8)]
        if len(late) > 5 and len(mid) > 5:
            return float(np.mean(late)) <= float(np.mean(mid))      # выше на экране = цена растёт
        return int(green.sum()) >= int(red.sum())

    async def compute(self, image_bytes: bytes, config: dict) -> str:
        try:
            image = Image.open(io.BytesIO(image_bytes))
            jpeg = None
            verdict = None
            if GEMINI_API_KEY:
                buf = io.BytesIO()
                image.convert("RGB").save(buf, format="JPEG", quality=85)
                jpeg = buf.getvalue()
                try:
                    verdict = await asyncio.to_thread(self._gemini_is_chart, jpeg)
                except Exception as ex:
                    logging.warning(f"Gemini chart check: {ex}")
            if verdict is None:
                verdict = self._chart_heuristic(image)
            if not verdict:
                return self.NOT_CHART
            if GEMINI_API_KEY and jpeg:
                try:
                    text = await asyncio.to_thread(self._gemini_sync, jpeg, config)
                    if text:
                        if "НЕ_ГРАФИК" in text.upper().replace(" ", "_")[:40]:
                            return self.NOT_CHART
                        return "--- TEAM MASTER AI CANDLE SCANNER ---\n" + text
                except Exception as ex:
                    logging.warning(f"Gemini error: {ex}")
            bullish = self._fallback_direction(image)
            direction = "BUY (ВВЕРХ) 📈" if bullish else "SELL (ВНИЗ) 📉"
            desc = ("Последние свечи движутся выше предыдущих — покупатели удерживают импульс. 🟢💪" if bullish
                    else "Последние свечи сместились ниже предыдущих — давление продавцов. 🔴⚔️")
            return ("--- TEAM MASTER SIGNAL V4.0 ---\n"
                    "📊 СТРАТЕГИЯ: " + str(config.get("стратегия", "Smart Money")) + "\n"
                    "⏱ ТАЙМФРЕЙМ: " + str(config.get("интервал", "M1")) + "\n"
                    "⏳ ЭКСПИРАЦИЯ: " + str(config.get("экспирация", "1м")) + "\n"
                    "-------------------------------\n"
                    "🔥 ВЕРДИКТ: " + direction + "\n"
                    "📌 ПРИЧИНА: " + desc + "\nНаправление определено по положению последних свечей на графике. ⚡\n🚀 Сигнал готов к входу!")
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
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
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
.sig{position:relative;display:none;margin-top:11px;background:var(--inner);border:2px solid var(--gold);border-radius:14px;padding:14px;text-align:center;animation:fu .3s ease}
.sig-meta{font-size:10px;color:var(--muted);font-weight:700;padding:0 24px}
.sig-x{position:absolute;top:7px;right:7px;width:28px;height:28px;border-radius:50%;border:1px solid rgba(255,255,255,.2);background:rgba(0,0,0,.4);color:#fff;font-size:14px;font-weight:900;line-height:1;cursor:pointer;display:flex;align-items:center;justify-content:center;z-index:3;padding:0}
.sig-x:active{transform:scale(.9);background:rgba(255,82,82,.35)}
.news-updated{display:flex;align-items:center;justify-content:space-between;gap:8px;text-align:left}
.news-refresh{flex-shrink:0;background:var(--gold-g);color:#000;border:none;border-radius:8px;padding:6px 10px;font-size:10px;font-weight:900;cursor:pointer;font-family:inherit}
.news-refresh:disabled{opacity:.6}
.news-refresh.spin b{display:inline-block;animation:nrSpin .8s linear infinite}
@keyframes nrSpin{to{transform:rotate(360deg)}}
.sig-dir{font-size:19px;font-weight:900;padding:11px;border-radius:11px;margin:8px 0}
.call{background:rgba(0,230,118,.1);color:var(--green);border:1px solid rgba(0,230,118,.3)}
.put{background:rgba(255,82,82,.1);color:var(--red);border:1px solid rgba(255,82,82,.3)}
.timer{font-size:12.5px;font-weight:900;color:var(--gold);background:rgba(255,215,0,.06);border:1px dashed var(--gold);border-radius:10px;padding:8px;margin-bottom:8px}
.strat{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:10px 12px;font-size:11px;color:#c5cdd8;text-align:left;line-height:1.6;margin-bottom:8px;white-space:pre-line}
.sig-stats{display:flex;justify-content:space-between;font-size:10px;font-weight:800;margin-bottom:8px}
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

.prof-top{display:flex;align-items:center;gap:12px;margin-bottom:12px}
.prof-top .profile-photo-upload{margin:0;flex-shrink:0;width:96px;height:96px;border-style:solid;box-shadow:0 0 18px rgba(255,215,0,.25)}
.wr-card{flex:1;min-width:0;background:linear-gradient(160deg,#121826,#0a0e17);border:1px solid var(--border-g);border-radius:16px;padding:11px 12px}
.wr-title{font-size:9px;font-weight:900;letter-spacing:.8px;color:var(--muted)}
.wr-big{font-size:30px;font-weight:900;line-height:1.1;margin:2px 0 7px}
.wr-scale{display:flex;height:9px;border-radius:9px;overflow:hidden;background:rgba(255,255,255,.07)}
.wr-scale i{display:block;height:100%;transition:width .8s cubic-bezier(.2,.8,.2,1)}
.wr-scale .g{background:linear-gradient(90deg,#00A152,#00E676)}
.wr-scale .r{background:linear-gradient(90deg,#FF5252,#D50000)}
.wr-legend{display:flex;justify-content:space-between;gap:6px;margin-top:7px;font-size:9.5px;font-weight:800}
.wr-legend .g{color:var(--green)}.wr-legend .r{color:var(--red)}
.wr-total{font-size:9px;color:var(--muted);font-weight:700;margin-top:5px}
.sess-overlay{position:fixed;inset:0;z-index:3000;background:rgba(2,4,8,.97);display:flex;justify-content:center;overflow-y:auto;animation:fadeIn .25s ease}
.sess{width:100%;max-width:440px;padding:16px 14px 28px;display:flex;flex-direction:column;gap:12px}
.sess-top{display:flex;justify-content:space-between;align-items:center}
.sess-top b{font-size:14px;letter-spacing:.5px;display:block}
.sess-top small{font-size:11px;color:var(--gold);font-weight:800}
.sess-ring{position:relative;width:170px;height:170px;margin:4px auto 0}
.sess-ring svg{width:100%;height:100%;transform:rotate(-90deg)}
.sess-ring circle{fill:none;stroke-width:11}
.sess-ring .bg{stroke:rgba(255,255,255,.07)}
.sess-ring .fg{stroke:var(--gold);stroke-linecap:round;stroke-dasharray:326.7;stroke-dashoffset:326.7;transition:stroke-dashoffset .6s cubic-bezier(.2,.8,.2,1),stroke .4s;filter:drop-shadow(0 0 6px rgba(255,215,0,.5))}
.sess-ring .mid{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center}
.sess-ring .mid b{font-size:36px;font-weight:900;line-height:1}
.sess-ring .mid span{font-size:8.5px;font-weight:800;color:var(--muted);letter-spacing:.8px;margin-top:4px}
.sess-chips{display:flex;justify-content:center;gap:8px;flex-wrap:wrap}
.sess-chips span{font-size:10.5px;font-weight:800;color:#c5cdd8;background:var(--card);border:1px solid var(--border);padding:5px 10px;border-radius:10px}
.sess-chips b{color:var(--gold)}
.sess-dots{display:flex;justify-content:center;gap:5px;flex-wrap:wrap;min-height:14px}
.sess-dots i{width:11px;height:11px;border-radius:50%;display:block;animation:fu .3s ease}
.sess-dots i.w{background:var(--green);box-shadow:0 0 7px rgba(0,230,118,.6)}
.sess-dots i.l{background:var(--red);box-shadow:0 0 7px rgba(255,82,82,.6)}
.sess-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.sess-box{border-radius:18px;padding:14px 10px;text-align:center;border:1.5px solid}
.sess-box.win{background:linear-gradient(160deg,rgba(0,230,118,.14),rgba(0,230,118,.03));border-color:rgba(0,230,118,.45)}
.sess-box.loss{background:linear-gradient(160deg,rgba(255,82,82,.14),rgba(255,82,82,.03));border-color:rgba(255,82,82,.45)}
.sb-title{font-size:10.5px;font-weight:900;letter-spacing:.6px}
.sess-box.win .sb-title{color:var(--green)}.sess-box.loss .sb-title{color:var(--red)}
.sb-num{font-size:54px;font-weight:900;line-height:1.1;margin:6px 0 10px}
.sess-box.win .sb-num{color:var(--green);text-shadow:0 0 18px rgba(0,230,118,.45)}
.sess-box.loss .sb-num{color:var(--red);text-shadow:0 0 18px rgba(255,82,82,.45)}
.sb-num.pop{animation:sbPop .3s ease}
@keyframes sbPop{0%{transform:scale(1)}50%{transform:scale(1.22)}100%{transform:scale(1)}}
.sb-btns{display:flex;gap:8px}
.sb-btns button{flex:1;height:46px;border-radius:13px;border:none;font-size:26px;font-weight:900;cursor:pointer;line-height:1}
.sb-btns button:active{transform:scale(.93)}
.sb-m{background:rgba(255,255,255,.07);color:#c5cdd8}
.sess-box.win .sb-p{background:linear-gradient(135deg,#00C853,#00E676);color:#053}
.sess-box.loss .sb-p{background:linear-gradient(135deg,#FF1744,#FF5252);color:#fff}
.rep-card{background:linear-gradient(160deg,#121826,#0a0e17);border:1px solid var(--border-g);border-radius:16px;padding:14px}
.rep-title{font-size:14px;font-weight:900;margin-bottom:2px}
.rep-date{font-size:9.5px;color:var(--muted);font-weight:700;margin-bottom:10px}
.rep-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-bottom:10px}
.rep-stats div{background:var(--inner);border:1px solid var(--border);border-radius:11px;padding:8px 4px;text-align:center}
.rep-stats b{display:block;font-size:19px;font-weight:900}
.rep-stats span{font-size:8.5px;color:var(--muted);font-weight:800;letter-spacing:.4px}
.rep-bar{display:flex;height:9px;border-radius:9px;overflow:hidden;background:rgba(255,255,255,.07);margin-bottom:11px}
.rep-bar i{display:block;height:100%}
.rep-bar .g{background:linear-gradient(90deg,#00A152,#00E676)}.rep-bar .r{background:linear-gradient(90deg,#FF5252,#D50000)}
.rep-p{font-size:11px;line-height:1.6;color:#d5dbe6;margin-bottom:7px}
.rep-tip{font-size:10.8px;line-height:1.55;font-weight:700;color:#f0e2a6;background:rgba(255,215,0,.07);border:1px solid rgba(255,215,0,.25);border-radius:10px;padding:8px 10px;margin-top:8px}
.msg.report .msg-bub{border-left-color:var(--gold);padding:8px}
.msg.expired .msg-bub{opacity:.6}
.msg.expired .msg-bub:hover{opacity:1}
.d-note.asset{background:rgba(120,170,255,.08);color:#c4d6ff;border:1px solid rgba(120,170,255,.25)}
.d-note.hist{background:rgba(0,230,118,.07);color:#b5f0cf;border:1px solid rgba(0,230,118,.25)}
.d-note.exp{background:rgba(255,255,255,.04);color:#b8c0d0;border:1px solid var(--border)}
.d-facts{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px}
.d-facts span{font-size:9.3px;font-weight:800;color:#c5cdd8;background:var(--inner);border:1px solid var(--border);padding:3px 7px;border-radius:7px}
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
.cat-meta{font-size:11px;color:var(--gold);font-weight:900;display:flex;align-items:center;gap:6px}
.cat-meta i{font-style:normal;color:var(--gold);font-size:9px}
.live-badge{font-size:8px;font-weight:900;color:var(--green);background:rgba(0,230,118,.12);border:1px solid rgba(0,230,118,.35);padding:2px 6px;border-radius:6px;margin-left:6px;letter-spacing:.3px}
.ind-row{display:flex;flex-wrap:wrap;gap:5px;justify-content:center;margin-bottom:8px}
.ind-row:empty{display:none}
.ind{font-size:9.5px;font-weight:800;padding:4px 8px;border-radius:7px;border:1px solid var(--border);background:var(--card);color:#c5cdd8}
.ind.up{color:var(--green);border-color:rgba(0,230,118,.35)}
.ind.down{color:var(--red);border-color:rgba(255,82,82,.35)}
.sig-meta{line-height:1.6}

/* ===== v33: анимация анализа ===== */
@keyframes fadeOut{to{opacity:0}}
@keyframes barUp{0%,100%{height:5px}50%{height:32px}}
@keyframes blip{0%{transform:scale(0);opacity:0}30%{opacity:1}100%{transform:scale(2.6);opacity:0}}
@keyframes corePulse{0%,100%{box-shadow:0 0 0 0 rgba(255,215,0,.45)}50%{box-shadow:0 0 0 16px rgba(255,215,0,0)}}
@keyframes sigPop{0%{opacity:0;transform:scale(.86) translateY(16px)}60%{opacity:1;transform:scale(1.03)}100%{opacity:1;transform:none}}
@keyframes glowGreen{0%{box-shadow:0 0 0 0 rgba(0,230,118,.55)}100%{box-shadow:0 0 0 28px rgba(0,230,118,0)}}
@keyframes glowRed{0%{box-shadow:0 0 0 0 rgba(255,82,82,.55)}100%{box-shadow:0 0 0 28px rgba(255,82,82,0)}}
@keyframes chipIn{from{opacity:0;transform:translateY(8px) scale(.85)}to{opacity:1;transform:none}}
@keyframes shine{from{transform:translateX(-120%)}to{transform:translateX(320%)}}
@keyframes msgIn{0%{opacity:0;transform:translateY(18px) scale(.96)}100%{opacity:1;transform:none}}
@keyframes msgFlash{0%{box-shadow:0 0 0 0 rgba(255,215,0,.5)}100%{box-shadow:0 0 0 18px rgba(255,215,0,0)}}
@keyframes dots{0%,80%,100%{transform:scale(.5);opacity:.4}40%{transform:scale(1);opacity:1}}
@keyframes ringOut{0%{transform:scale(.9);opacity:.9}100%{transform:scale(1.4);opacity:0}}
@keyframes liRise{from{opacity:0;transform:translateX(-8px)}to{opacity:1;transform:none}}

.analyze-overlay{position:fixed;inset:0;z-index:200;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:20px;background:radial-gradient(circle at 50% 36%,rgba(46,36,6,.96),rgba(4,6,10,.97) 64%);animation:fadeIn .25s ease;overflow:hidden}
.analyze-overlay.out{animation:fadeOut .3s ease forwards}
.radar{position:relative;width:190px;height:190px;margin-bottom:18px}
.radar::before,.radar::after{content:"";position:absolute;background:rgba(255,215,0,.14)}
.radar::before{left:50%;top:0;bottom:0;width:1px}
.radar::after{top:50%;left:0;right:0;height:1px}
.radar-ring{position:absolute;inset:0;border-radius:50%;border:1px solid rgba(255,215,0,.24)}
.radar-ring.r0{box-shadow:0 0 40px rgba(255,215,0,.16),inset 0 0 40px rgba(255,215,0,.07)}
.radar-ring.r2{inset:28px}.radar-ring.r3{inset:56px}
.radar-sweep{position:absolute;inset:0;border-radius:50%;background:conic-gradient(from 0deg,rgba(255,215,0,0) 0deg,rgba(255,215,0,0) 260deg,rgba(255,215,0,.6) 360deg);animation:spin 1.3s linear infinite}
.blip{position:absolute;width:9px;height:9px;border-radius:50%;background:var(--green);box-shadow:0 0 10px var(--green);opacity:0;animation:blip 1.8s ease-out infinite}
.blip.b1{top:26%;left:62%}.blip.b2{top:64%;left:28%;background:var(--red);box-shadow:0 0 10px var(--red);animation-delay:.5s}
.blip.b3{top:42%;left:78%;animation-delay:1s}.blip.b4{top:76%;left:60%;background:var(--red);box-shadow:0 0 10px var(--red);animation-delay:1.4s}
.radar-core{position:absolute;left:50%;top:50%;width:62px;height:62px;margin:-31px 0 0 -31px;border-radius:50%;background:linear-gradient(135deg,#1b1606,#0b0f17);border:2px solid var(--gold);display:flex;align-items:center;justify-content:center;font-size:26px;animation:corePulse 1.4s ease infinite}
.a-bars{display:flex;align-items:flex-end;gap:4px;height:34px;margin-bottom:14px}
.a-bars i{display:block;width:5px;height:6px;border-radius:2px;animation:barUp 1s ease-in-out infinite;opacity:.9}
.analyze-text{font-size:13px;font-weight:800;color:var(--gold);text-align:center;max-width:280px;line-height:1.5;min-height:20px;margin-bottom:10px}
.a-prog{width:min(260px,78vw);height:6px;background:rgba(255,255,255,.07);border-radius:6px;overflow:hidden}
.a-prog i{display:block;height:100%;width:0;border-radius:6px;background:linear-gradient(90deg,#BF953F,#FCF6BA,#00E676);box-shadow:0 0 12px rgba(255,215,0,.6);transition:width .2s linear}
.a-pct{font-size:11px;font-weight:900;color:var(--muted);margin:6px 0 12px}
.a-steps{list-style:none;width:min(260px,78vw);display:flex;flex-direction:column;gap:5px}
.a-steps li{font-size:10.5px;font-weight:700;color:#4d5668;display:flex;gap:8px;align-items:center;transition:color .25s}
.a-steps li b{width:14px;text-align:center;font-size:10px}
.a-steps li.now{color:var(--gold)}
.a-steps li.done{color:var(--green)}

/* ===== v33: раскрытие сигнала ===== */
.sig.is-call{border-color:var(--green)}
.sig.is-put{border-color:var(--red)}
.sig.reveal{animation:sigPop .7s cubic-bezier(.2,.9,.3,1.25)}
.sig.reveal.is-call{animation:sigPop .7s cubic-bezier(.2,.9,.3,1.25),glowGreen 1.5s .15s ease-out}
.sig.reveal.is-put{animation:sigPop .7s cubic-bezier(.2,.9,.3,1.25),glowRed 1.5s .15s ease-out}
.sig-dir{position:relative;overflow:hidden}
.sig-dir::after{content:"";position:absolute;top:0;bottom:0;left:0;width:35%;background:linear-gradient(100deg,transparent,rgba(255,255,255,.3),transparent);transform:translateX(-130%)}
.sig.reveal .sig-dir::after{animation:shine 1.1s .35s ease}
.sig.reveal .sig-dir{animation:sigPop .6s .1s cubic-bezier(.2,.9,.3,1.3) both}
.sig .ind-row .ind{animation:chipIn .4s both}
.conf{margin:2px 0 10px;text-align:left}
.conf-top{display:flex;align-items:baseline;gap:8px;font-size:10px;font-weight:800;color:var(--muted);margin-bottom:5px}
.conf-top b{font-size:15px;color:var(--gold);font-weight:900}
.conf-top em{font-style:normal;margin-left:auto;font-size:9.5px;color:#c5cdd8}
.conf-bar{height:7px;border-radius:7px;background:rgba(255,255,255,.07);overflow:hidden}
.conf-bar i{display:block;height:100%;width:0;border-radius:7px;background:linear-gradient(90deg,#BF953F,#FFD700);transition:width 1.1s cubic-bezier(.2,.8,.2,1)}
.sig.is-call .conf-bar i{background:linear-gradient(90deg,#00A152,#00E676)}
.sig.is-put .conf-bar i{background:linear-gradient(90deg,#D50000,#FF5252)}
#sigStrat{white-space:normal}
.d-head{font-size:10px;font-weight:900;color:var(--gold);letter-spacing:.4px;margin-bottom:6px;text-transform:uppercase}
.d-sum{font-size:11.5px;font-weight:800;color:#eef2f8;line-height:1.5;margin-bottom:7px}
.d-list{list-style:none;display:flex;flex-direction:column;gap:5px}
.d-list li{display:flex;gap:7px;align-items:flex-start;font-size:10.8px;color:#c5cdd8;line-height:1.5;animation:liRise .4s both}
.d-list li i{font-style:normal;color:var(--green);font-weight:900;flex-shrink:0}
.d-note{font-size:10.3px;line-height:1.5;margin-top:6px;padding:6px 8px;border-radius:8px}
.d-note.against{background:rgba(255,215,0,.07);color:#e8d9a0;border:1px solid rgba(255,215,0,.2)}
.d-note.warn{background:rgba(255,82,82,.08);color:#ffb3b3;border:1px solid rgba(255,82,82,.25)}
.d-foot{font-size:9.5px;color:var(--muted);font-weight:700;margin-top:7px}

/* ===== v33: чат с сигналами ===== */
.nav{position:relative}
.nav-badge{position:absolute;top:-3px;right:-1px;min-width:14px;height:14px;border-radius:7px;background:var(--red);color:#fff;font-size:8px;font-weight:900;display:flex;align-items:center;justify-content:center;padding:0 3px;animation:pulse 1.2s ease infinite}
#tabSigs{padding:0;overflow:hidden}
.chat-head{display:flex;align-items:center;gap:10px;padding:12px 14px;background:linear-gradient(180deg,#10151f,#0b0f17);border-bottom:1px solid var(--border-g)}
.bot-ava{position:relative;width:40px;height:40px;border-radius:50%;background:var(--gold-g);display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0}
.bot-ava i{position:absolute;inset:-4px;border-radius:50%;border:2px solid rgba(255,215,0,.5);animation:ringOut 2s ease-out infinite}
.chat-title{flex:1;min-width:0}
.chat-title b{font-size:13px;display:block}
.chat-title small{font-size:9.5px;color:var(--green);font-weight:800;display:flex;align-items:center;gap:5px;margin-top:2px}
.dot{width:7px;height:7px;border-radius:50%;background:var(--green);display:inline-block;animation:pulse 1.2s ease infinite}
.dot.off{background:var(--gold)}
.scan-ring{position:relative;width:46px;height:46px;flex-shrink:0}
.scan-ring svg{width:100%;height:100%;transform:rotate(-90deg)}
.scan-ring circle{fill:none;stroke-width:3}
.scan-ring .bg{stroke:rgba(255,255,255,.08)}
.scan-ring .fg{stroke:var(--gold);stroke-linecap:round;stroke-dasharray:106.8;stroke-dashoffset:106.8;transition:stroke-dashoffset .9s linear;filter:drop-shadow(0 0 3px rgba(255,215,0,.7))}
.scan-ring span{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:9.5px;font-weight:900;color:var(--gold)}
.chat-body{height:calc(100vh - 275px);min-height:320px;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:10px;background:radial-gradient(circle at 50% 0%,rgba(255,215,0,.05),transparent 60%),var(--inner)}
#chatList{display:flex;flex-direction:column;gap:10px}
.chat-empty{margin:auto;text-align:center;color:var(--muted);font-size:11px;font-weight:700;line-height:1.7;padding:20px}
.chat-empty b{display:block;font-size:36px;margin-bottom:8px;animation:bounceSmile 1.4s ease infinite}
.msg{display:flex;gap:8px;align-items:flex-start}
.msg.new{animation:msgIn .5s cubic-bezier(.2,.9,.3,1.2)}
.msg-ava{width:30px;height:30px;border-radius:50%;background:var(--card);border:1px solid var(--border-g);display:flex;align-items:center;justify-content:center;font-size:15px;flex-shrink:0}
.msg-bub{flex:1;min-width:0;background:var(--card);border:1px solid var(--border);border-left:3px solid var(--gold);border-radius:4px 14px 14px 14px;padding:10px 12px}
.msg.is-call .msg-bub{border-left-color:var(--green)}
.msg.is-put .msg-bub{border-left-color:var(--red)}
.msg.wait .msg-bub{border-left-color:#4a5568}
.msg.new .msg-bub{animation:msgFlash 1.4s ease-out}
.msg-top{display:flex;justify-content:space-between;align-items:center;gap:6px;margin-bottom:7px}
.msg-top b{font-size:12.5px;color:var(--gold);font-weight:900}
.msg-time{font-size:9.5px;color:var(--muted);font-weight:700}
.msg-dir{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.pill{padding:5px 10px;border-radius:9px;font-weight:900;font-size:11.5px}
.msg-conf{font-size:10.5px;font-weight:900;color:var(--gold)}
.msg-bar{height:5px;border-radius:5px;background:rgba(255,255,255,.07);overflow:hidden;margin:8px 0}
.msg-bar i{display:block;height:100%;border-radius:5px;transition:width 1.1s cubic-bezier(.2,.8,.2,1);background:linear-gradient(90deg,#BF953F,#FFD700)}
.msg.is-call .msg-bar i{background:linear-gradient(90deg,#00A152,#00E676)}
.msg.is-put .msg-bar i{background:linear-gradient(90deg,#D50000,#FF5252)}
.msg-meta{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}
.msg-meta span{font-size:9.5px;font-weight:800;color:#c5cdd8;background:var(--inner);border:1px solid var(--border);padding:3px 7px;border-radius:7px}
.msg-sum{font-size:11.5px;color:#e6ebf2;font-weight:700;line-height:1.5}
.msg-bub .d-sum{margin-top:2px}
.msg-bub .ind-row{justify-content:flex-start;margin:8px 0 0}
.msg-live{margin-top:9px;font-size:10.5px;font-weight:900;color:var(--gold);text-align:center;padding:6px 8px;border:1px dashed var(--gold);border-radius:8px;background:rgba(255,215,0,.05)}
.msg-live.done{color:var(--muted);border:1px solid var(--border);background:transparent}
.msg-foot{margin-top:7px;font-size:9px;color:var(--muted);font-weight:700}
.typing{display:flex;align-items:center;gap:5px;padding:2px 4px 2px 38px}
.typing span{width:6px;height:6px;border-radius:50%;background:var(--gold);animation:dots 1.2s ease infinite}
.typing span:nth-child(2){animation-delay:.15s}.typing span:nth-child(3){animation-delay:.3s}
.typing em{font-style:normal;font-size:10px;color:var(--muted);font-weight:700;margin-left:4px}
.chat-foot{padding:8px 12px;font-size:9px;color:var(--muted);font-weight:700;text-align:center;border-top:1px solid var(--border);background:var(--card);line-height:1.5}

/* ===== v33: избранное в профиле ===== */
.prof-fav{margin:6px 0 12px;padding:12px;background:var(--inner);border:1px solid var(--border-g);border-radius:12px}
.prof-fav h4{font-size:12px;font-weight:900;margin-bottom:4px}
.prof-fav .fav-list{max-height:230px}

/* ===== v35 polish ===== */
.msg-entry{display:flex;align-items:center;justify-content:center;gap:8px;margin:2px 0 9px;padding:9px 10px;border-radius:12px;background:linear-gradient(135deg,rgba(255,215,0,.18),rgba(255,215,0,.04));border:1px solid rgba(255,215,0,.45);font-size:11px;font-weight:800;color:#f0e2a6;letter-spacing:.3px}
.msg-entry b{font-size:17px;font-weight:900;color:var(--gold);letter-spacing:.8px;text-shadow:0 0 10px rgba(255,215,0,.45)}
.msg-live{position:relative;overflow:hidden}
.msg-live.wait{color:var(--gold);border-color:var(--gold);animation:livePulse 1.2s ease-in-out infinite}
.msg-live.run{color:#7dffb3;border:1px solid rgba(0,230,118,.55);background:rgba(0,230,118,.08);border-style:solid}
.msg-live.done{animation:none}
.msg-live .lv-bar{position:absolute;left:0;bottom:0;height:3px;width:0;background:linear-gradient(90deg,#FFD700,#00E676);transition:width .9s linear}
@keyframes livePulse{0%,100%{box-shadow:0 0 0 rgba(255,215,0,0)}50%{box-shadow:0 0 14px rgba(255,215,0,.35)}}
.pg-card{background:linear-gradient(160deg,#121826,#0a0e17);border:1px solid var(--border-g);border-radius:16px;padding:12px;margin-bottom:12px}
.pg-row{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:10px;font-weight:900;letter-spacing:.5px;color:var(--muted);margin-bottom:6px}
.pg-row b{color:var(--gold);font-size:12px}
.pg-lvl{font-size:15px;font-weight:900;margin-bottom:8px}
.pg-bar{height:11px;border-radius:11px;background:rgba(255,255,255,.07);overflow:hidden;position:relative}
.pg-bar i{display:block;height:100%;width:0;border-radius:11px;background:linear-gradient(90deg,#FFB300,#FFD700,#FFF176);box-shadow:0 0 10px rgba(255,215,0,.5);transition:width .9s cubic-bezier(.2,.8,.2,1)}
.pg-bar.gr i{background:linear-gradient(90deg,#00A152,#00E676);box-shadow:0 0 10px rgba(0,230,118,.45)}
.pg-sub{font-size:9.5px;color:var(--muted);font-weight:700;margin:6px 0 12px}
.pg-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:12px}
.pg-grid div{background:var(--inner);border:1px solid var(--border);border-radius:11px;padding:8px 2px;text-align:center}
.pg-grid b{display:block;font-size:16px;font-weight:900;color:var(--gold)}
.pg-grid span{font-size:8px;color:var(--muted);font-weight:800;letter-spacing:.4px}
.ab-hero{text-align:center;padding:18px 12px;border-radius:16px;background:radial-gradient(circle at 50% 0%,rgba(255,215,0,.2),transparent 70%),linear-gradient(160deg,#121826,#0a0e17);border:1px solid var(--border-g);margin-bottom:12px}
.ab-hero .lg{font-size:38px;margin-bottom:4px}
.ab-hero h4{font-size:17px;font-weight:900;letter-spacing:1px;margin-bottom:6px}
.ab-hero p{font-size:11.5px;color:#c5cdd8;line-height:1.7}
.ab-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}
.ab-it{background:var(--inner);border:1px solid var(--border-g);border-radius:13px;padding:11px 10px}
.ab-it b{display:block;font-size:11.5px;font-weight:900;margin-bottom:4px}
.ab-it span{font-size:10px;color:#b8c0d0;line-height:1.55;font-weight:600}
.ab-steps{background:var(--inner);border:1px solid var(--border-g);border-radius:14px;padding:12px;margin-bottom:12px}
.ab-steps h5{font-size:11.5px;font-weight:900;margin-bottom:8px;color:var(--gold)}
.ab-st{display:flex;gap:9px;align-items:flex-start;font-size:10.8px;color:#d5dbe6;line-height:1.55;margin-bottom:7px;font-weight:600}
.ab-st i{flex-shrink:0;width:20px;height:20px;border-radius:50%;background:linear-gradient(135deg,#FFB300,#FFD700);color:#1a1200;font-style:normal;font-weight:900;font-size:10.5px;display:flex;align-items:center;justify-content:center}
.ab-warn{font-size:9.5px;color:var(--muted);line-height:1.6;text-align:center;margin-bottom:12px;font-weight:600}
.rep-hero{display:flex;align-items:center;gap:12px;margin-bottom:12px}
.rep-ring{position:relative;width:78px;height:78px;flex-shrink:0}
.rep-ring svg{width:100%;height:100%;transform:rotate(-90deg)}
.rep-ring circle{fill:none;stroke-width:7}
.rep-ring .bg{stroke:rgba(255,255,255,.08)}
.rep-ring .fg{stroke-linecap:round;stroke-dasharray:213.6;filter:drop-shadow(0 0 5px currentColor)}
.rep-ring span{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:19px;font-weight:900}
.rep-ring span small{font-size:7.5px;color:var(--muted);letter-spacing:.6px;font-weight:800}
.rep-hero .rh{flex:1;min-width:0}
.rep-stats4{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:10px}
.rep-stats4 div{background:var(--inner);border:1px solid var(--border);border-radius:11px;padding:8px 2px;text-align:center}
.rep-stats4 b{display:block;font-size:15px;font-weight:900}
.rep-stats4 span{font-size:7.5px;color:var(--muted);font-weight:800;letter-spacing:.3px}
.rep-leg{display:flex;justify-content:space-between;font-size:9.5px;font-weight:800;margin:-4px 0 11px}
.rep-sec{font-size:9px;letter-spacing:.8px;font-weight:900;color:var(--muted);margin:10px 0 6px}
.nav.on{text-shadow:0 0 10px rgba(255,215,0,.5)}
.card{box-shadow:0 6px 22px rgba(0,0,0,.35)}
.msg-bub{box-shadow:0 4px 14px rgba(0,0,0,.3)}

/* ===== v36: чёткий шрифт, чёрный фон с узорами ===== */
*{font-family:Inter,"Segoe UI",Roboto,Arial,sans-serif;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
body{background-color:#020306;background-image:url("data:image/svg+xml,%3Csvg%20xmlns=%22http://www.w3.org/2000/svg%22%20width=%22140%22%20height=%22140%22%20viewBox=%220%200%20140%20140%22%3E%3Cpath%20d=%22M70%200L140%2070L70%20140L0%2070Z%22%20fill=%22none%22%20stroke=%22%23d4af37%22%20stroke-opacity=%22.07%22%20stroke-width=%221%22/%3E%3Cpath%20d=%22M70%2063L71.54%2068.46L77%2070L71.54%2071.54L70%2077L68.46%2071.54L63%2070L68.46%2068.46Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.38%22/%3E%3Cpath%20d=%22M0%20-5L1.1%20-1.1L5%200L1.1%201.1L0%205L-1.1%201.1L-5%200L-1.1%20-1.1Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.3%22/%3E%3Cpath%20d=%22M140%20-5L141.1%20-1.1L145%200L141.1%201.1L140%205L138.9%201.1L135%200L138.9%20-1.1Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.3%22/%3E%3Cpath%20d=%22M0%20135L1.1%20138.9L5%20140L1.1%20141.1L0%20145L-1.1%20141.1L-5%20140L-1.1%20138.9Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.3%22/%3E%3Cpath%20d=%22M140%20135L141.1%20138.9L145%20140L141.1%20141.1L140%20145L138.9%20141.1L135%20140L138.9%20138.9Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.3%22/%3E%3Cpath%20d=%22M35%2032L35.66%2034.34L38%2035L35.66%2035.66L35%2038L34.34%2035.66L32%2035L34.34%2034.34Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.22%22/%3E%3Cpath%20d=%22M105%20102L105.66%20104.34L108%20105L105.66%20105.66L105%20108L104.34%20105.66L102%20105L104.34%20104.34Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.22%22/%3E%3Cpath%20d=%22M105%2032L105.66%2034.34L108%2035L105.66%2035.66L105%2038L104.34%2035.66L102%2035L104.34%2034.34Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.22%22/%3E%3Cpath%20d=%22M35%20102L35.66%20104.34L38%20105L35.66%20105.66L35%20108L34.34%20105.66L32%20105L34.34%20104.34Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.22%22/%3E%3Ccircle%20cx=%2270%22%20cy=%2220%22%20r=%221%22%20fill=%22%23fff%22%20fill-opacity=%22.28%22/%3E%3Ccircle%20cx=%2220%22%20cy=%2270%22%20r=%221%22%20fill=%22%23fff%22%20fill-opacity=%22.28%22/%3E%3Ccircle%20cx=%22120%22%20cy=%2270%22%20r=%221%22%20fill=%22%23fff%22%20fill-opacity=%22.28%22/%3E%3Ccircle%20cx=%2270%22%20cy=%22120%22%20r=%221%22%20fill=%22%23fff%22%20fill-opacity=%22.28%22/%3E%3C/svg%3E");background-size:140px 140px}
.header,.card,.pg-card,.ab-hero{background-image:url("data:image/svg+xml,%3Csvg%20xmlns=%22http://www.w3.org/2000/svg%22%20width=%2256%22%20height=%2256%22%20viewBox=%220%200%2056%2056%22%3E%3Cpath%20d=%22M28%200L56%2028L28%2056L0%2028Z%22%20fill=%22none%22%20stroke=%22%23d4af37%22%20stroke-opacity=%22.06%22%20stroke-width=%221%22/%3E%3Cpath%20d=%22M28%2024.8L28.704%2027.296L31.2%2028L28.704%2028.704L28%2031.2L27.296%2028.704L24.8%2028L27.296%2027.296Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.22%22/%3E%3Cpath%20d=%22M0%20-2L0.44%20-0.44L2%200L0.44%200.44L0%202L-0.44%200.44L-2%200L-0.44%20-0.44Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.16%22/%3E%3Cpath%20d=%22M56%20-2L56.44%20-0.44L58%200L56.44%200.44L56%202L55.56%200.44L54%200L55.56%20-0.44Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.16%22/%3E%3Cpath%20d=%22M0%2054L0.44%2055.56L2%2056L0.44%2056.44L0%2058L-0.44%2056.44L-2%2056L-0.44%2055.56Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.16%22/%3E%3Cpath%20d=%22M56%2054L56.44%2055.56L58%2056L56.44%2056.44L56%2058L55.56%2056.44L54%2056L55.56%2055.56Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.16%22/%3E%3C/svg%3E"),linear-gradient(160deg,rgba(255,255,255,.02),rgba(0,0,0,0));background-size:56px 56px,100% 100%}
.card,.header{background-color:#080b12;border-color:rgba(212,175,55,.28)}
.chat-body{background-image:url("data:image/svg+xml,%3Csvg%20xmlns=%22http://www.w3.org/2000/svg%22%20width=%2256%22%20height=%2256%22%20viewBox=%220%200%2056%2056%22%3E%3Cpath%20d=%22M28%200L56%2028L28%2056L0%2028Z%22%20fill=%22none%22%20stroke=%22%23d4af37%22%20stroke-opacity=%22.06%22%20stroke-width=%221%22/%3E%3Cpath%20d=%22M28%2024.8L28.704%2027.296L31.2%2028L28.704%2028.704L28%2031.2L27.296%2028.704L24.8%2028L27.296%2027.296Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.22%22/%3E%3Cpath%20d=%22M0%20-2L0.44%20-0.44L2%200L0.44%200.44L0%202L-0.44%200.44L-2%200L-0.44%20-0.44Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.16%22/%3E%3Cpath%20d=%22M56%20-2L56.44%20-0.44L58%200L56.44%200.44L56%202L55.56%200.44L54%200L55.56%20-0.44Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.16%22/%3E%3Cpath%20d=%22M0%2054L0.44%2055.56L2%2056L0.44%2056.44L0%2058L-0.44%2056.44L-2%2056L-0.44%2055.56Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.16%22/%3E%3Cpath%20d=%22M56%2054L56.44%2055.56L58%2056L56.44%2056.44L56%2058L55.56%2056.44L54%2056L55.56%2055.56Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.16%22/%3E%3C/svg%3E"),radial-gradient(circle at 50% 0%,rgba(255,215,0,.05),transparent 60%);background-size:56px 56px,100% 100%}
.bnav{background-image:url("data:image/svg+xml,%3Csvg%20xmlns=%22http://www.w3.org/2000/svg%22%20width=%2256%22%20height=%2256%22%20viewBox=%220%200%2056%2056%22%3E%3Cpath%20d=%22M28%200L56%2028L28%2056L0%2028Z%22%20fill=%22none%22%20stroke=%22%23d4af37%22%20stroke-opacity=%22.06%22%20stroke-width=%221%22/%3E%3Cpath%20d=%22M28%2024.8L28.704%2027.296L31.2%2028L28.704%2028.704L28%2031.2L27.296%2028.704L24.8%2028L27.296%2027.296Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.22%22/%3E%3Cpath%20d=%22M0%20-2L0.44%20-0.44L2%200L0.44%200.44L0%202L-0.44%200.44L-2%200L-0.44%20-0.44Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.16%22/%3E%3Cpath%20d=%22M56%20-2L56.44%20-0.44L58%200L56.44%200.44L56%202L55.56%200.44L54%200L55.56%20-0.44Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.16%22/%3E%3Cpath%20d=%22M0%2054L0.44%2055.56L2%2056L0.44%2056.44L0%2058L-0.44%2056.44L-2%2056L-0.44%2055.56Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.16%22/%3E%3Cpath%20d=%22M56%2054L56.44%2055.56L58%2056L56.44%2056.44L56%2058L55.56%2056.44L54%2056L55.56%2055.56Z%22%20fill=%22%23d4af37%22%20fill-opacity=%220.16%22/%3E%3C/svg%3E");background-size:56px 56px}
.prof-top{justify-content:center}
.prof-top .profile-photo-upload{width:112px;height:112px}
.pg-grid{grid-template-columns:repeat(2,1fr)}
.pg-grid b{font-size:20px}
.pg-grid span{font-size:9px}
.exp-bar{display:flex;align-items:center;gap:10px;padding:9px 12px;background:#06080d;border-bottom:1px solid var(--border-g)}
.exp-bar>span{font-size:9px;font-weight:900;letter-spacing:.8px;color:var(--muted);white-space:nowrap}
.exp-bar .filter-row{flex:1;justify-content:flex-end;gap:5px;margin:0;flex-wrap:nowrap}
.exp-bar .filter-btn{flex:1;max-width:62px;text-align:center;padding:7px 4px;font-size:10.5px}

/* ===== v37: золотые переливающиеся кнопки, иконки, рынок закрыт ===== */
.btn{position:relative;overflow:hidden;isolation:isolate}
.btn::after{content:"";position:absolute;top:-10%;bottom:-10%;left:-70%;width:40%;background:linear-gradient(105deg,transparent,rgba(255,255,255,.6),transparent);transform:skewX(-22deg);animation:btnShine 3.8s ease-in-out infinite;pointer-events:none;z-index:2}
@keyframes btnShine{0%{left:-70%}55%,100%{left:140%}}
.btn-gold{background:linear-gradient(110deg,#7d5d14 0%,#d9a826 12%,#fff4b0 26%,#e8bb3a 40%,#9c7218 56%,#f8e48e 72%,#cf9f2d 88%,#7d5d14 100%);background-size:200% 100%;animation:sh 4s linear infinite;color:#1a1200;text-shadow:0 1px 0 rgba(255,255,255,.35);border:1px solid rgba(255,236,150,.7);box-shadow:inset 0 1px 0 rgba(255,255,255,.6),inset 0 -3px 0 rgba(0,0,0,.18),0 6px 22px rgba(212,166,42,.4)}
.btn-dark{background:linear-gradient(180deg,#0e131d,#06080d);color:#e9d78a;border:1px solid rgba(212,175,55,.6);box-shadow:inset 0 1px 0 rgba(255,215,0,.18),0 3px 12px rgba(0,0,0,.5)}
.btn-dark::after{background:linear-gradient(105deg,transparent,rgba(255,215,0,.28),transparent);animation-duration:5.2s}
.btn-green{background:linear-gradient(180deg,#3df08f,#00b45a);color:#02351a;border:1px solid rgba(160,255,200,.7);box-shadow:inset 0 1px 0 rgba(255,255,255,.55),0 5px 18px rgba(0,230,118,.3)}
.btn-red{background:linear-gradient(180deg,#ff6b6b,#c4122f);color:#fff;border:1px solid rgba(255,170,170,.6);box-shadow:inset 0 1px 0 rgba(255,255,255,.4),0 5px 18px rgba(255,23,68,.3)}
.btn:active{transform:scale(.98)}
.filter-btn{position:relative;overflow:hidden;background:linear-gradient(180deg,#0e131d,#06080d);color:#cdbf86;border:1px solid rgba(212,175,55,.5)}
.filter-btn.on{background:linear-gradient(110deg,#8a6a1c,#e8bb3a,#fff4b0,#e8bb3a,#8a6a1c);background-size:200% 100%;animation:sh 4s linear infinite;color:#1a1200;border:1px solid rgba(255,236,150,.7);box-shadow:0 0 14px rgba(255,215,0,.35),inset 0 1px 0 rgba(255,255,255,.6)}
.ai{display:inline-block;flex-shrink:0;border-radius:50%;overflow:hidden;vertical-align:middle;box-shadow:0 0 0 1.5px rgba(255,255,255,.16),0 2px 7px rgba(0,0,0,.55)}
.ai svg{display:block;width:100%;height:100%}
.ai-pair{display:inline-flex;align-items:center;flex-shrink:0}
.ai-pair .ai+.ai{margin-left:-9px;box-shadow:0 0 0 2px #080b12,0 2px 7px rgba(0,0,0,.55)}
.ai-row{display:inline-flex;align-items:center;gap:10px;font-style:normal;min-width:0}
.ai-name{font-style:normal;font-weight:800;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.selected-asset-info .ai-name{color:var(--gold);font-size:13.5px}
.fav-item.closed{opacity:.55}
.fav-item .lock{font-size:8.5px;font-weight:900;letter-spacing:.5px;color:#ff8a80;border:1px solid rgba(255,82,82,.5);border-radius:7px;padding:3px 6px;white-space:nowrap}
.msg-top b.ai-row{color:var(--gold);font-size:12.5px;gap:8px}
.closed-box{text-align:center;align-items:center;gap:10px;padding:22px 18px;border-color:rgba(255,82,82,.55);background-image:linear-gradient(180deg,rgba(255,82,82,.12),transparent 55%)}
.closed-box .cl-ico{font-size:46px;filter:drop-shadow(0 0 14px rgba(255,82,82,.6))}
.closed-box h3{font-size:20px;letter-spacing:1.5px;font-weight:900}
.closed-box .cl-asset{display:flex;justify-content:center;padding:8px 14px;border-radius:12px;background:var(--inner);border:1px solid var(--border-g);max-width:100%}
.closed-box p{font-size:12px;line-height:1.65;color:#c5cdd8;font-weight:600}
.closed-box .cl-open{font-size:12px;font-weight:900;color:var(--gold);background:rgba(255,215,0,.08);border:1px solid var(--border-g);border-radius:12px;padding:10px 12px;line-height:1.5;width:100%}
.closed-box .btn{width:100%}
#closedModal{z-index:340}
.mkt-note{padding:8px 12px;font-size:10px;font-weight:800;line-height:1.5;color:#ffb4ab;background:rgba(255,82,82,.08);border-bottom:1px solid rgba(255,82,82,.35)}
.selected-asset-box{background-image:linear-gradient(135deg,rgba(255,215,0,.1),transparent 60%);box-shadow:0 0 18px rgba(255,215,0,.12)}
.header h1,.header .title{text-shadow:0 0 14px rgba(255,215,0,.3)}
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
      <span class="sig-count-badge" id="headerSigCount">Сигналов: 0</span>
      <span style="font-size:10px;color:var(--gold);font-weight:800" id="liveClock">🕒 --:--:--</span>
    </div>
    <div class="selected-asset-box" onclick="openCatalogModal()">
      <div class="selected-asset-info">
        <span data-t="lblActiveAsset">ВЫБРАННЫЙ АКТИВ</span>
        <span id="currentAssetDisplay"></span>
      </div>
      <span style="font-size:22px">📋</span>
    </div>
    <div class="grid2" style="margin-bottom:10px">
      <div>
        <label class="lbl" data-t="lblTf">ИНТЕРВАЛ</label>
        <select class="sel" id="tf">
          <option value="S5">5 сек</option><option value="S15">15 сек</option><option value="S30">30 сек</option>
          <option value="M1" selected>1 мин</option><option value="M2">2 мин</option><option value="M3">3 мин</option><option value="M4">4 мин</option>
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
      <button class="sig-x" type="button" onclick="cancelSig()" aria-label="Закрыть">✕</button>
      <div class="sig-meta" id="sigMeta"></div>
      <div class="sig-dir call" id="sigDir">⬆️ CALL</div>
      <div class="timer">⏱ <span id="cd">01:00</span></div>
      <div class="ind-row" id="sigInd"></div>
      <div class="strat" id="sigStrat"></div>
      <div class="sig-stats"><span><span data-t="lblCombo">Связка</span>: <b id="sigCombo">Multi-Factor</b></span></div>
      <button class="btn btn-red btn-sm" id="btnCancel" onclick="cancelSig()" style="display:none" data-t="btnCancel">✖ Отменить</button>
      <button class="btn btn-gold btn-sm" id="btnNew" onclick="getSig()" style="display:none" data-t="btnNewSig">⚡ Новый сигнал</button>
    </div>
  </div>

  <div class="card tab hidden" id="tabSigs">
    <div class="chat-head">
      <div class="bot-ava">🤖<i></i></div>
      <div class="chat-title">
        <b class="g-text">Чат</b>
        <small id="chatStatus"><i class="dot"></i> онлайн</small>
      </div>
      <div class="scan-ring" title="До следующего скана">
        <svg viewBox="0 0 40 40"><circle class="bg" cx="20" cy="20" r="17"></circle><circle class="fg" id="scanArc" cx="20" cy="20" r="17"></circle></svg>
        <span id="chatCd">--:--</span>
      </div>
    </div>
    <div class="exp-bar"><span>⏱ ЭКСПИРАЦИЯ</span><div class="filter-row" id="expFilterRow">
      <button class="filter-btn" data-exp="1" onclick="setChatExp(1)">1 мин</button>
      <button class="filter-btn on" data-exp="2" onclick="setChatExp(2)">2 мин</button>
      <button class="filter-btn" data-exp="3" onclick="setChatExp(3)">3 мин</button>
      <button class="filter-btn" data-exp="5" onclick="setChatExp(5)">5 мин</button>
    </div></div>
    <div class="mkt-note hidden" id="mktNote"></div>
    <div class="chat-body" id="chatBody">
      <div class="typing hidden" id="chatTyping"><span></span><span></span><span></span><em>бот проверяет графики Binance…</em></div>
      <div class="chat-empty" id="chatEmpty"><b>📡</b>Бот сканирует реальные активы Binance (без OTC).<br>Сначала приходит сигнал со временем входа, затем таймер и сделка. Пока сделка идёт — новых сигналов нет.</div>
      <div id="chatList"></div>
    </div>
    <div class="chat-foot">Один сигнал за раз · новый приходит через 1 мин после экспирации · активы без OTC · не является финансовой рекомендацией</div>
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
    <div class="news-updated"><span id="newsUpdated">Обновление...</span><button class="news-refresh" id="newsRefreshBtn" type="button" onclick="loadNews(true)"><b>🔄</b> <span data-t="btnRefreshNewsShort">Обновить</span></button></div>
    <div class="news-list" id="newsList">
      <div class="news-loading"><div class="news-loading-wheel"></div><div class="news-loading-text">Загружаю календарь новостей...</div></div>
    </div>
  </div>

  <div class="card tab hidden" id="tabProfile">
    <h3 class="g-text" style="font-size:13px;margin-bottom:12px" data-t="profTitle">👤 Профиль</h3>
    <div class="prof-top">
      <div class="profile-photo-upload" onclick="document.getElementById('profPhotoInput').click()">
        <img id="profilePhotoImg" class="hidden" alt="photo">
        <span id="profilePhotoIcon" style="font-size:32px">📷</span>
        <input type="file" id="profPhotoInput" accept="image/*" style="display:none" onchange="handleProfilePhoto(event)">
      </div>
    </div>
    <div class="pg-card">
      <div class="pg-row"><span>🏅 УРОВЕНЬ ТРЕЙДЕРА</span><b id="pgLvlPct">0%</b></div>
      <div class="pg-lvl g-text" id="pgLvlName">Новичок</div>
      <div class="pg-bar"><i id="pgLvlFill"></i></div>
      <div class="pg-sub" id="pgLvlNext">До уровня «Ученик»: ещё 10 сделок</div>
      <div class="pg-row"><span>🎯 ЦЕЛЬ: ВИНРЕЙТ 70%</span><b id="pgGoalPct">0%</b></div>
      <div class="pg-bar gr"><i id="pgGoalFill"></i></div>
      <div class="pg-sub" id="pgGoalTxt" style="margin-bottom:0">Начни сессию — шкала заполнится</div>
      <div class="pg-grid">
        <div><b id="stSig">0</b><span>СИГНАЛОВ</span></div>
        <div><b id="stSess">0</b><span>СЕССИЙ</span></div>
      </div>
    </div>
    <label class="lbl">Pocket Option ID</label>
    <input class="input" id="profPocketId" placeholder="Ваш ID" onchange="saveProfileSettings()">
    <button class="btn btn-gold" id="sessBtn" onclick="openSession()" style="margin-bottom:8px">▶ Начать сессию</button>
    <button class="btn btn-dark btn-sm" onclick="openHistoryModal()" style="margin-bottom:8px">📜 История сигналов и сессий</button>
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
    <div class="ab-hero"><div class="lg">🚀</div><h4 class="g-text">TEAM MASTER VIP</h4>
      <p>Команда трейдеров и разработчиков. Мы соединяем технический анализ, искусственный интеллект и реальный рыночный опыт, чтобы давать понятные и рабочие сигналы для Pocket Option.</p></div>
    <div class="ab-grid">
      <div class="ab-it"><b>🤖 Бот-сигналы</b><span>Реальные активы без OTC. Один сигнал за раз.</span></div>
      <div class="ab-it"><b>🔍 Проверка</b><span>Каждый сигнал проходит проверку по истории графика.</span></div>
      <div class="ab-it"><b>📰 Новости</b><span>Календарь High/Medium событий в твоём времени.</span></div>
      <div class="ab-it"><b>📚 Обучение</b><span>50 стратегий с графиками и тестами.</span></div>
      <div class="ab-it"><b>📷 AI-сканер</b><span>Анализ скриншота графика за секунды.</span></div>
      <div class="ab-it"><b>🏁 Итоги сессий</b><span>Винрейт, серии и разбор каждой сессии.</span></div>
    </div>
    <div class="ab-steps"><h5>⚡ КАК РАБОТАЕТ СИГНАЛ</h5>
      <div class="ab-st"><i>1</i><span>В чат приходит сигнал: актив, направление и точное время входа.</span></div>
      <div class="ab-st"><i>2</i><span>Запускается таймер до входа — подготовь ставку.</span></div>
      <div class="ab-st"><i>3</i><span>В указанное время заходишь в сделку на выбранную экспирацию.</span></div>
      <div class="ab-st"><i>4</i><span>Пока сделка идёт, новых сигналов нет. Через 1 минуту после экспирации приходит следующий.</span></div>
    </div>
    <div class="ab-warn">⚠️ Торговля бинарными опционами связана с риском. Сигналы не являются финансовой рекомендацией. Соблюдай риск-менеджмент.</div>
    <div class="gap2"></div>
    <a href="https://t.me/+uekq4TquqkM4Mzcy" target="_blank" class="btn btn-gold">📢 Наш Telegram-канал</a>
  </div>

</div>

<div class="bnav">
  <div class="nav on" onclick="showTab('tabSig',this)"><span>⚡</span><span data-t="navSig">Сигнал</span></div>
  <div class="nav" onclick="showTab('tabSigs',this)"><span>🤖</span><span data-t="navSigs">Чат</span><span class="nav-badge hidden" id="navBadge">0</span></div>
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

<div class="modal-overlay hidden" id="closedModal" onclick="if(event.target===this)closeClosedModal()">
  <div class="modal-content closed-box">
    <div class="cl-ico">🔒</div>
    <h3 class="g-text">РЫНОК ЗАКРЫТ</h3>
    <div class="cl-asset" id="clAsset"></div>
    <p id="clText">Этот актив торгуется на реальном рынке, который сейчас закрыт. Получить сигнал по нему нельзя.</p>
    <div class="cl-open" id="clOpen"></div>
    <button class="btn btn-gold" onclick="closeClosedModal()">Понятно</button>
    <button class="btn btn-dark btn-sm" onclick="closeClosedModal();openCatalogModal()">Выбрать другой актив</button>
  </div>
</div>

<div class="sess-overlay hidden" id="sessOverlay">
  <div class="sess">
    <div class="sess-top">
      <div><b class="g-text">📊 ТОРГОВАЯ СЕССИЯ</b><small id="sessClock">⏱ 00:00:00</small></div>
      <button class="modal-close" onclick="minimizeSession()" aria-label="Свернуть">⌄</button>
    </div>
    <div class="sess-ring">
      <svg viewBox="0 0 120 120"><circle class="bg" cx="60" cy="60" r="52"></circle><circle class="fg" id="sessArc" cx="60" cy="60" r="52"></circle></svg>
      <div class="mid"><b id="sessWr">—</b><span>ВИНРЕЙТ СЕССИИ</span></div>
    </div>
    <div class="sess-chips"><span>Сделок: <b id="sessTotal">0</b></span><span id="sessStreakBox">Серия: <b id="sessStreak">—</b></span></div>
    <div class="sess-dots" id="sessDots"></div>
    <div class="sess-grid">
      <div class="sess-box win">
        <div class="sb-title">✅ ПЛЮС</div>
        <div class="sb-num" id="sessW">0</div>
        <div class="sb-btns"><button class="sb-m" onclick="sessAdj('w',-1)" aria-label="Убрать плюс">−</button><button class="sb-p" onclick="sessAdj('w',1)" aria-label="Добавить плюс">+</button></div>
      </div>
      <div class="sess-box loss">
        <div class="sb-title">❌ МИНУС</div>
        <div class="sb-num" id="sessL">0</div>
        <div class="sb-btns"><button class="sb-m" onclick="sessAdj('l',-1)" aria-label="Убрать минус">−</button><button class="sb-p" onclick="sessAdj('l',1)" aria-label="Добавить минус">+</button></div>
      </div>
    </div>
    <button class="btn btn-gold" onclick="finishSession()">🏁 Завершить сессию</button>
    <button class="btn btn-dark btn-sm" onclick="minimizeSession()">Свернуть (сессия продолжится)</button>
  </div>
</div>

<div class="sess-overlay hidden" id="repOverlay">
  <div class="sess">
    <div class="sess-top"><div><b class="g-text">🏁 ИТОГ СЕССИИ</b><small>Сохранён во вкладке «Чат»</small></div></div>
    <div id="repBody"></div>
    <button class="btn btn-gold" onclick="openReportInChat()">📡 Открыть в сигналах</button>
    <button class="btn btn-dark btn-sm" onclick="closeReport()">Закрыть</button>
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
  <div class="radar">
    <div class="radar-ring r0"></div><div class="radar-ring r2"></div><div class="radar-ring r3"></div>
    <div class="radar-sweep"></div>
    <span class="blip b1"></span><span class="blip b2"></span><span class="blip b3"></span><span class="blip b4"></span>
    <div class="radar-core" id="analyzeSmile">📡</div>
  </div>
  <div class="a-bars" id="aBars"></div>
  <div class="analyze-text" id="analyzeTxt">⚡ Подключаюсь к рынку...</div>
  <div class="a-prog"><i id="aFill"></i></div>
  <div class="a-pct" id="aPct">0%</div>
  <ul class="a-steps" id="aSteps"></ul>
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
var TFS = {S5:5,S15:15,S30:30,M1:60,M2:120,M3:180,M4:240,M5:300,M10:600,M15:900};

try {
  favs = JSON.parse(localStorage.getItem("tmv_favs") || "[]");
  sigCount = parseInt(localStorage.getItem("tmv_sigs") || "0");
  signalHistory = JSON.parse(localStorage.getItem("tmv_history") || "[]");
} catch(e) {}

var I18N = {
  ru: {lblActiveAsset:"ВЫБРАННЫЙ АКТИВ",lblTf:"ИНТЕРВАЛ",lblExp:"ЭКСПИРАЦИЯ",btnCalcSig:"⚡ Просчитать сигнал",btnAutoSig:"🤖 Выбрать топ актив",btnCancel:"✖ Отменить",btnNewSig:"⚡ Новый сигнал",favTabTitle:"⭐ Избранные активы",favTabDesc:"Нажмите на любой актив для перехода.",profFavEmpty:"Пока пусто — добавь актив из каталога",btnClearFav:"🗑 Очистить избранное",newsTitle:"📰 Форекс-новости",newsDesc:"Экономический календарь. High и Medium важность. Время — ваше локальное.",btnRefreshNews:"🔄 Обновить",btnRefreshNewsShort:"Обновить",eduTitle:"📚 Связки и стратегии",eduDesc:"50 стратегий: у каждой свой график-пример, подробные условия CALL/PUT и тест из 5 вопросов.",btnRandomEdu:"🎲 Случайная стратегия",btnBack:"← Назад",scanTitle:"📷 AI Сканер графика",scanPhText:"Откройте камеру или загрузите скриншот",btnCam:"📷 Камера",btnUpload:"📁 Загрузить",btnScan:"⚡ Сканировать",btnStopCam:"⏹ Остановить",profTitle:"👤 Профиль",lblSigCount:"Сигналов:",lblFavCount:"Избранных:",btnResetProf:"Сбросить профиль",navSig:"Сигнал",navSigs:"Чат",navNews:"Новости",navEdu:"Стратегии",navScan:"Сканер",navProf:"Профиль",navAbout:"О нас",catalogTitle:"Каталог активов",lblCombo:"Связка"},
  en: {lblActiveAsset:"SELECTED ASSET",lblTf:"TIMEFRAME",lblExp:"EXPIRATION",btnCalcSig:"⚡ Calculate Signal",btnAutoSig:"🤖 Top Asset",btnCancel:"✖ Cancel",btnNewSig:"⚡ New Signal",favTabTitle:"⭐ Favorites",favTabDesc:"Tap any asset to select it.",profFavEmpty:"Empty — add from catalog",btnClearFav:"🗑 Clear favorites",newsTitle:"📰 Forex News",newsDesc:"Economic calendar. High & Medium impact. Local time.",btnRefreshNews:"🔄 Refresh",btnRefreshNewsShort:"Refresh",eduTitle:"📚 Strategies",eduDesc:"50 strategies with chart examples and a 5-question test.",btnRandomEdu:"🎲 Random strategy",btnBack:"← Back",scanTitle:"📷 AI Chart Scanner",scanPhText:"Open camera or upload screenshot",btnCam:"📷 Camera",btnUpload:"📁 Upload",btnScan:"⚡ Scan",btnStopCam:"⏹ Stop",profTitle:"👤 Profile",lblSigCount:"Signals:",lblFavCount:"Favorites:",btnResetProf:"Reset profile",navSig:"Signal",navSigs:"Chat",navNews:"News",navEdu:"Strategies",navScan:"Scanner",navProf:"Profile",navAbout:"About",catalogTitle:"Asset Catalog",lblCombo:"Combo"},
  ua: {lblActiveAsset:"ОБРАНИЙ АКТИВ",lblTf:"ІНТЕРВАЛ",lblExp:"ЕКСПІРАЦІЯ",btnCalcSig:"⚡ Розрахувати сигнал",btnAutoSig:"🤖 Топ актив",btnCancel:"✖ Скасувати",btnNewSig:"⚡ Новий сигнал",favTabTitle:"⭐ Обрані",favTabDesc:"Натисніть на актив.",profFavEmpty:"Порожньо",btnClearFav:"🗑 Очистити",newsTitle:"📰 Новини",newsDesc:"Економічний календар. High і Medium. Локальний час.",btnRefreshNews:"🔄 Оновити",btnRefreshNewsShort:"Оновити",eduTitle:"📚 Стратегії",eduDesc:"50 стратегій з графіками та тестом із 5 питань.",btnRandomEdu:"🎲 Випадкова",btnBack:"← Назад",scanTitle:"📷 AI Сканер",scanPhText:"Камера або скріншот",btnCam:"📷 Камера",btnUpload:"📁 Завантажити",btnScan:"⚡ Сканувати",btnStopCam:"⏹ Зупинити",profTitle:"👤 Профіль",lblSigCount:"Сигналів:",lblFavCount:"Обраних:",btnResetProf:"Скинути",navSig:"Сигнал",navSigs:"Чат",navNews:"Новини",navEdu:"Стратегії",navScan:"Сканер",navProf:"Профіль",navAbout:"Про нас",catalogTitle:"Каталог",lblCombo:"Зв'язка"}
};

function esc(s) {
  return String(s == null ? "" : s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function showTab(id, el) {
  document.querySelectorAll(".tab").forEach(function(t){ t.classList.add("hidden"); });
  document.getElementById(id).classList.remove("hidden");
  document.querySelectorAll(".nav").forEach(function(n){ n.classList.remove("on"); });
  if (el) el.classList.add("on");
  if (id === "tabSigs") openChat();
}

function getCategories() { return ASSETS[currentLang] || ASSETS.ru; }

// ===== ИКОНКИ АКТИВОВ (векторные, чёткие на любом экране) =====
function assetKey(a) { return String(a).replace(/[^\x00-\x7F]+/g, "").replace(/\s+/g, " ").trim(); }
function assetName(a) { return String(a).replace(/^[^\x00-\x7F]+\s*/, "").trim() || String(a); }
function is24x7(a) {
  if (/OTC/i.test(a)) return true;
  var k = assetKey(a);
  return /^(BTC|BCH)\//.test(k) || ["Bitcoin", "Ethereum", "Solana", "Dash", "Chainlink"].indexOf(k) >= 0;
}
function forexClosedNow() {
  var d = new Date(), wd = d.getUTCDay(), h = d.getUTCHours() + d.getUTCMinutes() / 60;
  return wd === 6 || (wd === 5 && h >= 22) || (wd === 0 && h < 22);
}
function forexReopenDate() {
  var d = new Date(), add = (7 - d.getUTCDay()) % 7;
  var t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + add, 22, 0, 0);
  if (t <= Date.now()) t += 7 * 86400000;
  return new Date(t);
}
function assetClosed(a) { return !is24x7(a) && forexClosedNow(); }

function _H(cs) { var n = cs.length, s = ""; cs.forEach(function(c, i) { s += '<rect x="0" y="' + (i * 24 / n) + '" width="24" height="' + (24 / n + .3) + '" fill="' + c + '"/>'; }); return s; }
function _V(cs) { var n = cs.length, s = ""; cs.forEach(function(c, i) { s += '<rect y="0" x="' + (i * 24 / n) + '" height="24" width="' + (24 / n + .3) + '" fill="' + c + '"/>'; }); return s; }
function _star(cx, cy, r, f) {
  var p = [];
  for (var i = 0; i < 10; i++) { var rr = i % 2 ? r * .4 : r, an = -Math.PI / 2 + i * Math.PI / 5; p.push((cx + rr * Math.cos(an)).toFixed(2) + "," + (cy + rr * Math.sin(an)).toFixed(2)); }
  return '<polygon points="' + p.join(" ") + '" fill="' + f + '"/>';
}
function _cres(cx, cy, r, f, bg) { return '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="' + f + '"/><circle cx="' + (cx + r * .35) + '" cy="' + cy + '" r="' + (r * .8) + '" fill="' + bg + '"/>'; }
var FL = {
  USD: function() { var s = _H(["#B22234", "#fff", "#B22234", "#fff", "#B22234", "#fff", "#B22234"]) + '<rect width="12" height="13.8" fill="#3C3B6E"/>'; for (var y = 0; y < 3; y++) for (var x = 0; x < 3; x++) s += '<circle cx="' + (2.4 + x * 3.6) + '" cy="' + (2.6 + y * 4.2) + '" r=".8" fill="#fff"/>'; return s; },
  EUR: function() { var s = '<rect width="24" height="24" fill="#003399"/>'; for (var i = 0; i < 12; i++) { var an = i * Math.PI / 6; s += '<circle cx="' + (12 + 7 * Math.cos(an)).toFixed(2) + '" cy="' + (12 + 7 * Math.sin(an)).toFixed(2) + '" r="1.1" fill="#FFCC00"/>'; } return s; },
  GBP: function() { return '<rect width="24" height="24" fill="#012169"/><path d="M0 0L24 24M24 0L0 24" stroke="#fff" stroke-width="4.5"/><path d="M0 0L24 24M24 0L0 24" stroke="#C8102E" stroke-width="1.6"/><path d="M12 0V24M0 12H24" stroke="#fff" stroke-width="7"/><path d="M12 0V24M0 12H24" stroke="#C8102E" stroke-width="4"/>'; },
  JPY: function() { return '<rect width="24" height="24" fill="#fff"/><circle cx="12" cy="12" r="6" fill="#BC002D"/>'; },
  AUD: function() { return '<rect width="24" height="24" fill="#00247D"/><rect width="12" height="12" fill="#012169"/><path d="M0 0L12 12M12 0L0 12" stroke="#fff" stroke-width="2"/><path d="M6 0V12M0 6H12" stroke="#fff" stroke-width="3.4"/><path d="M6 0V12M0 6H12" stroke="#C8102E" stroke-width="1.8"/>' + _star(6, 18, 3, "#fff") + _star(17, 6, 1.7, "#fff") + _star(19.5, 12, 1.7, "#fff") + _star(17, 19, 1.7, "#fff") + _star(14, 11, 1.4, "#fff"); },
  NZD: function() { return '<rect width="24" height="24" fill="#00247D"/><rect width="12" height="12" fill="#012169"/><path d="M0 0L12 12M12 0L0 12" stroke="#fff" stroke-width="2"/><path d="M6 0V12M0 6H12" stroke="#fff" stroke-width="3.4"/><path d="M6 0V12M0 6H12" stroke="#C8102E" stroke-width="1.8"/>' + _star(18, 6, 1.9, "#CC142B") + _star(15, 11, 1.9, "#CC142B") + _star(21, 12, 1.9, "#CC142B") + _star(18, 19, 1.9, "#CC142B"); },
  CAD: function() { return _V(["#D52B1E", "#fff", "#fff", "#D52B1E"]) + '<path d="M12 5l1.6 3.2 2.4-.9-.9 3.5 1.8 1.1-4.3 1.4.2 2.2h-1.6l.2-2.2L6.9 12l1.8-1.1-.9-3.5 2.4.9z" fill="#D52B1E"/>'; },
  CHF: function() { return '<rect width="24" height="24" fill="#DA291C"/><path d="M12 5v14M5 12h14" stroke="#fff" stroke-width="4.4"/>'; },
  HUF: function() { return _H(["#CE2939", "#fff", "#477050"]); },
  TRY: function() { return '<rect width="24" height="24" fill="#E30A17"/>' + _cres(10.5, 12, 5.2, "#fff", "#E30A17") + _star(15.2, 12, 2, "#fff"); },
  ARS: function() { return _H(["#74ACDF", "#fff", "#74ACDF"]) + '<circle cx="12" cy="12" r="2.4" fill="#F6B40E"/>'; },
  BRL: function() { return '<rect width="24" height="24" fill="#009C3B"/><path d="M12 3.5L21.5 12 12 20.5 2.5 12z" fill="#FFDF00"/><circle cx="12" cy="12" r="4.3" fill="#002776"/>'; },
  CLP: function() { return _H(["#fff", "#D52B1E"]) + '<rect width="12" height="12" fill="#0039A6"/>' + _star(6, 6, 3.2, "#fff"); },
  COP: function() { return '<rect width="24" height="12" fill="#FCD116"/><rect y="12" width="24" height="6" fill="#003893"/><rect y="18" width="24" height="6" fill="#CE1126"/>'; },
  DZD: function() { return _V(["#006233", "#fff"]) + _cres(11.5, 12, 4.6, "#D21034", "#fff") + _star(14, 12, 1.8, "#D21034"); },
  EGP: function() { return _H(["#CE1126", "#fff", "#000"]) + '<circle cx="12" cy="12" r="2.2" fill="#C09300"/>'; },
  IDR: function() { return _H(["#E70011", "#fff"]); },
  INR: function() { return _H(["#FF9933", "#fff", "#138808"]) + '<circle cx="12" cy="12" r="2.6" fill="none" stroke="#000080" stroke-width="1"/>'; },
  MXN: function() { return _V(["#006847", "#fff", "#CE1126"]) + '<circle cx="12" cy="12" r="2.2" fill="#8C6D1F"/>'; },
  MYR: function() { return _H(["#CC0001", "#fff", "#CC0001", "#fff", "#CC0001", "#fff", "#CC0001"]) + '<rect width="13" height="13" fill="#010066"/>' + _cres(5.6, 6.5, 3.6, "#FC0", "#010066") + _star(9.4, 6.5, 1.8, "#FC0"); },
  PHP: function() { return _H(["#0038A8", "#CE1126"]) + '<path d="M0 0L13 12 0 24z" fill="#fff"/><circle cx="4.2" cy="12" r="1.7" fill="#FCD116"/>'; },
  PKR: function() { return '<rect width="24" height="24" fill="#01411C"/><rect width="6" height="24" fill="#fff"/>' + _cres(15, 12, 5, "#fff", "#01411C") + _star(18.2, 9.8, 1.7, "#fff"); },
  SGD: function() { return _H(["#EF3340", "#fff"]) + _cres(7, 6, 3.6, "#fff", "#EF3340"); },
  THB: function() { return '<rect width="24" height="24" fill="#A51931"/><rect y="4" width="24" height="16" fill="#F4F5F8"/><rect y="8" width="24" height="8" fill="#2D2A4A"/>'; },
  VND: function() { return '<rect width="24" height="24" fill="#DA251D"/>' + _star(12, 12.6, 7, "#FFFF00"); },
  ZAR: function() { return '<rect width="24" height="12" fill="#E03C31"/><rect y="12" width="24" height="12" fill="#001489"/><path d="M0 0L11 12 0 24" fill="#007749"/><path d="M0 2.6L8.8 12 0 21.4M8.8 12H24" stroke="#fff" stroke-width="4.5" fill="none"/><path d="M0 2.6L8.8 12 0 21.4M8.8 12H24" stroke="#007749" stroke-width="2.4" fill="none"/>'; },
  UAH: function() { return _H(["#0057B8", "#FFD700"]); },
  NGN: function() { return _V(["#008751", "#fff", "#008751"]); },
  KES: function() { return _H(["#000", "#fff", "#BB0000", "#fff", "#006600"]).replace(/height="4\.\d+"/g, 'height="4.9"'); },
  RUB: function() { return _H(["#fff", "#0039A6", "#D52B1E"]); }
};
function _txt(t, size, fill, y, extra) { return '<text x="12" y="' + (y || 16) + '" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" font-weight="900" font-size="' + size + '" fill="' + fill + '" ' + (extra || "") + '>' + t + '</text>'; }
function _coin(bg, inner) { return '<rect width="24" height="24" fill="' + bg + '"/>' + inner; }
var BTC_I = function() { return _coin("#F7931A", _txt("B", 15, "#fff", 18) + '<path d="M10.3 3.6v3M13.1 3.6v3M10.3 17.6v3M13.1 17.6v3" stroke="#fff" stroke-width="1.5"/>'); };
var STK = [
  [/^bitcoin/i, BTC_I],
  [/^ethereum/i, function() { return _coin("#627EEA", '<path d="M12 4l5 8.2-5 3-5-3z" fill="#fff" fill-opacity=".95"/><path d="M12 16.4l5-3.1-5 7zM12 16.4l-5-3.1 5 7z" fill="#fff" fill-opacity=".7"/>'); }],
  [/^solana/i, function() { return _coin("#0f0b1a", '<defs><linearGradient id="slg" x1="0" x2="1"><stop offset="0" stop-color="#00FFA3"/><stop offset="1" stop-color="#DC1FFF"/></linearGradient></defs><path d="M7.4 6.4h11.2l-2.2 2.6H5.2zM5.2 10.7h11.2l2.4 2.6H7.4zM7.4 15h11.2l-2.2 2.6H5.2z" fill="url(#slg)"/>'); }],
  [/^bnb/i, function() { return _coin("#F3BA2F", '<path d="M12 5l2.2 2.2L12 9.4 9.8 7.2zM6.6 10.4l2.2 2.2-2.2 2.2-2.2-2.2zM17.4 10.4l2.2 2.2-2.2 2.2-2.2-2.2zM12 15.6l2.2 2.2L12 20l-2.2-2.2zM12 10.2l1.8 1.8-1.8 1.8-1.8-1.8z" fill="#fff"/>'); }],
  [/^dogecoin/i, function() { return _coin("#C2A633", _txt("D", 14, "#fff", 18) + '<path d="M6.5 12h8" stroke="#fff" stroke-width="1.8"/>'); }],
  [/^chainlink/i, function() { return _coin("#2A5ADA", '<path d="M12 4.8l6 3.5v7.4l-6 3.5-6-3.5V8.3z" fill="none" stroke="#fff" stroke-width="2"/><path d="M12 9.2l2.4 1.4v2.8L12 14.8l-2.4-1.4v-2.8z" fill="#fff"/>'); }],
  [/^cardano/i, function() { return _coin("#0033AD", '<circle cx="12" cy="12" r="1.7" fill="#fff"/><circle cx="12" cy="6" r="1" fill="#fff"/><circle cx="12" cy="18" r="1" fill="#fff"/><circle cx="6.8" cy="9" r="1" fill="#fff"/><circle cx="17.2" cy="9" r="1" fill="#fff"/><circle cx="6.8" cy="15" r="1" fill="#fff"/><circle cx="17.2" cy="15" r="1" fill="#fff"/>'); }],
  [/^polkadot/i, function() { return _coin("#E6007A", '<ellipse cx="12" cy="7" rx="2.3" ry="3.4" fill="#fff"/><ellipse cx="12" cy="17" rx="2.3" ry="3.4" fill="#fff"/><ellipse cx="7.2" cy="9.8" rx="2.3" ry="3.4" transform="rotate(60 7.2 9.8)" fill="#fff"/><ellipse cx="16.8" cy="14.2" rx="2.3" ry="3.4" transform="rotate(60 16.8 14.2)" fill="#fff"/><ellipse cx="16.8" cy="9.8" rx="2.3" ry="3.4" transform="rotate(-60 16.8 9.8)" fill="#fff"/><ellipse cx="7.2" cy="14.2" rx="2.3" ry="3.4" transform="rotate(-60 7.2 14.2)" fill="#fff"/>'); }],
  [/^polygon/i, function() { return _coin("#8247E5", '<path d="M8 8.2l4-2.3 4 2.3v4.6l-4 2.3-4-2.3z" fill="none" stroke="#fff" stroke-width="2"/><path d="M8 12.6v3l4 2.3 4-2.3" fill="none" stroke="#fff" stroke-width="2"/>'); }],
  [/^litecoin/i, function() { return _coin("#345D9D", _txt("Ł", 15, "#fff", 18)); }],
  [/^tron/i, function() { return _coin("#EF0027", '<path d="M5.5 6l13 2.4-6.6 11.2z" fill="none" stroke="#fff" stroke-width="1.8" stroke-linejoin="round"/><path d="M5.5 6l6.4 13.6M18.5 8.4L9.4 12" stroke="#fff" stroke-width="1.4"/>'); }],
  [/^avalanche/i, function() { return _coin("#E84142", '<path d="M12 5.5l7 12.5h-4.2L12 13l-2.8 5H5z" fill="#fff"/>'); }],
  [/^toncoin/i, function() { return _coin("#0098EA", '<path d="M6.6 7.6h10.8L12 17.6z" fill="none" stroke="#fff" stroke-width="2" stroke-linejoin="round"/><path d="M12 8v9" stroke="#fff" stroke-width="1.6"/>'); }],
  [/^dash/i, function() { return _coin("#008DE4", _txt("D", 14, "#fff", 18) + '<path d="M7 12h6" stroke="#fff" stroke-width="1.6"/>'); }],
  [/^(ripple|xrp)/i, function() { return _coin("#23292F", _txt("X", 14, "#fff", 18)); }],
  [/^(shiba|pepe|doge)/i, function() { return _coin("#E4571F", _txt("S", 14, "#fff", 18)); }],
  [/^(bch|bitcoin cash)/i, function() { return _coin("#8DC351", _txt("B", 15, "#fff", 18)); }],
  [/^gold/i, function() { return _coin("#E9B93A", '<circle cx="12" cy="12" r="9" fill="none" stroke="#fff3b0" stroke-width="1.2"/>' + _txt("Au", 9, "#5a3f00", 15.5)); }],
  [/^silver/i, function() { return _coin("#C3CAD4", _txt("Ag", 9, "#333", 15.5)); }],
  [/^platinum/i, function() { return _coin("#AAB4C0", _txt("Pt", 9, "#222", 15.5)); }],
  [/^palladium/i, function() { return _coin("#8E99A6", _txt("Pd", 9, "#111", 15.5)); }],
  [/^(brent|wti)/i, function() { return _coin("#16181d", '<path d="M12 4.6c3.4 4 5.4 6.6 5.4 9.4a5.4 5.4 0 0 1-10.8 0C6.6 11.2 8.6 8.6 12 4.6z" fill="#F5B301"/><path d="M9.6 14.4a2.6 2.6 0 0 0 2.4 2.4" stroke="#16181d" stroke-width="1.4" fill="none" stroke-linecap="round"/>'); }],
  [/^natural gas/i, function() { return _coin("#0B5FB5", '<path d="M12 4.5c1 3 4.4 4.6 4.4 8.6a4.4 4.4 0 0 1-8.8 0c0-1.9.9-3 1.8-4 .2 1 .7 1.6 1.4 1.8C10.6 8.2 11 6.4 12 4.5z" fill="#fff"/>'); }],
  [/^us100/i, function() { return _coin("#0D1B3D", _txt("100", 8.5, "#FFD54A", 15)); }],
  [/^sp500/i, function() { return _coin("#C8102E", _txt("S&amp;P", 7.4, "#fff", 13.4) + _txt("500", 6.4, "#fff", 19.4)); }],
  [/^aus ?200/i, function() { return FL.AUD(); }],
  [/^vix/i, function() { return _coin("#fff", _txt("VIX", 8.4, "#1B2A5E", 15)); }],
  [/^apple/i, function() { return _coin("#fff", '<path d="M12 8c.9-1.1 2.4-1.2 3.4-.9-.1 1-.5 2-1.4 2.6 2.1.6 3.3 2.5 3.3 4.5 0 2.8-1.8 5.2-3.3 5.2-.8 0-1.3-.5-2-.5s-1.3.5-2.1.5c-1.7 0-3.4-2.8-3.4-5.5 0-2.4 1.8-3.9 3.5-3.9.9 0 1.5.5 2 .5z" fill="#111"/><path d="M13.3 4.6c.1 1-.5 2-1.4 2.4-.2-1 .4-2.1 1.4-2.4z" fill="#111"/>'); }],
  [/^intel/i, function() { return _coin("#0071C5", _txt("intel", 6.8, "#fff", 14) + '<ellipse cx="12" cy="12" rx="9.6" ry="5.2" fill="none" stroke="#fff" stroke-width=".7"/>'); }],
  [/^johnson/i, function() { return _coin("#D51900", _txt("J&amp;J", 8, "#fff", 15, 'font-style="italic"')); }],
  [/^tesla/i, function() { return _coin("#E31937", '<path d="M4.6 8c3.2-2.2 11.6-2.2 14.8 0l-1.1 1.8c-2.4-1.4-4.4-1.5-6.3-1.5s-3.9.1-6.3 1.5z" fill="#fff"/><path d="M10.7 10.4h2.6v8.6l-1.3 1.6-1.3-1.6z" fill="#fff"/>'); }],
  [/^fedex/i, function() { return _coin("#4D148C", '<text x="12" y="14" text-anchor="middle" font-family="Arial" font-weight="900" font-size="7.4"><tspan fill="#fff">Fed</tspan><tspan fill="#FF6600">Ex</tspan></text>'); }],
  [/^cisco/i, function() { var s = ""; [3.6, 6, 8.6, 6, 3.6, 6, 8.6, 6, 3.6].forEach(function(h, i) { s += '<rect x="' + (3.2 + i * 2) + '" y="' + (12 - h / 2 - 1.5) + '" width="1.3" height="' + h + '" rx=".6" fill="#fff"/>'; }); return _coin("#049FD9", s + _txt("CISCO", 4.6, "#fff", 19.5)); }],
  [/^netflix/i, function() { return _coin("#141414", '<path d="M8 4.6h2.7l2.6 7.6V4.6H16v14.8h-2.7l-2.6-7.6v7.6H8z" fill="#E50914"/>'); }],
  [/^boeing/i, function() { return _coin("#1B3F94", '<ellipse cx="12" cy="12" rx="8.6" ry="4.6" transform="rotate(-28 12 12)" fill="none" stroke="#fff" stroke-width="1.4"/><path d="M6 14.5c3-1 7-3.5 11-7" stroke="#fff" stroke-width="1.6" fill="none"/>'); }],
  [/^citigroup/i, function() { return _coin("#fff", _txt("citi", 8.6, "#003B70", 15.6) + '<path d="M6 9Q12 4 18 9" stroke="#E31837" stroke-width="1.7" fill="none" stroke-linecap="round"/>'); }],
  [/^alibaba/i, function() { return _coin("#FF6A00", '<circle cx="12" cy="10" r="3.4" fill="none" stroke="#fff" stroke-width="1.7"/><path d="M6.6 14.6c2.4 3 8.4 3.6 10.8.2" stroke="#fff" stroke-width="1.9" fill="none" stroke-linecap="round"/>'); }],
  [/^amazon/i, function() { return _coin("#fff", _txt("a", 15, "#111", 14.6) + '<path d="M6 17Q12 20.6 18 17" stroke="#FF9900" stroke-width="1.7" fill="none" stroke-linecap="round"/><path d="M16.4 16.2l1.8.8-.4-1.9" stroke="#FF9900" stroke-width="1.3" fill="none"/>'); }],
  [/^american express/i, function() { return _coin("#016FD0", _txt("AMERICAN", 4.4, "#fff", 11.4) + _txt("EXPRESS", 4.4, "#fff", 16.6)); }],
  [/^facebook/i, function() { return _coin("#1877F2", '<path d="M13.4 20.5v-7.2h2.4l.4-2.8h-2.8V8.8c0-.8.3-1.4 1.5-1.4h1.5V4.9c-.3 0-1.2-.1-2.2-.1-2.2 0-3.6 1.300-3.6 3.700v2h-2.4v2.800h2.400v7.200z" fill="#fff"/>'); }],
  [/^visa/i, function() { return _coin("#fff", _txt("VISA", 8.4, "#1A1F71", 15, 'font-style="italic"')); }],
  [/^microsoft/i, function() { return _coin("#fff", '<rect x="5.6" y="5.6" width="6" height="6" fill="#F25022"/><rect x="12.4" y="5.6" width="6" height="6" fill="#7FBA00"/><rect x="5.6" y="12.4" width="6" height="6" fill="#00A4EF"/><rect x="12.4" y="12.4" width="6" height="6" fill="#FFB900"/>'); }],
  [/^exxon/i, function() { return _coin("#E31B23", _txt("Exxon", 6.2, "#fff", 11.2) + _txt("Mobil", 6.2, "#fff", 17.4)); }],
  [/^advanced micro/i, function() { return _coin("#fff", _txt("AMD", 7.4, "#111", 15) + '<path d="M14.6 5.4h4.6v4.6z" fill="#00A651"/>'); }],
  [/^pfizer/i, function() { return _coin("#0033A0", '<ellipse cx="12" cy="12" rx="8.2" ry="5.2" fill="none" stroke="#fff" stroke-width="1.8"/>' + _txt("Pfizer", 5.2, "#fff", 14)); }],
  [/^coinbase/i, function() { return _coin("#0052FF", '<path d="M17 14.6A5.2 5.2 0 1 1 17 9.4" stroke="#fff" stroke-width="2.8" fill="none" stroke-linecap="round"/>'); }],
  [/^gamestop/i, function() { return _coin("#fff", '<text x="12" y="16" text-anchor="middle" font-family="Arial" font-weight="900" font-size="11"><tspan fill="#111">G</tspan><tspan fill="#E4002B">S</tspan></text>'); }],
  [/^palantir/i, function() { return _coin("#111", '<circle cx="12" cy="11" r="5.6" fill="none" stroke="#fff" stroke-width="1.9"/><rect x="8.6" y="15" width="6.8" height="2.4" rx=".8" fill="#fff"/>'); }],
  [/^mcdonald/i, function() { return _coin("#DA291C", _txt("M", 17, "#FFC72C", 18.4)); }],
  [/^marathon/i, function() { return _coin("#1c1c1c", _txt("M", 16, "#00D2A0", 18)); }]
];
function _ico(code) {
  if (FL[code]) return FL[code]();
  if (code === "BTC") return BTC_I();
  if (code === "BCH") return _coin("#8DC351", _txt("B", 15, "#fff", 18));
  return _coin("#2a3142", _txt(code, 7, "#fff", 15));
}
function _wrapI(inner, size) { return '<i class="ai" style="width:' + size + 'px;height:' + size + 'px"><svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">' + inner + '</svg></i>'; }
function assetIcon(label, size) {
  size = size || 28;
  var k = assetKey(label).replace(/\s*OTC$/i, "").trim();
  var m = k.match(/^([A-Z]{3})\/([A-Z]{3})$/);
  if (m) return '<i class="ai-pair">' + _wrapI(_ico(m[1]), size) + _wrapI(_ico(m[2]), size) + '</i>';
  for (var i = 0; i < STK.length; i++) if (STK[i][0].test(k)) return _wrapI(STK[i][1](), size);
  var ini = k.replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase() || "?";
  return _wrapI(_coin("#2a3142", _txt(ini, 10, "#FFD700", 15.6)), size);
}
function assetBadge(label, size) {
  return '<i class="ai-row">' + assetIcon(label, size) + '<em class="ai-name">' + esc(assetName(label)) + '</em></i>';
}

function showClosed(a) {
  var re = forexReopenDate();
  byId("clAsset").innerHTML = assetBadge(a, 30);
  byId("clText").textContent = "Этот актив торгуется на реальном рынке, который сейчас закрыт (выходные). Получить сигнал по нему нельзя.";
  var dn = re.toLocaleDateString("ru-RU", {weekday: "long", day: "numeric", month: "long"});
  byId("clOpen").innerHTML = "🕒 Откроется: <b>" + esc(dn) + " в " + fmtT(re).slice(0, 5) + "</b> (ваше время)<br><span style=\"color:var(--muted);font-weight:700\">Пока доступны OTC-активы и криптовалюты</span>";
  byId("closedModal").classList.remove("hidden");
}
function closeClosedModal() { byId("closedModal").classList.add("hidden"); }

function updateSelectedAssetUI() { document.getElementById("currentAssetDisplay").innerHTML = assetBadge(currentAsset, 26); }
setTimeout(function() { try { updateSelectedAssetUI(); } catch(e) {} }, 0);
var openCatIdx = 0;
function openCatalogModal() {
  openCatIdx = -1;
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
    html += '<span>' + esc(cat) + '</span><span class="cat-meta">' + items.length + ' <i>' + (isOpen ? '▼' : '▶') + '</i></span></div>';
    if (!isOpen) return;
    items.forEach(function(a) {
      var isFav = favs.indexOf(a) >= 0;
      var isLive = liveList.indexOf(a) >= 0;
      html += '<div class="fav-item' + (assetClosed(a) ? ' closed' : '') + '" onclick="selectAsset(\'' + a.replace(/'/g,"\\'") + '\')">';
      html += '<span>' + assetBadge(a, 28) + '</span>' + (assetClosed(a) ? '<b class="lock">🔒 ЗАКРЫТ</b>' : '');
      html += '</div>';
    });
  });
  body.innerHTML = html || '<div style="text-align:center;padding:20px;color:var(--muted)">Ничего не найдено</div>';
}

function selectAsset(a) {
  closeCatalogModal();
  if (assetClosed(a)) { showClosed(a); return; }
  if (a === currentAsset) return;
  currentAsset = a;
  updateSelectedAssetUI();
  cancelSig();
}

function toggleFav(a) {}
function renderMainFavs() {}
function clearFavs() {}

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
      try {
        var shtml = localStorage.getItem("tmv_sig_html");
        if (shtml) {
          var sbx = document.getElementById("sigBox");
          sbx.innerHTML = shtml;
          if (!sbx.querySelector(".sig-x")) sbx.insertAdjacentHTML("afterbegin", '<button class="sig-x" type="button" onclick="cancelSig()" aria-label="Закрыть">✕</button>');
          sbx.className = "sig " + (localStorage.getItem("tmv_sig_cls") || "");
        }
      } catch(e) {}
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

function byId(id) { return document.getElementById(id); }

var A_STEPS = [
  "Подключение к потоку котировок",
  "Чтение свечей и объёма",
  "EMA · RSI · MACD · Stoch · ADX",
  "Поиск уровней и свечных паттернов",
  "Подтверждение со старшего таймфрейма"
];
var A_ICONS = ["📡", "📊", "🧮", "🎯", "🧠"];
var A_PHRASES = [
  "Подключаюсь к потоку котировок...",
  "Считываю свечи и объём...",
  "Сверяю EMA / RSI / MACD...",
  "Ищу уровни и свечные паттерны...",
  "Формирую итоговый сигнал..."
];

function runAnalyzer(callback) {
  var overlay = byId("analyzeBox");
  var txtEl = byId("analyzeTxt"), smileEl = byId("analyzeSmile");
  var fill = byId("aFill"), pct = byId("aPct"), steps = byId("aSteps"), bars = byId("aBars");
  if (!bars.children.length) {
    for (var b = 0; b < 18; b++) {
      var bi = document.createElement("i");
      bi.style.background = (b % 2 === 0) ? "#00E676" : "#FF5252";
      bi.style.animationDelay = (b * 0.07).toFixed(2) + "s";
      bi.style.animationDuration = (0.8 + (b % 5) * 0.12).toFixed(2) + "s";
      bars.appendChild(bi);
    }
  }
  var sh = "";
  A_STEPS.forEach(function(s, i) { sh += '<li id="aStep' + i + '"><b>•</b><span>' + s + '</span></li>'; });
  steps.innerHTML = sh;
  fill.style.width = "0%";
  pct.textContent = "0%";
  overlay.classList.remove("hidden", "out");
  var total = 3200, t0 = Date.now(), cur = -1;
  var iv = setInterval(function() {
    var p = Math.min(1, (Date.now() - t0) / total);
    var eased = 1 - Math.pow(1 - p, 2);
    var val = Math.round(eased * 100);
    fill.style.width = val + "%";
    pct.textContent = val + "%";
    var idx = Math.min(A_STEPS.length - 1, Math.floor(p * A_STEPS.length));
    if (idx !== cur) {
      if (cur >= 0) {
        var prev = byId("aStep" + cur);
        prev.className = "done";
        prev.firstChild.textContent = "✓";
      }
      var now = byId("aStep" + idx);
      now.className = "now";
      now.firstChild.textContent = "▸";
      txtEl.textContent = A_PHRASES[idx];
      smileEl.textContent = A_ICONS[idx];
      cur = idx;
    }
    if (p >= 1) {
      clearInterval(iv);
      for (var k = 0; k < A_STEPS.length; k++) {
        var li = byId("aStep" + k);
        li.className = "done";
        li.firstChild.textContent = "✓";
      }
      fill.style.width = "100%";
      pct.textContent = "100%";
      txtEl.textContent = "✅ Сигнал сформирован";
      smileEl.textContent = "✅";
      setTimeout(function() {
        overlay.classList.add("out");
        setTimeout(function() {
          overlay.classList.add("hidden");
          overlay.classList.remove("out");
          if (callback) callback();
        }, 280);
      }, 420);
    }
  }, 60);
}

/* ===== Описание сигнала ===== */
function parseReason(r) {
  var lines = String(r || "").split("\n").map(function(s) { return s.trim(); }).filter(Boolean);
  var o = {conf: 0, quality: "", regime: "", points: [], against: "", warns: []};
  lines.forEach(function(l, i) {
    var m;
    if (l.indexOf("Уверенность") === 0 || l.indexOf("Режим:") === 0) return;
    if (l.indexOf("Против сигнала:") === 0) { o.against = l.replace("Против сигнала:", "").trim(); return; }
    if (l.indexOf("⚠️") === 0) { o.warns.push(l.replace("⚠️", "").trim()); return; }
    o.points.push(l);
  });
  return o;
}

function descHTML(headline, p) {
  var h = '<div class="d-head">📋 Описание сигнала</div>';
  if (headline) h += '<div class="d-sum">' + esc(headline) + '</div>';
  if (p.points && p.points.length) {
    h += '<ul class="d-list">';
    p.points.forEach(function(t, i) {
      h += '<li style="animation-delay:' + (0.35 + i * 0.12).toFixed(2) + 's"><i>✔</i><span>' + esc(t) + '</span></li>';
    });
    h += '</ul>';
  }
  if (p.against) h += '<div class="d-note against">⚖️ Против сигнала: ' + esc(p.against) + '</div>';
  (p.warns || []).forEach(function(w) { h += '<div class="d-note warn">⚠️ ' + esc(w) + '</div>'; });
  return h;
}

function fmtT(d) { return pad2(d.getHours()) + ":" + pad2(d.getMinutes()) + ":" + pad2(d.getSeconds()); }

function getSig() {
  if (assetClosed(currentAsset)) { showClosed(currentAsset); return; }
  var tf = byId("tf").value;
  var expSec = parseInt(byId("exp").value);
  var asset = currentAsset;
  // запрос стартует сразу, пока идёт анимация анализа
  var req = fetch("/get_signal?asset=" + encodeURIComponent(asset) + "&timeframe=" + encodeURIComponent(tf))
    .then(function(r) { return r.ok ? r.json() : null; })
    .catch(function() { return null; });
  runAnalyzer(async function() {
    var data = await req;
    if (data && data.closed) {
      if (timers.sig) clearInterval(timers.sig);
      try { localStorage.removeItem("tmv_sig_end"); localStorage.removeItem("tmv_sig_html"); } catch(e) {}
      byId("sigBox").style.display = "none";
      byId("btnCancel").style.display = "none";
      byId("btnNew").style.display = "block";
      showClosed(asset);
      return;
    }
    var sigBox = byId("sigBox");
    sigBox.classList.remove("reveal", "is-call", "is-put");
    if (!data || !data.signal) {
      if (timers.sig) clearInterval(timers.sig);
      try { localStorage.removeItem("tmv_sig_end"); localStorage.removeItem("tmv_sig_html"); } catch(e) {}
      byId("sigMeta").textContent = asset + " · " + tf;
      var de = byId("sigDir");
      de.textContent = "⚠️ НЕТ СИГНАЛА";
      de.className = "sig-dir put";
      byId("cd").textContent = "00:00";
      byId("sigInd").innerHTML = "";
      byId("sigStrat").innerHTML = "<b>Не удалось получить данные рынка.</b> Нажмите «Новый сигнал» и попробуйте ещё раз.";
      sigBox.style.display = "block";
      byId("btnCancel").style.display = "none";
      byId("btnNew").style.display = "block";
      return;
    }
    var isCall = data.signal === "UP";
    var dir = isCall ? "⬆️ CALL (ВВЕРХ)" : "⬇️ PUT (ВНИЗ)";
    var p = parseReason(data.reason);
    var t0 = new Date(), t1 = new Date(t0.getTime() + expSec * 1000);
    byId("sigMeta").innerHTML = assetBadge(asset, 22) + " · " + esc(tf) + " · " + expSec + " сек<br>🕒 Вход: <b>" + fmtT(t0) + "</b> → Экспирация: <b>" + fmtT(t1) + "</b>";
    var dirEl = byId("sigDir");
    dirEl.textContent = dir;
    dirEl.className = "sig-dir " + (isCall ? "call" : "put");
    var ih = "";
    (data.indicators || []).forEach(function(x, i) {
      ih += '<span class="ind ' + esc(x[2]) + '" style="animation-delay:' + (0.45 + i * 0.08).toFixed(2) + 's">' + esc(x[0]) + ' ' + esc(x[1]) + '</span>';
    });
    byId("sigInd").innerHTML = ih;
    byId("sigStrat").innerHTML = descHTML(data.headline, p);
    sigBox.style.display = "block";
    byId("btnCancel").style.display = "block";
    byId("btnNew").style.display = "none";
    sigBox.classList.add(isCall ? "is-call" : "is-put");
    void sigBox.offsetWidth;
    sigBox.classList.add("reveal");
    try {
      localStorage.setItem("tmv_sig_asset", asset);
      localStorage.setItem("tmv_sig_tf", tf);
      localStorage.setItem("tmv_sig_html", sigBox.innerHTML);
      localStorage.setItem("tmv_sig_cls", isCall ? "is-call" : "is-put");
    } catch(e) {}
    incSig();
    saveToHistory({time: new Date().toLocaleString("ru-RU"), asset: asset, tf: tf, exp: expSec, dir: dir, reason: data.reason || ""});
    startTimer("sig", "cd", expSec, function() {
      byId("btnCancel").style.display = "none";
      byId("btnNew").style.display = "block";
    }, "tmv_sig_end");
  });
}

/* ===== ЧАТ С СИГНАЛАМИ ===== */
var chat = {exp: 2, entry: 0, end: 0, span: 60, lastId: 0, ids: {}, items: {}, nextScan: 0, interval: 180, scanning: false, offset: 0, unread: 0, first: true, ok: true, seen: 0, status: ""};
try { chat.seen = parseInt(localStorage.getItem("tmv_chat_seen") || "0"); } catch(e) {}

function srvNow() { return Date.now() / 1000 + chat.offset; }
function fmtMS(sec) { sec = Math.max(0, Math.ceil(sec)); return pad2(Math.floor(sec / 60)) + ":" + pad2(sec % 60); }
function chatOpen() { var t = byId("tabSigs"); return !!t && !t.classList.contains("hidden"); }

function updateBadge() {
  var b = byId("navBadge");
  if (!b) return;
  if (chat.unread > 0) { b.textContent = chat.unread > 9 ? "9+" : String(chat.unread); b.classList.remove("hidden"); }
  else b.classList.add("hidden");
}

function scrollChat(force) {
  var b = byId("chatBody");
  if (b && force) b.scrollTop = 0;
}

function reorderChat() {
  var now = srvNow(), arr = [], k;
  for (k in chat.items) arr.push(chat.items[k]);
  arr.forEach(function(it) { it.active = it.end > now; });
  arr.sort(function(a, b) {
    if (a.active !== b.active) return a.active ? -1 : 1;
    return b.ts - a.ts;
  });
  var list = byId("chatList"), changed = false, i;
  for (i = 0; i < arr.length; i++) { if (list.children[i] !== arr[i].el) { changed = true; break; } }
  if (changed) {
    var fr = document.createDocumentFragment();
    arr.forEach(function(it) { fr.appendChild(it.el); });
    list.appendChild(fr);
  }
  arr.forEach(function(it) { it.el.classList.toggle("expired", !it.active && it.end > 0); });
  while (arr.length > 80) {
    var old = arr.pop();
    if (old.el.parentNode) old.el.parentNode.removeChild(old.el);
    delete chat.items[old.id];
  }
}

function markSeen() {
  chat.unread = 0;
  if (chat.lastId) {
    chat.seen = chat.lastId;
    try { localStorage.setItem("tmv_chat_seen", String(chat.seen)); } catch(e) {}
  }
  updateBadge();
}

function openChat() {
  markSeen();
  scrollChat(true);
  setTimeout(function() { scrollChat(true); }, 60);
  pollChat();
}

function addChatMsg(m, fresh) {
  var list = byId("chatList");
  byId("chatEmpty").classList.add("hidden");
  var d = document.createElement("div");
  d.id = "m" + m.id;
  var end = 0;
  if (m.kind === "wait") {
    d.className = "msg wait" + (fresh ? " new" : "");
    d.innerHTML = '<div class="msg-ava">🤖</div><div class="msg-bub"><div class="msg-top"><b>😴 Скан рынка</b><span class="msg-time">' +
      fmtT(new Date(m.ts * 1000)) + '</span></div><div class="msg-sum">' + esc(m.text) + '</div></div>';
  } else {
    var up = m.dir === "CALL";
    var mins = Math.max(1, Math.round(m.exp / 60));
    var entryTs = m.entry || m.ts;
    var p = {points: m.points || [], against: m.against || "", warns: m.warns || []};
    var ih = "";
    (m.indicators || []).forEach(function(x, i) {
      ih += '<span class="ind ' + esc(x[2]) + '" style="animation:chipIn .4s ' + (i * 0.07).toFixed(2) + 's both">' + esc(x[0]) + ' ' + esc(x[1]) + '</span>';
    });
    var facts = "";
    (m.facts || []).forEach(function(f) { facts += '<span>' + esc(f) + '</span>'; });
    var hist = "";
    if (m.hist && m.hist[1]) {
      var hp = Math.round(m.hist[0] / m.hist[1] * 100);
      hist = '<div class="d-note hist">🔍 Проверка по графику: ' + m.hist[0] + ' из ' + m.hist[1] + ' (' + hp +
        '%) последних сигналов на ' + mins + ' мин по этому активу отработали бы в плюс</div>';
    }
    d.className = "msg " + (up ? "is-call" : "is-put") + (fresh ? " new" : "");
    d.innerHTML =
      '<div class="msg-ava">🤖</div><div class="msg-bub">' +
      '<div class="msg-top"><b class="ai-row">' + assetIcon(m.asset, 26) + '<em class="ai-name" style="color:var(--gold)">' + esc(assetName(m.asset)) + '</em></b><span class="msg-time">' + fmtT(new Date(m.ts * 1000)) + '</span></div>' +
      '<div class="msg-dir"><span class="pill ' + (up ? "call" : "put") + '">' + (up ? "⬆️ CALL · ВВЕРХ" : "⬇️ PUT · ВНИЗ") + '</span>' +
      '<span class="msg-conf">' + (m.conf || "") + '%</span></div>' +
      '<div class="msg-entry">🕒 ЗАХОДИТЬ В <b>' + fmtT(new Date(entryTs * 1000)) + '</b></div>' +
      '<div class="msg-meta"><span>⏳ Экспирация ' + mins + ' мин</span><span>📊 Интервал ' + mins + ' мин</span><span>💲 ' + esc(m.price) + '</span></div>' +
      descHTML(m.headline, p) +
      (m.asset_note ? '<div class="d-note asset">🎯 ' + esc(m.asset_note) + '</div>' : '') +
      (facts ? '<div class="d-facts">' + facts + '</div>' : '') +
      hist +
      (m.exp_note ? '<div class="d-note exp">⏱ ' + esc(m.exp_note) + '</div>' : '') +
      (ih ? '<div class="ind-row">' + ih + '</div>' : '') +
      '<div class="msg-live wait" data-ts="' + m.ts + '" data-entry="' + entryTs + '" data-end="' + (entryTs + m.exp) + '" data-exp="' + m.exp + '">⏱ вход через …<i class="lv-bar"></i></div>' +
      '<div class="msg-foot">🔎 Проверено активов: ' + (m.scanned || "—") + ' · источник: Binance</div></div>';
    end = entryTs + m.exp;
  }
  list.appendChild(d);
  chat.items[m.id] = {id: m.id, el: d, ts: m.ts, end: end};
  reorderChat();
  if (fresh) {
    if (chatOpen()) { chat.lastId = Math.max(chat.lastId, m.id); markSeen(); scrollChat(true); }
    else { chat.unread++; updateBadge(); }
    try { if (navigator.vibrate) navigator.vibrate(60); } catch(e) {}
  } else if (m.id > chat.seen) {
    chat.unread++;
  }
}

async function pollChat() {
  try {
    var ex = chat.exp;
    var r = await fetch("/bot_feed?after=" + chat.lastId + "&exp=" + ex);
    if (!r.ok) return;
    var d = await r.json();
    if (ex !== chat.exp) return;
    chat.offset = d.server_time - Date.now() / 1000;
    if (d.next_scan !== chat.nextScan) chat.span = Math.max(20, d.next_scan - d.server_time);
    chat.nextScan = d.next_scan;
    chat.entry = d.entry || 0;
    var mn = byId("mktNote");
    if (mn) {
      if (d.forex_closed) {
        var ro = new Date(d.reopen * 1000);
        mn.innerHTML = "🔒 Реальный рынок закрыт (выходные) — бот даёт сигналы по криптовалютам. Форекс откроется: <b>" +
          esc(ro.toLocaleDateString("ru-RU", {weekday: "long"})) + " в " + fmtT(ro).slice(0, 5) + "</b>";
        mn.classList.remove("hidden");
      } else mn.classList.add("hidden");
    }
    chat.end = d.end || 0;
    chat.interval = d.interval || 180;
    chat.scanning = !!d.scanning;
    chat.ok = d.ok !== false;
    var fresh = !chat.first;
    (d.messages || []).forEach(function(m) {
      if (chat.ids[m.id]) return;
      chat.ids[m.id] = 1;
      if (m.id > chat.lastId) chat.lastId = m.id;
      addChatMsg(m, fresh);
    });
    if (chat.first) {
      chat.first = false;
      if (chatOpen()) markSeen(); else updateBadge();
    }
  } catch(e) {}
}

function tickChat() {
  var now = srvNow();
  var rem = chat.nextScan ? chat.nextScan - now : chat.interval;
  byId("chatCd").textContent = fmtMS(rem);
  var arc = byId("scanArc");
  var prog = chat.nextScan ? 1 - Math.max(0, Math.min(1, rem / (chat.span || 60))) : 0;
  arc.style.strokeDashoffset = (106.8 * (1 - prog)).toFixed(1);
  var typing = chat.scanning || (chat.nextScan && rem <= 6 && rem > -20);
  var tEl = byId("chatTyping");
  var wasHidden = tEl.classList.contains("hidden");
  tEl.classList.toggle("hidden", !typing);
  if (typing && wasHidden) scrollChat(false);
  var st;
  if (chat.scanning) st = "🔎 сканирую рынок…";
  else if (chat.entry && now < chat.entry) st = "⏳ вход в " + fmtT(new Date(chat.entry * 1000));
  else if (chat.end && now < chat.end) st = "🔥 сделка идёт · новых сигналов нет";
  else if (chat.end && chat.nextScan && now < chat.nextScan) st = "⏸ пауза · скоро новый сигнал";
  else st = "онлайн";
  if (st !== chat.status) { chat.status = st; byId("chatStatus").innerHTML = '<i class="dot"></i> ' + st; }
  document.querySelectorAll(".msg-live").forEach(function(n) {
    var en = parseFloat(n.getAttribute("data-entry")), ed = parseFloat(n.getAttribute("data-end")), t0 = parseFloat(n.getAttribute("data-ts")) || (en - 60);
    var ex = parseFloat(n.getAttribute("data-exp")) || 120, bar = n.querySelector(".lv-bar"), txt, pr = 0;
    if (now < en) { n.className = "msg-live wait"; txt = "⏳ До входа " + fmtMS(en - now) + " · заходить в " + fmtT(new Date(en * 1000)); pr = (now - t0) / Math.max(1, en - t0); }
    else if (now < ed) { n.className = "msg-live run"; txt = "🔥 Сделка идёт · до экспирации " + fmtMS(ed - now); pr = 1 - (ed - now) / ex; }
    else { if (n.classList.contains("done")) return; n.className = "msg-live done"; txt = "✅ Экспирация завершена"; pr = 0; }
    n.innerHTML = txt + '<i class="lv-bar" style="width:' + Math.max(0, Math.min(1, pr)) * 100 + '%"></i>';
  });
  reorderChat();
}

function setChatExp(n) {
  if (chat.exp === n) return;
  chat.exp = n;
  try { localStorage.setItem("tmv_chat_exp", String(n)); } catch(e) {}
  markExpBtn();
  Object.keys(chat.items).forEach(function(k) { var it = chat.items[k]; if (it.el && it.el.parentNode) it.el.parentNode.removeChild(it.el); });
  chat.items = {}; chat.ids = {}; chat.lastId = 0; chat.first = true; chat.unread = 0;
  chat.nextScan = 0; chat.entry = 0; chat.end = 0; chat.status = "";
  byId("chatEmpty").classList.remove("hidden");
  try { reports.slice().reverse().forEach(function(r) { addReportToChat(r, false); }); } catch(e) {}
  updateBadge();
  pollChat();
}
function markExpBtn() {
  document.querySelectorAll("#expFilterRow .filter-btn").forEach(function(b) {
    b.classList.toggle("on", parseInt(b.getAttribute("data-exp")) === chat.exp);
  });
}
try { var _ce = parseInt(localStorage.getItem("tmv_chat_exp")); if ([1, 2, 3, 5].indexOf(_ce) >= 0) chat.exp = _ce; markExpBtn(); } catch(e) {}

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
  if (!signalHistory.length && !reports.length) {
    body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--muted);font-size:12px">История пуста. Сигналы появятся после расчёта.</div>';
  } else {
    var html = "";
    reports.forEach(function(r) {
      html += '<div class="hist-item"><div class="h-meta">' + esc(r.date || "") + ' · сессия · ' + durText(r.dur) + '</div>' +
        '<div class="h-dir" style="color:var(--gold)">' + esc(r.grade) + ' — ' + r.w + ' / ' + r.l + ' (' + r.wr + '%)</div>' +
        '<div class="h-reason">' + esc(r.paras[0] || "") + '</div></div>';
    });
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
  try { localStorage.removeItem("tmv_sig_html"); } catch(e) {}
  document.getElementById("sigBox").style.display = "none";
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
  var rb = document.getElementById("newsRefreshBtn");
  if (rb) { rb.disabled = true; rb.classList.add("spin"); }
  if (force && (!newsCache || !newsCache.length)) {
    list.innerHTML = '<div class="news-loading"><div class="news-loading-wheel"></div><div class="news-loading-text">Загружаю календарь новостей...</div></div>';
    upd.textContent = "⏳ Загрузка...";
  }
  try {
    var res = await fetch("/news" + (force ? "?refresh=1&t=" + Date.now() : ""));
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
  if (rb) { rb.disabled = false; rb.classList.remove("spin"); }
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
  var hs = document.getElementById("headerSigCount");
  if (hs) hs.textContent = "Сигналов: " + sigCount;
  renderWinrate();
}

function getTot() {
  var w = 0, l = 0;
  try { w = parseInt(localStorage.getItem("tmv_wins") || "0") || 0; l = parseInt(localStorage.getItem("tmv_losses") || "0") || 0; } catch(e) {}
  return {w: w, l: l};
}
function setTot(w, l) {
  try { localStorage.setItem("tmv_wins", String(w)); localStorage.setItem("tmv_losses", String(l)); } catch(e) {}
}

var LEVELS = [[0, "Новичок"], [10, "Ученик"], [30, "Трейдер"], [75, "Профи"], [150, "Мастер"], [300, "Легенда"]];
function renderLevel() {
  var t = getTot(), n = t.w + t.l, i = 0;
  for (var k = 0; k < LEVELS.length; k++) if (n >= LEVELS[k][0]) i = k;
  var cur = LEVELS[i], nx = LEVELS[i + 1], pct = 100;
  if (nx) pct = Math.round((n - cur[0]) / (nx[0] - cur[0]) * 100);
  byId("pgLvlName").textContent = "Ур. " + (i + 1) + " · " + cur[1];
  byId("pgLvlPct").textContent = pct + "%";
  byId("pgLvlFill").style.width = pct + "%";
  byId("pgLvlNext").textContent = nx ? "До уровня «" + nx[1] + "»: ещё " + (nx[0] - n) + " " + plural(nx[0] - n, "сделка", "сделки", "сделок") : "Максимальный уровень достигнут 🏆";
  var wr = n ? Math.round(t.w / n * 100) : 0, gp = Math.min(100, Math.round(wr / 70 * 100));
  byId("pgGoalPct").textContent = wr + "% / 70%";
  byId("pgGoalFill").style.width = gp + "%";
  byId("pgGoalTxt").textContent = !n ? "Начни сессию — шкала заполнится" : (wr >= 70 ? "Цель достигнута 🔥" : "До цели не хватает " + (70 - wr) + " п.п.");
  byId("stSig").textContent = sigCount;
  byId("stSess").textContent = reports.length;
}

function renderWinrate() {
  try { renderLevel(); } catch(e) {}
  var t = getTot(), n = t.w + t.l;
  var big = byId("wrBig");
  if (!big) return;
  if (!n) {
    big.textContent = "—";
    byId("wrG").style.width = "0%"; byId("wrR").style.width = "0%";
    byId("wrWinPct").textContent = "0%"; byId("wrLossPct").textContent = "0%";
    byId("wrTotal").textContent = "Сделок пока нет — начни сессию";
    return;
  }
  var wr = Math.round(t.w / n * 100);
  big.textContent = wr + "%";
  byId("wrG").style.width = (t.w / n * 100) + "%";
  byId("wrR").style.width = (t.l / n * 100) + "%";
  byId("wrWinPct").textContent = wr + "% (" + t.w + ")";
  byId("wrLossPct").textContent = (100 - wr) + "% (" + t.l + ")";
  byId("wrTotal").textContent = "Всего сделок: " + n + " · сессий: " + reports.length;
}

function loadProfile() {
  try {
    document.getElementById("profPocketId").value = localStorage.getItem("tmv_pocketId") || "";
    var photo = localStorage.getItem("tmv_profPhoto");
    if (photo) {
      document.getElementById("profilePhotoImg").src = photo;
      document.getElementById("profilePhotoImg").classList.remove("hidden");
      document.getElementById("profilePhotoIcon").classList.add("hidden");
    }
  } catch(e) {}
  updateProfileStats();
  updateSessBtn();
}

function saveProfileSettings() {
  try { localStorage.setItem("tmv_pocketId", document.getElementById("profPocketId").value); } catch(e) {}
}

function resetProfile() {
  try {
    ["tmv_pocketId","tmv_favStrat","tmv_profPhoto","tmv_sigs","tmv_history","tmv_sig_html","tmv_chat_seen",
     "tmv_wins","tmv_losses","tmv_reports","tmv_session"].forEach(function(k){ localStorage.removeItem(k); });
  } catch(e) {}
  sigCount = 0; signalHistory = []; reports = []; sess = null;
  Object.keys(chat.items).forEach(function(id) {
    if (String(id).indexOf("r") === 0) {
      var it = chat.items[id];
      if (it.el.parentNode) it.el.parentNode.removeChild(it.el);
      delete chat.items[id];
    }
  });
  document.getElementById("profPocketId").value = "";
  document.getElementById("profilePhotoImg").classList.add("hidden");
  document.getElementById("profilePhotoImg").removeAttribute("src");
  document.getElementById("profilePhotoIcon").classList.remove("hidden");
  updateProfileStats();
  updateSessBtn();
}

/* ===== ТОРГОВАЯ СЕССИЯ ===== */
var reports = [], sess = null, lastReportId = null;
try {
  reports = JSON.parse(localStorage.getItem("tmv_reports") || "[]");
  sess = JSON.parse(localStorage.getItem("tmv_session") || "null");
} catch(e) { reports = []; sess = null; }
if (!Array.isArray(reports)) reports = [];
if (sess && !Array.isArray(sess.ev)) sess = null;

function saveSess() {
  try { if (sess) localStorage.setItem("tmv_session", JSON.stringify(sess)); else localStorage.removeItem("tmv_session"); } catch(e) {}
}
function fmtHMS(sec) { sec = Math.max(0, Math.floor(sec)); return pad2(Math.floor(sec / 3600)) + ":" + pad2(Math.floor(sec % 3600 / 60)) + ":" + pad2(sec % 60); }
function sessCounts() {
  var w = 0, l = 0;
  if (sess) sess.ev.forEach(function(e) { if (e === "w") w++; else l++; });
  return {w: w, l: l};
}
function updateSessBtn() {
  var b = byId("sessBtn");
  if (!b) return;
  b.textContent = sess ? "▶ Продолжить сессию · " + fmtHMS((Date.now() - sess.start) / 1000) : "▶ Начать сессию";
}
function openSession() {
  if (!sess) { sess = {start: Date.now(), ev: []}; saveSess(); }
  byId("sessOverlay").classList.remove("hidden");
  renderSession();
  tickSess();
}
function minimizeSession() { byId("sessOverlay").classList.add("hidden"); updateSessBtn(); }
function tickSess() {
  if (!sess) return;
  var c = byId("sessClock");
  if (c) c.textContent = "⏱ " + fmtHMS((Date.now() - sess.start) / 1000);
  updateSessBtn();
}
function sessAdj(t, d) {
  if (!sess) return;
  if (d > 0) sess.ev.push(t);
  else { var i = sess.ev.lastIndexOf(t); if (i < 0) return; sess.ev.splice(i, 1); }
  saveSess();
  renderSession(t);
  try { if (navigator.vibrate) navigator.vibrate(d > 0 ? 25 : 12); } catch(e) {}
}
function renderSession(popKey) {
  if (!sess) return;
  var c = sessCounts(), n = c.w + c.l;
  byId("sessW").textContent = c.w;
  byId("sessL").textContent = c.l;
  if (popKey) {
    var el = byId(popKey === "w" ? "sessW" : "sessL");
    el.classList.remove("pop"); void el.offsetWidth; el.classList.add("pop");
  }
  byId("sessTotal").textContent = n;
  var wr = n ? Math.round(c.w / n * 100) : 0;
  byId("sessWr").textContent = n ? wr + "%" : "—";
  var arc = byId("sessArc");
  arc.style.strokeDashoffset = (326.7 * (1 - (n ? c.w / n : 0))).toFixed(1);
  arc.style.stroke = !n ? "#FFD700" : (wr >= 60 ? "#00E676" : (wr >= 45 ? "#FFD700" : "#FF5252"));
  var dots = "", last = sess.ev.slice(-30);
  last.forEach(function(e) { dots += '<i class="' + e + '"></i>'; });
  byId("sessDots").innerHTML = dots;
  var st = "—";
  if (n) {
    var lastE = sess.ev[n - 1], k = 0;
    for (var i = n - 1; i >= 0 && sess.ev[i] === lastE; i--) k++;
    st = (lastE === "w" ? "🔥 " + k + " плюс" : "🧊 " + k + " минус") + (k > 1 ? "а подряд" : "");
    if (lastE === "w" && k > 1) st = "🔥 " + k + " плюса подряд";
    if (lastE === "l" && k > 1) st = "🧊 " + k + " минуса подряд";
  }
  byId("sessStreak").textContent = st;
}

function plural(n, a, b, c) {
  var m = n % 100, d = n % 10;
  if (m >= 11 && m <= 14) return c;
  if (d === 1) return a;
  if (d >= 2 && d <= 4) return b;
  return c;
}
function durText(sec) {
  var h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60);
  if (h) return h + " ч " + m + " мин";
  if (m) return m + " мин";
  return sec + " сек";
}

function buildReport(s) {
  var ev = s.ev, w = 0, l = 0, mw = 0, ml = 0, cw = 0, cl = 0;
  ev.forEach(function(e) {
    if (e === "w") { w++; cw++; cl = 0; if (cw > mw) mw = cw; }
    else { l++; cl++; cw = 0; if (cl > ml) ml = cl; }
  });
  var n = w + l, wr = Math.round(w / n * 100);
  var dur = Math.max(1, Math.round((Date.now() - s.start) / 1000));
  var tot = getTot(), prevN = tot.w + tot.l, prevWr = prevN ? Math.round(tot.w / prevN * 100) : null;
  var grade;
  if (wr >= 80) grade = "🏆 Блестящая сессия";
  else if (wr >= 65) grade = "🔥 Сильная сессия";
  else if (wr >= 55) grade = "✅ Уверенная сессия";
  else if (wr >= 45) grade = "⚖️ Ровная сессия — около нуля";
  else if (wr >= 30) grade = "⚠️ Просадочная сессия";
  else grade = "🛑 Тяжёлая сессия";
  var paras = [];
  paras.push("За " + durText(dur) + " ты провёл " + n + " " + plural(n, "сделку", "сделки", "сделок") + ": " + w + " в плюс и " + l +
    " в минус. Винрейт сессии — " + wr + "%.");
  var pace = n / (dur / 3600);
  if (dur < 300) paras.push("Сессия короткая — выводы предварительные, набери больше сделок для точной картины.");
  else if (pace > 30) paras.push("Темп ~" + Math.round(pace) + " сделок в час — очень частый: на таком ритме растёт доля импульсивных входов.");
  else if (pace < 6) paras.push("Темп ~" + Math.max(1, Math.round(pace)) + " " + plural(Math.max(1, Math.round(pace)), "сделка", "сделки", "сделок") + " в час — спокойный и выборочный, это про качество входов.");
  else paras.push("Темп ~" + Math.round(pace) + " сделок в час — рабочий ритм без перегруза.");
  var sp = "Лучшая серия плюсов — " + mw + ", самая длинная серия минусов — " + ml + ".";
  if (ml >= 3) sp += " После трёх минусов подряд пауза 5–10 минут обычно сохраняет депозит.";
  else if (mw >= 4) sp += " Серия плюсов — повод не повышать ставку, а держать план.";
  paras.push(sp);
  if (n >= 6) {
    var half = Math.floor(n / 2), f1 = ev.slice(0, half), f2 = ev.slice(half);
    var r1 = Math.round(f1.filter(function(e) { return e === "w"; }).length / f1.length * 100);
    var r2 = Math.round(f2.filter(function(e) { return e === "w"; }).length / f2.length * 100);
    if (r2 - r1 >= 15) paras.push("Динамика: ко второй половине сессии ты разогнался — " + r1 + "% → " + r2 + "%. Ритм пойман.");
    else if (r1 - r2 >= 15) paras.push("Динамика: во второй половине результат просел — " + r1 + "% → " + r2 + "%. Похоже на усталость или попытку отыграться; лучше завершать раньше.");
    else paras.push("Динамика: результат держался ровно всю сессию — " + r1 + "% → " + r2 + "%.");
  }
  if (prevN >= 10) {
    var diff = wr - prevWr;
    if (Math.abs(diff) < 3) paras.push("Это на уровне твоего общего винрейта (" + prevWr + "%).");
    else paras.push("Это " + (diff > 0 ? "выше" : "ниже") + " твоего общего винрейта (" + prevWr + "%) на " + Math.abs(diff) + " п.п.");
  }
  var tip;
  if (wr >= 65) tip = "Что дальше: зафиксируй, какие активы и время суток дали плюс, и повтори в следующей сессии. Ставку не повышай.";
  else if (wr >= 50) tip = "Что дальше: сократи число сделок и бери только сигналы с высокой уверенностью и подтверждением на графике.";
  else tip = "Что дальше: сделай паузу, разбери убыточные входы и вернись с меньшей ставкой. Лимит потерь на сессию — обязателен.";
  return {id: Date.now(), ts: srvNow(), start: s.start, dur: dur, w: w, l: l, n: n, wr: wr, mw: mw, ml: ml,
          grade: grade, paras: paras, tip: tip, date: new Date().toLocaleString("ru-RU")};
}

function repHTML(r) {
  var col = r.wr >= 55 ? "#00E676" : (r.wr >= 45 ? "#FFD700" : "#FF5252");
  var off = (213.6 * (1 - r.wr / 100)).toFixed(1);
  var h = '<div class="rep-card"><div class="rep-hero"><div class="rep-ring" style="color:' + col + '"><svg viewBox="0 0 80 80"><circle class="bg" cx="40" cy="40" r="34"></circle><circle class="fg" cx="40" cy="40" r="34" stroke="' + col + '" style="stroke-dashoffset:' + off + '"></circle></svg><span style="color:' + col + '">' + r.wr + '%<small>ВИНРЕЙТ</small></span></div>' +
    '<div class="rh"><div class="rep-title g-text">' + esc(r.grade) + '</div><div class="rep-date">🏁 Итог сессии · ' + esc(r.date) + '</div></div></div>';
  h += '<div class="rep-stats4"><div><b style="color:var(--green)">' + r.w + '</b><span>ПЛЮС</span></div><div><b style="color:var(--red)">' + r.l +
       '</b><span>МИНУС</span></div><div><b style="color:var(--gold)">' + r.n + '</b><span>СДЕЛОК</span></div><div><b>' + durText(r.dur) + '</b><span>ВРЕМЯ</span></div></div>';
  h += '<div class="rep-bar"><i class="g" style="width:' + r.wr + '%"></i><i class="r" style="width:' + (100 - r.wr) + '%"></i></div>';
  h += '<div class="rep-leg"><span style="color:var(--green)">✅ ' + r.wr + '%</span><span style="color:var(--muted)">🔥 серия +' + r.mw + ' · 🧊 серия −' + r.ml + '</span><span style="color:var(--red)">❌ ' + (100 - r.wr) + '%</span></div>';
  h += '<div class="rep-sec">📋 РАЗБОР СЕССИИ</div>';
  r.paras.forEach(function(p) { h += '<div class="rep-p">' + esc(p) + '</div>'; });
  h += '<div class="rep-tip">💡 ' + esc(r.tip) + '</div></div>';
  return h;
}

function addReportToChat(r, fresh) {
  var id = "r" + r.id;
  if (chat.items[id]) return;
  byId("chatEmpty").classList.add("hidden");
  var d = document.createElement("div");
  d.id = "m" + id;
  d.className = "msg report" + (fresh ? " new" : "");
  d.innerHTML = '<div class="msg-ava">🏁</div><div class="msg-bub" style="padding:0;border:none;background:none">' + repHTML(r) + '</div>';
  byId("chatList").appendChild(d);
  chat.items[id] = {id: id, el: d, ts: r.ts, end: 0};
  reorderChat();
}

function finishSession() {
  if (!sess) return;
  if (!sess.ev.length) {
    if (confirm("Сделок в сессии нет. Закрыть её без отчёта?")) {
      sess = null; saveSess(); byId("sessOverlay").classList.add("hidden"); updateSessBtn();
    }
    return;
  }
  if (!confirm("Завершить сессию и получить итог?")) return;
  var r = buildReport(sess), t = getTot();
  setTot(t.w + r.w, t.l + r.l);
  reports.unshift(r);
  if (reports.length > 50) reports = reports.slice(0, 50);
  try { localStorage.setItem("tmv_reports", JSON.stringify(reports)); } catch(e) {}
  sess = null; saveSess();
  byId("sessOverlay").classList.add("hidden");
  addReportToChat(r, true);
  renderWinrate(); updateSessBtn();
  lastReportId = r.id;
  byId("repBody").innerHTML = repHTML(r);
  byId("repOverlay").classList.remove("hidden");
}
function closeReport() { byId("repOverlay").classList.add("hidden"); }
function openReportInChat() {
  closeReport();
  showTab("tabSigs", document.querySelectorAll(".nav")[1]);
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
  pollChat();
  setInterval(pollChat, 4000);
  tickChat();
  setInterval(tickChat, 1000);
  reports.slice().reverse().forEach(function(r) { addReportToChat(r, false); });
  if (sess) updateSessBtn();
  setInterval(tickSess, 1000);
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


HTF_MAP = {"S5": "S30", "S15": "M1", "S30": "M2", "M1": "M5", "M2": "M10", "M3": "M15", "M4": "M15", "M5": "M15"}


@app.get("/get_signal")
async def get_signal(asset: str = "🇪🇺🇺🇸 EUR/USD OTC", timeframe: str = "M1"):
    if not _asset_is_24x7(asset) and _forex_closed():
        return {"closed": True, "asset": asset, "reopen": _forex_reopen_ts(),
                "message": "Рынок закрыт — сигнал по этому активу недоступен."}
    if timeframe not in TF_SEC:
        timeframe = "M1"
    try:
        asyncio.create_task(send_tg_signal_alert(asset, timeframe))
    except Exception:
        pass
    try:
        candles, source = await get_candles(asset, timeframe, 120)
        if not candles or len(candles) < 10:
            raise RuntimeError("no candles")
    except Exception as e:
        logging.warning(f"get_signal candles: {_redact(e)}")
        candles, source = sim_candles(asset, timeframe, 120), "otc"
    is_pro = bool(otchards_symbol_for(asset))
    result = None
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
    if result is None:
        try:
            signal, reason, conf = deep_analysis(candles[-50:])
        except Exception as e:
            logging.warning(f"deep_analysis error: {e}")
            up = candles[-1][3] >= candles[-1][0]
            signal, reason, conf = ("UP" if up else "DOWN"), "Импульс последних свечей подтверждает направление.", 60
        result = {"signal": signal, "confidence": conf, "reason": reason, "indicators": [],
                  "quality": _quality_for(conf)}
    return {"signal": result["signal"], "reason": result["reason"], "confidence": result["confidence"],
            "headline": _signal_headline(result), "indicators": result.get("indicators", []),
            "quality": result.get("quality", ""), "source": source, "is_pro": is_pro,
            "price": candles[-1][3], "server_time": time.time()}


# ===== БОТ С СИГНАЛАМИ: все активы без OTC, график и свечи с Binance =====
def _asset_is_24x7(label):
    """OTC и криптовалюты торгуются круглосуточно; реальные форекс/металлы/индексы — по расписанию."""
    if "OTC" in str(label).upper():
        return True
    key = re.sub(r"\s+", " ", re.sub(r"[^\x00-\x7F]+", "", str(label))).strip()
    return key.split("/")[0] in ("BTC", "BCH") or key in ("Bitcoin", "Ethereum", "Solana", "Dash", "Chainlink")


def _forex_closed(now=None):
    """Реальный форекс закрыт с пятницы 22:00 до воскресенья 22:00 UTC."""
    t = time.gmtime(now or time.time())
    h = t.tm_hour + t.tm_min / 60.0
    return t.tm_wday == 5 or (t.tm_wday == 4 and h >= 22) or (t.tm_wday == 6 and h < 22)


def _forex_reopen_ts(now=None):
    now = now or time.time()
    t = time.gmtime(now)
    days = (6 - t.tm_wday) % 7                      # до ближайшего воскресенья
    base = int(now // 86400) * 86400 + days * 86400 + 22 * 3600
    return base if base > now else base + 7 * 86400


BOT_EXP_CHOICES = (1, 2, 3, 5)                                    # фильтр экспирации (минуты)
BOT_DEFAULT_EXP = 2                                               # по умолчанию 2 минуты
BOT_LEAD = 40                                                     # минимум секунд до входа в сделку
BOT_PAUSE = 60                                                    # пауза после экспирации до нового сигнала
BOT_MIN_CONF = int(os.environ.get("BOT_MIN_CONF", "70"))          # минимум уверенности анализа
BOT_MIN_HIST = float(os.environ.get("BOT_MIN_HIST", "0.58"))      # минимум доли сработавших сигналов на истории
BOT_BACKTEST = 32                                                 # сколько прошлых свечей проверяем
_BOT = {}             # экспирация (мин) -> {"feed","state","task","last_poll"}
_BOT_DEAD = {}        # актив -> до какого времени не опрашиваем (нет данных на Binance)
_BOT_SEQ = [0]

_FLAG = {"EUR": "🇪🇺", "USD": "🇺🇸", "GBP": "🇬🇧", "AUD": "🇦🇺", "NZD": "🇳🇿", "CAD": "🇨🇦", "CHF": "🇨🇭", "JPY": "🇯🇵"}
_ICON = {"Bitcoin": "🪙", "Ethereum": "💠", "Solana": "☀️", "BNB": "🔶", "Dogecoin": "🐕", "Chainlink": "🔗",
         "Cardano": "🔷", "Polkadot": "⚪", "Polygon": "🟣", "Litecoin": "⚡", "TRON": "🔺", "Avalanche": "🏔️",
         "Toncoin": "💎", "Dash": "💨", "Gold": "🥇", "Ripple": "💧", "Shiba Inu": "🐶", "Pepe": "🐸",
         "NEAR": "🌐", "Cosmos": "⚛️", "Uniswap": "🦄", "Aptos": "🅰️", "Arbitrum": "🔵", "Optimism": "🔴",
         "Sui": "🌊", "Injective": "💉", "Filecoin": "📁", "Ethereum Classic": "🟢", "Bitcoin Cash": "💵",
         "Stellar": "🌟", "Hedera": "♦️"}
_BOT_INVERT = {"USD/JPY", "USD/CAD", "USD/CHF"}   # на Binance котируются наоборот (JPYUSDT и т.д.) — переворачиваем


def _bot_label(name):
    if "/" in name:
        a, b = name.split("/")
        return f"{_FLAG.get(a, '')}{_FLAG.get(b, '')} {name}"
    return f"{_ICON.get(name, '🪙')} {name}"


def _bot_kind(name):
    return "forex" if "/" in name else ("metal" if name == "Gold" else "crypto")


def _bot_catalog_assets():
    """Только реальные активы из каталога поиска — без OTC, у которых есть котировки Binance."""
    out, seen = [], set()
    for lst in (_BEST, _CURRENCY, _CRYPTO, _COMMODITIES, _STOCKS, _INDICES):
        for a in lst:
            if "OTC" in a.upper():
                continue
            key = re.sub(r"[^\x00-\x7F]+", "", a)
            key = re.sub(r"\s+", " ", key).strip()
            if key in BINANCE_MAPPING and key not in seen:
                seen.add(key)
                out.append((a, key, _bot_kind(key)))
    return out


BOT_ASSETS = _bot_catalog_assets()


def _fmt_price(p):
    return f"{p:.2f}" if p > 1000 else (f"{p:.3f}" if p > 100 else (f"{p:.4f}" if p > 10 else (f"{p:.5f}" if p > 0.1 else f"{p:.7f}")))


def _quality_for(conf):
    return "СИЛЬНЫЙ" if conf >= 80 else ("СРЕДНИЙ" if conf >= 68 else "СЛАБЫЙ — лучше пропустить")


# --- особенности каждого актива: описание подбирается под конкретный актив ---
ASSET_NOTES = {
    "Bitcoin": ["BTC — флагман рынка: его ход задаёт тон всем альтам, ликвидность максимальная, ложных проколов меньше.",
                "У биткоина самый глубокий стакан, поэтому сетапы на нём чище, чем на большинстве монет."],
    "Ethereum": ["ETH обычно идёт в связке с BTC, но с более резкими импульсами — бета выше.",
                 "Эфир чувствителен к движению биткоина: если BTC тормозит, импульс ETH может быстро затухнуть."],
    "Solana": ["SOL — быстрая и волатильная монета: импульсы короткие и резкие, входить нужно без опоздания.",
               "Солана часто даёт длинные свечи с выносом ликвидности — поэтому важна подтверждённость тренда."],
    "BNB": ["BNB двигается спокойнее остальных: тренд держит дольше, откаты неглубокие.",
            "Монета биржи Binance — ход плавный, ложных рывков заметно меньше, чем у мем-активов."],
    "Dogecoin": ["DOGE — мем-актив: двигается на эмоциях и объёмах, тени длинные, поэтому нужны подтверждения.",
                 "Догкоин легко разгоняется от новостей и твитов, но так же быстро сдувается — сделка короткая."],
    "Chainlink": ["LINK — монета среднего размера: импульсы чёткие, а уровни отрабатываются аккуратно.",
                  "Chainlink часто уважает технические уровни, поэтому отскоки от границ диапазона надёжнее."],
    "Cardano": ["ADA ходит плавно и часто стоит во флэте, поэтому берём только выраженный импульс.",
                "Cardano редко делает резкие рывки — тренд развивается постепенно."],
    "Polkadot": ["DOT — монета среднего класса: движения чистые, но бывают затяжные боковики.",
                 "Polkadot реагирует на общий настрой рынка альткоинов — смотрим, чтобы он совпадал с сигналом."],
    "Polygon": ["POL/MATIC — активно торгуемая монета: хорошая ликвидность и чёткие свечные модели.",
                "Polygon часто ходит рывками после накопления — сигнал строится на выходе из сжатия."],
    "Litecoin": ["LTC — «старичок» рынка: часто стоит в боковике и просыпается резко, берём только чёткие входы.",
                 "Litecoin двигается спокойно и предсказуемо, импульсы редкие, но обычно доходят до цели."],
    "TRON": ["TRX — одна из самых «тихих» монет: диапазон узкий, шум небольшой, ценятся чёткие пробои.",
             "TRON редко резко дёргается — поэтому сигнал на ней опирается на аккуратный тренд."],
    "Avalanche": ["AVAX — высоковолатильная монета: тренды сильные, но откаты глубокие.",
                  "Avalanche хорошо следует за общим трендом альтов и даёт протяжённые движения."],
    "Toncoin": ["TON — монета с мощным ретейл-спросом: импульсы быстрые, а ликвидность в пиках может тонеть.",
                "Toncoin реагирует на новости экосистемы Telegram — движения бывают резкими."],
    "Dash": ["DASH — менее ликвидный актив: ход рваный, поэтому важна подтверждённость сразу несколькими индикаторами.",
             "Dash часто стоит и срывается резко — сигнал учитывает именно такие моменты."],
    "Gold": ["Золото — защитный актив: реагирует на доллар и доходности облигаций, тренд держит плавно.",
             "Gold движется взвешенно, ложные проколы редки, а откаты обычно короткие."],
    "Ripple": ["XRP — новостной актив: резкие движения на заявлениях и судебных новостях, между ними — флэт.",
               "Ripple часто даёт мощные короткие импульсы после долгого сжатия цены."],
    "Shiba Inu": ["SHIB — мем-монета: огромный объём при мелкой цене, движения эмоциональные и быстрые.",
                  "Shiba Inu живёт на хайпе — поэтому сигнал требует подтверждения объёмом и трендом."],
    "Pepe": ["PEPE — мем-актив с экстремальной волатильностью: свечи резкие, тени длинные.",
             "Pepe двигается волнами: пампы и откаты быстрые, вход должен быть точным."],
    "NEAR": ["NEAR — актив сектора ИИ и инфраструктуры: хорошо разгоняется в трендовые дни.",
             "Near часто идёт импульсами с чёткими откатами, на которых строятся входы."],
    "Cosmos": ["ATOM — монета среднего класса, ход ровный; реакции на уровни аккуратные.",
               "Cosmos любит боковик — сигналом считаем только выход из него."],
    "Uniswap": ["UNI — токен DeFi: ходит от общего аппетита к риску, импульсы средней силы.",
                "Uniswap держит уровни и даёт читаемые свечные модели."],
    "Aptos": ["APT — молодой L1-актив: тренды сильные, но резкие развороты случаются.",
              "Aptos чувствителен к разблокировкам токенов — поэтому сигнал учитывает силу импульса."],
    "Arbitrum": ["ARB — токен L2: динамичен, хорошо следует за ETH.",
                 "Arbitrum даёт чистые направленные движения, когда эфир в тренде."],
    "Optimism": ["OP — L2-токен: импульсы быстрые, коррелирует с ETH и сектором L2.",
                 "Optimism часто повторяет движение эфира с небольшим запозданием."],
    "Sui": ["SUI — быстрорастущий L1: волатильность высокая, импульсы протяжённые.",
            "Sui в тренде идёт длинными свечами, а в откате быстро возвращается к уровню."],
    "Injective": ["INJ — активный DeFi-актив: сильные импульсы и такие же сильные откаты.",
                  "Injective любит резкие рывки — сигнал строится на подтверждённом тренде."],
    "Filecoin": ["FIL — монета с умеренной ликвидностью: ход неровный, но уровни уважает.",
                 "Filecoin часто стоит в диапазоне и резко выходит из него."],
    "Ethereum Classic": ["ETC — старая монета с относительно спокойным ходом и редкими всплесками.",
                         "Ethereum Classic часто «просыпается» вместе с рынком майнинговых монет."],
    "Bitcoin Cash": ["BCH — «старый» форк биткоина: ходит плавно, импульсы короткие.",
                     "Bitcoin Cash тяготеет к диапазонам, поэтому сигнал опирается на выход из них."],
    "Stellar": ["XLM — спокойная монета, часто во флэте и коррелирует с XRP.",
                "Stellar двигается рывками вслед за Ripple — смотрим, чтобы направление совпадало."],
    "Hedera": ["HBAR — монета среднего размера: тренды чёткие, но резкие развороты бывают.",
               "Hedera хорошо реагирует на технические уровни."],
    "EUR/USD": ["EUR/USD — самая ликвидная валютная пара: движения чистые, спред минимальный; зависит от данных США и ЕЦБ.",
                "Евро/доллар — самая «чистая» пара: техника отрабатывается лучше, чем на экзотике."],
    "GBP/USD": ["GBP/USD — «кабель»: резкая пара с ложными проколами уровней, зато движения протяжённые.",
                "Фунт/доллар любит импульсы на лондонской сессии; важна подтверждённость тренда."],
    "AUD/USD": ["AUD/USD — «аусси»: связан с сырьём и азиатской сессией, поведение зависит от настроя к риску.",
                "Австралиец следует за аппетитом к риску и Китаем — движения плавные."],
    "NZD/USD": ["NZD/USD — «киви»: коррелирует с AUD, ход плавный, ликвидность ниже, чем у основных пар.",
                "Новозеландец часто повторяет AUD/USD с небольшим отставанием."],
    "EUR/GBP": ["EUR/GBP — кросс без доллара: волатильность низкая, часто боковик, ценятся выходы за диапазон.",
                "Евро/фунт ходит узко — поэтому сигнал на нём опирается на явный пробой и тренд."],
    "USD/JPY": ["USD/JPY — пара зависит от доходностей США и политики Банка Японии; тренды длинные и устойчивые.",
                "Доллар/иена часто идёт одним направлением долго — откаты коротки."],
    "USD/CAD": ["USD/CAD — «луни»: связана с нефтью, поэтому цена на нефть задаёт фон движению.",
                "Доллар/канадец чувствителен к нефти и данным по занятости США."],
    "USD/CHF": ["USD/CHF — «швейцар»: защитная валюта, часто зеркалит EUR/USD.",
                "Доллар/франк движется плавно и чаще всего противоположно евро."],
    "EUR/JPY": ["EUR/JPY — кросс с высокой чувствительностью к риску: тренды сильные, откаты резкие.",
                "Евро/иена хорошо реагирует на общий настрой рынка."],
    "GBP/JPY": ["GBP/JPY — «дракон»: одна из самых волатильных пар, свечи длинные, тени большие.",
                "Фунт/иена движется резкими волнами — нужна точность входа."],
    "AUD/JPY": ["AUD/JPY — индикатор аппетита к риску: растёт на подъёме рынков и падает при страхе.",
                "Аусси/иена идёт в тренде вместе с фондовыми рынками."],
    "EUR/AUD": ["EUR/AUD — кросс со средней волатильностью, тренды длинные, но неспешные.",
                "Евро/аусси чаще всего двигается волнами по несколько часов."],
    "GBP/AUD": ["GBP/AUD — волатильный кросс: большие свечи, быстрые развороты.",
                "Фунт/аусси даёт резкие движения на открытии Лондона."],
    "EUR/CHF": ["EUR/CHF — очень спокойный кросс: диапазоны узкие, сигнал на пробое.",
                "Евро/франк почти всегда в узком диапазоне — выходы из него отрабатываются чисто."],
    "GBP/CHF": ["GBP/CHF — кросс с высокой волатильностью и резкими откатами.",
                "Фунт/франк часто ходит протяжёнными волнами."],
    "AUD/CHF": ["AUD/CHF — отражает риск против безопасности: растёт при оптимизме, падает при страхе.",
                "Аусси/франк реагирует на настроения на глобальных рынках."],
    "CAD/JPY": ["CAD/JPY — связан с нефтью и аппетитом к риску: тренды устойчивые.",
                "Канадец/иена часто идёт за ценой нефти и фондовым рынком."],
    "CHF/JPY": ["CHF/JPY — две защитные валюты: движения медленные, тренд развивается постепенно.",
                "Франк/иена спокойный кросс — шум ниже обычного."],
    "AUD/CAD": ["AUD/CAD — сырьевой кросс: ход неспешный, тренды длинные.",
                "Аусси/канадец реагирует на динамику сырьевых цен."],
    "AUD/NZD": ["AUD/NZD — самый «тихий» кросс: диапазон узкий, ценятся чёткие пробои.",
                "Аусси/киви часто стоит, а затем делает короткий уверенный рывок."],
    "EUR/CAD": ["EUR/CAD — кросс со средней волатильностью: зависит от нефти и данных еврозоны.",
                "Евро/канадец двигается волнами по несколько часов."],
    "GBP/CAD": ["GBP/CAD — волатильный кросс: цена любит ложные проколы и резкие возвраты.",
                "Фунт/канадец даёт сильные импульсы на данных Великобритании."],
    "NZD/JPY": ["NZD/JPY — рискованный кросс: хорошо следует за фондовыми рынками.",
                "Киви/иена в трендовые дни даёт очень чистые движения."],
}

_EXP_NOTES = {
    1: "Экспирация 1 мин — быстрая сделка: сигнал живёт секунды, входить нужно сразу.",
    2: "Экспирация 2 мин — ловим продолжение текущего импульса на двух свечах.",
    3: "Экспирация 3 мин — золотая середина: шум минутных свечей отсекается, импульс ещё живой.",
    4: "Экспирация 4 мин — ставка на направление тренда, мелкие колебания не мешают.",
    5: "Экспирация 5 мин — самый «спокойный» вариант: сделка идёт по тренду старшего таймфрейма.",
}


def _session_note(kind, now_utc):
    h, wd = now_utc.hour, now_utc.weekday()
    if kind == "crypto":
        if wd >= 5:
            return "Выходной день: объёмы тоньше, возможны резкие проколы — вход аккуратнее."
        if 13 <= h < 21:
            return "Идёт американская сессия — по крипте это пик объёмов."
        if 7 <= h < 13:
            return "Европейская сессия: объёмы растут, движения становятся чище."
        return "Азиатская сессия: объёмы умеренные, ход спокойнее."
    if 12 <= h < 16:
        return "Пересечение Лондона и Нью-Йорка — самое активное время для этой пары."
    if 7 <= h < 12:
        return "Идёт лондонская сессия — ликвидность высокая."
    if 16 <= h < 21:
        return "Идёт сессия Нью-Йорка — цена чувствительна к данным США."
    if 0 <= h < 7:
        return "Азиатская сессия: ликвидность ниже, ход спокойнее."
    return "Межсессионье: спреды шире, возможны ложные проколы."


_HEADLINES = {
    ("crypto", True): [
        "{n}: покупатели выкупают просадки, {a} из {t} индикаторов смотрят вверх.",
        "{n} удерживает импульс вверх — структура свечей остаётся бычьей ({a} из {t} за рост).",
        "По {n} спрос сильнее предложения: цена закрепляется выше средних, ожидаем продолжение роста.",
    ],
    ("crypto", False): [
        "{n}: продавцы давят на каждый отскок, {a} из {t} индикаторов за снижение.",
        "{n} теряет импульс — структура свечей медвежья ({a} из {t} за падение).",
        "По {n} предложение перевешивает: цена закрепляется ниже средних, ожидаем продолжение снижения.",
    ],
    ("forex", True): [
        "{n}: покупатели удерживают инициативу, {a} из {t} индикаторов смотрят вверх.",
        "{n} формирует восходящий ход — быки контролируют последние свечи ({a} из {t}).",
        "По паре {n} цена выше ключевых средних — приоритет на покупку.",
    ],
    ("forex", False): [
        "{n}: продавцы удерживают инициативу, {a} из {t} индикаторов смотрят вниз.",
        "{n} формирует нисходящий ход — медведи контролируют последние свечи ({a} из {t}).",
        "По паре {n} цена ниже ключевых средних — приоритет на продажу.",
    ],
    ("metal", True): [
        "{n}: спрос на защитный актив растёт, {a} из {t} индикаторов за рост.",
        "Золото уверенно тянется вверх — покупатели держат цену выше средних ({a} из {t}).",
    ],
    ("metal", False): [
        "{n}: интерес к защитному активу ослабевает, {a} из {t} индикаторов за снижение.",
        "Золото откатывается — продавцы удерживают цену ниже средних ({a} из {t}).",
    ],
}


def _bot_facts(cn, n):
    """Конкретные числа по графику — у каждого сигнала они свои."""
    a = np.array(cn, dtype=float)
    h, l, c = a[:, 1], a[:, 2], a[:, 3]
    facts = []
    k = min(6, len(c) - 1)
    if k >= 3 and c[-k - 1] > 0:
        mv = (c[-1] / c[-k - 1] - 1) * 100
        facts.append(f"За {k * n} мин цена {'выросла' if mv > 0 else 'снизилась'} на {abs(mv):.2f}%")
    rng = (h - l) / np.maximum(c, 1e-12) * 100
    now_v, base = float(rng[-5:].mean()), float(rng[-60:].mean()) if len(rng) >= 20 else float(rng.mean())
    if base > 0:
        r = now_v / base
        if r >= 1.3:
            facts.append(f"Волатильность выше обычной (×{r:.1f})")
        elif r <= 0.75:
            facts.append(f"Рынок затих (×{r:.1f} от обычной волатильности)")
        else:
            facts.append("Волатильность в норме")
    hi, lo = float(h[-31:-1].max()), float(l[-31:-1].min())
    if hi > lo:
        pos = (c[-1] - lo) / (hi - lo)
        if pos > 0.85:
            facts.append("Цена у верха диапазона 30 свечей")
        elif pos < 0.15:
            facts.append("Цена у низа диапазона 30 свечей")
        else:
            facts.append(f"Цена в {pos * 100:.0f}% диапазона 30 свечей")
    return facts


def _bot_tailor(label, name, kind, up, n, res, cn, ts):
    rnd = random.Random(f"{label}{int(ts // 30)}")
    pool = _HEADLINES.get((kind, up)) or _HEADLINES[("crypto", up)]
    headline = rnd.choice(pool).format(n=name, a=res.get("agree", 0), t=res.get("tv", 0))
    notes = ASSET_NOTES.get(name) or ["Актив ликвидный — сигнал построен по его собственному графику."]
    asset_note = rnd.choice(notes)
    now_utc = datetime.fromtimestamp(ts, timezone.utc)
    asset_note = f"{asset_note} {_session_note(kind, now_utc)}"
    return headline, asset_note, _bot_facts(cn, n), _EXP_NOTES[n]


def _bot_eval_sync(items, exps=(2,)):
    """Тяжёлая часть (numpy): анализ каждого актива на 1–5 мин + проверка на истории. Возвращает кандидатов."""
    out = []
    for label, name, kind, raw1, raw5 in items:
        try:
            htf = _agg(raw5, 3) if raw5 and len(raw5) >= 66 else None
            htf_sign = 0
            if htf and len(htf) >= 22:
                hc = np.array(htf, dtype=float)[:, 3]
                htf_sign = 1 if _ema(hc, 9)[-1] > _ema(hc, 21)[-1] else -1
            best = None
            for n in exps:
                cn = _agg(raw1, n)[-130:]
                if len(cn) < 70:
                    continue
                res = pro_analysis(cn, htf)
                sig = 1 if res["signal"] == "UP" else -1
                if res["confidence"] < BOT_MIN_CONF:
                    continue
                if htf_sign and htf_sign != sig:
                    continue
                if res["agree"] / max(1, res["tv"]) < 0.66:
                    continue
                # проверка на истории: как бы сработал этот же анализ на последних закрытых свечах
                wins = tot = 0
                for k in range(2, BOT_BACKTEST + 2):
                    end = len(cn) - k
                    if end < 50:
                        break
                    r = pro_analysis(cn[:end], None)
                    s = 1 if r["signal"] == "UP" else -1
                    d = cn[end][3] - cn[end - 1][3]
                    if d == 0:
                        continue
                    tot += 1
                    if (d > 0) == (s > 0):
                        wins += 1
                if tot < 20:
                    continue
                wr = wins / tot
                if wr < BOT_MIN_HIST:
                    continue
                score = res["confidence"] + (wr - 0.5) * 80 + (3 if htf_sign == sig else 0)
                if best is None or score > best["score"]:
                    best = {"label": label, "name": name, "kind": kind, "n": n, "res": res, "cn": cn,
                            "wins": wins, "tot": tot, "score": score}
            if best:
                out.append(best)
        except Exception as e:
            logging.warning(f"bot eval {name}: {e}")
    return out


async def _bot_fetch_one(label, name, kind, sem):
    until = _BOT_DEAD.get(name)
    if until and until > time.time():
        return None
    async with sem:
        r1, r5 = await asyncio.gather(fetch_binance_candles(name, "M1", 600),
                                      fetch_binance_candles(name, "M5", 150), return_exceptions=True)
    if isinstance(r1, Exception) or not r1 or len(r1) < 130:
        return ("dead", name)
    if isinstance(r5, Exception):
        r5 = None
    if name in _BOT_INVERT:
        inv = lambda rows: [[1 / x[0], 1 / x[2], 1 / x[1], 1 / x[3]] for x in rows if min(x) > 0]
        r1 = inv(r1)
        r5 = inv(r5) if r5 else None
    return (label, name, kind, r1, r5)


def _bot_ch(n):
    ch = _BOT.get(n)
    if ch is None:
        ch = {"feed": deque(maxlen=100), "task": None, "last_poll": time.time(),
              "state": {"next_scan": time.time() + 3, "scanning": False, "entry": 0, "end": 0, "ok": True}}
        _BOT[n] = ch
    return ch


def _bot_ensure(n):
    ch = _bot_ch(n)
    ch["last_poll"] = time.time()
    t = ch["task"]
    if t is None or t.done():
        ch["task"] = asyncio.create_task(_bot_channel_loop(n))
    return ch


def _bot_push(ch, msg):
    _BOT_SEQ[0] += 1
    msg["id"] = int(time.time() * 1000) * 10 + (_BOT_SEQ[0] % 10)
    ch["feed"].append(msg)


async def bot_scan_once(n, ch):
    """Один скан. Возвращает время (unix), когда нужен следующий скан."""
    S = ch["state"]
    sem = asyncio.Semaphore(10)
    closed = _forex_closed()
    assets = [a for a in BOT_ASSETS if (not closed) or _asset_is_24x7(a[0])]   # рынок закрыт — только крипта
    fetched = await asyncio.gather(*[_bot_fetch_one(l, nm, k, sem) for l, nm, k in assets], return_exceptions=True)
    ok_items, dead = [], []
    for f in fetched:
        if not f or isinstance(f, Exception):
            continue
        if f[0] == "dead":
            dead.append(f[1])
        else:
            ok_items.append(f)
    S["ok"] = bool(ok_items)
    if ok_items:
        for nm in dead:
            _BOT_DEAD[nm] = time.time() + 900
    if not ok_items:
        return time.time() + 30
    cands = await asyncio.to_thread(_bot_eval_sync, ok_items, (n,))
    now = time.time()
    cands.sort(key=lambda x: -x["score"])
    if not cands:
        last = ch["feed"][-1] if ch["feed"] else None
        if not last or last.get("kind") != "wait":
            _bot_push(ch, {"ts": now, "kind": "wait",
                           "text": f"Проверил {len(ok_items)} активов на Binance — чёткого сетапа сейчас нет. "
                                   f"Проверю снова через 30 секунд."
                                   + (" Форекс закрыт — работаю по криптовалютам." if closed else "")})
        return now + 30
    p = cands[0]                      # один сигнал за раз — лучший
    res = p["res"]
    up = res["signal"] == "UP"
    headline, asset_note, facts, exp_note = _bot_tailor(p["label"], p["name"], p["kind"], up, n, res, p["cn"], now)
    entry = math.ceil((now + BOT_LEAD) / 60.0) * 60          # ровная минута входа (например 15:53:00)
    end = entry + n * 60
    _bot_push(ch, {
        "ts": now, "entry": entry, "kind": "signal", "asset": p["label"], "tf": f"M{n}", "exp": n * 60,
        "dir": "CALL" if up else "PUT", "conf": res["confidence"],
        "quality": res.get("quality", _quality_for(res["confidence"])),
        "price": _fmt_price(p["cn"][-1][3]),
        "headline": headline, "asset_note": asset_note, "facts": facts, "exp_note": exp_note,
        "points": res.get("points", [])[:3], "against": res.get("against", ""),
        "warns": [w.replace("⚠️", "").strip() for w in res.get("warns", [])],
        "indicators": res.get("indicators", []), "hist": [p["wins"], p["tot"]],
        "scanned": len(ok_items),
    })
    S["entry"], S["end"] = entry, end
    return end + BOT_PAUSE            # пока идёт сигнал — тишина; новый через минуту после экспирации


async def _bot_channel_loop(n):
    await asyncio.sleep(2)
    ch = _bot_ch(n)
    S = ch["state"]
    while True:
        if n != BOT_DEFAULT_EXP and time.time() - ch["last_poll"] > 600:
            return                    # никто не смотрит эту экспирацию — канал засыпает
        S["scanning"] = True
        try:
            nxt = await bot_scan_once(n, ch)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.warning(f"signal bot {n}m: {e}")
            nxt = time.time() + 30
        S["scanning"] = False
        S["next_scan"] = nxt
        await asyncio.sleep(max(1.0, nxt - time.time()))


async def signal_bot_loop():
    ch = _bot_ch(BOT_DEFAULT_EXP)
    ch["task"] = asyncio.current_task()
    await _bot_channel_loop(BOT_DEFAULT_EXP)


@app.get("/bot_feed")
async def bot_feed(after: int = 0, exp: int = 2):
    if exp not in BOT_EXP_CHOICES:
        exp = BOT_DEFAULT_EXP
    ch = _bot_ensure(exp)
    S = ch["state"]
    msgs = [m for m in ch["feed"] if m["id"] > after]
    return {"messages": msgs[-60:], "next_scan": S["next_scan"], "entry": S["entry"], "end": S["end"],
            "exp": exp, "scanning": S["scanning"], "ok": S["ok"], "server_time": time.time(),
            "forex_closed": _forex_closed(), "reopen": _forex_reopen_ts()}


def _signal_headline(res):
    """Короткое чёткое описание сигнала одной фразой."""
    up = res.get("signal") == "UP"
    side = "Покупатели" if up else "Продавцы"
    way = "вверх" if up else "вниз"
    tv, agree = res.get("tv"), res.get("agree")
    if tv:
        return f"{side} держат инициативу: {agree} из {tv} индикаторов смотрят {way}."
    return f"{side} перевешивают по совокупности индикаторов — ожидаем движение {way}."



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
async def get_news(refresh: int = 0):
    if _NEWS_CACHE["data"] and time.time() - _NEWS_CACHE["ts"] < (8 if refresh else 60):
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
