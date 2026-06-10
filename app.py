from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash, Response, make_response
from flask_wtf.csrf import CSRFProtect, CSRFError
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import quote_plus, urlparse
from functools import wraps
import threading
import logging
import sys
import time
import re
import json
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
import sqlite3
import database as db
from database import (ST_AGENDADO, ST_EM_ANDAMENTO, ST_CONCLUIDO,
                      ST_CANCELADO, ST_NAO_COMP, ST_WALKIN)
import os, secrets

# ── Sentry — activo só se SENTRY_DSN estiver definido (produção) ──────────────
_sentry_dsn = os.environ.get("SENTRY_DSN", "")
if _sentry_dsn:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        sentry_sdk.init(dsn=_sentry_dsn, integrations=[FlaskIntegration()],
                        traces_sample_rate=0.05)
    except ImportError:
        pass

app = Flask(__name__)
# PythonAnywhere corre atrás de nginx (proxy reverso) — necessário para HTTPS/CSRF correcto
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
csrf = CSRFProtect()
# Versão dos assets — muda a cada deploy para forçar cache-bust no browser
_ASSET_VER  = int(time.time())
_APP_START  = time.monotonic()   # âncora para calcular uptime da app

# ══════════════════════════════════════════════════════════════
#  CONFIGURAÇÃO DE SEGURANÇA
# ══════════════════════════════════════════════════════════════

app.config['MAX_CONTENT_LENGTH']      = 2 * 1024 * 1024  # 2 MB max upload
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE']   = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=14)
app.config['SESSION_REFRESH_EACH_REQUEST'] = True
app.config['WTF_CSRF_SSL_STRICT']     = False
app.config['WTF_CSRF_TIME_LIMIT']     = None

# ── Secret key ────────────────────────────────────────────────
_key_file = os.path.join(os.path.dirname(__file__), ".secret_key")
if os.environ.get("SECRET_KEY"):
    app.secret_key = os.environ["SECRET_KEY"]
elif os.path.exists(_key_file):
    with open(_key_file) as f:
        app.secret_key = f.read().strip()
else:
    _k = secrets.token_hex(32)
    with open(_key_file, "w") as f:
        f.write(_k)
    os.chmod(_key_file, 0o600)
    app.secret_key = _k

# ── Expose csrf on app so blueprints can use @app.csrf.exempt ─
app.csrf = csrf

# ══════════════════════════════════════════════════════════════
#  IMPORTS DE HELPERS
# ══════════════════════════════════════════════════════════════

from helpers import (
    _log, _blog, _agora, _pc_get, _pc_set, _pc_del, _pcache,
    _push_async, _elog, _JsonFormatter, _make_json_handler,
    _ip_attempts, _ip_lock, _IP_WINDOW,
    _ip_backoff,
    _user_fails, _user_lock, _USER_LOCKOUT,
    _api_calls, _api_lock, _API_WINDOW,
    _pc_evict, _invalidar_idx,
    MOEDAS, _MOEDA_MAP, VOCAB_TIPOS, get_vocab,
)

# ══════════════════════════════════════════════════════════════
#  HEADERS DE SEGURANÇA HTTP
# ══════════════════════════════════════════════════════════════

_CSP_TMPL = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{{nonce}}'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "worker-src 'self'; "
    "manifest-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


@app.before_request
def _gerar_csp_nonce():
    """Gera um nonce criptográfico único por pedido para a CSP."""
    from flask import g
    g.csp_nonce = secrets.token_hex(16)


@app.before_request
def _verificar_plano():
    """Bloqueia staff de barbearias com plano expirado."""
    from flask import g
    g.plano_info = None
    if "user_id" not in session:
        return
    role = session.get("role")
    if role == "root" or session.get("root_gerir"):
        return
    barbearia_id = session.get("barbearia_id")
    if not barbearia_id:
        return
    ep = request.endpoint or ""
    if ep in ("static", "login", "logout", "conta_suspensa"):
        return
    _ck = f"plano:{barbearia_id}:"
    plano = _pc_get(_ck)
    if plano is None:
        plano = db.verificar_plano(barbearia_id)
        _pc_set(_ck, plano, 300)
    g.plano_info = plano
    if plano and not plano.get("sem_limite") and not plano.get("ativo"):
        return redirect(url_for("conta_suspensa"))


