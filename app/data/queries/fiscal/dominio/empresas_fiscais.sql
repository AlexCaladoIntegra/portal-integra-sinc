-- ============================================================================
-- empresas_fiscais.sql -- quais empresas tem movimento fiscal, e de que tipo
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional). Lido pelo importador, nunca
-- pela aplicacao web.
--
-- GLOBAL, e e o censo que decide quais empresas ganham linha em
-- `bi_empresa_fiscal` -- a raiz de dependencia do modulo, para a qual as FK dos
-- dois fatos e da apuracao apontam.
--
-- Uma consulta para o parque inteiro em vez de um EXISTS por empresa: medido em
-- 11/09/2026, 2,3 segundos para as tres tabelas. Por empresa seriam tres idas
-- ao driver vezes 620, que e o tipo de laço que `dominio.sessao()` existe para
-- evitar.
--
-- Os tres tipos vem separados porque a tela precisa deles separados: empresa sem
-- saida e sem entrada mas com servico e caso real (medido, a empresa 5), e a
-- tela de Saidas dela nao pode abrir prometendo mercadoria.
--
-- O DISTINCT esta dentro de cada ramo do UNION ALL, e nao fora: assim cada
-- tabela e reduzida ao seu conjunto de empresas antes de as tres se juntarem.
--
-- Sem parametros: e o parque inteiro, por desenho.
-- ============================================================================
SELECT codi_emp,
       MAX(s) AS tem_saida,
       MAX(e) AS tem_entrada,
       MAX(v) AS tem_servico
FROM (
    SELECT DISTINCT codi_emp, 1 AS s, 0 AS e, 0 AS v FROM bethadba.efsaidas
    UNION ALL
    SELECT DISTINCT codi_emp, 0 AS s, 1 AS e, 0 AS v FROM bethadba.efentradas
    UNION ALL
    SELECT DISTINCT codi_emp, 0 AS s, 0 AS e, 1 AS v FROM bethadba.efservicos
) x
GROUP BY codi_emp
ORDER BY codi_emp
