"""Regra de negócio da importação de dados.

Cópia do `services.py` do Portal Integra. Toda execução passa por
`executar()` e fica registrada no histórico — que é a MESMA tabela
`importacao_execucao` do Portal, gravada com `origem='sinc'`.

**Execução síncrona**, por decisão consciente: a importação de empresas lê 917
linhas e grava algumas centenas em menos de um segundo, e resolver isso com
fila de jobs hoje seria infraestrutura sem problema. O limite para revisar essa
decisão está em `DURACAO_LIMITE_SEGUNDOS`, e ele **só se aplica à tela** — ver o
comentário lá.
"""

from __future__ import annotations

import logging
import time

from ..data.connection import trava_de_sessao
from ..shared.errors import ErroApp, ErroConflito, ErroNaoEncontrado, ErroValidacao
from . import assinatura
from .importadores import REGISTRO
from .repositories import ImportacaoRepository, empresas_ativas

logger = logging.getLogger(__name__)

ORIGEM_PAINEL = "painel"
ORIGEM_CLI = "cli"
# A origem deste projeto. A coluna `origem` é VARCHAR(20) sem CHECK — só
# `status` tem —, então o valor novo não exige migration. É o que faz as
# execuções daqui aparecerem no /admin do Portal distinguidas das de lá.
ORIGEM_SINC = "sinc"

# Acima disto, uma execução disparada PELA TELA segura a requisição HTTP tempo
# demais, e o importador deveria migrar para segundo plano.
#
# **Só vale para a tela**, e a distinção não é detalhe. Rodando pela CLI ou pelo
# agendador não existe requisição a segurar: a primeira rodada completa levou 24
# minutos e emitiu SEIS avisos destes, todos dizendo o óbvio sobre um trabalho
# que é longo por natureza. Log noturno cheio de WARNING inócuo treina quem lê a
# ignorar WARNING — e o próximo será o que importava.
DURACAO_LIMITE_SEGUNDOS = 20

# As origens em que a duração é sintoma. `ORIGEM_CLI` e `ORIGEM_SINC` ficam de
# fora: lá a demora é o trabalho, não um problema.
ORIGENS_COM_LIMITE_DE_DURACAO = frozenset({ORIGEM_PAINEL})

# Depois disto, uma linha em `executando` é considerada abandonada.
#
# No Portal o número tinha de ser maior que o `--timeout` do gunicorn. Aqui não
# há gunicorn — e o limite ficou o MESMO de propósito: as duas aplicações
# escrevem na mesma tabela, e um limite menor aqui encerraria o registro de uma
# importação que ainda está viva do lado do Portal.
LIMITE_DE_ORFA_SEGUNDOS = 900


def listar() -> list[dict]:
    """Importadores disponíveis, cada um com a sua última execução real.

    É o que a tela renderiza: acrescentar importador no REGISTRO faz ele
    aparecer no painel sem tocar em template nem em rota.
    """
    repo = ImportacaoRepository()
    # Encerra o registro de execução abandonada ANTES de montar os cards: é esta
    # listagem que mostra "em execução", e sem a varredura ela mostraria isso
    # para sempre depois de um worker morto.
    expiradas = repo.expirar_orfas(LIMITE_DE_ORFA_SEGUNDOS)
    if expiradas:
        logger.warning("%s execução(ões) de importação abandonada(s) encerrada(s)", expiradas)

    return [
        {
            "chave": modulo.CHAVE,
            "nome": modulo.NOME,
            "descricao": modulo.DESCRICAO,
            "fonte": modulo.FONTE,
            # `resumo()` é opcional: descreve o estado local do que aquele
            # importador alimenta, para o card explicar o próprio resultado.
            "resumo": modulo.resumo() if hasattr(modulo, "resumo") else None,
            # Importador que sabe dizer o que falta importar ganha, na tela,
            # a lista com seleção em vez de campo de códigos.
            "tem_pendentes": hasattr(modulo, "pendentes"),
            "ultima_execucao": repo.ultima_execucao(modulo.CHAVE),
        }
        for modulo in REGISTRO.values()
    ]


def _importador(chave: str):
    modulo = REGISTRO.get(chave)
    if modulo is None:
        raise ErroNaoEncontrado(f"Importador '{chave}' não existe.")
    return modulo


