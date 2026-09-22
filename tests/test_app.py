"""A fundação: factory, sondas, cabeçalhos de segurança e a política de CSP."""

from __future__ import annotations

import pytest

from app import SERVIDOR_HOST
from app.config import Settings


def test_sonda_de_vida_nao_toca_em_dependencia(client):
    """`/health/live` responde com o banco e o Domínio fora.

    As Settings do teste apontam para um banco que não existe. Se esta sonda
    abrisse conexão, ela falharia — e uma sonda de vida que depende do banco
    derruba o processo justamente quando ele precisa continuar de pé para
    relatar o problema.
    """
    resposta = client.get("/health/live")
    assert resposta.status_code == 200
    assert resposta.get_json()["data"]["status"] == "live"


def test_servidor_escuta_so_no_loopback():
    """A constante é a proteção desta aplicação — ver `app/__init__.py`.

    Este processo grava no PostgreSQL de produção do Portal sem autenticação.
    O teste existe para que transformá-la em configuração seja uma decisão
    deliberada, com um teste vermelho pelo caminho, e não um descuido.
    """
    assert SERVIDOR_HOST == "127.0.0.1"


def test_cabecalhos_de_seguranca_em_toda_resposta(client):
    r = client.get("/health/live")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert r.headers["Cache-Control"] == "no-store"


def test_id_de_correlacao_em_toda_resposta(client):
    """Não só nas de erro: quem relata um problema relata "veio errado", não 500."""
    r = client.get("/health/live")
    assert r.headers["X-Request-Id"] != "-"


def test_csp_tem_nonce_e_nao_libera_script_externo(client):
    politica = client.get("/health/live").headers["Content-Security-Policy"]
    assert "script-src 'self' 'nonce-" in politica
    # As bibliotecas de terceiro do Portal são vendorizadas; nenhuma CDN entra
    # em script-src. Fechar a diretiva é o que faz a tela funcionar em rede
    # interna e sem internet.
    assert "cdn." not in politica
    assert "frame-ancestors 'none'" in politica
    assert "report-uri /api/v1/csp-report" in politica


def test_nonce_muda_a_cada_requisicao(client):
    """Nonce repetido não é nonce: um atacante que o descubra o reusa."""
    primeiro = client.get("/health/live").headers["Content-Security-Policy"]
    segundo = client.get("/health/live").headers["Content-Security-Policy"]
    assert primeiro != segundo


def test_csp_em_report_only_quando_desligada(settings):
    """`CSP_ENFORCE=false` é o interruptor de emergência: observa sem bloquear."""
    from app import create_app

    settings_sem_csp = settings.model_copy(update={"csp_enforce": False})
    cliente = create_app(settings_sem_csp).test_client()
    r = cliente.get("/health/live")
    assert "Content-Security-Policy-Report-Only" in r.headers
    assert "Content-Security-Policy" not in r.headers


def test_estatico_leva_versao_do_mtime(app):
    """Sem o `?v=`, o browser serve o CSS do cache junto com o HTML novo."""
    with app.test_request_context():
        from flask import url_for

        assert "?v=" in url_for("static", filename="css/tokens.css")


def test_credencial_do_banco_e_obrigatoria():
    """Subir sem credencial só adiaria a falha até a primeira sincronização."""
    with pytest.raises(ValueError, match="DATABASE_PASSWORD"):
        Settings(_env_file=None, database_user="x", database_password="")
