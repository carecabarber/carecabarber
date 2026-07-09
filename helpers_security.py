# helpers_security.py — Logging, rate limiting, auth, push, validação de imagens
# Importa bid, LOGOS_DIR, ALLOWED_LOGO_EXTS de helpers_booking (sem ciclos).

from flask import session, redirect, url_for, request
from functools import wraps
from collections import defaultdict
from collections.abc import Callable
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
    # Stubs para que patch('helpers_security.webpush') funcione em testes
    # sem pywebpush instalado. Nunca são chamados em produção (_PUSH_OK é False).
    def webpush(*a, **kw):  # type: ignore[misc]
        raise RuntimeError("pywebpush não instalado")
    class WebPushException(Exception):  # type: ignore[misc]
        """Stub compatível com pywebpush.WebPushException — aceita response=."""
        def __init__(self, message="", response=None, **kw):
            super().__init__(message)
            self.response = response

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


_PUSH_RETRY_DELAYS = (0, 2, 8)  # segundos entre tentativas (3 total)
_PUSH_EXPIRED_CODES = frozenset((404, 410))  # status que indicam subscrição inválida


def _push_one(sub: dict, payload: str) -> str:
    """Tenta enviar push a uma subscrição com retry + backoff exponencial.

    Retorna:
      "ok"      — envio bem-sucedido
      "expired" — endpoint expirado (404/410), não deve ser retentado
      "failed"  — falhou nas 3 tentativas por outro motivo
    """
    endpoint = sub["endpoint"]
    last_exc = None
    for delay in _PUSH_RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            webpush(
                subscription_info={"endpoint": endpoint,
                                   "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=payload,
                vapid_private_key=_VAPID_PRIVATE_KEY,
                vapid_claims=_VAPID_CLAIMS,
            )
            return "ok"
        except WebPushException as e:
            if e.response and e.response.status_code in _PUSH_EXPIRED_CODES:
                # Subscrição expirada/inválida — sem retry
                return "expired"
            last_exc = e
        except Exception as e:
            last_exc = e
    # Esgotou as tentativas
    _elog.error("push_failed", extra={"endpoint": endpoint[:80], "exc": str(last_exc)})
    return "failed"


def _push_notif(barbearia_id: int, titulo: str, corpo: str, barbeiro_id: int | None = None, url: str = "/") -> None:
    if not _PUSH_OK or not _VAPID_PRIVATE_KEY:
        return
    subs = db.push_listar(barbearia_id, barbeiro_id=barbeiro_id)
    if not subs:
        return
    payload = json.dumps({"titulo": titulo, "corpo": corpo, "url": url})
    invalidas = []
    for sub in subs:
        result = _push_one(sub, payload)
        if result == "expired":
            invalidas.append(sub["endpoint"])
    if invalidas:
        db.push_remover_expiradas(invalidas)


def _push_async(barbearia_id: int, titulo: str, corpo: str, barbeiro_id: int | None = None, url: str = "/") -> None:
    t = threading.Thread(target=_push_notif,
                         args=(barbearia_id, titulo, corpo, barbeiro_id, url),
                         daemon=True)
    t.start()


def _push_espera(entrada: dict, barbearia_id: int) -> None:
    """Push ao cliente da fila de espera quando um slot fica livre.

    Chama de forma assíncrona (thread daemon) — não bloqueia o pedido HTTP.
    Silencioso em caso de erro; o slot já foi marcado como livre na BD.
    """
    def _enviar():
        tel = (entrada.get("telefone") or "").strip()
        if not tel or not _PUSH_OK or not _VAPID_PRIVATE_KEY:
            return
        subs = db.cliente_push_listar_por_tel(tel, barbearia_id)
        if not subs:
            return
        data = entrada.get("data_preferida", "")
        try:
            _barb = db.get_barbearia(barbearia_id)
            slug = _barb.get("slug", "") if _barb else ""
        except Exception:
            slug = ""
        url = f"/cliente/{slug}" if slug else "/"
        payload = json.dumps({
            "titulo": "🎉 Vaga disponível!",
            "corpo":  f"Abriu uma vaga para o dia {data}. Entra na app para marcar!",
            "url":    url,
        })
        invalidas = []
        for sub in subs:
            result = _push_one(sub, payload)
            if result == "expired":
                invalidas.append(sub["endpoint"])
        if invalidas:
            try:
                db.push_remover_expiradas(invalidas)
            except Exception:
                pass

    threading.Thread(target=_enviar, daemon=True,
                     name=f"push-espera-{barbearia_id}").start()


# ── Caminho do ficheiro de alertas críticos ──────────────────
_ALERT_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "alerts_criticos.log")
_log_alertas = logging.getLogger("alertas")


