"""A API da sincronização: diagnóstico, listagem, execução e a rodada completa.

Nenhum teste abre conexão. O `REGISTRO` é substituído por dublês, e o
diagnóstico e o repositório de histórico são interceptados — é o que permite
exercitar a ordem das fases, a parada na primeira falha e os contratos de
resposta sem PostgreSQL e sem Domínio.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.health import services as health_services
from app.importacao import services
from app.shared.errors import ErroConflito, ErroValidacao


def importador(chave, *, nome=None, resultado=None, erro=None, **extras):
    """Um dublê que cumpre o contrato de `importadores/__init__.py`."""

    def executar(incluir=False, dry_run=False, ids=None):
        executar.chamadas.append({"incluir": incluir, "dry_run": dry_run, "ids": ids})
        if erro is not None:
            raise erro
        return resultado or {"lidas": 1, "incluidas": 0, "atualizadas": 1, "ignoradas": 0}

    executar.chamadas = []
    return SimpleNamespace(
        CHAVE=chave,
        NOME=nome or chave.title(),
        DESCRICAO=f"descrição de {chave}",
        FONTE=f"Domínio · {chave}",
        executar=executar,
        **extras,
    )


@pytest.fixture
def registro(monkeypatch):
    """`REGISTRO` vazio e editável, com o histórico e a trava neutralizados."""
    reg: dict = {}
    monkeypatch.setattr(services, "REGISTRO", reg)

    class RepoFalso:
        linhas: list = []

        def registrar_inicio(self, chave, id_usuario, dry_run, origem):
            RepoFalso.linhas.append(
                {"chave": chave, "id_usuario": id_usuario, "dry_run": dry_run, "origem": origem}
            )
            return len(RepoFalso.linhas)

        def registrar_sucesso(self, *_a, **_k):
            pass

        def registrar_erro(self, *_a, **_k):
            pass

        def expirar_orfas(self, *_a, **_k):
            return 0

        def ultima_execucao(self, *_a, **_k):
            return None

    RepoFalso.linhas = []
    monkeypatch.setattr(services, "ImportacaoRepository", RepoFalso)

    # A trava é do PostgreSQL; aqui ela sempre concede.
    from contextlib import contextmanager

    @contextmanager
    def trava(_nome):
        yield True

    monkeypatch.setattr(services, "trava_de_sessao", trava)
    return reg


def rodar_tudo(client, corpo=None):
    """`POST /tudo` devolve NDJSON: uma linha por evento, `fim` por último.

    Devolve `(eventos, fim)` — os eventos de progresso e o placar final.
    """
    import json

    resposta = client.post("/api/v1/sincronizacao/tudo", json=corpo or {})
    assert resposta.status_code == 200
    assert resposta.mimetype == "application/x-ndjson"

    cru = resposta.get_data(as_text=True).splitlines()
    linhas = [json.loads(linha) for linha in cru if linha.strip()]
    assert linhas[-1]["evento"] == "fim", "a última linha tem de ser o fim"
    return linhas[:-1], linhas[-1]


# ── Diagnóstico ──────────────────────────────────────────────────────────────


def test_diagnostico_responde_as_tres_perguntas(client, monkeypatch):
    monkeypatch.setattr(
        health_services, "_checar_origem", lambda: {"situacao": "ok", "mensagem": "a"}
    )
    monkeypatch.setattr(
        health_services, "_checar_destino", lambda: {"situacao": "ok", "mensagem": "b"}
    )
    monkeypatch.setattr(
        health_services, "_checar_schema", lambda: {"situacao": "ok", "mensagem": "c"}
    )
    dados = client.get("/api/v1/sincronizacao/diagnostico").get_json()["data"]
    assert set(dados) == {"origem", "destino", "schema", "pronto"}
    assert dados["pronto"] is True


def test_schema_divergente_nao_impede_sincronizar(client, monkeypatch):
    """É aviso, não recusa — o caso comum é migration aditiva que não afeta nada.

    Derrubar a operação do cliente por isso seria pior que o risco. Ver
    `SCHEMA_REVISAO_ESPERADA` em `app/config.py`.
    """
    monkeypatch.setattr(
        health_services, "_checar_origem", lambda: {"situacao": "ok", "mensagem": ""}
    )
    monkeypatch.setattr(
        health_services, "_checar_destino", lambda: {"situacao": "ok", "mensagem": ""}
    )
    monkeypatch.setattr(
        health_services, "_checar_schema", lambda: {"situacao": "divergente", "mensagem": ""}
    )
    assert client.get("/api/v1/sincronizacao/diagnostico").get_json()["data"]["pronto"] is True


# ── Listagem ─────────────────────────────────────────────────────────────────


def test_listagem_vazia_e_resposta_valida(client, registro):
    """A Etapa 2 entrega a tela com o REGISTRO vazio: tem de responder, não falhar."""
    corpo = client.get("/api/v1/sincronizacao").get_json()
    assert corpo["data"] == []
    assert "dominio_configurado" in corpo["meta"]


def test_listagem_descreve_o_importador_sem_tocar_no_template(client, registro):
    registro["empresas"] = importador("empresas", nome="Empresas")
    item = client.get("/api/v1/sincronizacao").get_json()["data"][0]
    assert item["chave"] == "empresas"
    assert item["nome"] == "Empresas"
    assert item["tem_pendentes"] is False
    assert item["resumo"] is None


def test_gancho_de_pendentes_muda_o_card(client, registro):
    registro["empresas"] = importador("empresas", pendentes=lambda _busca: [])
    assert client.get("/api/v1/sincronizacao").get_json()["data"][0]["tem_pendentes"] is True


def test_opcoes_nao_existe_no_contrato(client, registro):
    """No Portal `OPCOES` é contrato pela metade: a tela desenha as caixas e a
    rota descarta o valor. Aqui ele não foi copiado — declará-lo num importador
    não pode ressuscitar o controle inerte."""
    registro["x"] = importador("x", OPCOES=({"chave": "a", "rotulo": "A"},))
    assert "opcoes" not in client.get("/api/v1/sincronizacao").get_json()["data"][0]


# ── Execução de um importador ────────────────────────────────────────────────


def test_executar_grava_origem_sinc_e_sem_usuario(client, registro):
    """É `origem` que distingue, no histórico compartilhado, esta execução das
    disparadas no /admin do Portal. `id_usuario` vai nulo: aqui não há login."""
    registro["empresas"] = importador("empresas")
    client.post("/api/v1/sincronizacao/empresas", json={})
    linha = services.ImportacaoRepository.linhas[-1]
    assert linha["origem"] == "sinc"
    assert linha["id_usuario"] is None


def test_chave_inexistente_da_404(client, registro):
    assert client.post("/api/v1/sincronizacao/nao_existe", json={}).status_code == 404


def test_id_booleano_e_recusado(client, registro):
    """Em Python `bool` É `int`, então `[true]` no JSON virava o id `1` — a
    empresa 1, importada sem ninguém ter pedido. Erro de cliente que produzia
    trabalho plausível."""
    registro["empresas"] = importador("empresas")
    resposta = client.post("/api/v1/sincronizacao/empresas", json={"ids": [True]})
    assert resposta.status_code == 422


def test_execucao_concorrente_da_409(client, registro, monkeypatch):
    """A trava é do PostgreSQL e vale entre este processo e o Portal."""
    from contextlib import contextmanager

    @contextmanager
    def ocupada(_nome):
        yield False

    monkeypatch.setattr(services, "trava_de_sessao", ocupada)
    registro["empresas"] = importador("empresas")
    assert client.post("/api/v1/sincronizacao/empresas", json={}).status_code == 409


def test_erro_do_importador_e_repropagado(client, registro):
    """Quem chamou precisa saber que não importou — o histórico registra e o
    erro sobe."""
    registro["empresas"] = importador("empresas", erro=ErroValidacao("origem fora"))
    resposta = client.post("/api/v1/sincronizacao/empresas", json={})
    assert resposta.status_code == 422
    assert resposta.get_json()["error"]["message"] == "origem fora"


# ── A rodada completa ────────────────────────────────────────────────────────


def test_tudo_com_registro_vazio_conclui_zerado(client, registro):
    """O critério de aceite da Etapa 2: o mecanismo roda antes de existir dado."""
    _, dados = rodar_tudo(client)
    assert dados["concluido"] is True
    assert dados["fases"] == []
    assert dados["total"] == {"lidas": 0, "incluidas": 0, "atualizadas": 0, "ignoradas": 0}


def test_tudo_respeita_a_ordem_do_registro(client, registro):
    """A ordem é de DEPENDÊNCIA: saldos antes do plano de contas viola FK."""
    ordem: list[str] = []
    for chave in ("empresas", "contabil_plano", "contabil_saldos"):
        mod = importador(chave)
        original = mod.executar

        def executar(incluir=False, dry_run=False, ids=None, _c=chave, _o=original):
            ordem.append(_c)
            return _o(incluir=incluir, dry_run=dry_run, ids=ids)

        mod.executar = executar
        registro[chave] = mod

    rodar_tudo(client)
    assert ordem == ["empresas", "contabil_plano", "contabil_saldos"]


def test_tudo_para_na_primeira_falha(client, registro):
    """Seguir depois de uma falha produz o pior resultado deste domínio:
    `contabil_saldos` sobre um plano que não atualizou grava saldo em conta que
    já não existe, e o descarte acontece em silêncio."""
    registro["empresas"] = importador("empresas")
    registro["contabil_plano"] = importador("contabil_plano", erro=ErroConflito("travado"))
    registro["contabil_saldos"] = importador("contabil_saldos")

    _, dados = rodar_tudo(client)

    assert dados["concluido"] is False
    assert [f["chave"] for f in dados["fases"]] == ["empresas", "contabil_plano"]
    assert dados["fases"][-1]["status"] == "erro"
    # A que nem chegou a rodar não aparece, e não foi chamada.
    assert registro["contabil_saldos"].executar.chamadas == []


def test_tudo_nunca_inclui_registro_novo(client, registro):
    """Incluir é decisão de quem opera, com a lista na frente. Uma rodada em
    lote — ainda mais agendada de madrugada — atualiza o que já existe."""
    registro["empresas"] = importador("empresas")
    rodar_tudo(client)
    assert registro["empresas"].executar.chamadas[0]["incluir"] is False


def test_tudo_soma_o_placar_das_fases(client, registro):
    registro["a"] = importador(
        "a", resultado={"lidas": 10, "incluidas": 1, "atualizadas": 2, "ignoradas": 7}
    )
    registro["b"] = importador(
        "b", resultado={"lidas": 5, "incluidas": 0, "atualizadas": 5, "ignoradas": 0}
    )
    total = rodar_tudo(client)[1]["total"]
    assert total == {"lidas": 15, "incluidas": 1, "atualizadas": 7, "ignoradas": 7}


def test_tudo_propaga_dry_run(client, registro):
    registro["a"] = importador("a")
    rodar_tudo(client, {"dry_run": True})
    assert registro["a"].executar.chamadas[0]["dry_run"] is True


# ── O aviso de duração só vale para a tela ───────────────────────────────────


def _demorado(chave, segundos=30):
    """Um dublê que mente sobre o relógio, para não deixar o teste lento."""
    import time

    relogio = iter([0.0, float(segundos)])
    return chave, relogio, time


def test_rodada_longa_pela_CLI_nao_emite_aviso_de_duracao(client, registro, monkeypatch, caplog):
    """Uma rodada completa levou 24 minutos e emitiu SEIS avisos destes.

    Pela CLI não há requisição HTTP a segurar: a demora é o trabalho. Log
    noturno cheio de WARNING inócuo treina quem lê a ignorar WARNING — e o
    próximo será o que importava.
    """
    import logging

    registro["empresas"] = importador("empresas")
    monkeypatch.setattr(services.time, "monotonic", iter([0.0, 999.0]).__next__)

    with caplog.at_level(logging.WARNING):
        services.executar("empresas", origem=services.ORIGEM_CLI)

    assert "avaliar execução em segundo plano" not in caplog.text


def test_rodada_longa_pela_TELA_ainda_avisa(client, registro, monkeypatch, caplog):
    """Ali o aviso continua sendo sintoma: a execução segura o navegador."""
    import logging

    registro["empresas"] = importador("empresas")
    monkeypatch.setattr(services.time, "monotonic", iter([0.0, 999.0]).__next__)

    with caplog.at_level(logging.WARNING):
        services.executar("empresas", origem=services.ORIGEM_PAINEL)

    assert "avaliar execução em segundo plano" in caplog.text


# ── O progresso por fase ─────────────────────────────────────────────────────


def test_cada_fase_anuncia_inicio_e_fim(client, registro):
    """É o que a tela desenha. Sem o `iniciou`, quem espera 24 minutos não sabe
    em que pé está — e uma execução longa fica indistinguível de uma travada."""
    registro["empresas"] = importador("empresas", nome="Empresas")
    registro["contabil_plano"] = importador("contabil_plano", nome="Plano")

    eventos, fim = rodar_tudo(client)

    assert [(e["evento"], e["chave"]) for e in eventos] == [
        ("iniciou", "empresas"),
        ("concluiu", "empresas"),
        ("iniciou", "contabil_plano"),
        ("concluiu", "contabil_plano"),
    ]
    assert fim["concluido"] is True


def test_o_progresso_diz_quantas_fases_faltam(client, registro):
    """`indice` e `de` são o que permite desenhar "3 de 10" sem o cliente
    precisar conhecer o REGISTRO."""
    for chave in ("a", "b", "c"):
        registro[chave] = importador(chave)

    eventos, _ = rodar_tudo(client)
    inicios = [e for e in eventos if e["evento"] == "iniciou"]
    assert [(e["indice"], e["de"]) for e in inicios] == [(1, 3), (2, 3), (3, 3)]


def test_a_fase_que_falha_anuncia_o_erro_e_a_rodada_para(client, registro):
    registro["a"] = importador("a")
    registro["b"] = importador("b", erro=ErroConflito("travado"))
    registro["c"] = importador("c")

    eventos, fim = rodar_tudo(client)

    concluidas = [e for e in eventos if e["evento"] == "concluiu"]
    assert [(e["chave"], e["status"]) for e in concluidas] == [("a", "sucesso"), ("b", "erro")]
    assert concluidas[-1]["erro"] == "travado"
    assert fim["concluido"] is False
    # A terceira nem foi anunciada: ela não chegou a rodar.
    assert not any(e["chave"] == "c" for e in eventos)


def test_gancho_de_progresso_que_explode_nao_derruba_a_rodada(client, registro, monkeypatch):
    """Apresentar progresso não pode custar o trabalho."""
    registro["a"] = importador("a")

    def gancho_quebrado(_evento):
        raise RuntimeError("cliente sumiu")

    resultado = services.executar_tudo(progresso=gancho_quebrado)
    assert resultado["concluido"] is True


def test_o_status_http_e_200_mesmo_quando_a_rodada_falha(client, registro):
    """O cabeçalho sai antes de a primeira fase terminar — não há como voltar
    atrás. Quem consome decide pelo evento `fim`."""
    registro["a"] = importador("a", erro=ErroConflito("travado"))
    resposta = client.post("/api/v1/sincronizacao/tudo", json={})
    assert resposta.status_code == 200


# ── O recorte por empresa ────────────────────────────────────────────────────


def test_tudo_sem_ids_roda_o_parque(client, registro):
    registro["a"] = importador("a")
    rodar_tudo(client)
    assert registro["a"].executar.chamadas[0]["ids"] is None


def test_tudo_com_empresa_recorta_TODOS_os_conjuntos(client, registro):
    """É o lookup da tela: escolhida uma empresa, os dez conjuntos rodam só
    para ela. Recortar a metade seria pior que não recortar — o BI ficaria com
    uma empresa atualizada num conjunto e velha no outro."""
    for chave in ("a", "b", "c"):
        registro[chave] = importador(chave)

    rodar_tudo(client, {"ids": [272]})

    for chave in ("a", "b", "c"):
        assert registro[chave].executar.chamadas[0]["ids"] == [272]


def test_id_booleano_e_recusado_ANTES_de_comecar_a_transmitir(client, registro):
    """Uma vez transmitindo, o status já foi 200 e não há como devolver 422.

    Em Python `bool` É `int`, então `[true]` viraria o id `1` — a empresa 1,
    sincronizada sem ninguém ter pedido.
    """
    registro["a"] = importador("a")
    resposta = client.post("/api/v1/sincronizacao/tudo", json={"ids": [True]})
    assert resposta.status_code == 422
    assert registro["a"].executar.chamadas == []
