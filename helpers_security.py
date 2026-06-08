# helpers_security.py — Logging, rate limiting, auth, push, validação de imagens
# Importa bid, LOGOS_DIR, ALLOWED_LOGO_EXTS de helpers_booking (sem ciclos).

from flask import session, redirect, url_for, request
from functools import wraps
from collections import defaultdict
from html import escape as _html_escape
import threading
import logging
import sys
import time
import json
import os
import secrets

from werkzeug.security import generate_password_hash

import database as db
from helpers_booking import bid, LOGOS_DIR, ALLOWED_LOGO_EXTS

# ── Push notifications (pywebpush) ───────────────────────────────
try:
    from pywebpush import webpush, WebPushException
    _PUSH_OK = True
except ImportError:
    _PUSH_OK = False

# ── PDF (reportlab) ───────────────────────────────────────────────
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    import io as _io
    _PDF_OK = True
except ImportError:
    _PDF_OK = False


# ── Hash pré-computado para mitigação de timing attack no login ──
_DUMMY_HASH = generate_password_hash("__dummy_timing_placeholder__", method="pbkdf2:sha256:10000")


# ── VAPID keys para Web Push ────────────────────────────────────
_VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
_VAPID_PUBLIC_KEY  = os.environ.get("VAPID_PUBLIC_KEY",
    "BLj4hePnB09gzgmImA3M02SvlW9H9iyk35NuB85ykeodf1so1YDZuTGdG5P7r_5mCDwrS3GBrYAdDAJfyVi0Law")
_VAPID_CLAIMS      = {"sub": "mailto:carecabarber@gmail.com"}


def _push_notif(barbearia_id, titulo, corpo, barbeiro_id=None, url="/"):
    if not _PUSH_OK or not _VAPID_PRIVATE_KEY:
        return
    subs = db.push_listar(barbearia_id, barbeiro_id=barbeiro_id)
    if not subs:
        return
    payload = json.dumps({"titulo": titulo, "corpo": corpo, "url": url})
    invalidas = []
    for sub in subs:
        try:
            webpush(
                subscription_info={"endpoint": sub["endpoint"],
                                   "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=payload,
                vapid_private_key=_VAPID_PRIVATE_KEY,
                vapid_claims=_VAPID_CLAIMS,
            )
        except WebPushException as e:
            if e.response and e.response.status_code in (404, 410):
                invalidas.append(sub["endpoint"])
        except Exception:
            pass
    if invalidas:
        db.push_remover_expiradas(invalidas)


def _push_async(barbearia_id, titulo, corpo, barbeiro_id=None, url="/"):
    t = threading.Thread(target=_push_notif,
                         args=(barbearia_id, titulo, corpo, barbeiro_id, url),
                         daemon=True)
    t.start()


# ══════════════════════════════════════════════════════════════
#  LOGGING ESTRUTURADO (JSON)
# ══════════════════════════════════════════════════════════════

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts":     self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k not in logging.LogRecord.__init__.__code__.co_varnames and \
               k not in ("msg","args","levelname","levelno","pathname","filename",
                         "module","exc_info","exc_text","stack_info","lineno",
                         "funcName","created","msecs","relativeCreated","thread",
                         "threadName","processName","process","name","message",
                         "taskName","asctime"):
                payload[k] = v
        return json.dumps(payload, ensure_ascii=False, default=str)


def _make_json_handler(stream=sys.stderr) -> logging.StreamHandler:
    h = logging.StreamHandler(stream)
    h.setFormatter(_JsonFormatter())
    return h


_slog = logging.getLogger("security")
_slog.setLevel(logging.WARNING)
if not _slog.handlers:
    _slog.addHandler(_make_json_handler())
    _slog.propagate = False

_blog_logger = logging.getLogger("business")
_blog_logger.setLevel(logging.INFO)
if not _blog_logger.handlers:
    _blog_logger.addHandler(_make_json_handler())
    _blog_logger.propagate = False

_elog = logging.getLogger("errors")
_elog.setLevel(logging.ERROR)
if not _elog.handlers:
    _elog.addHandler(_make_json_handler())
    _elog.propagate = False


def _log(msg: str, **extra):
    try:
        ip   = request.remote_addr or "?" if request else "?"
        path = request.path if request else "-"
    except RuntimeError:
        ip = path = "?"
    _slog.warning(msg, extra={"ip": ip, "path": path, **extra})


def _blog(evento: str, **kwargs):
    _blog_logger.info(evento, extra=kwargs)


# ══════════════════════════════════════════════════════════════
#  RATE LIMITING
# ══════════════════════════════════════════════════════════════

_ip_attempts: dict  = defaultdict(list)
_ip_lock              = threading.Lock()
_IP_MAX    = 10
_IP_WINDOW = 300

_ip_backoff: dict = {}

_user_fails: dict = defaultdict(list)
_user_lock            = threading.Lock()
_USER_MAX     = 5
_USER_LOCKOUT = 900

_api_calls: dict  = defaultdict(list)
_api_lock             = threading.Lock()
_API_MAX    = 120
_API_WINDOW = 60


