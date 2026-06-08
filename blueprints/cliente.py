from flask import render_template, request, redirect, url_for, session, flash
import database as db
from database import ST_AGENDADO, ST_EM_ANDAMENTO, ST_WALKIN
from helpers import (
    _log, _blog, _agora, _limpar, _val_data, _val_hora, _no_passado, _dentro_horario,
    _invalidar_idx, _api_ok, _booking_lock,
    get_vocab, _MOEDA_MAP, _TEL_RE, _MAX_TEL, _MAX_MOTIVO,
    _normalizar_tel, enriquecer_lista, enriquecer,
)


def register(app):

    @app.route("/cliente/<slug>", methods=["GET","POST"])
    def cliente_entrada(slug):
        barbearia = db.get_barbearia_por_slug(slug)
        if not barbearia or not barbearia["ativa"]:
            return render_template("404.html"), 404
        barbearia_id = barbearia["id"]
        erro = None
        if request.method == "POST":
            nome = _limpar(request.form.get("nome",""))
            tel  = _limpar(request.form.get("telefone",""), _MAX_TEL)

            if not nome or not tel:
                erro = "Preenche o teu nome e telemóvel."
            elif len(nome) < 2:
                erro = "Nome demasiado curto."
            elif not _TEL_RE.match(tel):
                erro = "Número de telemóvel inválido."
            else:
                session.clear()
                session.permanent = True
                session.update({
                    "user_nome":    nome,
                    "role":         "cliente",
                    "telefone":     _normalizar_tel(tel) or tel,
                    "barbearia_id": barbearia_id,
                })
                return redirect(url_for("cliente_home", slug=slug))
        _mc = db.get_config("moeda", barbearia["id"], "ECV") or "ECV"
        return render_template("cliente_entrada.html", erro=erro, barbearia=barbearia,
                               moeda_simbolo=_MOEDA_MAP.get(_mc, _mc),
                               vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))


    @app.route("/cliente/<slug>/area")
    def cliente_home(slug):
        barbearia = db.get_barbearia_por_slug(slug)
        if not barbearia or not barbearia["ativa"]:
            return render_template("404.html"), 404
        barbearia_id = barbearia["id"]
        if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia_id:
            return redirect(url_for("cliente_entrada", slug=slug))
        _proximas_statuses = {"agendado", "em_andamento"}
        _raw = enriquecer_lista(db.listar_por_telefone(session.get("telefone",""), barbearia_id))
        _proximas = [a for a in _raw if a.get("status") in _proximas_statuses]
        _outras   = sorted([a for a in _raw if a.get("status") not in _proximas_statuses],
                           key=lambda a: a.get("data_hora", ""), reverse=True)
        agendamentos = _proximas + _outras
        return render_template("cliente_home.html", agendamentos=agendamentos, barbearia=barbearia,
                               vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))


    @app.route("/cliente/<slug>/marcar", methods=["GET","POST"])
    def cliente_marcar(slug):
        barbearia = db.get_barbearia_por_slug(slug)
        if not barbearia or not barbearia["ativa"]:
            return render_template("404.html"), 404
        barbearia_id = barbearia["id"]
        if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia_id:
            return redirect(url_for("cliente_entrada", slug=slug))
        servicos  = db.listar_servicos(barbearia_id)
        barbeiros = db.listar_barbeiros(barbearia_id, incluir_chefe=True)
        erro = None
        if request.method == "POST":
            if not _api_ok(request.remote_addr or "?"):
                erro = "Demasiados pedidos. Aguarda um momento."
                return render_template("cliente_marcar.html", barbearia=barbearia, servicos=servicos,
                                       barbeiros=barbeiros, erro=erro,
                                       vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom"))), 429
            try:
                sid = int(request.form.get("servico_id", 0))
            except (ValueError, TypeError):
                sid = 0
            try:
                _bid_raw = request.form.get("barbeiro_id") or None
                bid_ = int(_bid_raw) if _bid_raw else None
            except (ValueError, TypeError):
                bid_ = None
            data  = _limpar(request.form.get("data",""), 10)
            hora  = _limpar(request.form.get("hora",""), 5)

            if not sid or not data or not hora:
                erro = "Preenche todos os campos obrigatórios."
            elif not _val_data(data) or not _val_hora(hora):
                erro = "Data ou hora inválida."
            elif _no_passado(data, hora):
                erro = "Não podes agendar no passado."
            else:
                dh = f"{data} {hora}:00"
                s  = db.servico_por_id(sid)
                if not s or s.get("barbearia_id") != barbearia_id:
                    erro = "Serviço inválido."
                elif bid_:
                    _bv = db.get_barbeiro(bid_)
                    if not _bv or _bv.get("barbearia_id") != barbearia_id:
                        erro = f"{get_vocab(barbearia.get('tipo'), barbearia.get('vocab_custom')).get('profissional', 'Profissional')} inválido."
                if not erro:
                    ok_h, msg_h = _dentro_horario(data, hora, s["duracao_min"], barbearia_id)
                    if not ok_h:
                        erro = msg_h
                if not erro and bid_:
                    aus = db.ausencia_ativa(bid_, data, hora)
                    if aus:
                        erro = f"{aus['barbeiro_nome']} está indisponível. Escolhe outro barbeiro ou data."
                    else:
                        livre, conflito = db.verificar_disponibilidade(bid_, dh, s["duracao_min"], barbearia_id)
                        if not livre:
                            erro = f"Já existe marcação às {conflito['data_hora'][11:16]}. Escolhe outro horário."
                if not erro:
                    with _booking_lock:
                        if bid_:
                            livre, conflito = db.verificar_disponibilidade(bid_, dh, s["duracao_min"], barbearia_id)
                            if not livre:
                                erro = f"Já existe marcação às {conflito['data_hora'][11:16]}. Escolhe outro horário."
                        else:
                            max_dia   = int(db.get_config("max_por_dia", barbearia_id, 20))
                            ativos    = db.contar_ativos_dia(barbearia_id, data)
                            if ativos >= max_dia:
                                erro = "Não há vagas disponíveis para este dia. Escolhe outra data."
                        if not erro:
                            novo_id = db.criar_agendamento(
                                session.get("user_nome",""), sid, dh, barbearia_id,
                                bid_, "agendado", 0, session.get("telefone"))
                            _invalidar_idx(barbearia_id)
                            _s = db.servico_por_id(sid)
                            from helpers import _push_async
                            _push_async(barbearia_id,
                                        "📅 Novo agendamento",
                                        f"{session.get('user_nome','Cliente')} marcou {_s['nome'] if _s else ''} para {dh[8:10]}/{dh[5:7]} {dh[11:16]}",
                                        barbeiro_id=bid_)
                            return redirect(url_for("cliente_confirmacao", slug=slug, id=novo_id))
        hoje = _agora().strftime("%Y-%m-%d")
        return render_template("cliente_marcar.html", servicos=servicos, barbeiros=barbeiros,
                               hoje=hoje, agora=_agora().strftime("%H:%M"),
                               erro=erro, barbearia=barbearia,
                               vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))


    @app.route("/cliente/<slug>/confirmacao/<int:id>")
    def cliente_confirmacao(slug, id):
        barbearia = db.get_barbearia_por_slug(slug)
        if not barbearia or not barbearia["ativa"]:
            return render_template("404.html"), 404
        barbearia_id = barbearia["id"]
        if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia_id:
            return redirect(url_for("cliente_entrada", slug=slug))
        ag = db.get_agendamento(id)
        if not ag or ag["telefone"] != session.get("telefone") or ag["barbearia_id"] != barbearia_id:
            return redirect(url_for("cliente_home", slug=slug))
        s = db.servico_por_id(ag["servico_id"])
        b = db.get_barbeiro(ag["barbeiro_id"])
        return render_template("cliente_confirmacao.html", ag=ag, servico=s,
                               barbeiro=b, barbearia=barbearia,
                               vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))


    @app.route("/cliente/<slug>/cancelar/<int:id>", methods=["POST"])
    def cliente_cancelar(slug, id):
        barbearia = db.get_barbearia_por_slug(slug)
        if not barbearia or not barbearia["ativa"]:
            return redirect(url_for("login"))
        barbearia_id = barbearia["id"]
        if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia_id:
            return redirect(url_for("cliente_entrada", slug=slug))
        if not _api_ok(request.remote_addr or "?"):
            return redirect(url_for("cliente_home", slug=slug))
        ag = db.get_agendamento(id)
        if (ag and ag["telefone"] == session.get("telefone")
                and ag["barbearia_id"] == barbearia_id
                and ag["status"] == ST_AGENDADO):
            db.cancelar_agendamento(id)
            _invalidar_idx(barbearia_id)
        return redirect(url_for("cliente_home", slug=slug))


    @app.route("/cliente/<slug>/reagendar/<int:id>", methods=["GET","POST"])
    def cliente_reagendar(slug, id):
        barbearia = db.get_barbearia_por_slug(slug)
        if not barbearia or not barbearia["ativa"]:
            return render_template("404.html"), 404
        barbearia_id = barbearia["id"]
        if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia_id:
            return redirect(url_for("cliente_entrada", slug=slug))
        ag = db.get_agendamento(id)
        if (not ag or ag["telefone"] != session.get("telefone")
                or ag["barbearia_id"] != barbearia_id
                or ag["status"] != ST_AGENDADO):
            return redirect(url_for("cliente_home", slug=slug))
        erro = None
        if request.method == "POST":
            if not _api_ok(request.remote_addr or "?"):
                return redirect(url_for("cliente_home", slug=slug))
            try:
                sid = int(request.form.get("servico_id") or ag["servico_id"])
            except (ValueError, TypeError):
                sid = ag["servico_id"]
            try:
                _bid_raw = request.form.get("barbeiro_id") or None
                bid_ = int(_bid_raw) if _bid_raw else ag.get("barbeiro_id")
            except (ValueError, TypeError):
                bid_ = ag.get("barbeiro_id")
            data  = _limpar(request.form.get("data",""), 10)
            hora  = _limpar(request.form.get("hora",""), 5)
            if not data or not hora:
                erro = "Preenche a data e hora."
            elif not _val_data(data) or not _val_hora(hora):
                erro = "Data ou hora inválida."
            elif _no_passado(data, hora):
                erro = "Não podes reagendar para uma data no passado."
            dur = 0
            if not erro:
                dh  = f"{data} {hora}:00"
                s   = db.servico_por_id(sid)
                if not s or s.get("barbearia_id") != barbearia_id:
                    erro = "Serviço inválido."
                else:
                    dur = s["duracao_min"]
            if not erro and bid_:
                _barb_v = db.get_barbeiro(bid_)
                if not _barb_v or _barb_v.get("barbearia_id") != barbearia_id:
                    erro = f"{get_vocab(barbearia.get('tipo'), barbearia.get('vocab_custom')).get('profissional', 'Profissional')} inválido."
            if not erro:
                ok_h, msg_h = _dentro_horario(data, hora, dur, barbearia_id)
                if not ok_h:
                    erro = msg_h
            if not erro:
                if bid_:
                    aus = db.ausencia_ativa(bid_, data, hora)
                    if aus:
                        erro = f"{aus['barbeiro_nome']} está indisponível. Escolhe outro barbeiro ou data."
                if not erro:
                    with _booking_lock:
                        livre, conflito = db.verificar_disponibilidade(bid_, dh, dur, barbearia_id, excluir_id=id)
                        if not livre:
                            erro = f"Conflito às {conflito['data_hora'][11:16]}. Escolhe outro horário."
                        if not erro:
                            db.reagendar_agendamento(id, dh, bid_, sid)
                            db.invalidar_cache_slots(barbearia_id)
                            _invalidar_idx(barbearia_id)
                            return redirect(url_for("cliente_home", slug=slug))
        hoje = _agora().strftime("%Y-%m-%d")
        return render_template("reagendar.html", ag=enriquecer(ag),
                               servicos=db.listar_servicos(barbearia_id),
                               barbeiros=db.listar_barbeiros(barbearia_id, incluir_chefe=True),
                               hoje=hoje, erro=erro, origem="cliente", barbearia=barbearia,
                               vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))


    @app.route("/cliente/<slug>/iniciar-servico/<int:id>", methods=["POST"])
    def cliente_iniciar_servico(slug, id):
        """Cliente inicia o seu serviço directamente."""
        barbearia = db.get_barbearia_por_slug(slug)
        if not barbearia or not barbearia["ativa"]:
            return redirect(url_for("login"))
        barbearia_id = barbearia["id"]
        if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia_id:
            return redirect(url_for("cliente_entrada", slug=slug))
        ag = db.get_agendamento(id)
        if (not ag or ag.get("telefone") != session.get("telefone")
                or ag.get("barbearia_id") != barbearia_id
                or ag.get("status") not in (ST_AGENDADO, ST_WALKIN)):
            return redirect(url_for("cliente_home", slug=slug))
        if not db.iniciar_trabalho(id):
            _vcli = get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom"))
            flash(f"⚠️ O {_vcli.get('profissional','Barbeiro').lower()} já tem um {_vcli.get('servico','serviço').lower()} em curso. Aguarda.", "erro")
        else:
            _invalidar_idx(barbearia_id)
        return redirect(url_for("cliente_home", slug=slug))


    @app.route("/cliente/<slug>/terminar-servico/<int:id>", methods=["POST"])
    def cliente_terminar_servico(slug, id):
        """Cliente termina o seu serviço directamente."""
        barbearia = db.get_barbearia_por_slug(slug)
        if not barbearia or not barbearia["ativa"]:
            return redirect(url_for("login"))
        barbearia_id = barbearia["id"]
        if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia_id:
            return redirect(url_for("cliente_entrada", slug=slug))
        ag = db.get_agendamento(id)
        if (not ag or ag.get("telefone") != session.get("telefone")
                or ag.get("barbearia_id") != barbearia_id
                or ag.get("status") != "em_andamento"):
            return redirect(url_for("cliente_home", slug=slug))
        try:
            valor = max(0, min(int(request.form.get("valor", 0) or 0), 999_999))
        except (ValueError, TypeError):
            valor = 0
        db.terminar_trabalho(id, valor)
        _invalidar_idx(barbearia_id)
        return redirect(url_for("cliente_home", slug=slug))


    @app.route("/reagendar-link/<token>", methods=["GET","POST"])
    def reagendar_link(token):
        """Reagendamento público — o cliente acede via link com token único."""
        ag = db.get_agendamento_por_token(token)
        if not ag:
            return render_template("404.html"), 404

        barbearia_id = ag["barbearia_id"]
        barbearia    = db.get_barbearia(barbearia_id)
        if not barbearia or not barbearia["ativa"]:
            return render_template("404.html"), 404

        erro = None
        if request.method == "POST":
            try:
                sid = int(request.form.get("servico_id") or ag["servico_id"])
            except (ValueError, TypeError):
                sid = ag["servico_id"]
            try:
                _bid_raw = request.form.get("barbeiro_id") or None
                bid_     = int(_bid_raw) if _bid_raw else ag.get("barbeiro_id")
            except (ValueError, TypeError):
                bid_ = ag.get("barbeiro_id")
            data = _limpar(request.form.get("data", ""), 10)
            hora = _limpar(request.form.get("hora", ""), 5)
            if not data or not hora:
                erro = "Preenche a data e hora."
            elif not _val_data(data) or not _val_hora(hora):
                erro = "Data ou hora inválida."
            elif _no_passado(data, hora, barbearia_id):
                erro = "Não podes reagendar para uma data no passado."
            dur = 0
            if not erro:
                dh = f"{data} {hora}:00"
                s  = db.servico_por_id(sid)
                if not s or s.get("barbearia_id") != barbearia_id:
                    erro = "Serviço inválido."
                else:
                    dur = s["duracao_min"]
            if not erro and bid_:
                _barb_v = db.get_barbeiro(bid_)
                if not _barb_v or _barb_v.get("barbearia_id") != barbearia_id:
                    erro = f"{get_vocab(barbearia.get('tipo'), barbearia.get('vocab_custom')).get('profissional', 'Profissional')} inválido."
            if not erro:
                ok_h, msg_h = _dentro_horario(data, hora, dur, barbearia_id)
                if not ok_h:
                    erro = msg_h
            if not erro:
                if bid_:
                    aus = db.ausencia_ativa(bid_, data, hora)
                    if aus:
                        erro = f"{aus['barbeiro_nome']} está indisponível. Escolhe outro horário."
                if not erro:
                    with _booking_lock:
                        livre, conflito = db.verificar_disponibilidade(
                            bid_, dh, dur, barbearia_id, excluir_id=ag["id"])
                        if not livre:
                            erro = f"Conflito às {conflito['data_hora'][11:16]}. Escolhe outro horário."
                        if not erro:
                            db.reagendar_agendamento(ag["id"], dh, bid_, sid)
                            db.invalidar_cache_slots(barbearia_id)
                            _invalidar_idx(barbearia_id)
                            return render_template("reagendar_link_ok.html",
                                                   ag=enriquecer(db.get_agendamento(ag["id"])),
                                                   barbearia=barbearia,
                                                   vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))
        hoje = _agora().strftime("%Y-%m-%d")
        return render_template("reagendar.html",
                               ag=enriquecer(ag),
                               servicos=db.listar_servicos(barbearia_id),
                               barbeiros=db.listar_barbeiros(barbearia_id, incluir_chefe=True),
                               hoje=hoje, erro=erro,
                               origem="link_token",
                               barbearia=barbearia,
                               vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))


    @app.route("/cancelar-link/<token>", methods=["GET","POST"])
    def cancelar_link(token):
        """Cancelamento público — cliente acede via link com token único."""
        ag = db.get_agendamento_por_token(token)
        if not ag:
            return render_template("404.html"), 404

        barbearia_id = ag["barbearia_id"]
        barbearia    = db.get_barbearia(barbearia_id)
        if not barbearia or not barbearia["ativa"]:
            return render_template("404.html"), 404

        erro_cancelar = None
        if request.method == "POST":
            confirmado = request.form.get("confirmar") == "sim"
            if confirmado:
                db.cancelar_agendamento(ag["id"])
                ag_pos = db.get_agendamento(ag["id"])
                if ag_pos and ag_pos["status"] != "cancelado":
                    erro_cancelar = "Não foi possível cancelar — o atendimento já está em curso."
                else:
                    _invalidar_idx(barbearia_id)
                    return render_template("cancelar_link_ok.html",
                                           ag=enriquecer(ag_pos or ag),
                                           barbearia=barbearia,
                                           vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))

        return render_template("cancelar_link.html",
                               ag=enriquecer(ag),
                               barbearia=barbearia,
                               erro=erro_cancelar,
                               vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))


    @app.route("/avaliar-link/<token>", methods=["GET", "POST"])
    def avaliar_link(token):
        """Avaliação pública — cliente acede via link com token único."""
        ag = db.get_agendamento_por_token_avaliar(token)
        if not ag:
            return render_template("404.html"), 404
        barbearia = db.get_barbearia(ag["barbearia_id"])
        if not barbearia or not barbearia["ativa"]:
            return render_template("404.html"), 404
        nao_concluido = ag.get("status") != "concluido"
        ja_avaliou    = ag.get("avaliacao") is not None
        sucesso = False
        erro    = None
        if request.method == "POST" and not ja_avaliou and not nao_concluido:
            try:
                nota = int(request.form.get("nota", 0))
                if nota not in (1, 2, 3, 4, 5):
                    raise ValueError
                db.guardar_avaliacao(ag["id"], ag["barbearia_id"], nota)
                sucesso = True
            except (ValueError, TypeError):
                erro = "Seleciona uma avaliação entre 1 e 5 estrelas."
        s = db.servico_por_id(ag["servico_id"])
        b = db.get_barbeiro(ag.get("barbeiro_id"))
        return render_template("avaliar_link.html",
                               ag=ag, servico=s, barbeiro=b,
                               barbearia=barbearia,
                               sucesso=sucesso, ja_avaliou=ja_avaliou,
                               nao_concluido=nao_concluido, erro=erro,
                               vocab=get_vocab(barbearia.get("tipo") if barbearia else None, barbearia.get("vocab_custom") if barbearia else None))
