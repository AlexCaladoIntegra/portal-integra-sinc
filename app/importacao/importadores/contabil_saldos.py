"""Importador dos saldos contábeis mensais: Domínio → `bi_saldo_mensal`.

Lê `ctlancto` agregado por (conta, ano, mês) nas medidas que as demonstrações
exigem — ver o cabeçalho de `bi/dominio/saldos_mensais.sql`, a migration
`0005_bi_contabil` e a `0009_bi_saldo_dc` para o porquê de nenhuma delas ser
redundância:

    movimento                       o líquido SEM encerramento  -> DRE
    movimento_total                 o líquido COM encerramento  -> Balanço
    debito_total / credito_total    as duas pernas separadas    -> Balancete

O par de pernas chegou na `0009` e nasce **anulável**: competência gravada
antes daquela revisão fica com `NULL` até a empresa ser reimportada. É o
`resumo()` que conta quantas faltam — com `DEFAULT 0` a tela mostraria um
Balancete zerado e plausível, sem erro e sem log.

**Histórico completo, sempre.** Não há recorte de período porque o Balanço
precisa da acumulação desde o primeiro lançamento da empresa: um recorte
deixaria o saldo de abertura de fora e o Balanço não fecharia. O custo é baixo
porque a agregação acontece na origem — 553 mil lançamentos da empresa 272 saem
como 3.338 linhas em ~240 ms.

Depende de `contabil_plano`: a FK de `bi_saldo_mensal` é composta
(`id_empresa`, `codi_cta`) e aponta para `bi_conta`. A dependência é verificada
antes de ler a origem, com mensagem própria — sem isso o erro chegaria ao
usuário como violação de chave estrangeira do PostgreSQL.
"""

from __future__ import annotations

import logging
from datetime import date

from ...data.connection import get_connection, linhas_dict, transacao
from ...data.sql import carregar_sql
from ...shared.errors import ErroValidacao
from .. import dominio

logger = logging.getLogger(__name__)

CHAVE = "contabil_saldos"
NOME = "Saldos contábeis mensais"
DESCRICAO = (
    "Movimento mensal por conta, no histórico completo da empresa. "
    "Alimenta os indicadores e os gráficos do BI Contábil."
)
FONTE = "Domínio · ctlancto"

# Faixa de competência plausível. Fora dela, a data do lançamento no Domínio
# está digitada errada — 2044 no lugar de 2024, 2202 no lugar de 2022. São
# poucas linhas (19 em 545 mil, na base do escritório) mas carregam valor
# real, então não são descartadas: ficam registradas no log para correção na
# origem. Ver `_ler`.
ANO_MINIMO = 1990


def _ano_maximo() -> int:
    """Ano corrente + 1: lançamento de provisão no ano seguinte é legítimo."""
    return date.today().year + 1


# ── Estado local ─────────────────────────────────────────────────────────────


def _empresas_com_plano() -> list[dict]:
    """Empresas do portal com estrutura contábil, e o estado dos saldos de cada.

    `competencia_max` ignora o ano implausível, e não é preciosismo: a data do
    lançamento vem digitada errada na origem em algumas empresas — 2424, 2223,
    2202, 2121, 2050, 2044 —, e `MAX` pega justamente o maior deles. O importador
    já registra isso no log e **importa a linha assim mesmo** (ver `_ler`, e o
    porquê de não descartar), então o valor errado está no banco de propósito.

    Sem o filtro, o card do importador anuncia "última competência 10/2424" —
    que se lê como tela quebrada, e esconde qual é de fato o último mês trazido.
    Vale `None` quando a empresa só tem competência implausível: aí não há última
    competência honesta a mostrar.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id_empresa,
                   e.razao_social,
                   e.nome_fantasia,
                   e.cnpj,
                   COUNT(s.codi_cta)                          AS linhas,
                   MAX(s.ano * 100 + s.mes) FILTER (
                       WHERE s.ano BETWEEN %(ano_minimo)s AND %(ano_maximo)s
                   )                                          AS competencia_max,
                   COUNT(s.codi_cta)
                       FILTER (WHERE s.debito_total IS NULL)  AS linhas_sem_dc
              FROM empresas e
              JOIN bi_empresa_contabil bc ON bc.id_empresa = e.id_empresa
              LEFT JOIN bi_saldo_mensal s ON s.id_empresa = e.id_empresa
             WHERE e.ativo
             GROUP BY e.id_empresa, e.razao_social, e.nome_fantasia, e.cnpj
             ORDER BY COALESCE(e.razao_social, ''), e.id_empresa
            """,
            {"ano_minimo": ANO_MINIMO, "ano_maximo": _ano_maximo()},
        )
        return linhas_dict(cur)


