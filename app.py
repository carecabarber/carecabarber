from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from flask_wtf.csrf import CSRFProtect
from datetime import datetime, timedelta
from urllib.parse import quote_plus
from functools import wraps
from collections import defaultdict
import threading
import logging
import time
import re
from werkzeug.security import check_password_hash, generate_password_hash
import database as db
import os, secrets

app = Flask(__name__)
csrf = CSRFProtect()
_booking_lock = threading.Lock()

# ══════════════════════════════════════════════════════════════
#  CONFIGURAÇÃO DE SEGURANÇA
# ══════════════════════════════════════════════════════════════

app.config['MAX_CONTENT_LENGTH']      = 2 * 1024 * 1024  # 2 MB max upload
app.config['SESSION_COOKIE_HTTPONLY'] = True    # cookie inacessível ao JS
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # bloqueia CSRF cross-site
app.config['SESSION_COOKIE_SECURE']   = True    # cookie só via HTTPS
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)

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

# ── Constantes de validação ───────────────────────────────────
DIAS_PT           = ["Segunda","Terça","Quarta","Quinta","Sexta","Sábado","Domingo"]
LOGOS_DIR         = os.path.join(os.path.dirname(__file__), "static", "logos")
ALLOWED_LOGO_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}

_MAX_NOME    = 100   # comprimento máximo de nomes
_MAX_TEL     = 20    # comprimento máximo de telefone
_MAX_USERNAME = 50   # comprimento máximo de username
_MAX_MOTIVO  = 300   # comprimento máximo de motivos/notas

# Hash pré-computado para mitigação de timing attack no login
# (garante que check_password_hash corre mesmo quando username não existe)
_DUMMY_HASH = generate_password_hash("__dummy_timing_placeholder__")

_DATA_RE  = re.compile(r'^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$')
_HORA_RE  = re.compile(r'^(?:[01]\d|2[0-3]):[0-5]\d$')
_USER_RE  = re.compile(r'^[a-zA-Z0-9_.-]{3,50}$')   # username seguro
_TEL_RE   = re.compile(r'^[\d\s\+\-\(\)]{7,20}$')    # telefone básico

# ── Magic bytes para validação de imagens ─────────────────────
_IMG_MAGIC = [
    b'\xff\xd8\xff',           # JPEG
    b'\x89PNG\r\n\x1a\n',      # PNG
    b'GIF87a', b'GIF89a',      # GIF
]

# ══════════════════════════════════════════════════════════════
#  LOGGING DE SEGURANÇA
# ══════════════════════════════════════════════════════════════

