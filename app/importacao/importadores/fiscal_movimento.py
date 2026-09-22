"""Importador do movimento fiscal: Domínio → `bi_fiscal_nota_mensal`.

Traz os TRÊS fatos de nota — saída, entrada e serviço — agregados por
(ano, mês, espécie, CFOP, acumulador, participante, UF, situação), com os
impostos somados do item. É a tabela que alimenta quase toda a tela: só o bloco
"Total por Produto" sai de outra.

**Histórico completo, sem janela.** A agregação acontece na origem e o parque
inteiro cabe em 2,36 milhões de linhas — 6,15 milhões de notas de saída viram
1,68 milhão. Recortar economizaria pouco e quebraria a comparação ano a ano, que
é o uso principal de uma tela fiscal. Mesma decisão de `contabil_saldos`, pela
razão simétrica: lá porque o Balanço acumula, aqui porque o agregado já é
pequeno.

**As três consultas de origem têm regras que custaram caro** — leia o cabeçalho
de `fiscal/dominio/saidas_mensais.sql` antes de mexer. Em resumo: a derivada
agrega ao grão da NOTA antes do join (senão a contagem de notas é multiplicada
pelo número de itens), o join é LEFT (8,23% das notas não têm item, e com join
interno somem R$ 101,2 milhões por trimestre) e o filtro do item vive dentro da
derivada.

**`modelo` é desnormalizado aqui**, a partir de `bi_fiscal_especie`. A mesma
espécie de documento tem vários códigos no Domínio — NF-e tem 4, NFC-e tem 3,
NFS-e tem 6 —, e agrupar por `codi_esp` parte o NFC-e em três fatias no gráfico
de espécie. Guardar o modelo na linha do fato torna o agrupamento certo o mais
fácil de escrever.

Depende de `fiscal_dimensoes`: a FK de `bi_fiscal_nota_mensal` aponta para
`bi_empresa_fiscal`. A dependência é conferida antes de ler a origem, com
mensagem própria — sem isso o erro chegaria como violação de chave estrangeira.
"""

from __future__ import annotations

import logging
from datetime import date

from psycopg2.extras import execute_values

from ...config import get_settings
from ...data.connection import get_connection, linhas_dict, transacao
from ...data.sql import carregar_sql
from ...fiscal import modelos
from ...shared.errors import ErroValidacao
from .. import dominio

logger = logging.getLogger(__name__)

CHAVE = "fiscal_movimento"
NOME = "Movimento fiscal mensal"
DESCRICAO = (
    "Nota de entrada, de saída e de serviço, agregadas por mês, com os impostos "
    "destacados. Alimenta os KPIs, os rankings e o mapa do BI Fiscal."
)
FONTE = "Domínio · efsaidas, efentradas, efservicos"

# O teto do Portal (20) virou CONFIGURAÇÃO aqui — ver `sinc_teto_de_empresas`
# em `app/config.py`. Lá ele existe porque a rota roda síncrona dentro da
# requisição e o gunicorn corta; aqui a carga em lote é o trabalho, e a
# mensagem de erro do próprio Portal já mandava usar o CLI para isso.
LOTE_GRAVACAO = 5_000

# Faixa de competência plausível, como em `contabil_saldos`. Fora dela a data
# está digitada errada na origem: medido, `efentradas` tem uma nota em 7024 e
# outra em 2120. A linha é IMPORTADA assim mesmo — o portal é espelho da origem,
# e descartá-la faria o total do portal discordar do Domínio sem ninguém
# conseguir explicar. O filtro serve ao `resumo()`, para o card não anunciar
# "última competência 08/7024".
ANO_MINIMO = 1990

# Os três fatos, na ordem em que entram. `tipo` é a coluna que separa os três na
# tabela única, e há guarda estática exigindo que todo SQL do módulo a filtre:
# uma consulta que a esqueça soma compra dentro de faturamento, e o número sai
# plausível.
TIPOS = (
    ("S", "fiscal/dominio/saidas_mensais.sql", "C", 2),
    ("E", "fiscal/dominio/entradas_mensais.sql", "F", 2),
    ("V", "fiscal/dominio/servicos_mensais.sql", "C", 1),
)

