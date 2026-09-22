"""Histórico de execuções (`importacao_execucao`).

A tabela é do Portal e **compartilhada**: as linhas com `origem='painel'` vêm
do /admin de lá, as com `origem='sinc'` vêm daqui. Este projeto não a cria nem
a altera — ver "Sem Alembic" no CLAUDE.md.

O `LEFT JOIN usuario` ficou, ao contrário do que o plano previa. A tabela
`usuario` está no mesmo banco, e as linhas do Portal têm autor: descartar o
join perderia essa informação para metade do histórico que a tela mostra. As
linhas daqui têm `id_usuario` nulo — aqui não há login —, e `nome_usuario`
volta `None`, que a tela traduz para "pelo sincronizador".
"""

from __future__ import annotations

from ..data.connection import get_connection, linha_dict, linhas_dict, transacao
from ..shared import nomes

STATUS_EXECUTANDO = "executando"
STATUS_SUCESSO = "sucesso"
STATUS_ERRO = "erro"


class ImportacaoRepository:
    def registrar_inicio(
        self, chave: str, id_usuario: int | None, dry_run: bool, origem: str
    ) -> int:
        """Abre a linha do histórico e devolve o id.

        Gravada em transação própria, ANTES da importação: se o processo morrer
        no meio, a linha fica em `executando` e a tela mostra que a execução foi
        interrompida — melhor do que não haver registro nenhum.
        """
        with transacao() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO importacao_execucao (chave, id_usuario, dry_run, origem, status)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id_importacao_execucao
                """,
                (chave, id_usuario, dry_run, origem, STATUS_EXECUTANDO),
            )
            return cur.fetchone()[0]

    def registrar_sucesso(self, id_execucao: int, contagens: dict) -> None:
        with transacao() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE importacao_execucao
                   SET status = %s, concluida_em = NOW(),
                       lidas = %s, incluidas = %s, atualizadas = %s, ignoradas = %s
                 WHERE id_importacao_execucao = %s
                """,
                (
                    STATUS_SUCESSO,
                    contagens.get("lidas", 0),
                    contagens.get("incluidas", 0),
                    contagens.get("atualizadas", 0),
                    contagens.get("ignoradas", 0),
                    id_execucao,
                ),
            )

    def expirar_orfas(self, segundos: int) -> int:
        """Marca como `erro` execução que ficou em `executando` além do limite.

        A linha em `executando` depois de o processo morrer é decisão declarada
        em `registrar_inicio` — "melhor do que não haver registro nenhum". O que
        ela não podia continuar sendo é **eterna**: o card do importador mostrava
        "em execução" para sempre e ninguém sabia se havia algo rodando.

        O limite tem de ser maior que o `--timeout` do gunicorn (120 s desde o
        SEG-001), senão esta varredura mataria o registro de uma importação que
        ainda está viva.
        """
        with transacao() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE importacao_execucao
                   SET status = %s,
                       concluida_em = NOW(),
                       erro = %s
                 WHERE status = %s
                   AND iniciada_em < NOW() - make_interval(secs => %s)
                """,
                (
                    STATUS_ERRO,
                    "Execução interrompida: o processo não registrou conclusão. "
                    "Provável reinício do worker ou queda da conexão.",
                    STATUS_EXECUTANDO,
                    segundos,
                ),
            )
            return cur.rowcount

    def registrar_erro(self, id_execucao: int, erro: str) -> None:
        with transacao() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE importacao_execucao SET status = %s, concluida_em = NOW(), "
                "erro = %s WHERE id_importacao_execucao = %s",
                (STATUS_ERRO, erro[:2000], id_execucao),
            )

    def ultima_execucao(self, chave: str) -> dict | None:
        """Última execução real (simulações não contam como importação)."""
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id_importacao_execucao AS id, e.chave, e.status, e.origem,
                       e.iniciada_em, e.concluida_em,
                       e.lidas, e.incluidas, e.atualizadas, e.ignoradas, e.erro,
                       u.nome_usuario
                FROM importacao_execucao e
                LEFT JOIN usuario u ON u.id_usuario = e.id_usuario
                WHERE e.chave = %s AND NOT e.dry_run
                ORDER BY e.iniciada_em DESC
                LIMIT 1
                """,
                (chave,),
            )
            return linha_dict(cur)

    def historico(self, chave: str | None = None, limit: int = 20, offset: int = 0) -> list[dict]:
        sql = """
            SELECT e.id_importacao_execucao AS id, e.chave, e.status, e.origem, e.dry_run,
                   e.iniciada_em, e.concluida_em,
                   e.lidas, e.incluidas, e.atualizadas, e.ignoradas, e.erro,
                   u.nome_usuario
            FROM importacao_execucao e
            LEFT JOIN usuario u ON u.id_usuario = e.id_usuario
        """
        params: list = []
        if chave:
            sql += " WHERE e.chave = %s"
            params.append(chave)
        sql += " ORDER BY e.iniciada_em DESC LIMIT %s OFFSET %s"
        params += [limit, offset]

        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return linhas_dict(cur)

    def contar_historico(self, chave: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM importacao_execucao"
        params: tuple = ()
        if chave:
            sql += " WHERE chave = %s"
            params = (chave,)
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]


def empresas_ativas(busca: str | None = None) -> list[dict]:
    """As empresas ativas do Portal, para o lookup da tela.

    Usa a cascata `NOME_NO_CADASTRO` de `app/shared/nomes.py` — a mesma dos
    cadastros do Portal, que prefere `nome_exibicao` e `apelido` à razão
    social. É ela que distingue estabelecimentos do mesmo grupo: cinco
    empresas do DOURAGLASS têm a MESMA razão social e apelidos diferentes.

    Ordenada pelo nome, que é por onde se procura — não pelo código.
    """
    sql = f"""
        SELECT e.id_empresa,
               {nomes.NOME_NO_CADASTRO} AS nome,
               e.cnpj,
               e.situacao_origem
          FROM empresas e
         WHERE e.ativo
    """
    params: list = []
    if busca and busca.strip():
        # `%s` nos dois lados; o termo nunca entra no texto do SQL.
        sql += f" AND ({nomes.NOME_NO_CADASTRO} ILIKE %s OR CAST(e.id_empresa AS TEXT) = %s)"
        params += [f"%{busca.strip()}%", busca.strip()]
    sql += f" ORDER BY {nomes.NOME_NO_CADASTRO}, e.id_empresa"

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return linhas_dict(cur)
