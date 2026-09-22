"""Endpoints da sincronização — consumidos pela tela.

Espelha `app/importacao/routes.py` do Portal, com três diferenças:

- **sem `@login_required` / `@admin_required`**: aqui não há usuário. A
  proteção é o servidor escutar só em 127.0.0.1 (ver `app/__init__.py`);
- **`/diagnostico`**, que a tela consulta antes de liberar o botão;
- **`/tudo`**, a rodada completa na ordem do REGISTRO.

Zero regra de negócio: cada rota lê o `request`, chama o service e devolve.
"""

from __future__ import annotations

import json
import logging
import queue
import threading

from flask import Blueprint, Response, request, stream_with_context

from ..health import services as health_services
from ..shared.errors import ErroValidacao
from ..shared.pagination import Paginacao
from ..shared.responses import ok
from . import dominio, services

importacao_bp = Blueprint("importacao", __name__, url_prefix="/api/v1/sincronizacao")
logger = logging.getLogger(__name__)


@importacao_bp.get("")
def listar():
    """Importadores disponíveis, com a última execução de cada um."""
    return ok(
        services.listar(),
        meta={"dominio_configurado": dominio.configurado()},
    )


@importacao_bp.get("/diagnostico")
def diagnostico():
    """As duas pontas e o schema do destino.

    Rota própria, e não um campo no `meta` de `listar()`: ela abre conexão com
    as duas pontas, e a listagem é chamada depois de toda execução para
    atualizar os cards. Junto, cada sincronização pagaria um handshake ODBC
    a mais só para redesenhar um card que não mudou.
    """
    return ok(health_services.diagnosticar())


@importacao_bp.get("/empresas")
def empresas():
    """As empresas ativas do Portal — o lookup que recorta a rodada completa.

    Vem daqui, e não de `/<chave>/pendentes`: aquela lista o que a ORIGEM tem
    e o Portal não. Esta lista o que o Portal TEM, que é o universo do que se
    pode sincronizar.
    """
    pag = Paginacao.de_request()
    itens, total = services.empresas(request.args.get("busca"), limit=pag.limit, offset=pag.offset)
    return ok(itens, meta=pag.meta(total))


@importacao_bp.post("/tudo")
def executar_tudo():
    """A rodada completa, na ordem do REGISTRO, parando na primeira falha.

    Corpo (opcional): `dry_run` apenas simula; `ids` recorta a rodada às
    empresas informadas — é o lookup da tela.

    ## Responde em NDJSON, e não no envelope

    É a **única** rota do projeto fora do contrato `{data: …}`, e o motivo é a
    duração: a rodada completa levou 24 minutos medidos. Uma resposta única
    deixaria a tela dizendo "Sincronizando…" por 24 minutos, indistinguível de
    uma execução travada — e quem espera clica de novo, que é o que a trava de
    sessão do PostgreSQL recusa com 409.

    Cada linha é um evento JSON, transmitido quando acontece:

        {"evento":"iniciou",  "chave":"…", "nome":"…", "indice":3, "de":10}
        {"evento":"concluiu", "chave":"…", "status":"sucesso", "lidas":…}
        {"evento":"fim",      "concluido":true, "total":{…}, "fases":[…]}

    **O status HTTP é sempre 200**, inclusive quando a rodada falha: o cabeçalho
    sai antes de a primeira fase terminar. Quem consome decide pelo evento
    `fim`, e a ausência dele significa conexão interrompida — não sucesso.
    """
    dados = request.get_json(silent=True) or {}
    dry_run = bool(dados.get("dry_run"))
    ids = dados.get("ids")
    # A MESMA validação de `services.executar`, aplicada antes de a resposta
    # começar a sair: uma vez transmitindo, o status já foi 200 e não há como
    # devolver 422.
    if ids is not None and not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise ErroValidacao("A lista de ids deve conter apenas números inteiros.")

    def transmitir():
        """Ponte entre o gancho de progresso e a resposta.

        `executar_tudo` é uma função comum que CHAMA o gancho; um gerador
        precisa que os eventos venham até ele. A fila faz essa volta: a
        sincronização roda numa thread e empurra, este gerador puxa e emite.

        Acumular numa lista e emitir no fim seria mais simples e não
        transmitiria nada — a tela receberia os dez eventos de uma vez, 24
        minutos depois, que é o problema que esta rota existe para resolver.

        A thread não recebe o contexto do Flask, e não precisa: nada abaixo de
        `services` conhece `request` ou `current_app` — o pool do PostgreSQL é
        `ThreadedConnectionPool` e a trava de sessão pega conexão própria.
        """
        fila: queue.Queue = queue.Queue()
        acabou = object()
        saida: dict = {}

        def trabalhar():
            try:
                saida["dados"] = services.executar_tudo(
                    dry_run=dry_run,
                    progresso=fila.put,
                    origem=services.ORIGEM_SINC,
                    ids=ids,
                )
            except Exception as exc:  # a rodada já trata ErroApp; isto é a rede de baixo
                logger.exception("Rodada completa falhou")
                saida["erro"] = str(exc)
            finally:
                fila.put(acabou)

        trabalhador = threading.Thread(target=trabalhar, name="rodada_completa", daemon=True)
        trabalhador.start()

        while True:
            evento = fila.get()
            if evento is acabou:
                break
            yield json.dumps(evento, default=str) + "\n"

        trabalhador.join()
        if "erro" in saida:
            fim = {"concluido": False, "fases": [], "total": {}, "erro": saida["erro"]}
        else:
            fim = saida["dados"]
        yield json.dumps({"evento": "fim", **fim}, default=str) + "\n"

    return Response(stream_with_context(transmitir()), mimetype="application/x-ndjson")


@importacao_bp.post("/<chave>")
def executar(chave: str):
    """Executa um importador.

    Corpo (opcional): `incluir` traz os registros novos, `dry_run` apenas
    simula, `ids` restringe a alguns registros da origem.

    `id_usuario` vai sempre nulo: aqui não há login. Quem distingue esta
    execução das do Portal no histórico compartilhado é `origem='sinc'`.
    """
    dados = request.get_json(silent=True) or {}
    resultado = services.executar(
        chave,
        incluir=bool(dados.get("incluir")),
        dry_run=bool(dados.get("dry_run")),
        ids=dados.get("ids"),
        id_usuario=None,
        origem=services.ORIGEM_SINC,
    )
    return ok(resultado)


@importacao_bp.get("/<chave>/pendentes")
def pendentes(chave: str):
    """O que a origem tem e o Portal ainda não — a lista que a tela oferece
    para marcar. Já exclui o que está cadastrado, então não repete registro."""
    pag = Paginacao.de_request()
    itens, total = services.pendentes(
        chave, request.args.get("busca"), limit=pag.limit, offset=pag.offset
    )
    return ok(itens, meta=pag.meta(total))


@importacao_bp.get("/historico")
def historico():
    """Histórico paginado, incluindo simulações e execuções com erro.

    Traz as linhas das DUAS origens: as do /admin do Portal e as daqui. É
    proposital — quem investiga uma divergência precisa ver a sequência
    inteira, não a metade que este processo escreveu.
    """
    pag = Paginacao.de_request()
    itens, total = services.historico(request.args.get("chave"), limit=pag.limit, offset=pag.offset)
    return ok(itens, meta=pag.meta(total))
