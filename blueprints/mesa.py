from flask import render_template, request, redirect, url_for, session, jsonify, flash
import database as db
from database import ST_AGENDADO, ST_EM_ANDAMENTO, ST_WALKIN
from helpers import (
    _log, _blog, _agora, _limpar, _api_ok, _invalidar_idx, _booking_lock,
    get_vocab, _MOEDA_MAP,
    _pc_get, _pc_set, _CLEANUP_LOCK_TTL,
)


def _acao_iniciar(barb: dict, ag_id: int):
    """Valida e inicia um serviço em nome do barbeiro `barb`.
    Devolve (payload_json, status_http). Partilhado pela via QR e pela via
    código manual — a validação tem de ser exactamente a mesma nas duas."""
    if not ag_id:
        return {"ok": False, "error": "Agendamento não especificado"}, 400
    ag = db.get_agendamento(ag_id)
    if not ag or ag.get("barbeiro_id") != barb["id"] or ag.get("barbearia_id") != barb["barbearia_id"]:
        return {"ok": False, "error": "Agendamento não pertence a este barbeiro"}, 403
    if ag["status"] not in (ST_AGENDADO, ST_WALKIN):
        return {"ok": False, "error": "Já iniciado ou concluído"}, 400
    ag_preso = db.get_servico_em_andamento(barb["id"])
    if ag_preso:
        return {"ok": False,
                "error": "Já tens um serviço em curso. Termina-o primeiro.",
                "ag_em_curso_id": ag_preso["id"]}, 400
    try:
        ok = db.iniciar_trabalho(ag_id)
    except Exception:
        return {"ok": False, "error": "Erro interno. Tenta novamente."}, 500
    if not ok:
        return {"ok": False, "error": "Não foi possível iniciar. Verifica se já está em curso."}, 400
    _invalidar_idx(barb["barbearia_id"])
    return {"ok": True}, 200


def _acao_terminar(barb: dict, ag_id: int, valor_pedido=0):
    """Valida e termina um serviço. Devolve (payload_json, status_http)."""
    if not ag_id:
        return {"ok": False, "error": "Agendamento não especificado"}, 400
    ag = db.get_agendamento(ag_id)
    if not ag or ag.get("barbeiro_id") != barb["id"] or ag.get("barbearia_id") != barb["barbearia_id"]:
        return {"ok": False, "error": "Agendamento não pertence a este barbeiro"}, 403
    if ag["status"] != ST_EM_ANDAMENTO:
        return {"ok": False, "error": "Serviço não está em andamento"}, 400
    try:
        valor = max(0, min(int(valor_pedido or 0), 999_999))
    except (TypeError, ValueError):
        valor = 0
    # Protecção anti-fraude: valor=0 usa preço configurado no serviço
    if valor == 0:
        _srv = db.servico_por_id(ag.get("servico_id"))
        if _srv and _srv.get("preco"):
            valor = _srv["preco"]
    db.terminar_trabalho(ag_id, valor)
    _invalidar_idx(barb["barbearia_id"])
    return {"ok": True, "valor": valor}, 200


