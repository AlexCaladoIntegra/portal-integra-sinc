"""Espelho das CNDs: `fiscal-monitor-cpf` → módulo Documentos do Portal.

Lê as certidões negativas de débito que o `fiscal-monitor-cpf` já emitiu e cria
o documento correspondente no Portal, com o PDF disponível para visualizar e
baixar pelas rotas que já existem lá.

**Não consulta o SERPRO.** Cada consulta custa R$ 0,8788 e a decisão de gastar
continua sendo do projeto de origem. Aqui só se espelha o que já foi pago.

A SPEC é `changes/CND-001-espelho-de-cnd-do-fiscal-monitor.md`, no repositório
do `portal-integra` — ela mora lá porque a Fase A foi uma migration de lá.

## O único importador que lê FORA do Domínio

Os outros dez leem `bethadba` e gravam tabelas de BI. Este lê um PostgreSQL
(`app/importacao/fiscal.py`), consulta o Domínio para UMA coisa — de quais
empresas um CPF é sócio — e grava documento com binário.

Daí as duas dependências que ele tem e nenhum outro tem:

- `app/importacao/fiscal.py`, a segunda origem;
- `app/documentos/escrita.py`, a cópia declarada do caminho de gravação do
  Portal.

## Por que a certidão de SÓCIO é metade separada

A CND de uma pessoa física entra nas empresas em que ela é sócia atual, e esse
vínculo não está nem na origem nem no Portal: está em
`bethadba.gequadrosocietario_socios`. Medido em 05/10/2026, dos 33 CPFs com
certidão arquivada **27 são sócios atuais, e 16 respondem por mais de uma
empresa** — a certidão vai para todas, porque quem abre a pasta de qualquer uma
delas precisa encontrá-la.

Sem o Domínio, ou sem o tipo `CND_FEDERAL_SOCIO` que a migration `0039` criou,
essa metade **não é nem tentada**: as certidões de pessoa jurídica continuam
entrando e o placar diz quantas ficaram de fora e por quê. Lê-las sem ter como
resolvê-las custaria a transferência de um PDF por certidão para descartar
todas.

## O que o placar diz além das contagens

"ignoradas: 12" não diz o que fazer. Vão cinco listas junto, e só quando têm
conteúdo — chave vazia em toda resposta treina quem lê a ignorá-las. É a mesma
razão de `nao_cadastradas` no importador de empresas.
"""

from __future__ import annotations

import logging
import re

from ...config import get_settings
from ...data.connection import get_connection, linha_dict, linhas_dict
from ...data.schema import tem_coluna_de_titular
from ...documentos import escrita
from ...shared.errors import ErroValidacao
from .. import dominio, fiscal

logger = logging.getLogger(__name__)

CHAVE = "cnd_documentos"
NOME = "Certidões negativas (CND)"
DESCRICAO = (
    "A CND Federal já emitida pelo fiscal-monitor vira documento no Portal, com "
    "o PDF para visualizar e baixar. A da empresa e a dos sócios dela."
)
FONTE = "fiscal-monitor-cpf · contribuintes, arquivos"

# O código do tipo de documento, por tipo de contribuinte. O CÓDIGO é o
# contrato; `id_tipo_documento` é `SERIAL` e difere entre instalações — medido,
# `CND_FEDERAL` é 186 nesta e nasce 57 num banco criado do zero.
TIPO_POR_CONTRIBUINTE = {"CNPJ": "CND_FEDERAL", "CPF": "CND_FEDERAL_SOCIO"}

# O carimbo que distingue o que este importador gravou, em `doc_arquivo.origem`
# e `doc_documento_versao.origem_processo`. Permite achar tudo que ele criou
# sem depender da data.
ORIGEM_DO_DOCUMENTO = escrita.ORIGEM

