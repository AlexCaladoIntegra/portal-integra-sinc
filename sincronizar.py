"""Sincronização sem tela — para o agendador, e para a carga inicial.

    python sincronizar.py                      a rodada completa, na ordem
    python sincronizar.py --importador X       só um conjunto de dados
    python sincronizar.py --empresa 272        restrito a uma ou mais empresas
    python sincronizar.py --dry-run            simula, não grava
    python sincronizar.py --listar             mostra os conjuntos e sai
    python sincronizar.py --serve              daemon com cron (ver abaixo)

## Os dois jeitos de agendar, e qual escolher

**Agendador de Tarefas do Windows** — o recomendado. Use
`sincronizar-agendado.bat`. O Windows acorda o processo na hora marcada, ele
roda, grava e sai. Sobrevive a reinício da máquina, não depende de ninguém
deixar janela aberta, e o próprio Agendador mostra o resultado da última
execução.

**`--serve`** — um processo que fica vivo e dispara no horário de `SINC_CRON`.
Serve quando a máquina já tem o sincronizador aberto o dia todo e não se quer
mexer no Agendador. O preço é que, se o processo morrer, ninguém percebe até a
sincronização não ter acontecido.

## O que a rodada completa faz

Percorre os dez conjuntos na ordem de dependência e **para na primeira falha**
— a ordem é de dependência, e seguir depois de um erro grava sobre dado
incompleto. Nunca inclui registro novo (`incluir=False`): trazer empresa para
o Portal é decisão de quem opera, com a lista na frente.

Cada conjunto é registrado em `importacao_execucao` com `origem='sinc'`, então
o resultado da madrugada aparece de manhã no histórico da tela — **que é o
único lugar que o mostra.** A tela de importação do Portal saiu do ar em
23/09/2026, e do lado de lá ninguém mais lê essa tabela.

## Códigos de saída

    0  tudo certo
    1  a sincronização falhou (o motivo está no log e no histórico)
    2  já havia uma execução em andamento — nada foi feito
    3  configuração inválida (.env, conexões, conjunto inexistente)

As duas pontas são conferidas ANTES de a primeira fase rodar: origem fora ou
destino fora sai como `3`, com uma linha dizendo qual, em vez de dez fases
falhando em sequência.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PASTA_DE_LOG = BASE_DIR / "logs"

# Códigos de saída. O agendador só enxerga o número: 2 precisa se distinguir
# de 1 porque "já estava rodando" não é falha e não deve acordar ninguém.
OK = 0
FALHOU = 1
EM_ANDAMENTO = 2
CONFIGURACAO = 3


def _configurar_log(nivel: str) -> None:
    """Console **e** arquivo rotativo.

    O arquivo não é conveniência: rodando pelo Agendador de Tarefas não há
    console para onde olhar, e sem ele a única pista de uma madrugada que deu
    errado seria a linha de erro no histórico — sem traceback, sem contexto.

    Cinco arquivos de 2 MB: cobre semanas de operação e não cresce sozinho.
    """
    PASTA_DE_LOG.mkdir(exist_ok=True)
    formato = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    arquivo = logging.handlers.RotatingFileHandler(
        PASTA_DE_LOG / "sincronizacao.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    arquivo.setFormatter(formato)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formato)

    raiz = logging.getLogger()
    raiz.setLevel(getattr(logging, nivel.upper(), logging.INFO))
    raiz.handlers = [arquivo, console]


def _relatar(resultado: dict, log: logging.Logger) -> int:
    """Escreve o placar e devolve o código de saída."""
    for fase in resultado["fases"]:
        if fase["status"] == "erro":
            log.error("  %-24s ERRO: %s", fase["chave"], fase["erro"])
        else:
            log.info(
                "  %-24s lidas %s · incluídas %s · atualizadas %s · ignoradas %s · %ss",
                fase["chave"],
                fase["lidas"],
                fase["incluidas"],
                fase["atualizadas"],
                fase["ignoradas"],
                fase["duracao_segundos"],
            )

    total = resultado["total"]
    if resultado["concluido"]:
        log.info(
            "Rodada concluída: lidas %s · incluídas %s · atualizadas %s · ignoradas %s",
            total["lidas"],
            total["incluidas"],
            total["atualizadas"],
            total["ignoradas"],
        )
        return OK

    ultima = resultado["fases"][-1]["chave"] if resultado["fases"] else "—"
    log.error("Rodada INTERROMPIDA em '%s'. Os conjuntos seguintes não rodaram.", ultima)
    return FALHOU


def sincronizar(args) -> int:
    from app.importacao import services
    from app.shared.errors import ErroApp, ErroConflito

    log = logging.getLogger("sincronizar")

    try:
        simulacao = " (simulação)" if args.dry_run else ""
        if args.importador:
            log.info("Sincronizando '%s'%s", args.importador, simulacao)
            resultado = services.executar(
                args.importador,
                dry_run=args.dry_run,
                ids=args.empresa,
                origem=services.ORIGEM_CLI,
            )
            log.info("  %s", resultado)
            return OK

        log.info("Rodada completa%s", simulacao)

        # O mesmo gancho que alimenta a tela: sem ele, um log noturno de 24
        # minutos fica mudo do começo ao fim, e uma rodada travada é
        # indistinguível de uma longa. O `_relatar` no fim continua, e é o
        # placar; isto aqui é o andamento.
        def anunciar(evento):
            if evento["evento"] == "iniciou":
                log.info("  [%s/%s] %s…", evento["indice"], evento["de"], evento["nome"])

        return _relatar(services.executar_tudo(dry_run=args.dry_run, progresso=anunciar), log)

    except ErroConflito as exc:
        # Não é falha: o operador pode estar sincronizando pela tela agora. A
        # trava do PostgreSQL fez o seu trabalho, e a próxima janela pega.
        log.warning("%s", exc.message)
        return EM_ANDAMENTO
    except ErroApp as exc:
        log.error("%s", exc.message)
        return FALHOU
    except Exception:
        log.exception("Falha não prevista")
        return FALHOU


def servir(args) -> int:
    """Daemon: dispara a rodada completa na expressão cron de `SINC_CRON`."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    from app.config import get_settings

    log = logging.getLogger("sincronizar")
    s = get_settings()
    cron = args.cron or s.sinc_cron

    # Expressão inválida é erro de CONFIGURAÇÃO, e tem de sair como tal. Sem
    # isto o traceback subiria até o `cmd.exe` e o agendador mostraria um
    # código que não diz o que fazer.
    try:
        gatilho = CronTrigger.from_crontab(cron, timezone=s.tz)
    except ValueError as exc:
        log.error("Expressão cron inválida (%r): %s", cron, exc)
        return CONFIGURACAO

    agendador = BlockingScheduler(timezone=s.tz)
    agendador.add_job(
        sincronizar,
        gatilho,
        kwargs={"args": args},
        id="rodada_completa",
        name="Sincronização completa",
        # Se a máquina estava suspensa na hora marcada, roda ao acordar — desde
        # que dentro de uma hora. Mais que isso já é a janela seguinte, e rodar
        # de dia atrapalharia quem está usando o Portal.
        misfire_grace_time=3600,
        # Uma execução por vez. A trava do PostgreSQL já recusaria a segunda,
        # mas com 409 no histórico — ruído para um caso que o agendador sabe
        # evitar sozinho.
        max_instances=1,
        coalesce=True,
    )

    log.info("Agendado: '%s' (%s). Ctrl+C encerra.", cron, s.tz)
    try:
        agendador.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Encerrado.")
    return OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sincroniza o Domínio para o Portal Integra, sem tela.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--importador", metavar="CHAVE", help="roda só um conjunto de dados")
    parser.add_argument(
        "--empresa",
        type=int,
        action="append",
        metavar="ID",
        help="restringe às empresas informadas (repetível)",
    )
    parser.add_argument("--dry-run", action="store_true", help="simula, sem gravar")
    parser.add_argument("--listar", action="store_true", help="lista os conjuntos e sai")
    parser.add_argument("--serve", action="store_true", help="daemon com cron")
    parser.add_argument("--cron", metavar="EXPR", help="expressão cron do --serve")
    parser.add_argument("--log-level", default=None, metavar="NIVEL")
    args = parser.parse_args(argv)

    try:
        from app.config import get_settings

        nivel = args.log_level or get_settings().log_level
    except Exception as exc:
        # Sem console de log ainda: a configuração é o que o instala.
        print(f"Configuração inválida: {exc}", file=sys.stderr)  # noqa: T201
        return CONFIGURACAO

    _configurar_log(nivel)
    log = logging.getLogger("sincronizar")

    from app.health import services as health_services
    from app.importacao import dominio
    from app.importacao.importadores import REGISTRO

    if args.listar:
        for chave, modulo in REGISTRO.items():
            log.info("%-24s %s", chave, modulo.NOME)
        return OK

    if args.importador and args.importador not in REGISTRO:
        log.error(
            "Conjunto '%s' não existe. Use --listar para ver os disponíveis.", args.importador
        )
        return CONFIGURACAO

    # Recusa cedo e com a mensagem certa. Sem isto, cada um dos dez conjuntos
    # falharia por conta própria e o log da madrugada teria dez tracebacks
    # dizendo a mesma coisa.
    if not dominio.configurado():
        log.error(
            "A conexão com o Domínio não está configurada. "
            "Defina DOMINIO_DSN (ou DOMINIO_CONNSTR) no .env."
        )
        return CONFIGURACAO

    # As duas pontas, não só a origem. Rodando às duas da manhã, a diferença
    # entre "o destino está fora" e dez fases falhando em sequência é entre um
    # log que se lê em cinco segundos e dez tracebacks dizendo a mesma coisa —
    # mais dez linhas de erro no histórico que o Portal vai exibir de manhã.
    #
    # Só no modo de execução: o `--serve` fica vivo justamente para tentar de
    # novo amanhã, e derrubá-lo porque o banco caiu agora seria trocar uma
    # janela perdida por todas as seguintes.
    if not args.serve:
        diagnostico = health_services.diagnosticar()
        for ponta in ("origem", "destino"):
            if diagnostico[ponta]["situacao"] != "ok":
                log.error("%s: %s", ponta.upper(), diagnostico[ponta]["mensagem"])
                return CONFIGURACAO
        if diagnostico["schema"]["situacao"] != "ok":
            # AVISO, não recusa — a mesma decisão de `SCHEMA_REVISAO_ESPERADA`.
            log.warning("SCHEMA: %s", diagnostico["schema"]["mensagem"])

    return servir(args) if args.serve else sincronizar(args)


if __name__ == "__main__":
    sys.exit(main())
