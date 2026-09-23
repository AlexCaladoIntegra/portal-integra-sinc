# CLAUDE.md — Portal Integra Sinc

Referência de arquitetura e convenções. Leia antes de escrever código aqui.
O `README.md` cobre instalação, execução e diagnóstico — não repita isso aqui.

## Contexto

Processo separado que lê o Domínio Contábil (SQL Anywhere, ODBC) e grava no
PostgreSQL do Portal Integra. Roda na máquina do cliente, onde o driver ODBC
existe; o Portal, na VPS, não tem rota para o banco do cliente nem o driver na
imagem.

O plano completo, com as cinco etapas e as armadilhas de cada uma, está em
`changes/SINC-000-plano-de-desenvolvimento.md`. Ele morava fora do repositório
até 23/09/2026; revisão de decisão agora passa por PR, como qualquer código.
O `portal-integra` tem um plano próprio para o
mesmo problema — `changes/SYNC-001-plano-de-migracao-arquitetural.md` — que
desenha a integração por **API HTTP com HMAC**. Não é o caminho escolhido
(grava-se direto no PostgreSQL), mas o §16.9 de lá é a lista canônica das
armadilhas de extração e vale por si.

## Stack

- Python 3.13, Flask 3.1 (app factory + blueprints)
- PostgreSQL via psycopg2 — **sem ORM**, SQL escrito à mão
- pyodbc contra SQL Anywhere, **somente leitura**
- pydantic-settings para configuração (`app/config.py`)
- Jinja2 + CSS/JS vanilla, sem etapa de build
- ruff e pytest

**Sem Alembic, e é regra:** este projeto não é dono de tabela nenhuma. Quem cria
e altera as tabelas de destino é o `portal-integra`. Acrescentar schema por aqui
criaria uma segunda verdade ao lado da dele.

**Sem Flask-Login e sem Flask-WTF:** não há usuário nem formulário. A proteção é
`SERVIDOR_HOST = "127.0.0.1"`, constante em `app/__init__.py`. Não a transforme
em configuração — há teste vermelho no caminho de quem tentar, e o motivo está
no cabeçalho do arquivo.

## As duas entradas

    run.py           a tela, em 127.0.0.1. Uma pessoa na frente.
    sincronizar.py   sem tela: CLI, e o daemon `--serve`. Ninguém na frente.

A diferença não é cosmética e decide o comportamento:

- `sincronizar.py` devolve **código de saída** (0/1/2/3), porque é a única
  coisa que o Agendador de Tarefas enxerga. O `2` — "já estava rodando" — tem
  de continuar distinto do `1`.
- `sincronizar.py` escreve log em **arquivo**, porque não há console.
- `sincronizar-agendado.bat` **não pode ter `pause`**: pendurava a tarefa até
  o timeout. O `abrir-sinc.bat` tem, de propósito. Há teste para os dois.

## Anatomia de um módulo

Igual à do Portal, e os nomes de camada são em **inglês** mesmo com o resto do
código em português:

| Arquivo | Responsabilidade |
|---|---|
| `routes.py` | camada HTTP: lê `request`, chama o service, devolve resposta. Sem regra de negócio, sem SQL. |
| `services.py` | regra de negócio; levanta `ErroApp` quando a regra não é atendida. |
| `repositories.py` | SQL. Recebe e devolve tipos simples, nunca objeto de request. Não conhece Flask. |

Fluxo obrigatório: `routes → services → repositories`.

## Convenções de código

- Arquivos e pacotes em `snake_case`, **em português do domínio** — exceto os
  nomes de camada acima.
- Funções: `snake_case`, verbo em português; helpers privados com `_`.
- Constantes: `UPPER_SNAKE`. Blueprints: `<modulo>_bp`.
- Chaves de JSON em **inglês e estáveis** (`data`, `meta`, `error`); mensagens ao
  usuário em **português**.
- `from __future__ import annotations` no topo; type hints nas assinaturas
  públicas.
- Linha de até 100 colunas; `ruff format` decide o resto.
- **Sem cabeçalho de controle nos arquivos** — autoria e data são do git.
- Docstrings em português, dizendo a regra e o que acontece nos extremos.
- **Comentário explica o porquê, não o quê.** Vale mais que qualquer outra regra
  desta lista.

## Banco

- Leitura `with get_connection()`, escrita `with transacao()`. Repository **não**
  chama `commit()`/`rollback()`: a transação é do context manager.
- Sempre `%s`. Nunca f-string com dado de entrada. As exceções existentes
  montam **identificador** (nome de tabela/coluna) a partir de constante do
  módulo, nunca de requisição — mantenha essa invariante ao adaptar.
- Resultado sai como dict (`linhas_dict`/`linha_dict`), nunca tupla crua.
- SQL grande em `app/data/queries/<dominio>/<nome>.sql`.
- **Nenhum código escreve `created_at`/`updated_at`** — são DEFAULT mais trigger
  no Portal.
