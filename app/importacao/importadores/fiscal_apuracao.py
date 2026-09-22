"""Importador da apuração de impostos: Domínio → `bi_fiscal_apuracao`.

É 1:1 com `efsdoimp` — 207.601 linhas no parque inteiro, a tabela mais barata do
módulo — e alimenta a Árvore de Impostos, a tabela de Impostos Mensais e o card
de ISS.

**Vem antes de `fiscal_produto` no `REGISTRO` embora seja mais independente**,
pelo mesmo motivo que `contabil_dfc` vem antes de `contabil_lancamentos`: é o que
a pessoa vê. Com movimento e sem apuração, a tabela "Impostos Mensais" abre
vazia, e quem segue a lista de cima para baixo nunca encontra esse estado.

**`saldo_a_recolher` é a guia; `saldo_antes_deducoes` não é.** As duas vêm, e
guardá-las juntas é o que torna a regra verificável — como `movimento` e
`movimento_total` em `bi_saldo_mensal`. Medido no 1T/2026: COFINS Lucro Real tem
R$ 1.022.635,84 de guia contra R$ 1.630.802,40 antes das deduções, 59,5% a mais.
Trocar as duas não gera erro nenhum.

**Não há medida de imposto PAGO, e a ausência é deliberada.** `efsdoimp.dpag_sim`
tem zero preenchimentos desde 2022 e `efpagimp` registrou 13 pagamentos em 2026
contra 962 em 2022: o registro de baixa no Domínio parou. Uma coluna de valor
pago ficaria zerada e alguém a leria como "nada foi pago" em vez de "não se
sabe", e um card de imposto em aberto mostraria 100% de tudo.
"""

from __future__ import annotations

import logging

from ...config import get_settings
from ...data.connection import get_connection, linhas_dict, transacao
from ...data.sql import carregar_sql
from ...shared.errors import ErroValidacao
from .. import dominio

logger = logging.getLogger(__name__)

CHAVE = "fiscal_apuracao"
NOME = "Apuração de impostos"
DESCRICAO = (
    "O saldo apurado de cada imposto por competência — o valor da guia. "
    "Alimenta a Árvore de Impostos e a tabela de Impostos Mensais."
)
FONTE = "Domínio · efsdoimp"

# O teto do Portal (20) virou CONFIGURAÇÃO aqui — ver `sinc_teto_de_empresas`
# em `app/config.py`. Lá ele existe porque a rota roda síncrona dentro da
# requisição e o gunicorn corta; aqui a carga em lote é o trabalho, e a
# mensagem de erro do próprio Portal já mandava usar o CLI para isso.

# Os DOZE componentes da apuração, de `efsdoimp`. A ordem é a da demonstração,
# do topo para o rodapé — ela não é funcional, e existe para que quem comparar
# esta lista com o relatório do Domínio percorra os dois no mesmo sentido.
#
# Eles não se derivam dos saldos que já vinham: saldo é o resultado da conta, e
# estas são as parcelas. Ver o cabeçalho de `apuracao.sql` e a migration
# `0019_apuracao_componentes`.
COMPONENTES = (
    "saldo_credor_anterior",
    "debito_saidas",
    "outros_debitos",
    "estorno_creditos",
    "credito_entradas",
    "outros_creditos",
    "credito_presumido",
    "estorno_debitos",
    "outros_acrescimos",
    "outras_deducoes",
    "diferido_anterior",
    "diferido_periodo",
)

