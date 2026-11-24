"""
live_module.py — ПРЯМОЙ ЭФИР + ID + ВИДЕО + РАСПИСАНИЕ + ГОЛОС (один файл-модуль)

Подключение к твоему main.py (2 строки, в самый конец файла, ПЕРЕД uvicorn.run / if __name__):

    from live_module import mount_live
    mount_live(app, db_exec=_dbx, pg=_MT_PG)

Страницы:
    /live          — сайт для зрителей: эфир + чат, видео-материалы, расписание, профиль с ID
    /live/admin    — ТВОЯ консоль (пароль ADMIN_PASSWORD): эфир с камерой/голосом, смена голоса,
                     загрузка видео, расписание, список пользователей, бан в чате

Зависимости: fastapi, uvicorn, python-multipart (у тебя уже есть). Больше ничего.
Отдельный запуск для теста:  python live_module.py  → http://localhost:8000/live

ENV (необязательные):
    LIVE_ADMIN_PASSWORD / ADMIN_PASSWORD / ADMIN_KEY — пароль консоли
    LIVE_UPLOAD_DIR   — куда складывать видео (на Render смонтируй диск, напр. /data/uploads)
    LIVE_MAX_UPLOAD_MB— лимит загрузки (по умолчанию 500)
    LIVE_ICE_JSON     — свои ICE/TURN-серверы, JSON-массив, напр.
                        [{"urls":"turn:turn.example.com:3478","username":"u","credential":"p"}]
    LIVE_DB_PATH      — sqlite-файл (если нет db_exec и нет DATABASE_URL)
    DATABASE_URL      — Postgres (если не передаёшь db_exec)
"""
import os
import re
import json
import time
import hmac
import random
import hashlib
import secrets
import asyncio
import logging
import pathlib
import sqlite3
import threading
import mimetypes
from collections import deque
from urllib.parse import urlparse

from fastapi import APIRouter, Request, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

log = logging.getLogger("live")

VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v", ".ogg"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


# ───────────────────────── БАЗА ДАННЫХ ─────────────────────────
class _DB:
    """Если передан db_exec (твой _dbx) — работает через него, иначе своя sqlite/Postgres."""

    def __init__(self, fn=None, pg=False):
        self.fn = fn
        self.url = os.environ.get("DATABASE_URL", "").strip()
        self.pg = bool(pg) or (fn is None and self.url.startswith(("postgres://", "postgresql://")))
        self.lock = threading.Lock()
        self.conn = None

    def _connect(self):
        if self.pg:
            import psycopg2
            c = psycopg2.connect(self.url, connect_timeout=8)
            c.autocommit = True
            return c
        c = sqlite3.connect(os.environ.get("LIVE_DB_PATH", "/tmp/live.db"), check_same_thread=False, timeout=15)
        try:
            c.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        return c

    def run(self, sql, args=(), mode="all"):
        if self.fn:
            return self.fn(sql, tuple(args), mode)
        with self.lock:
            for attempt in (0, 1):
                try:
                    if self.conn is None:
                        self.conn = self._connect()
                    cur = self.conn.cursor()
                    cur.execute(sql.replace("?", "%s") if self.pg else sql, tuple(args))
                    if mode == "exec":
                        n = cur.rowcount
                        if not self.pg:
                            self.conn.commit()
                        cur.close()
                        return n
                    cols = [d[0] for d in cur.description] if cur.description else []
                    rows = [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []
                    if not self.pg:
                        self.conn.commit()
                    cur.close()
                    return (rows[0] if rows else None) if mode == "one" else rows
                except Exception:
                    try:
                        if self.conn:
                            self.conn.close()
                    except Exception:
                        pass
                    self.conn = None
                    if attempt:
                        raise

    async def q(self, sql, args=(), mode="all"):
        return await asyncio.to_thread(self.run, sql, args, mode)

    def init(self):
        pk = "BIGSERIAL PRIMARY KEY" if self.pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
        self.run("""CREATE TABLE IF NOT EXISTS lv_users (uid BIGINT PRIMARY KEY, name TEXT DEFAULT '',
            created BIGINT DEFAULT 0, last_seen BIGINT DEFAULT 0, banned INTEGER DEFAULT 0,
            ip TEXT DEFAULT '', ua TEXT DEFAULT '')""", (), "exec")
        self.run("""CREATE TABLE IF NOT EXISTS lv_videos (id %s, title TEXT DEFAULT '', descr TEXT DEFAULT '',
            url TEXT DEFAULT '', ts BIGINT DEFAULT 0, sort INTEGER DEFAULT 0, active INTEGER DEFAULT 1)""" % pk, (), "exec")
        self.run("""CREATE TABLE IF NOT EXISTS lv_events (id %s, title TEXT DEFAULT '', descr TEXT DEFAULT '',
            link TEXT DEFAULT '', start_ts BIGINT DEFAULT 0, ts BIGINT DEFAULT 0, active INTEGER DEFAULT 1)""" % pk, (), "exec")
        self.run("CREATE TABLE IF NOT EXISTS lv_kv (k TEXT PRIMARY KEY, v TEXT DEFAULT '')", (), "exec")


# ───────────────────────── ВСПОМОГАТЕЛЬНОЕ ─────────────────────────
def _clean(s, n):
    return re.sub(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f<>]", "", str(s or "")).strip()[:n]


def _num(x, lo, hi, d=0.0):
    try:
        return max(lo, min(hi, float(x)))
    except Exception:
        return d


def _voice_clean(d):
    d = d if isinstance(d, dict) else {}
    return {"preset": _clean(d.get("preset") or "normal", 16),
            "pitch": _num(d.get("pitch"), -0.8, 0.8),        # -0.8…+0.8  (≈ ±октава)
            "bass": _num(d.get("bass"), -12, 12),             # дБ, низы (больше = «мужественнее»)
            "treble": _num(d.get("treble"), -12, 12),         # дБ, верха
            "robot": 1 if d.get("robot") else 0}


VOICE_DEFAULT = {"preset": "normal", "pitch": 0.0, "bass": 0.0, "treble": 0.0, "robot": 0}


def _media_embed(u):
    """Как показывать видео на сайте: youtube/vimeo/rutube → iframe, mp4 → <video>, иначе ссылка."""
    u = (u or "").strip()
    m = re.search(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})", u)
    if m:
        return "iframe", "https://www.youtube.com/embed/%s?rel=0&modestbranding=1&playsinline=1" % m.group(1)
    m = re.search(r"vimeo\.com/(?:video/)?(\d{6,12})", u)
    if m:
        return "iframe", "https://player.vimeo.com/video/" + m.group(1)
    m = re.search(r"rutube\.ru/(?:video|play/embed)/([0-9a-f]{32})", u)
    if m:
        return "iframe", "https://rutube.ru/play/embed/" + m.group(1)
    if re.search(r"\.(mp4|webm|mov|m4v|ogg)(\?|$)", u, re.I):
        return "file", u
    return "link", u


_URL_RE = re.compile(r"^(https?://[^\s<>\"']+|/[A-Za-z0-9_\-/]*/media/[A-Za-z0-9._-]+)$")


def _safe_url(u):
    u = _clean(u, 700)
    return u if (not u or _URL_RE.match(u)) else None


class _Hub:
    """Комната эфира в памяти: кто подключён, кто ведущий, чат."""

    def __init__(self):
        self.conns = {}          # cid -> {"ws","uid","name","admin","banned","t"}
        self.host = None         # cid ведущего
        self.live = False
        self.title = ""
        self.chat = deque(maxlen=60)

    def count(self):
        return len(self.conns) - (1 if self.host in self.conns else 0)

    async def send(self, cid, msg):
        c = self.conns.get(cid)
        if not c:
            return
        try:
            await c["ws"].send_text(json.dumps(msg, ensure_ascii=False))
        except Exception:
            self.conns.pop(cid, None)

    async def broadcast(self, msg, skip=None):
        for cid in list(self.conns):
            if cid != skip:
                await self.send(cid, msg)


