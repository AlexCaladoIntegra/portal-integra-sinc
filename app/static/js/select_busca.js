/* ============================================================================
   select_busca.js — window.SelectBusca

   Transforma um `<select>` grande num campo com BUSCA: digita-se qualquer
   pedaço do texto e a lista filtra na hora.

   **Por que existe.** A digitação nativa de um `<select>` casa só o COMEÇO do
   texto da opção, e o rótulo do lookup de empresa é `id - nome` ("272 - BOB'S")
   — o começo é o id, então digitar "BOB" não achava nada. Numa lista de 589
   empresas, isso é a diferença entre encontrar e rolar.

   **O `<select>` continua no DOM, escondido, e continua sendo a verdade.** É
   ele que tem o `name`, então o formulário submete o id como antes; e as cinco
   telas do BI leem `getElementById("...Empresa").value` para montar o `fetch`.
   Trocá-lo por um `<input>` exigiria mexer nos dois contratos ao mesmo tempo,
   e o campo escondido custa nada.

   ## Duas formas, e a segunda é o `<select multiple>`

   Os filtros de CFOP e Acumulador das telas de Entradas e Saídas escolhem
   VÁRIOS códigos, e nasceram como `<select multiple size="3">` — o controle que
   o navegador dá de graça e que ninguém usa direito: para marcar dois códigos é
   preciso saber que se segura Ctrl, e a lista mostra três linhas de um cadastro
   que tem 382 acumuladores no parque.

   Em `multiple`, o componente:

     * mantém o campo como BUSCA (nunca escreve o rótulo escolhido nele);
     * mostra o escolhido em CHIPS, cada um com o seu botão de tirar;
     * NÃO fecha a lista ao escolher — quem marca dois códigos marca o segundo
       logo depois do primeiro;
     * alterna a opção em vez de trocar `value`, que em `multiple` só leria a
       primeira marcada.

   O `<select>` continua sendo a verdade nas duas formas, e é ele que o
   formulário submete: `?cfop=1102&cfop=1202` sai do navegador sozinho.

   Liga-se sozinho, no `DOMContentLoaded`, em todo `select[data-busca]`. É
   melhoria progressiva de um controle de formulário: sem o JS a tela continua
   com um select comum que funciona.

   Depende de window.Portal (portal.js) só para escapar texto.
   ============================================================================ */

