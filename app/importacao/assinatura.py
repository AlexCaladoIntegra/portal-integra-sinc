"""A impressão digital do que a origem tem, por empresa.

Serve para responder, antes de ler, a pergunta que hoje ninguém faz: *mudou
alguma coisa nesta empresa desde a última sincronização?* Ver
`changes/SINC-006-assinatura-e-incremental.md` para as medições e as decisões.

## Por que é barato

A assinatura de UMA empresa custa 8 a 17 vezes menos que ler os dados dela.
Mas o ganho que decide não é esse: é agrupar por `codi_emp` numa varredura só,
em vez de 363 idas ao ODBC. Medido em 21/09/2026, o parque inteiro nas cinco
entidades pesadas sai em **12,3 segundos**.

## O que ela NÃO enxerga

`COUNT` + `SUM` é cego a edição que preserva os dois — trocar o valor entre
dois lançamentos, ou uma correção que se anula. É improvável e é real, e a
mitigação é operacional, não de código: uma rodada periódica ignorando a
assinatura. Acrescentar mais medidas reduz a cegueira sem eliminá-la, e custa
tempo de origem; não vale.

## Fase 0: aqui só se MEDE

Nada é pulado. Este módulo calcula, compara com o guardado e devolve o
resultado para o log. Ligar o pulo é a Fase 1, e só depois de a Fase 0 dizer
qual é a taxa real.
"""

from __future__ import annotations

import hashlib
import logging
from decimal import Decimal

from ..config import get_settings
from . import dominio, estado

logger = logging.getLogger(__name__)

# Só as cinco entidades pesadas. As outras cinco (`empresas`,
# `contabil_plano`, `contabil_dfc`, `fiscal_cadastros`, `fiscal_dimensoes`)
# sincronizam em menos de 3 s cada — calcular assinatura para elas custaria
# mais que o trabalho que evitaria.
ENTIDADES = (
    "contabil_saldos",
    "contabil_lancamentos",
    "fiscal_movimento",
    "fiscal_apuracao",
    "fiscal_produto",
)


def _recorte(ids: list[int] | None) -> str:
    """`AND codi_emp IN (…)`, ou vazio.

    Sem isto, a assinatura de UMA empresa custaria a varredura do parque
    inteiro — 8 s no caso do produto fiscal, sobre uma sincronização de 4 s.
    O custo tem de ser proporcional ao escopo.

    Os ids entram **interpolados**, e é a mesma exceção que os importadores
    abrem: são inteiros, garantidos pela validação de `services.executar`, que
    recusa qualquer coisa que não seja `int` — inclusive `bool`. O `assert`
    existe para que essa garantia não dependa de quem chama.
    """
    if not ids:
        return ""
    assert all(isinstance(i, int) and not isinstance(i, bool) for i in ids)
    return f" AND codi_emp IN ({','.join(str(i) for i in ids)})"


def _fontes(entidade: str, ids: list[int] | None = None) -> list[tuple[str, tuple]]:
    """As consultas de origem de uma entidade, e os parâmetros de cada uma.

    Devolve uma LISTA porque três entidades leem de mais de uma tabela — o
    movimento fiscal vem de saídas, entradas e serviços. As contagens e as
    somas das várias fontes são acumuladas por empresa antes de virar hash.

    As consultas são deliberadamente **rasas**: `COUNT` e `SUM` sobre a tabela
    base, sem os JOINs e as regras que o importador aplica. A assinatura não
    precisa reproduzir a transformação — precisa mudar quando a origem muda.
    """
    schema = get_settings().dominio_schema
    recorte = _recorte(ids)
    # `WHERE 1=1` para o recorte poder entrar sempre como `AND`, sem cada
    # consulta ter de saber se já tem WHERE próprio.
    onde = f"WHERE 1=1{recorte}"

    if entidade == "contabil_saldos":
        # Sem recorte de data: os saldos acumulam desde o primeiro lançamento
        # da empresa, então qualquer lançamento de qualquer época os altera.
        return [
            (
                f"SELECT codi_emp, COUNT(*) AS n, SUM(vlor_lan) AS s "
                f"FROM {schema}.ctlancto {onde} GROUP BY codi_emp",
                (),
            )
        ]

    if entidade == "contabil_lancamentos":
        # Recortado pela MESMA janela que o importador grava. Sem o recorte, um
        # lançamento de 2019 mudaria a assinatura e provocaria uma releitura
        # que não traria nada — a janela não o alcança.
        from .importadores.contabil_lancamentos import janela

        inicio, fim = janela()
        return [
            (
                f"SELECT codi_emp, COUNT(*) AS n, SUM(vlor_lan) AS s "
                f"FROM {schema}.ctlancto {onde} AND data_lan >= ? AND data_lan < ? "
                f"GROUP BY codi_emp",
                (inicio, fim),
            )
        ]

    if entidade == "fiscal_movimento":
        return [
            (
                f"SELECT codi_emp, COUNT(*) AS n, SUM(vcon_sai) AS s "
                f"FROM {schema}.efsaidas {onde} GROUP BY codi_emp",
                (),
            ),
            (
                f"SELECT codi_emp, COUNT(*) AS n, SUM(vcon_ent) AS s "
                f"FROM {schema}.efentradas {onde} GROUP BY codi_emp",
                (),
            ),
            (
                f"SELECT codi_emp, COUNT(*) AS n, SUM(vcon_ser) AS s "
                f"FROM {schema}.efservicos {onde} GROUP BY codi_emp",
                (),
            ),
        ]

    if entidade == "fiscal_apuracao":
        return [
            (
                f"SELECT codi_emp, COUNT(*) AS n, SUM(sdev_sim) AS s "
                f"FROM {schema}.efsdoimp {onde} GROUP BY codi_emp",
                (),
            )
        ]

    if entidade == "fiscal_produto":
        return [
            (
                f"SELECT codi_emp, COUNT(*) AS n, SUM(VALOR_CONTABIL_MSP) AS s "
                f"FROM {schema}.efmvspro {onde} GROUP BY codi_emp",
                (),
            ),
            (
                f"SELECT codi_emp, COUNT(*) AS n, SUM(VALOR_CONTABIL_MEP) AS s "
                f"FROM {schema}.efmvepro {onde} GROUP BY codi_emp",
                (),
            ),
        ]

    raise ValueError(f"Entidade sem assinatura definida: {entidade!r}")


