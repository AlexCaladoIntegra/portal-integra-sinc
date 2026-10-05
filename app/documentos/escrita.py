"""O caminho de gravação no módulo Documentos do Portal — CÓPIA DECLARADA.

**Não "melhore" nada aqui.** Cada função abaixo nomeia o arquivo do
`portal-integra` de onde veio, e a fidelidade é o ponto: o módulo de lá tem um
único ponto de criação, `ArquivoService.enviar()`, e o `services_obtencao.py`
dele diz por quê:

    "um segundo caminho de gravação divergiria dele na primeira correção feita
     de um lado — e o lado que ninguém olha é justamente o do robô"

Este arquivo **é** esse segundo caminho. Ele existe porque um processo separado
não consegue importar o service do Portal, e a alternativa — reimplementar à
mão — produziria uma divergência maior e sem rastro. O que se faz é copiar com
o endereço de origem anotado, e cercar com três guardas:

1. o cabeçalho de cada função diz de onde ela veio;
2. `SCHEMA_REVISAO_ESPERADA` amarra o processo à revisão `0039`, conferida no
   boot e no `/health`;
3. `conferir_colunas()` compara o que este arquivo escreve com o que o
   `information_schema` do destino exige — coluna `NOT NULL` sem default que a
   cópia não preencha deixa a suíte vermelha antes de deixar a rodada vermelha.

## O que NÃO foi copiado, e por quê

| Do Portal | Por que fica de fora |
|---|---|
| `_conferir_metadados` | campo dinâmico é do cadastro do tipo, e a CND não tem |
| `_enfileirar_ocr` | o OCR é do worker do Portal, que roda lá |
| `_conferir_documento_escolhido` | é do modal "renovar ESTE"; aqui não há pessoa |
| `como_novo` | idem |
| `aceita_manual`, `validade_obrigatoria` | o Portal só os exige quando a origem é `manual` |

## A ordem das gravações é a do original, e ela não é arbitrária

    documento (quando é a primeira) → versão → arquivo → promoção → histórico

Tudo numa transação só. Separá-las deixaria estados que nenhuma tela mostra:
versão sem arquivo, arquivo sem promoção, documento sem vigente.
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date

from psycopg2 import Binary
from psycopg2 import errors as pg_errors

from ..data.connection import get_connection, linha_dict, linhas_dict, transacao
from ..shared.errors import ErroApp, ErroConflito, ErroValidacao

logger = logging.getLogger(__name__)

# Quantos bytes bastam para reconhecer um formato. Do `mime.py` do Portal.
BYTES_DE_ASSINATURA = 16

# Os motivos que `ck_doc_versao_motivo` aceita. Só usamos dois.
MOTIVO_INCLUSAO = "INCLUSAO"
MOTIVO_RENOVACAO = "RENOVACAO"

# O carimbo que distingue o que este processo gravou. Vai para
# `doc_arquivo.origem` e `doc_documento_versao.origem_processo`, e é o que
# permite achar tudo que o espelho criou sem depender da data.
ORIGEM = "cnd:fiscal-monitor"


class ArquivoDuplicado(ErroConflito):
    """O mesmo conteúdo já é versão deste documento.

    Separado de `ErroConflito` porque o chamador o trata como SUCESSO: a rodada
    diária reencontra a mesma certidão enquanto ela vale, e isso é o caso
    normal, não falha. Ver `services_obtencao.persistir` no Portal, que faz o
    mesmo.
    """

    code = "arquivo_duplicado"


# ── Cópia de `app/documentos/hash_arquivo.py` ────────────────────────────────


def calcular_hash(conteudo: bytes) -> str:
    """SHA-256 em hexadecimal MINÚSCULO.

    Cópia de `hash_arquivo.calcular`. A caixa importa: o `CHECK` do banco é
    `hash_sha256 ~ '^[0-9a-f]{64}$'` e recusa maiúscula, justamente para o
    portal não aceitar o mesmo arquivo duas vezes por causa de um `A` contra
    um `a`.
    """
    if not conteudo:
        raise ValueError("arquivo vazio não tem hash útil")
    return hashlib.sha256(conteudo).hexdigest()


# ── Cópia de `app/documentos/mime.py` ────────────────────────────────────────


def _assinatura(linha: dict) -> bytes | None:
    bruto = (linha.get("assinatura_hex") or "").strip()
    if not bruto:
        return None
    try:
        return bytes.fromhex(bruto)
    except ValueError:  # pragma: no cover - o CHECK do banco já recusa
        return None


def detectar_extensao(conteudo: bytes, catalogo: list[dict]) -> str | None:
    """A extensão que os bytes aparentam, ou `None`.

    Cópia de `mime.detectar`. A **assinatura mais longa vence** em caso de
    prefixo comum: sem isso, um formato cuja assinatura é prefixo de outro
    roubaria a detecção do mais específico.
    """
    if not conteudo:
        return None
    inicio = conteudo[:BYTES_DE_ASSINATURA]
    candidatos = []
    for linha in catalogo:
        assinatura = _assinatura(linha)
        if assinatura and inicio.startswith(assinatura):
            candidatos.append((len(assinatura), linha["extensao"]))
    if not candidatos:
        return None
    return max(candidatos)[1]


def conferir_conteudo(conteudo: bytes, extensao: str, catalogo: list[dict]) -> None:
    """O conteúdo corresponde à extensão declarada? Levanta se não.

    Cópia da lógica de `mime.conferir`, com a diferença de devolver `None` e
    levantar em vez de montar uma `Conferencia` — aqui não há formulário para
    receber o erro campo a campo.

    Para a CND isto se resume a `%PDF`, e é uma guarda real: um PDF truncado
    pela origem passaria o `tamanho_bytes > 0` do banco e chegaria à tela como
    um arquivo que não abre.
    """
    if not conteudo:
        raise ErroValidacao("O arquivo enviado está vazio.")

    extensao = (extensao or "").strip().lower().lstrip(".")
    por_extensao = {linha["extensao"]: linha for linha in catalogo}
    declarada = por_extensao.get(extensao)
    if declarada is None:
        raise ErroValidacao(f"Extensão .{extensao} não é conhecida pelo portal.")

    detectada = detectar_extensao(conteudo, catalogo)
    esperada = _assinatura(declarada)

    if esperada is not None:
        if conteudo[:BYTES_DE_ASSINATURA].startswith(esperada):
            return
    elif detectada is None:
        # Sem assinatura própria: basta não ser outra coisa reconhecível.
        return

    aparenta = f".{detectada}" if detectada else "outro formato"
    raise ErroValidacao(
        f"O conteúdo não é .{extensao}: ele aparenta ser {aparenta}. "
        "O arquivo veio corrompido da origem."
    )


def mime_de(extensao: str, catalogo: list[dict]) -> str | None:
    """O MIME cadastrado da extensão. Cópia de `mime.mime_de`."""
    extensao = (extensao or "").strip().lower().lstrip(".")
    for linha in catalogo:
        if linha["extensao"] == extensao:
            return linha["mime_type"]
    return None


# ── Cópia de `app/documentos/repositories_arquivo.py` ────────────────────────

_CONFLITO_POR_CONSTRAINT = {
    "uq_doc_arquivo_documento_hash": "Este arquivo já é uma versão deste documento.",
    "uq_doc_arquivo_versao": "Esta versão já tem arquivo.",
    "uq_doc_versao_numero": "Outra versão deste documento acabou de ser criada.",
    "uq_doc_documento_vigente": (
        "Outro envio acabou de criar este documento. A próxima rodada grava "
        "este arquivo como nova versão."
    ),
}

# A violação que o espelho trata como SUCESSO. As outras três são corridas
# reais e merecem erro; esta é o caso normal da rodada diária reencontrando a
# mesma certidão.
_DUPLICADO = "uq_doc_arquivo_documento_hash"


@contextmanager
def _conflito_traduzido():
    """Violação conhecida vira `ErroConflito` (409); desconhecida re-levanta.

    Cópia de `repositories_arquivo._conflito_traduzido`, com o mapa reduzido às
    quatro constraints que este caminho pode violar. **Desconhecida re-levanta**,
    e isso é deliberado: engolir constraint nova faria uma regra de integridade
    do Portal virar silêncio aqui.
    """
    try:
        yield
    except pg_errors.UniqueViolation as exc:
        nome = exc.diag.constraint_name
        mensagem = _CONFLITO_POR_CONSTRAINT.get(nome)
        if mensagem is None:
            raise
        if nome == _DUPLICADO:
            raise ArquivoDuplicado(mensagem) from exc
        raise ErroConflito(mensagem) from exc


@contextmanager
def transacao_de_escrita():
    """A transação que envolve documento + versão + arquivo + promoção.

    Cópia de `ArquivoRepository.transacao_de_escrita`. O `with` precisa envolver
    as gravações todas para a tradução de conflito alcançá-las.
    """
    with _conflito_traduzido(), transacao() as conn, conn.cursor() as cur:
        yield conn, cur


# ── O contrato de entrada ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Certidao:
    """Uma certidão pronta para virar documento.

    Dataclass congelado e não doze parâmetros soltos, pelo mesmo argumento do
    `Envio` do Portal: trocar a ordem de `(numero, protocolo)` ou de
    `(data_emissao, data_validade)` grava sem erro, e a tela mostra uma
    certidão vencida como válida.
    """

    conteudo: bytes
    nome_original: str
    id_empresa: int
    id_tipo_documento: int
    titulo: str
    # Nulo = a certidão é da PRÓPRIA empresa. Preenchido = é de um sócio, e aí
    # entra na chave natural (migration `0039`).
    documento_titular: str | None = None
    nome_titular: str | None = None
    data_emissao: date | None = None
    data_validade: date | None = None
    numero: str | None = None
    observacao: str | None = None
    metadados: dict = field(default_factory=dict)

    @property
    def extensao(self) -> str:
        """Do NOME do arquivo, minúscula e sem ponto.

        Do nome e não de um campo próprio, como no `Envio` do Portal: é o nome
        que viaja até o `Content-Disposition`, e derivar a extensão dele é o
        que impede o arquivo de ser servido com uma extensão e baixado com
        outra.
        """
        _, _, sufixo = self.nome_original.rpartition(".")
        return sufixo.strip().lower()


# ── A guarda de colunas ──────────────────────────────────────────────────────

# O que este arquivo escreve, por tabela. É a lista que `conferir_colunas()`
# compara com o `information_schema` do destino — e é por isso que ela é uma
# constante e não uma leitura do próprio SQL: um `INSERT` que deixasse de
# gravar uma coluna continuaria casando consigo mesmo.
COLUNAS_ESCRITAS = {
    "doc_documento": (
        "id_empresa",
        "id_tipo_documento",
        "titulo",
        "data_emissao",
        "data_validade",
        "numero",
        "estado",
        "documento_titular",
        "nome_titular",
    ),
    "doc_documento_versao": (
        "id_documento",
        "numero_versao",
        "motivo",
        "observacao",
        "data_emissao",
        "data_validade",
        "numero",
        "origem_processo",
    ),
    "doc_arquivo": (
        "id_versao",
        "id_documento",
        "conteudo",
        "nome_original",
        "extensao",
        "mime_type",
        "tamanho_bytes",
        "hash_sha256",
        "origem",
    ),
    "doc_historico": ("id_documento", "id_versao", "evento", "origem", "observacao"),
}


def conferir_colunas() -> list[str]:
    """As divergências entre o que a cópia grava e o que o destino exige.

    Devolve uma lista de frases, vazia quando está tudo certo. Duas perguntas:

    1. **coluna que escrevemos e não existe** — a migration mudou de nome, e o
       `INSERT` falharia no meio da rodada;
    2. **coluna `NOT NULL` sem default que não escrevemos** — uma migration
       nova acrescentou campo obrigatório, e o `INSERT` falharia igual.

    É a terceira guarda do cabeçalho, e a única que pega uma migration do
    Portal ANTES de a rodada quebrar. Roda no `/health` e no teste.
    """
    problemas: list[str] = []
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name,
                   is_nullable = 'NO' AS obrigatoria,
                   column_default IS NOT NULL AS tem_default
              FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = ANY(%s)
            """,
            (list(COLUNAS_ESCRITAS),),
        )
        real: dict[str, dict[str, tuple[bool, bool]]] = {}
        for linha in linhas_dict(cur):
            real.setdefault(linha["table_name"], {})[linha["column_name"]] = (
                linha["obrigatoria"],
                linha["tem_default"],
            )

    for tabela, escritas in COLUNAS_ESCRITAS.items():
        colunas = real.get(tabela)
        if not colunas:
            problemas.append(f"a tabela {tabela} não existe no destino")
            continue
        for coluna in escritas:
            if coluna not in colunas:
                problemas.append(f"{tabela}.{coluna}: o espelho grava, e a coluna não existe")
        for coluna, (obrigatoria, tem_default) in sorted(colunas.items()):
            if obrigatoria and not tem_default and coluna not in escritas:
                problemas.append(
                    f"{tabela}.{coluna}: é NOT NULL sem default e o espelho não a grava"
                )
    return problemas


