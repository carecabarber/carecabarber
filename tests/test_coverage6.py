"""tests/test_coverage6.py — Cobertura das zonas em falta (roçar a perfeição).

Alvos:
  blueprints/servicos.py  (79%) → 90%+
    ValueError dur/preco, IDOR editar, abertura>=fecho,
    ValueError geral, tz inválido, remover_dia action
  blueprints/cliente.py   (81%) → 92%+
    barbearia inativa, validação entrada, rate limit, _dentro_horario,
    ausência, conflito disponibilidade, booking_lock, max vagas,
    confirmação, cancelar rate limit, reagendar erros,
    iniciar/terminar serviço, reagendar_link, cancelar_link, avaliar_link

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_coverage6.py -v
"""

import os, sys, pytest, tempfile, shutil
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-cov6-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ══════════════════════════════════════════════════════════════
#  FIXTURE
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def ctx():
    import database as db
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_cov6.db")
    orig    = _db_conn.DB_PATH

    _db_conn.DB_PATH = tmp_db
    db.DB_PATH       = tmp_db
    _db_conn._CONN   = None

    db.init_db()

    bid = db.criar_barbearia("Barbearia Cov6", tipo="barbearia")
    db.registar_pagamento(bid, "exp")
    with db._write() as c:
        c.execute("UPDATE barbearias SET slug=? WHERE id=?", ("cov6", bid))

    # Barbearia inativa para testes de 404
    bid_inativa = db.criar_barbearia("Inativa", tipo="barbearia")
    with db._write() as c:
        c.execute("UPDATE barbearias SET slug=?, ativa=0 WHERE id=?",
                  ("cov6-inativa", bid_inativa))

    # Chefe
    db.criar_chefe("Chefe Cov6", "chefe_cov6", "senha_cov6", bid)
    chefe = db.get_barbeiro_por_username("chefe_cov6")
    chefe_id = chefe["id"]

    # Barbeiro
    db.criar_barbeiro("Barbeiro Cov6", bid)
    with db._read() as c:
        barb_id = c.execute(
            "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
            ("Barbeiro Cov6", bid)).fetchone()["id"]

    # Serviço
    db.criar_servico("Corte Cov6", 30, bid, preco=500)
    with db._read() as c:
        svc_id = c.execute(
            "SELECT id FROM servicos WHERE barbearia_id=?", (bid,)).fetchone()["id"]

    # Serviço de outra barbearia (para IDOR)
    bid2 = db.criar_barbearia("Outra Cov6", tipo="barbearia")
    db.criar_servico("Serv Outra", 30, bid2, preco=100)
    with db._read() as c:
        svc_id2 = c.execute(
            "SELECT id FROM servicos WHERE barbearia_id=?", (bid2,)).fetchone()["id"]

    # Dia fechado (para remover_dia)
    db.adicionar_dia_fechado("2030-01-01", "Feriado", bid)
    with db._read() as c:
        dia_id = c.execute(
            "SELECT id FROM dias_fechados WHERE barbearia_id=?", (bid,)).fetchone()["id"]

    tel    = "912345678"
    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    # Agendamento principal (agendado) — usado em marcar/home/confirmacao
    ag_id = db.criar_agendamento(
        "Cliente Cov6", svc_id, f"{amanha} 10:00:00",
        bid, barbeiro_id=barb_id, telefone=tel)

    # Agendamento em andamento — para cliente_terminar_servico
    ag_em_andamento_id = db.criar_agendamento(
        "Cliente Cov6", svc_id, f"{amanha} 11:00:00",
        bid, barbeiro_id=barb_id, telefone=tel)
    db.iniciar_trabalho(ag_em_andamento_id)

    # Agendamento para cliente_iniciar_servico (separado para não colidir)
    ag_iniciar_id = db.criar_agendamento(
        "Cliente Cov6", svc_id, f"{amanha} 12:00:00",
        bid, barbeiro_id=barb_id, telefone=tel)

    # Agendamento dedicado para reagendar_link
    ag_reagendar_id = db.criar_agendamento(
        "Cliente Cov6", svc_id, f"{amanha} 14:00:00",
        bid, barbeiro_id=barb_id, telefone=tel)
    token_reagendar = db.gerar_token_reagendar(ag_reagendar_id)

    # Agendamento dedicado para cancelar_link
    ag_cancelar_id = db.criar_agendamento(
        "Cliente Cov6", svc_id, f"{amanha} 15:00:00",
        bid, barbeiro_id=barb_id, telefone=tel)
    with db._read() as c:
        row = c.execute(
            "SELECT token_reagendar FROM agendamentos WHERE id=?",
            (ag_cancelar_id,)).fetchone()
        token_cancelar = row["token_reagendar"]

    # Agendamento concluído — para avaliar_link
    ag_concluido_id = db.criar_agendamento(
        "Cliente Cov6", svc_id, f"{amanha} 09:00:00",
        bid, barbeiro_id=barb_id, telefone=tel)
    db.iniciar_trabalho(ag_concluido_id)
    db.terminar_trabalho(ag_concluido_id, 500)
    with db._read() as c:
        row2 = c.execute(
            "SELECT token_avaliar FROM agendamentos WHERE id=?",
            (ag_concluido_id,)).fetchone()
        token_avaliar = row2["token_avaliar"]

    yield {
        "db": db,
        "bid": bid, "bid2": bid2,
        "chefe_id": chefe_id,
        "barb_id": barb_id,
        "svc_id": svc_id, "svc_id2": svc_id2,
        "dia_id": dia_id,
        "tel": tel, "amanha": amanha,
        "ag_id": ag_id,
        "ag_em_andamento_id": ag_em_andamento_id,
        "ag_iniciar_id": ag_iniciar_id,
        "ag_reagendar_id": ag_reagendar_id,
        "ag_cancelar_id": ag_cancelar_id,
        "ag_concluido_id": ag_concluido_id,
        "token_reagendar": token_reagendar,
        "token_cancelar": token_cancelar,
        "token_avaliar": token_avaliar,
    }

    _db_conn._reset_conn()
    db._CONN = None
    _db_conn.DB_PATH = orig
    db.DB_PATH       = orig
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def client(ctx):
    import app as app_module
    app_module.app.config.update({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-cov6",
        "SESSION_COOKIE_SECURE": False,
    })
    with app_module.app.test_client() as c:
        yield c, ctx


