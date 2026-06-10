"""
tests/test_coverage.py — Testes de cobertura para módulos com baixa cobertura.

Cobre:
  - db/relatorios.py      (era 5%)
  - db/agendamentos.py    (era 40%) — horarios_disponiveis, verificar_disponibilidade, edge cases
  - db/servicos.py        (era 45%)
  - db/barbearia.py       (era 65%) — configs, dias fechados, ausências, horários
  - blueprints/api.py     (era 33%) — estado, slots, lembretes, novos-agendamentos
  - blueprints/servicos.py (era 16%) — CRUD serviços + configurações
  - blueprints/cliente.py  (era 8%)  — entrada, home, marcar
  - helpers_booking.py    (era 56%) — validação, vocab, pluralizar, cache
  - helpers_security.py   (era 66%) — rate limit, mime, imagem

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_coverage.py -v
"""
import os, sys, json, pytest, tempfile, shutil
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test-secret-coverage")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ══════════════════════════════════════════════════════════════
#  FIXTURES
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def ctx():
    """DB temporária com dados ricos para testes de cobertura."""
    import database as db_module
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_cov.db")
    orig    = _db_conn.DB_PATH

    _db_conn.DB_PATH  = tmp_db
    db_module.DB_PATH = tmp_db
    _db_conn._CONN    = None

    db_module.init_db()

    bid = db_module.criar_barbearia("Barbearia Cov", tipo="barbearia")
    db_module.registar_pagamento(bid, "exp")

    db_module.criar_chefe("Chefe Cov", "chefe_cov", "senha_cov", bid)
    chefe_id = db_module.get_barbeiro_por_username("chefe_cov")["id"]

    db_module.criar_barbeiro("Barbeiro Cov", bid)
    with db_module._read() as c:
        barb_id = c.execute(
            "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
            ("Barbeiro Cov", bid)).fetchone()["id"]
    db_module.set_credenciais(barb_id, "barb_cov", "pass_cov")

    db_module.criar_servico("Corte Cov", 30, bid, preco=800)
    db_module.criar_servico("Barba Cov", 20, bid, preco=500)
    with db_module._read() as c:
        rows = c.execute("SELECT id FROM servicos WHERE barbearia_id=?", (bid,)).fetchall()
    svc_id  = rows[0]["id"]
    svc2_id = rows[1]["id"]

    # Horário da barbearia: segunda a sexta 08:00-19:00, sábado 08:00-14:00
    for dia in range(5):
        db_module.set_horario_dia(dia, "08:00", "19:00", 0, bid)
    db_module.set_horario_dia(5, "08:00", "14:00", 0, bid)   # sábado
    db_module.set_horario_dia(6, "08:00", "19:00", 1, bid)   # domingo fechado

    # Agendamentos concluídos para relatorios
    hoje = datetime.now().strftime("%Y-%m-%d")
    for i in range(3):
        ag_id = db_module.criar_agendamento(
            f"Cliente {i}", svc_id,
            f"{hoje} 09:{i*10:02d}:00",
            bid, barbeiro_id=barb_id, valor=800)
        db_module.iniciar_trabalho(ag_id)
        db_module.terminar_trabalho(ag_id)

    # Um agendamento activo (futuro) para slots
    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    ag_futuro = db_module.criar_agendamento(
        "Cliente Futuro", svc_id,
        f"{amanha} 10:00:00",
        bid, barbeiro_id=barb_id)

    yield {
        "db":       db_module,
        "bid":      bid,
        "chefe_id": chefe_id,
        "barb_id":  barb_id,
        "svc_id":   svc_id,
        "svc2_id":  svc2_id,
        "hoje":     hoje,
        "amanha":   amanha,
        "ag_futuro": ag_futuro,
        "tmp_dir":  tmp_dir,
    }

    _db_conn._reset_conn()
    db_module._CONN   = None
    _db_conn.DB_PATH  = orig
    db_module.DB_PATH = orig
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def client(ctx):
    import app as app_module
    app_module.app.config.update({
        "TESTING": True, "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-cov", "SESSION_COOKIE_SECURE": False,
    })
    with app_module.app.test_client() as c:
        yield c, ctx


