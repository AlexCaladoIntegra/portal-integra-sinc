-- ============================================================================
-- dfc_contas.sql — quais contas somam em cada linha da DFC indireta
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional `?`). Lido pelo importador,
-- nunca pela aplicação web.
--
-- O JOIN é `LEFT`, e a diferença importa. O projeto de referência usa JOIN
-- interno com `ct.SITUACAO_CTA = 'A'` e nunca fica sabendo o que descartou. Com
-- `LEFT`, o vínculo que aponta para conta inativa — ou para conta que não
-- existe — chega aqui e o importador o CONTA antes de descartar.
--
-- O descarte em si é PARIDADE, não perda: `bi_conta` só guarda conta ativa
-- (`plano_contas.sql` filtra `SITUACAO_CTA = 'A'`), e o projeto de referência
-- descarta exatamente as mesmas. Medido em 09/09/2026, nos 53.210 vínculos da
-- origem: 1.816 apontam para conta INATIVA, 452 para conta SINTÉTICA e ZERO
-- para conta inexistente. Contar e avisar é o que separa "esta empresa não tem
-- essa conta" de "o plano do portal está velho".
--
-- `tipo_cta` VEM JUNTO porque a sintética vinculada é gravada e descartada no
-- CÁLCULO, não na importação: somá-la duplicaria o valor, já que a agregação da
-- linha percorre as analíticas. Gravar preserva o que a origem declara e mantém
-- o descarte visível numa contagem.
--
-- O JOIN casa por `cc.codi_emp` e não por `cc.CODI_EMP_PLANO`, reproduzindo o
-- projeto de referência. As duas colunas são iguais em 100% das linhas — zero
-- diferenças nos 53.209 vínculos —, então a escolha não muda número nenhum
-- hoje; casar pela mesma coluna que ele casa é o que mantém a paridade se um
-- dia divergirem.
--
-- Parâmetros posicionais:  ?  codi_emp
-- ============================================================================
SELECT
  cc.CODI_INDIRETO AS codigo,
  cc.CODI_CTA      AS codi_cta,
  ct.tipo_cta      AS tipo_cta,
  ct.SITUACAO_CTA  AS situacao_cta
FROM bethadba.CTGRUPOSDFC_INDIRETO_CONTAS cc
LEFT JOIN bethadba.ctcontas ct
  ON ct.codi_emp = cc.codi_emp
 AND ct.codi_cta = cc.CODI_CTA
WHERE cc.codi_emp = ?
ORDER BY cc.CODI_INDIRETO, cc.CODI_CTA