def _chefe(c, ctx):
    with c.session_transaction() as s:
        s["user_id"]      = ctx["chefe_id"]
        s["role"]         = "chefe"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"]    = "Chefe Cov6"
    return c


def _cliente(c, ctx):
    with c.session_transaction() as s:
        s["role"]         = "cliente"
        s["barbearia_id"] = ctx["bid"]
        s["telefone"]     = ctx["tel"]
        s["user_nome"]    = "Cliente Cov6"
        s.pop("user_id", None)
    return c


# ══════════════════════════════════════════════════════════════
#  servicos.py — zonas em falta
# ══════════════════════════════════════════════════════════════

class TestServicosCobertura:

    def test_criar_servico_valor_invalido(self, client):
        """POST /servicos com dur/preco não numérico → ValueError → defaults (linhas 23-24)."""
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/servicos", data={
            "nome": "Teste Err", "duracao_min": "abc", "preco": "nao_numero"})
        assert r.status_code in (200, 302)

    def test_editar_servico_idor(self, client):
        """POST /servicos/editar/<id_de_outra_barbearia> → redirect sem editar (linha 39)."""
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/servicos/editar/{ctx['svc_id2']}",
                   data={"nome": "Hack", "duracao_min": "30", "preco": "100"})
        assert r.status_code == 302
        assert "/servicos" in r.location

    def test_editar_servico_valor_invalido(self, client):
        """POST /servicos/editar/<id> com dur/preco não numérico → linhas 44-45."""
        c, ctx = client
        _chefe(c, ctx)
        r = c.post(f"/servicos/editar/{ctx['svc_id']}",
                   data={"nome": "Corte Ok", "duracao_min": "xyz", "preco": "xyz"})
        assert r.status_code == 302

    def test_config_horario_abertura_ge_fecho(self, client):
        """Horário com abertura >= fecho → reset para 08:00-19:00 (linha 78)."""
        c, ctx = client
        _chefe(c, ctx)
        data = {"acao": "horario"}
        for d in range(7):
            data[f"aberto_{d}"]   = "on"
            data[f"abertura_{d}"] = "20:00"   # >= fecho → reset
            data[f"fecho_{d}"]    = "08:00"
        r = c.post("/configuracoes", data=data)
        assert r.status_code == 302

    def test_config_geral_valor_invalido(self, client):
        """POST /configuracoes acao=geral com buf/mpd não numérico → linhas 86-87."""
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/configuracoes", data={
            "acao": "geral",
            "buffer_minutos": "nao_numero",
            "max_por_dia":    "tambem_nao",
        })
        assert r.status_code == 302

    def test_config_geral_tz_invalido(self, client):
        """Timezone inválido → flash de erro (linhas 95-100)."""
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/configuracoes", data={
            "acao":      "geral",
            "buffer_minutos": "10",
            "max_por_dia":    "20",
            "timezone":  "Invalido/Nao_Existe_Em_Parte_Alguma",
        })
        assert r.status_code == 302

    def test_config_remover_dia_fechado(self, client):
        """acao=remover_dia com id válido → apaga (linhas 107-114)."""
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/configuracoes", data={
            "acao": "remover_dia", "dia_id": str(ctx["dia_id"])})
        assert r.status_code == 302

    def test_config_remover_dia_valor_invalido(self, client):
        """acao=remover_dia com id não numérico → except silencioso (linhas 113-114)."""
        c, ctx = client
        _chefe(c, ctx)
        r = c.post("/configuracoes", data={"acao": "remover_dia", "dia_id": "nao_numero"})
        assert r.status_code == 302