def _como_chefe(c, ctx):
    with c.session_transaction() as s:
        s["user_id"] = ctx["chefe_id"]
        s["role"]    = "chefe"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"] = "Chefe Cov"
    return c


def _como_barbeiro(c, ctx):
    with c.session_transaction() as s:
        s["user_id"] = ctx["barb_id"]
        s["role"]    = "barbeiro"
        s["barbearia_id"] = ctx["bid"]
        s["user_nome"] = "Barbeiro Cov"
    return c


def _limpar_sessao(c):
    with c.session_transaction() as s:
        s.clear()
    return c


# ══════════════════════════════════════════════════════════════
#  db/relatorios.py
# ══════════════════════════════════════════════════════════════

class TestRelatorios:

    def test_duracao_real_minutos_basico(self, ctx):
        db = ctx["db"]
        assert db.duracao_real_minutos("2026-06-01 09:00:00", "2026-06-01 09:30:00") == 30

    def test_duracao_real_minutos_none(self, ctx):
        db = ctx["db"]
        assert db.duracao_real_minutos(None, "2026-06-01 09:30:00") is None
        assert db.duracao_real_minutos("2026-06-01 09:00:00", None) is None

    def test_duracao_real_minutos_formato_invalido(self, ctx):
        assert ctx["db"].duracao_real_minutos("invalido", "2026-06-01 09:30:00") is None

    def test_estatisticas_estrutura(self, ctx):
        db = ctx["db"]
        stats = db.estatisticas(ctx["bid"])
        assert "hoje" in stats
        assert "semana" in stats
        assert "mes" in stats
        assert "top_servicos" in stats
        assert "barbeiros_stats" in stats
        assert isinstance(stats["hoje"]["clientes"], int)
        assert isinstance(stats["hoje"]["valor"], int)

    def test_estatisticas_com_concluidos(self, ctx):
        """3 atendimentos concluídos hoje → hoje.clientes >= 3."""
        stats = ctx["db"].estatisticas(ctx["bid"])
        assert stats["hoje"]["clientes"] >= 3

    def test_estatisticas_filtro_barbeiro(self, ctx):
        db = ctx["db"]
        stats = db.estatisticas(ctx["bid"], barbeiro_id=ctx["barb_id"])
        assert "hoje" in stats
        assert stats["hoje"]["clientes"] >= 3

    def test_estatisticas_detalhadas_barbeiro(self, ctx):
        db = ctx["db"]
        det = db.estatisticas_detalhadas_barbeiro(ctx["barb_id"], ctx["bid"])
        assert "hoje" in det
        assert "barbeiro" in det
        assert "top_servicos" in det

    def test_tendencia_semanal_lista(self, ctx):
        resultado = ctx["db"].tendencia_semanal(ctx["bid"])
        assert isinstance(resultado, list)
        # Pode estar vazia se todos os concluídos são hoje e semana 0
        for item in resultado:
            assert "label" in item
            assert "clientes" in item
            assert "valor" in item

    def test_tendencia_semanal_com_barbeiro(self, ctx):
        resultado = ctx["db"].tendencia_semanal(ctx["bid"], barbeiro_id=ctx["barb_id"])
        assert isinstance(resultado, list)

    def test_tendencia_semanal_semanas_param(self, ctx):
        r1 = ctx["db"].tendencia_semanal(ctx["bid"], semanas=4)
        r2 = ctx["db"].tendencia_semanal(ctx["bid"], semanas=20)
        assert isinstance(r1, list)
        assert isinstance(r2, list)


# ══════════════════════════════════════════════════════════════
#  db/agendamentos.py — horarios_disponiveis + verificar_disponibilidade
# ══════════════════════════════════════════════════════════════

