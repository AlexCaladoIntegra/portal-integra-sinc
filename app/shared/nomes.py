"""Como o portal chama uma empresa — as duas cascatas, num lugar só.

São expressões SQL sobre a tabela `empresas`, e moram aqui porque **quatro
módulos** as usam: o BI Contábil, o BI Fiscal, o cadastro matriz/filiais e os
dois cadastros manuais de grupo. Escritas à mão em cada um — que era o estado
até a FIS-009 — elas divergem no dia em que alguém melhorar uma, e a mesma
empresa passa a ter dois nomes em duas telas vizinhas **sem que número nenhum
mude**.

Puras de propósito: são texto, sem I/O. Quem carrega consulta de arquivo é
`app/data/sql.py`, e é lá que mora o `carregar_sql_com_nome` que injeta estas
expressões no marcador `{nome_da_empresa}`.

## Por que DUAS, e não uma

A diferença é o FANTASIA, e ela é anterior a este módulo:

    exibição (BI)    nome_exibicao -> apelido -> nome_fantasia -> razao_social
    cadastro (admin) nome_exibicao -> apelido ->                  razao_social

O BI mostra a empresa como o CLIENTE a reconhece — a 272 é "BOB'S" para quem
trabalha nela e "SF VILAS GOURMET LTDA" só no contrato social. É o `display_name`
do `bi-contabil-dominio`, e a paridade com ele importa.

O cadastro mostra a empresa como o CONTRATO a nomeia, e a razão está registrada
em `agrupamentos/repositories.py` desde o ADM-003: as telas de vínculo listam
empresas para o analista LIGAR, e ali a identidade formal é o que desambigua. As
duas telas de cadastro combinam entre si de propósito.

**Unificá-las não é tarefa deste módulo.** Se um dia forem a mesma, é aqui que
as duas viram uma.

## O APELIDO entra nas duas, no mesmo degrau (FIS-009, RF-10)

Logo abaixo de `nome_exibicao` e acima do que a origem oferece. Ele é do Domínio
(`geempre.apel_emp`) e o importador o sobrescreve; `nome_exibicao` é do PORTAL
(IMP-001), o importador nunca a toca, e é por ela que o analista corrige um
apelido truncado sem que a próxima importação desfaça.

**O que o apelido resolve é o que nenhum dos outros resolvia: estabelecimento.**
Medido no grupo DOURAGLASS, cinco empresas do mesmo CNPJ raiz:

    510  fantasia "DOURAGLASS"   razão "...LTDA"   apelido "DOURAGLASS MTZ"
    512  fantasia "DOURAGLAS"    razão "...LTDA"   apelido "DOURAGLASS FILIAL CG"
    513  fantasia "DOURAGLASS"   razão "...LTDA"   apelido "DOURAGLASS FILIAL SP"
    514  fantasia "GLASSBOX"     razão "...LTDA"   apelido "DOURAGLASS FIL DDOS"
    515  fantasia vazio          razão "...LTDA"   apelido "DOURAGLASS FILIAL MG"

A razão social das cinco é a MESMA string. No cadastro matriz/filiais — a tela
que existe para dizer qual delas é a matriz — as cinco apareciam com o mesmo
texto, e é ali que o apelido vale mais.

Cobertura medida nas 920 empresas do Domínio: `apel_emp` tem ZERO vazios e 583
valores distintos nas 589 ativas, contra 212 vazios no fantasia. Os degraus
abaixo dele são inalcançáveis hoje, e ficam.

## O preço

`apel_emp` é `char(20)` e o Domínio trunca no meio da palavra:
`CARLOS ROBERTO JUNQU`, `NATHALIA NUNES GUEDI`. E é apelido de ESCRITÓRIO, não
nome do cliente: a 651 é `N.FARMA CALL CENTER` onde o fantasia diz "NACIONAL
FARMA". Em 105 das 589 ativas os dois são idênticos.
"""

from __future__ import annotations

# `e` é o alias de `empresas` nas consultas que consomem estas expressões. O
# alias curto é o que permite a cascata ser um pedaço de texto em vez de uma
# função SQL com parâmetro.
#
# O último degrau NÃO é `NULL`: uma empresa sem nome nenhum sairia como célula
# vazia no meio de uma lista, e "Empresa 318" pelo menos diz de quem é a linha.
NOME_DA_EMPRESA = """COALESCE(NULLIF(btrim(e.nome_exibicao), ''),
                NULLIF(btrim(e.apelido), ''),
                NULLIF(btrim(e.nome_fantasia), ''),
                NULLIF(btrim(e.razao_social), ''),
                'Empresa ' || e.id_empresa)"""

NOME_NO_CADASTRO = """COALESCE(NULLIF(btrim(e.nome_exibicao), ''),
                NULLIF(btrim(e.apelido), ''),
                NULLIF(btrim(e.razao_social), ''),
                'Empresa ' || e.id_empresa)"""
