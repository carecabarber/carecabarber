from flask import render_template, request, redirect, url_for, session, flash
from zoneinfo import ZoneInfo
import database as db
from helpers import (
    _log, _blog, _agora, _limpar, _val_data, _val_hora,
    _invalidar_idx, chefe_required, bid,
    MOEDAS, _MOEDA_MAP, DIAS_PT, _HORA_RE, _MAX_MOTIVO,
    _pc_del,
)


def register(app):

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
            if nome:
                db.criar_servico(nome, dur, barbearia_id, preco)
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
        if nome:
            db.atualizar_servico(id, nome, dur, preco, barbearia_id=s["barbearia_id"])
            db.invalidar_cache_slots(s["barbearia_id"])
            _invalidar_idx(s["barbearia_id"])
            flash(f"✓ Serviço «{nome}» atualizado!", "sucesso")
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
                db.set_config("buffer_minutos", buf, barbearia_id)
                db.set_config("max_por_dia",    mpd, barbearia_id)
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
