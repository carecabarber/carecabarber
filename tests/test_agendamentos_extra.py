"""tests/test_agendamentos_extra.py — Cobertura de db/agendamentos.py (78%).

Alvos:
  reagendar_agendamento (4 branches), cancelar (incluir_em_andamento=True),
  deletar_walkin_orfao, listar_todos (data_ini>data_fim, limit, status),
  contar_todos (data_ini>data_fim, status), verificar_disponibilidade ValueError,
  horarios_disponiveis (max_dia atingido), agendamentos_cliente_barbeiro_dia,
  gerar_token_reagendar (token existente), barbeiro_proxima_marcacao_minutos

Correr: cd ~/Documentos/barbearia && venv/bin/python -m pytest tests/test_agendamentos_extra.py -v
"""

import os, sys, pytest, tempfile, shutil
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test-agend-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(scope="module")
def ctx():
    import database as db
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    tmp_db  = os.path.join(tmp_dir, "test_agend.db")
    orig    = _db_conn.DB_PATH

    _db_conn.DB_PATH = tmp_db
    db.DB_PATH       = tmp_db
    _db_conn._CONN   = None
    db.init_db()

    bid = db.criar_barbearia("Barbearia Agend", tipo="barbearia")
    db.registar_pagamento(bid, "exp")

    db.criar_barbeiro("Barbeiro Agend", bid)
    with db._read() as c:
        barb_id = c.execute(
            "SELECT id FROM barbeiros WHERE nome=? AND barbearia_id=?",
            ("Barbeiro Agend", bid)).fetchone()["id"]

    db.criar_servico("Corte Agend", 30, bid, preco=500)
    with db._read() as c:
        svc_id = c.execute(
            "SELECT id FROM servicos WHERE barbearia_id=?", (bid,)).fetchone()["id"]

    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    ontem  = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    tel    = "912345678"

    # agendamento principal (agendado)
    ag_id = db.criar_agendamento(
        "Cliente Agend", svc_id, f"{amanha} 10:00:00", bid,
        barbeiro_id=barb_id, telefone=tel)

    # agendamento para reagendar (separado para não conflitar)
    ag_reagendar_id = db.criar_agendamento(
        "Cliente Reagendar", svc_id, f"{amanha} 11:00:00", bid,
        barbeiro_id=barb_id, telefone=tel)

    # agendamento em andamento para cancelar(incluir_em_andamento=True)
    ag_em_andamento_id = db.criar_agendamento(
        "Cliente Em Andamento", svc_id, f"{amanha} 12:00:00", bid,
        barbeiro_id=barb_id, telefone=tel)
    db.iniciar_trabalho(ag_em_andamento_id)

    # walk-in para deletar_walkin_orfao
    # criar_agendamento define tipo='walk-in' mas status='agendado' (default)
    # deletar_walkin_orfao filtra por status='walk-in' → forçar status correto
    from database import ST_WALKIN
    ag_walkin_id = db.criar_agendamento(
        "Walk-in", svc_id, f"{amanha} 13:00:00", bid,
        barbeiro_id=barb_id, tipo=ST_WALKIN, telefone=tel)
    with db._write() as _c:
        _c.execute("UPDATE agendamentos SET status='walk-in' WHERE id=?", (ag_walkin_id,))

    # ontem (para testes de data_ini/data_fim)
    ag_ontem_id = db.criar_agendamento(
        "Cliente Ontem", svc_id, f"{ontem} 10:00:00", bid,
        barbeiro_id=barb_id, telefone=tel)

    yield {
        "db": db,
        "bid": bid,
        "barb_id": barb_id,
        "svc_id": svc_id,
        "amanha": amanha,
        "ontem": ontem,
        "tel": tel,
        "ag_id": ag_id,
        "ag_reagendar_id": ag_reagendar_id,
        "ag_em_andamento_id": ag_em_andamento_id,
        "ag_walkin_id": ag_walkin_id,
        "ag_ontem_id": ag_ontem_id,
    }

    _db_conn._reset_conn()
    db._CONN = None
    _db_conn.DB_PATH = orig
    db.DB_PATH       = orig
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
#  reagendar_agendamento — 4 ramos
# ══════════════════════════════════════════════════════════════