# ───────────────────────── ПОДКЛЮЧЕНИЕ ─────────────────────────
def mount_live(app, prefix="/live", db_exec=None, pg=False, is_admin=None):
    """
    app       — твой FastAPI()
    db_exec   — твоя функция _dbx(sql, args, mode) (необязательно)
    pg        — True, если твоя база Postgres (_MT_PG)
    is_admin  — необязательно: функция(request)->bool, если хочешь пускать в консоль по другой авторизации
    """
    prefix = "/" + prefix.strip("/")
    db = _DB(db_exec, pg)
    hub = _Hub()
    r = APIRouter(prefix=prefix)
    st = {"ready": False, "secret": "", "lock": asyncio.Lock()}

    pw = (os.environ.get("LIVE_ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD") or os.environ.get("ADMIN_KEY") or "").strip()
    if not pw:
        pw = secrets.token_urlsafe(10)
        log.warning("Пароль консоли %s/admin не задан. Временный пароль на этот запуск: %s (задай ADMIN_PASSWORD)", prefix, pw)
    adm_key = hashlib.sha256(("lvadm|" + pw).encode()).digest()
    max_up = int(os.environ.get("LIVE_MAX_UPLOAD_MB", "500")) * 1024 * 1024

    upd = None
    for p in (os.environ.get("LIVE_UPLOAD_DIR"), os.environ.get("UPLOAD_DIR"), "/data/uploads" if os.path.isdir("/data") else None, "uploads", "/tmp/uploads"):
        if not p:
            continue
        try:
            d = pathlib.Path(p)
            d.mkdir(parents=True, exist_ok=True)
            (d / ".w").write_text("1")
            upd = d
            break
        except Exception:
            continue
    upd = upd or pathlib.Path("/tmp")

    # ---- init ----
    async def ready():
        if st["ready"]:
            return
        async with st["lock"]:
            if st["ready"]:
                return
            await asyncio.to_thread(db.init)
            row = await db.q("SELECT v FROM lv_kv WHERE k='secret'", (), "one")
            if not row:
                await db.q("INSERT INTO lv_kv(k,v) VALUES('secret',?) ON CONFLICT (k) DO NOTHING", (secrets.token_hex(32),), "exec")
                row = await db.q("SELECT v FROM lv_kv WHERE k='secret'", (), "one")
            st["secret"] = row["v"]
            st["ready"] = True
            log.info("live: готово (%s), папка видео: %s", "postgres" if db.pg else "sqlite/внешняя БД", upd)

    # ---- ID пользователей (навсегда) ----
    def sign(uid):
        return hmac.new(st["secret"].encode(), ("lv%d" % uid).encode(), hashlib.sha256).hexdigest()[:28]

    def token(uid):
        return "%d.%s" % (uid, sign(uid))

    def verify(tok):
        try:
            u, s = str(tok).split(".", 1)
            uid = int(u)
            if hmac.compare_digest(s, sign(uid)):
                return uid
        except Exception:
            pass
        return None

    def req_uid(request):
        for t in (request.headers.get("x-live-tok") or "", request.cookies.get("lv_tok") or ""):
            if t:
                uid = verify(t)
                if uid:
                    return uid
        return None

    def ip_of(request):
        xf = request.headers.get("x-forwarded-for") or ""
        return (xf.split(",")[0].strip() if xf else (request.client.host if request.client else ""))[:60]

    def https(request):
        return request.url.scheme == "https" or (request.headers.get("x-forwarded-proto") or "").startswith("https")

    def set_user_cookie(resp, request, tok):
        resp.set_cookie("lv_tok", tok, max_age=3 * 365 * 86400, httponly=True, samesite="lax", secure=https(request), path="/")

    async def get_user(uid):
        u = await db.q("SELECT uid,name,created,last_seen,banned FROM lv_users WHERE uid=?", (uid,), "one")
        if u is None:  # база была сброшена, а токен у человека жив — создаём строку с тем же ID
            now = int(time.time())
            await db.q("INSERT INTO lv_users(uid,created,last_seen) VALUES(?,?,?) ON CONFLICT (uid) DO NOTHING", (uid, now, now), "exec")
            u = await db.q("SELECT uid,name,created,last_seen,banned FROM lv_users WHERE uid=?", (uid,), "one")
        return u

    # ---- админ-авторизация ----
    def adm_sign(exp):
        return hmac.new(adm_key, ("a%d" % exp).encode(), hashlib.sha256).hexdigest()[:40]

    def adm_cookie_ok(cookie):
        try:
            e, s = (cookie or "").split(".", 1)
            return int(e) > time.time() and hmac.compare_digest(s, adm_sign(int(e)))
        except Exception:
            return False

    def is_adm(request):
        if adm_cookie_ok(request.cookies.get("lv_adm")) and request.headers.get("x-req") == "1":
            return True
        try:
            return bool(is_admin and is_admin(request))
        except Exception:
            return False

    def bad(msg, code=400):
        return JSONResponse({"ok": False, "error": msg}, status_code=code)

    async def body(request):
        try:
            d = await request.json()
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    async def kv_get(k, default=None):
        row = await db.q("SELECT v FROM lv_kv WHERE k=?", (k,), "one")
        try:
            return json.loads(row["v"]) if row else default
        except Exception:
            return default

    async def kv_set(k, v):
        await db.q("INSERT INTO lv_kv(k,v) VALUES(?,?) ON CONFLICT (k) DO UPDATE SET v=excluded.v", (k, json.dumps(v, ensure_ascii=False)), "exec")

    # ═══════════════ ПУБЛИЧНОЕ API ═══════════════
    reg_rl = {}

    @r.post("/api/register")
    async def api_register(request: Request):
        """Выдаёт ID навсегда. Если у человека уже есть валидный токен — возвращает тот же ID."""
        await ready()
        uid = req_uid(request)
        if uid:
            await get_user(uid)
        else:
            ip = ip_of(request)
            now = time.time()
            lst = [t for t in reg_rl.get(ip, []) if now - t < 3600]
            if len(lst) >= 15:
                return bad("Слишком много запросов", 429)
            lst.append(now)
            reg_rl[ip] = lst
            ua = (request.headers.get("user-agent") or "")[:200]
            ok = False
            for _ in range(40):
                uid = random.randint(1000000, 9999999)
                n = await db.q("INSERT INTO lv_users(uid,created,last_seen,ip,ua) VALUES(?,?,?,?,?) ON CONFLICT (uid) DO NOTHING",
                               (uid, int(now), int(now), ip, ua), "exec")
                if n == 1:
                    ok = True
                    break
            if not ok:
                return bad("Не удалось создать ID", 500)
        u = await get_user(uid)
        tok = token(uid)
        resp = JSONResponse({"ok": True, "uid": uid, "token": tok, "name": u["name"] or ""})
        set_user_cookie(resp, request, tok)
        return resp

    @r.post("/api/restore")
    async def api_restore(request: Request):
        """Вернуть свой ID по коду восстановления (если очистил браузер)."""
        await ready()
        d = await body(request)
        uid = verify(_clean(d.get("code"), 80))
        if not uid:
            return bad("Неверный код", 403)
        u = await get_user(uid)
        tok = token(uid)
        resp = JSONResponse({"ok": True, "uid": uid, "token": tok, "name": u["name"] or ""})
        set_user_cookie(resp, request, tok)
        return resp

    @r.get("/api/me")
    async def api_me(request: Request):
        await ready()
        uid = req_uid(request)
        if not uid:
            return JSONResponse({"ok": False})
        u = await get_user(uid)
        await db.q("UPDATE lv_users SET last_seen=? WHERE uid=?", (int(time.time()), uid), "exec")
        return {"ok": True, "uid": uid, "token": token(uid), "name": u["name"] or "", "banned": bool(u["banned"]),
                "created": u["created"]}

    @r.post("/api/profile")
    async def api_profile(request: Request):
        await ready()
        uid = req_uid(request)
        if not uid:
            return bad("Нет ID", 401)
        d = await body(request)
        name = re.sub(r"\s+", " ", _clean(d.get("name"), 24))
        await get_user(uid)
        await db.q("UPDATE lv_users SET name=? WHERE uid=?", (name, uid), "exec")
        return {"ok": True, "name": name}

    @r.get("/api/config")
    async def api_config():
        ice = [{"urls": "stun:stun.l.google.com:19302"}, {"urls": "stun:stun1.l.google.com:19302"}]
        try:
            custom = json.loads(os.environ.get("LIVE_ICE_JSON", "") or "[]")
            if isinstance(custom, list) and custom:
                ice = custom
        except Exception:
            pass
        return {"ok": True, "ice": ice, "live": hub.live, "title": hub.title, "count": hub.count()}

    def vid_row(x):
        kind, embed = _media_embed(x["url"])
        return {"id": x["id"], "title": x["title"], "descr": x["descr"], "url": x["url"], "kind": kind, "embed": embed,
                "ts": x["ts"], "active": x["active"], "sort": x["sort"]}

    @r.get("/api/videos")
    async def api_videos():
        await ready()
        rows = await db.q("SELECT id,title,descr,url,ts,sort,active FROM lv_videos WHERE active=1 ORDER BY sort DESC, id DESC LIMIT 200", (), "all")
        return {"ok": True, "items": [vid_row(x) for x in rows]}

    @r.get("/api/events")
    async def api_events():
        await ready()
        rows = await db.q("SELECT id,title,descr,link,start_ts FROM lv_events WHERE active=1 AND start_ts>? ORDER BY start_ts ASC LIMIT 100",
                          (int(time.time()) - 6 * 3600,), "all")
        return {"ok": True, "items": rows}

    # ═══════════════ АДМИН API ═══════════════
    fails = {}

    @r.post("/api/admin/login")
    async def adm_login(request: Request):
        ip = ip_of(request)
        n, t = fails.get(ip, (0, 0))
        if time.time() - t > 600:
            n = 0
        if n >= 8:
            return bad("Слишком много попыток, подожди 10 минут", 429)
        d = await body(request)
        if not hmac.compare_digest(str(d.get("password") or "").encode(), pw.encode()):
            fails[ip] = (n + 1, time.time())
            return bad("Неверный пароль", 403)
        fails.pop(ip, None)
        exp = int(time.time()) + 12 * 3600
        resp = JSONResponse({"ok": True})
        resp.set_cookie("lv_adm", "%d.%s" % (exp, adm_sign(exp)), max_age=12 * 3600, httponly=True, samesite="strict",
                        secure=https(request), path="/")
        return resp

    @r.post("/api/admin/logout")
    async def adm_logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie("lv_adm", path="/")
        return resp

    @r.get("/api/admin/me")
    async def adm_me(request: Request):
        return {"ok": is_adm(request)}

    # --- голос: ТОЛЬКО админ может читать/менять ---
    @r.get("/api/admin/voice")
    async def adm_voice_get(request: Request):
        if not is_adm(request):
            return bad("auth", 401)
        await ready()
        return {"ok": True, "voice": _voice_clean(await kv_get("voice", VOICE_DEFAULT))}

    @r.post("/api/admin/voice")
    async def adm_voice_set(request: Request):
        if not is_adm(request):
            return bad("auth", 401)
        await ready()
        v = _voice_clean(await body(request))
        await kv_set("voice", v)
        return {"ok": True, "voice": v}

    # --- видео ---
    @r.get("/api/admin/videos")
    async def adm_videos(request: Request):
        if not is_adm(request):
            return bad("auth", 401)
        await ready()
        rows = await db.q("SELECT id,title,descr,url,ts,sort,active FROM lv_videos ORDER BY sort DESC, id DESC LIMIT 500", (), "all")
        return {"ok": True, "items": [vid_row(x) for x in rows]}

    @r.post("/api/admin/video")
    async def adm_video_add(request: Request):
        if not is_adm(request):
            return bad("auth", 401)
        await ready()
        d = await body(request)
        title = _clean(d.get("title"), 120)
        url = _safe_url(d.get("url"))
        if not title or not url:
            return bad("Нужны название и корректная ссылка")
        await db.q("INSERT INTO lv_videos(title,descr,url,ts,sort,active) VALUES(?,?,?,?,?,1)",
                   (title, _clean(d.get("descr"), 1000), url, int(time.time()), int(_num(d.get("sort"), -999, 999)),), "exec")
        return {"ok": True}

    @r.post("/api/admin/video/update")
    async def adm_video_upd(request: Request):
        if not is_adm(request):
            return bad("auth", 401)
        await ready()
        d = await body(request)
        vid = int(_num(d.get("id"), 0, 10**12))
        if d.get("delete"):
            row = await db.q("SELECT url FROM lv_videos WHERE id=?", (vid,), "one")
            await db.q("DELETE FROM lv_videos WHERE id=?", (vid,), "exec")
            if row and (row["url"] or "").startswith(prefix + "/media/"):
                try:
                    (upd / pathlib.Path(row["url"]).name).unlink(missing_ok=True)
                except Exception:
                    pass
        else:
            if "active" in d:
                await db.q("UPDATE lv_videos SET active=? WHERE id=?", (1 if d.get("active") else 0, vid), "exec")
            if "title" in d:
                await db.q("UPDATE lv_videos SET title=? WHERE id=?", (_clean(d.get("title"), 120), vid), "exec")
            if "descr" in d:
                await db.q("UPDATE lv_videos SET descr=? WHERE id=?", (_clean(d.get("descr"), 1000), vid), "exec")
            if "sort" in d:
                await db.q("UPDATE lv_videos SET sort=? WHERE id=?", (int(_num(d.get("sort"), -999, 999)), vid), "exec")
        return {"ok": True}

    @r.post("/api/admin/upload")
    async def adm_upload(request: Request, file: UploadFile = File(...), title: str = Form(""), descr: str = Form("")):
        if not is_adm(request):
            return bad("auth", 401)
        await ready()
        ext = pathlib.Path(file.filename or "").suffix.lower()
        if ext not in VIDEO_EXT | IMG_EXT:
            return bad("Формат не поддерживается (mp4, webm, mov, m4v, ogg, jpg, png, webp, gif)")
        name = secrets.token_hex(8) + ext
        dest = upd / name
        size = 0
        try:
            with open(dest, "wb") as f:
                while True:
                    chunk = await file.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_up:
                        f.close()
                        dest.unlink(missing_ok=True)
                        return bad("Файл слишком большой (лимит %d МБ)" % (max_up // 1048576))
                    f.write(chunk)
        except Exception as e:
            dest.unlink(missing_ok=True)
            return bad("Ошибка записи: %s" % str(e)[:100], 500)
        url = "%s/media/%s" % (prefix, name)
        t = _clean(title, 120) or pathlib.Path(file.filename or name).stem[:120]
        await db.q("INSERT INTO lv_videos(title,descr,url,ts,sort,active) VALUES(?,?,?,?,0,1)", (t, _clean(descr, 1000), url, int(time.time())), "exec")
        return {"ok": True, "url": url}

    # --- расписание («когда и что») ---
    @r.get("/api/admin/events")
    async def adm_events(request: Request):
        if not is_adm(request):
            return bad("auth", 401)
        await ready()
        rows = await db.q("SELECT id,title,descr,link,start_ts,active FROM lv_events ORDER BY start_ts DESC LIMIT 300", (), "all")
        return {"ok": True, "items": rows}

    @r.post("/api/admin/event")
    async def adm_event_add(request: Request):
        if not is_adm(request):
            return bad("auth", 401)
        await ready()
        d = await body(request)
        title = _clean(d.get("title"), 160)
        ts = int(_num(d.get("start_ts"), 0, 4102444800))
        link = _safe_url(d.get("link"))
        if not title or not ts or link is None:
            return bad("Нужны название и дата/время")
        await db.q("INSERT INTO lv_events(title,descr,link,start_ts,ts,active) VALUES(?,?,?,?,?,1)",
                   (title, _clean(d.get("descr"), 1000), link, ts, int(time.time())), "exec")
        return {"ok": True}

    @r.post("/api/admin/event/update")
    async def adm_event_upd(request: Request):
        if not is_adm(request):
            return bad("auth", 401)
        await ready()
        d = await body(request)
        eid = int(_num(d.get("id"), 0, 10**12))
        if d.get("delete"):
            await db.q("DELETE FROM lv_events WHERE id=?", (eid,), "exec")
        elif "active" in d:
            await db.q("UPDATE lv_events SET active=? WHERE id=?", (1 if d.get("active") else 0, eid), "exec")
        return {"ok": True}

    # --- люди ---
    @r.get("/api/admin/users")
    async def adm_users(request: Request):
        if not is_adm(request):
            return bad("auth", 401)
        await ready()
        rows = await db.q("SELECT uid,name,created,last_seen,banned FROM lv_users ORDER BY last_seen DESC LIMIT 300", (), "all")
        online = {c["uid"] for c in hub.conns.values() if c.get("uid")}
        for x in rows:
            x["online"] = x["uid"] in online
        return {"ok": True, "items": rows, "total": (await db.q("SELECT COUNT(*) AS n FROM lv_users", (), "one"))["n"]}

    @r.post("/api/admin/ban")
    async def adm_ban(request: Request):
        if not is_adm(request):
            return bad("auth", 401)
        await ready()
        d = await body(request)
        uid = int(_num(d.get("uid"), 0, 10**15))
        b = 1 if d.get("banned") else 0
        await db.q("UPDATE lv_users SET banned=? WHERE uid=?", (b, uid), "exec")
        for c in hub.conns.values():
            if c.get("uid") == uid:
                c["banned"] = bool(b)
        return {"ok": True}

    # ═══════════════ ВИДЕО-ФАЙЛЫ (с перемоткой) ═══════════════
    @r.get("/media/{name}")
    async def media(name: str, request: Request):
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name):
            return Response(status_code=404)
        path = upd / name
        if not path.is_file():
            return Response(status_code=404)
        size = path.stat().st_size
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        start, end, status = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)", request.headers.get("range") or "")
        if m:
            a, b = m.groups()
            if a == "" and b != "":
                start = max(0, size - int(b))
            elif a != "":
                start = int(a)
                end = int(b) if b else size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                return Response(status_code=416, headers={"Content-Range": "bytes */%d" % size})
            status = 206

        def it():
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    ch = f.read(min(1 << 20, left))
                    if not ch:
                        break
                    left -= len(ch)
                    yield ch

        h = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1), "Cache-Control": "public, max-age=3600"}
        if status == 206:
            h["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
        return StreamingResponse(it(), status_code=status, media_type=ctype, headers=h)

    # ═══════════════ WEBSOCKET: эфир, сигналинг WebRTC, чат ═══════════════
    @r.websocket("/ws")
    async def ws_ep(ws: WebSocket):
        await ws.accept()
        try:
            await ready()
        except Exception:
            await ws.close()
            return
        uid = verify(ws.query_params.get("tok") or ws.cookies.get("lv_tok") or "")
        # админ-роль: только по cookie и только с нашего же сайта (защита от чужих страниц)
        org = urlparse(ws.headers.get("origin") or "").netloc
        host_h = (ws.headers.get("host") or "")
        admin = adm_cookie_ok(ws.cookies.get("lv_adm")) and (not org or org == host_h)
        name, banned = "", False
        if uid:
            u = await get_user(uid)
            name, banned = u["name"] or "", bool(u["banned"])
            await db.q("UPDATE lv_users SET last_seen=? WHERE uid=?", (int(time.time()), uid), "exec")
        cid = secrets.token_hex(6)
        hub.conns[cid] = {"ws": ws, "uid": uid, "name": name, "admin": admin, "banned": banned, "t": 0.0}
        await hub.send(cid, {"t": "hello", "cid": cid, "uid": uid, "admin": admin, "live": hub.live, "title": hub.title,
                             "count": hub.count(), "chat": list(hub.chat)})
        await hub.broadcast({"t": "count", "n": hub.count()})
        try:
            while True:
                m = json.loads(await ws.receive_text())
                t = m.get("t")
                c = hub.conns.get(cid)
                if not c:
                    break
                if t == "host" and c["admin"]:
                    if hub.host and hub.host != cid:
                        await hub.send(hub.host, {"t": "kicked"})
                    hub.host = cid
                    await hub.send(cid, {"t": "host_ok"})
                elif t == "live" and hub.host == cid:
                    hub.live = bool(m.get("on"))
                    hub.title = _clean(m.get("title"), 120)
                    await hub.broadcast({"t": "live", "on": hub.live, "title": hub.title}, skip=cid)
                elif t == "watch":
                    if hub.live and hub.host and hub.host in hub.conns and cid != hub.host:
                        await hub.send(hub.host, {"t": "viewer", "cid": cid})
                elif t == "sig":
                    to = m.get("to")
                    if to in hub.conns and (cid == hub.host or to == hub.host):
                        await hub.send(to, {"t": "sig", "from": cid, "data": m.get("data")})
                elif t == "name" and c["uid"]:
                    u = await get_user(c["uid"])
                    c["name"] = u["name"] or ""
                elif t == "chat":
                    txt = _clean(m.get("text"), 300)
                    now = time.time()
                    if not txt or c["banned"] or now - c["t"] < 1.2:
                        continue
                    c["t"] = now
                    msg = {"t": "chat", "uid": c["uid"], "name": ("Админ" if c["admin"] else (c["name"] or ("ID %s" % (c["uid"] or "гость")))),
                           "adm": c["admin"], "text": txt, "ts": int(now)}
                    hub.chat.append(msg)
                    await hub.broadcast(msg)
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            hub.conns.pop(cid, None)
            if hub.host == cid:
                hub.host = None
                if hub.live:
                    hub.live = False
                    await hub.broadcast({"t": "live", "on": False, "title": ""})
            await hub.broadcast({"t": "count", "n": hub.count()})

    # ═══════════════ СТРАНИЦЫ ═══════════════
    @r.get("", response_class=HTMLResponse)
    @r.get("/", response_class=HTMLResponse)
    async def page_site():
        return HTMLResponse(_SITE_HTML.replace("__P__", prefix), headers={"Cache-Control": "no-store"})

    @r.get("/admin", response_class=HTMLResponse)
    async def page_admin():
        return HTMLResponse(_ADMIN_HTML.replace("__P__", prefix),
                            headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "X-Robots-Tag": "noindex"})

    app.include_router(r)
    log.info("live: страницы %s и %s/admin подключены", prefix, prefix)
    return r