# ══════════════════════════════════════════════════════════════
#  cliente.py — entrada / validação
# ══════════════════════════════════════════════════════════════

class TestClienteEntrada:

    def test_barbearia_inexistente_404(self, client):
        """Slug inexistente → 404 (linhas 17-18)."""
        c, ctx = client
        r = c.get("/cliente/slug-que-nao-existe-xyz")
        assert r.status_code == 404

    def test_barbearia_inativa_404(self, client):
        """Barbearia inativa → 404."""
        c, ctx = client
        r = c.get("/cliente/cov6-inativa")
        assert r.status_code == 404

    def test_nome_vazio(self, client):
        """Nome vazio → erro (linha 25-26)."""
        c, ctx = client
        r = c.post("/cliente/cov6", data={"nome": "", "telefone": "912345678"})
        assert r.status_code == 200

    def test_nome_curto(self, client):
        """Nome de 1 caracter → erro 'demasiado curto' (linhas 27-28)."""
        c, ctx = client
        r = c.post("/cliente/cov6", data={"nome": "A", "telefone": "912345678"})
        assert r.status_code == 200
        assert "curto" in r.data.decode("utf-8", errors="replace").lower()

    def test_tel_invalido(self, client):
        """Telefone inválido → erro (linhas 29-30)."""
        c, ctx = client
        r = c.post("/cliente/cov6", data={"nome": "Ana", "telefone": "abc"})
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════
#  cliente.py — cliente_marcar
# ══════════════════════════════════════════════════════════════

