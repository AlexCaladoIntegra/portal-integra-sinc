"""O importador de empresas: `bethadba.geempre` → `empresas`.

Domínio e PostgreSQL são dublês. O que está sob teste é a **política**: quais
colunas o importador pode escrever, quem ele pode cadastrar, e o que ele
recusa a fazer.

A divisão de propriedade dos dados é o contrato central:

    do Domínio : razao_social, nome_fantasia, apelido, cnpj
    do Portal  : id_empresa, ativo, nome_exibicao, usa_folha_pagamento

Quebrá-la não dá erro. Sobrescrever `nome_exibicao` apagaria a correção manual
do analista; sobrescrever `ativo` reativaria empresa que alguém desligou de
propósito — e as duas coisas só apareceriam semanas depois, num relatório.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.importacao import dominio
from app.importacao.importadores import empresas
from app.shared.errors import ErroValidacao

# Uma matriz, uma filial, uma inativa e uma com campos vazios na origem.
ORIGEM = [
    {
        "id_empresa": 10,
        "razao_social": "ACME LTDA",
        "nome_fantasia": "ACME",
        "apelido": "ACME MTZ",
        "cnpj": "12345678000181",
        "status": "A",
    },
    {
        "id_empresa": 11,
        "razao_social": "ACME FILIAL LTDA",
        "nome_fantasia": "  ",
        "apelido": "ACME FIL",
        "cnpj": "12345678000290",
        "status": "A",
    },
    {
        "id_empresa": 12,
        "razao_social": "ENCERRADA LTDA",
        "nome_fantasia": "X",
        "apelido": "ENC",
        "cnpj": "99999999000199",
        "status": "I",
    },
]


class CursorFalso:
    """Registra o que foi escrito, sem banco."""

    def __init__(self, escritas):
        self.escritas = escritas

    def execute(self, sql, params=None):
        self.escritas.append(("execute", " ".join(sql.split()), params))

    def executemany(self, sql, seq):
        self.escritas.append(("executemany", " ".join(sql.split()), list(seq)))

    def fetchone(self):
        return (0,)

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def ambiente(monkeypatch):
    """Domínio, banco e sugestão de grupos dublados.

    Devolve o registro de escritas — é por ele que se confere o que o
    importador gravou, e principalmente o que ele NÃO gravou.
    """
    escritas: list = []

    def consultar(_sql, params=()):
        # O filtro por id é aplicado pelo Domínio, via `IN (?,?)`. O dublê
        # precisa honrá-lo: sem isso `executar(ids=[10])` receberia o cadastro
        # inteiro e os testes de escopo passariam por acidente.
        return [dict(x) for x in ORIGEM if not params or x["id_empresa"] in params]

    monkeypatch.setattr(dominio, "consultar", consultar)

    @contextmanager
    def conexao():
        yield type("C", (), {"cursor": lambda _s: CursorFalso(escritas)})()

    monkeypatch.setattr(empresas, "get_connection", conexao)
    monkeypatch.setattr(empresas, "transacao", conexao)
    # A sugestão de grupos tem teste próprio em test_sugestao_grupos.py.
    monkeypatch.setattr(empresas, "_sugerir_grupos", lambda _dry: {})
    return escritas


def no_portal(monkeypatch, ids, ativos=None):
    """Simula o cadastro do Portal: `ids` existem, `ativos` estão ativas."""
    ativos = ids if ativos is None else ativos
    monkeypatch.setattr(
        empresas, "_ids_no_portal", lambda apenas_ativas: set(ativos if apenas_ativas else ids)
    )


def updates(escritas):
    return [e for e in escritas if "UPDATE empresas" in e[1]]


def inserts(escritas):
    return [e for e in escritas if "INSERT INTO empresas" in e[1]]


# ── Leitura ──────────────────────────────────────────────────────────────────


def test_campo_vazio_na_origem_vira_nulo(ambiente):
    """String vazia poluiria a tela e atrapalha a busca."""
    filial = next(linha for linha in empresas.ler(None) if linha["id_empresa"] == 11)
    assert filial["nome_fantasia"] is None


# ── A divisão de propriedade ─────────────────────────────────────────────────


def test_update_toca_apenas_as_quatro_colunas_do_dominio(ambiente, monkeypatch):
    no_portal(monkeypatch, {10, 11})
    empresas.executar()

    sql = updates(ambiente)[0][1]
    for coluna in ("razao_social", "nome_fantasia", "apelido", "cnpj"):
        assert coluna in sql


@pytest.mark.parametrize("coluna", ["ativo", "nome_exibicao", "usa_folha_pagamento"])
def test_update_nunca_toca_coluna_do_portal(ambiente, monkeypatch, coluna):
    """Estas são do Portal. Sobrescrevê-las desfaria decisão do analista — sem
    erro, sem log, e só visível semanas depois."""
    no_portal(monkeypatch, {10, 11})
    empresas.executar()
    assert coluna not in updates(ambiente)[0][1]


def test_insercao_grava_so_id_e_ativo(ambiente, monkeypatch):
    """A empresa nasce com o mínimo; os dados vêm no UPDATE da mesma execução."""
    no_portal(monkeypatch, set())
    empresas.executar(incluir=True, ids=[10])

    sql = inserts(ambiente)[0][1]
    assert "INSERT INTO empresas (id_empresa, ativo)" in sql
    assert "ON CONFLICT (id_empresa) DO NOTHING" in sql


# ── Quem entra, e quem decide ────────────────────────────────────────────────


def test_incluir_sem_ids_e_recusado(ambiente, monkeypatch):
    """A guarda que impede um sincronizador mal configurado de despejar as 920
    empresas do Domínio no Portal. O Portal recebe as empresas que alguém
    decidiu colocar nele, uma a uma."""
    no_portal(monkeypatch, set())
    with pytest.raises(ErroValidacao, match="códigos"):
        empresas.executar(incluir=True)
    assert inserts(ambiente) == []


def test_empresa_inativa_na_origem_nao_e_cadastrada(ambiente, monkeypatch):
    """Não faz sentido trazer para o Portal cadastro que a origem encerrou."""
    no_portal(monkeypatch, set())
    resultado = empresas.executar(incluir=True, ids=[12])
    assert resultado["incluidas"] == 0


def test_empresa_inativada_no_portal_nao_e_reinserida(ambiente, monkeypatch):
    """Reinserir desfaria uma decisão do administrador — por isso a checagem de
    existência considera TODAS, ativas ou não."""
    no_portal(monkeypatch, ids={10}, ativos=set())
    resultado = empresas.executar(incluir=True, ids=[10])
    assert resultado["incluidas"] == 0
    assert inserts(ambiente) == []


def test_so_atualiza_empresa_ativa_no_portal(ambiente, monkeypatch):
    """O escritório tem centenas de empresas no Domínio; só as que participam
    do Portal são espelhadas."""
    no_portal(monkeypatch, ids={10, 11}, ativos={10})
    resultado = empresas.executar()

    assert resultado["atualizadas"] == 1
    assert resultado["ignoradas"] == 2
    assert [linha["id_empresa"] for linha in updates(ambiente)[0][2]] == [10]


def test_diz_quais_codigos_pedidos_ficaram_de_fora(ambiente, monkeypatch):
    """Sem isso a tela só consegue dizer "ignoradas: 920", que não explica nada
    nem diz o que fazer."""
    no_portal(monkeypatch, {10})
    resultado = empresas.executar(ids=[10, 11])
    assert resultado["nao_cadastradas"] == [11]


# ── Simulação ────────────────────────────────────────────────────────────────


def test_dry_run_nao_escreve_nada(ambiente, monkeypatch):
    no_portal(monkeypatch, {10, 11})
    empresas.executar(dry_run=True)
    assert updates(ambiente) == []
    assert inserts(ambiente) == []


def test_dry_run_preve_o_mesmo_numero_da_execucao_real(ambiente, monkeypatch):
    """O previsto considera o que existiria DEPOIS da inclusão. Sem isso a
    simulação diria "atualizadas 0" e a execução real diria 1."""
    no_portal(monkeypatch, set())
    simulado = empresas.executar(incluir=True, ids=[10], dry_run=True)
    assert simulado["incluidas"] == 1
    assert simulado["atualizadas"] == 1


# ── Pendências ───────────────────────────────────────────────────────────────


def test_pendentes_exclui_as_ja_cadastradas_e_as_inativas(ambiente, monkeypatch):
    no_portal(monkeypatch, {10})
    assert [e["id_empresa"] for e in empresas.pendentes(None)] == [11]


def test_pendentes_busca_por_codigo_razao_e_cnpj(ambiente, monkeypatch):
    no_portal(monkeypatch, set())
    assert [e["id_empresa"] for e in empresas.pendentes("11")] == [11]
    assert [e["id_empresa"] for e in empresas.pendentes("acme filial")] == [11]
    assert [e["id_empresa"] for e in empresas.pendentes("12345678000181")] == [10]


def test_sugestao_de_grupos_roda_depois_do_update(ambiente, monkeypatch):
    """A ordem importa: é o UPDATE que traz o `cnpj` do Domínio, e é do CNPJ que
    a sugestão vive. Invertido, a primeira sincronização de uma empresa nova
    não a agruparia."""
    ordem: list[str] = []
    monkeypatch.setattr(empresas, "_sugerir_grupos", lambda _dry: ordem.append("sugestao") or {})

    class CursorQueRegistra(CursorFalso):
        def executemany(self, sql, seq):
            if "UPDATE empresas" in " ".join(sql.split()):
                ordem.append("update")
            super().executemany(sql, seq)

    @contextmanager
    def conexao():
        yield type("C", (), {"cursor": lambda _s: CursorQueRegistra(ambiente)})()

    monkeypatch.setattr(empresas, "transacao", conexao)
    no_portal(monkeypatch, {10, 11})

    empresas.executar()
    assert ordem == ["update", "sugestao"]
