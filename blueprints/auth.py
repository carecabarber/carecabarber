from flask import render_template, request, redirect, url_for, session, make_response
import database as db
from helpers import (
    _log, _ip_ok, _ip_retry_after, _record_fail, _clear_fails, _user_locked,
    _DUMMY_HASH, _USER_RE, _blog, _limpar, _MAX_USERNAME,
)
from werkzeug.security import check_password_hash


def register(app):

    @app.route("/login", methods=["GET","POST"])
    def login():
        erro = None
        if request.method == "POST":
            ip       = request.remote_addr or "unknown"
            username = _limpar(request.form.get("username",""), _MAX_USERNAME)
            senha    = request.form.get("senha","")

            # 1. Verificar rate limit por IP (com backoff exponencial)
            if not _ip_ok(ip):
                _log(f"RATE_LIMIT_IP username={username}")
                retry_after = _ip_retry_after(ip)
                if retry_after > 60:
                    minutos = (retry_after + 59) // 60
                    erro = f"Demasiadas tentativas. Aguarda {minutos} minuto(s) e tenta novamente."
                else:
                    erro = "Demasiadas tentativas. Aguarda uns momentos e tenta novamente."
                resp = make_response(render_template("login.html", erro=erro))
                if retry_after > 0:
                    resp.headers["Retry-After"] = str(retry_after)
                return resp

            # 2. Verificar bloqueio por username
            elif _user_locked(username):
                _log(f"USER_LOCKED username={username}")
                erro = "Utilizador ou senha incorretos."

            else:
                staff = db.get_barbeiro_por_username(username)

                # Mitigação de timing attack
                if staff:
                    senha_ok = db.verificar_senha(staff, senha)
                else:
                    check_password_hash(_DUMMY_HASH, senha)  # consumir tempo igual
                    senha_ok = False

                if senha_ok:
                    _clear_fails(username)
                    session.clear()             # elimina sessão anterior
                    session.permanent = True
                    session.update({
                        "user_id":      staff["id"],
                        "user_nome":    staff["nome"],
                        "role":         staff["role"],
                        "barbearia_id": staff["barbearia_id"],
                    })
                    _blog("LOGIN_OK", bid=staff["barbearia_id"], uid=staff["id"], role=staff["role"])
                    if staff["role"] == "root":
                        return redirect(url_for("root_dashboard"))
                    return redirect(url_for("index"))
                else:
                    _record_fail(username)
                    _u_log = (username[:6] + "…") if len(username) > 6 else "***"
                    _log(f"LOGIN_FAIL username_prefix={_u_log}")
                    erro = "Utilizador ou senha incorretos."

        return render_template("login.html", erro=erro)


    @app.route("/logout", methods=["POST"])
    def logout():
        era_cliente  = session.get("role") == "cliente"
        barbearia_id = session.get("barbearia_id")
        session.clear()
        if era_cliente and barbearia_id:
            barbearia = db.get_barbearia(barbearia_id)
            if barbearia and barbearia.get("slug"):
                return redirect(url_for("cliente_entrada", slug=barbearia["slug"]))
        return redirect(url_for("login"))
