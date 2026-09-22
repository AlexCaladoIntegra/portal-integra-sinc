"""Importador do plano de contas contábil: Domínio → `bi_conta`, `bi_grupo_dre`
e `bi_empresa_contabil`.

As três tabelas vêm juntas porque descrevem a mesma coisa — a **estrutura**
contábil de uma empresa — e porque nenhuma delas é útil sozinha: sem o plano os
saldos não têm onde se prender (a FK de `bi_saldo_mensal` é composta), sem a
estrutura do DRE o plano não sabe em que linha do demonstrativo cada conta cai,
e sem o fechamento a tela não sabe onde termina a janela do período.

Divisão de propriedade dos dados:

    do Domínio : bi_conta, bi_grupo_dre, bi_empresa_contabil.fechamento_data
    do portal  : bi_conceito_conta (o mapeamento de conceitos), painel_layout

Este importador **não toca** em `bi_conceito_conta`: o mapeamento é uma decisão
do analista, e uma reimportação de estrutura não pode desfazê-la.

Vocabulário (`incluir` / `dry_run` / `ids`), com uma diferença deliberada em
relação ao importador de `empresas`: aqui `incluir` **não exige `ids`**. Lá a
recusa existe porque a origem tem 917 empresas e adotar todas é uma decisão de
quem administra o portal, não de quem clica em Importar. Aqui o conjunto já foi
decidido — são as empresas que estão no portal — e trazer a estrutura de todas
elas é exatamente a rotina esperada.
"""

from __future__ import annotations

import logging

from ...data.connection import get_connection, linhas_dict, transacao
from ...data.sql import carregar_sql
from .. import dominio

logger = logging.getLogger(__name__)

CHAVE = "contabil_plano"
NOME = "Plano de contas contábil"
DESCRICAO = (
    "Plano de contas, estrutura do DRE e data de fechamento das empresas do "
    "portal. Pré-requisito dos saldos."
)
FONTE = "Domínio · ctcontas, CtGruposDre, ctparmto"


# ── Leitura da origem ────────────────────────────────────────────────────────


def _ler_empresa(consultar, id_empresa: int) -> dict:
    """Estrutura contábil de uma empresa, em três consultas na mesma conexão."""
    contas = consultar(carregar_sql("bi/dominio/plano_contas.sql"), (id_empresa,))
    grupos = consultar(carregar_sql("bi/dominio/dre_estrutura.sql"), (id_empresa,))
    fechamento = consultar(carregar_sql("bi/dominio/fechamento.sql"), (id_empresa,))

    return {
        "id_empresa": id_empresa,
        "contas": [
            {
                "codi_cta": linha["codi_cta"],
                "nome": (linha["nome_cta"] or "").strip() or None,
                "clas_cta": (linha["clas_cta"] or "").strip(),
                "tipo": (linha["tipo_cta"] or "").strip().upper(),
                "grupo_dre": linha["grdre_efetivo"],
                # NÃO é o mesmo valor da linha acima, e a diferença é a razão de
                # a coluna existir: `grupo_dre` é o grupo já resolvido pela
                # herança e só sai em analítica; `grupo_dre_proprio` é o que a
                # PRÓPRIA conta declara, e é ele que marca a sintética que serve
                # de RAIZ à subárvore do drill-down do DRE (BIC-003).
                "grupo_dre_proprio": linha["grdre_proprio"],
            }
            for linha in contas
            # Conta sem classificação não tem como ser agregada por prefixo nem
            # posicionada no Balanço — entra como ignorada, não como linha nula.
            if (linha["clas_cta"] or "").strip()
            and (linha["tipo_cta"] or "").strip().upper() in ("A", "S")
        ],
        "grupos": [
            {
                "codigo": linha["codigo"],
                "sequencia": linha["sequencia"],
                "descricao": (linha["descricao"] or "").strip(),
                "operacao": linha["operacao"],
                "codigo_pai": linha["codigo_pai"],
                "nivel": linha["nivel"],
            }
            for linha in grupos
            if linha["codigo"] is not None and (linha["descricao"] or "").strip()
        ],
        "fechamento_data": fechamento[0]["fechamento_data"] if fechamento else None,
        "contas_lidas": len(contas),
        "grupos_lidos": len(grupos),
    }


# ── Estado local ─────────────────────────────────────────────────────────────


def _empresas_do_portal(apenas_ativas: bool = True) -> list[dict]:
    sql = """
        SELECT e.id_empresa,
               e.razao_social,
               e.nome_fantasia,
               e.cnpj,
               (bc.id_empresa IS NOT NULL) AS tem_plano
          FROM empresas e
          LEFT JOIN bi_empresa_contabil bc ON bc.id_empresa = e.id_empresa
    """
    if apenas_ativas:
        sql += " WHERE e.ativo"
    sql += " ORDER BY COALESCE(e.razao_social, ''), e.id_empresa"

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return linhas_dict(cur)


