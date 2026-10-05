"""A cópia declarada do caminho de gravação (CND-001).

`app/documentos/escrita.py` é o segundo caminho de gravação do módulo
Documentos, e o Portal declara que um segundo caminho **divergiria do primeiro
na primeira correção feita de um lado**. Estes testes são a parte verificável
das três guardas que tornam a cópia sustentável.

O que eles travam:

1. o hash é minúsculo, porque o `CHECK` do banco recusa maiúscula;
2. o conteúdo é conferido por ASSINATURA e não pelo nome;
3. a ordem das gravações é a do original, e cai numa transação só;
4. `uq_doc_arquivo_documento_hash` vira `ArquivoDuplicado` e as outras três
   constraints viram `ErroConflito` — e uma constraint DESCONHECIDA re-levanta;
5. a lista de colunas escritas bate com o `INSERT` de cada tabela.

A guarda que compara com o `information_schema` do destino precisa de banco e
mora em `test_escrita_contra_o_destino.py`.
"""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import pytest
from psycopg2 import errors as pg_errors

from app.documentos import escrita
from app.shared.errors import ErroConflito, ErroValidacao

PDF = b"%PDF-1.4\n%fake\n"
CATALOGO = [
    {"extensao": "pdf", "mime_type": "application/pdf", "assinatura_hex": "25504446"},
    {"extensao": "png", "mime_type": "image/png", "assinatura_hex": "89504E47"},
    {"extensao": "xml", "mime_type": "application/xml", "assinatura_hex": None},
]


def certidao(**campos):
    base = {
        "conteudo": PDF,
        "nome_original": "cnd-federal.pdf",
        "id_empresa": 272,
        "id_tipo_documento": 186,
        "titulo": "CND Federal RFB/PGFN",
    }
    return escrita.Certidao(**{**base, **campos})


# ── 1. O hash ────────────────────────────────────────────────────────────────


def test_o_hash_e_minusculo():
    """O `CHECK` do banco é `^[0-9a-f]{64}$`. Maiúscula faria o INSERT falhar —
    e, pior, faria o mesmo arquivo entrar duas vezes por causa de um `A` contra
    um `a`."""
    sha = escrita.calcular_hash(PDF)
    assert sha == sha.lower()
    assert re.fullmatch(r"[0-9a-f]{64}", sha)


def test_arquivo_vazio_nao_tem_hash():
    with pytest.raises(ValueError):
        escrita.calcular_hash(b"")


# ── 2. A assinatura de bytes ─────────────────────────────────────────────────


def test_pdf_de_verdade_passa():
    escrita.conferir_conteudo(PDF, "pdf", CATALOGO)


def test_pdf_truncado_pela_origem_e_recusado():
    """Um PDF cortado passaria o `tamanho_bytes > 0` do banco e chegaria à tela
    como um arquivo que não abre. A assinatura é o que o pega."""
    with pytest.raises(ErroValidacao, match="não é .pdf"):
        escrita.conferir_conteudo(b"isto nao e um pdf", "pdf", CATALOGO)


def test_a_mensagem_nomeia_o_que_o_conteudo_APARENTA_ser():
    """ "O conteúdo não corresponde" manda adivinhar. Dizer o que ele parece ser
    é o que permite achar o arquivo trocado na origem."""
    png = bytes.fromhex("89504E47") + b"resto"
    with pytest.raises(ErroValidacao, match=r"aparenta ser \.png"):
        escrita.conferir_conteudo(png, "pdf", CATALOGO)


def test_a_assinatura_mais_longa_vence():
    """Formato cuja assinatura é prefixo de outro roubaria a detecção do mais
    específico."""
    catalogo = [
        {"extensao": "aa", "mime_type": "x", "assinatura_hex": "25"},
        {"extensao": "pdf", "mime_type": "application/pdf", "assinatura_hex": "25504446"},
    ]
    assert escrita.detectar_extensao(PDF, catalogo) == "pdf"


def test_extensao_fora_do_catalogo_e_recusada():
    with pytest.raises(ErroValidacao, match="não é conhecida"):
        escrita.conferir_conteudo(PDF, "exe", CATALOGO)


def test_conteudo_vazio_e_recusado():
    with pytest.raises(ErroValidacao, match="vazio"):
        escrita.conferir_conteudo(b"", "pdf", CATALOGO)


