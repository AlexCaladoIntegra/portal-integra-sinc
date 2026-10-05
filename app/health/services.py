"""Diagnóstico das pontas que o sincronizador liga.

Uma função só, `diagnosticar()`, servida pelo `/health` e pelo card de
diagnóstico da tela. É o primeiro lugar em que alguém olha quando uma
sincronização falha, e por isso ela responde quatro perguntas separadas em vez
de um "ok/não ok":

    origem   o Domínio respondeu?     (ODBC — driver instalado, DSN, credencial)
    fiscal   o fiscal-monitor-cpf?    (rota, credencial, banco existe)
    destino  o PostgreSQL respondeu?  (rota, credencial, banco existe)
    schema   é o banco que eu espero? (revisão do Alembic do Portal)

Separadas porque falham por motivos diferentes e mandam quem investiga para
lugares diferentes. Um "indisponível" agregado faria procurar rede quando o
problema é o driver ODBC de 32 bits instalado numa máquina de 64.

## A ponta que NÃO entra no `pronto`

`fiscal` serve a UM conjunto de dados, o `cnd_documentos`. Os outros dez não a
conhecem. Por isso ela é reportada e **não** entra no veredito que libera o
botão: tratá-la como bloqueio faria uma instalação sem o fiscal-monitor ficar
sem sincronizar BI nenhum.

Quem decide o que fazer com ela é o próprio importador, que recusa com a
mensagem certa — e a rodada completa segue nos outros dez, porque o mapa
`DEPENDE_DE` não liga nenhum a ele.

**Nenhuma exceção sobe daqui.** Diagnóstico que quebra ao diagnosticar não
serve para nada: o erro vira o próprio resultado, com o texto para quem lê.
"""

from __future__ import annotations

import logging

from ..config import get_settings
from ..data.connection import get_connection
from ..data.schema import conferir, revisao_aplicada, tem_coluna_de_titular
from ..documentos.escrita import conferir_colunas
from ..importacao import dominio, fiscal

logger = logging.getLogger(__name__)


def _checar_destino() -> dict:
    """O PostgreSQL do Portal respondeu?"""
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    except Exception as exc:
        logger.warning("Destino indisponível: %s", exc)
        return {
            "situacao": "erro",
            "mensagem": "Não foi possível falar com o PostgreSQL do Portal. "
            "Confira DATABASE_HOST, DATABASE_USER e a rota de rede no .env.",
        }
    return {"situacao": "ok", "mensagem": "PostgreSQL do Portal respondeu."}


def _checar_origem() -> dict:
    """O Domínio respondeu?

    "Não configurado" é estado à parte de "erro", e a distinção é o que a tela
    precisa: sem DSN no `.env` não há falha nenhuma a investigar — há uma
    instalação que ainda não terminou.
    """
    if not dominio.configurado():
        return {
            "situacao": "ausente",
            "mensagem": "A conexão com o Domínio não está configurada. "
            "Defina DOMINIO_DSN (ou DOMINIO_CONNSTR) no .env.",
        }
    try:
        with dominio.conexao() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
    except Exception as exc:
        logger.warning("Origem indisponível: %s", exc)
        return {
            "situacao": "erro",
            "mensagem": "Não foi possível ler o Domínio. Confira se o driver "
            "ODBC do SQL Anywhere está instalado (64 bits) e se o DSN e a "
            "credencial do .env estão certos.",
        }
    return {"situacao": "ok", "mensagem": "Domínio respondeu."}


def _checar_fiscal() -> dict:
    """O fiscal-monitor-cpf respondeu?

    "Não configurado" é estado à parte de "erro", e a distinção é o que a tela
    precisa: sem host no `.env` não há falha a investigar — há um conjunto de
    dados que esta instalação não usa.

    A consulta é contra `contribuintes` de propósito, e não um `SELECT 1`: é a
    tabela que o importador lê, e uma credencial que conecta mas não a enxerga
    falharia só na rodada.
    """
    if not get_settings().fiscal_configurado:
        return {
            "situacao": "ausente",
            "mensagem": "A conexão com o fiscal-monitor-cpf não está configurada. "
            "Sem ela só o conjunto 'Certidões negativas (CND)' fica de fora — os "
            "outros dez não dependem dela. Defina FISCAL_HOST e FISCAL_USER no .env.",
        }
    try:
        with fiscal.conexao() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM contribuintes LIMIT 1")
            cur.fetchone()
    except Exception as exc:
        logger.warning("Origem fiscal indisponível: %s", exc)
        return {
            "situacao": "erro",
            "mensagem": "Não foi possível ler o fiscal-monitor-cpf. Confira "
            "FISCAL_HOST, FISCAL_USER e a rota de rede no .env.",
        }
    return {"situacao": "ok", "mensagem": "fiscal-monitor-cpf respondeu."}


