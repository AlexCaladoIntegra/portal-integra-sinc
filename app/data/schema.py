"""A revisão de schema do Portal, lida do banco.

Este processo grava em dezoito tabelas de que **não é dono**: quem as cria e
altera é o Alembic do `portal-integra`. Uma migration nova lá pode acrescentar
coluna obrigatória, apertar uma constraint ou mudar chave natural — e o sintoma
disso aqui é um erro de integridade no meio de uma carga de milhões de linhas,
ou, pior, dado gravado onde não devia, sem erro nenhum.

Por isso a revisão é conferida e reportada. Ver `SCHEMA_REVISAO_ESPERADA` em
`app/config.py` para por que isto **avisa** em vez de recusar.
"""

from __future__ import annotations

import logging

from ..config import SCHEMA_REVISAO_ESPERADA
from .connection import get_connection

logger = logging.getLogger(__name__)


def revisao_aplicada() -> str | None:
    """A revisão em `alembic_version`, ou `None` se a tabela não existe.

    `None` é resposta legítima e não erro: é o que se vê apontando para um
    banco vazio, ou para um que não é o do Portal. Quem chama decide o que
    dizer — aqui só se relata o fato.

    A conexão pode falhar (banco fora, credencial errada, rota morta); a
    exceção **sobe**. Confundir "não consegui perguntar" com "não há revisão"
    faria o `/health` dizer que o schema está errado quando o problema é a
    rede, mandando quem investiga para o lado oposto.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.alembic_version')")
        if cur.fetchone()[0] is None:
            return None
        cur.execute("SELECT version_num FROM alembic_version")
        linha = cur.fetchone()
        return linha[0] if linha else None


def conferir(aplicada: str | None) -> tuple[str, str]:
    """Traduz a revisão encontrada em `(situacao, mensagem)`.

    `situacao` é `ok`, `divergente` ou `ausente` — chave estável, consumida
    pela tela. A mensagem é o texto em português para quem lê.

    É função **pura**: recebe a revisão e não vai ao banco. É o que permite
    testar os três casos sem PostgreSQL.
    """
    if aplicada == SCHEMA_REVISAO_ESPERADA:
        return "ok", f"Schema na revisão {SCHEMA_REVISAO_ESPERADA}."
    if aplicada is None:
        return (
            "ausente",
            "O banco não tem a tabela alembic_version. Ou ele está vazio, ou "
            "não é o banco do Portal Integra. Confira DATABASE_NAME no .env.",
        )
    return (
        "divergente",
        f"O banco está na revisão {aplicada} e este sincronizador foi escrito "
        f"para a {SCHEMA_REVISAO_ESPERADA}. Confira se alguma migration do "
        "portal-integra alterou as tabelas sincronizadas antes de continuar. "
        "A 0039 é a que trouxe `documento_titular`: antes dela, a CND de um "
        "sócio vira uma versão da CND da própria empresa.",
    )


def tem_coluna_de_titular() -> bool:
    """A `0039` chegou de fato?

    Conferir a COLUNA e não só a revisão, porque as duas respondem perguntas
    diferentes: a revisão diz o que o Alembic registrou, e a coluna diz o que o
    banco tem. Elas divergem quando alguém aplica DDL à mão.

    É esta checagem que decide se a parte de SÓCIO do importador
    `cnd_documentos` pode rodar. Os dez importadores de BI não dependem dela.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'doc_documento'
               AND column_name IN ('documento_titular', 'nome_titular')
            """
        )
        return cur.fetchone()[0] == 2


def conferir_no_boot() -> None:
    """Confere e registra no log. Nunca levanta.

    Chamada quando o processo sobe, antes de a tela existir. Falha de conexão
    aqui **não** derruba nada: o banco pode estar fora no momento do boot e
    voltar depois, e é o `/health` (e a tela) que reportam o estado corrente.
    """
    try:
        situacao, mensagem = conferir(revisao_aplicada())
    except Exception:
        logger.warning(
            "Não foi possível conferir a revisão do schema no boot. "
            "A tela e o /health reportam o estado atual.",
            exc_info=True,
        )
        return

    if situacao == "ok":
        logger.info(mensagem)
    else:
        logger.warning("SCHEMA: %s", mensagem)