def pendentes(busca: str | None = None) -> list[dict]:
    """Empresas com plano importado e **sem** saldo nenhum.

    Empresa que já tem saldo fica fora da lista: para ela a operação é a
    atualização de rotina, não uma inclusão a escolher.
    """
    alvo = (busca or "").strip().lower()
    disponiveis = []

    for empresa in _empresas_com_plano():
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
    """Estado local: quantas empresas têm saldo e até quando."""
    empresas = _empresas_com_plano()
    com_saldo = [e for e in empresas if e["linhas"]]

    dados: dict = {
        "rotulo": "Empresas com saldos",
        "valor": f"{len(com_saldo)} de {len(empresas)}",
    }

    if not empresas:
        dados["alerta"] = (
            "Nenhuma empresa tem plano de contas importado. Rode antes o importador "
            "'Plano de contas contábil' — os saldos se prendem às contas dele."
        )
        return dados

    if not com_saldo:
        dados["alerta"] = (
            "As empresas têm plano de contas, mas nenhum saldo. Marque-as na lista "
            "abaixo: sem saldo a tela do BI não tem número para mostrar."
        )
        return dados

    # `competencia_max` é nulo quando a empresa só tem competência implausível
    # — ver `_empresas_com_plano`. Nesse caso não há última competência honesta,
    # e omitir a linha diz mais do que exibir um ano digitado errado.
    plausiveis = [e["competencia_max"] for e in com_saldo if e["competencia_max"]]
    if plausiveis:
        ultima = max(plausiveis)
        dados["detalhe"] = f"última competência {ultima % 100:02d}/{ultima // 100}"

    # Débito e Crédito chegaram na migration `0009`, depois de os saldos já
    # terem sido importados uma vez. Quem não reimportar fica com o par nulo, e
    # o Balancete da empresa não tem as duas pernas do movimento.
    #
    # A contagem é de EMPRESAS, não de linhas: o administrador decide o que
    # reimportar por empresa, e "1.204.331 competências" não diz quantas vezes
    # ele precisa clicar. E a condição é ter pelo menos uma competência sem o
    # par — basta uma para o Balancete daquela empresa sair incompleto.
    sem_dc = [e for e in com_saldo if e["linhas_sem_dc"]]
    if sem_dc:
        dados["alerta"] = (
            f"{len(sem_dc)} de {len(com_saldo)} empresa(s) com saldo importado antes de "
            "Débito e Crédito existirem. Rode este importador de novo para elas: sem o "
            "par, o Balancete não tem as duas pernas do movimento."
        )
    return dados


# ── Execução ─────────────────────────────────────────────────────────────────


def _selecionar_alvo(
    incluir: bool, ids: list[int] | None
) -> tuple[list[int], list[int], list[int]]:
    """Devolve (alvo, sem_plano, sem_saldo_nao_incluidas).

    Sem `incluir`, só as empresas que **já** têm saldo: é a rotina de
    atualização. Com `incluir`, também as que ainda não têm.
    """
    empresas = {e["id_empresa"]: e for e in _empresas_com_plano()}

    pedidas = ids if ids else list(empresas)
    sem_plano = [i for i in pedidas if i not in empresas]
    candidatas = [i for i in pedidas if i in empresas]

    if incluir:
        return candidatas, sem_plano, []

    alvo = [i for i in candidatas if empresas[i]["linhas"]]
    return alvo, sem_plano, [i for i in candidatas if not empresas[i]["linhas"]]


