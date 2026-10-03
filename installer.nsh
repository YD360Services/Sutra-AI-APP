; NSIS script for handling upgrades of previous app versions
!macro customInit
  ; Terminate any existing running processes of previous or current app versions so files are not locked
  nsExec::ExecToStack 'cmd /c taskkill /F /IM RM.exe /T'
  nsExec::ExecToStack 'cmd /c taskkill /F /IM "Roundmate AI.exe" /T'
  nsExec::ExecToStack 'cmd /c taskkill /F /IM Roundmate.exe /T'
  nsExec::ExecToStack 'cmd /c taskkill /F /IM RoundMate.exe /T'
  nsExec::ExecToStack 'cmd /c taskkill /F /IM stealth_keyhook.exe /T'

  ; Look for existing install directory from previous versions in Registry
  ; 1. Check current app HKCU
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\com.rm.stealthapp" "InstallLocation"
  ${If} $0 != ""
  ${AndIf} ${FileExists} "$0"
    StrCpy $INSTDIR $0
  ${Else}
    ; 2. Check legacy Roundmate AI HKCU
    ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Roundmate AI" "InstallLocation"
    ${If} $0 != ""
    ${AndIf} ${FileExists} "$0"
      StrCpy $INSTDIR $0
    ${Else}
      ; 3. Check current app HKLM
      ReadRegStr $0 HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\com.rm.stealthapp" "InstallLocation"
      ${If} $0 != ""
      ${AndIf} ${FileExists} "$0"
        StrCpy $INSTDIR $0
      ${Else}
        ; 4. Check legacy Roundmate AI HKLM
        ReadRegStr $0 HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\Roundmate AI" "InstallLocation"
        ${If} $0 != ""
        ${AndIf} ${FileExists} "$0"
          StrCpy $INSTDIR $0
        ${EndIf}
      ${EndIf}
    ${EndIf}
  ${EndIf}

  ; If legacy uninstaller exists, trigger silent uninstall of old version entry before new install
  ReadRegStr $1 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Roundmate AI" "QuietUninstallString"
  ${If} $1 != ""
    ExecWait '$1'
  ${Else}
    ReadRegStr $1 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Roundmate AI" "UninstallString"
    ${If} $1 != ""
      ExecWait '$1 /S _?=$INSTDIR'
    ${EndIf}
  ${EndIf}
!macroend

!macro customInstall
  ; Clean up legacy shortcuts and uninstall entries from older versions
  Delete "$DESKTOP\Roundmate AI.lnk"
  Delete "$SMPROGRAMS\Roundmate AI.lnk"
  Delete "$SMPROGRAMS\Roundmate AI\Roundmate AI.lnk"
  RMDir "$SMPROGRAMS\Roundmate AI"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Roundmate AI"
!macroend
