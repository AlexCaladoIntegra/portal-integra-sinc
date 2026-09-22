"""Sondas de saúde.

    /health/live   o processo está de pé? Não toca em dependência nenhuma.
    /health        as duas pontas respondem? Abre conexão com as duas.

A separação é a de sempre: uma sonda de vida que dependa do banco derruba o
processo quando o banco cai, que é justamente quando ele precisa continuar de
pé para relatar o problema.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from . import services

health_bp = Blueprint("health", __name__, url_prefix="/health")


@health_bp.get("/live")
def live():
    """Vivo. Sem I/O, sempre 200 enquanto o processo responder."""
    return jsonify(
        {
            "data": {
                "status": "live",
                "app": current_app.config["APP_NAME"],
                "versao": current_app.config["APP_VERSION"],
            }
        }
    )


@health_bp.get("")
@health_bp.get("/")
def health():
    """Diagnóstico completo.

    Responde **200 mesmo degradado**, com o detalhe no corpo. O status HTTP
    diria só "algo está errado"; o corpo diz qual das três pontas e o que
    fazer. Quem monitora olha `data.pronto`.
    """
    return jsonify({"data": services.diagnosticar()})
