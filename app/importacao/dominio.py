"""Conexão com o Domínio (SQL Anywhere) — somente leitura.

Único ponto do projeto que abre essa conexão. Nenhuma tela consulta o Domínio
direto: os importadores leem daqui e gravam no PostgreSQL do portal.

Requisitos de ambiente: `pyodbc` (no requirements) e o **driver ODBC do SQL
Anywhere instalado na máquina**. O driver é proprietário e não vem na imagem
Docker — ver a nota de deploy em changes/IMP-001-importacao-dados.md.
"""

from __future__ import annotations

import codecs
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from ..config import get_settings
from ..shared.errors import ErroApp

logger = logging.getLogger(__name__)

# ── O codec tolerante, e por que ele precisa existir ─────────────────────────
#
# O SQL Anywhere devolve texto em cp1252, e cp1252 tem CINCO bytes sem
# significado: 0x81, 0x8D, 0x8F, 0x90 e 0x9D. Nenhum deles deveria aparecer — e
# aparecem, porque há texto gravado na origem com bytes UTF-8 dentro de coluna
# cp1252.
#
# Caso real, empresa 536, achado ao ler `ctlancto` em 04/09/2026: o histórico
# "Pix qr code recebido de Clarice Gabriela Ávila Gonder" tem o "Á" gravado como
# o par UTF-8 0xC3 0x81. O próprio Domínio o exibe como `Ã?vila`. São cinco
# linhas nessa empresa, e o `strict` derrubava a importação inteira dela.
#
# A escolha é entre três, e duas são piores:
#
#   `strict`    uma linha de dado sujo impede a empresa inteira de ser
#               importada. Foi o que acontecia.
#   latin-1     nunca falha, mas mapeia 0x80-0x9F para controles — e ali moram
#               caracteres cp1252 LEGÍTIMOS e comuns em texto contábil
#               (travessão, aspas curvas). Trocaria um erro alto por corrupção
#               silenciosa em texto que hoje chega certo.
#   `replace`   o byte inválido vira U+FFFD e o resto do texto sobrevive.
#
# `replace` é o único que mantém o portal ESPELHO da origem: o Domínio mostra
# `Ã?vila`, o portal mostra `Ã�vila`. Os dois estão igualmente errados, e é
# assim que se descobre que o dado precisa ser corrigido lá, não aqui. Reverter
# a dupla codificação aqui seria adivinhar — e adivinhar certo em 5 linhas custa
# adivinhar errado em texto que por acaso pareça UTF-8.
CODEC_TOLERANTE = "cp1252_tolerante"


def _buscar_codec(nome: str):
    # `codecs.lookup` normaliza o nome para minúsculas e troca espaço e hífen
    # por underscore antes de chamar as funções de busca registradas.
    if nome.lower().replace("-", "_").replace(" ", "_") != CODEC_TOLERANTE:
        return None
    base = codecs.lookup("cp1252")

    # O `errors` que o chamador pedir é IGNORADO de propósito: o ponto deste
    # codec é que ele nunca levanta. Aceitá-lo devolveria a `strict` pela porta
    # dos fundos, num argumento que o pyodbc nem passa.
    def decodificar(entrada, _errors="replace"):
        return base.decode(entrada, "replace")

    def codificar(entrada, _errors="replace"):
        return base.encode(entrada, "replace")

    return codecs.CodecInfo(codificar, decodificar, name=CODEC_TOLERANTE)


codecs.register(_buscar_codec)

# Lote padrão de `sessao_em_lotes()`. Cinco mil linhas de `ctlancto` — a maior
# das tabelas lidas — ficam na casa de poucos MB, e o número de idas ao driver
# continua baixo até na empresa de 370 mil lançamentos.
LOTE_PADRAO = 5_000

# ── Os dois timeouts, e por que os números são tão diferentes ────────────────
#
# Sem eles, `pyodbc.connect` espera indefinidamente quando o host do SQL
# Anywhere está inalcançável de um jeito que não RECUSA a conexão — firewall com
# DROP, host desligado, rota morta. O tratamento de erro deste módulo é
# completo (503, mensagem do driver só no log), e nunca rodava: a chamada não
# voltava. Quem pagava era o worker do gunicorn.
#
# CONECTAR é o caso que precisa ser curto: 10 s é muito mais do que um handshake
# na rede interna leva, e transforma "worker pendurado" em "503 explicando".
#
# CONSULTAR é generoso de propósito. `ctlancto` tem 14,5 milhões de linhas e o
# importador de lançamentos lê a janela de uma empresa por vez — a maior com 370
# mil. O teto existe para que uma consulta desgovernada termine em erro em vez
# de nunca, e não para disciplinar duração: cortar mais fundo exigiria MEDIR a
# leitura da maior empresa, e um número escolhido no palpite quebraria a
# importação justamente dela. Acima disto o worker já teria sido morto pelo
# gunicorn (120 s); o valor serve ao CLI, que não tem quem o mate.
SEGUNDOS_PARA_CONECTAR = 10
SEGUNDOS_POR_CONSULTA = 300