def digitar(contagem: int, soma: Decimal) -> str:
    """`md5(f"{contagem}:{soma}")`, com a soma em duas casas.

    O formato é o do SYNC-001 §16.5, e a fidelidade é deliberada: qualquer
    hash serviria, mas manter o mesmo contrato mantém a porta aberta para a
    arquitetura HTTP daquele plano, se o Portal um dia sair da rede do cliente.

    A formatação FAZ PARTE da assinatura — `1200.5` e `1200.50` dão hashes
    diferentes, o que custaria um reenvio desnecessário. Duas casas, ponto,
    sem separador de milhar.

    MD5 aqui não é escolha de segurança: é impressão digital de dado próprio,
    num arquivo local. Não há adversário a considerar.
    """
    texto = f"{contagem}:{Decimal(soma).quantize(Decimal('0.01'))}"
    return hashlib.md5(texto.encode("utf-8"), usedforsecurity=False).hexdigest()


def calcular(entidade: str, ids: list[int] | None = None) -> dict[int, str]:
    """A assinatura de cada empresa que a origem conhece, para uma entidade.

    Empresa sem nenhuma linha na origem **não aparece** no resultado — e a
    ausência é significativa: ela se distingue de uma empresa com zero linhas
    somando zero, que apareceria com a assinatura de `0:0.00`.
    """
    acumulado: dict[int, list] = {}
    for sql, params in _fontes(entidade, ids):
        for linha in dominio.consultar(sql, params):
            atual = acumulado.setdefault(linha["codi_emp"], [0, Decimal(0)])
            atual[0] += linha["n"] or 0
            atual[1] += Decimal(linha["s"] or 0)
    return {empresa: digitar(n, s) for empresa, (n, s) in acumulado.items()}


# ── Fase 0: observar e registrar ─────────────────────────────────────────────
#
# As duas funções abaixo são as ÚNICAS que `services` chama, e nenhuma delas
# levanta. É deliberado e é a propriedade que torna a Fase 0 de risco zero:
# medição que derruba a operação que estava medindo é pior que não medir.


def observar(chave: str, ids: list[int] | None) -> dict[int, str] | None:
    """Calcula a assinatura, compara com a guardada e REGISTRA NO LOG.

    **Não pula nada** — é a Fase 0. Devolve as assinaturas do escopo para que
    `registrar` as persista depois do sucesso, sem recalcular.

    A margem de erro da medição, e ela é conhecida: quando `ids` é `None`, o
    escopo aqui são todas as empresas que a ORIGEM conhece, enquanto o
    importador aplica pré-requisitos próprios (ter plano, estar habilitada).
    Uma empresa que a origem tem e o importador pula ganha um checkpoint que
    não mereceu, e na medição seguinte aparece como "inalterada". Isso torna a
    taxa medida **otimista** nas rodadas sem `ids`.

    Corrigir exige o importador dizer quais empresas processou, o que ele hoje
    não faz — e é trabalho da Fase 1, não desta.
    """
    if chave not in ENTIDADES:
        return None

    try:
        atual = calcular(chave, ids)
    except Exception:
        logger.warning(
            "[ASSINATURA] %s: não foi possível calcular. Seguindo sem medir.", chave, exc_info=True
        )
        return None

    try:
        guardadas = estado.ler(chave)
    except Exception:
        logger.warning(
            "[ASSINATURA] %s: não foi possível ler o estado local.", chave, exc_info=True
        )
        return atual

    if not guardadas:
        logger.info(
            "[ASSINATURA] %s: primeira medição — %d empresa(s) na origem, nenhuma base "
            "de comparação ainda. A taxa aparece a partir da próxima execução.",
            chave,
            len(atual),
        )
        return atual

    inalteradas = sum(1 for e, v in atual.items() if guardadas.get(e) == v)
    if atual:
        logger.info(
            "[ASSINATURA] %s: %d de %d empresa(s) inalteradas desde a última "
            "sincronização (%.0f%%). Fase 0 — nada foi pulado.",
            chave,
            inalteradas,
            len(atual),
            100 * inalteradas / len(atual),
        )
    return atual


def registrar(chave: str, valores: dict[int, str] | None) -> None:
    """Guarda as assinaturas do que acabou de ser sincronizado.

    Recebe o que `observar` já calculou: recalcular custaria uma segunda
    varredura da origem e, pior, registraria um estado diferente do que a
    sincronização de fato leu.
    """
    if not valores:
        return
    try:
        estado.gravar(chave, valores)
    except Exception:
        logger.warning(
            "[ASSINATURA] %s: não foi possível gravar o estado local.", chave, exc_info=True
        )
