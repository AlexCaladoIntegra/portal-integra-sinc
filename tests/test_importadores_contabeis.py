"""BI Contábil: as regras puras dos quatro importadores.

Sem Domínio e sem PostgreSQL. O que está aqui são as decisões que, erradas,
produzem **número plausível** — sem erro, sem log, e indistinguível de um
relatório correto até alguém somar à mão.

Os quatro módulos são cópia literal do Portal. Estes testes existem para que a
cópia não derive: cada caso é uma regra medida contra dados reais lá, e o custo
de quebrá-la é um BI que mente.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.importacao.importadores import contabil_dfc, contabil_lancamentos

# ── A janela dos lançamentos ─────────────────────────────────────────────────


def test_janela_cobre_o_ano_corrente_e_os_dois_anteriores(settings, monkeypatch):
    """O default é 3, o mesmo do Portal — e tem de continuar sendo: os dois
    escrevem em `bi_lancamento`, e divergir faria o aviso de "período anterior
    à janela" mentir sobre o que existe na tabela."""
    monkeypatch.setattr(contabil_lancamentos, "get_settings", lambda: settings)
    assert contabil_lancamentos.janela(date(2026, 6, 15)) == (date(2024, 1, 1), date(2027, 1, 1))


def test_o_fim_da_janela_e_EXCLUSIVO(settings, monkeypatch):
    """Devolver 31/12 convidaria a esquecer o próprio 31/12 num `<`, ou a
    incluir o 01/01 seguinte num `<=`."""
    monkeypatch.setattr(contabil_lancamentos, "get_settings", lambda: settings)
    _, fim = contabil_lancamentos.janela(date(2026, 6, 15))
    assert fim == date(2027, 1, 1)


def test_janela_acompanha_a_configuracao(settings, monkeypatch):
    monkeypatch.setattr(
        contabil_lancamentos,
        "get_settings",
        lambda: settings.model_copy(update={"sinc_anos_janela": 1}),
    )
    assert contabil_lancamentos.janela(date(2026, 6, 15)) == (date(2026, 1, 1), date(2027, 1, 1))


# ── As duas razões de descarte de uma perna ──────────────────────────────────


def lancamento(**campos):
    base = {
        "nume_lan": 1,
        "cdeb_lan": 10,
        "ccre_lan": 20,
        "data_lan": date(2026, 1, 5),
        "vlor_lan": 100,
        "orig_lan": 1,
        "chis_lan": "  histórico  ",
        "ndoc_lan": 7,
        "codi_lote": 3,
        "codi_usu": " ANA ",
    }
    return {**base, **campos}


def test_conta_zero_e_sentinela_e_nao_merece_aviso():
    """O zero é o "sem conta neste lado" do Domínio: ~12% das pernas de cada
    lado. Avisar sobre isso faria o alerta disparar em toda sincronização por
    um motivo benigno — e alerta que sempre dispara deixa de ser lido."""
    contagem = contabil_lancamentos._contagem_zerada(1)
    pernas = contabil_lancamentos._pernas(lancamento(cdeb_lan=0), 1, {10, 20}, contagem)

    assert [p["natureza"] for p in pernas] == ["C"]
    assert contagem["sem_conta"] == 1
    assert contagem["fora_do_plano"] == 0


def test_conta_fora_do_plano_e_contada_a_parte():
    """A conta existe no lançamento e não está em `bi_conta` — tipicamente
    inativada no Domínio depois de ter tido movimento. Esta merece aviso: o
    razão da conta irmã vai existir e o dela não."""
    contagem = contabil_lancamentos._contagem_zerada(1)
    pernas = contabil_lancamentos._pernas(lancamento(), 1, {10}, contagem)

    assert [p["natureza"] for p in pernas] == ["D"]
    assert contagem["fora_do_plano"] == 1
    assert contagem["sem_conta"] == 0


def test_a_perna_irma_SOBREVIVE_ao_descarte_da_outra():
    """Descartar o lançamento inteiro quando um lado cai seria o defeito.

    A perna irmã tem saldo em `bi_saldo_mensal` — `contabil_saldos` a manteve
    pela mesma regra — e o drill-down dela deixaria de fechar com o próprio
    saldo. Número plausível, sem erro nenhum.
    """
    contagem = contabil_lancamentos._contagem_zerada(1)
    pernas = contabil_lancamentos._pernas(lancamento(ccre_lan=999), 1, {10}, contagem)
    assert len(pernas) == 1
    assert pernas[0]["codi_cta"] == 10


def test_o_grao_e_a_PERNA_e_nao_o_lancamento():
    contagem = contabil_lancamentos._contagem_zerada(1)
    pernas = contabil_lancamentos._pernas(lancamento(), 1, {10, 20}, contagem)
    assert [(p["natureza"], p["codi_cta"]) for p in pernas] == [("D", 10), ("C", 20)]
    # O valor é o mesmo nas duas: o sinal vem da natureza, não do número.
    assert {p["valor"] for p in pernas} == {100}


def test_texto_vazio_vira_nulo():
    contagem = contabil_lancamentos._contagem_zerada(1)
    perna = contabil_lancamentos._pernas(
        lancamento(chis_lan="   ", codi_usu="", ndoc_lan=0), 1, {10, 20}, contagem
    )[0]
    assert perna["historico"] is None
    assert perna["usuario"] is None
    assert perna["documento"] is None


# ── A classificação da DFC ───────────────────────────────────────────────────


