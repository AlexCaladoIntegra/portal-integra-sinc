"""SINC-006 Fase 0 — a assinatura da origem e o estado local.

O Domínio é dublê. O que está sob teste é o contrato: o formato do hash, o que
a assinatura enxerga e o que ela não enxerga, e a garantia de que a medição
**nunca derruba a sincronização que estava medindo**.

Esta última é a propriedade que torna a Fase 0 de risco zero, e é a única que
não dá para verificar olhando o código: precisa de um teste que quebre o
Domínio de propósito.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.importacao import assinatura, dominio, estado


@pytest.fixture
def banco(tmp_path):
    """Um SQLite descartável — nunca o `estado-sinc.db` da instalação."""
    return tmp_path / "estado.db"


# ── O formato do hash ────────────────────────────────────────────────────────


def test_a_formatacao_faz_parte_da_assinatura():
    """Contrato do SYNC-001 §16.5: duas casas, ponto, sem separador de milhar.

    `1200.5` e `1200.50` dariam hashes diferentes, o que custaria uma
    releitura inútil. O teste trava as duas como IGUAIS.
    """
    assert assinatura.digitar(10, Decimal("1200.5")) == assinatura.digitar(10, Decimal("1200.50"))


def test_contagem_e_soma_mudam_a_assinatura():
    base = assinatura.digitar(10, Decimal("100.00"))
    assert assinatura.digitar(11, Decimal("100.00")) != base
    assert assinatura.digitar(10, Decimal("100.01")) != base


def test_centavo_muda_a_assinatura():
    """A terceira casa não; o centavo sim. É a resolução do dado no Portal."""
    assert assinatura.digitar(1, Decimal("10.00")) != assinatura.digitar(1, Decimal("10.01"))


def test_a_cegueira_conhecida_do_COUNT_mais_SUM():
    """Documenta o limite do §5 do SINC-006, em vez de fingir que não existe.

    Duas edições que se anulam — um lançamento sobe 50, outro desce 50 —
    preservam contagem e soma, e passam despercebidas. A mitigação é
    operacional: uma rodada periódica ignorando a assinatura.
    """
    antes = assinatura.digitar(2, Decimal("100.00") + Decimal("200.00"))
    depois = assinatura.digitar(2, Decimal("150.00") + Decimal("150.00"))
    assert antes == depois


# ── O recorte por empresa ────────────────────────────────────────────────────


def test_sem_ids_a_consulta_varre_o_parque():
    sql, _ = assinatura._fontes("fiscal_apuracao")[0]
    assert "codi_emp IN" not in sql


def test_com_ids_a_consulta_e_recortada():
    """Sem o recorte, a assinatura de UMA empresa custaria a varredura do
    parque inteiro — 8 s do produto fiscal sobre uma sincronização de 4 s."""
    sql, _ = assinatura._fontes("fiscal_apuracao", [272, 318])[0]
    assert "codi_emp IN (272,318)" in sql


def test_id_nao_inteiro_e_recusado():
    """Os ids entram interpolados. A validação de `services` já os garante; o
    assert existe para que a garantia não dependa de quem chama."""
    with pytest.raises(AssertionError):
        assinatura._fontes("fiscal_apuracao", [True])


def test_entidade_sem_assinatura_definida_e_erro_de_programacao():
    with pytest.raises(ValueError, match="empresas"):
        assinatura._fontes("empresas")


# ── O acúmulo de várias fontes ───────────────────────────────────────────────


def test_movimento_soma_saidas_entradas_e_servicos(monkeypatch):
    """Três tabelas, uma assinatura por empresa."""
    respostas = iter(
        [
            [{"codi_emp": 1, "n": 10, "s": Decimal("100.00")}],
            [{"codi_emp": 1, "n": 5, "s": Decimal("50.00")}],
            [{"codi_emp": 1, "n": 2, "s": Decimal("25.00")}],
        ]
    )
    monkeypatch.setattr(dominio, "consultar", lambda *_a, **_k: next(respostas))

    calculado = assinatura.calcular("fiscal_movimento")
    assert calculado == {1: assinatura.digitar(17, Decimal("175.00"))}


def test_empresa_sem_linha_na_origem_nao_aparece(monkeypatch):
    """E a ausência é significativa: ela se distingue de uma empresa com zero
    linhas somando zero, que apareceria com a assinatura de `0:0.00`."""
    monkeypatch.setattr(dominio, "consultar", lambda *_a, **_k: [])
    assert assinatura.calcular("fiscal_apuracao") == {}


def test_soma_nula_na_origem_nao_quebra(monkeypatch):
    """`SUM` de tabela vazia devolve NULL, não zero."""
    monkeypatch.setattr(
        dominio, "consultar", lambda *_a, **_k: [{"codi_emp": 1, "n": 0, "s": None}]
    )
    assert assinatura.calcular("fiscal_apuracao") == {1: assinatura.digitar(0, Decimal(0))}


# ── O estado local ───────────────────────────────────────────────────────────


def test_primeira_leitura_vem_vazia(banco):
    assert estado.ler("fiscal_apuracao", banco) == {}


def test_grava_e_le(banco):
    estado.gravar("fiscal_apuracao", {1: "aaa", 2: "bbb"}, banco)
    assert estado.ler("fiscal_apuracao", banco) == {1: "aaa", 2: "bbb"}


def test_gravar_nao_apaga_quem_nao_veio(banco):
    """Uma sincronização de três empresas não pode invalidar o checkpoint das
    outras trezentas."""
    estado.gravar("fiscal_apuracao", {1: "aaa", 2: "bbb"}, banco)
    estado.gravar("fiscal_apuracao", {1: "zzz"}, banco)
    assert estado.ler("fiscal_apuracao", banco) == {1: "zzz", 2: "bbb"}


def test_entidades_nao_se_misturam(banco):
    estado.gravar("fiscal_apuracao", {1: "aaa"}, banco)
    estado.gravar("fiscal_produto", {1: "bbb"}, banco)
    assert estado.ler("fiscal_apuracao", banco) == {1: "aaa"}
    assert estado.ler("fiscal_produto", banco) == {1: "bbb"}


def test_esquecer_forca_releitura_completa(banco):
    estado.gravar("fiscal_apuracao", {1: "aaa"}, banco)
    estado.gravar("fiscal_produto", {1: "bbb"}, banco)
    assert estado.esquecer("fiscal_apuracao", banco) == 1
    assert estado.ler("fiscal_apuracao", banco) == {}
    assert estado.ler("fiscal_produto", banco) == {1: "bbb"}


# ── A propriedade que torna a Fase 0 de risco zero ───────────────────────────


def test_observar_nao_levanta_quando_a_origem_cai(monkeypatch, caplog):
    """Medição que derruba a operação que estava medindo é pior que não medir."""

    def explode(*_a, **_k):
        raise RuntimeError("Domínio fora")

    monkeypatch.setattr(dominio, "consultar", explode)
    assert assinatura.observar("fiscal_apuracao", None) is None
    assert "não foi possível calcular" in caplog.text


def test_observar_nao_levanta_quando_o_estado_local_falha(monkeypatch):
    monkeypatch.setattr(dominio, "consultar", lambda *_a, **_k: [{"codi_emp": 1, "n": 1, "s": 1}])

    def explode(*_a, **_k):
        raise OSError("disco cheio")

    monkeypatch.setattr(estado, "ler", explode)
    # Devolve o calculado assim mesmo: sem base de comparação, mas sem quebrar.
    assert assinatura.observar("fiscal_apuracao", None) is not None


def test_registrar_nao_levanta(monkeypatch):
    def explode(*_a, **_k):
        raise OSError("disco cheio")

    monkeypatch.setattr(estado, "gravar", explode)
    assinatura.registrar("fiscal_apuracao", {1: "aaa"})  # não deve levantar


def test_entidade_fora_da_lista_e_ignorada_sem_custo(monkeypatch):
    """As cinco leves não pagam o mecanismo — nem uma ida à origem."""

    def nao_deveria(*_a, **_k):
        raise AssertionError("não devia consultar a origem")

    monkeypatch.setattr(dominio, "consultar", nao_deveria)
    assert assinatura.observar("empresas", None) is None


# ── A Fase 0 não pula nada ───────────────────────────────────────────────────


def test_fase_0_apenas_MEDE(monkeypatch, caplog):
    """A garantia central desta fase: `observar` informa, e nunca decide.

    Ela devolve as assinaturas para registro posterior — não uma lista do que
    pular. Ligar o pulo é a Fase 1, e exige mudar quem chama.
    """
    import logging

    monkeypatch.setattr(
        dominio, "consultar", lambda *_a, **_k: [{"codi_emp": 1, "n": 1, "s": Decimal("1.00")}]
    )
    igual = assinatura.digitar(1, Decimal("1.00"))
    monkeypatch.setattr(estado, "ler", lambda _e: {1: igual})

    with caplog.at_level(logging.INFO):
        devolvido = assinatura.observar("fiscal_apuracao", None)

    assert devolvido == {1: igual}
    assert "nada foi pulado" in caplog.text.lower()