def executar(
    chave: str,
    *,
    incluir: bool = False,
    dry_run: bool = False,
    ids: list[int] | None = None,
    id_usuario: int | None = None,
    origem: str = ORIGEM_PAINEL,
) -> dict:
    """Roda um importador, registrando início, resultado ou falha.

    Devolve as contagens mais o id da execução. Erro do importador é
    registrado no histórico e **repropagado** — quem chamou precisa saber que
    não importou.
    """
    modulo = _importador(chave)
    # `bool` sai explicitamente: em Python ele É um `int`, então `[true]` no
    # JSON passava na checagem e virava o id `1` — a empresa 1, importada sem
    # ninguém ter pedido. Erro de cliente que produzia trabalho plausível.
    if ids is not None and not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise ErroValidacao("A lista de ids deve conter apenas números inteiros.")

    # Uma execução por importador. A trava é do PostgreSQL, não do processo —
    # e o NOME é idêntico ao do Portal de propósito: como os dois apontam para o
    # mesmo banco, uma importação disparada no /admin do Portal e uma
    # sincronização daqui não rodam juntas. Isso é desejado, não acidente.
    #
    # O cenário não é teórico: até o SEG-001 a importação de lançamentos era
    # morta aos 30 s, o administrador via o erro e clicava de novo — e a segunda
    # entrava sobre os locks de linha que o `_apagar_janela` da primeira ainda
    # mantinha, com as duas apagando a mesma janela e inserindo as mesmas
    # chaves. Com o timeout corrigido a janela diminuiu, e não fechou.
    with trava_de_sessao(f"importacao:{chave}") as livre:
        if not livre:
            raise ErroConflito(
                f"A importação '{modulo.NOME}' já está em execução. "
                "Aguarde a conclusão e veja o resultado no histórico."
            )
        return _executar_travado(
            modulo,
            chave,
            incluir=incluir,
            dry_run=dry_run,
            ids=ids,
            id_usuario=id_usuario,
            origem=origem,
        )


def _executar_travado(
    modulo,
    chave: str,
    *,
    incluir: bool,
    dry_run: bool,
    ids: list[int] | None,
    id_usuario: int | None,
    origem: str,
) -> dict:
    """O corpo da execução, já com a trava em mãos.

    Separado de `executar` só para o `with` da trava não empurrar o corpo inteiro
    um nível para dentro — a leitura do caminho de erro é a parte que mais
    importa aqui.
    """
    repo = ImportacaoRepository()
    id_execucao = repo.registrar_inicio(chave, id_usuario, dry_run, origem)
    inicio = time.monotonic()

    # SINC-006, Fase 0: mede quantas empresas estariam inalteradas e REGISTRA
    # NO LOG — sem pular nenhuma. Depois de uma semana de operação normal, o
    # log responde se ligar o pulo (Fase 1) vale a pena.
    #
    # Depois do `inicio`, de propósito: a medição entra na duração gravada no
    # histórico, porque é custo real da execução. Deixá-la fora faria a Fase 1
    # parecer um ganho maior do que é.
    assinaturas = assinatura.observar(chave, ids)

    try:
        contagens = modulo.executar(incluir=incluir, dry_run=dry_run, ids=ids)
    except ErroApp as exc:
        # Erro previsto (validação, origem indisponível): registra e avisa, sem
        # traceback. O detalhe técnico, quando existe, já foi logado na origem.
        repo.registrar_erro(id_execucao, f"{type(exc).__name__}: {exc}")
        logger.warning("Importação '%s' recusada: %s", chave, exc)
        raise
    except Exception as exc:
        repo.registrar_erro(id_execucao, f"{type(exc).__name__}: {exc}")
        logger.exception("Importação '%s' falhou", chave)
        raise

    duracao = time.monotonic() - inicio
    repo.registrar_sucesso(id_execucao, contagens)

    # Só depois do sucesso, e nunca em simulação: o checkpoint diz "isto já foi
    # gravado no Portal". Registrá-lo num `dry_run` faria a próxima execução
    # real acreditar que já tinha sincronizado o que ninguém gravou — o pior
    # defeito possível deste mecanismo, porque some dado em silêncio.
    if not dry_run:
        assinatura.registrar(chave, assinaturas)

    if origem in ORIGENS_COM_LIMITE_DE_DURACAO and duracao > DURACAO_LIMITE_SEGUNDOS:
        logger.warning(
            "Importação '%s' levou %.1fs (limite %ss) — avaliar execução em segundo plano.",
            chave,
            duracao,
            DURACAO_LIMITE_SEGUNDOS,
        )

    return {**contagens, "id_execucao": id_execucao, "duracao_segundos": round(duracao, 2)}


def pendentes(
    chave: str, busca: str | None = None, limit: int = 200, offset: int = 0
) -> tuple[list[dict], int]:
    """Registros da origem que ainda não estão no portal, paginados.

    A leitura da origem acontece a cada página — no volume atual (917 empresas
    em ~120 ms) isso é mais simples e mais correto que manter cache, que
    envelheceria justamente enquanto alguém importa.
    """
    modulo = _importador(chave)
    if not hasattr(modulo, "pendentes"):
        raise ErroNaoEncontrado(f"O importador '{chave}' não lista pendências.")

    itens = modulo.pendentes(busca)
    return itens[offset : offset + limit], len(itens)


