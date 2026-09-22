-- ============================================================================
-- impostos.sql -- o imposto que a empresa apura, e o nome dele (Dominio)
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional). Lido pelo importador, nunca
-- pela aplicacao web.
--
-- Quem MANDA e `efsdoimp`, e nao o cadastro: a consulta parte da apuracao e
-- busca o nome com LEFT JOIN. Fosse ao contrario, um imposto com guia lancada e
-- sem vigencia cadastrada em `EFIMPOSTO` sumiria da Arvore de Impostos levando
-- o valor junto -- e a Arvore deixaria de somar o total, sem erro nenhum.
--
-- `EFIMPOSTO` e uma VIEW sobre GEIMPOSTO + GEIMPOSTO_VIGENCIA que ja resolve a
-- ultima vigencia e ja filtra o sistema fiscal (CODI_SIS = 5). Usa-la evita
-- repetir aqui a regra de qual vigencia vale.
--
-- O MAX no nome e deliberado: a view devolve uma linha por vigencia vigente e,
-- havendo empate, o nome e escolhido de forma estavel em vez de multiplicar a
-- linha do imposto. O nome cru vai para `bi_fiscal_imposto.nome`, e o
-- `nome_canonico` sai de `app/fiscal/impostos.py`, que e PURO -- medido, o
-- codi_imp 1 (o ICMS) tem variantes de nome que vao de "3" a "ICMS NORMAL", e
-- nenhuma normalizacao de string recupera "ICMS" a partir de "3".
--
-- Parametros posicionais:
--   ?  codi_emp
-- ============================================================================
SELECT s.codi_imp,
       MAX(i.NOME_IMP) AS nome_imp
FROM bethadba.efsdoimp s
LEFT JOIN bethadba.EFIMPOSTO i
       ON i.CODI_EMP = s.codi_emp AND i.CODI_IMP = s.codi_imp
WHERE s.codi_emp = ?
GROUP BY s.codi_imp
ORDER BY s.codi_imp
