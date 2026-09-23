# SINC-006 — Assinatura de origem: sincronizar só o que mudou

Hoje toda sincronização lê tudo. Este documento avalia o custo disso, mede o
que uma impressão digital por empresa economizaria, e propõe uma execução em
três fases — sendo a primeira **medir sem agir**.

Data da análise: 21/09/2026. Medições contra o Domínio e o `portalintegra`
reais desta instalação.

---

## 1. Resumo executivo

**Não existe nenhum controle de incremental.** Busca por `checkpoint`,
`watermark`, `md5`, `hashlib`, `incremental`, `sincronizado_em` e
`data_alteracao` em todo o código: zero ocorrência. Nenhuma das 23 consultas
de origem filtra por data de alteração. A única com qualquer recorte é
`lancamentos.sql`, e o recorte dela é uma **janela de 3 anos**, não uma marca
d'água — ela relê os 3 anos inteiros a cada execução.

O que limita o trabalho hoje é o operador escolhendo empresas na tela.

**A medição que decide:** a impressão digital do parque inteiro custa
**12,3 segundos**, em cinco consultas. Contra isso, uma rodada completa é de
dezenas de minutos a horas. A assinatura se paga mesmo pulando só 10% das
empresas.

**A recomendação:** uma Fase 0 que calcula e registra, **sem pular nada**. Em
uma semana de operação normal ela entrega a taxa de pulo real — o único número
que não dá para medir hoje e que decide se o resto vale.

---

## 2. O que existe hoje, e o que não existe

### 2.1 Contra duplicação: existe, e é forte

A garantia não está no importador — está no **schema**. Dezessete das dezoito
tabelas sincronizadas têm chave primária natural:

```
empresas                   PK (id_empresa)
bi_conta                   PK (id_empresa, codi_cta)
bi_saldo_mensal            PK (id_empresa, codi_cta, ano, mes)
bi_lancamento              PK (id_empresa, nume_lan, natureza)
bi_fiscal_produto_mensal   PK (id_empresa, ano, mes, tipo, codi_pdi, codi_nat, codi_acu)
bi_fiscal_apuracao         PK (id_empresa, codi_imp, competencia, periodicidade)
…
```

A exceção é `bi_fiscal_nota_mensal`, com PK sintética `BIGSERIAL`, protegida
pelo índice `uq_bi_fiscal_nota_grao` — 12 colunas, `NULLS NOT DISTINCT`.
Conferido em 21/09/2026: **zero grupos duplicados** na tabela.

Duplicar é **impossível**, não improvável: mesmo com um defeito no importador,
o PostgreSQL recusa. Vira erro visível, nunca linha repetida.

Somam-se a isso os três idiomas de gravação (`ON CONFLICT DO UPDATE`,
substituição de partição, reconciliação) e a trava de sessão do PostgreSQL, que
impede a tela e a rodada agendada de se atropelarem.

> Quando este documento foi escrito, a trava também excluía a via do Portal.
> Ela não existe mais: em 23/09/2026 o `portal-integra` removeu os próprios
> importadores, e este projeto passou a ser o único caminho de dados.

### 2.2 Contra releitura: não existe nada

E a exclusão na origem se divide:

| | Reflete exclusão? | Por quê |
|---|---|---|
| Tabelas por empresa (saldos, lançamentos, movimento, apuração, produto, participantes, DFC, plano) | **Sim** | a gravação apaga o recorte e reinsere: o que sumiu não volta |
| `bi_fiscal_especie`, `bi_fiscal_cfop` | **Não** | só `INSERT … ON CONFLICT DO UPDATE`, sem reconciliação |
| `empresas` | **Não** | só `UPDATE` — ver a pendência das 4 encerradas, no README |

O vazamento dos dois catálogos globais é pequeno (espécie e CFOP só crescem),
mas é real e vale registrar.

---

## 3. As medições

### 3.1 Assinatura por empresa × leitura completa

Empresa 272, contra o Domínio real:

| entidade | ler tudo | assinatura | ganho |
|---|---|---|---|
| `contabil_saldos` | 0,37 s | 0,05 s | 8× |
| `contabil_lancamentos` | 2,53 s | 0,28 s | 9× |
| `fiscal_movimento` (saídas) | 2,81 s | 0,16 s | 17× |
| `fiscal_produto` (saídas) | 2,36 s | 0,20 s | 12× |
| `fiscal_apuracao` | 0,03 s | 0,01 s | 2× |

O ganho não é maior porque **"ler tudo" já é a consulta agregada** que o
importador usa: o Domínio agrega antes de entregar. Não se compara contra o
dado bruto.

### 3.2 Assinatura do parque inteiro — o número que decide

Agrupando por `codi_emp` numa varredura só, em vez de 363 idas ao ODBC:

| tabela de origem | tempo | empresas |
|---|---|---|
| `ctlancto` (saldos + lançamentos) | 2,5 s | 514 |
| `efsaidas` (movimento saída) | 2,6 s | 387 |
| `efentradas` (movimento entrada) | 0,7 s | 513 |
| `efmvspro` (produto) | 6,3 s | 298 |
| `efsdoimp` (apuração) | 0,2 s | 615 |
| **TOTAL** | **12,3 s** | |

Referência do outro lado: a empresa 272 sozinha leva ~16 s somando as cinco
entidades. O parque inteiro é de dezenas de minutos a horas.

### 3.3 A taxa de pulo — o que NÃO foi medido

Distribuição da última competência com movimento fiscal, por empresa:

