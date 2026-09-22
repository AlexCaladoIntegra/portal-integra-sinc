"""Importador da estrutura da DFC indireta: Domínio → `bi_linha_dfc` e
`bi_linha_dfc_conta`.

As duas tabelas vêm juntas porque nenhuma é útil sozinha: sem as linhas o
vínculo não tem onde se prender (a FK é composta), e sem o vínculo as linhas
analíticas saem todas zeradas.

Divisão de propriedade dos dados:

    do Domínio : bi_linha_dfc, bi_linha_dfc_conta
    do portal  : nada — este importador é dono de todas as colunas das duas

Depende do **plano de contas**: `bi_linha_dfc_conta` tem FK composta para
`bi_conta`, e a estrutura sem o plano não tem em que se prender. Recusa cedo,
com mensagem própria, quando a empresa pedida não tem plano — a alternativa é
uma violação de FK cuja mensagem não diz a quem lê o que fazer.

DOIS DESCARTES, E ELES NÃO SÃO A MESMA COISA. Medido nos 53.210 vínculos da
origem em 09/09/2026:

    1.816  apontam para conta INATIVA no Domínio
        0  apontam para conta que não existe em `ctcontas`
      452  apontam para conta SINTÉTICA

O primeiro é **paridade**: `bi_conta` só guarda conta ativa, e o projeto de
referência descarta as mesmas pelo `JOIN ... AND ct.SITUACAO_CTA = 'A'`. Não há
o que consertar, e o aviso existe só para o número não parecer perda.

O segundo — conta ATIVA na origem e ausente de `bi_conta` — é outra coisa: quer
dizer que o plano do portal está velho, e a ação é reimportar o plano. Hoje são
zero; contá-los separado é o que impede o dia em que não forem de se confundir
com o primeiro caso.

O terceiro NÃO é descarte de importação: a sintética vinculada é GRAVADA, e
descartada no cálculo. Somá-la duplicaria o valor, porque a agregação da linha
já percorre as analíticas — o projeto de referência faz o mesmo, em
`if m.tipo_cta == "A"`. Gravar preserva o que a origem declara e mantém o
descarte visível numa contagem, em vez de escondê-lo aqui.
"""

from __future__ import annotations

import logging

from ...data.connection import get_connection, linhas_dict, transacao
from ...data.sql import carregar_sql
from ...shared.errors import ErroValidacao
from .. import dominio

logger = logging.getLogger(__name__)

CHAVE = "contabil_dfc"
NOME = "Estrutura da DFC"
DESCRICAO = (
    "Linhas da Demonstração dos Fluxos de Caixa (método indireto) e as contas "
    "vinculadas a cada uma. Requer o plano de contas."
)
FONTE = "Domínio · CTGRUPOSDFC_INDIRETO, CTGRUPOSDFC_INDIRETO_CONTAS"

# Os domínios das três colunas codificadas, como o `remarks` da origem os
# declara. Espelham os CHECK da migration `0010_bi_linha_dfc`: um valor fora
# daqui violaria a constraint e derrubaria a empresa inteira, então o
# importador descarta a LINHA e conta, em vez de estourar.
#
# Medido em 09/09/2026 nas 11.055 linhas da origem: zero fora do domínio, nas
# três colunas. As listas existem para o dia em que isso mudar — e para que
# nesse dia o sintoma seja uma linha a menos com contagem, e não uma
# `CheckViolation` no meio de um `executemany`.
TIPOS_DE_LINHA = ("V", "S", "A")
ATIVIDADES = (1, 2, 3)
FORMAS_DE_APURACAO = ("L", "P", "D", "C", "J", "A", "I", "E", "S", "T")


# ── Leitura da origem ────────────────────────────────────────────────────────


def _ler_empresa(consultar, id_empresa: int) -> dict:
    """Estrutura da DFC de uma empresa, em duas consultas na mesma conexão."""
    linhas = consultar(carregar_sql("bi/dominio/dfc_estrutura.sql"), (id_empresa,))
    vinculos = consultar(carregar_sql("bi/dominio/dfc_contas.sql"), (id_empresa,))

    return {
        "id_empresa": id_empresa,
        "linhas_brutas": linhas,
        "vinculos_brutos": vinculos,
        "linhas_lidas": len(linhas),
        "vinculos_lidos": len(vinculos),
    }


