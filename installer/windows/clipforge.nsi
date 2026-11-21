; CLIPFORGE for Windows 10 and 11 — setup.exe
;
; Build (on Linux or Windows, NSIS 3):   installer/windows/build.sh
; Installs into %LOCALAPPDATA%\ClipForge. All the work is done by scripts\install.ps1, one step at a
; time, so the progress bar moves and the details list shows what is happening. Running setup again
; updates CLIPFORGE and keeps all data.

Unicode true
SetCompressor /SOLID lzma
ManifestDPIAware true

!ifndef VERSION
  !define VERSION "0.2.1"
!endif
!ifndef PAYLOAD
  !define PAYLOAD "build\payload"
!endif
!ifndef OUTFILE
  !define OUTFILE "build\ClipForge-Setup-${VERSION}.exe"
!endif

!define APP "CLIPFORGE"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipForge"
; setup.exe is a 32-bit program: Windows shows it SysWOW64 (32-bit programs only, no wsl.exe) when it
; asks for System32. "Sysnative" is how a 32-bit program reaches the real, 64-bit System32.
!define SYSNATIVE "$WINDIR\Sysnative"
!define PS '"${SYSNATIVE}\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File'

Name "${APP}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\ClipForge"
InstallDirRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipForge" "InstallLocation"  ; updates go to the same folder
RequestExecutionLevel admin   ; turning on WSL 2 needs administrator rights once
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
!define MUI_WELCOMEPAGE_TEXT "${APP} turns your long videos into Shorts and Reels and publishes them.$\r$\n$\r$\nSetup prepares everything automatically:$\r$\n  • turns on WSL 2 (Windows Subsystem for Linux) if needed$\r$\n  • creates CLIPFORGE's own Linux environment with Docker Engine$\r$\n  • builds CLIPFORGE and downloads its speech model$\r$\n$\r$\nThe first installation downloads about 5 GB and takes 20–40 minutes. If Windows has to restart to turn on WSL 2, setup continues by itself after you sign in again.$\r$\n$\r$\nKeep the PC on and connected while setup runs."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Open ${APP} now"
!define MUI_FINISHPAGE_RUN_FUNCTION OpenClipForge

Var Resume
Var WantDesktop
Var WantAutostart
Var DesktopBox
Var AutostartBox
Var Failed
Var Restart

!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipWhenResuming
!insertmacro MUI_PAGE_WELCOME
Page custom OptionsPage OptionsLeave
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipWhenResuming
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipFinishOnFailure
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "${APP} needs 64-bit Windows 10 (version 2004 or later) or Windows 11."
    Abort
  ${EndIf}
  ${If} ${IsNativeARM64}
    MessageBox MB_ICONSTOP "${APP} needs a PC with an Intel or AMD processor; ARM-based PCs are not supported."
    Abort
  ${EndIf}
  StrCpy $WantDesktop ${BST_CHECKED}
  StrCpy $WantAutostart ${BST_UNCHECKED}
  StrCpy $Failed "0"
  StrCpy $Restart "0"
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} $0 "/resume" $1
  ${IfNot} ${Errors}
    StrCpy $Resume "1"
    ReadRegStr $INSTDIR HKCU "${UNINSTALL_KEY}" "InstallLocation"
    ReadRegDWORD $WantDesktop HKCU "${UNINSTALL_KEY}" "DesktopShortcut"
    ReadRegDWORD $WantAutostart HKCU "${UNINSTALL_KEY}" "Autostart"
    ${If} $INSTDIR == ""
      StrCpy $INSTDIR "$LOCALAPPDATA\ClipForge"
    ${EndIf}
  ${EndIf}
FunctionEnd

Function SkipWhenResuming
  ${If} $Resume == "1"
    Abort
  ${EndIf}
FunctionEnd

Function SkipFinishOnFailure
  ${If} $Failed == "1"
  ${OrIf} $Restart == "1"
    Abort
  ${EndIf}
FunctionEnd

Function OptionsPage
  ${If} $Resume == "1"
    Abort
  ${EndIf}
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
  ${AndIf} $Restart == "0"
    DetailPrint "${TEXT}"
    nsExec::ExecToLog '${PS} "$INSTDIR\app\installer\windows\scripts\install.ps1" -Step ${NAME} -InstallDir "$INSTDIR"'
    Pop $0
    ${If} $0 == "3010"
      StrCpy $Restart "1"
    ${ElseIf} $0 != "0"
      StrCpy $Failed "1"
      DetailPrint "Setup stopped at: ${TEXT} (exit code $0)"
    ${EndIf}
  ${EndIf}
!macroend

