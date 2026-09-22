"""Importador de empresas: `bethadba.geempre` (Domínio) → `empresas` (Portal).

Cópia do importador homônimo do Portal Integra, com uma correção de referência
no cabeçalho (o módulo de sugestão mudou de lugar lá) e nada mais. A lógica é
literal — ver "Sobre o código copiado" no README.

Divisão de propriedade dos dados — a regra que este arquivo existe para
respeitar:

    do Domínio : razao_social, nome_fantasia, cnpj, apelido, situacao_origem
    do Portal  : id_empresa, ativo, nome_exibicao, usa_folha_pagamento

O importador grava **só** as colunas do Domínio. `updated_at` fica com o
trigger `set_updated_at()`.

`situacao_origem` (revisão 0021) é o `stat_emp` da origem, e é **informação, não
comando**: `ativo` continua sendo do Portal e este importador nunca a toca. Uma
empresa pode estar encerrada no Domínio e ainda precisar aparecer no Portal
enquanto se fecha o exercício — desativá-la sozinho seria pior que não avisar.

Depois de sincronizar, ele **sugere grupos matriz/filiais** pela raiz do CNPJ
(ADM-002). Isso não contraria a divisão acima: o grupo não vem do Domínio — lá
não existe vínculo matriz/filial, só `cgce_emp` —, é **derivado** dele. E a
sugestão só cria o que não existe: raiz cujo qualquer membro já esteja em algum
grupo é pulada inteira, para a importação não desfazer o que o analista ajustou
na tela. Ver `app/matriz_filiais/sugestao.py`.

As colunas `id_grupo`, `grupo_nome`, `id_empresa_matriz` e `tipo_empresa` de
`empresas` **não existem mais**: vinham do GRP (Betha), fonte que nunca entrou,
e o ADM-002 as removeu junto com a ambiguidade que elas criavam.
"""

from __future__ import annotations

import logging

from ...data.connection import get_connection, transacao
from ...shared.errors import ErroValidacao
from .. import dominio

logger = logging.getLogger(__name__)

CHAVE = "empresas"
NOME = "Empresas"
DESCRICAO = "Razão social, nome fantasia, apelido e CNPJ do cadastro de empresas."
FONTE = "Domínio · bethadba.geempre"

# `stat_emp = 'A'` é empresa ativa no Domínio. `razao_emp` pode vir vazia em
# cadastro antigo, daí o fallback para `nome_emp`. RTRIM porque as colunas são
# CHAR de largura fixa.
_SQL = """
    SELECT e.codi_emp                                   AS id_empresa,
           RTRIM(COALESCE(NULLIF(RTRIM(e.razao_emp), ''),
                          e.nome_emp))                  AS razao_social,
           RTRIM(e.fantasia_emp)                        AS nome_fantasia,
           RTRIM(e.apel_emp)                            AS apelido,
           RTRIM(e.cgce_emp)                            AS cnpj,
           e.stat_emp                                   AS status
    FROM {schema}.geempre e
    WHERE e.codi_emp IS NOT NULL
    {filtro}
    ORDER BY e.codi_emp
"""


def ler(ids: list[int] | None = None) -> list[dict]:
    """Lê o cadastro de empresas do Domínio, opcionalmente filtrado por id."""
    from ...config import get_settings

    filtro = ""
    params: tuple = ()
    if ids:
        filtro = f"AND e.codi_emp IN ({','.join('?' * len(ids))})"
        params = tuple(ids)

    sql = _SQL.format(schema=get_settings().dominio_schema, filtro=filtro)
    linhas = dominio.consultar(sql, params)

    for linha in linhas:
        # Campo vazio no Domínio vira NULL no portal: string vazia poluiria a
        # tela e atrapalha a busca.
        for campo in ("nome_fantasia", "cnpj"):
            if not (linha.get(campo) or "").strip():
                linha[campo] = None
    return linhas


