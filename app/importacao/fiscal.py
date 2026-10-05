"""Conexão com o `fiscal-monitor-cpf` (PostgreSQL) — somente leitura.

A SEGUNDA origem deste projeto, ao lado de `dominio.py`. Único ponto que abre
essa conexão, e serve a um importador só: o `cnd_documentos`, que espelha as
certidões negativas de débito no módulo Documentos do Portal.

## Por que um pool próprio, e não o `data/connection.py`

Aquele é do DESTINO. São dois bancos diferentes, com credenciais, tetos e
disponibilidade diferentes — e, principalmente, com direções diferentes: um é
só leitura e o outro é onde se grava. Um pool só, parametrizado, economizaria
trinta linhas e tornaria possível escrever na origem por engano de argumento.

É o mesmo desenho de `dominio.py`, que também abre a própria conexão com a
origem dele e nunca toca no pool do destino.

## Por que não é o `psycopg2` direto, sem pool

Porque a rodada lê em duas fases — a lista de certidões e depois o PDF de cada
uma — e abrir conexão por PDF seria um handshake por documento. Com um pool
pequeno o custo some, e o `with conexao()` continua devolvendo ao pool.

## O que ele NUNCA faz

Escreve. O `statement_timeout` entra como `options` do libpq e a sessão é
aberta em `default_transaction_read_only` — ver `_opcoes_de_sessao`. Não é
defesa contra um atacante: é defesa contra um `UPDATE` digitado numa consulta
deste módulo, que é o erro plausível. O projeto de origem é de outra equipe e
o banco dele não é nosso para corrigir.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from typing import Any

import psycopg2
from psycopg2 import pool

from ..config import Settings, get_settings
from ..shared.errors import ErroApp

logger = logging.getLogger(__name__)

_pool: pool.ThreadedConnectionPool | None = None
_lock = Lock()


class ErroFiscalMonitor(ErroApp):
    """Falha ao ler o fiscal-monitor-cpf. 503: é indisponibilidade de
    dependência externa, não erro de quem pediu o espelho."""

    code = "fiscal_monitor_indisponivel"
    status = 503
    message = "Não foi possível ler o fiscal-monitor-cpf. Verifique a conexão e tente de novo."


def configurado() -> bool:
    """True se há como conectar. A tela usa isto para explicar em vez de falhar."""
    return get_settings().fiscal_configurado


def _opcoes_de_sessao(s: Settings) -> dict[str, str]:
    """Os parâmetros que o pool aplica a TODA conexão da origem.

    `default_transaction_read_only` vai junto do teto, e não num `SET` depois do
    connect, pelo mesmo motivo do sinc: o `SET` precisaria ser reemitido a cada
    conexão nova do pool, e quem esquecesse abriria uma conexão gravável — sem
    erro e sem log, que é como este defeito nasce.
    """
    partes = ["-c default_transaction_read_only=on"]
    if s.fiscal_statement_timeout_ms > 0:
        partes.append(f"-c statement_timeout={s.fiscal_statement_timeout_ms}")
    return {"options": " ".join(partes)}


def obter_pool() -> pool.ThreadedConnectionPool:
    """Pool da origem, criado sob demanda.

    Lazy e não no import, como o do destino: assim a aplicação sobe e o
    `/health` responde "origem fora" quando o fiscal-monitor está inalcançável,
    em vez de quebrar no boot.
    """
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                s = get_settings()
                if not s.fiscal_configurado:
                    raise ErroFiscalMonitor(
                        "A conexão com o fiscal-monitor-cpf não está configurada — "
                        "defina FISCAL_HOST e FISCAL_USER no .env."
                    )
                try:
                    _pool = pool.ThreadedConnectionPool(
                        minconn=1,
                        maxconn=4,
                        host=s.fiscal_host,
                        port=s.fiscal_port,
                        user=s.fiscal_user,
                        password=s.fiscal_password,
                        dbname=s.fiscal_name,
                        connect_timeout=s.fiscal_connect_timeout,
                        sslmode=s.fiscal_sslmode,
                        **_opcoes_de_sessao(s),
                    )
                except psycopg2.Error as exc:
                    # A mensagem do libpq traz host, usuário e banco, e não pode
                    # chegar ao navegador. Fica no log.
                    logger.error("Falha ao conectar no fiscal-monitor-cpf: %s", exc)
                    raise ErroFiscalMonitor from exc
                logger.info(
                    "Pool do fiscal-monitor iniciado (db=%s sslmode=%s statement_timeout=%sms)",
                    s.fiscal_name,
                    s.fiscal_sslmode,
                    s.fiscal_statement_timeout_ms or "sem teto",
                )
    return _pool


def fechar_pool() -> None:
    """Fecha todas as conexões (usado em teardown de teste e shutdown)."""
    global _pool
    with _lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None


@contextmanager
def conexao() -> Iterator[psycopg2.extensions.connection]:
    """Conexão de LEITURA com a origem. Sempre faz rollback ao sair.

    Rollback e não commit: a sessão é `read_only`, então não há o que commitar
    — o que o rollback evita é a conexão voltar ao pool com uma transação
    aberta, segurando um snapshot por toda a rodada.
    """
    p = obter_pool()
    conn = p.getconn()
    try:
        yield conn
    except psycopg2.Error as exc:
        logger.error("Falha ao ler o fiscal-monitor-cpf: %s", exc)
        raise ErroFiscalMonitor from exc
    finally:
        try:
            conn.rollback()
        except Exception:
            logger.warning("Falha ao descartar transação de leitura", exc_info=True)
        p.putconn(conn)


def consultar(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Executa um SELECT na origem e devolve lista de dicionários.

    Dicionário e nunca tupla crua: índice numérico em camada de cima é o
    caminho mais curto para um bug silencioso quando o SELECT muda de ordem — e
    aqui uma coluna trocada põe a validade de uma certidão no campo da emissão.
    """
    with conexao() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        colunas = [d.name for d in cur.description]
        return [dict(zip(colunas, linha, strict=True)) for linha in cur.fetchall()]


def consultar_um(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    """A primeira linha, ou `None`."""
    linhas = consultar(sql, params)
    return linhas[0] if linhas else None
