"""tests/test_autorizacao.py — Quem NÃO pode chegar a quê.

O resto da suite prova que as coisas funcionam. Este ficheiro prova que estão
fechadas, que é outra pergunta: a fuga do token do painel pelo QR passou por
992 testes verdes porque nenhum deles tentava chegar a sítio nenhum.

São três camadas:

1. `EXIGE` declara, para cada rota, o nível mínimo de quem lá pode entrar.
   `test_todas_as_rotas_estao_classificadas` rebenta se alguém acrescentar uma
   rota e não a declarar aqui — é o que impede o padrão de voltar.
2. A matriz percorre todas as rotas com todas as personas abaixo do nível
   exigido e confirma que são recusadas E que a resposta não traz dados de
   dentro (nomes de clientes, telefones, tokens).
3. Testes dirigidos ao que a matriz não vê: IDOR entre barbearias, confusão
   entre tokens, e mutações que não podem acontecer.

Correr: venv/bin/python -m pytest tests/test_autorizacao.py -v
"""

import os, sys, json, shutil, tempfile, pytest
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test-autz-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ══════════════════════════════════════════════════════════════
#  NÍVEIS
# ══════════════════════════════════════════════════════════════

# Ordem de privilégio. Quem está abaixo do exigido tem de ser recusado.
NIVEIS = {"publico": 0, "token": 0, "cliente": 1, "staff": 2, "chefe": 3, "root": 4}

# Personas que a matriz experimenta em cada rota, por ordem de privilégio.
PERSONAS_ORDEM = ["anon", "cliente", "barbeiro", "chefe", "root"]
PERSONA_NIVEL  = {"anon": 0, "cliente": 1, "barbeiro": 2, "chefe": 3, "root": 4}

