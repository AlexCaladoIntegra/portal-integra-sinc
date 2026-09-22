-- ============================================================================
-- cfop.sql -- o catalogo de natureza de operacao / CFOP (Dominio)
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional). Lido pelo importador, nunca
-- pela aplicacao web.
--
-- GLOBAL, como `efespecies`: `efnatureza` nao tem `codi_emp`.
--
-- A PK e (codi_nat, versao_nat), e a versao importa: `versao_nat = 2` sao os
-- 701 CFOPs de 4 digitos do SPED/NF-e, e `versao_nat = 1` sao 850 codigos
-- antigos de 3 digitos. Trazer so a versao 2 perderia a nota historica; juntar
-- as duas pelo `codi_nat` sozinho faria o CFOP 510 (antigo) colidir com nada e
-- o 5102 (atual) ficar sem par -- sao numeracoes diferentes.
--
-- `masc_nat` e a mascara do Dominio, que agrupa CFOPs numa arvore (5405 cai sob
-- 5409). E ela que da hierarquia ao filtro de CFOP da tela de graca.
--
-- Sem parametros: sao 1.551 linhas no total.
-- ============================================================================
SELECT codi_nat,
       versao_nat,
       nome_nat,
       masc_nat
FROM bethadba.efnatureza
ORDER BY versao_nat, codi_nat