def _texto(valor) -> str:
    return (valor or "").strip()


def _codigo(valor) -> str | None:
    """Código de uma letra, normalizado. String vazia vira `None`.

    A origem devolve `TIPO` como `NULL` em header decorativo, mas um `CHAR(1)`
    lido por ODBC pode chegar como `' '`. Tratá-los igual é o que faz o header
    continuar header depois da importação.
    """
    texto = _texto(valor).upper()
    return texto or None


def classificar(dados: dict, contas_do_plano: set[int]) -> dict:
    """Separa o que entra do que é descartado, com o motivo de cada descarte.

    **Pura**: recebe o que a origem devolveu e o conjunto de contas que o portal
    tem, e não abre conexão nenhuma. É onde moram as duas regras que decidem o
    que a tela vai mostrar, e por isso é testável sem o Domínio nem o banco.

    Descarta a LINHA quando um dos três códigos está fora do domínio ou a
    descrição é vazia — nesses casos a linha não caberia nos CHECK da tabela.
    Descarta o VÍNCULO quando a conta não está em `bi_conta`, e o motivo separa
    "inativa na origem" (paridade, nada a fazer) de "ausente do plano do portal"
    (reimportar o plano).
    """
    linhas: list[dict] = []
    fora_do_dominio: list[int] = []

    for bruta in dados["linhas_brutas"]:
        codigo = bruta["codigo"]
        tipo_linha = _codigo(bruta["tipo_linha"])
        forma = _codigo(bruta["forma_apuracao"])
        descricao = _texto(bruta["descricao"])

        valida = (
            codigo is not None
            and bruta["ordem"] is not None
            and bruta["atividade"] in ATIVIDADES
            and tipo_linha in TIPOS_DE_LINHA
            and (forma is None or forma in FORMAS_DE_APURACAO)
            and descricao
        )
        if not valida:
            fora_do_dominio.append(codigo)
            continue

        linhas.append(
            {
                "codigo": codigo,
                "ordem": bruta["ordem"],
                "atividade": bruta["atividade"],
                "tipo_linha": tipo_linha,
                "forma_apuracao": forma,
                "descricao": descricao[:200],
            }
        )

    codigos_validos = {linha["codigo"] for linha in linhas}
    vinculos: list[dict] = []
    conta_inativa = 0
    conta_fora_do_plano: list[int] = []
    linha_inexistente = 0

    for bruto in dados["vinculos_brutos"]:
        if bruto["codigo"] not in codigos_validos:
            # Vínculo apontando para linha que a origem não tem (ou que foi
            # descartada acima). A FK o recusaria; contar é o que faz aparecer.
            linha_inexistente += 1
            continue
        if bruto["codi_cta"] not in contas_do_plano:
            if _texto(bruto["situacao_cta"]).upper() == "A":
                # Ativa lá e ausente aqui: o plano do portal está velho.
                conta_fora_do_plano.append(bruto["codi_cta"])
            else:
                # Inativa ou inexistente na origem — o mesmo que o projeto de
                # referência descarta. É paridade.
                conta_inativa += 1
            continue
        vinculos.append({"codigo": bruto["codigo"], "codi_cta": bruto["codi_cta"]})

    return {
        "linhas": linhas,
        "vinculos": vinculos,
        "descartes": {
            "linha_fora_do_dominio": fora_do_dominio,
            "conta_inativa": conta_inativa,
            "conta_fora_do_plano": sorted(set(conta_fora_do_plano)),
            "vinculo_sem_linha": linha_inexistente,
        },
    }


def _contas_do_plano(id_empresa: int) -> set[int]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT codi_cta FROM bi_conta WHERE id_empresa = %s", (id_empresa,))
        return {linha[0] for linha in cur.fetchall()}


# ── Estado local ─────────────────────────────────────────────────────────────