# ── A gravação ───────────────────────────────────────────────────────────────


def _catalogo_de_extensoes() -> list[dict]:
    """O catálogo do destino. Cópia de `TipoDocumentoRepository.extensoes_disponiveis`.

    Lido do BANCO e não de uma constante local: é o Portal que decide quais
    extensões existem e qual assinatura cada uma tem, e uma cópia da tabela
    aqui divergiria no dia em que ele cadastrasse outra.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT extensao, mime_type, assinatura_hex, permite_inline
              FROM doc_extensao
             WHERE ativo
             ORDER BY extensao
            """
        )
        return linhas_dict(cur)


def _extensoes_do_tipo(id_tipo: int) -> list[str]:
    """As extensões que o tipo aceita. Cópia de `extensoes_do_tipo`.

    Tipo sem nenhuma faz o Portal recusar o upload com "este tipo ainda não tem
    extensões configuradas" — e aqui a rodada inteira pararia nele.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.extensao
              FROM doc_tipo_extensao te
              JOIN doc_extensao e ON e.id_extensao = te.id_extensao
             WHERE te.id_tipo_documento = %s
            """,
            (id_tipo,),
        )
        return [linha[0] for linha in cur.fetchall()]


def _documento_do_par(cur, certidao: Certidao) -> dict | None:
    """O documento ATIVO do par empresa×tipo×titular, se houver.

    Cópia de `DocumentoRepository.documento_do_par`, sem a competência — nenhum
    tipo de certidão tem `tem_competencia`, e passá-la sempre nula só
    acrescentaria um parâmetro que nunca varia.

    `IS NOT DISTINCT FROM` e não `=`: o caso comum é titular nulo dos dois
    lados, e `NULL = NULL` nunca casa.
    """
    cur.execute(
        """
        SELECT id_documento, id_versao_atual, titulo
          FROM doc_documento
         WHERE id_empresa = %(empresa)s
           AND id_tipo_documento = %(tipo)s
           AND competencia IS NULL
           AND documento_titular IS NOT DISTINCT FROM %(titular)s
           AND situacao <> 'EXCLUIDO'
        """,
        {
            "empresa": certidao.id_empresa,
            "tipo": certidao.id_tipo_documento,
            "titular": certidao.documento_titular,
        },
    )
    return linha_dict(cur)


