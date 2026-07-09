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
# Privacidade: send_default_pii=False (clientes reais — não enviar IPs, cookies,
# nem corpos de pedido para o Sentry). release/environment ajudam a correlacionar
# erros com cada deploy. O estado fica em _SENTRY_ATIVO e é exposto em /healthz
# para confirmar a partir de produção (curl .../healthz → "sentry": true).
_sentry_dsn = os.environ.get("SENTRY_DSN", "")
_SENTRY_ATIVO = False
if _sentry_dsn:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        try:
            with open(os.path.join(os.path.dirname(__file__), "version.txt")) as _svf:
                _sentry_release = "barbearia@" + _svf.read().strip()
        except Exception:
            _sentry_release = None
        sentry_sdk.init(
            dsn=_sentry_dsn,
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.05,
            send_default_pii=False,
            environment=os.environ.get("SENTRY_ENV", "production"),
            release=_sentry_release,
        )
        _SENTRY_ATIVO = True
        logging.getLogger("sentry").info("Sentry activo (release=%s)", _sentry_release)
    except ImportError:
        logging.getLogger("sentry").warning("SENTRY_DSN definido mas sentry-sdk não instalado")

app = Flask(__name__)
# PythonAnywhere corre atrás de nginx (proxy reverso) — necessário para HTTPS/CSRF correcto
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
csrf = CSRFProtect()
# Versão dos assets — muda a cada deploy para forçar cache-bust no browser
_ASSET_VER  = int(time.time())
_APP_START  = time.monotonic()   # âncora para calcular uptime da app
# Número de deploy (incrementado automaticamente pelo deploy.sh)
try:
    with open(os.path.join(os.path.dirname(__file__), "version.txt")) as _vf:
        _APP_VERSION = int(_vf.read().strip())
except Exception:
    _APP_VERSION = 0

# ── Configuração por ambiente (Railway/prod vs PythonAnywhere) ─────────────────
# Todas INERTES quando as env não estão definidas → o comportamento em
# PythonAnywhere fica byte-idêntico. Só ganham efeito quando o ambiente Railway
# as injecta na migração. NÃO exigem alterar código na altura do cutover.
#
#   LOGOS_DIR          — pasta dos logos fora do repositório (volume persistente).
#   TENANT_BASE_DOMAIN — domínio-mãe p/ subdomínios por estabelecimento
#                        (ex.: "carecabarber.com" → joao.carecabarber.com). Cada
#                        estabelecimento ganha o SEU URL automaticamente pelo slug.
#   CANONICAL_URL      — origem legítima usada pelo beacon anti-clone. Segue para
#                        Railway sem editar templates.
_LOGOS_DIR_ENV      = os.environ.get("LOGOS_DIR", "").strip()
_TENANT_BASE_DOMAIN = db.normalizar_dominio(os.environ.get("TENANT_BASE_DOMAIN", "").strip())
_CANONICAL_URL      = (os.environ.get("CANONICAL_URL", "").strip()
                       or "https://carecabarber.pythonanywhere.com")
# Subdomínios reservados que NUNCA são resolvidos como estabelecimento.
_SUBDOMINIOS_RESERVADOS = frozenset(("www", "api", "app", "admin", "static", "mail", "ns", "cdn"))

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
app.config['SENTRY_ATIVO']            = _SENTRY_ATIVO

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