def pendentes(busca: str | None = None) -> list[dict]:
    """Empresas que existem no Domínio e **ainda não estão** no portal.

    É a lista que a tela de importação oferece para marcar. Já vem sem as
    cadastradas, então não há como importar a mesma empresa duas vezes nem
    ver linha repetida.

    Empresa inativa no Domínio (`stat_emp <> 'A'`) fica fora: não faz sentido
    trazer para o portal cadastro que a origem já encerrou.

    Ordenada por razão social — é por ela que se procura uma empresa.
    """
    no_portal = _ids_no_portal(apenas_ativas=False)
    alvo = (busca or "").strip().lower()

    disponiveis = []
    for linha in ler(None):
        if linha["id_empresa"] in no_portal:
            continue
        if (linha.get("status") or "A").strip() != "A":
            continue
        if alvo and not (
            alvo in (linha["razao_social"] or "").lower()
            or alvo in (linha["nome_fantasia"] or "").lower()
            or alvo in (linha["cnpj"] or "")
            or alvo == str(linha["id_empresa"])
        ):
            continue
        disponiveis.append(
            {
                "id_empresa": linha["id_empresa"],
                "razao_social": linha["razao_social"],
                "nome_fantasia": linha["nome_fantasia"],
                "cnpj": linha["cnpj"],
            }
        )

    disponiveis.sort(key=lambda e: ((e["razao_social"] or "").upper(), e["id_empresa"]))
    return disponiveis


def resumo() -> dict:
    """Estado local, mostrado no card do importador.

    Responde a pergunta que a contagem crua não responde: "por que ele leu 917
    e ignorou 917?". Se o portal não tem empresa cadastrada, não há o que
    atualizar — e o `alerta` diz o caminho.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM empresas")
        total = cur.fetchone()[0]
        # Encerradas na ORIGEM e ativas no PORTAL. A coluna é informação (ver o
        # cabeçalho), então nada acontece sozinho — mas ficar calado deixaria
        # essas empresas somando nos consolidados sem ninguém saber.
        cur.execute(
            "SELECT id_empresa FROM empresas "
            "WHERE ativo AND situacao_origem IS NOT NULL AND situacao_origem <> 'A' "
            "ORDER BY id_empresa"
        )
        encerradas = [linha[0] for linha in cur.fetchall()]

    dados = {"rotulo": "Empresas no portal", "valor": total}
    if encerradas:
        dados["alerta"] = (
            f"{len(encerradas)} empresa(s) ativas aqui e ENCERRADAS no Domínio: "
            f"{', '.join(str(i) for i in encerradas[:12])}"
            f"{'…' if len(encerradas) > 12 else ''}. "
            "Elas continuam sendo atualizadas e somando nos consolidados. "
            "Desativá-las é decisão do Portal — a sincronização não mexe em 'ativo'."
        )
    if not total:
        dados["alerta"] = (
            "O portal ainda não tem nenhuma empresa cadastrada. Informe os códigos "
            "e marque a opção de cadastrar para trazê-las — sem isso a importação "
            "não tem o que atualizar."
        )
    return dados


def _ids_no_portal(apenas_ativas: bool) -> set[int]:
    sql = "SELECT id_empresa FROM empresas"
    if apenas_ativas:
        sql += " WHERE ativo"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return {linha[0] for linha in cur.fetchall()}


def executar(incluir: bool = False, dry_run: bool = False, ids: list[int] | None = None) -> dict:
    """Sincroniza as empresas e devolve a contagem do que aconteceu.

    Por padrão **só atualiza** as empresas já cadastradas e ativas no portal.
    É deliberado: o escritório tem centenas de empresas no Domínio e apenas as
    que participam do portal devem ser espelhadas.

    `incluir=True` cadastra as empresas **informadas em `ids`** que ainda não
    existem (grava só `id_empresa` e `ativo`). Incluir sem informar os códigos
    é recusado: o portal recebe as empresas que alguém decidiu colocar nele,
    uma a uma — em volume, isso é trabalho da opção "Incluir empresa" da tela
    de Empresas, não de uma importação que traz tudo.
    """
    if incluir and not ids:
        raise ErroValidacao(
            "Informe os códigos das empresas a cadastrar. A importação não "
            "cadastra todas as empresas do Domínio de uma vez."
        )

    linhas = ler(ids)
    resultado = {"lidas": len(linhas), "incluidas": 0, "atualizadas": 0, "ignoradas": 0}
    faltantes: list[int] = []

    if incluir:
        # Considera todas as existentes, ativas ou não: reinserir uma empresa
        # que foi inativada no portal desfaria uma decisão do administrador.
        existentes = _ids_no_portal(apenas_ativas=False)
        faltantes = [
            linha["id_empresa"]
            for linha in linhas
            if (linha.get("status") or "A").strip() == "A" and linha["id_empresa"] not in existentes
        ]
        resultado["incluidas"] = len(faltantes)
        if faltantes and not dry_run:
            with transacao() as conn, conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO empresas (id_empresa, ativo) VALUES (%s, TRUE) "
                    "ON CONFLICT (id_empresa) DO NOTHING",
                    [(id_empresa,) for id_empresa in faltantes],
                )

    # No dry-run, considera como alvo o que existiria depois da inclusão, para
    # que o número previsto corresponda ao da execução real.
    alvo_ids = _ids_no_portal(apenas_ativas=True)
    if dry_run:
        alvo_ids = alvo_ids | set(faltantes)

    alvo = [linha for linha in linhas if linha["id_empresa"] in alvo_ids]
    resultado["atualizadas"] = len(alvo)
    resultado["ignoradas"] = len(linhas) - len(alvo)

    # Códigos pedidos que ficaram de fora por não estarem no portal. Sem isso a
    # tela só consegue dizer "ignoradas: 917", que não explica nada nem diz o
    # que fazer. Só quando há `ids`: sem eles a lista seria o cadastro inteiro
    # do escritório.
    if ids and not incluir:
        resultado["nao_cadastradas"] = [
            linha["id_empresa"] for linha in linhas if linha["id_empresa"] not in alvo_ids
        ]

    if alvo and not dry_run:
        with transacao() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                UPDATE empresas
                   SET razao_social    = %(razao_social)s,
                       nome_fantasia   = %(nome_fantasia)s,
                       apelido         = %(apelido)s,
                       cnpj            = %(cnpj)s,
                       situacao_origem = %(situacao_origem)s
                 WHERE id_empresa      = %(id_empresa)s
                """,
                [
                    {
                        "id_empresa": linha["id_empresa"],
                        "razao_social": linha["razao_social"],
                        "nome_fantasia": linha["nome_fantasia"],
                        "apelido": linha["apelido"],
                        "cnpj": linha["cnpj"],
                        # `stat_emp` vem como CHAR(1) de largura fixa; o RTRIM
                        # está na consulta. Vazio vira NULL, que se lê como
                        # "a origem não informou" e não como uma situação.
                        "situacao_origem": (linha.get("status") or "").strip() or None,
                    }
                    for linha in alvo
                ],
            )

    resultado.update(_sugerir_grupos(dry_run))

    logger.info(
        "Importação de empresas%s: %s",
        " (simulação)" if dry_run else "",
        resultado,
    )
    return resultado


