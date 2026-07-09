from datetime import datetime, date
from flask import render_template, request, redirect, url_for, session, flash
import database as db
from database import ST_AGENDADO, ST_EM_ANDAMENTO, ST_CONCLUIDO
from helpers import (
    _log, _blog, _agora, _limpar, _val_data, _val_hora, _no_passado, _dentro_horario,
    _invalidar_idx, _api_ok, _booking_lock,
    get_vocab, _MOEDA_MAP, _TEL_RE, _MAX_TEL, _MAX_MOTIVO,
    _normalizar_tel, enriquecer_lista, enriquecer,
    _VAPID_PUBLIC_KEY, _PUSH_OK,
)


def _barbearia_indisponivel(barbearia) -> bool:
    """True se a barbearia não pode receber clientes: inexistente, desativada,
    ou com o plano expirado. Verificar a expiração aqui (e não só a coluna
    `ativa`) fecha a janela entre a data de expiração e o cron nocturno
    `desativar_planos_expirados()` — nessa janela `ativa` ainda é 1."""
    if not barbearia or not barbearia.get("ativa"):
        return True
    expira = barbearia.get("plano_expira_em")
    if expira:
        try:
            if date.fromisoformat(expira) < date.today():
                return True
        except (ValueError, TypeError):
            pass
    return False