# ── Crawlers de IA / scrapers de clonagem ──────────────────────────
# Bots que raspam conteúdo para treinar modelos ou clonar sites. Bloqueá-los
# não impede um humano de copiar (impossível na web), mas trava a via
# "meter o URL numa IA e pedir para clonar" — o vector suspeito de cópia.
# Correspondência por substring, minúsculas, no User-Agent.
_BOTS_IA_BLOQUEADOS = (
    "gptbot", "oai-searchbot", "chatgpt-user", "chatgpt",          # OpenAI
    "claudebot", "claude-web", "anthropic-ai", "anthropic",        # Anthropic
    "ccbot",                                                       # Common Crawl (usado p/ treino)
    "google-extended",                                             # Google IA
    "perplexitybot", "perplexity",                                 # Perplexity
    "bytespider",                                                  # ByteDance/TikTok
    "amazonbot", "applebot-extended",                              # Amazon / Apple IA
    "diffbot", "imagesiftbot", "omgili", "omgilibot",             # scrapers de dados
    "meta-externalagent", "facebookbot",                          # Meta IA
    "cohere-ai", "cohere-training-data-crawler",                   # Cohere
    "youbot", "petalbot", "timpibot", "webzio-extended",
    "scrapy", "python-requests", "python-urllib", "httrack",       # ferramentas de clonagem
    "wget", "curl", "node-fetch", "axios", "go-http-client",
)


# Só as páginas HTML PÚBLICAS e clonáveis são protegidas. Deixar de fora APIs,
# webhooks, monitorização e páginas autenticadas evita partir integrações
# legítimas (que usam curl/python-requests) — o alvo é a clonagem visual.
_ENDPOINTS_PUBLICOS_PROTEGIDOS = frozenset((
    "cliente_entrada",   # página de marcação do cliente — o principal activo copiável
    "login",             # ecrã de entrada de staff
))

# ── (B) Rate-limit anti-ripper ─────────────────────────────────────
# Trava site-rippers (HTTrack/wget -r) que puxam a página centenas de vezes.
# Limite GENEROSO: um humano nunca recarrega 40x em 20s; um ripper sim.
# Estado por-worker (multi-worker PA): cada processo conta o seu tráfego — chega
# para travar um ripper, que satura um worker rapidamente.
_scrape_hits  = {}                 # ip -> [timestamps]
_scrape_lock  = threading.Lock()
_SCRAPE_WINDOW = 20                # segundos
_SCRAPE_MAX    = 40                # pedidos/janela por IP antes de 429

# ── (C) Honeypot ───────────────────────────────────────────────────
# Link invisível para humanos (display:none) mas seguido por crawlers que puxam
# todos os href. Quem lá bate é bot → banido temporariamente das páginas públicas.
_trap_banidos  = {}                # ip -> expiry_ts
_TRAP_BAN_SECS = 1800             # 30 min
_trap_lock     = threading.Lock()


def _ip_pedido() -> str:
    """IP do cliente (ProxyFix já normaliza remote_addr atrás do nginx)."""
    return request.remote_addr or "?"


@app.before_request
def _bloquear_bots_ia():
    """Camada anti-clonagem nas páginas públicas: bots de IA, scrapers, rippers
    e IPs apanhados no honeypot. Só actua em _ENDPOINTS_PUBLICOS_PROTEGIDOS —
    nunca em /health, /static, /api, webhooks nem páginas autenticadas.
    """
    if (request.endpoint or "") not in _ENDPOINTS_PUBLICOS_PROTEGIDOS:
        return
    ip = _ip_pedido()

    # (C) IP já banido pelo honeypot?
    _now = time.time()
    with _trap_lock:
        exp = _trap_banidos.get(ip)
        if exp:
            if exp > _now:
                return ("", 403)
            del _trap_banidos[ip]  # ban expirou

    # (A/bots) User-Agent de crawler de IA ou ferramenta de scraping
    ua = (request.headers.get("User-Agent") or "").lower()
    if not ua:
        return  # sem UA: não bloqueia (pode ser verificação interna legítima)
    for _b in _BOTS_IA_BLOQUEADOS:
        if _b in ua:
            _log(f"bot-ia bloqueado ua={ua[:120]} path={request.path}")
            return ("", 403)

    # (B) Rate-limit por IP — trava rippers em massa
    with _scrape_lock:
        janela = _scrape_hits.get(ip)
        if janela is None:
            janela = []
            _scrape_hits[ip] = janela
        # descartar timestamps fora da janela
        corte = _now - _SCRAPE_WINDOW
        janela[:] = [t for t in janela if t > corte]
        janela.append(_now)
        excedeu = len(janela) > _SCRAPE_MAX
        # limpeza oportunista: evitar crescimento sem limite do dict
        if len(_scrape_hits) > 5000:
            for k in [k for k, v in _scrape_hits.items() if not v or v[-1] < corte][:2000]:
                _scrape_hits.pop(k, None)
    if excedeu:
        _log(f"rate-limit clonagem ip={ip} path={request.path}")
        return ("Demasiados pedidos.", 429)


