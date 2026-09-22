"""Configuração do sincronizador — lida do ambiente/.env via pydantic-settings.

Fonte única de verdade. Nenhum módulo lê `os.environ` direto: importe
`get_settings()`.

É a versão podada do `app/config.py` do Portal Integra. Saíram os 21 campos de
sessão, login, SMTP e trava de produção — este processo não tem usuário, não
manda e-mail e não é servidor público. Ficaram as duas pontas que ele liga:
o PostgreSQL do Portal (destino) e o Domínio via ODBC (origem).

O que NÃO saiu, e por quê:

- `_credencial_obrigatoria` fica. Subir sem credencial do banco só adiaria a
  falha até a primeira sincronização, que é onde ela custa mais caro.
- `csp_enforce` fica. A tela é servida por Flask como a do Portal, com nonce
  por request, e o interruptor de emergência vale aqui pela mesma razão.

O que saiu e merece nota:

- `secret_key` não é mais configuração. Esta aplicação não tem login, sessão
  nem flash; o Flask só exige a chave para assinar cookie de sessão, que aqui
  não existe. Um valor efêmero por processo (ver `app/__init__.py`) é mais
  seguro que um segredo a mais no `.env` para ninguém usar.
- `app_host` não é mais configuração: é `127.0.0.1` fixo, no factory. Este
  processo grava no banco de produção SEM autenticação — a proteção é não
  escutar fora do loopback, e configuração é o que permite alguém desfazer
  isso por engano.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

# A revisão do schema do Portal contra a qual este sincronizador foi escrito.
#
# Ele grava em tabelas de que NÃO é dono — quem as cria e altera é o Alembic do
# portal-integra. Uma migration nova lá pode acrescentar coluna obrigatória,
# apertar constraint ou mudar chave natural, e o sintoma disso do lado de cá é
# `IntegrityError` no meio de uma carga de milhões de linhas — ou, pior, dado
# gravado onde não devia, sem erro nenhum.
#
# Por isso o boot compara esta constante com `alembic_version` e avisa em voz
# alta quando divergem. É AVISO e não recusa: o caso comum de divergência é uma
# migration aditiva que não afeta nada do que o sincronizador escreve, e
# derrubar a operação do cliente por isso seria pior que o risco. Quem lê o
# aviso decide.
SCHEMA_REVISAO_ESPERADA = "0021_empresa_situacao_origem"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Identificação ────────────────────────────────────────────────────────
    app_name: str = "Portal Integra Sinc"
    app_version: str = "0.1.0"

    # ── Flask ────────────────────────────────────────────────────────────────
    # A porta é 7820 e não 7810 de propósito: o Portal pode estar no ar na
    # mesma máquina durante o desenvolvimento, e duas aplicações disputando a
    # porta falham com "address already in use" num ponto que não explica nada.
    flask_debug: bool = False
    app_port: int = 7820

    csp_enforce: bool = True

    # ── PostgreSQL do Portal (DESTINO — o sincronizador GRAVA aqui) ──────────
    database_host: str = "localhost"
    database_port: int = 5432
    database_user: str = ""
    database_password: str = ""
    database_name: str = "portalintegra"
    db_pool_min: int = 1
    db_pool_max: int = 20

    # Os dois tetos, com a mesma doutrina do Portal: teto curto para CONECTAR
    # (host que não recusa a conexão deixa o `connect` no retry de SYN do SO,
    # ~130 s), teto generoso para CONSULTAR — ele existe para que uma consulta
    # desgovernada termine em erro em vez de nunca, não para disciplinar
    # duração. `0` desliga o teto por consulta.
    #
    # O MESMO tamanho do Portal (20). Nasceu 5, com o raciocínio de que "aqui
    # não há request concorrente: é uma tela para um operador". O raciocínio
    # estava errado, e custou um defeito:
    #
    # ao terminar uma sincronização, a tela recarrega tudo de uma vez — a
    # listagem dos dez cards, os NOVE `pendentes` em paralelo, o diagnóstico e
    # o histórico. O navegador mantém seis conexões simultâneas por host, cada
    # requisição segura ao menos uma conexão do pool, e a `listar` segura a
    # dela enquanto chama `resumo()` dez vezes. Com cinco, o pool estourava:
    #
    #     psycopg2.pool.PoolError: connection pool exhausted
    #
    # O `/diagnostico` caía junto, e a tela — que desabilita o botão quando o
    # diagnóstico falha — deixava o operador sem conseguir sincronizar de novo.
    #
    # "Um operador" nunca significou "uma requisição".
    db_connect_timeout: int = 10
    db_statement_timeout_ms: int = 300_000

    # ── Domínio / SQL Anywhere (ORIGEM — somente leitura) ────────────────────
    # Informe DOMINIO_DSN (+ usuário/senha) ou DOMINIO_CONNSTR, para o caso de
    # uma string ODBC completa. Vazio desativa a leitura: a tela avisa e a
    # sincronização recusa, em vez de falhar sem explicar.
    #
    # Exige o driver ODBC do SQL Anywhere instalado na máquina (64 bits). Ele é
    # proprietário e não vem por pip — é justamente por isso que este processo
    # existe separado do Portal.
    dominio_dsn: str = ""
    dominio_connstr: str = ""
    dominio_user: str = ""
    dominio_password: str = ""
    dominio_schema: str = "bethadba"

    # ── Os dois tetos da sincronização contábil ──────────────────────────────
    #
    # No Portal os dois são constante no código, e o motivo declarado lá é o
    # gunicorn: a rota de importação roda SÍNCRONA dentro da requisição HTTP, e
    # selecionar 333 empresas prenderia um worker até o nginx cortar a conexão,
    # deixando a importação órfã no histórico.
    #
    # Aqui não há gunicorn nem nginx — e, mais que isso, **a carga em lote é o
    # trabalho deste processo**. O próprio Portal reconhece isso: a mensagem de
    # erro do teto manda usar o CLI para a carga inicial. Este projeto É esse
    # caminho, então o teto vira configuração e nasce desligado.
    #
    # `sinc_teto_de_empresas = 0` desliga o teto. O que continua protegendo
    # contra execução duplicada é a trava de sessão do PostgreSQL, não o teto.
    # O que NÃO há é barra de progresso: uma rodada longa aparece como um
    # fetch pendurado no navegador. Quem quiser o freio de volta põe um número.
    sinc_teto_de_empresas: int = 0

    # Ano corrente e os N-1 anteriores, na importação de lançamentos. Não é
    # constante de gosto: decide o tamanho da tabela local e o que a tela do
    # Portal pode oferecer de drill-down. Para menos exige recarga; para mais,
    # espaço. **Tem de concordar com o `janela()` do Portal** — os dois leem a
    # mesma tabela, e divergir faz o aviso de "período anterior à janela"
    # mentir sobre o que existe.
    sinc_anos_janela: int = 3

    # ── Agendamento ──────────────────────────────────────────────────────────
    # Expressão cron do daemon (`sincronizar.py --serve`). Só vale nesse modo:
    # quem agenda pelo Agendador de Tarefas do Windows define o horário LÁ, e
    # este campo é ignorado.
    #
    # 02:00 por default, no fuso de `tz`. A rodada completa do parque é longa
    # — medido, ~16 s por empresa nas cinco entidades pesadas — e precisa de
    # uma janela em que ninguém esteja usando o Portal.
    sinc_cron: str = "0 2 * * *"

    # ── Operação ─────────────────────────────────────────────────────────────
    tz: str = "America/Cuiaba"
    log_level: str = "INFO"

    @field_validator("database_user", "database_password")
    @classmethod
    def _credencial_obrigatoria(cls, v: str, info) -> str:
        if not v:
            raise ValueError(f"{info.field_name.upper()} não configurada — defina no .env")
        return v

    @property
    def database_url(self) -> str:
        """URL SQLAlchemy do banco do Portal.

        Este projeto não roda Alembic — não é dono de tabela nenhuma. A
        propriedade fica porque é o formato que as ferramentas de linha de
        comando (psql, alembic do Portal, dumps) esperam, e montá-la à mão com
        a senha sem `quote_plus` é o erro que se comete uma vez por projeto.
        """
        senha = quote_plus(self.database_password)
        return (
            f"postgresql+psycopg2://{self.database_user}:{senha}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )


@lru_cache
def get_settings() -> Settings:
    """Settings do processo (instância única)."""
    return Settings()