def _criar_documento(cur, certidao: Certidao) -> int:
    """Cópia de `DocumentoRepository.criar_documento`.

    `estado = CONCLUIDO` porque o documento nasce COM arquivo: deixá-lo em
    `AGUARDANDO_OBTENCAO` o faria aparecer como pendente no dashboard do Portal.

    Não grava `id_fonte`: a coluna existe no Portal, nunca recebeu FK, e **nada
    lá a escreve**. Preenchê-la aqui criaria a primeira linha divergente.
    """
    cur.execute(
        """
        INSERT INTO doc_documento
               (id_empresa, id_tipo_documento, titulo, data_emissao, data_validade,
                numero, estado, documento_titular, nome_titular)
        VALUES (%(id_empresa)s, %(id_tipo_documento)s, %(titulo)s, %(data_emissao)s,
                %(data_validade)s, %(numero)s, 'CONCLUIDO', %(documento_titular)s,
                %(nome_titular)s)
     RETURNING id_documento
        """,
        {
            "id_empresa": certidao.id_empresa,
            "id_tipo_documento": certidao.id_tipo_documento,
            "titulo": certidao.titulo.strip(),
            "data_emissao": certidao.data_emissao,
            "data_validade": certidao.data_validade,
            "numero": certidao.numero,
            "documento_titular": certidao.documento_titular,
            "nome_titular": certidao.nome_titular,
        },
    )
    return cur.fetchone()[0]


