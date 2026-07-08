import os
import re
import time
from flask import render_template, jsonify, Response
import database as db
from helpers import _log

# Cache do corpo do service worker por versão de deploy (evita reler/reescrever
# o ficheiro em cada pedido — só recompõe quando version.txt muda).
_SW_CACHE = {"ver": None, "body": None}


def register(app, app_start_ref: float, indices_prontos_ref: object) -> None:
    """
    app_start_ref: reference to _APP_START float
    indices_prontos_ref: reference to _indices_prontos threading.Event
    """

    @app.route("/healthz")
    def healthz():
        """Endpoint de health check.
        Retorna sempre 200 se a app está viva — db_ok=False indica DB inacessível
        neste worker (normal com locking_mode=EXCLUSIVE e múltiplos workers)."""
        db_ok = False
        db_msg = None
        try:
            with db._read() as _hc:
                _hc.execute("SELECT 1").fetchone()
            db_ok = True
        except Exception as e:
            db_msg = str(e)
        return jsonify({
            "status":   "ok",
            "db":       db_ok,
            "db_msg":   db_msg,
            "indices":  indices_prontos_ref.is_set(),
            "uptime_s": int(time.monotonic() - app_start_ref),
            "sentry":   bool(app.config.get("SENTRY_ATIVO")),
        }), 200


    @app.route("/manifest.json")
    def pwa_manifest():
        """Serve o manifest.json com Content-Type correcto para PWA."""
        response = app.send_static_file("manifest.json")
        response.headers["Content-Type"] = "application/manifest+json"
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response


    @app.route("/sw.js")
    def service_worker():
        """Serve o Service Worker a partir da raiz (/), com APP_VERSION ligado
        ao número de deploy (version.txt).

        Porquê: o cache estático do SW tem a chave `cb-static-<APP_VERSION>` e o
        handler `activate` só apaga caches cuja chave != versão actual. Se o
        APP_VERSION fosse fixo ('v14'), o cache NUNCA era invalidado → após cada
        deploy o SW servia JS/CSS obsoletos (cache-first) com HTML novo
        (network-first) → "JS antigo + HTML novo" = freeze recorrente do PWA.
        Injectando o nº de deploy, o SW muda a cada deploy → activate limpa o
        cache antigo → assets sempre frescos.
        """
        try:
            with open(os.path.join(app.root_path, "version.txt")) as _vf:
                _ver = (_vf.read().strip() or "0")
        except Exception:
            _ver = "0"

        if _SW_CACHE["ver"] != _ver or _SW_CACHE["body"] is None:
            try:
                with open(os.path.join(app.static_folder, "sw.js"),
                          encoding="utf-8") as _sf:
                    _src = _sf.read()
                _src = re.sub(
                    r"const APP_VERSION\s*=\s*'[^']*';",
                    "const APP_VERSION    = 'd%s';" % _ver,
                    _src, count=1)
                _SW_CACHE["ver"] = _ver
                _SW_CACHE["body"] = _src
            except Exception:
                # Fallback seguro: servir o ficheiro estático tal como está
                response = app.send_static_file("sw.js")
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Service-Worker-Allowed"] = "/"
                return response

        response = Response(_SW_CACHE["body"], mimetype="application/javascript")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Service-Worker-Allowed"] = "/"
        return response


    @app.route("/offline")
    def offline():
        """Página de fallback mostrada pelo Service Worker quando não há rede."""
        return render_template("offline.html")
