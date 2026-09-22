/* ============================================================================
   portal.js — helpers de requisição comuns a QUALQUER tela do portal.

   Estava dentro de admin.js, e por isso só existia na página /admin. Com o
   primeiro módulo de produto (BI Contábil) a mesma lógica passou a ser
   necessária fora do painel — e envelope de resposta e CSRF são coisas que
   têm de ser tratadas num lugar só.

   Carregar ANTES de qualquer JS de view. `admin.js` mantém `window.Admin`
   apontando para cá, para as views do painel não mudarem.

   Convenções: nada de handler inline no HTML (a CSP bloqueia), e toda
   resposta vem no envelope {data, meta}.
   ============================================================================ */

(function () {
    "use strict";

    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

    function pedir(url, opcoes) {
        opcoes = opcoes || {};
        opcoes.headers = Object.assign(
            { "Content-Type": "application/json", "X-CSRFToken": csrf },
            opcoes.headers || {}
        );
        return fetch(url, opcoes).then(function (resp) {
            /* 204 não tem corpo — resp.json() quebraria com erro de sintaxe.
               É o que a exclusão devolve. */
            if (resp.status === 204) {
                if (!resp.ok) throw new Error("Falha na requisição.");
                return null;
            }
            return resp.json().then(function (corpo) {
                if (!resp.ok) {
                    var info = (corpo && corpo.error) || {};
                    var erro = new Error(info.message || "Falha na requisição.");
                    /* `details` traz {campo: mensagem} nas validações; sem
                       repassar, a tela não teria como marcar o campo errado. */
                    erro.code = info.code;
                    erro.details = info.details;
                    throw erro;
                }
                return corpo;
            });
        });
    }

    /* Escapa para contexto de TEXTO: `<`, `>` e `&`. NÃO escapa aspas, e não
       precisa — entre tags elas não têm significado. Para dentro de um atributo
       use `atributo()`. */
    function texto(valor) {
        var el = document.createElement("span");
        el.textContent = valor == null ? "" : String(valor);
        return el.innerHTML;
    }

    /* Escapa para contexto de ATRIBUTO: o que `texto()` faz, mais as duas
       aspas.

       Existe porque a regra "todo texto que vem do banco passa por
       `Portal.texto()`" estava INCOMPLETA, e a diferença só aparece dentro de um
       atributo: `value="' + texto(nome) + '"` com um `"` no nome sai do atributo
       e o resto do valor passa a ser marcação. Achado na revisão de 10/09/2026
       em `admin_empresas.js`, onde o nome vem de `empresa_email.nome` — texto
       livre de até 120 caracteres, sem restrição de caractere.

       Aplicar em valor sem aspas é INÓCUO: o parser de HTML decodifica `&quot;`
       e `&#39;` de volta, tanto em `getAttribute` quanto em `dataset`. É isso
       que permitiu converter os 31 usos de uma vez, e é isso que permite a
       guarda estática não ter lista de exceções. */
    function atributo(valor) {
        return texto(valor).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function dataHora(iso) {
        if (!iso) return "—";
        var d = new Date(iso);
        return isNaN(d) ? "—" : d.toLocaleString("pt-BR");
    }

    /* Carrega uma listagem paginada por inteiro, seguindo `meta.pagination`.
       Usado pelas telas que mostram a lista completa: a API continua paginada
       (nenhuma resposta é ilimitada), mas a tela não obriga a clicar em
       "carregar mais" para ver o que existe. */
    function pedirTudo(url, pagina) {
        pagina = pagina || 200;
        var itens = [];

        function proxima(offset) {
            var separador = url.indexOf("?") === -1 ? "?" : "&";
            return pedir(url + separador + "limit=" + pagina + "&offset=" + offset)
                .then(function (corpo) {
                    itens = itens.concat(corpo.data || []);
                    var pag = (corpo.meta || {}).pagination || {};
                    if (pag.has_more) return proxima(offset + pagina);
                    return { itens: itens, total: pag.total != null ? pag.total : itens.length };
                });
        }

        return proxima(0);
    }

    window.Portal = {
        pedir: pedir,
        pedirTudo: pedirTudo,
        texto: texto,
        atributo: atributo,
        dataHora: dataHora
    };
}());
