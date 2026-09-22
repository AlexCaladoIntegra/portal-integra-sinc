-- ============================================================================
-- clientes.sql -- o cliente referenciado por saida ou servico (Dominio)
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional). Lido pelo importador, nunca
-- pela aplicacao web.
--
-- POR EMPRESA, e e a dimensao mais perigosa do modulo. `codi_cli` e um contador
-- SEQUENCIAL por empresa: medido no grupo matriz/filiais da empresa 480, 100%
-- dos 7.455 codigos de cliente da filial existem tambem na matriz e ZERO deles
-- e a mesma pessoa. Uma query consolidada que fixasse o representante juntaria
-- os valores da filial com os NOMES dos clientes da matriz.
--
-- `cgce_cli` e o que o ranking consolidado agrupa, nunca o codigo: o mesmo
-- cliente que compra de 8 filiais tem 8 codigos e apareceria 8 vezes num Top-10.
-- Medido: `efclientes` tem 728.632 linhas para 298.975 CNPJs distintos.
--
-- `sigl_est` daqui NAO e a fonte de UF de tela nenhuma: `efsaidas` e
-- `efservicos` trazem `sigl_est` NOT NULL no proprio cabecalho, e e de la que o
-- fato tira a UF do mapa. So a ENTRADA depende do cadastro do participante, e
-- la quem serve e `effornece`. A coluna vem junto assim mesmo porque e o
-- cadastro do cliente, e mante-la alinhada com `fornecedores.sql` e o que
-- permite os dois virarem uma tabela so no portal.
--
-- Parametros posicionais:
--   ?  codi_emp
-- ============================================================================
SELECT c.codi_cli,
       c.nome_cli,
       c.cgce_cli,
       c.sigl_est
FROM bethadba.efclientes c
WHERE c.codi_emp = ?
  AND (EXISTS (SELECT 1 FROM bethadba.efsaidas s
                WHERE s.codi_emp = c.codi_emp AND s.codi_cli = c.codi_cli)
    OR EXISTS (SELECT 1 FROM bethadba.efservicos v
                WHERE v.codi_emp = c.codi_emp AND v.codi_cli = c.codi_cli))
ORDER BY c.codi_cli