@app.route("/config/backup.json")
@app.route("/wp-admin/")
@app.route("/.git/config")
def _honeypot():
    """(C) Rotas-armadilha que só bots/scanners procuram. Quem lá bate é banido
    das páginas públicas por 30 min. Um humano nunca acede a estes caminhos —
    são iscos referenciados por um link invisível na página do cliente."""
    # Nunca banir motores de busca legítimos (não seguem estes links por respeitarem
    # o robots.txt, mas é uma salvaguarda extra para não prejudicar a indexação).
    ua = (request.headers.get("User-Agent") or "").lower()
    if any(b in ua for b in ("googlebot", "bingbot", "duckduckbot", "slurp", "yandex")):
        return ("", 404)
    ip = _ip_pedido()
    with _trap_lock:
        _trap_banidos[ip] = time.time() + _TRAP_BAN_SECS
        if len(_trap_banidos) > 10000:  # limpeza defensiva
            _now = time.time()
            for k in [k for k, v in _trap_banidos.items() if v < _now]:
                _trap_banidos.pop(k, None)
    _log(f"honeypot apanhou ip={ip} path={request.path}")
    return ("", 404)


@app.route("/robots.txt")
def robots():
    """robots.txt — proíbe crawlers de IA e limita indexação a páginas públicas.

    Servido explicitamente (Flask não serve /robots.txt por omissão). Complementa
    o bloqueio por User-Agent: bots honestos param aqui, os desonestos apanham 403.
    """
    linhas = ["User-agent: " + b for b in (
        "GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-Web",
        "anthropic-ai", "CCBot", "Google-Extended", "PerplexityBot", "Bytespider",
        "Amazonbot", "Applebot-Extended", "Diffbot", "ImagesiftBot", "Omgilibot",
        "meta-externalagent", "FacebookBot", "cohere-ai", "YouBot", "PetalBot",
    )]
    # Bots de IA: bloqueio total. Motores de busca legítimos (User-agent: *):
    # indexação normal permitida, mas fora dos caminhos-armadilha (honeypot) e
    # das áreas internas — assim o Googlebot nunca cai no honeypot.
    corpo = ("\n".join(linhas) + "\nDisallow: /\n\n"
             "User-agent: *\n"
             "Disallow: /config/\n"
             "Disallow: /wp-admin/\n"
             "Disallow: /.git/\n"
             "Disallow: /api/\n"
             "Disallow: /mesa/\n"
             "Disallow: /root/\n")
    return app.response_class(corpo, mimetype="text/plain")


# ── Logos fora do repositório (volume persistente Railway) ─────────────────────
# O filesystem do Railway é efémero: logos enviados após deploy perdem-se a cada
# reinício. Com LOGOS_DIR a apontar para um volume montado, servimo-los a partir
# de lá em vez de static/logos. Rota registada SÓ quando LOGOS_DIR está definido —
# em PythonAnywhere não existe, e os logos continuam a ser servidos por /static.
if _LOGOS_DIR_ENV:
    from flask import send_from_directory

    @app.route("/static/logos/<path:filename>")
    def _logos_volume(filename):
        resp = make_response(send_from_directory(_LOGOS_DIR_ENV, filename))
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp


