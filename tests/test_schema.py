"""A conferência da revisão de schema do Portal.

`conferir()` é pura — recebe a revisão e devolve o veredito. É o que permite
testar os três casos sem PostgreSQL, e é por isso que ela existe separada de
`revisao_aplicada()`.
"""

from __future__ import annotations

from app.config import SCHEMA_REVISAO_ESPERADA
from app.data.schema import conferir


def test_revisao_igual_a_esperada_e_ok():
    situacao, mensagem = conferir(SCHEMA_REVISAO_ESPERADA)
    assert situacao == "ok"
    assert SCHEMA_REVISAO_ESPERADA in mensagem


def test_revisao_diferente_e_divergente_e_cita_as_duas():
    situacao, mensagem = conferir("0099_outra_coisa")
    assert situacao == "divergente"
    # As DUAS revisões na mensagem: quem lê precisa saber de onde para onde,
    # senão tem de ir ao código descobrir o que era esperado.
    assert "0099_outra_coisa" in mensagem
    assert SCHEMA_REVISAO_ESPERADA in mensagem


def test_sem_tabela_alembic_e_ausente_e_nao_divergente():
    """Banco sem `alembic_version` não é schema errado — é banco errado.

    A distinção importa porque manda quem investiga para lugares diferentes:
    divergente é migration do Portal, ausente é `DATABASE_NAME` no `.env`.
    """
    situacao, mensagem = conferir(None)
    assert situacao == "ausente"
    assert "DATABASE_NAME" in mensagem
