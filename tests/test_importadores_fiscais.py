"""BI Fiscal: os dois módulos puros e as regras que mentem quando quebram.

Sem Domínio e sem PostgreSQL.

As armadilhas deste módulo são piores que as do contábil porque nenhuma delas
dá erro: a competência errada produz um BI **vazio**, que se lê como "não há
movimento no período"; o modelo errado agrupa nota de serviço com conhecimento
de transporte; a contagem líquida subtrai o cancelamento duas vezes. Cada teste
aqui trava uma dessas.
"""

from __future__ import annotations

import pytest

from app.fiscal import impostos, modelos
from app.importacao.importadores import fiscal_dimensoes

# ── O nome canônico do imposto ───────────────────────────────────────────────


def test_o_mapa_cobre_os_44_impostos_da_origem():
    """Conferido em 21/09/2026 contra `efsdoimp`: 44 no mapa, 44 códigos
    distintos na origem, zero lacuna dos dois lados."""
    assert len(impostos.CANONICOS) == 44


def test_canoniza_pelo_CODIGO_e_nao_pelo_nome():
    """`codi_imp` 1 aparece na origem como "3", "ICMS ", "ICMS" e "ICMS NORMAL".

    Nenhuma normalização de string recupera "ICMS" a partir de "3" — é por isso
    que a chave é o código, e é a razão de o módulo existir.
    """
    assert impostos.canonizar(1, "3") == impostos.canonizar(1, "ICMS NORMAL")


def test_nunca_devolve_Outros():
    """Código desconhecido com nome vira o nome limpo; sem nome, vira
    "Imposto <n>". "Outros" juntaria impostos distintos numa linha só do
    gráfico, e ninguém notaria."""
    assert impostos.canonizar(99999, "TRIBUTO NOVO") == "TRIBUTO NOVO"
    assert impostos.canonizar(99999, None) == "Imposto 99999"
    assert "Outros" not in impostos.CANONICOS.values()


def test_nao_funde_codigos_diferentes():
    """`codi_imp` 4 e 17 são regimes distintos de ICMS."""
    assert impostos.canonizar(4) != impostos.canonizar(17)


def test_espaco_interno_e_colapsado():
    """`NOME_IMP` é largura fixa e chega com "IRRF  Propaganda"."""
    assert impostos.canonizar(99999, "IRRF  Propaganda") == "IRRF Propaganda"


def test_truncamento_acontece_em_Python_e_nao_no_banco():
    """`bi_fiscal_imposto.nome_canonico` é VARCHAR(40). Deixar o PostgreSQL
    recusar derrubaria a importação inteira daquela empresa por causa de um
    rótulo."""
    longo = impostos.canonizar(99999, "X" * 120)
    assert len(longo) <= impostos.TAMANHO_MAXIMO


def test_nenhum_rotulo_do_mapa_estoura_a_coluna():
    for codigo, nome in impostos.CANONICOS.items():
        assert len(nome) <= impostos.TAMANHO_MAXIMO, f"codi_imp={codigo}"


# ── O modelo do documento ────────────────────────────────────────────────────


def test_modelo_nao_e_especie():
    """A armadilha nº 4 do SYNC-001, e ela é literal: `codi_esp = 57` é NFS-e,
    enquanto o MODELO '57' é CT-e.

    É por isso que `rotulo()` recebe **string** e nunca int — passar o
    `codi_esp` aqui agruparia nota de serviço com conhecimento de transporte,
    e o total continuaria fechando.
    """
    assert "CT-e" in modelos.rotulo("57")
    assert modelos.rotulo("57") != modelos.rotulo("03")


def test_modelo_vazio_vira_ZZ():
    """Espécie fora do catálogo entra como ZZ e a linha SOBREVIVE — perdê-la
    tiraria valor do KPI, e o log diz o que rodar para corrigir."""
    assert modelos.normalizar(None) == "ZZ"
    assert modelos.normalizar("") == "ZZ"
    assert modelos.normalizar("   ") == "ZZ"


def test_modelo_e_normalizado_para_dois_caracteres():
    """A coluna é CHAR(2); a origem entrega com espaço de largura fixa."""
    assert modelos.normalizar(" 55 ") == "55"
    for codigo in modelos.ROTULOS:
        assert len(codigo) == 2, codigo


def test_rotulo_de_modelo_desconhecido_nao_explode():
    assert modelos.rotulo("XX")


# ── O documento do participante ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("12.345.678/0001-81", "12345678000181"),
        ("336.449.558-08", "33644955808"),
        ("  12345678000181  ", "12345678000181"),
        ("", None),
        (None, None),
        ("///---", None),  # pontuação sem dígito nenhum
    ],
)
def test_documento_fica_so_com_digitos(entrada, esperado):
    """`documento` é a chave do ranking consolidado entre empresas do grupo —
    o mesmo fornecedor tem `codi_for` diferente em cada uma. Com pontuação, o
    mesmo CNPJ viraria duas linhas no ranking, cada uma com metade do valor."""
    assert fiscal_dimensoes._documento(entrada) == esperado


# ── O teto, agora configurável ───────────────────────────────────────────────


def test_teto_zero_desliga(settings):
    """O default deste projeto. A carga em lote é o trabalho dele — e o que
    protege contra execução duplicada é a trava de sessão, não o teto."""
    assert settings.sinc_teto_de_empresas == 0


@pytest.mark.parametrize(
    "modulo",
    ["fiscal_apuracao", "fiscal_dimensoes", "fiscal_movimento", "fiscal_produto"],
)
def test_os_quatro_leem_o_teto_da_configuracao(modulo):
    """Guarda contra a constante voltar por cópia: se alguém recopiar um
    importador do Portal sem reaplicar a mudança, o teto de 20 volta em
    silêncio e a carga inicial passa a ser recusada."""
    from importlib import import_module
    from pathlib import Path

    fonte = import_module(f"app.importacao.importadores.{modulo}")
    codigo = Path(fonte.__file__).read_text(encoding="utf-8")
    assert "TETO_DE_EMPRESAS = 20" not in codigo
    assert "sinc_teto_de_empresas" in codigo
