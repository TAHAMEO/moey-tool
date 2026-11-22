; CLIPFORGE for Windows 10 and 11 — setup.exe
;
; Build (on Linux or Windows, NSIS 3):   installer/windows/build.sh
; CLIPFORGE runs as an ordinary Windows app: no Docker, no Linux, no WSL, and setup needs no
; administrator rights. It installs into %LOCALAPPDATA%\ClipForge (or the folder you choose). All
; the work is done by scripts\install.ps1, one step at a time, so the progress bar moves and the
; details list shows what is happening. Running setup again updates CLIPFORGE and keeps all data.

Unicode true
SetCompressor /SOLID lzma
ManifestDPIAware true

!ifndef VERSION
  !define VERSION "0.3.0"
!endif
!ifndef PAYLOAD
  !define PAYLOAD "build\payload"
!endif
!ifndef OUTFILE
  !define OUTFILE "build\ClipForge-Setup-${VERSION}.exe"
!endif

!define APP "CLIPFORGE"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipForge"
; setup.exe is a 32-bit program: Windows shows it SysWOW64 (32-bit programs only) when it asks for
; System32. "Sysnative" is how a 32-bit program reaches the real, 64-bit System32.
!define SYSNATIVE "$WINDIR\Sysnative"
!define PS '"${SYSNATIVE}\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File'
!define LAUNCHER '"$INSTDIR\app\installer\windows\scripts\clipforge.ps1"'

Name "${APP}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\ClipForge"
InstallDirRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipForge" "InstallLocation"  ; updates go to the same folder
RequestExecutionLevel user   ; everything is installed for you alone, in your own folders
ShowInstDetails show
ShowUninstDetails show
BrandingText "${APP} ${VERSION}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APP}"
VIAddVersionKey "FileDescription" "${APP} setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "CLIPFORGE"

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"

!define MUI_ICON "clipforge.ico"
!define MUI_UNICON "clipforge.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Install ${APP}"
!define MUI_WELCOMEPAGE_TEXT "${APP} turns your long videos into Shorts and Reels and publishes them.$\r$\n$\r$\n${APP} runs as an ordinary Windows app — no Docker, no Linux, no administrator rights. Setup downloads what it needs (Python, its database and video tools: about 1.2 GB, plus the 1.8 GB speech model) and takes about 10–30 minutes, depending on your connection.$\r$\n$\r$\nKeep the PC on and connected while setup runs."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Open ${APP} now"
!define MUI_FINISHPAGE_RUN_FUNCTION OpenClipForge

Var WantDesktop
Var WantAutostart
Var DesktopBox
Var AutostartBox
Var Failed

!insertmacro MUI_PAGE_WELCOME
Page custom OptionsPage OptionsLeave
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipFinishOnFailure
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "${APP} needs 64-bit Windows 10 or 11."
    Abort
  ${EndIf}
  ${If} ${IsNativeARM64}
    MessageBox MB_ICONSTOP "${APP} needs a PC with an Intel or AMD processor; ARM-based PCs are not supported."
    Abort
  ${EndIf}
  ; Older versions continued after a restart with "setup.exe /resume". Nothing needs a restart any
  ; more: such a leftover start just ends.
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} $0 "/resume" $1
  ${IfNot} ${Errors}
    Quit
  ${EndIf}
  ReadRegDWORD $WantDesktop HKCU "${UNINSTALL_KEY}" "DesktopShortcut"
  ReadRegDWORD $WantAutostart HKCU "${UNINSTALL_KEY}" "Autostart"
  ${If} $WantDesktop == ""
    StrCpy $WantDesktop ${BST_CHECKED}
  ${EndIf}
  ${If} $WantAutostart == ""
    StrCpy $WantAutostart ${BST_UNCHECKED}
  ${EndIf}
  StrCpy $Failed "0"
FunctionEnd

Function SkipFinishOnFailure
  ${If} $Failed == "1"
    Abort
  ${EndIf}
