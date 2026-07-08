from flask import render_template, request, redirect, url_for, session, flash
from zoneinfo import ZoneInfo
import database as db
from helpers import (
    _log, _blog, _agora, _limpar, _val_data, _val_hora,
    _invalidar_idx, chefe_required, bid,
    MOEDAS, _MOEDA_MAP, DIAS_PT, _HORA_RE, _MAX_MOTIVO,
    _pc_del, _TEL_RE, _normalizar_tel, get_vocab,
)

_PERIODOS_VALIDOS = (30, 60, 90, 180, 365)


def register(app) -> None:

    @app.route("/servicos", methods=["GET","POST"])
    @chefe_required
    def servicos():
        barbearia_id = bid()
        if request.method == "POST":
            nome = _limpar(request.form.get("nome",""))
            try:
                dur   = max(5,  min(int(request.form.get("duracao_min") or 30), 300))
                preco = max(0, min(int(request.form.get("preco") or 0), 999_999))
            except (ValueError, TypeError):
                dur, preco = 30, 0
            categoria = _limpar(request.form.get("categoria", ""), 40) or None
            if nome:
                db.criar_servico(nome, dur, barbearia_id, preco, categoria=categoria)
                db.invalidar_cache_slots(barbearia_id)
                _invalidar_idx(barbearia_id)
                flash(f"✓ Serviço «{nome}» criado com sucesso!", "sucesso")
            return redirect(url_for("servicos"))
        return render_template("servicos.html", servicos=db.listar_servicos(barbearia_id, apenas_ativos=False))


    @app.route("/servicos/editar/<int:id>", methods=["POST"])
    @chefe_required
    def editar_servico(id):
        s = db.servico_por_id(id)
        if not s or s.get("barbearia_id") != bid():
            return redirect(url_for("servicos"))
        nome = _limpar(request.form.get("nome",""))
        try:
            dur   = max(5,  min(int(request.form.get("duracao_min") or 30), 300))
            preco = max(0, min(int(request.form.get("preco") or 0), 999_999))
        except (ValueError, TypeError):
            dur, preco = 30, 0
        categoria = _limpar(request.form.get("categoria", ""), 40) or None
        if nome:
            db.atualizar_servico(id, nome, dur, preco, barbearia_id=s["barbearia_id"], categoria=categoria)
            db.invalidar_cache_slots(s["barbearia_id"])
            _invalidar_idx(s["barbearia_id"])
            flash(f"✓ Serviço «{nome}» atualizado!", "sucesso")
        return redirect(url_for("servicos"))


    @app.route("/servicos/mover/<int:id>/<direcao>", methods=["POST"])
    @chefe_required
    def mover_servico(id, direcao):
        if direcao in ("up", "down"):
            s = db.servico_por_id(id)
            if s and s.get("barbearia_id") == bid():
                db.mover_servico(id, direcao, bid())
        return redirect(url_for("servicos"))


    @app.route("/servicos/toggle/<int:id>", methods=["POST"])
    @chefe_required
    def toggle_servico(id):
        s = db.servico_por_id(id)
        if s and s.get("barbearia_id") == bid():
            db.toggle_servico_ativo(id, bid(), 0 if s["ativo"] else 1)
            _invalidar_idx(bid())
        return redirect(url_for("servicos"))


    @app.route("/servicos/apagar/<int:id>", methods=["POST"])
    @chefe_required
    def apagar_servico(id):
        s = db.servico_por_id(id)
        if s and s.get("barbearia_id") == bid():
            db.apagar_servico(id, barbearia_id=bid())
            _invalidar_idx(bid())
        return redirect(url_for("servicos"))


    @app.route("/configuracoes", methods=["GET","POST"])
    @chefe_required
    def configuracoes():
        barbearia_id = bid()
        if request.method == "POST":
            acao = request.form.get("acao")
            if acao == "horario":
                for dia in range(7):
                    aberto     = request.form.get(f"aberto_{dia}")
                    abertura_v = request.form.get(f"abertura_{dia}", "08:00")
                    fecho_v    = request.form.get(f"fecho_{dia}",    "19:00")
                    if not _HORA_RE.match(abertura_v): abertura_v = "08:00"
                    if not _HORA_RE.match(fecho_v):    fecho_v    = "19:00"
                    if abertura_v >= fecho_v:
                        abertura_v, fecho_v = "08:00", "19:00"
                    db.set_horario_dia(dia, abertura_v, fecho_v, 0 if aberto else 1, barbearia_id)
                db.invalidar_cache_slots(barbearia_id)
                flash("✓ Horário de funcionamento guardado!", "sucesso")
            elif acao == "geral":
                try:
                    buf = max(0, min(int(request.form.get("buffer_minutos", 10)), 60))
                    mpd = max(1, min(int(request.form.get("max_por_dia", 20)), 200))
                except (ValueError, TypeError):
                    buf, mpd = 10, 20
                try:
                    min_h_r = max(0, min(int(request.form.get("min_horas_reagendar", 0)), 168))
                except (ValueError, TypeError):
                    min_h_r = 0
                db.set_config("buffer_minutos",      buf,     barbearia_id)
                db.set_config("max_por_dia",         mpd,     barbearia_id)
                db.set_config("min_horas_reagendar", min_h_r, barbearia_id)
                moeda_nova = request.form.get("moeda", "ECV")
                if moeda_nova in _MOEDA_MAP:
                    db.set_config("moeda", moeda_nova, barbearia_id)
                tz_novo = _limpar(request.form.get("timezone", ""), 60)
                if tz_novo:
                    try:
                        ZoneInfo(tz_novo)
                        db.set_barbearia_tz(barbearia_id, tz_novo)
                        db.invalidar_cache_slots(barbearia_id)
                    except Exception:
                        flash("⚠️ Fuso horário inválido — ignorado.", "erro")
                flash("✓ Configurações gerais guardadas!", "sucesso")
            elif acao == "dia_fechado":
                data   = _limpar(request.form.get("data_fechada",""), 10)
                motivo = _limpar(request.form.get("motivo_fechado",""), _MAX_MOTIVO)
                if data and _val_data(data):
                    db.adicionar_dia_fechado(data, motivo, barbearia_id)
            elif acao == "remover_dia":
                try:
                    dia_id = int(request.form.get("dia_id", 0))
                    dias   = db.listar_dias_fechados(barbearia_id)
                    if any(d["id"] == dia_id for d in dias):
                        db.remover_dia_fechado(dia_id)
                except (ValueError, TypeError):
                    pass
            elif acao == "fidelidade":
                fid_ativo = "1" if request.form.get("fidelidade_ativo") == "1" else "0"
                try:
                    fid_vis = max(2, min(50, int(request.form.get("fidelidade_visitas", 10))))
                except (ValueError, TypeError):
                    fid_vis = 10
                fid_premio = _limpar(request.form.get("fidelidade_premio", "Serviço gratuito"), 80) or "Serviço gratuito"
                db.set_config("fidelidade_ativo",   fid_ativo,  barbearia_id)
                db.set_config("fidelidade_visitas", fid_vis,    barbearia_id)
                db.set_config("fidelidade_premio",  fid_premio, barbearia_id)
                flash("✓ Programa de fidelidade guardado!", "sucesso")
            return redirect(url_for("configuracoes"))
        horario       = db.get_horario(barbearia_id)
        dias_fechados = db.listar_dias_fechados(barbearia_id)
        configs       = db.get_todas_configs(barbearia_id)
        barbearia     = db.get_barbearia(barbearia_id)
        return render_template("configuracoes.html", horario=horario,
                               dias_fechados=dias_fechados, configs=configs,
                               barbearia=barbearia,
                               moedas=MOEDAS,
                               dias_pt=DIAS_PT, hoje=_agora().strftime("%Y-%m-%d"))


    @app.route("/clientes")
    @chefe_required
    def clientes_analytics():
        barbearia_id = bid()
        try:
            periodo = int(request.args.get("periodo", 0))
            if periodo not in _PERIODOS_VALIDOS:
                periodo = 0
        except (ValueError, TypeError):
            periodo = 0
        clientes     = db.analytics_clientes(barbearia_id, limite=100,
                                             periodo_dias=periodo or None)
        moeda_cod    = db.get_config("moeda", barbearia_id, "ECV") or "ECV"
        barbearia    = db.get_barbearia(barbearia_id)
        vocab        = get_vocab(barbearia.get("tipo") if barbearia else None,
                                 barbearia.get("vocab_custom") if barbearia else None)
        fid_ativo    = db.get_config("fidelidade_ativo", barbearia_id, "0") == "1"
        try:
            fid_target = int(db.get_config("fidelidade_visitas", barbearia_id, "10") or 10)
        except (ValueError, TypeError):
            fid_target = 10
        return render_template("clientes_analytics.html",
                               clientes=clientes,
                               moeda_simbolo=_MOEDA_MAP.get(moeda_cod, moeda_cod),
                               vocab=vocab,
                               fid_ativo=fid_ativo,
                               fid_target=fid_target,
                               periodo=periodo,
                               periodos=_PERIODOS_VALIDOS)


    @app.route("/clientes/<path:tel_enc>/fidelidade-reset", methods=["POST"])
    @chefe_required
    def cliente_fidelidade_reset(tel_enc):
        """Regista reset manual do ciclo de fidelidade para um cliente."""
        from urllib.parse import unquote
        barbearia_id = bid()
        telefone = _limpar(unquote(tel_enc), 20)
        if telefone:
            obs = _limpar(request.form.get("obs", ""), 200)
            db.fidelidade_reset(barbearia_id, telefone, obs or None)
            flash("✓ Ciclo de fidelidade reiniciado.", "sucesso")
        return redirect(url_for("clientes_analytics"))


    @app.route("/clientes-bloqueados")
    @chefe_required
    def clientes_bloqueados():
        barbearia_id = bid()
        bloqueados = db.clientes_bloqueados_listar(barbearia_id)
        return render_template("clientes_bloqueados.html", bloqueados=bloqueados)

    @app.route("/clientes/bloquear", methods=["POST"])
    @chefe_required
    def cliente_bloquear_post():
        barbearia_id = bid()
        tel    = _limpar(request.form.get("telefone", ""))
        motivo = _limpar(request.form.get("motivo", ""), 200)
        if not tel or not _TEL_RE.match(tel):
            flash("Número de telemóvel inválido.", "erro")
            return redirect(url_for("clientes_bloqueados"))
        tel_norm = _normalizar_tel(tel) or tel
        db.cliente_bloquear(barbearia_id, tel_norm, motivo)
        flash(f"✓ Número {tel_norm} bloqueado.", "sucesso")
        return redirect(url_for("clientes_bloqueados"))

    @app.route("/clientes/desbloquear/<int:id>", methods=["POST"])
    @chefe_required
    def cliente_desbloquear_post(id):
        db.cliente_desbloquear(id)
        flash("✓ Cliente desbloqueado.", "sucesso")
        return redirect(url_for("clientes_bloqueados"))