window.SelectBusca = (function () {
    "use strict";

    /* Compara sem acento e sem caixa: "INDUSTRIA" acha "INDÚSTRIA", e o
       contrário também. Sem isto, metade das razões sociais com acento fica
       inalcançável para quem digita sem. */
    function normalizar(texto) {
        return (texto || "")
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .toLowerCase();
    }

    function SelectBusca(select) {
        this.select = select;
        this.multiplo = !!select.multiple;
        this.aberto = false;
        this.indice = -1;
        this.itens = [];

        this._montar();
        this._ligar();
        this._sincronizarTexto();
    }

    SelectBusca.prototype._montar = function () {
        var texto = window.Portal.texto;
        var atributo = window.Portal.atributo;

        this.caixa = document.createElement("div");
        this.caixa.className = "select-busca";

        this.entrada = document.createElement("input");
        this.entrada.type = "text";
        this.entrada.className = "form-input select-busca-campo";
        this.entrada.setAttribute("role", "combobox");
        this.entrada.setAttribute("aria-expanded", "false");
        this.entrada.setAttribute("aria-autocomplete", "list");
        this.entrada.setAttribute("autocomplete", "off");
        this.entrada.placeholder = this.select.getAttribute("data-busca")
            || "Digite para buscar…";

        /* O select MANTÉM o id: as cinco telas do BI o leem por
           `getElementById` para montar o `fetch`, e a guarda de ids da suíte
           confere esses literais contra o template. A entrada recebe um id
           derivado (`<id>Busca`) só para o `<label for>` poder apontar para
           ela — ver abaixo. */
        if (this.select.id) this.entrada.id = this.select.id + "Busca";

        this.lista = document.createElement("div");
        this.lista.className = "select-busca-lista";
        this.lista.setAttribute("role", "listbox");
        this.lista.hidden = true;

        /* A régua de chips vem ANTES do campo: ela cresce para baixo conforme a
           pessoa marca códigos, e com o campo em cima o cursor não se mexeria
           do lugar a cada escolha. */
        if (this.multiplo) {
            this.entrada.setAttribute("aria-multiselectable", "true");
            this.chips = document.createElement("div");
            this.chips.className = "select-busca-chips";
            this.chips.hidden = true;
        }

        this.select.classList.add("select-busca-nativo");
        this.select.hidden = true;
        this.select.setAttribute("tabindex", "-1");

        this.select.parentNode.insertBefore(this.caixa, this.select);
        if (this.chips) this.caixa.appendChild(this.chips);
        this.caixa.appendChild(this.entrada);
        this.caixa.appendChild(this.lista);
        this.caixa.appendChild(this.select);

        /* O rótulo passa a apontar para o campo de busca, que é o que recebe o
           foco. Sem isto, clicar no rótulo focaria um select escondido. */
        var rotulo = this.select.id
            ? document.querySelector('label[for="' + this.select.id + '"]')
            : null;
        if (rotulo && this.entrada.id) rotulo.setAttribute("for", this.entrada.id);

        this._vazioHtml = '<div class="select-busca-vazio">Nada encontrado.</div>';
        this._texto = texto;
        /* `_desenhar` é outro método, logo outro escopo: sem guardar a
           referência aqui ela seria `undefined` lá, e a lista da busca quebraria
           inteira com `ReferenceError`. Mesmo motivo do `_texto` acima. */
        this._atributo = atributo;
    };

    SelectBusca.prototype._ligar = function () {
        var self = this;

        this.entrada.addEventListener("focus", function () {
            self.abrir();
            /* Seleciona o texto atual: quem foca já quer digitar outra coisa, e
               ter de apagar o nome da empresa antes é um passo a mais. */
            self.entrada.select();
        });

        this.entrada.addEventListener("input", function () {
            self.abrir();
            self._desenhar(self.entrada.value);
        });

        this.entrada.addEventListener("keydown", function (evento) {
            if (evento.key === "ArrowDown" || evento.key === "ArrowUp") {
                evento.preventDefault();
                if (!self.aberto) self.abrir();
                self._mover(evento.key === "ArrowDown" ? 1 : -1);
                return;
            }
            if (evento.key === "Enter") {
                if (self.aberto && self.itens[self.indice]) {
                    evento.preventDefault();
                    self.escolher(self.itens[self.indice].valor);
                }
                return;
            }
            if (evento.key === "Escape" && self.aberto) {
                evento.preventDefault();
                self.fechar();
            }
        });

        this.lista.addEventListener("mousedown", function (evento) {
            /* `mousedown` e não `click`: o `blur` da entrada fecharia a lista
               antes de o clique acontecer. */
            var item = evento.target.closest("[data-valor]");
            if (!item) return;
            evento.preventDefault();
            self.escolher(item.getAttribute("data-valor"));
        });

        if (this.multiplo) {
            this.chips.addEventListener("click", function (evento) {
                var botao = evento.target.closest("[data-tirar]");
                if (!botao) return;
                self.alternar(botao.getAttribute("data-tirar"), false);
                self.entrada.focus();
            });

            /* Backspace num campo vazio tira o último chip — é o gesto que todo
               campo de etiquetas tem, e sem ele a única saída é mirar o ×. */
            this.entrada.addEventListener("keydown", function (evento) {
                if (evento.key !== "Backspace" || self.entrada.value) return;
                var marcadas = self._marcadas();
                if (!marcadas.length) return;
                evento.preventDefault();
                self.alternar(marcadas[marcadas.length - 1], false);
            });
        }

        this.entrada.addEventListener("blur", function () {
            /* Fecha e DEVOLVE o texto da opção selecionada. Deixar no campo o
               que a pessoa digitou faria a tela mentir: o filtro submeteria a
               empresa antiga com o nome de outra escrito no campo. */
            window.setTimeout(function () {
                if (!self.caixa.contains(document.activeElement)) self.fechar();
            }, 0);
        });
    };

    /* Os valores marcados, na ordem em que estão no `<select>`. */
    SelectBusca.prototype._marcadas = function () {
        return Array.prototype.filter.call(this.select.options, function (opcao) {
            return opcao.selected && opcao.value !== "";
        }).map(function (opcao) {
            return opcao.value;
        });
    };

    SelectBusca.prototype.abrir = function () {
        if (this.aberto) return;
        this.aberto = true;
        this.lista.hidden = false;
        this.entrada.setAttribute("aria-expanded", "true");
        this._desenhar("");
    };

    SelectBusca.prototype.fechar = function () {
        if (!this.aberto) return;
        this.aberto = false;
        this.lista.hidden = true;
        this.entrada.setAttribute("aria-expanded", "false");
        this.indice = -1;
        this._sincronizarTexto();
    };

    SelectBusca.prototype._opcoes = function () {
        return Array.prototype.map.call(this.select.options, function (opcao) {
            return { valor: opcao.value, rotulo: opcao.textContent.trim() };
        });
    };

    SelectBusca.prototype._desenhar = function (termo) {
        var alvo = normalizar(termo);
        /* No múltiplo, `select.value` devolve só a PRIMEIRA marcada — usá-lo
           aqui deixaria as outras sem marca na lista, e a pessoa remarcaria um
           código que já estava escolhido. */
        var escolhidas = this.multiplo ? this._marcadas() : [this.select.value];
        var texto = this._texto;
        var atributo = this._atributo;

        this.itens = this._opcoes().filter(function (opcao) {
            return !alvo || normalizar(opcao.rotulo).indexOf(alvo) !== -1;
        });

        this.lista.innerHTML = this.itens.length
            ? this.itens.map(function (opcao) {
                  var marcada = escolhidas.indexOf(opcao.valor) !== -1;
                  return '<div class="select-busca-item' + (marcada ? " is-atual" : "") + '"'
                      + ' role="option" aria-selected="' + (marcada ? "true" : "false") + '"'
                      + ' data-valor="' + atributo(opcao.valor) + '">'
                      + texto(opcao.rotulo) + "</div>";
              }).join("")
            : this._vazioHtml;

        /* Começa destacando a opção atual quando ela está na lista: as setas
           passam a andar a partir de onde a pessoa está, e não do topo. */
        var posicao = -1;
        this.itens.forEach(function (opcao, i) {
            if (escolhidas.indexOf(opcao.valor) !== -1 && posicao === -1) posicao = i;
        });
        this.indice = posicao !== -1 ? posicao : (this.itens.length ? 0 : -1);
        this._destacar();
    };

    SelectBusca.prototype._mover = function (passo) {
        if (!this.itens.length) return;
        this.indice = (this.indice + passo + this.itens.length) % this.itens.length;
        this._destacar();
    };

    SelectBusca.prototype._destacar = function () {
        var nos = this.lista.querySelectorAll("[data-valor]");
        var self = this;
        nos.forEach(function (no, i) {
            var ligado = i === self.indice;
            no.classList.toggle("is-marcado", ligado);
            if (ligado && no.scrollIntoView) no.scrollIntoView({ block: "nearest" });
        });
    };

    SelectBusca.prototype.escolher = function (valor) {
        if (this.multiplo) {
            /* ALTERNA: clicar de novo no código já marcado o tira, que é o que
               a lista mostra (ela traz a marca). E a lista NÃO fecha — quem
               escolhe dois códigos escolhe o segundo logo depois do primeiro. */
            var marcadas = this._marcadas();
            this.alternar(valor, marcadas.indexOf(valor) === -1);
            this.entrada.focus();
            return;
        }
        this.select.value = valor;
        /* Dispara `change` no select: é ele que qualquer outro código escuta, e
           o componente não pode ser o único a saber que a escolha mudou. */
        this.select.dispatchEvent(new Event("change", { bubbles: true }));
        this.fechar();
        this.entrada.focus();
    };

    /* Marca ou desmarca UMA opção do `<select multiple>`.

       Mexe em `opcao.selected` e não em `select.value`: em `multiple`, atribuir
       a `value` marca uma e DESMARCA todas as outras, sem erro nenhum — o
       filtro passaria a mandar um CFOP onde a pessoa escolheu três. */
    SelectBusca.prototype.alternar = function (valor, ligar) {
        var opcao = Array.prototype.find.call(this.select.options, function (o) {
            return o.value === valor;
        });
        if (!opcao) return;
        opcao.selected = !!ligar;
        this.select.dispatchEvent(new Event("change", { bubbles: true }));
        this._sincronizarTexto();
        if (this.aberto) this._desenhar(this.entrada.value);
    };

    SelectBusca.prototype._sincronizarTexto = function () {
        if (this.multiplo) return this._sincronizarChips();
        var escolhida = this.select.options[this.select.selectedIndex];
        var rotulo = escolhida ? escolhida.textContent.trim() : "";
        this.entrada.value = rotulo;

        /* O texto completo no `title`, porque o campo pode cortar. A LISTA
           cresce com o conteúdo e nunca corta — é lá que se lê para escolher —,
           mas o campo tem largura fixa: medido em 10/09/2026, o rótulo mais
           longo do parque tem 74 caracteres com o sufixo "— sem dados" e não
           cabe nos 440px. Sem o `title`, a empresa escolhida ficaria sem jeito
           de conferir. */
        this.entrada.title = rotulo;
    };

    /* O campo de busca do múltiplo NUNCA recebe o rótulo escolhido: ele é uma
       busca, e o que está escolhido são os chips. Escrever o rótulo ali faria o
       próximo foco filtrar a lista por ele, mostrando uma opção só. */
    SelectBusca.prototype._sincronizarChips = function () {
        var texto = this._texto;
        var atributo = this._atributo;
        var rotulos = {};
        Array.prototype.forEach.call(this.select.options, function (opcao) {
            rotulos[opcao.value] = opcao.textContent.trim();
        });

        var marcadas = this._marcadas();
        this.chips.hidden = marcadas.length === 0;
        this.chips.innerHTML = marcadas.map(function (valor) {
            var rotulo = rotulos[valor] || valor;
            return '<span class="select-busca-chip" title="' + atributo(rotulo) + '">'
                + texto(rotulo)
                + '<button type="button" class="select-busca-chip-tirar"'
                + ' data-tirar="' + atributo(valor) + '"'
                + ' aria-label="Tirar ' + atributo(rotulo) + '">'
                + "&times;</button></span>";
        }).join("");

        /* O placeholder DIZ quantos estão escolhidos quando há chips: com a
           régua cheia, o campo vazio embaixo dela parece um filtro em branco. */
        this.entrada.placeholder = marcadas.length
            ? "Acrescentar outro… (" + marcadas.length + " escolhido"
                + (marcadas.length === 1 ? "" : "s") + ")"
            : (this.select.getAttribute("data-busca") || "Digite para buscar…");
        this.entrada.title = marcadas.map(function (v) { return rotulos[v] || v; }).join(", ");
    };

    function ligarTodos() {
        document.querySelectorAll("select[data-busca]").forEach(function (select) {
            if (!select.dataset.buscaLigada) {
                select.dataset.buscaLigada = "1";
                new SelectBusca(select);
            }
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", ligarTodos);
    } else {
        ligarTodos();
    }

    SelectBusca.ligarTodos = ligarTodos;
    return SelectBusca;
}());