def _proximo_numero_de_versao(cur, id_documento: int) -> int:
    """`MAX + 1`, lido DENTRO da transação.

    Cópia de `proximo_numero_de_versao`. Não é garantia: duas transações
    simultâneas leem o mesmo máximo. Quem garante é `uq_doc_versao_numero`, e a
    segunda recebe 409 traduzido acima.

    **Conta as versões EXCLUÍDAS também**: renumerar por cima de uma versão
    apagada faria o histórico mentir sobre a que foi citada num e-mail.
    """
    cur.execute(
        "SELECT COALESCE(MAX(numero_versao), 0) + 1 FROM doc_documento_versao "
        "WHERE id_documento = %s",
        (id_documento,),
    )
    return cur.fetchone()[0]


def _criar_versao(cur, id_documento: int, numero_versao: int, motivo: str, c: Certidao) -> int:
    """Cópia de `DocumentoRepository.criar_versao`.

    `id_usuario` vai nulo: aqui não há login. Quem distingue esta versão das do
    Portal é `origem_processo`.
    """
    cur.execute(
        """
        INSERT INTO doc_documento_versao
               (id_documento, numero_versao, motivo, observacao, data_emissao,
                data_validade, numero, origem_processo)
        VALUES (%(id_documento)s, %(numero_versao)s, %(motivo)s, %(observacao)s,
                %(data_emissao)s, %(data_validade)s, %(numero)s, %(origem_processo)s)
     RETURNING id_versao
        """,
        {
            "id_documento": id_documento,
            "numero_versao": numero_versao,
            "motivo": motivo,
            "observacao": c.observacao,
            "data_emissao": c.data_emissao,
            "data_validade": c.data_validade,
            "numero": c.numero,
            "origem_processo": ORIGEM,
        },
    )
    return cur.fetchone()[0]


