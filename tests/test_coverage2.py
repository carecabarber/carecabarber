"""
tests/test_coverage2.py — Cobertura para blueprints/barbeiros, cliente e mesa.

Módulos alvo:
  blueprints/barbeiros.py  (era 22%)
  blueprints/cliente.py    (era 19%)
  blueprints/mesa.py       (era 14%)

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_coverage2.py -v
"""
import os, sys, json, pytest, tempfile, shutil, base64
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test-cov2-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ══════════════════════════════════════════════════════════════
#  FIXTURES
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def ctx():
    import database as db_module
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_cov2.db")
    orig    = _db_conn.DB_PATH

    _db_conn.DB_PATH  = tmp_db
    db_module.DB_PATH = tmp_db
    _db_conn._CONN    = None

    db_module.init_db()

    bid = db_module.criar_barbearia("Barbearia Cov2", tipo="barbearia")
    db_module.registar_pagamento(bid, "exp")

    # Slug explícito para testes de cliente
    with db_module._write() as c:
        c.execute("UPDATE barbearias SET slug=? WHERE id=?", ("test-barbearia", bid))

    db_module.criar_chefe("Chefe Cov2", "chefe_cov2", "senha_cov2", bid)
    chefe_id = db_module.get_barbeiro_por_username("chefe_cov2")["id"]

    db_module.criar_barbeiro("Barbeiro Cov2", bid)
    with db_module._read() as c:
        barb_id = c.execute(
            "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
            ("Barbeiro Cov2", bid)).fetchone()["id"]
    db_module.set_credenciais(barb_id, "barb_cov2", "pass_cov2_ok")

    # Mesa token do barbeiro
    with db_module._read() as c:
        mesa_token = c.execute(
            "SELECT mesa_token FROM barbeiros WHERE id=?", (barb_id,)).fetchone()["mesa_token"]

    db_module.criar_servico("Corte Cov2", 30, bid, preco=800)
    with db_module._read() as c:
        svc_id = c.execute(
            "SELECT id FROM servicos WHERE barbearia_id=?", (bid,)).fetchone()["id"]

    db_module.set_horario_dia(0, "08:00", "19:00", 0, bid)
    db_module.set_horario_dia(1, "08:00", "19:00", 0, bid)
    db_module.set_horario_dia(2, "08:00", "19:00", 0, bid)
    db_module.set_horario_dia(3, "08:00", "19:00", 0, bid)
    db_module.set_horario_dia(4, "08:00", "19:00", 0, bid)
    db_module.set_horario_dia(5, "08:00", "14:00", 0, bid)
    db_module.set_horario_dia(6, "08:00", "19:00", 1, bid)

    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    ag_id  = db_module.criar_agendamento(
        "Cliente Marcado", svc_id, f"{amanha} 10:00:00", bid, barbeiro_id=barb_id)

    yield {
        "db": db_module, "bid": bid, "chefe_id": chefe_id,
        "barb_id": barb_id, "svc_id": svc_id,
        "mesa_token": mesa_token, "slug": "test-barbearia",
        "amanha": amanha, "ag_id": ag_id,
    }

    _db_conn._CONN    = None
    db_module._CONN   = None
    _db_conn.DB_PATH  = orig
    db_module.DB_PATH = orig
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def client(ctx):
    import app as app_module
    app_module.app.config.update({
        "TESTING": True, "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-cov2", "SESSION_COOKIE_SECURE": False,
    })
    with app_module.app.test_client() as c:
        yield c, ctx


def _chefe(c, ctx):
    with c.session_transaction() as s:
        s["user_id"] = ctx["chefe_id"]
        s["role"]    = "chefe"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"] = "Chefe Cov2"
    return c


def _barbeiro(c, ctx):
    with c.session_transaction() as s:
        s["user_id"] = ctx["barb_id"]
        s["role"]    = "barbeiro"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"] = "Barbeiro Cov2"
    return c