@app.after_request
def set_security_headers(response):
    from flask import g
    nonce = getattr(g, "csp_nonce", secrets.token_hex(16))
    csp   = _CSP_TMPL.replace("{{nonce}}", nonce)
    h = response.headers
    h.setdefault('Content-Security-Policy',    csp)
    h.setdefault('X-Content-Type-Options',     'nosniff')
    h.setdefault('X-Frame-Options',            'DENY')
    h.setdefault('Referrer-Policy',            'strict-origin-when-cross-origin')
    h.setdefault('Strict-Transport-Security',  'max-age=63072000; includeSubDomains; preload')
    h.setdefault('Permissions-Policy',
                 'camera=(self), microphone=(), geolocation=(), payment=(), '
                 'usb=(), bluetooth=(), serial=(), hid=()')
    h.pop('Server',       None)
    h.pop('X-Powered-By', None)
    if (response.content_type.startswith('text/html') and
            'user_id' in session):
        h.setdefault('Cache-Control', 'no-store, no-cache, must-revalidate, private')
        h.setdefault('Pragma', 'no-cache')
    elif response.content_type.startswith('application/json'):
        h.setdefault('Cache-Control', 'no-store')
    return response


@app.context_processor
def _inject_csp_nonce():
    """Torna o nonce disponível em todos os templates."""
    from flask import g
    plano_info = None
    if session.get("role") == "chefe" and not session.get("root_gerir"):
        plano_info = getattr(g, "plano_info", None)
    return {
        "csp_nonce":  getattr(g, "csp_nonce", ""),
        "agora_iso":  _agora().strftime("%Y-%m-%dT%H:%M:%S"),
        "plano_info": plano_info,
        "av":         _ASSET_VER,
    }


@app.template_filter("moeda")
def moeda_filter(value):
    try:
        return f"{int(value):,}".replace(",", ".")
    except (ValueError, TypeError):
        return "0"


@app.template_filter("omit_keys")
def omit_keys_filter(lst, *keys):
    """Remove chaves sensíveis de uma lista de dicts antes de serializar para JSON."""
    keys_set = set(keys)
    return [{k: v for k, v in d.items() if k not in keys_set} for d in lst]


@app.template_filter("from_json")
def from_json_filter(value):
    """Deserializa uma string JSON num dict/lista."""
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


@app.template_filter("tel")
def tel_filter(value):
    """Formata número de telefone como xxx xx xx."""
    if not value:
        return ""
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 7:
        return f"{digits[:3]} {digits[3:5]} {digits[5:]}"
    if len(digits) == 8:
        return f"{digits[:4]} {digits[4:6]} {digits[6:]}"
    return str(value)


@app.context_processor
def inject_moeda():
    """Injeta moeda_simbolo em todos os templates."""
    bid_sess = session.get("barbearia_id")
    if bid_sess:
        cod = db.get_config("moeda", bid_sess, "ECV") or "ECV"
    else:
        cod = "ECV"
    return {"moeda_simbolo": _MOEDA_MAP.get(cod, cod)}


@app.context_processor
def inject_vocab():
    """Injeta vocab (vocabulário adaptativo) em todos os templates."""
    bid_sess = session.get("barbearia_id")
    tipo = None
    vocab_custom = None
    if bid_sess:
        b = db.get_barbearia(bid_sess)
        if b:
            tipo = b.get("tipo")
            vocab_custom = b.get("vocab_custom")
    return {"vocab": get_vocab(tipo, vocab_custom), "VOCAB_TIPOS": VOCAB_TIPOS}


# ══════════════════════════════════════════════════════════════
#  REGISTAR BLUEPRINTS
# ══════════════════════════════════════════════════════════════

from blueprints import auth, root, agendamentos, relatorios, barbeiros, cliente, servicos, api, mesa

auth.register(app)
root.register(app)
agendamentos.register(app)
relatorios.register(app)
barbeiros.register(app)
cliente.register(app)
servicos.register(app)
api.register(app)
mesa.register(app)

# pwa blueprint precisa de _APP_START e _indices_prontos — registado mais abaixo


# ══════════════════════════════════════════════════════════════
#  ERROR HANDLERS
# ══════════════════════════════════════════════════════════════

@app.errorhandler(404)
def nao_encontrado(e):
    return render_template("404.html"), 404