# Nível mínimo exigido por rota.
#
#   publico — aberta a toda a gente, não há nada lá dentro para proteger
#   token   — sem sessão, mas o segredo está no URL; ver TestTokens
#   cliente/staff/chefe/root — exige sessão desse nível ou acima
#
# Ao acrescentar uma rota, classifica-a aqui. Se não souberes qual é, é sinal
# de que a rota ainda não está pensada.
EXIGE = {
    # ── Infra-estrutura e páginas sem conteúdo privado ──
    "_clone_beacon":             "publico",
    "_honeypot":                 "publico",
    "health":                    "publico",
    "healthz":                   "publico",
    "robots":                    "publico",
    "offline":                   "publico",
    "pwa_manifest":              "publico",
    "service_worker":            "publico",
    "login":                     "publico",
    "logout":                    "publico",
    "conta_suspensa":            "publico",
    "api_spec":                  "publico",   # só o mapa de rotas, sem dados
    "api_push_vapid_public":     "publico",   # chave pública, é para ser pública
    "foto_barbeiro":             "publico",   # foto do profissional, aparece na página do cliente

    # ── Páginas do cliente: o segredo é o token no URL ──
    "avaliar_link":              "token",
    "cancelar_link":             "token",
    "reagendar_link":            "token",
    "ag_acao_cliente":           "token",
    "cliente_confirmar":         "token",

    # ── Mesa: dois tokens distintos, ver TestTokens ──
    "mesa":                      "token",   # mesa_token — PRIVADO, abre o painel
    "mesa_entrar":               "token",   # qr_token — público, só a fila
    "mesa_entrar_legado":        "token",
    "mesa_info":                 "token",
    "mesa_iniciar":              "token",   # só mesa_token
    "mesa_terminar":             "token",   # só mesa_token
    "mesa_walkin_post":          "token",

    # ── Área do cliente: exige sessão de cliente ──
    "cliente_entrada":           "publico",  # é o próprio ecrã de entrada
    "cliente_marcar":            "publico",  # marcar sem conta é o objectivo
    "cliente_home":              "cliente",
    "cliente_cancelar":          "cliente",
    "cliente_reagendar":         "cliente",
    "cliente_confirmacao":       "cliente",
    "cliente_fila_espera":       "cliente",
    "cliente_dispensar_espera":  "cliente",
    "cliente_acao_servico":      "cliente",
    "api_cliente_push_subscribe":   "cliente",
    "api_cliente_push_unsubscribe": "cliente",
    "api_meu_status":            "cliente",

    # ── Staff ──
    "index":                     "staff",
    "novo":                      "staff",
    "walkin":                    "staff",
    "historico":                 "staff",
    "minhas_marcacoes":          "staff",
    "perfil":                    "staff",
    "perfil_foto_upload":        "staff",
    "perfil_foto_apagar":        "staff",
    "perfil_revogar_qr":         "staff",
    "set_theme":                 "staff",
    "iniciar":                   "staff",
    "terminar":                  "staff",
    "cancelar":                  "staff",
    "reagendar":                 "staff",
    "avaliar":                   "staff",
    "nao_compareceu":            "staff",
    "bloquear_horario":          "staff",
    "desbloquear_horario":       "staff",
    "estatisticas":              "staff",
    "estatisticas_barbeiro":     "staff",
    # `/api/estado` é de propósito acessível ao cliente: devolve-lhe o hash das
    # marcações DELE (ramo `role == "cliente"`), que é o que faz o polling da
    # página do cliente funcionar. Não é uma falha de classificação.
    "api_estado":                "cliente",
    "api_lembretes":             "staff",
    "api_novos_agendamentos":    "staff",
    "api_marcar_lembrete":       "staff",
    "api_tempo":                 "staff",
    "api_slots":                 "staff",
    "api_push_subscribe":        "staff",
    "api_push_unsubscribe":      "staff",

    # ── Chefe ──
    "barbeiros":                 "chefe",
    "barbeiro_foto_upload":      "chefe",
    "barbeiro_foto_apagar":      "chefe",
    "editar_barbeiro":           "chefe",
    "apagar_barbeiro":           "chefe",
    "toggle_barbeiro":           "chefe",
    "set_credenciais":           "chefe",
    "repor_senha_barbeiro":      "chefe",
    "revogar_qr_barbeiro":       "chefe",
    "set_pausa_almoco":          "chefe",
    "criar_ausencia":            "chefe",
    "apagar_ausencia":           "chefe",
    "servicos":                  "chefe",
    "editar_servico":            "chefe",
    "apagar_servico":            "chefe",
    "toggle_servico":            "chefe",
    "mover_servico":             "chefe",
    "configuracoes":             "chefe",
    "clientes_analytics":        "chefe",
    "clientes_bloqueados":       "chefe",
    "cliente_bloquear_post":     "chefe",
    "cliente_desbloquear_post":  "chefe",
    "cliente_fidelidade_reset":  "chefe",
    "fila_espera":               "chefe",
    "fila_espera_remover":       "chefe",
    "historico_exportar_csv":    "chefe",
    "relatorio_pdf":             "chefe",

    # ── Root ──
    "root_dashboard":            "root",
    "root_criar_barbearia":      "root",
    "root_editar_barbearia":     "root",
    "root_apagar_barbearia":     "root",
    "root_toggle_barbearia":     "root",
    "root_gerir_barbearia":      "root",
    "root_sair_barbearia":       "root",
    "root_logo_barbearia":       "root",
    "root_alterar_senha":        "root",
    "root_registar_pagamento":   "root",
    "root_cancelar_plano":       "root",
    "root_planos_barbearia":     "root",
    "root_precos":               "root",
    "root_precos_barbearia":     "root",
    "root_definir_precos":       "root",
    "sso_invoice":               "root",
}


# ══════════════════════════════════════════════════════════════
#  MUNDO — duas barbearias completas, com canários
# ══════════════════════════════════════════════════════════════

# Strings que só existem dentro da barbearia A. Se alguma aparecer numa
# resposta a quem não tem nível, houve fuga — não interessa o código HTTP.
CANARIOS = ["ZzCanarioClienteA", "ZzCanarioNotaPrivadaA"]