def linha_dfc(**campos):
    base = {
        "codigo": 1,
        "ordem": 10,
        "atividade": 1,
        "tipo_linha": "A",
        "forma_apuracao": "L",
        "descricao": "Recebimento de clientes",
    }
    return {**base, **campos}


def classificar(linhas, vinculos=(), contas=frozenset()):
    return contabil_dfc.classificar(
        {"linhas_brutas": list(linhas), "vinculos_brutos": list(vinculos)}, set(contas)
    )


def test_linha_valida_passa():
    assert len(classificar([linha_dfc()])["linhas"]) == 1


@pytest.mark.parametrize(
    "invalida",
    [
        {"atividade": 9},  # fora de (1,2,3)
        {"tipo_linha": "Z"},  # fora de (V,S,A)
        {"forma_apuracao": "Q"},  # fora das dez
        {"descricao": "   "},  # o CHECK da tabela recusa
        {"ordem": None},  # a ordem é chave natural: a cascata LÊ a posição
    ],
)
def test_linha_fora_do_dominio_e_descartada(invalida):
    """Os CHECK de `bi_linha_dfc` recusariam cada um destes. Descartar aqui,
    com o motivo, é melhor que abortar a importação inteira no banco."""
    assert classificar([linha_dfc(**invalida)])["linhas"] == []


def test_forma_de_apuracao_nula_e_valida():
    """É o cabeçalho decorativo — linha sem cálculo próprio."""
    assert len(classificar([linha_dfc(forma_apuracao=None)])["linhas"]) == 1


def test_vinculo_de_conta_ausente_do_plano_e_descartado():
    resultado = classificar(
        [linha_dfc()],
        vinculos=[{"codigo": 1, "codi_cta": 500, "situacao_cta": "A", "tipo_cta": "A"}],
        contas=set(),
    )
    assert resultado["vinculos"] == []


def test_vinculo_de_conta_no_plano_passa():
    resultado = classificar(
        [linha_dfc()],
        vinculos=[{"codigo": 1, "codi_cta": 500, "situacao_cta": "A", "tipo_cta": "A"}],
        contas={500},
    )
    assert [(v["codigo"], v["codi_cta"]) for v in resultado["vinculos"]] == [(1, 500)]


def test_vinculo_de_linha_descartada_nao_sobrevive():
    """Vínculo apontando para linha que não entrou violaria a FK composta."""
    resultado = classificar(
        [linha_dfc(atividade=9)],
        vinculos=[{"codigo": 1, "codi_cta": 500, "situacao_cta": "A", "tipo_cta": "A"}],
        contas={500},
    )
    assert resultado["vinculos"] == []


# ── A regressão de desempenho que custou 96 s por abertura de tela ───────────


def test_empresas_com_saldo_nao_usa_EXISTS_correlacionado_na_selecao():
    """Guarda contra o padrão que o Portal usa e que aqui foi medido.

    `EXISTS (SELECT 1 FROM bi_lancamento WHERE id_empresa = e.id_empresa)` na
    LISTA DE SELEÇÃO vira um `SubPlan` executado por linha. Como só 3 das 306
    empresas têm lançamento, o planejador estima que a condição casa com um
    terço da tabela e escolhe varredura sequencial — e provar a AUSÊNCIA numa
    varredura custa a tabela inteira, 1,7 milhão de linhas, 306 vezes.

    Medido: 66,6 s contra 0,19 s da reescrita, resultado idêntico.

    O teste é textual de propósito: o defeito não muda nenhum resultado, então
    nenhum teste de comportamento o pegaria. O que ele muda é o tempo, e tempo
    não entra em assert sem tornar a suíte instável.
    """
    from pathlib import Path

    from app.importacao.importadores import contabil_lancamentos

    codigo = Path(contabil_lancamentos.__file__).read_text(encoding="utf-8")
    inicio = codigo.index("def _empresas_com_saldo")
    corpo = codigo[inicio : codigo.index("def pendentes", inicio)]

    consulta = corpo[corpo.index('"""', corpo.index('"""') + 3) :]
    assert "EXISTS (SELECT 1 FROM bi_lancamento" not in consulta
    assert "com_lancamento AS (SELECT DISTINCT id_empresa FROM bi_lancamento)" in consulta


# ── As mensagens que mandavam o operador a lugar nenhum ──────────────────────


@pytest.mark.parametrize(
    ("modulo", "trecho"),
    [
        ("contabil_saldos", "não muda nada"),
        ("contabil_dfc", "não muda nada"),
        ("contabil_lancamentos", "não muda nada"),
        ("fiscal_movimento", "não muda nada"),
        ("fiscal_apuracao", "não muda nada"),
        ("fiscal_produto", "não muda nada"),
    ],
)
def test_a_recusa_admite_que_a_origem_pode_nao_ter_o_dado(modulo, trecho):
    """A mensagem antiga mandava "rode antes o importador X" — e para 187 das
    608 empresas ativas rodá-lo não traz nada, porque o Domínio não tem
    escrituração contábil para elas. O operador entrava num laço: rodava,
    lia zero, tentava de novo, mesma mensagem.

    Foi o que aconteceu com a empresa 514 em 22/09/2026.
    """
    from importlib import import_module
    from pathlib import Path

    fonte = import_module(f"app.importacao.importadores.{modulo}")
    codigo = Path(fonte.__file__).read_text(encoding="utf-8")
    assert trecho in codigo
