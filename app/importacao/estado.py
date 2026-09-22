"""O estado local deste processo — um SQLite ao lado do executável.

Hoje guarda uma coisa só: a assinatura da origem, por entidade e empresa, do
que foi sincronizado pela última vez.

## Por que aqui, e não no PostgreSQL do Portal

Uma tabela no `portalintegra` exigiria migration, e este projeto **não é dono
de tabela nenhuma** — é a regra do CLAUDE.md. Mais que isso: este estado é
deste processo, não do Portal. Duas instalações do sincronizador apontando
para o mesmo Portal teriam checkpoints diferentes, e estaria certo: cada uma
sabe o que ELA leu.

## Por que dá para guardar fora do banco

Perder o arquivo custa **uma rodada completa** — o sincronizador volta a ler
tudo, como faz hoje. Degradação segura, não corrupção. É essa propriedade que
autoriza o estado a morar num arquivo que ninguém faz backup.

O arquivo está no `.gitignore` pelo mesmo motivo do `.env`: é de instalação,
não de código.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..config import BASE_DIR

logger = logging.getLogger(__name__)

CAMINHO = BASE_DIR / "estado-sinc.db"

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS assinatura (
    entidade      TEXT    NOT NULL,
    id_empresa    INTEGER NOT NULL,
    valor         TEXT    NOT NULL,
    atualizada_em TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (entidade, id_empresa)
)
"""


@contextmanager
def _conexao(caminho: Path | None = None):
    """Abre o SQLite, criando o arquivo e a tabela na primeira vez.

    `caminho` existe para o teste apontar para um arquivo temporário. Em
    produção ninguém passa.
    """
    conn = sqlite3.connect(caminho or CAMINHO)
    try:
        conn.execute(_ESQUEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ler(entidade: str, caminho: Path | None = None) -> dict[int, str]:
    """As assinaturas guardadas de uma entidade. Vazio na primeira execução."""
    with _conexao(caminho) as conn:
        cur = conn.execute(
            "SELECT id_empresa, valor FROM assinatura WHERE entidade = ?", (entidade,)
        )
        return dict(cur.fetchall())


def gravar(entidade: str, valores: dict[int, str], caminho: Path | None = None) -> None:
    """Grava ou atualiza as assinaturas informadas.

    Não apaga o que não veio: uma sincronização de três empresas não pode
    invalidar o checkpoint das outras trezentas.
    """
    if not valores:
        return
    with _conexao(caminho) as conn:
        conn.executemany(
            "INSERT INTO assinatura (entidade, id_empresa, valor, atualizada_em) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT (entidade, id_empresa) DO UPDATE "
            "   SET valor = excluded.valor, atualizada_em = excluded.atualizada_em",
            [(entidade, empresa, valor) for empresa, valor in valores.items()],
        )


def esquecer(entidade: str | None = None, caminho: Path | None = None) -> int:
    """Descarta o checkpoint, para forçar releitura completa.

    Sem `entidade`, descarta tudo. É o equivalente a apagar o arquivo, mas sem
    exigir que alguém saiba onde ele fica.
    """
    with _conexao(caminho) as conn:
        if entidade is None:
            cur = conn.execute("DELETE FROM assinatura")
        else:
            cur = conn.execute("DELETE FROM assinatura WHERE entidade = ?", (entidade,))
        return cur.rowcount