@pytest.fixture(scope="module")
def mundo():
    import database as db
    import db._conn as _db_conn

    tmp_dir = tempfile.mkdtemp()
    orig    = _db_conn.DB_PATH
    _db_conn.DB_PATH = os.path.join(tmp_dir, "autz.db")
    db.DB_PATH       = _db_conn.DB_PATH
    _db_conn._CONN   = None
    db.init_db()

    def _barbearia(nome, slug, cliente_canario=False):
        bid = db.criar_barbearia(nome, tipo="barbearia")
        db.registar_pagamento(bid, "exp")
        with db._write() as c:
            c.execute("UPDATE barbearias SET slug=? WHERE id=?", (slug, bid))
        db.criar_chefe(f"Chefe {nome}", f"chefe_{slug}", "senha123", bid)
        db.criar_barbeiro(f"Barbeiro {nome}", bid)
        db.criar_servico(f"Corte {nome}", 30, bid, preco=1000)
        with db._read() as c:
            chefe = c.execute("SELECT id FROM barbeiros WHERE username=?",
                              (f"chefe_{slug}",)).fetchone()["id"]
            barb  = c.execute(
                "SELECT id, mesa_token, qr_token FROM barbeiros "
                "WHERE nome=? AND barbearia_id=?", (f"Barbeiro {nome}", bid)).fetchone()
            svc   = c.execute("SELECT id FROM servicos WHERE barbearia_id=?",
                              (bid,)).fetchone()["id"]
        amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        ag = db.criar_agendamento(
            "ZzCanarioClienteA" if cliente_canario else f"Cliente {nome}",
            svc, f"{amanha} 10:00:00", bid, barbeiro_id=barb["id"],
            telefone="912000111" if cliente_canario else "911111111",
            notas="ZzCanarioNotaPrivadaA" if cliente_canario else None)
        with db._read() as c:
            toks = c.execute(
                "SELECT token_reagendar, token_avaliar FROM agendamentos WHERE id=?",
                (ag,)).fetchone()
        # Um segundo agendamento, este a decorrer. Sem ele, /api/tempo devolve
        # zeros a toda a gente e a matriz dá-o por seguro sem o ter testado —
        # a rota só revela alguma coisa quando há um cronómetro a andar.
        ag_and = db.criar_agendamento(
            "ZzCanarioClienteA" if cliente_canario else f"Andamento {nome}",
            svc, f"{amanha} 11:00:00", bid, barbeiro_id=barb["id"],
            telefone="912000111" if cliente_canario else "911111111")
        db.iniciar_trabalho(ag_and)
        return {
            "bid": bid, "slug": slug, "chefe_id": chefe, "barb_id": barb["id"],
            "mesa_token": barb["mesa_token"], "qr_token": barb["qr_token"],
            "svc_id": svc, "ag_id": ag, "ag_andamento_id": ag_and,
            "tok_reagendar": toks["token_reagendar"],
            "tok_avaliar": toks["token_avaliar"],
            "telefone": "912000111" if cliente_canario else "911111111",
        }

    A = _barbearia("Alfa", "alfa", cliente_canario=True)
    B = _barbearia("Beta", "beta")

    yield {"db": db, "A": A, "B": B}

    _db_conn._reset_conn()
    db._CONN = None
    _db_conn.DB_PATH = orig
    db.DB_PATH       = orig
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def cli(mundo):
    import app as app_module
    app_module.app.config.update({
        "TESTING": True, "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-autz", "SESSION_COOKIE_SECURE": False,
    })
    with app_module.app.test_client() as c:
        yield c, mundo


@pytest.fixture(autouse=True)
def _sem_bans():
    """Limpa o ban do honeypot antes e depois de cada teste deste ficheiro.

    Este ficheiro bate em todas as rotas da app, e o estado de banimento vive
    num dicionário de módulo — partilhado por toda a sessão de pytest. Sem esta
    limpeza, um 403 apanhado aqui viajava para os ficheiros seguintes.
    """
    import app as app_module
    app_module._trap_banidos.clear()
    yield
    app_module._trap_banidos.clear()


def _persona(c, mundo, quem):
    """Põe a sessão na persona pedida. Todas pertencem à barbearia A."""
    A = mundo["A"]
    with c.session_transaction() as s:
        s.clear()
        if quem == "anon":
            return
        if quem == "cliente":
            s["role"] = "cliente"; s["user_id"] = 0
            s["barbearia_id"] = A["bid"]; s["telefone"] = A["telefone"]
        elif quem == "barbeiro":
            s["role"] = "barbeiro"; s["user_id"] = A["barb_id"]
            s["barbearia_id"] = A["bid"]; s["user_nome"] = "Barbeiro Alfa"
        elif quem == "chefe":
            s["role"] = "chefe"; s["user_id"] = A["chefe_id"]
            s["barbearia_id"] = A["bid"]; s["user_nome"] = "Chefe Alfa"
        elif quem == "root":
            s["role"] = "root"; s["user_id"] = A["chefe_id"]; s["user_nome"] = "Root"


# ══════════════════════════════════════════════════════════════
#  Construção dos URLs a partir do mapa real
# ══════════════════════════════════════════════════════════════

