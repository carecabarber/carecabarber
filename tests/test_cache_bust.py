"""Testes unitários para o mecanismo de cache bust cross-worker (helpers_booking.py).

Cobre:
  - _bust_bid_from_key: extracção de barbearia_id de todas as formas de chave
  - _bust_mtime / _bust_touch: leitura e escrita do ficheiro de bust
  - _pc_set / _pc_get: comportamento normal e invalidação cross-worker
  - _pc_evict: evicção por TTL com o novo formato de tuplo 3 elementos
  - _invalidar_idx: toca no ficheiro de bust ao invalidar
"""

import os
import time
import pytest
from unittest.mock import patch


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def limpar_pcache():
    """Garante que o _pcache está limpo antes e depois de cada teste."""
    from helpers_booking import _pcache, _pcache_lock
    with _pcache_lock:
        _pcache.clear()
    yield
    with _pcache_lock:
        _pcache.clear()


@pytest.fixture()
def tmp_bust(tmp_path, monkeypatch):
    """Redirige os ficheiros de bust para tmp_path para não poluir /tmp."""
    import helpers_booking as hb
    monkeypatch.setattr(hb, "_bust_path", lambda bid: str(tmp_path / f"ccb_bust_{bid}"))
    return tmp_path


# ─── _bust_bid_from_key ───────────────────────────────────────────────────────

class TestBustBidFromKey:
    """Extracção de barbearia_id das diferentes formas de chave de cache."""

    from helpers_booking import _bust_bid_from_key  # importado uma vez

    @pytest.mark.parametrize("key,expected", [
        ("idx_ag:1:2024-01-15",       1),
        ("idx_ag:42:2024-12-31",      42),
        ("resumo:7:2024-01-15",       7),
        ("bloq:3:2024-06-10",         3),
        ("novos:99:2024-01-01",       99),
        ("lemb:5:2024-03-20",         5),
        # estado tem role no 2º segmento
        ("estado:chefe:2:2024-01-15", 2),
        ("estado:barb:8:2024-01-15",  8),
        ("estado:cli:11:2024-01-15",  11),
        # chaves sem escopo de barbearia → None
        ("plano:1:",                  None),
        ("slots:1:barbeiro",          None),
        ("sessao:abc",                None),
        ("",                          None),
    ])
    def test_extraccao(self, key, expected):
        from helpers_booking import _bust_bid_from_key
        assert _bust_bid_from_key(key) == expected


# ─── _bust_mtime / _bust_touch ───────────────────────────────────────────────

class TestBustFileOps:

    def test_mtime_sem_ficheiro_retorna_zero(self, tmp_bust):
        from helpers_booking import _bust_mtime
        assert _bust_mtime(999) == 0.0

    def test_touch_cria_ficheiro(self, tmp_bust):
        from helpers_booking import _bust_touch, _bust_mtime
        _bust_touch(1)
        assert _bust_mtime(1) > 0.0

    def test_touch_actualiza_mtime(self, tmp_bust):
        from helpers_booking import _bust_touch, _bust_mtime
        _bust_touch(1)
        t1 = _bust_mtime(1)
        time.sleep(0.02)   # 20ms de margem para o FS
        _bust_touch(1)
        t2 = _bust_mtime(1)
        assert t2 >= t1    # deve ser igual ou mais recente

    def test_touch_falha_silenciosa(self, monkeypatch):
        """_bust_touch em path inválido não lança excepção."""
        import helpers_booking as hb
        monkeypatch.setattr(hb, "_bust_path", lambda bid: "/nao/existe/ccb_bust_1")
        hb._bust_touch(1)  # não deve lançar


# ─── _pc_set / _pc_get — comportamento normal ─────────────────────────────────

