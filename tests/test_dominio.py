"""O conector do Domínio — o que nele não pode ser "limpo"."""

from __future__ import annotations

import logging

from app.importacao import dominio


class ErroFalso(Exception):
    pass


class ConexaoQueRecusaOTeto:
    """O driver da SAP responde `HYC00 Driver not capable` a
    `SQL_ATTR_QUERY_TIMEOUT`. Este dublê faz o mesmo."""

    def __setattr__(self, nome, valor):
        if nome == "timeout":
            raise ErroFalso("HYC00 Driver not capable")
        super().__setattr__(nome, valor)


class PyodbcFalso:
    Error = ErroFalso


def test_recusa_do_teto_nao_derruba_a_conexao(caplog):
    """Sem a guarda, **todo** importador morria com "Não foi possível ler o
    Domínio. Verifique a conexão" — com a conexão perfeita, já aberta na linha
    de cima."""
    dominio._avisou_sem_teto = False
    with caplog.at_level(logging.WARNING):
        dominio._tentar_teto_de_consulta(PyodbcFalso, ConexaoQueRecusaOTeto())
    assert "SEM TETO POR CONSULTA" in caplog.text


def test_o_aviso_sai_UMA_vez_por_processo(caplog):
    """Medido numa rodada completa em 22/09/2026: 713 linhas idênticas, 52% dos
    bytes do log. Num log noturno rotativo isso corta pela metade o histórico
    que sobra para investigar, e enterra o que importa.

    O driver ou aceita o atributo ou não — a resposta é do DRIVER, não da
    conexão. Dizê-la uma vez diz tudo que há para dizer.
    """
    dominio._avisou_sem_teto = False
    with caplog.at_level(logging.WARNING):
        for _ in range(50):
            dominio._tentar_teto_de_consulta(PyodbcFalso, ConexaoQueRecusaOTeto())
    assert caplog.text.count("SEM TETO POR CONSULTA") == 1


def test_driver_que_aceita_o_teto_nao_gera_aviso(caplog):
    class ConexaoNormal:
        pass

    dominio._avisou_sem_teto = False
    with caplog.at_level(logging.WARNING):
        dominio._tentar_teto_de_consulta(PyodbcFalso, ConexaoNormal())
    assert "SEM TETO" not in caplog.text


def test_codec_tolerante_nunca_levanta():
    """Há texto UTF-8 gravado dentro de coluna cp1252 na origem (empresa 536,
    `ctlancto`). Com `strict`, cinco linhas derrubavam a importação inteira
    daquela empresa."""
    import codecs

    codec = codecs.lookup(dominio.CODEC_TOLERANTE)
    # Os cinco bytes sem significado em cp1252, mais um par UTF-8 solto.
    assert codec.decode(b"\x81\x8d\x8f\x90\x9d\xc3\x81")[0]