_COLUNAS = (
    "id_empresa",
    "ano",
    "mes",
    "tipo",
    "codi_esp",
    "modelo",
    "codi_nat",
    "versao_nat",
    "codi_acu",
    "papel",
    "participante",
    "uf",
    "situacao",
    "qtd_notas",
    "qtd_canceladas",
    "qtd_notas_sem_item",
    "valor_contabil",
    "valor_contabil_itens",
    "valor_produtos",
    "base_icms",
    "valor_icms",
    "base_icms_st",
    "valor_icms_st",
    "base_ipi",
    "valor_ipi",
    "base_pis",
    "valor_pis",
    "base_cofins",
    "valor_cofins",
    "base_iss",
    "valor_iss",
)

_MEDIDAS = _COLUNAS[13:]


def _ano_maximo() -> int:
    return date.today().year + 1


# ── Estado local ─────────────────────────────────────────────────────────────


def _empresas_habilitadas() -> list[dict]:
    """Empresas com cadastro fiscal, e o estado do movimento de cada uma.

    `competencia_max` ignora o ano implausível pelo mesmo motivo de
    `contabil_saldos`: sem o filtro, `MAX` pega justamente a data digitada
    errada e o card anuncia uma competência que se lê como tela quebrada.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id_empresa,
                   e.razao_social,
                   e.nome_fantasia,
                   e.cnpj,
                   COUNT(n.id_fiscal_nota_mensal) AS linhas,
                   MAX(n.ano * 100 + n.mes) FILTER (
                       WHERE n.ano BETWEEN %(ano_minimo)s AND %(ano_maximo)s
                   ) AS competencia_max
              FROM empresas e
              JOIN bi_empresa_fiscal bf ON bf.id_empresa = e.id_empresa
              LEFT JOIN bi_fiscal_nota_mensal n ON n.id_empresa = e.id_empresa
             WHERE e.ativo
             GROUP BY e.id_empresa, e.razao_social, e.nome_fantasia, e.cnpj
             ORDER BY COALESCE(e.razao_social, ''), e.id_empresa
            """,
            {"ano_minimo": ANO_MINIMO, "ano_maximo": _ano_maximo()},
        )
        return linhas_dict(cur)


def pendentes(busca: str | None = None) -> list[dict]:
    """Empresas habilitadas e **sem** movimento importado."""
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
    com_movimento = [e for e in empresas if e["linhas"]]

    dados: dict = {
        "rotulo": "Empresas com movimento fiscal",
        "valor": f"{len(com_movimento)} de {len(empresas)}",
    }

    if not empresas:
        dados["alerta"] = (
            "Nenhuma empresa habilitada no BI Fiscal. Rode antes o importador "
            "'Cadastros fiscais da empresa' — o movimento se prende a ele."
        )
        return dados

    if not com_movimento:
        dados["alerta"] = (
            "As empresas estão habilitadas, mas nenhuma tem movimento. Marque-as na "
            "lista abaixo: sem movimento a tela do BI Fiscal não tem número a mostrar."
        )
        return dados

    plausiveis = [e["competencia_max"] for e in com_movimento if e["competencia_max"]]
    if plausiveis:
        ultima = max(plausiveis)
        dados["detalhe"] = f"última competência {ultima % 100:02d}/{ultima // 100}"
    return dados


# ── Leitura ──────────────────────────────────────────────────────────────────


def _modelos_por_especie() -> dict[int, str]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT codi_esp, codigo_modelo FROM bi_fiscal_especie")
        return {linha[0]: linha[1] for linha in cur.fetchall()}


def _inteiro(valor) -> int | None:
    return None if valor is None else int(valor)


