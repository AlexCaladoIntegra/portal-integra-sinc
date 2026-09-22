@echo off
rem ============================================================================
rem  abrir-sinc.bat - sobe o Portal Integra Sinc e abre a tela no navegador.
rem
rem  Duplo clique basta. A janela do console fica aberta com o log: fechar a
rem  janela ou Ctrl+C derruba o servidor.
rem ============================================================================
setlocal

rem Roda sempre da pasta do .bat, e nao de onde foi chamado. Sem isto, um
rem atalho na area de trabalho procuraria o .env no lugar errado.
cd /d "%~dp0"

rem UTF-8 no console. O log tem acento em quase toda linha ("Dominio
rem respondeu", "revisao do schema") e sem isto sai ilegivel, o que faz
rem mensagem normal parecer defeito.
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo.
    echo  O ambiente virtual nao existe.
    echo.
    echo  Rode uma vez, nesta pasta:
    echo      python -m venv .venv
    echo      .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist "%~dp0.env" (
    echo.
    echo  O arquivo .env nao existe.
    echo.
    echo  Copie o modelo e preencha as conexoes:
    echo      copy .env.example .env
    echo.
    pause
    exit /b 1
)

rem A porta vem do .env (APP_PORT). Se a configuracao estiver invalida, o
rem proprio run.py vai recusar com a mensagem certa logo abaixo - aqui so
rem precisamos de um numero para montar a URL.
set "PORTA=7820"
for /f "usebackq tokens=*" %%P in (`"%PY%" -c "from app.config import get_settings; print(get_settings().app_port)" 2^>nul`) do set "PORTA=%%P"

rem Abre o navegador quando a porta ATENDER, e nao depois de uma espera fixa:
rem um tempo curto demais abre uma pagina de erro, que se le como app quebrado.
rem Desiste em ~24 s - a essa altura o console ja mostrou o motivo.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "$p=%PORTA%; foreach($i in 1..60){ try{ (New-Object Net.Sockets.TcpClient('127.0.0.1',$p)).Close(); Start-Process ('http://127.0.0.1:' + $p); break } catch { Start-Sleep -Milliseconds 400 } }"

echo.
echo  Portal Integra Sinc - http://127.0.0.1:%PORTA%
echo  Ctrl+C encerra.
echo.

"%PY%" run.py
set "CODIGO=%ERRORLEVEL%"

rem Sem o pause, um erro de configuracao fecha a janela antes de alguem ler a
rem mensagem - que e justamente quando a mensagem importa.
if not "%CODIGO%"=="0" (
    echo.
    echo  O servidor encerrou com erro ^(codigo %CODIGO%^).
    echo.
    pause
)
exit /b %CODIGO%
