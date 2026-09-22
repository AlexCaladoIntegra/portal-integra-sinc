-- ============================================================================
-- plano_contas.sql — plano de contas da empresa, com o grupo do DRE resolvido
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional `?`). Lido pelo importador,
-- nunca pela aplicação web.
--
-- Devolve TODAS as contas ativas (sintéticas e analíticas). `grdre_efetivo` é
-- calculado só para as analíticas — são elas que carregam saldo; a sintética é
-- nó intermediário, e a agregação dela é por prefixo de `clas_cta`.
--
-- REGRA DO grdre_efetivo (espelha o comportamento do Domínio, e é o motivo
-- desta query existir em vez de um SELECT simples):
--
--   Percorre a própria conta e as sintéticas ancestrais (por prefixo de
--   `clas_cta`) e escolhe o `grdre_cta` MAIS ESPECÍFICO QUE EXISTA de fato em
--   CtGruposDre daquela empresa. Sem o `EXISTS`, uma conta cujo grupo não foi
--   cadastrado na estrutura DRE "aponta para o vazio" e o valor dela
--   desaparece do demonstrativo — o Domínio, nesse caso, sobe na hierarquia
--   até achar um grupo válido, e é isso que o `ORDER BY LENGTH(...) DESC`
--   reproduz.
--
-- O portal grava `grdre_efetivo` em `bi_conta.grupo_dre`, não o valor cru:
-- replicar esta recursão em cima de um plano de contas que o portal não é dono
-- seria manter a mesma regra em dois lugares.
--
-- E O grdre_proprio, QUE NÃO É A MESMA COISA (BIC-003). São duas colunas, e
-- trocá-las não gera erro:
--
--   grdre_efetivo  o grupo JÁ RESOLVIDO pela herança, só em analítica. É ele
--                  que soma a linha do DRE.
--   grdre_proprio  o `grdre_cta` da PRÓPRIA conta, sem herança nenhuma, em
--                  conta de qualquer tipo. É ele — e só ele — que identifica a
--                  RAIZ da subárvore do drill-down: a sintética que "é" aquela
--                  linha do demonstrativo no Domínio.
--
-- Por que o segundo precisa existir: o `CASE` do efetivo é `tipo_cta = 'A'`,
-- então toda sintética sai com ele nulo — e é justamente na sintética que a
-- classificação mora. Medido em 04/09/2026: das 122.773 sintéticas ativas,
-- 17.164 declaram `grdre_cta` e 12.809 apontam para grupo que existe de fato,
-- contra 5.904 analíticas. Sem esta coluna a subárvore não sabe onde começar e
-- sobe até o topo do plano, arrastando níveis comuns a vários grupos.
--
-- O mesmo `EXISTS` do efetivo se aplica aqui, e pelo mesmo motivo: grupo que
-- não está em CtGruposDre não é raiz de linha nenhuma. Sem ele a coluna
-- guardaria código que não casa com nada, e a contagem de "quantas empresas já
-- têm raiz" — que é o critério de aceite da fase 2 e o alerta do `resumo()` —
-- contaria empresa que na prática continua sem raiz.
--
-- Parâmetros posicionais:
--   ?  codi_emp
-- ============================================================================
SELECT
  c.codi_cta,
  TRIM(c.nome_cta) AS nome_cta,
  TRIM(c.clas_cta) AS clas_cta,
  c.tipo_cta,
  CASE WHEN c.tipo_cta = 'A' THEN (
    SELECT TOP 1 x.grdre_cta
    FROM bethadba.ctcontas x
    WHERE x.codi_emp = c.codi_emp
      AND x.grdre_cta IS NOT NULL
      AND c.clas_cta LIKE x.clas_cta || '%'
      AND EXISTS (
        SELECT 1 FROM bethadba.CtGruposDre dg
        WHERE dg.codi_emp = c.codi_emp AND dg.codigo = x.grdre_cta
      )
    ORDER BY LENGTH(x.clas_cta) DESC
  ) END AS grdre_efetivo,
  CASE WHEN EXISTS (
    SELECT 1 FROM bethadba.CtGruposDre dg
    WHERE dg.codi_emp = c.codi_emp AND dg.codigo = c.grdre_cta
  ) THEN c.grdre_cta END AS grdre_proprio
FROM bethadba.ctcontas c
WHERE c.codi_emp = ?
  AND c.SITUACAO_CTA = 'A'
ORDER BY c.clas_cta