class TestClienteMarcar:

    def test_rate_limit_429(self, client):
        """_api_ok=False → 429 com mensagem (linhas 77-81)."""
        c, ctx = client
        _cliente(c, ctx)
        with patch("blueprints.cliente._api_ok", return_value=False):
            r = c.post("/cliente/cov6/marcar", data={
                "servico_id": str(ctx["svc_id"]),
                "barbeiro_id": str(ctx["barb_id"]),
                "data": "2030-12-31", "hora": "10:00",
            })
        assert r.status_code == 429

    def test_dentro_horario_erro(self, client):
        """_dentro_horario devolve False → erro exibido (linhas 110-112)."""
        c, ctx = client
        _cliente(c, ctx)
        with patch("blueprints.cliente._dentro_horario",
                   return_value=(False, "Fora do horário de funcionamento.")):
            r = c.post("/cliente/cov6/marcar", data={
                "servico_id": str(ctx["svc_id"]),
                "barbeiro_id": str(ctx["barb_id"]),
                "data": "2030-12-31", "hora": "10:00",
            })
        assert r.status_code == 200
        html = r.data.decode("utf-8", errors="replace")
        assert "Fora" in html

    def test_ausencia_barbeiro(self, client):
        """Barbeiro em ausência → erro (linhas 113-116)."""
        c, ctx = client
        _cliente(c, ctx)
        fake_aus = {"barbeiro_nome": "Barbeiro Cov6"}
        with patch("blueprints.cliente._dentro_horario", return_value=(True, "")):
            with patch("blueprints.cliente.db.ausencia_ativa", return_value=fake_aus):
                r = c.post("/cliente/cov6/marcar", data={
                    "servico_id": str(ctx["svc_id"]),
                    "barbeiro_id": str(ctx["barb_id"]),
                    "data": "2030-12-31", "hora": "10:00",
                })
        assert r.status_code == 200
        assert "indisponível" in r.data.decode("utf-8", errors="replace")

    def test_conflict_disponibilidade(self, client):
        """Conflito na 1ª verificação → erro (linhas 118-120)."""
        c, ctx = client
        _cliente(c, ctx)
        fake_conflito = {"data_hora": "2030-12-31 10:00:00"}
        with patch("blueprints.cliente._dentro_horario", return_value=(True, "")):
            with patch("blueprints.cliente.db.ausencia_ativa", return_value=None):
                with patch("blueprints.cliente.db.verificar_disponibilidade",
                           return_value=(False, fake_conflito)):
                    r = c.post("/cliente/cov6/marcar", data={
                        "servico_id": str(ctx["svc_id"]),
                        "barbeiro_id": str(ctx["barb_id"]),
                        "data": "2030-12-31", "hora": "10:00",
                    })
        assert r.status_code == 200

    def test_booking_lock_conflict(self, client):
        """Conflito apenas dentro do booking_lock → erro (linhas 122-126)."""
        c, ctx = client
        _cliente(c, ctx)
        fake_conflito = {"data_hora": "2030-12-31 10:00:00"}
        with patch("blueprints.cliente._dentro_horario", return_value=(True, "")):
            with patch("blueprints.cliente.db.ausencia_ativa", return_value=None):
                with patch("blueprints.cliente.db.verificar_disponibilidade",
                           side_effect=[(True, None), (False, fake_conflito)]):
                    r = c.post("/cliente/cov6/marcar", data={
                        "servico_id": str(ctx["svc_id"]),
                        "barbeiro_id": str(ctx["barb_id"]),
                        "data": "2030-12-31", "hora": "10:00",
                    })
        assert r.status_code == 200

    def test_max_vagas_esgotadas(self, client):
        """Sem barbeiro + dia cheio → erro de vagas (linhas 128-131)."""
        c, ctx = client
        _cliente(c, ctx)
        with patch("blueprints.cliente._dentro_horario", return_value=(True, "")):
            with patch("blueprints.cliente.db.contar_ativos_dia", return_value=20):
                r = c.post("/cliente/cov6/marcar", data={
                    "servico_id": str(ctx["svc_id"]),
                    # sem barbeiro_id
                    "data": "2030-12-31", "hora": "10:00",
                })
        assert r.status_code == 200
        assert "vagas" in r.data.decode("utf-8", errors="replace").lower()