# ── (E) Beacon de detecção de clones ───────────────────────────────
# GIF transparente 1×1. A página pública embute JS que compara o host que o
# SERVIDOR renderizou (server-side, congelado no HTML) com o host que o browser
# vê. Se forem diferentes, o HTML foi movido para outra origem → é um clone/mirror,
# e o browser da vítima do clone carrega este pixel a apontar para o NOSSO
# servidor, deixando-nos um registo de QUEM copiou (host, referrer, slug, quando).
#
# Porque é robusto contra o vector reportado ("meter URL numa IA e clonar"):
#   • No nosso site (ou num domínio próprio de tenant que o NOSSO backend serve),
#     o host renderizado == host do browser → o beacon NUNCA dispara (zero ruído,
#     zero privacidade dos nossos visitantes). Domínios próprios funcionam de
#     borla porque o servidor renderiza-os já com o host correcto.
#   • Num clone estático (HTML copiado e servido noutro domínio) o host congelado
#     no HTML ≠ host do browser → dispara. O clone raramente replica a nossa CSP
#     (é definida server-side), portanto o pixel passa.
#   • Um clonador que reescreva o host embutido derrota-o — como qualquer detecção
#     client-side. O objectivo é apanhar a cópia ingénua, que é a ameaça real.
_PIXEL_GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
              b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01"
              b"\x00\x00\x02\x02D\x01\x00;")


@app.route("/_cb/px.gif")
def _clone_beacon():
    """Recebe o beacon de páginas servidas fora da origem legítima (clones).

    Nunca falha e nunca bloqueia — devolve sempre o pixel. Os parâmetros são
    apenas registados (sanitizados: sem newlines, truncados) para investigação;
    não são reflectidos em lado nenhum (sem risco de XSS/log-injection)."""
    def _limpo(v: str, n: int = 200) -> str:
        return (v or "").replace("\n", " ").replace("\r", " ").strip()[:n]

    host_browser = _limpo(request.args.get("h", ""), 120)   # host visto pelo browser
    host_orig    = _limpo(request.args.get("o", ""), 120)   # host que o servidor renderizou
    slug         = _limpo(request.args.get("s", ""), 80)    # estabelecimento clonado
    quando       = _limpo(request.args.get("t", ""), 40)    # carimbo do snapshot
    ref          = _limpo(request.headers.get("Referer", ""), 200)
    ip           = _ip_pedido()
    _log(f"CLONE-ALERTA beacon host_clone={host_browser!r} origem_legit={host_orig!r} "
         f"slug={slug!r} snapshot={quando!r} ref={ref!r} ip={ip}")
    resp = Response(_PIXEL_GIF, mimetype="image/gif")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.before_request
def _gerar_csp_nonce():
    """Gera um nonce criptográfico único por pedido para a CSP."""
    from flask import g
    g.csp_nonce  = secrets.token_hex(16)
    g.request_id = secrets.token_hex(8)   # trace ID — correlaciona logs com erros do utilizador


