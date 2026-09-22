"""Importador do movimento por produto: Domínio → `bi_fiscal_produto_mensal`.

Alimenta **um único bloco** da tela — "Total por Produto". Todos os outros saem
de `bi_fiscal_nota_mensal`, e é por isso que este é o último do `REGISTRO` e a
tela funciona inteira sem ele: a mesma relação que `contabil_lancamentos` tem
com o razão do BI Contábil, onde a tabela toda sai certa e um bloco fica vazio
com aviso.

**Traz os dois lados, saída e entrada.** A entrada é o dado que menos colapsa do
módulo — 4.652.037 itens viram 2.360.661 linhas (1,97x), contra 5,20x da saída —,
então ela custa 71% do tamanho da saída para 27% dos itens. Entra assim mesmo,
por decisão de produto de 11/09/2026: sem ela a tela responde "o que eu mais
vendo" e não "o que eu mais compro".

**O grão inclui CFOP e acumulador**, e não só o produto. Sem eles o bloco não
responderia aos dois filtros da barra, e ficaria mostrando o ranking do período
inteiro enquanto o resto da tela mostra o do filtro — dois números certos
discordando na mesma tela. Custo medido: +0,36% de linhas.

**`codi_pdi` sai da origem com espaço** (`char(14)`, "        SM66493"), e o TRIM
acontece no SQL dos dois lados — aqui e em `produtos.sql`. Sem ele o código
daqui nunca casaria com o do cadastro, e o ranking sairia mostrando o código no
lugar da descrição.
"""

from __future__ import annotations

import logging
from datetime import date

from psycopg2.extras import execute_values

from ...config import get_settings
from ...data.connection import get_connection, linhas_dict, transacao
from ...data.sql import carregar_sql
from ...shared.errors import ErroValidacao
from .. import dominio

logger = logging.getLogger(__name__)

CHAVE = "fiscal_produto"
NOME = "Movimento fiscal por produto"
DESCRICAO = (
    "O item da nota, agregado por mês, produto, CFOP e acumulador. "
    "Alimenta o bloco “Total por Produto” — a tela funciona sem ele."
)
FONTE = "Domínio · efmvspro, efmvepro"

# O teto do Portal (20) virou CONFIGURAÇÃO aqui — ver `sinc_teto_de_empresas`
# em `app/config.py`. Lá ele existe porque a rota roda síncrona dentro da
# requisição e o gunicorn corta; aqui a carga em lote é o trabalho, e a
# mensagem de erro do próprio Portal já mandava usar o CLI para isso.
LOTE_GRAVACAO = 5_000

TIPOS = (
    ("S", "fiscal/dominio/produto_saidas_mensais.sql"),
    ("E", "fiscal/dominio/produto_entradas_mensais.sql"),
)

_COLUNAS = (
    "id_empresa",
    "ano",
    "mes",
    "tipo",
    "codi_pdi",
    "codi_nat",
    "codi_acu",
    "qtd_itens",
    "quantidade",
    "valor_produtos",
    "valor_contabil",
    "base_icms",
    "valor_icms",
    "valor_icms_st",
    "valor_ipi",
    "valor_pis",
    "valor_cofins",
)

_MEDIDAS = _COLUNAS[7:]

ANO_MINIMO = 1990


def _ano_maximo() -> int:
    return date.today().year + 1


# ── Estado local ─────────────────────────────────────────────────────────────


