"""tests/test_ciclo_limpeza.py — Cobertura de _ciclo_limpeza() em app.py.

Testa cada ramo da função extraída do loop de background _thread_limpeza:
  - ciclo normal (ciclo=1): _pc_evict, _rl_evict, invalidar_cache_slots, lembretes
  - ciclo % 6 == 0 (ciclo=0,6): limpar_em_andamento_presos + _invalidar_idx
  - ciclo % 288 == 0 (ciclo=0): desativar_planos_expirados
  - exceptions em cada bloco try/except → warning logado, sem crash

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_ciclo_limpeza.py -v
"""

import os, sys, logging, pytest
from unittest.mock import patch, MagicMock

os.environ.setdefault("SECRET_KEY", "test-ciclo-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(scope="module")
def app_module():
    import app as _app
    return _app


# ══════════════════════════════════════════════════════════════
#  Testes de _ciclo_limpeza
# ══════════════════════════════════════════════════════════════

class TestCicloLimpeza:

    def _run(self, app_module, ciclo,
             pc_evict=None, rl_evict=None,
             inv_slots=None, lembretes=None,
             listar=None, limpar_presos=None,
             desativar=None):
        """Corre _ciclo_limpeza(ciclo) com todas as dependências mockadas."""
        with patch("app._pc_evict",            side_effect=pc_evict or (lambda: None)), \
             patch("app._rl_evict",            side_effect=rl_evict or (lambda: None)), \
             patch("app.db.invalidar_cache_slots",
                   side_effect=inv_slots or (lambda: None)), \
             patch("app._enviar_lembretes_push",
                   side_effect=lembretes or (lambda: None)), \
             patch("app.db.listar_barbearias",
                   return_value=listar if listar is not None else []), \
             patch("app.db.limpar_em_andamento_presos",
                   return_value=limpar_presos if limpar_presos is not None else 0), \
             patch("app.db.desativar_planos_expirados",
                   side_effect=desativar or (lambda: None)), \
             patch("app._invalidar_idx"):
            app_module._ciclo_limpeza(ciclo)

    # ── Ciclo normal (não divisível por 6 nem 288) ────────────

    def test_ciclo_normal_sem_erros(self, app_module):
        """ciclo=1: percorre todos os try/except sem erros."""
        self._run(app_module, ciclo=1)

    def test_ciclo_normal_pc_evict_exception(self, app_module, caplog):
        """_pc_evict lança exceção → warning logado, execução continua."""
        with caplog.at_level(logging.WARNING, logger="limpeza"):
            self._run(app_module, ciclo=1,
                      pc_evict=RuntimeError("falha pc_evict"))
        assert "pc_evict" in caplog.text

    def test_ciclo_normal_rl_evict_exception(self, app_module, caplog):
        """_rl_evict lança exceção → warning logado."""
        with caplog.at_level(logging.WARNING, logger="limpeza"):
            self._run(app_module, ciclo=1,
                      rl_evict=RuntimeError("falha rl_evict"))
        assert "rl_evict" in caplog.text

    def test_ciclo_normal_inv_slots_exception(self, app_module, caplog):
        """invalidar_cache_slots lança exceção → warning logado."""
        with caplog.at_level(logging.WARNING, logger="limpeza"):
            self._run(app_module, ciclo=1,
                      inv_slots=RuntimeError("falha inv_slots"))
        assert "invalidar_cache_slots" in caplog.text

    def test_ciclo_normal_lembretes_exception(self, app_module, caplog):
        """_enviar_lembretes_push lança exceção → warning logado."""
        with caplog.at_level(logging.WARNING, logger="limpeza"):
            self._run(app_module, ciclo=1,
                      lembretes=RuntimeError("falha lembretes"))
        assert "lembretes" in caplog.text

    # ── Ciclo % 6 == 0 ───────────────────────────────────────

    def test_ciclo_6_limpa_presos_sem_libertados(self, app_module):
        """ciclo=6: limpar_em_andamento_presos retorna 0 → sem _invalidar_idx."""
        barbearias = [{"id": 1}]
        with patch("app._invalidar_idx") as mock_inv:
            self._run(app_module, ciclo=6, listar=barbearias, limpar_presos=0)
            mock_inv.assert_not_called()

    def test_ciclo_6_limpa_presos_com_libertados(self, app_module):
        """ciclo=6: limpar_em_andamento_presos retorna > 0 → _invalidar_idx chamado."""
        barbearias = [{"id": 42}]
        with patch("app._invalidar_idx") as mock_inv, \
             patch("app._pc_evict"), \
             patch("app._rl_evict"), \
             patch("app.db.invalidar_cache_slots"), \
             patch("app._enviar_lembretes_push"), \
             patch("app.db.listar_barbearias", return_value=barbearias), \
             patch("app.db.limpar_em_andamento_presos", return_value=3), \
             patch("app.db.desativar_planos_expirados"):
            app_module._ciclo_limpeza(6)
        mock_inv.assert_called_once_with(42)

    def test_ciclo_6_listar_exception(self, app_module, caplog):
        """ciclo=6: listar_barbearias lança exceção → warning logado."""
        with caplog.at_level(logging.WARNING, logger="limpeza"):
            self._run(app_module, ciclo=6,
                      listar=None,  # será substituído pelo side_effect
                      )
            # Forçar via patch directo
        with caplog.at_level(logging.WARNING, logger="limpeza"), \
             patch("app._pc_evict"), \
             patch("app._rl_evict"), \
             patch("app.db.invalidar_cache_slots"), \
             patch("app._enviar_lembretes_push"), \
             patch("app.db.listar_barbearias", side_effect=RuntimeError("db erro")), \
             patch("app.db.desativar_planos_expirados"), \
             patch("app._invalidar_idx"):
            app_module._ciclo_limpeza(6)
        assert "limpeza" in caplog.text

    # ── Ciclo % 288 == 0 ─────────────────────────────────────

    def test_ciclo_288_desativar_planos(self, app_module):
        """ciclo=0 (% 288 == 0): desativar_planos_expirados é chamado."""
        with patch("app._pc_evict"), \
             patch("app._rl_evict"), \
             patch("app.db.invalidar_cache_slots"), \
             patch("app._enviar_lembretes_push"), \
             patch("app.db.listar_barbearias", return_value=[]), \
             patch("app.db.desativar_planos_expirados") as mock_desat, \
             patch("app._invalidar_idx"):
            app_module._ciclo_limpeza(0)
        mock_desat.assert_called_once()

    def test_ciclo_288_desativar_exception(self, app_module, caplog):
        """ciclo=288: desativar_planos_expirados lança exceção → warning logado."""
        with caplog.at_level(logging.WARNING, logger="limpeza"), \
             patch("app._pc_evict"), \
             patch("app._rl_evict"), \
             patch("app.db.invalidar_cache_slots"), \
             patch("app._enviar_lembretes_push"), \
             patch("app.db.listar_barbearias", return_value=[]), \
             patch("app.db.desativar_planos_expirados",
                   side_effect=RuntimeError("planos erro")), \
             patch("app._invalidar_idx"):
            app_module._ciclo_limpeza(288)
        assert "desativar" in caplog.text

    def test_ciclo_nao_multiplo_de_6_nao_chama_limpar(self, app_module):
        """ciclo=1: listar_barbearias NÃO é chamado."""
        with patch("app._pc_evict"), \
             patch("app._rl_evict"), \
             patch("app.db.invalidar_cache_slots"), \
             patch("app._enviar_lembretes_push"), \
             patch("app.db.listar_barbearias") as mock_listar, \
             patch("app.db.desativar_planos_expirados"), \
             patch("app._invalidar_idx"):
            app_module._ciclo_limpeza(1)
        mock_listar.assert_not_called()

    def test_ciclo_nao_multiplo_de_288_nao_chama_desativar(self, app_module):
        """ciclo=6: desativar_planos_expirados NÃO é chamado."""
        with patch("app._pc_evict"), \
             patch("app._rl_evict"), \
             patch("app.db.invalidar_cache_slots"), \
             patch("app._enviar_lembretes_push"), \
             patch("app.db.listar_barbearias", return_value=[]), \
             patch("app.db.desativar_planos_expirados") as mock_desat, \
             patch("app._invalidar_idx"):
            app_module._ciclo_limpeza(6)
        mock_desat.assert_not_called()
