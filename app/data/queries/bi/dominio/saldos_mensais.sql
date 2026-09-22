-- ============================================================================
-- saldos_mensais.sql — movimento mensal por conta, nas DUAS medidas (Domínio)
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional `?`). Lido pelo importador,
-- nunca pela aplicação web.
--
-- É a query central do módulo: agrega `ctlancto` em um valor por
-- (conta, ano, mês) e devolve as duas medidas de que as duas demonstrações
-- precisam. Convenção de sinal do Domínio e do portal:
--
--   saldo credor = SUM(créditos) - SUM(débitos)
--
-- Logo conta de ativo tem saldo NEGATIVO, e passivo/PL positivo.
--
-- AS TRÊS MEDIDAS, e por que nenhuma delas é redundância:
--
--   movimento_total  o LIQUIDO de todos os lançamentos. Base do BALANÇO.
--                    Acumulada desde o início da operação, dá o saldo da conta
--                    numa data.
--   movimento        o líquido só de `orig_lan <> 2`, ou seja SEM os
--                    lançamentos de encerramento. Base do DRE, que quer
--                    movimentação econômica — com o encerramento dentro,
--                    mediria o zeramento em vez da operação.
--   debito_total     as duas PERNAS separadas, do lado total (com
--   credito_total    encerramento). Base do BALANCETE, que não pergunta
--                    "quanto sobrou no mês" e sim "quanto entrou e quanto
--                    saiu" — e `credito - debito` não se desfaz nas duas
--                    parcelas depois de somado.
--
-- Trocar as duas primeiras de lugar não gera erro nenhum: o DRE passa a
-- mostrar zeros no ano encerrado e o Balanço deixa de fechar. Conferido contra
-- a empresa 272 em 31/05/2026: Ativo 2.941.870,81 = Passivo+PL 2.912.432,91 +
-- Resultado 29.437,90, ao centavo, acumulando `movimento_total`.
--
-- O par novo (BIC-004) sai do lado TOTAL de propósito, e a invariante que o
-- prende ao antigo é exata, porque é NUMERIC:
--
--     movimento_total = credito_total - debito_total
--
-- Sem recorte de data de propósito: o Balanço precisa da acumulação desde o
-- primeiro lançamento da empresa, então não há o que recortar. O custo é baixo
-- porque a agregação acontece aqui — 553 mil lançamentos da empresa 272 saem
-- como 3.338 linhas em ~240 ms.
--
-- O UNION ALL existe porque cada lançamento tem duas pernas em colunas
-- diferentes (`cdeb_lan` e `ccre_lan`): não há como somar as duas num GROUP BY
-- só. A ordem das colunas tem de ser idêntica nos dois ramos.
--
-- Parâmetros posicionais (o codi_emp entra duas vezes, um por ramo):
--   ?  codi_emp
--   ?  codi_emp
-- ============================================================================
SELECT codi_cta, ano, mes,
       SUM(cre_total) - SUM(deb_total) AS movimento_total,
       SUM(cre_op)    - SUM(deb_op)    AS movimento,
       SUM(deb_total)                  AS debito_total,
       SUM(cre_total)                  AS credito_total
FROM (
  SELECT cdeb_lan AS codi_cta,
         YEAR(data_lan) AS ano, MONTH(data_lan) AS mes,
         SUM(vlor_lan) AS deb_total,
         SUM(CASE WHEN orig_lan <> 2 THEN vlor_lan ELSE 0 END) AS deb_op,
         CAST(0 AS NUMERIC(18,2)) AS cre_total,
         CAST(0 AS NUMERIC(18,2)) AS cre_op
  FROM bethadba.ctlancto
  WHERE codi_emp = ?
  GROUP BY cdeb_lan, YEAR(data_lan), MONTH(data_lan)
  UNION ALL
  SELECT ccre_lan AS codi_cta,
         YEAR(data_lan) AS ano, MONTH(data_lan) AS mes,
         CAST(0 AS NUMERIC(18,2)) AS deb_total,
         CAST(0 AS NUMERIC(18,2)) AS deb_op,
         SUM(vlor_lan) AS cre_total,
         SUM(CASE WHEN orig_lan <> 2 THEN vlor_lan ELSE 0 END) AS cre_op
  FROM bethadba.ctlancto
  WHERE codi_emp = ?
  GROUP BY ccre_lan, YEAR(data_lan), MONTH(data_lan)
) AS mov
GROUP BY codi_cta, ano, mes
ORDER BY codi_cta, ano, mes
