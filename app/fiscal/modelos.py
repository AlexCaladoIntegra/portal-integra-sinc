"""O rótulo do documento fiscal, a partir do MODELO — nunca da espécie.

Puro, sem I/O. Consumido pelo importador `fiscal_cadastros`, que desnormaliza
`codigo_modelo` na linha do fato, e pelas telas que agrupam por espécie.

**A regra central: agrupe por `codigo_modelo`, nunca por `codi_esp` nem por
`nome_esp`.** No Domínio a mesma espécie de documento tem vários códigos, e
medido em 11/09/2026 nas 60 linhas de `efespecies`:

    modelo 55 (NF-e)   -> codi_esp 36, 39, 50, 51
    modelo 65 (NFC-e)  -> codi_esp 46, 49, 53
    modelo 57 (CT-e)   -> codi_esp 38, 44, 52
    modelo 03 (NFS-e)  -> codi_esp 31, 34, 37, 41, 43, 57   (SEIS)
    modelo ZZ          -> codi_esp 40, 42, 45, 47, 48, 54, 55, 58, 59

Agrupar por `codi_esp` parte o NFC-e em três fatias no gráfico de Espécie NF, e
as três somam menos que a NF-e sozinha — um ranking plausível e errado. Medido
nas saídas de 2023 em diante: 53 tem 1.384.043 notas, 46 tem 949.641 e 49 tem
243.274, contra 1.503.375 da NF-e. Separados, o NFC-e nunca aparece em primeiro;
somados, ele quase empata.

**E `nome_esp` não serve de rótulo**: `codi_esp = 50` tem `nome_esp` igual a
`"nr   36"`, que é lixo de cadastro.

**A armadilha com nome próprio:** `codi_esp = 57` é "NF de Serviços" (modelo
`03`), enquanto o MODELO `'57'` é CT-e. Os dois campos são numéricos e parecidos,
e trocá-los põe serviço dentro de transporte — sem erro, e com o total geral
inalterado. Por isso `rotulo()` recebe o modelo como **string** e nunca um
inteiro: `rotulo(57)` não compila em nada útil, `rotulo("57")` é CT-e e
`rotulo("03")` é NFS-e.
"""

from __future__ import annotations

# Os 40 modelos que existem em `efespecies` (medido em 11/09/2026). É a tabela de
# modelo de documento fiscal do SPED, que é norma nacional e não cadastro do
# escritório — por isso cabe em código, no precedente da `0006_bi_conceitos_legado`.
#
# Os rótulos são os nomes do Domínio, com as abreviações desfeitas: `nome_esp` é
# `char(30)` e corta palavra no meio ("NF de Serviço de Telecomun."), o que numa
# legenda de gráfico fica ilegível.
ROTULOS: dict[str, str] = {
    "01": "Nota Fiscal",
    "02": "NF de Venda a Consumidor",
    "03": "NFS-e",
    "04": "NF de Produtor",
    "06": "Conta de Energia Elétrica",
    "07": "NF de Serviço de Transporte",
    "08": "Conhecimento de Transporte Rodoviário",
    "09": "Conhecimento de Transporte Aquaviário",
    "10": "Conhecimento Aéreo",
    "11": "Conhecimento de Transporte Ferroviário",
    "13": "Bilhete de Passagem Rodoviário",
    "14": "Bilhete de Passagem Aquaviário",
    "15": "Bilhete de Passagem e NF de Bagagem",
    "16": "Bilhete de Passagem Ferroviário",
    "17": "Despacho de Transporte",
    "18": "Resumo de Movimento Diário",
    "1B": "NF Avulsa",
    "20": "Ordem de Coleta de Cargas",
    "21": "NF de Serviço de Comunicação",
    "22": "NF de Serviço de Telecomunicação",
    "23": "GNRE",
    "24": "Autorização de Carregamento e Transporte",
    "25": "Manifesto de Carga",
    "26": "Conhecimento de Transporte Multimodal",
    "28": "Conta de Fornecimento de Gás Canalizado",
    "29": "Conta de Fornecimento de Água Canalizada",
    "2D": "Cupom Fiscal",
    "2E": "Bilhete de Passagem emitido por ECF",
    "30": "Manifesto de Voo",
    "31": "Bilhete/Recibo do Passageiro",
    "3A": "NFS-e Simplificada",
    "3B": "NFS-e Avulsa",
    "55": "NF-e",
    "57": "CT-e",
    "62": "NFCom",
    "65": "NFC-e",
    "66": "NF de Energia Elétrica Eletrônica",
    "8B": "Conhecimento de Transporte de Cargas Avulso",
    "XX": "Nota Fiscal de Entrada",
    "ZZ": "Documento não fiscal",
}

# Os modelos que o BI Fiscal trata como documento de SERVIÇO. É o que liga a
# espécie ao fato `tipo = 'V'` e ao card de ISS.
MODELOS_DE_SERVICO = frozenset({"03", "3A", "3B"})


def normalizar(codigo_modelo: str | None) -> str:
    """Limpa o `codigo_modelo` da origem: `char(2)`, com espaço à direita.

    Devolve `"ZZ"` para vazio ou nulo, e não uma string vazia: o fato tem a
    coluna `NOT NULL`, e um modelo em branco no `GROUP BY` sai como uma fatia
    sem nome no gráfico. `"ZZ"` é o próprio código de "documento não fiscal" do
    Domínio, e é onde essas linhas pertencem.
    """
    limpo = (codigo_modelo or "").strip().upper()
    return limpo or "ZZ"


def rotulo(codigo_modelo: str | None) -> str:
    """O nome de exibição do modelo.

    Modelo desconhecido vira `"Modelo <código>"` em vez de cair num rótulo
    genérico: o Domínio pode ganhar modelo novo (a reforma tributária já trouxe
    IBS e CBS do lado dos impostos), e uma fatia chamada "Outros" esconderia
    justamente a novidade que alguém precisa ver.
    """
    codigo = normalizar(codigo_modelo)
    return ROTULOS.get(codigo, f"Modelo {codigo}")


def e_servico(codigo_modelo: str | None) -> bool:
    """O modelo é de documento de serviço?"""
    return normalizar(codigo_modelo) in MODELOS_DE_SERVICO