FunctionEnd

Function OptionsPage
  !insertmacro MUI_HEADER_TEXT "Options" "Choose how you want to start ${APP}."
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateCheckbox} 0 0 100% 12u "Create a desktop shortcut"
  Pop $DesktopBox
  ${NSD_SetState} $DesktopBox $WantDesktop
  ${NSD_CreateCheckbox} 0 20u 100% 12u "Start ${APP} in the background when I sign in to Windows"
  Pop $AutostartBox
  ${NSD_SetState} $AutostartBox $WantAutostart
  ${NSD_CreateLabel} 12u 34u 95% 36u "Recommended if you schedule Instagram posts or use watched folders: those need ${APP} running. Public YouTube posts you schedule go out even when the PC is off."
  Pop $0
  nsDialogs::Show
FunctionEnd

Function OptionsLeave
  ${NSD_GetState} $DesktopBox $WantDesktop
  ${NSD_GetState} $AutostartBox $WantAutostart
FunctionEnd

; Runs one install.ps1 step, streaming its output into the details list.
!macro Step NAME TEXT
  ${If} $Failed == "0"
    DetailPrint "${TEXT}"
    nsExec::ExecToLog '${PS} "$INSTDIR\app\installer\windows\scripts\install.ps1" -Step ${NAME} -InstallDir "$INSTDIR"'
    Pop $0
    ${If} $0 != "0"
      StrCpy $Failed "1"
      DetailPrint "Setup stopped at: ${TEXT} (exit code $0)"
    ${EndIf}
  ${EndIf}
!macroend