def _checar_schema() -> dict:
    """A revisão do schema do Portal é a que este código espera?

    Duas perguntas numa: a revisão registrada e a COLUNA que a certidão de
    sócio usa. Um banco que diga `0039` e não tenha `documento_titular` é o
    caso de DDL aplicada à mão, e o sintoma dele seria um documento gravado no
    titular errado, sem erro.
    """
    try:
        situacao, mensagem = conferir(revisao_aplicada())
    except Exception:
        # Sem banco não dá para perguntar. Não é divergência de schema, e dizer
        # que é mandaria quem investiga para o lado errado — `_checar_destino`
        # já reportou o motivo real.
        return {
            "situacao": "desconhecido",
            "mensagem": "Não foi possível ler a revisão do schema: o banco não respondeu.",
            "tem_titular": False,
        }

    try:
        tem_titular = tem_coluna_de_titular()
    except Exception:
        tem_titular = False

    if situacao == "ok" and not tem_titular:
        return {
            "situacao": "divergente",
            "mensagem": "O banco diz estar na revisão esperada, mas `doc_documento` "
            "não tem as colunas de titular. Alguém aplicou DDL à mão: a certidão "
            "de sócio não tem onde entrar.",
            "tem_titular": False,
            "colunas": [],
        }

    # A terceira guarda da cópia declarada de `app/documentos/escrita.py`: o
    # que ela grava contra o que o destino exige. Ela pega uma migration do
    # Portal ANTES de a rodada quebrar — uma coluna obrigatória nova faria todo
    # INSERT de documento falhar no meio, e o aviso aqui chega com o nome dela.
    #
    # Divergência de coluna **rebaixa a situação**, mesmo com a revisão certa:
    # a revisão diz o que o Alembic registrou, e a coluna diz o que o banco
    # tem. Quando as duas discordam, quem manda é a coluna.
    try:
        problemas = conferir_colunas()
    except Exception:
        problemas = []

    if problemas:
        return {
            "situacao": "divergente",
            "mensagem": "O destino não bate com o que o conjunto de certidões grava: "
            + "; ".join(problemas[:3])
            + ("…" if len(problemas) > 3 else ""),
            "tem_titular": tem_titular,
            "colunas": problemas,
        }
    return {
        "situacao": situacao,
        "mensagem": mensagem,
        "tem_titular": tem_titular,
        "colunas": [],
    }


def diagnosticar() -> dict:
    """As quatro checagens, mais os dois vereditos.

    `pronto` responde a pergunta que a tela faz antes de liberar o botão: dá
    para sincronizar agora? Schema divergente **não** impede — é aviso, pela
    razão em `app/config.py`. E `fiscal` também não: ela serve a um conjunto
    só, e tratá-la como bloqueio faria uma instalação sem o fiscal-monitor
    ficar sem sincronizar BI nenhum.

    `pronto_para_cnd` é a segunda pergunta, e existe separada porque a resposta
    "não" não bloqueia a rodada: bloqueia um conjunto dos onze. Note que ela
    **não** exige o Domínio — sem ele a certidão da empresa continua entrando,
    e só a do sócio fica de fora.
    """
    origem = _checar_origem()
    fiscal_ = _checar_fiscal()
    destino = _checar_destino()
    schema = _checar_schema()
    return {
        "origem": origem,
        "fiscal": fiscal_,
        "destino": destino,
        "schema": schema,
        "pronto": origem["situacao"] == "ok" and destino["situacao"] == "ok",
        "pronto_para_cnd": (
            destino["situacao"] == "ok"
            and fiscal_["situacao"] == "ok"
            and bool(schema.get("tem_titular"))
        ),
    }