def pendentes(busca: str | None = None) -> list[dict]:
    """Empresas do portal que ainda **não têm** estrutura contábil importada.

    É a lista que a tela oferece para marcar. Já exclui as importadas, então
    não há como trazer a mesma empresa duas vezes nem ver linha repetida.
    """
    alvo = (busca or "").strip().lower()
    disponiveis = []

    for empresa in _empresas_do_portal():
        if empresa["tem_plano"]:
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
    """Estado local, mostrado no card. Responde de antemão por que uma
    importação de saldos pode não ter o que fazer."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT (SELECT COUNT(*) FROM empresas WHERE ativo)   AS empresas,
                   (SELECT COUNT(*) FROM bi_empresa_contabil)    AS com_plano,
                   (SELECT COUNT(*) FROM bi_conta)               AS contas,
                   (SELECT COUNT(DISTINCT id_empresa) FROM bi_conta
                     WHERE grupo_dre_proprio IS NOT NULL)        AS com_raiz
            """
        )
        estado = linhas_dict(cur)[0]

    dados = {
        "rotulo": "Empresas com plano importado",
        "valor": f"{estado['com_plano']} de {estado['empresas']}",
        "detalhe": f"{estado['contas']} contas · {estado['com_raiz']} com raiz do DRE",
    }
    if not estado["empresas"]:
        dados["alerta"] = (
            "O portal ainda não tem empresa cadastrada. Importe empresas antes — "
            "sem elas não há plano de contas a trazer."
        )
    elif not estado["com_plano"]:
        dados["alerta"] = (
            "Nenhuma empresa tem plano de contas importado. Marque as empresas na "
            "lista abaixo: os saldos e a tela do BI dependem disto."
        )
    elif not estado["com_raiz"]:
        # `grupo_dre_proprio` chegou no BIC-003, depois de o plano já ter sido
        # importado uma vez. Quem não reimportar fica com a coluna nula, e o
        # sintoma é MUDO onde aparece: a árvore do DRE não acha onde começar e
        # sobe até o topo do plano, arrastando níveis comuns a vários grupos.
        #
        # A condição é **nenhuma** empresa com raiz, e não "menos empresas com
        # raiz do que com plano". Medido depois da reimportação de 04/09/2026:
        # 304 das 417 têm raiz, e as outras 113 simplesmente não têm conta
        # nenhuma declarando grupo na origem — reimportar não muda isso. Com a
        # comparação, o alerta ficaria ligado para sempre, e alerta que sempre
        # dispara é alerta que ninguém lê. Zero, ao contrário, só acontece se a
        # reimportação não rodou.
        dados["alerta"] = (
            "Nenhuma empresa tem a raiz do DRE preenchida — o plano foi importado antes "
            "desta coluna existir. Reimporte: sem ela, a árvore de contas da tela de DRE "
            "começa no topo do plano em vez de na linha do demonstrativo."
        )
    return dados


# ── Execução ─────────────────────────────────────────────────────────────────


def _selecionar_alvo(incluir: bool, ids: list[int] | None) -> tuple[list[int], list[int]]:
    """Empresas a processar e as que foram pedidas mas não estão no portal.

    Sem `incluir`, só as que **já** têm estrutura importada: é a rotina de
    atualização. Com `incluir`, também as que ainda não têm.
    """
    empresas = _empresas_do_portal()
    no_portal = {e["id_empresa"]: e for e in empresas}

    if ids:
        fora = [i for i in ids if i not in no_portal]
        candidatas = [i for i in ids if i in no_portal]
    else:
        fora = []
        candidatas = list(no_portal)

    if incluir:
        return candidatas, fora
    return [i for i in candidatas if no_portal[i]["tem_plano"]], fora


