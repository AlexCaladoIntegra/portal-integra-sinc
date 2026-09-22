"""Importador das dimensões fiscais da empresa: Domínio → cadastros do módulo.

Traz acumulador, participante (cliente e fornecedor), produto e imposto, e é
quem **estabelece a empresa** no BI Fiscal gravando `bi_empresa_fiscal` — a raiz
para a qual as FK dos dois fatos e da apuração apontam. É o análogo de
`contabil_plano`, e como lá, os importadores seguintes recusam empresa sem
cadastro **antes** de ler a origem.

**Lê e grava empresa a empresa, no formato de `contabil_lancamentos` e NÃO no de
`contabil_saldos`.** Aquele monta `{id: _ler(...) for id in alvo}`, lendo todas
as empresas antes de gravar qualquer uma — o que aqui seria catastrófico: a
empresa 481 tem 61.563 produtos e `desc_pdi` é `varchar(255)`, dando ~18 MB só
dela. Com o teto de 20 empresas por execução, o pico chegaria a ~360 MB, que é
exatamente o defeito que `contabil_lancamentos` mediu em 304,4 MB.

E dentro da empresa, `efprodutos` sai em LOTES. É a única tabela do módulo com
texto longo e seis dígitos de linhas por empresa — e, contraintuitivamente, a
maior transferência do BI Fiscal inteiro: 1,6 milhão de linhas contra 2,36
milhões do fato de nota, mas com 300 bytes por linha em vez de 200.

**As dimensões trazem só o REFERENCIADO por algum movimento**, e não o cadastro
inteiro. Medido em 11/09/2026: acumulador 12.695 de 63.230 (corta 80%), imposto
6.082 de 103.463 (corta 94%), produto 1.615.251 de 2.830.476 (corta 43%).

**Não há FK do fato para estas tabelas, e é decisão.** No contábil a FK de
`bi_saldo_mensal` para `bi_conta` existe porque saldo de conta fora do plano não
significa nada — a conta É a estrutura. Aqui a dimensão é só o RÓTULO: uma nota
cujo participante não foi importado continua sendo uma nota com valor certo, e
só perde o nome no ranking. Com FK, a mesma situação derrubaria a importação
inteira da empresa. O preço é que as consultas da tela usam `LEFT JOIN` e
mostram o código quando o nome falta.
"""

from __future__ import annotations

import logging
import re

from psycopg2.extras import execute_values

from ...config import get_settings
from ...data.connection import get_connection, linhas_dict, transacao
from ...data.sql import carregar_sql
from ...fiscal import impostos as impostos_puro
from ...shared.errors import ErroValidacao
from .. import dominio

logger = logging.getLogger(__name__)

CHAVE = "fiscal_dimensoes"
NOME = "Cadastros fiscais da empresa"
DESCRICAO = (
    "Acumulador, cliente, fornecedor, produto e imposto de cada empresa. "
    "Habilita a empresa no BI Fiscal — os demais importadores do módulo a exigem."
)
FONTE = "Domínio · efacumulador, efclientes, effornece, efprodutos, EFIMPOSTO"

# Empresas por execução pelo painel. A rota é síncrona, dentro da requisição
# HTTP, com `proxy_read_timeout 90s` no nginx — a mesma constante e a mesma razão
# de `contabil_lancamentos`. A carga inicial roda pelo CLI, que não tem o teto.
# O teto do Portal (20) virou CONFIGURAÇÃO aqui — ver `sinc_teto_de_empresas`
# em `app/config.py`. Lá ele existe porque a rota roda síncrona dentro da
# requisição e o gunicorn corta; aqui a carga em lote é o trabalho, e a
# mensagem de erro do próprio Portal já mandava usar o CLI para isso.

# Linhas por ida ao driver ODBC, para `efprodutos`. Ver `dominio.sessao_em_lotes`.
LOTE_LEITURA = 5_000

# Tuplas por INSERT do `execute_values`.
LOTE_GRAVACAO = 5_000

# Só dígitos: `cgce_cli`/`cgce_for` são `char(14)` e vêm com espaço, e há
# cadastro com ponto e barra digitados à mão. É por este valor que o ranking
# consolidado agrupa — duas grafias do mesmo CNPJ virariam dois fornecedores.
_NAO_DIGITO = re.compile(r"\D")


def _documento(valor: str | None) -> str | None:
    limpo = _NAO_DIGITO.sub("", valor or "")
    return limpo or None


# ── Estado local ─────────────────────────────────────────────────────────────