# O que a origem grava quando não sabe o nome do contribuinte. Medido em
# 05/10/2026: **9 dos 33 CPFs com certidão arquivada** têm literalmente "Não
# informado" na coluna `nome`.
#
# Sem esta lista, nove documentos entrariam com o título "CND Federal RFB/PGFN
# — Não informado" e com `nome_titular` preenchido com a frase. A tela do
# Portal mostra `nome_titular || documento_titular`, então a frase ESCONDERIA o
# CPF, que é justamente o que desambigua dois sócios da mesma empresa.
NOMES_SEM_NOME = frozenset({"não informado", "nao informado", "não informado.", "-", "--"})

_SO_DIGITOS = re.compile(r"\D")


# ── Leitura da origem ────────────────────────────────────────────────────────

# Uma linha por contribuinte que TEM PDF de CND arquivado, a mais recente.
#
# O JOIN é por `cpf_cnpj` e NÃO por `caminho_disco`, e a razão é medida:
# `contribuintes.cnd_pdf_path` sai de `path.relative` e no Windows vem com
# barra invertida, enquanto `arquivo_referencias.caminho_disco` é normalizado
# com barra normal em `backend/utils/arquivoBanco.js`. Casar por caminho exige
# um `replace` e quebra no dia em que o backend rodar em Linux.
#
# Os bytes vêm de `arquivos.conteudo`. O disco da máquina do backend NUNCA é
# lido: a gravação lá é `void guardarArquivoSemFalhar(...)`, sem `await`, e
# pode falhar em silêncio — contribuinte sem bytes é pulado e contado.
_CERTIDOES = """
SELECT DISTINCT ON (c.cpf_cnpj)
       c.id                     AS id_contribuinte,
       c.tipo,
       c.cpf_cnpj,
       c.nome,
       c.cnd_tipo,
       c.cnd_data_emissao::date AS data_emissao,
       c.cnd_data_validade      AS data_validade,
       c.cnd_codigo_controle    AS numero,
       r.nome_arquivo,
       a.conteudo,
       a.sha256
  FROM contribuintes c
  JOIN arquivo_referencias r ON r.cpf_cnpj = c.cpf_cnpj AND r.origem = 'CND'
  JOIN arquivos a            ON a.id = r.arquivo_id
 WHERE (%(tipo)s IS NULL OR c.tipo = %(tipo)s)
 ORDER BY c.cpf_cnpj, a.created_at DESC
"""

# O quadro societário mais recente, só sócio ATUAL, só pessoa física.
#
# `tipo_insc = 2` é CPF em `gesocios` — sem ela entrariam sócios pessoa
# jurídica, cujo documento nunca casaria com um CPF da origem.
#
# `q.data` faz parte da CHAVE PRIMÁRIA: o quadro é versionado por data. Sem o
# `MAX`, um sócio que saiu e voltou apareceria duas vezes, e a certidão dele
# entraria duplicada na mesma empresa.
#
# `data_saida IS NULL` define sócio ATUAL. Medido, corta 58 dos 905 vínculos.
_VINCULO_SOCIO = """
SELECT RTRIM(s.inscricao) AS cpf,
       q.codi_emp         AS id_empresa
  FROM {schema}.gequadrosocietario_socios q
  JOIN {schema}.gesocios s ON s.i_socio = q.i_socio
 WHERE s.tipo_insc = 2
   AND RTRIM(s.inscricao) <> ''
   AND q.data_saida IS NULL
   AND q.data = (SELECT MAX(q2.data)
                   FROM {schema}.gequadrosocietario_socios q2
                  WHERE q2.codi_emp = q.codi_emp AND q2.i_socio = q.i_socio)
"""


def _digitos(valor: str | None) -> str:
    return _SO_DIGITOS.sub("", valor or "")


def _certidoes(tipo: str | None) -> list[dict]:
    """As certidões com PDF arquivado, uma por contribuinte.

    `tipo` recorta na CONSULTA e não em Python porque `conteudo` é BYTEA: a
    certidão de um contribuinte que vai ser descartado custaria a
    transferência do PDF inteiro pela rede.
    """
    return fiscal.consultar(_CERTIDOES, {"tipo": tipo})