# ══════════════════════════════════════════════════════════════
#  cliente.py — confirmação, cancelar, reagendar (sessão)
# ══════════════════════════════════════════════════════════════

class TestClienteConfirmacaoECancelar:

    def test_confirmacao_tel_errado(self, client):
        """Telefone de sessão ≠ ag.telefone → redirect (linhas 160-161)."""
        c, ctx = client
        with c.session_transaction() as s:
            s["role"]         = "cliente"
            s["barbearia_id"] = ctx["bid"]
            s["telefone"]     = "999999999"   # tel diferente do ag
        r = c.get(f"/cliente/cov6/confirmacao/{ctx['ag_id']}")
        assert r.status_code == 302

    def test_cancelar_rate_limit(self, client):
        """_api_ok=False em cancelar → redirect para home (linhas 177-178)."""
        c, ctx = client
        _cliente(c, ctx)
        with patch("blueprints.cliente._api_ok", return_value=False):
            r = c.post(f"/cliente/cov6/cancelar/{ctx['ag_id']}")
        assert r.status_code == 302

    def test_reagendar_ag_not_found(self, client):
        """ag inexistente → redirect para home (linhas 197-200)."""
        c, ctx = client
        _cliente(c, ctx)
        r = c.get("/cliente/cov6/reagendar/9999")
        assert r.status_code == 302

    def test_reagendar_post_data_hora_invalida(self, client):
        """POST reagendar com data inválida → erro mostrado (linhas 218-222)."""
        c, ctx = client
        _cliente(c, ctx)
        r = c.post(f"/cliente/cov6/reagendar/{ctx['ag_id']}",
                   data={"data": "nao_e_data", "hora": "xx:xx"})
        assert r.status_code == 200

    def test_reagendar_post_no_passado(self, client):
        """POST reagendar com data no passado → erro (linha 220-221)."""
        c, ctx = client
        _cliente(c, ctx)
        r = c.post(f"/cliente/cov6/reagendar/{ctx['ag_id']}",
                   data={"data": "2000-01-01", "hora": "10:00"})
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════
#  cliente.py — iniciar / terminar serviço (rotas cliente)
# ══════════════════════════════════════════════════════════════

class TestClienteIniciarTerminar:

    def test_iniciar_servico_ag_not_found(self, client):
        """ag 9999 não existe → redirect (linhas 270-274)."""
        c, ctx = client
        _cliente(c, ctx)
        r = c.post("/cliente/cov6/iniciar-servico/9999")
        assert r.status_code == 302

    def test_iniciar_servico_ok(self, client):
        """ag_iniciar_id está agendado → iniciar (linhas 275-280)."""
        c, ctx = client
        _cliente(c, ctx)
        r = c.post(f"/cliente/cov6/iniciar-servico/{ctx['ag_iniciar_id']}")
        assert r.status_code == 302

    def test_terminar_servico_ag_not_found(self, client):
        """ag 9999 → redirect (linhas 292-296)."""
        c, ctx = client
        _cliente(c, ctx)
        r = c.post("/cliente/cov6/terminar-servico/9999", data={"valor": "0"})
        assert r.status_code == 302

    def test_terminar_servico_ok(self, client):
        """ag_em_andamento_id está em andamento → terminar (linhas 297-303)."""
        c, ctx = client
        _cliente(c, ctx)
        r = c.post(f"/cliente/cov6/terminar-servico/{ctx['ag_em_andamento_id']}",
                   data={"valor": "500"})
        assert r.status_code == 302


# ══════════════════════════════════════════════════════════════
#  cliente.py — reagendar_link (token público)
# ══════════════════════════════════════════════════════════════

