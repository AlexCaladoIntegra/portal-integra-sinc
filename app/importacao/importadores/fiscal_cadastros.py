"""Importador dos cadastros fiscais GLOBAIS: Domínio → espécie e CFOP.

É o único importador do portal **sem seleção de empresa**, e não por
simplificação: `efespecies` e `efnatureza` não têm `codi_emp` na origem. São as
duas dimensões de topo do BI Fiscal, e é isso que faz a consolidação do módulo
atravessá-las sem assimetria nenhuma — do lado contábil o eixo principal (o
plano de contas) é por empresa, e é daí que nasce toda a regra
representante/membros do `Escopo`.

Por isso também não expõe `pendentes()`: não há o que escolher, são 1.611 linhas
no total e a execução leva segundos.

**Vem primeiro no `REGISTRO` porque é o único que pode**: não depende de nada, e
`bi_fiscal_nota_mensal` guarda `modelo` desnormalizado — sem o catálogo de
espécie, o importador de movimento não sabe qual modelo gravar na linha do fato.
"""

from __future__ import annotations

import logging

from ...data.connection import get_connection, linhas_dict, transacao
from ...data.sql import carregar_sql
from ...fiscal import modelos
from .. import dominio

logger = logging.getLogger(__name__)

CHAVE = "fiscal_cadastros"
NOME = "Cadastros fiscais globais"
DESCRICAO = (
    "Espécie de documento fiscal e CFOP. São globais no Domínio (não têm "
    "empresa) e alimentam os filtros e o gráfico de espécie do BI Fiscal."
)
FONTE = "Domínio · efespecies, efnatureza"


# ── Estado local ─────────────────────────────────────────────────────────────


def _contagens() -> dict:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT (SELECT COUNT(*) FROM bi_fiscal_especie) AS especies,
                   (SELECT COUNT(*) FROM bi_fiscal_cfop)    AS cfops
            """
        )
        return linhas_dict(cur)[0]


def resumo() -> dict:
    """Estado local: quantas espécies e quantos CFOPs o portal conhece."""
    atual = _contagens()
    dados: dict = {
        "rotulo": "Catálogos fiscais",
        "valor": f"{atual['especies']} espécies · {atual['cfops']} CFOPs",
    }
    if not atual["especies"] or not atual["cfops"]:
        dados["alerta"] = (
            "Os catálogos ainda não foram importados. Rode este importador antes "
            "dos demais do BI Fiscal: sem a espécie, o movimento não sabe qual "
            "modelo de documento gravar."
        )
    return dados


# ── Execução ─────────────────────────────────────────────────────────────────


def _ler_especies(consultar) -> list[dict]:
    """As espécies, já com o modelo normalizado.

    `modelos.normalizar` resolve o `char(2)` com espaço à direita e devolve
    `"ZZ"` para vazio. A normalização acontece aqui, e não na consulta, porque
    é regra do portal e tem teste sem banco.
    """
    linhas = consultar(carregar_sql("fiscal/dominio/especies.sql"))
    return [
        {
            "codi_esp": int(linha["codi_esp"]),
            "nome": (linha["nome_esp"] or "").strip(),
            "codigo_modelo": modelos.normalizar(linha["codigo_modelo"]),
        }
        for linha in linhas
    ]


def _ler_cfops(consultar) -> list[dict]:
    linhas = consultar(carregar_sql("fiscal/dominio/cfop.sql"))
    return [
        {
            "codi_nat": int(linha["codi_nat"]),
            "versao_nat": int(linha["versao_nat"]),
            "nome": (linha["nome_nat"] or "").strip(),
            "masc_nat": (linha["masc_nat"] or "").strip() or None,
        }
        for linha in linhas
    ]


def _gravar_especies(linhas: list[dict]) -> tuple[int, int]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT codi_esp FROM bi_fiscal_especie")
        existentes = {linha[0] for linha in cur.fetchall()}

    novas = sum(1 for x in linhas if x["codi_esp"] not in existentes)

    with transacao() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO bi_fiscal_especie (codi_esp, nome, codigo_modelo)
            VALUES (%(codi_esp)s, %(nome)s, %(codigo_modelo)s)
            ON CONFLICT (codi_esp) DO UPDATE
               SET nome          = EXCLUDED.nome,
                   codigo_modelo = EXCLUDED.codigo_modelo
            """,
            linhas,
        )
    return novas, len(linhas) - novas


def _gravar_cfops(linhas: list[dict]) -> tuple[int, int]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT codi_nat, versao_nat FROM bi_fiscal_cfop")
        existentes = {(linha[0], linha[1]) for linha in cur.fetchall()}

    novas = sum(1 for x in linhas if (x["codi_nat"], x["versao_nat"]) not in existentes)

    with transacao() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO bi_fiscal_cfop (codi_nat, versao_nat, nome, masc_nat)
            VALUES (%(codi_nat)s, %(versao_nat)s, %(nome)s, %(masc_nat)s)
            ON CONFLICT (codi_nat, versao_nat) DO UPDATE
               SET nome     = EXCLUDED.nome,
                   masc_nat = EXCLUDED.masc_nat
            """,
            linhas,
        )
    return novas, len(linhas) - novas


def executar(incluir: bool = False, dry_run: bool = False, ids: list[int] | None = None) -> dict:
    """Traz os dois catálogos globais.

    `incluir` e `ids` não se aplicam: a origem não tem empresa, então não há o
    que incluir nem o que restringir. A assinatura os mantém porque é o contrato
    do `REGISTRO` — o painel e o CLI chamam todos os importadores da mesma
    forma, e uma assinatura diferente obrigaria o orquestrador a saber de qual
    importador está falando.

    Mas eles não são engolidos em silêncio: uma seleção pedida e ignorada volta
    marcada no resultado. Sem isso, quem marcasse empresas e visse o mesmo
    número de linhas concluiria que a seleção funcionou.
    """
    resultado = {"lidas": 0, "incluidas": 0, "atualizadas": 0, "ignoradas": 0}
    if incluir or ids:
        resultado["selecao_ignorada"] = (
            "Este importador é global: espécie e CFOP não têm empresa no Domínio. "
            "A seleção não muda o que é trazido."
        )
        logger.info(
            "Cadastros fiscais: seleção ignorada (incluir=%s, ids=%s) — catálogo global.",
            incluir,
            ids,
        )

    with dominio.sessao() as consultar:
        especies = _ler_especies(consultar)
        cfops = _ler_cfops(consultar)

    resultado["lidas"] = len(especies) + len(cfops)

    if dry_run:
        resultado["incluidas"] = resultado["lidas"]
        logger.info("Importação de cadastros fiscais (simulação): %s", resultado)
        return resultado

    for linhas, gravar in ((especies, _gravar_especies), (cfops, _gravar_cfops)):
        if not linhas:
            continue
        novas, atualizadas = gravar(linhas)
        resultado["incluidas"] += novas
        resultado["atualizadas"] += atualizadas

    logger.info("Importação de cadastros fiscais: %s", resultado)
    return resultado