Section "CLIPFORGE" SecMain
  CreateDirectory "$INSTDIR"
  ClearErrors
  FileOpen $0 "$INSTDIR\write-test.tmp" w
  ${If} ${Errors}
    MessageBox MB_ICONSTOP "Setup can't write to $INSTDIR (an older version may have been installed there with administrator rights).$\r$\n$\r$\nRun setup again and choose another folder, for example $LOCALAPPDATA\ClipForge." /SD IDOK
    StrCpy $Failed "1"
    Abort
  ${EndIf}
  FileClose $0
  Delete "$INSTDIR\write-test.tmp"
  SetOutPath "$INSTDIR"
  ; An update: stop the running CLIPFORGE (this or an older version) before its files are replaced.
  ${If} ${FileExists} "$INSTDIR\app\installer\windows\scripts\clipforge.ps1"
    DetailPrint "Stopping the running ${APP}…"
    nsExec::ExecToLog '${PS} ${LAUNCHER} stop -NoWindow'
    Pop $0
  ${EndIf}

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  ${If} "$EXEPATH" != "$INSTDIR\setup.exe"
    CopyFiles /SILENT "$EXEPATH" "$INSTDIR\setup.exe"
  ${EndIf}
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "${APP}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\clipforge.ico"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "DesktopShortcut" $WantDesktop
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "Autostart" $WantAutostart

  DetailPrint "Copying ${APP}…"
  RMDir /r "$INSTDIR\app"
  SetOutPath "$INSTDIR\app"
  File /r "${PAYLOAD}\*.*"
  SetOutPath "$INSTDIR"
  File "clipforge.ico"

  !insertmacro Step "preflight" "Step 1 of 6: checking this PC"
  !insertmacro Step "previous" "Step 2 of 6: stopping a running ${APP}"
  ${If} ${FileExists} "$INSTDIR\previous-linux-version"
    MessageBox MB_YESNO|MB_ICONQUESTION "An older ${APP} that ran in a Linux environment (WSL) is on this PC. The new ${APP} doesn't need it.$\r$\n$\r$\nDelete it, with its projects and settings? (They are not moved over. Choose No to keep it for now; it stays stopped.)" /SD IDNO IDNO keep_previous
      !insertmacro Step "remove-previous" "Removing the older, Linux-based ${APP}"
    keep_previous:
  ${EndIf}
  !insertmacro Step "runtime" "Step 3 of 6: downloading Python and ${APP}'s components (about 1.2 GB, 5–15 minutes)"
  !insertmacro Step "setup" "Step 4 of 6: creating ${APP}'s database"
  !insertmacro Step "models" "Step 5 of 6: downloading the AI models (1.8 GB speech model, with progress)"

  ${If} $Failed == "1"
    Goto failed  ; no shortcuts to something that isn't installed yet
  ${EndIf}

  ; Shortcuts
  CreateDirectory "$SMPROGRAMS\${APP}"
  CreateShortCut "$SMPROGRAMS\${APP}\${APP}.lnk" "powershell.exe" '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ${LAUNCHER} open' "$INSTDIR\clipforge.ico" 0 SW_SHOWMINIMIZED
  CreateShortCut "$SMPROGRAMS\${APP}\Stop ${APP}.lnk" "powershell.exe" '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ${LAUNCHER} stop' "$INSTDIR\clipforge.ico" 0 SW_SHOWMINIMIZED
  CreateShortCut "$SMPROGRAMS\${APP}\${APP} watched folders.lnk" "$VIDEOS\ClipForge"  ; the Videos folder, wherever it is
  CreateShortCut "$SMPROGRAMS\${APP}\Uninstall ${APP}.lnk" "$INSTDIR\Uninstall.exe"
  ${If} $WantDesktop == ${BST_CHECKED}
    CreateShortCut "$DESKTOP\${APP}.lnk" "powershell.exe" '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ${LAUNCHER} open' "$INSTDIR\clipforge.ico" 0 SW_SHOWMINIMIZED
  ${Else}
    Delete "$DESKTOP\${APP}.lnk"
  ${EndIf}
  ${If} $WantAutostart == ${BST_CHECKED}
    CreateShortCut "$SMSTARTUP\${APP}.lnk" "powershell.exe" '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ${LAUNCHER} start' "$INSTDIR\clipforge.ico" 0 SW_SHOWMINIMIZED
  ${Else}
    Delete "$SMSTARTUP\${APP}.lnk"
  ${EndIf}

  !insertmacro Step "start" "Step 6 of 6: starting ${APP}"
  failed:
  ${If} $Failed == "1"
    MessageBox MB_ICONSTOP "Setup could not finish. The details are listed above and saved in $INSTDIR\install.log.$\r$\n$\r$\nRunning setup again continues where it stopped." /SD IDOK
    SetDetailsView show
    Abort
  ${EndIf}
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" $0
  DetailPrint "Done. ${APP} is running at http://localhost:3000"
SectionEnd

Function OpenClipForge
  ; CLIPFORGE is already running (setup's last step waited for it). Explorer opens the address in
  ; the default browser.
  Exec '"$WINDIR\explorer.exe" "http://localhost:3000"'
FunctionEnd

Section "Uninstall"
  DetailPrint "Stopping ${APP}…"
  nsExec::ExecToLog '${PS} ${LAUNCHER} stop -NoWindow'
  Pop $0
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "Also delete everything ${APP} stored — your projects, clips, settings and connected accounts?$\r$\n$\r$\nChoose No to keep them for a later re-install. Your Videos\ClipForge folder is never deleted." /SD IDNO IDNO keep
    DetailPrint "Deleting ${APP}'s data…"
    RMDir /r "$INSTDIR\data"
    RMDir /r "$INSTDIR\logs"
    Delete "$INSTDIR\config.env"
  keep:
  Delete "$SMPROGRAMS\${APP}\*.lnk"
  RMDir "$SMPROGRAMS\${APP}"
  Delete "$DESKTOP\${APP}.lnk"
  Delete "$SMSTARTUP\${APP}.lnk"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\run"
  Delete "$INSTDIR\setup.exe"
  Delete "$INSTDIR\clipforge.ico"
  Delete "$INSTDIR\install.log"
  Delete "$INSTDIR\Uninstall.exe"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  RMDir "$INSTDIR"
SectionEnd