@app.errorhandler(413)
def ficheiro_grande(e):
    if (request.is_json or
            request.headers.get("X-Requested-With") == "XMLHttpRequest" or
            request.accept_mimetypes.best == "application/json"):
        return jsonify({"erro": "Ficheiro demasiado grande. Máximo 2 MB."}), 413
    flash("⚠️ Ficheiro demasiado grande. Máximo 2 MB.", "erro")
    ref = request.referrer or ""
    if ref and urlparse(ref).netloc == request.host:
        return redirect(ref)
    return redirect(url_for("index"))


@app.errorhandler(500)
def erro_servidor(e):
    import traceback as _tb
    _log(f"ERRO_500 path={request.path} err={e}")
    _elog.error("500", extra={
        "method": request.method, "path": request.path,
        "exc": _tb.format_exc()[:800]})
    try:
        barbearias = db.listar_barbearias(apenas_ativas=True)
        for _b in barbearias:
            _push_async(_b["id"], "⚠️ Erro 500",
                        f"{request.method} {request.path[:60]}")
    except Exception:
        pass
    return render_template("500.html", path=request.path), 500


@app.errorhandler(RuntimeError)
def _handle_runtime(e):
    """DB_TIMEOUT: lock NFS preso após 3 tentativas (~26s) → 503."""
    if "DB_TIMEOUT" in str(e):
        _log(f"DB_TIMEOUT path={request.path} — lock não obtido após 3 tentativas")
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": "Serviço temporariamente indisponível. Tenta novamente."}), 503
        flash("Serviço temporariamente indisponível. Tenta novamente.", "erro")
        ref = request.referrer or url_for("login")
        if ref and request.host in ref:
            return redirect(ref), 303
        return redirect(url_for("login")), 303
    raise e


@app.errorhandler(OSError)
def _handle_oserror(e):
    """Ignora write errors de clientes que desligaram (BrokenPipe, etc.).
    Acontece quando o CI/reload termina pedidos em voo — não é erro da aplicação."""
    msg = str(e).lower()
    if any(p in msg for p in ("write error", "broken pipe", "connection reset", "epipe")):
        return "", 499   # 499 = client closed request (nginx convention)
    raise e


@app.errorhandler(CSRFError)
def csrf_error(e):
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": False, "error": "Sessão expirada. Recarrega a página."}), 400
    flash("A sessão expirou. Por favor, entra novamente.", "erro")
    return redirect(url_for("login"))


# ══════════════════════════════════════════════════════════════
#  BACKUP AUTOMÁTICO
# ══════════════════════════════════════════════════════════════

def _copy_com_timeout(src, dst, timeout=60):
    """shutil.copy2 com timeout — previne hang indefinido em NFS."""
    import shutil as _shutil
    err = []
    def _do():
        try:
            _shutil.copy2(src, dst)
        except Exception as _e:
            err.append(_e)
    t = threading.Thread(target=_do, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"copy2 bloqueou mais de {timeout}s (NFS?)")
    if err:
        raise err[0]


def _fazer_backup_arranque():
    """Backup diário da BD. Máx. 1× por dia. Aguarda 5 min após arranque.
    Guarda os últimos 14 dias (PA recicla workers várias vezes/dia → ~1 cópia/dia)."""
    import glob
    import sqlite3 as _sqlite3
    _log_bak = logging.getLogger("backup")
    _base   = os.path.dirname(os.path.abspath(__file__))
    bak_dir = os.path.join(_base, "backups")

    time.sleep(300)

    try:
        os.makedirs(bak_dir, exist_ok=True)
        hoje = _agora().strftime("%Y%m%d")
        if glob.glob(os.path.join(bak_dir, f"barbearia_{hoje}_*.db")):
            return
    except Exception:
        return

    for tentativa in range(3):
        try:
            ts   = _agora().strftime("%Y%m%d_%H%M%S")
            dest = os.path.join(bak_dir, f"barbearia_{ts}.db")
            db.backup_db(dest)
            import sqlite3 as _sqlite3
            chk_conn = _sqlite3.connect(dest, timeout=10)
            try:
                resultado = chk_conn.execute("PRAGMA integrity_check").fetchone()
                if resultado[0] != "ok":
                    raise RuntimeError(f"Backup corrompido: {resultado[0]}")
            finally:
                chk_conn.close()
            todos = sorted(glob.glob(os.path.join(bak_dir, "barbearia_*.db")))
            for antigo in todos[:-14]:
                try: os.remove(antigo)
                except OSError: pass
            _log_bak.info(f"Backup criado e verificado: {dest}")
            return
        except Exception as e:
            if tentativa == 2:
                _log_bak.error(f"Backup falhou: {e}")
            else:
                time.sleep(5)


