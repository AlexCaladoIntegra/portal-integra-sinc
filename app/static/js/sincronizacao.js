/* ============================================================================
   sincronizacao.js — a tela de sincronização.

   Adaptado do `admin_importacao.js` do Portal Integra. O que mudou:

   - o prefixo da API (`/api/v1/sincronizacao`);
   - `window.Portal` direto, no lugar de `window.Admin` — aquele apelido mora
     no `admin.js` do Portal, que é o shell de abas do painel e não existe aqui;
   - o card de DIAGNÓSTICO e o botão "Sincronizar tudo", que são desta tela
     — e este último lê um FLUXO NDJSON, mostrando cada fase conforme ela
     termina, em vez de esperar 24 minutos por uma resposta só;
   - as opções por importador saíram junto com o gancho `OPCOES` (ver o
     cabeçalho de `app/importacao/importadores/__init__.py`);
   - o histórico traz linhas das DUAS origens, e a coluna "Por" distingue
     quem disparou no Portal de quem disparou aqui.

   O card de cada conjunto de dados se monta a partir do que a API descreve.
   Para importador que sabe dizer o que falta (`tem_pendentes`), a tela mostra
   a lista do que ainda não está no Portal, com marcação — nada de digitar
   código e descobrir depois que nada aconteceu.
   ============================================================================ */