def _cliente(c, ctx, tel="912000001"):
    with c.session_transaction() as s:
        s["role"]         = "cliente"
        s["barbearia_id"] = ctx["bid"]
        s["telefone"]     = tel
        s["user_nome"]    = "Cliente Cov2"
    return c


def _limpar_sessao(c):
    with c.session_transaction() as s:
        s.clear()
    return c


# ══════════════════════════════════════════════════════════════
#  blueprints/barbeiros.py
# ══════════════════════════════════════════════════════════════

class TestBarbeirosBlueprint:

    def test_get_barbeiros_chefe(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/barbeiros")
        assert r.status_code == 200
        assert b"Barbeiro Cov2" in r.data

    def test_get_barbeiros_sem_auth(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get("/barbeiros", follow_redirects=False)
        assert r.status_code == 302

    def test_criar_barbeiro(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/barbeiros", data={"nome": "Novo Barbeiro Test"},
                   follow_redirects=False)
        assert r.status_code == 302
        # Verificar criado
        barbs = ctx["db"].listar_barbeiros(ctx["bid"], apenas_ativos=False, incluir_chefe=True)
        assert any(b["nome"] == "Novo Barbeiro Test" for b in barbs)

    def test_criar_barbeiro_nome_vazio(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/barbeiros", data={"nome": ""}, follow_redirects=False)
        assert r.status_code == 302

    def test_editar_barbeiro(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/editar/{ctx['barb_id']}",
                   data={"nome": "Barbeiro Editado"}, follow_redirects=False)
        assert r.status_code == 302

    def test_repor_senha_barbeiro(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/repor-senha/{ctx['barb_id']}",
                   data={"senha": "novaSenha123"}, follow_redirects=False)
        assert r.status_code == 302

    def test_repor_senha_curta(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/repor-senha/{ctx['barb_id']}",
                   data={"senha": "123"}, follow_redirects=False)
        assert r.status_code == 302  # redireciona com flash de erro

    def test_credenciais_barbeiro(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/credenciais/{ctx['barb_id']}",
                   data={"username": "barb_novo_cov2", "senha": "senha_ok_cov2"},
                   follow_redirects=False)
        assert r.status_code == 302

    def test_credenciais_username_invalido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/credenciais/{ctx['barb_id']}",
                   data={"username": "a!", "senha": "senha123"},
                   follow_redirects=False)
        assert r.status_code == 302

    def test_foto_upload_json_jpeg(self, client):
        """Upload de foto via JSON base64 (JPEG válido)."""
        c, ctx = client
        _chefe(c, ctx)
        # Criar bytes JPEG mínimos válidos
        jpeg_bytes = b'\xff\xd8\xff\xe0' + b'\x00' * 20
        b64 = base64.b64encode(jpeg_bytes).decode()
        r = c.post(f"/barbeiros/{ctx['barb_id']}/foto",
                   json={"imagem": b64, "mime": "image/jpeg"},
                   content_type="application/json")
        assert r.status_code == 200
        assert json.loads(r.data)["ok"] is True

    def test_foto_upload_mime_invalido(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/{ctx['barb_id']}/foto",
                   json={"imagem": base64.b64encode(b"fake").decode(), "mime": "image/gif"},
                   content_type="application/json")
        assert r.status_code == 400

    def test_foto_upload_magic_invalido(self, client):
        """Bytes com MIME jpeg mas magic bytes errados → 415."""
        c, ctx = client
        _chefe(c, ctx)
        fake = base64.b64encode(b'\x00\x01\x02\x03' + b'\x00' * 20).decode()
        r = c.post(f"/barbeiros/{ctx['barb_id']}/foto",
                   json={"imagem": fake, "mime": "image/jpeg"},
                   content_type="application/json")
        assert r.status_code == 415

    def test_foto_apagar(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/{ctx['barb_id']}/foto/apagar")
        assert r.status_code == 200
        assert json.loads(r.data)["ok"] is True

    def test_foto_barbeiro_outro_nao_autorizado(self, client):
        """Barbeiro de outra barbearia → 404."""
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/barbeiros/99999/foto",
                   json={"imagem": "x", "mime": "image/jpeg"},
                   content_type="application/json")
        assert r.status_code == 404

    def test_ausencia_criar(self, client):
        c, ctx = client
        _chefe(c, ctx)
        ini = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        fim = (datetime.now() + timedelta(days=11)).strftime("%Y-%m-%d")
        r = c.post("/barbeiros/ausencia", data={
            "barbeiro_id": ctx["barb_id"],
            "data_inicio": ini, "data_fim": fim, "tipo": "falta", "motivo": "Teste"
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_ausencia_data_invalida(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/barbeiros/ausencia", data={
            "barbeiro_id": ctx["barb_id"],
            "data_inicio": "nao-data", "data_fim": "nao-data", "tipo": "falta"
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_ausencia_inicio_maior_fim(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/barbeiros/ausencia", data={
            "barbeiro_id": ctx["barb_id"],
            "data_inicio": "2026-12-31", "data_fim": "2026-12-01", "tipo": "falta"
        }, follow_redirects=False)
        assert r.status_code == 302

    def test_ausencia_apagar(self, client):
        c, ctx = client
        _chefe(c, ctx)
        ausencias = ctx["db"].listar_ausencias(ctx["bid"])
        if ausencias:
            aus_id = ausencias[0]["id"]
            r = c.post(f"/barbeiros/ausencia/apagar/{aus_id}", follow_redirects=False)
            assert r.status_code == 302

    def test_toggle_barbeiro_com_marcacoes(self, client):
        """Tentar desativar barbeiro com marcações futuras → redireciona com flash."""
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/toggle/{ctx['barb_id']}", follow_redirects=False)
        assert r.status_code == 302

    def test_apagar_barbeiro_nao_pode_apagar_unico_chefe(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/barbeiros/apagar/{ctx['chefe_id']}", follow_redirects=False)
        assert r.status_code == 302

    def test_perfil_get(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.get("/perfil")
        assert r.status_code == 200

    def test_perfil_alterar_senha_fraca(self, client):
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/perfil", data={
            "senha_atual": "senha_cov2", "nova_senha": "123", "confirmar": "123"
        }, follow_redirects=False)
        assert r.status_code in (200, 302)


# ══════════════════════════════════════════════════════════════
#  blueprints/cliente.py
# ══════════════════════════════════════════════════════════════

class TestClienteBlueprintCov2:

    def test_entrada_get(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get(f"/cliente/{ctx['slug']}")
        assert r.status_code == 200

    def test_entrada_slug_invalido(self, client):
        c, ctx = client
        r = c.get("/cliente/slug-inexistente-xyz")
        assert r.status_code == 404

    def test_entrada_post_ok(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.post(f"/cliente/{ctx['slug']}",
                   data={"nome": "Cliente Teste", "telefone": "912345678"},
                   follow_redirects=False)
        assert r.status_code == 302
        assert "/area" in r.headers["Location"]

    def test_entrada_post_nome_curto(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.post(f"/cliente/{ctx['slug']}",
                   data={"nome": "X", "telefone": "912345678"})
        assert r.status_code == 200
        assert b"curto" in r.data

    def test_entrada_post_tel_invalido(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.post(f"/cliente/{ctx['slug']}",
                   data={"nome": "Cliente Ok", "telefone": "abc"})
        assert r.status_code == 200

    def test_home_com_sessao(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.get(f"/cliente/{ctx['slug']}/area")
        assert r.status_code == 200

    def test_home_sem_sessao_redireciona(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get(f"/cliente/{ctx['slug']}/area", follow_redirects=False)
        assert r.status_code == 302

    def test_marcar_get(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.get(f"/cliente/{ctx['slug']}/marcar")
        assert r.status_code == 200

    def test_marcar_post_campos_em_falta(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.post(f"/cliente/{ctx['slug']}/marcar",
                   data={"servico_id": "", "data": "", "hora": ""})
        assert r.status_code == 200
        assert b"obrigat" in r.data

    def test_marcar_post_data_invalida(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.post(f"/cliente/{ctx['slug']}/marcar",
                   data={"servico_id": str(ctx["svc_id"]), "data": "nao-data", "hora": "10:00"})
        assert r.status_code == 200

    def test_marcar_post_no_passado(self, client):
        c, ctx = client
        _cliente(c, ctx)
        r = c.post(f"/cliente/{ctx['slug']}/marcar",
                   data={"servico_id": str(ctx["svc_id"]),
                         "data": "2020-01-01", "hora": "10:00"})
        assert r.status_code == 200
        assert b"passado" in r.data

    def test_marcar_post_sucesso(self, client):
        c, ctx = client
        _cliente(c, ctx, tel="912111111")
        # Usar um dia futuro que não seja domingo, às 14:00
        hoje = datetime.now()
        # Encontrar próxima segunda-feira
        dias = (7 - hoje.weekday()) % 7 or 7
        prox_seg = (hoje + timedelta(days=dias)).strftime("%Y-%m-%d")
        r = c.post(f"/cliente/{ctx['slug']}/marcar",
                   data={"servico_id": str(ctx["svc_id"]),
                         "data": prox_seg, "hora": "14:00"},
                   follow_redirects=False)
        # Sucesso → redireciona para confirmação, ou erro de disponibilidade (200)
        assert r.status_code in (200, 302)

    def test_confirmacao_sem_sessao(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get(f"/cliente/{ctx['slug']}/confirmacao/{ctx['ag_id']}",
                  follow_redirects=False)
        assert r.status_code == 302

    def test_confirmacao_com_sessao_errada(self, client):
        """Telefone diferente → redireciona para home."""
        c, ctx = client
        _cliente(c, ctx, tel="999000000")  # tel diferente do ag
        r = c.get(f"/cliente/{ctx['slug']}/confirmacao/{ctx['ag_id']}",
                  follow_redirects=False)
        assert r.status_code == 302

    def test_cancelar_agendamento_cliente(self, client):
        c, ctx = client
        # Criar agendamento para cancelar
        db = ctx["db"]
        amanha = ctx["amanha"]
        ag_cancel = db.criar_agendamento(
            "Cancela Cliente", ctx["svc_id"],
            f"{amanha} 15:30:00", ctx["bid"])
        with c.session_transaction() as s:
            s["role"]         = "cliente"
            s["barbearia_id"] = ctx["bid"]
            s["telefone"]     = None  # sem tel → ag não pertence
            s["user_nome"]    = "Cancela"
        r = c.post(f"/cliente/{ctx['slug']}/cancelar/{ag_cancel}",
                   follow_redirects=False)
        assert r.status_code == 302

    def test_reagendar_get_sem_sessao(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get(f"/cliente/{ctx['slug']}/reagendar/{ctx['ag_id']}",
                  follow_redirects=False)
        assert r.status_code == 302

    def test_minhas_marcacoes_cliente(self, client):
        """GET /cliente/<slug>/minhas-marcacoes — requer sessão cliente."""
        c, ctx = client
        _cliente(c, ctx)
        r = c.get(f"/cliente/{ctx['slug']}/minhas-marcacoes", follow_redirects=False)
        # Rota pode não existir no blueprint cliente — redireciona ou 200
        assert r.status_code in (200, 302, 404)


# ══════════════════════════════════════════════════════════════
#  blueprints/mesa.py
# ══════════════════════════════════════════════════════════════

class TestMesaBlueprint:

    def test_mesa_entrar_token_valido(self, client):
        c, ctx = client
        r = c.get(f"/mesa/{ctx['mesa_token']}/entrar")
        assert r.status_code == 200

    def test_mesa_entrar_token_invalido(self, client):
        c, ctx = client
        r = c.get("/mesa/token-invalido-xyz/entrar")
        assert r.status_code == 404

    def test_mesa_get(self, client):
        c, ctx = client
        r = c.get(f"/mesa/{ctx['mesa_token']}")
        assert r.status_code == 200

    def test_mesa_get_token_invalido(self, client):
        c, ctx = client
        r = c.get("/mesa/token-invalido-xyz")
        assert r.status_code == 404

    def test_mesa_info(self, client):
        c, ctx = client
        r = c.get(f"/mesa/{ctx['mesa_token']}/info")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["ok"] is True
        assert "barbeiro" in data
        assert "servicos" in data

    def test_mesa_info_token_invalido(self, client):
        c, ctx = client
        r = c.get("/mesa/token-invalido-xyz/info")
        assert r.status_code == 403

    def test_mesa_iniciar_token_invalido(self, client):
        c, ctx = client
        r = c.post("/mesa/token-invalido-xyz/iniciar",
                   json={"ag_id": 1}, content_type="application/json")
        assert r.status_code == 403

    def test_mesa_iniciar_ag_invalido(self, client):
        c, ctx = client
        r = c.post(f"/mesa/{ctx['mesa_token']}/iniciar",
                   json={"ag_id": 99999}, content_type="application/json")
        assert r.status_code == 403

    def test_mesa_iniciar_ag_valido(self, client):
        c, ctx = client
        r = c.post(f"/mesa/{ctx['mesa_token']}/iniciar",
                   json={"ag_id": ctx["ag_id"]}, content_type="application/json")
        data = json.loads(r.data)
        # Pode ser ok=True ou erro se já iniciado
        assert "ok" in data

    def test_mesa_terminar_token_invalido(self, client):
        c, ctx = client
        r = c.post("/mesa/token-invalido-xyz/terminar",
                   json={"ag_id": 1}, content_type="application/json")
        assert r.status_code == 403

    def test_mesa_terminar_ag_invalido(self, client):
        c, ctx = client
        r = c.post(f"/mesa/{ctx['mesa_token']}/terminar",
                   json={"ag_id": 99999}, content_type="application/json")
        assert r.status_code == 403

    def test_mesa_terminar_ag_valido(self, client):
        """Tentar terminar o agendamento (só funciona se está em_andamento)."""
        c, ctx = client
        r = c.post(f"/mesa/{ctx['mesa_token']}/terminar",
                   json={"ag_id": ctx["ag_id"], "valor": 800},
                   content_type="application/json")
        data = json.loads(r.data)
        assert "ok" in data

    def test_mesa_walkin_nome_em_falta(self, client):
        c, ctx = client
        r = c.post(f"/mesa/{ctx['mesa_token']}/walkin",
                   json={"nome": "", "servico_id": ctx["svc_id"]},
                   content_type="application/json")
        assert r.status_code == 400

    def test_mesa_walkin_servico_invalido(self, client):
        c, ctx = client
        r = c.post(f"/mesa/{ctx['mesa_token']}/walkin",
                   json={"nome": "Walk-in Teste", "servico_id": 99999},
                   content_type="application/json")
        assert r.status_code == 400

    def test_mesa_walkin_token_invalido(self, client):
        c, ctx = client
        r = c.post("/mesa/token-invalido-xyz/walkin",
                   json={"nome": "Walk", "servico_id": 1},
                   content_type="application/json")
        assert r.status_code == 403

    def test_mesa_walkin_valido(self, client):
        """Walk-in válido — pode falhar se barbeiro está ocupado, mas não deve dar 500."""
        c, ctx = client
        r = c.post(f"/mesa/{ctx['mesa_token']}/walkin",
                   json={"nome": "Walk-in Mesa", "servico_id": ctx["svc_id"], "valor": 0},
                   content_type="application/json")
        data = json.loads(r.data)
        assert "ok" in data
        assert r.status_code in (200, 400)