class TestDisponibilidade:

    def test_horarios_disponiveis_dia_aberto(self, ctx):
        db = ctx["db"]
        amanha = ctx["amanha"]
        slots = db.horarios_disponiveis(ctx["barb_id"], amanha, 30, ctx["bid"])
        assert isinstance(slots, list)
        assert len(slots) > 0
        # Cada slot tem hora, tipo, espera_min
        for s in slots:
            assert "hora" in s
            assert "tipo" in s
            assert "espera_min" in s

    def test_horarios_disponiveis_slot_10h_ocupado(self, ctx):
        """10:00 deve aparecer como 'ocupado' pois há ag_futuro às 10:00."""
        slots = ctx["db"].horarios_disponiveis(ctx["barb_id"], ctx["amanha"], 30, ctx["bid"])
        horas_ocupadas = [s["hora"] for s in slots if s["tipo"] == "ocupado"]
        assert "10:00" in horas_ocupadas

    def test_horarios_disponiveis_dia_fechado(self, ctx):
        """Domingo está fechado — deve devolver lista vazia."""
        db = ctx["db"]
        # Encontrar o próximo domingo
        hoje = datetime.now()
        dias_para_dom = (6 - hoje.weekday()) % 7 or 7
        domingo = (hoje + timedelta(days=dias_para_dom)).strftime("%Y-%m-%d")
        slots = db.horarios_disponiveis(ctx["barb_id"], domingo, 30, ctx["bid"])
        assert slots == []

    def test_horarios_disponiveis_dia_especial_fechado(self, ctx):
        """Adicionar dia fechado → horarios_disponiveis devolve []."""
        db = ctx["db"]
        data_fechada = (datetime.now() + timedelta(days=15)).strftime("%Y-%m-%d")
        db.adicionar_dia_fechado(data_fechada, "Feriado teste", ctx["bid"])
        slots = db.horarios_disponiveis(ctx["barb_id"], data_fechada, 30, ctx["bid"])
        assert slots == []
        # Remover depois
        dias = db.listar_dias_fechados(ctx["bid"])
        for d in dias:
            if d["data"] == data_fechada:
                db.remover_dia_fechado(d["id"])
                break

    def test_horarios_disponiveis_sem_barbeiro(self, ctx):
        """barbeiro_id=None — devolve slots sem filtro por barbeiro."""
        slots = ctx["db"].horarios_disponiveis(None, ctx["amanha"], 30, ctx["bid"])
        assert isinstance(slots, list)

    def test_verificar_disponibilidade_sem_conflito(self, ctx):
        """Slot livre deve devolver (True, None)."""
        db = ctx["db"]
        livre, conflito = db.verificar_disponibilidade(
            ctx["barb_id"], f"{ctx['amanha']} 15:00:00", 30, ctx["bid"])
        assert livre is True
        assert conflito is None

    def test_verificar_disponibilidade_com_conflito(self, ctx):
        """10:00 ocupa até ~10:40 com buffer — 10:20 deve ser conflito."""
        db = ctx["db"]
        ok, conflito = db.verificar_disponibilidade(
            ctx["barb_id"], f"{ctx['amanha']} 10:20:00", 30, ctx["bid"])
        assert ok is False
        assert conflito is not None

    def test_verificar_disponibilidade_sem_barbeiro(self, ctx):
        """barbeiro_id=None → sempre livre (sem constraint)."""
        ok, _ = ctx["db"].verificar_disponibilidade(
            None, f"{ctx['amanha']} 10:00:00", 30, ctx["bid"])
        assert ok is True

    def test_verificar_disponibilidade_excluir_id(self, ctx):
        """excluir_id do próprio agendamento → reagendamento não conflita consigo mesmo."""
        db = ctx["db"]
        ok, _ = db.verificar_disponibilidade(
            ctx["barb_id"], f"{ctx['amanha']} 10:00:00", 30, ctx["bid"],
            excluir_id=ctx["ag_futuro"])
        assert ok is True

    def test_marcar_nao_compareceu(self, ctx):
        db = ctx["db"]
        ag_id = db.criar_agendamento(
            "Cliente NC", ctx["svc_id"],
            f"{ctx['amanha']} 16:00:00",
            ctx["bid"], barbeiro_id=ctx["barb_id"])
        resultado = db.marcar_nao_compareceu(ag_id)
        assert resultado is True
        # Segunda vez não faz nada (já não está 'agendado')
        assert db.marcar_nao_compareceu(ag_id) is False

    def test_contar_visitas(self, ctx):
        """3 atendimentos concluídos com tel=None → 0 visitas por tel."""
        visitas = ctx["db"].contar_visitas("999000000", ctx["bid"])
        assert visitas == 0

    def test_contar_visitas_batch(self, ctx):
        result = ctx["db"].contar_visitas_batch(["999000000", "999000001"], ctx["bid"])
        assert isinstance(result, dict)

    def test_listar_todos_vazio(self, ctx):
        rows = ctx["db"].listar_todos(ctx["bid"], data="2000-01-01")
        assert isinstance(rows, list)

    def test_resumo_hoje(self, ctx):
        r = ctx["db"].resumo_hoje(ctx["bid"])
        assert "clientes" in r
        assert "valor" in r
        assert r["clientes"] >= 3


