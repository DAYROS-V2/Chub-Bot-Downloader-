@echo off
title Chub AI Character Downloader
color 0A
cls

chcp 65001 >nul 2>&1
echo.
echo  ================================================
echo    Chub AI Character Downloader
echo    Downloads character cards only
echo  ================================================
echo.
echo   [1]  Download single character
echo   [2]  Search and download
echo   [3]  Creator download
echo   [4]  Tag download
echo   [5]  Event download
echo   [6]  Preview search
echo   [7]  Preview tag
echo   [8]  Login / setup Chub profile
echo   [9]  Exit
echo.

set /p CHOICE="   Pick a mode (1-9): "

if "%CHOICE%"=="1" goto SINGLE
if "%CHOICE%"=="2" goto SEARCH
if "%CHOICE%"=="3" goto CREATOR
if "%CHOICE%"=="4" goto TAG
if "%CHOICE%"=="5" goto EVENT
if "%CHOICE%"=="6" goto PREVIEW
if "%CHOICE%"=="7" goto PREVIEWTAG
if "%CHOICE%"=="8" goto LOGIN
if "%CHOICE%"=="9" goto END

echo   Invalid choice. Try again.
pause
goto END

:FORMAT
echo.
echo   Format
echo   [1]  PNG card
echo   [2]  JSON
echo   [3]  Both
set "FORMAT_CHOICE=1"
set /p FORMAT_CHOICE="   Pick format [default 1]: "
if "%FORMAT_CHOICE%"=="" set "FORMAT_CHOICE=1"
set "FORMAT=png"
if "%FORMAT_CHOICE%"=="1" set "FORMAT=png"
if "%FORMAT_CHOICE%"=="2" set "FORMAT=json"
if "%FORMAT_CHOICE%"=="3" set "FORMAT=both"
exit /b

:SORT
echo.
echo   Sort
echo   [1]  User Default
echo   [2]  Latest
echo   [3]  # Downloads
echo   [4]  Popularity
echo   [5]  Underrated
echo   [6]  Recent Hits
echo   [7]  Trending
echo   [8]  Timeline
echo   [9]  Evergreen Event
echo   [10] Random
set "SORT_CHOICE=2"
set /p SORT_CHOICE="   Pick sort [default 2]: "
if "%SORT_CHOICE%"=="" set "SORT_CHOICE=2"
set "SORT=latest"
if "%SORT_CHOICE%"=="1" set "SORT=user_default"
if "%SORT_CHOICE%"=="2" set "SORT=latest"
if "%SORT_CHOICE%"=="3" set "SORT=downloads"
if "%SORT_CHOICE%"=="4" set "SORT=popularity"
if "%SORT_CHOICE%"=="5" set "SORT=underrated"
if "%SORT_CHOICE%"=="6" set "SORT=recent_hits"
if "%SORT_CHOICE%"=="7" set "SORT=trending"
if "%SORT_CHOICE%"=="8" set "SORT=timeline"
if "%SORT_CHOICE%"=="9" set "SORT=evergreen"
if "%SORT_CHOICE%"=="10" set "SORT=random"
exit /b

:MATCH
echo.
echo   Tag Matching
echo   [1]  All tags (Love AND Human)
echo   [2]  Any tag (Love OR Human)
set "MATCH_CHOICE=1"
set /p MATCH_CHOICE="   Pick matching [default 1]: "
if "%MATCH_CHOICE%"=="" set "MATCH_CHOICE=1"
set "MATCH=all"
if "%MATCH_CHOICE%"=="2" set "MATCH=any"
exit /b

:CONCURRENCY
echo.
set "CONCURRENCY=4"
set /p CONCURRENCY="   Batch size - cards downloaded at once [default 4, pick 1-20]: "
if "%CONCURRENCY%"=="" set "CONCURRENCY=4"
exit /b

:SINGLE
echo.
set /p TARGET="   Paste character URL/path: "
call :FORMAT
cd /d "%~dp0"
py chub_downloader.py single "%TARGET%" --format %FORMAT%
goto DONE

:SEARCH
echo.
call :CONCURRENCY
set /p QUERY="   Search text: "
set "PAGES=1"
set /p PAGES="   Pages [default 1, -1 forever]: "
if "%PAGES%"=="" set "PAGES=1"
call :SORT
call :FORMAT
cd /d "%~dp0"
py chub_downloader.py search "%QUERY%" --pages %PAGES% --sort %SORT% --format %FORMAT% --concurrency %CONCURRENCY%
goto DONE

:CREATOR
echo.
call :CONCURRENCY
set /p CREATOR="   Creator username/profile URL: "
set "PAGES=1"
set /p PAGES="   Pages [default 1, -1 forever]: "
if "%PAGES%"=="" set "PAGES=1"
call :SORT
call :FORMAT
cd /d "%~dp0"
py chub_downloader.py creator "%CREATOR%" --pages %PAGES% --sort %SORT% --format %FORMAT% --concurrency %CONCURRENCY%
goto DONE

:TAG
echo.
call :CONCURRENCY
set /p TAGS="   Tag(s), comma separated or tag URL: "
set "PAGES=1"
set /p PAGES="   Pages [default 1, -1 forever]: "
if "%PAGES%"=="" set "PAGES=1"
call :SORT
call :MATCH
call :FORMAT
cd /d "%~dp0"
py chub_downloader.py tag "%TAGS%" --pages %PAGES% --sort %SORT% --match %MATCH% --format %FORMAT% --concurrency %CONCURRENCY%
goto DONE

:EVENT
echo.
call :CONCURRENCY
set /p EVENT="   Event name/tag/URL: "
set "PAGES=1"
set /p PAGES="   Pages [default 1, -1 forever]: "
if "%PAGES%"=="" set "PAGES=1"
call :SORT
call :FORMAT
cd /d "%~dp0"
py chub_downloader.py event "%EVENT%" --pages %PAGES% --sort %SORT% --format %FORMAT% --concurrency %CONCURRENCY%
goto DONE

:PREVIEW
echo.
set /p QUERY="   Search text: "
call :SORT
cd /d "%~dp0"
py chub_downloader.py preview "%QUERY%" --sort %SORT%
goto DONE

:PREVIEWTAG
echo.
set /p TAGS="   Tag(s), comma separated or tag URL: "
call :SORT
call :MATCH
cd /d "%~dp0"
py chub_downloader.py preview-tag "%TAGS%" --sort %SORT% --match %MATCH%
goto DONE

:LOGIN
cd /d "%~dp0"
py chub_downloader.py login
goto DONE

:DONE
echo.
echo  ================================================
echo   Operation complete. Check the character exports folder.
echo  ================================================

:END
echo.
echo  Press any key to close...
pause >nul
