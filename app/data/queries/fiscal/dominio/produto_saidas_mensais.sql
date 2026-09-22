-- ============================================================================
-- produto_saidas_mensais.sql -- o item da nota de SAIDA, por mes (Dominio)
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional). Lido pelo importador, nunca
-- pela aplicacao web.
--
-- Alimenta so o bloco "Total por Produto". Todos os outros blocos da tela saem
-- de `bi_fiscal_nota_mensal`, e por isso este importador e o ULTIMO da lista e a
-- tela funciona inteira sem ele -- a mesma relacao que `contabil_lancamentos`
-- tem com o razao do BI Contabil.
--
-- O GRAO TEM CFOP E ACUMULADOR, e nao so o produto. Sem eles o bloco nao
-- responde aos dois filtros da barra, e ficaria mostrando o ranking do periodo
-- inteiro enquanto o resto da tela mostra o do filtro -- dois numeros certos
-- discordando na mesma tela. Custo medido: 829.975 linhas viram 832.975 em
-- 2025, +0,36%.
--
-- `cfop_msp` e o CFOP DO ITEM, e nao o do cabecalho. Medido no 1T/2026, os dois
-- concordam em 609.343 de 609.473 itens (99,979%) -- e cada tabela carrega a
-- verdade do seu proprio grao em vez de herdar a do vizinho.
--
-- `data_msp` e a competencia, e nao a data do cabecalho: medido em Jan/2026, as
-- duas sao iguais em 184.409 de 184.409 itens. Filtrar pelo item e o que permite
-- ao otimizador podar antes do join.
--
-- TRIM em `codi_pdi` porque a origem e char(14) e devolve o codigo com espaco
-- ("        SM66493"). Sem o TRIM o codigo daqui nunca casa com o de
-- `produtos.sql`, e o ranking sai com o codigo no lugar da descricao.
--
-- LEFT JOIN no cabecalho: o join existe so para buscar o acumulador, e um item
-- cujo cabecalho sumiu na origem nao pode desaparecer do ranking em silencio.
-- Acumulador ausente vira 0, que a tela mostra como nao informado.
--
-- Parametros posicionais:
--   ?  codi_emp
-- ============================================================================
SELECT m.codi_emp,
       YEAR(m.data_msp)  AS ano,
       MONTH(m.data_msp) AS mes,
       TRIM(m.codi_pdi)  AS codi_pdi,
       m.cfop_msp        AS codi_nat,
       COALESCE(s.codi_acu, 0) AS codi_acu,
       COUNT(*)                         AS qtd_itens,
       SUM(COALESCE(m.qtde_msp, 0))     AS quantidade,
       SUM(COALESCE(m.vpro_msp, 0))     AS valor_produtos,
       SUM(COALESCE(m.VALOR_CONTABIL_MSP, 0)) AS valor_contabil,
       SUM(COALESCE(m.bicms_msp, 0))          AS base_icms,
       SUM(COALESCE(m.valor_icms_msp, 0))     AS valor_icms,
       SUM(COALESCE(m.valor_subtri_msp, 0))   AS valor_icms_st,
       SUM(COALESCE(m.vipi_msp, 0))           AS valor_ipi,
       SUM(COALESCE(m.valor_pis_msp, 0))      AS valor_pis,
       SUM(COALESCE(m.valor_cofins_msp, 0))   AS valor_cofins
FROM bethadba.efmvspro m
LEFT JOIN bethadba.efsaidas s
       ON s.codi_emp = m.codi_emp AND s.codi_sai = m.codi_sai
WHERE m.codi_emp = ?
GROUP BY m.codi_emp, YEAR(m.data_msp), MONTH(m.data_msp), TRIM(m.codi_pdi),
         m.cfop_msp, COALESCE(s.codi_acu, 0)