# ───────────────────────── ОБЩИЙ JS ─────────────────────────
_COMMON_JS = r"""
const P="__P__";
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let TOK=localStorage.getItem("lv_tok")||"";
async function api(path,opt){
  opt=opt||{};
  opt.headers=Object.assign({"x-live-tok":TOK,"x-req":"1"},opt.headers||{});
  if(opt.json!==undefined){opt.method=opt.method||"POST";opt.headers["content-type"]="application/json";opt.body=JSON.stringify(opt.json);}
  let d={};
  try{const r=await fetch(P+"/api"+path,opt);d=await r.json();d._status=r.status;}catch(e){d={ok:false,error:"сеть"};}
  return d;
}
async function ensureId(){
  const d=await api("/register",{method:"POST"});
  if(d.ok){TOK=d.token;localStorage.setItem("lv_tok",TOK);}
  return d;
}
function fmtTime(ts){return new Date(ts*1000).toLocaleString([], {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});}
let ICE=[{urls:"stun:stun.l.google.com:19302"}];
async function loadCfg(){const d=await api("/config");if(d.ok&&d.ice)ICE=d.ice;return d;}
"""

_BASE_CSS = r"""
:root{--bg:#0b0d12;--card:#151922;--line:#252b38;--tx:#e8ecf4;--mut:#8b94a7;--ac:#e0a93a;--red:#e5484d;--grn:#30a46c}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.4 -apple-system,Segoe UI,Roboto,sans-serif}
button,input,select,textarea{font:inherit;color:var(--tx)}
button{background:var(--ac);color:#111;border:0;border-radius:10px;padding:9px 14px;font-weight:600;cursor:pointer}
button.sec{background:#222a38;color:var(--tx)}button.red{background:var(--red);color:#fff}button:disabled{opacity:.45}
input,select,textarea{background:#0f131b;border:1px solid var(--line);border-radius:10px;padding:9px 11px;width:100%}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px;margin:10px 0}
.mut{color:var(--mut);font-size:13px}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.row>*{flex:1}
.tabs{display:flex;gap:6px;overflow-x:auto;padding:8px 10px;position:sticky;top:0;background:var(--bg);z-index:5}
.tabs button{background:#1a202b;color:var(--mut);white-space:nowrap}.tabs button.on{background:var(--ac);color:#111}
main{padding:0 10px 90px;max-width:900px;margin:0 auto}
.stage{position:relative;background:#000;border-radius:14px;overflow:hidden;aspect-ratio:16/9}
.stage video{width:100%;height:100%;object-fit:contain;background:#000}
.off{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--mut);text-align:center;padding:20px}
.badge{position:absolute;top:8px;left:8px;background:var(--red);color:#fff;border-radius:8px;padding:2px 8px;font-size:12px;font-weight:700}
.cnt{position:absolute;top:8px;right:8px;background:#000a;border-radius:8px;padding:2px 8px;font-size:12px}
.snd{position:absolute;left:50%;bottom:12px;transform:translateX(-50%)}
.chat{height:240px;overflow-y:auto;background:#0f131b;border:1px solid var(--line);border-radius:12px;padding:8px;margin:10px 0 6px}
.chat div{margin:3px 0;word-break:break-word}.chat b{color:var(--ac)}.chat .adm b{color:var(--grn)}
.pill{background:#1a202b;border:1px solid var(--line);border-radius:999px;padding:4px 10px;font-size:13px;cursor:pointer}
h1,h2,h3{margin:8px 0}.hide{display:none!important}
table{width:100%;border-collapse:collapse}td,th{padding:6px;border-bottom:1px solid var(--line);text-align:left;font-size:13px}
"""

