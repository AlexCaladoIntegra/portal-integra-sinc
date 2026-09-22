"""Entrypoint da aplicação.

    python run.py

Não há gunicorn e não há produção: este processo roda na máquina do operador,
sempre do mesmo jeito. O servidor do Werkzeug basta — a carga é um operador
numa tela.

## O endereço não é configurável, e é a proteção

O Portal Integra deixa `APP_HOST` no `.env` porque lá existe login e a
exposição é uma decisão de operação. Aqui não: esta aplicação grava no
PostgreSQL de produção do Portal **sem autenticação nenhuma**. A única coisa
entre ela e a rede é escutar só no loopback, e configuração é o que permite
desfazer isso por engano — um `.env` copiado de outra máquina, e o banco do
cliente fica escrevível por quem alcançar a porta.

Por isso `SERVIDOR_HOST` é constante em `app/__init__.py`. Se um dia for
preciso acesso remoto, o passo certo é acrescentar login (reusando a tabela
`usuario` do próprio Portal, já que a conexão existe), não trocar a constante.

## O debugger

`FLASK_DEBUG=true` liga o recarregamento automático e o console interativo do
Werkzeug. Como o servidor só escuta em 127.0.0.1, o console só alcança quem já
está na máquina — que é o mesmo quem já pode ler o `.env`. É seguro aqui, e
não seria se o endereço fosse configurável.
"""

from __future__ import annotations

import socket
import sys

from app import SERVIDOR_HOST, create_app
from app.config import get_settings
from app.data.schema import conferir_no_boot

app = create_app()


def _porta_livre(porta: int) -> bool:
    """A porta está livre?

    **No Windows, dois processos PODEM ligar na mesma porta** — o Werkzeug abre
    o socket com `SO_REUSEADDR`, e ali isso não significa "reusar o endereço
    depois que o anterior fechou", significa "dividir". Qual dos dois atende
    cada conexão é indefinido.

    O sintoma é cruel: o servidor novo sobe dizendo "Running on
    http://127.0.0.1:7820", o navegador responde, e quem atende é o processo
    ANTIGO — com o código velho. Custou duas investigações erradas em
    22/09/2026: uma correção de pool medida contra o servidor que não a tinha.

    `SO_EXCLUSIVEADDRUSE` é a resposta do Windows para isso. Em sistemas que
    não o têm, o `bind` normal já basta.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        exclusivo = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusivo is not None:
            s.setsockopt(socket.SOL_SOCKET, exclusivo, 1)
        try:
            s.bind((SERVIDOR_HOST, porta))
        except OSError:
            return False
    return True


if __name__ == "__main__":
    settings = get_settings()

    if not _porta_livre(settings.app_port):
        print(  # noqa: T201
            f"\n  A porta {settings.app_port} já está em uso — provavelmente por outra\n"
            f"  janela do sincronizador.\n\n"
            f"  Use a que já está aberta, ou feche-a antes. Subir uma segunda faria\n"
            f"  as duas dividirem a porta, e o navegador falaria com qualquer uma\n"
            f"  das duas sem avisar qual.\n",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Depois do create_app, de propósito: `_configurar_logging` já rodou, e o
    # recado sobre o schema precisa sair formatado como as outras linhas.
    # Nunca levanta — ver o corpo da função.
    conferir_no_boot()

    app.run(
        host=SERVIDOR_HOST,
        port=settings.app_port,
        debug=settings.flask_debug,
    )
