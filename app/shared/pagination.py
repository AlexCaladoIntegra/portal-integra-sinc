"""Paginação das listagens.

Regra do projeto: **toda listagem é paginada**. Nenhum endpoint devolve uma
coleção inteira — nem "porque hoje são poucos registros".

Uso na rota:

    pag = Paginacao.de_request()
    itens, total = ClienteRepository().listar(limit=pag.limit, offset=pag.offset)
    return ok(itens, meta=pag.meta(total))
"""

from __future__ import annotations

from dataclasses import dataclass

from flask import request

from .errors import ErroValidacao

LIMITE_PADRAO = 50
LIMITE_MAXIMO = 200


@dataclass(frozen=True)
class Paginacao:
    limit: int = LIMITE_PADRAO
    offset: int = 0

    @classmethod
    def de_request(cls, args=None) -> Paginacao:
        """Lê `limit`/`offset` da query string.

        Valor não numérico ou negativo é erro de validação (422) — silenciar
        esconde bug de frontend. `limit` acima de LIMITE_MAXIMO é reduzido ao
        máximo, porque o cliente pedir demais não é erro, é excesso de ambição.
        """
        args = args if args is not None else request.args
        limit = _inteiro(args.get("limit"), "limit", LIMITE_PADRAO)
        offset = _inteiro(args.get("offset"), "offset", 0)

        if limit < 1:
            raise ErroValidacao("limit deve ser maior que zero.")
        if offset < 0:
            raise ErroValidacao("offset não pode ser negativo.")

        return cls(limit=min(limit, LIMITE_MAXIMO), offset=offset)

    def meta(self, total: int) -> dict:
        """Bloco `meta.pagination` da resposta."""
        return {
            "pagination": {
                "limit": self.limit,
                "offset": self.offset,
                "total": total,
                "has_more": self.offset + self.limit < total,
            }
        }


def _inteiro(valor: str | None, campo: str, default: int) -> int:
    if valor is None or valor == "":
        return default
    try:
        return int(valor)
    except (TypeError, ValueError):
        raise ErroValidacao(f"{campo} deve ser um número inteiro.") from None
