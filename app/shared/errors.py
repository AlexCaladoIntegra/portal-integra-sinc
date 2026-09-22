"""Exceções de aplicação — o vocabulário de erro compartilhado.

Cada camada levanta a exceção que descreve o problema em termos de domínio; a
tradução para HTTP acontece num único lugar (`_registrar_erros` na factory).
Nenhuma rota precisa de try/except para o caminho de erro esperado.

Erros específicos de um módulo devem herdar destes — por exemplo:

    class ClienteNaoEncontrado(ErroNaoEncontrado):
        code = "cliente_nao_encontrado"
"""

from __future__ import annotations

from typing import Any


class ErroApp(Exception):
    """Base de todo erro tratado da aplicação.

    `code` é o identificador estável consumido pelo frontend (não traduza);
    `message` é o texto exibido ao usuário; `details` carrega o detalhamento
    estruturado (ex.: erros por campo numa validação).
    """

    code: str = "erro_interno"
    status: int = 500
    message: str = "Erro interno."

    def __init__(self, message: str | None = None, *, details: Any = None) -> None:
        self.message = message or type(self).message
        self.details = details
        super().__init__(self.message)


class ErroValidacao(ErroApp):
    code = "validacao"
    status = 422
    message = "Dados inválidos."


class ErroNaoEncontrado(ErroApp):
    code = "nao_encontrado"
    status = 404
    message = "Registro não encontrado."


class ErroConflito(ErroApp):
    code = "conflito"
    status = 409
    message = "A operação conflita com o estado atual do registro."


class ErroSemPermissao(ErroApp):
    code = "sem_permissao"
    status = 403
    message = "Você não tem permissão para esta ação."


class ErroNaoAutenticado(ErroApp):
    code = "nao_autenticado"
    status = 401
    message = "Autenticação necessária."


class ErroIndisponivel(ErroApp):
    code = "indisponivel"
    status = 503
    message = "Serviço temporariamente indisponível."
