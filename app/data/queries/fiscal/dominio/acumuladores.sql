-- ============================================================================
-- acumuladores.sql -- o acumulador usado pela empresa (Dominio)
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional). Lido pelo importador, nunca
-- pela aplicacao web.
--
-- POR EMPRESA. `efacumulador` tem PK (CODI_EMP, CODI_ACU), entao "acumulador 3"
-- de duas empresas sao coisas diferentes. Isto e o que obriga toda query
-- consolidada do modulo a ligar a dimensao pelo MEMBRO -- ver o cabecalho da
-- migration `0015_bi_fiscal`.
--
-- Traz so o acumulador REFERENCIADO por algum movimento, e nao o cadastro
-- inteiro: medido em 11/09/2026, sao 12.695 pares (empresa, acumulador) usados
-- contra 63.230 cadastrados -- 80% do cadastro nunca aparece numa nota.
--
-- Os tres EXISTS existem porque `codi_acu` esta nos TRES cabecalhos (saida,
-- entrada e servico), e um acumulador usado so em entrada nao pode sumir.
--
-- Parametros posicionais:
--   ?  codi_emp
-- ============================================================================
SELECT a.CODI_ACU,
       a.NOME_ACU
FROM bethadba.efacumulador a
WHERE a.CODI_EMP = ?
  AND (EXISTS (SELECT 1 FROM bethadba.efsaidas s
                WHERE s.codi_emp = a.CODI_EMP AND s.codi_acu = a.CODI_ACU)
    OR EXISTS (SELECT 1 FROM bethadba.efentradas e
                WHERE e.codi_emp = a.CODI_EMP AND e.codi_acu = a.CODI_ACU)
    OR EXISTS (SELECT 1 FROM bethadba.efservicos v
                WHERE v.codi_emp = a.CODI_EMP AND v.codi_acu = a.CODI_ACU))
ORDER BY a.CODI_ACU
