"""
Testes de regressão — protecção anti-clonagem.

Garantem que:
  1. Crawlers de IA e ferramentas de scraping recebem 403 nas páginas públicas.
  2. Browsers normais NÃO são bloqueados.
  3. Rotas internas (/health) e webhooks NÃO são afectados (monitorização segura).
  4. /robots.txt é servido e proíbe os bots de IA.

Correr: cd ~/Documentos/barbearia && python -m pytest tests/test_anti_clone.py -v
"""
import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret-key-apenas-para-testes")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tests.test_smoke import client  # reutiliza o fixture com BD em memória  # noqa: F401,E402


# ── UAs que DEVEM ser bloqueados nas páginas públicas ──────────────
UAS_BLOQUEADOS = [
    "Mozilla/5.0 (compatible; GPTBot/1.1; +https://openai.com/gptbot)",
    "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "anthropic-ai",
    "Mozilla/5.0 (compatible; CCBot/2.0; +https://commoncrawl.org/faq/)",
    "Mozilla/5.0 (compatible; PerplexityBot/1.0)",
    "Bytespider",
    "curl/8.0.1",
    "python-requests/2.31.0",
    "Scrapy/2.11 (+https://scrapy.org)",
    "Wget/1.21",
]

# ── UAs de browsers legítimos que NÃO devem ser bloqueados ─────────
UAS_LEGITIMOS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
]


def test_bots_ia_bloqueados_no_login(client):
    for ua in UAS_BLOQUEADOS:
        r = client.get("/login", headers={"User-Agent": ua})
        assert r.status_code == 403, f"UA de bot devia ser 403: {ua!r} -> {r.status_code}"


def test_browser_normal_nao_bloqueado_no_login(client):
    for ua in UAS_LEGITIMOS:
        r = client.get("/login", headers={"User-Agent": ua})
        assert r.status_code != 403, f"Browser legítimo bloqueado por engano: {ua!r}"


def test_health_nao_afectado_por_curl(client):
    # Monitorização usa curl/requests — nunca pode ser bloqueada.
    r = client.get("/health", headers={"User-Agent": "curl/8.0.1"})
    assert r.status_code != 403


def test_sem_user_agent_nao_bloqueia(client):
    # Verificações internas sem UA não devem apanhar 403.
    r = client.get("/login", headers={"User-Agent": ""})
    assert r.status_code != 403


def test_robots_txt_servido_e_proibe_bots(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    corpo = r.get_data(as_text=True)
    assert "GPTBot" in corpo
    assert "ClaudeBot" in corpo
    assert "Disallow: /" in corpo
    # caminhos-armadilha proibidos a bots honestos (protege Googlebot do honeypot)
    assert "/config/" in corpo and "/wp-admin/" in corpo


# ── (C) Honeypot ───────────────────────────────────────────────────
def test_honeypot_bane_ip(client):
    br = {"User-Agent": "Mozilla/5.0 Chrome/120"}
    ov = {"REMOTE_ADDR": "203.0.113.7"}
    # bater na armadilha → 404
    r = client.get("/wp-admin/", headers=br, environ_overrides=ov)
    assert r.status_code == 404
    # a seguir, o IP fica banido das páginas públicas → 403
    r = client.get("/login", headers=br, environ_overrides=ov)
    assert r.status_code == 403


def test_honeypot_nao_bane_googlebot(client):
    br = {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"}
    ov = {"REMOTE_ADDR": "203.0.113.8"}
    client.get("/config/backup.json", headers=br, environ_overrides=ov)
    r = client.get("/login", headers=br, environ_overrides=ov)
    assert r.status_code != 403, "Googlebot não pode ser banido pelo honeypot"


# ── (B) Rate-limit anti-ripper ─────────────────────────────────────
def test_rate_limit_trava_ripper(client):
    br = {"User-Agent": "Mozilla/5.0 Chrome/120"}
    ov = {"REMOTE_ADDR": "198.51.100.42"}
    codes = [client.get("/login", headers=br, environ_overrides=ov).status_code
             for _ in range(45)]
    assert 429 in codes, "Rajada de pedidos do mesmo IP deve disparar 429"
    assert codes[0] != 429, "Os primeiros pedidos não devem ser bloqueados"


# ── (E) Beacon de detecção de clones ───────────────────────────────
def test_beacon_devolve_pixel_gif(client):
    br = {"User-Agent": "Mozilla/5.0 Chrome/120"}
    r = client.get("/_cb/px.gif?h=clone.example.com&o=carecabarber.pythonanywhere.com"
                   "&s=barbearia-x&t=2026-07-09T10:00:00", headers=br)
    assert r.status_code == 200
    assert r.mimetype == "image/gif"
    assert r.get_data().startswith(b"GIF89a")
    assert "no-store" in r.headers.get("Cache-Control", "")


def test_beacon_nunca_bloqueado_nem_falha(client):
    # Sem parâmetros e com UA de bot — tem de continuar a devolver o pixel (nunca 4xx/5xx).
    r = client.get("/_cb/px.gif", headers={"User-Agent": "curl/8.0.1"})
    assert r.status_code == 200
    assert r.get_data().startswith(b"GIF89a")


def test_login_embute_beacon_e_host_servidor(client):
    # A página de login tem de conter o beacon e o host renderizado pelo servidor.
    r = client.get("/login", headers={"User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"})
    corpo = r.get_data(as_text=True)
    assert "/_cb/px.gif" in corpo, "beacon anti-clone ausente do login"
    assert "location.host" in corpo
