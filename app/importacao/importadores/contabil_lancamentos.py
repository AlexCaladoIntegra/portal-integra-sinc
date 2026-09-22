"""Importador dos lançamentos contábeis: Domínio → `bi_lancamento`.

É o que sustenta o drill-down da tela de DRE: clicar na conta analítica e ver o
razão dela. Os outros importadores contábeis trazem agregado; este traz o grão
mais fino que existe, e por isso é o único com janela, teto e leitura em lotes.

**O grão é a PERNA, não o lançamento.** Cada lançamento do Domínio tem duas
pernas em colunas diferentes (`cdeb_lan` e `ccre_lan`) e vira até duas linhas
aqui, com `natureza` 'D' e 'C'. A migration `0008_bi_lancamento` registra as
três razões de correção por trás disso; a que se sente neste arquivo é a
primeira: **a regra de descarte é perna a perna, e é literalmente a mesma de
`contabil_saldos`** — há teste que compara as duas.

**Por que existe uma janela de anos.** `ctlancto` tem 14,5 milhões de linhas.
A janela de 3 anos é o que mantém a tabela local proporcional ao que a tela
oferece — e ela é a razão de `bi_lancamento` NUNCA poder ser a fonte do saldo
anterior do razão (Regra 18 do BIC-003): esse vem de `bi_saldo_mensal`, que tem
o histórico completo.

**Por que a seleção é por empresa.** Medido em 04/09/2026, no biênio 2025-2026:
a mediana é de **1.039** lançamentos por empresa, 287 das 333 ficam abaixo de
20 mil, e o custo se concentra em **14** empresas acima de 100 mil. Trazer tudo
de todas seria pagar pelas 14 para servir as 333.

Depende de `contabil_plano`: a FK de `bi_lancamento` é composta e aponta para
`bi_conta`. A dependência é verificada antes de ler a origem, com mensagem
própria — sem isso o erro chegaria como violação de chave estrangeira do
PostgreSQL, cuja mensagem não diz a quem lê o que precisa ser feito.
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

CHAVE = "contabil_lancamentos"
NOME = "Lançamentos contábeis"
DESCRICAO = (
    "Lançamento a lançamento das contas do plano, nos últimos 3 anos. "
    "Alimenta o detalhamento por lançamento na tela de DRE."
)
FONTE = "Domínio · ctlancto"

# As duas constantes do Portal viraram CONFIGURAÇÃO aqui — ver
# `sinc_anos_janela` e `sinc_teto_de_empresas` em `app/config.py` para o
# porquê. O default da janela é o mesmo 3 do Portal, e tem de continuar sendo:
# os dois escrevem em `bi_lancamento`.

# Linhas por ida ao driver ODBC. Ver `dominio.sessao_em_lotes`.
LOTE_LEITURA = 5_000

# Tuplas por INSERT do `execute_values`.
LOTE_GRAVACAO = 5_000

# O sentinela do Domínio para "não há conta deste lado do lançamento". O código
# 0 não existe em `ctcontas`; é o mesmo valor que `contabil_saldos` descarta.
SEM_CONTA = 0


def janela(hoje: date | None = None) -> tuple[date, date]:
    """Primeiro dia do ano mais antigo da janela, e o primeiro dia do ano
    SEGUINTE ao corrente.

    O fim é exclusivo de propósito — ver o cabeçalho de
    `bi/dominio/lancamentos.sql`. Devolver 31/12 convidaria a esquecer o próprio
    31/12 num `<`, ou a incluir o 01/01 seguinte num `<=`.

    **Pública, e é a única fonte da fronteira da janela.** O modal do razão a lê
    para avisar quando o período pedido começa antes dela (Regra 16 do
    BIC-003) — repetir o número lá faria os dois divergirem no dia em que
    alguém mudasse um, e o aviso passaria a mentir justamente sobre o que
    existe para avisar. É import de `bi` para `importacao`, e é seguro: este
    módulo não toca em `dominio` no topo, e o `pyodbc` de lá é importado dentro
    da função — a aplicação web nunca o carrega.
    """
    anos = get_settings().sinc_anos_janela
    ano = (hoje or date.today()).year
    return date(ano - anos + 1, 1, 1), date(ano + 1, 1, 1)


# ── Estado local ─────────────────────────────────────────────────────────────


def _empresas_com_saldo() -> list[dict]:
    """Empresas com saldo importado, e se cada uma já tem lançamento.

    O universo é quem **tem saldo**, e não quem tem plano: empresa sem saldo não
    tem número no DRE, então não há o que detalhar nela. É a mesma lógica de
    dependência que `contabil_saldos` aplica sobre `contabil_plano`.

    `linhas` é BOOLEANO aqui, e não uma contagem: quem precisa do total é o
    `resumo()`, e ele o pede uma vez só.

    ## A única DIVERGÊNCIA de desempenho em relação ao Portal

    O Portal escreve este booleano como `EXISTS (SELECT 1 FROM bi_lancamento
    WHERE id_empresa = e.id_empresa)` **na lista de seleção**, apostando que o
    prefixo `id_empresa` da chave primária o serviria. Não serve, e o plano de
    execução diz por quê:

        SubPlan 1
          -> Seq Scan on bi_lancamento  (loops=306)
               Rows Removed by Filter: 1695457

    Só **3** das 306 empresas têm lançamento, então o planejador estima que
    `id_empresa = ?` casa com um terço da tabela e escolhe varredura sequencial.
    Para as 303 empresas sem nenhuma linha, provar a ausência custa a tabela
    INTEIRA — 306 varreduras de 1,7 milhão de linhas, ~519 milhões de linhas
    lidas. Medido: **66,6 s**, e a tela paga isso a cada abertura e depois de
    cada sincronização.

    A reescrita abaixo calcula o conjunto de empresas com lançamento **uma vez**
    e junta por ele. Medido: **0,19 s**, com resultado idêntico linha a linha.

    Vale a pena portar de volta para o `portal-integra`: a tela de importação
    de lá tem exatamente a mesma espera.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH com_lancamento AS (SELECT DISTINCT id_empresa FROM bi_lancamento)
            SELECT e.id_empresa,
                   e.razao_social,
                   e.nome_fantasia,
                   e.cnpj,
                   (c.id_empresa IS NOT NULL) AS linhas
              FROM empresas e
              JOIN bi_empresa_contabil bc ON bc.id_empresa = e.id_empresa
              LEFT JOIN com_lancamento c ON c.id_empresa = e.id_empresa
             WHERE e.ativo
               AND EXISTS (SELECT 1 FROM bi_saldo_mensal s
                            WHERE s.id_empresa = e.id_empresa)
             ORDER BY COALESCE(e.razao_social, ''), e.id_empresa
            """
        )
        return linhas_dict(cur)


def pendentes(busca: str | None = None) -> list[dict]:
    """Empresas com saldo e **sem** lançamento nenhum.

    É a lista que interessa marcar: são exatamente as empresas em que a tela de
    DRE abre com números e o detalhamento por lançamento fica vazio. Empresa que
    já tem lançamento sai da lista — para ela a operação é a atualização de
    rotina, não uma inclusão a escolher.
    """
    alvo = (busca or "").strip().lower()
    disponiveis = []

    for empresa in _empresas_com_saldo():
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


def _total_de_linhas() -> int:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM bi_lancamento")
        return cur.fetchone()[0]


def resumo() -> dict:
    """Estado local: quantas empresas têm lançamento, e qual é a janela."""
    empresas = _empresas_com_saldo()
    com_lancamento = [e for e in empresas if e["linhas"]]
    inicio, fim = janela()

    dados: dict = {
        "rotulo": "Empresas com lançamentos",
        "valor": f"{len(com_lancamento)} de {len(empresas)}",
        "detalhe": f"janela {inicio.year}–{fim.year - 1}",
    }

    if not empresas:
        dados["alerta"] = (
            "Nenhuma empresa tem saldo contábil importado. Rode antes o importador "
            "'Saldos contábeis mensais' — sem número no DRE não há o que detalhar."
        )
        return dados

    if not com_lancamento:
        dados["alerta"] = (
            "Nenhuma empresa tem lançamentos importados. A tela de DRE funciona sem "
            "eles, mas clicar numa conta analítica não mostra o razão."
        )
    else:
        dados["detalhe"] += f" · {_total_de_linhas():,} linhas".replace(",", ".")
    return dados


# ── Leitura ──────────────────────────────────────────────────────────────────


def _contas_validas(id_empresa: int) -> set[int]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT codi_cta FROM bi_conta WHERE id_empresa = %s", (id_empresa,))
        return {linha[0] for linha in cur.fetchall()}


def _pernas(linha: dict, id_empresa: int, contas_validas: set[int], contagem: dict) -> list[dict]:
    """As pernas gravaveis de um lançamento — nenhuma, uma ou duas.

    A regra de descarte é **a mesma** de `contabil_saldos._ler`, e a igualdade
    tem teste. As duas razões são contadas separadamente porque só uma merece
    aviso:

    `sem_conta` — a perna vem com conta **nula ou zero**. O zero é o sentinela
    do Domínio para "sem conta neste lado", e é normal: medido no biênio, são
    ~12% das pernas de cada lado. Avisar sobre isso faria o alerta disparar em
    toda importação por um motivo benigno.

    `fora_do_plano` — a conta existe no lançamento e não está em `bi_conta`,
    tipicamente por ter sido inativada no Domínio depois de ter tido movimento.
    Este merece aviso: o razão da conta irmã vai existir e o dela não.

    **Descartar o lançamento inteiro quando um lado cai seria o defeito.** A
    perna irmã tem saldo em `bi_saldo_mensal` — `contabil_saldos` a manteve pela
    mesma regra — e o drill-down dela deixaria de fechar com o próprio saldo.
    Número plausível, sem erro nenhum.
    """
    pernas = []
    for natureza, coluna in (("D", "cdeb_lan"), ("C", "ccre_lan")):
        codi_cta = linha[coluna]
        if not codi_cta or codi_cta == SEM_CONTA:
            contagem["sem_conta"] += 1
            continue
        if codi_cta not in contas_validas:
            contagem["fora_do_plano"] += 1
            continue
        pernas.append(
            {
                "id_empresa": id_empresa,
                "nume_lan": linha["nume_lan"],
                "natureza": natureza,
                "codi_cta": codi_cta,
                "data_lan": linha["data_lan"],
                "valor": linha["vlor_lan"] or 0,
                "orig_lan": linha["orig_lan"],
                "historico": (linha["chis_lan"] or "").strip() or None,
                "documento": linha["ndoc_lan"] or None,
                "lote": linha["codi_lote"] or None,
                "usuario": (linha["codi_usu"] or "").strip() or None,
            }
        )
    return pernas


def _contagem_zerada(id_empresa: int) -> dict:
    return {
        "id_empresa": id_empresa,
        "lancamentos": 0,
        "lidas": 0,
        "gravaveis": 0,
        "sem_conta": 0,
        "fora_do_plano": 0,
    }


def _ler_em_lotes(consultar, id_empresa: int, contas_validas: set[int], intervalo, contagem: dict):
    """Gera as pernas graváveis, lote a lote, **acumulando em `contagem`**.

    Gerador, e não lista: o ponto de ler em lotes é o pico de memória, e
    devolver a lista inteira no fim jogaria o ganho fora. Quem consome grava (ou
    descarta, enquanto a gravação não existe) e segue.

    `contagem` é do chamador e é mutada aqui, em vez de sair junto com cada
    lote. As duas formas funcionam, e esta diz a verdade: a contagem é de toda a
    empresa, não daquele lote — devolvê-la a cada `yield` faria parecer que se
    pode usar a de um lote isoladamente.

    As contagens são em **pernas**, não em lançamentos: um lançamento oferece
    duas e nem sempre as duas entram. Contar lançamentos faria `ignoradas`
    parecer sempre metade do lido.
    """
    inicio, fim = intervalo

    for lote in consultar(
        carregar_sql("bi/dominio/lancamentos.sql"),
        (id_empresa, inicio, fim),
        tamanho=LOTE_LEITURA,
    ):
        pernas = []
        for linha in lote:
            contagem["lancamentos"] += 1
            contagem["lidas"] += 2  # todo lançamento oferece duas pernas
            pernas.extend(_pernas(linha, id_empresa, contas_validas, contagem))
        contagem["gravaveis"] += len(pernas)
        yield pernas


# ── Gravação ─────────────────────────────────────────────────────────────────

# `execute_values` monta um único INSERT com N tuplas de VALUES. `executemany`
# do psycopg2 faz um round-trip por linha — a 780 mil linhas isso é latência de
# rede pura, medida em minutos. Os outros importadores usam `executemany` porque
# escrevem milhares, não centenas de milhares.
INSERIR = """
    INSERT INTO bi_lancamento (id_empresa, nume_lan, natureza, codi_cta, data_lan,
                               valor, orig_lan, historico, documento, lote, usuario)
    VALUES %s
