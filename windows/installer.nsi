; ============================================================
;  Free LLM Router — установщик для Windows 11 (NSIS 3.x)
;
;  Один Setup.exe:
;    1) кладёт программу в %LOCALAPPDATA%\FreeLLMRouter (без прав админа)
;    2) находит Python или ставит Python 3.12 (если при сборке приложен installer)
;    3) ставит pip-пакеты — в установщике есть офлайн-копия
;    4) создаёт ярлыки и запись «Приложения и возможности»
;    5) запускает сервер и открывает чат
;
;  Сборка: build_setup.sh (Linux) / build_setup.cmd (Windows, makensis в PATH)
; ============================================================

Unicode true
SetCompressor /SOLID lzma
SetCompressorDictSize 64
RequestExecutionLevel user

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "WordFunc.nsh"

; ---------- параметры сборки (передаются -D...) ----------
!ifndef APP_VERSION
  !define APP_VERSION "1.0.0"
!endif
!ifndef SOURCE_DIR
  !error "SOURCE_DIR is not defined (payload folder)"
!endif
!ifndef WHEELS_DIR
  !define WHEELS_DIR ""
!endif
!ifndef PYINST_DIR
  !define PYINST_DIR ""
!endif
!ifndef ICON_FILE
  !define ICON_FILE ""
!endif

!define APP_NAME      "Free LLM Router"
!define APP_EXE       "launcher.cmd"
!define UNINST_KEY    "Software\Microsoft\Windows\CurrentVersion\Uninstall\FreeLLMRouter"
!define REG_APP       "Software\FreeLLMRouter"

Name "${APP_NAME} ${APP_VERSION}"
OutFile "..\releases\FreeLLMRouter-Setup-${APP_VERSION}.exe"
InstallDir "$LOCALAPPDATA\FreeLLMRouter"
InstallDirRegKey HKCU "${REG_APP}" "InstallDir"
BrandingText "${APP_NAME} ${APP_VERSION}"
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName"     "${APP_NAME}"
VIAddVersionKey "FileDescription" "${APP_NAME} — бесплатные нейросети и OpenAI-совместимый шлюз"
VIAddVersionKey "FileVersion"     "${APP_VERSION}"
VIAddVersionKey "ProductVersion"  "${APP_VERSION}"
VIAddVersionKey "LegalCopyright"  "Local, self-hosted"

!if "${ICON_FILE}" != ""
  Icon "${ICON_FILE}"
  UninstallIcon "${ICON_FILE}"
!endif

; ---------- язык: русский, если система русская, иначе английский ----------
!define MUI_LANGDLL_REGISTRY_ROOT "HKCU"
!define MUI_LANGDLL_REGISTRY_KEY "${REG_APP}"
!define MUI_LANGDLL_REGISTRY_VALUENAME "InstallerLanguage"
!define MUI_LANGDLL_ALWAYSSHOW

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Запустить Free LLM Router и открыть чат"
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\GET-KEYS.md"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "Открыть памятку: где взять бесплатные API-ключи"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED
!define MUI_FINISHPAGE_LINK "Бесплатные ключи: console.groq.com/keys · cloud.cerebras.ai · aistudio.google.com/apikey"
!define MUI_FINISHPAGE_LINK_LOCATION "https://console.groq.com/keys"
!define MUI_FINISHPAGE_NOREBOOTSUPPORT

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "Russian"
!insertmacro MUI_LANGUAGE "English"

; ============================================================
;  Каталоги
; ============================================================

!macro ExtractPayload
  SetOutPath "$INSTDIR"
  File /r "${SOURCE_DIR}\*.*"
!macroend

; ============================================================
;  Секции
; ============================================================

Section "Программа (обязательно)" SecApp
  SectionIn RO

  ; --- остановить работающий экземпляр ---
  DetailPrint "Останавливаю запущенный сервер (если был)..."
  nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" "$INSTDIR\windows\runtime\launcher.py" --stop'

  DetailPrint "Распаковываю файлы..."
  !insertmacro ExtractPayload

  ; --- Python ---
  ${If} ${FileExists} "$INSTDIR\runtime\python.exe"
    DetailPrint "Python: используется встроенный (runtime\python.exe)."
    WriteINIStr "$INSTDIR\python-path.ini" "python" "exe" "$INSTDIR\runtime\python.exe"
  ${Else}
    DetailPrint "Ищу Python на этом компьютере..."
    Call FindSystemPython
    ${If} $0 == ""
      Call InstallPython
    ${EndIf}
    ${If} $0 != ""
      WriteINIStr "$INSTDIR\python-path.ini" "python" "exe" "$0"
    ${EndIf}
  ${EndIf}

  ; --- зависимости ---
  ${If} $0 != ""
    ${If} ${FileExists} "$INSTDIR\wheels\*.whl"
      DetailPrint "Ставлю Python-пакеты (офлайн)..."
      nsExec::ExecToLog '"$0" -m pip install --disable-pip-version-check -q --no-index --find-links "$INSTDIR\wheels" -r "$INSTDIR\requirements.txt"'
    ${Else}
      DetailPrint "Ставлю Python-пакеты (из интернета)..."
      nsExec::ExecToLog '"$0" -m pip install --disable-pip-version-check -q -r "$INSTDIR\requirements.txt"'
    ${EndIf}
  ${EndIf}

  ; --- конфиг ---
  ${IfNot} ${FileExists} "$INSTDIR\.env"
    CopyFiles /SILENT "$INSTDIR\.env.example" "$INSTDIR\.env"
  ${EndIf}

  ; --- ключи реестра ---
  WriteRegStr HKCU "${REG_APP}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${REG_APP}" "Python" "$0"

  ; --- «Приложения и возможности» ---
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  ${GetSize} "$INSTDIR" "/S=0K" $2 $3 $4
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayName"     "${APP_NAME}"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayVersion"  "${APP_VERSION}"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayIcon"     "$INSTDIR\windows\assets\app.ico"
  WriteRegStr   HKCU "${UNINST_KEY}" "Publisher"       "Free LLM Router"
  WriteRegStr   HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr   HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr   HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  IntOp $2 $2 / 1024
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" "$2"
SectionEnd

