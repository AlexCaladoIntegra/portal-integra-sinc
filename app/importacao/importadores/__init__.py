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

# ── Quem depende de quem ─────────────────────────────────────────────────────
#
# A ordem do REGISTRO garante que um conjunto rode depois de quem ele precisa.
# Este mapa diz de QUEM — e a diferença aparece quando algo falha.
#
# O caso que o motivou: a empresa 514 não tem escrituração contábil no Domínio
# (zero contas em `ctcontas`), então `contabil_lancamentos` recusa. Sem este
# mapa, a rodada parava ali e os CINCO conjuntos fiscais nunca rodavam — apesar
# de o fiscal não depender do contábil em nada. São **187 das 608 empresas
# ativas** sem plano de contas na origem, medido em 22/09/2026: quase um terço
# do parque ficava sem sincronização fiscal por causa de um ramo que não se
# aplica a ele.
#
# Declarado aqui, e não como atributo em cada módulo, para não tocar nos dez
# arquivos copiados do Portal. É conhecimento de ORQUESTRAÇÃO, e a orquestração
# é deste projeto.
DEPENDE_DE: dict[str, tuple[str, ...]] = {
    "empresas": (),
    "contabil_plano": ("empresas",),
    "contabil_saldos": ("contabil_plano",),
    "contabil_dfc": ("contabil_plano",),
    "contabil_lancamentos": ("contabil_plano", "contabil_saldos"),
    # Global: espécies e CFOP não têm dono, e não dependem de empresa nenhuma.
    "fiscal_cadastros": (),
    "fiscal_dimensoes": ("empresas",),
    "fiscal_movimento": ("fiscal_dimensoes", "fiscal_cadastros"),
    "fiscal_apuracao": ("fiscal_dimensoes",),
    "fiscal_produto": ("fiscal_dimensoes",),
}


def depende_de(chave: str, quebrados: set[str]) -> str | None:
    """A primeira dependência quebrada de `chave`, ou `None` se o caminho está livre.

    Percorre a cadeia inteira, não só o vizinho: `fiscal_movimento` depende de
    `fiscal_dimensoes`, que depende de `empresas` — se `empresas` falhou, o
    movimento não deve rodar mesmo sem citá-la.
    """
    vistos: set[str] = set()
    fila = list(DEPENDE_DE.get(chave, ()))
    while fila:
        atual = fila.pop(0)
        if atual in quebrados:
            return atual
        if atual in vistos:
            continue
        vistos.add(atual)
        fila.extend(DEPENDE_DE.get(atual, ()))
    return None


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