def _ip_ok(ip: str) -> bool:
    if not ip or ip in ("?", "unknown"):
        ip = "__unknown__"
    now = time.time()
    with _ip_lock:
        if ip in _ip_backoff:
            ate, nivel = _ip_backoff[ip]
            if now < ate:
                return False
            del _ip_backoff[ip]
        hist = [t for t in _ip_attempts[ip] if now - t < _IP_WINDOW]
        _ip_attempts[ip] = hist
        if len(hist) >= _IP_MAX:
            nivel_atual = _ip_backoff.get(ip, (0, 0))[1] if ip in _ip_backoff else 0
            nivel_novo  = nivel_atual + 1
            espera      = min(30 * (2 ** nivel_atual), 1800)
            _ip_backoff[ip] = (now + espera, nivel_novo)
            return False
        _ip_attempts[ip].append(now)
    return True


def _ip_retry_after(ip: str) -> int:
    if not ip or ip in ("?", "unknown"):
        ip = "__unknown__"
    with _ip_lock:
        if ip in _ip_backoff:
            ate, _ = _ip_backoff[ip]
            return max(0, int(ate - time.time()))
    return 0


def _api_ok(ip: str) -> bool:
    if not ip or ip == "?":
        ip = "__unknown__"
    now = time.time()
    with _api_lock:
        hist = [t for t in _api_calls[ip] if now - t < _API_WINDOW]
        _api_calls[ip] = hist
        if len(hist) >= _API_MAX:
            return False
        _api_calls[ip].append(now)
    return True


def _user_locked(username: str) -> bool:
    now = time.time()
    with _user_lock:
        hist = [t for t in _user_fails[username] if now - t < _USER_LOCKOUT]
        _user_fails[username] = hist
        return len(hist) >= _USER_MAX


def _record_fail(username: str):
    with _user_lock:
        _user_fails[username].append(time.time())


def _clear_fails(username: str):
    with _user_lock:
        _user_fails[username] = []


# ══════════════════════════════════════════════════════════════
#  VALIDAÇÃO DE UPLOADS (magic bytes)
# ══════════════════════════════════════════════════════════════

_IMG_MAGIC = [
    b'\xff\xd8\xff',
    b'\x89PNG\r\n\x1a\n',
    b'GIF87a', b'GIF89a',
]


def _mime_ok(file_bytes: bytes) -> bool:
    for magic in _IMG_MAGIC:
        if file_bytes.startswith(magic):
            return True
    if file_bytes[:4] == b'RIFF' and file_bytes[8:12] == b'WEBP':
        return True
    return False


def _salvar_logo(file, barbearia_id):
    if not file or not file.filename:
        return None
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_LOGO_EXTS:
        return None
    header = file.read(12)
    file.seek(0)
    if not _mime_ok(header):
        _log(f"UPLOAD_BLOQUEADO ext={ext} barbearia={barbearia_id}")
        return None
    os.makedirs(LOGOS_DIR, exist_ok=True)
    for f in os.listdir(LOGOS_DIR):
        if f.startswith(f"logo_{barbearia_id}."):
            try:
                os.remove(os.path.join(LOGOS_DIR, f))
            except OSError:
                pass
    filename = f"logo_{barbearia_id}.{ext}"
    file.save(os.path.join(LOGOS_DIR, filename))
    return filename


_FOTO_MIME_OK   = {"image/jpeg", "image/png", "image/webp"}
_FOTO_MAX_BYTES = 2 * 1024 * 1024

_FOTO_MAGIC = [
    (b'\xff\xd8\xff',       "image/jpeg"),
    (b'\x89PNG\r\n\x1a\n', "image/png"),
    (b'RIFF',               "image/webp"),
]


def _validar_imagem(dados: bytes, mime: str) -> bool:
    for magic, mime_ok in _FOTO_MAGIC:
        if dados[:len(magic)] == magic:
            if mime_ok == "image/webp" and dados[8:12] != b'WEBP':
                continue
            return mime == mime_ok
    return False


# ══════════════════════════════════════════════════════════════
#  DECORADORES DE AUTENTICAÇÃO
# ══════════════════════════════════════════════════════════════

def staff_required(f):
    @wraps(f)
    def d(*a, **kw):
        if "user_id" not in session:
            return redirect(url_for("login"))
        role = session.get("role")
        if role == "root":
            return redirect(url_for("root_dashboard"))
        if role == "cliente":
            barbearia_id_ = bid()
            if not barbearia_id_:
                session.clear()
                return redirect(url_for("login"))
            barbearia = db.get_barbearia(barbearia_id_)
            if not barbearia or not barbearia.get("slug"):
                session.clear()
                return redirect(url_for("login"))
            return redirect(url_for("cliente_home", slug=barbearia["slug"]))
        if role in ("chefe", "barbeiro") and not session.get("barbearia_id"):
            session.clear()
            return redirect(url_for("login"))
        return f(*a, **kw)
    return d


def chefe_required(f):
    @wraps(f)
    def d(*a, **kw):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "chefe":
            return redirect(url_for("index"))
        if not session.get("barbearia_id"):
            session.clear()
            return redirect(url_for("login"))
        return f(*a, **kw)
    return d


def root_required(f):
    @wraps(f)
    def d(*a, **kw):
        if "user_id" not in session or session.get("role") != "root":
            return redirect(url_for("login"))
        return f(*a, **kw)
    return d


# ══════════════════════════════════════════════════════════════
#  AUTORIZAÇÃO DE AGENDAMENTOS
# ══════════════════════════════════════════════════════════════

def pode_gerir_agendamento(ag):
    if not ag or ag.get("barbearia_id") != bid():
        _log(f"IDOR_BLOCK ag_id={ag.get('id') if ag else '?'} barbearia={ag.get('barbearia_id') if ag else '?'}")
        return False
    if session.get("role") == "chefe":
        return True
    return ag.get("barbeiro_id") == session.get("user_id")
