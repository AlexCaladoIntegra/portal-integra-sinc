"""A tela — que é CASCA: diagnóstico, cards e histórico vêm da API.

Os testes daqui cobrem o que o servidor decide: que a casca sobe sem depender
de conexão nenhuma, que os elementos que o JS procura existem, e a ordem de
carga de CSS e JS. O conteúdo é testado em `test_sincronizacao.py`.

A divisão importa: até a Etapa 1 o diagnóstico era renderizado aqui e os testes
liam o HTML. Com a montagem no JS, um teste que continuasse lendo o HTML
passaria a testar o template e não o comportamento.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def corpo(client) -> str:
    """O HTML da tela. NÃO abre conexão: a casca não consulta nada."""
    resposta = client.get("/")
    assert resposta.status_code == 200
    return resposta.get_data(as_text=True)


def test_casca_sobe_sem_tocar_nas_conexoes(corpo):
    """As Settings do teste apontam para um banco que não existe.

    Se a tela consultasse o diagnóstico no servidor, este teste esperaria o
    timeout de conexão a cada execução. Que ela não o faça é o que mantém a
    suíte rápida e a tela viva quando as duas pontas caem.
    """
    assert "Sincronizar dados" in corpo


@pytest.mark.parametrize(
    "elemento",
    [
        "sincDiagnostico",  # onde o diagnóstico é montado
        "btnSincronizarTudo",  # o disparo da rodada completa
        "tudoResultado",  # o placar dela
        "sincLista",  # os cards de cada conjunto
        "tbody-historico",  # as últimas execuções
    ],
)
def test_elemento_que_o_js_procura_existe_na_casca(corpo, elemento):
    """Guarda paramétrica no espírito da do Portal.

    `sincronizacao.js` faz `getElementById` de cada um destes. Um id renomeado
    no template deixa a tela em branco **com a suíte verde** — o JS sai pelo
    `if (!lista) return;` sem erro no console.
    """
    assert f'id="{elemento}"' in corpo


def test_botao_de_sincronizar_nasce_desabilitado(corpo):
    """Só o diagnóstico o libera.

    Sem as duas conexões de pé, clicar produziria uma fila de erros no
    histórico e nenhuma informação nova.
    """
    trecho = corpo[corpo.index('id="btnSincronizarTudo"') : corpo.index("Sincronizar tudo")]
    assert "disabled" in trecho


def test_portal_js_carrega_antes_do_js_da_tela(corpo):
    """`sincronizacao.js` usa `window.Portal` no corpo do IIFE.

    Na ordem inversa ele fica indefinido e a tela quebra no navegador — com a
    suíte verde, porque nada disso roda em Python. É a mesma guarda que o
    Portal tem para `portal.js` antes de `admin.js`.
    """
    assert corpo.index("js/portal.js") < corpo.index("js/sincronizacao.js")


def test_todo_script_executavel_leva_nonce(corpo):
    """Sem o nonce, a CSP bloqueia o script e a tela fica em branco em silêncio."""
    import re

    for tag in re.findall(r"<script[^>]*>", corpo):
        assert "nonce=" in tag, f"script sem nonce: {tag}"


def test_colspan_do_vazio_bate_com_as_colunas_da_tabela(corpo):
    """Linha vazia com colspan errado desalinha a tabela inteira."""
    cabecalhos = corpo[corpo.index("<thead>") : corpo.index("</thead>")].count("<th>")
    assert f'colspan="{cabecalhos}"' in corpo


def test_sinc_css_e_a_ultima_folha(corpo):
    """A folha de override deste projeto carrega por último.

    Se `admin.css` ou `importacao.css` vierem depois, `sinc.css` só funciona
    por acidente de especificidade — e o dia em que uma regra do Portal ficar
    mais específica, o ajuste some sem nada acusar.
    """
    posicao_sinc = corpo.index("css/sinc.css")
    for antes in ("css/tokens.css", "css/mobile.css", "css/admin.css", "css/importacao.css"):
        assert corpo.index(antes) < posicao_sinc, f"{antes} deveria vir antes de sinc.css"


def test_cabecalho_mostra_o_banco_de_destino(corpo):
    """A pergunta que este processo tem de responder o tempo todo é "em qual?"."""
    assert "banco_de_teste" in corpo


def test_cabecalho_nao_vaza_credencial(corpo):
    assert "DATABASE_PASSWORD" not in corpo
    assert 'value="teste"' not in corpo


# ── A tela de "aguarde" ──────────────────────────────────────────────────────


@pytest.fixture
def js_da_tela() -> str:
    from pathlib import Path

    return Path("app/static/js/sincronizacao.js").read_text(encoding="utf-8")


def test_o_loader_global_existe_na_casca(corpo):
    """É o overlay que o `sincronizacao.js` aciona. Sem ele no `base.html`, o
    `window.appLoader` fica indefinido e toda execução roda sem "aguarde" —
    em silêncio, porque as chamadas são guardadas."""
    assert 'id="appLoader"' in corpo
    assert "window.appLoader" in corpo


def test_toda_execucao_mostra_o_aguarde(js_da_tela):
    """Os quatro caminhos que disparam trabalho: simular, importar
    selecionadas, sincronizar um card, e a rodada completa."""
    assert js_da_tela.count("aguardar(") >= 4


def test_todo_aguarde_tem_um_esconde_no_finally(js_da_tela):
    """O overlay é modal e bloqueia a página. Um caminho que o mostre e não o
    esconda deixa a tela inutilizável até um F5 — e o erro que causa isso é
    justamente o que o usuário não consegue relatar, porque não dá para clicar
    em nada."""
    assert js_da_tela.count(".finally(pronto)") >= 2


def test_as_chamadas_do_loader_sao_guardadas(js_da_tela):
    """Mesmo padrão do PortalDP: `if (window.dpLoader)`. A tela tem de
    funcionar mesmo se o `base.html` mudar e o loader sumir."""
    assert "if (window.appLoader) window.appLoader.show" in js_da_tela
    assert "if (window.appLoader) window.appLoader.hide" in js_da_tela


def test_nenhum_comentario_de_python_no_javascript(js_da_tela):
    """Um `#` no lugar de `//` é erro de sintaxe: o arquivo inteiro deixa de
    carregar e a tela fica em branco, com a suíte verde."""
    for numero, linha in enumerate(js_da_tela.splitlines(), 1):
        assert not linha.lstrip().startswith("#"), f"linha {numero}: {linha.strip()}"


# ── O beco sem saída de 22/09/2026 ───────────────────────────────────────────


def test_falha_do_diagnostico_NAO_desabilita_o_botao(js_da_tela):
    """Falhar ao LER o diagnóstico não é saber que as pontas estão fora.

    O defeito real: com o pool do PostgreSQL pequeno, o recarregamento
    pós-sincronização estourava, o `/diagnostico` caía junto, e o `.catch`
    desativava o único jeito de agir — sem nada para reativá-lo, porque só o
    próprio diagnóstico o reativa. O operador ficava sem conseguir sincronizar
    de novo.

    O teste lê o bloco do `.catch` de `carregarDiagnostico` e exige que ele não
    desabilite nada.
    """
    inicio = js_da_tela.index("function carregarDiagnostico")
    fim = js_da_tela.index("Card de um conjunto de dados", inicio)
    corpo = js_da_tela[inicio:fim]

    catch = corpo[corpo.index(".catch(") :]
    assert "botao.disabled = true" not in catch
    # E deve dizer que o botão segue liberado, para não parecer defeito.
    assert "segue liberado" in catch


def test_o_pool_comporta_o_recarregamento_da_tela():
    """Ao terminar uma sincronização a tela dispara 12 requisições de uma vez.

    Medido em 22/09/2026: pico de 14 conexões simultâneas no PostgreSQL. Com
    `db_pool_max = 5` — o valor com que este projeto nasceu — seis das doze
    falhavam com `connection pool exhausted`.
    """
    from app.config import Settings

    padrao = Settings(_env_file=None, database_user="x", database_password="x")
    assert padrao.db_pool_max >= 20