def _empresas_com_plano() -> list[dict]:
    """Empresas do portal com plano importado, e o estado da DFC de cada uma."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id_empresa,
                   e.razao_social,
                   e.nome_fantasia,
                   e.cnpj,
                   COUNT(DISTINCT l.codigo) AS linhas
              FROM empresas e
              JOIN bi_empresa_contabil bc ON bc.id_empresa = e.id_empresa
              LEFT JOIN bi_linha_dfc l    ON l.id_empresa  = e.id_empresa
             WHERE e.ativo
             GROUP BY e.id_empresa, e.razao_social, e.nome_fantasia, e.cnpj
             ORDER BY COALESCE(e.razao_social, ''), e.id_empresa
            """
        )
        return linhas_dict(cur)


def _todas_as_empresas() -> set[int]:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_empresa FROM empresas WHERE ativo")
        return {linha[0] for linha in cur.fetchall()}


def pendentes(busca: str | None = None) -> list[dict]:
    """Empresas com plano importado e **sem** estrutura de DFC.

    Empresa que já tem estrutura fica fora: para ela a operação é a atualização
    de rotina, não uma inclusão a escolher.
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
    """Estado local: quantas empresas têm estrutura de DFC, e o tamanho dela."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT (SELECT COUNT(*) FROM bi_empresa_contabil)                 AS com_plano,
                   (SELECT COUNT(DISTINCT id_empresa) FROM bi_linha_dfc)      AS com_dfc,
                   (SELECT COUNT(*) FROM bi_linha_dfc)                        AS linhas,
                   (SELECT COUNT(*) FROM bi_linha_dfc_conta)                  AS vinculos
            """
        )
        estado = linhas_dict(cur)[0]

    dados = {
        "rotulo": "Empresas com estrutura da DFC",
        "valor": f"{estado['com_dfc']} de {estado['com_plano']}",
        "detalhe": f"{estado['linhas']} linhas · {estado['vinculos']} contas vinculadas",
    }
    if not estado["com_plano"]:
        dados["alerta"] = (
            "Nenhuma empresa tem plano de contas importado. Rode antes o importador "
            "'Plano de contas contábil' — a estrutura da DFC se prende às contas dele."
        )
    elif not estado["com_dfc"]:
        dados["alerta"] = (
            "Nenhuma empresa tem estrutura da DFC importada. Marque as empresas na "
            "lista abaixo: sem ela a tela de DFC não tem linha nenhuma a mostrar."
        )
    elif not estado["vinculos"]:
        # Linhas sem vínculo nenhum é o estado que produz uma DFC visualmente
        # completa e inteiramente zerada — a tela mostra os cabeçalhos de
        # atividade e nada mais, porque a regra de omissão esconde toda linha
        # zerada. Sem este alerta, isso se lê como "a empresa não se movimentou".
        dados["alerta"] = (
            "As linhas da DFC foram importadas, mas nenhuma conta ficou vinculada a "
            "elas. Reimporte: sem o vínculo, a tela mostra só os cabeçalhos das "
            "atividades e todos os valores zerados."
        )
    return dados


# ── Execução ─────────────────────────────────────────────────────────────────


def _selecionar_alvo(
    incluir: bool, ids: list[int] | None
) -> tuple[list[int], list[int], list[int]]:
    """Empresas a processar, as pedidas sem plano e as puladas por não terem
    estrutura ainda.

    Sem `incluir`, só as que **já** têm estrutura importada: é a rotina de
    atualização. Com `incluir`, também as que ainda não têm.
    """
    empresas = _empresas_com_plano()
    com_plano = {e["id_empresa"]: e for e in empresas}

    if ids:
        sem_plano = [i for i in ids if i not in com_plano]
        candidatas = [i for i in ids if i in com_plano]
    else:
        sem_plano = []
        candidatas = list(com_plano)

    if incluir:
        return candidatas, sem_plano, []

    sem_estrutura = [i for i in candidatas if not com_plano[i]["linhas"]]
    return [i for i in candidatas if com_plano[i]["linhas"]], sem_plano, sem_estrutura


def _gravar(id_empresa: int, classificado: dict) -> tuple[int, int]:
    """Persiste a estrutura de uma empresa. Devolve (incluídas, atualizadas) de
    LINHAS — a contagem segue a entidade principal do importador.

    Tudo numa transação só: linhas novas com o vínculo antigo produziriam uma
    DFC silenciosamente errada — linha certa, valor de outra.

    Nenhum UPDATE escreve `updated_at` — é do trigger `set_updated_at()`.
    """
    linhas = classificado["linhas"]
    vinculos = classificado["vinculos"]

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT codigo FROM bi_linha_dfc WHERE id_empresa = %s", (id_empresa,))
        existentes = {linha[0] for linha in cur.fetchall()}

    novas = sum(1 for linha in linhas if linha["codigo"] not in existentes)
    atualizadas = len(linhas) - novas

    with transacao() as conn, conn.cursor() as cur:
        # A ORDEM SAI DO CAMINHO ANTES DO UPSERT, e isto não é enfeite.
        #
        # `uq_bi_linha_dfc_ordem` é UNIQUE por empresa. Quando o analista troca
        # duas linhas de posição no Domínio, o upsert da primeira colide com a
        # segunda, que ainda tem a ordem antiga — `UniqueViolation` no meio de
        # um `executemany`, numa empresa que só teve duas linhas reordenadas.
        #
        # Negativar tira todas do espaço de valores que o upsert vai usar
        # (`ORDEM` na origem é sempre >= 1, medido). Linha que não vier na
        # origem fica com ordem negativa e cai no DELETE logo abaixo.
        #
        # A alternativa seria a constraint DEFERRABLE, e ela custaria uma
        # migration a mais para resolver o mesmo com menos evidência no código.
        cur.execute(
            "UPDATE bi_linha_dfc SET ordem = -ordem WHERE id_empresa = %s AND ordem > 0",
            (id_empresa,),
        )

        if linhas:
            cur.executemany(
                """
                INSERT INTO bi_linha_dfc (id_empresa, codigo, ordem, atividade,
                                          tipo_linha, forma_apuracao, descricao)
                VALUES (%(id_empresa)s, %(codigo)s, %(ordem)s, %(atividade)s,
                        %(tipo_linha)s, %(forma_apuracao)s, %(descricao)s)
                ON CONFLICT (id_empresa, codigo) DO UPDATE
                   SET ordem          = EXCLUDED.ordem,
                       atividade      = EXCLUDED.atividade,
                       tipo_linha     = EXCLUDED.tipo_linha,
                       forma_apuracao = EXCLUDED.forma_apuracao,
                       descricao      = EXCLUDED.descricao
                """,
                [{**linha, "id_empresa": id_empresa} for linha in linhas],
            )

        # Linha que saiu da origem some do portal, com os vínculos dela via
        # CASCADE. Manter linha que a origem não tem mais deixaria a tela com
        # um item que ninguém consegue explicar — e, se ela for sintética, um
        # subtotal a mais no meio da cascata.
        codigos = [linha["codigo"] for linha in linhas]
        cur.execute(
            "DELETE FROM bi_linha_dfc WHERE id_empresa = %s AND codigo <> ALL(%s)",
            (id_empresa, codigos),
        )

        if vinculos:
            cur.executemany(
                """
                INSERT INTO bi_linha_dfc_conta (id_empresa, codigo, codi_cta)
                VALUES (%(id_empresa)s, %(codigo)s, %(codi_cta)s)
                ON CONFLICT (id_empresa, codigo, codi_cta) DO NOTHING
                """,
                [{**vinculo, "id_empresa": id_empresa} for vinculo in vinculos],
            )

        # Vínculo desfeito na origem em linha que PERMANECE — o CASCADE acima
        # não o alcança, porque a linha dele não foi removida. Sem este DELETE
        # a conta continuaria somando numa linha de onde o analista a tirou.
        cur.execute(
            """
            DELETE FROM bi_linha_dfc_conta v
             WHERE v.id_empresa = %s
               AND NOT EXISTS (
                   SELECT 1
                     FROM unnest(%s::integer[], %s::integer[]) AS origem(codigo, codi_cta)
                    WHERE origem.codigo   = v.codigo
                      AND origem.codi_cta = v.codi_cta
               )
            """,
            (
                id_empresa,
                [v["codigo"] for v in vinculos],
                [v["codi_cta"] for v in vinculos],
            ),
        )

    return novas, atualizadas


def executar(incluir: bool = False, dry_run: bool = False, ids: list[int] | None = None) -> dict:
    """Traz a estrutura da DFC das empresas e devolve a contagem.

    Por padrão **só atualiza** as empresas que já têm estrutura importada.
    `incluir=True` traz também as que ainda não têm; `ids` restringe.

    As contagens são de **linhas** — a entidade principal. `lidas` inclui os
    vínculos, porque eles vêm da origem na mesma execução e ignorá-los faria a
    contagem parecer menor do que o trabalho.
    """
    alvo, sem_plano, sem_estrutura = _selecionar_alvo(incluir, ids)

    if sem_plano and ids:
        # Recusa antes de ler a origem: o erro real seria uma violação de FK do
        # PostgreSQL, cuja mensagem não diz a quem lê o que precisa ser feito.
        raise ErroValidacao(
            "Estas empresas não têm plano de contas importado: "
            f"{', '.join(str(i) for i in sem_plano)}. "
            "Rode antes o importador 'Plano de contas contábil'."
        )

    resultado = {"lidas": 0, "incluidas": 0, "atualizadas": 0, "ignoradas": 0}
    if sem_estrutura:
        # Explica por que a rotina não tocou nelas, em vez de deixar o número
        # de ignoradas sem causa aparente.
        resultado["sem_estrutura"] = sem_estrutura

    if not alvo:
        logger.info(
            "Importação da estrutura da DFC%s: nenhuma empresa alvo (incluir=%s, ids=%s)",
            " (simulação)" if dry_run else "",
            incluir,
            ids,
        )
        return resultado

    with dominio.sessao() as consultar:
        lidos = [_ler_empresa(consultar, id_empresa) for id_empresa in alvo]

    sem_linha_na_origem: list[int] = []
    plano_desatualizado: list[int] = []

    for dados in lidos:
        id_empresa = dados["id_empresa"]
        resultado["lidas"] += dados["linhas_lidas"] + dados["vinculos_lidos"]

        classificado = classificar(dados, _contas_do_plano(id_empresa))
        descartes = classificado["descartes"]
        resultado["ignoradas"] += (
            len(descartes["linha_fora_do_dominio"])
            + descartes["conta_inativa"]
            + len(descartes["conta_fora_do_plano"])
            + descartes["vinculo_sem_linha"]
        )

        if not dados["linhas_lidas"]:
            # Metade do parque está aqui: 336 das 676 empresas com plano não têm
            # estrutura de DFC no Domínio. Não é erro, e a tela tem estado
            # próprio para isso — mas a importação precisa dizer, senão o
            # "0 incluídas" se lê como falha.
            sem_linha_na_origem.append(id_empresa)

        if descartes["conta_fora_do_plano"]:
            plano_desatualizado.append(id_empresa)
            logger.warning(
                "Empresa %s: %s conta(s) ativa(s) no Domínio estão vinculadas à DFC e "
                "não existem em bi_conta — reimporte o plano de contas. Contas: %s",
                id_empresa,
                len(descartes["conta_fora_do_plano"]),
                descartes["conta_fora_do_plano"][:10],
            )

        if dry_run:
            # Na simulação, tudo que a origem tem conta como inclusão: sem
            # gravar não há como saber o que já existia sem repetir a consulta
            # local, e a simulação existe para dizer o tamanho do trabalho.
            resultado["incluidas"] += len(classificado["linhas"])
            continue

        novas, atualizadas = _gravar(id_empresa, classificado)
        resultado["incluidas"] += novas
        resultado["atualizadas"] += atualizadas

    if sem_linha_na_origem:
        resultado["sem_estrutura_na_origem"] = sem_linha_na_origem
    if plano_desatualizado:
        resultado["plano_desatualizado"] = plano_desatualizado

    logger.info(
        "Importação da estrutura da DFC%s em %s empresa(s): %s",
        " (simulação)" if dry_run else "",
        len(alvo),
        resultado,
    )
    return resultado
