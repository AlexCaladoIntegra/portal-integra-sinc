"""A entrada sem tela — o que o Agendador de Tarefas executa.

O que está sob teste é o **código de saída**, porque é a única coisa que o
agendador enxerga. Um `2` (já estava rodando) confundido com `1` (falhou)
acorda alguém de madrugada por um não-problema; o contrário esconde uma
sincronização que não aconteceu.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import sincronizar
from app.shared.errors import ErroConflito, ErroValidacao


@pytest.fixture(autouse=True)
def sem_arquivo_de_log(monkeypatch):
    """O teste não escreve em `logs/` — isso é efeito de instalação."""
    monkeypatch.setattr(sincronizar, "_configurar_log", lambda _n: None)


@pytest.fixture
def dominio_ok(monkeypatch):
    from app.importacao import dominio

    monkeypatch.setattr(dominio, "configurado", lambda: True)


def rodada(concluido, fases=(), total=None):
    return {
        "fases": list(fases),
        "total": total or {"lidas": 0, "incluidas": 0, "atualizadas": 0, "ignoradas": 0},
        "concluido": concluido,
    }


# ── Os códigos de saída ──────────────────────────────────────────────────────


def test_rodada_completa_bem_sucedida_sai_zero(monkeypatch, dominio_ok):
    from app.importacao import services

    monkeypatch.setattr(services, "executar_tudo", lambda **_k: rodada(True))
    assert sincronizar.main([]) == sincronizar.OK


def test_rodada_interrompida_sai_um(monkeypatch, dominio_ok):
    from app.importacao import services

    fases = [{"chave": "empresas", "status": "erro", "erro": "origem fora"}]
    monkeypatch.setattr(services, "executar_tudo", lambda **_k: rodada(False, fases))
    assert sincronizar.main([]) == sincronizar.FALHOU


def test_ja_em_execucao_sai_DOIS_e_nao_um(monkeypatch, dominio_ok):
    """A distinção que existe para o agendador não acordar ninguém à toa.

    O operador pode estar sincronizando pela tela na hora da janela. A trava
    do PostgreSQL fez o seu trabalho, e a próxima madrugada pega.
    """
    from app.importacao import services

    def ocupado(**_k):
        raise ErroConflito("A importação 'Empresas' já está em execução.")

    monkeypatch.setattr(services, "executar_tudo", ocupado)
    assert sincronizar.main([]) == sincronizar.EM_ANDAMENTO


def test_erro_de_regra_sai_um(monkeypatch, dominio_ok):
    from app.importacao import services

    def recusa(**_k):
        raise ErroValidacao("empresa sem plano de contas")

    monkeypatch.setattr(services, "executar_tudo", recusa)
    assert sincronizar.main([]) == sincronizar.FALHOU


def test_falha_inesperada_sai_um_e_nao_propaga(monkeypatch, dominio_ok):
    """Traceback subindo até o `cmd.exe` daria um código de saída qualquer, e
    o Agendador mostraria um número que não significa nada."""
    from app.importacao import services

    def explode(**_k):
        raise RuntimeError("driver morreu")

    monkeypatch.setattr(services, "executar_tudo", explode)
    assert sincronizar.main([]) == sincronizar.FALHOU


# ── As recusas antes de começar ──────────────────────────────────────────────


def test_dominio_nao_configurado_recusa_cedo(monkeypatch):
    """Sem isto, cada um dos dez conjuntos falharia por conta própria e o log
    da madrugada teria dez tracebacks dizendo a mesma coisa."""
    from app.importacao import dominio

    monkeypatch.setattr(dominio, "configurado", lambda: False)
    assert sincronizar.main([]) == sincronizar.CONFIGURACAO


def test_conjunto_inexistente_recusa_cedo(dominio_ok):
    assert sincronizar.main(["--importador", "nao_existe"]) == sincronizar.CONFIGURACAO


def test_listar_nao_toca_na_origem(monkeypatch):
    """`--listar` é para conferir a instalação — tem de funcionar com o
    Domínio fora."""
    from app.importacao import dominio

    monkeypatch.setattr(dominio, "configurado", lambda: False)
    assert sincronizar.main(["--listar"]) == sincronizar.OK


# ── O que a rodada agendada nunca faz ────────────────────────────────────────


def test_rodada_completa_nunca_inclui_registro_novo(monkeypatch, dominio_ok):
    """Trazer empresa para o Portal é decisão de quem opera, com a lista na
    frente — não de um processo que roda às duas da manhã."""
    from app.importacao import services

    recebido = {}

    def espiao(**kwargs):
        recebido.update(kwargs)
        return rodada(True)

    monkeypatch.setattr(services, "executar_tudo", espiao)
    sincronizar.main([])
    assert "incluir" not in recebido


def test_um_conjunto_so_repassa_empresas_e_simulacao(monkeypatch, dominio_ok):
    from app.importacao import services

    recebido = {}

    def espiao(chave, **kwargs):
        recebido["chave"] = chave
        recebido.update(kwargs)
        return {"lidas": 0, "incluidas": 0, "atualizadas": 0, "ignoradas": 0}

    monkeypatch.setattr(services, "executar", espiao)
    sincronizar.main(["--importador", "empresas", "--empresa", "272", "--dry-run"])

    assert recebido["chave"] == "empresas"
    assert recebido["ids"] == [272]
    assert recebido["dry_run"] is True
    assert recebido["origem"] == services.ORIGEM_CLI


# ── A armadilha do .bat agendado ─────────────────────────────────────────────


def test_o_bat_agendado_nao_tem_pause():
    """`pause` numa tarefa agendada deixaria o processo pendurado esperando
    uma tecla que ninguém vai apertar, e a tarefa só morreria no timeout do
    Agendador — uma madrugada inteira travada, sem nada no log.

    O `abrir-sinc.bat` TEM pause de propósito: lá existe alguém na frente.
    """
    codigo = Path("sincronizar-agendado.bat").read_text(encoding="ascii")
    linhas = [linha for linha in codigo.splitlines() if linha.strip().lower().startswith("pause")]
    assert not linhas


def test_o_bat_agendado_propaga_o_codigo_de_saida():
    """Sem isto, o Agendador mostra sempre 0 e uma falha noturna fica muda."""
    codigo = Path("sincronizar-agendado.bat").read_text(encoding="ascii")
    assert "exit /b %ERRORLEVEL%" in codigo


@pytest.mark.parametrize("bat", ["abrir-sinc.bat", "sincronizar-agendado.bat"])
def test_os_bat_sao_ascii_puro(bat):
    """O `cmd.exe` lê o arquivo na code page ATIVA, e a linha do `chcp` só vale
    para o que vem depois dela. Um travessão no lugar errado vira lixo ou
    quebra o parse."""
    Path(bat).read_text(encoding="ascii")  # levanta se houver byte alto


@pytest.mark.parametrize("bat", ["abrir-sinc.bat", "sincronizar-agendado.bat"])
def test_os_bat_rodam_da_propria_pasta(bat):
    """Sem `cd /d %~dp0`, uma tarefa agendada (que começa em C:\\Windows)
    procuraria o `.env` no lugar errado."""
    assert 'cd /d "%~dp0"' in Path(bat).read_text(encoding="ascii")


# ── O daemon ─────────────────────────────────────────────────────────────────


def test_cron_invalida_sai_como_CONFIGURACAO(dominio_ok):
    """Não como falha genérica: o agendador mostra o número, e `3` diz onde
    mexer. Um traceback subindo até o `cmd.exe` daria um código qualquer."""
    assert sincronizar.main(["--serve", "--cron", "isto nao e cron"]) == sincronizar.CONFIGURACAO


def test_cron_valida_chega_a_agendar(monkeypatch, dominio_ok):
    """Não deixa o `BlockingScheduler` bloquear o teste — só confere que o
    gatilho foi aceito e o trabalho, registrado."""
    registrado = {}

    class AgendadorFalso:
        def __init__(self, **_k):
            pass

        def add_job(self, funcao, gatilho, **kwargs):
            registrado["gatilho"] = gatilho
            registrado["id"] = kwargs.get("id")
            registrado["max_instances"] = kwargs.get("max_instances")

        def start(self):
            registrado["iniciou"] = True

    import apscheduler.schedulers.blocking as blocking

    monkeypatch.setattr(blocking, "BlockingScheduler", AgendadorFalso)

    assert sincronizar.main(["--serve", "--cron", "0 2 * * *"]) == sincronizar.OK
    assert registrado["id"] == "rodada_completa"
    # Uma execução por vez: a trava do PostgreSQL recusaria a segunda, mas com
    # 409 no histórico — ruído para um caso que o agendador evita sozinho.
    assert registrado["max_instances"] == 1
    assert registrado["iniciou"] is True


# ── A porta compartilhada ────────────────────────────────────────────────────


def test_run_py_recusa_subir_com_a_porta_ocupada():
    """No Windows dois processos PODEM ligar na mesma porta, e quem atende cada
    conexão é indefinido.

    O sintoma é cruel: o servidor novo sobe dizendo "Running on …", o navegador
    responde, e quem atende é o processo ANTIGO — com o código velho. Custou
    duas investigações erradas em 22/09/2026.
    """
    import socket

    import run

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as ocupando:
        ocupando.bind(("127.0.0.1", 0))
        ocupando.listen(1)
        porta = ocupando.getsockname()[1]
        assert run._porta_livre(porta) is False

    # Solta a porta: volta a ficar livre.
    assert run._porta_livre(porta) is True


# ── As duas pontas, conferidas antes de começar ──────────────────────────────


def _diagnostico(monkeypatch, origem="ok", destino="ok", schema="ok"):
    from app.health import services as saude

    monkeypatch.setattr(
        saude,
        "diagnosticar",
        lambda: {
            "origem": {"situacao": origem, "mensagem": "origem"},
            "destino": {"situacao": destino, "mensagem": "destino"},
            "schema": {"situacao": schema, "mensagem": "schema"},
            "pronto": origem == "ok" and destino == "ok",
        },
    )


def test_destino_fora_sai_como_CONFIGURACAO(monkeypatch, dominio_ok):
    """Às duas da manhã, a diferença entre "o destino está fora" e dez fases
    falhando em sequência é entre um log que se lê em cinco segundos e dez
    tracebacks dizendo a mesma coisa."""
    _diagnostico(monkeypatch, destino="erro")
    assert sincronizar.main([]) == sincronizar.CONFIGURACAO


def test_origem_fora_sai_como_CONFIGURACAO(monkeypatch, dominio_ok):
    _diagnostico(monkeypatch, origem="erro")
    assert sincronizar.main([]) == sincronizar.CONFIGURACAO


def test_schema_divergente_AVISA_e_deixa_rodar(monkeypatch, dominio_ok, caplog):
    """A mesma decisão de `SCHEMA_REVISAO_ESPERADA`: o caso comum é migration
    aditiva que não afeta nada, e derrubar a operação do cliente por isso seria
    pior que o risco."""
    import logging

    from app.importacao import services

    _diagnostico(monkeypatch, schema="divergente")
    monkeypatch.setattr(services, "executar_tudo", lambda **_k: rodada(True))

    with caplog.at_level(logging.WARNING):
        assert sincronizar.main([]) == sincronizar.OK
    assert "SCHEMA" in caplog.text


def test_o_daemon_NAO_e_derrubado_por_ponta_fora(monkeypatch, dominio_ok):
    """`--serve` fica vivo justamente para tentar de novo amanhã. Derrubá-lo
    porque o banco caiu agora seria trocar uma janela perdida por todas as
    seguintes."""
    _diagnostico(monkeypatch, destino="erro")
    chamou = {}
    monkeypatch.setattr(sincronizar, "servir", lambda _a: chamou.setdefault("sim", True) or 0)
    sincronizar.main(["--serve"])
    assert chamou.get("sim") is True