Section "CLIPFORGE" SecMain
  SetOutPath "$INSTDIR"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  ${If} "$EXEPATH" != "$INSTDIR\setup.exe"  ; after a restart, setup runs from there itself
    CopyFiles /SILENT "$EXEPATH" "$INSTDIR\setup.exe"
  ${EndIf}
  ; Remember the choices so a restart (or an update) continues with the same ones.
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

  ${IfNot} $Resume == "1"
    DetailPrint "Copying ${APP}…"
    RMDir /r "$INSTDIR\app"
    SetOutPath "$INSTDIR\app"
    File /r "${PAYLOAD}\*.*"
    SetOutPath "$INSTDIR"
    File "clipforge.ico"
  ${EndIf}

  !insertmacro Step "preflight" "Step 1 of 7: checking this PC"
  !insertmacro Step "wsl" "Step 2 of 7: turning on WSL 2"
  !insertmacro Step "distro" "Step 3 of 7: creating CLIPFORGE's Linux environment"
  !insertmacro Step "system" "Step 4 of 7: installing Docker Engine"
  !insertmacro Step "app" "Step 5 of 7: building CLIPFORGE (the longest step: 10–30 minutes)"
  !insertmacro Step "models" "Step 6 of 7: downloading the speech model (1.8 GB)"

  ${If} $Restart == "1"
    ; install.ps1 has arranged for setup to continue after the next sign-in.
    SetErrorLevel 3010
    MessageBox MB_YESNO|MB_ICONINFORMATION "Windows needs to restart to finish turning on WSL 2.$\r$\n$\r$\nSetup continues automatically after you sign in again (if it doesn't, run setup again). Restart now?" /SD IDNO IDNO +2
      Reboot
    DetailPrint "Restart Windows to continue; setup resumes after you sign in."
    Return
  ${EndIf}

  ; Shortcuts
  CreateDirectory "$SMPROGRAMS\${APP}"
  CreateShortCut "$SMPROGRAMS\${APP}\${APP}.lnk" "powershell.exe" '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$INSTDIR\app\installer\windows\scripts\clipforge.ps1" open' "$INSTDIR\clipforge.ico" 0 SW_SHOWMINIMIZED
  CreateShortCut "$SMPROGRAMS\${APP}\Stop ${APP}.lnk" "powershell.exe" '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$INSTDIR\app\installer\windows\scripts\clipforge.ps1" stop' "$INSTDIR\clipforge.ico" 0 SW_SHOWMINIMIZED
  CreateShortCut "$SMPROGRAMS\${APP}\${APP} watched folders.lnk" "$PROFILE\Videos\ClipForge"
  CreateShortCut "$SMPROGRAMS\${APP}\Uninstall ${APP}.lnk" "$INSTDIR\Uninstall.exe"
  ${If} $WantDesktop == ${BST_CHECKED}
    CreateShortCut "$DESKTOP\${APP}.lnk" "powershell.exe" '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$INSTDIR\app\installer\windows\scripts\clipforge.ps1" open' "$INSTDIR\clipforge.ico" 0 SW_SHOWMINIMIZED
  ${Else}
    Delete "$DESKTOP\${APP}.lnk"
  ${EndIf}
  ${If} $WantAutostart == ${BST_CHECKED}
    CreateShortCut "$SMSTARTUP\${APP}.lnk" "powershell.exe" '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$INSTDIR\app\installer\windows\scripts\clipforge.ps1" start' "$INSTDIR\clipforge.ico" 0 SW_SHOWMINIMIZED
  ${Else}
    Delete "$SMSTARTUP\${APP}.lnk"
  ${EndIf}

  !insertmacro Step "start" "Step 7 of 7: starting CLIPFORGE"
  ${If} $Failed == "1"
    MessageBox MB_ICONSTOP "Setup could not finish. The details are listed above and saved in $INSTDIR\install.log.$\r$\n$\r$\nRunning setup again continues where it stopped." /SD IDOK
    SetDetailsView show
    Abort
  ${EndIf}
  ${GetSize} "$INSTDIR\wsl" "/S=0K" $0 $1 $2
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" $0
  DetailPrint "Done. ${APP} is running at http://localhost:3000"
SectionEnd

Function OpenClipForge
  Exec '"${SYSNATIVE}\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$INSTDIR\app\installer\windows\scripts\clipforge.ps1" open'
FunctionEnd

Section "Uninstall"
  DetailPrint "Stopping ${APP}…"
  nsExec::ExecToLog '${PS} "$INSTDIR\app\installer\windows\scripts\clipforge.ps1" stop -NoWindow'
  Pop $0
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "Also delete everything ${APP} stored — your projects, clips, settings and connected accounts?$\r$\n$\r$\nChoose No to keep them for a later re-install. Your Videos\ClipForge folder is never deleted." /SD IDNO IDNO keep
    DetailPrint "Deleting CLIPFORGE's Linux environment and data…"
    nsExec::ExecToLog '"${SYSNATIVE}\wsl.exe" --unregister ClipForge'
    Pop $0
    RMDir /r "$INSTDIR\wsl"
  keep:
  Delete "$SMPROGRAMS\${APP}\*.lnk"
  RMDir "$SMPROGRAMS\${APP}"
  Delete "$DESKTOP\${APP}.lnk"
  Delete "$SMSTARTUP\${APP}.lnk"
  RMDir /r "$INSTDIR\app"
  Delete "$INSTDIR\setup.exe"
  Delete "$INSTDIR\clipforge.ico"
  Delete "$INSTDIR\Uninstall.exe"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\RunOnce" "ClipForgeSetup"
  nsExec::Exec '"${SYSNATIVE}\schtasks.exe" /Delete /TN "CLIPFORGE setup" /F'  ; continue-after-restart task, if one is left
  Pop $0
  RMDir "$INSTDIR"
SectionEnd
