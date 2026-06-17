"""tests/test_load.py — Load testing concorrente da app Flask.

Usa werkzeug.serving.make_server para arrancar um servidor real em background
(shutdown limpo, sem reimportação de módulos, sem app.run() em thread).

Excluído do suite normal e do gate de deploy.
Correr manualmente: pytest tests/test_load.py -v -s
"""

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
import requests

# ── Configuração ──────────────────────────────────────────────
WORKERS = 50
REQUESTS_PER_WORKER = 4   # 200 pedidos total
TIMEOUT = 10
MAX_ERROR_RATE = 0.05     # 5%
MAX_P95_MS = 2000         # 2s
MIN_RPS = 20              # req/s mínimo


# ── Fixture: servidor werkzeug em thread ─────────────────────
@pytest.fixture(scope="module")
def servidor_load(tmp_path_factory):
    """Arranca Flask via make_server numa porta livre. Shutdown garantido."""
    from werkzeug.serving import make_server

    tmp = tmp_path_factory.mktemp("load_db")

    # Importar app com DB temporária via env
    import os
    os.environ.setdefault("BARBEARIA_DB_PATH", str(tmp / "load.db"))

    from app import app as flask_app
    flask_app.config["TESTING"] = True
    flask_app.config.setdefault("SECRET_KEY", "load-test-key")

    # Porta livre
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]

    srv = make_server("127.0.0.1", porta, flask_app)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    base = f"http://127.0.0.1:{porta}"

    # Aguardar arranque (máx 8s)
    for _ in range(16):
        try:
            if requests.get(f"{base}/login", timeout=1).status_code in (200, 302, 404):
                break
        except Exception:
            pass
        time.sleep(0.5)

    yield base

    srv.shutdown()


# ── Helpers ──────────────────────────────────────────────────

def _get(url: str, session: requests.Session) -> dict:
    t0 = time.perf_counter()
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        return {"ms": (time.perf_counter() - t0) * 1000, "status": r.status_code, "erro": None}
    except Exception as e:
        return {"ms": (time.perf_counter() - t0) * 1000, "status": 0, "erro": str(e)}


def _post_login(url: str, session: requests.Session, user: str, pw: str) -> dict:
    t0 = time.perf_counter()
    try:
        r = session.post(url, data={"usuario": user, "senha": pw},
                         timeout=TIMEOUT, allow_redirects=False)
        return {"ms": (time.perf_counter() - t0) * 1000, "status": r.status_code, "erro": None}
    except Exception as e:
        return {"ms": (time.perf_counter() - t0) * 1000, "status": 0, "erro": str(e)}


def _pct(dados: list, p: float) -> float:
    s = sorted(dados)
    return s[min(int(len(s) * p / 100), len(s) - 1)]


def _report(resultados: list, label: str) -> dict:
    ms   = [r["ms"] for r in resultados]
    errs = [r for r in resultados if r["status"] >= 500 or r["status"] == 0]
    taxa = len(errs) / len(resultados) if resultados else 1.0
    p50, p95, p99 = _pct(ms, 50), _pct(ms, 95), _pct(ms, 99)
    print(f"\n  [{label}] n={len(resultados)} "
          f"p50={p50:.0f}ms p95={p95:.0f}ms p99={p99:.0f}ms "
          f"erros={len(errs)} ({taxa*100:.1f}%)")
    return {"p50": p50, "p95": p95, "p99": p99, "taxa_erros": taxa}


def _disparar(url: str, fn=None) -> list:
    fn = fn or (lambda s: _get(url, s))
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(fn, requests.Session()) for _ in range(WORKERS * REQUESTS_PER_WORKER)]
        return [f.result() for f in as_completed(futs)]


# ── Testes ────────────────────────────────────────────────────

class TestLoadEndpoints:

    def test_login_get(self, servidor_load):
        """GET /login: 200 pedidos, p95 < 2s, erros < 5%."""
        url = f"{servidor_load}/login"
        stats = _report(_disparar(url), "GET /login")
        assert stats["taxa_erros"] <= MAX_ERROR_RATE
        assert stats["p95"] <= MAX_P95_MS

    def test_healthz(self, servidor_load):
        """GET /healthz: endpoint leve — p95 < 500ms."""
        url = f"{servidor_load}/healthz"
        stats = _report(_disparar(url), "GET /healthz")
        assert stats["taxa_erros"] <= MAX_ERROR_RATE
        assert stats["p95"] <= 500

    def test_root(self, servidor_load):
        """GET /: landing page aguenta carga."""
        url = f"{servidor_load}/"
        stats = _report(_disparar(url), "GET /")
        assert stats["taxa_erros"] <= MAX_ERROR_RATE
        assert stats["p95"] <= MAX_P95_MS

    def test_post_login_sem_5xx(self, servidor_load):
        """50 POSTs simultâneos ao /login não devem causar 5xx."""
        url = f"{servidor_load}/login"
        resultados = []
        with ThreadPoolExecutor(max_workers=50) as ex:
            futs = [ex.submit(_post_login, url, requests.Session(), f"u{i}", "errado")
                    for i in range(50)]
            resultados = [f.result() for f in as_completed(futs)]
        errs_5xx = [r for r in resultados if r["status"] >= 500]
        _report(resultados, "POST /login (errado)")
        assert len(errs_5xx) == 0, f"{len(errs_5xx)} respostas 5xx"

    def test_throughput_minimo(self, servidor_load):
        """Throughput ≥ 20 req/s em GET /login com 20 workers."""
        url = f"{servidor_load}/login"
        n = 100
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=20) as ex:
            futs = [ex.submit(_get, url, requests.Session()) for _ in range(n)]
            [f.result() for f in as_completed(futs)]
        rps = n / (time.perf_counter() - t0)
        print(f"\n  Throughput: {rps:.1f} req/s")
        assert rps >= MIN_RPS, f"Throughput {rps:.1f} req/s < mínimo {MIN_RPS}"