@app.before_request
def _resolver_dominio_proprio():
    """Encaminha domínios próprios de estabelecimentos para a sua entrada de cliente.

    Se o pedido chegar por um domínio próprio VERIFICADO (ex.: joao.com), a raiz
    "/" redirecciona para a entrada de cliente desse estabelecimento. Quando o
    Host é o domínio principal (ou qualquer domínio sem correspondência), esta
    função é inerte — o comportamento actual mantém-se inalterado.

    A resolução Host→estabelecimento é cacheada por worker (TTL 300s, negativos
    incluídos) para não fazer uma query à BD em cada pedido do domínio principal.
    """
    from flask import g
    g.tenant = None
    host = db.normalizar_dominio(request.host)
    if not host:
        return

    # (1) Subdomínio por estabelecimento — <slug>.TENANT_BASE_DOMAIN.
    # Gated por TENANT_BASE_DOMAIN: em PythonAnywhere (env ausente) é totalmente
    # inerte. Em Railway com DNS wildcard (*.carecabarber.com) dá a CADA
    # estabelecimento o seu próprio URL sem verificação manual de domínio.
    if _TENANT_BASE_DOMAIN and host.endswith("." + _TENANT_BASE_DOMAIN):
        sub = host[: -(len(_TENANT_BASE_DOMAIN) + 1)]   # tira ".base"
        # só o primeiro rótulo conta (joao.x.carecabarber.com → ignora)
        if sub and "." not in sub and sub not in _SUBDOMINIOS_RESERVADOS:
            _cks = f"sub:{sub}"
            barbearia = _pc_get(_cks)
            if barbearia is None:
                barbearia = db.get_barbearia_por_slug(sub) or False
                _pc_set(_cks, barbearia, 300)
            if barbearia:
                g.tenant = barbearia
                if request.path == "/" and request.method == "GET":
                    return redirect(url_for("cliente_entrada", slug=barbearia["slug"]))
                return   # subdomínio resolvido — não tentar domínio próprio

    # (2) Domínio próprio VERIFICADO (ex.: joao.com).
    _ck = f"dom:{host}"
    barbearia = _pc_get(_ck)
    if barbearia is None:                       # ausente do cache → resolver
        barbearia = db.get_barbearia_por_dominio(host) or False
        _pc_set(_ck, barbearia, 300)
    if not barbearia:                           # domínio principal/desconhecido
        return
    g.tenant = barbearia
    # Só a raiz é redireccionada; restantes caminhos (/cliente/..., /static, API)
    # continuam a funcionar normalmente sob o domínio próprio.
    if request.path == "/" and request.method == "GET":
        return redirect(url_for("cliente_entrada", slug=barbearia["slug"]))


@app.before_request
def _verificar_plano():
    """Bloqueia staff de barbearias suspensas: plano expirado OU desactivadas
    manualmente pelo root (ativa=0).

    `plano.ativo` já combina `bool(ativa)` com a validade do prazo, por isso é a
    única condição necessária. NÃO usar `sem_limite` como escape — uma barbearia
    de plano ilimitado (plano_expira_em=NULL) que o root bloqueie (ativa=0) tem
    `sem_limite=True` mas `ativo=False`, e TEM de ser suspensa. O escape antigo
    deixava esses estabelecimentos abrir mesmo depois de bloqueados."""
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
        # TTL curto (60s): em multi-worker cada processo tem cache própria; um TTL
        # curto garante que um bloqueio do root propaga a todos os workers em ≤60s.
        # verificar_plano é uma leitura de 1 linha — o custo é negligenciável.
        _pc_set(_ck, plano, 60)
    g.plano_info = plano
    if plano and not plano.get("ativo"):
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
    # Isolamento de origem cruzada: separa a nossa janela de openers cross-origin
    # (protege contra reverse-tabnabbing e XS-Leaks em páginas autenticadas).
    # 'allow-popups' preserva os popups que a app abre (impressão de QR via
    # window.open), que de outro modo perderiam a referência ao opener.
    h.setdefault('Cross-Origin-Opener-Policy', 'same-origin-allow-popups')
    # Bloqueia políticas cross-domain legadas (Flash/PDF a ler dados do domínio).
    h.setdefault('X-Permitted-Cross-Domain-Policies', 'none')
    h.setdefault('Referrer-Policy',            'strict-origin-when-cross-origin')
    h.setdefault('Strict-Transport-Security',  'max-age=63072000; includeSubDomains; preload')
    h.setdefault('Permissions-Policy',
                 'camera=(self), microphone=(), geolocation=(), payment=(), '
                 'usb=(), bluetooth=(), serial=(), hid=()')
    h.pop('Server',       None)
    h.pop('X-Powered-By', None)
    rid = getattr(g, "request_id", None)
    if rid:
        h['X-Request-ID'] = rid   # visível em DevTools — utilizador pode reportar este ID
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
        "csp_nonce":   getattr(g, "csp_nonce", ""),
        "cb_host":     request.host,   # host que o SERVIDOR viu — para o beacon anti-clone
        "cb_canonical": _CANONICAL_URL,  # origem legítima do beacon (env-driven; segue p/ Railway)
        "agora_iso":   _agora().strftime("%Y-%m-%dT%H:%M:%S"),
        "plano_info":  plano_info,
        "av":          _ASSET_VER,
        "app_version": _APP_VERSION,
        "tema_claro":  request.cookies.get("cb-theme") == "light",
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