# ───────────────────────── СТРАНИЦА ЗРИТЕЛЯ ─────────────────────────
_SITE_HTML = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Прямой эфир</title>
<style>""" + _BASE_CSS + r"""
header{display:flex;justify-content:space-between;align-items:center;padding:10px}
#player{position:fixed;inset:0;background:#000e;z-index:20;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:10px}
#player .box{width:100%;max-width:900px}#player iframe,#player video{width:100%;aspect-ratio:16/9;border:0;background:#000;border-radius:12px}
.vcard{cursor:pointer}
</style></head><body>
<header><b id="ttl">📡 Прямой эфир</b><span class="pill" id="badge">ID …</span></header>
<div class="tabs" id="tabs">
 <button data-t="live" class="on">Эфир</button><button data-t="videos">Видео</button>
 <button data-t="events">Расписание</button><button data-t="me">Профиль</button></div>
<main>
<section id="t-live">
 <div class="stage"><video id="v" autoplay playsinline muted></video>
  <div class="off" id="off">Эфир сейчас не идёт.<br>Загляни в «Расписание» — там время следующего.</div>
  <span class="badge hide" id="lb">● LIVE</span><span class="cnt hide" id="cnt"></span>
  <button class="snd hide" id="snd">🔊 Включить звук</button></div>
 <div id="livetitle" class="mut" style="margin-top:6px"></div>
 <div class="chat" id="chat"></div>
 <div class="row"><input id="msg" maxlength="300" placeholder="Сообщение в чат…" style="flex:5"><button id="sendb">➤</button></div>