class TestPcSetGet:

    def test_set_e_get_normal(self, tmp_bust):
        from helpers_booking import _pc_set, _pc_get
        _pc_set("idx_ag:1:2024-01-15", {"x": 1}, ttl=60)
        assert _pc_get("idx_ag:1:2024-01-15") == {"x": 1}

    def test_get_expirado_retorna_none(self, tmp_bust):
        from helpers_booking import _pc_set, _pc_get
        _pc_set("idx_ag:1:2024-01-15", "valor", ttl=0.001)
        time.sleep(0.05)
        assert _pc_get("idx_ag:1:2024-01-15") is None

    def test_get_chave_inexistente(self):
        from helpers_booking import _pc_get
        assert _pc_get("idx_ag:999:nao_existe") is None

    def test_chaves_sem_bid_nao_checam_bust(self, tmp_bust):
        """Chaves sem barbearia_id (ex: 'plano:1:') não dependem do ficheiro de bust."""
        from helpers_booking import _pc_set, _pc_get, _bust_touch
        _pc_set("plano:1:", "ok", ttl=60)
        _bust_touch(1)   # tocar em barbearia 1 não deve afectar esta chave
        assert _pc_get("plano:1:") == "ok"


# ─── _pc_get — invalidação cross-worker ──────────────────────────────────────

class TestCacheInvalidacaoCrossWorker:

    def test_miss_quando_bust_mais_recente(self, tmp_bust):
        """Outro worker invalidou → pc_get deve devolver None."""
        from helpers_booking import _pc_set, _pc_get, _bust_touch
        _pc_set("idx_ag:1:2024-01-15", "valor_antigo", ttl=60)
        # Simular outro worker a tocar no ficheiro de bust
        time.sleep(0.02)
        _bust_touch(1)
        # O dado cached agora é mais antigo que o ficheiro → miss
        assert _pc_get("idx_ag:1:2024-01-15") is None

    def test_hit_quando_bust_anterior_ao_set(self, tmp_bust):
        """Bust tocado ANTES do set → cache ainda válida."""
        from helpers_booking import _pc_set, _pc_get, _bust_touch
        _bust_touch(1)
        time.sleep(0.02)
        _pc_set("idx_ag:1:2024-01-15", "valor_novo", ttl=60)
        # O set foi depois do bust → deve ser hit
        assert _pc_get("idx_ag:1:2024-01-15") == "valor_novo"

    def test_diferentes_barbearias_isoladas(self, tmp_bust):
        """Bust de barbearia 2 não invalida cache de barbearia 1."""
        from helpers_booking import _pc_set, _pc_get, _bust_touch
        _pc_set("idx_ag:1:2024-01-15", "barb1", ttl=60)
        _bust_touch(2)   # invalidar barbearia 2
        assert _pc_get("idx_ag:1:2024-01-15") == "barb1"

    def test_bust_invalida_estado_chefe(self, tmp_bust):
        """Chaves 'estado:chefe:N:' também são invalidadas pelo bust de N."""
        from helpers_booking import _pc_set, _pc_get, _bust_touch
        _pc_set("estado:chefe:5:2024-01-15", ["ag1", "ag2"], ttl=60)
        time.sleep(0.02)
        _bust_touch(5)
        assert _pc_get("estado:chefe:5:2024-01-15") is None


# ─── _pc_evict — novo formato de tuplo 3 elementos ───────────────────────────

class TestPcEvict:

    def test_evict_remove_expirados(self, tmp_bust):
        from helpers_booking import _pc_set, _pc_evict, _pcache, _pcache_lock
        _pc_set("idx_ag:1:2024-01-15", "x", ttl=0.001)
        _pc_set("idx_ag:2:2024-01-15", "y", ttl=60)
        time.sleep(0.05)
        _pc_evict()
        with _pcache_lock:
            assert "idx_ag:1:2024-01-15" not in _pcache
            assert "idx_ag:2:2024-01-15" in _pcache

    def test_evict_nao_remove_validos(self, tmp_bust):
        from helpers_booking import _pc_set, _pc_evict, _pcache, _pcache_lock
        _pc_set("idx_ag:3:2024-01-15", "val", ttl=60)
        _pc_evict()
        with _pcache_lock:
            assert "idx_ag:3:2024-01-15" in _pcache


# ─── _invalidar_idx toca no bust file ────────────────────────────────────────

