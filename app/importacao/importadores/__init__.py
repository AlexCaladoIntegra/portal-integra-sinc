"""Um módulo por entidade importada, registrados em `REGISTRO`.

Completo: `empresas` (Etapa 3), BI Contábil (4) e BI Fiscal (5).

## O contrato de um módulo

    CHAVE: str        identificador estável, usado na URL e no histórico
    NOME: str         o título do card
    DESCRICAO: str    o que este importador traz
    FONTE: str        de onde vem, para a tela dizer a origem

    def executar(incluir: bool = False, dry_run: bool = False,
                 ids: list[int] | None = None) -> dict
        devolve {"lidas", "incluidas", "atualizadas", "ignoradas"}

Dois ganchos OPCIONAIS, que mudam a tela sem tocar no JS:

    pendentes(busca) -> list[dict]
        o que existe na origem e ainda não está no Portal. Quem o expõe ganha,
        no card, a lista com marcação em vez do botão simples.

    resumo() -> {"rotulo", "valor", "detalhe"?, "alerta"?}
        o estado local do que este importador alimenta, para o card explicar o
        próprio resultado.

## `OPCOES` não existe aqui, e é decisão

No Portal há um terceiro gancho, `OPCOES`, que é **contrato pela metade**:
`services.listar` o expõe, o JS desenha as caixas e manda o estado no POST, e
a rota lê apenas `incluir`, `dry_run` e `ids` — descartando o resto. Declarar
`OPCOES` lá põe um controle inerte na tela, e nenhum dos dez importadores o
usa.

Não copiamos o gancho. Se algum importador precisar de uma opção própria, o
caminho é acrescentá-la ao contrato de ponta a ponta — rota inclusive —, e não
reviver o meio-caminho.

## A ORDEM importa

`REGISTRO` é ordenado por **dependência**, e é a ordem em que
`POST /api/v1/sincronizacao/tudo` executa. As FKs do Portal são compostas e
`ON DELETE CASCADE`: gravar saldos antes do plano de contas viola integridade,
e gravar movimento fiscal antes das dimensões grava fato sem rótulo.

    1  empresas               a raiz de tudo
    2  contabil_plano         3  contabil_saldos
    4  contabil_dfc           5  contabil_lancamentos
    6  fiscal_cadastros       7  fiscal_dimensoes
    8  fiscal_movimento       9  fiscal_apuracao      10  fiscal_produto

`fiscal_cadastros` é o único GLOBAL — espécies e CFOP não têm `codi_emp` na
origem. Ele vem antes de `fiscal_dimensoes` porque o movimento desnormaliza o
`modelo` a partir da espécie, e sem o catálogo toda nota entraria como `'ZZ'`.
"""

from __future__ import annotations

from types import ModuleType

from . import (
    contabil_dfc,
    contabil_lancamentos,
    contabil_plano,
    contabil_saldos,
    empresas,
    fiscal_apuracao,
    fiscal_cadastros,
    fiscal_dimensoes,
    fiscal_movimento,
    fiscal_produto,
)

REGISTRO: dict[str, ModuleType] = {
    empresas.CHAVE: empresas,
    contabil_plano.CHAVE: contabil_plano,
    contabil_saldos.CHAVE: contabil_saldos,
    contabil_dfc.CHAVE: contabil_dfc,
    contabil_lancamentos.CHAVE: contabil_lancamentos,
    fiscal_cadastros.CHAVE: fiscal_cadastros,
    fiscal_dimensoes.CHAVE: fiscal_dimensoes,
    fiscal_movimento.CHAVE: fiscal_movimento,
    fiscal_apuracao.CHAVE: fiscal_apuracao,
    fiscal_produto.CHAVE: fiscal_produto,
}
