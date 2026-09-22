# queries/

O SQL das consultas ao **Domínio**, versionado em arquivo.

Chega vazio na Etapa 1. As 23 consultas entram nas Etapas 4 e 5, copiadas do
`portal-integra` com o caminho relativo **preservado**:

```
bi/dominio/       7 arquivos  — plano de contas, DRE, saldos, lançamentos, DFC
fiscal/dominio/  16 arquivos  — espécies, CFOP, dimensões, movimento, apuração
```

Os importadores as referenciam por string literal
(`carregar_sql("bi/dominio/saldos_mensais.sql")`), então reorganizar a árvore
quebra a cópia em silêncio.

## O que vale saber antes de mexer

- **Dialeto SQL Anywhere**, não PostgreSQL: placeholder posicional `?`,
  `CURRENT DATE` sem underscore, `DATEADD` em vez de `interval`, sem
  `DATE_TRUNC`. `RTRIM()` é obrigatório em coluna `CHAR` de largura fixa.
- O schema `bethadba` está **escrito no corpo** de 22 dos 23 arquivos. Trocar
  `DOMINIO_SCHEMA` no `.env` só afeta a importação de empresas.
- Cada arquivo tem, no topo, o propósito, os parâmetros esperados e as regras
  que não são óbvias. **Elas não são decoração:** a maior parte registra um caso
  medido em que a consulta "certa" produzia número plausível e errado. Leia
  antes de simplificar.