def register(app) -> None:

    @app.route("/cliente/<slug>", methods=["GET","POST"])
    def cliente_entrada(slug):
        barbearia = db.get_barbearia_por_slug(slug)
        if _barbearia_indisponivel(barbearia):
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
                tel_norm = _normalizar_tel(tel) or tel
                if db.cliente_bloqueado(barbearia_id, tel_norm):
                    erro = "Não é possível efectuar marcações com este número. Contacta a barbearia."
            if not erro:
                session.clear()
                session.permanent = True
                session.update({
                    "user_nome":    nome,
                    "role":         "cliente",
                    "telefone":     _normalizar_tel(tel) or tel,
                    "barbearia_id": barbearia_id,
                })
                return redirect(url_for("cliente_home", slug=slug))
        _mc   = db.get_config("moeda", barbearia["id"], "ECV") or "ECV"
        aval  = db.media_avaliacoes(barbearia_id)
        return render_template("cliente_entrada.html", erro=erro, barbearia=barbearia,
                               moeda_simbolo=_MOEDA_MAP.get(_mc, _mc),
                               aval=aval,
                               vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))


    @app.route("/cliente/<slug>/area")
    def cliente_home(slug):
        barbearia = db.get_barbearia_por_slug(slug)
        if _barbearia_indisponivel(barbearia):
            return render_template("404.html"), 404
        barbearia_id = barbearia["id"]
        if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia_id:
            return redirect(url_for("cliente_entrada", slug=slug))
        _proximas_statuses = {ST_AGENDADO, ST_EM_ANDAMENTO}
        _raw = enriquecer_lista(db.listar_por_telefone(session.get("telefone",""), barbearia_id))
        # Enriquecer com telefone do barbeiro (para link WhatsApp) — 1 query total
        _barb_tel = {b["id"]: b.get("telefone") for b in db.listar_barbeiros(barbearia_id, incluir_chefe=True)}
        # Destaque "a pagar" quando o serviço acabou de terminar (janela de 30 min).
        # Tempo calculado no servidor — nunca no browser.
        _agora_naive = _agora(barbearia_id).replace(tzinfo=None)
        for _a in _raw:
            _a["barbeiro_telefone"] = _barb_tel.get(_a.get("barbeiro_id"))
            _a["recem_concluido"] = False
            if _a.get("status") == ST_CONCLUIDO and _a.get("fim"):
                try:
                    _fim_dt = datetime.strptime(_a["fim"][:19], "%Y-%m-%d %H:%M:%S")
                    _mins = (_agora_naive - _fim_dt).total_seconds() / 60
                    _a["recem_concluido"] = 0 <= _mins <= 30
                except (ValueError, TypeError):
                    pass
        _proximas = [a for a in _raw if a.get("status") in _proximas_statuses]
        _outras   = sorted([a for a in _raw if a.get("status") not in _proximas_statuses],
                           key=lambda a: a.get("data_hora", ""), reverse=True)
        agendamentos = _proximas + _outras
        _slots_disponiveis = db.espera_verificar_cliente(barbearia_id, session.get("telefone",""))
        _moeda_cod = db.get_config("moeda", barbearia_id, "ECV") or "ECV"
        from helpers import _MOEDA_MAP
        # Fidelidade — stamp card
        _fid_ativo = db.get_config("fidelidade_ativo", barbearia_id, "0") == "1"
        _fidelidade = None
        if _fid_ativo:
            try:
                _fid_target = int(db.get_config("fidelidade_visitas", barbearia_id, "10") or 10)
            except (ValueError, TypeError):
                _fid_target = 10
            _fid_target = max(2, min(50, _fid_target))
            _fid_visitas = db.visitas_cliente(barbearia_id, session.get("telefone",""))
            _fid_premio  = db.get_config("fidelidade_premio", barbearia_id, "Serviço gratuito") or "Serviço gratuito"
            _fid_ciclo   = _fid_visitas % _fid_target  # posição no ciclo atual
            _fid_ganhos  = _fid_visitas // _fid_target  # prémios já ganhos
            _fidelidade = {
                "visitas": _fid_visitas,
                "target": _fid_target,
                "ciclo": _fid_ciclo,
                "ganhos": _fid_ganhos,
                "premio": _fid_premio,
                "proximo_em": _fid_target - _fid_ciclo,
            }
        return render_template("cliente_home.html", agendamentos=agendamentos, barbearia=barbearia,
                               vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")),
                               vapid_public_key=_VAPID_PUBLIC_KEY if _PUSH_OK else None,
                               slots_disponiveis=_slots_disponiveis,
                               moeda_simbolo=_MOEDA_MAP.get(_moeda_cod, _moeda_cod),
                               fidelidade=_fidelidade)


    @app.route("/cliente/<slug>/marcar", methods=["GET","POST"])
    def cliente_marcar(slug):
        barbearia = db.get_barbearia_por_slug(slug)
        if _barbearia_indisponivel(barbearia):
            return render_template("404.html"), 404
        barbearia_id = barbearia["id"]
        if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia_id:
            return redirect(url_for("cliente_entrada", slug=slug))
        tel_sessao = session.get("telefone", "")
        if db.cliente_bloqueado(barbearia_id, tel_sessao):
            flash("A tua conta foi bloqueada. Contacta a barbearia.", "erro")
            return redirect(url_for("cliente_home", slug=slug))
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
                    if not _bv or _bv.get("barbearia_id") != barbearia_id or not _bv.get("ativo"):
                        erro = f"{get_vocab(barbearia.get('tipo'), barbearia.get('vocab_custom')).get('profissional', 'Profissional')} inválido."
                if not erro:
                    ok_h, msg_h = _dentro_horario(data, hora, s["duracao_min"], barbearia_id)
                    if not ok_h:
                        erro = msg_h
                if not erro and bid_:
                    livre, hora_conf = db.verificar_disponibilidade(bid_, dh, s["duracao_min"], barbearia_id)
                    if not livre:
                        erro = f"Já existe marcação às {hora_conf or '?'}. Escolhe outro horário."
                if not erro:
                    with _booking_lock:
                        # Limite diário aplica-se sempre — com ou sem barbeiro escolhido
                        max_dia = int(db.get_config("max_por_dia", barbearia_id, 20) or 20)
                        ativos  = db.contar_ativos_dia(barbearia_id, data)
                        if ativos >= max_dia:
                            erro = "Não há vagas disponíveis para este dia. Escolhe outra data."
                        if not erro and bid_:
                            # Re-verificar ausência e disponibilidade dentro do lock (TOCTOU fix)
                            aus = db.ausencia_ativa(bid_, data, hora, duracao_min=s["duracao_min"])
                            if aus:
                                erro = f"{aus['barbeiro_nome']} está indisponível. Escolhe outro barbeiro ou data."
                            else:
                                livre, hora_conf = db.verificar_disponibilidade(bid_, dh, s["duracao_min"], barbearia_id)
                                if not livre:
                                    erro = f"Já existe marcação às {hora_conf or '?'}. Escolhe outro horário."
                        # Limite: 1 marcação online por cliente por dia
                        if not erro:
                            _tel_s = session.get("telefone", "")
                            if _tel_s and db.contar_marcacoes_cliente_dia(_tel_s, data, barbearia_id) >= 1:
                                erro = "Já tens uma marcação para esse dia. Cancela a anterior ou escolhe outra data."
                        if not erro:
                            novo_id = db.criar_agendamento(
                                session.get("user_nome",""), sid, dh, barbearia_id,
                                bid_, "agendado", 0, session.get("telefone"),
                                duracao_min=s["duracao_min"], verificar_conflito=True)
                            if novo_id == -1:
                                erro = "Esse horário acabou de ser ocupado. Escolhe outro."
                            else:
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
        if _barbearia_indisponivel(barbearia):
            return render_template("404.html"), 404
        barbearia_id = barbearia["id"]
        if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia_id:
            return redirect(url_for("cliente_entrada", slug=slug))
        ag = db.get_agendamento(id)
        if not ag or ag["telefone"] != session.get("telefone") or ag["barbearia_id"] != barbearia_id:
            return redirect(url_for("cliente_home", slug=slug))
        s = db.servico_por_id(ag["servico_id"])
        b = db.get_barbeiro(ag.get("barbeiro_id")) if ag.get("barbeiro_id") else None
        return render_template("cliente_confirmacao.html", ag=ag, servico=s,
                               barbeiro=b, barbearia=barbearia,
                               vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))


    @app.route("/cliente/<slug>/confirmar/<token>")
    def cliente_confirmar(slug, token):
        """Rota pública (sem login) para confirmação de presença via link."""
        barbearia = db.get_barbearia_por_slug(slug)
        if _barbearia_indisponivel(barbearia):
            return render_template("404.html"), 404
        ag = db.confirmar_agendamento(token)
        if ag and ag["barbearia_id"] != barbearia["id"]:
            ag = None   # token pertence a outra barbearia
        ja_confirmado = ag and bool(ag.get("confirmado"))
        return render_template("confirmar.html", barbearia=barbearia, ag=ag,
                               ja_confirmado=ja_confirmado,
                               vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))


    @app.route("/cliente/<slug>/cancelar/<int:id>", methods=["POST"])
    def cliente_cancelar(slug, id):
        barbearia = db.get_barbearia_por_slug(slug)
        if _barbearia_indisponivel(barbearia):
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
            cancelado = db.cancelar_agendamento(id)
            if cancelado:
                _invalidar_idx(barbearia_id)
                # Notificar fila de espera — push ao próximo cliente se tiver subscrição
                try:
                    from helpers import _push_espera
                    data_cancelada = ag["data_hora"][:10]
                    _entrada = db.espera_notificar_proximo(barbearia_id, data_cancelada, ag.get("barbeiro_id"))
                    if _entrada:
                        _push_espera(_entrada, barbearia_id)
                except Exception as _e:
                    import logging as _lg
                    _lg.getLogger("fila_espera").warning("espera_notificar_proximo falhou: %s", _e)
            else:
                flash("Não foi possível cancelar — a marcação já não está activa.", "aviso")
        return redirect(url_for("cliente_home", slug=slug))


    @app.route("/cliente/<slug>/reagendar/<int:id>", methods=["GET","POST"])
    def cliente_reagendar(slug, id):
        barbearia = db.get_barbearia_por_slug(slug)
        if _barbearia_indisponivel(barbearia):
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
        # Prazo mínimo de reagendamento configurado pelo chefe
        _min_h = int(db.get_config("min_horas_reagendar", barbearia_id, "0") or 0)
        if _min_h > 0:
            try:
                _ag_dt = datetime.strptime(ag["data_hora"], "%Y-%m-%d %H:%M:%S")
                if (_ag_dt - _agora(barbearia_id)).total_seconds() < _min_h * 3600:
                    erro = f"Só podes reagendar com mais de {_min_h}h de antecedência."
            except (ValueError, TypeError):
                pass
        if request.method == "POST" and not erro:
            if not _api_ok(request.remote_addr or "?"):
                return redirect(url_for("cliente_home", slug=slug))
            try:
                sid = int(request.form.get("servico_id") or ag["servico_id"])
            except (ValueError, TypeError):
                sid = int(ag["servico_id"]) if ag.get("servico_id") else None
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
                    aus = db.ausencia_ativa(bid_, data, hora, duracao_min=dur)
                    if aus:
                        erro = f"{aus['barbeiro_nome']} está indisponível. Escolhe outro barbeiro ou data."
                if not erro:
                    with _booking_lock:
                        livre, hora_conf = db.verificar_disponibilidade(bid_, dh, dur, barbearia_id, excluir_id=id)
                        if not livre:
                            erro = f"Conflito às {hora_conf or '?'}. Escolhe outro horário."
                        if not erro:
                            if db.reagendar_agendamento(id, dh, bid_, sid, duracao_min=dur, verificar_conflito=True):
                                db.invalidar_cache_slots(barbearia_id)
                                _invalidar_idx(barbearia_id)
                                return redirect(url_for("cliente_home", slug=slug))
                            erro = "Esse horário acabou de ser ocupado. Escolhe outro."
        hoje = _agora().strftime("%Y-%m-%d")
        return render_template("reagendar.html", ag=enriquecer(ag),
                               servicos=db.listar_servicos(barbearia_id),
                               barbeiros=db.listar_barbeiros(barbearia_id, incluir_chefe=True),
                               hoje=hoje, erro=erro, origem="cliente", barbearia=barbearia,
                               vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))


    @app.route("/reagendar-link/<token>", methods=["GET","POST"])
    def reagendar_link(token):
        """Reagendamento público — o cliente acede via link com token único."""
        ag = db.get_agendamento_por_token(token)
        if not ag:
            return render_template("404.html"), 404

        barbearia_id = ag["barbearia_id"]
        barbearia    = db.get_barbearia(barbearia_id)
        if _barbearia_indisponivel(barbearia):
            return render_template("404.html"), 404

        erro = None
        # Prazo mínimo de reagendamento configurado pelo chefe
        _min_h_rl = int(db.get_config("min_horas_reagendar", barbearia_id, "0") or 0)
        if _min_h_rl > 0:
            try:
                _ag_dt_rl = datetime.strptime(ag["data_hora"], "%Y-%m-%d %H:%M:%S")
                if (_ag_dt_rl - _agora(barbearia_id)).total_seconds() < _min_h_rl * 3600:
                    erro = f"Só podes reagendar com mais de {_min_h_rl}h de antecedência."
            except (ValueError, TypeError):
                pass
        if request.method == "POST" and not erro:
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
                    aus = db.ausencia_ativa(bid_, data, hora, duracao_min=dur)
                    if aus:
                        erro = f"{aus['barbeiro_nome']} está indisponível. Escolhe outro horário."
                if not erro:
                    with _booking_lock:
                        livre, hora_conf = db.verificar_disponibilidade(
                            bid_, dh, dur, barbearia_id, excluir_id=ag["id"])
                        if not livre:
                            erro = f"Conflito às {hora_conf or '?'}. Escolhe outro horário."
                        if not erro:
                            if db.reagendar_agendamento(ag["id"], dh, bid_, sid, duracao_min=dur, verificar_conflito=True):
                                db.invalidar_cache_slots(barbearia_id)
                                _invalidar_idx(barbearia_id)
                                return render_template("reagendar_link_ok.html",
                                                       ag=enriquecer(db.get_agendamento(ag["id"])),
                                                       barbearia=barbearia,
                                                       vocab=get_vocab(barbearia.get("tipo"), barbearia.get("vocab_custom")))
                            erro = "Esse horário acabou de ser ocupado. Escolhe outro."
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
        if _barbearia_indisponivel(barbearia):
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
        if _barbearia_indisponivel(barbearia):
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


    @app.route("/cliente/<slug>/fila-espera", methods=["POST"])
    def cliente_fila_espera(slug):
        barbearia = db.get_barbearia_por_slug(slug)
        if _barbearia_indisponivel(barbearia):
            return redirect(url_for("cliente_entrada", slug=slug))
        barbearia_id = barbearia["id"]
        if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia_id:
            return redirect(url_for("cliente_entrada", slug=slug))
        if not _api_ok(request.remote_addr or "?"):
            flash("Demasiados pedidos. Aguarda um momento.")
            return redirect(url_for("cliente_home", slug=slug))
        data = _limpar(request.form.get("data",""), 10)
        try:
            sid = int(request.form.get("servico_id", 0))
        except (ValueError, TypeError):
            sid = 0
        try:
            bid_raw = request.form.get("barbeiro_id") or None
            bid_ = int(bid_raw) if bid_raw else None
        except (ValueError, TypeError):
            bid_ = None
        if not data or not _val_data(data):
            flash("Data inválida.")
            return redirect(url_for("cliente_marcar", slug=slug))
        nome = session.get("user_nome", "")
        tel  = session.get("telefone", "")
        ok = db.espera_adicionar(barbearia_id, nome, tel, sid or None, bid_, data)
        if ok:
            flash("Adicionado à fila de espera! Avisamos quando houver vaga.", "sucesso")
        else:
            flash("Já estás na fila de espera para este dia.")
        return redirect(url_for("cliente_home", slug=slug))


    @app.route("/cliente/<slug>/dispensar-espera/<int:id>", methods=["POST"])
    def cliente_dispensar_espera(slug, id):
        barbearia = db.get_barbearia_por_slug(slug)
        if not barbearia: return redirect(url_for("login"))
        if session.get("role") != "cliente" or session.get("barbearia_id") != barbearia["id"]:
            return redirect(url_for("cliente_entrada", slug=slug))
        try:
            db.espera_marcar_notificado(id)
        except Exception as _e:
            import logging as _lg
            _lg.getLogger("fila_espera").warning("espera_marcar_notificado falhou id=%s: %s", id, _e)
        return redirect(url_for("cliente_home", slug=slug))
