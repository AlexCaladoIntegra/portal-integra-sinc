"""Diagnóstico das duas pontas que o sincronizador liga.

Uma função só, `diagnosticar()`, servida pelo `/health` e, a partir da Etapa 2,
pelo card de diagnóstico da tela. É o primeiro lugar em que alguém olha quando
uma sincronização falha, e por isso ela responde três perguntas separadas em
vez de um "ok/não ok":

    origem   o Domínio respondeu?     (ODBC — driver instalado, DSN, credencial)
    destino  o PostgreSQL respondeu?  (rota, credencial, banco existe)
    schema   é o banco que eu espero? (revisão do Alembic do Portal)

Separadas porque as três falham por motivos diferentes e mandam quem investiga
para lugares diferentes. Um "indisponível" agregado faria procurar rede quando
o problema é o driver ODBC de 32 bits instalado numa máquina de 64.

**Nenhuma exceção sobe daqui.** Diagnóstico que quebra ao diagnosticar não
serve para nada: o erro vira o próprio resultado, com o texto para quem lê.
"""

from __future__ import annotations

import logging

from ..data.connection import get_connection
from ..data.schema import conferir, revisao_aplicada
from ..importacao import dominio

logger = logging.getLogger(__name__)


def _checar_destino() -> dict:
    """O PostgreSQL do Portal respondeu?"""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    except Exception as exc:
        logger.warning("Destino indisponível: %s", exc)
        return {
            "situacao": "erro",
            "mensagem": "Não foi possível falar com o PostgreSQL do Portal. "
            "Confira DATABASE_HOST, DATABASE_USER e a rota de rede no .env.",
        }
    return {"situacao": "ok", "mensagem": "PostgreSQL do Portal respondeu."}


def _checar_origem() -> dict:
    """O Domínio respondeu?

    "Não configurado" é estado à parte de "erro", e a distinção é o que a tela
    precisa: sem DSN no `.env` não há falha nenhuma a investigar — há uma
    instalação que ainda não terminou.
    """
    if not dominio.configurado():
        return {
            "situacao": "ausente",
            "mensagem": "A conexão com o Domínio não está configurada. "
            "Defina DOMINIO_DSN (ou DOMINIO_CONNSTR) no .env.",
        }
    try:
        with dominio.conexao() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
    except Exception as exc:
        logger.warning("Origem indisponível: %s", exc)
        return {
            "situacao": "erro",
            "mensagem": "Não foi possível ler o Domínio. Confira se o driver "
            "ODBC do SQL Anywhere está instalado (64 bits) e se o DSN e a "
            "credencial do .env estão certos.",
        }
    return {"situacao": "ok", "mensagem": "Domínio respondeu."}


def _checar_schema() -> dict:
    """A revisão do schema do Portal é a que este código espera?"""
    try:
        situacao, mensagem = conferir(revisao_aplicada())
    except Exception:
        # Sem banco não dá para perguntar. Não é divergência de schema, e dizer
        # que é mandaria quem investiga para o lado errado — `_checar_destino`
        # já reportou o motivo real.
        return {
            "situacao": "desconhecido",
            "mensagem": "Não foi possível ler a revisão do schema: o banco não respondeu.",
        }
    return {"situacao": situacao, "mensagem": mensagem}


def diagnosticar() -> dict:
    """As três checagens, mais o veredito agregado.

    `pronto` responde a única pergunta que a tela precisa fazer antes de
    liberar o botão: dá para sincronizar agora? Schema divergente **não**
    impede — é aviso, pela razão em `app/config.py`.
    """
    origem = _checar_origem()
    destino = _checar_destino()
    schema = _checar_schema()
    return {
        "origem": origem,
        "destino": destino,
        "schema": schema,
        "pronto": origem["situacao"] == "ok" and destino["situacao"] == "ok",
    }
