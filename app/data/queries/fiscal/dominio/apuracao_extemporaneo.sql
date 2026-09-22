-- ============================================================================
-- apuracao_extemporaneo.sql -- o ICMS de documentos extemporaneos, POR UF
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional). Lido pelo importador, nunca
-- pela aplicacao web.
--
-- E A UNICA CONSULTA DE ORIGEM DO MODULO COM NOME DE TABELA VARIAVEL, e a razao
-- e da origem: "ICMS documentos Extemporaneos a recolher" nao existe em
-- `efsdoimp`. Ele vive em `EFSDOIMP_ESTADUAL_<UF>`, uma tabela por estado -- e
-- **nem todas tem a coluna**.
--
-- Medido em 18/09/2026: das onze UFs do parque, so MS e PE tem
-- `ICMS_DOCUMENTOS_EXTEMPORANEOS_RECOLHER`. SP, MT, PA, SC, MG, PR, CE, TO e RS
-- nao tem a coluna -- nao e que ela esteja vazia, e que a tabela daquele estado
-- nao a declara, e a consulta morreria com erro de coluna inexistente.
--
-- Quem decide se esta consulta roda e `TABELA_DE_EXTEMPORANEO_POR_UF`, no
-- importador. Empresa de UF fora do mapa nao recebe a consulta, e a coluna do
-- portal fica NULL -- que ali significa "a origem NAO INFORMA extemporaneo para
-- esta linha", e nao zero.
--
-- A frase e larga de proposito, porque a falta tem TRES causas: a UF sem a
-- coluna, a linha que nao e de ICMS, e a linha de ICMS sem extensao estadual.
--
-- A SEGUNDA e a mais comum e a menos obvia: esta tabela tem uma linha por linha
-- de ICMS, e nao por linha de apuracao. Medido na 510, de 432 linhas de
-- `efsdoimp` so 31 tem extensao em MS -- e sao 31 das 32 de ICMS. PIS, COFINS,
-- IRPJ e as retencoes nao tem extensao nenhuma, e e assim que tem de ser:
-- extemporaneo e conceito de ICMS. No parque reimportado, 20.309 das 145.667
-- linhas recebem valor.
--
-- Zerar em qualquer dos tres casos afirmaria que nao ha documento extemporaneo a
-- recolher -- sobre um imposto que nao tem esse conceito, ou sobre um estado
-- onde ninguem sabe.
--
-- O NOME DA TABELA ENTRA POR FORMATACAO, e nao por parametro: identificador nao
-- aceita placeholder em dialeto nenhum. O valor vem do mapa do importador, que e
-- constante do codigo -- nunca de entrada. E a mesma excecao que
-- `app/agrupamentos/repositories.py` abre para o eixo.
--
-- O VALOR E RARISSIMO, e vale saber antes de investir nele: em 25.784 linhas de
-- MS, o historico inteiro, UMA tem extemporaneo diferente de zero. A coluna
-- existe pela paridade com o relatorio -- ele mostra a linha, e uma tela que a
-- escondesse divergiria do documento que o cliente confere.
--
-- Parametros posicionais:
--   ?  codi_emp
-- ============================================================================
SELECT x.codi_imp,
       x.data_sim AS competencia,
       x.pdic_sim AS periodicidade,
       x.ICMS_DOCUMENTOS_EXTEMPORANEOS_RECOLHER AS extemporaneo_recolher
FROM bethadba.{tabela} x
WHERE x.codi_emp = ?
ORDER BY x.codi_imp, x.data_sim, x.pdic_sim
