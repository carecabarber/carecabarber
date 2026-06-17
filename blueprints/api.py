from datetime import datetime
from flask import request, session, jsonify
import database as db
from database import ST_EM_ANDAMENTO
from helpers import (
    _log, _blog, _agora, _api_ok,
    _pc_get, _pc_set, _pc_del, _invalidar_idx,
    staff_required, bid,
    _VAPID_PUBLIC_KEY, _val_data,
)


def register(app) -> None:

    @app.route("/api/push/vapid-public")
    def api_push_vapid_public():
        """Devolve a chave pública VAPID para o cliente subscrever."""
        return jsonify({"publicKey": _VAPID_PUBLIC_KEY})


    @app.route("/api/push/subscribe", methods=["POST"])
    @staff_required
    def api_push_subscribe():
        """Guarda subscripção Web Push do barbeiro autenticado."""
        data = request.get_json(silent=True) or {}
        endpoint = data.get("endpoint", "").strip()
        p256dh   = (data.get("keys") or {}).get("p256dh", "").strip()
        auth     = (data.get("keys") or {}).get("auth", "").strip()
        if not endpoint or not p256dh or not auth:
            return jsonify({"ok": False, "erro": "dados incompletos"}), 400
        db.push_guardar(session["user_id"], bid(), endpoint, p256dh, auth)
        return jsonify({"ok": True})


    @app.route("/api/push/unsubscribe", methods=["POST"])
    @staff_required
    def api_push_unsubscribe():
        """Remove subscripção Web Push do browser actual."""
        data = request.get_json(silent=True) or {}
        endpoint = data.get("endpoint", "").strip()
        if endpoint:
            db.push_remover(endpoint)
        return jsonify({"ok": True})


    @app.route("/api/tempo/<int:id>")
    def api_tempo(id):
        if not _api_ok(request.remote_addr or "?"):
            return jsonify({"segundos": 0, "estimado": 0, "em_atraso": False}), 429
        if "user_id" not in session:
            return jsonify({"segundos": 0, "estimado": 0, "em_atraso": False})
        ag = db.get_agendamento(id)
        if not ag or not ag["inicio"] or ag.get("barbearia_id") != session.get("barbearia_id"):
            return jsonify({"segundos": 0, "estimado": 0, "em_atraso": False})
        try:
            inicio = datetime.strptime(ag["inicio"], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return jsonify({"segundos": 0, "estimado": 0, "em_atraso": False})
        segundos = max(0, int((_agora() - inicio).total_seconds()))
        s        = db.servico_por_id(ag["servico_id"])
        estimado = (s["duracao_min"] * 60) if s else 0
        return jsonify({"segundos": segundos, "estimado": estimado, "em_atraso": segundos > estimado})


    @app.route("/api/slots")
    def api_slots():
        if not _api_ok(request.remote_addr or "?"):
            return jsonify({"erro": "Demasiados pedidos. Tenta novamente mais tarde."}), 429
        barbeiro_id  = request.args.get("barbeiro_id", type=int)
        data         = request.args.get("data","")
        sid          = request.args.get("servico_id", type=int)
        barbearia_id = session.get("barbearia_id")

        if not (barbeiro_id and data and sid):
            return jsonify([])
        if not _val_data(data):
            return jsonify([])
        barb = db.get_barbeiro(barbeiro_id)
        if not barb:
            return jsonify([])
        if barbearia_id:
            if barb.get("barbearia_id") != barbearia_id:
                _log(f"IDOR_SLOTS barbeiro={barbeiro_id} barbearia_sessao={barbearia_id}")
                return jsonify([])
        else:
            barbearia_id = barb.get("barbearia_id")
        s = db.servico_por_id(sid)
        if not s or s.get("barbearia_id") != barbearia_id:
            return jsonify([])

        slots = db.horarios_disponiveis(barbeiro_id, data, s["duracao_min"], barbearia_id)
        if session.get("role") == "cliente":
            tel = session.get("telefone","")
            if tel:
                minhas       = db.agendamentos_cliente_barbeiro_dia(tel, barbeiro_id, data, barbearia_id)
                horas_minhas = {a["data_hora"][11:16] for a in minhas}
                for slot in slots:
                    if slot["hora"] in horas_minhas:
                        slot["tipo"] = "minha_marcacao"
        return jsonify(slots)


    @app.route("/api/lembretes")
    def api_lembretes():
        if not _api_ok(request.remote_addr or "?"):
            return jsonify([]), 429
        if "user_id" not in session or session.get("role") == "cliente":
            return jsonify([])
        barbearia_id = bid()
        filtro_bid   = None if session.get("role") == "chefe" else session.get("user_id")
        try:
            _min = min(120, max(5, int(request.args.get("minutos", 30))))
        except (ValueError, TypeError):
            _min = 30
        _ck = f"lemb:{barbearia_id}:{filtro_bid}:{_min}"
        cached = _pc_get(_ck)
        if cached is not None:
            return jsonify(cached)
        proximos  = db.proximos_agendamentos(barbearia_id, minutos=_min, barbeiro_id=filtro_bid)
        sids      = list({a["servico_id"] for a in proximos})
        servicos_m = {s["id"]: s for s in db.listar_servicos(barbearia_id) if s["id"] in sids}
        resultado = []
        for a in proximos:
            s = servicos_m.get(a["servico_id"])
            try:
                hm = datetime.strptime(a["data_hora"], "%Y-%m-%d %H:%M:%S")
                minutos_ate = int((hm - _agora()).total_seconds() / 60)
            except (ValueError, TypeError):
                minutos_ate = 0
            resultado.append({
                "id":             a["id"],
                "cliente":        a["cliente"],
                "telefone":       a["telefone"] or "",
                "hora":           a["data_hora"][11:16],
                "servico":        s["nome"] if s else "—",
                "minutos_ate":    minutos_ate,
                "token_confirmar": a.get("token_confirmar") or "",
                "lembrete_wa_em": a.get("lembrete_wa_em") or "",
            })
        _pc_set(_ck, resultado, 20)
        return jsonify(resultado)


    @app.route("/api/marcar-lembrete/<int:id>", methods=["POST"])
    def api_marcar_lembrete(id):
        """Marca que o lembrete WA foi enviado para este agendamento."""
        if "user_id" not in session or session.get("role") == "cliente":
            return jsonify({"ok": False}), 403
        barbearia_id = bid()
        ok = db.marcar_lembrete_wa(id, barbearia_id)
        # Invalidar cache de lembretes para reflectir o novo estado
        filtro_bid = None if session.get("role") == "chefe" else session.get("user_id")
        for _min in (5, 10, 15, 20, 30, 60, 90, 120):
            _pc_del(f"lemb:{barbearia_id}:{filtro_bid}:{_min}")
        return jsonify({"ok": ok})


    @app.route("/api/meu-status")
    def api_meu_status():
        if not _api_ok(request.remote_addr or "?"):
            return jsonify([]), 429
        tel          = session.get("telefone","")
        barbearia_id = session.get("barbearia_id")
        if not tel or not barbearia_id:
            return jsonify([])
        agendamentos = db.listar_por_telefone(tel, barbearia_id)
        em_andamento = [a for a in agendamentos if a["status"] == ST_EM_ANDAMENTO]
        if not em_andamento:
            return jsonify([])
        sids  = list({a["servico_id"] for a in em_andamento if a.get("servico_id")})
        bids_ = list({a["barbeiro_id"] for a in em_andamento if a.get("barbeiro_id")})
        smap  = db.get_servicos_por_ids(sids)
        bmap  = db.get_barbeiros_por_ids(bids_)
        resultado = []
        for a in em_andamento:
            s = smap.get(a["servico_id"])
            b = bmap.get(a["barbeiro_id"])
            resultado.append({"id": a["id"],
                              "servico":  s["nome"] if s else "—",
                              "barbeiro": b["nome"] if b else "—"})
        return jsonify(resultado)


    @app.route("/api/novos-agendamentos")
    def api_novos_agendamentos():
        """Devolve agendamentos novos com id > desde_id (polling de notificações)."""
        if not _api_ok(request.remote_addr or "?"):
            return jsonify([]), 429
        if "user_id" not in session or session.get("role") == "cliente":
            return jsonify([])
        barbearia_id = bid()
        filtro_bid   = None if session.get("role") == "chefe" else session.get("user_id")
        try:
            desde_id = int(request.args.get("desde_id", 0))
        except (ValueError, TypeError):
            desde_id = 0
        _ck = f"novos:{barbearia_id}:{filtro_bid}"
        cached = _pc_get(_ck)
        if cached is not None:
            return jsonify([a for a in cached if a["id"] > desde_id])
        novos = db.novos_agendamentos(barbearia_id, 0, filtro_bid)
        _srvs = {s["id"]: s for s in db.listar_servicos(barbearia_id)}
        _barbs = {b["id"]: b for b in db.listar_barbeiros(barbearia_id, incluir_chefe=True)}
        todos = []
        for a in novos:
            s = _srvs.get(a["servico_id"])
            b = _barbs.get(a.get("barbeiro_id"))
            todos.append({
                "id":       a["id"],
                "cliente":  a["cliente"],
                "hora":     a["data_hora"][11:16],
                "servico":  s["nome"] if s else "—",
                "barbeiro": b["nome"] if b else "—",
                "tipo":     a.get("tipo", "agendado"),
            })
        _pc_set(_ck, todos, 15)
        return jsonify([a for a in todos if a["id"] > desde_id])


    @app.route("/api/estado")
    def api_estado():
        if not _api_ok(request.remote_addr or "?"):
            return jsonify({"h": ""}), 429
        if "user_id" not in session:
            return jsonify({"h": ""})
        role         = session.get("role")
        barbearia_id = session.get("barbearia_id")
        if role == "cliente":
            _ck = f"estado:cli:{barbearia_id}:{session.get('telefone','')}"
            cached = _pc_get(_ck)
            if cached is not None:
                return jsonify({"h": cached})
            h = db.estado_cliente(session.get("telefone",""), barbearia_id)
            _pc_set(_ck, h, 2)
        elif role == "chefe":
            _ck = f"estado:chefe:{barbearia_id}:"
            cached = _pc_get(_ck)
            if cached is not None:
                return jsonify({"h": cached})
            h = db.estado_hoje(barbearia_id)
            _pc_set(_ck, h, 2)
        else:
            uid = session.get("user_id")
            _ck = f"estado:barb:{barbearia_id}:{uid}"
            cached = _pc_get(_ck)
            if cached is not None:
                return jsonify({"h": cached})
            h = db.estado_hoje(barbearia_id, uid)
            _pc_set(_ck, h, 2)
        return jsonify({"h": h})


    @app.route("/api/cliente-push/subscribe", methods=["POST"])
    def api_cliente_push_subscribe():
        from helpers import _PUSH_OK
        # Auth antes de feature check: 401/403 têm prioridade sobre 503
        if session.get("role") != "cliente":
            return jsonify({"ok": False, "error": "Não autorizado"}), 403
        if not _PUSH_OK:
            return jsonify({"ok": False, "error": "Push não disponível"}), 503
        barbearia_id = session.get("barbearia_id")
        telefone = session.get("telefone", "")
        if not telefone or not barbearia_id:
            return jsonify({"ok": False, "error": "Sessão inválida"}), 400
        data = request.get_json(silent=True) or {}
        endpoint = data.get("endpoint", "")
        p256dh   = data.get("p256dh", "")
        auth_key = data.get("auth", "")
        if not endpoint or not p256dh or not auth_key:
            return jsonify({"ok": False, "error": "Dados incompletos"}), 400
        try:
            db.cliente_push_guardar(telefone, barbearia_id, endpoint, p256dh, auth_key)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500


    @app.route("/api/cliente-push/unsubscribe", methods=["POST"])
    def api_cliente_push_unsubscribe():
        if session.get("role") != "cliente":
            return jsonify({"ok": False}), 403
        data = request.get_json(silent=True) or {}
        endpoint = data.get("endpoint", "")
        if endpoint:
            try:
                db.cliente_push_remover(endpoint)
            except Exception as _e:
                import logging
                logging.getLogger("push").warning("cliente_push_remover falhou: %s", _e)
        return jsonify({"ok": True})


    @app.route("/api/spec")
    def api_spec():
        """Documentação programática das rotas da API (JSON)."""
        from flask import url_for as _uf
        routes = []
        for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
            if rule.rule.startswith("/static"):
                continue
            methods = sorted(m for m in rule.methods if m not in ("HEAD", "OPTIONS"))
            routes.append({
                "path":     rule.rule,
                "methods":  methods,
                "endpoint": rule.endpoint,
                "auth":     not rule.rule.startswith(("/cliente/", "/api/push/vapid",
                                                       "/healthz", "/login", "/offline",
                                                       "/manifest", "/sw.js", "/foto/",
                                                       "/avaliar-link/", "/reagendar-link/",
                                                       "/cancelar-link/", "/ag/")),
            })
        return jsonify({
            "version":     "1.0",
            "base_url":    request.host_url.rstrip("/"),
            "total_routes": len(routes),
            "docs":        "/docs/API.md",
            "routes":      routes,
        })