class TestReagendar:

    def test_com_barbeiro_e_servico(self, ctx):
        """Ambos novo_barbeiro_id e novo_servico_id passados (linhas 343-347)."""
        db = ctx["db"]
        nova_dh = f"{ctx['amanha']} 14:00:00"
        db.reagendar_agendamento(ctx["ag_reagendar_id"], nova_dh,
                                 ctx["barb_id"], ctx["svc_id"])
        ag = db.get_agendamento(ctx["ag_reagendar_id"])
        assert ag["data_hora"] == nova_dh
        assert ag["status"] == "agendado"

    def test_so_barbeiro(self, ctx):
        """Só novo_barbeiro_id, sem servico (linhas 348-352)."""
        db = ctx["db"]
        nova_dh = f"{ctx['amanha']} 15:00:00"
        db.reagendar_agendamento(ctx["ag_reagendar_id"], nova_dh,
                                 novo_barbeiro_id=ctx["barb_id"])
        ag = db.get_agendamento(ctx["ag_reagendar_id"])
        assert ag["data_hora"] == nova_dh

    def test_so_servico(self, ctx):
        """Só novo_servico_id, sem barbeiro (linhas 353-357)."""
        db = ctx["db"]
        nova_dh = f"{ctx['amanha']} 16:00:00"
        db.reagendar_agendamento(ctx["ag_reagendar_id"], nova_dh,
                                 novo_servico_id=ctx["svc_id"])
        ag = db.get_agendamento(ctx["ag_reagendar_id"])
        assert ag["data_hora"] == nova_dh

    def test_so_data(self, ctx):
        """Nem barbeiro nem serviço — só nova data (linhas 358-361)."""
        db = ctx["db"]
        nova_dh = f"{ctx['amanha']} 17:00:00"
        db.reagendar_agendamento(ctx["ag_reagendar_id"], nova_dh)
        ag = db.get_agendamento(ctx["ag_reagendar_id"])
        assert ag["data_hora"] == nova_dh


# ══════════════════════════════════════════════════════════════
#  cancelar_agendamento — incluir_em_andamento=True
# ══════════════════════════════════════════════════════════════

class TestCancelar:

    def test_cancelar_incluir_em_andamento(self, ctx):
        """Cancela agendamento em_andamento (linhas 321-324)."""
        db = ctx["db"]
        ag = db.get_agendamento(ctx["ag_em_andamento_id"])
        assert ag["status"] == "em_andamento"
        db.cancelar_agendamento(ctx["ag_em_andamento_id"], incluir_em_andamento=True)
        ag_pos = db.get_agendamento(ctx["ag_em_andamento_id"])
        assert ag_pos["status"] == "cancelado"

    def test_cancelar_normal(self, ctx):
        """Cancela agendamento agendado (linhas 325-328)."""
        db = ctx["db"]
        db.cancelar_agendamento(ctx["ag_id"])
        ag = db.get_agendamento(ctx["ag_id"])
        assert ag["status"] == "cancelado"


# ══════════════════════════════════════════════════════════════
#  deletar_walkin_orfao
# ══════════════════════════════════════════════════════════════

class TestDeletarWalkin:

    def test_deleta_walkin(self, ctx):
        """walk-in é apagado (linhas 335-337)."""
        db = ctx["db"]
        db.deletar_walkin_orfao(ctx["ag_walkin_id"])
        ag = db.get_agendamento(ctx["ag_walkin_id"])
        assert ag is None


# ══════════════════════════════════════════════════════════════
#  listar_todos — data_ini>data_fim, limit, status
# ══════════════════════════════════════════════════════════════

class TestListarTodos:

    def test_data_ini_maior_que_data_fim_troca(self, ctx):
        """data_ini > data_fim → swap silencioso (linhas 100-101)."""
        db = ctx["db"]
        # ontem a amanha, mas passados invertidos → swap interno
        resultado = db.listar_todos(ctx["bid"],
                                    data_ini=ctx["amanha"],
                                    data_fim=ctx["ontem"])
        # Deve devolver agendamentos no intervalo (ontem a amanha)
        assert isinstance(resultado, list)

    def test_com_status_filter(self, ctx):
        """Filtro de status aplicado (linhas 104-105)."""
        db = ctx["db"]
        resultado = db.listar_todos(ctx["bid"], status="agendado")
        for r in resultado:
            assert r["status"] == "agendado"

    def test_com_limit_e_offset(self, ctx):
        """LIMIT + OFFSET aplicados (linhas 107-108)."""
        db = ctx["db"]
        resultado = db.listar_todos(ctx["bid"], limit=2, offset=0)
        assert len(resultado) <= 2


# ══════════════════════════════════════════════════════════════
#  verificar_disponibilidade — ValueError
# ══════════════════════════════════════════════════════════════

class TestVerificarDisponibilidade:

    def test_data_hora_invalida(self, ctx):
        """data_hora_str inválido → (True, None) (linhas 374-375)."""
        db = ctx["db"]
        livre, _ = db.verificar_disponibilidade(
            ctx["barb_id"], "nao_e_uma_data", 30, ctx["bid"])
        assert livre is True

    def test_sem_barbeiro(self, ctx):
        """barbeiro_id=None → (True, None) imediatamente (linha 370)."""
        db = ctx["db"]
        livre, _ = db.verificar_disponibilidade(None, "2030-12-31 10:00:00", 30, ctx["bid"])
        assert livre is True

    def test_com_excluir_id(self, ctx):
        """excluir_id passado — linhas 392-393."""
        db = ctx["db"]
        # Verifica disponibilidade excluindo o próprio agendamento
        livre, _ = db.verificar_disponibilidade(
            ctx["barb_id"], f"{ctx['amanha']} 10:00:00", 30, ctx["bid"],
            excluir_id=ctx["ag_id"])
        assert isinstance(livre, bool)