def _ler(consultar, id_empresa: int, tipo: str, sql: str, papel: str, params: int, mapa, contagem):
    """Um dos três fatos de uma empresa, já na forma de `bi_fiscal_nota_mensal`.

    `params` é quantas vezes o `codi_emp` entra na consulta: duas em saída e
    entrada (a derivada dos itens e o cabeçalho), uma em serviço, que não tem
    derivada porque `efservicos` não tem item agregável.
    """
    linhas = consultar(carregar_sql(sql), (id_empresa,) * params)
    aceitas = []

    for linha in linhas:
        ano, mes = _inteiro(linha["ano"]), _inteiro(linha["mes"])
        if ano is None or mes is None or not 1 <= mes <= 12:
            # Nota sem data utilizável. A CHECK do mês recusaria a linha e
            # derrubaria a empresa inteira; descartar e contar diz mais.
            contagem["sem_competencia"] += 1
            continue
        if not ANO_MINIMO <= ano <= _ano_maximo():
            contagem["competencia_implausivel"] += 1
            contagem["anos_implausiveis"].add(ano)

        codi_esp = int(linha["codi_esp"])
        modelo = mapa.get(codi_esp)
        if modelo is None:
            # Espécie que o catálogo não tem. Acontece quando `fiscal_cadastros`
            # não rodou depois de o Domínio ganhar espécie nova. A linha entra
            # com "ZZ" e o log diz o que rodar — perdê-la tiraria valor do KPI.
            contagem["especie_desconhecida"].add(codi_esp)
            modelo = modelos.normalizar(None)

        aceitas.append(
            (
                id_empresa,
                ano,
                mes,
                tipo,
                codi_esp,
                modelo,
                _inteiro(linha["codi_nat"]),
                _inteiro(linha["versao_nat"]),
                int(linha["codi_acu"] or 0),
                papel,
                int(linha["participante"]),
                (linha["uf"] or "").strip() or None,
                int(linha["situacao"] or 0),
                *(linha[medida] or 0 for medida in _MEDIDAS),
            )
        )

    contagem["lidas"] += len(linhas)
    return aceitas


# ── Gravação ─────────────────────────────────────────────────────────────────


def _gravar(id_empresa: int, por_tipo: dict[str, list[tuple]]) -> int:
    """Recarrega as fatias (empresa, tipo) numa transação só.

    **DELETE + INSERT, e não upsert**, como `contabil_lancamentos`: o upsert não
    sabe remover o que saiu da origem, e nota estornada continuaria aqui
    inflando o KPI de faturamento. O DELETE é por (empresa, tipo) e não por
    empresa, para uma execução que traga só saída não apagar a entrada.
    """
    total = 0
    with transacao() as conn, conn.cursor() as cur:
        for tipo, linhas in por_tipo.items():
            cur.execute(
                "DELETE FROM bi_fiscal_nota_mensal WHERE id_empresa = %s AND tipo = %s",
                (id_empresa, tipo),
            )
            if not linhas:
                continue
            execute_values(
                cur,
                f"INSERT INTO bi_fiscal_nota_mensal ({', '.join(_COLUNAS)}) VALUES %s",
                linhas,
                page_size=LOTE_GRAVACAO,
            )
            total += len(linhas)

        # `competencia_max` é desta tabela, e por isso é este importador que a
        # escreve. Ela define a janela do período da tela, e sai do MOVIMENTO e
        # não da apuração — derivá-la da apuração faria a janela encolher
        # exatamente nas empresas que faturaram e não apuraram, que é a primeira
        # pendência da tela de Início.
        cur.execute(
            """
            UPDATE bi_empresa_fiscal bf
               SET competencia_max = sub.competencia
              FROM (
                  SELECT MAKE_DATE(MAX(ano * 100 + mes) / 100,
                                   MAX(ano * 100 + mes) %% 100, 1) AS competencia
                    FROM bi_fiscal_nota_mensal
                   WHERE id_empresa = %(id_empresa)s
                     AND ano BETWEEN %(ano_minimo)s AND %(ano_maximo)s
              ) sub
             WHERE bf.id_empresa = %(id_empresa)s
            """,
            {
                "id_empresa": id_empresa,
                "ano_minimo": ANO_MINIMO,
                "ano_maximo": _ano_maximo(),
            },
        )
    return total


