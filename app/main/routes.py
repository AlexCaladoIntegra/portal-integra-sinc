"""Blueprint `main` — as telas HTML.

Há uma só: a tela de sincronização. Ela é **casca**, e todo o conteúdo
(diagnóstico, cards e histórico) vem da API por `sincronizacao.js`.

Na Etapa 1 o diagnóstico era renderizado aqui, no servidor. Passou para o JS
quando a tela ganhou execução: depois de cada sincronização o diagnóstico
precisa ser relido — uma queda do Domínio no meio da rodada é exatamente o que
se quer ver —, e manter as duas vias significaria o mesmo card montado em dois
lugares, divergindo na primeira alteração.
"""

from __future__ import annotations

from flask import Blueprint, render_template

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
def inicio():
    """A tela de sincronização."""
    return render_template("index.html", active_nav="inicio")