def _gravar_arquivo(cur, id_versao: int, id_documento: int, c: Certidao, mime: str, sha: str):
    """Cópia de `ArquivoRepository.gravar`.

    `Binary` e não `bytes` cru: é o adaptador que o psycopg2 usa para `BYTEA`, e
    sem ele o driver tenta tratar os bytes como texto.
    """
    cur.execute(
        """
        INSERT INTO doc_arquivo
               (id_versao, id_documento, conteudo, nome_original, extensao,
                mime_type, tamanho_bytes, hash_sha256, origem)
        VALUES (%(id_versao)s, %(id_documento)s, %(conteudo)s, %(nome_original)s,
                %(extensao)s, %(mime_type)s, %(tamanho_bytes)s, %(hash_sha256)s,
                %(origem)s)
     RETURNING id_arquivo
        """,
        {
            "id_versao": id_versao,
            "id_documento": id_documento,
            "conteudo": Binary(c.conteudo),
            "nome_original": c.nome_original.strip(),
            "extensao": c.extensao,
            "mime_type": mime,
            "tamanho_bytes": len(c.conteudo),
            "hash_sha256": sha,
            "origem": ORIGEM,
        },
    )
    return cur.fetchone()[0]


def _promover_versao(cur, id_documento: int, id_versao: int, c: Certidao) -> None:
    """Aponta `id_versao_atual` E copia as datas da versão.

    Cópia de `DocumentoRepository.promover_versao`. **As duas coisas numa função
    só**: as datas do documento são as da vigente, e em dois lugares elas
    divergiriam na primeira correção feita de um lado — a listagem mostraria uma
    validade e o detalhe outra.

    `COALESCE` no número, como no original: uma reemissão sem código de controle
    não apaga o da certidão anterior.
    """
    cur.execute(
        """
        UPDATE doc_documento
           SET id_versao_atual = %(id_versao)s,
               data_emissao    = %(data_emissao)s,
               data_validade   = %(data_validade)s,
               numero          = COALESCE(%(numero)s, numero),
               estado          = 'CONCLUIDO'
         WHERE id_documento = %(id_documento)s
        """,
        {
            "id_versao": id_versao,
            "data_emissao": c.data_emissao,
            "data_validade": c.data_validade,
            "numero": c.numero,
            "id_documento": id_documento,
        },
    )


