-- ============================================================================
-- dre_estrutura.sql — estrutura das linhas do DRE da empresa (Domínio)
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional `?`). Lido pelo importador,
-- nunca pela aplicação web.
--
-- Cada empresa tem a sua estrutura em CtGruposDre — não existe uma estrutura
-- global. A hierarquia dos subtotais é dada por CODIGO_PAI (0 = raiz).
--
--   operacao = 1  linha que RECEBE saldo: valor = soma das contas cujo
--                 grdre_efetivo aponta para este código
--   operacao = 2  SUBTOTAL: valor = soma recursiva dos filhos por codigo_pai
--
-- `COALESCE(CODIGO_PAI, 0)` normaliza a raiz: no Domínio ela aparece como NULL
-- em algumas empresas e como 0 em outras, e o motor de cálculo precisa de uma
-- forma só para saber onde a recursão para.
--
-- Parâmetros posicionais:
--   ?  codi_emp
-- ============================================================================
SELECT
  codigo,
  sequencia,
  TRIM(descricao)         AS descricao,
  operacao,
  NIVEL_DE_AGLUTINACAO    AS nivel,
  COALESCE(CODIGO_PAI, 0) AS codigo_pai
FROM bethadba.CtGruposDre
WHERE codi_emp = ?
ORDER BY sequencia