def _sugerir_grupos(dry_run: bool) -> dict:
    """Cria os grupos matriz/filiais que a raiz do CNPJ indica e ainda não existem.

    Roda **depois** do `UPDATE`, e a ordem importa: é o `UPDATE` que traz o
    `cnpj` do Domínio, e é do CNPJ que a sugestão vive. Invertido, a primeira
    importação de uma empresa nova não a agruparia.

    Vive aqui, e não num importador próprio, porque não é uma entidade nova
    vinda de fora — é uma leitura do que acabou de chegar. E como este
    importador se dispara pelo painel quando se quiser, ele **é** o botão de
    "sugerir grupos".
    """
    from ...matriz_filiais.repositories import GrupoMatrizFiliaisRepository
    from ...matriz_filiais.sugestao import montar_sugestoes

    repo = GrupoMatrizFiliaisRepository()
    sugestoes, contagens = montar_sugestoes(repo.empresas_para_sugestao(), repo.nomes_em_uso())

    if sugestoes and not dry_run:
        repo.criar_em_lote(sugestoes)

    if contagens.ambiguas:
        # Aviso e não erro: o cadastro duplicado é do Domínio, não do portal, e
        # a importação não tem por que falhar por causa dele. Mas ficar calado
        # deixaria o analista sem saber por que aquelas empresas não agruparam.
        logger.warning(
            "Grupo matriz/filiais: %s raiz(es) de CNPJ com cadastro duplicado não "
            "foram agrupadas (empresas %s). São a mesma pessoa jurídica cadastrada "
            "mais de uma vez no Domínio — somá-las contaria o mesmo movimento em "
            "dobro. Confira o cadastro na origem.",
            contagens.ambiguas,
            sorted(contagens.ambiguas_ids),
        )

    return contagens.como_dict()
