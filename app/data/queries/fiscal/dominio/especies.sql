-- ============================================================================
-- especies.sql -- o catalogo de especie de documento fiscal (Dominio)
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional). Lido pelo importador, nunca
-- pela aplicacao web.
--
-- GLOBAL: `efespecies` nao tem `codi_emp`. E uma das duas dimensoes de topo do
-- BI Fiscal que atravessam a consolidacao sem assimetria nenhuma -- do lado
-- contabil o eixo principal (o plano de contas) e por empresa, e e dai que
-- nasce toda a assimetria representante/membros do `Escopo`.
--
-- `codigo_modelo` e a coluna que a tela agrupa, e `nome_esp` so serve de
-- detalhe. A mesma especie tem varios codigos: NF-e tem 4 (36, 39, 50, 51),
-- NFC-e tem 3 (46, 49, 53), CT-e tem 3 e NFS-e tem SEIS. Agrupar por `codi_esp`
-- parte o NFC-e em tres fatias e o grafico de especie mente -- ver o cabecalho
-- de `app/fiscal/modelos.py`.
--
-- Sem parametros: sao 60 linhas no total.
-- ============================================================================
SELECT codi_esp,
       nome_esp,
       codigo_modelo
FROM bethadba.efespecies
ORDER BY codi_esp
