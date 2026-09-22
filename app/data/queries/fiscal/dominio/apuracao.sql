-- ============================================================================
-- apuracao.sql -- o saldo apurado de cada imposto, por competencia (Dominio)
-- ----------------------------------------------------------------------------
-- Dialeto: SQL Anywhere (placeholder posicional). Lido pelo importador, nunca
-- pela aplicacao web.
--
-- 1:1 com `efsdoimp` -- nao ha agregacao possivel, e nao deve haver. A PK da
-- origem e (codi_emp, codi_imp, data_sim, pdic_sim), e `pdic_sim` (a
-- periodicidade) faz parte do grao de verdade: medido em 11/09/2026, das 15.320
-- competencias com valor, 1.156 tem de 2 a 5 periodicidades, e sao guias
-- LEGITIMAMENTE separadas (ICMS decendial, FUNDERSUL semanal). Somar por
-- competencia sem filtrar periodicidade esta certo; REMOVE-LA do grao perderia
-- a diferenca entre uma guia de R$ 10 mil e cinco de R$ 2 mil, e o vencimento
-- de cada uma.
--
-- AS DUAS MEDIDAS DE SALDO, e a razao de nenhuma ser redundancia -- e a mesma
-- de `movimento` e `movimento_total` em `bi_saldo_mensal`:
--
--   sdev_sim   "Saldo a Recolher"                  -> E A GUIA
--   srec_sim   "Saldo a recolher ANTES das deducoes"
--
-- As duas existem, sao diferentes, e trocar uma pela outra nao gera erro
-- nenhum. Medido no 1T/2026: COFINS Lucro Real tem sdev R$ 1.022.635,84 contra
-- srec R$ 1.630.802,40 -- 59,5% a mais. E a relacao nao tem sinal fixo: em IRPJ
-- Lucro Presumido o sdev e MAIOR que o srec. Nao ha regra a deduzir; ha so
-- "sdev e a guia, ponto".
--
-- NAO HA COLUNA DE PAGAMENTO, e a ausencia e deliberada. `dpag_sim` tem ZERO
-- preenchimentos desde 2022 e `efpagimp` registrou 13 pagamentos em 2026 contra
-- 962 em 2022: o registro de baixa no Dominio parou. Trazer `vlrpago_sim` daria
-- uma coluna zerada que alguem leria como "nada foi pago", em vez de "nao se
-- sabe" -- e um card de imposto em aberto mostraria 100% de tudo.
--
-- OS DOZE COMPONENTES, e por que eles nao sao derivaveis dos saldos (FIS-009).
--
-- As seis medidas acima sao todas SALDOS -- o resultado da conta. A demonstracao
-- da apuracao mostra as PARCELAS, e nenhuma delas se deduz do resultado. Medido
-- na 510, Jan/2026, ICMS: a grade derivava Debito 160.476,44 e Credito 0,00 dos
-- dois saldos, onde o relatorio do Dominio diz 448.587,46 e 288.111,02.
--
-- A aritmetica que as doze fecham, e que o payload confere:
--
--   Total de debitos  = debito_saidas + outros_debitos + estorno_creditos
--   Total de creditos = saldo_credor_anterior + credito_entradas
--                     + outros_creditos + credito_presumido + estorno_debitos
--   a recolher        = debitos - creditos + outros_acrescimos - outras_deducoes
--
-- Ela fecha em 7.500 das 7.610 linhas de ICMS desde 2025, e NAO em todas: as 110
-- restantes sao reais e grandes (empresa 219, ago/2026, residuo R$ 174.377,82).
-- Quem consumir estas colunas precisa do estado "nao fecha" -- ver
-- `apuracao_payload`.
--
-- `crei_sim` (credito presumido) e ZERO no parque inteiro, em todos os impostos,
-- desde 2025. Ele vem assim mesmo: e uma linha do relatorio, e uma coluna que
-- nao viesse obrigaria a tela a inventar o zero em vez de le-lo.
--
-- O EXTEMPORANEO NAO ESTA AQUI, e nao e esquecimento: ele vive em
-- `EFSDOIMP_ESTADUAL_<UF>`, uma tabela por estado, e nem todas tem a coluna. Ver
-- `apuracao_extemporaneo.sql`.
--
-- Sem recorte de data: sao 207.601 linhas no parque inteiro, a tabela mais
-- barata do modulo.
--
-- Parametros posicionais:
--   ?  codi_emp
-- ============================================================================
SELECT s.codi_imp,
       s.data_sim AS competencia,
       s.pdic_sim AS periodicidade,
       s.sdev_sim AS saldo_a_recolher,
       s.srec_sim AS saldo_antes_deducoes,
       s.scre_sim AS saldo_credor,
       s.bcal_sim AS base,
       s.aliq_sim AS aliquota,
       s.dvct_sim AS vencimento,
       s.SALDO_CREDOR_ANTERIOR AS saldo_credor_anterior,
       s.vims_sim AS debito_saidas,
       s.outd_sim AS outros_debitos,
       s.estc_sim AS estorno_creditos,
       s.vime_sim AS credito_entradas,
       s.outc_sim AS outros_creditos,
       s.crei_sim AS credito_presumido,
       s.estd_sim AS estorno_debitos,
       s.oacr_sim AS outros_acrescimos,
       s.oded_sim AS outras_deducoes,
       s.SALDO_DIFERIDO_ANTERIOR AS diferido_anterior,
       s.sdif_sim AS diferido_periodo
FROM bethadba.efsdoimp s
WHERE s.codi_emp = ?
ORDER BY s.codi_imp, s.data_sim, s.pdic_sim