def _empresas() -> list[dict]:
    """Empresas ativas do portal, e o estado fiscal de cada uma."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id_empresa,
                   e.razao_social,
                   e.nome_fantasia,
                   e.cnpj,
                   (bf.id_empresa IS NOT NULL) AS tem_cadastro,
                   COALESCE(p.participantes, 0) AS participantes,
                   COALESCE(pr.produtos, 0)     AS produtos
              FROM empresas e
              LEFT JOIN bi_empresa_fiscal bf ON bf.id_empresa = e.id_empresa
              LEFT JOIN (
                  SELECT id_empresa, COUNT(*) AS participantes
                    FROM bi_fiscal_participante GROUP BY id_empresa
              ) p ON p.id_empresa = e.id_empresa
              LEFT JOIN (
                  SELECT id_empresa, COUNT(*) AS produtos
                    FROM bi_fiscal_produto GROUP BY id_empresa
              ) pr ON pr.id_empresa = e.id_empresa
             WHERE e.ativo
             ORDER BY COALESCE(e.razao_social, ''), e.id_empresa
            """
        )
        return linhas_dict(cur)


def pendentes(busca: str | None = None) -> list[dict]:
    """Empresas ativas ainda **sem** cadastro fiscal no portal.

    Empresa que já tem cadastro fica fora: para ela a operação é a atualização
    de rotina, não uma inclusão a escolher — mesma regra de `contabil_saldos`.
    """
    alvo = (busca or "").strip().lower()
    disponiveis = []

    for empresa in _empresas():
        if empresa["tem_cadastro"]:
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
    """Estado local: quantas empresas estão habilitadas no BI Fiscal."""
    empresas = _empresas()
    com_cadastro = [e for e in empresas if e["tem_cadastro"]]

    dados: dict = {
        "rotulo": "Empresas com cadastro fiscal",
        "valor": f"{len(com_cadastro)} de {len(empresas)}",
    }

    if not com_cadastro:
        dados["alerta"] = (
            "Nenhuma empresa habilitada no BI Fiscal. Marque-as na lista abaixo: "
            "os importadores de movimento e de apuração recusam empresa sem cadastro."
        )
        return dados

    dados["detalhe"] = (
        f"{sum(e['participantes'] for e in com_cadastro):,} participantes · "
        f"{sum(e['produtos'] for e in com_cadastro):,} produtos"
    ).replace(",", ".")

    sem_produto = [e for e in com_cadastro if not e["produtos"]]
    if sem_produto:
        dados["alerta"] = (
            f"{len(sem_produto)} de {len(com_cadastro)} empresa(s) com cadastro e sem "
            "produto importado. O bloco “Total por Produto” abre vazio para elas."
        )
    return dados


# ── Leitura da origem ────────────────────────────────────────────────────────


def _tudo(consultar, sql: str, params: tuple = ()) -> list[dict]:
    """Consome um `sessao_em_lotes` inteiro. Para as consultas pequenas."""
    return [linha for lote in consultar(sql, params, tamanho=LOTE_LEITURA) for linha in lote]


def _censo(consultar) -> dict[int, dict]:
    """Quais empresas do Domínio têm movimento fiscal, e de que tipo.

    Uma consulta para o parque inteiro, e não um EXISTS por empresa: são 2,3 s
    contra 620 idas ao driver. Ver `fiscal/dominio/empresas_fiscais.sql`.
    """
    linhas = _tudo(consultar, carregar_sql("fiscal/dominio/empresas_fiscais.sql"))
    return {
        int(linha["codi_emp"]): {
            "tem_saida": bool(linha["tem_saida"]),
            "tem_entrada": bool(linha["tem_entrada"]),
            "tem_servico": bool(linha["tem_servico"]),
        }
        for linha in linhas
    }


def _ler_acumuladores(consultar, id_empresa: int) -> list[tuple]:
    linhas = _tudo(consultar, carregar_sql("fiscal/dominio/acumuladores.sql"), (id_empresa,))
    return [
        (id_empresa, int(linha["CODI_ACU"]), (linha["NOME_ACU"] or "").strip()) for linha in linhas
    ]


