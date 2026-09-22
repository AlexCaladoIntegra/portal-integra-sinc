-- ============================================================================
-- fornecedores.sql -- o fornecedor referenciado por entrada (Dominio)
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional). Lido pelo importador, nunca
-- pela aplicacao web.
--
-- POR EMPRESA, como `clientes.sql`, e com a mesma armadilha: `codi_for` e um
-- contador sequencial por empresa. Medido: `effornece` tem 324.430 linhas para
-- 91.697 CNPJs distintos -- 3,5x duplicado, porque o cadastro se repete em cada
-- empresa que compra do mesmo fornecedor.
--
-- `sigl_est` daqui e a UNICA fonte de UF das ENTRADAS: `efentradas` nao tem
-- coluna de estado no cabecalho, ao contrario de `efsaidas`. Medido em 2025,
-- zero orfaos neste join.
--
-- Parametros posicionais:
--   ?  codi_emp
-- ============================================================================
SELECT f.codi_for,
       f.nome_for,
       f.cgce_for,
       f.sigl_est
FROM bethadba.effornece f
WHERE f.codi_emp = ?
  AND EXISTS (SELECT 1 FROM bethadba.efentradas e
               WHERE e.codi_emp = f.codi_emp AND e.codi_for = f.codi_for)
ORDER BY f.codi_for