# Rotas que a matriz classifica mas não visita. O honeypot bane o IP de quem lá
# bate — e o IP aqui é o 127.0.0.1 de toda a gente. Visitá-lo faria o teste
# seguinte apanhar 403 sem razão nenhuma, e (por o ban viver num dicionário de
# módulo) contaminaria os ficheiros de teste a seguir a este.
_NAO_VISITAR = {"_honeypot"}


def _urls(mundo):
    """Um URL por rota, com parâmetros válidos da barbearia A.

    Sai do url_map em vez de uma lista escrita à mão para que uma rota nova
    apareça aqui sozinha — e depois rebente no teste de classificação.
    """
    import app as app_module
    A = mundo["A"]
    valores = {
        "token": A["tok_avaliar"], "slug": A["slug"], "id": A["ag_id"],
        "ag_id": A["ag_id"], "barbeiro_id": A["barb_id"], "acao": "iniciar",
        "direcao": "cima", "tel_enc": A["telefone"],
    }
    # Onde o <int:id> não é um agendamento
    por_endpoint = {
        # O cronómetro só diz alguma coisa se estiver a andar — ver `mundo`.
        "api_tempo": {"id": A["ag_andamento_id"]},
        "estatisticas_barbeiro": {"id": A["barb_id"]},
        "editar_barbeiro": {"id": A["barb_id"]}, "apagar_barbeiro": {"id": A["barb_id"]},
        "toggle_barbeiro": {"id": A["barb_id"]}, "set_credenciais": {"id": A["barb_id"]},
        "repor_senha_barbeiro": {"id": A["barb_id"]}, "revogar_qr_barbeiro": {"id": A["barb_id"]},
        "set_pausa_almoco": {"id": A["barb_id"]}, "barbeiro_foto_upload": {"id": A["barb_id"]},
        "barbeiro_foto_apagar": {"id": A["barb_id"]},
        "editar_servico": {"id": A["svc_id"]}, "apagar_servico": {"id": A["svc_id"]},
        "toggle_servico": {"id": A["svc_id"]}, "mover_servico": {"id": A["svc_id"]},
        "mesa": {"token": A["mesa_token"]}, "mesa_iniciar": {"token": A["mesa_token"]},
        "mesa_terminar": {"token": A["mesa_token"]}, "mesa_entrar_legado": {"token": A["mesa_token"]},
        "mesa_entrar": {"token": A["qr_token"]}, "mesa_info": {"token": A["qr_token"]},
        "mesa_walkin_post": {"token": A["qr_token"]},
        "reagendar_link": {"token": A["tok_reagendar"]},
        "cliente_confirmar": {"token": A["tok_reagendar"]},
        "root_editar_barbearia": {"id": A["bid"]}, "root_apagar_barbearia": {"id": A["bid"]},
        "root_toggle_barbearia": {"id": A["bid"]}, "root_gerir_barbearia": {"id": A["bid"]},
        "root_logo_barbearia": {"id": A["bid"]}, "root_registar_pagamento": {"id": A["bid"]},
        "root_cancelar_plano": {"id": A["bid"]}, "root_planos_barbearia": {"id": A["bid"]},
        "root_precos_barbearia": {"id": A["bid"]},
    }
    out = []
    for r in app_module.app.url_map.iter_rules():
        if r.endpoint == "static" or r.endpoint in _NAO_VISITAR:
            continue
        args = {}
        for arg in r.arguments:
            args[arg] = por_endpoint.get(r.endpoint, {}).get(arg, valores.get(arg, 1))
        metodos = [m for m in ("GET", "POST") if m in r.methods]
        if not metodos:
            continue
        try:
            with app_module.app.test_request_context():
                from flask import url_for
                url = url_for(r.endpoint, **args)
        except Exception:
            url = r.rule
        out.append((r.endpoint, url, metodos[0]))
    return sorted(set(out))


# ══════════════════════════════════════════════════════════════
#  1. Classificação obrigatória
# ══════════════════════════════════════════════════════════════

