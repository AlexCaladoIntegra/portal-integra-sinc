"""O caminho de gravação no módulo Documentos do Portal.

`escrita.py` é uma **cópia declarada** de `ArquivoService.enviar()` do
`portal-integra`. Ela existe porque este processo não consegue importar o
service de lá, e reimplementar à mão produziria uma divergência maior e sem
rastro. Ver o cabeçalho do arquivo para as três guardas que a sustentam.

Fica num pacote próprio, e não dentro de `importacao/`, pela mesma razão que
`app/fiscal/` existe separado: é vocabulário do DESTINO, não da extração. Um
segundo importador que grave documento usa o mesmo caminho.
"""
