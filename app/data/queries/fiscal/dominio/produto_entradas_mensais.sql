-- ============================================================================
-- produto_entradas_mensais.sql -- o item da nota de ENTRADA, por mes (Dominio)
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional). Lido pelo importador, nunca
-- pela aplicacao web.
--
-- Gemea de `produto_saidas_mensais.sql`: as cinco regras daquele cabecalho
-- valem identicas aqui, com os nomes de coluna no sufixo `_mep`.
--
-- O LADO QUE MENOS COLAPSA DO MODULO INTEIRO. Medido: 4.652.037 itens de
-- entrada viram 2.360.661 linhas -- 1,97x, contra 5,20x da saida. A entrada
-- custa 71% do tamanho da saida para 27% dos itens, porque nota de compra tem
-- poucos itens repetidos e cada fornecedor traz produto proprio.
--
-- E por isso que a caixa "incluir entradas" existe no importador: quem quiser
-- so "o que eu mais vendo" nao paga por "o que eu mais compro".
--
-- Nao ha ISS na entrada, como nao ha na saida: `valor_issqn_msp` soma R$ 0,00
-- no parque. As colunas de ISS nao existem neste grao de proposito.
--
-- Parametros posicionais:
--   ?  codi_emp
-- ============================================================================
SELECT m.codi_emp,
       YEAR(m.data_mep)  AS ano,
       MONTH(m.data_mep) AS mes,
       TRIM(m.codi_pdi)  AS codi_pdi,
       m.cfop_mep        AS codi_nat,
       COALESCE(e.codi_acu, 0) AS codi_acu,
       COUNT(*)                         AS qtd_itens,
       SUM(COALESCE(m.qtde_mep, 0))     AS quantidade,
       SUM(COALESCE(m.vpro_mep, 0))     AS valor_produtos,
       SUM(COALESCE(m.VALOR_CONTABIL_MEP, 0)) AS valor_contabil,
       SUM(COALESCE(m.bicms_mep, 0))          AS base_icms,
       SUM(COALESCE(m.valor_icms_mep, 0))     AS valor_icms,
       SUM(COALESCE(m.valor_subtri_mep, 0))   AS valor_icms_st,
       SUM(COALESCE(m.vipi_mep, 0))           AS valor_ipi,
       SUM(COALESCE(m.valor_pis_mep, 0))      AS valor_pis,
       SUM(COALESCE(m.valor_cofins_mep, 0))   AS valor_cofins
FROM bethadba.efmvepro m
LEFT JOIN bethadba.efentradas e
       ON e.codi_emp = m.codi_emp AND e.codi_ent = m.codi_ent
WHERE m.codi_emp = ?
GROUP BY m.codi_emp, YEAR(m.data_mep), MONTH(m.data_mep), TRIM(m.codi_pdi),
         m.cfop_mep, COALESCE(e.codi_acu, 0)
