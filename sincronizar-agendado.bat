@echo off
rem ============================================================================
rem  sincronizar-agendado.bat - a rodada completa, sem tela.
rem
rem  Feito para o Agendador de Tarefas do Windows. NAO tem pause e NAO abre
rem  navegador: o processo roda, grava, escreve o log e sai.
rem
rem  Registrar a tarefa (uma vez, num prompt de administrador):
rem
rem    schtasks /create /tn "Portal Integra Sinc" /sc daily /st 02:00 ^
rem             /tr "\"%~dp0sincronizar-agendado.bat\"" /ru SISTEMA
rem
rem  Trocar SISTEMA pela conta de servico que tiver acesso ao Dominio. ATENCAO:
rem  se o DSN ODBC for de USUARIO, ele so existe para a conta que o criou. Veja
rem  a secao do erro IM014 no README antes de escolher a conta.
rem
rem  Codigos de saida, que o Agendador mostra na coluna "Resultado":
rem      0  tudo certo
rem      1  a sincronizacao falhou
rem      2  ja havia execucao em andamento (nao e falha)
rem      3  configuracao invalida
rem ============================================================================
setlocal

cd /d "%~dp0"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [ERRO] Ambiente virtual ausente em %PY%
    exit /b 3
)
if not exist "%~dp0.env" (
    echo [ERRO] Arquivo .env ausente em %~dp0
    exit /b 3
)

rem Sem `pause` em nenhum caminho: numa tarefa agendada ele deixaria o
rem processo pendurado esperando uma tecla que ninguem vai apertar, e a
rem tarefa so morreria no timeout do Agendador. O log fica em logs\.
"%PY%" sincronizar.py %*

exit /b %ERRORLEVEL%