class ErroDominio(ErroApp):
    """Falha ao ler o Domínio. 503: é indisponibilidade de dependência externa,
    não erro de quem pediu a importação."""

    code = "dominio_indisponivel"
    status = 503
    message = "Não foi possível ler o Domínio. Verifique a conexão e tente de novo."


def configurado() -> bool:
    """True se há como conectar. A tela usa isto para explicar em vez de falhar."""
    s = get_settings()
    return bool(s.dominio_connstr or s.dominio_dsn)


def _connstr() -> str:
    s = get_settings()
    if s.dominio_connstr:
        return s.dominio_connstr
    if s.dominio_dsn:
        return f"DSN={s.dominio_dsn};UID={s.dominio_user};PWD={s.dominio_password}"
    raise ErroDominio(
        "Conexão com o Domínio não configurada — defina DOMINIO_DSN (ou DOMINIO_CONNSTR) no .env."
    )


# Uma vez por processo — ver o corpo de `_tentar_teto_de_consulta`.
_avisou_sem_teto = False


def _tentar_teto_de_consulta(pyodbc, conn) -> None:
    """Põe o teto por consulta SE o driver souber — e segue em frente se não.

    **O driver do SQL Anywhere NÃO sabe**, e isto custou toda a importação.

    `conn.timeout = N` vira `SQLSetConnectAttr(SQL_ATTR_QUERY_TIMEOUT)`, e o
    driver da SAP responde `HYC00 — Driver not capable`. Como a atribuição estava
    dentro do `try` que traduz tudo para `ErroDominio`, **todo** importador
    morria com "Não foi possível ler o Domínio. Verifique a conexão e tente de
    novo" — uma mensagem que manda conferir a conexão, quando a conexão estava
    perfeita: ela já tinha sido aberta com sucesso na linha de cima.

    Relatado em 11/09/2026 e reproduzido no driver real, isolado ao atributo: o
    `connect`, o `setdecoding` e o `setencoding` passam; só este falha.

    A suíte não pegaria, e não é falha dela: o Domínio entra sempre por dublê,
    que é o desenho do projeto. Um dublê nunca recusaria um atributo que o driver
    de verdade recusa — por isso a guarda que nasce com esta correção é sobre a
    TOLERÂNCIA, e não sobre o valor.

    O teto vira, então, uma defesa best-effort, e isso é o certo: ele existe para
    que uma consulta desgovernada termine em erro em vez de nunca, e uma defesa
    que impede o uso correto é pior que defesa nenhuma. O que **não** é
    best-effort é o teto de CONEXÃO (`SEGUNDOS_PARA_CONECTAR`), que vai como
    parâmetro do `connect` e o driver aceita — e é ele que cobre o caso que
    motivou os dois: host que não recusa e não responde.

    O aviso NOMEIA o que se perdeu, e sai **uma vez por processo**. Calar
    deixaria o operador achando que há um teto de 300 s onde não há; repetir
    por conexão é pior de outro jeito.

    ## A divergência em relação ao Portal, e o número que a motivou

    Lá o aviso sai a cada conexão. Aqui isso não serve: medido numa rodada
    completa em 22/09/2026, foram **713 linhas idênticas — 52% dos bytes do
    log**. Num log noturno rotativo (5 × 2 MB) isso corta pela metade o
    histórico que sobra para investigar, e enterra o que importa.

    O driver ou aceita o atributo ou não; a resposta é do DRIVER, não da
    conexão. Dizê-la uma vez diz tudo que há para dizer.
    """
    global _avisou_sem_teto
    try:
        conn.timeout = SEGUNDOS_POR_CONSULTA
    except pyodbc.Error as exc:
        if _avisou_sem_teto:
            return
        _avisou_sem_teto = True
        logger.warning(
            "SEM TETO POR CONSULTA — o driver ODBC recusou "
            "SQL_ATTR_QUERY_TIMEOUT (%s). A leitura do Domínio segue sem limite "
            "de duração; quem corta é o operador ou o agendador. "
            "Este aviso sai uma vez por execução.",
            exc,
        )