@app.context_processor
def inject_pdf_ok():
    """Injeta pdf_ok em todos os templates — usado para esconder o botão PDF quando
    reportlab não está instalado. Evita mostrar funcionalidade indisponível."""
    from helpers import _PDF_OK
    return {"pdf_ok": _PDF_OK}


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
        if ref and urlparse(ref).netloc == request.host:
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
#  HEALTH CHECK
# ══════════════════════════════════════════════════════════════

@app.route("/health")
@csrf.exempt
def health():
    """Endpoint de health check para monitorização externa (UptimeRobot, etc.).

    Verifica conectividade com a BD.  Devolve 200 OK se tudo estiver operacional,
    503 Service Unavailable se a BD não responder.
    Sem autenticação — só retorna dados mínimos (sem informação sensível).
    """
    db_ok = False
    try:
        with db._read() as _hc:
            _hc.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        pass
    uptime_s = int(time.monotonic() - _APP_START)
    payload = {
        "ok":      db_ok,
        "uptime":  uptime_s,
        "version": _APP_VERSION,
    }
    return jsonify(payload), (200 if db_ok else 503)


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
    """Limpa state expirado do rate limiter (chamado pelo background thread)."""
    from db import rate_limit as _rl
    try:
        _rl.cleanup()
    except Exception:
        pass
    # Manter limpeza dos _user_fails em memória
    now = time.time()
    with _user_lock:
        for u in list(_user_fails.keys()):
            _user_fails[u] = [t for t in _user_fails[u] if now - t < _USER_LOCKOUT]
            if not _user_fails[u]:
                del _user_fails[u]


# IDs de agendamentos para os quais o lembrete já foi enviado
# Formato: {chave: timestamp_float} — limpo periodicamente em _ciclo_limpeza
# Lock obrigatório: _thread_limpeza e request handlers podem aceder concorrentemente
_lembretes_enviados: dict = {}
_lembretes_lock = threading.Lock()
_LEMBRETES_MAX = 50_000  # cap de segurança: ~5 MB máx, evita OOM em produção


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
                    f"WHERE a.barbearia_id=? AND a.status='{ST_AGENDADO}' AND a.data_hora BETWEEN ? AND ?",
                    (bid_, ini, fim)).fetchall()
            for ag in _rows:
                chave = (ag["id"], "1h")
                with _lembretes_lock:
                    if chave in _lembretes_enviados:
                        continue
                    if len(_lembretes_enviados) >= _LEMBRETES_MAX:
                        continue  # cap de segurança
                    _lembretes_enviados[chave] = time.time()
                hora = ag["data_hora"][11:16]
                _push_async(bid_,
                            "🔔 Marcação em 1 hora",
                            f"{ag['cliente']} — {ag['servico_nome'] or 'Corte'} às {hora}",
                            barbeiro_id=ag["barbeiro_id"])
        except Exception as _e:
            _log_lim.warning("Erro em _enviar_lembretes_push bid=%s: %s", bid_, _e)


def _push_notif_sub(endpoint, p256dh, auth, barbearia_id, titulo, corpo, url="/"):
    """Envia push directamente a uma subscripção (endpoint+keys) de cliente."""
    from helpers import _PUSH_OK, _VAPID_PRIVATE_KEY, _VAPID_CLAIMS, _push_one
    if not _PUSH_OK or not _VAPID_PRIVATE_KEY:
        return
    import json as _json
    payload = _json.dumps({"titulo": titulo, "corpo": corpo, "url": url})
    sub = {"endpoint": endpoint, "p256dh": p256dh, "auth": auth}
    result = _push_one(sub, payload)
    if result == "expired":
        try:
            db.push_remover_expiradas([endpoint])
        except Exception:
            pass