# ── Execução ─────────────────────────────────────────────────────────────────


def _selecionar_alvo(incluir: bool, ids: list[int] | None):
    """Devolve (alvo, sem_cadastro, sem_movimento_nao_incluidas)."""
    empresas = {e["id_empresa"]: e for e in _empresas_habilitadas()}
    pedidas = ids if ids else list(empresas)
    sem_cadastro = [i for i in pedidas if i not in empresas]
    candidatas = [i for i in pedidas if i in empresas]

    if incluir:
        return candidatas, sem_cadastro, []
    alvo = [i for i in candidatas if empresas[i]["linhas"]]
    return alvo, sem_cadastro, [i for i in candidatas if not empresas[i]["linhas"]]


def executar(incluir: bool = False, dry_run: bool = False, ids: list[int] | None = None) -> dict:
    """Traz os três fatos de nota e devolve a contagem de linhas agregadas."""
    alvo, sem_cadastro, sem_movimento = _selecionar_alvo(incluir, ids)

    if sem_cadastro and ids:
        raise ErroValidacao(
            "Estas empresas não têm cadastro fiscal importado: "
            f"{', '.join(str(i) for i in sem_cadastro)}. "
            "Rode antes o importador 'Cadastros fiscais da empresa'."
        )

    teto = get_settings().sinc_teto_de_empresas
    if teto and len(alvo) > teto:
        raise ErroValidacao(
            f"Selecione no máximo {teto} empresas por execução — foram pedidas "
            f"{len(alvo)}. Aumente ou desligue SINC_TETO_DE_EMPRESAS no .env "
            "(0 desliga)."
        )

    resultado = {"lidas": 0, "incluidas": 0, "atualizadas": 0, "ignoradas": 0}
    if sem_movimento:
        resultado["sem_movimento"] = sem_movimento

    if not alvo:
        logger.info(
            "Importação de movimento fiscal%s: nenhuma empresa alvo (incluir=%s, ids=%s)",
            " (simulação)" if dry_run else "",
            incluir,
            ids,
        )
        return resultado

    mapa = _modelos_por_especie()
    if not mapa:
        raise ErroValidacao(
            "O catálogo de espécies está vazio. Rode antes o importador "
            "'Cadastros fiscais globais' — sem ele o movimento não sabe qual "
            "modelo de documento gravar, e o gráfico de espécie sai sem rótulo."
        )

    for id_empresa in alvo:
        contagem = {
            "lidas": 0,
            "sem_competencia": 0,
            "competencia_implausivel": 0,
            "anos_implausiveis": set(),
            "especie_desconhecida": set(),
        }
        with dominio.sessao() as consultar:
            por_tipo = {
                tipo: _ler(consultar, id_empresa, tipo, sql, papel, params, mapa, contagem)
                for tipo, sql, papel, params in TIPOS
            }

        gravaveis = sum(len(v) for v in por_tipo.values())
        resultado["lidas"] += contagem["lidas"]
        resultado["ignoradas"] += contagem["lidas"] - gravaveis

        if contagem["competencia_implausivel"]:
            logger.warning(
                "Empresa %s: %s linha(s) com ano implausível %s — a data da nota está "
                "digitada errada no Domínio. O valor foi importado como está, mas não "
                "vai aparecer em nenhum período da tela.",
                id_empresa,
                contagem["competencia_implausivel"],
                sorted(contagem["anos_implausiveis"]),
            )
        if contagem["especie_desconhecida"]:
            logger.warning(
                "Empresa %s: espécie(s) %s fora do catálogo — gravadas como modelo ZZ. "
                "Rode o importador 'Cadastros fiscais globais'.",
                id_empresa,
                sorted(contagem["especie_desconhecida"]),
            )

        if dry_run:
            resultado["incluidas"] += gravaveis
            continue

        resultado["incluidas"] += _gravar(id_empresa, por_tipo)

    logger.info(
        "Importação de movimento fiscal%s em %s empresa(s): %s",
        " (simulação)" if dry_run else "",
        len(alvo),
        resultado,
    )
    return resultado