def _ler(consultar, id_empresa: int, contas_validas: set[int]) -> tuple[list[dict], dict]:
    """Saldos mensais de uma empresa. Devolve (linhas, contagem por motivo).

    Duas razões diferentes fazem uma linha ser descartada, e confundi-las custa
    caro em direções opostas:

    `sem_conta` — a perna do lançamento não aponta para conta nenhuma:
    `cdeb_lan`/`ccre_lan` vem **nulo ou zero**. O zero é o sentinela do Domínio
    para "sem conta neste lado" — o código 0 não existe em `ctcontas`, e o
    saldo acumulado dele é exatamente 0,00 (conferido na empresa 272, onde são
    94 pernas em 47 competências). É normal e inofensivo, e aparece em quase
    toda empresa.

    `fora_do_plano` — a conta existe no lançamento mas não está em `bi_conta`,
    tipicamente por ter sido inativada no Domínio (`SITUACAO_CTA <> 'A'`) depois
    de ter tido movimento. Este caso **pode quebrar o Balanço**: se o saldo
    acumulado dela não for zero, a identidade `Ativo = Passivo + PL` deixa de
    fechar, e o sintoma aparece na tela como número errado, não como erro.

    Por isso só o segundo gera aviso. Avisar nos dois faria o alerta disparar em
    toda importação por um motivo benigno — e alerta que sempre dispara é
    alerta que ninguém lê.
    """
    linhas = consultar(carregar_sql("bi/dominio/saldos_mensais.sql"), (id_empresa, id_empresa))

    aceitas = []
    contagem = {
        "lidas": len(linhas),
        "sem_conta": 0,
        "fora_do_plano": 0,
        "sem_competencia": 0,
        "competencia_implausivel": 0,
    }
    anos_implausiveis: set[int] = set()

    for linha in linhas:
        codi_cta = linha["codi_cta"]
        if not codi_cta:  # None ou 0 — ver a docstring
            contagem["sem_conta"] += 1
            continue
        if codi_cta not in contas_validas:
            contagem["fora_do_plano"] += 1
            continue
        if linha["ano"] is None or linha["mes"] is None:
            contagem["sem_competencia"] += 1
            continue

        ano = int(linha["ano"])
        # Ano fora de qualquer faixa razoável é erro de digitação na DATA do
        # lançamento, no Domínio: 2044 no lugar de 2024, 2202 no lugar de 2022.
        # A linha **entra** de propósito — o portal é espelho da origem, e
        # descartá-la faria o Balanço do portal discordar do Balanço do Domínio
        # sem que ninguém conseguisse explicar a diferença. O que se faz é
        # tornar o problema visível para quem pode corrigi-lo na origem.
        if not (ANO_MINIMO <= ano <= _ano_maximo()):
            contagem["competencia_implausivel"] += 1
            anos_implausiveis.add(ano)

        aceitas.append(
            {
                "id_empresa": id_empresa,
                "codi_cta": codi_cta,
                "ano": ano,
                "mes": int(linha["mes"]),
                "movimento": linha["movimento"] or 0,
                "movimento_total": linha["movimento_total"] or 0,
                # As duas pernas do Balancete. Trocá-las de lugar aqui não gera
                # erro: o Balancete sai com Débito e Crédito invertidos, os
                # totais continuam iguais entre si e a Diferença continua
                # zerando. A invariante do teste é o que morde —
                # `movimento_total = credito_total - debito_total` só vale com
                # o par na ordem certa.
                "debito_total": linha["debito_total"] or 0,
                "credito_total": linha["credito_total"] or 0,
            }
        )
    contagem["anos_implausiveis"] = sorted(anos_implausiveis)
    return aceitas, contagem


def _contas_validas(id_empresa: int) -> set[int]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT codi_cta FROM bi_conta WHERE id_empresa = %s", (id_empresa,))
        return {linha[0] for linha in cur.fetchall()}


def _gravar(id_empresa: int, linhas: list[dict]) -> tuple[int, int]:
    """Persiste os saldos de uma empresa. Devolve (incluídas, atualizadas).

    Nenhum UPDATE escreve `updated_at` — é do trigger `set_updated_at()`.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT codi_cta, ano, mes FROM bi_saldo_mensal WHERE id_empresa = %s",
            (id_empresa,),
        )
        existentes = {(linha[0], linha[1], linha[2]) for linha in cur.fetchall()}

    novas = sum(1 for x in linhas if (x["codi_cta"], x["ano"], x["mes"]) not in existentes)

    with transacao() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO bi_saldo_mensal (id_empresa, codi_cta, ano, mes,
                                         movimento, movimento_total,
                                         debito_total, credito_total)
            VALUES (%(id_empresa)s, %(codi_cta)s, %(ano)s, %(mes)s,
                    %(movimento)s, %(movimento_total)s,
                    %(debito_total)s, %(credito_total)s)
            ON CONFLICT (id_empresa, codi_cta, ano, mes) DO UPDATE
               SET movimento       = EXCLUDED.movimento,
                   movimento_total = EXCLUDED.movimento_total,
                   debito_total    = EXCLUDED.debito_total,
                   credito_total   = EXCLUDED.credito_total
            """,
            linhas,
        )

        # Competência que a origem não tem mais (lançamento estornado, mês
        # reaberto e zerado) precisa sair, senão o saldo acumulado do Balanço
        # segue somando um movimento que deixou de existir.
        #
        # O conjunto obsoleto é calculado aqui, e não por um NOT IN com milhares
        # de tuplas: as duas listas de chaves já estão em memória, e um DELETE
        # dirigido preserva o `created_at` das linhas que continuam válidas.
        obsoletas = existentes - {(x["codi_cta"], x["ano"], x["mes"]) for x in linhas}
        if obsoletas:
            cur.executemany(
                """
                DELETE FROM bi_saldo_mensal
                 WHERE id_empresa = %s AND codi_cta = %s AND ano = %s AND mes = %s
                """,
                [(id_empresa, *chave) for chave in obsoletas],
            )
            logger.info(
                "Empresa %s: %s competência(s) removida(s) por não existirem mais na origem.",
                id_empresa,
                len(obsoletas),
            )

    return novas, len(linhas) - novas


