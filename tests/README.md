# tests/

`pytest`, com as fixtures em `conftest.py`.

**Nenhum teste lê o `.env` e nenhum abre conexão.** As Settings são montadas na
fixture com `_env_file=None`. O motivo é direto: o `.env` desta aplicação aponta
para o PostgreSQL de produção do Portal, e um teste que o lesse escreveria lá.

O Domínio é sempre dublê — é o desenho herdado do `portal-integra`, e é o que
permite rodar a suíte numa máquina sem o driver ODBC.
