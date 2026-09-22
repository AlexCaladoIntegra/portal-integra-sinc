"""Factory da aplicação Flask do Portal Integra Sinc.

A factory apenas monta a aplicação: configuração, cabeçalhos de segurança,
blueprints e tratamento de erro. Regra de negócio nunca mora aqui.

É a factory do Portal Integra, podada. O que saiu e por quê:

- **Flask-Login e Flask-WTF.** Esta aplicação não tem usuário nem formulário;
  a proteção é escutar só em `127.0.0.1` (ver `SERVIDOR_HOST` abaixo).
- **ProxyFix.** Não há nginx à frente. O `X-Forwarded-Proto` que ele lê seria
  aceito de quem quer que alcançasse a porta, o que é o oposto de proteção.
- **A trava de produção.** Não há `FLASK_ENV` aqui: não existe "produção" para
  este processo — ele roda na máquina do operador, sempre do mesmo jeito.

O que ficou, e é o que dá a cara e a segurança de base: nonce de CSP por
request, os cabeçalhos de segurança, o `?v=<mtime>` dos estáticos, o
`_quer_json()` e os handlers de erro.
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, g, render_template, request
from werkzeug.exceptions import HTTPException

from .config import Settings, get_settings
from .shared.errors import ErroApp
from .shared.responses import erro

logger = logging.getLogger(__name__)

# Status com página de erro própria em app/templates/errors/.
STATUS_COM_PAGINA = (400, 403, 404, 500)

# A interface em que o servidor escuta. É constante, e não configuração, de
# propósito: este processo grava no PostgreSQL de produção do Portal SEM
# autenticação nenhuma. A única coisa entre ele e a rede é este endereço.
#
# No Portal, `app_host` é configurável porque lá existe login e a exposição é
# uma decisão de operação. Aqui, torná-la configurável seria oferecer um jeito
# de desfazer a proteção por engano — uma linha no `.env` copiada de outra
# máquina, e o banco do cliente fica escrevível por quem alcançar a porta.
#
# Se um dia for preciso acesso remoto, o passo certo é acrescentar login
# (reusando a tabela `usuario` do próprio Portal, já que a conexão existe),
# não trocar esta constante.
SERVIDOR_HOST = "127.0.0.1"


def _quer_json() -> bool:
    """Decide entre resposta JSON e página HTML.

    O default é HTML: `Accept` ausente ou genérico tem de cair na página,
    senão um acesso direto pelo navegador recebe o envelope de erro cru. JSON
    só quando o caminho é de API, o corpo veio em JSON, ou o cliente pediu
    JSON e explicitamente não aceita HTML.
    """
    if request.path.startswith(("/api/", "/health")):
        return True
    if request.is_json:
        return True
    aceita = request.accept_mimetypes
    return aceita.accept_json and not aceita.accept_html


def create_app(settings: Settings | None = None) -> Flask:
    """Cria a aplicação. `settings` permite injetar configuração nos testes."""
    settings = settings or get_settings()

    app = Flask(__name__)
    app.extensions["settings"] = settings

    app.config.update(
        # Efêmera por processo, e é o certo aqui: não há sessão, cookie
        # assinado nem flash para sobreviver a um restart. O Flask só exige a
        # chave para assinar a sessão; gerá-la elimina um segredo do `.env` que
        # ninguém usaria e que, esquecido em "change-me", daria falsa sensação
        # de configuração feita.
        SECRET_KEY=secrets.token_hex(32),
        DEBUG=settings.flask_debug,
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
        # Cache longo dos estáticos: seguro porque toda URL de static carrega
        # ?v=<mtime> (ver abaixo) — arquivo novo, URL nova.
        SEND_FILE_MAX_AGE_DEFAULT=timedelta(days=30),
        APP_NAME=settings.app_name,
        APP_VERSION=settings.app_version,
    )

    _configurar_logging(settings)
    _registrar_estaticos_versionados(app)
    _registrar_seguranca(app, settings)
    _registrar_blueprints(app)
    _registrar_erros(app)

    return app


# ── Blocos da factory ────────────────────────────────────────────────────────


def _configurar_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _registrar_estaticos_versionados(app: Flask) -> None:
    """Acrescenta ?v=<mtime> a toda url_for('static', ...).

    Sem isto o browser serve CSS/JS do cache junto com o HTML novo e a tela
    aparece sem estilo até um Ctrl+F5. Arquivo alterado muda o mtime, logo a
    URL, logo o download; os inalterados seguem em cache.
    """

    @app.url_defaults
    def _versionar_estaticos(endpoint, values):
        if endpoint != "static" or "filename" not in values or "v" in values:
            return
        try:
            caminho = Path(app.static_folder) / values["filename"]
            values["v"] = int(caminho.stat().st_mtime)
        except OSError:
            pass  # arquivo ausente: segue sem versão (url_for não deve quebrar)


def _registrar_seguranca(app: Flask, settings: Settings) -> None:
    """Nonce da CSP, cabeçalhos de segurança e a própria política."""

    @app.before_request
    def _csp_nonce():
        g.csp_nonce = secrets.token_urlsafe(16)
        # O id de correlação nasce junto do nonce, pelo mesmo motivo: é o
        # before_request mais antigo, e tem de existir antes de qualquer coisa
        # poder rejeitar a requisição.
        g.req_id = secrets.token_hex(4)

    @app.context_processor
    def _contexto_global():
        return {
            "csp_nonce": getattr(g, "csp_nonce", ""),
            "app_name": settings.app_name,
            # O destino aparece no cabeçalho de TODA tela. Este processo grava
            # num banco de produção, e a pergunta que ele precisa responder o
            # tempo todo é "em qual?": apontar o .env para o banco errado é
            # indistinguível de apontar para o certo até alguém conferir os
            # números, que é tarde. Nunca a senha, nunca o usuário.
            "destino": {
                "banco": settings.database_name,
                "servidor": f"{settings.database_host}:{settings.database_port}",
            },
        }

    @app.after_request
    def _cabecalhos_seguranca(response):
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "0"
        # Sem HSTS: este servidor é http://127.0.0.1 e o cabeçalho faria o
        # navegador fixar HTTPS para o localhost inteiro, atrapalhando todo
        # projeto que sirva HTTP na mesma origem — sem forma óbvia de desfazer.
        cabecalho = (
            "Content-Security-Policy"
            if settings.csp_enforce
            else "Content-Security-Policy-Report-Only"
        )
        response.headers[cabecalho] = _politica_csp()

        # A tela mostra razão social, CNPJ e contagens de movimento das
        # empresas clientes. Não fica no cache de disco do navegador — vale
        # também para o botão Voltar. Só quando ninguém já decidiu: o
        # `send_file` dos estáticos põe o `public, max-age=` que o
        # `?v=<mtime>` torna seguro.
        response.headers.setdefault("Cache-Control", "no-store")
        return response


def _politica_csp() -> str:
    """CSP com nonce por request — a mesma política do Portal.

    `script-src` fechado em `self` mais o nonce. A única origem externa é o
    Google Fonts, em `style-src`/`font-src`, porque o `base.html` carrega IBM
    Plex Sans, Outfit e os Material Symbols de lá.

    O `unsafe-inline` em `style-src` acompanha o Portal: `errors/_erro.html`
    tem um bloco `<style>` embutido, e o `style=` por atributo é como o portal
    posiciona elementos. Divergir aqui quebraria o CSS copiado.

    `report-uri` vale nos dois modos, e é o que torna a política
    diagnosticável: sem ele, a violação vai só para o console de quem estiver
    com o DevTools aberto — e uma violação de CSP quebra o JS da tela em
    silêncio, que é o defeito mais caro de achar ao montar a tela da Etapa 2.
    """
    # Import local: o caminho tem UMA fonte (o módulo que declara a rota), e um
    # import no topo da factory criaria ciclo com o blueprint que ela registra.
    from .api.routes import CAMINHO_RELATORIO_CSP

    nonce = getattr(g, "csp_nonce", "")
    script_src = f"'self' 'nonce-{nonce}'" if nonce else "'self'"
    return (
        "default-src 'self'; "
        f"script-src {script_src}; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        f"report-uri {CAMINHO_RELATORIO_CSP}"
    )


def _registrar_blueprints(app: Flask) -> None:
    """Um blueprint por módulo funcional, registrado explicitamente aqui."""
    from .api.routes import api_bp
    from .health.routes import health_bp
    from .importacao.routes import importacao_bp
    from .main.routes import main_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(importacao_bp)


def _identidade_da_requisicao() -> str:
    """`req=a1b2c3d4 GET /api/v1/sincronizacao` — o prefixo de toda linha de erro.

    Sem `user=`: aqui não há usuário. Fica o id de correlação, que é o que
    permite ligar uma reclamação ("a sincronização de saldos deu erro agora")
    à linha certa do log quando há mais de uma execução no mesmo minuto.

    **Nunca levanta.** É código de caminho de ERRO: uma exceção aqui
    substituiria o problema real por outro, dentro do handler que existe para
    relatá-lo. Daí o `getattr` — fora de requisição, `g` pode não ter nada.
    """
    return f"req={getattr(g, 'req_id', '-')} {request.method} {request.path}"


def _registrar_erros(app: Flask) -> None:
    """Traduz exceções para o contrato de resposta (JSON) ou para tela (HTML).

    É o único lugar que conhece códigos HTTP de erro: as camadas de baixo
    levantam `ErroApp` (ver `app/shared/errors.py`) e param aqui.
    """

    @app.after_request
    def _expor_id_da_requisicao(response):
        response.headers["X-Request-Id"] = getattr(g, "req_id", "-")
        return response

    def _pagina_erro(status: int, mensagem: str):
        template = f"errors/{status}.html" if status in STATUS_COM_PAGINA else "errors/500.html"
        return render_template(template, mensagem=mensagem, status=status), status

    @app.errorhandler(ErroApp)
    def _erro_app(exc: ErroApp):
        logger.info("[%s] %s: %s", _identidade_da_requisicao(), type(exc).__name__, exc)
        if _quer_json():
            return erro(exc.code, exc.message, details=exc.details, status=exc.status)
        return _pagina_erro(exc.status, exc.message)

    @app.errorhandler(HTTPException)
    def _http_exception(exc: HTTPException):
        status = exc.code or 500
        mensagem = exc.description or "Requisição inválida."
        if _quer_json():
            return erro(f"http_{status}", mensagem, status=status)
        return _pagina_erro(status, mensagem)

    @app.errorhandler(Exception)
    def _erro_inesperado(_exc: Exception):
        logger.exception("[%s] Erro não tratado", _identidade_da_requisicao())
        mensagem = "Erro interno. O detalhe foi registrado no log."
        if _quer_json():
            return erro("erro_interno", mensagem, status=500)
        return _pagina_erro(500, mensagem)