def _ler_participantes(consultar, id_empresa: int) -> list[tuple]:
    """Cliente e fornecedor na mesma forma — é o que os faz caber numa tabela só.

    `papel` entra na chave porque `codi_cli = 100` e `codi_for = 100` são pessoas
    diferentes: são contadores independentes em cadastros separados.
    """
    linhas: list[tuple] = []
    for sql, papel, prefixo in (
        ("fiscal/dominio/clientes.sql", "C", "cli"),
        ("fiscal/dominio/fornecedores.sql", "F", "for"),
    ):
        for linha in _tudo(consultar, carregar_sql(sql), (id_empresa,)):
            linhas.append(
                (
                    id_empresa,
                    papel,
                    int(linha[f"codi_{prefixo}"]),
                    (linha[f"nome_{prefixo}"] or "").strip() or None,
                    _documento(linha[f"cgce_{prefixo}"]),
                    (linha["sigl_est"] or "").strip() or None,
                )
            )
    return linhas


def _ler_impostos(consultar, id_empresa: int) -> list[tuple]:
    """O imposto que a empresa apura, com o nome cru E o canônico.

    `canonizar` é puro e decide quantos galhos a Árvore de Impostos tem: sem
    ele, o `codi_imp` 1 aparece como quatro galhos de ICMS, porque o cadastro
    do Domínio o nomeia de "ICMS " a "ICMS NORMAL" a, literalmente, "3".
    """
    linhas = _tudo(consultar, carregar_sql("fiscal/dominio/impostos.sql"), (id_empresa,))
    saida = []
    for linha in linhas:
        codi_imp = int(linha["codi_imp"])
        nome = (linha["nome_imp"] or "").strip()
        saida.append(
            (
                id_empresa,
                codi_imp,
                nome or f"Imposto {codi_imp}",
                impostos_puro.canonizar(codi_imp, nome),
            )
        )
    return saida


# ── Gravação ─────────────────────────────────────────────────────────────────


def _marcar_empresa(cur, id_empresa: int, flags: dict) -> None:
    """Cria ou atualiza a linha de `bi_empresa_fiscal`.

    As duas competências ficam de fora: `competencia_max` é do importador de
    movimento e `competencia_apuracao_max` é do de apuração. Cada um escreve a
    coluna que sabe medir — preenchê-las aqui, a partir de uma consulta de MAX
    na origem, faria a tela prometer um período que o portal ainda não tem.
    """
    cur.execute(
        """
        INSERT INTO bi_empresa_fiscal (id_empresa, tem_saida, tem_entrada, tem_servico)
        VALUES (%(id_empresa)s, %(tem_saida)s, %(tem_entrada)s, %(tem_servico)s)
        ON CONFLICT (id_empresa) DO UPDATE
           SET tem_saida   = EXCLUDED.tem_saida,
               tem_entrada = EXCLUDED.tem_entrada,
               tem_servico = EXCLUDED.tem_servico
        """,
        {"id_empresa": id_empresa, **flags},
    )


def _recarregar(cur, tabela: str, colunas: tuple[str, ...], id_empresa: int, linhas: list[tuple]):
    """DELETE da fatia da empresa + INSERT, dentro da transação de quem chama.

    Recarga e não upsert, como `contabil_lancamentos`: o upsert não sabe remover
    o que saiu da origem, e cadastro removido lá continuaria aqui — um produto
    que ninguém vende mais seguiria no ranking, e um participante excluído
    seguiria nomeando notas.

    A identificação da tabela e das colunas entra por f-string porque
    identificador não aceita placeholder. As duas vêm de constantes deste
    módulo, nunca de requisição.
    """
    cur.execute(f"DELETE FROM {tabela} WHERE id_empresa = %s", (id_empresa,))
    if linhas:
        execute_values(
            cur,
            f"INSERT INTO {tabela} ({', '.join(colunas)}) VALUES %s",
            linhas,
            page_size=LOTE_GRAVACAO,
        )


def _gravar_produtos(cur, id_empresa: int, consultar) -> int:
    """Os produtos, em lotes — a única leitura preguiçosa do módulo.

    O DELETE acontece antes do primeiro lote, dentro da mesma transação: uma
    falha no meio faz rollback e a empresa fica exatamente como estava.
    """
    cur.execute("DELETE FROM bi_fiscal_produto WHERE id_empresa = %s", (id_empresa,))
    total = 0
    for lote in consultar(
        carregar_sql("fiscal/dominio/produtos.sql"), (id_empresa,), tamanho=LOTE_LEITURA
    ):
        linhas = [
            (
                id_empresa,
                (linha["codi_pdi"] or "").strip(),
                (linha["desc_pdi"] or "").strip() or None,
                (linha["cncm_pdi"] or "").strip() or None,
            )
            for linha in lote
            if (linha["codi_pdi"] or "").strip()
        ]
        if not linhas:
            continue
        execute_values(
            cur,
            "INSERT INTO bi_fiscal_produto (id_empresa, codi_pdi, descricao, ncm) VALUES %s "
            "ON CONFLICT (id_empresa, codi_pdi) DO NOTHING",
            linhas,
            page_size=LOTE_GRAVACAO,
        )
        total += len(linhas)
    return total