```
2026-09     8        2026-06     9        2026-03     5
2026-08   217        2026-05     4        2026-02     3
2026-07    41        2026-04     7
```

**Cuidado com a leitura fácil.** "98% têm última competência anterior ao mês
corrente" é verdade e é enganoso: 217 das 363 têm **agosto** como última
competência, e agosto está sendo fechado *agora*, em setembro. Essas empresas
recebem lançamento todo dia, e a assinatura delas muda toda noite.

O que se pode afirmar: **~69 empresas (19%) têm movimento parado há dois meses
ou mais** — pulo certo. O resto é desconhecido.

A taxa real de "nada mudou entre ontem e hoje" **exige dois retratos separados
no tempo**. É exatamente o que a Fase 0 existe para produzir.

---

## 4. Decisões de desenho

### D1 — Recorte por EMPRESA, não por ano

A gravação dos nossos importadores substitui a **partição inteira da empresa**
(`DELETE WHERE id_empresa …` seguido de `INSERT` em massa). Pular a empresa
inteira encaixa sem tocar em uma linha do código de escrita.

O SYNC-001 propõe recorte por `(empresa, ano)`, e ali faz sentido porque a
remessa HTTP é paginada. Aqui obrigaria a mudar o `DELETE` dos quatro
importadores de partição — e traz o problema que o próprio SYNC-001 registra
em §24 D3: um ano que suma inteiro da origem nunca seria apagado.

*Recorte por empresa. Sem mudança na escrita.*

### D2 — Comparar origem contra ORIGEM, com estado local

A alternativa elegante seria comparar a assinatura da origem com uma
equivalente calculada no Portal, dispensando estado. **Não funciona**: o
importador transforma e descarta. Na empresa 272, 971 de 537.536 pernas foram
descartadas por conta fora do plano; `SUM` na origem nunca vai bater com `SUM`
no Portal.

Então: guarda-se a assinatura da origem depois de cada sincronização
bem-sucedida, e compara-se com a de agora.

Perder o estado custa **uma rodada completa** — degradação segura, não
corrupção. É a propriedade que permite guardá-lo fora do banco do Portal.

### D3 — SQLite local, e não uma tabela no Portal

Uma tabela no `portalintegra` exigiria migration, e este projeto tem "sem
migrations" como regra — ele não é dono de tabela nenhuma. O estado é **deste
processo**, não do Portal: um arquivo ao lado do executável, no `.gitignore`.

### D4 — O formato da assinatura é o do SYNC-001

`md5(f"{contagem}:{soma}")`, com a soma em duas casas, ponto como separador,
sem separador de milhar. Não porque precise ser MD5 — qualquer coisa serviria
—, mas porque **manter o mesmo contrato mantém a porta aberta** para a
arquitetura HTTP do SYNC-001, se um dia o Portal sair da rede do cliente.

---

## 5. O risco, nomeado

`COUNT` + `SUM` é **cego a edição que preserva os dois**. Trocar o valor entre
dois lançamentos, ou uma correção que se anula, passa despercebido.

É improvável e é real. A mitigação é barata e não exige código novo: uma
rodada **ignorando a assinatura** de tempos em tempos — semanal ou mensal —,
que é o que a operação faria de qualquer forma.

O que NÃO mitiga: acrescentar mais medidas à assinatura reduz a cegueira sem
eliminá-la, e custa tempo de origem. Não vale.

---

## 6. Plano de execução

### Fase 0 — Medir sem agir  ← *a que se faz agora*

Calcular a assinatura de cada empresa antes de sincronizar, comparar com a
guardada, **registrar no log quantas teriam sido puladas — e não pular
nenhuma.**

Risco zero: nada muda no comportamento. Entregável: a taxa de pulo real, em
uma semana de operação normal.

| Arquivo | O quê |
|---|---|
| `app/importacao/assinatura.py` | as consultas de origem por entidade e o cálculo |
| `app/importacao/estado.py` | o SQLite local: ler e gravar assinatura |
| `app/importacao/services.py` | duas chamadas em `_executar_travado`, que **nunca levantam** |

Cobertura: as cinco entidades pesadas — `contabil_saldos`,
`contabil_lancamentos`, `fiscal_movimento`, `fiscal_apuracao`,
`fiscal_produto`. As outras cinco são rápidas e não pagam o mecanismo.

**Critério de aceite:** a sincronização continua idêntica em resultado e em
duração; o log passa a dizer, por entidade, quantas empresas estavam
inalteradas; derrubar o arquivo de estado não quebra nada.

### Fase 1 — Ligar o pulo

Só depois de a Fase 0 dar um número. Acrescenta o pulo de fato, com uma opção
`forcar` que o ignora — na tela e na API.

**Se a taxa vier abaixo de ~15%, esta fase não deve ser feita**, e a Fase 0 já
pagou por si mesma ao evitar o trabalho.

### Fase 2 — A rodada de reconciliação

Periódica, ignorando a assinatura, para cobrir a cegueira do §5. Faz sentido
junto com o agendamento, não antes.

---

## 7. Sequência: isto depende de agendar

**A SINC-006 só rende junto com a rodada automática.** Enquanto a operação for
uma pessoa escolhendo empresas na tela, o gargalo não é ler demais — é que
ninguém está rodando o parque inteiro.

Se o próximo passo real for agendar a rodada noturna (o `APScheduler` já está
no `requirements.txt` desde a Etapa 1, sem uso), a SINC-006 vem antes dela. Se
não for, a Fase 0 mede de graça enquanto a operação segue manual, e as Fases 1
e 2 esperam.
