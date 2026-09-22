"""Fixtures compartilhadas.

As Settings dos testes são construídas à mão e **nunca** lidas do `.env`: um
teste que dependesse do arquivo passaria ou falharia conforme a máquina, e o
`.env` desta aplicação aponta para o PostgreSQL de produção do Portal.
"""

from __future__ import annotations

import pytest

from app import create_app
from app.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Settings mínimas e válidas, sem tocar no `.env`."""
    return Settings(
        _env_file=None,
        database_user="teste",
        database_password="teste",
        database_name="banco_de_teste",
        dominio_dsn="",
    )


@pytest.fixture
def app(settings: Settings):
    app = create_app(settings)
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def client(app):
    return app.test_client()