# ══════════════════════════════════════════════════════════════
#  db/servicos.py
# ══════════════════════════════════════════════════════════════

class TestServicosDB:

    def test_listar_servicos_ativos(self, ctx):
        svcs = ctx["db"].listar_servicos(ctx["bid"])
        assert len(svcs) >= 2

    def test_listar_servicos_todos(self, ctx):
        svcs = ctx["db"].listar_servicos(ctx["bid"], apenas_ativos=False)
        assert len(svcs) >= 2

    def test_servico_por_id_existente(self, ctx):
        s = ctx["db"].servico_por_id(ctx["svc_id"])
        assert s is not None
        assert s["nome"] == "Corte Cov"

    def test_servico_por_id_inexistente(self, ctx):
        assert ctx["db"].servico_por_id(99999) is None

    def test_atualizar_servico(self, ctx):
        db = ctx["db"]
        db.atualizar_servico(ctx["svc2_id"], "Barba Editada", 25, 600, ctx["bid"])
        s = db.servico_por_id(ctx["svc2_id"])
        assert s["nome"] == "Barba Editada"
        assert s["duracao_min"] == 25

    def test_apagar_servico(self, ctx):
        db = ctx["db"]
        db.criar_servico("Temp para apagar", 15, ctx["bid"], preco=0)
        with db._read() as c:
            row = c.execute("SELECT id FROM servicos WHERE nome=? AND barbearia_id=?",
                            ("Temp para apagar", ctx["bid"])).fetchone()
        tmp_id = row["id"]
        db.apagar_servico(tmp_id, barbearia_id=ctx["bid"])
        assert db.servico_por_id(tmp_id) is None

    def test_get_servicos_por_ids(self, ctx):
        smap = ctx["db"].get_servicos_por_ids([ctx["svc_id"], ctx["svc2_id"]])
        assert ctx["svc_id"] in smap
        assert ctx["svc2_id"] in smap


# ══════════════════════════════════════════════════════════════
#  db/barbearia.py — configs, horários, dias fechados, ausências
# ══════════════════════════════════════════════════════════════

class TestBarbeariaCob:

    def test_get_set_config(self, ctx):
        db = ctx["db"]
        db.set_config("test_key", "test_val", ctx["bid"])
        assert db.get_config("test_key", ctx["bid"]) == "test_val"

    def test_get_config_default(self, ctx):
        val = ctx["db"].get_config("chave_inexistente", ctx["bid"], default="fallback")
        assert val == "fallback"

    def test_get_todas_configs(self, ctx):
        configs = ctx["db"].get_todas_configs(ctx["bid"])
        assert isinstance(configs, dict)

    def test_get_horario(self, ctx):
        horario = ctx["db"].get_horario(ctx["bid"])
        assert isinstance(horario, list)
        assert len(horario) == 7

    def test_get_horario_dia(self, ctx):
        h = ctx["db"].get_horario_dia(0, ctx["bid"])  # segunda
        assert h["hora_abertura"] == "08:00"
        assert h["hora_fecho"] == "19:00"

    def test_get_horario_dia_fechado(self, ctx):
        h = ctx["db"].get_horario_dia(6, ctx["bid"])  # domingo
        assert h["fechado"] == 1

    def test_dias_fechados_crud(self, ctx):
        db = ctx["db"]
        data = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        db.adicionar_dia_fechado(data, "Feriado Cov", ctx["bid"])
        dias = db.listar_dias_fechados(ctx["bid"])
        ids = [d["id"] for d in dias if d["data"] == data]
        assert len(ids) >= 1
        assert db.dia_esta_fechado(data, ctx["bid"]) is True
        db.remover_dia_fechado(ids[0])
        assert db.dia_esta_fechado(data, ctx["bid"]) is False

    def test_ausencias_crud(self, ctx):
        db = ctx["db"]
        ini = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
        fim = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        db.criar_ausencia(ctx["barb_id"], ini, fim, "falta")
        ausencias = db.listar_ausencias(ctx["bid"])
        ids = [a["id"] for a in ausencias if a["barbeiro_id"] == ctx["barb_id"]]
        assert len(ids) >= 1
        assert db.barbeiro_ausente(ctx["barb_id"], ini) is True
        db.apagar_ausencia(ids[-1])

    def test_editar_barbearia(self, ctx):
        db = ctx["db"]
        db.editar_barbearia(ctx["bid"], "Barbearia Cov Editada")
        b = db.get_barbearia(ctx["bid"])
        assert b["nome"] == "Barbearia Cov Editada"


