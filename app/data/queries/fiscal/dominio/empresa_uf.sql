-- ============================================================================
-- empresa_uf.sql -- a UF de uma empresa, no cadastro do Dominio
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional). Lido pelo importador, nunca
-- pela aplicacao web.
--
-- Existe por causa de UMA coisa: decidir se `apuracao_extemporaneo.sql` roda
-- para esta empresa, e contra qual tabela. O portal NAO guarda a UF da empresa
-- -- `empresas` tem razao social, fantasia, apelido e CNPJ, e `bi_empresa_fiscal`
-- guarda competencias. A UF que o portal tem e a da NOTA
-- (`bi_fiscal_nota_mensal.uf`), que e outra coisa: e o destino ou a origem do
-- documento, nao o domicilio do contribuinte.
--
-- Consulta de UMA linha por empresa, chamada no maximo 20 vezes por execucao
-- (o teto do importador). Nao vale coluna nova no portal enquanto for so isto:
-- uma coluna de UF em `empresas` precisaria de migration, de importador e de
-- reimportacao para responder a uma pergunta que o proprio importador ja esta
-- em posicao de fazer.
--
-- Parametros posicionais:
--   ?  codi_emp
-- ============================================================================
SELECT e.esta_emp AS uf
FROM bethadba.geempre e
WHERE e.codi_emp = ?