class TestInvalidarIdxBust:

    def test_invalidar_toca_bust(self, tmp_bust):
        """_invalidar_idx deve criar/tocar o ficheiro de bust da barbearia."""
        from helpers_booking import _invalidar_idx, _bust_mtime
        import database as db
        # Bust não existe ainda
        assert _bust_mtime(7) == 0.0
        with patch.object(db, "invalidar_cache_slots"):
            _invalidar_idx(7)
        assert _bust_mtime(7) > 0.0

    def test_invalidar_limpa_cache_local(self, tmp_bust):
        """_invalidar_idx remove entradas do _pcache local."""
        from helpers_booking import _pc_set, _pc_get, _invalidar_idx
        import database as db
        _pc_set("idx_ag:4:2024-01-15", "dado", ttl=60)
        with patch.object(db, "invalidar_cache_slots"):
            _invalidar_idx(4)
        assert _pc_get("idx_ag:4:2024-01-15") is None


# ─── Cache de slots (db/_conn.py) — invalidação cross-worker ─────────────────
# A cache de slots disponíveis (caminho público de marcação) usa o MESMO
# ficheiro-sentinela /tmp/ccb_bust_{bid} que o _pcache do dashboard. Antes
# desta correcção, um worker B servia slots obsoletos até ao TTL (até 60 s)
# depois de um booking num worker A — podendo mostrar como livre um slot ocupado.

class TestSlotsCacheCrossWorker:

    @pytest.fixture(autouse=True)
    def limpar_slots(self):
        import db._conn as c
        with c._slots_cache_lock:
            c._slots_cache.clear()
        yield
        with c._slots_cache_lock:
            c._slots_cache.clear()

    @pytest.fixture()
    def tmp_slots_bust(self, tmp_path, monkeypatch):
        import db._conn as c
        monkeypatch.setattr(c, "_slots_bust_path",
                            lambda bid: str(tmp_path / f"ccb_bust_{bid}"))
        return tmp_path

    def test_bid_from_key(self):
        import db._conn as c
        assert c._slots_bid_from_key("99:1:2030-01-01:30") == "99"
        assert c._slots_bid_from_key("sem_bid") is None
        assert c._slots_bid_from_key("") is None

    def test_set_e_get_normal(self, tmp_slots_bust):
        import db._conn as c
        c._slots_cache_set("99:1:2030-01-01:30", ["A"], 60)
        assert c._slots_cache_get("99:1:2030-01-01:30") == ["A"]

    def test_miss_quando_bust_mais_recente(self, tmp_slots_bust):
        """Outro worker invalidou → get devolve None mesmo sem expirar."""
        import db._conn as c
        key = "99:1:2030-01-01:30"
        c._slots_cache_set(key, ["A"], 60)
        time.sleep(0.02)
        c._slots_bust_touch(99)   # outro worker
        assert c._slots_cache_get(key) is None

    def test_hit_quando_bust_anterior(self, tmp_slots_bust):
        import db._conn as c
        key = "99:1:2030-01-01:30"
        c._slots_bust_touch(99)
        time.sleep(0.02)
        c._slots_cache_set(key, ["B"], 60)
        assert c._slots_cache_get(key) == ["B"]

    def test_barbearias_isoladas(self, tmp_slots_bust):
        import db._conn as c
        c._slots_cache_set("1:0:2030-01-01:30", ["barb1"], 60)
        c._slots_bust_touch(2)   # invalidar outra barbearia
        assert c._slots_cache_get("1:0:2030-01-01:30") == ["barb1"]

    def test_invalidar_cache_slots_toca_bust(self, tmp_slots_bust):
        """invalidar_cache_slots(bid) sinaliza os outros workers via ficheiro."""
        import db._conn as c
        assert c._slots_bust_mtime(7) == 0.0
        c.invalidar_cache_slots(7)
        assert c._slots_bust_mtime(7) > 0.0

    def test_gc_sem_bid_nao_toca_bust(self, tmp_slots_bust):
        """invalidar_cache_slots() (GC, sem bid) não cria ficheiros de bust."""
        import db._conn as c
        c.invalidar_cache_slots(None)
        # nenhum ficheiro criado em tmp_path
        assert list(tmp_slots_bust.iterdir()) == []