class TestClassificacao:

    def test_todas_as_rotas_estao_classificadas(self, cli):
        """Rota nova sem nível declarado = teste vermelho.

        É este teste que impede o padrão de voltar: não dá para pôr uma rota
        em produção sem alguém escrever, aqui, quem lá pode chegar.
        """
        c, mundo = cli
        import app as app_module
        rotas = {r.endpoint for r in app_module.app.url_map.iter_rules()
                 if r.endpoint != "static"}
        nao_classificadas = sorted(rotas - set(EXIGE))
        assert not nao_classificadas, (
            "Rotas sem nível declarado em EXIGE (tests/test_autorizacao.py): "
            + ", ".join(nao_classificadas))

    def test_nao_ha_lixo_na_tabela(self, cli):
        """O contrário: níveis declarados para rotas que já não existem."""
        c, mundo = cli
        import app as app_module
        rotas = {r.endpoint for r in app_module.app.url_map.iter_rules()}
        assert not sorted(set(EXIGE) - rotas)

    def test_niveis_sao_validos(self):
        assert not [e for e, n in EXIGE.items() if n not in NIVEIS]


# ══════════════════════════════════════════════════════════════
#  2. A matriz
# ══════════════════════════════════════════════════════════════

def _corpo(r):
    try:
        return r.data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _recusado(r):
    """Recusa aceitável: redirect para fora, código de erro, ou 200 vazio.

    O 200 vazio conta porque várias APIs falham em silêncio (devolvem `[]` em
    vez de 403). Feio, mas não é fuga — e o canário apanha se for.
    """
    if r.status_code in (301, 302, 303, 400, 401, 403, 404, 405, 429, 503):
        return True
    corpo = _corpo(r).strip()
    if corpo in ("", "[]", "{}"):
        return True
    # JSON só com zeros/vazios (ex.: {"h": ""} ou {"segundos": 0, ...}) é a
    # resposta-de-recusa das APIs que falham em silêncio: não diz nada.
    try:
        dados = json.loads(corpo)
    except (ValueError, TypeError):
        return False
    if isinstance(dados, dict):
        return not any(dados.values())
    if isinstance(dados, list):
        return not dados
    return False


class TestMatriz:
    """Cada rota, contra cada persona abaixo do nível exigido."""

    @pytest.mark.parametrize("quem", PERSONAS_ORDEM)
    def test_personas_abaixo_do_nivel_sao_recusadas(self, cli, quem):
        c, mundo = cli
        falhas = []
        for endpoint, url, metodo in _urls(mundo):
            exigido = EXIGE.get(endpoint)
            if exigido is None or exigido == "token":
                continue                      # token tem o seu próprio teste
            if PERSONA_NIVEL[quem] >= NIVEIS[exigido]:
                continue                      # tem nível, não é este o teste
            _persona(c, mundo, quem)
            r = c.get(url) if metodo == "GET" else c.post(url, data={})
            if not _recusado(r):
                falhas.append(f"{quem} entrou em {endpoint} ({metodo} {url}) "
                              f"→ {r.status_code}, exige {exigido}")
        assert not falhas, "\n".join(falhas)

    @pytest.mark.parametrize("quem", PERSONAS_ORDEM)
    def test_nenhuma_resposta_traz_dados_de_dentro(self, cli, quem):
        """A verificação que interessa: o código HTTP mente, o conteúdo não."""
        c, mundo = cli
        falhas = []
        for endpoint, url, metodo in _urls(mundo):
            exigido = EXIGE.get(endpoint)
            if exigido is None or exigido == "token":
                continue
            if PERSONA_NIVEL[quem] >= NIVEIS[exigido]:
                continue
            _persona(c, mundo, quem)
            r = c.get(url) if metodo == "GET" else c.post(url, data={})
            corpo = _corpo(r)
            for canario in CANARIOS:
                if canario in corpo:
                    falhas.append(f"{endpoint} ({metodo} {url}) mostrou '{canario}' a {quem}")
        assert not falhas, "\n".join(falhas)

    def test_nenhuma_pagina_publica_expoe_tokens(self, cli):
        """Nenhum token privado pode aparecer numa página sem sessão.

        Foi exactamente isto que falhou com o QR: o mesa_token ia no HTML de
        uma página que qualquer cliente abria.
        """
        c, mundo = cli
        A = mundo["A"]
        _persona(c, mundo, "anon")
        falhas = []
        for endpoint, url, metodo in _urls(mundo):
            if EXIGE.get(endpoint) not in ("publico", "token"):
                continue
            if metodo != "GET":
                continue
            corpo = _corpo(c.get(url))
            if A["mesa_token"] in corpo and endpoint not in ("mesa", "mesa_iniciar",
                                                             "mesa_terminar"):
                falhas.append(f"{endpoint} ({url}) expôs o mesa_token")
        assert not falhas, "\n".join(falhas)