(function () {
    "use strict";

    var API = "/api/v1/sincronizacao";
    var pedir = window.Portal.pedir;
    var pedirTudo = window.Portal.pedirTudo;
    var texto = window.Portal.texto;
    var atributo = window.Portal.atributo;
    var dataHora = window.Portal.dataHora;

    var lista = document.getElementById("sincLista");
    if (!lista) return;

    // Pendências carregadas por importador, para o filtro rodar sem ir ao servidor.
    var pendentes = {};

    /* ── A tela de "aguarde" ──────────────────────────────────────────────────
       Mesmo padrão do PortalDP (`dpLoader.show()` / `.hide()`): overlay modal
       antes de toda execução, escondido no `.finally`. O `window.appLoader` é
       a mesma coisa com outro nome, e já vinha no `base.html` copiado do
       Portal Integra — até aqui, sem ninguém o chamar.

       Ele NÃO substitui o texto "Sincronizando…" do card: aquele diz o que
       aconteceu e fica na tela depois; este diz que a máquina está ocupada
       AGORA e impede clique enquanto isso. Uma sincronização de lançamentos
       leva ~2 minutos por empresa, e sem o overlay a tela parece travada —
       clicar de novo é a reação natural, e é o que a trava de sessão do
       PostgreSQL recusa com 409.

       Guardado em `if (window.appLoader)`, como no PortalDP: a tela tem de
       funcionar mesmo se o `base.html` mudar e o loader sumir. */
    function aguardar(mensagem) {
        if (window.appLoader) window.appLoader.show(mensagem);
    }

    function pronto() {
        if (window.appLoader) window.appLoader.hide();
    }

    function nomeDoCard(card) {
        var titulo = card.querySelector(".page-title");
        return titulo ? titulo.textContent.trim() : "os dados";
    }

    /* ── Leitura de fluxo NDJSON ──────────────────────────────────────────────
       `POST /tudo` transmite uma linha JSON por evento, conforme acontece, em
       vez de responder uma vez ao fim de 24 minutos. `Portal.pedir` não serve
       aqui: ele lê o envelope de uma resposta inteira.

       Chama `aoEvento` para cada evento de progresso e resolve com o `fim`.

       **Fim ausente é FALHA, não sucesso.** A conexão pode cair no meio, e o
       status HTTP já saiu 200 antes da primeira fase — sem esta verificação,
       uma rodada interrompida na metade se leria como concluída. */
    function lerFluxo(url, corpo, aoEvento) {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return fetch(url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": meta ? meta.getAttribute("content") : ""
            },
            body: JSON.stringify(corpo || {})
        }).then(function (resposta) {
            if (!resposta.ok) {
                throw new Error("O servidor recusou a rodada (HTTP " + resposta.status + ").");
            }
            var leitor = resposta.body.getReader();
            var decodificador = new TextDecoder();
            var resto = "";
            var fim = null;

            function processar(pedaco) {
                resto += pedaco;
                var linhas = resto.split("\n");
                // A última pode estar cortada no meio: volta para o buffer.
                resto = linhas.pop();
                linhas.forEach(function (linha) {
                    if (!linha.trim()) return;
                    var evento = JSON.parse(linha);
                    if (evento.evento === "fim") fim = evento;
                    else aoEvento(evento);
                });
            }

            function puxar() {
                return leitor.read().then(function (r) {
                    if (r.done) {
                        processar(decodificador.decode());
                        if (!fim) {
                            throw new Error(
                                "A conexão caiu antes do fim da rodada. Veja o histórico "
                                + "abaixo para saber até onde ela chegou."
                            );
                        }
                        return fim;
                    }
                    processar(decodificador.decode(r.value, { stream: true }));
                    return puxar();
                });
            }
            return puxar();
        });
    }

    /* ── Formatação ───────────────────────────────────────────────────────── */

    function contagens(exec) {
        if (!exec) return "";
        return "lidas " + exec.lidas
            + " · incluídas " + exec.incluidas
            + " · atualizadas " + exec.atualizadas
            + " · ignoradas " + exec.ignoradas;
    }

    function cnpjFormatado(cnpj) {
        if (!cnpj || cnpj.length !== 14) return cnpj || "";
        return cnpj.slice(0, 2) + "." + cnpj.slice(2, 5) + "." + cnpj.slice(5, 8)
            + "/" + cnpj.slice(8, 12) + "-" + cnpj.slice(12);
    }

    var CHIP = { sucesso: "regular", erro: "crit", executando: "media" };

    function chipSituacao(status) {
        return '<span class="ativo-chip ativo-chip--' + (CHIP[status] || "inativo") + '">'
            + '<span class="ativo-chip-dot"></span>' + texto(status) + "</span>";
    }

    /* ── Diagnóstico ──────────────────────────────────────────────────────────
       Cor NUNCA sozinha: chip colorido + rótulo textual. É a regra do
       tokens.css, e num diagnóstico a situação é a informação inteira. */

    var CHIP_DIAG = {
        ok: "regular", erro: "crit", divergente: "media",
        ausente: "inativo", desconhecido: "inativo"
    };
    var ROTULO_DIAG = {
        ok: "OK", erro: "Falhou", divergente: "Divergente",
        ausente: "Não configurado", desconhecido: "Desconhecido"
    };

    function linhaDiagnostico(titulo, estado) {
        return '<div class="import-resumo">'
            + "<strong>" + texto(titulo) + ":</strong> "
            + '<span class="ativo-chip ativo-chip--' + (CHIP_DIAG[estado.situacao] || "inativo") + '">'
            +   '<span class="ativo-chip-dot"></span>' + texto(ROTULO_DIAG[estado.situacao] || estado.situacao)
            + "</span>"
            + '<p class="page-subtitle">' + texto(estado.mensagem) + "</p>"
            + "</div>";
    }

    function carregarDiagnostico() {
        var alvo = document.getElementById("sincDiagnostico");
        var botao = document.getElementById("btnSincronizarTudo");
        if (!alvo) return;

        return pedir(API + "/diagnostico").then(function (corpo) {
            var d = corpo.data;
            alvo.innerHTML = linhaDiagnostico("Origem · Domínio", d.origem)
                + linhaDiagnostico("Destino · PostgreSQL do Portal", d.destino)
                + linhaDiagnostico("Schema do destino", d.schema);

            // O botão não fica escondido quando não dá para sincronizar: fica
            // DESABILITADO, com o motivo logo acima. Esconder o controle faria
            // a tela parecer quebrada em vez de explicada.
            if (botao) {
                botao.disabled = !d.pronto;
                botao.title = d.pronto
                    ? "Roda todos os conjuntos de dados, na ordem"
                    : "Corrija as conexões acima para liberar a sincronização";
            }
        }).catch(function (erro) {
            alvo.innerHTML = '<div class="import-resultado import-resultado--aviso">'
                + "Não foi possível ler o diagnóstico: " + texto(erro.message)
                + " O botão segue liberado — se as conexões estiverem mesmo fora, "
                + "a sincronização dirá o motivo.</div>";
            // O botão NÃO é desabilitado aqui, e a diferença custou um defeito:
            // falhar ao LER o diagnóstico não é o mesmo que saber que as pontas
            // estão fora. Quando o pool do PostgreSQL estourava no recarregamento
            // pós-sincronização, esta linha desativava o único jeito de agir — e
            // nada o reativava, porque só o próprio diagnóstico o reativa.
            //
            // Um diagnóstico ilegível deixa a tela SEM informação; tirar o botão
            // junto a deixa sem saída.
        });
    }

    /* ── O lookup de empresa ──────────────────────────────────────────────────
       Vazio = o parque inteiro, que é o default e o que a rodada agendada faz.
       Escolhida uma empresa, os dez conjuntos rodam só para ela.

       A lista vem de `/empresas` — o que o PORTAL tem — e não de
       `/<chave>/pendentes`, que lista o que a ORIGEM tem e o Portal não. São
       universos diferentes: só se pode sincronizar o que já está cadastrado. */

    function empresaEscolhida() {
        var sel = document.getElementById("selEmpresa");
        var valor = sel && sel.value;
        return valor ? Number(valor) : null;
    }

    function rotularBotao() {
        var rotulo = document.getElementById("btnSincronizarTudoRotulo");
        var sel = document.getElementById("selEmpresa");
        if (!rotulo || !sel) return;
        // "Sincronizar tudo" com uma empresa escolhida seria mentira: o botão
        // passa a dizer de quem.
        rotulo.textContent = sel.value
            ? "Sincronizar " + sel.options[sel.selectedIndex].textContent.trim()
            : "Sincronizar tudo";
    }

    function carregarEmpresas() {
        var sel = document.getElementById("selEmpresa");
        var ajuda = document.getElementById("selEmpresaAjuda");
        if (!sel) return;

        return pedirTudo(API + "/empresas").then(function (r) {
            r.itens.forEach(function (e) {
                var opcao = document.createElement("option");
                opcao.value = e.id_empresa;
                // "272 - BOB'S", o mesmo rótulo dos lookups do Portal. O
                // encerramento na origem aparece aqui porque é onde alguém
                // está prestes a escolher a empresa.
                opcao.textContent = e.id_empresa + " - " + (e.nome || "(sem nome)")
                    + (e.situacao_origem && e.situacao_origem !== "A" ? "  [encerrada na origem]" : "");
                sel.appendChild(opcao);
            });
            if (ajuda) {
                ajuda.textContent = r.itens.length
                    + " empresa(s) ativas. Sem escolha, a rodada percorre todas.";
            }
            // O componente decora `select[data-busca]` no DOMContentLoaded, que
            // já passou quando as opções chegam — daí a religada explícita.
            if (window.SelectBusca) window.SelectBusca.ligarTodos();
            sel.addEventListener("change", rotularBotao);
            rotularBotao();
        }).catch(function (erro) {
            if (ajuda) ajuda.textContent = "Não foi possível listar as empresas: " + erro.message;
        });
    }

    /* ── Card de um conjunto de dados ─────────────────────────────────────── */

    function blocoSelecao() {
        return '<div class="import-selecao">'
            + '<div class="import-selecao-cabecalho">'
            +   '<span class="usuario-block-label">Disponíveis para importar</span>'
            +   '<span class="emp-count" data-selecao-contagem>carregando…</span>'
            + "</div>"
            + '<div class="emp-search-row">'
            +   '<span class="material-symbols-outlined">search</span>'
            +   '<input type="text" data-selecao-filtro placeholder="Filtrar...">'
            + "</div>"
            + '<div class="emp-actions">'
            +   '<button type="button" class="emp-action-btn" data-selecao-todas>Marcar todas</button>'
            +   '<span class="emp-action-sep">·</span>'
            +   '<button type="button" class="emp-action-btn" data-selecao-nenhuma>Desmarcar</button>'
            + "</div>"
            + '<div class="emp-list import-selecao-lista" data-selecao-lista>'
            +   '<div class="emp-empty">Carregando…</div>'
            + "</div>"
            + "</div>";
    }

    function acoes(item) {
        if (item.tem_pendentes) {
            return '<button class="btn-primary btn-primary--accent" data-acao="importar-selecionadas" disabled>'
                +   '<span class="material-symbols-outlined">cloud_download</span>'
                +   " Importar selecionadas"
                + "</button>"
                + '<button class="btn-secondary" data-acao="atualizar">'
                +   '<span class="material-symbols-outlined">refresh</span>'
                +   " Atualizar as já cadastradas"
                + "</button>";
        }
        return '<button class="btn-secondary" data-acao="simular">'
            +   '<span class="material-symbols-outlined">visibility</span> Simular'
            + "</button>"
            + '<button class="btn-primary" data-acao="importar">'
            +   '<span class="material-symbols-outlined">cloud_download</span> Sincronizar'
            + "</button>";
    }

    // `detalhe` e `alerta` são condicionais, e no Portal ficaram meses sendo
    // montados pelos importadores e descartados aqui — com o sintoma mudo por
    // construção: a ausência deles se lê como "não há nada a avisar". O que se
    // perdia era a instrução do que reimportar.
    function blocoResumo(resumo) {
        return '<div class="import-resumo">'
            +   "<strong>" + texto(resumo.rotulo) + ":</strong> " + texto(resumo.valor)
            +   (resumo.detalhe ? " · " + texto(resumo.detalhe) : "")
            +   (resumo.alerta
                    ? '<p class="import-alerta">' + texto(resumo.alerta) + "</p>"
                    : "")
            + "</div>";
    }

    function autorDe(exec) {
        // As linhas daqui têm id_usuario nulo — não há login. As do Portal têm
        // autor. Mostrar "null" seria pior que não mostrar nada.
        if (exec.nome_usuario) return texto(exec.nome_usuario);
        return exec.origem === "sinc" ? "pelo sincronizador" : "—";
    }

    function card(item) {
        var ultima = item.ultima_execucao;

        return '<div class="admin-card import-card" data-chave="' + atributo(item.chave) + '"'
            + ' data-pendentes="' + (item.tem_pendentes ? "1" : "0") + '">'
            + '<div class="page-header">'
            +   "<div>"
            +     '<h3 class="page-title">' + texto(item.nome) + "</h3>"
            +     '<p class="page-subtitle">' + texto(item.descricao) + "</p>"
            +     '<p class="page-subtitle"><strong>Origem:</strong> ' + texto(item.fonte) + "</p>"
            +   "</div>"
            + "</div>"
            + (item.resumo ? blocoResumo(item.resumo) : "")
            + '<div class="import-ultima page-subtitle">'
            +   (ultima
                    ? "Última sincronização: " + dataHora(ultima.concluida_em || ultima.iniciada_em)
                      + " " + autorDe(ultima)
                      + " — " + contagens(ultima)
                    : "Nunca sincronizado.")
            + "</div>"
            + (item.tem_pendentes ? blocoSelecao() : "")
            + '<div class="import-acoes">' + acoes(item) + "</div>"
            + '<div class="import-resultado" hidden></div>'
            + "</div>";
    }

    function carregarLista() {
        return pedir(API).then(function (corpo) {
            var itens = corpo.data || [];
            lista.innerHTML = itens.length
                ? itens.map(card).join("")
                : '<div class="admin-card"><p class="import-ultima">'
                  + "Nenhum conjunto de dados está registrado nesta instalação. "
                  + "Empresas, BI Contábil e BI Fiscal entram nas próximas etapas.</p></div>";

            var configurado = corpo.meta && corpo.meta.dominio_configurado;
            if (configurado) {
                itens.filter(function (i) { return i.tem_pendentes; })
                    .forEach(function (i) { carregarPendentes(i.chave); });
            }
        }).catch(function (erro) {
            lista.innerHTML = '<div class="admin-card"><p>' + texto(erro.message) + "</p></div>";
        });
    }

    /* ── Pendências ───────────────────────────────────────────────────────── */

    function cardDe(chave) {
        return lista.querySelector('.import-card[data-chave="' + chave + '"]');
    }

    function carregarPendentes(chave) {
        var card = cardDe(chave);
        if (!card) return;
        var alvo = card.querySelector("[data-selecao-lista]");

        return pedirTudo(API + "/" + encodeURIComponent(chave) + "/pendentes")
            .then(function (r) {
                pendentes[chave] = r.itens;
                desenharPendentes(chave, "");
            })
            .catch(function (erro) {
                alvo.innerHTML = '<div class="emp-empty">' + texto(erro.message) + "</div>";
                card.querySelector("[data-selecao-contagem]").textContent = "";
            });
    }

    function nomeDe(item) {
        return item.razao_social || item.nome_fantasia || "(sem razão social na origem)";
    }

    function desenharPendentes(chave, filtro) {
        var card = cardDe(chave);
        if (!card) return;

        var todos = pendentes[chave] || [];
        var alvo = (filtro || "").trim().toLowerCase();
        var visiveis = !alvo ? todos : todos.filter(function (e) {
            return String(e.id_empresa) === alvo
                || nomeDe(e).toLowerCase().indexOf(alvo) !== -1
                || (e.cnpj || "").indexOf(alvo) !== -1;
        });

        var alvoLista = card.querySelector("[data-selecao-lista]");
        if (!todos.length) {
            alvoLista.innerHTML = '<div class="emp-empty">Nada pendente: tudo que existe na '
                + "origem já está no Portal.</div>";
        } else if (!visiveis.length) {
            alvoLista.innerHTML = '<div class="emp-empty">Nenhum registro com esse filtro.</div>';
        } else {
            alvoLista.innerHTML = visiveis.map(function (e) {
                return '<label class="emp-item emp-item--flat">'
                    + '<input type="checkbox" class="emp-item-cb" value="' + e.id_empresa + '">'
                    + '<span class="emp-item-cod">' + texto(e.id_empresa) + "</span>"
                    + '<span class="emp-item-nome">' + texto(nomeDe(e))
                    + (e.cnpj ? ' <span class="import-cnpj">' + texto(cnpjFormatado(e.cnpj))
                        + "</span>" : "")
                    + "</span>"
                    + "</label>";
            }).join("");
        }
        atualizarContagem(chave);
    }

    function marcados(chave) {
        var card = cardDe(chave);
        if (!card) return [];
        return Array.prototype.slice
            .call(card.querySelectorAll(".emp-item-cb:checked"))
            .map(function (cb) { return Number(cb.value); });
    }

    function atualizarContagem(chave) {
        var card = cardDe(chave);
        if (!card) return;
        var total = (pendentes[chave] || []).length;
        var selecionados = marcados(chave).length;

        card.querySelector("[data-selecao-contagem]").textContent = total
            ? (selecionados ? selecionados + " de " + total + " marcadas" : total + " pendentes")
            : "";

        var botao = card.querySelector('[data-acao="importar-selecionadas"]');
        if (botao) {
            botao.disabled = selecionados === 0;
            botao.lastChild.textContent = selecionados
                ? " Importar selecionadas (" + selecionados + ")"
                : " Importar selecionadas";
        }
    }

    var timerFiltro;
    lista.addEventListener("input", function (e) {
        var campo = e.target.closest("[data-selecao-filtro]");
        if (!campo) return;
        var chave = campo.closest(".import-card").dataset.chave;
        clearTimeout(timerFiltro);
        timerFiltro = setTimeout(function () {
            desenharPendentes(chave, campo.value);
        }, 150);
    });

    lista.addEventListener("change", function (e) {
        var cb = e.target.closest(".emp-item-cb");
        if (cb) atualizarContagem(cb.closest(".import-card").dataset.chave);
    });

    lista.addEventListener("click", function (e) {
        var card = e.target.closest(".import-card");
        if (!card) return;
        var chave = card.dataset.chave;

        /* Marcar/desmarcar age sobre o que está visível: com a lista filtrada,
           "todas" significa "todas as que estou vendo". */
        if (e.target.closest("[data-selecao-todas]")) {
            card.querySelectorAll(".emp-item-cb").forEach(function (cb) { cb.checked = true; });
            atualizarContagem(chave);
        } else if (e.target.closest("[data-selecao-nenhuma]")) {
            card.querySelectorAll(".emp-item-cb").forEach(function (cb) { cb.checked = false; });
            atualizarContagem(chave);
        }
    });

    /* ── Execução de um card ──────────────────────────────────────────────── */

    lista.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-acao]");
        if (!btn) return;

        var card = btn.closest(".import-card");
        var chave = card.dataset.chave;
        var acao = btn.dataset.acao;
        var resultado = card.querySelector(".import-resultado");
        var botoes = card.querySelectorAll("[data-acao]");

        var corpo = { dry_run: acao === "simular" };
        if (acao === "importar-selecionadas") {
            corpo.ids = marcados(chave);
            corpo.incluir = true;
            if (!corpo.ids.length) return;
        }

        botoes.forEach(function (b) { b.disabled = true; });
        resultado.hidden = false;
        resultado.className = "import-resultado";
        resultado.textContent = corpo.dry_run ? "Simulando…" : "Sincronizando…";

        // A mensagem diz O QUE está rodando e dá a expectativa de duração.
        // "Aguarde" sozinho não responde a pergunta que quem espera faz.
        var nome = nomeDoCard(card);
        if (corpo.dry_run) {
            aguardar("Simulando a sincronização de " + nome + ". Nada será gravado.");
        } else if (acao === "importar-selecionadas") {
            aguardar("Importando " + corpo.ids.length + " registro(s) de " + nome + ".");
        } else {
            aguardar("Sincronizando " + nome + ". Leituras grandes levam alguns minutos.");
        }

        pedir(API + "/" + encodeURIComponent(chave), {
            method: "POST",
            body: JSON.stringify(corpo)
        }).then(function (resp) {
            var d = resp.data;
            var gravou = (d.incluidas || 0) + (d.atualizadas || 0) > 0;

            // Regra do Portal, e ela é o ponto: resultado que não gravou nada
            // sai em ÂMBAR, nunca verde, com o motivo e o próximo passo. Verde
            // com zeros se lê como sucesso e esconde que nada aconteceu.
            resultado.className = "import-resultado import-resultado--"
                + (gravou || corpo.dry_run ? "ok" : "aviso");
            resultado.textContent = (corpo.dry_run ? "Simulação: " : "Concluído: ")
                + contagens(d) + " · " + d.duracao_segundos + "s"
                + (corpo.dry_run ? " (nada foi gravado)" : "");

            if (!gravou && !corpo.dry_run) {
                var p = document.createElement("p");
                p.className = "import-explicacao";
                p.textContent = acao === "atualizar"
                    ? "Nada a atualizar: o Portal não tem registro correspondente ao que a "
                      + "origem devolveu. Marque acima o que deseja importar."
                    : "Nada foi gravado.";
                resultado.appendChild(p);
            }

            return carregarLista();
        }).catch(function (erro) {
            resultado.className = "import-resultado import-resultado--erro";
            resultado.textContent = erro.message;
        }).finally(function () {
            botoes.forEach(function (b) { b.disabled = false; });
            // O overlay só sai depois do histórico, e não junto com a resposta
            // do POST: `carregarLista()` relê o `resumo()` dos dez
            // importadores — que vai ao Domínio e ao PostgreSQL — e o
            // histórico vem depois. Escondendo antes, a tela volta e congela
            // de novo por alguns segundos, que é pior que continuar esperando.
            Promise.resolve(carregarHistorico()).finally(pronto);
        });
    });

    /* ── Sincronizar tudo ─────────────────────────────────────────────────── */

    var btnTudo = document.getElementById("btnSincronizarTudo");
    if (btnTudo) {
        btnTudo.addEventListener("click", function () {
            var alvo = document.getElementById("tudoResultado");
            btnTudo.disabled = true;
            alvo.hidden = false;
            alvo.className = "import-resultado";

            // Cabeçalho separado das linhas de fase: `textContent` no `alvo`
            // apagaria o histórico que vai sendo acumulado embaixo.
            alvo.innerHTML = "";
            var cabecalho = document.createElement("p");
            cabecalho.textContent = "Iniciando…";
            alvo.appendChild(cabecalho);
            // A rodada completa é a mais longa de todas: dez conjuntos, na
            // ordem, e ela PARA na primeira falha. Dizer as duas coisas evita
            // que um resultado parcial seja lido como execução incompleta por
            // engano do programa.
            var empresa = empresaEscolhida();
            var deQuem = empresa
                ? "da empresa " + empresa
                : "de todas as empresas";
            aguardar(
                "Sincronizando todos os conjuntos de dados " + deQuem
                + ", na ordem de dependência. A rodada para na primeira falha."
            );

            // `/tudo` responde em NDJSON, uma linha por evento, transmitida
            // quando acontece — por isso `fetch` cru em vez de `Portal.pedir`,
            // que só sabe ler o envelope de uma resposta inteira.
            lerFluxo(API + "/tudo", empresa ? { ids: [empresa] } : {}, function (evento) {
                if (evento.evento === "iniciou") {
                    aguardar(
                        "Sincronizando " + evento.nome
                        + " (" + evento.indice + " de " + evento.de + ")."
                    );
                    cabecalho.textContent = "Em andamento: " + evento.nome
                        + " — " + evento.indice + " de " + evento.de + "…";
                    return;
                }
                if (evento.evento === "concluiu") {
                    var linha = document.createElement("p");
                    linha.className = "import-explicacao";
                    linha.textContent = (evento.status === "erro" ? "✕ " : "✓ ")
                        + evento.nome + " — "
                        + (evento.status === "erro" ? evento.erro : contagens(evento));
                    alvo.appendChild(linha);
                }
            })
                .then(function (d) {
                    var fases = d.fases || [];

                    if (!fases.length) {
                        alvo.className = "import-resultado import-resultado--aviso";
                        cabecalho.textContent = "Nada a sincronizar: nenhum conjunto de dados "
                            + "está registrado nesta instalação.";
                        return carregarLista();
                    }

                    // Só o cabeçalho muda: o placar por fase já está na tela,
                    // escrito conforme cada uma terminou. Reescrevê-lo aqui
                    // duplicaria a informação e apagaria a ordem em que ela
                    // apareceu — que é o que diz até onde a rodada chegou.
                    alvo.className = "import-resultado import-resultado--"
                        + (d.concluido ? "ok" : "erro");
                    cabecalho.textContent = d.concluido
                        ? "Concluído: " + contagens(d.total)
                        : "Interrompido em '" + fases[fases.length - 1].nome
                          + "'. Os conjuntos seguintes não rodaram.";

                    return carregarLista();
                })
                .catch(function (erro) {
                    alvo.className = "import-resultado import-resultado--erro";
                    cabecalho.textContent = erro.message;
                })
                .finally(function () {
                    btnTudo.disabled = false;
                    // O diagnóstico é relido de propósito: se o Domínio caiu
                    // no meio da rodada, é aqui que isso aparece.
                    Promise.all([
                        Promise.resolve(carregarDiagnostico()),
                        Promise.resolve(carregarHistorico()),
                    ]).finally(pronto);
                });
        });
    }

    /* ── Histórico ────────────────────────────────────────────────────────── */

    function carregarHistorico() {
        var tbody = document.getElementById("tbody-historico");
        if (!tbody) return;

        return pedir(API + "/historico?limit=20").then(function (corpo) {
            var itens = corpo.data || [];
            if (!itens.length) {
                tbody.innerHTML = '<tr class="table-empty"><td colspan="6">'
                    + "Nenhuma sincronização executada.</td></tr>";
                return;
            }
            tbody.innerHTML = itens.map(function (e) {
                return "<tr>"
                    + "<td>" + dataHora(e.iniciada_em) + "</td>"
                    + "<td>" + texto(e.chave) + (e.dry_run ? " (simulação)" : "") + "</td>"
                    + "<td>" + texto(e.origem) + "</td>"
                    + "<td>" + autorDe(e) + "</td>"
                    + "<td>" + (e.status === "erro" ? texto(e.erro) : contagens(e)) + "</td>"
                    + "<td>" + chipSituacao(e.status) + "</td>"
                    + "</tr>";
            }).join("");
        }).catch(function (erro) {
            tbody.innerHTML = '<tr class="table-empty"><td colspan="6">'
                + texto(erro.message) + "</td></tr>";
        });
    }

    /* ── A carga inicial ──────────────────────────────────────────────────────
       O "aguarde" vale aqui mais que em qualquer execução, e o número é a
       razão: MEDIDO em 21/09/2026, a listagem dos cards leva **96 segundos**.

       Não é rede nem desenho da tela — é o gancho `resumo()` dos dez
       importadores. Cada um consulta a origem para dizer o próprio estado
       ("363 de 589", "1.709.925 linhas"), e alguns desses `COUNT` varrem
       tabelas de milhões de linhas no Domínio. A tela chama todos, em série, a
       cada abertura e depois de cada sincronização.

       Sem overlay, o operador abre a tela, vê "Carregando…" por um minuto e
       meio e conclui que travou. Com ele, ao menos sabe que é trabalho.

       O consumo dos 96 s continua sendo um problema — ver "Pendências
       conhecidas" no README. O overlay torna o sintoma honesto, não o cura. */
    aguardar("Consultando o estado de cada conjunto de dados na origem e no Portal.");
    Promise.all([
        Promise.resolve(carregarDiagnostico()),
        Promise.resolve(carregarEmpresas()),
        Promise.resolve(carregarLista()),
        Promise.resolve(carregarHistorico()),
    ]).finally(pronto);
}());
