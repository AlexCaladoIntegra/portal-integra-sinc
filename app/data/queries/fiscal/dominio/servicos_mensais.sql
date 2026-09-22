-- ============================================================================
-- servicos_mensais.sql -- a nota de SERVICO prestado, por mes (Dominio)
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional). Lido pelo importador, nunca
-- pela aplicacao web.
--
-- E o terceiro fato do modulo, e o brief nao o previa. Sem ele a tela de Saidas
-- nao tem a receita de servico: a NFS-e PRESTADA vive aqui, e a TOMADA entra em
-- `efentradas` como especie de modelo 03.
--
-- TRES AUSENCIAS DA ORIGEM, todas verificadas no catalogo da tabela:
--
-- 1. NAO HA CFOP. `efservicos` nao tem `codi_nat`, e por isso as duas colunas
--    saem NULAS aqui -- e por isso elas sao anulaveis no fato e o indice de CFOP
--    e parcial. Um CFOP zero seria um codigo que nao existe, e alguem faria join
--    interno com o catalogo e perderia a linha inteira.
--
-- 2. NAO HA IMPOSTO. A tabela nao tem coluna de ISS nem de base. O ISS destacado
--    existe em `efmvvpro`, que cobre apenas 35,7% das notas de servico -- por
--    isso o card de ISS da tela NAO sai daqui e sim da apuracao, com a
--    consequencia declarada de nao reagir aos filtros de CFOP, acumulador,
--    cliente e produto. Todas as colunas de imposto saem zeradas, e e deliberado.
--
-- 3. NAO HA ITEM agregavel de forma confiavel, pelo mesmo motivo.
--    `qtd_notas_sem_item` sai como a contagem inteira, para a tela nunca
--    prometer Total por Produto sobre servico.
--
-- `sigl_est` e NOT NULL no cabecalho, como em `efsaidas` -- so a entrada precisa
-- do cadastro do participante para ter UF.
--
-- Parametros posicionais:
--   ?  codi_emp
-- ============================================================================
SELECT v.codi_emp,
       YEAR(v.dser_ser)  AS ano,
       MONTH(v.dser_ser) AS mes,
       v.codi_esp,
       CAST(NULL AS INTEGER)  AS codi_nat,
       CAST(NULL AS SMALLINT) AS versao_nat,
       v.codi_acu,
       v.codi_cli AS participante,
       v.sigl_est AS uf,
       COALESCE(v.situacao_ser, 0) AS situacao,
       COUNT(*) AS qtd_notas,
       SUM(CASE WHEN v.cancelada_ser = 'S' OR v.situacao_ser = 2 THEN 1 ELSE 0 END)
           AS qtd_canceladas,
       COUNT(*) AS qtd_notas_sem_item,
       SUM(COALESCE(v.vcon_ser, 0))           AS valor_contabil,
       SUM(COALESCE(v.VALOR_SERVICOS_SER, 0)) AS valor_produtos,
       CAST(0 AS NUMERIC(18,2)) AS valor_contabil_itens,
       CAST(0 AS NUMERIC(18,2)) AS base_icms,
       CAST(0 AS NUMERIC(18,2)) AS valor_icms,
       CAST(0 AS NUMERIC(18,2)) AS base_icms_st,
       CAST(0 AS NUMERIC(18,2)) AS valor_icms_st,
       CAST(0 AS NUMERIC(18,2)) AS base_ipi,
       CAST(0 AS NUMERIC(18,2)) AS valor_ipi,
       CAST(0 AS NUMERIC(18,2)) AS base_pis,
       CAST(0 AS NUMERIC(18,2)) AS valor_pis,
       CAST(0 AS NUMERIC(18,2)) AS base_cofins,
       CAST(0 AS NUMERIC(18,2)) AS valor_cofins,
       CAST(0 AS NUMERIC(18,2)) AS base_iss,
       CAST(0 AS NUMERIC(18,2)) AS valor_iss
FROM bethadba.efservicos v
WHERE v.codi_emp = ?
GROUP BY v.codi_emp, YEAR(v.dser_ser), MONTH(v.dser_ser), v.codi_esp,
         v.codi_acu, v.codi_cli, v.sigl_est, v.situacao_ser
