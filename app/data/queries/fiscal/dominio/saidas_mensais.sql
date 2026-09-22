-- ============================================================================
-- saidas_mensais.sql -- o cabecalho da nota de SAIDA, agregado por mes (Dominio)
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional). Lido pelo importador, nunca
-- pela aplicacao web.
--
-- E a consulta central do modulo. Agrega `efsaidas` por
-- (ano, mes, especie, CFOP, acumulador, cliente, UF, situacao) e traz junto os
-- impostos, que vivem no ITEM e nao no cabecalho.
--
-- Sem recorte de data, de proposito: a agregacao acontece aqui e o historico
-- completo do parque cabe em 1,68 milhao de linhas. Recortar economizaria pouco
-- e quebraria a comparacao ano a ano, que e o uso principal da tela. Mesma
-- decisao de `bi/dominio/saldos_mensais.sql`.
--
-- TRES REGRAS QUE CUSTARAM CARO, e nenhuma e estilo:
--
-- 1. A derivada agrega ao grao da NOTA ANTES do join. Os impostos estao no item
--    e `qtd_notas` esta no cabecalho: um join direto multiplicaria a contagem
--    pelo numero de itens da nota, e o KPI "Qtd. Entradas/Saidas" sairia varias
--    vezes maior sem nada indicando isso.
--
-- 2. LEFT JOIN, nunca interno. Medido no 1T/2026: 23.411 de 284.443 notas
--    (8,23%) nao tem item nenhum -- o CT-e nao tem em 100% dos casos, e a NF-e
--    em 9,9%. Com join interno somem R$ 101,2 milhoes por trimestre, e a tela
--    nao erra: ela mostra MENOS, que e pior, porque nada acusa. E o mesmo
--    defeito que `bi/dfc_saldos.sql` corrigiu na empresa 272.
--
-- 3. O filtro do item vive DENTRO da derivada. Movido para o WHERE de fora ele
--    anula o LEFT JOIN em silencio, porque a nota sem item tem as colunas de
--    `i` nulas e qualquer condicao sobre elas a descarta.
--
-- AS DUAS MEDIDAS DE VALOR, e por que nenhuma e redundancia:
--
--   valor_contabil        `vcon_sai`, do CABECALHO. E o KPI "Valor Total".
--   valor_contabil_itens  a soma de `VALOR_CONTABIL_MSP` dos itens. E o teto do
--                         bloco "Total por Produto".
--
-- As duas discordam em 4,54% por causa da regra 2, e e a diferenca entre elas
-- -- junto com `qtd_notas_sem_item` -- que permite a tela MEDIR o desencontro
-- em vez de afirmar. Sem o par, ninguem consegue explicar por que o Total por
-- Produto soma menos que o Valor Total da mesma tela.
--
-- O CANCELAMENTO TEM DUAS MARCAS, e uma sozinha nao basta: medido no bienio
-- 2025-2026, 4.899 notas tem `cancelada_sai = 'N'` com `situacao_sai = 2`.
-- O valor esta a salvo (cancelada vale R$ 0,00 em 100% das 21.026), mas
-- `qtd_notas` contaria 21 mil notas que nao existem. Por isso `situacao` entra
-- no GRAO e `qtd_canceladas` e medida a parte.
--
-- `compte_sai` NAO aparece aqui e nao pode aparecer: e lixo, com ano 1900 em
-- 6,12 milhoes das 6,15 milhoes de linhas. A competencia e `dsai_sai`.
--
-- Parametros posicionais:
--   ?  codi_emp   (na derivada dos itens)
--   ?  codi_emp   (no cabecalho)
-- ============================================================================
SELECT s.codi_emp,
       YEAR(s.dsai_sai)  AS ano,
       MONTH(s.dsai_sai) AS mes,
       s.codi_esp,
       s.codi_nat,
       s.versao_nat,
       s.codi_acu,
       s.codi_cli AS participante,
       s.sigl_est AS uf,
       s.situacao_sai AS situacao,
       COUNT(*) AS qtd_notas,
       SUM(CASE WHEN s.cancelada_sai = 'S' OR s.situacao_sai = 2 THEN 1 ELSE 0 END)
           AS qtd_canceladas,
       SUM(CASE WHEN i.codi_sai IS NULL THEN 1 ELSE 0 END) AS qtd_notas_sem_item,
       SUM(s.vcon_sai)  AS valor_contabil,
       SUM(s.vprod_sai) AS valor_produtos,
       SUM(COALESCE(i.vcontabil, 0)) AS valor_contabil_itens,
       SUM(COALESCE(i.bicms, 0))     AS base_icms,
       SUM(COALESCE(i.icms, 0))      AS valor_icms,
       SUM(COALESCE(i.bicmsst, 0))   AS base_icms_st,
       SUM(COALESCE(i.icmsst, 0))    AS valor_icms_st,
       SUM(COALESCE(i.bipi, 0))      AS base_ipi,
       SUM(COALESCE(i.ipi, 0))       AS valor_ipi,
       SUM(COALESCE(i.bpis, 0))      AS base_pis,
       SUM(COALESCE(i.pis, 0))       AS valor_pis,
       SUM(COALESCE(i.bcofins, 0))   AS base_cofins,
       SUM(COALESCE(i.cofins, 0))    AS valor_cofins,
       SUM(COALESCE(i.biss, 0))      AS base_iss,
       SUM(COALESCE(i.iss, 0))       AS valor_iss
FROM bethadba.efsaidas s
LEFT JOIN (
    SELECT m.codi_emp,
           m.codi_sai,
           SUM(m.VALOR_CONTABIL_MSP) AS vcontabil,
           SUM(m.bicms_msp)          AS bicms,
           SUM(m.valor_icms_msp)     AS icms,
           SUM(m.bicmsst_msp)        AS bicmsst,
           SUM(m.valor_subtri_msp)   AS icmsst,
           SUM(m.bcal_msp)           AS bipi,
           SUM(m.vipi_msp)           AS ipi,
           SUM(m.bc_pis_msp)         AS bpis,
           SUM(m.valor_pis_msp)      AS pis,
           SUM(m.bc_cofins_msp)      AS bcofins,
           SUM(m.valor_cofins_msp)   AS cofins,
           SUM(m.bc_issqn_msp)       AS biss,
           SUM(m.valor_issqn_msp)    AS iss
    FROM bethadba.efmvspro m
    WHERE m.codi_emp = ?
    GROUP BY m.codi_emp, m.codi_sai
) i ON i.codi_emp = s.codi_emp AND i.codi_sai = s.codi_sai
WHERE s.codi_emp = ?
GROUP BY s.codi_emp, YEAR(s.dsai_sai), MONTH(s.dsai_sai), s.codi_esp,
         s.codi_nat, s.versao_nat, s.codi_acu, s.codi_cli, s.sigl_est,
         s.situacao_sai
