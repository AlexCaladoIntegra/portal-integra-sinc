"""A sugestão de grupo matriz/filiais pela raiz do CNPJ.

`montar_sugestoes` é **pura** — recebe as empresas e devolve os grupos a criar.
É o que permite conferir cada regra com dez linhas de cadastro e sem banco.

Estes testes existem porque o módulo foi **copiado** do Portal, e cópia sem
teste próprio diverge do original na primeira alteração sem nada acusar. Cada
caso aqui é uma regra que, quebrada, produz consolidação errada em silêncio —
nunca um erro.
"""

from __future__ import annotations

from app.matriz_filiais.repositories import FILIAL, MATRIZ
from app.matriz_filiais.sugestao import montar_sugestoes


def empresa(id_empresa, cnpj, nome="ACME LTDA", ja_agrupada=False):
    return {"id_empresa": id_empresa, "nome": nome, "cnpj": cnpj, "ja_agrupada": ja_agrupada}


def test_mesma_raiz_vira_um_grupo_com_a_0001_de_matriz():
    sugestoes, c = montar_sugestoes(
        [
            empresa(10, "12345678000290"),  # filial
            empresa(11, "12345678000181"),  # matriz (sufixo 0001)
        ]
    )
    assert len(sugestoes) == 1
    assert sugestoes[0].matriz == 11
    assert dict(sugestoes[0].membros) == {11: MATRIZ, 10: FILIAL}
    assert c.sugeridos == 1
    assert c.empresas_vinculadas == 2


def test_sem_sufixo_0001_a_matriz_e_o_menor_id():
    """O fallback do `bi-contabil-dominio`, preservado."""
    sugestoes, _ = montar_sugestoes([empresa(30, "12345678000371"), empresa(20, "12345678000290")])
    assert sugestoes[0].matriz == 20


def test_raiz_com_um_membro_so_nao_vira_grupo():
    _, c = montar_sugestoes([empresa(1, "12345678000181")])
    assert c.sugeridos == 0
    assert c.isoladas == 1


def test_cpf_nao_tem_raiz_de_cnpj():
    """11 dígitos é produtor rural ou pessoa física: não pode ter filial."""
    _, c = montar_sugestoes([empresa(1, "33644955808"), empresa(2, "03658887109")])
    assert c.sem_cnpj == 2
    assert c.sugeridos == 0


def test_cnpj_vazio_ou_malformado_conta_como_sem_cnpj():
    _, c = montar_sugestoes([empresa(1, None), empresa(2, ""), empresa(3, "123")])
    assert c.sem_cnpj == 3


def test_sufixo_repetido_e_ambiguo_e_nao_agrupa():
    """Duas ativas com o MESMO CNPJ completo não são matriz e filial: são a
    mesma pessoa jurídica cadastrada duas vezes no Domínio.

    Somar as duas contaria o mesmo movimento em dobro; escolher uma em silêncio
    cortaria o balanço pela metade — com todos os índices ainda plausíveis.
    Fica para o analista, e a contagem diz quais.
    """
    _, c = montar_sugestoes([empresa(1, "12345678000181"), empresa(2, "12345678000181")])
    assert c.sugeridos == 0
    assert c.ambiguas == 1
    assert c.ambiguas_ids == [1, 2]


def test_raiz_ja_agrupada_e_pulada_INTEIRA():
    """A regra que protege o trabalho manual.

    Se o analista renomeou o grupo, tirou uma filial ou juntou dois CNPJs, a
    derivação não opina de novo — e não reagrupa "o resto" da raiz, porque isso
    contrariaria a decisão dele tanto quanto sobrescrever.
    """
    _, c = montar_sugestoes(
        [
            empresa(1, "12345678000181", ja_agrupada=True),
            empresa(2, "12345678000290"),
            empresa(3, "12345678000371"),
        ]
    )
    assert c.sugeridos == 0
    assert c.ja_agrupadas == 1


def test_nome_repetido_ganha_o_codigo_da_matriz():
    """O nome do grupo tem índice único, e o escritório tem razão social
    repetida entre raízes diferentes. Sem desambiguar, a colisão abortaria a
    importação inteira."""
    sugestoes, _ = montar_sugestoes(
        [
            empresa(1, "11111111000191", nome="ACME LTDA"),
            empresa(2, "11111111000272", nome="ACME LTDA"),
            empresa(3, "22222222000191", nome="ACME LTDA"),
            empresa(4, "22222222000272", nome="ACME LTDA"),
        ]
    )
    nomes = sorted(s.nome for s in sugestoes)
    assert nomes == ["ACME LTDA", "ACME LTDA (3)"]


def test_nome_ja_em_uso_no_banco_tambem_desambigua():
    sugestoes, _ = montar_sugestoes(
        [empresa(1, "11111111000191", nome="ACME LTDA"), empresa(2, "11111111000272")],
        nomes_em_uso={"acme ltda"},
    )
    assert sugestoes[0].nome == "ACME LTDA (1)"


def test_cnpj_com_pontuacao_e_normalizado():
    sugestoes, _ = montar_sugestoes(
        [empresa(1, "12.345.678/0001-81"), empresa(2, "12345678000290")]
    )
    assert len(sugestoes) == 1
    assert sugestoes[0].cnpj_raiz == "12345678"


def test_contagens_explicam_o_zero():
    """ "sugeridos 0" pode ser "não há grupo a formar" ou "todos já cadastrados",
    e as duas pedem ação diferente de quem lê o card."""
    _, c = montar_sugestoes(
        [
            empresa(1, "11111111000191", ja_agrupada=True),
            empresa(2, "11111111000272", ja_agrupada=True),
            empresa(3, "22222222000191"),
            empresa(4, "33644955808"),
        ]
    )
    d = c.como_dict()
    assert d["grupos_sugeridos"] == 0
    assert d["raizes_ja_agrupadas"] == 1
    assert d["raizes_isoladas"] == 1
    assert d["empresas_sem_cnpj"] == 1
