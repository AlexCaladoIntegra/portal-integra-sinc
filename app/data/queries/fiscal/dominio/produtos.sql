-- ============================================================================
-- produtos.sql -- o produto referenciado por algum item de nota (Dominio)
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional). Lido pelo importador, nunca
-- pela aplicacao web.
--
-- POR EMPRESA: `efprodutos` tem PK (codi_pdi, codi_emp), e o mesmo codigo em
-- duas empresas e outro produto. Medido no grupo da matriz 705: so 54,3% dos
-- codigos usados na filial existem na matriz, e desses apenas 35,9% tem a mesma
-- descricao. E por isso que o ranking consolidado NAO funde produto entre
-- membros -- fundir acertaria o varejo e erraria o agro, invisivelmente.
--
-- Traz so o produto REFERENCIADO: 1.615.251 pares contra 2.830.476 cadastrados,
-- medido -- corta 43% e, com `desc_pdi`, e a maior transferencia do modulo
-- inteiro. E uma DIMENSAO, e nao um fato: e a razao de este importador ler e
-- gravar empresa a empresa, com `fetchmany`, em vez de acumular tudo em memoria
-- como faz o de saldos contabeis.
--
-- `codi_pdi` e char(14) na origem, nao inteiro: ha codigo de produto com letra.
--
-- Parametros posicionais:
--   ?  codi_emp
-- ============================================================================
SELECT p.codi_pdi,
       p.desc_pdi,
       p.cncm_pdi
FROM bethadba.efprodutos p
WHERE p.codi_emp = ?
  AND (EXISTS (SELECT 1 FROM bethadba.efmvspro i
                WHERE i.codi_emp = p.codi_emp AND i.codi_pdi = p.codi_pdi)
    OR EXISTS (SELECT 1 FROM bethadba.efmvepro e
                WHERE e.codi_emp = p.codi_emp AND e.codi_pdi = p.codi_pdi))
ORDER BY p.codi_pdi