# A tabela de onde sai o ICMS de documentos extemporâneos, POR UF — e o mapa é a
# lista de quem TEM a coluna, não a de quem tem a tabela.
#
# `EFSDOIMP_ESTADUAL_<UF>` existe para os 27 estados; só estes sete declaram
# `ICMS_DOCUMENTOS_EXTEMPORANEOS_RECOLHER`. Nos outros a consulta não volta
# vazia — ela **morre** com erro de coluna inexistente, e é por isso que o mapa
# é uma condição de execução e não um `LEFT JOIN`.
#
# Medido em 18/09/2026 contra o parque: das onze UFs com apuração, só MS (409 de
# 445 empresas) e PE (1) estão aqui. SP, MT, PA, SC, MG, PR, CE, TO e RS ficam de
# fora, e as empresas delas gravam `NULL` — que naquela coluna significa "a
# origem não tem esta coluna para esta UF", e nunca zero.
TABELA_DE_EXTEMPORANEO_POR_UF = {
    "GO": "EFSDOIMP_ESTADUAL_GO",
    "MS": "EFSDOIMP_ESTADUAL_MS",
    "PB": "EFSDOIMP_ESTADUAL_PB",
    "PE": "EFSDOIMP_ESTADUAL_PE",
    "PI": "EFSDOIMP_ESTADUAL_PI",
    "RJ": "EFSDOIMP_ESTADUAL_RJ",
    "RO": "EFSDOIMP_ESTADUAL_RO",
}


# ── Estado local ─────────────────────────────────────────────────────────────