"""

_COLUNAS = (
    "id_empresa",
    "nume_lan",
    "natureza",
    "codi_cta",
    "data_lan",
    "valor",
    "orig_lan",
    "historico",
    "documento",
    "lote",
    "usuario",
)


def _apagar_janela(cur, id_empresa: int, intervalo) -> int:
    """Remove a janela inteira da empresa e devolve quantas linhas saíram.

    **`DELETE` + `INSERT`, e não upsert.** `ON CONFLICT DO UPDATE` preservaria
    `created_at`, mas não sabe remover o que foi estornado na origem — e
    lançamento estornado que ficasse aqui apareceria no razão de uma conta cujo
    saldo não o contém mais. `contabil_saldos` resolve isso calculando o
    conjunto obsoleto em Python, mas lá são 3.338 chaves por empresa; aqui
    seriam centenas de milhares, que é exatamente a memória que a leitura em
    lotes existe para não gastar.

    **Não há índice em `(id_empresa, data_lan)`, e a medição fechou a questão.**
    A migration `0008` deixou o assunto em aberto com um limiar de 10 s. Medido
    em 04/09/2026 na maior empresa — 780.040 linhas apagadas de uma tabela de
    1,3 milhão: **642 ms**. E o `EXPLAIN ANALYZE` mostrou que o planejador nem
    usa o prefixo da chave primária, como se supunha: ele escolhe o
    `ix_bi_lancamento_conta`, cuja primeira coluna também é `id_empresa` e que
    ainda filtra `data_lan` dentro do próprio índice. O índice do drill-down
    serve os dois usos, e o segundo índice não precisa existir.
    """
    inicio, fim = intervalo
    cur.execute(
        "DELETE FROM bi_lancamento  WHERE id_empresa = %s AND data_lan >= %s AND data_lan < %s",
        (id_empresa, inicio, fim),
    )
    return cur.rowcount


def _inserir(cur, pernas: list[dict]) -> None:
    execute_values(
        cur,
        INSERIR,
        [tuple(perna[coluna] for coluna in _COLUNAS) for perna in pernas],
        page_size=LOTE_GRAVACAO,
    )


def _gravar(consultar, id_empresa: int, contas: set[int], intervalo, contagem: dict) -> int:
    """Recarrega a janela de uma empresa. Devolve quantas linhas o DELETE tirou.

    **Uma transação para a empresa inteira**, e o DELETE acontece dentro dela.
    A alternativa considerada na spec era uma transação por (empresa, ano), para
    que uma falha no meio deixasse anos completos gravados — mas isso defende
    contra uma escrita parcial que a transação já impede: aqui, uma falha
    qualquer faz rollback e a empresa fica **exatamente** como estava. Ficou o
    caminho de menos peças.

    A leitura do Domínio acontece **dentro** da transação PostgreSQL, lote a
    lote. É deliberado: acumular tudo antes de abrir a transação devolveria o
    pico de memória que a leitura em lotes existe para evitar. O custo é a
    transação ficar aberta enquanto o ODBC lê — aceitável, porque ninguém mais
    escreve nesta tabela.
    """
    with transacao() as conn, conn.cursor() as cur:
        removidas = _apagar_janela(cur, id_empresa, intervalo)
        for pernas in _ler_em_lotes(consultar, id_empresa, contas, intervalo, contagem):
            if pernas:
                _inserir(cur, pernas)
    return removidas


def _contar_inclusao(removidas: int, gravadas: int) -> tuple[int, int, int]:
    """Devolve (incluídas, atualizadas, desaparecidas).

    **É uma aproximação, e ela é declarada.** Como a recarga apaga a janela e
    reinsere, não há como saber quais das linhas gravadas já existiam sem
    carregar o conjunto de chaves antigas em memória — centenas de milhares de
    tuplas, que é o custo que a estratégia de recarga existe para evitar.

    `atualizadas = min(removidas, gravadas)` é **exato** quando a origem só
    ganhou linhas ou só perdeu linhas desde a última importação, que é o caso
    normal. Ele subestima a inclusão quando houve as duas coisas no mesmo
    período — e subestimar é o lado certo de errar: a contagem nunca promete
    novidade que não houve.
    """
    atualizadas = min(removidas, gravadas)
    return gravadas - atualizadas, atualizadas, max(0, removidas - gravadas)


# ── Execução ─────────────────────────────────────────────────────────────────


def _selecionar_alvo(
    incluir: bool, ids: list[int] | None
) -> tuple[list[int], list[int], list[int]]:
    """Devolve (alvo, sem_saldo, sem_lancamento_nao_incluidas).

    Sem `incluir`, só as empresas que **já** têm lançamento: é a rotina de
    atualização. Com `incluir`, também as que ainda não têm.
    """
    empresas = {e["id_empresa"]: e for e in _empresas_com_saldo()}

    pedidas = ids if ids else list(empresas)
    sem_saldo = [i for i in pedidas if i not in empresas]
    candidatas = [i for i in pedidas if i in empresas]

    if incluir:
        return candidatas, sem_saldo, []

    alvo = [i for i in candidatas if empresas[i]["linhas"]]
    return alvo, sem_saldo, [i for i in candidatas if not empresas[i]["linhas"]]


def executar(incluir: bool = False, dry_run: bool = False, ids: list[int] | None = None) -> dict:
    """Traz os lançamentos da janela e devolve a contagem de PERNAS.

    Por padrão só atualiza as empresas que já têm lançamento; `incluir=True`
    traz também as que ainda não têm; `ids` restringe.
    """
    alvo, sem_saldo, sem_lancamento = _selecionar_alvo(incluir, ids)

    if sem_saldo and ids:
        # Recusa antes de ler a origem. O erro real seria uma violação de FK do
        # PostgreSQL, cuja mensagem não diz a quem lê o que fazer.
        raise ErroValidacao(
            "Estas empresas não têm saldo contábil importado: "
            f"{', '.join(str(i) for i in sem_saldo)}. "
            "Ou os importadores 'Plano de contas contábil' e 'Saldos contábeis "
            "mensais' ainda não rodaram para elas, ou o Domínio não tem "
            "escrituração contábil para elas — e nesse caso rodá-los de novo "
            "não muda nada. Medido em 22/09/2026: 187 das 608 empresas ativas "
            "não têm nenhuma conta na origem, e para essas o BI Contábil "
            "simplesmente não se aplica."
        )

    teto = get_settings().sinc_teto_de_empresas
    if teto and len(alvo) > teto:
        raise ErroValidacao(
            f"{len(alvo)} empresas selecionadas, e o limite por execução é {teto}. "
            "Lançamento é o dado mais volumoso do Domínio. Aumente ou desligue "
            "SINC_TETO_DE_EMPRESAS no .env (0 desliga), ou selecione menos empresas."
        )

    resultado = {"lidas": 0, "incluidas": 0, "atualizadas": 0, "ignoradas": 0}
    if sem_lancamento:
        resultado["sem_lancamento"] = sem_lancamento

    if not alvo:
        logger.info(
            "Importação de lançamentos%s: nenhuma empresa alvo (incluir=%s, ids=%s)",
            " (simulação)" if dry_run else "",
            incluir,
            ids,
        )
        return resultado

    intervalo = janela()
    with dominio.sessao_em_lotes() as consultar:
        for id_empresa in alvo:
            contas = _contas_validas(id_empresa)
            contagem = _contagem_zerada(id_empresa)

            if dry_run:
                for _pernas in _ler_em_lotes(consultar, id_empresa, contas, intervalo, contagem):
                    pass
                resultado["incluidas"] += contagem["gravaveis"]
            else:
                removidas = _gravar(consultar, id_empresa, contas, intervalo, contagem)
                incluidas, atualizadas, sumidas = _contar_inclusao(removidas, contagem["gravaveis"])
                resultado["incluidas"] += incluidas
                resultado["atualizadas"] += atualizadas
                if sumidas:
                    resultado["desaparecidas"] = resultado.get("desaparecidas", 0) + sumidas
                    logger.info(
                        "Empresa %s: %s perna(s) que a origem não tem mais na janela.",
                        id_empresa,
                        sumidas,
                    )

            resultado["lidas"] += contagem["lidas"]
            resultado["ignoradas"] += contagem["lidas"] - contagem["gravaveis"]

            if contagem["fora_do_plano"]:
                logger.warning(
                    "Empresa %s: %s perna(s) de conta que não está no plano importado. "
                    "Reimporte o plano de contas — o razão dessas contas vai ficar vazio "
                    "na tela de DRE, com o saldo delas aparecendo mesmo assim.",
                    id_empresa,
                    contagem["fora_do_plano"],
                )

            logger.info(
                "Empresa %s: %s lançamento(s) na janela %s-%s, %s perna(s) gravável(is), "
                "%s sem conta, %s fora do plano.",
                id_empresa,
                contagem["lancamentos"],
                intervalo[0].year,
                intervalo[1].year - 1,
                contagem["gravaveis"],
                contagem["sem_conta"],
                contagem["fora_do_plano"],
            )

    logger.info(
        "Importação de lançamentos%s em %s empresa(s): %s",
        " (simulação)" if dry_run else "",
        len(alvo),
        resultado,
    )
    return resultado