def _push_espera(entrada: dict, barbearia_id: int) -> None:
    """Envia push ao cliente da fila de espera quando um slot fica livre.

    Executa em background (não bloqueia o pedido HTTP que despoletou o cancelamento).
    Silencioso em caso de erro — o slot já foi marcado como livre na BD.
    """
    tel = (entrada.get("telefone") or "").strip()
    if not tel:
        return
    subs = db.cliente_push_listar_por_tel(tel, barbearia_id)
    if not subs:
        return
    data = entrada.get("data_preferida", "")
    try:
        barbearia = db.get_barbearia(barbearia_id)
        slug = barbearia.get("slug", "") if barbearia else ""
    except Exception:
        slug = ""
    url = f"/cliente/{slug}" if slug else "/"

    def _enviar():
        invalidas = []
        for sub in subs:
            try:
                _push_notif_sub(
                    sub["endpoint"], sub["p256dh"], sub["auth"],
                    barbearia_id,
                    "🎉 Vaga disponível!",
                    f"Abriu uma vaga para o dia {data}. Entra na app para marcar!",
                    url,
                )
            except Exception as _e:
                logging.getLogger("fila_espera").warning(
                    "push_espera falhou sub=%s tel=%s: %s",
                    sub.get("endpoint", "?")[:40], tel, _e)
        if invalidas:
            try:
                db.push_remover_expiradas(invalidas)
            except Exception:
                pass

    threading.Thread(target=_enviar, daemon=True, name=f"push-espera-{barbearia_id}").start()