</section>
<section id="t-videos" class="hide"><div id="vlist" class="mut">Загрузка…</div></section>
<section id="t-events" class="hide"><div id="elist" class="mut">Загрузка…</div></section>
<section id="t-me" class="hide">
 <div class="card"><div class="mut">Твой ID (навсегда, привязан к этому сайту)</div><h1 id="myid">…</h1>
  <input id="nm" maxlength="24" placeholder="Твоё имя в чате"><div style="height:8px"></div><button id="savenm">Сохранить имя</button></div>
 <div class="card"><div class="mut">Код восстановления — сохрани, если боишься потерять ID (очистка браузера, новый телефон)</div>
  <input id="code" readonly><div style="height:8px"></div><button class="sec" id="copycode">Копировать</button>
  <hr style="border:0;border-top:1px solid var(--line);margin:12px 0">
  <div class="mut">Уже есть код? Вставь, чтобы вернуть свой ID:</div>
  <input id="rcode" placeholder="1234567.abcdef…"><div style="height:8px"></div><button class="sec" id="restore">Восстановить</button></div>
</section></main>
<div id="player" class="hide"><div class="box"><div id="pbox"></div><div style="height:10px"></div><button class="sec" id="pclose">Закрыть ✕</button></div></div>
<script>""" + _COMMON_JS + r"""
const v=$("#v");let ws=null,pc=null,pend=[],myCid=null,wantLive=false;
function tab(t){document.querySelectorAll("#tabs button").forEach(b=>b.classList.toggle("on",b.dataset.t===t));
  ["live","videos","events","me"].forEach(x=>$("#t-"+x).classList.toggle("hide",x!==t));
  if(t==="videos")loadVideos();if(t==="events")loadEvents();}
$("#tabs").onclick=e=>{if(e.target.dataset.t)tab(e.target.dataset.t)};
function addChat(m){const d=document.createElement("div");if(m.adm)d.className="adm";d.innerHTML="<b>"+esc(m.name)+":</b> "+esc(m.text);
  const c=$("#chat");const bot=c.scrollTop+c.clientHeight>=c.scrollHeight-40;c.appendChild(d);if(bot)c.scrollTop=c.scrollHeight;}
function setLive(on,title){wantLive=on;$("#lb").classList.toggle("hide",!on);$("#off").classList.toggle("hide",on);
  $("#livetitle").textContent=on?(title||""):"";if(title&&on)$("#ttl").textContent="📡 "+title;
  if(!on){closePc();v.srcObject=null;$("#snd").classList.add("hide");$("#ttl").textContent="📡 Прямой эфир";}
  else if(ws&&ws.readyState===1)ws.send(JSON.stringify({t:"watch"}));}
function closePc(){if(pc){try{pc.close()}catch(e){}pc=null}pend=[]}
function send(o){if(ws&&ws.readyState===1)ws.send(JSON.stringify(o))}
async function onSig(from,d){
  if(d.sdp){closePc();pc=new RTCPeerConnection({iceServers:ICE});
    pc.ontrack=e=>{v.srcObject=e.streams[0];v.play().catch(()=>{});v.muted=true;$("#snd").classList.remove("hide");};
    pc.onicecandidate=e=>{if(e.candidate)send({t:"sig",to:from,data:{ice:e.candidate}})};
    pc.onconnectionstatechange=()=>{if(pc&&(pc.connectionState==="failed"||pc.connectionState==="disconnected")&&wantLive)setTimeout(()=>send({t:"watch"}),1500)};
    await pc.setRemoteDescription(d.sdp);for(const c of pend)await pc.addIceCandidate(c).catch(()=>{});pend=[];
    const a=await pc.createAnswer();await pc.setLocalDescription(a);send({t:"sig",to:from,data:{sdp:pc.localDescription}});}
  else if(d.ice){if(pc&&pc.remoteDescription)await pc.addIceCandidate(d.ice).catch(()=>{});else pend.push(d.ice);}
}
function connect(){
  ws=new WebSocket((location.protocol==="https:"?"wss://":"ws://")+location.host+P+"/ws?tok="+encodeURIComponent(TOK));
  ws.onmessage=async e=>{const m=JSON.parse(e.data);
    if(m.t==="hello"){myCid=m.cid;$("#chat").innerHTML="";(m.chat||[]).forEach(addChat);setLive(m.live,m.title);cnt(m.count)}
    else if(m.t==="live")setLive(m.on,m.title);
    else if(m.t==="count")cnt(m.n);
    else if(m.t==="chat")addChat(m);
    else if(m.t==="sig")onSig(m.from,m.data);};
  ws.onclose=()=>{setTimeout(connect,2000)};
}
function cnt(n){const c=$("#cnt");c.textContent="👁 "+n;c.classList.toggle("hide",!wantLive)}
$("#snd").onclick=()=>{v.muted=false;v.play().catch(()=>{});$("#snd").classList.add("hide")};
function sendChat(){const t=$("#msg").value.trim();if(!t)return;send({t:"chat",text:t});$("#msg").value=""}
$("#sendb").onclick=sendChat;$("#msg").onkeydown=e=>{if(e.key==="Enter")sendChat()};
async function loadVideos(){const d=await api("/videos");const el=$("#vlist");
  if(!d.ok||!d.items.length){el.textContent="Пока нет видео.";return}
  el.innerHTML=d.items.map(x=>'<div class="card vcard" data-id="'+x.id+'"><b>'+esc(x.title)+'</b><div class="mut">'+esc(x.descr)+'</div></div>').join("");
  el.onclick=ev=>{const c=ev.target.closest(".vcard");if(!c)return;const it=d.items.find(z=>z.id==c.dataset.id);openVideo(it)};}
function openVideo(it){const b=$("#pbox");b.innerHTML="";
  if(it.kind==="iframe"){const f=document.createElement("iframe");f.src=it.embed;f.allow="autoplay; fullscreen; picture-in-picture";f.allowFullscreen=true;b.appendChild(f)}
  else if(it.kind==="file"){const x=document.createElement("video");x.src=it.embed;x.controls=true;x.autoplay=true;x.playsInline=true;b.appendChild(x)}
  else{window.open(it.url,"_blank");return}
  $("#player").classList.remove("hide");}
$("#pclose").onclick=()=>{$("#pbox").innerHTML="";$("#player").classList.add("hide")};
async function loadEvents(){const d=await api("/events");const el=$("#elist");
  if(!d.ok||!d.items.length){el.textContent="Пока ничего не запланировано.";return}
  el.innerHTML=d.items.map(x=>'<div class="card"><b>'+esc(x.title)+'</b><div style="color:var(--ac)">'+fmtTime(x.start_ts)+'</div><div class="mut">'+esc(x.descr)+'</div>'+
   (x.link?'<a style="color:var(--ac)" target="_blank" rel="noopener" href="'+esc(x.link)+'">Ссылка</a>':"")+'</div>').join("");}
async function loadMe(){const d=await api("/me");if(!d.ok)return;
  $("#badge").textContent="ID "+d.uid;$("#myid").textContent=d.uid;$("#nm").value=d.name||"";$("#code").value=d.token;}