Section "Ярлыки (Пуск и рабочий стол)" SecShortcuts
  DetailPrint "Создаю ярлыки..."
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\windows\setup_shortcuts.ps1" -Root "$INSTDIR"'
SectionEnd

Section "Пакеты Python: офлайн-копия (~3.8 МБ)" SecDeps
  DetailPrint "Кладу офлайн-пакеты..."
  SetOutPath "$INSTDIR\wheels"
  File /r "${WHEELS_DIR}\*.whl"
SectionEnd

; ---------- описания секций ----------
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
!insertmacro MUI_DESCRIPTION_TEXT ${SecApp} "Программа, чат и OpenAI-совместимый шлюз. Занимает ~30 МБ."
!insertmacro MUI_DESCRIPTION_TEXT ${SecShortcuts} "Ярлык в меню «Пуск» и на рабочем столе."
!insertmacro MUI_DESCRIPTION_TEXT ${SecDeps} "Копия pip-пакетов для установки без интернета. Снимите галочку, если устанавливаете с интернетом."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; ---------- функции поиска/установки Python ----------
Function FindSystemPython
  StrCpy $0 ""
  nsExec::ExecToStack 'cmd /C where python >"%TEMP%\flmr_py.txt" 2>nul'
  Pop $1
  ClearErrors
  FileOpen $3 "$TEMP\flmr_py.txt" r
  ${IfNot} ${Errors}
    FileRead $3 $4
    FileClose $3
    ${WordFind} "$4" "$\r$\n" "+1" $5
    ${If} $5 != ""
      Call CheckPythonVersion
    ${EndIf}
  ${EndIf}

  ${If} $0 == ""
    StrCpy $6 "$LOCALAPPDATA\Programs\Python"
    FindFirst $7 $8 "$6\Python3*\python.exe"
    ${IfNot} ${Errors}
      FindClose $7
      StrCpy $5 "$6\$8\python.exe"
      Call CheckPythonVersion
    ${EndIf}
  ${EndIf}

  ${If} $0 != ""
    DetailPrint "Найден Python: $0"
  ${Else}
    DetailPrint "Python не найден."
  ${EndIf}
FunctionEnd

; на входе $5 — кандидат; на выходе $0 — путь или ""
Function CheckPythonVersion
  StrCpy $9 ""
  nsExec::ExecToStack '"$5" -c "import sys;raise SystemExit(0 if sys.version_info>=(3,9) else 1)"'
  Pop $9
  ${If} $9 == "0"
    StrCpy $0 "$5"
  ${Else}
    StrCpy $0 ""
  ${EndIf}
FunctionEnd

Function InstallPython
  ${If} ${FileExists} "$INSTDIR\windows\python\python-installer.exe"
    DetailPrint "Ставлю Python 3.12 (тихо)..."
    nsExec::ExecToLog '"$INSTDIR\windows\python\python-installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=0 Include_test=0 Shortcuts=0'
    StrCpy $0 "$LOCALAPPDATA\Programs\Python\Python312\python.exe"
    ${IfNot} ${FileExists} "$0"
      StrCpy $0 "$LOCALAPPDATA\Programs\Python\Python312-32\python.exe"
    ${EndIf}
    ${IfNot} ${FileExists} "$0"
      StrCpy $0 ""
    ${EndIf}
  ${Else}
    MessageBox MB_ICONEXCLAMATION|MB_OK "Python 3.9+ не найден на компьютере.$\r$\n$\r$\nУстановите Python с python.org (галочка «Add python.exe to PATH») и запустите установщик ещё раз."
    StrCpy $0 ""
  ${EndIf}
FunctionEnd

; ============================================================
;  Удаление
; ============================================================

Section "Uninstall"
  DetailPrint "Останавливаю сервер..."
  nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" "$INSTDIR\windows\runtime\launcher.py" --stop'

  DetailPrint "Удаляю ярлыки..."
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\windows\setup_shortcuts.ps1" -Uninstall'

  ; тихое удаление (/S) — без вопросов, чистим всё
  IfSilent un_cleanup

  MessageBox MB_YESNO|MB_ICONQUESTION "Удалить сохранённые API-ключи (.env)?" IDNO un_keep_env
  Delete "$INSTDIR\.env"
  un_keep_env:

  MessageBox MB_YESNO|MB_ICONQUESTION "Удалить и саму папку программы (журналы, настройки)?" IDNO un_keep_dir
  RMDir /r "$INSTDIR"

  Goto un_cleanup

  un_keep_dir:
  Goto un_registry

  un_cleanup:
  ; история чатов лежит в %LOCALAPPDATA%\FreeLLMRouter\data и НЕ удаляется:
  ; переустановка и удаление программы не должны стоить пользователю диалогов.
  RMDir /r "$INSTDIR"

  un_registry:
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "${REG_APP}"
SectionEnd
