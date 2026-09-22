"""Pool de conexões PostgreSQL e os dois context managers de acesso.

Regra do projeto:

    get_connection()  →  LEITURA. Sempre faz rollback ao sair, para que a
                         conexão nunca volte ao pool com transação aberta.
    transacao()       →  ESCRITA. Commit no sucesso, rollback na exceção.

Nenhum repository deve chamar `conn.commit()` ou `conn.rollback()`: a
transação é responsabilidade do context manager. Se um método escreve, ele
usa `transacao()`.

O pool é criado na primeira conexão (lazy), não no import — assim a aplicação
sobe e o /health responde "degraded" quando o banco está fora, em vez de
quebrar no boot.

Cópia do `app/data/connection.py` do Portal Integra, com uma única mudança:
`settings_ativas()` saiu e o pool lê `get_settings()` direto. Lá a função
existia para permitir que `create_app` injetasse outro Settings (um teste
apontando para outro banco); aqui o factory não injeta nada, e manter a
indireção significaria importar Flask num módulo que não precisa dele.
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

logger = logging.getLogger(__name__)

_pool: pool.ThreadedConnectionPool | None = None
_lock = Lock()


def _opcoes_de_sessao(s: Settings) -> dict[str, str]:
    """Os parâmetros de sessão que o pool aplica a TODA conexão.

    Hoje só o `statement_timeout`. Vai como `options` do libpq e não como um
    `SET` depois do connect porque o `SET` precisaria ser reemitido a cada
    conexão nova do pool, e quem esquecesse deixaria a conexão sem teto — sem
    erro e sem log, que é como este defeito nasceu.

    `0` devolve dicionário vazio: o `options` nem é enviado, e a sessão fica com
    o default do servidor. Ver `db_statement_timeout_ms` em `app/config.py`.
    """
    if s.db_statement_timeout_ms <= 0:
        return {}
    return {"options": f"-c statement_timeout={s.db_statement_timeout_ms}"}


def obter_pool() -> pool.ThreadedConnectionPool:
    """Pool do processo, criado sob demanda.

    É global: o primeiro uso fixa o destino para o processo inteiro. Num teste
    que precise trocar de banco, chame `fechar_pool()` antes.

    Os dois tetos entram AQUI, e não em cada chamada: `connect_timeout` e
    `statement_timeout` são propriedades da conexão, e o pool é o único lugar
    que abre uma. Ver `app/config.py` para o porquê de cada número.
    """
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                s = get_settings()
                _pool = pool.ThreadedConnectionPool(
                    minconn=s.db_pool_min,
                    maxconn=s.db_pool_max,
                    host=s.database_host,
                    port=s.database_port,
                    user=s.database_user,
                    password=s.database_password,
                    dbname=s.database_name,
                    connect_timeout=s.db_connect_timeout,
                    **_opcoes_de_sessao(s),
                )
                logger.info(
                    "Pool PostgreSQL iniciado (min=%s max=%s db=%s "
                    "connect_timeout=%ss statement_timeout=%sms)",
                    s.db_pool_min,
                    s.db_pool_max,
                    s.database_name,
                    s.db_connect_timeout,
                    s.db_statement_timeout_ms or "sem teto",
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
def get_connection() -> Iterator[psycopg2.extensions.connection]:
    """Conexão para LEITURA — descarta a transação implícita ao sair."""
    p = obter_pool()
    conn = p.getconn()
    try:
        yield conn
    finally:
        try:
            conn.rollback()
        except Exception:
            logger.warning("Falha ao descartar transação de leitura", exc_info=True)
        p.putconn(conn)


@contextmanager
def trava_de_sessao(nome: str) -> Iterator[bool]:
    """Tenta uma trava exclusiva com o nome dado. Rende True se conseguiu.

    É `pg_try_advisory_lock` — de SESSÃO, e não de transação. A distinção é a
    única que importa aqui: a importação não roda dentro de uma transação só,
    ela abre várias (`registrar_inicio`, o `transacao()` de cada empresa,
    `registrar_sucesso`). Uma trava de transação soltaria no primeiro commit,
    isto é, antes do trabalho que ela existe para proteger.

    **Não precisa de tabela, e é por isso que serve aqui**: o SEG-002 não abre
    migration. Uma coluna de lock precisaria de limpeza, e limpeza de lock é a
    origem clássica do bloqueio permanente — se o processo morre no meio, a
    linha fica travada para sempre. A trava do PostgreSQL solta sozinha quando a
    conexão cai, inclusive num worker morto pelo gunicorn.

    A conexão fica retida enquanto o `with` estiver aberto, e é deliberado: é o
    que dá à trava o tempo de vida da operação. O `commit` depois de pedi-la
    fecha a transação implícita — sem ele a conexão fica `idle in transaction`
    por todo o trabalho.

    Não conseguir a trava **não é erro deste módulo**: quem chamou decide o que
    dizer. Aqui só se informa o fato.
    """
    p = obter_pool()
    conn = p.getconn()
    obtida = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s)::bigint)", (nome,))
            obtida = bool(cur.fetchone()[0])
        conn.commit()
        yield obtida
    finally:
        try:
            if obtida:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(hashtext(%s)::bigint)", (nome,))
            conn.commit()
        except Exception:
            # Conexão já perdida: a trava morreu com ela, que é o desenho.
            logger.warning("Falha ao liberar a trava %r", nome, exc_info=True)
        p.putconn(conn)


@contextmanager
def transacao() -> Iterator[psycopg2.extensions.connection]:
    """Conexão para ESCRITA — commit no sucesso, rollback em qualquer exceção."""
    p = obter_pool()
    conn = p.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


# ── Conversão de resultado ───────────────────────────────────────────────────
# Repositories devolvem dict (ou dataclass), nunca tupla crua: índice numérico
# em camada de cima é o caminho mais curto para um bug silencioso quando o
# SELECT muda de ordem.


def linhas_dict(cur) -> list[dict[str, Any]]:
    """Todas as linhas do cursor como lista de dicionários."""
    colunas = [d.name for d in cur.description]
    return [dict(zip(colunas, linha, strict=True)) for linha in cur.fetchall()]


def linha_dict(cur) -> dict[str, Any] | None:
    """Primeira linha do cursor como dicionário, ou None."""
    linha = cur.fetchone()
    if linha is None:
        return None
    colunas = [d.name for d in cur.description]
    return dict(zip(colunas, linha, strict=True))