$("#badge").onclick=()=>tab("me");
$("#savenm").onclick=async()=>{const d=await api("/profile",{json:{name:$("#nm").value}});if(d.ok){send({t:"name"});$("#savenm").textContent="Сохранено ✓";setTimeout(()=>$("#savenm").textContent="Сохранить имя",1500)}};
$("#copycode").onclick=()=>{navigator.clipboard&&navigator.clipboard.writeText($("#code").value);$("#copycode").textContent="Скопировано ✓"};
$("#restore").onclick=async()=>{const d=await api("/restore",{json:{code:$("#rcode").value}});
  if(d.ok){TOK=d.token;localStorage.setItem("lv_tok",TOK);location.reload()}else alert("Неверный код")};
(async()=>{await ensureId();await loadCfg();await loadMe();connect();})();
</script></body></html>"""

# ───────────────────────── КОНСОЛЬ АДМИНА ─────────────────────────
_ADMIN_HTML = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="robots" content="noindex">
<title>Консоль эфира</title><style>""" + _BASE_CSS + r"""
#login{max-width:340px;margin:80px auto;padding:0 14px}
.prev{width:100%;aspect-ratio:16/9;background:#000;border-radius:12px}
label{display:block;margin:8px 0 3px}input[type=range]{padding:0}
</style></head><body>
<div id="login" class="hide"><h2>🔐 Консоль эфира</h2><input id="pw" type="password" placeholder="Пароль"><div style="height:8px"></div>
<button id="go" style="width:100%">Войти</button><div id="lerr" class="mut"></div></div>
<div id="app" class="hide">
<div class="tabs" id="tabs"><button data-t="live" class="on">Эфир</button><button data-t="videos">Видео</button>
<button data-t="events">Расписание</button><button data-t="users">Люди</button><button data-t="out" class="sec">Выйти</button></div>
<main>

<section id="t-live">
 <video id="prev" class="prev" autoplay playsinline muted></video>
 <div class="card">
  <div class="row"><select id="cam"></select><select id="mic"></select></div>
  <div class="row" style="margin-top:8px"><button id="start">▶ Включить камеру и микрофон</button><button id="screen" class="sec">🖥 Экран</button></div>
  <div class="row" style="margin-top:8px"><button id="mcam" class="sec">📷 Камера вкл</button><button id="mmic" class="sec">🎙 Микрофон вкл</button></div>
 </div>
 <div class="card">
  <h3>🎚 Голос <span class="mut">(меняешь только ты)</span></h3>
  <select id="preset"></select>
  <label>Высота голоса (минус = ниже/мужественнее): <b id="pv"></b></label><input id="pitch" type="range" min="-0.8" max="0.8" step="0.01">
  <label>Низы (бас, дБ): <b id="bv"></b></label><input id="bass" type="range" min="-12" max="12" step="1">
  <label>Верха (дБ): <b id="tv"></b></label><input id="treble" type="range" min="-12" max="12" step="1">
  <label><input id="robot" type="checkbox" style="width:auto"> Робот-эффект</label>
  <label><input id="mon" type="checkbox" style="width:auto"> Слышать себя (только в наушниках!)</label>
  <div class="mut" id="vsave"></div>
 </div>
 <div class="card"><input id="title" maxlength="120" placeholder="Название эфира"><div style="height:8px"></div>
  <div class="row"><button id="golive" disabled>🔴 В ЭФИР</button><button id="stop" class="red hide">⏹ Закончить</button></div>
  <div class="mut" id="lstat" style="margin-top:6px">Сначала включи камеру.</div></div>
 <div class="chat" id="chat"></div>
 <div class="row"><input id="msg" maxlength="300" placeholder="Написать в чат…" style="flex:5"><button id="sendb">➤</button></div>
</section>

<section id="t-videos" class="hide">
 <div class="card"><h3>➕ Добавить видео</h3>
  <input id="vt" placeholder="Название"><div style="height:6px"></div><textarea id="vd" rows="2" placeholder="Описание"></textarea><div style="height:6px"></div>
  <input id="vu" placeholder="Ссылка: YouTube / Vimeo / Rutube / прямая на .mp4"><div style="height:6px"></div>
  <button id="vadd">Добавить по ссылке</button>
  <hr style="border:0;border-top:1px solid var(--line);margin:12px 0">
  <div class="mut">Или загрузи файл прямо на сайт (mp4, webm, mov…):</div>
  <input id="vf" type="file" accept="video/*,image/*"><div style="height:6px"></div><button id="vup">Загрузить файл</button>
  <div class="mut" id="vst"></div></div>
 <div id="vlist"></div>
</section>

<section id="t-events" class="hide">
 <div class="card"><h3>➕ Что и когда</h3>
  <input id="et" placeholder="Что будет (название)"><div style="height:6px"></div>
  <input id="ed" type="datetime-local"><div style="height:6px"></div>
  <textarea id="ee" rows="2" placeholder="Описание (необязательно)"></textarea><div style="height:6px"></div>
  <input id="el" placeholder="Ссылка (необязательно)"><div style="height:6px"></div><button id="eadd">Добавить в расписание</button></div>
 <div id="elist"></div>
</section>

<section id="t-users" class="hide"><div class="mut" id="utot"></div><div id="ulist"></div></section>
</main></div>

<script>""" + _COMMON_JS + r"""
function show(t){document.querySelectorAll("#tabs button").forEach(b=>b.classList.toggle("on",b.dataset.t===t));
  ["live","videos","events","users"].forEach(x=>$("#t-"+x).classList.toggle("hide",x!==t));
  if(t==="videos")loadVideos();if(t==="events")loadEvents();if(t==="users")loadUsers();}
$("#tabs").onclick=async e=>{const t=e.target.dataset.t;if(!t)return;
  if(t==="out"){await api("/admin/logout",{method:"POST"});location.reload()}else show(t)};
$("#go").onclick=async()=>{const d=await api("/admin/login",{json:{password:$("#pw").value}});
  if(d.ok)init();else $("#lerr").textContent=d.error||"Ошибка"};
$("#pw").onkeydown=e=>{if(e.key==="Enter")$("#go").click()};

/* ───── ГОЛОС: pitch-shifter (Jungle, delay-modulation) ───── */
function fadeBuf(ctx,act,fade){const l1=act*ctx.sampleRate,l2=(act-2*fade)*ctx.sampleRate,l=l1+l2;
  const b=ctx.createBuffer(1,l,ctx.sampleRate),p=b.getChannelData(0),fl=fade*ctx.sampleRate,i1=fl,i2=l1-fl;
  for(let i=0;i<l1;i++)p[i]=i<i1?Math.sqrt(i/fl):(i>=i2?Math.sqrt(1-(i-i2)/fl):1);for(let i=l1;i<l;i++)p[i]=0;return b}
function delBuf(ctx,act,fade,up){const l1=act*ctx.sampleRate,l2=(act-2*fade)*ctx.sampleRate,l=l1+l2;
  const b=ctx.createBuffer(1,l,ctx.sampleRate),p=b.getChannelData(0);
  for(let i=0;i<l1;i++)p[i]=up?(l1-i)/l:i/l1;for(let i=l1;i<l;i++)p[i]=0;return b}
class Jungle{constructor(ctx){this.ctx=ctx;const dT=0.1,fT=0.05,bT=0.1;this.dT=dT;
  const inp=ctx.createGain(),out=ctx.createGain();this.input=inp;this.output=out;
  const m=[0,1,2,3].map(()=>ctx.createBufferSource());const dn=delBuf(ctx,bT,fT,false),up=delBuf(ctx,bT,fT,true);
  m[0].buffer=dn;m[1].buffer=dn;m[2].buffer=up;m[3].buffer=up;m.forEach(x=>x.loop=true);
  this.g=m.map(()=>ctx.createGain());this.g[2].gain.value=0;this.g[3].gain.value=0;m.forEach((x,i)=>x.connect(this.g[i]));
  this.mg1=ctx.createGain();this.mg2=ctx.createGain();this.g[0].connect(this.mg1);this.g[2].connect(this.mg1);this.g[1].connect(this.mg2);this.g[3].connect(this.mg2);
  const d1=ctx.createDelay(),d2=ctx.createDelay();this.mg1.connect(d1.delayTime);this.mg2.connect(d2.delayTime);
  const f1=ctx.createBufferSource(),f2=ctx.createBufferSource(),fb=fadeBuf(ctx,bT,fT);f1.buffer=fb;f2.buffer=fb;f1.loop=true;f2.loop=true;
  const x1=ctx.createGain(),x2=ctx.createGain();x1.gain.value=0;x2.gain.value=0;f1.connect(x1.gain);f2.connect(x2.gain);
  inp.connect(d1);inp.connect(d2);d1.connect(x1);d2.connect(x2);x1.connect(out);x2.connect(out);
  const t=ctx.currentTime+0.05,t2=t+bT-fT;m[0].start(t);m[1].start(t2);m[2].start(t);m[3].start(t2);f1.start(t);f2.start(t2);this.setDelay(dT)}
 setDelay(d){this.mg1.gain.setTargetAtTime(0.5*d,0,0.01);this.mg2.gain.setTargetAtTime(0.5*d,0,0.01)}
 setPitch(mult){const up=mult>0;this.g[0].gain.value=up?0:1;this.g[1].gain.value=up?0:1;this.g[2].gain.value=up?1:0;this.g[3].gain.value=up?1:0;this.setDelay(this.dT*Math.abs(mult))}}
const PRESETS={normal:{n:"Обычный",pitch:0,bass:0,treble:0,robot:0},
 male:{n:"Мужской",pitch:-0.18,bass:4,treble:-2,robot:0},deep:{n:"Глубокий бас",pitch:-0.35,bass:7,treble:-4,robot:0},
 female:{n:"Женский",pitch:0.22,bass:-2,treble:2,robot:0},child:{n:"Высокий",pitch:0.45,bass:-4,treble:4,robot:0},
 robot:{n:"Робот",pitch:0,bass:0,treble:0,robot:1},custom:{n:"Свой (ползунки)"}};
$("#preset").innerHTML=Object.keys(PRESETS).map(k=>'<option value="'+k+'">'+PRESETS[k].n+'</option>').join("");
let AC=null,jg,dry,wet,bassF,trebF,ringG,robG,dirG,comp,dest,monG,micStream=null,camTrack=null,outStream=null,saveT=0;
function buildAudio(stream){
  AC=new(window.AudioContext||window.webkitAudioContext)();const src=AC.createMediaStreamSource(stream);
  jg=new Jungle(AC);dry=AC.createGain();wet=AC.createGain();const vm=AC.createGain();
  src.connect(dry);src.connect(jg.input);jg.output.connect(wet);dry.connect(vm);wet.connect(vm);
  dirG=AC.createGain();robG=AC.createGain();robG.gain.value=0;ringG=AC.createGain();ringG.gain.value=0;
  const osc=AC.createOscillator();osc.frequency.value=55;osc.connect(ringG.gain);osc.start();
  vm.connect(dirG);vm.connect(ringG);ringG.connect(robG);
  bassF=AC.createBiquadFilter();bassF.type="lowshelf";bassF.frequency.value=180;
  trebF=AC.createBiquadFilter();trebF.type="highshelf";trebF.frequency.value=3500;
  comp=AC.createDynamicsCompressor();dest=AC.createMediaStreamDestination();
  dirG.connect(bassF);robG.connect(bassF);bassF.connect(trebF);trebF.connect(comp);comp.connect(dest);
  monG=AC.createGain();monG.gain.value=0;comp.connect(monG);monG.connect(AC.destination);applyVoice(curVoice());}
function curVoice(){return{preset:$("#preset").value,pitch:+$("#pitch").value,bass:+$("#bass").value,treble:+$("#treble").value,robot:$("#robot").checked?1:0}}
function showVoice(v){$("#preset").value=PRESETS[v.preset]?v.preset:"custom";$("#pitch").value=v.pitch;$("#bass").value=v.bass;$("#treble").value=v.treble;$("#robot").checked=!!v.robot;
  $("#pv").textContent=(+v.pitch).toFixed(2);$("#bv").textContent=v.bass;$("#tv").textContent=v.treble}
function applyVoice(v){showVoice(v);if(!AC)return;const p=Math.abs(v.pitch)<0.02;
  dry.gain.value=p?1:0;wet.gain.value=p?0:1;if(!p)jg.setPitch(v.pitch);
  bassF.gain.value=v.bass;trebF.gain.value=v.treble;dirG.gain.value=v.robot?0:1;robG.gain.value=v.robot?1.4:0}
async function saveVoice(){const v=curVoice();$("#vsave").textContent="Сохраняю…";const d=await api("/admin/voice",{json:v});$("#vsave").textContent=d.ok?"Сохранено на сервере ✓":"Ошибка сохранения"}
function voiceChanged(fromPreset){let v=curVoice();
  if(fromPreset&&v.preset!=="custom"){v=Object.assign({preset:v.preset},PRESETS[v.preset])}else{v.preset="custom"}
  applyVoice(v);clearTimeout(saveT);saveT=setTimeout(saveVoice,500)}
$("#preset").onchange=()=>voiceChanged(true);["pitch","bass","treble","robot"].forEach(i=>$("#"+i).oninput=()=>voiceChanged(false));
$("#mon").onchange=()=>{if(monG)monG.gain.value=$("#mon").checked?1:0};

/* ───── КАМЕРА / МИКРОФОН ───── */
async function listDevices(){const ds=await navigator.mediaDevices.enumerateDevices();
  $("#cam").innerHTML=ds.filter(d=>d.kind==="videoinput").map(d=>'<option value="'+esc(d.deviceId)+'">'+esc(d.label||"Камера")+'</option>').join("");
  $("#mic").innerHTML=ds.filter(d=>d.kind==="audioinput").map(d=>'<option value="'+esc(d.deviceId)+'">'+esc(d.label||"Микрофон")+'</option>').join("")}
async function startMedia(){
  try{
    if(micStream)micStream.getTracks().forEach(t=>t.stop());if(camTrack)camTrack.stop();
    const cam=$("#cam").value,mic=$("#mic").value;
    const s=await navigator.mediaDevices.getUserMedia({video:cam?{deviceId:{exact:cam},width:{ideal:1280},height:{ideal:720}}:{width:{ideal:1280},height:{ideal:720}},
      audio:{deviceId:mic?{exact:mic}:undefined,echoCancellation:false,noiseSuppression:true,autoGainControl:false}});
    micStream=new MediaStream(s.getAudioTracks());camTrack=s.getVideoTracks()[0];
    if(AC){try{AC.close()}catch(e){}}buildAudio(micStream);
    outStream=new MediaStream([camTrack,dest.stream.getAudioTracks()[0]]);
    $("#prev").srcObject=new MediaStream([camTrack]);$("#golive").disabled=false;$("#lstat").textContent="Камера готова. Жми «В ЭФИР».";
    await listDevices();
    for(const p of Object.values(peers)){const sd=p.getSenders().find(x=>x.track&&x.track.kind==="video");if(sd)sd.replaceTrack(camTrack)}
  }catch(e){$("#lstat").textContent="Нет доступа к камере/микрофону: "+e.message+" (нужен HTTPS и разрешение в браузере)"}}
$("#start").onclick=startMedia;
$("#screen").onclick=async()=>{try{const s=await navigator.mediaDevices.getDisplayMedia({video:true});const t=s.getVideoTracks()[0];
  $("#prev").srcObject=new MediaStream([t]);for(const p of Object.values(peers)){const sd=p.getSenders().find(x=>x.track&&x.track.kind==="video");if(sd)sd.replaceTrack(t)}
  t.onended=()=>{if(camTrack){$("#prev").srcObject=new MediaStream([camTrack]);for(const p of Object.values(peers)){const sd=p.getSenders().find(x=>x.track&&x.track.kind==="video");if(sd)sd.replaceTrack(camTrack)}}}}catch(e){}};
$("#mcam").onclick=()=>{if(!camTrack)return;camTrack.enabled=!camTrack.enabled;$("#mcam").textContent=camTrack.enabled?"📷 Камера вкл":"📷 Камера выкл"};
$("#mmic").onclick=()=>{if(!micStream)return;const t=micStream.getAudioTracks()[0];t.enabled=!t.enabled;$("#mmic").textContent=t.enabled?"🎙 Микрофон вкл":"🎙 Микрофон выкл"};

/* ───── ЭФИР (WebRTC от ведущего к зрителям) ───── */
let ws=null,peers={},pend={},isLive=false;
function send(o){if(ws&&ws.readyState===1)ws.send(JSON.stringify(o))}
function addChat(m){const d=document.createElement("div");if(m.adm)d.className="adm";d.innerHTML="<b>"+esc(m.name)+":</b> "+esc(m.text);const c=$("#chat");c.appendChild(d);c.scrollTop=c.scrollHeight}
async function onViewer(cid){
  if(!outStream)return;if(peers[cid]){try{peers[cid].close()}catch(e){}}
  const pc=new RTCPeerConnection({iceServers:ICE});peers[cid]=pc;pend[cid]=[];
  outStream.getTracks().forEach(t=>pc.addTrack(t,outStream));
  pc.onicecandidate=e=>{if(e.candidate)send({t:"sig",to:cid,data:{ice:e.candidate}})};
  pc.onconnectionstatechange=()=>{if(pc.connectionState==="failed"||pc.connectionState==="closed"){try{pc.close()}catch(e){}if(peers[cid]===pc)delete peers[cid]}};
  const o=await pc.createOffer();await pc.setLocalDescription(o);send({t:"sig",to:cid,data:{sdp:pc.localDescription}})}
async function onSig(from,d){const pc=peers[from];if(!pc)return;
  if(d.sdp){await pc.setRemoteDescription(d.sdp);for(const c of(pend[from]||[]))await pc.addIceCandidate(c).catch(()=>{});pend[from]=[]}
  else if(d.ice){if(pc.remoteDescription)await pc.addIceCandidate(d.ice).catch(()=>{});else(pend[from]=pend[from]||[]).push(d.ice)}}
function connectWS(){
  ws=new WebSocket((location.protocol==="https:"?"wss://":"ws://")+location.host+P+"/ws");
  ws.onopen=()=>{send({t:"host"})};
  ws.onmessage=async e=>{const m=JSON.parse(e.data);
    if(m.t==="hello"){$("#chat").innerHTML="";(m.chat||[]).forEach(addChat);if(!m.admin)$("#lstat").textContent="⚠ Сервер не признал тебя админом — перезайди."}
    else if(m.t==="host_ok"){if(isLive)send({t:"live",on:true,title:$("#title").value})}
    else if(m.t==="viewer")onViewer(m.cid);
    else if(m.t==="sig")onSig(m.from,m.data);
    else if(m.t==="count"){if(isLive)$("#lstat").textContent="🔴 В эфире · зрителей: "+m.n}
    else if(m.t==="chat")addChat(m);
    else if(m.t==="kicked"){isLive=false;$("#lstat").textContent="Эфир открыт в другой вкладке."}};
  ws.onclose=()=>{setTimeout(connectWS,2000)}}
$("#golive").onclick=()=>{if(!outStream)return;isLive=true;send({t:"live",on:true,title:$("#title").value});
  $("#golive").classList.add("hide");$("#stop").classList.remove("hide");$("#lstat").textContent="🔴 В эфире"};
$("#stop").onclick=()=>{isLive=false;send({t:"live",on:false});Object.values(peers).forEach(p=>{try{p.close()}catch(e){}});peers={};
  $("#stop").classList.add("hide");$("#golive").classList.remove("hide");$("#lstat").textContent="Эфир завершён."};
function sendChat(){const t=$("#msg").value.trim();if(t){send({t:"chat",text:t});$("#msg").value=""}}
$("#sendb").onclick=sendChat;$("#msg").onkeydown=e=>{if(e.key==="Enter")sendChat()};

/* ───── ВИДЕО ───── */
async function loadVideos(){const d=await api("/admin/videos");const el=$("#vlist");if(!d.ok){return}
  el.innerHTML=d.items.map(x=>'<div class="card"><b>'+esc(x.title)+'</b> <span class="mut">'+(x.active?"":"(скрыто)")+'</span><div class="mut">'+esc(x.url)+'</div>'+
   '<div class="row" style="margin-top:6px"><button class="sec" data-a="tg" data-id="'+x.id+'" data-v="'+(x.active?0:1)+'">'+(x.active?"Скрыть":"Показать")+'</button>'+
   '<button class="red" data-a="del" data-id="'+x.id+'">Удалить</button></div></div>').join("")||'<div class="mut">Пусто.</div>';
  el.onclick=async e=>{const b=e.target.closest("button");if(!b)return;
   if(b.dataset.a==="del"){if(confirm("Удалить видео?"))await api("/admin/video/update",{json:{id:+b.dataset.id,delete:true}})}
   else await api("/admin/video/update",{json:{id:+b.dataset.id,active:+b.dataset.v}});loadVideos()}}
$("#vadd").onclick=async()=>{const d=await api("/admin/video",{json:{title:$("#vt").value,descr:$("#vd").value,url:$("#vu").value}});
  if(d.ok){$("#vt").value=$("#vd").value=$("#vu").value="";loadVideos()}else alert(d.error||"Ошибка")};
$("#vup").onclick=()=>{const f=$("#vf").files[0];if(!f)return alert("Выбери файл");
  const fd=new FormData();fd.append("file",f);fd.append("title",$("#vt").value);fd.append("descr",$("#vd").value);
  const x=new XMLHttpRequest();x.open("POST",P+"/api/admin/upload");x.setRequestHeader("x-req","1");
  x.upload.onprogress=e=>{if(e.lengthComputable)$("#vst").textContent="Загрузка… "+Math.round(e.loaded/e.total*100)+"%"};
  x.onload=()=>{let d={};try{d=JSON.parse(x.responseText)}catch(e){}$("#vst").textContent=d.ok?"Готово ✓":(d.error||"Ошибка");if(d.ok){$("#vt").value=$("#vd").value="";$("#vf").value="";loadVideos()}};
  x.onerror=()=>{$("#vst").textContent="Ошибка сети"};x.send(fd)};

/* ───── РАСПИСАНИЕ ───── */
async function loadEvents(){const d=await api("/admin/events");const el=$("#elist");if(!d.ok)return;
  el.innerHTML=d.items.map(x=>'<div class="card"><b>'+esc(x.title)+'</b> <span class="mut">'+(x.active?"":"(скрыто)")+'</span><div style="color:var(--ac)">'+fmtTime(x.start_ts)+'</div><div class="mut">'+esc(x.descr)+'</div>'+
   '<div class="row" style="margin-top:6px"><button class="sec" data-a="tg" data-id="'+x.id+'" data-v="'+(x.active?0:1)+'">'+(x.active?"Скрыть":"Показать")+'</button>'+
   '<button class="red" data-a="del" data-id="'+x.id+'">Удалить</button></div></div>').join("")||'<div class="mut">Пусто.</div>';
  el.onclick=async e=>{const b=e.target.closest("button");if(!b)return;
   if(b.dataset.a==="del"){if(confirm("Удалить?"))await api("/admin/event/update",{json:{id:+b.dataset.id,delete:true}})}
   else await api("/admin/event/update",{json:{id:+b.dataset.id,active:+b.dataset.v}});loadEvents()}}
$("#eadd").onclick=async()=>{const ts=Math.floor(new Date($("#ed").value).getTime()/1000);
  const d=await api("/admin/event",{json:{title:$("#et").value,descr:$("#ee").value,link:$("#el").value,start_ts:ts}});
  if(d.ok){$("#et").value=$("#ee").value=$("#el").value="";loadEvents()}else alert(d.error||"Ошибка")};

/* ───── ЛЮДИ ───── */
async function loadUsers(){const d=await api("/admin/users");if(!d.ok)return;$("#utot").textContent="Всего пользователей: "+d.total;
  $("#ulist").innerHTML='<table><tr><th>ID</th><th>Имя</th><th>Был</th><th></th></tr>'+d.items.map(u=>'<tr><td>'+u.uid+(u.online?" 🟢":"")+'</td><td>'+esc(u.name)+'</td><td>'+(u.last_seen?fmtTime(u.last_seen):"")+
   '</td><td><button class="'+(u.banned?"sec":"red")+'" data-u="'+u.uid+'" data-b="'+(u.banned?0:1)+'">'+(u.banned?"Разбан":"Бан чата")+'</button></td></tr>').join("")+'</table>';
  $("#ulist").onclick=async e=>{const b=e.target.closest("button");if(!b)return;await api("/admin/ban",{json:{uid:+b.dataset.u,banned:+b.dataset.b}});loadUsers()}}

async function init(){
  $("#login").classList.add("hide");$("#app").classList.remove("hide");await loadCfg();
  const vd=await api("/admin/voice");if(vd.ok)showVoice(vd.voice);
  try{await listDevices()}catch(e){}connectWS()}
(async()=>{const d=await api("/admin/me");if(d.ok)init();else $("#login").classList.remove("hide")})();
</script></body></html>"""


# ───────────────────────── ОТДЕЛЬНЫЙ ЗАПУСК ─────────────────────────
if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    logging.basicConfig(level=logging.INFO)
    _app = FastAPI()
    mount_live(_app)
    uvicorn.run(_app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
