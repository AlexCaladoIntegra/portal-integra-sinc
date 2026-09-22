-- ============================================================================
-- fechamento.sql — data de fechamento contábil da empresa (Domínio)
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional `?`). Lido pelo importador,
-- nunca pela aplicação web.
--
-- Semântica de `fechamento_data`, validada contra a tela "Fechamento" do
-- Domínio: é o PRIMEIRO DIA DO ÚLTIMO MÊS FECHADO.
--
--   fechamento_data = 2026-05-01  ->  último mês fechado = 05/2026
--                                     próximo mês em aberto = 06/2026
--
-- A interpretação inversa ("mês em aberto") é o erro natural de quem lê a
-- coluna pela primeira vez, e foi de fato cometida e corrigida no projeto de
-- referência em 2026-07-06. É ela que define o fim da janela YTD da tela.
--
-- Pode vir NULL: empresa sem parametrização de fechamento é caso real.
--
-- Parâmetros posicionais:
--   ?  codi_emp
-- ============================================================================
SELECT fechamento_data
FROM bethadba.ctparmto
WHERE codi_emp = ?