def test_o_mime_vem_do_CATALOGO_e_nao_de_um_literal():
    """É o Portal que decide qual MIME cada extensão tem. Uma constante local
    divergiria no dia em que ele mudasse."""
    assert escrita.mime_de("pdf", CATALOGO) == "application/pdf"
    assert escrita.mime_de("zzz", CATALOGO) is None


# ── 3. A extensão sai do NOME ────────────────────────────────────────────────


def test_a_extensao_sai_do_nome_do_arquivo():
    """Do nome e não de um campo próprio: é o nome que viaja até o
    `Content-Disposition`, e derivá-la dele impede o arquivo de ser servido com
    uma extensão e baixado com outra."""
    assert certidao(nome_original="cnd-federal.PDF").extensao == "pdf"
    assert certidao(nome_original="sem_ponto").extensao == "sem_ponto"


# ── 4. A tradução de conflito ────────────────────────────────────────────────


class _Diag:
    """O `diag` que o psycopg2 não deixa escrever.

    `Diagnostics.constraint_name` é um atributo de leitura implementado em C —
    um teste que tentasse atribuí-lo falha com `AttributeError`. O dublê
    reproduz só a forma que `_conflito_traduzido` consulta.
    """

    def __init__(self, nome: str) -> None:
        self.constraint_name = nome


class _UniqueFalsa(pg_errors.UniqueViolation):
    def __init__(self, nome: str) -> None:
        super().__init__(nome)
        self._diag = _Diag(nome)

    @property
    def diag(self):  # type: ignore[override]
        return self._diag


def _violacao(constraint: str):
    return _UniqueFalsa(constraint)


def test_o_mesmo_pdf_no_mesmo_documento_vira_ArquivoDuplicado():
    """É o caso NORMAL da rodada diária, que reencontra a mesma certidão
    enquanto ela vale. O chamador o trata como sucesso — por isso ele tem
    classe própria e não é um `ErroConflito` qualquer."""
    with pytest.raises(escrita.ArquivoDuplicado):  # noqa: SIM117
        with escrita._conflito_traduzido():
            raise _violacao("uq_doc_arquivo_documento_hash")


@pytest.mark.parametrize(
    "constraint",
    ["uq_doc_arquivo_versao", "uq_doc_versao_numero", "uq_doc_documento_vigente"],
)
def test_as_outras_corridas_viram_ErroConflito(constraint):
    with pytest.raises(ErroConflito) as capturado:  # noqa: SIM117
        with escrita._conflito_traduzido():
            raise _violacao(constraint)
    assert not isinstance(capturado.value, escrita.ArquivoDuplicado)


def test_constraint_DESCONHECIDA_re_levanta():
    """Engolir constraint nova faria uma regra de integridade do Portal virar
    silêncio aqui — e o espelho seguiria gravando contra uma regra que ele não
    conhece."""
    with pytest.raises(pg_errors.UniqueViolation):  # noqa: SIM117
        with escrita._conflito_traduzido():
            raise _violacao("uq_que_ninguem_mapeou")


# ── 5. A lista de colunas bate com o SQL ─────────────────────────────────────