def executar(incluir: bool = False, dry_run: bool = False, ids: list[int] | None = None) -> dict:
    """Traz os saldos mensais e devolve a contagem de linhas por competência.

    Por padrão **só atualiza** as empresas que já têm saldo. `incluir=True`
    traz também as que ainda não têm; `ids` restringe.
    """
    alvo, sem_plano, sem_saldo = _selecionar_alvo(incluir, ids)

    if sem_plano and ids:
        # Recusa antes de ler a origem: o erro real seria uma violação de FK do
        # PostgreSQL, cuja mensagem não diz a quem lê o que precisa ser feito.
        raise ErroValidacao(
            "Estas empresas não têm plano de contas importado: "
            f"{', '.join(str(i) for i in sem_plano)}. "
            "Ou o importador 'Plano de contas contábil' ainda não rodou para "
            "elas, ou o Domínio não tem escrituração contábil para elas — e "
            "nesse caso rodá-lo de novo não muda nada, porque não há o que "
            "trazer. Confira em 'Plano de contas contábil': se ele ler zero "
            "contas, o BI Contábil não se aplica a essa empresa."
        )

    resultado = {"lidas": 0, "incluidas": 0, "atualizadas": 0, "ignoradas": 0}
    if sem_saldo:
        # Explica por que a rotina não tocou nelas, em vez de deixar o número
        # de ignoradas sem causa aparente.
        resultado["sem_saldo"] = sem_saldo

    if not alvo:
        logger.info(
            "Importação de saldos%s: nenhuma empresa alvo (incluir=%s, ids=%s)",
            " (simulação)" if dry_run else "",
            incluir,
            ids,
        )
        return resultado

    with dominio.sessao() as consultar:
        lidos = {
            id_empresa: _ler(consultar, id_empresa, _contas_validas(id_empresa))
            for id_empresa in alvo
        }

    for id_empresa, (linhas, contagem) in lidos.items():
        resultado["lidas"] += contagem["lidas"]
        resultado["ignoradas"] += contagem["lidas"] - len(linhas)

        if contagem["fora_do_plano"]:
            # O único descarte que pode quebrar o Balanço — ver `_ler`.
            logger.warning(
                "Empresa %s: %s competência(s) de conta que não está no plano importado. "
                "Reimporte o plano de contas; se a conta foi inativada no Domínio com "
                "saldo, a identidade Ativo = Passivo + PL não vai fechar.",
                id_empresa,
                contagem["fora_do_plano"],
            )

        if contagem["competencia_implausivel"]:
            # Não impede a importação: o valor entra onde a origem o colocou.
            # O aviso existe para alguém corrigir a DATA no Domínio — enquanto
            # não corrigir, esse movimento não aparece em nenhum período que a
            # tela ofereça, e o Balanço fica menor que a realidade.
            logger.warning(
                "Empresa %s: %s competência(s) com ano implausível %s — a data do "
                "lançamento está digitada errada no Domínio. O valor foi importado "
                "como está, mas não vai aparecer em nenhum período da tela.",
                id_empresa,
                contagem["competencia_implausivel"],
                contagem["anos_implausiveis"],
            )

        if dry_run:
            resultado["incluidas"] += len(linhas)
            continue

        if not linhas:
            logger.info("Empresa %s não tem lançamento contábil no Domínio.", id_empresa)
            continue

        novas, atualizadas = _gravar(id_empresa, linhas)
        resultado["incluidas"] += novas
        resultado["atualizadas"] += atualizadas

    logger.info(
        "Importação de saldos%s em %s empresa(s): %s",
        " (simulação)" if dry_run else "",
        len(alvo),
        resultado,
    )
    return resultado