def _empresas_habilitadas() -> list[dict]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id_empresa,
                   e.razao_social,
                   e.nome_fantasia,
                   e.cnpj,
                   COUNT(a.codi_imp) AS linhas,
                   MAX(a.competencia) AS competencia_max
              FROM empresas e
              JOIN bi_empresa_fiscal bf ON bf.id_empresa = e.id_empresa
              LEFT JOIN bi_fiscal_apuracao a ON a.id_empresa = e.id_empresa
             WHERE e.ativo
             GROUP BY e.id_empresa, e.razao_social, e.nome_fantasia, e.cnpj
             ORDER BY COALESCE(e.razao_social, ''), e.id_empresa
            """
        )
        return linhas_dict(cur)


def pendentes(busca: str | None = None) -> list[dict]:
    """Empresas habilitadas e **sem** apuração importada."""
    alvo = (busca or "").strip().lower()
    disponiveis = []

    for empresa in _empresas_habilitadas():
        if empresa["linhas"]:
            continue
        if alvo and not (
            alvo in (empresa["razao_social"] or "").lower()
            or alvo in (empresa["nome_fantasia"] or "").lower()
            or alvo in (empresa["cnpj"] or "")
            or alvo == str(empresa["id_empresa"])
        ):
            continue
        disponiveis.append(
            {
                "id_empresa": empresa["id_empresa"],
                "razao_social": empresa["razao_social"],
                "nome_fantasia": empresa["nome_fantasia"],
                "cnpj": empresa["cnpj"],
            }
        )
    return disponiveis


def resumo() -> dict:
    empresas = _empresas_habilitadas()
    com_apuracao = [e for e in empresas if e["linhas"]]

    dados: dict = {
        "rotulo": "Empresas com apuração",
        "valor": f"{len(com_apuracao)} de {len(empresas)}",
    }

    if not empresas:
        dados["alerta"] = (
            "Nenhuma empresa habilitada no BI Fiscal. Rode antes o importador "
            "'Cadastros fiscais da empresa'."
        )
        return dados

    if not com_apuracao:
        dados["alerta"] = (
            "As empresas estão habilitadas, mas nenhuma tem apuração. Sem ela a "
            "Árvore de Impostos e a tabela de Impostos Mensais abrem vazias."
        )
        return dados

    competencias = [e["competencia_max"] for e in com_apuracao if e["competencia_max"]]
    if competencias:
        ultima = max(competencias)
        dados["detalhe"] = f"última guia {ultima.month:02d}/{ultima.year}"
    return dados


# ── Execução ─────────────────────────────────────────────────────────────────


def _extemporaneo(consultar, id_empresa: int) -> dict[tuple, object]:
    """O ICMS de documentos extemporâneos, por `(imposto, competência, periodicidade)`.

    Devolve `{}` quando a UF da empresa não está no mapa, e um mapa PARCIAL
    quando está: a tabela estadual tem uma linha por linha de ICMS, e não por
    linha de apuração. Medido na 510, de 432 linhas de `efsdoimp` só 31 têm
    extensão em MS — e são 31 das 32 de ICMS.

    O que o chamador grava para toda linha sem par é `NULL`, e ele significa a
    frase LARGA: "a origem não informa extemporâneo para esta linha". As três
    causas — UF sem a coluna, linha que não é de ICMS, linha de ICMS sem extensão
    — são indistinguíveis daqui e não precisam ser distinguidas: em todas, zerar
    afirmaria que não há documento extemporâneo a recolher. É a mesma regra que
    mantém a coluna de imposto PAGO fora desta tabela.

    A UF vem do Domínio porque o portal não a guarda — ver `empresa_uf.sql`.
    """
    uf = consultar(carregar_sql("fiscal/dominio/empresa_uf.sql"), (id_empresa,))
    sigla = (uf[0]["uf"] or "").strip().upper() if uf else ""
    tabela = TABELA_DE_EXTEMPORANEO_POR_UF.get(sigla)
    if not tabela:
        return {}

    # O identificador entra por formatação porque placeholder não vale para nome
    # de tabela em dialeto nenhum. O valor sai do mapa acima, que é constante do
    # código — nunca de entrada.
    sql = carregar_sql("fiscal/dominio/apuracao_extemporaneo.sql").format(tabela=tabela)
    return {
        (int(linha["codi_imp"]), linha["competencia"], int(linha["periodicidade"] or 0)): linha[
            "extemporaneo_recolher"
        ]
        for linha in consultar(sql, (id_empresa,))
        if linha["competencia"] is not None
    }


def _ler(consultar, id_empresa: int) -> list[dict]:
    linhas = consultar(carregar_sql("fiscal/dominio/apuracao.sql"), (id_empresa,))
    extemporaneo = _extemporaneo(consultar, id_empresa)
    return [
        {
            "id_empresa": id_empresa,
            "codi_imp": int(linha["codi_imp"]),
            "competencia": linha["competencia"],
            "periodicidade": int(linha["periodicidade"] or 0),
            "saldo_a_recolher": linha["saldo_a_recolher"] or 0,
            "saldo_antes_deducoes": linha["saldo_antes_deducoes"] or 0,
            "saldo_credor": linha["saldo_credor"] or 0,
            "base": linha["base"] or 0,
            "aliquota": linha["aliquota"] or 0,
            "vencimento": linha["vencimento"],
            # Os componentes caem para zero quando a origem manda NULL, e isso é
            # diferente do NULL da coluna do portal: aqui a linha EXISTE e o
            # Domínio não preencheu a parcela; lá a linha nunca foi reimportada.
            **{campo: linha[campo] or 0 for campo in COMPONENTES},
            # E este NÃO cai para zero: `None` é o estado "a origem não informa
            # extemporâneo para esta linha", e é o que a tela lê para omitir a
            # linha do rodapé em vez de mostrar um zero que ninguém apurou.
            "extemporaneo_recolher": extemporaneo.get(
                (
                    int(linha["codi_imp"]),
                    linha["competencia"],
                    int(linha["periodicidade"] or 0),
                )
            ),
        }
        for linha in linhas
        if linha["competencia"] is not None
    ]


def _gravar(id_empresa: int, linhas: list[dict]) -> int:
    """Recarrega a apuração da empresa.

    Recarga e não upsert, como no movimento: competência reaberta e zerada na
    origem precisa sair, senão a Árvore de Impostos segue somando uma guia que
    deixou de existir.
    """
    with transacao() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM bi_fiscal_apuracao WHERE id_empresa = %s", (id_empresa,))
        if linhas:
            # As colunas novas entram pela lista, e não escritas à mão duas
            # vezes: são treze, e um par que saísse de ordem gravaria o débito no
            # crédito sem que nada acusasse.
            extras = (*COMPONENTES, "extemporaneo_recolher")
            cur.executemany(
                f"""
                INSERT INTO bi_fiscal_apuracao
                       (id_empresa, codi_imp, competencia, periodicidade,
                        saldo_a_recolher, saldo_antes_deducoes, saldo_credor,
                        base, aliquota, vencimento,
                        {", ".join(extras)})
                VALUES (%(id_empresa)s, %(codi_imp)s, %(competencia)s, %(periodicidade)s,
                        %(saldo_a_recolher)s, %(saldo_antes_deducoes)s, %(saldo_credor)s,
                        %(base)s, %(aliquota)s, %(vencimento)s,
                        {", ".join(f"%({campo})s" for campo in extras)})
                ON CONFLICT (id_empresa, codi_imp, competencia, periodicidade) DO NOTHING
                """,
                linhas,
            )

        # `competencia_apuracao_max` é desta tabela, e fica SEPARADA de
        # `competencia_max`, que é do movimento. O desencontro entre as duas é o
        # que mede a pendência "faturou e não apurou" — colapsá-las numa coluna
        # só apagaria o indicador.
        cur.execute(
            """
            UPDATE bi_empresa_fiscal bf
               SET competencia_apuracao_max = (
                       SELECT MAX(competencia) FROM bi_fiscal_apuracao
                        WHERE id_empresa = %(id_empresa)s
                   )
             WHERE bf.id_empresa = %(id_empresa)s
            """,
            {"id_empresa": id_empresa},
        )
    return len(linhas)


def _selecionar_alvo(incluir: bool, ids: list[int] | None):
    empresas = {e["id_empresa"]: e for e in _empresas_habilitadas()}
    pedidas = ids if ids else list(empresas)
    sem_cadastro = [i for i in pedidas if i not in empresas]
    candidatas = [i for i in pedidas if i in empresas]

    if incluir:
        return candidatas, sem_cadastro, []
    alvo = [i for i in candidatas if empresas[i]["linhas"]]
    return alvo, sem_cadastro, [i for i in candidatas if not empresas[i]["linhas"]]


def executar(incluir: bool = False, dry_run: bool = False, ids: list[int] | None = None) -> dict:
    """Traz a apuração de impostos das empresas habilitadas."""
    alvo, sem_cadastro, sem_apuracao = _selecionar_alvo(incluir, ids)

    if sem_cadastro and ids:
        raise ErroValidacao(
            "Estas empresas não têm cadastro fiscal importado: "
            f"{', '.join(str(i) for i in sem_cadastro)}. "
            "Ou o importador 'Cadastros fiscais da empresa' ainda não rodou "
            "para elas, ou o Domínio não tem movimento fiscal para elas — e "
            "nesse caso rodá-lo de novo não muda nada, porque não há o que "
            "trazer. Se ele ler zero linhas, o BI Fiscal não se aplica a "
            "essa empresa."
        )

    teto = get_settings().sinc_teto_de_empresas
    if teto and len(alvo) > teto:
        raise ErroValidacao(
            f"Selecione no máximo {teto} empresas por execução — foram pedidas "
            f"{len(alvo)}. Aumente ou desligue SINC_TETO_DE_EMPRESAS no .env "
            "(0 desliga)."
        )

    resultado = {"lidas": 0, "incluidas": 0, "atualizadas": 0, "ignoradas": 0}
    if sem_apuracao:
        resultado["sem_apuracao"] = sem_apuracao

    if not alvo:
        logger.info(
            "Importação de apuração fiscal%s: nenhuma empresa alvo (incluir=%s, ids=%s)",
            " (simulação)" if dry_run else "",
            incluir,
            ids,
        )
        return resultado

    with dominio.sessao() as consultar:
        lidos = {id_empresa: _ler(consultar, id_empresa) for id_empresa in alvo}

    for id_empresa, linhas in lidos.items():
        resultado["lidas"] += len(linhas)
        if dry_run:
            resultado["incluidas"] += len(linhas)
            continue
        resultado["incluidas"] += _gravar(id_empresa, linhas)

    logger.info(
        "Importação de apuração fiscal%s em %s empresa(s): %s",
        " (simulação)" if dry_run else "",
        len(alvo),
        resultado,
    )
    return resultado