# ── Execução ─────────────────────────────────────────────────────────────────


def _selecionar_alvo(incluir: bool, ids: list[int] | None) -> tuple[list[int], list[int]]:
    """Devolve (alvo, sem_cadastro_nao_incluidas)."""
    empresas = {e["id_empresa"]: e for e in _empresas()}
    pedidas = ids if ids else list(empresas)
    candidatas = [i for i in pedidas if i in empresas]

    if incluir:
        return candidatas, []
    alvo = [i for i in candidatas if empresas[i]["tem_cadastro"]]
    return alvo, [i for i in candidatas if not empresas[i]["tem_cadastro"]]


def executar(incluir: bool = False, dry_run: bool = False, ids: list[int] | None = None) -> dict:
    """Traz os cadastros fiscais e habilita as empresas no módulo.

    Por padrão **só atualiza** as que já têm cadastro. `incluir=True` traz
    também as que ainda não têm; `ids` restringe.
    """
    alvo, sem_cadastro = _selecionar_alvo(incluir, ids)

    teto = get_settings().sinc_teto_de_empresas
    if teto and len(alvo) > teto:
        raise ErroValidacao(
            f"Selecione no máximo {teto} empresas por execução — foram pedidas "
            f"{len(alvo)}. Aumente ou desligue SINC_TETO_DE_EMPRESAS no .env "
            "(0 desliga)."
        )

    resultado = {"lidas": 0, "incluidas": 0, "atualizadas": 0, "ignoradas": 0}
    if sem_cadastro:
        resultado["sem_cadastro"] = sem_cadastro

    if not alvo:
        logger.info(
            "Importação de dimensões fiscais%s: nenhuma empresa alvo (incluir=%s, ids=%s)",
            " (simulação)" if dry_run else "",
            incluir,
            ids,
        )
        return resultado

    sem_movimento: list[int] = []

    with dominio.sessao_em_lotes() as consultar:
        censo = _censo(consultar)

        for id_empresa in alvo:
            flags = censo.get(id_empresa)
            if flags is None:
                # A empresa existe no portal e não tem nota nenhuma na origem.
                # Não é erro: é o caso de quem só faz contabilidade. Criar a
                # linha faria a tela do BI Fiscal oferecê-la com tudo vazio.
                sem_movimento.append(id_empresa)
                continue

            acumuladores = _ler_acumuladores(consultar, id_empresa)
            participantes = _ler_participantes(consultar, id_empresa)
            impostos = _ler_impostos(consultar, id_empresa)
            resultado["lidas"] += len(acumuladores) + len(participantes) + len(impostos)

            if dry_run:
                resultado["incluidas"] += len(acumuladores) + len(participantes) + len(impostos)
                continue

            with transacao() as conn, conn.cursor() as cur:
                _marcar_empresa(cur, id_empresa, flags)
                _recarregar(
                    cur,
                    "bi_fiscal_acumulador",
                    ("id_empresa", "codi_acu", "nome"),
                    id_empresa,
                    acumuladores,
                )
                _recarregar(
                    cur,
                    "bi_fiscal_participante",
                    ("id_empresa", "papel", "codigo", "nome", "documento", "uf"),
                    id_empresa,
                    participantes,
                )
                _recarregar(
                    cur,
                    "bi_fiscal_imposto",
                    ("id_empresa", "codi_imp", "nome", "nome_canonico"),
                    id_empresa,
                    impostos,
                )
                produtos = _gravar_produtos(cur, id_empresa, consultar)

            resultado["lidas"] += produtos
            resultado["incluidas"] += (
                len(acumuladores) + len(participantes) + len(impostos) + produtos
            )

    if sem_movimento:
        resultado["sem_movimento"] = sem_movimento
        logger.info(
            "%s empresa(s) sem movimento fiscal no Domínio — não habilitadas: %s",
            len(sem_movimento),
            sem_movimento,
        )

    logger.info(
        "Importação de dimensões fiscais%s em %s empresa(s): %s",
        " (simulação)" if dry_run else "",
        len(alvo),
        resultado,
    )
    return resultado