def _contar_na_origem() -> dict:
    """Quantas certidões existem, por tipo, sem trazer um byte de PDF."""
    return fiscal.consultar_um(
        """
        SELECT COUNT(*) FILTER (WHERE x.tipo = 'CNPJ') AS cnpj,
               COUNT(*) FILTER (WHERE x.tipo = 'CPF')  AS cpf
          FROM (SELECT DISTINCT c.cpf_cnpj, c.tipo
                  FROM contribuintes c
                  JOIN arquivo_referencias r
                    ON r.cpf_cnpj = c.cpf_cnpj AND r.origem = 'CND') x
        """
    ) or {"cnpj": 0, "cpf": 0}


def _vinculos_de_socio() -> dict[str, list[int]]:
    """`{cpf: [id_empresa, …]}` dos sócios atuais, lido UMA vez por rodada.

    Uma consulta para o parque inteiro, e não uma por certidão: são 905 linhas
    contra dezenas de idas ao driver ODBC, que é o laço que `dominio.sessao()`
    existe para evitar.

    O CPF é normalizado com `zfill(11)`: `inscricao` é `char(14)` e o zero à
    esquerda se perde em cadastro antigo.

    **O `id_empresa` do Portal É o `codi_emp` do Domínio.** Não há tradução a
    fazer — a PK de `empresas` é o código do ERP.
    """
    sql = _VINCULO_SOCIO.format(schema=get_settings().dominio_schema)
    mapa: dict[str, list[int]] = {}
    for linha in dominio.consultar(sql):
        cpf = (linha["cpf"] or "").strip().zfill(11)
        if len(cpf) == 11:
            mapa.setdefault(cpf, []).append(int(linha["id_empresa"]))
    return mapa


# ── Leitura do destino ───────────────────────────────────────────────────────


def _empresas_por_cnpj() -> dict[str, list[int]]:
    """Todas as empresas ATIVAS indexadas pelo CNPJ só com dígitos.

    Devolve **lista** de ids e não um id, de propósito: medido em 05/10/2026,
    há **33 CNPJs repetidos entre empresas ativas**. Um dicionário de
    `str → int` escolheria uma em silêncio, e a certidão entraria na empresa
    errada — as duas plausíveis, mesma raiz e mesma razão social.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT regexp_replace(cnpj, '[^0-9]', '', 'g') AS digitos, id_empresa
              FROM empresas
             WHERE ativo AND cnpj IS NOT NULL AND btrim(cnpj) <> ''
             ORDER BY id_empresa
            """
        )
        mapa: dict[str, list[int]] = {}
        for linha in linhas_dict(cur):
            if linha["digitos"]:
                mapa.setdefault(linha["digitos"], []).append(linha["id_empresa"])
        return mapa


def _empresas_ativas(ids: list[int]) -> set[int]:
    """Quais dos ids são empresa ATIVA. O quadro societário do Domínio cita
    empresa que o Portal não tem, e gravar nela violaria a FK."""
    if not ids:
        return set()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id_empresa FROM empresas WHERE ativo AND id_empresa = ANY(%s)",
            (list(ids),),
        )
        return {linha[0] for linha in cur.fetchall()}


