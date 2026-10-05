"""O importador de certidões: casar, decidir e contar (CND-001).

Nenhum teste abre conexão: as duas origens, o destino, a gravação e o catálogo
de tipos são dublados. O que está sob teste é a **decisão** — qual certidão
entra, em qual empresa, e o que o placar diz do que ficou de fora.

As três regras que, quebradas, produzem dano silencioso:

1. **CNPJ ambíguo é pulado.** Há 33 CNPJs repetidos entre empresas ativas do
   Portal, medido em 05/10/2026. Escolher uma poria a certidão na empresa
   errada, e as duas são plausíveis.
2. **`ArquivoDuplicado` é sucesso.** A rodada diária reencontra a mesma
   certidão enquanto ela vale. Tratá-la como falha faria toda rodada a partir
   da segunda terminar em erro.
3. **A certidão de sócio vai para TODAS as empresas dele.** Dos 33 CPFs com
   certidão, 16 respondem por mais de uma empresa.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.documentos import escrita
from app.importacao import dominio
from app.importacao.importadores import cnd_documentos as cnd
from app.shared.errors import ErroValidacao

PDF = b"%PDF-1.4\nfake\n"


def certidao(cpf_cnpj="12345678000181", tipo="CNPJ", **campos):
    base = {
        "id_contribuinte": 1,
        "tipo": tipo,
        "cpf_cnpj": cpf_cnpj,
        "nome": "ACME LTDA",
        "cnd_tipo": "CND",
        "data_emissao": date(2026, 9, 29),
        "data_validade": date(2027, 3, 28),
        "numero": "ABC123",
        "nome_arquivo": "cnd-cnpj-12345678000181.pdf",
        "conteudo": PDF,
        "sha256": "a" * 64,
    }
    return {**base, **campos}


def socio(cpf="33644955808", **campos):
    campos.setdefault("nome", "FULANO DE TAL")
    return certidao(cpf_cnpj=cpf, tipo="CPF", **campos)


@pytest.fixture
def ambiente(monkeypatch, settings):
    """As duas origens, o destino e a gravação dublados.

    `gravadas` guarda as `Certidao` que chegaram a `escrita.gravar` — é por ele
    que se confere o que o importador decidiu, e principalmente o que ele NÃO
    enviou.

    `socios=False` por default: os testes de CNPJ rodam como se o Domínio não
    estivesse configurado, que é o estado em que a metade do sócio simplesmente
    não é tentada.
    """
    estado: dict = {
        "certidoes": [],
        "mapa": {},
        "gravadas": [],
        "resultados": {},
        "socios": False,
        "vinculos": {},
        "ativas": set(),
        "na_origem": {"cnpj": 0, "cpf": 0},
        "tipos": {"CND_FEDERAL": 186, "CND_FEDERAL_SOCIO": 228},
        "tem_titular": True,
    }

    monkeypatch.setattr(
        cnd,
        "get_settings",
        lambda: settings.model_copy(update={"fiscal_host": "x", "fiscal_user": "y"}),
    )
    monkeypatch.setattr(dominio, "configurado", lambda: estado["socios"])
    monkeypatch.setattr(cnd, "tem_coluna_de_titular", lambda: estado["tem_titular"])
    monkeypatch.setattr(cnd, "_vinculos_de_socio", lambda: estado["vinculos"])
    monkeypatch.setattr(cnd, "_contar_na_origem", lambda: estado["na_origem"])
    monkeypatch.setattr(cnd, "_empresas_ativas", lambda _ids: estado["ativas"])
    monkeypatch.setattr(cnd, "_empresas_por_cnpj", lambda: estado["mapa"])
    monkeypatch.setattr(cnd, "_tipos_de_documento", lambda: estado["tipos"])
    monkeypatch.setattr(
        cnd,
        "_certidoes",
        lambda tipo: [c for c in estado["certidoes"] if tipo is None or c["tipo"] == tipo],
    )

    def gravar(c: escrita.Certidao):
        estado["gravadas"].append(c)
        resposta = estado["resultados"].get(c.id_empresa, {"criou": True})
        if resposta.get("duplicado"):
            raise escrita.ArquivoDuplicado("Este arquivo já é uma versão deste documento.")
        return {"id_documento": 1, "id_versao": 1, "numero_versao": 1, "criou": resposta["criou"]}

    monkeypatch.setattr(escrita, "gravar", gravar)
    return estado


# ── O contrato do REGISTRO ───────────────────────────────────────────────────


def test_cumpre_o_contrato_do_registro():
    """Um conjunto sem as quatro constantes some do card sem erro nenhum."""
    for atributo in ("CHAVE", "NOME", "DESCRICAO", "FONTE"):
        assert getattr(cnd, atributo), atributo
    assert callable(cnd.executar)


def test_esta_no_registro_e_no_mapa_de_dependencia():
    """Conjunto novo fora do mapa não bloquearia os dependentes dele — uma
    falha silenciosa de orquestração, que é a pior espécie."""
    from app.importacao.importadores import DEPENDE_DE, REGISTRO

    assert REGISTRO[cnd.CHAVE] is cnd
    assert DEPENDE_DE[cnd.CHAVE] == ("empresas",)


def test_depende_so_de_empresas():
    """A FK do documento é para `empresas`, e nada de BI entra nessa conta.
    Depender do contábil ou do fiscal faria uma falha lá levar a certidão
    junto — e são 187 das 608 empresas sem escrituração contábil."""
    from app.importacao.importadores import DEPENDE_DE, depende_de

    assert depende_de(cnd.CHAVE, {"contabil_plano", "fiscal_dimensoes"}) is None
    assert depende_de(cnd.CHAVE, {"empresas"}) == "empresas"
    assert "contabil_plano" not in DEPENDE_DE[cnd.CHAVE]


# ── A recusa antes de tentar ─────────────────────────────────────────────────


def test_sem_a_origem_configurada_recusa_com_a_mensagem_certa(monkeypatch, settings):
    """Não é erro de conexão: a instalação simplesmente não terminou. E os
    outros dez conjuntos da rodada seguem rodando."""
    monkeypatch.setattr(cnd, "get_settings", lambda: settings)
    with pytest.raises(ErroValidacao, match="FISCAL_HOST"):
        cnd.executar()


def test_sem_o_tipo_no_portal_recusa(ambiente):
    """Sem `CND_FEDERAL` não há onde gravar. Recusar cedo é melhor que dezenas
    de falhas em sequência."""
    ambiente["tipos"] = {}
    with pytest.raises(ErroValidacao, match="CND_FEDERAL"):
        cnd.executar()


# ── O caminho feliz, empresa ─────────────────────────────────────────────────


def test_a_certidao_entra_na_empresa_do_cnpj(ambiente):
    ambiente["certidoes"] = [certidao()]
    ambiente["mapa"] = {"12345678000181": [272]}

    placar = cnd.executar()

    assert placar["lidas"] == 1
    assert placar["incluidas"] == 1
    assert [c.id_empresa for c in ambiente["gravadas"]] == [272]


def test_os_campos_da_origem_chegam_ao_documento(ambiente):
    """Trocar emissão por validade grava sem erro, e a tela mostra uma certidão
    vencida como válida."""
    ambiente["certidoes"] = [certidao()]
    ambiente["mapa"] = {"12345678000181": [272]}

    cnd.executar()
    c = ambiente["gravadas"][0]

    assert c.data_emissao == date(2026, 9, 29)
    assert c.data_validade == date(2027, 3, 28)
    assert c.numero == "ABC123"
    assert c.id_tipo_documento == 186
    assert c.conteudo == PDF
    assert c.documento_titular is None, "certidão de empresa não tem titular"


def test_o_cnpj_e_comparado_so_por_DIGITOS(ambiente):
    """O Portal guarda `cnpj` em `VARCHAR(18)` e a origem só com dígitos. Sem a
    normalização dos dois lados, nenhuma certidão casaria."""
    ambiente["certidoes"] = [certidao(cpf_cnpj="12.345.678/0001-81")]
    ambiente["mapa"] = {"12345678000181": [272]}

    assert cnd.executar()["incluidas"] == 1


# ── O que NÃO entra, e por quê ───────────────────────────────────────────────


def test_cnpj_sem_empresa_e_pulado_e_LISTADO(ambiente):
    ambiente["certidoes"] = [certidao()]
    ambiente["mapa"] = {}

    placar = cnd.executar()

    assert placar["ignoradas"] == 1
    assert placar["sem_empresa"] == ["12345678000181"]
    assert ambiente["gravadas"] == []


def test_cnpj_AMBIGUO_e_pulado_e_nao_resolvido(ambiente):
    """A regra que mais protege deste conjunto.

    Há 33 CNPJs repetidos entre empresas ativas. Escolher uma em silêncio poria
    a certidão na empresa errada, e as duas são plausíveis — mesma raiz, mesma
    razão social, estabelecimentos diferentes.
    """
    ambiente["certidoes"] = [certidao()]
    ambiente["mapa"] = {"12345678000181": [272, 318]}

    placar = cnd.executar()

    assert placar["empresa_ambigua"] == ["12345678000181"]
    assert ambiente["gravadas"] == [], "nenhuma empresa pode ter recebido a certidão"


def test_o_mesmo_pdf_ja_arquivado_conta_como_IGNORADA(ambiente):
    """O caso NORMAL da rodada diária. Tratá-lo como falha faria toda rodada a
    partir da segunda terminar em erro."""
    ambiente["certidoes"] = [certidao()]
    ambiente["mapa"] = {"12345678000181": [272]}
    ambiente["resultados"] = {272: {"duplicado": True}}

    placar = cnd.executar()

    assert placar["ignoradas"] == 1
    assert placar["incluidas"] == 0
    assert placar["ja_arquivada"] == ["12345678000181"]


def test_reemissao_conta_como_ATUALIZADA(ambiente):
    ambiente["certidoes"] = [certidao()]
    ambiente["mapa"] = {"12345678000181": [272]}
    ambiente["resultados"] = {272: {"criou": False}}

    assert cnd.executar()["atualizadas"] == 1


def test_certidao_sem_validade_entra_e_e_LISTADA(ambiente):
    """Decisão D6 da SPEC: 34 dos PDFs do acervo não têm data. Eles entram, e a
    tela do Portal os classifica como SEM_VALIDADE — fora do controle de
    vencimento. A lista torna isso visível em vez de silencioso."""
    ambiente["certidoes"] = [certidao(data_validade=None)]
    ambiente["mapa"] = {"12345678000181": [272]}

    placar = cnd.executar()

    assert placar["incluidas"] == 1
    assert placar["sem_validade"] == ["12345678000181"]
    assert ambiente["gravadas"][0].data_validade is None


# ── A metade do sócio ────────────────────────────────────────────────────────


def test_a_certidao_de_socio_entra_em_TODAS_as_empresas_dele(ambiente):
    """Decisão D5 da SPEC, e o número que a motivou: dos 33 CPFs com certidão,
    16 são sócios de mais de uma empresa — um deles de cinco. Quem abre a pasta
    de qualquer uma precisa encontrá-la."""
    ambiente["socios"] = True
    ambiente["certidoes"] = [socio()]
    ambiente["vinculos"] = {"33644955808": [272, 318, 510]}
    ambiente["ativas"] = {272, 318, 510}

    placar = cnd.executar()

    assert placar["incluidas"] == 3
    assert sorted(c.id_empresa for c in ambiente["gravadas"]) == [272, 318, 510]


def test_o_socio_leva_titular_e_o_tipo_proprio(ambiente):
    """Sem o titular, a CND do sócio viraria uma VERSÃO da CND da própria
    empresa — o defeito que a migration `0039` fechou."""
    ambiente["socios"] = True
    ambiente["certidoes"] = [socio()]
    ambiente["vinculos"] = {"33644955808": [272]}
    ambiente["ativas"] = {272}

    cnd.executar()
    c = ambiente["gravadas"][0]

    assert c.documento_titular == "33644955808"
    assert c.nome_titular == "FULANO DE TAL"
    assert c.id_tipo_documento == 228, "tem de ser CND_FEDERAL_SOCIO"
    assert "FULANO DE TAL" in c.titulo


def test_dois_socios_da_MESMA_empresa_entram_os_dois(ambiente):
    """A prova de que a `0039` serve ao caso real. Antes dela, o segundo
    sobrescreveria o primeiro — sem erro, com a tela mostrando uma certidão
    válida de outra pessoa."""
    ambiente["socios"] = True
    ambiente["certidoes"] = [
        socio(cpf="11111111111", id_contribuinte=1, nome="SOCIO A"),
        socio(cpf="22222222222", id_contribuinte=2, nome="SOCIO B"),
    ]
    ambiente["vinculos"] = {"11111111111": [272], "22222222222": [272]}
    ambiente["ativas"] = {272}

    placar = cnd.executar()

    assert placar["incluidas"] == 2
    assert sorted(c.documento_titular for c in ambiente["gravadas"]) == [
        "11111111111",
        "22222222222",
    ]
    assert {c.id_empresa for c in ambiente["gravadas"]} == {272}


def test_quem_nao_e_socio_de_ninguem_entra_em_sem_vinculo(ambiente):
    """Medido: 6 dos 33 CPFs com certidão não são sócios atuais. Lista separada
    de `sem_empresa` porque a ação é outra — aqui não há o que fazer no Portal,
    o cadastro é da origem."""
    ambiente["socios"] = True
    ambiente["certidoes"] = [socio()]
    ambiente["vinculos"] = {}

    placar = cnd.executar()

    assert placar["sem_vinculo"] == ["33644955808"]
    assert "sem_empresa" not in placar


def test_socio_de_empresa_que_o_portal_nao_tem_entra_em_sem_empresa(ambiente):
    """A outra razão de lista vazia, e ela pede ação diferente: a empresa
    existe no Domínio e não está ativa no Portal."""
    ambiente["socios"] = True
    ambiente["certidoes"] = [socio()]
    ambiente["vinculos"] = {"33644955808": [999]}
    ambiente["ativas"] = set()

    placar = cnd.executar()

    assert placar["sem_empresa"] == ["33644955808"]
    assert "sem_vinculo" not in placar


def test_nome_placeholder_da_origem_vira_NULO(ambiente):
    """Medido: 9 dos 33 CPFs têm literalmente "Não informado" na coluna `nome`.

    A tela do Portal mostra `nome_titular || documento_titular`. Com a frase
    gravada, nove documentos ficariam indistinguíveis e o CPF nunca apareceria.
    """
    ambiente["socios"] = True
    ambiente["certidoes"] = [socio(nome="Não informado")]
    ambiente["vinculos"] = {"33644955808": [272]}
    ambiente["ativas"] = {272}

    cnd.executar()
    c = ambiente["gravadas"][0]

    assert c.nome_titular is None
    assert c.titulo == "CND Federal RFB/PGFN"
    assert c.documento_titular == "33644955808"


def test_nome_de_verdade_e_preservado(ambiente):
    """O inverso da guarda acima: a lista de placeholders não pode engolir um
    nome real."""
    ambiente["socios"] = True
    ambiente["certidoes"] = [socio(nome="  MARIA DA SILVA  ")]
    ambiente["vinculos"] = {"33644955808": [272]}
    ambiente["ativas"] = {272}

    cnd.executar()

    assert ambiente["gravadas"][0].nome_titular == "MARIA DA SILVA"


# ── A degradação PARCIAL ─────────────────────────────────────────────────────


def test_sem_dominio_a_metade_do_socio_NAO_e_tentada(ambiente):
    """As certidões de empresa continuam entrando, e o placar diz quantas de
    sócio ficaram de fora E por quê.

    Lê-las sem ter como resolvê-las custaria a transferência de um PDF por
    certidão para descartar todas — por isso elas nem são lidas, e a contagem
    vem de uma consulta sem bytes.
    """
    ambiente["socios"] = False
    ambiente["na_origem"] = {"cnpj": 1, "cpf": 33}
    ambiente["certidoes"] = [certidao(), socio()]
    ambiente["mapa"] = {"12345678000181": [272]}

    placar = cnd.executar()

    assert placar["incluidas"] == 1, "a certidão de empresa entra mesmo assim"
    assert placar["socios_nao_tentados"] == 33
    assert "Domínio" in placar["motivo_dos_socios"]


def test_sem_a_coluna_de_titular_a_metade_do_socio_tambem_nao_e_tentada(ambiente):
    """DDL aplicada à mão: o banco diz estar na revisão certa e a coluna não
    existe. Sem ela, a certidão do sócio viraria versão da da empresa."""
    ambiente["socios"] = True
    ambiente["tem_titular"] = False
    ambiente["na_origem"] = {"cnpj": 0, "cpf": 7}

    placar = cnd.executar()

    assert placar["socios_nao_tentados"] == 7
    assert "titular" in placar["motivo_dos_socios"]


def test_sem_o_tipo_do_socio_a_metade_dele_nao_e_tentada(ambiente):
    ambiente["socios"] = True
    ambiente["tipos"] = {"CND_FEDERAL": 186}
    ambiente["na_origem"] = {"cnpj": 0, "cpf": 5}

    placar = cnd.executar()

    assert placar["socios_nao_tentados"] == 5
    assert "0039" in placar["motivo_dos_socios"]


# ── O recorte, a simulação e o placar ────────────────────────────────────────


def test_o_recorte_por_empresa_vale_para_os_dois(ambiente):
    ambiente["socios"] = True
    ambiente["certidoes"] = [certidao(), socio()]
    ambiente["mapa"] = {"12345678000181": [272]}
    ambiente["vinculos"] = {"33644955808": [272, 318]}
    ambiente["ativas"] = {272, 318}

    cnd.executar(ids=[318])

    assert [c.id_empresa for c in ambiente["gravadas"]] == [318]


def test_dry_run_nao_grava_nada(ambiente):
    """A simulação existe para dizer o TAMANHO do trabalho."""
    ambiente["certidoes"] = [certidao()]
    ambiente["mapa"] = {"12345678000181": [272]}

    placar = cnd.executar(dry_run=True)

    assert placar["incluidas"] == 1
    assert ambiente["gravadas"] == [], "dry_run não pode chamar a gravação"


def test_incluir_volta_MARCADO_em_vez_de_engolido(ambiente):
    """Quem marcasse a opção e visse o mesmo número concluiria que ela
    funcionou. É o que `fiscal_cadastros` faz com a seleção de empresa."""
    ambiente["certidoes"] = []
    assert "selecao_ignorada" in cnd.executar(incluir=True)
    assert "selecao_ignorada" not in cnd.executar()


def test_as_listas_so_aparecem_quando_tem_conteudo(ambiente):
    """Uma chave com lista vazia em toda resposta treina quem lê a ignorá-las."""
    ambiente["certidoes"] = [certidao()]
    ambiente["mapa"] = {"12345678000181": [272]}

    placar = cnd.executar()

    for nome in ("sem_empresa", "empresa_ambigua", "ja_arquivada", "sem_validade", "sem_vinculo"):
        assert nome not in placar


def test_o_placar_tem_as_quatro_contagens_do_contrato(ambiente):
    """`services.executar_tudo` soma estas quatro. Faltando uma, a rodada
    completa somaria zero sem acusar."""
    ambiente["certidoes"] = [certidao()]
    ambiente["mapa"] = {"12345678000181": [272]}

    placar = cnd.executar()

    for medida in ("lidas", "incluidas", "atualizadas", "ignoradas"):
        assert isinstance(placar[medida], int), medida


# ── O diagnóstico ────────────────────────────────────────────────────────────


def test_a_ponta_fiscal_nao_entra_no_pronto(client, monkeypatch):
    """Tratá-la como bloqueio faria uma instalação sem o fiscal-monitor ficar
    sem sincronizar BI nenhum."""
    from app.health import services as saude

    monkeypatch.setattr(saude, "_checar_origem", lambda: {"situacao": "ok", "mensagem": "a"})
    monkeypatch.setattr(saude, "_checar_destino", lambda: {"situacao": "ok", "mensagem": "b"})
    monkeypatch.setattr(saude, "_checar_fiscal", lambda: {"situacao": "erro", "mensagem": "c"})
    monkeypatch.setattr(
        saude, "_checar_schema", lambda: {"situacao": "ok", "mensagem": "d", "tem_titular": True}
    )

    dados = client.get("/api/v1/sincronizacao/diagnostico").get_json()["data"]

    assert dados["pronto"] is True, "a ponta fiscal não pode bloquear a sincronização"
    assert dados["pronto_para_cnd"] is False


def test_sem_fiscal_configurado_a_linha_diz_ausente_e_nao_erro(client, settings, monkeypatch):
    """ "Não configurado" é estado à parte: não há falha a investigar, há um
    conjunto que esta instalação não usa."""
    from app.health import services as saude

    monkeypatch.setattr(saude, "get_settings", lambda: settings)
    assert saude._checar_fiscal()["situacao"] == "ausente"


def test_a_tela_mostra_a_quarta_linha():
    """Guarda estática: a linha existe no JS e carrega a ressalva de que ela
    não bloqueia."""
    from pathlib import Path

    js = Path("app/static/js/sincronizacao.js").read_text(encoding="utf-8")
    assert "d.fiscal" in js
    assert "fica de fora" in js