@contextmanager
def conexao() -> Iterator:
    """Conexão read-only, fechada ao sair.

    O SQL Anywhere devolve texto em cp1252; sem declarar o decoding, nome de
    empresa com acento chega truncado ou levanta UnicodeDecodeError.

    O decoding é o `CODEC_TOLERANTE` e não o `cp1252` puro — ver o cabeçalho do
    módulo. Em resumo: há texto na origem com byte que cp1252 não define, e com
    o codec estrito uma única linha suja derruba a importação da empresa
    inteira.

    Qualquer falha vira `ErroDominio` — a mensagem do driver traz host, usuário
    e caminho do banco, e não pode chegar ao navegador.

    Os dois timeouts são o que faz esse tratamento de erro chegar a rodar: sem
    eles a chamada não volta quando o host não recusa a conexão. Ver
    `SEGUNDOS_PARA_CONECTAR` e `SEGUNDOS_POR_CONSULTA`.
    """
    try:
        import pyodbc
    except ImportError as exc:
        raise ErroDominio(
            "pyodbc não instalado nesta máquina — a importação não está disponível."
        ) from exc

    try:
        conn = pyodbc.connect(_connstr(), readonly=True, timeout=SEGUNDOS_PARA_CONECTAR)
    except pyodbc.Error as exc:
        logger.error("Falha ao conectar no Domínio: %s", exc)
        raise ErroDominio from exc

    try:
        _tentar_teto_de_consulta(pyodbc, conn)
        conn.setdecoding(pyodbc.SQL_CHAR, encoding=CODEC_TOLERANTE)
        conn.setdecoding(pyodbc.SQL_WCHAR, encoding=CODEC_TOLERANTE)
        conn.setencoding(encoding="cp1252")
        yield conn
    except pyodbc.Error as exc:
        logger.error("Falha ao ler o Domínio: %s", exc)
        raise ErroDominio from exc
    finally:
        # O context manager do pyodbc encerra a transação mas NÃO fecha a
        # conexão; sem isto a sessão fica pendurada no SQL Anywhere.
        conn.close()


def _consultar_em(conn, sql: str, params: tuple = ()) -> list[dict]:
    cur = conn.cursor()
    cur.execute(sql, params)
    colunas = [c[0] for c in cur.description]
    return [dict(zip(colunas, linha, strict=True)) for linha in cur.fetchall()]


@contextmanager
def sessao() -> Iterator[Callable[..., list[dict]]]:
    """Abre UMA conexão e entrega a função de consulta que a reaproveita.

    Para o importador que faz várias consultas — por exemplo uma por empresa,
    vezes uma por entidade — `consultar()` abriria e fecharia uma conexão em
    cada uma. Com 40 empresas e 3 consultas por empresa isso são 120 handshakes
    no SQL Anywhere para ler alguns milhares de linhas.

        with dominio.sessao() as consultar:
            contas = consultar(sql_plano, (id_empresa,))
            grupos = consultar(sql_dre, (id_empresa,))
    """
    with conexao() as conn:

        def consultar_aqui(sql: str, params: tuple = ()) -> list[dict]:
            return _consultar_em(conn, sql, params)

        yield consultar_aqui


def consultar(sql: str, params: tuple = ()) -> list[dict]:
    """Executa um SELECT no Domínio e devolve lista de dicionários.

    Abre e fecha uma conexão. Para várias consultas em sequência, use
    `sessao()`.
    """
    with conexao() as conn:
        return _consultar_em(conn, sql, params)


def _consultar_em_lotes(
    conn, sql: str, params: tuple = (), tamanho: int = LOTE_PADRAO
) -> Iterator[list[dict]]:
    """Mesma consulta de `_consultar_em`, entregue em lotes de `tamanho`."""
    cur = conn.cursor()
    cur.execute(sql, params)
    colunas = [c[0] for c in cur.description]
    while True:
        linhas = cur.fetchmany(tamanho)
        if not linhas:
            return
        yield [dict(zip(colunas, linha, strict=True)) for linha in linhas]


@contextmanager
def sessao_em_lotes() -> Iterator[Callable[..., Iterator[list[dict]]]]:
    """Como `sessao()`, mas a consulta devolve LOTES em vez do resultado inteiro.

    `sessao()` materializa tudo (`fetchall`), e é o certo para os importadores
    que existiam até aqui: o de saldos lê 553 mil lançamentos da empresa 272 e
    recebe 3.338 linhas, porque a agregação acontece na origem.

    O de lançamentos não agrega nada — o grão dele **é** o lançamento. Medido no
    Domínio em 04/09/2026, a maior empresa tem **370.437** lançamentos no biênio
    2025-2026; materializados como dicionários, são centenas de MB de uma vez
    só, num processo que também mantém uma transação PostgreSQL aberta. Ler em
    lotes mantém o pico proporcional ao lote, e não à empresa.

        with dominio.sessao_em_lotes() as consultar:
            for lote in consultar(sql, (id_empresa,), tamanho=5_000):
                gravar(lote)

    O consumo é **preguiçoso**: cada lote só sai do driver quando o laço pede.
    Por isso o gerador tem de ser esgotado (ou abandonado) antes de a sessão
    fechar — a conexão morre com o `with`.
    """
    with conexao() as conn:

        def consultar_aqui(
            sql: str, params: tuple = (), tamanho: int = LOTE_PADRAO
        ) -> Iterator[list[dict]]:
            return _consultar_em_lotes(conn, sql, params, tamanho)

        yield consultar_aqui
