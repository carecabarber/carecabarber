from flask import render_template, request, redirect, url_for, session
from urllib.parse import quote_plus
import json
import database as db
from helpers import (
    _log, _blog, _limpar, _salvar_logo, _USER_RE, _MAX_USERNAME, root_required,
    VOCAB_TIPOS, get_vocab, MOEDAS, _MOEDA_MAP, _pc_del,
)


def register(app):

    @app.route("/root")
    @root_required
    def root_dashboard():
        barbearias = db.listar_barbearias()
        planos    = db.verificar_todos_planos()
        historico = db.listar_todos_pagamentos()
        erro = request.args.get("erro")
        ok   = request.args.get("ok")
        return render_template("root.html", barbearias=barbearias, planos=planos,
                               historico=historico, plano_exp=db.PLANO_EXP, erro=erro, ok=ok)


    @app.route("/root/criar", methods=["POST"])
    @root_required
    def root_criar_barbearia():
        nome       = _limpar(request.form.get("nome",""))
        chefe_nome = _limpar(request.form.get("chefe_nome",""))
        username   = _limpar(request.form.get("username",""), _MAX_USERNAME).lower()
        senha      = request.form.get("senha","").strip()

        if not nome or not chefe_nome or not username or not senha:
            return redirect(url_for("root_dashboard") + "?erro=" + quote_plus("Preenche todos os campos."))
        if not _USER_RE.match(username):
            return redirect(url_for("root_dashboard") + "?erro=" + quote_plus("Username inválido. Usa apenas letras, números, _ ou ."))
        if len(senha) < 6:
            return redirect(url_for("root_dashboard") + "?erro=" + quote_plus("Senha deve ter pelo menos 6 caracteres."))
        if db.username_existe(username):
            return redirect(url_for("root_dashboard") + "?erro=" + quote_plus("Username já existe, escolhe outro."))

        tipo = request.form.get("tipo", "barbearia").strip()
        vocab_custom_json = None
        if tipo == 'outro':
            vc = {
                'tipo_label':   _limpar(request.form.get("outro_tipo_label", "")).strip() or "Outro",
                'profissional': _limpar(request.form.get("outro_profissional", "")).strip() or "Profissional",
                'servico':      _limpar(request.form.get("outro_servico", "")).strip() or "Serviço",
                'agendamento':  _limpar(request.form.get("outro_agendamento", "")).strip() or "Marcação",
            }
            vocab_custom_json = json.dumps(vc, ensure_ascii=False)
        barbearia_id = db.criar_barbearia(nome, tipo=tipo, vocab_custom_json=vocab_custom_json)
        db.criar_chefe(chefe_nome, username, senha, barbearia_id)
        logo = request.files.get("logo")
        if logo and logo.filename:
            filename = _salvar_logo(logo, barbearia_id)
            if filename:
                db.set_logo(barbearia_id, filename)
        vc_parsed = json.loads(vocab_custom_json) if vocab_custom_json else {}
        tipo_label = vc_parsed.get('tipo_label') or VOCAB_TIPOS.get(tipo, VOCAB_TIPOS['barbearia'])['tipo_label']
        return redirect(url_for("root_dashboard") + "?ok=" + quote_plus(f"{tipo_label} «{nome}» criada com sucesso!"))


    @app.route("/root/toggle/<int:id>", methods=["POST"])
    @root_required
    def root_toggle_barbearia(id):
        db.toggle_barbearia(id)
        return redirect(url_for("root_dashboard"))


    @app.route("/root/editar/<int:id>", methods=["POST"])
    @root_required
    def root_editar_barbearia(id):
        nome = _limpar(request.form.get("nome",""))
        if nome:
            db.editar_barbearia(id, nome)
        tipo = request.form.get("tipo", "").strip()
        if tipo and tipo in db._TIPOS_VALIDOS:
            db.set_tipo_barbearia(id, tipo)
        if tipo == 'outro':
            vc = {
                'tipo_label':   _limpar(request.form.get("outro_tipo_label", "")).strip() or "Outro",
                'profissional': _limpar(request.form.get("outro_profissional", "")).strip() or "Profissional",
                'servico':      _limpar(request.form.get("outro_servico", "")).strip() or "Serviço",
                'agendamento':  _limpar(request.form.get("outro_agendamento", "")).strip() or "Marcação",
            }
            db.set_vocab_custom(id, json.dumps(vc, ensure_ascii=False))
        else:
            db.set_vocab_custom(id, None)
        return redirect(url_for("root_dashboard"))


    @app.route("/root/logo/<int:id>", methods=["POST"])
    @root_required
    def root_logo_barbearia(id):
        logo = request.files.get("logo")
        if logo and logo.filename:
            filename = _salvar_logo(logo, id)
            if filename:
                db.set_logo(id, filename)
        return redirect(url_for("root_dashboard"))


    @app.route("/root/alterar-senha", methods=["POST"])
    @root_required
    def root_alterar_senha():
        atual    = request.form.get("senha_atual","")
        nova     = request.form.get("senha_nova","")
        confirma = request.form.get("senha_confirma","")
        root     = db.get_barbeiro(session["user_id"])
        if not db.verificar_senha(root, atual):
            return redirect(url_for("root_dashboard") + "?erro=" + quote_plus("Senha atual incorreta."))
        if nova != confirma:
            return redirect(url_for("root_dashboard") + "?erro=" + quote_plus("As novas senhas não coincidem."))
        if len(nova) < 6:
            return redirect(url_for("root_dashboard") + "?erro=" + quote_plus("Senha deve ter pelo menos 6 caracteres."))
        db.alterar_senha(session["user_id"], nova)
        return redirect(url_for("root_dashboard") + "?ok=" + quote_plus("Senha alterada com sucesso."))


    @app.route("/root/gerir/<int:id>", methods=["POST"])
    @root_required
    def root_gerir_barbearia(id):
        """Impersonação de barbearia pelo root."""
        barbearia = db.get_barbearia(id)
        if not barbearia:
            return redirect(url_for("root_dashboard"))
        session["barbearia_id"] = id
        session["role"]         = "chefe"
        session["root_gerir"]   = True
        return redirect(url_for("index"))


    @app.route("/root/planos/<int:id>")
    @root_required
    def root_planos_barbearia(id):
        barbearia = db.get_barbearia(id)
        if not barbearia:
            return redirect(url_for("root_dashboard"))
        plano     = db.verificar_plano(id)
        historico = db.listar_pagamentos(id)
        planos_def = db.PLANOS
        plano_exp  = db.PLANO_EXP
        precos_b, moeda_b = db.get_planos_precos_barbearia(id)
        erro = request.args.get("erro")
        ok   = request.args.get("ok")
        return render_template("root_planos.html", b=barbearia, plano=plano,
                               historico=historico, planos_def=planos_def,
                               precos=precos_b, moeda_b=moeda_b,
                               moedas=MOEDAS,
                               plano_exp=plano_exp, erro=erro, ok=ok)


    @app.route("/root/barbearia/<int:id>/precos", methods=["POST"])
    @root_required
    def root_precos_barbearia(id):
        """Guarda preços e moeda específicos para uma barbearia."""
        barbearia = db.get_barbearia(id)
        if not barbearia:
            return redirect(url_for("root_dashboard"))
        moeda = request.form.get("moeda", "ECV").strip()
        if moeda not in _MOEDA_MAP:
            moeda = "ECV"
        for codigo in db.PLANOS:
            val = request.form.get(f"preco_{codigo}", "0").strip()
            if val.isdigit():
                db.set_plano_preco_barbearia(id, codigo, int(val), moeda)
        destino = url_for("root_planos_barbearia", id=id)
        return redirect(destino + "?ok=" + quote_plus("Preços actualizados."))


    def _next_seguro():
        """Devolve URL de retorno seguro."""
        nxt = request.form.get("_next", "")
        if nxt.startswith("/root/planos/"):
            return nxt.split("?")[0]
        return url_for("root_dashboard")


    @app.route("/root/pagamento/<int:id>", methods=["POST"])
    @root_required
    def root_registar_pagamento(id):
        codigo  = request.form.get("plano", "1m")
        destino = _next_seguro()
        res = db.registar_pagamento(id, codigo_plano=codigo)
        if not res:
            return redirect(destino + "?erro=" + quote_plus("Plano ou barbearia inválida."))
        if res.get("erro") == "plano_ativo":
            ate = res["expira_em"]
            ate_txt = "sem prazo" if ate is None else f"até {ate}"
            return redirect(destino + "?erro=" + quote_plus(
                f"Já existe um plano activo ({ate_txt}). Cancela primeiro para registar um novo."))
        _pc_del(f"plano:{id}:")
        expira_txt = "sem prazo" if res["expira_em"] == "9999-12-31" else f"Válido até {res['expira_em']}"
        return redirect(destino + "?ok=" + quote_plus(
            f"Plano {res['nome_plano']} registado. {expira_txt}."))


    @app.route("/root/cancelar-plano/<int:id>", methods=["POST"])
    @root_required
    def root_cancelar_plano(id):
        db.cancelar_plano(id)
        _pc_del(f"plano:{id}:")
        b = db.get_barbearia(id)
        nome = b["nome"] if b else f"ID {id}"
        destino = _next_seguro()
        return redirect(destino + "?ok=" + quote_plus(f"Plano de «{nome}» cancelado."))


    @app.route("/root/precos")
    @root_required
    def root_precos():
        planos_def = db.PLANOS
        precos     = db.get_planos_precos()
        erro = request.args.get("erro")
        ok   = request.args.get("ok")
        return render_template("root_precos.html", planos_def=planos_def, precos=precos, erro=erro, ok=ok)


    @app.route("/root/planos/precos", methods=["POST"])
    @root_required
    def root_definir_precos():
        for codigo in db.PLANOS:
            val = request.form.get(f"preco_{codigo}", "0").strip()
            if val.isdigit():
                db.set_plano_preco(codigo, int(val))
        destino = _next_seguro()
        return redirect(destino + "?ok=" + quote_plus("Preços actualizados."))


    @app.route("/conta-suspensa")
    def conta_suspensa():
        """Página mostrada a staff de barbearia com plano expirado."""
        barbearia_id = session.get("barbearia_id")
        plano = db.verificar_plano(barbearia_id) if barbearia_id else None
        return render_template("conta_suspensa.html", plano=plano), 402


    @app.route("/root/sair-barbearia", methods=["POST"])
    def root_sair_barbearia():
        if "user_id" not in session or (
                session.get("role") != "root" and not session.get("root_gerir")):
            return redirect(url_for("login"))
        session["role"] = "root"
        session.pop("barbearia_id", None)
        session.pop("root_gerir", None)
        return redirect(url_for("root_dashboard"))