def _empresas_habilitadas() -> list[dict]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id_empresa,
                   e.razao_social,
                   e.nome_fantasia,
                   e.cnpj,
                   COUNT(p.codi_pdi) AS linhas
              FROM empresas e
              JOIN bi_empresa_fiscal bf ON bf.id_empresa = e.id_empresa
              LEFT JOIN bi_fiscal_produto_mensal p ON p.id_empresa = e.id_empresa
             WHERE e.ativo
             GROUP BY e.id_empresa, e.razao_social, e.nome_fantasia, e.cnpj
             ORDER BY COALESCE(e.razao_social, ''), e.id_empresa
            """
        )
        return linhas_dict(cur)


def pendentes(busca: str | None = None) -> list[dict]:
    """Empresas habilitadas e **sem** movimento por produto importado."""
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
    com_produto = [e for e in empresas if e["linhas"]]

    dados: dict = {
        "rotulo": "Empresas com produto",
        "valor": f"{len(com_produto)} de {len(empresas)}",
    }

    if not empresas:
        dados["alerta"] = (
            "Nenhuma empresa habilitada no BI Fiscal. Rode antes o importador "
            "'Cadastros fiscais da empresa'."
        )
        return dados

    faltam = len(empresas) - len(com_produto)
    if faltam:
        dados["alerta"] = (
            f"{faltam} de {len(empresas)} empresa(s) sem movimento por produto. "
            "O bloco “Total por Produto” abre vazio para elas, com aviso — o resto "
            "da tela fica certo."
        )
    return dados


# ── Execução ─────────────────────────────────────────────────────────────────


def _ler(consultar, id_empresa: int, tipo: str, sql: str, contagem: dict) -> list[tuple]:
    linhas = consultar(carregar_sql(sql), (id_empresa,))
    aceitas = []

    for linha in linhas:
        ano = None if linha["ano"] is None else int(linha["ano"])
        mes = None if linha["mes"] is None else int(linha["mes"])
        codi_pdi = (linha["codi_pdi"] or "").strip()
        if ano is None or mes is None or not 1 <= mes <= 12 or not codi_pdi:
            # Item sem data utilizável ou sem produto. A CHECK do mês recusaria
            # a linha e derrubaria a empresa inteira; descartar e contar diz
            # mais, e o bloco que ela serve é o único afetado.
            contagem["descartadas"] += 1
            continue
        if not ANO_MINIMO <= ano <= _ano_maximo():
            contagem["competencia_implausivel"] += 1

        aceitas.append(
            (
                id_empresa,
                ano,
                mes,
                tipo,
                codi_pdi,
                int(linha["codi_nat"] or 0),
                int(linha["codi_acu"] or 0),
                *(linha[medida] or 0 for medida in _MEDIDAS),
            )
        )

    contagem["lidas"] += len(linhas)
    return aceitas


def _gravar(id_empresa: int, por_tipo: dict[str, list[tuple]]) -> int:
    """Recarrega as fatias (empresa, tipo), como o importador de movimento.

    `ON CONFLICT DO NOTHING` cobre a colisão que a origem pode produzir dentro
    de um mesmo lote: dois itens com o mesmo (mês, produto, CFOP, acumulador)
    já vêm agregados pela consulta, mas o TRIM do código pode unir dois códigos
    que só diferiam por espaço — e aí a chave repete.
    """
    total = 0
    with transacao() as conn, conn.cursor() as cur:
        for tipo, linhas in por_tipo.items():
            cur.execute(
                "DELETE FROM bi_fiscal_produto_mensal WHERE id_empresa = %s AND tipo = %s",
                (id_empresa, tipo),
            )
            if not linhas:
                continue
            execute_values(
                cur,
                f"INSERT INTO bi_fiscal_produto_mensal ({', '.join(_COLUNAS)}) VALUES %s "
                "ON CONFLICT (id_empresa, ano, mes, tipo, codi_pdi, codi_nat, codi_acu) "
                "DO NOTHING",
                linhas,
                page_size=LOTE_GRAVACAO,
            )
            total += len(linhas)
    return total


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
    """Traz o movimento por produto, dos dois lados."""
    alvo, sem_cadastro, sem_produto = _selecionar_alvo(incluir, ids)

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
    if sem_produto:
        resultado["sem_produto"] = sem_produto

    if not alvo:
        logger.info(
            "Importação de produto fiscal%s: nenhuma empresa alvo (incluir=%s, ids=%s)",
            " (simulação)" if dry_run else "",
            incluir,
            ids,
        )
        return resultado

    for id_empresa in alvo:
        contagem = {"lidas": 0, "descartadas": 0, "competencia_implausivel": 0}
        with dominio.sessao() as consultar:
            por_tipo = {
                tipo: _ler(consultar, id_empresa, tipo, sql, contagem) for tipo, sql in TIPOS
            }

        gravaveis = sum(len(v) for v in por_tipo.values())
        resultado["lidas"] += contagem["lidas"]
        resultado["ignoradas"] += contagem["lidas"] - gravaveis

        if contagem["competencia_implausivel"]:
            logger.warning(
                "Empresa %s: %s linha(s) de produto com ano implausível — a data do "
                "item está digitada errada no Domínio. Importadas como estão.",
                id_empresa,
                contagem["competencia_implausivel"],
            )

        if dry_run:
            resultado["incluidas"] += gravaveis
            continue

        resultado["incluidas"] += _gravar(id_empresa, por_tipo)

    logger.info(
        "Importação de produto fiscal%s em %s empresa(s): %s",
        " (simulação)" if dry_run else "",
        len(alvo),
        resultado,
    )
    return resultado
