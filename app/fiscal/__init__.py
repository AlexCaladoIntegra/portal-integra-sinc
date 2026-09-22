"""Vocabulário fiscal — os dois módulos PUROS que a extração usa.

São cópia literal do Portal, sem I/O e sem dependência nenhuma:

- `impostos.py`: o nome canônico do imposto, pelo CÓDIGO e nunca por
  normalização de string — e nunca "Outros".
- `modelos.py`: o rótulo do documento, pelo MODELO e não pela espécie.

O resto do módulo `app/fiscal/` do Portal (as três telas, os payloads, o mapa,
o comparativo) é de apresentação e ficou lá.
"""