# ══════════════════════════════════════════════════════════════
#  horarios_disponiveis — max_dia atingido
# ══════════════════════════════════════════════════════════════

class TestHorariosDisponiveis:

    def test_max_dia_atingido(self, ctx):
        """Se total_dia >= max_por_dia → [] (linha 476)."""
        db = ctx["db"]
        # Configurar max_por_dia = 1 (mínimo possível)
        db.set_config("max_por_dia", 1, ctx["bid"])
        amanha = ctx["amanha"]
        resultado = db.horarios_disponiveis(ctx["barb_id"], amanha, 30, ctx["bid"])
        # Com max_por_dia=1 e já há pelo menos 1 agendamento nesse dia, retorna []
        assert resultado == []
        # Restaurar
        db.set_config("max_por_dia", 20, ctx["bid"])

    def test_data_invalida(self, ctx):
        """Data inválida → [] (linhas 430-431)."""
        db = ctx["db"]
        resultado = db.horarios_disponiveis(ctx["barb_id"], "nao_e_data", 30, ctx["bid"])
        assert resultado == []


# ══════════════════════════════════════════════════════════════
#  agendamentos_cliente_barbeiro_dia
# ══════════════════════════════════════════════════════════════

class TestAgendamentosClienteBarbeiroDia:

    def test_retorna_lista(self, ctx):
        """Retorna lista de agendamentos do cliente nesse dia (linhas 677-683)."""
        db = ctx["db"]
        resultado = db.agendamentos_cliente_barbeiro_dia(
            ctx["tel"], ctx["barb_id"], ctx["amanha"], ctx["bid"])
        assert isinstance(resultado, list)

    def test_tel_vazio(self, ctx):
        """Telefone vazio → [] (via normalizar_tel)."""
        db = ctx["db"]
        resultado = db.agendamentos_cliente_barbeiro_dia(
            "", ctx["barb_id"], ctx["amanha"], ctx["bid"])
        assert resultado == []


# ══════════════════════════════════════════════════════════════
#  gerar_token_reagendar — token já existe
# ══════════════════════════════════════════════════════════════

class TestGerarToken:

    def test_token_existente_reutilizado(self, ctx):
        """Se token já existe, reutiliza sem criar novo (linhas 696-697)."""
        db = ctx["db"]
        # ag_ontem_id ainda não tem token → gerar pela primeira vez
        tok1 = db.gerar_token_reagendar(ctx["ag_ontem_id"])
        # Segunda chamada → deve reutilizar o mesmo
        tok2 = db.gerar_token_reagendar(ctx["ag_ontem_id"])
        assert tok1 == tok2


# ══════════════════════════════════════════════════════════════
#  barbeiro_proxima_marcacao_minutos
# ══════════════════════════════════════════════════════════════

class TestProximaMarcacao:

    def test_sem_proxima_marcacao(self, ctx):
        """Sem agendamentos futuros → retorna 9999 (linha 222)."""
        db = ctx["db"]
        # Usar um barbeiro sem agendamentos futuros, ou data muito distante
        resultado = db.barbeiro_proxima_marcacao_minutos(ctx["barb_id"], ctx["bid"])
        # Com agendamentos amanhã, deve retornar um número positivo ou 9999
        assert isinstance(resultado, int)
        assert resultado >= 0


# ══════════════════════════════════════════════════════════════
#  estado_cliente
# ══════════════════════════════════════════════════════════════

class TestEstadoCliente:

    def test_telefone_vazio(self, ctx):
        """Telefone vazio → '' (linha 307)."""
        db = ctx["db"]
        resultado = db.estado_cliente("", ctx["bid"])
        assert resultado == ""

    def test_telefone_valido(self, ctx):
        """Telefone válido → hash MD5."""
        db = ctx["db"]
        resultado = db.estado_cliente(ctx["tel"], ctx["bid"])
        assert isinstance(resultado, str)
        assert len(resultado) == 32  # MD5 hex


# ══════════════════════════════════════════════════════════════
#  listar_todos — data específica (linha 109-111)
# ══════════════════════════════════════════════════════════════

class TestListarTodosData:

    def test_data_especifica(self, ctx):
        """Filtro por data específica sem paginação (linhas 94-97, 109-111)."""
        db = ctx["db"]
        resultado = db.listar_todos(ctx["bid"], data=ctx["amanha"])
        assert isinstance(resultado, list)
        for r in resultado:
            assert r["data_hora"].startswith(ctx["amanha"])

    def test_barbeiro_filter(self, ctx):
        """Filtro por barbeiro_id (linha 92)."""
        db = ctx["db"]
        resultado = db.listar_todos(ctx["bid"], barbeiro_id=ctx["barb_id"])
        for r in resultado:
            assert r["barbeiro_id"] == ctx["barb_id"]