# ══════════════════════════════════════════════════════════════
#  blueprints/api.py
# ══════════════════════════════════════════════════════════════

class TestAPIEndpoints:

    def test_vapid_public_key(self, client):
        c, ctx = client
        r = c.get("/api/push/vapid-public")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "publicKey" in data

    def test_api_estado_sem_auth(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get("/api/estado")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "h" in data

    def test_api_estado_chefe(self, client):
        c, ctx = client
        _como_chefe(c, ctx)
        r = c.get("/api/estado")
        assert r.status_code == 200
        assert "h" in json.loads(r.data)

    def test_api_estado_barbeiro(self, client):
        c, ctx = client
        _como_barbeiro(c, ctx)
        r = c.get("/api/estado")
        assert r.status_code == 200
        assert "h" in json.loads(r.data)

    def test_api_lembretes_sem_auth(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get("/api/lembretes")
        assert r.status_code == 200
        assert json.loads(r.data) == []

    def test_api_lembretes_chefe(self, client):
        c, ctx = client
        _como_chefe(c, ctx)
        r = c.get("/api/lembretes")
        assert r.status_code == 200
        assert isinstance(json.loads(r.data), list)

    def test_api_novos_agendamentos_sem_auth(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get("/api/novos-agendamentos")
        assert r.status_code == 200
        assert json.loads(r.data) == []

    def test_api_novos_agendamentos_chefe(self, client):
        c, ctx = client
        _como_chefe(c, ctx)
        r = c.get("/api/novos-agendamentos?desde_id=0")
        assert r.status_code == 200
        assert isinstance(json.loads(r.data), list)

    def test_api_slots_sem_params(self, client):
        c, ctx = client
        r = c.get("/api/slots")
        assert r.status_code == 200
        assert json.loads(r.data) == []

    def test_api_slots_com_params(self, client):
        c, ctx = client
        _como_chefe(c, ctx)
        amanha = ctx["amanha"]
        r = c.get(f"/api/slots?barbeiro_id={ctx['barb_id']}&data={amanha}"
                  f"&servico_id={ctx['svc_id']}")
        assert r.status_code == 200
        slots = json.loads(r.data)
        assert isinstance(slots, list)

    def test_api_slots_data_invalida(self, client):
        c, ctx = client
        r = c.get(f"/api/slots?barbeiro_id={ctx['barb_id']}&data=nao-e-data"
                  f"&servico_id={ctx['svc_id']}")
        assert r.status_code == 200
        assert json.loads(r.data) == []

    def test_api_slots_barbeiro_inexistente(self, client):
        c, ctx = client
        r = c.get(f"/api/slots?barbeiro_id=99999&data={ctx['amanha']}"
                  f"&servico_id={ctx['svc_id']}")
        assert r.status_code == 200
        assert json.loads(r.data) == []

    def test_api_tempo_sem_auth(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get(f"/api/tempo/{ctx['ag_futuro']}")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["segundos"] == 0

    def test_api_meu_status_sem_session(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get("/api/meu-status")
        assert r.status_code == 200
        assert json.loads(r.data) == []


# ══════════════════════════════════════════════════════════════
#  blueprints/servicos.py
# ══════════════════════════════════════════════════════════════

class TestServicosBlueprint:

    def test_get_servicos_sem_auth(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get("/servicos", follow_redirects=False)
        assert r.status_code == 302  # redirect para login

    def test_get_servicos_chefe(self, client):
        c, ctx = client
        _como_chefe(c, ctx)
        r = c.get("/servicos")
        assert r.status_code == 200
        assert b"Corte Cov" in r.data or b"servico" in r.data.lower()

    def test_criar_servico_via_blueprint(self, client):
        c, ctx = client
        _como_chefe(c, ctx)
        r = c.post("/servicos", data={"nome": "Serviço Blueprint", "duracao_min": "45", "preco": "1200"},
                   follow_redirects=False)
        assert r.status_code == 302

    def test_editar_servico_via_blueprint(self, client):
        c, ctx = client
        _como_chefe(c, ctx)
        r = c.post(f"/servicos/editar/{ctx['svc_id']}",
                   data={"nome": "Corte Editado", "duracao_min": "35", "preco": "900"},
                   follow_redirects=False)
        assert r.status_code == 302

    def test_apagar_servico_via_blueprint(self, client):
        """Criar temporário e apagá-lo via blueprint."""
        c, ctx = client
        _como_chefe(c, ctx)
        ctx["db"].criar_servico("Temp Blueprint", 15, ctx["bid"], preco=0)
        with ctx["db"]._read() as conn:
            row = conn.execute("SELECT id FROM servicos WHERE nome=? AND barbearia_id=?",
                               ("Temp Blueprint", ctx["bid"])).fetchone()
        tmp_id = row["id"]
        r = c.post(f"/servicos/apagar/{tmp_id}", follow_redirects=False)
        assert r.status_code == 302

    def test_configuracoes_get(self, client):
        c, ctx = client
        _como_chefe(c, ctx)
        r = c.get("/configuracoes")
        assert r.status_code == 200

    def test_configuracoes_post_horario(self, client):
        c, ctx = client
        _como_chefe(c, ctx)
        data = {"acao": "horario"}
        for dia in range(7):
            data[f"aberto_{dia}"] = "1"
            data[f"abertura_{dia}"] = "08:00"
            data[f"fecho_{dia}"]    = "18:00"
        r = c.post("/configuracoes", data=data, follow_redirects=False)
        assert r.status_code == 302

    def test_configuracoes_post_geral(self, client):
        c, ctx = client
        _como_chefe(c, ctx)
        r = c.post("/configuracoes",
                   data={"acao": "geral", "buffer_minutos": "5",
                         "max_por_dia": "15", "moeda": "EUR"},
                   follow_redirects=False)
        assert r.status_code == 302

    def test_configuracoes_dia_fechado_post(self, client):
        c, ctx = client
        _como_chefe(c, ctx)
        data_f = (datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d")
        r = c.post("/configuracoes",
                   data={"acao": "dia_fechado", "data_fechada": data_f, "motivo_fechado": "Teste"},
                   follow_redirects=False)
        assert r.status_code == 302


# ══════════════════════════════════════════════════════════════
#  blueprints/cliente.py
# ══════════════════════════════════════════════════════════════

class TestClienteBlueprint:

    def test_cliente_entrada_slug_invalido(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get("/cliente/slug-que-nao-existe")
        assert r.status_code == 404

    def test_cliente_entrada_get(self, client):
        c, ctx = client
        _limpar_sessao(c)
        b = ctx["db"].get_barbearia(ctx["bid"])
        slug = b.get("slug") or f"barbearia-{ctx['bid']}"
        r = c.get(f"/cliente/{slug}")
        assert r.status_code == 200

    def test_cliente_entrada_post_valido(self, client):
        c, ctx = client
        _limpar_sessao(c)
        b = ctx["db"].get_barbearia(ctx["bid"])
        slug = b.get("slug") or f"barbearia-{ctx['bid']}"
        r = c.post(f"/cliente/{slug}",
                   data={"nome": "Cliente Teste", "telefone": "912345678"},
                   follow_redirects=False)
        assert r.status_code == 302

    def test_cliente_entrada_post_sem_dados(self, client):
        c, ctx = client
        _limpar_sessao(c)
        b = ctx["db"].get_barbearia(ctx["bid"])
        slug = b.get("slug") or f"barbearia-{ctx['bid']}"
        r = c.post(f"/cliente/{slug}", data={"nome": "", "telefone": ""},
                   follow_redirects=False)
        assert r.status_code == 200  # mantém na página com erro

    def test_cliente_home_sem_sessao(self, client):
        c, ctx = client
        _limpar_sessao(c)
        b = ctx["db"].get_barbearia(ctx["bid"])
        slug = b.get("slug") or f"barbearia-{ctx['bid']}"
        r = c.get(f"/cliente/{slug}/area", follow_redirects=False)
        assert r.status_code == 302  # redireciona para entrada

    def test_cliente_home_com_sessao(self, client):
        c, ctx = client
        b = ctx["db"].get_barbearia(ctx["bid"])
        slug = b.get("slug") or f"barbearia-{ctx['bid']}"
        with c.session_transaction() as s:
            s["role"]         = "cliente"
            s["barbearia_id"] = ctx["bid"]
            s["telefone"]     = "912345678"
            s["user_nome"]    = "Cliente Teste"
        r = c.get(f"/cliente/{slug}/area")
        assert r.status_code == 200

    def test_cliente_marcar_get(self, client):
        c, ctx = client
        b = ctx["db"].get_barbearia(ctx["bid"])
        slug = b.get("slug") or f"barbearia-{ctx['bid']}"
        with c.session_transaction() as s:
            s["role"]         = "cliente"
            s["barbearia_id"] = ctx["bid"]
            s["telefone"]     = "912345678"
            s["user_nome"]    = "Cliente Teste"
        r = c.get(f"/cliente/{slug}/marcar")
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════
#  helpers_booking.py — funções de validação e vocab
# ══════════════════════════════════════════════════════════════

class TestHelpersBooking:

    def test_val_data_valida(self):
        from helpers_booking import _val_data
        assert _val_data("2026-06-15") is True
        assert _val_data("2000-01-01") is True

    def test_val_data_invalida(self):
        from helpers_booking import _val_data
        assert _val_data("") is False
        assert _val_data("2026-13-01") is False  # mês 13
        assert _val_data("2026/06/15") is False
        assert _val_data(None) is False

    def test_val_hora_valida(self):
        from helpers_booking import _val_hora
        assert _val_hora("08:00") is True
        assert _val_hora("23:59") is True

    def test_val_hora_invalida(self):
        from helpers_booking import _val_hora
        assert _val_hora("24:00") is False
        assert _val_hora("8:0") is False
        assert _val_hora("") is False
        assert _val_hora(None) is False

    def test_limpar(self):
        from helpers_booking import _limpar
        assert _limpar("  olá  ") == "olá"
        assert _limpar("x" * 200, maxlen=10) == "x" * 10
        assert _limpar(None) == ""

    def test_pluralize_pt(self):
        from helpers_booking import _pluralize_pt
        assert _pluralize_pt("Barbeiro") == "Barbeiros"
        assert _pluralize_pt("Profissional") == "Profissionais"
        assert _pluralize_pt("Serviço") == "Serviços"  # termina em 'ço' → +s
        assert _pluralize_pt("Marcação") == "Marcações"
        assert _pluralize_pt("") == ""

    def test_get_vocab_barbearia(self):
        from helpers_booking import get_vocab
        v = get_vocab("barbearia")
        assert v["profissional"] == "Barbeiro"
        assert v["agendamento"] == "Marcação"

    def test_get_vocab_spa(self):
        from helpers_booking import get_vocab
        v = get_vocab("spa")
        assert v["profissional"] == "Terapeuta"

    def test_get_vocab_outro_custom(self):
        from helpers_booking import get_vocab
        custom = json.dumps({"tipo_label": "Clínica", "profissional": "Médico",
                             "servico": "Consulta", "agendamento": "Consulta"})
        v = get_vocab("outro", custom)
        assert v["profissional"] == "Médico"
        assert v["servico"] == "Consulta"

    def test_get_vocab_tipo_none(self):
        from helpers_booking import get_vocab
        v = get_vocab(None)
        assert v["profissional"] == "Barbeiro"  # fallback para barbearia

    def test_cache_get_set_del(self):
        from helpers_booking import _pc_get, _pc_set, _pc_del
        _pc_set("test:cov:1", "valor_teste", 60)
        assert _pc_get("test:cov:1") == "valor_teste"
        _pc_del("test:cov:")
        assert _pc_get("test:cov:1") is None

    def test_cache_expirado(self):
        from helpers_booking import _pc_get, _pc_set
        import time
        _pc_set("test:exp:1", "expira", 0.001)
        time.sleep(0.01)
        assert _pc_get("test:exp:1") is None

    def test_pc_evict(self):
        from helpers_booking import _pc_set, _pc_evict, _pc_get
        import time
        _pc_set("test:evict:1", "x", 0.001)
        time.sleep(0.01)
        _pc_evict()
        assert _pc_get("test:evict:1") is None


# ══════════════════════════════════════════════════════════════
#  helpers_security.py — rate limit + mime
# ══════════════════════════════════════════════════════════════

class TestHelpersSecurity:

    def test_mime_ok_jpeg(self):
        from helpers_security import _mime_ok
        assert _mime_ok(b'\xff\xd8\xff\xe0' + b'\x00' * 100) is True

    def test_mime_ok_png(self):
        from helpers_security import _mime_ok
        assert _mime_ok(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100) is True

    def test_mime_ok_webp(self):
        from helpers_security import _mime_ok
        assert _mime_ok(b'RIFF\x00\x00\x00\x00WEBP' + b'\x00' * 100) is True

    def test_mime_ok_invalido(self):
        from helpers_security import _mime_ok
        assert _mime_ok(b'\x00\x01\x02\x03') is False
        assert _mime_ok(b'EXE header') is False

    def test_validar_imagem_jpeg(self):
        from helpers_security import _validar_imagem
        dados = b'\xff\xd8\xff\xe0' + b'\x00' * 100
        assert _validar_imagem(dados, "image/jpeg") is True
        assert _validar_imagem(dados, "image/png") is False

    def test_validar_imagem_png(self):
        from helpers_security import _validar_imagem
        dados = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        assert _validar_imagem(dados, "image/png") is True

    def test_user_locked_e_record_fail(self):
        from helpers_security import _user_locked, _record_fail, _clear_fails, _USER_MAX
        user = "test_lockout_cov"
        _clear_fails(user)
        assert _user_locked(user) is False
        for _ in range(_USER_MAX):
            _record_fail(user)
        assert _user_locked(user) is True
        _clear_fails(user)
        assert _user_locked(user) is False

    def test_api_ok_basico(self):
        from helpers_security import _api_ok
        # Primeiro pedido de um IP novo deve passar
        assert _api_ok("192.168.99.1") is True

    def test_ip_retry_after_sem_bloqueio(self):
        from helpers_security import _ip_retry_after
        assert _ip_retry_after("192.168.99.200") == 0


# ══════════════════════════════════════════════════════════════
#  Rotas protegidas — auth redirect
# ══════════════════════════════════════════════════════════════

class TestRotasProtegidas:

    def test_index_sem_auth_redireciona(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 302

    def test_barbeiros_sem_auth(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get("/barbeiros", follow_redirects=False)
        assert r.status_code == 302

    def test_servicos_sem_auth(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get("/servicos", follow_redirects=False)
        assert r.status_code == 302

    def test_configuracoes_sem_auth(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get("/configuracoes", follow_redirects=False)
        assert r.status_code == 302

    def test_novo_sem_auth(self, client):
        c, ctx = client
        _limpar_sessao(c)
        r = c.get("/novo", follow_redirects=False)
        assert r.status_code == 302

    def test_historico_chefe(self, client):
        c, ctx = client
        _como_chefe(c, ctx)
        r = c.get("/historico")
        assert r.status_code == 200

    def test_estatisticas_chefe(self, client):
        c, ctx = client
        _como_chefe(c, ctx)
        r = c.get("/estatisticas")
        assert r.status_code == 200

    def test_healthz(self, client):
        c, ctx = client
        r = c.get("/healthz")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["status"] == "ok"
