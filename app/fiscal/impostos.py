"""O nome canônico do imposto — decide os galhos da Árvore de Impostos.

Puro, sem I/O. Consumido pelo importador `fiscal_apuracao`, que grava
`bi_fiscal_imposto.nome_canonico` ao lado do `nome` cru da origem.

**Por que canonizar pelo CÓDIGO e não pelo nome.** O cadastro de imposto do
Domínio (`GEIMPOSTO`) é por EMPRESA e por VIGÊNCIA, então o mesmo imposto tem
nomes diferentes conforme quem cadastrou. Medido em 11/09/2026 nos 44
`codi_imp` que aparecem em `efsdoimp`:

    codi_imp   1  vai de "3" a "ICMS NORMAL", passando por "ICMS " e "ICMS"
    codi_imp   7  "IRPJ LUCRO PRESUMIDO" e "IRPJ-LP"
    codi_imp   6  "CONTRIBUICAO SOCIAL" e "CSLL LUCRO PRESUMIDO"
    codi_imp  17  "PIS LUCRO REAL" (636 empresas) e "PIS(nao cumulativo)" (35)
    codi_imp  24  "Contribuição Social(Retida)" e "PIS/COFINS/CSLL"

Um deles — o ICMS, que é o maior imposto do parque com R$ 79,1 milhões de guia
em 2025 — tem uma variante cujo nome é literalmente `"3"`. Nenhuma normalização
de string recupera "ICMS" a partir de `"3"`, e agrupar pelo nome cru põe o ICMS
em quatro galhos diferentes da Árvore, nenhum deles somando o imposto inteiro.

A diferença entre `"ICMS "` e `"ICMS"` (462 e 745 linhas, medido) é o caso fácil
e o mais traiçoeiro: um espaço no fim produz dois galhos visualmente IDÊNTICOS
na tela, e quem olha conclui que a tela está duplicando linhas.

**O que este módulo NÃO faz: fundir códigos diferentes.** `codi_imp` 4 (PIS
Lucro Presumido) e 17 (PIS Não Cumulativo) são regimes distintos com guias
distintas, e o dashboard de referência os mostra separados. Canonizar é
desfazer a variação de CADASTRO sobre o mesmo código, nunca a variação real
entre códigos.
"""

from __future__ import annotations

# Os 44 impostos que de fato aparecem em `efsdoimp` (medido em 11/09/2026), com
# o rótulo que vai para a tela. Cabe em código porque `codi_imp` é estável entre
# empresas — é o `NOME_IMP` que varia — e porque é este mapa que decide quantos
# galhos a Árvore de Impostos tem.
#
# Os rótulos de 162 e 163 são os nomes curtos pelos quais o escritório os
# conhece: no cadastro eles têm 78 e 88 caracteres ("Fundo de Desenvolvimento do
# Sistema Rodoviário do Estado do Mato Grosso do Sul"), que não cabem em legenda
# de gráfico nenhuma. Todo rótulo aqui respeita os 40 caracteres da coluna.
CANONICOS: dict[int, str] = {
    1: "ICMS",
    2: "IPI",
    3: "ISS",
    4: "PIS Lucro Presumido",
    5: "COFINS Lucro Presumido",
    6: "CSLL Lucro Presumido",
    7: "IRPJ Lucro Presumido",
    8: "DIFAL",
    9: "Substituição Tributária",
    10: "Simples (regime antigo)",
    16: "IRRF",
    17: "PIS Não Cumulativo",
    18: "ISS Retido",
    19: "COFINS Não Cumulativa",
    22: "PIS Retido",
    23: "COFINS Retido",
    24: "PIS/COFINS/CSLL Retidos",
    25: "Contribuições Retidas na Fonte",
    26: "INSS Retido",
    27: "ICMS Antecipado",
    28: "FUNRURAL",
    31: "ICMS ST Antecipação Total",
    33: "IRPJ Postergado",
    34: "ICMS Farmácia",
    38: "Retenções de Órgãos Públicos",
    39: "IRRF Propaganda",
    44: "Simples Nacional",
    63: "IRRF Aluguéis PF",
    64: "Simples MEI",
    103: "CPRB",
    108: "PIS SCP",
    109: "COFINS SCP",
    110: "CSLL SCP",
    111: "IRPJ SCP",
    122: "FAI",
    133: "PIS Importação",
    134: "COFINS Importação",
    145: "ICMS DIFAL Antecipado",
    146: "Fundo de Combate à Pobreza",
    162: "FUNDERSUL",
    163: "FUNDEMS",
    164: "ICMS Equalização Simples Nacional",
    183: "IBS",
    184: "CBS",
}

# Limite da coluna `bi_fiscal_imposto.nome_canonico`. O truncamento acontece
# aqui, e não no banco: o PostgreSQL levantaria erro e derrubaria a importação
# inteira de uma empresa por causa de um nome comprido de cadastro.
TAMANHO_MAXIMO = 40


def _limpar(nome: str | None) -> str:
    """Tira o espaço das pontas e colapsa o espaço interno.

    O colapso não é enfeite: `nome_esp` e `NOME_IMP` são colunas de largura fixa
    no Domínio e chegam com espaço duplicado no meio ("IRRF  Propaganda"), que
    produz dois galhos indistinguíveis na tela.
    """
    return " ".join((nome or "").split())


def canonizar(codi_imp: int, nome: str | None = None) -> str:
    """O nome canônico do imposto, para agrupar a Árvore.

    `nome` é o cru da origem e só é usado como FALLBACK, para código que não
    está no catálogo — o Domínio ganha imposto novo (IBS e CBS entraram com a
    reforma tributária e já estão escriturados em 250 empresas).

    Código desconhecido e sem nome vira `"Imposto <código>"`, nunca um rótulo
    genérico como "Outros": um galho chamado "Outros" esconderia justamente o
    imposto novo que alguém precisa ver, e some com ele na soma de um agregado
    que ninguém consegue abrir.
    """
    conhecido = CANONICOS.get(codi_imp)
    if conhecido:
        return conhecido

    limpo = _limpar(nome)
    if not limpo:
        return f"Imposto {codi_imp}"
    return limpo[:TAMANHO_MAXIMO].strip()