def alerta_critico(titulo: str, detalhe: str = "") -> None:
    """Alerta crítico de sistema — escreve em ficheiro + push para todos os admins.

    Usar para eventos que requerem intervenção humana imediata:
    DB corrompida, integrity_check falhado, disco cheio, etc.

    Não lança excepção — falha silenciosa para não bloquear a app.
    """
    import datetime

    # 1. Ficheiro de alerta local (persiste mesmo sem push)
    try:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        linha = f"[{ts}] CRITICO: {titulo}"
        if detalhe:
            linha += f" | {detalhe}"
        with open(_ALERT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
        _log_alertas.critical("%s | %s", titulo, detalhe)
    except Exception:
        pass

    # 2. Push para todos os admins de todas as barbearias activas
    if not _PUSH_OK or not _VAPID_PRIVATE_KEY:
        return

    def _enviar():
        try:
            barbearias = db.listar_barbearias(apenas_ativas=True)
        except Exception:
            return
        payload = json.dumps({"titulo": f"⚠️ {titulo}", "corpo": detalhe or titulo, "url": "/"})
        invalidas = []
        for barb in barbearias:
            try:
                subs = db.push_listar(barb["id"])
            except Exception:
                continue
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
            try:
                db.push_remover_expiradas(invalidas)
            except Exception:
                pass

    threading.Thread(target=_enviar, daemon=True).start()


# ══════════════════════════════════════════════════════════════
#  LOGGING ESTRUTURADO (JSON)
# ══════════════════════════════════════════════════════════════

_LOG_BUILTIN_KEYS = frozenset((
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName", "asctime",
))


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
        # Inclui todos os campos extra passados via extra={} ou **kwargs
        for k, v in record.__dict__.items():
            if k not in _LOG_BUILTIN_KEYS:
                payload[k] = v
        return json.dumps(payload, ensure_ascii=False, default=str)


def _make_json_handler(stream: object = sys.stderr) -> logging.StreamHandler:
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


def _log(msg: str, **extra) -> None:
    try:
        ip   = request.remote_addr or "?" if request else "?"
        path = request.path if request else "-"
    except RuntimeError:
        ip = path = "?"
    _slog.warning(msg, extra={"ip": ip, "path": path, **extra})


def _blog(evento: str, **kwargs) -> None:
    _blog_logger.info(evento, extra=kwargs)


# ── Trilho de auditoria (acções sensíveis) ─────────────────────────
# Regista QUEM (uid/role), DE ONDE (ip), fez O QUÊ (acção), a QUEM (alvo).
# Vai para o log JSON de segurança E para um ficheiro local append-only,
# para revisão humana rápida sem depender do agregador de logs. Nunca lança.
_AUDIT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auditoria.log")


def _audit(acao: str, alvo: str = "", **extra) -> None:
    """Trilho de auditoria de acções sensíveis (login root, impersonação,
    criar/activar/eliminar estabelecimento, mudança de senha, etc.).

    O actor e o IP são deduzidos da sessão/pedido activos. Falha em silêncio."""
    try:
        uid  = session.get("user_id") if session else None
        role = session.get("role") if session else None
    except RuntimeError:
        uid = role = None
    try:
        ip = (request.remote_addr or "?") if request else "?"
    except RuntimeError:
        ip = "?"
    _slog.warning(f"AUDIT {acao}",
                  extra={"ip": ip, "path": "-", "audit": True, "acao": acao,
                         "alvo": str(alvo), "actor_uid": uid, "actor_role": role, **extra})
    # Ficheiro local append-only (best-effort, para revisão humana directa)
    try:
        import datetime
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        campos = " ".join(f"{k}={v}" for k, v in extra.items())
        linha = (f"[{ts}] {acao} actor={uid}({role}) ip={ip} alvo={alvo}"
                 + (f" {campos}" if campos else ""))
        with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
#  RATE LIMITING
# ══════════════════════════════════════════════════════════════

_ip_attempts: dict  = {}   # legado — estado real em db/rate_limit.py
_ip_lock              = threading.Lock()
_IP_MAX    = 10
_IP_WINDOW = 300

_ip_backoff: dict = {}   # legado

_user_fails: dict = defaultdict(list)
_user_lock            = threading.Lock()
_USER_MAX     = 5
_USER_LOCKOUT = 900

# Carregar falhas persistidas no arranque — sobrevive a restarts do servidor
def _carregar_user_fails() -> None:
    try:
        from db import rate_limit as _rl
        for username, timestamps in _rl.user_fail_load_all(_USER_LOCKOUT).items():
            _user_fails[username].extend(timestamps)
    except Exception:
        pass  # BD não disponível no arranque? cache fica vazio, sem problema

_carregar_user_fails()

_api_calls: dict  = {}   # legado
_api_lock             = threading.Lock()
_API_MAX    = 120
_API_WINDOW = 60


def _api_ok(ip: str) -> bool:
    from db import rate_limit as _rl
    from flask import session as _sess
    if not ip or ip == "?":
        ip = "__unknown__"
    # Chave composta: IP + user_id (quando autenticado) — evita que 1 user bloqueie todos
    uid = _sess.get("user_id") if _sess else None
    chave = f"{ip}:{uid}" if uid else ip
    ok = _rl.api_ok(chave, max_req=_API_MAX, window=_API_WINDOW)
    if not ok:
        _slog.warning("RATE_LIMIT_API", extra={
            "ip": ip,
            "uid": uid,
            "path": request.path if request else "-",
        })
    return ok


def _ip_ok(ip: str) -> bool:
    from db import rate_limit as _rl
    if not ip or ip in ("?", "unknown"):
        ip = "__unknown__"
    return _rl.ip_ok(ip, max_attempts=_IP_MAX, window=_IP_WINDOW)


def _ip_retry_after(ip: str) -> int:
    from db import rate_limit as _rl
    if not ip or ip in ("?", "unknown"):
        ip = "__unknown__"
    return _rl.ip_retry_after(ip)


def _user_locked(username: str) -> bool:
    now = time.time()
    with _user_lock:
        hist = [t for t in _user_fails[username] if now - t < _USER_LOCKOUT]
        _user_fails[username] = hist
        return len(hist) >= _USER_MAX


def _record_fail(username: str) -> None:
    with _user_lock:
        _user_fails[username].append(time.time())
    try:
        from db import rate_limit as _rl
        _rl.user_fail_record(username)
    except Exception:
        pass  # falha silenciosa — o cache em memória garante o funcionamento


def _clear_fails(username: str) -> None:
    with _user_lock:
        _user_fails[username] = []
    try:
        from db import rate_limit as _rl
        _rl.user_fail_clear(username)
    except Exception:
        pass


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


def _salvar_logo(file: object, barbearia_id: int) -> str | None:
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

def staff_required(f: Callable) -> Callable:
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


def chefe_required(f: Callable) -> Callable:
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


def root_required(f: Callable) -> Callable:
    @wraps(f)
    def d(*a, **kw):
        if "user_id" not in session or session.get("role") != "root":
            return redirect(url_for("login"))
        return f(*a, **kw)
    return d


# ══════════════════════════════════════════════════════════════
#  AUTORIZAÇÃO DE AGENDAMENTOS
# ══════════════════════════════════════════════════════════════

def pode_gerir_agendamento(ag: dict | None) -> bool:
    if not ag or ag.get("barbearia_id") != bid():
        _log(f"IDOR_BLOCK ag_id={ag.get('id') if ag else '?'} barbearia={ag.get('barbearia_id') if ag else '?'}")
        return False
    if session.get("role") == "chefe":
        return True
    return ag.get("barbeiro_id") == session.get("user_id")