# ══════════════════════════════════════════════════════════════
#  3. IDOR entre barbearias
# ══════════════════════════════════════════════════════════════

class TestIsolamentoEntreBarbearias:
    """O chefe da Beta não pode tocar em nada da Alfa.

    A matriz não apanha isto: o chefe da Beta TEM nível de chefe, portanto
    passa o guarda. O que tem de o parar é a verificação de barbearia_id
    dentro de cada rota.
    """

    def _chefe_beta(self, c, mundo):
        B = mundo["B"]
        with c.session_transaction() as s:
            s.clear()
            s["role"] = "chefe"; s["user_id"] = B["chefe_id"]
            s["barbearia_id"] = B["bid"]; s["user_nome"] = "Chefe Beta"

    def test_nao_le_agendamentos_da_outra(self, cli):
        c, mundo = cli
        self._chefe_beta(c, mundo)
        for url in ("/historico", "/", "/clientes", "/fila-espera"):
            corpo = _corpo(c.get(url))
            for canario in CANARIOS:
                assert canario not in corpo, f"{url} mostrou {canario} ao chefe da Beta"

    def test_nao_apaga_barbeiro_da_outra(self, cli):
        c, mundo = cli
        db, A = mundo["db"], mundo["A"]
        self._chefe_beta(c, mundo)
        c.post(f"/barbeiros/apagar/{A['barb_id']}")
        assert db.get_barbeiro(A["barb_id"]) is not None

    def test_nao_desativa_barbeiro_da_outra(self, cli):
        c, mundo = cli
        db, A = mundo["db"], mundo["A"]
        antes = db.get_barbeiro(A["barb_id"])["ativo"]
        self._chefe_beta(c, mundo)
        c.post(f"/barbeiros/toggle/{A['barb_id']}")
        assert db.get_barbeiro(A["barb_id"])["ativo"] == antes

    def test_nao_revoga_qr_da_outra(self, cli):
        c, mundo = cli
        db, A = mundo["db"], mundo["A"]
        antes = db.get_barbeiro(A["barb_id"])["mesa_token"]
        self._chefe_beta(c, mundo)
        c.post(f"/barbeiros/{A['barb_id']}/revogar-qr")
        assert db.get_barbeiro(A["barb_id"])["mesa_token"] == antes

    def test_nao_rouba_credenciais_da_outra(self, cli):
        c, mundo = cli
        db, A = mundo["db"], mundo["A"]
        antes = db.get_barbeiro(A["barb_id"]).get("password_hash")
        self._chefe_beta(c, mundo)
        c.post(f"/barbeiros/credenciais/{A['barb_id']}",
               data={"username": "roubado", "senha": "senha123"})
        assert db.get_barbeiro(A["barb_id"]).get("password_hash") == antes
        assert db.get_barbeiro_por_username("roubado") is None

    def test_nao_repoe_senha_da_outra(self, cli):
        c, mundo = cli
        db, A = mundo["db"], mundo["A"]
        antes = db.get_barbeiro(A["chefe_id"]).get("password_hash")
        self._chefe_beta(c, mundo)
        c.post(f"/barbeiros/repor-senha/{A['chefe_id']}", data={"senha": "invadido123"})
        assert db.get_barbeiro(A["chefe_id"]).get("password_hash") == antes

    def test_nao_apaga_servico_da_outra(self, cli):
        c, mundo = cli
        db, A = mundo["db"], mundo["A"]
        self._chefe_beta(c, mundo)
        c.post(f"/servicos/apagar/{A['svc_id']}")
        assert db.servico_por_id(A["svc_id"]) is not None

    def test_nao_cancela_agendamento_da_outra(self, cli):
        c, mundo = cli
        db, A = mundo["db"], mundo["A"]
        antes = db.get_agendamento(A["ag_id"])["status"]
        self._chefe_beta(c, mundo)
        c.post(f"/cancelar/{A['ag_id']}")
        assert db.get_agendamento(A["ag_id"])["status"] == antes

    def test_nao_termina_agendamento_da_outra(self, cli):
        c, mundo = cli
        db, A = mundo["db"], mundo["A"]
        antes = db.get_agendamento(A["ag_id"])["status"]
        self._chefe_beta(c, mundo)
        c.post(f"/terminar/{A['ag_id']}", data={"valor": "5000"})
        assert db.get_agendamento(A["ag_id"])["status"] == antes

    def test_nao_exporta_csv_da_outra(self, cli):
        c, mundo = cli
        self._chefe_beta(c, mundo)
        corpo = _corpo(c.get("/historico/exportar.csv"))
        for canario in CANARIOS:
            assert canario not in corpo

    def test_nao_ve_estatisticas_de_barbeiro_da_outra(self, cli):
        c, mundo = cli
        A = mundo["A"]
        self._chefe_beta(c, mundo)
        corpo = _corpo(c.get(f"/estatisticas/barbeiro/{A['barb_id']}"))
        assert "Barbeiro Alfa" not in corpo