# ══════════════════════════════════════════════════════════════
#  THREAD DE LIMPEZA AUTOMÁTICA
# ══════════════════════════════════════════════════════════════

def _rl_evict():
    """Remove entradas expiradas dos dicionários de rate-limiting."""
    now = time.time()
    with _ip_lock:
        for ip in list(_ip_attempts.keys()):
            _ip_attempts[ip] = [t for t in _ip_attempts[ip] if now - t < _IP_WINDOW]
            if not _ip_attempts[ip]:
                del _ip_attempts[ip]
        for ip in list(_ip_backoff.keys()):
            if now >= _ip_backoff[ip][0]:
                del _ip_backoff[ip]
    with _user_lock:
        for u in list(_user_fails.keys()):
            _user_fails[u] = [t for t in _user_fails[u] if now - t < _USER_LOCKOUT]
            if not _user_fails[u]:
                del _user_fails[u]
    with _api_lock:
        for ip in list(_api_calls.keys()):
            _api_calls[ip] = [t for t in _api_calls[ip] if now - t < _API_WINDOW]
            if not _api_calls[ip]:
                del _api_calls[ip]


# IDs de agendamentos para os quais o lembrete T-1h já foi enviado
_lembretes_enviados: set = set()


def _enviar_lembretes_push():
    """Envia push T-1h para o barbeiro de cada marcação próxima."""
    from helpers import _PUSH_OK, _VAPID_PRIVATE_KEY
    if not _PUSH_OK or not _VAPID_PRIVATE_KEY:
        return
    try:
        barbearias = db.listar_barbearias(apenas_ativas=True)
    except Exception:
        return
    for b in barbearias:
        bid_ = b["id"]
        try:
            agora_b  = _agora(barbearia_id=bid_)
            ini = (agora_b + timedelta(minutes=55)).strftime("%Y-%m-%d %H:%M:%S")
            fim = (agora_b + timedelta(minutes=65)).strftime("%Y-%m-%d %H:%M:%S")
            with db._read() as _conn:
                _rows = _conn.execute(
                    "SELECT a.id, a.cliente, a.barbeiro_id, a.data_hora, s.nome AS servico_nome "
                    "FROM agendamentos a LEFT JOIN servicos s ON s.id=a.servico_id "
                    "WHERE a.barbearia_id=? AND a.status='agendado' AND a.data_hora BETWEEN ? AND ?",
                    (bid_, ini, fim)).fetchall()
            for ag in _rows:
                chave = (ag["id"], "1h")
                if chave in _lembretes_enviados:
                    continue
                _lembretes_enviados.add(chave)
                hora = ag["data_hora"][11:16]
                _push_async(bid_,
                            "🔔 Marcação em 1 hora",
                            f"{ag['cliente']} — {ag['servico_nome'] or 'Corte'} às {hora}",
                            barbeiro_id=ag["barbeiro_id"])
        except Exception:
            pass


def _thread_limpeza():
    """Background: limpa atendimentos 'em_andamento' presos e faz GC de memória.
    Cada operação é separada por time.sleep(0.05) para ceder o GIL à main thread
    e evitar starvar pedidos HTTP que precisem de _CONN_LOCK."""
    _log_lim = logging.getLogger("limpeza")
    _indices_prontos.wait(timeout=180)
    _ciclo = 0
    while True:
        time.sleep(0.05)   # ceder GIL à main thread antes de começar ciclo
        try:
            _pc_evict()
        except Exception as e:
            _log_lim.warning(f"Erro em _pc_evict: {e}")
        time.sleep(0.05)
        try:
            _rl_evict()
        except Exception as e:
            _log_lim.warning(f"Erro em _rl_evict: {e}")
        time.sleep(0.05)
        try:
            db.invalidar_cache_slots()
        except Exception as e:
            _log_lim.warning(f"Erro em invalidar_cache_slots: {e}")
        time.sleep(0.05)
        try:
            _enviar_lembretes_push()
        except Exception as e:
            _log_lim.warning(f"Erro nos lembretes push: {e}")
        if _ciclo % 6 == 0:
            time.sleep(0.05)
            try:
                barbearias = db.listar_barbearias(apenas_ativas=True)
                for b in barbearias:
                    time.sleep(0.02)  # janela entre barbearias para HTTP requests
                    libertados = db.limpar_em_andamento_presos(b["id"], horas=8)
                    if libertados:
                        _invalidar_idx(b["id"])
            except Exception as e:
                _log_lim.warning(f"Erro na limpeza automática: {e}")
        if _ciclo % 288 == 0:
            time.sleep(0.05)
            try:
                db.desativar_planos_expirados()
            except Exception as e:
                _log_lim.warning(f"Erro em desativar_planos_expirados: {e}")
        _ciclo += 1
        time.sleep(5 * 60)