def _tipos_de_documento() -> dict[str, int]:
    """`{codigo: id_tipo_documento}` dos dois tipos que o espelho usa."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT codigo, id_tipo_documento FROM doc_tipo_documento "
            "WHERE codigo = ANY(%s) AND ativo",
            (sorted(set(TIPO_POR_CONTRIBUINTE.values())),),
        )
        return {linha[0]: linha[1] for linha in cur.fetchall()}


# ── Estado local, para o card ────────────────────────────────────────────────


def resumo() -> dict:
    """O que a tela mostra no card.

    Responde de antemão a pergunta que a contagem crua não responde: "por que
    ele leu 36 e incluiu 0?".
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FILTER (WHERE d.documento_titular IS NULL)     AS de_empresa,
                   COUNT(*) FILTER (WHERE d.documento_titular IS NOT NULL) AS de_socio
              FROM doc_documento d
              JOIN doc_arquivo a ON a.id_versao = d.id_versao_atual
             WHERE a.origem = %s AND d.situacao <> 'EXCLUIDO'
            """,
            (ORIGEM_DO_DOCUMENTO,),
        )
        no_portal = linha_dict(cur) or {"de_empresa": 0, "de_socio": 0}

    dados: dict = {
        "rotulo": "Certidões no Portal",
        "valor": f"{no_portal['de_empresa']} de empresa · {no_portal['de_socio']} de sócio",
    }

    if not get_settings().fiscal_configurado:
        dados["alerta"] = (
            "A conexão com o fiscal-monitor-cpf não está configurada. Defina "
            "FISCAL_HOST e FISCAL_USER no .env — sem ela não há certidão a trazer."
        )
        return dados

    try:
        origem = _contar_na_origem()
    except Exception:
        # O card não pode quebrar porque a origem caiu: o resto da tela depende
        # dele, e o diagnóstico já reporta a ponta fora.
        logger.warning("Não foi possível contar as certidões na origem.", exc_info=True)
        dados["alerta"] = "Não foi possível ler o fiscal-monitor-cpf. Veja o diagnóstico acima."
        return dados

    dados["detalhe"] = f"na origem: {origem['cnpj']} de empresa · {origem['cpf']} de sócio"
    if not origem["cnpj"] and not origem["cpf"]:
        dados["alerta"] = (
            "A origem não tem nenhuma certidão arquivada. Quem emite a CND é o "
            "fiscal-monitor-cpf, e este conjunto só espelha o que já existe lá."
        )
    return dados


# ── A decisão: de quem é esta certidão ───────────────────────────────────────


class _Placar:
    """As contagens e as cinco listas, montadas conforme a rodada anda."""

    def __init__(self) -> None:
        self.lidas = 0
        self.incluidas = 0
        self.atualizadas = 0
        self.ignoradas = 0
        self.sem_empresa: list[str] = []
        self.empresa_ambigua: list[str] = []
        self.ja_arquivada: list[str] = []
        self.sem_validade: list[str] = []
        self.sem_vinculo: list[str] = []
        self.socios_nao_tentados = 0
        self.motivo_dos_socios: str | None = None

    def como_dict(self) -> dict:
        dados = {
            "lidas": self.lidas,
            "incluidas": self.incluidas,
            "atualizadas": self.atualizadas,
            "ignoradas": self.ignoradas,
        }
        for nome in (
            "sem_empresa",
            "empresa_ambigua",
            "ja_arquivada",
            "sem_validade",
            "sem_vinculo",
        ):
            valor = getattr(self, nome)
            if valor:
                dados[nome] = valor
        if self.socios_nao_tentados:
            dados["socios_nao_tentados"] = self.socios_nao_tentados
            dados["motivo_dos_socios"] = self.motivo_dos_socios
        return dados


def _nome_do_titular(certidao: dict) -> str | None:
    """O nome da pessoa, ou `None` quando a origem não o tem.

    `None` e não a frase da origem: com ele a tela cai para o CPF, que
    identifica. Com a frase, nove documentos ficariam indistinguíveis.
    """
    nome = (certidao.get("nome") or "").strip()
    if not nome or nome.lower() in NOMES_SEM_NOME:
        return None
    return nome


def _titulo(certidao: dict) -> str:
    base = "CND Federal RFB/PGFN"
    if certidao["tipo"] == "CPF":
        nome = _nome_do_titular(certidao)
        return f"{base} — {nome}" if nome else base
    return base


def _observacao(certidao: dict) -> str:
    """O rastro da origem, no histórico do documento.

    Guarda o id do contribuinte porque é por ele que se volta à origem quando
    um número não bate. A data de emissão não entra: ela é coluna, e repeti-la
    criaria duas verdades.
    """
    partes = [f"fiscal-monitor contribuinte {certidao['id_contribuinte']}"]
    if certidao.get("cnd_tipo"):
        partes.append(f"tipo {certidao['cnd_tipo']}")
    if certidao.get("sha256"):
        partes.append(f"sha256 da origem {certidao['sha256'][:12]}")
    return " · ".join(partes)


def _empresas_do_cnpj(documento: str, mapa: dict[str, list[int]], placar: _Placar) -> list[int]:
    achadas = mapa.get(documento, [])
    if not achadas:
        placar.sem_empresa.append(documento)
        return []
    if len(achadas) > 1:
        placar.empresa_ambigua.append(documento)
        logger.warning(
            "CNPJ %s está em %s empresas ativas (%s) — certidão não espelhada. "
            "Quem decide qual é delas é quem opera.",
            documento,
            len(achadas),
            achadas,
        )
        return []
    return achadas


def _empresas_do_socio(
    documento: str, vinculos: dict[str, list[int]], ativas: set[int], placar: _Placar
) -> list[int]:
    """As empresas em que esta pessoa é sócia ATUAL.

    Duas razões diferentes dão lista vazia, e elas pedem ação diferente:

    `sem_vinculo` — não é sócia de ninguém no Domínio. Medido, 6 dos 33. Não há
    o que fazer no Portal; o cadastro é da origem.

    `sem_empresa` — é sócia, mas de empresa que o Portal não tem ativa. A ação
    é cadastrar ou reativar.
    """
    codigos = vinculos.get(documento, [])
    if not codigos:
        placar.sem_vinculo.append(documento)
        return []
    no_portal = [c for c in codigos if c in ativas]
    if not no_portal:
        placar.sem_empresa.append(documento)
        logger.info(
            "CPF %s é sócio de %s empresa(s) no Domínio, nenhuma ativa no Portal.",
            documento,
            len(codigos),
        )
        return []
    return no_portal


# ── A gravação de uma certidão ───────────────────────────────────────────────


def _espelhar_uma(
    certidao: dict, id_empresa: int, id_tipo: int, placar: _Placar, dry_run: bool
) -> None:
    documento = _digitos(certidao["cpf_cnpj"])
    titular = documento if certidao["tipo"] == "CPF" else None

    pronta = escrita.Certidao(
        conteudo=bytes(certidao["conteudo"]),
        nome_original=certidao["nome_arquivo"] or "cnd-federal.pdf",
        id_empresa=id_empresa,
        id_tipo_documento=id_tipo,
        titulo=_titulo(certidao),
        documento_titular=titular,
        nome_titular=_nome_do_titular(certidao) if titular else None,
        data_emissao=certidao["data_emissao"],
        data_validade=certidao["data_validade"],
        numero=(certidao.get("numero") or "").strip() or None,
        observacao=_observacao(certidao),
    )

    if dry_run:
        # Na simulação tudo conta como inclusão: sem gravar não há como saber o
        # que já existia sem repetir a consulta local, e a simulação existe
        # para dizer o TAMANHO do trabalho. Mesma decisão dos outros dez.
        placar.incluidas += 1
        return

    try:
        resultado = escrita.gravar(pronta)
    except escrita.ArquivoDuplicado:
        # O caso NORMAL da rodada diária: a mesma certidão, ainda válida, já
        # está arquivada. Sucesso contado em `ignoradas`, nunca falha — é o que
        # `ObtencaoService.persistir` do Portal faz.
        placar.ignoradas += 1
        placar.ja_arquivada.append(documento)
        return

    if resultado["criou"]:
        placar.incluidas += 1
    else:
        placar.atualizadas += 1


def _da_para_socios(tipos: dict[str, int]) -> tuple[bool, str | None]:
    """A metade do sócio pode rodar? E, se não, por quê.

    A frase devolvida vai para o placar e para o log — "não rodou" sem o motivo
    faria alguém procurar no lugar errado.
    """
    if not dominio.configurado():
        return False, (
            "a conexão com o Domínio não está configurada, e é dela que sai o "
            "vínculo entre o CPF do sócio e a empresa"
        )
    if "CND_FEDERAL_SOCIO" not in tipos:
        return False, (
            "o tipo de documento CND_FEDERAL_SOCIO não existe ou está inativo no "
            "Portal — confira se a migration 0039 foi aplicada"
        )
    if not tem_coluna_de_titular():
        return False, (
            "`doc_documento` não tem as colunas de titular: sem elas a certidão "
            "do sócio viraria uma versão da certidão da própria empresa"
        )
    return True, None


# ── Execução ─────────────────────────────────────────────────────────────────


def executar(incluir: bool = False, dry_run: bool = False, ids: list[int] | None = None) -> dict:
    """Espelha as certidões e devolve a contagem.

    `incluir` **não se aplica**: documento não tem "cadastrar o registro novo"
    a decidir — ou a certidão existe na origem, ou não existe. A assinatura o
    mantém porque é o contrato do `REGISTRO`, e o valor pedido volta marcado no
    resultado em vez de ser engolido: sem isso, quem marcasse a opção e visse o
    mesmo número concluiria que ela funcionou. É o que `fiscal_cadastros` faz
    com a seleção de empresa.

    `ids` recorta às empresas informadas, como nos outros conjuntos.
    """
    if not get_settings().fiscal_configurado:
        # Recusa com a mensagem certa, e não com um erro de conexão: a
        # instalação simplesmente não terminou. Os outros dez conjuntos da
        # rodada seguem rodando — o mapa `DEPENDE_DE` não liga nenhum a este.
        raise ErroValidacao(
            "A conexão com o fiscal-monitor-cpf não está configurada. Defina "
            "FISCAL_HOST e FISCAL_USER no .env — sem ela não há certidão a trazer."
        )

    tipos = _tipos_de_documento()
    if "CND_FEDERAL" not in tipos:
        raise ErroValidacao(
            "O tipo de documento CND_FEDERAL não existe ou está inativo no Portal. "
            "Confira o cadastro de tipos do módulo Documentos."
        )

    placar = _Placar()

    fazer_socios, motivo = _da_para_socios(tipos)
    vinculos: dict[str, list[int]] = {}
    ativas: set[int] = set()

    if fazer_socios:
        vinculos = _vinculos_de_socio()
        ativas = _empresas_ativas(sorted({c for lista in vinculos.values() for c in lista}))
        logger.info(
            "Vínculo de sócio: %s CPF(s) ligados a %s empresa(s) ativas no Portal.",
            len(vinculos),
            len(ativas),
        )
    else:
        placar.socios_nao_tentados = _contar_na_origem().get("cpf", 0)
        placar.motivo_dos_socios = motivo
        if placar.socios_nao_tentados:
            logger.warning(
                "%s certidão(ões) de sócio não foram tentadas — %s",
                placar.socios_nao_tentados,
                motivo,
            )

    certidoes = _certidoes(tipo=None if fazer_socios else "CNPJ")
    mapa = _empresas_por_cnpj()
    recorte = set(ids) if ids else None

    for certidao in certidoes:
        placar.lidas += 1
        documento = _digitos(certidao["cpf_cnpj"])
        empresas = (
            _empresas_do_cnpj(documento, mapa, placar)
            if certidao["tipo"] == "CNPJ"
            else _empresas_do_socio(documento, vinculos, ativas, placar)
        )
        if not empresas:
            placar.ignoradas += 1
            continue

        id_tipo = tipos[TIPO_POR_CONTRIBUINTE[certidao["tipo"]]]
        if certidao["data_validade"] is None:
            # Decisão D6 da SPEC: entra assim mesmo, e a tela do Portal o
            # classifica como SEM_VALIDADE — fora do controle de vencimento.
            # A lista é o que torna isso visível em vez de silencioso.
            placar.sem_validade.append(documento)

        for id_empresa in empresas:
            if recorte is not None and id_empresa not in recorte:
                placar.ignoradas += 1
                continue
            _espelhar_uma(certidao, id_empresa, id_tipo, placar, dry_run)

    resultado = placar.como_dict()
    if incluir:
        resultado["selecao_ignorada"] = (
            "Este conjunto não tem o que incluir: ou a certidão existe na origem, "
            "ou não existe. A opção não muda o que é trazido."
        )
        logger.info("CND: `incluir` ignorado — não há registro novo a decidir.")
    logger.info("Espelho de CND%s: %s", " (simulação)" if dry_run else "", resultado)
    return resultado