# ══════════════════════════════════════════════════════════════
#  4. Confusão entre tokens
# ══════════════════════════════════════════════════════════════

class TestTokens:
    """Cada token abre só a sua porta.

    O incidente do QR foi precisamente uma confusão destas: o token que abria
    o painel era o mesmo que ia impresso no papel.
    """

    def test_qr_token_nao_abre_o_painel(self, cli):
        c, mundo = cli
        _persona(c, mundo, "anon")
        assert c.get(f"/mesa/{mundo['A']['qr_token']}").status_code == 404

    def test_qr_token_nao_inicia_nem_termina(self, cli):
        c, mundo = cli
        A = mundo["A"]
        _persona(c, mundo, "anon")
        for acao in ("iniciar", "terminar"):
            r = c.post(f"/mesa/{A['qr_token']}/{acao}", json={"id": A["ag_id"]})
            assert r.status_code in (400, 403, 404), f"{acao} aceitou o qr_token"

    def test_mesa_token_nao_serve_de_qr(self, cli):
        c, mundo = cli
        _persona(c, mundo, "anon")
        assert c.get(f"/q/{mundo['A']['mesa_token']}").status_code == 404

    def test_token_da_outra_barbearia_nao_serve(self, cli):
        c, mundo = cli
        A, B = mundo["A"], mundo["B"]
        _persona(c, mundo, "anon")
        corpo = _corpo(c.get(f"/mesa/{B['mesa_token']}"))
        for canario in CANARIOS:
            assert canario not in corpo, "painel da Beta mostrou dados da Alfa"

    def test_token_inventado_nao_abre_nada(self, cli):
        c, mundo = cli
        _persona(c, mundo, "anon")
        for url in ("/mesa/xpto", "/q/xpto", "/ag/xpto", "/avaliar-link/xpto",
                    "/cancelar-link/xpto", "/reagendar-link/xpto"):
            r = c.get(url)
            assert r.status_code in (302, 404), f"{url} → {r.status_code}"
            for canario in CANARIOS:
                assert canario not in _corpo(r)

    def test_painel_da_mesa_nao_e_indexado(self, cli):
        """O painel vive num URL sem login. Não pode ir parar a um motor de busca."""
        c, mundo = cli
        _persona(c, mundo, "anon")
        r = c.get(f"/mesa/{mundo['A']['mesa_token']}")
        assert r.status_code == 200
        cabecalho = (r.headers.get("X-Robots-Tag", "") + _corpo(r)).lower()
        assert "noindex" in cabecalho, "painel da mesa sem noindex"

    def test_urls_com_token_nao_ficam_em_cache(self, cli):
        """Tablet partilhado: o botão "voltar" não pode trazer o cliente anterior."""
        c, mundo = cli
        A = mundo["A"]
        _persona(c, mundo, "anon")
        for url in (f"/mesa/{A['mesa_token']}", f"/q/{A['qr_token']}",
                    f"/ag/{A['tok_avaliar']}"):
            r = c.get(url)
            assert "no-store" in r.headers.get("Cache-Control", ""), \
                f"{url} sem no-store"

    def test_robots_cobre_todos_os_caminhos_com_segredo(self, cli):
        """Cada prefixo com token declarado no código tem de estar no robots.txt.

        O `/q/` andou dias fora desta lista por ninguém se ter lembrado do
        ficheiro ao criar a rota. Assim a lista deixa de depender da memória.
        """
        c, mundo = cli
        import app as app_module
        corpo = _corpo(c.get("/robots.txt"))
        for prefixo in app_module._PATHS_COM_SEGREDO:
            assert f"Disallow: {prefixo}" in corpo, f"{prefixo} fora do robots.txt"
