"""Grupo matriz/filiais — só a parte que a sincronização de empresas usa.

O cadastro em si (CRUD, telas, validação) é do Portal e fica lá. O que veio
para cá é a **sugestão automática** pela raiz do CNPJ, que roda logo depois do
UPDATE de empresas: sem ela, empresa que entre por este processo nunca é
agrupada, e a consolidação do BI a perde em silêncio.

- `sugestao.py`: cópia literal do módulo do Portal. É puro, sem I/O.
- `repositories.py`: só os três métodos que a sugestão chama.
"""