# ══════════════════════════════════════════════════════════════
#  ARRANQUE
# ══════════════════════════════════════════════════════════════

csrf.init_app(app)
db.init_db()

def _migrar_hashes_lentos():
    """Migra scrypt e pbkdf2:sha256:600000 → pbkdf2:sha256:10000."""
    import sqlite3 as _sq, secrets as _sec, string as _str
    _base = os.path.dirname(os.path.abspath(__file__))
    _flag = os.path.join(_base, ".migr2_done")
    _out  = os.path.join(_base, ".migr_tmp")
    if os.path.exists(_flag):
        return
    try:
        _conn = _sq.connect(os.path.join(_base, "barbearia.db"), timeout=30)
        _rows = _conn.execute(
            "SELECT id, nome, username, role, password_hash FROM barbeiros"
            " WHERE password_hash LIKE 'scrypt:%'"
            "    OR password_hash LIKE 'pbkdf2:sha256:600000%'"
            "    OR password_hash LIKE 'pbkdf2:sha256:260000%'"
        ).fetchall()
        open(_flag, "w").close()
        if not _rows:
            _conn.close()
            return
        _chars = _str.ascii_letters + _str.digits
        _out_lines = ["=== Migração hashes lentos → pbkdf2:sha256:10000 ==="]
        for _r in _rows:
            _pw = ''.join(_sec.choice(_chars) for _ in range(14))
            _h  = generate_password_hash(_pw, method="pbkdf2:sha256:10000")
            _conn.execute("UPDATE barbeiros SET password_hash=? WHERE id=?", (_h, _r[0]))
            _out_lines.append(f"  {_r[1]} ({_r[2]}, {_r[3]}) → senha temp: {_pw}")
            logging.getLogger("migr").warning(f"MIGR_HASH id={_r[0]} user={_r[2]} tipo={_r[4][:20]}")
        _conn.commit()
        _conn.close()
        with open(_out, "a") as _f:
            _f.write("\n".join(_out_lines) + "\n")
        logging.getLogger("migr").warning(
            f"Migração hashes concluída: {len(_rows)} conta(s). Ver .migr_tmp")
    except Exception as _e:
        logging.getLogger("migr").error(f"Erro na migração de hashes: {_e}")

_migrar_hashes_lentos()


# ── Pré-aquecimento de templates Jinja2 ──────────────────────────────────────
with app.app_context():
    _tmpl_loader = app.jinja_env.loader
    if _tmpl_loader:
        _tmpl_log = logging.getLogger("tmpl_warmup")
        for _tmpl_name in _tmpl_loader.list_templates():
            try:
                app.jinja_env.get_template(_tmpl_name)
            except Exception as _tmpl_e:
                _tmpl_log.warning(f"Falha ao pré-carregar template {_tmpl_name}: {_tmpl_e}")


# Event que sinaliza quando _garantir_indices() terminou.
_indices_prontos = threading.Event()


def _garantir_indices():
    """Sinaliza que os índices estão prontos.
    init_db() já cria todos os índices necessários — esta função existe apenas
    para sinalizar _indices_prontos sem adquirir _CONN_LOCK no arranque."""
    _indices_prontos.set()


# Register pwa blueprint after _APP_START and _indices_prontos are defined
from blueprints import pwa
pwa.register(app, _APP_START, _indices_prontos)

_t_indices = threading.Thread(target=_garantir_indices, daemon=True, name="indices-bg")
_t_indices.start()
_t_limpeza = threading.Thread(target=_thread_limpeza, daemon=True, name="limpeza-bg")
_t_limpeza.start()
_t_backup = threading.Thread(target=_fazer_backup_arranque, daemon=True, name="backup-arranque")
_t_backup.start()

if __name__ == "__main__":
    _debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=5000, debug=_debug)
