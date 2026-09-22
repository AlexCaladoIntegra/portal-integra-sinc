"""Leitura do Domínio e gravação no PostgreSQL do Portal.

- `dominio.py`: conexão read-only com o Domínio (SQL Anywhere). Cópia
  literal do módulo homônimo do Portal Integra — ver o cabeçalho de lá.
- `importadores/`: um módulo por entidade, registrados em `REGISTRO`.
  Chega vazio na Etapa 2 e é preenchido nas Etapas 3 a 5.
- `services.py`: executa, mede e registra no histórico (Etapa 2).
- `repositories.py`: histórico em `importacao_execucao` (Etapa 2).
- `routes.py`: endpoints da tela (Etapa 2).
"""