_log_file = os.path.join(os.path.dirname(__file__), "security.log")
logging.basicConfig(
    filename=_log_file,
    level=logging.WARNING,
    format='%(asctime)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
_slog = logging.getLogger("security")


def _log(msg: str):
    ip = (request.remote_addr or "?") if request else "?"
    _slog.warning(f"[{ip}] {msg}")


# ══════════════════════════════════════════════════════════════
#  RATE LIMITING  (sem dependências externas)
# ══════════════════════════════════════════════════════════════

# — por IP —
_ip_attempts: dict  = defaultdict(list)
_IP_MAX    = 10    # tentativas por janela
_IP_WINDOW = 300   # 5 minutos

# — por username —
_user_fails: dict = defaultdict(list)
_USER_MAX     = 5
_USER_LOCKOUT = 900   # 15 minutos de bloqueio


def _ip_ok(ip: str) -> bool:
    now  = time.time()
    hist = [t for t in _ip_attempts[ip] if now - t < _IP_WINDOW]
    _ip_attempts[ip] = hist
    if len(hist) >= _IP_MAX:
        return False
    _ip_attempts[ip].append(now)
    return True


def _user_locked(username: str) -> bool:
    now  = time.time()
    hist = [t for t in _user_fails[username] if now - t < _USER_LOCKOUT]
    _user_fails[username] = hist
    return len(hist) >= _USER_MAX


def _record_fail(username: str):
    _user_fails[username].append(time.time())


def _clear_fails(username: str):
    _user_fails[username] = []


# ══════════════════════════════════════════════════════════════
#  HEADERS DE SEGURANÇA HTTP
# ══════════════════════════════════════════════════════════════

# CSP: permite scripts inline (necessário pelos templates atuais)
# mas bloqueia recursos externos não autorizados
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


@app.after_request
def set_security_headers(response):
    h = response.headers
    h.setdefault('Content-Security-Policy',    _CSP)
    h.setdefault('X-Content-Type-Options',     'nosniff')
    h.setdefault('X-Frame-Options',            'DENY')
    h.setdefault('X-XSS-Protection',           '1; mode=block')
    h.setdefault('Referrer-Policy',            'strict-origin-when-cross-origin')
    h.setdefault('Strict-Transport-Security',  'max-age=31536000; includeSubDomains')
    h.setdefault('Permissions-Policy',
                 'camera=(), microphone=(), geolocation=(), payment=()')
    # Remover headers que revelam stack tecnológico
    h.pop('Server',       None)
    h.pop('X-Powered-By', None)
    return response


# ══════════════════════════════════════════════════════════════
#  HELPERS DE VALIDAÇÃO
# ══════════════════════════════════════════════════════════════

def _val_data(v: str) -> bool:
    return bool(v and _DATA_RE.match(v))


def _val_hora(v: str) -> bool:
    return bool(v and _HORA_RE.match(v))


def _limpar(v: str, maxlen: int = _MAX_NOME) -> str:
    return (v or "").strip()[:maxlen]


# ══════════════════════════════════════════════════════════════
#  VALIDAÇÃO DE UPLOADS (magic bytes)
# ══════════════════════════════════════════════════════════════

def _mime_ok(file_bytes: bytes) -> bool:
    for magic in _IMG_MAGIC:
        if file_bytes.startswith(magic):
            return True
    if file_bytes[:4] == b'RIFF' and file_bytes[8:12] == b'WEBP':
        return True   # WEBP
    return False


def _salvar_logo(file, barbearia_id):
    """Salva logo com validação de extensão + magic bytes. Devolve filename ou None."""
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


# ══════════════════════════════════════════════════════════════
#  HELPERS DE SESSÃO
# ══════════════════════════════════════════════════════════════

def bid():
    return session.get("barbearia_id")


@app.template_filter("moeda")
def moeda_filter(value):
    try:
        return f"{int(value):,}".replace(",", ".")
    except (ValueError, TypeError):
        return "0"


# ══════════════════════════════════════════════════════════════
#  DECORADORES
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
#  LOGIN / LOGOUT
# ══════════════════════════════════════════════════════════════

@app.route("/login", methods=["GET","POST"])
def login():
    erro = None
    if request.method == "POST":
        ip       = request.remote_addr or "unknown"
        username = _limpar(request.form.get("username",""), _MAX_USERNAME)
        senha    = request.form.get("senha","")

        # 1. Verificar rate limit por IP
        if not _ip_ok(ip):
            _log(f"RATE_LIMIT_IP username={username}")
            erro = "Demasiadas tentativas. Aguarda 5 minutos e tenta novamente."

        # 2. Verificar bloqueio por username
        elif _user_locked(username):
            _log(f"USER_LOCKED username={username}")
            erro = "Conta temporariamente bloqueada. Aguarda 15 minutos."

        else:
            staff = db.get_barbeiro_por_username(username)

            # Mitigação de timing attack: sempre corre check_password_hash
            # mesmo quando o username não existe — resposta em tempo constante
            if staff:
                senha_ok = db.verificar_senha(staff, senha)
            else:
                check_password_hash(_DUMMY_HASH, senha)  # consumir tempo igual
                senha_ok = False

            if senha_ok:
                _clear_fails(username)
                session.clear()             # elimina sessão anterior
                session.permanent = True
                session.update({
                    "user_id":      staff["id"],
                    "user_nome":    staff["nome"],
                    "role":         staff["role"],
                    "barbearia_id": staff["barbearia_id"],
                })
                if staff["role"] == "root":
                    return redirect(url_for("root_dashboard"))
                return redirect(url_for("index"))
            else:
                _record_fail(username)
                _log(f"LOGIN_FAIL username={username}")
                erro = "Utilizador ou senha incorretos."

    return render_template("login.html", erro=erro)


@app.route("/logout")
def logout():
    era_cliente  = session.get("role") == "cliente"
    barbearia_id = session.get("barbearia_id")
    session.clear()
    if era_cliente and barbearia_id:
        barbearia = db.get_barbearia(barbearia_id)
        if barbearia and barbearia.get("slug"):
            return redirect(url_for("cliente_entrada", slug=barbearia["slug"]))
    return redirect(url_for("login"))


# ══════════════════════════════════════════════════════════════
#  ROOT DASHBOARD
# ══════════════════════════════════════════════════════════════

@app.route("/root")
@root_required
def root_dashboard():
    barbearias = db.listar_barbearias()
    erro = request.args.get("erro")
    ok   = request.args.get("ok")
    return render_template("root.html", barbearias=barbearias, erro=erro, ok=ok)


@app.route("/root/criar", methods=["POST"])
@root_required
def root_criar_barbearia():
    nome       = _limpar(request.form.get("nome",""))
    chefe_nome = _limpar(request.form.get("chefe_nome",""))
    username   = _limpar(request.form.get("username",""), _MAX_USERNAME).lower()
    senha      = request.form.get("senha","").strip()

    if not nome or not chefe_nome or not username or not senha:
        return redirect(url_for("root_dashboard") + "?erro=" + quote_plus("Preenche todos os campos."))
    if not _USER_RE.match(username):
        return redirect(url_for("root_dashboard") + "?erro=" + quote_plus("Username inválido. Usa apenas letras, números, _ ou ."))
    if len(senha) < 6:
        return redirect(url_for("root_dashboard") + "?erro=" + quote_plus("Senha deve ter pelo menos 6 caracteres."))
    if db.username_existe(username):
        return redirect(url_for("root_dashboard") + "?erro=" + quote_plus("Username já existe, escolhe outro."))

    barbearia_id = db.criar_barbearia(nome)
    db.criar_chefe(chefe_nome, username, senha, barbearia_id)
    logo = request.files.get("logo")
    if logo and logo.filename:
        filename = _salvar_logo(logo, barbearia_id)
        if filename:
            db.set_logo(barbearia_id, filename)
    return redirect(url_for("root_dashboard") + "?ok=" + quote_plus(f"Barbearia {nome} criada com sucesso!"))


@app.route("/root/toggle/<int:id>", methods=["POST"])
@root_required
def root_toggle_barbearia(id):
    db.toggle_barbearia(id)
    return redirect(url_for("root_dashboard"))


@app.route("/root/editar/<int:id>", methods=["POST"])
@root_required
def root_editar_barbearia(id):
    nome = _limpar(request.form.get("nome",""))
    if nome:
        db.editar_barbearia(id, nome)
    return redirect(url_for("root_dashboard"))


@app.route("/root/logo/<int:id>", methods=["POST"])
@root_required
def root_logo_barbearia(id):
    logo = request.files.get("logo")
    if logo and logo.filename:
        filename = _salvar_logo(logo, id)
        if filename:
            db.set_logo(id, filename)
    return redirect(url_for("root_dashboard"))


@app.route("/root/alterar-senha", methods=["POST"])
@root_required
def root_alterar_senha():
    atual    = request.form.get("senha_atual","")
    nova     = request.form.get("senha_nova","")
    confirma = request.form.get("senha_confirma","")
    root     = db.get_barbeiro(session["user_id"])
    if not db.verificar_senha(root, atual):
        return redirect(url_for("root_dashboard") + "?erro=" + quote_plus("Senha atual incorreta."))
    if nova != confirma:
        return redirect(url_for("root_dashboard") + "?erro=" + quote_plus("As novas senhas não coincidem."))
    if len(nova) < 6:
        return redirect(url_for("root_dashboard") + "?erro=" + quote_plus("Senha deve ter pelo menos 6 caracteres."))
    db.alterar_senha(session["user_id"], nova)
    return redirect(url_for("root_dashboard") + "?ok=" + quote_plus("Senha alterada com sucesso."))


@app.route("/root/gerir/<int:id>")
@root_required
def root_gerir_barbearia(id):
    barbearia = db.get_barbearia(id)
    if not barbearia:
        return redirect(url_for("root_dashboard"))
    session["barbearia_id"] = id
    session["role"]         = "chefe"
    session["root_gerir"]   = True
    return redirect(url_for("index"))


@app.route("/root/sair-barbearia")
def root_sair_barbearia():
    if not session.get("root_gerir"):
        return redirect(url_for("login"))
    session["role"]         = "root"
    session["barbearia_id"] = None
    session.pop("root_gerir", None)
    return redirect(url_for("root_dashboard"))


# ══════════════════════════════════════════════════════════════
#  ÁREA DO CLIENTE
# ══════════════════════════════════════════════════════════════

@app.route("/cliente/<slug>", methods=["GET","POST"])
def cliente_entrada(slug):
    barbearia = db.get_barbearia_por_slug(slug)
    if not barbearia or not barbearia["ativa"]:
        return render_template("404.html"), 404
    barbearia_id = barbearia["id"]
    erro = None
    if request.method == "POST":
        nome = _limpar(request.form.get("nome",""))
        tel  = _limpar(request.form.get("telefone",""), _MAX_TEL)

        if not nome or not tel:
            erro = "Preenche o teu nome e telemóvel."
        elif len(nome) < 2:
            erro = "Nome demasiado curto."
        elif not _TEL_RE.match(tel):
            erro = "Número de telemóvel inválido."
        else:
            session.clear()
            session.permanent = True
            session.update({
                "user_nome":    nome,
                "role":         "cliente",
                "telefone":     tel,
                "barbearia_id": barbearia_id,
            })
            return redirect(url_for("cliente_home", slug=slug))
    return render_template("cliente_entrada.html", erro=erro, barbearia=barbearia)


@app.route("/cliente/<slug>/area")
def cliente_home(slug):
    barbearia = db.get_barbearia_por_slug(slug)
    if not barbearia:
        return render_template("404.html"), 404
    barbearia_id = barbearia["id"]
    if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia_id:
        return redirect(url_for("cliente_entrada", slug=slug))
    agendamentos = [enriquecer(a) for a in db.listar_por_telefone(session.get("telefone",""), barbearia_id)]
    return render_template("cliente_home.html", agendamentos=agendamentos, barbearia=barbearia)


@app.route("/cliente/<slug>/marcar", methods=["GET","POST"])
def cliente_marcar(slug):
    barbearia = db.get_barbearia_por_slug(slug)
    if not barbearia:
        return render_template("404.html"), 404
    barbearia_id = barbearia["id"]
    if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia_id:
        return redirect(url_for("cliente_entrada", slug=slug))
    servicos  = db.listar_servicos(barbearia_id)
    barbeiros = db.listar_barbeiros(barbearia_id, incluir_chefe=True)
    erro = None
    if request.method == "POST":
        try:
            sid = int(request.form.get("servico_id", 0))
        except (ValueError, TypeError):
            sid = 0
        bid_  = request.form.get("barbeiro_id") or None
        data  = _limpar(request.form.get("data",""), 10)
        hora  = _limpar(request.form.get("hora",""), 5)

        if not sid or not data or not hora:
            erro = "Preenche todos os campos obrigatórios."
        elif not _val_data(data) or not _val_hora(hora):
            erro = "Data ou hora inválida."
        else:
            dh = f"{data} {hora}:00"
            s  = db.servico_por_id(sid)
            if not s or s.get("barbearia_id") != barbearia_id:
                erro = "Serviço inválido."
            elif bid_:
                aus = db.ausencia_ativa(bid_, data, hora)
                if aus:
                    erro = f"{aus['barbeiro_nome']} está indisponível. Escolhe outro barbeiro ou data."
                else:
                    livre, conflito = db.verificar_disponibilidade(bid_, dh, s["duracao_min"], barbearia_id)
                    if not livre:
                        erro = f"Já existe marcação às {conflito['data_hora'][11:16]}. Escolhe outro horário."
            if not erro:
                with _booking_lock:
                    if bid_:
                        livre, conflito = db.verificar_disponibilidade(bid_, dh, s["duracao_min"], barbearia_id)
                        if not livre:
                            erro = f"Já existe marcação às {conflito['data_hora'][11:16]}. Escolhe outro horário."
                    if not erro:
                        novo_id = db.criar_agendamento(
                            session.get("user_nome",""), sid, dh, barbearia_id,
                            bid_, "agendado", 0, session.get("telefone"))
                        return redirect(url_for("cliente_confirmacao", slug=slug, id=novo_id))
    hoje = datetime.now().strftime("%Y-%m-%d")
    return render_template("cliente_marcar.html", servicos=servicos, barbeiros=barbeiros,
                           hoje=hoje, agora=datetime.now().strftime("%H:%M"),
                           erro=erro, barbearia=barbearia)


@app.route("/cliente/<slug>/confirmacao/<int:id>")
def cliente_confirmacao(slug, id):
    barbearia = db.get_barbearia_por_slug(slug)
    if not barbearia:
        return render_template("404.html"), 404
    barbearia_id = barbearia["id"]
    if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia_id:
        return redirect(url_for("cliente_entrada", slug=slug))
    ag = db.get_agendamento(id)
    if not ag or ag["telefone"] != session.get("telefone") or ag["barbearia_id"] != barbearia_id:
        return redirect(url_for("cliente_home", slug=slug))
    s = db.servico_por_id(ag["servico_id"])
    b = db.get_barbeiro(ag["barbeiro_id"])
    return render_template("cliente_confirmacao.html", ag=ag, servico=s,
                           barbeiro=b, barbearia=barbearia)


@app.route("/cliente/<slug>/cancelar/<int:id>", methods=["POST"])
def cliente_cancelar(slug, id):
    barbearia = db.get_barbearia_por_slug(slug)
    if not barbearia:
        return redirect(url_for("login"))
    barbearia_id = barbearia["id"]
    if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia_id:
        return redirect(url_for("cliente_entrada", slug=slug))
    ag = db.get_agendamento(id)
    if (ag and ag["telefone"] == session.get("telefone")
            and ag["barbearia_id"] == barbearia_id
            and ag["status"] == "agendado"):
        db.cancelar_agendamento(id)
    return redirect(url_for("cliente_home", slug=slug))


@app.route("/cliente/<slug>/reagendar/<int:id>", methods=["GET","POST"])
def cliente_reagendar(slug, id):
    barbearia = db.get_barbearia_por_slug(slug)
    if not barbearia:
        return render_template("404.html"), 404
    barbearia_id = barbearia["id"]
    if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia_id:
        return redirect(url_for("cliente_entrada", slug=slug))
    ag = db.get_agendamento(id)
    if (not ag or ag["telefone"] != session.get("telefone")
            or ag["barbearia_id"] != barbearia_id
            or ag["status"] != "agendado"):
        return redirect(url_for("cliente_home", slug=slug))
    erro = None
    if request.method == "POST":
        try:
            sid = int(request.form.get("servico_id") or ag["servico_id"])
        except (ValueError, TypeError):
            sid = ag["servico_id"]
        bid_  = request.form.get("barbeiro_id") or ag["barbeiro_id"]
        data  = _limpar(request.form.get("data",""), 10)
        hora  = _limpar(request.form.get("hora",""), 5)
        if not data or not hora:
            erro = "Preenche a data e hora."
        elif not _val_data(data) or not _val_hora(hora):
            erro = "Data ou hora inválida."
        if not erro:
            dh  = f"{data} {hora}:00"
            s   = db.servico_por_id(sid)
            dur = s["duracao_min"] if s else 30
            if bid_:
                aus = db.ausencia_ativa(bid_, data, hora)
                if aus:
                    erro = f"{aus['barbeiro_nome']} está indisponível. Escolhe outro barbeiro ou data."
            if not erro:
                with _booking_lock:
                    livre, conflito = db.verificar_disponibilidade(bid_, dh, dur, barbearia_id, excluir_id=id)
                    if not livre:
                        erro = f"Conflito às {conflito['data_hora'][11:16]}. Escolhe outro horário."
                    if not erro:
                        db.reagendar_agendamento(id, dh, bid_)
                        return redirect(url_for("cliente_home", slug=slug))
    hoje = datetime.now().strftime("%Y-%m-%d")
    return render_template("reagendar.html", ag=enriquecer(ag),
                           servicos=db.listar_servicos(barbearia_id),
                           barbeiros=db.listar_barbeiros(barbearia_id, incluir_chefe=True),
                           hoje=hoje, erro=erro, origem="cliente", barbearia=barbearia)


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def enriquecer(agendamento):
    row = dict(agendamento)
    s = db.servico_por_id(row["servico_id"])
    row["servico_nome"]     = s["nome"]         if s else "Desconhecido"
    row["duracao_estimada"] = s["duracao_min"]   if s else 0
    row["preco"]            = s.get("preco", 0)  if s else 0
    row["valor"]            = row.get("valor") or 0
    row["telefone"]         = row.get("telefone") or ""
    b = db.get_barbeiro(row.get("barbeiro_id"))
    row["barbeiro_nome"] = b["nome"] if b else "—"
    row["duracao_real"]  = db.duracao_real_minutos(row.get("inicio"), row.get("fim"))
    dh = row.get("data_hora") or ""
    row["hora"] = dh[11:16] if len(dh) >= 16 else ""
    row["data"] = dh[:10]
    row["tipo"] = row.get("tipo") or "agendado"
    if row["inicio"] and not row["fim"]:
        try:
            inicio = datetime.strptime(row["inicio"], "%Y-%m-%d %H:%M:%S")
            row["segundos_decorridos"] = int((datetime.now() - inicio).total_seconds())
        except (ValueError, TypeError):
            row["segundos_decorridos"] = 0
    else:
        row["segundos_decorridos"] = 0
    try:
        hm = datetime.strptime(row["data_hora"], "%Y-%m-%d %H:%M:%S")
        row["minutos_ate"] = int((hm - datetime.now()).total_seconds() / 60)
    except Exception:
        row["minutos_ate"] = 999
    return row


# ══════════════════════════════════════════════════════════════
#  DASHBOARD
# ══════════════════════════════════════════════════════════════

@app.route("/")
@staff_required
def index():
    barbearia_id = bid()
    if session.get("role") == "chefe":
        filtro_bid = request.args.get("barbeiro_id", type=int)
    else:
        filtro_bid = session.get("user_id")
    agendamentos = [enriquecer(a) for a in db.listar_hoje(barbearia_id, filtro_bid)]
    em_andamento = [a for a in agendamentos if a["status"] == "em_andamento"]
    barbeiros    = db.listar_barbeiros(barbearia_id, incluir_chefe=True) if session.get("role") == "chefe" else []
    return render_template("index.html", agendamentos=agendamentos, em_andamento=em_andamento,
                           barbeiros=barbeiros, barbeiro_id_sel=filtro_bid,
                           resumo=db.resumo_hoje(barbearia_id, filtro_bid),
                           agora=datetime.now().strftime("%H:%M"))


# ══════════════════════════════════════════════════════════════
#  NOVO AGENDAMENTO
# ══════════════════════════════════════════════════════════════

@app.route("/novo", methods=["GET","POST"])
@staff_required
def novo():
    barbearia_id = bid()
    erro = None
    if request.method == "POST":
        nome = _limpar(request.form.get("cliente",""))
        tel  = _limpar(request.form.get("telefone",""), _MAX_TEL) or None
        try:
            sid = int(request.form.get("servico_id", 0))
        except (ValueError, TypeError):
            sid = 0
        data = _limpar(request.form.get("data",""), 10)
        hora = _limpar(request.form.get("hora",""), 5)
        bid_ = (request.form.get("barbeiro_id") or None) if session.get("role") == "chefe" else session.get("user_id")

        if not nome or not sid or not data or not hora:
            erro = "Preenche todos os campos obrigatórios."
        elif not _val_data(data) or not _val_hora(hora):
            erro = "Data ou hora inválida."
        if not erro:
            dh = f"{data} {hora}:00"
            s  = db.servico_por_id(sid)
            if not s or s.get("barbearia_id") != barbearia_id:
                erro = "Serviço inválido ou removido."
        if not erro:
            with _booking_lock:
                if bid_:
                    livre, conflito = db.verificar_disponibilidade(bid_, dh, s["duracao_min"], barbearia_id)
                    if not livre:
                        erro = f"Conflito às {conflito['data_hora'][11:16]}. Escolhe outro horário."
                if not erro:
                    db.criar_agendamento(nome, sid, dh, barbearia_id, bid_, "agendado", 0, tel)
                    return redirect(url_for("index"))
    hoje = datetime.now().strftime("%Y-%m-%d")
    return render_template("novo.html", servicos=db.listar_servicos(barbearia_id),
                           barbeiros=db.listar_barbeiros(barbearia_id, incluir_chefe=True),
                           hoje=hoje, agora=datetime.now().strftime("%H:%M"), erro=erro)


# ══════════════════════════════════════════════════════════════
#  WALK-IN
# ══════════════════════════════════════════════════════════════

@app.route("/walkin", methods=["GET","POST"])
@staff_required
def walkin():
    barbearia_id = bid()
    if request.method == "POST":
        nome = _limpar(request.form.get("cliente",""))
        tel  = _limpar(request.form.get("telefone",""), _MAX_TEL) or None
        try:
            sid = int(request.form.get("servico_id", 0))
        except (ValueError, TypeError):
            sid = 0
        bid_ = (request.form.get("barbeiro_id") or None) if session.get("role") == "chefe" else session.get("user_id")
        if nome and sid:
            s = db.servico_por_id(sid)
            if s and s.get("barbearia_id") == barbearia_id:
                agora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                novo_id = db.criar_agendamento(nome, sid, agora_str, barbearia_id, bid_, "walk-in", 0, tel)
                db.iniciar_trabalho(novo_id)
                return redirect(url_for("index"))
    return render_template("walkin.html", servicos=db.listar_servicos(barbearia_id),
                           barbeiros=db.listar_barbeiros(barbearia_id, incluir_chefe=True))


# ══════════════════════════════════════════════════════════════
#  AUTORIZAÇÃO DE AGENDAMENTOS  (anti-IDOR)
# ══════════════════════════════════════════════════════════════

def pode_gerir_agendamento(ag):
    """Verifica posse do agendamento: barbearia_id obrigatório + role."""
    if not ag or ag.get("barbearia_id") != bid():
        _log(f"IDOR_BLOCK ag_id={ag.get('id') if ag else '?'} barbearia={ag.get('barbearia_id') if ag else '?'}")
        return False
    if session.get("role") == "chefe":
        return True
    return ag.get("barbeiro_id") == session.get("user_id")


# ══════════════════════════════════════════════════════════════
#  AÇÕES DE AGENDAMENTO
# ══════════════════════════════════════════════════════════════

@app.route("/iniciar/<int:id>", methods=["POST"])
@staff_required
def iniciar(id):
    ag = db.get_agendamento(id)
    if ag and pode_gerir_agendamento(ag):
        db.iniciar_trabalho(id)
    return redirect(url_for("index"))


@app.route("/terminar/<int:id>", methods=["POST"])
@staff_required
def terminar(id):
    ag = db.get_agendamento(id)
    if ag and pode_gerir_agendamento(ag):
        try:
            valor = int(request.form.get("valor") or 0)
            valor = max(0, min(valor, 999_999))   # clamp razoável
        except (ValueError, TypeError):
            valor = 0
        db.terminar_trabalho(id, valor)
    return redirect(url_for("index"))


@app.route("/nao-compareceu/<int:id>", methods=["POST"])
@staff_required
def nao_compareceu(id):
    ag = db.get_agendamento(id)
    if ag and pode_gerir_agendamento(ag):
        db.marcar_nao_compareceu(id)
    return redirect(url_for("index"))


@app.route("/cancelar/<int:id>", methods=["POST"])
@staff_required
def cancelar(id):
    ag = db.get_agendamento(id)
    if ag and pode_gerir_agendamento(ag):
        db.cancelar_agendamento(id)
    return redirect(url_for("index"))


@app.route("/reagendar/<int:id>", methods=["GET","POST"])
@staff_required
def reagendar(id):
    barbearia_id = bid()
    ag = db.get_agendamento(id)
    if not ag or ag["status"] != "agendado" or not pode_gerir_agendamento(ag):
        return redirect(url_for("index"))
    is_chefe = session.get("role") == "chefe"
    erro = None
    if request.method == "POST":
        try:
            sid = int(request.form.get("servico_id") or ag["servico_id"])
        except (ValueError, TypeError):
            sid = ag["servico_id"]
        bid_ = (request.form.get("barbeiro_id") or ag["barbeiro_id"]) if is_chefe else session.get("user_id")
        data = _limpar(request.form.get("data",""), 10)
        hora = _limpar(request.form.get("hora",""), 5)
        if not data or not hora:
            erro = "Preenche a data e hora."
        elif not _val_data(data) or not _val_hora(hora):
            erro = "Data ou hora inválida."
        if not erro:
            dh = f"{data} {hora}:00"
            s  = db.servico_por_id(sid)
            if bid_:
                aus = db.ausencia_ativa(bid_, data, hora)
                if aus:
                    erro = f"{aus['barbeiro_nome']} está indisponível. Escolhe outro barbeiro ou data."
            if not erro:
                with _booking_lock:
                    livre, conflito = db.verificar_disponibilidade(
                        bid_, dh, s["duracao_min"] if s else 30, barbearia_id, excluir_id=id)
                    if not livre:
                        erro = f"Conflito às {conflito['data_hora'][11:16]}. Escolhe outro horário."
                    if not erro:
                        db.reagendar_agendamento(id, dh, bid_)
                        return redirect(url_for("index"))
    hoje = datetime.now().strftime("%Y-%m-%d")
    return render_template("reagendar.html", ag=enriquecer(ag),
                           servicos=db.listar_servicos(barbearia_id),
                           barbeiros=db.listar_barbeiros(barbearia_id, incluir_chefe=True) if is_chefe else [],
                           hoje=hoje, erro=erro, origem="staff")


# ══════════════════════════════════════════════════════════════
#  HISTÓRICO
# ══════════════════════════════════════════════════════════════

@app.route("/historico")
@staff_required
def historico():
    barbearia_id = bid()
    if session.get("role") == "chefe":
        filtro_bid = request.args.get("barbeiro_id", type=int)
    else:
        filtro_bid = session.get("user_id")
    data_sel     = _limpar(request.args.get("data",""), 10)
    if data_sel and not _val_data(data_sel):
        data_sel = ""
    datas        = db.listar_datas_historico(barbearia_id, filtro_bid)
    agendamentos = [enriquecer(a) for a in db.listar_todos(barbearia_id, filtro_bid, data_sel or None)]
    barbeiros    = db.listar_barbeiros(barbearia_id, incluir_chefe=True) if session.get("role") == "chefe" else []
    total_valor  = sum(a["valor"] for a in agendamentos if a["status"] == "concluido")
    total_cortes = sum(1 for a in agendamentos if a["status"] == "concluido")
    return render_template("historico.html", agendamentos=agendamentos,
                           barbeiros=barbeiros, barbeiro_id_sel=filtro_bid,
                           total_valor=total_valor, total_cortes=total_cortes,
                           datas=datas, data_sel=data_sel)


# ══════════════════════════════════════════════════════════════
#  ESTATÍSTICAS
# ══════════════════════════════════════════════════════════════

@app.route("/estatisticas")
@staff_required
def estatisticas():
    barbearia_id = bid()
    if session.get("role") != "chefe":
        return redirect(url_for("estatisticas_barbeiro", id=session.get("user_id")))
    stats = db.estatisticas(barbearia_id)
    return render_template("estatisticas.html", stats=stats)


@app.route("/estatisticas/barbeiro/<int:id>")
@staff_required
def estatisticas_barbeiro(id):
    barbearia_id = bid()
    # Barbeiro só pode ver as suas próprias estatísticas
    if session.get("role") != "chefe" and session.get("user_id") != id:
        _log(f"IDOR_STATS user={session.get('user_id')} tentou ver stats de barbeiro={id}")
        return redirect(url_for("estatisticas_barbeiro", id=session.get("user_id")))
    # Chefe só pode ver barbeiros da sua barbearia
    b = db.get_barbeiro(id)
    if not b or b.get("barbearia_id") != barbearia_id:
        return redirect(url_for("estatisticas"))
    det = db.estatisticas_detalhadas_barbeiro(id, barbearia_id)
    if not det["barbeiro"]:
        return redirect(url_for("estatisticas"))
    return render_template("estatisticas_barbeiro.html", det=det, dias_pt=DIAS_PT,
                           is_chefe=session.get("role") == "chefe")


# ══════════════════════════════════════════════════════════════
#  PERFIL
# ══════════════════════════════════════════════════════════════

@app.route("/perfil", methods=["GET","POST"])
@staff_required
def perfil():
    erro, ok = None, None
    if request.method == "POST":
        atual    = request.form.get("senha_atual","")
        nova     = request.form.get("senha_nova","")
        confirma = request.form.get("senha_confirma","")
        barb     = db.get_barbeiro(session["user_id"])
        if not barb or not barb.get("username"):
            erro = "Sem credenciais definidas. Pede ao chefe para configurar o teu acesso."
        else:
            staff = db.get_barbeiro_por_username(barb["username"])
            if not db.verificar_senha(staff, atual):
                erro = "Senha atual incorreta."
            elif nova != confirma:
                erro = "As novas senhas não coincidem."
            elif len(nova) < 6:
                erro = "A senha deve ter pelo menos 6 caracteres."
            else:
                db.alterar_senha(session["user_id"], nova)
                ok = "Senha alterada com sucesso."
    return render_template("perfil.html", erro=erro, ok=ok)


# ══════════════════════════════════════════════════════════════
#  GESTÃO DE EQUIPA
# ══════════════════════════════════════════════════════════════

@app.route("/barbeiros", methods=["GET","POST"])
@chefe_required
def barbeiros():
    barbearia_id = bid()
    if request.method == "POST":
        nome = _limpar(request.form.get("nome",""))
        if nome:
            db.criar_barbeiro(nome, barbearia_id)
        return redirect(url_for("barbeiros"))
    return render_template("barbeiros.html",
                           barbeiros=db.listar_barbeiros(barbearia_id, apenas_ativos=False),
                           todos_barbeiros=db.listar_barbeiros(barbearia_id, apenas_ativos=False, incluir_chefe=True),
                           ausencias=db.listar_ausencias(barbearia_id),
                           hoje=datetime.now().strftime("%Y-%m-%d"))


@app.route("/barbeiros/toggle/<int:id>", methods=["POST"])
@chefe_required
def toggle_barbeiro(id):
    b = db.get_barbeiro(id)
    if b and b.get("barbearia_id") == bid():
        db.toggle_barbeiro(id)
    return redirect(url_for("barbeiros"))


@app.route("/barbeiros/editar/<int:id>", methods=["POST"])
@chefe_required
def editar_barbeiro(id):
    nome = _limpar(request.form.get("nome",""))
    b    = db.get_barbeiro(id)
    if nome and b and b.get("barbearia_id") == bid():
        db.editar_barbeiro(id, nome)
    return redirect(url_for("barbeiros"))


@app.route("/barbeiros/repor-senha/<int:id>", methods=["POST"])
@chefe_required
def repor_senha_barbeiro(id):
    senha = request.form.get("senha","").strip()
    b     = db.get_barbeiro(id)
    if senha and len(senha) >= 6 and b and b.get("barbearia_id") == bid():
        db.repor_senha_barbeiro(id, senha)
    return redirect(url_for("barbeiros"))


@app.route("/barbeiros/credenciais/<int:id>", methods=["POST"])
@chefe_required
def set_credenciais(id):
    username = _limpar(request.form.get("username",""), _MAX_USERNAME).lower()
    senha    = request.form.get("senha","").strip()
    b        = db.get_barbeiro(id)
    if not (b and b.get("barbearia_id") == bid()):
        return redirect(url_for("barbeiros"))
    if not username or not _USER_RE.match(username):
        return redirect(url_for("barbeiros") + "?erro=username_invalido")
    if not senha or len(senha) < 6:
        return redirect(url_for("barbeiros") + "?erro=senha_curta")
    ok = db.set_credenciais(id, username, senha)
    if not ok:
        return redirect(url_for("barbeiros") + "?erro=username_duplicado")
    return redirect(url_for("barbeiros"))


@app.route("/barbeiros/ausencia", methods=["POST"])
@chefe_required
def criar_ausencia():
    try:
        barbeiro_id = int(request.form.get("barbeiro_id", 0))
    except (ValueError, TypeError):
        barbeiro_id = 0
    # Verificar que o barbeiro pertence a esta barbearia
    b = db.get_barbeiro(barbeiro_id) if barbeiro_id else None
    if not b or b.get("barbearia_id") != bid():
        return redirect(url_for("barbeiros"))
    data_inicio = _limpar(request.form.get("data_inicio",""), 10)
    data_fim    = _limpar(request.form.get("data_fim",""), 10)
    if barbeiro_id and _val_data(data_inicio) and _val_data(data_fim):
        db.criar_ausencia(
            barbeiro_id=barbeiro_id, data_inicio=data_inicio, data_fim=data_fim,
            tipo=request.form.get("tipo","falta"),
            motivo=_limpar(request.form.get("motivo",""), _MAX_MOTIVO),
            hora_inicio=request.form.get("hora_inicio") or None,
            hora_fim=request.form.get("hora_fim")    or None,
        )
    return redirect(url_for("barbeiros"))


@app.route("/barbeiros/ausencia/apagar/<int:id>", methods=["POST"])
@chefe_required
def apagar_ausencia(id):
    ausencias = db.listar_ausencias(bid())
    if any(a["id"] == id for a in ausencias):
        db.apagar_ausencia(id)
    return redirect(url_for("barbeiros"))


# ══════════════════════════════════════════════════════════════
#  SERVIÇOS
# ══════════════════════════════════════════════════════════════

@app.route("/servicos", methods=["GET","POST"])
@chefe_required
def servicos():
    barbearia_id = bid()
    if request.method == "POST":
        nome = _limpar(request.form.get("nome",""))
        try:
            dur   = max(5,  min(int(request.form.get("duracao_min") or 30), 300))
            preco = max(0, min(int(request.form.get("preco") or 0), 999_999))
        except (ValueError, TypeError):
            dur, preco = 30, 0
        if nome:
            db.criar_servico(nome, dur, barbearia_id, preco)
        return redirect(url_for("servicos"))
    return render_template("servicos.html", servicos=db.listar_servicos(barbearia_id, apenas_ativos=False))


@app.route("/servicos/editar/<int:id>", methods=["POST"])
@chefe_required
def editar_servico(id):
    s = db.servico_por_id(id)
    if not s or s.get("barbearia_id") != bid():
        return redirect(url_for("servicos"))
    nome = _limpar(request.form.get("nome",""))
    try:
        dur   = max(5,  min(int(request.form.get("duracao_min") or 30), 300))
        preco = max(0, min(int(request.form.get("preco") or 0), 999_999))
    except (ValueError, TypeError):
        dur, preco = 30, 0
    if nome:
        db.atualizar_servico(id, nome, dur, preco)
    return redirect(url_for("servicos"))


@app.route("/servicos/apagar/<int:id>", methods=["POST"])
@chefe_required
def apagar_servico(id):
    s = db.servico_por_id(id)
    if s and s.get("barbearia_id") == bid():
        db.apagar_servico(id)
    return redirect(url_for("servicos"))


# ══════════════════════════════════════════════════════════════
#  CONFIGURAÇÕES
# ══════════════════════════════════════════════════════════════

@app.route("/configuracoes", methods=["GET","POST"])
@chefe_required
def configuracoes():
    barbearia_id = bid()
    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "horario":
            for dia in range(7):
                aberto     = request.form.get(f"aberto_{dia}")
                abertura_v = request.form.get(f"abertura_{dia}", "08:00")
                fecho_v    = request.form.get(f"fecho_{dia}",    "19:00")
                # Validar formato HH:MM
                if not _HORA_RE.match(abertura_v): abertura_v = "08:00"
                if not _HORA_RE.match(fecho_v):    fecho_v    = "19:00"
                db.set_horario_dia(dia, abertura_v, fecho_v, 0 if aberto else 1, barbearia_id)
        elif acao == "geral":
            try:
                buf = max(0, min(int(request.form.get("buffer_minutos", 10)), 60))
                mpd = max(1, min(int(request.form.get("max_por_dia", 20)), 200))
            except (ValueError, TypeError):
                buf, mpd = 10, 20
            db.set_config("buffer_minutos", buf, barbearia_id)
            db.set_config("max_por_dia",    mpd, barbearia_id)
        elif acao == "dia_fechado":
            data   = _limpar(request.form.get("data_fechada",""), 10)
            motivo = _limpar(request.form.get("motivo_fechado",""), _MAX_MOTIVO)
            if data and _val_data(data):
                db.adicionar_dia_fechado(data, motivo, barbearia_id)
        elif acao == "remover_dia":
            try:
                dia_id = int(request.form.get("dia_id", 0))
                dias   = db.listar_dias_fechados(barbearia_id)
                if any(d["id"] == dia_id for d in dias):
                    db.remover_dia_fechado(dia_id)
            except (ValueError, TypeError):
                pass
        return redirect(url_for("configuracoes"))
    horario       = db.get_horario(barbearia_id)
    dias_fechados = db.listar_dias_fechados(barbearia_id)
    configs       = db.get_todas_configs(barbearia_id)
    return render_template("configuracoes.html", horario=horario,
                           dias_fechados=dias_fechados, configs=configs,
                           dias_pt=DIAS_PT, hoje=datetime.now().strftime("%Y-%m-%d"))


# ══════════════════════════════════════════════════════════════
#  API
# ══════════════════════════════════════════════════════════════

@app.route("/api/tempo/<int:id>")
def api_tempo(id):
    if "user_id" not in session:
        return jsonify({"segundos": 0, "estimado": 0, "em_atraso": False})
    ag = db.get_agendamento(id)
    # Verificar que o agendamento pertence à barbearia da sessão
    if not ag or not ag["inicio"] or ag.get("barbearia_id") != session.get("barbearia_id"):
        return jsonify({"segundos": 0, "estimado": 0, "em_atraso": False})
    inicio   = datetime.strptime(ag["inicio"], "%Y-%m-%d %H:%M:%S")
    segundos = int((datetime.now() - inicio).total_seconds())
    s        = db.servico_por_id(ag["servico_id"])
    estimado = (s["duracao_min"] * 60) if s else 0
    return jsonify({"segundos": segundos, "estimado": estimado, "em_atraso": segundos > estimado})


@app.route("/api/slots")
def api_slots():
    barbeiro_id  = request.args.get("barbeiro_id", type=int)
    data         = request.args.get("data","")
    sid          = request.args.get("servico_id", type=int)
    barbearia_id = session.get("barbearia_id")

    if not (barbeiro_id and data and sid and barbearia_id):
        return jsonify([])
    # Validar formato da data
    if not _val_data(data):
        return jsonify([])
    # Validar que barbeiro e serviço pertencem a esta barbearia (anti-IDOR)
    barb = db.get_barbeiro(barbeiro_id)
    if not barb or barb.get("barbearia_id") != barbearia_id:
        _log(f"IDOR_SLOTS barbeiro={barbeiro_id} barbearia_sessao={barbearia_id}")
        return jsonify([])
    s = db.servico_por_id(sid)
    if not s or s.get("barbearia_id") != barbearia_id:
        return jsonify([])

    slots = db.horarios_disponiveis(barbeiro_id, data, s["duracao_min"], barbearia_id)
    if session.get("role") == "cliente":
        tel = session.get("telefone","")
        if tel:
            minhas       = db.agendamentos_cliente_barbeiro_dia(tel, barbeiro_id, data, barbearia_id)
            horas_minhas = {a["data_hora"][11:16] for a in minhas}
            for slot in slots:
                if slot["hora"] in horas_minhas:
                    slot["tipo"] = "minha_marcacao"
    return jsonify(slots)


@app.route("/api/lembretes")
def api_lembretes():
    if "user_id" not in session or session.get("role") == "cliente":
        return jsonify([])
    barbearia_id = bid()
    filtro_bid   = None if session.get("role") == "chefe" else session.get("user_id")
    resultado    = []
    for a in db.proximos_agendamentos(barbearia_id, minutos=20, barbeiro_id=filtro_bid):
        s = db.servico_por_id(a["servico_id"])
        try:
            hm = datetime.strptime(a["data_hora"], "%Y-%m-%d %H:%M:%S")
            minutos_ate = int((hm - datetime.now()).total_seconds() / 60)
        except (ValueError, TypeError):
            minutos_ate = 0
        resultado.append({"id": a["id"], "cliente": a["cliente"],
                          "telefone": a["telefone"] or "", "hora": a["data_hora"][11:16],
                          "servico": s["nome"] if s else "—", "minutos_ate": minutos_ate})
    return jsonify(resultado)


@app.route("/api/meu-status")
def api_meu_status():
    tel          = session.get("telefone","")
    barbearia_id = session.get("barbearia_id")
    if not tel or not barbearia_id:
        return jsonify([])
    agendamentos = db.listar_por_telefone(tel, barbearia_id)
    resultado    = []
    for a in agendamentos:
        if a["status"] == "em_andamento":
            s = db.servico_por_id(a["servico_id"])
            b = db.get_barbeiro(a["barbeiro_id"])
            resultado.append({"id": a["id"],
                              "servico":  s["nome"] if s else "—",
                              "barbeiro": b["nome"] if b else "—"})
    return jsonify(resultado)


@app.route("/api/estado")
def api_estado():
    if "user_id" not in session:
        return jsonify({"h": ""})
    role         = session.get("role")
    barbearia_id = session.get("barbearia_id")
    if role == "cliente":
        h = db.estado_cliente(session.get("telefone",""), barbearia_id)
    elif role == "chefe":
        h = db.estado_hoje(barbearia_id)
    else:
        h = db.estado_hoje(barbearia_id, session.get("user_id"))
    return jsonify({"h": h})


# ══════════════════════════════════════════════════════════════
#  ERROR HANDLERS
# ══════════════════════════════════════════════════════════════

@app.errorhandler(404)
def nao_encontrado(e):
    return render_template("404.html"), 404


@app.errorhandler(413)
def ficheiro_grande(e):
    return jsonify({"erro": "Ficheiro demasiado grande. Máximo 2 MB."}), 413


@app.errorhandler(500)
def erro_servidor(e):
    _log(f"ERRO_500 path={request.path}")
    return render_template("404.html"), 500


# ══════════════════════════════════════════════════════════════
#  ARRANQUE
# ══════════════════════════════════════════════════════════════

csrf.init_app(app)
db.init_db()

if __name__ == "__main__":
    _debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=5000, debug=_debug)
