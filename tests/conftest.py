"""tests/conftest.py — Fixtures e hooks partilhados por todos os testes.

Garante que o estado do rate limiter é limpo antes de cada ficheiro de testes,
evitando interferências entre módulos quando o suite corre no mesmo processo.
"""

import pytest


@pytest.fixture(autouse=True, scope="module")
def reset_rate_limiter():
    """Limpa o estado do rate limiter SQLite antes de cada módulo de testes.
    Evita que tentativas de login acumuladas num módulo bloqueiem o módulo seguinte.
    """
    try:
        import db.rate_limit as _rl
        _rl.reset_all()
    except Exception:
        pass
    yield
    # Limpar também no teardown para não deixar estado sujo
    try:
        import db.rate_limit as _rl
        _rl.reset_all()
    except Exception:
        pass
