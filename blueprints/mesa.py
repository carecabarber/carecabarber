from flask import render_template, request, redirect, url_for, session, jsonify
import database as db
from database import ST_AGENDADO, ST_EM_ANDAMENTO, ST_WALKIN
from helpers import (
    _log, _blog, _agora, _limpar, _api_ok, _invalidar_idx, _booking_lock,
    get_vocab, _MOEDA_MAP,
)


def register(app) -> None:

    @app.route("/mesa/<token>/entrar")
    @app.csrf.exempt
    def mesa_entrar(token):
        """Página pública para clientes — abre ao escanear o QR da mesa."""
        barb = db.get_barbeiro_por_mesa_token(token)
        if not barb or not barb.get("barbearia_id"):
            return render_template("404.html"), 404
        barbearia = db.get_barbearia(barb["barbearia_id"])
        if not barbearia or not barbearia["ativa"]:
            return render_template("404.html"), 404
        servicos = [s for s in db.listar_servicos(barb["barbearia_id"]) if s.get("ativo", 1)]
        _moeda_cod = db.get_config("moeda", barb["barbearia_id"], "ECV") or "ECV"
        return render_template("mesa_entrar.html",
                               barbeiro=barb, barbearia=barbearia,
                               servicos=servicos, mesa_token=token,
                               moeda_simbolo=_MOEDA_MAP.get(_moeda_cod, _moeda_cod),
                               vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))


    @app.route("/mesa/<token>")
    @app.csrf.exempt
    def mesa(token):
        barb = db.get_barbeiro_por_mesa_token(token)
        if not barb or not barb.get("barbearia_id"):
            return render_template("404.html"), 404
        barbearia = db.get_barbearia(barb["barbearia_id"])
        if not barbearia or not barbearia["ativa"]:
            return render_template("404.html"), 404
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
        if not ag_id:
            return jsonify({"ok": False, "error": "Agendamento não especificado"}), 400
        ag = db.get_agendamento(ag_id)
        if not ag or ag.get("barbeiro_id") != barb["id"] or ag.get("barbearia_id") != barb["barbearia_id"]:
            return jsonify({"ok": False, "error": "Agendamento não pertence a este barbeiro"}), 403
        if ag["status"] not in (ST_AGENDADO, ST_WALKIN):
            return jsonify({"ok": False, "error": "Já iniciado ou concluído"}), 400
        if db.barbeiro_tem_em_andamento(barb["id"]):
            return jsonify({"ok": False, "error": "Já tens um serviço em curso. Termina-o primeiro."}), 400
        try:
            ok = db.iniciar_trabalho(ag_id)
        except Exception:
            return jsonify({"ok": False, "error": "Erro interno. Tenta novamente."}), 500
        if not ok:
            return jsonify({"ok": False, "error": "Não foi possível iniciar. Verifica se já está em curso."}), 400
        _invalidar_idx(barb["barbearia_id"])
        return jsonify({"ok": True})


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
        if not ag_id:
            return jsonify({"ok": False, "error": "Agendamento não especificado"}), 400
        ag = db.get_agendamento(ag_id)
        if not ag or ag.get("barbeiro_id") != barb["id"] or ag.get("barbearia_id") != barb["barbearia_id"]:
            return jsonify({"ok": False, "error": "Agendamento não pertence a este barbeiro"}), 403
        if ag["status"] != ST_EM_ANDAMENTO:
            return jsonify({"ok": False, "error": "Serviço não está em andamento"}), 400
        try:
            valor = max(0, min(int(data.get("valor", 0)), 999_999))
        except (TypeError, ValueError):
            valor = 0
        # Protecção anti-fraude: valor=0 usa preço configurado no serviço
        if valor == 0:
            _srv = db.servico_por_id(ag.get("servico_id"))
            if _srv and _srv.get("preco"):
                valor = _srv["preco"]
        db.terminar_trabalho(ag_id, valor)
        _invalidar_idx(barb["barbearia_id"])
        return jsonify({"ok": True})


    @app.route("/mesa/<token>/info", methods=["GET"])
    @app.csrf.exempt
    def mesa_info(token):
        """API pública — devolve info do barbeiro + serviços para walk-in do cliente."""
        barb = db.get_barbeiro_por_mesa_token(token)
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


    @app.route("/mesa/<token>/walkin", methods=["POST"])
    @app.csrf.exempt
    def mesa_walkin_post(token):
        if not _api_ok(request.remote_addr or "?"):
            return jsonify({"ok": False, "error": "Demasiados pedidos. Aguarda."}), 429
        barb = db.get_barbeiro_por_mesa_token(token)
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
            if db.barbeiro_tem_em_andamento(barb["id"]):
                return jsonify({"ok": False,
                                "error": "Já tens um serviço em curso. Termina-o primeiro."}), 400
            duracao_servico = s.get("duracao_min") or 30
            buffer = int(db.get_config("buffer_minutos", barb["barbearia_id"]) or 10)
            minutos_ate_proxima = db.barbeiro_proxima_marcacao_minutos(barb["id"], barb["barbearia_id"])
            if minutos_ate_proxima < (duracao_servico + buffer):
                return jsonify({"ok": False,
                                "error": f"Não há tempo suficiente — próxima marcação em {minutos_ate_proxima} min "
                                         f"e este serviço demora ~{duracao_servico} min."}), 400
            try:
                valor = max(0, min(int(data.get("valor", 0)), 999_999))
            except (TypeError, ValueError):
                valor = 0
            agora_str = _agora_wi.strftime("%Y-%m-%d %H:%M:%S")
            novo_id = db.criar_agendamento(nome, sid, agora_str,
                                           barb["barbearia_id"], barb["id"], ST_WALKIN, valor)
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
                        "token_cliente": token_cliente})


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
            if not _api_ok(request.remote_addr or "?"):
                return render_template("erro_simples.html",
                                       msg="Demasiados pedidos. Aguarda um momento."), 429
            acao = request.form.get("acao")
            if acao == "iniciar" and ag["status"] in (ST_AGENDADO, ST_WALKIN):
                if not ag.get("barbeiro_id"):
                    erro = f"Marcação sem {_prof.lower()} atribuído."
                elif db.barbeiro_tem_em_andamento(ag["barbeiro_id"]):
                    erro = f"O {_prof.lower()} já tem um {_servc.lower()} em curso. Aguarda um momento."
                else:
                    if db.iniciar_trabalho(ag["id"]):
                        _invalidar_idx(ag["barbearia_id"])
                        return redirect(url_for("ag_acao_cliente", token=token))
                    else:
                        erro = "Não foi possível iniciar — serviço já em curso."
            elif acao == "terminar" and ag["status"] == ST_EM_ANDAMENTO:
                # Terminação via token público bloqueada — só staff autenticado pode terminar
                erro = "Acção não permitida por este link."
            ag = db.get_agendamento_por_token_avaliar(token) or ag

        return render_template("ag_acao.html",
                               ag=ag, barbearia=barbearia,
                               barbeiro=barbeiro, servico=servico,
                               erro=erro, token=token,
                               moeda_simbolo=_moeda_sim,
                               vocab=_vocab)
