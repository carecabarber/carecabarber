import base64
from flask import render_template, request, redirect, url_for, session, flash, Response, jsonify
import database as db
from helpers import (
    _log, _blog, _agora, _limpar, _val_data, _val_hora,
    _invalidar_idx, _pc_del, _api_ok,
    staff_required, chefe_required, bid,
    get_vocab, _USER_RE, _MAX_USERNAME, _MAX_MOTIVO, _HORA_RE,
    _validar_imagem, _FOTO_MIME_OK, _FOTO_MAX_BYTES,
    _html_escape,
)


def register(app):

    @app.route("/barbeiros", methods=["GET","POST"])
    @chefe_required
    def barbeiros():
        barbearia_id = bid()
        if request.method == "POST":
            nome = _limpar(request.form.get("nome",""))
            if nome:
                db.criar_barbeiro(nome, barbearia_id)
                _bb = db.get_barbearia(barbearia_id) or {}
                _vb = get_vocab(_bb.get("tipo"), _bb.get("vocab_custom"))
                flash(f"✓ {_vb.get('profissional','Barbeiro')} «{nome}» criado com sucesso!", "sucesso")
            return redirect(url_for("barbeiros"))
        return render_template("barbeiros.html",
                               barbeiros=db.listar_barbeiros(barbearia_id, apenas_ativos=False, incluir_chefe=True),
                               todos_barbeiros=db.listar_barbeiros(barbearia_id, apenas_ativos=False, incluir_chefe=True),
                               ausencias=db.listar_ausencias(barbearia_id),
                               hoje=_agora().strftime("%Y-%m-%d"))


    @app.route("/barbeiros/toggle/<int:id>", methods=["POST"])
    @chefe_required
    def toggle_barbeiro(id):
        barbearia_id = bid()
        b = db.get_barbeiro(id)
        if not b or b.get("barbearia_id") != barbearia_id:
            return redirect(url_for("barbeiros"))
        if id == session.get("user_id"):
            flash("⚠️ Não podes desativar a tua própria conta.", "erro")
            return redirect(url_for("barbeiros"))
        if b.get("ativo"):
            hoje_str = _agora().strftime("%Y-%m-%d")
            n_futuros = db.contar_agendamentos_futuros_barbeiro(id, hoje_str)
            if n_futuros > 0:
                flash(f"⚠️ {b['nome']} tem {n_futuros} marcação(ões) futuras. "
                      f"Reagenda-as antes de desativar.", "erro")
                return redirect(url_for("barbeiros"))
        db.toggle_barbeiro(id)
        return redirect(url_for("barbeiros"))


    @app.route("/barbeiros/apagar/<int:id>", methods=["POST"])
    @chefe_required
    def apagar_barbeiro(id):
        barbearia_id = bid()
        b = db.get_barbeiro(id)
        if not b or b.get("barbearia_id") != barbearia_id:
            return redirect(url_for("barbeiros"))
        if id == session.get("user_id"):
            flash("⚠️ Não podes apagar a tua própria conta.", "erro")
            return redirect(url_for("barbeiros"))
        if b.get("role") == "chefe":
            outros_chefes = db.contar_chefes_ativos(barbearia_id, excluir_id=id)
            if outros_chefes == 0:
                flash("⚠️ Não podes apagar o único chefe da barbearia.", "erro")
                return redirect(url_for("barbeiros"))
        hoje_str = _agora(barbearia_id).strftime("%Y-%m-%d")
        n_futuros = db.contar_agendamentos_futuros_barbeiro(id, hoje_str)
        if n_futuros > 0:
            flash(f"⚠️ {b['nome']} tem {n_futuros} marcação(ões) futuras. Reagenda-as antes de apagar.", "erro")
            return redirect(url_for("barbeiros"))
        resultado = db.apagar_barbeiro(id, barbearia_id)
        if resultado == "soft":
            flash(f"✓ {b['nome']} removido (histórico preservado).", "sucesso")
        else:
            flash(f"✓ {b['nome']} apagado.", "sucesso")
        _pc_del(f"barbeiros:{barbearia_id}:")
        return redirect(url_for("barbeiros"))


    @app.route("/barbeiros/editar/<int:id>", methods=["POST"])
    @chefe_required
    def editar_barbeiro(id):
        nome = _limpar(request.form.get("nome",""))
        b    = db.get_barbeiro(id)
        if nome and b and b.get("barbearia_id") == bid():
            db.editar_barbeiro(id, nome, barbearia_id=bid())
        return redirect(url_for("barbeiros"))


    @app.route("/barbeiros/repor-senha/<int:id>", methods=["POST"])
    @chefe_required
    def repor_senha_barbeiro(id):
        senha = request.form.get("senha","").strip()
        b     = db.get_barbeiro(id)
        if not (b and b.get("barbearia_id") == bid()):
            return redirect(url_for("barbeiros"))
        if not senha or len(senha) < 6:
            flash("⚠️ A senha deve ter pelo menos 6 caracteres.", "erro")
            return redirect(url_for("barbeiros"))
        db.repor_senha_barbeiro(id, senha)
        flash(f"✓ Senha de {b['nome']} reposta com sucesso.", "sucesso")
        return redirect(url_for("barbeiros"))


    @app.route("/barbeiros/credenciais/<int:id>", methods=["POST"])
    @chefe_required
    def set_credenciais(id):
        username = _limpar(request.form.get("username",""), _MAX_USERNAME).lower()
        senha    = request.form.get("senha","").strip()
        b        = db.get_barbeiro(id)
        if not (b and b.get("barbearia_id") == bid()):
            return redirect(url_for("barbeiros"))
        if not username or not _USER_RE.match(username):
            return redirect(url_for("barbeiros") + "?erro=username_invalido")
        if not senha or len(senha) < 6:
            return redirect(url_for("barbeiros") + "?erro=senha_curta")
        ok = db.set_credenciais(id, username, senha)
        if not ok:
            return redirect(url_for("barbeiros") + "?erro=username_duplicado")
        return redirect(url_for("barbeiros"))


    @app.route("/barbeiros/<int:id>/foto", methods=["POST"])
    @app.csrf.exempt
    @chefe_required
    def barbeiro_foto_upload(id):
        """Chefe faz upload da foto de qualquer barbeiro da sua barbearia."""
        b = db.get_barbeiro(id)
        if not b or b.get("barbearia_id") != bid():
            return jsonify({"ok": False, "erro": "Barbeiro não encontrado."}), 404

        if request.is_json:
            data = request.get_json(silent=True) or {}
            b64  = data.get("imagem", "")
            mime = data.get("mime", "image/jpeg")
            if not b64 or mime not in _FOTO_MIME_OK:
                return jsonify({"ok": False, "erro": "Formato inválido."}), 400
            try:
                raw = base64.b64decode(b64)
            except Exception:
                return jsonify({"ok": False, "erro": "Dados corrompidos."}), 400
            if len(raw) > _FOTO_MAX_BYTES:
                return jsonify({"ok": False, "erro": "Foto demasiado grande (máx 2 MB)."}), 413
            if not _validar_imagem(raw, mime):
                return jsonify({"ok": False, "erro": "O ficheiro não é uma imagem válida."}), 415
            db.guardar_foto_perfil(id, raw, mime)
            return jsonify({"ok": True})

        f = request.files.get("foto")
        if not f or not f.filename:
            return jsonify({"ok": False, "erro": "Nenhum ficheiro recebido."}), 400
        mime = f.content_type or "image/jpeg"
        if mime not in _FOTO_MIME_OK:
            return jsonify({"ok": False, "erro": "Formato não suportado. Usa JPEG, PNG ou WebP."}), 415
        raw = f.read(_FOTO_MAX_BYTES + 1)
        if len(raw) > _FOTO_MAX_BYTES:
            return jsonify({"ok": False, "erro": "Foto demasiado grande (máx 2 MB)."}), 413
        if not _validar_imagem(raw, mime):
            return jsonify({"ok": False, "erro": "O ficheiro não é uma imagem válida."}), 415
        db.guardar_foto_perfil(id, raw, mime)
        return jsonify({"ok": True})


    @app.route("/barbeiros/<int:id>/foto/apagar", methods=["POST"])
    @app.csrf.exempt
    @chefe_required
    def barbeiro_foto_apagar(id):
        """Chefe remove a foto de um barbeiro da sua barbearia."""
        b = db.get_barbeiro(id)
        if not b or b.get("barbearia_id") != bid():
            return jsonify({"ok": False, "erro": "Barbeiro não encontrado."}), 404
        db.apagar_foto_perfil(id)
        return jsonify({"ok": True})


    @app.route("/barbeiros/ausencia", methods=["POST"])
    @chefe_required
    def criar_ausencia():
        try:
            barbeiro_id = int(request.form.get("barbeiro_id", 0))
        except (ValueError, TypeError):
            barbeiro_id = 0
        b = db.get_barbeiro(barbeiro_id) if barbeiro_id else None
        if not b or b.get("barbearia_id") != bid():
            return redirect(url_for("barbeiros"))
        data_inicio = _limpar(request.form.get("data_inicio",""), 10)
        data_fim    = _limpar(request.form.get("data_fim",""), 10)
        if barbeiro_id and _val_data(data_inicio) and _val_data(data_fim):
            if data_inicio > data_fim:
                flash("⚠️ A data de início deve ser anterior ou igual à data de fim.", "erro")
                return redirect(url_for("barbeiros"))
            hora_inicio = request.form.get("hora_inicio") or None
            hora_fim    = request.form.get("hora_fim")    or None
            if bool(hora_inicio) != bool(hora_fim):
                hora_inicio = hora_fim = None
            elif hora_inicio and hora_fim:
                if not _HORA_RE.match(hora_inicio) or not _HORA_RE.match(hora_fim):
                    hora_inicio = hora_fim = None
                elif hora_inicio >= hora_fim:
                    flash("⚠️ A hora de início deve ser anterior à hora de fim.", "erro")
                    return redirect(url_for("barbeiros"))
            db.criar_ausencia(
                barbeiro_id=barbeiro_id, data_inicio=data_inicio, data_fim=data_fim,
                tipo=request.form.get("tipo","falta"),
                motivo=_limpar(request.form.get("motivo",""), _MAX_MOTIVO),
                hora_inicio=hora_inicio,
                hora_fim=hora_fim,
            )
            db.invalidar_cache_slots(bid())
            _invalidar_idx(bid())
        return redirect(url_for("barbeiros"))


    @app.route("/barbeiros/ausencia/apagar/<int:id>", methods=["POST"])
    @chefe_required
    def apagar_ausencia(id):
        barbearia_id = bid()
        ausencias = db.listar_ausencias(barbearia_id)
        if any(a["id"] == id for a in ausencias):
            db.apagar_ausencia(id)
            db.invalidar_cache_slots(barbearia_id)
            _invalidar_idx(barbearia_id)
        return redirect(url_for("barbeiros"))


    @app.route("/perfil", methods=["GET","POST"])
    @staff_required
    def perfil():
        if session.get("root_gerir"):
            flash("⚠️ Em modo root não é possível editar o perfil.", "erro")
            return redirect(url_for("index"))
        erro, ok = None, None
        if request.method == "POST":
            atual    = request.form.get("senha_atual","")
            nova     = request.form.get("senha_nova","")
            confirma = request.form.get("senha_confirma","")
            barb     = db.get_barbeiro(session["user_id"])
            if not barb or not barb.get("username"):
                erro = "Sem credenciais definidas. Pede ao chefe para configurar o teu acesso."
            else:
                if not db.verificar_senha(barb, atual):
                    erro = "Senha atual incorreta."
                elif nova != confirma:
                    erro = "As novas senhas não coincidem."
                elif len(nova) < 6:
                    erro = "A senha deve ter pelo menos 6 caracteres."
                else:
                    db.alterar_senha(session["user_id"], nova)
                    ok = "Senha alterada com sucesso."
        credenciais  = []
        barb_atual   = db.get_barbeiro(session["user_id"])
        mesa_token   = barb_atual.get("mesa_token") if barb_atual else None
        tem_foto     = bool(barb_atual and barb_atual.get("foto_perfil"))
        return render_template("perfil.html", erro=erro, ok=ok,
                               credenciais=credenciais, mesa_token=mesa_token,
                               barbeiro=barb_atual, tem_foto=tem_foto)


    @app.route("/perfil/foto", methods=["POST"])
    @app.csrf.exempt
    @staff_required
    def perfil_foto_upload():
        """Recebe a foto de perfil (ficheiro ou base64 da câmara)."""
        if session.get("root_gerir"):
            return jsonify({"ok": False, "erro": "Operação não disponível em modo root."}), 403
        uid = session["user_id"]

        if request.is_json:
            data = request.get_json(silent=True) or {}
            b64  = data.get("imagem", "")
            mime = data.get("mime", "image/jpeg")
            if not b64 or mime not in _FOTO_MIME_OK:
                return jsonify({"ok": False, "erro": "Formato inválido."}), 400
            try:
                raw = base64.b64decode(b64)
            except Exception:
                return jsonify({"ok": False, "erro": "Dados corrompidos."}), 400
            if len(raw) > _FOTO_MAX_BYTES:
                return jsonify({"ok": False, "erro": "Foto demasiado grande (máx 2 MB)."}), 413
            if not _validar_imagem(raw, mime):
                return jsonify({"ok": False, "erro": "O ficheiro não é uma imagem válida."}), 415
            db.guardar_foto_perfil(uid, raw, mime)
            return jsonify({"ok": True})

        f = request.files.get("foto")
        if not f or not f.filename:
            return jsonify({"ok": False, "erro": "Nenhum ficheiro recebido."}), 400
        mime = f.content_type or "image/jpeg"
        if mime not in _FOTO_MIME_OK:
            return jsonify({"ok": False, "erro": "Formato não suportado. Usa JPEG, PNG ou WebP."}), 415
        raw = f.read(_FOTO_MAX_BYTES + 1)
        if len(raw) > _FOTO_MAX_BYTES:
            return jsonify({"ok": False, "erro": "Foto demasiado grande (máx 2 MB)."}), 413
        if not _validar_imagem(raw, mime):
            return jsonify({"ok": False, "erro": "O ficheiro não é uma imagem válida."}), 415
        db.guardar_foto_perfil(uid, raw, mime)
        return jsonify({"ok": True})


    @app.route("/perfil/foto/apagar", methods=["POST"])
    @app.csrf.exempt
    @staff_required
    def perfil_foto_apagar():
        """Remove a foto de perfil do utilizador actual."""
        if session.get("root_gerir"):
            return jsonify({"ok": False, "erro": "Operação não disponível em modo root."}), 403
        db.apagar_foto_perfil(session["user_id"])
        return jsonify({"ok": True})


    @app.route("/foto/<int:barbeiro_id>")
    def foto_barbeiro(barbeiro_id):
        """Serve a foto de perfil de um barbeiro."""
        if not _api_ok(request.remote_addr or "?"):
            return Response("", status=429)
        barb = db.get_barbeiro(barbeiro_id)
        if not barb:
            return Response("", status=404)
        _bb = db.get_barbearia(barb.get("barbearia_id"))
        if not _bb or not _bb.get("ativa"):
            return Response("", status=404)
        dados, mime = db.get_foto_perfil(barbeiro_id)
        if not dados:
            inicial = _html_escape(barb["nome"][0].upper() if barb.get("nome") else "?")
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80" viewBox="0 0 80 80">'
                f'<rect width="80" height="80" rx="40" fill="#2a2a2a"/>'
                f'<text x="50%" y="54%" dominant-baseline="middle" text-anchor="middle" '
                f'fill="#e0e0e0" font-size="36" font-family="sans-serif" font-weight="700">{inicial}</text>'
                f'</svg>'
            )
            return Response(svg, mimetype="image/svg+xml",
                            headers={"Cache-Control": "no-store"})
        return Response(dados, mimetype=mime,
                        headers={"Cache-Control": "private, max-age=3600"})
