-- ============================================================================
-- lancamentos.sql — lançamentos contábeis de uma empresa numa janela de datas
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional `?`). Lido pelo importador,
-- nunca pela aplicação web.
--
-- Devolve o lançamento CRU, uma linha por lançamento e não por perna: as duas
-- pernas vivem em colunas diferentes da mesma linha (`cdeb_lan` e `ccre_lan`),
-- e é o importador que as separa em dois registros de `bi_lancamento`. Fazer o
-- desdobramento aqui exigiria um UNION ALL que dobraria a leitura da tabela
-- para não economizar nada — do outro lado, transformar uma linha em duas é
-- uma repetição de laço.
--
-- POR QUE `codi_emp` SOZINHO NO FILTRO É O QUE IMPORTA. Ele é o prefixo da
-- chave primária de `ctlancto`, e é o caminho de acesso: o mesmo caminho pelo
-- qual o importador de saldos lê 553 mil lançamentos da empresa 272 em ~240 ms.
-- O recorte por `data_lan` é pós-filtro sobre essa mesma varredura. Por isso a
-- leitura é UMA consulta por (empresa, janela) — pedir mês a mês seriam 36
-- varreduras da tabela para o mesmo resultado.
--
-- O intervalo é meio-aberto (`>=` no início, `<` no fim), e não `BETWEEN`: o
-- fim é o primeiro dia do ano seguinte, então não há como esquecer o
-- 31/12 nem incluir o 01/01 seguinte por engano.
--
-- Sem `TOP`: a paginação é do lado do driver (`fetchmany`, ver
-- `dominio.sessao_em_lotes`). A maior empresa tem 370.437 lançamentos no
-- biênio 2025-2026, e materializar isso de uma vez são centenas de MB.
--
-- `RTRIM(codi_usu)` porque a coluna é CHAR(30) de largura fixa no SQL Anywhere
-- e chega cheia de espaços à direita — o mesmo cuidado que o importador de
-- empresas já tem.
--
-- O QUE NÃO SE FILTRA AQUI, e é deliberado:
--
--   `orig_lan`  vem cru. São 31 valores distintos nos dados, não dois, e é a
--               única coluna que diz de onde o lançamento veio. Quem exclui o
--               encerramento é a consulta de leitura da tela, não a importação
--               — o portal é espelho da origem.
--   a conta 0   o sentinela do Domínio para "sem conta neste lado" chega aqui e
--               é descartado no importador, junto com a conta que não está no
--               plano. As duas razões são contadas separadamente, porque só a
--               segunda merece aviso.
--
-- Parâmetros posicionais:
--   ?  codi_emp
--   ?  data inicial (inclusiva)
--   ?  data final (EXCLUSIVA)
-- ============================================================================
SELECT
  l.nume_lan,
  l.data_lan,
  l.vlor_lan,
  l.orig_lan,
  l.cdeb_lan,
  l.ccre_lan,
  l.chis_lan,
  l.ndoc_lan,
  l.codi_lote,
  RTRIM(l.codi_usu) AS codi_usu
FROM bethadba.ctlancto l
WHERE l.codi_emp  = ?
  AND l.data_lan >= ?
  AND l.data_lan  < ?
ORDER BY l.nume_lan
