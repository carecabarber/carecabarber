import re
import csv
import io
from datetime import datetime, timedelta
from flask import render_template, request, redirect, url_for, session, flash, Response, jsonify
import database as db
from database import ST_AGENDADO, ST_EM_ANDAMENTO, ST_CONCLUIDO, ST_CANCELADO, ST_NAO_COMP, ST_WALKIN
from helpers import (
    _log, _blog, _agora, _limpar, _val_data, _val_hora, _no_passado, _dentro_horario,
    _parse_booking_form, _enriquecer_row, enriquecer_lista, enriquecer,
    _invalidar_idx, _pc_get, _pc_set, _pc_del, _api_ok, _booking_lock,
    staff_required, chefe_required, pode_gerir_agendamento, bid,
    _push_async, _push_espera, get_vocab, DIAS_PT, _MOEDA_MAP,
    _MAX_TEL, _MAX_MOTIVO, _HISTORY_PER_PAGE, _DASHBOARD_CACHE_TTL,
    _BLOQ_CACHE_TTL, _CLEANUP_LOCK_TTL, _HORA_RE,
    _normalizar_tel,
)


def register(app) -> None:

    @app.route("/")
    @staff_required
    def index():
        barbearia_id = bid()
        # Limpar atendimentos presos — throttled a 1x por 5min por barbearia
        _lck = f"limpeza:{barbearia_id}"
        if _pc_get(_lck) is None:
            db.limpar_em_andamento_presos(barbearia_id)
            _pc_set(_lck, 1, _CLEANUP_LOCK_TTL)
        if session.get("role") == "chefe":
            filtro_bid = request.args.get("barbeiro_id", type=int)
        else:
            filtro_bid = session.get("user_id")
        _agora_dt = _agora()
        hoje_str  = _agora_dt.strftime("%Y-%m-%d")
        # ── Cache de agendamentos de hoje ──────────────────────────
        # fresh=1 força leitura fresca da BD (após iniciar/terminar/etc.),
        # evitando que o cache por-worker faça a ação parecer que falhou.
        _fresh = request.args.get("fresh")
        _ck_ag = f"idx_ag:{barbearia_id}:{filtro_bid}"
        agendamentos = None if _fresh else _pc_get(_ck_ag)
        if agendamentos is None:
            agendamentos = enriquecer_lista(db.listar_hoje(barbearia_id, filtro_bid))
            _pc_set(_ck_ag, agendamentos, _DASHBOARD_CACHE_TTL)
        else:
            # Recalcular segundos_decorridos para atendimentos em curso (não guardar no cache)
            agendamentos = [dict(a) for a in agendamentos]
            for a in agendamentos:
                if a.get("status") == "em_andamento" and a.get("inicio"):
                    try:
                        _ini = datetime.strptime(a["inicio"], "%Y-%m-%d %H:%M:%S")
                        a["segundos_decorridos"] = max(0, int((_agora() - _ini).total_seconds()))
                        a["hora_fim_estimada"] = (_ini + timedelta(minutes=a.get("duracao_estimada", 0))).strftime("%H:%M")
                    except (ValueError, TypeError):
                        pass
        em_andamento = [a for a in agendamentos if a["status"] == ST_EM_ANDAMENTO]
        barbeiros    = db.listar_barbeiros(barbearia_id, incluir_chefe=True) if session.get("role") == "chefe" else []
        # ── Cache de bloqueios (mudam raramente) ───────────────────
        _ck_bloq = f"bloq:{barbearia_id}:{hoje_str}:{filtro_bid}"
        bloqueios = _pc_get(_ck_bloq)
        if bloqueios is None:
            bloqueios = db.listar_bloqueios_dia(barbearia_id, hoje_str, filtro_bid)
            _pc_set(_ck_bloq, bloqueios, _BLOQ_CACHE_TTL)
        # ── Cache do resumo ─────────────────────────────────────────
        _ck_res = f"resumo:{barbearia_id}:{filtro_bid}"
        resumo = _pc_get(_ck_res)
        if resumo is None:
            resumo = db.resumo_hoje(barbearia_id, filtro_bid)
            _pc_set(_ck_res, resumo, _DASHBOARD_CACHE_TTL)
        # Resumo automático do fim do dia — só mostrar após hora de fecho
        _statuses_terminal = {"concluido", "cancelado", "nao_compareceu"}
        _todos_terminal = (len(agendamentos) > 0
                           and all(a["status"] in _statuses_terminal for a in agendamentos))
        _horario_hoje = db.get_horario_dia(_agora_dt.weekday(), barbearia_id)
        _passou_fecho = True
        if _horario_hoje and not _horario_hoje.get("fechado"):
            try:
                _hora_fecho_str = _horario_hoje.get("hora_fecho", "")
                if _hora_fecho_str:
                    _fecho_dt = datetime.strptime(f"{hoje_str} {_hora_fecho_str}:00", "%Y-%m-%d %H:%M:%S")
                    _passou_fecho = _agora_dt >= _fecho_dt
            except (ValueError, TypeError):
                pass
        resumo_fim_dia = None
        if _todos_terminal and _passou_fecho:
            _concluidos     = [a for a in agendamentos if a["status"] == ST_CONCLUIDO]
            _avals          = [a["avaliacao"] for a in _concluidos if a.get("avaliacao")]
            resumo_fim_dia  = {
                "concluidos":      len(_concluidos),
                "cancelados":      sum(1 for a in agendamentos if a["status"] == ST_CANCELADO),
                "nao_compareceu":  sum(1 for a in agendamentos if a["status"] == ST_NAO_COMP),
                "total_valor":     sum((a["valor"] or 0) for a in _concluidos),
                "media_avaliacao": round(sum(_avals) / len(_avals), 1) if _avals else None,
                "n_avals":         len(_avals),
            }
        # Resumo enriquecido: receita esperada + breakdown de estados
        _status_ativos = {ST_AGENDADO, ST_EM_ANDAMENTO, ST_CONCLUIDO}
        resumo_extra = {
            "agendados":     sum(1 for a in agendamentos if a["status"] == ST_AGENDADO),
            "valor_esperado": sum((a.get("preco") or 0) for a in agendamentos
                                  if a["status"] in _status_ativos),
        }
        # ── Stats semanais (só chefe, cache 5min) ──────────────────
        stats_dashboard = None
        if session.get("role") == "chefe":
            _ck_sd = f"stats_dash:{barbearia_id}"
            stats_dashboard = _pc_get(_ck_sd)
            if stats_dashboard is None:
                _ini_sem  = (_agora_dt - timedelta(days=_agora_dt.weekday())).strftime("%Y-%m-%d")
                _ini_ant  = (_agora_dt - timedelta(days=_agora_dt.weekday() + 7)).strftime("%Y-%m-%d")
                _fim_ant  = (_agora_dt - timedelta(days=_agora_dt.weekday() + 1)).strftime("%Y-%m-%d")
                _30d      = (_agora_dt - timedelta(days=30)).strftime("%Y-%m-%d")
                with db._read() as _sc:
                    _rs = _sc.execute(
                        "SELECT COALESCE(SUM(valor),0) AS v FROM agendamentos "
                        "WHERE barbearia_id=? AND status='concluido' AND data_hora>=?",
                        (barbearia_id, _ini_sem + " 00:00:00")).fetchone()
                    rec_sem = (_rs["v"] if _rs else 0) or 0
                    _ra = _sc.execute(
                        "SELECT COALESCE(SUM(valor),0) AS v FROM agendamentos "
                        "WHERE barbearia_id=? AND status='concluido' "
                        "AND data_hora BETWEEN ? AND ?",
                        (barbearia_id, _ini_ant + " 00:00:00", _fim_ant + " 23:59:59")).fetchone()
                    rec_ant = (_ra["v"] if _ra else 0) or 0
                    _rp = _sc.execute(
                        "SELECT s.nome, COUNT(*) AS n FROM agendamentos a "
                        "JOIN servicos s ON s.id=a.servico_id "
                        "WHERE a.barbearia_id=? AND a.status='concluido' AND a.data_hora>=? "
                        "GROUP BY a.servico_id ORDER BY n DESC LIMIT 1",
                        (barbearia_id, _30d + " 00:00:00")).fetchone()
                    _rh = _sc.execute(
                        "SELECT SUBSTR(data_hora,12,2) AS hora, COUNT(*) AS n FROM agendamentos "
                        "WHERE barbearia_id=? AND status='concluido' AND data_hora>=? "
                        "GROUP BY hora ORDER BY n DESC LIMIT 1",
                        (barbearia_id, _30d + " 00:00:00")).fetchone()
                    # Receita diária últimos 7 dias
                    _7d_ini = (_agora_dt - timedelta(days=6)).strftime("%Y-%m-%d")
                    _rows_d = _sc.execute(
                        "SELECT DATE(data_hora) AS dia, COALESCE(SUM(valor),0) AS v "
                        "FROM agendamentos WHERE barbearia_id=? AND status='concluido' "
                        "AND DATE(data_hora) >= ? GROUP BY dia",
                        (barbearia_id, _7d_ini)).fetchall()
                _dias_dict = {r["dia"]: r["v"] for r in _rows_d}
                _DIAS_PT_ABREV = ["Seg","Ter","Qua","Qui","Sex","Sáb","Dom"]
                _hoje_str = _agora_dt.strftime("%Y-%m-%d")
                _dias_semana = []
                for _i in range(7):
                    _d = (_agora_dt - timedelta(days=6 - _i)).date()
                    _ds = _d.strftime("%Y-%m-%d")
                    _dias_semana.append({
                        "dia": _ds,
                        "label": _DIAS_PT_ABREV[_d.weekday()],
                        "v": _dias_dict.get(_ds, 0),
                        "hoje": _ds == _hoje_str,
                    })
                _max_d = max((x["v"] for x in _dias_semana), default=0) or 1
                for _dx in _dias_semana:
                    _dx["pct"] = round(_dx["v"] / _max_d * 100)
                _pct = round((rec_sem - rec_ant) / rec_ant * 100) if rec_ant else None
                stats_dashboard = {
                    "rec_sem":         rec_sem,
                    "rec_ant":         rec_ant,
                    "rec_diff":        rec_sem - rec_ant,
                    "rec_pct":         _pct,
                    "rec_pct_abs":     abs(_pct) if _pct is not None else None,
                    "servico_popular": dict(_rp) if _rp and _rp["n"] else None,
                    "hora_pico":       dict(_rh) if _rh and _rh["n"] else None,
                    "dias_semana":     _dias_semana,
                }
                _pc_set(_ck_sd, stats_dashboard, 300)
        # Agendamentos nas próximas 2h sem confirmação do cliente (alerta ao chefe)
        if session.get("role") == "chefe":
            _2h_str    = (_agora_dt + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
            _agora_str = _agora_dt.strftime("%Y-%m-%d %H:%M:%S")
            nao_confirmados_ids = {
                a["id"] for a in agendamentos
                if (a.get("status") == ST_AGENDADO
                    and not a.get("confirmado")
                    and a.get("token_confirmar")
                    and _agora_str <= a["data_hora"] <= _2h_str)
            }
        else:
            nao_confirmados_ids = set()
        return render_template("index.html", agendamentos=agendamentos, em_andamento=em_andamento,
                               barbeiros=barbeiros, barbeiro_id_sel=filtro_bid,
                               resumo=resumo,
                               resumo_extra=resumo_extra,
                               bloqueios=bloqueios,
                               resumo_fim_dia=resumo_fim_dia,
                               stats_dashboard=stats_dashboard,
                               nao_confirmados_ids=nao_confirmados_ids,
                               agora=_agora_dt.strftime("%H:%M"),
                               agora_iso=_agora_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                               tz_barbearia=db.get_barbearia_tz(barbearia_id))


    @app.route("/novo", methods=["GET","POST"])
    @staff_required
    def novo():
        barbearia_id = bid()
        barbearia    = db.get_barbearia(barbearia_id)
        erro = None
        if request.method == "POST":
            nome  = _limpar(request.form.get("cliente",""))
            tel   = _normalizar_tel(_limpar(request.form.get("telefone",""), _MAX_TEL)) or None
            notas = _limpar(request.form.get("notas",""), _MAX_MOTIVO) or None
            _default_bid = None if session.get("role") == "chefe" else session.get("user_id")
            sid, bid_, data, hora, dh, s, erro = _parse_booking_form(
                barbearia_id, barbearia=barbearia, default_bid=_default_bid)
            if not erro and not nome:
                erro = "Preenche o nome do cliente."
            if not erro:
                ok_h, msg_h = _dentro_horario(data, hora, s["duracao_min"], barbearia_id)
                if not ok_h:
                    erro = msg_h
            if not erro:
                with _booking_lock:
                    if bid_:
                        livre, hora_conf = db.verificar_disponibilidade(bid_, dh, s["duracao_min"], barbearia_id)
                        if not livre:
                            erro = f"Conflito às {hora_conf or '?'}. Escolhe outro horário."
                    if not erro:
                        _novo_id = db.criar_agendamento(nome, sid, dh, barbearia_id, bid_, ST_AGENDADO, 0, tel, notas,
                                                        duracao_min=s["duracao_min"], verificar_conflito=True)
                        if _novo_id == -1:
                            erro = "Esse horário acabou de ser ocupado. Escolhe outro."
                        else:
                            _blog("NOVO_AGENDAMENTO", bid=barbearia_id, barb=bid_, sid=sid, dh=dh)
                            _invalidar_idx(barbearia_id)
                            return redirect(url_for("index", fresh=1))
        hoje = _agora().strftime("%Y-%m-%d")
        return render_template("novo.html", servicos=db.listar_servicos(barbearia_id),
                               barbeiros=db.listar_barbeiros(barbearia_id, incluir_chefe=True),
                               hoje=hoje, agora=_agora().strftime("%H:%M"), erro=erro)


    @app.route("/walkin", methods=["GET","POST"])
    @staff_required
    def walkin():
        barbearia_id = bid()
        if request.method == "POST":
            nome  = _limpar(request.form.get("cliente",""))
            tel   = _normalizar_tel(_limpar(request.form.get("telefone",""), _MAX_TEL)) or None
            notas = _limpar(request.form.get("notas",""), _MAX_MOTIVO) or None
            try:
                sid = int(request.form.get("servico_id", 0))
            except (ValueError, TypeError):
                sid = 0
            if session.get("role") == "chefe":
                try:
                    _bid_raw = request.form.get("barbeiro_id") or None
                    bid_ = int(_bid_raw) if _bid_raw else None
                except (ValueError, TypeError):
                    bid_ = None
            else:
                bid_ = session.get("user_id")
            if not nome or not sid:
                flash("⚠️ Preenche o nome do cliente e o serviço.", "erro")
                return redirect(url_for("walkin"))
            if len(nome) < 2:
                flash("⚠️ Nome demasiado curto.", "erro")
                return redirect(url_for("walkin"))
            if nome and sid:
                s = db.servico_por_id(sid)
                _barb_ok = True
                if bid_:
                    _bv = db.get_barbeiro(bid_)
                    _barb_ok = bool(_bv and _bv.get("barbearia_id") == barbearia_id)
                    if _bv and not _bv.get("ativo"):
                        flash(f"⚠️ {_bv['nome']} está desativado.", "erro")
                        return redirect(url_for("walkin"))
                if s and s.get("barbearia_id") == barbearia_id and _barb_ok:
                    _agora_wi = _agora()
                    _data_wi  = _agora_wi.strftime("%Y-%m-%d")
                    _hora_wi  = _agora_wi.strftime("%H:%M")
                    _bloq = db.ausencia_ativa(bid_, _data_wi, _hora_wi) if bid_ else None
                    _barbearia_wi = db.get_barbearia(barbearia_id) or {}
                    _vw = get_vocab(_barbearia_wi.get("tipo"), _barbearia_wi.get("vocab_custom"))
                    if _bloq:
                        _motivo = _bloq.get("motivo") or "bloqueio"
                        flash(f"🔒 {_bloq.get('barbeiro_nome', _vw.get('profissional','Profissional'))} está em pausa ({_motivo}). "
                              f"Bloqueado até às {_bloq.get('hora_fim','?')}.", "erro")
                        return redirect(url_for("walkin"))
                    agora_str = _agora_wi.strftime("%Y-%m-%d %H:%M:%S")
                    _aviso_conflito = None
                    with _booking_lock:
                        if bid_ and db.barbeiro_tem_em_andamento(bid_):
                            flash(f"⚠️ O {_vw.get('profissional','Barbeiro').lower()} já tem um {_vw.get('servico','serviço').lower()} em curso. Aguarda que termine.", "erro")
                            return redirect(url_for("walkin"))
                        # Aviso (não bloqueia): o walk-in começa AGORA e pode invadir uma
                        # marcação futura do barbeiro se o serviço for mais longo que a folga.
                        if bid_:
                            _livre_wi, _hc_wi = db.verificar_disponibilidade(bid_, agora_str, s["duracao_min"], barbearia_id)
                            if not _livre_wi:
                                _aviso_conflito = _hc_wi
                        novo_id = db.criar_agendamento(nome, sid, agora_str, barbearia_id, bid_, ST_WALKIN, 0, tel, notas)
                        ok = db.iniciar_trabalho(novo_id)
                        if not ok:
                            db.deletar_walkin_orfao(novo_id)
                            flash(f"⚠️ O {_vw.get('profissional','Barbeiro').lower()} já tem um {_vw.get('servico','serviço').lower()} em curso. Tenta novamente.", "erro")
                            return redirect(url_for("walkin"))
                        _blog("WALKIN", bid=barbearia_id, barb=bid_, sid=sid, ag_id=novo_id)
                    _invalidar_idx(barbearia_id)
                    if _aviso_conflito:
                        flash(f"⚠️ Walk-in iniciado, mas sobrepõe-se à marcação das {_aviso_conflito}. "
                              f"Convém reagendar essa marcação.", "aviso")
                    return redirect(url_for("index", fresh=1))
        _agora_dt   = _agora()
        _weekday    = _agora_dt.weekday()
        _horario    = db.get_horario_dia(_weekday, barbearia_id)
        _fora_horario = False
        if not _horario.get("fechado"):
            try:
                _fecho    = datetime.strptime(f"{_agora_dt.strftime('%Y-%m-%d')} {_horario['hora_fecho']}:00", "%Y-%m-%d %H:%M:%S")
                _abertura = datetime.strptime(f"{_agora_dt.strftime('%Y-%m-%d')} {_horario['hora_abertura']}:00", "%Y-%m-%d %H:%M:%S")
                _fora_horario = _agora_dt >= _fecho or _agora_dt < _abertura
            except (ValueError, TypeError):
                pass
        return render_template("walkin.html", servicos=db.listar_servicos(barbearia_id),
                               barbeiros=db.listar_barbeiros(barbearia_id, incluir_chefe=True),
                               fora_horario=_fora_horario,
                               hora_fecho=_horario.get("hora_fecho",""))


    @app.route("/iniciar/<int:id>", methods=["POST"])
    @staff_required
    def iniciar(id):
        ag = db.get_agendamento(id)
        if not ag:
            flash("⚠️ Marcação não encontrada.", "erro")
            return redirect(url_for("index"))
        if not pode_gerir_agendamento(ag):
            flash("⚠️ Sem permissão para gerir esta marcação.", "erro")
            return redirect(url_for("index"))
        if ag.get("barbeiro_id") and db.barbeiro_tem_em_andamento(ag["barbeiro_id"]):
            _bb = db.get_barbearia(ag["barbearia_id"]) or {}
            _bv = get_vocab(_bb.get("tipo"), _bb.get("vocab_custom"))
            flash(f"Este {_bv.get('profissional','Barbeiro').lower()} já tem um {_bv.get('servico','serviço').lower()} em curso. Termina-o primeiro.", "erro")
            return redirect(url_for("index"))
        if not db.iniciar_trabalho(id):
            flash("⚠️ Não foi possível iniciar — verifica se já está em curso.", "erro")
        else:
            _invalidar_idx(ag["barbearia_id"])
            if ag.get("barbeiro_id") and ag["barbeiro_id"] != session.get("user_id"):
                cliente = ag.get("cliente", "Cliente")
                _push_async(ag["barbearia_id"],
                            "✂️ Atendimento iniciado",
                            f"{cliente} está a ser atendido",
                            barbeiro_id=ag["barbeiro_id"])
        return redirect(url_for("index", fresh=1))


    @app.route("/terminar/<int:id>", methods=["POST"])
    @staff_required
    def terminar(id):
        ag = db.get_agendamento(id)
        if not ag:
            flash("⚠️ Marcação não encontrada.", "erro")
            return redirect(url_for("index"))
        if not pode_gerir_agendamento(ag):
            flash("⚠️ Sem permissão para gerir esta marcação.", "erro")
            return redirect(url_for("index"))
        try:
            valor = int(request.form.get("valor") or 0)
            valor = max(0, min(valor, 999_999))
        except (ValueError, TypeError):
            valor = 0
        db.terminar_trabalho(id, valor)
        _blog("TERMINAR", bid=ag["barbearia_id"], ag_id=id, valor=valor)
        _invalidar_idx(ag["barbearia_id"])
        try:
            nota_raw = request.form.get("avaliacao")
            if nota_raw:
                nota = int(nota_raw)
                if nota not in (1, 2, 3, 4, 5):
                    raise ValueError("nota fora do intervalo")
                db.guardar_avaliacao(id, ag["barbearia_id"], nota)
        except (ValueError, TypeError):
            pass
        return redirect(url_for("index", fresh=1))


    @app.route("/avaliar/<int:id>", methods=["POST"])
    @staff_required
    def avaliar(id):
        """Endpoint AJAX para registar/alterar avaliação de um agendamento concluído."""
        ag = db.get_agendamento(id)
        if not ag or not pode_gerir_agendamento(ag) or ag["status"] != ST_CONCLUIDO:
            return jsonify({"ok": False, "error": "Sem permissão ou estado inválido"}), 403
        try:
            data = request.get_json(silent=True) or {}
            nota = int(data.get("nota", 0))
            if nota not in (1, 2, 3, 4, 5):
                return jsonify({"ok": False, "error": "Nota deve ser entre 1 e 5"}), 400
            db.guardar_avaliacao(id, ag["barbearia_id"], nota)
            return jsonify({"ok": True})
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "Nota inválida. Usa um valor entre 1 e 5."}), 400


    @app.route("/nao-compareceu/<int:id>", methods=["POST"])
    @staff_required
    def nao_compareceu(id):
        ag = db.get_agendamento(id)
        if ag and pode_gerir_agendamento(ag) and ag["status"] == ST_AGENDADO:
            if db.marcar_nao_compareceu(id):
                _invalidar_idx(ag["barbearia_id"])
        return redirect(url_for("index", fresh=1))


    @app.route("/bloquear", methods=["POST"])
    @staff_required
    def bloquear_horario():
        """Cria um bloqueio de horário para um barbeiro."""
        barbearia_id = bid()
        hora_ini = _limpar(request.form.get("hora_inicio", ""), 5)
        hora_fim = _limpar(request.form.get("hora_fim", ""), 5)
        motivo   = _limpar(request.form.get("motivo", ""), _MAX_MOTIVO)
        data     = _limpar(request.form.get("data", _agora().strftime("%Y-%m-%d")), 10)
        if session.get("role") == "chefe":
            try:
                barbeiro_id_ = int(request.form.get("barbeiro_id") or 0)
            except (ValueError, TypeError):
                barbeiro_id_ = 0
        else:
            barbeiro_id_ = session.get("user_id")
        if not barbeiro_id_:
            return redirect(url_for("index"))
        b = db.get_barbeiro(barbeiro_id_)
        if not b or b.get("barbearia_id") != barbearia_id:
            return redirect(url_for("index"))
        if not _val_data(data) or not _val_hora(hora_ini) or not _val_hora(hora_fim):
            flash("Data ou hora inválida.", "erro")
            return redirect(url_for("index"))
        if hora_ini >= hora_fim:
            flash("A hora de início deve ser anterior à hora de fim.", "erro")
            return redirect(url_for("index"))
        if data < _agora().strftime("%Y-%m-%d"):
            flash("Não é possível criar bloqueios em datas passadas.", "erro")
            return redirect(url_for("index"))
        try:
            db.criar_bloqueio_hora(barbeiro_id_, data, hora_ini, hora_fim, motivo)
        except ValueError as e:
            flash(f"⚠️ {e}", "erro")
            return redirect(url_for("index"))
        db.invalidar_cache_slots(barbearia_id)
        _invalidar_idx(barbearia_id)
        flash(f"🔒 Bloqueio registado: {hora_ini}–{hora_fim}" + (f" ({motivo})" if motivo else ""), "sucesso")
        return redirect(url_for("index"))


    @app.route("/desbloquear/<int:id>", methods=["POST"])
    @staff_required
    def desbloquear_horario(id):
        """Remove um bloqueio de horário."""
        barbearia_id = bid()
        ausencias = db.listar_ausencias(barbearia_id)
        a = next((x for x in ausencias if x["id"] == id and x.get("tipo") == "bloqueio"), None)
        if a:
            if session.get("role") == "chefe" or a["barbeiro_id"] == session.get("user_id"):
                db.apagar_ausencia(id)
                db.invalidar_cache_slots(barbearia_id)
                _invalidar_idx(barbearia_id)
        return redirect(url_for("index"))


    @app.route("/cancelar/<int:id>", methods=["POST"])
    @staff_required
    def cancelar(id):
        ag = db.get_agendamento(id)
        if ag and pode_gerir_agendamento(ag):
            db.cancelar_agendamento(id, incluir_em_andamento=True)
            _blog("CANCELAR", bid=ag["barbearia_id"], ag_id=id, uid=session.get("user_id"))
            _invalidar_idx(ag["barbearia_id"])
            # Notificar fila de espera — push ao próximo cliente se tiver subscrição
            try:
                data_cancelada = ag["data_hora"][:10]
                _entrada = db.espera_notificar_proximo(ag["barbearia_id"], data_cancelada, ag.get("barbeiro_id"))
                if _entrada:
                    _push_espera(_entrada, ag["barbearia_id"])
            except Exception as _e:
                _log(f"ESPERA_NOTIF_ERR ag={id} err={_e}")
        return redirect(url_for("index", fresh=1))


    @app.route("/reagendar/<int:id>", methods=["GET","POST"])
    @staff_required
    def reagendar(id):
        barbearia_id = bid()
        ag = db.get_agendamento(id)
        if not ag or ag["status"] != ST_AGENDADO or not pode_gerir_agendamento(ag):
            return redirect(url_for("index"))
        is_chefe = session.get("role") == "chefe"
        _barbearia_r = db.get_barbearia(barbearia_id) or {}
        _vprof_r = get_vocab(_barbearia_r.get("tipo"), _barbearia_r.get("vocab_custom")).get("profissional", "Barbeiro")
        erro = None
        if request.method == "POST":
            try:
                sid = int(request.form.get("servico_id") or ag["servico_id"])
            except (ValueError, TypeError):
                sid = ag["servico_id"]
            if is_chefe:
                try:
                    _bid_raw = request.form.get("barbeiro_id") or None
                    bid_ = int(_bid_raw) if _bid_raw else ag.get("barbeiro_id")
                except (ValueError, TypeError):
                    bid_ = ag.get("barbeiro_id")
            else:
                bid_ = session.get("user_id")
            data = _limpar(request.form.get("data",""), 10)
            hora = _limpar(request.form.get("hora",""), 5)
            if not data or not hora:
                erro = "Preenche a data e hora."
            elif not _val_data(data) or not _val_hora(hora):
                erro = "Data ou hora inválida."
            elif _no_passado(data, hora):
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
                    erro = f"{get_vocab(_barbearia_r.get('tipo'), _barbearia_r.get('vocab_custom')).get('profissional', 'Profissional')} inválido."
            if not erro:
                ok_h, msg_h = _dentro_horario(data, hora, dur, barbearia_id)
                if not ok_h:
                    erro = msg_h
            if not erro:
                if bid_:
                    aus = db.ausencia_ativa(bid_, data, hora, duracao_min=dur)
                    if aus:
                        erro = f"{aus['barbeiro_nome']} está indisponível. Escolhe outro {_vprof_r.lower()} ou data."
                if not erro:
                    with _booking_lock:
                        livre, hora_conf = db.verificar_disponibilidade(
                            bid_, dh, dur, barbearia_id, excluir_id=id)
                        if not livre:
                            erro = f"Conflito às {hora_conf or '?'}. Escolhe outro horário."
                        if not erro:
                            if db.reagendar_agendamento(id, dh, bid_, sid, duracao_min=dur, verificar_conflito=True):
                                db.invalidar_cache_slots(barbearia_id)
                                _invalidar_idx(barbearia_id)
                                return redirect(url_for("index"))
                            erro = "Esse horário acabou de ser ocupado. Escolhe outro."
        hoje = _agora().strftime("%Y-%m-%d")
        return render_template("reagendar.html", ag=enriquecer(ag),
                               servicos=db.listar_servicos(barbearia_id),
                               barbeiros=db.listar_barbeiros(barbearia_id, incluir_chefe=True) if is_chefe else [],
                               hoje=hoje, erro=erro, origem="staff",
                               vocab=get_vocab(_barbearia_r.get("tipo"), _barbearia_r.get("vocab_custom")))


    @app.route("/historico")
    @staff_required
    def historico():
        barbearia_id = bid()
        if session.get("role") == "chefe":
            filtro_bid = request.args.get("barbeiro_id", type=int)
            if filtro_bid is not None:
                _barbs_validos = {b["id"] for b in db.listar_barbeiros(barbearia_id, incluir_chefe=True)}
                if filtro_bid not in _barbs_validos:
                    filtro_bid = None
        else:
            filtro_bid = session.get("user_id")
        data_sel      = _limpar(request.args.get("data",""), 10)
        periodo       = request.args.get("periodo", "")
        status_filtro = request.args.get("status", "")
        _STATUS_OK    = {"", "concluido", "cancelado", "agendado", "nao_compareceu"}
        if status_filtro not in _STATUS_OK:
            status_filtro = ""
        if data_sel and not _val_data(data_sel):
            data_sel = ""
        data_ini = data_fim = None
        if periodo == "hoje":
            hoje = _agora(barbearia_id).date()
            data_ini = data_fim = hoje.strftime("%Y-%m-%d")
            data_sel = ""
        elif periodo == "semana":
            hoje = _agora(barbearia_id).date()
            data_ini = (hoje - timedelta(days=hoje.weekday())).strftime("%Y-%m-%d")
            data_fim = hoje.strftime("%Y-%m-%d")
            data_sel = ""
        elif periodo == "mes":
            hoje = _agora(barbearia_id).date()
            data_ini = hoje.strftime("%Y-%m-01")
            data_fim = hoje.strftime("%Y-%m-%d")
            data_sel = ""
        if not data_sel and data_fim is None:
            hoje = _agora(barbearia_id).date()
            data_fim = hoje.strftime("%Y-%m-%d")
            if data_ini is None:
                data_ini = (hoje - timedelta(days=30)).strftime("%Y-%m-%d")
        if data_sel:
            page = 1
            _limit = _offset = None
            total_registos = None
        else:
            try:
                try:
                    page = max(1, min(int(request.args.get("pagina", 1)), 10000))
                except (TypeError, ValueError):
                    page = 1
            except (ValueError, TypeError):
                page = 1
            _offset = (page - 1) * _HISTORY_PER_PAGE
            _limit  = _HISTORY_PER_PAGE
            total_registos = db.contar_todos(barbearia_id, filtro_bid, None,
                                             data_ini=data_ini, data_fim=data_fim,
                                             status=status_filtro or None)
        datas        = db.listar_datas_historico(barbearia_id, filtro_bid)
        agendamentos = enriquecer_lista(
                        db.listar_todos(barbearia_id, filtro_bid, data_sel or None,
                                        data_ini=data_ini, data_fim=data_fim,
                                        limit=_limit, offset=_offset or 0,
                                        status=status_filtro or None))
        barbeiros    = db.listar_barbeiros(barbearia_id, incluir_chefe=True) if session.get("role") == "chefe" else []
        total_valor  = sum((a["valor"] or 0) for a in agendamentos if a["status"] == ST_CONCLUIDO)
        total_cortes = sum(1 for a in agendamentos if a["status"] == ST_CONCLUIDO)
        total_paginas = (((total_registos or 0) + _HISTORY_PER_PAGE - 1) // _HISTORY_PER_PAGE) if total_registos else 1
        return render_template("historico.html", agendamentos=agendamentos,
                               barbeiros=barbeiros, barbeiro_id_sel=filtro_bid,
                               total_valor=total_valor, total_cortes=total_cortes,
                               datas=datas, data_sel=data_sel, periodo=periodo,
                               status_filtro=status_filtro,
                               pagina=page, total_paginas=total_paginas,
                               total_registos=total_registos)


    @app.route("/minhas-marcacoes")
    @staff_required
    def minhas_marcacoes():
        """Vista pessoal com marcações de hoje em diante."""
        if session.get("role") not in ("barbeiro", "chefe"):
            return redirect(url_for("index"))
        barbearia_id = bid()
        barbeiro_id  = session.get("user_id")
        if barbeiro_id and not session.get("root_gerir"):
            agendamentos = enriquecer_lista(
                db.listar_proximas_barbeiro(barbearia_id, barbeiro_id))
        else:
            agendamentos = []
        from collections import OrderedDict
        _agora_local = _agora(barbearia_id)
        hoje   = _agora_local.strftime("%Y-%m-%d")
        amanha = (_agora_local + timedelta(days=1)).strftime("%Y-%m-%d")
        grupos = OrderedDict()
        for a in agendamentos:
            data = (a.get("data_hora") or "")[:10]
            if data not in grupos:
                grupos[data] = []
            grupos[data].append(a)
        return render_template("minhas_marcacoes.html",
                               grupos=grupos,
                               hoje=hoje,
                               amanha=amanha,
                               total=len(agendamentos))


    @app.route("/historico/exportar.csv")
    @chefe_required
    def historico_exportar_csv():
        """Exporta o histórico filtrado para CSV."""
        barbearia_id = bid()
        filtro_bid   = request.args.get("barbeiro_id", type=int)
        if filtro_bid is not None:
            _barbs_validos = {b["id"] for b in db.listar_barbeiros(barbearia_id, incluir_chefe=True)}
            if filtro_bid not in _barbs_validos:
                filtro_bid = None
        data_sel      = _limpar(request.args.get("data",""), 10)
        periodo       = request.args.get("periodo","")
        status_filtro = request.args.get("status","")
        _STATUS_OK    = {"", "concluido", "cancelado", "agendado", "nao_compareceu"}
        if status_filtro not in _STATUS_OK:
            status_filtro = ""
        if data_sel and not _val_data(data_sel): data_sel = ""
        data_ini = data_fim = None
        if periodo == "hoje":
            hoje = _agora().date()
            data_ini = data_fim = hoje.strftime("%Y-%m-%d")
            data_sel = ""
        elif periodo == "semana":
            hoje = _agora().date()
            data_ini = (hoje - timedelta(days=hoje.weekday())).strftime("%Y-%m-%d")
            data_fim = hoje.strftime("%Y-%m-%d")
            data_sel = ""
        elif periodo == "mes":
            hoje = _agora().date()
            data_ini = hoje.strftime("%Y-%m-01")
            data_fim = hoje.strftime("%Y-%m-%d")
            data_sel = ""
        agendamentos = enriquecer_lista(
                        db.listar_todos(barbearia_id, filtro_bid, data_sel or None,
                                        data_ini=data_ini, data_fim=data_fim,
                                        status=status_filtro or None))
        _CSV_FORMULA = re.compile(r'^[=+\-@|]')
        def _csv_safe(v):
            s = str(v) if v is not None else ""
            return "'" + s if _CSV_FORMULA.match(s) else s

        buf = io.StringIO()
        w   = csv.writer(buf)
        _moeda_csv = _MOEDA_MAP.get(db.get_config("moeda", barbearia_id, "ECV") or "ECV", "ECV")
        w.writerow(["Data", "Hora", "Cliente", "Telefone", "Serviço", "Barbeiro",
                    "Estado", f"Valor ({_moeda_csv})", "Duração Est. (min)", "Duração Real (min)", "Avaliação"])
        for a in agendamentos:
            w.writerow([
                a["data"], a["hora"], _csv_safe(a["cliente"]), _csv_safe(a.get("telefone","")),
                _csv_safe(a["servico_nome"]), _csv_safe(a["barbeiro_nome"]), a["status"],
                a.get("valor",""), a["duracao_estimada"],
                a.get("duracao_real","") if a.get("duracao_real") is not None else "",
                a.get("avaliacao","") or "",
            ])
        output  = buf.getvalue()
        ts      = _agora().strftime("%Y%m%d_%H%M")
        nome    = f"historico_{ts}.csv"
        return app.response_class(
            output.encode("utf-8-sig"),
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{nome}"'}
        )


    # ── Fila de espera — painel staff ─────────────────────────────────────────

    @app.route("/fila-espera")
    @chefe_required
    def fila_espera():
        barbearia_id = bid()
        resultado    = db.espera_listar_activa(barbearia_id, limit=100)
        barbearia    = db.get_barbearia(barbearia_id)
        vocab        = get_vocab(barbearia.get("tipo") if barbearia else None,
                                 barbearia.get("vocab_custom") if barbearia else None)
        # Agrupar por data
        from collections import defaultdict as _dd
        por_data = _dd(list)
        for e in resultado["items"]:
            por_data[e["data_preferida"]].append(e)
        grupos = sorted(por_data.items())
        return render_template("fila_espera.html",
                               grupos=grupos,
                               total=resultado["total"],
                               vocab=vocab)


    @app.route("/fila-espera/<int:id>/remover", methods=["POST"])
    @chefe_required
    def fila_espera_remover(id):
        barbearia_id = bid()
        db.espera_remover(id, barbearia_id)
        flash("Entrada removida da fila de espera.", "sucesso")
        return redirect(url_for("fila_espera"))
