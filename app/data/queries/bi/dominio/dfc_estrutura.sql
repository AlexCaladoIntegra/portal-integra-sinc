-- ============================================================================
-- dfc_estrutura.sql — as linhas da DFC indireta de uma empresa
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional `?`). Lido pelo importador,
-- nunca pela aplicação web.
--
-- Cada linha do cadastro DECLARA A PRÓPRIA FÓRMULA. Não há regra fixa em
-- código: a estrutura inteira da tela — quantas linhas, em que ordem, com que
-- texto, em qual atividade e apurada de que jeito — vem daqui, por empresa.
--
--   NATUREZA   o que a linha É na tela
--                V  cabeçalho de atividade  ("ATIVIDADES OPERACIONAIS")
--                S  subtotal, ou header de bloco quando TIPO é nulo
--                A  linha de detalhe, que recebe o saldo das contas vinculadas
--
--   TIPO       COMO o valor é apurado
--                L Lancamentos   P Saldo Periodo   D Total Debito
--                C Total credito J Lucro-Prejuizo  A Lucro antes do IR
--                I Inverter natureza do saldo      E Saldo-Exercicio
--                S Sub-Total     T Total           NULO header decorativo
--
-- O dicionário acima não é dedução: está no `remarks` das próprias colunas, no
-- banco do Domínio. O projeto de referência o documenta como "convenção
-- deduzida a partir da observação da UI" e usa apenas dois dos dez valores —
-- ver "O que a paridade custa, medido" em changes/BIC-006.
--
-- `ORDER BY ORDEM` NÃO É COSMÉTICO. A hierarquia da tela é derivada de POSIÇÃO
-- mais NATUREZA (a origem não tem CODIGO_PAI), e o valor de um subtotal é a
-- soma das analíticas da mesma atividade que vêm ANTES dele. Ordem diferente
-- não muda só a aparência: muda o número.
--
-- Só o INDIRETO. `CTGRUPOSDFC_DIRETO` é outra estrutura, com outra tabela de
-- vínculo e sem coluna TIPO, e fica para sprint própria.
--
-- Parâmetros posicionais:  ?  codi_emp
-- ============================================================================
SELECT
  CODI_INDIRETO   AS codigo,
  ORDEM           AS ordem,
  ATIVIDADE       AS atividade,
  NATUREZA        AS tipo_linha,
  TIPO            AS forma_apuracao,
  TRIM(DESCRICAO) AS descricao
FROM bethadba.CTGRUPOSDFC_INDIRETO
WHERE codi_emp = ?
ORDER BY ORDEM