def register(app) -> None:

    @app.route("/q/<token>")
    @app.csrf.exempt
    def mesa_entrar(token):
        """Página pública do cliente — abre ao escanear o QR da mesa.

        Usa o qr_token (público). O mesa_token nunca aparece aqui: quem lê o QR
        não pode chegar ao painel do profissional.
        """
        barb = db.get_barbeiro_por_qr_token(token)
        if not barb or not barb.get("barbearia_id"):
            return render_template("404.html"), 404
        barbearia = db.get_barbearia(barb["barbearia_id"])
        if not barbearia or not barbearia["ativa"]:
            return render_template("404.html"), 404
        servicos = [s for s in db.listar_servicos(barb["barbearia_id"]) if s.get("ativo", 1)]
        _moeda_cod = db.get_config("moeda", barb["barbearia_id"], "ECV") or "ECV"
        return render_template("mesa_entrar.html",
                               barbeiro=barb, barbearia=barbearia,
                               servicos=servicos, qr_token=token,
                               moeda_simbolo=_MOEDA_MAP.get(_moeda_cod, _moeda_cod),
                               vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))


    @app.route("/mesa/<token>/entrar")
    @app.csrf.exempt
    def mesa_entrar_legado(token):
        """QRs antigos apontam para aqui (traziam o mesa_token). Redirecciona
        para o endereço público novo.

        Aceita duas coisas: o mesa_token ainda em uso (quem não revogou) e o
        mesa_token já revogado (quem revogou, mas ainda tem o papel velho na
        mesa). Em qualquer dos casos só devolve o qr_token — daqui não se chega
        ao painel. Depois de revogar, o papel antigo passa a valer só para isto.
        """
        barb = (db.get_barbeiro_por_mesa_token(token)
                or db.get_barbeiro_por_mesa_token_revogado(token))
        if not barb or not barb.get("qr_token"):
            return render_template("404.html"), 404
        return redirect(url_for("mesa_entrar", token=barb["qr_token"]))


    @app.route("/mesa/<token>")
    @app.csrf.exempt
    def mesa(token):
        barb = db.get_barbeiro_por_mesa_token(token)
        if not barb or not barb.get("barbearia_id"):
            return render_template("404.html"), 404
        barbearia = db.get_barbearia(barb["barbearia_id"])
        if not barbearia or not barbearia["ativa"]:
            return render_template("404.html"), 404
        # Limpar serviços presos (throttled 5 min) — resolve bloqueios cross-day
        _lck = f"limpeza_mesa:{barb['barbearia_id']}"
        if _pc_get(_lck) is None:
            db.limpar_em_andamento_presos(barb["barbearia_id"])
            _pc_set(_lck, 1, _CLEANUP_LOCK_TTL)
        hoje = _agora(barb["barbearia_id"]).strftime("%Y-%m-%d")
        ags  = db.get_agendamentos_mesa(barb["id"], barb["barbearia_id"], hoje)
        servicos = db.listar_servicos(barb["barbearia_id"])
        _mc  = db.get_config("moeda", barb["barbearia_id"], "ECV") or "ECV"
        return render_template("mesa.html",
                               barbeiro=barb, barbearia=barbearia,
                               agendamentos=ags, servicos=servicos,
                               hoje=hoje, mesa_token=token,
                               moeda_simbolo=_MOEDA_MAP.get(_mc, _mc),
                               vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))


    @app.route("/mesa/<token>/iniciar", methods=["POST"])
    @app.csrf.exempt
    def mesa_iniciar(token):
        if not _api_ok(request.remote_addr or "?"):
            return jsonify({"ok": False, "error": "Demasiados pedidos. Aguarda."}), 429
        barb = db.get_barbeiro_por_mesa_token(token)
        if not barb:
            return jsonify({"ok": False, "error": "Token inválido"}), 403
        data  = request.get_json(silent=True) or {}
        try:
            ag_id = int(data.get("ag_id", 0))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Agendamento inválido"}), 400
        payload, http = _acao_iniciar(barb, ag_id)
        return jsonify(payload), http


    @app.route("/mesa/<token>/terminar", methods=["POST"])
    @app.csrf.exempt
    def mesa_terminar(token):
        if not _api_ok(request.remote_addr or "?"):
            return jsonify({"ok": False, "error": "Demasiados pedidos. Aguarda."}), 429
        barb = db.get_barbeiro_por_mesa_token(token)
        if not barb:
            return jsonify({"ok": False, "error": "Token inválido"}), 403
        data  = request.get_json(silent=True) or {}
        try:
            ag_id = int(data.get("ag_id", 0))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Agendamento inválido"}), 400
        payload, http = _acao_terminar(barb, ag_id, data.get("valor", 0))
        return jsonify(payload), http


    def _barb_por_qualquer_token(token):
        """Aceita o qr_token (cliente) ou o mesa_token (profissional).

        Devolve (barbeiro, publico) — `publico=True` quando veio pelo QR, para
        as rotas saberem que não podem confiar em valores enviados pelo cliente.
        """
        barb = db.get_barbeiro_por_qr_token(token)
        if barb:
            return barb, True
        return db.get_barbeiro_por_mesa_token(token), False


    @app.route("/q/<token>/info", methods=["GET"])
    @app.route("/mesa/<token>/info", methods=["GET"])
    @app.csrf.exempt
    def mesa_info(token):
        """API pública — devolve info do barbeiro + serviços para walk-in do cliente."""
        barb, _pub = _barb_por_qualquer_token(token)
        if not barb or not barb.get("barbearia_id"):
            return jsonify({"ok": False, "error": "Token inválido"}), 403
        barbearia = db.get_barbearia(barb["barbearia_id"])
        if not barbearia or not barbearia["ativa"]:
            return jsonify({"ok": False, "error": "Barbearia inativa"}), 403
        servicos = db.listar_servicos(barb["barbearia_id"])
        return jsonify({
            "ok": True,
            "barbeiro": barb["nome"],
            "barbearia": barbearia["nome"],
            "servicos": [{"id": s["id"], "nome": s["nome"],
                          "duracao": s.get("duracao_min", 30),
                          "preco": s.get("preco", 0)} for s in servicos if s.get("ativo", 1)]
        })


    @app.route("/q/<token>/walkin", methods=["POST"])
    @app.route("/mesa/<token>/walkin", methods=["POST"])
    @app.csrf.exempt
    def mesa_walkin_post(token):
        if not _api_ok(request.remote_addr or "?"):
            return jsonify({"ok": False, "error": "Demasiados pedidos. Aguarda."}), 429
        barb, _publico = _barb_por_qualquer_token(token)
        if not barb or not barb.get("barbearia_id"):
            return jsonify({"ok": False, "error": "Token inválido"}), 403
        if not barb.get("ativo", 1):
            return jsonify({"ok": False, "error": "Barbeiro inactivo"}), 403
        barbearia = db.get_barbearia(barb["barbearia_id"])
        if not barbearia or not barbearia["ativa"]:
            return jsonify({"ok": False, "error": "Barbearia inativa"}), 403
        data = request.get_json(silent=True) or {}
        nome = _limpar(str(data.get("nome", ""))).strip()
        try:
            sid = int(data.get("servico_id", 0))
        except (TypeError, ValueError):
            sid = 0
        if not nome or len(nome) < 2:
            return jsonify({"ok": False, "error": "Nome do cliente obrigatório (mín. 2 letras)"}), 400
        if not sid:
            return jsonify({"ok": False, "error": "Escolhe um serviço"}), 400
        s = db.servico_por_id(sid)
        if not s or s.get("barbearia_id") != barb["barbearia_id"]:
            return jsonify({"ok": False, "error": "Serviço inválido"}), 400
        _agora_wi = _agora(barb["barbearia_id"])
        with _booking_lock:
            # ausencia_ativa DENTRO do lock — evita TOCTOU (regra #17)
            _bloq = db.ausencia_ativa(barb["id"],
                                      _agora_wi.strftime("%Y-%m-%d"),
                                      _agora_wi.strftime("%H:%M"))
            if _bloq:
                return jsonify({"ok": False,
                                "error": f"Barbeiro em pausa até {_bloq.get('hora_fim','?')}"}), 400
            # Só bloqueia quem arranca o serviço já a seguir (o profissional).
            # Pelo QR o cliente fica em fila, e é justamente quando o
            # profissional está ocupado que ele precisa de se registar.
            if not _publico:
                _ag_preso = db.get_servico_em_andamento(barb["id"])
                if _ag_preso:
                    return jsonify({"ok": False,
                                    "error": "Já tens um serviço em curso. Termina-o primeiro.",
                                    "ag_em_curso_id": _ag_preso["id"]}), 400
            duracao_servico = s.get("duracao_min") or 30
            buffer = int(db.get_config("buffer_minutos", barb["barbearia_id"]) or 10)
            minutos_ate_proxima = db.barbeiro_proxima_marcacao_minutos(barb["id"], barb["barbearia_id"])
            if minutos_ate_proxima < (duracao_servico + buffer):
                return jsonify({"ok": False,
                                "error": f"Não há tempo suficiente — próxima marcação em {minutos_ate_proxima} min "
                                         f"e este serviço demora ~{duracao_servico} min."}), 400
            if _publico:
                # Veio pelo QR da mesa: o valor não é do cliente. Usa a tabela.
                valor = 0
            else:
                try:
                    valor = max(0, min(int(data.get("valor", 0)), 999_999))
                except (TypeError, ValueError):
                    valor = 0
            agora_str = _agora_wi.strftime("%Y-%m-%d %H:%M:%S")
            novo_id = db.criar_agendamento(nome, sid, agora_str,
                                           barb["barbearia_id"], barb["id"], ST_WALKIN, valor,
                                           status=ST_WALKIN if _publico else None)
            # Arrancar o serviço lança o cronómetro e o valor na facturação —
            # é do profissional. Pelo QR fica em fila, à espera que ele inicie.
            if not _publico:
                ok = db.iniciar_trabalho(novo_id)
                if not ok:
                    db.deletar_walkin_orfao(novo_id)
                    return jsonify({"ok": False,
                                    "error": "Já tens um serviço em curso. Termina-o primeiro."}), 400
        _invalidar_idx(barb["barbearia_id"])
        ag_novo = db.get_agendamento(novo_id)
        token_cliente = ag_novo.get("token_avaliar") if ag_novo else None
        return jsonify({"ok": True, "ag_id": novo_id, "cliente": nome,
                        "servico_nome": s["nome"], "valor": valor,
                        "iniciado": not _publico,
                        # preço de tabela → pré-preenche o campo ao terminar
                        "preco": s.get("preco") or 0,
                        "token_cliente": token_cliente})


    @app.route("/cliente/<slug>/servico/<int:ag_id>/<acao>", methods=["POST"])
    def cliente_acao_servico(slug, ag_id, acao):
        """Descontinuada — iniciar/terminar é trabalho do staff.

        Mantida só para não partir links/marcadores antigos: avisa e devolve
        o cliente à sua página. O cliente marca, reagenda e cancela; quem
        arranca e fecha o serviço (e portanto lança o valor na facturação)
        é o profissional, no painel ou no ecrã da mesa.
        """
        barbearia = db.get_barbearia_por_slug(slug)
        if not barbearia or not barbearia["ativa"]:
            return redirect(url_for("login"))
        if (session.get("role") != "cliente"
                or session.get("barbearia_id") != barbearia["id"]):
            return redirect(url_for("cliente_entrada", slug=slug))
        _prof = get_vocab(barbearia.get("tipo"),
                          barbearia.get("vocab_custom"))["profissional"].lower()
        flash(f"Só o {_prof} pode iniciar ou terminar o atendimento.", "aviso")
        return redirect(url_for("cliente_home", slug=slug))


    @app.route("/ag/<token>", methods=["GET", "POST"])
    @app.csrf.exempt
    def ag_acao_cliente(token):
        ag = db.get_agendamento_por_token_avaliar(token)
        if not ag:
            return render_template("erro_simples.html",
                                   msg="Marcação não encontrada ou expirada."), 404
        barbearia = db.get_barbearia(ag["barbearia_id"])
        barbeiro  = db.get_barbeiro(ag["barbeiro_id"]) if ag.get("barbeiro_id") else None
        servico   = db.servico_por_id(ag["servico_id"]) if ag.get("servico_id") else None

        _vocab    = get_vocab(barbearia.get("tipo") if barbearia else None,
                             barbearia.get("vocab_custom") if barbearia else None)
        _prof     = _vocab.get("profissional", "Barbeiro")
        _servc    = _vocab.get("servico", "serviço")
        _moeda_cod = db.get_config("moeda", ag["barbearia_id"], "ECV") or "ECV"
        _moeda_sim = _MOEDA_MAP.get(_moeda_cod, _moeda_cod)

        erro = None
        if request.method == "POST":
            # Iniciar/terminar é trabalho do staff — este link é só de consulta.
            # Evita que o cliente arranque o cronómetro ou feche o serviço
            # (o valor do serviço entra na facturação).
            erro = f"Só o {_prof.lower()} pode iniciar ou terminar o {_servc.lower()}."

        return render_template("ag_acao.html",
                               ag=ag, barbearia=barbearia,
                               barbeiro=barbeiro, servico=servico,
                               erro=erro, token=token,
                               moeda_simbolo=_moeda_sim,
                               vocab=_vocab)