FONTE = Path("app/documentos/escrita.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("tabela", sorted(escrita.COLUNAS_ESCRITAS))
def test_toda_coluna_declarada_aparece_no_insert(tabela):
    """`COLUNAS_ESCRITAS` é o que a guarda compara com o destino. Se ela
    divergir do `INSERT` de verdade, a guarda passa a conferir uma ficção.

    O teste lê o `INSERT INTO <tabela>` do próprio arquivo e exige que toda
    coluna declarada esteja lá.
    """
    inicio = FONTE.index(f"INSERT INTO {tabela}")
    trecho = FONTE[inicio : FONTE.index("VALUES", inicio)]
    for coluna in escrita.COLUNAS_ESCRITAS[tabela]:
        assert coluna in trecho, f"{tabela}.{coluna} está em COLUNAS_ESCRITAS e não no INSERT"


def test_o_insert_nao_grava_coluna_fora_da_lista():
    """O inverso do teste acima, e o que mantém a guarda honesta: uma coluna
    gravada e não declarada passaria despercebida se o destino a removesse."""
    for tabela, declaradas in escrita.COLUNAS_ESCRITAS.items():
        inicio = FONTE.index(f"INSERT INTO {tabela}")
        trecho = FONTE[inicio : FONTE.index("VALUES", inicio)]
        achadas = {c.strip() for c in re.findall(r"[a-z_]+(?=[,)\s])", trecho.split("(", 1)[1])}
        extras = {c for c in achadas if c.startswith(("id_", "nome_", "data_"))} - set(declaradas)
        # Só confere o que é claramente nome de coluna: o regex é largo de
        # propósito, e a asserção é sobre o que ele acerta com certeza.
        assert not extras, f"{tabela}: {extras} no INSERT e fora de COLUNAS_ESCRITAS"


def test_id_fonte_nunca_e_gravado():
    """A coluna existe no Portal, nunca recebeu FK e **nada lá a escreve**.
    Preenchê-la aqui criaria a primeira linha divergente.

    A guarda olha só os `INSERT`: o cabeçalho de `_criar_documento` cita a
    coluna para explicar por que ela fica de fora, e proibir a palavra no
    arquivo inteiro proibiria a explicação junto.
    """
    for tabela in escrita.COLUNAS_ESCRITAS:
        inicio = FONTE.index(f"INSERT INTO {tabela}")
        trecho = FONTE[
            inicio : FONTE.index("RETURNING", inicio)
            if "RETURNING" in FONTE[inicio : inicio + 900]
            else inicio + 900
        ]
        assert "id_fonte" not in trecho, tabela


def test_created_at_e_updated_at_nao_sao_escritos():
    """São DEFAULT mais trigger no Portal, e há teste estático varrendo o
    repositório de lá. A cópia respeita a mesma regra."""
    for tabela, colunas in escrita.COLUNAS_ESCRITAS.items():
        assert "created_at" not in colunas, tabela
        assert "updated_at" not in colunas, tabela


# ── 6. As invariantes que o cabeçalho promete ────────────────────────────────


def test_o_binario_vai_por_Binary_e_nao_bytes_cru():
    """É o adaptador do `BYTEA`. Sem ele o driver trata os bytes como texto."""
    assert "Binary(c.conteudo)" in FONTE


def test_tudo_numa_transacao_so():
    """Separar as gravações deixaria estados que nenhuma tela mostra: versão
    sem arquivo, arquivo sem promoção, documento sem vigente."""
    inicio = FONTE.index("def gravar(certidao: Certidao)")
    corpo = FONTE[inicio:]
    assert corpo.count("with transacao_de_escrita()") == 1
    for passo in (
        "_criar_versao(",
        "_gravar_arquivo(",
        "_promover_versao(",
        "_registrar_historico(",
    ):
        assert passo in corpo


def test_a_ordem_das_gravacoes_e_a_do_original():
    """documento → versão → arquivo → promoção → histórico."""
    corpo = FONTE[FONTE.index("def gravar(certidao: Certidao)") :]
    ordem = [
        corpo.index("_criar_documento(cur"),
        corpo.index("_criar_versao(cur"),
        corpo.index("_gravar_arquivo(cur"),
        corpo.index("_promover_versao(cur"),
        corpo.index("_registrar_historico(cur"),
    ]
    assert ordem == sorted(ordem)


def test_a_origem_carimba_as_tres_tabelas():
    """Sem o carimbo, a auditoria mostraria o documento do espelho igual a um
    enviado por pessoa."""
    assert escrita.ORIGEM == "cnd:fiscal-monitor"
    assert FONTE.count('"origem": ORIGEM') >= 2
    assert '"origem_processo": ORIGEM' in FONTE


def test_o_titular_entra_na_busca_do_par():
    """Sem ele, a CND de um sócio acharia a CND da empresa e viraria uma versão
    dela — o defeito que a `0039` fechou."""
    corpo = FONTE[FONTE.index("def _documento_do_par") : FONTE.index("def _criar_documento")]
    assert "documento_titular IS NOT DISTINCT FROM" in corpo
    assert "documento_titular = " not in corpo


def test_a_certidao_e_congelada():
    """Trocar a ordem de `(data_emissao, data_validade)` grava sem erro, e a
    tela mostra uma certidão vencida como válida."""
    c = certidao(data_validade=date(2027, 3, 28))
    with pytest.raises(FrozenInstanceError):
        c.data_validade = date(2020, 1, 1)
