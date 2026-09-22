"""Blueprint `api` — endpoints JSON versionados sob /api/v1.

Reservado para os endpoints que não pertencem a nenhum módulo específico (por
exemplo, dados agregados para dashboards que cruzam módulos). Cada módulo serve
os seus próprios endpoints, e é onde eles devem ficar — `app/importacao/routes.py`
para a sincronização.

Hoje mora aqui uma coisa só, e ela é exatamente do tipo que o módulo foi
reservado para receber: o destino dos relatórios de violação da CSP, que é
política da aplicação inteira e não de módulo nenhum.

Convenções obrigatórias, quando algo entrar aqui:
- envelope de resposta de `app/shared/responses.py`;
- paginação de `app/shared/pagination.py` em toda listagem;
- erro levantado como `ErroApp`, nunca `jsonify(erro=...)` na rota.
"""

from __future__ import annotations

import logging

from flask import Blueprint, request

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")
logger = logging.getLogger(__name__)

# ── O destino dos relatórios de CSP ──────────────────────────────────────────
#
# `CAMINHO_RELATORIO_CSP` é a FONTE ÚNICA do caminho: `_politica_csp()` em
# app/__init__.py o importa para montar a diretiva `report-uri`. Repetir a
# string nos dois lugares faria a política apontar para um endereço que não
# existe no dia em que um dos dois mudasse — e o sintoma seria "os relatórios
# pararam de chegar", que ninguém nota, porque relatório que não chega se parece
# com ausência de violação.
CAMINHO_RELATORIO = "/csp-report"
CAMINHO_RELATORIO_CSP = f"{api_bp.url_prefix}{CAMINHO_RELATORIO}"

# Os campos do relatório que vão para o log. O corpo inteiro não vai: ele é
# escrito por um cliente não autenticado, e `original-policy` sozinho repete a
# política completa em cada linha.
CAMPOS_DO_RELATORIO = (
    "violated-directive",
    "effective-directive",
    "blocked-uri",
    "document-uri",
    "line-number",
    "source-file",
)

TAMANHO_MAXIMO_CAMPO = 200


def _limpar(valor) -> str:
    """Um campo do relatório, seguro para entrar numa linha de log.

    O valor vem de um POST sem autenticação, então duas coisas saem: **quebra
    de linha** e **excesso de tamanho**.

    A quebra de linha é defesa em PROFUNDIDADE, e vale dizer por quê: hoje o
    `logger.warning` recebe o dicionário inteiro e o `%s` de um dict usa o
    `repr` de cada valor, que já escapa `\\n` como dois caracteres. Nenhuma
    quebra chega ao arquivo pelo caminho atual. Ela sai aqui porque o dia em que
    alguém formatar os campos um a um — `logger.warning("... %s", blocked)` — a
    proteção passaria a depender de uma decisão de formatação, e forjar linha de
    log no arquivo que a gente usa para investigar o atacante é caro demais para
    ficar dependendo disso. A guarda de verdade está no teste de `_limpar`, e
    não no do endpoint: pelo endpoint ela não é observável.

    O tamanho, ao contrário, é load-bearing agora: sem teto, um campo de
    megabytes enche o disco de log a custo zero para quem posta.
    """
    texto = str(valor).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return texto[:TAMANHO_MAXIMO_CAMPO]


@api_bp.post(CAMINHO_RELATORIO)
def receber_relatorio_csp():
    """Destino do `report-uri` da CSP. Só registra no log.

    **Sem autenticação, e é obrigatório que seja assim:** a tela de login também
    carrega a política, e é justamente a violação de quem ainda não entrou que
    ninguém veria de outra forma.

    No Portal esta rota é a única isenta de CSRF, porque o navegador manda o
    relatório por conta própria, sem token e sem cookie. Aqui não há proteção
    de CSRF a isentar — esta aplicação não tem sessão nem formulário — então a
    isenção sumiu junto com o mecanismo.

    Responde **204 sempre**, inclusive para corpo inválido: o cliente é o
    navegador, não há nada que ele possa corrigir, e uma resposta de erro só
    diria a um spammer que ele acertou o endereço. O limite de taxa fica na
    borda — que aqui é o próprio loopback: ninguém de fora alcança a porta.

    `force=True` porque o navegador envia `Content-Type: application/csp-report`,
    que o Flask não reconhece como JSON — sem isso o corpo chegaria vazio e o
    log registraria violação sem nenhum detalhe.
    """
    corpo = request.get_json(force=True, silent=True) or {}
    relatorio = corpo.get("csp-report") if isinstance(corpo, dict) else None

    if not isinstance(relatorio, dict):
        logger.warning("Relatório de CSP em formato inesperado (ignorado)")
        return "", 204

    detalhes = {
        campo: _limpar(relatorio[campo]) for campo in CAMPOS_DO_RELATORIO if campo in relatorio
    }
    logger.warning("Violação de CSP: %s", detalhes or "sem detalhe no relatório")
    return "", 204