def _gravar(dados: dict) -> tuple[int, int]:
    """Persiste a estrutura de uma empresa. Devolve (incluídas, atualizadas) de
    contas — a contagem segue a entidade principal do importador.

    Tudo numa transação só: um plano de contas gravado pela metade, com a
    estrutura do DRE antiga, produziria demonstrativo silenciosamente errado.

    Nenhum UPDATE escreve `updated_at` — é do trigger `set_updated_at()`.
    """
    id_empresa = dados["id_empresa"]

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT codi_cta FROM bi_conta WHERE id_empresa = %s", (id_empresa,))
        existentes = {linha[0] for linha in cur.fetchall()}

    novas = sum(1 for c in dados["contas"] if c["codi_cta"] not in existentes)
    atualizadas = len(dados["contas"]) - novas

    with transacao() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO bi_empresa_contabil (id_empresa, fechamento_data)
            VALUES (%s, %s)
            ON CONFLICT (id_empresa) DO UPDATE
               SET fechamento_data = EXCLUDED.fechamento_data
            """,
            (id_empresa, dados["fechamento_data"]),
        )

        if dados["contas"]:
            cur.executemany(
                """
                INSERT INTO bi_conta (id_empresa, codi_cta, nome, clas_cta, tipo,
                                      grupo_dre, grupo_dre_proprio)
                VALUES (%(id_empresa)s, %(codi_cta)s, %(nome)s, %(clas_cta)s,
                        %(tipo)s, %(grupo_dre)s, %(grupo_dre_proprio)s)
                ON CONFLICT (id_empresa, codi_cta) DO UPDATE
                   SET nome              = EXCLUDED.nome,
                       clas_cta          = EXCLUDED.clas_cta,
                       tipo              = EXCLUDED.tipo,
                       grupo_dre         = EXCLUDED.grupo_dre,
                       grupo_dre_proprio = EXCLUDED.grupo_dre_proprio
                """,
                [{**conta, "id_empresa": id_empresa} for conta in dados["contas"]],
            )

        if dados["grupos"]:
            cur.executemany(
                """
                INSERT INTO bi_grupo_dre (id_empresa, codigo, sequencia, descricao,
                                          operacao, codigo_pai, nivel)
                VALUES (%(id_empresa)s, %(codigo)s, %(sequencia)s, %(descricao)s,
                        %(operacao)s, %(codigo_pai)s, %(nivel)s)
                ON CONFLICT (id_empresa, codigo) DO UPDATE
                   SET sequencia  = EXCLUDED.sequencia,
                       descricao  = EXCLUDED.descricao,
                       operacao   = EXCLUDED.operacao,
                       codigo_pai = EXCLUDED.codigo_pai,
                       nivel      = EXCLUDED.nivel
                """,
                [{**grupo, "id_empresa": id_empresa} for grupo in dados["grupos"]],
            )

        # Conta que saiu do plano (inativada no Domínio) some do portal, junto
        # com os saldos dela via CASCADE: manter conta que a origem não tem mais
        # deixaria o Balanço somando saldo de conta encerrada.
        codigos = [c["codi_cta"] for c in dados["contas"]]
        if codigos:
            cur.execute(
                "DELETE FROM bi_conta WHERE id_empresa = %s AND codi_cta <> ALL(%s)",
                (id_empresa, codigos),
            )

    return novas, atualizadas


def executar(incluir: bool = False, dry_run: bool = False, ids: list[int] | None = None) -> dict:
    """Traz a estrutura contábil das empresas e devolve a contagem.

    Por padrão **só atualiza** as empresas que já têm estrutura importada.
    `incluir=True` traz também as que ainda não têm; `ids` restringe.

    As contagens são de **contas** — a entidade principal. `lidas` inclui as
    linhas de estrutura do DRE, porque elas também vêm da origem nesta mesma
    execução e ignorá-las faria a contagem parecer menor do que o trabalho.
    """
    alvo, fora_do_portal = _selecionar_alvo(incluir, ids)

    resultado = {"lidas": 0, "incluidas": 0, "atualizadas": 0, "ignoradas": 0}
    if fora_do_portal:
        # Sem esta lista, o resultado diria só "ignoradas: N" e não explicaria
        # que a empresa pedida simplesmente não está cadastrada no portal.
        resultado["nao_cadastradas"] = fora_do_portal

    if not alvo:
        logger.info(
            "Importação do plano de contas%s: nenhuma empresa alvo (incluir=%s, ids=%s)",
            " (simulação)" if dry_run else "",
            incluir,
            ids,
        )
        return resultado

    with dominio.sessao() as consultar:
        lidos = [_ler_empresa(consultar, id_empresa) for id_empresa in alvo]

    for dados in lidos:
        resultado["lidas"] += dados["contas_lidas"] + dados["grupos_lidos"]
        resultado["ignoradas"] += dados["contas_lidas"] - len(dados["contas"])

        if dry_run:
            # Na simulação, tudo que a origem tem conta como inclusão: sem
            # gravar não há como saber o que já existia sem repetir a consulta
            # local, e a simulação existe para dizer o tamanho do trabalho.
            resultado["incluidas"] += len(dados["contas"])
            continue

        if not dados["grupos"]:
            # Empresa sem estrutura de DRE cadastrada no Domínio: o plano entra,
            # mas o demonstrativo sairia vazio e ninguém saberia por quê.
            logger.warning(
                "Empresa %s não tem estrutura de DRE em CtGruposDre — os blocos de "
                "resultado da tela ficarão sem dado.",
                dados["id_empresa"],
            )

        novas, atualizadas = _gravar(dados)
        resultado["incluidas"] += novas
        resultado["atualizadas"] += atualizadas

    logger.info(
        "Importação do plano de contas%s em %s empresa(s): %s",
        " (simulação)" if dry_run else "",
        len(alvo),
        resultado,
    )
    return resultado