def empresas(busca: str | None = None, limit: int = 200, offset: int = 0) -> tuple[list[dict], int]:
    """As empresas ativas do Portal — o lookup que recorta a rodada completa."""
    itens = empresas_ativas(busca)
    return itens[offset : offset + limit], len(itens)


def historico(chave: str | None = None, limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    repo = ImportacaoRepository()
    if chave:
        _importador(chave)  # 404 para chave inexistente
    return repo.historico(chave, limit, offset), repo.contar_historico(chave)


def executar_tudo(
    *,
    dry_run: bool = False,
    origem: str = ORIGEM_SINC,
    progresso=None,
    ids: list[int] | None = None,
) -> dict:
    """Roda o REGISTRO inteiro, na ordem, e **para na primeira falha**.

    É o que o botão "Sincronizar tudo" dispara, e o que o agendamento vai
    chamar depois da Etapa 5.

    ## Por que parar, e não seguir com as demais

    A ordem do REGISTRO é de **dependência**. Seguir depois de uma falha
    produziria o pior resultado possível deste domínio: `contabil_saldos`
    rodando sobre um plano de contas que `contabil_plano` não conseguiu
    atualizar grava saldo em conta que já não existe — e o descarte acontece
    em silêncio, com o BI mostrando número plausível e menor. Um erro no meio
    de uma cadeia de dependência não é "uma fase que falhou": é todo o resto
    ficando suspeito.

    ## `ids` recorta a rodada a algumas empresas

    É o lookup da tela: com uma empresa escolhida, os dez conjuntos rodam só
    para ela. Cada importador aplica o recorte à sua maneira e os dois globais
    — `fiscal_cadastros` e, em parte, `empresas` — o ignoram por natureza, o
    que é o comportamento certo: espécie e CFOP não têm dono.

    Sem `ids`, roda o parque inteiro, como antes.

    ## `incluir` é sempre False, e não é configurável aqui

    Incluir registro novo é decisão de quem opera, tomada com a lista na
    frente — e no caso de `empresas` o próprio importador recusa `incluir` sem
    `ids` justamente para isso. Uma rodada em lote, ainda mais agendada de
    madrugada, atualiza o que já existe. O que entra no Portal entra pelo card,
    um a um.

    ## `progresso` — quem está esperando precisa saber em que pé está

    Chamado ao ENTRAR e ao SAIR de cada fase, com um dicionário de evento. A
    rodada completa levou 24 minutos medidos; sem isto, tanto a tela quanto o
    log noturno ficam mudos do começo ao fim e uma execução longa é
    indistinguível de uma travada.

    É um gancho, e não um `yield`, de propósito: a regra de ordem e de parada
    fica num lugar só, e cada chamador a apresenta como quiser — a tela
    transmite os eventos, a CLI os escreve no log.

    Nunca deixa uma exceção do gancho derrubar a sincronização: apresentar
    progresso não pode custar o trabalho.

    Devolve o placar por fase mais o agregado. A fase que falhou vem com
    `status='erro'` e a mensagem; as que nem chegaram a rodar não aparecem.
    """
    fases: list[dict] = []
    total = {"lidas": 0, "incluidas": 0, "atualizadas": 0, "ignoradas": 0}
    quantas = len(REGISTRO)

    def avisar(evento: dict) -> None:
        if progresso is None:
            return
        try:
            progresso(evento)
        except Exception:
            logger.warning("Gancho de progresso falhou; a rodada segue.", exc_info=True)

    for indice, (chave, modulo) in enumerate(REGISTRO.items(), 1):
        avisar(
            {
                "evento": "iniciou",
                "chave": chave,
                "nome": modulo.NOME,
                "indice": indice,
                "de": quantas,
            }
        )
        try:
            resultado = executar(chave, incluir=False, dry_run=dry_run, origem=origem, ids=ids)
        except ErroApp as exc:
            fase = {"chave": chave, "nome": modulo.NOME, "status": "erro", "erro": exc.message}
            fases.append(fase)
            avisar({"evento": "concluiu", "indice": indice, "de": quantas, **fase})
            logger.warning("Rodada interrompida em '%s': %s", chave, exc)
            return {"fases": fases, "total": total, "concluido": False}
        except Exception as exc:
            fase = {"chave": chave, "nome": modulo.NOME, "status": "erro", "erro": str(exc)}
            fases.append(fase)
            avisar({"evento": "concluiu", "indice": indice, "de": quantas, **fase})
            logger.exception("Rodada interrompida em '%s'", chave)
            return {"fases": fases, "total": total, "concluido": False}

        fase = {"chave": chave, "nome": modulo.NOME, "status": "sucesso", **resultado}
        fases.append(fase)
        avisar({"evento": "concluiu", "indice": indice, "de": quantas, **fase})
        for medida in total:
            total[medida] += resultado.get(medida, 0)

    return {"fases": fases, "total": total, "concluido": True}