class TestReagendarLink:

    def test_get_token_invalido(self, client):
        """Token inválido → 404 (linha 311)."""
        c, ctx = client
        r = c.get("/reagendar-link/token-xyz-invalido-123")
        assert r.status_code == 404

    def test_get_ok(self, client):
        """GET com token válido → renderiza formulário (200)."""
        c, ctx = client
        r = c.get(f"/reagendar-link/{ctx['token_reagendar']}")
        assert r.status_code == 200

    def test_post_data_invalida(self, client):
        """POST com data/hora inválidas → página com erro."""
        c, ctx = client
        r = c.post(f"/reagendar-link/{ctx['token_reagendar']}",
                   data={"data": "nao_data", "hora": "xx:xx"})
        assert r.status_code == 200

    def test_post_no_passado(self, client):
        """POST com data no passado → erro."""
        c, ctx = client
        r = c.post(f"/reagendar-link/{ctx['token_reagendar']}",
                   data={"data": "2000-01-01", "hora": "10:00"})
        assert r.status_code == 200

    def test_post_sucesso(self, client):
        """POST com dados válidos → reagendamento e render ok (linhas 364-371).
        Deve ser o último teste a usar token_reagendar — após sucesso o token é nullado."""
        c, ctx = client
        with patch("blueprints.cliente._dentro_horario", return_value=(True, "")):
            with patch("blueprints.cliente.db.ausencia_ativa", return_value=None):
                with patch("blueprints.cliente.db.verificar_disponibilidade",
                           return_value=(True, None)):
                    r = c.post(f"/reagendar-link/{ctx['token_reagendar']}",
                               data={
                                   "servico_id":  str(ctx["svc_id"]),
                                   "barbeiro_id": str(ctx["barb_id"]),
                                   "data": "2030-12-31",
                                   "hora": "14:00",
                               })
        # Sucesso → render reagendar_link_ok.html (200)
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════
#  cliente.py — cancelar_link (token público)
# ══════════════════════════════════════════════════════════════

class TestCancelarLink:

    def test_get_token_invalido(self, client):
        """Token inválido → 404."""
        c, ctx = client
        r = c.get("/cancelar-link/token-xyz-invalido-456")
        assert r.status_code == 404

    def test_get_ok(self, client):
        """GET com token válido → mostra página de confirmação (200)."""
        c, ctx = client
        r = c.get(f"/cancelar-link/{ctx['token_cancelar']}")
        assert r.status_code == 200

    def test_post_sem_confirmar(self, client):
        """POST sem confirmar=sim → volta a mostrar a página (sem cancelar)."""
        c, ctx = client
        r = c.post(f"/cancelar-link/{ctx['token_cancelar']}",
                   data={"confirmar": "nao"})
        assert r.status_code == 200

    def test_post_confirmar_cancela(self, client):
        """POST com confirmar=sim → cancela e renderiza cancelar_link_ok.html.
        Deve ser o último teste a usar token_cancelar."""
        c, ctx = client
        r = c.post(f"/cancelar-link/{ctx['token_cancelar']}",
                   data={"confirmar": "sim"})
        # Agendamento cancelado → render cancelar_link_ok.html (200)
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════
#  cliente.py — avaliar_link (token público)
# ══════════════════════════════════════════════════════════════

class TestAvaliarLink:

    def test_get_token_invalido(self, client):
        """Token inválido → 404 (linha 422)."""
        c, ctx = client
        r = c.get("/avaliar-link/token-xyz-invalido-789")
        assert r.status_code == 404

    def test_get_ok(self, client):
        """GET com token válido → renderiza formulário (200)."""
        c, ctx = client
        r = c.get(f"/avaliar-link/{ctx['token_avaliar']}")
        assert r.status_code == 200

    def test_post_nota_invalida(self, client):
        """POST com nota=6 (fora de 1-5) → erro (linhas 434-438).
        Deve correr ANTES de post_nota_valida para ja_avaliou=False."""
        c, ctx = client
        r = c.post(f"/avaliar-link/{ctx['token_avaliar']}", data={"nota": "6"})
        assert r.status_code == 200

    def test_post_nota_valida(self, client):
        """POST com nota=5 → sucesso (linhas 430-436)."""
        c, ctx = client
        r = c.post(f"/avaliar-link/{ctx['token_avaliar']}", data={"nota": "5"})
        assert r.status_code == 200