def _registrar_historico(cur, id_documento: int, id_versao: int, observacao: str | None) -> None:
    """Cópia de `historico.registrar`, com o evento fixo em `UPLOAD`.

    Na mesma transação, e não depois: fora dela, a linha sobreviveria ao
    rollback e o histórico afirmaria um upload que não aconteceu.

    `usuario_nome` fica nulo e `origem` leva o carimbo do processo. Sem o
    carimbo, a auditoria mostraria o documento do espelho igual a um enviado
    por pessoa.
    """
    cur.execute(
        """
        INSERT INTO doc_historico (id_documento, id_versao, evento, origem, observacao)
        VALUES (%(id_documento)s, %(id_versao)s, 'UPLOAD', %(origem)s, %(observacao)s)
        """,
        {
            "id_documento": id_documento,
            "id_versao": id_versao,
            "origem": ORIGEM,
            "observacao": observacao,
        },
    )


def gravar(certidao: Certidao) -> dict:
    """Cria o documento (ou a versão nova) e devolve o que aconteceu.

    É a cópia de `ArquivoService.enviar()` para o caminho do robô. A ordem é a
    do original, e tudo cai numa transação só.

    Levanta `ArquivoDuplicado` quando o MESMO PDF já é versão deste documento —
    o caso normal da rodada diária. Quem chama trata como sucesso.

    Devolve `{"id_documento", "id_versao", "numero_versao", "hash_sha256",
    "motivo", "criou"}`.
    """
    sha = calcular_hash(certidao.conteudo)

    catalogo = _catalogo_de_extensoes()
    permitidas = _extensoes_do_tipo(certidao.id_tipo_documento)
    if not permitidas:
        raise ErroValidacao(
            f"O tipo de documento {certidao.id_tipo_documento} não tem extensões "
            "configuradas no Portal — nenhum arquivo entra nele."
        )
    if certidao.extensao not in permitidas:
        raise ErroValidacao(
            f"O tipo não aceita .{certidao.extensao}. Aceita: {', '.join(sorted(permitidas))}."
        )
    conferir_conteudo(certidao.conteudo, certidao.extensao, catalogo)
    mime = mime_de(certidao.extensao, catalogo) or "application/octet-stream"

    with transacao_de_escrita() as (_conn, cur):
        existente = _documento_do_par(cur, certidao)
        criou = existente is None

        if criou:
            id_documento = _criar_documento(cur, certidao)
            motivo = MOTIVO_INCLUSAO
        else:
            id_documento = existente["id_documento"]
            motivo = MOTIVO_RENOVACAO

        numero_versao = _proximo_numero_de_versao(cur, id_documento)
        id_versao = _criar_versao(cur, id_documento, numero_versao, motivo, certidao)
        _gravar_arquivo(cur, id_versao, id_documento, certidao, mime, sha)
        _promover_versao(cur, id_documento, id_versao, certidao)
        _registrar_historico(cur, id_documento, id_versao, certidao.observacao)

    logger.info(
        "Documento %s %s: %s v%s (%s bytes, %s)",
        id_documento,
        "criado" if criou else "renovado",
        certidao.titulo,
        numero_versao,
        len(certidao.conteudo),
        sha[:12],
    )
    return {
        "id_documento": id_documento,
        "id_versao": id_versao,
        "numero_versao": numero_versao,
        "hash_sha256": sha,
        "motivo": motivo,
        "criou": criou,
    }


__all__ = [
    "ORIGEM",
    "ArquivoDuplicado",
    "Certidao",
    "ErroApp",
    "calcular_hash",
    "conferir_colunas",
    "gravar",
]
