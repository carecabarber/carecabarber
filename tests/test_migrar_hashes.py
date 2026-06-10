"""tests/test_migrar_hashes.py — Cobertura de _migrar_hashes_lentos() em app.py.

A função _migrar_hashes_lentos() (linhas 525-552) corre no arranque para migrar
hashes scrypt/pbkdf2:600000 para pbkdf2:10000. É guardada atrás de um flag
(.migr2_done). Para testar, removemos o flag temporariamente e mockamos
sqlite3.connect para não tocar na base de dados de produção.

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_migrar_hashes.py -v
"""

import os, shutil, sqlite3, tempfile, pytest
from unittest.mock import patch, MagicMock

os.environ.setdefault("SECRET_KEY", "test-migr-secret")

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


PROJ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAG_PATH = os.path.join(PROJ_DIR, ".migr2_done")
FLAG_BAK  = FLAG_PATH + ".bak_test"


@pytest.fixture(autouse=True)
def restaurar_flag():
    """Move o flag antes do teste e restaura-o no teardown."""
    tinha_flag = os.path.exists(FLAG_PATH)
    if tinha_flag:
        shutil.move(FLAG_PATH, FLAG_BAK)
    yield
    # Limpar flag criado pelo teste
    if os.path.exists(FLAG_PATH):
        os.remove(FLAG_PATH)
    # Restaurar flag original
    if tinha_flag and os.path.exists(FLAG_BAK):
        shutil.move(FLAG_BAK, FLAG_PATH)


@pytest.fixture(scope="module")
def app_module():
    import app as _app
    return _app


# ══════════════════════════════════════════════════════════════
#  Testes
# ══════════════════════════════════════════════════════════════

class TestMigrarHashesLentos:

    def test_flag_ausente_sem_hashes_lentos(self, app_module, tmp_path):
        """Flag ausente + DB sem hashes lentos → cria flag, retorna sem migrar."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []  # sem rows

        with patch("sqlite3.connect", return_value=mock_conn):
            app_module._migrar_hashes_lentos()

        assert os.path.exists(FLAG_PATH), "Flag deve ser criado"
        mock_conn.close.assert_called_once()

    def test_flag_presente_retorna_cedo(self, app_module):
        """Se o flag existir, a função retorna imediatamente sem abrir DB."""
        # Re-criar o flag (removido pelo autouse fixture)
        open(FLAG_PATH, "w").close()

        with patch("sqlite3.connect") as mock_sq:
            app_module._migrar_hashes_lentos()

        mock_sq.assert_not_called()

    def test_flag_ausente_com_hashes_lentos(self, app_module, tmp_path):
        """Flag ausente + DB com 1 hash scrypt → migra e cria .migr_tmp."""
        out_path = os.path.join(PROJ_DIR, ".migr_tmp")
        # Limpar .migr_tmp anterior se existir
        if os.path.exists(out_path):
            os.remove(out_path)

        # Conexão real a DB temp com 1 row de hash lento
        tmp_db = str(tmp_path / "barbearia.db")
        conn_real = sqlite3.connect(tmp_db)
        conn_real.execute(
            "CREATE TABLE barbeiros "
            "(id INT, nome TEXT, username TEXT, role TEXT, password_hash TEXT)")
        conn_real.execute(
            "INSERT INTO barbeiros VALUES (1, 'Teste', 'teste_u', 'barbeiro', 'scrypt:32768:8:1$xxx')")
        conn_real.commit()
        conn_real.close()

        # Redirigir sqlite3.connect para a DB temp
        with patch("sqlite3.connect", return_value=sqlite3.connect(tmp_db)):
            app_module._migrar_hashes_lentos()

        assert os.path.exists(FLAG_PATH), "Flag deve ser criado"
        # O hash deve ter sido actualizado na DB temp
        conn_check = sqlite3.connect(tmp_db)
        row = conn_check.execute(
            "SELECT password_hash FROM barbeiros WHERE id=1").fetchone()
        conn_check.close()
        assert not row[0].startswith("scrypt:"), "Hash deve ter sido migrado"
        # Limpar .migr_tmp criado
        if os.path.exists(out_path):
            os.remove(out_path)

    def test_exception_na_db_loga_erro(self, app_module, caplog):
        """Excepção ao conectar à DB → loga erro (linha 552)."""
        import logging
        with patch("sqlite3.connect", side_effect=Exception("DB inacessível")):
            with caplog.at_level(logging.ERROR, logger="migr"):
                app_module._migrar_hashes_lentos()
        assert "migra" in caplog.text.lower() or "erro" in caplog.text.lower()