- Exclusão mútua de operação longa é `trava_de_sessao()`. Ela impede a tela e a
  rodada agendada de se atropelarem — o caso real é alguém sincronizar às 02:00
  sem saber que o Agendador acabou de disparar. Mantenha o nome
  `f"importacao:{chave}"`: até 23/09/2026 ele também excluía a via do Portal, e
  volta a valer se aquela via renascer.

## Frontend

- `tokens.css` **sempre primeiro**; os demais só usam `var(--token)`.
- **Nenhum literal de cor novo.** `--surface` **não existe** — use `--card` ou
  `--panel-inset`. Um `var(--surface)` não dá erro, só deixa o elemento
  transparente.
- **Cor sempre com label textual.** Nunca comunique situação só pelo chip.
- `.btn-primary` é o **discreto**; o de destaque é `.btn-primary--accent`.
- Resultado que não gravou nada sai em **âmbar `--aviso`, nunca verde**, com o
  motivo e o próximo passo.
- Proibido `onclick=` no HTML — a CSP bloqueia. Use `data-*` e delegação.
- Todo `<script>` executável leva `nonce="{{ csp_nonce }}"`.
- Texto vindo do banco passa por `Portal.texto()`; dentro de atributo,
  `Portal.atributo()`.
- O tema claro **não** redefine tokens: toda regra nova precisa do par
  `body.light` escrito à mão.

Os seis CSS e o `portal.js` são **cópia literal do Portal**. Não os edite: o que
este projeto precisa de diferente mora em `css/sinc.css`.

## A exceção ao envelope de resposta

Toda rota responde `{data: …}` / `{error: …}` — **menos uma**.
`POST /api/v1/sincronizacao/tudo` responde **NDJSON**, uma linha JSON por
evento, transmitida conforme acontece. O motivo é a duração: a rodada completa
levou 24 minutos medidos, e uma resposta única deixaria a tela muda o tempo
todo, indistinguível de uma execução travada.

Duas consequências que quem mexer precisa saber:

- **O status HTTP é sempre 200**, inclusive quando a rodada falha — o cabeçalho
  sai antes de a primeira fase terminar. Quem consome decide pelo evento `fim`.
- **Fim ausente é FALHA**, não sucesso: significa conexão interrompida. O
  `lerFluxo` do `sincronizacao.js` verifica isso explicitamente.

A regra de ordem e de parada continua num lugar só: `services.executar_tudo`
recebe um gancho `progresso`, e cada chamador apresenta como quiser — a rota
transmite, a CLI escreve no log.

## Erros

| Exceção | Status |
|---|---|
| `ErroValidacao` | 422 |
| `ErroNaoEncontrado` | 404 |
| `ErroConflito` | 409 |
| `ErroIndisponivel` | 503 |

A tradução para HTTP acontece **só** em `_registrar_erros`, na factory. Motivo
técnico nunca vai para a tela.

## O código copiado do Portal

É deliberado. A lógica de leitura do Domínio tem regra de negócio medida contra
dados reais, e reescrevê-la produz **número plausível e errado** — o defeito mais
caro deste domínio, porque não gera erro, não gera log e se lê como um relatório
normal.

**"Cópia" virou "o original".** Em 23/09/2026 o `portal-integra` removeu os
próprios importadores (Fase 9 do SYNC-001): não há mais um segundo lugar onde
essa lógica viva, nem para onde portar correção de volta, nem contra o que
conferir se uma reescrita foi fiel. A regra de não editar para "melhorar" ficou
mais forte, não mais fraca.

Antes de "limpar" qualquer coisa em `importacao/`, leia o cabeçalho do arquivo.
Os três que mais custam:

1. O **codec `cp1252_tolerante`** de `dominio.py` ignora de propósito o `errors`
   que o chamador pedir. Há texto UTF-8 dentro de coluna cp1252 na origem.
2. O `try/except` do teto de consulta: o driver da SAP responde `HYC00 Driver
   not capable`, e sem a guarda **todo** importador morre com erro genérico de
   conexão, com a conexão perfeita.
3. O `conn.close()` no `finally`: o context manager do pyodbc encerra a
   transação mas **não** fecha a conexão.

Divergência declarada em relação ao Portal: **não existe `OPCOES`** no contrato
dos importadores. Lá é contrato pela metade — a tela desenha as caixas e a rota
descarta o valor — e nenhum dos dez importadores o usa.

## Antes de terminar

1. Mapeie os arquivos afetados antes de editar.
2. Rode `ruff check . && ruff format . && pytest`.
3. Diga quais arquivos mudaram e quais validações rodaram.

## Proibido

- Regra de negócio em `routes.py`; acesso a banco em template.
- Superusuário do banco na aplicação.
- Segredo em código, log ou resposta.
- Criar migration aqui.
- Tornar `SERVIDOR_HOST` configurável.
- Editar ou commitar o `.env`.
- Editar os CSS e o `portal.js` copiados do Portal.
