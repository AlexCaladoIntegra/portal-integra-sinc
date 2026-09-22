"""Sugestão de grupos a partir da raiz do CNPJ. Pura, sem I/O.

Ponto de partida para o cadastro, não substituto dele: o CNPJ acerta o caso
geral (estabelecimentos da mesma pessoa jurídica) e não sabe nada do caso
particular — grupo econômico com CNPJs distintos, filial que não deve entrar no
consolidado, cadastro duplicado. Quem decide isso é o analista, na tela.

Daí a regra que atravessa o módulo: **sugere o que não existe e nunca toca no
que existe.** Uma raiz cujo qualquer membro já esteja em algum grupo é pulada
inteira — se alguém renomeou o grupo, tirou uma filial ou juntou dois CNPJs, a
derivação não opina de novo. Sem isso, a próxima importação desfaria o trabalho
manual em silêncio, que é a classe de defeito da Regra 9 do BIC-001.

Em Python e não em SQL pelo mesmo motivo de `agregar_por_sintetica`: aqui a
regra se confere com dez linhas de cadastro e sem banco nenhum.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .repositories import FILIAL, MATRIZ

_SO_DIGITOS = re.compile(r"[^0-9]")

# O sufixo de estabelecimento da matriz no CNPJ: dígitos 9 a 12.
SUFIXO_MATRIZ = "0001"


@dataclass(frozen=True)
class Sugestao:
    """Um grupo a criar."""

    nome: str
    cnpj_raiz: str
    matriz: int
    membros: tuple[tuple[int, str], ...]  # (id_empresa, papel)

    def para_repositorio(self) -> list[dict]:
        return [{"id_empresa": i, "papel": papel} for i, papel in self.membros]


@dataclass
class Contagens:
    """O que a importação fez e o que deixou de fazer, e por quê.

    Contagem crua não explica: "sugeridos 0" pode ser "não há grupo a formar" ou
    "todos já estão cadastrados", e as duas pedem ação diferente de quem lê.
    """

    sugeridos: int = 0
    empresas_vinculadas: int = 0
    ja_agrupadas: int = 0  # raízes puladas por já ter grupo
    ambiguas: int = 0  # raízes com CNPJ completo repetido
    sem_cnpj: int = 0  # empresas sem CNPJ de 14 dígitos (CPF, vazio, inválido)
    isoladas: int = 0  # raízes com uma empresa ativa só
    ambiguas_ids: list[int] = field(default_factory=list)

    def como_dict(self) -> dict:
        return {
            "grupos_sugeridos": self.sugeridos,
            "empresas_vinculadas": self.empresas_vinculadas,
            "raizes_ja_agrupadas": self.ja_agrupadas,
            "raizes_ambiguas": self.ambiguas,
            "empresas_sem_cnpj": self.sem_cnpj,
            "raizes_isoladas": self.isoladas,
            "ambiguas_ids": sorted(self.ambiguas_ids),
        }


def _normalizar(nome: str) -> str:
    """Como o índice único compara: sem caixa e sem espaço nas pontas."""
    return " ".join((nome or "").split()).lower()


def montar_sugestoes(
    linhas: list[dict], nomes_em_uso: set[str] | None = None
) -> tuple[list[Sugestao], Contagens]:
    """Grupos a criar a partir das empresas ativas.

    `linhas` são dicts com `id_empresa`, `nome`, `cnpj` e `ja_agrupada` — as
    empresas **ativas** do portal. `nomes_em_uso` são os nomes de grupo que já
    existem, para a sugestão não colidir com o índice único.

    Devolve as sugestões e as contagens do que foi pulado, com o motivo.
    """
    nomes = {_normalizar(n) for n in (nomes_em_uso or set())}
    contagens = Contagens()

    por_raiz: dict[str, list[dict]] = {}
    for linha in linhas:
        digitos = _SO_DIGITOS.sub("", linha.get("cnpj") or "")
        # 11 dígitos é CPF (produtor rural, pessoa física): não tem raiz de
        # CNPJ, logo não pode ter filial. Vazio e malformado, idem.
        if len(digitos) != 14:
            contagens.sem_cnpj += 1
            continue
        por_raiz.setdefault(digitos[:8], []).append({**linha, "sufixo": digitos[8:12]})

    sugestoes: list[Sugestao] = []
    for raiz in sorted(por_raiz):
        membros = sorted(por_raiz[raiz], key=lambda m: m["id_empresa"])

        if len(membros) == 1:
            contagens.isoladas += 1
            continue

        # Uma raiz mexida pelo analista é pulada INTEIRA, e não só na parte que
        # sobrou: reagrupar o resto contrariaria a decisão dele tanto quanto
        # sobrescrever. O índice único em `id_empresa` também barraria o
        # vínculo, mas falhar por constraint não é o comportamento — é pular e
        # reportar.
        if any(m["ja_agrupada"] for m in membros):
            contagens.ja_agrupadas += 1
            continue

        # Sufixo repetido entre ativas não é matriz e filial: é a mesma pessoa
        # jurídica cadastrada duas vezes. Somar as duas contaria o mesmo
        # movimento em dobro; escolher uma em silêncio cortaria o balanço pela
        # metade, com todos os índices ainda plausíveis. Fica para o analista.
        sufixos = [m["sufixo"] for m in membros]
        if len(set(sufixos)) != len(sufixos):
            contagens.ambiguas += 1
            contagens.ambiguas_ids.extend(m["id_empresa"] for m in membros)
            continue

        matriz = next(
            (m for m in membros if m["sufixo"] == SUFIXO_MATRIZ),
            membros[0],  # sem 0001, o menor id — o fallback do bi-contabil-dominio
        )
        nome = _nome_livre(matriz, nomes)
        nomes.add(_normalizar(nome))

        sugestoes.append(
            Sugestao(
                nome=nome,
                cnpj_raiz=raiz,
                matriz=matriz["id_empresa"],
                membros=tuple(
                    (
                        m["id_empresa"],
                        MATRIZ if m["id_empresa"] == matriz["id_empresa"] else FILIAL,
                    )
                    for m in membros
                ),
            )
        )
        contagens.sugeridos += 1
        contagens.empresas_vinculadas += len(membros)

    return sugestoes, contagens


def _nome_livre(matriz: dict, nomes: set[str]) -> str:
    """Nome do grupo: o da matriz, com o código dela quando já estiver em uso.

    O nome tem índice único, então uma colisão abortaria a importação inteira —
    e colisão acontece: o escritório tem razões sociais repetidas entre raízes
    de CNPJ diferentes. Desambiguar pelo código da matriz é feio e é honesto; o
    analista renomeia depois, e o rename não é desfeito porque a raiz passa a
    contar como "já agrupada".
    """
    base = " ".join((matriz["nome"] or f"Empresa {matriz['id_empresa']}").split())
    if _normalizar(base) not in nomes:
        return base
    return f"{base} ({matriz['id_empresa']})"
