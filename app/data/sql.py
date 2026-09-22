"""Carregamento do SQL versionado em `app/data/queries/`.

As consultas ao Domínio são grandes, têm regra de negócio no corpo e precisam
ser lidas e revisadas sem abrir código Python — por isso moram em arquivo.
Nas Etapas 4 e 5 vêm 23 delas, copiadas do Portal com o caminho relativo
preservado: os importadores as referenciam por string literal
(`carregar_sql("bi/dominio/saldos_mensais.sql")`), e mudar a árvore quebraria
a cópia em silêncio.

O arquivo é lido uma vez por processo (`cache`). Alterar um `.sql` exige
reiniciar; `limpar_cache()` existe para o teste que precisa disso.

Podado em relação ao do Portal: saíram `carregar_sql_com_nome` (depende de
`app/shared/nomes.py`, que é cascata de exibição de nome de empresa — coisa de
tela) e `padrao_de_busca` (monta `ILIKE` de campo de busca). Nenhum dos dois
tem uso na extração.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

QUERIES_DIR = Path(__file__).resolve().parent / "queries"


@cache
def carregar_sql(nome: str) -> str:
    """SQL de `app/data/queries/<nome>`, relativo e com barras normais.

    `nome` inesperado é erro de programação, não de entrada: `FileNotFoundError`
    com o caminho absoluto na mensagem, para o desenvolvedor achar o arquivo em
    vez de receber um erro de sintaxe do driver com SQL vazio.

    Recusa caminho que escape de `queries/` — o parâmetro nunca deve vir de
    requisição, e a guarda existe para que isso continue verdade.
    """
    caminho = (QUERIES_DIR / nome).resolve()
    if not caminho.is_relative_to(QUERIES_DIR):
        raise ValueError(f"Caminho de SQL fora de queries/: {nome!r}")
    if not caminho.is_file():
        raise FileNotFoundError(f"SQL não encontrado: {caminho}")
    return caminho.read_text(encoding="utf-8")


def limpar_cache() -> None:
    """Descarta o cache de arquivos lidos (usado em teste)."""
    carregar_sql.cache_clear()
