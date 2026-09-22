"""Contrato de resposta JSON da API.

Todo endpoint JSON responde num destes dois formatos — nunca um dict solto:

    sucesso: {"data": <payload>, "meta": {...}}          # "meta" é opcional
    erro:    {"error": {"code": ..., "message": ..., "details": ...}}

As chaves do envelope são em inglês e estáveis (o frontend depende delas); as
mensagens são em português, voltadas ao usuário final.
"""

from __future__ import annotations

from typing import Any

from flask import jsonify


def ok(data: Any, meta: dict | None = None, status: int = 200) -> tuple:
    """Resposta de sucesso."""
    corpo: dict[str, Any] = {"data": data}
    if meta:
        corpo["meta"] = meta
    return jsonify(corpo), status


def criado(data: Any, meta: dict | None = None) -> tuple:
    """Resposta de criação (201)."""
    return ok(data, meta=meta, status=201)


def sem_conteudo() -> tuple:
    """Resposta sem corpo (204) — usada em exclusões."""
    return "", 204


def erro(
    code: str,
    message: str,
    *,
    details: Any = None,
    status: int = 400,
) -> tuple:
    """Resposta de erro. Normalmente não é chamada direto: levante um `ErroApp`
    (ver `app/shared/errors.py`) e deixe o handler da factory montar isto."""
    payload: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        payload["details"] = details
    return jsonify({"error": payload}), status
