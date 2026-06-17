import time
from flask import render_template, jsonify
import database as db
from helpers import _log


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
        """Serve o Service Worker a partir da raiz (/)."""
        response = app.send_static_file("sw.js")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Service-Worker-Allowed"] = "/"
        return response


    @app.route("/offline")
    def offline():
        """Página de fallback mostrada pelo Service Worker quando não há rede."""
        return render_template("offline.html")
