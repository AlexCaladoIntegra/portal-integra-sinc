"""Acesso a `grupo_matriz_filiais` — apenas o necessário para a sugestão.

Recorte do `matriz_filiais/repositories.py` do Portal (427 linhas). Ficaram os
três métodos que `montar_sugestoes` consome, mais o `_gravar_membros` que o
`criar_em_lote` usa. Todo o CRUD — criar pela tela, atualizar, excluir, listar,
buscar — ficou lá, porque é do cadastro e não da sincronização.

As constantes `MATRIZ`/`FILIAL` vêm junto por dependência: `sugestao.py` as
importa deste módulo, e é o único ponto em que a cópia precisou manter o
caminho do import intacto.
"""

from __future__ import annotations

from ..data.connection import get_connection, linhas_dict, transacao
from ..shared import nomes

MATRIZ = "M"
FILIAL = "F"


class GrupoMatrizFiliaisRepository:
    def empresas_para_sugestao(self) -> list[dict]:
        """Empresas **ativas**, com o CNPJ e se já estão em algum grupo.

        Inline e não em arquivo porque é um `SELECT` simples: a política de
        agrupamento — quem é matriz, o que é ambíguo, o que se pula — vive em
        `sugestao.py`, onde se confere com dez linhas de cadastro e sem banco.

        Empresa **inativa** não entra: filial desativada somada no consolidado
        faria dinheiro aparecer do nada, sem nenhum sinal na tela.
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT e.id_empresa,
                       {nomes.NOME_NO_CADASTRO} AS nome,
                       e.cnpj,
                       EXISTS (SELECT 1 FROM grupo_matriz_filiais_empresa v
                                WHERE v.id_empresa = e.id_empresa) AS ja_agrupada
                  FROM empresas e
                 WHERE e.ativo
                 ORDER BY e.id_empresa
                """
            )
            return linhas_dict(cur)

    def nomes_em_uso(self) -> set[str]:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT nome FROM grupo_matriz_filiais")
            return {linha[0] for linha in cur.fetchall()}

    def criar_em_lote(self, sugestoes) -> int:
        """Cria vários grupos com os seus vínculos, numa transação só.

        Tudo ou nada: metade dos grupos criados deixaria a carteira num estado
        que ninguém pediu e que a próxima execução não corrige — porque as
        raízes já criadas passariam a contar como "já agrupadas".
        """
        if not sugestoes:
            return 0

        with transacao() as conn, conn.cursor() as cur:
            for sugestao in sugestoes:
                cur.execute(
                    "INSERT INTO grupo_matriz_filiais (nome) VALUES (%s) "
                    "RETURNING id_grupo_matriz_filiais",
                    (sugestao.nome,),
                )
                self._gravar_membros(cur, cur.fetchone()[0], sugestao.para_repositorio())
            return len(sugestoes)

    def _gravar_membros(self, cur, id_grupo: int, membros: list[dict]) -> None:
        if not membros:
            return
        cur.executemany(
            """
            INSERT INTO grupo_matriz_filiais_empresa (id_grupo_matriz_filiais, id_empresa, papel)
            VALUES (%s, %s, %s)
            """,
            [(id_grupo, m["id_empresa"], m["papel"]) for m in membros],
        )
