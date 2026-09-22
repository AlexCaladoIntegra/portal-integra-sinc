-- ============================================================================
-- entradas_mensais.sql -- o cabecalho da nota de ENTRADA, por mes (Dominio)
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional). Lido pelo importador, nunca
-- pela aplicacao web.
--
-- Gemea de `saidas_mensais.sql`, e as tres regras de la valem identicas: a
-- derivada agrega ao grao da NOTA antes do join, o join e LEFT e o filtro do
-- item vive dentro da derivada. Leia aquele cabecalho antes de mexer neste.
--
-- DUAS DIFERENCAS DE ORIGEM, e as duas viram limite declarado na tela:
--
-- 1. `efentradas` NAO TEM coluna de cancelamento. As saidas tem duas marcas
--    independentes (`cancelada_sai` e `situacao_sai`); aqui existe so
--    `situacao_ent`. `qtd_canceladas` sai so dela, e a tela nao pode dizer
--    "0 notas de entrada canceladas" -- tem de dizer que a origem nao informa.
--    Zero por ausencia de dado e zero por ausencia de fato se leem igual.
--
-- 2. `efentradas` NAO TEM UF no cabecalho, ao contrario de `efsaidas` e de
--    `efservicos`, que trazem `sigl_est` NOT NULL. A UF da entrada vem do
--    cadastro do fornecedor, e por isso este SELECT junta `effornece` -- medido
--    em 2025, zero orfaos nesse join. Sem ele o mapa de Entradas fica vazio.
--
-- E ha duas datas absurdas em `dent_ent` (ano 7024 e ano 2120, uma nota cada).
-- Elas entram como estao: o portal e espelho da origem, e descartar faria o
-- total do portal discordar do Dominio sem ninguem conseguir explicar. Quem
-- filtra ano implausivel e o `resumo()` do importador, para nao anunciar
-- "ultima competencia 08/7024".
--
-- Parametros posicionais:
--   ?  codi_emp   (na derivada dos itens)
--   ?  codi_emp   (no cabecalho)
-- ============================================================================
SELECT e.codi_emp,
       YEAR(e.dent_ent)  AS ano,
       MONTH(e.dent_ent) AS mes,
       e.codi_esp,
       e.codi_nat,
       e.versao_nat,
       e.codi_acu,
       e.codi_for AS participante,
       f.sigl_est AS uf,
       e.situacao_ent AS situacao,
       COUNT(*) AS qtd_notas,
       SUM(CASE WHEN e.situacao_ent = 2 THEN 1 ELSE 0 END) AS qtd_canceladas,
       SUM(CASE WHEN i.codi_ent IS NULL THEN 1 ELSE 0 END) AS qtd_notas_sem_item,
       SUM(e.vcon_ent)  AS valor_contabil,
       SUM(e.vprod_ent) AS valor_produtos,
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
       CAST(0 AS NUMERIC(18,2))      AS base_iss,
       CAST(0 AS NUMERIC(18,2))      AS valor_iss
FROM bethadba.efentradas e
LEFT JOIN bethadba.effornece f
       ON f.codi_emp = e.codi_emp AND f.codi_for = e.codi_for
LEFT JOIN (
    SELECT m.codi_emp,
           m.codi_ent,
           SUM(m.VALOR_CONTABIL_MEP) AS vcontabil,
           SUM(m.bicms_mep)          AS bicms,
           SUM(m.valor_icms_mep)     AS icms,
           SUM(m.bicmsst_mep)        AS bicmsst,
           SUM(m.valor_subtri_mep)   AS icmsst,
           SUM(m.bcal_mep)           AS bipi,
           SUM(m.vipi_mep)           AS ipi,
           SUM(m.bc_pis_mep)         AS bpis,
           SUM(m.valor_pis_mep)      AS pis,
           SUM(m.bc_cofins_mep)      AS bcofins,
           SUM(m.valor_cofins_mep)   AS cofins
    FROM bethadba.efmvepro m
    WHERE m.codi_emp = ?
    GROUP BY m.codi_emp, m.codi_ent
) i ON i.codi_emp = e.codi_emp AND i.codi_ent = e.codi_ent
WHERE e.codi_emp = ?
GROUP BY e.codi_emp, YEAR(e.dent_ent), MONTH(e.dent_ent), e.codi_esp,
         e.codi_nat, e.versao_nat, e.codi_acu, e.codi_for, f.sigl_est,
         e.situacao_ent