def _enviar_lembretes_push_cliente():
    """Envia push 24h antes ao cliente de cada marcação próxima."""
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
            agora_b = _agora(barbearia_id=bid_)
            # Janela: entre 23h55 e 24h05 antes da marcação
            ini = (agora_b + timedelta(hours=23, minutes=55)).strftime("%Y-%m-%d %H:%M:%S")
            fim = (agora_b + timedelta(hours=24, minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
            with db._read() as _conn:
                _rows = _conn.execute(
                    "SELECT a.id, a.cliente, a.telefone, a.data_hora, s.nome AS servico_nome, "
                    "       b.nome AS barbeiro_nome "
                    "FROM agendamentos a "
                    "LEFT JOIN servicos s ON s.id=a.servico_id "
                    "LEFT JOIN barbeiros b ON b.id=a.barbeiro_id "
                    f"WHERE a.barbearia_id=? AND a.status='{ST_AGENDADO}' "
                    "  AND a.data_hora BETWEEN ? AND ? AND a.telefone IS NOT NULL AND a.telefone != ''",
                    (bid_, ini, fim)).fetchall()
            for ag in _rows:
                chave = (ag["id"], "24h_cliente")
                with _lembretes_lock:
                    if chave in _lembretes_enviados:
                        continue
                    if len(_lembretes_enviados) >= _LEMBRETES_MAX:
                        continue  # cap de segurança
                    _lembretes_enviados[chave] = time.time()
                tel = ag["telefone"]
                if not tel:
                    continue
                subs = db.cliente_push_listar_por_tel(tel, bid_)
                if not subs:
                    continue
                hora = ag["data_hora"][11:16]
                barb = ag["barbeiro_nome"] or ""
                corpo = f"{ag['servico_nome'] or 'Corte'} amanhã às {hora}"
                if barb:
                    corpo += f" com {barb}"
                for sub in subs:
                    try:
                        _push_notif_sub(sub["endpoint"], sub["p256dh"], sub["auth"],
                                        bid_, "⏰ Lembrete de amanhã", corpo)
                    except Exception as _e:
                        _log_lim_cli = logging.getLogger("lembretes_cliente")
                        _log_lim_cli.warning("Erro push sub %s: %s", sub.get("endpoint", "?"), _e)
        except Exception as _e:
            _log_lim_cli2 = logging.getLogger("lembretes_cliente")
            _log_lim_cli2.warning("Erro em _enviar_lembretes_push_cliente bid=%s: %s", bid_, _e)


def _ciclo_limpeza(ciclo: int) -> None:
    """Um ciclo do loop de limpeza — extraído para ser testável."""
    _log_lim = logging.getLogger("limpeza")
    try:
        _pc_evict()
    except Exception as e:
        _log_lim.warning("Erro em _pc_evict: %s", e)
    try:
        _rl_evict()
    except Exception as e:
        _log_lim.warning("Erro em _rl_evict: %s", e)
    try:
        db.invalidar_cache_slots()
    except Exception as e:
        _log_lim.warning("Erro em invalidar_cache_slots: %s", e)
    try:
        _enviar_lembretes_push()
    except Exception as e:
        _log_lim.warning("Erro nos lembretes push: %s", e)
    try:
        _enviar_lembretes_push_cliente()
    except Exception as e:
        _log_lim.warning("Erro nos lembretes push cliente: %s", e)
    if ciclo % 6 == 0:
        try:
            for b in db.listar_barbearias(apenas_ativas=True):
                libertados = db.limpar_em_andamento_presos(b["id"], horas=8)
                if libertados:
                    _invalidar_idx(b["id"])
        except Exception as e:
            _log_lim.warning("Erro na limpeza automática: %s", e)
        try:
            db.espera_limpar_expiradas()
        except Exception as e:
            _log_lim.warning("Erro ao limpar fila de espera: %s", e)
        try:
            _limite = time.time() - 48 * 3600  # manter 48h
            with _lembretes_lock:
                expirados = [c for c, t in _lembretes_enviados.items() if t < _limite]
                for c in expirados:
                    del _lembretes_enviados[c]
        except Exception as e:
            _log_lim.warning("Erro ao limpar _lembretes_enviados: %s", e)
    # ciclo > 0: nunca corre no arranque. integrity_check(1) faz uma varredura
    # completa da DB e segura um lock de leitura ao nível do ficheiro durante
    # toda a duração; em modo single-worker sem WAL, o primeiro _write() pós-login
    # bloquearia atrás dela (até busy_timeout=60s) → "travou logo no login" após
    # cada deploy/reload. Adiado para o 1.º ciclo diário (288×5min ≈ 24h).
    if ciclo > 0 and ciclo % 288 == 0:
        try:
            db.desativar_planos_expirados()
        except Exception as e:
            _log_lim.warning("Erro em desativar_planos_expirados: %s", e)
        try:
            from db._conn import _read
            from helpers_security import alerta_critico
            # Usa a conexão de leitura (_READ_CONN/_READ_LOCK), nunca a write conn
            # partilhada sem _CONN_LOCK — evita uso concorrente do mesmo _CONN.
            with _read() as _c:
                row = _c.execute("PRAGMA integrity_check(1)").fetchone()
            ok  = row[0] if row else "error"
            if ok != "ok":
                _log_lim.error("DB integrity_check falhou: %s", ok)
                alerta_critico("DB corrompida", f"integrity_check: {ok}")
        except Exception as e:
            _log_lim.error("Erro em integrity_check: %s", e)


def _thread_limpeza():
    """Background thread: chama _ciclo_limpeza() em loop com sleeps para ceder o GIL."""
    _indices_prontos.wait(timeout=180)
    _ciclo = 0
    _log_tl = logging.getLogger("limpeza")
    while True:
        time.sleep(0.05)
        try:
            _ciclo_limpeza(_ciclo)
        except Exception as _e_tl:
            _log_tl.error("_ciclo_limpeza falhou inesperadamente ciclo=%s: %s", _ciclo, _e_tl)
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
            logging.getLogger("migr").warning(f"MIGR_HASH id={_r[0]} tipo={_r[4][:20]}")
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
