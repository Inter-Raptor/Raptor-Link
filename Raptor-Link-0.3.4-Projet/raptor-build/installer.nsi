Unicode True
!include "MUI2.nsh"
!include "x64.nsh"
Name "Raptor Link"
!ifndef APPDIR
!define APPDIR "../RaptorLink"
!endif
!ifndef OUTPUT
!define OUTPUT "../Raptor-Link-Setup-0.3.7.exe"
!endif
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\RaptorLink"
InstallDirRegKey HKCU "Software\AuroraWLED" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
Icon "${APPDIR}\\icon.ico"
UninstallIcon "${APPDIR}\\icon.ico"
VIProductVersion "0.3.7.0"
VIAddVersionKey "ProductName" "Raptor Link"
VIAddVersionKey "FileDescription" "Raptor Link - installation Windows"
VIAddVersionKey "FileVersion" "0.3.7"
VIAddVersionKey "LegalCopyright" "Application independante - 2026"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Bienvenue dans Raptor Link"
!define MUI_WELCOMEPAGE_TEXT "Synchronisez vos eclairages WLED avec les animations iCUE.$\r$\n$\r$\nInterface visuelle, plusieurs appareils et automatismes.$\r$\n$\r$\nWindows 64 bits et WLED sont necessaires. iCUE est optionnel. Les autres sources RGB utilisent OpenRGB en beta.$\r$\n$\r$\nFermez Aurora et votre ancienne passerelle avant de poursuivre."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\RaptorLink.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Ouvrir Raptor Link"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "French"
!insertmacro MUI_LANGUAGE "English"
Function .onInit
  !insertmacro MUI_LANGDLL_DISPLAY
  System::Call 'kernel32::OpenMutexW(i 0x100000, i 0, w "Local\AuroraWLED-0.2") p.r0'
  ${If} $0 != 0
    System::Call 'kernel32::CloseHandle(p r0)'
    MessageBox MB_ICONSTOP "Fermez Aurora WLED / Raptor Link via Quitter dans son icone pres de l'horloge, puis relancez l'installation."
    Abort
  ${EndIf}
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "Raptor Link necessite Windows 64 bits."
    Abort
  ${EndIf}
FunctionEnd
Section "Raptor Link"
 SetOutPath "$INSTDIR"
 Delete "$DESKTOP\Aurora WLED.lnk"
 RMDir /r "$SMPROGRAMS\Aurora WLED"
 Delete "$INSTDIR\AuroraWLED.exe"
 File /r /x "__pycache__" /x "demo-data" "${APPDIR}\\*"
 WriteUninstaller "$INSTDIR\Desinstaller.exe"
 CreateDirectory "$SMPROGRAMS\Raptor Link"
 CreateShortcut "$SMPROGRAMS\Raptor Link\Raptor Link.lnk" "$INSTDIR\RaptorLink.exe" "" "$INSTDIR\icon.ico"
 CreateShortcut "$SMPROGRAMS\Raptor Link\Desinstaller.lnk" "$INSTDIR\Desinstaller.exe"
 CreateShortcut "$DESKTOP\Raptor Link.lnk" "$INSTDIR\RaptorLink.exe" "" "$INSTDIR\icon.ico"
 WriteRegStr HKCU "Software\AuroraWLED" "InstallDir" "$INSTDIR"
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AuroraWLED" "DisplayName" "Raptor Link"
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AuroraWLED" "UninstallString" '"$INSTDIR\Desinstaller.exe"'
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AuroraWLED" "DisplayIcon" "$INSTDIR\icon.ico"
 WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AuroraWLED" "DisplayVersion" "0.3.7 beta"
 WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AuroraWLED" "NoModify" 1
 WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AuroraWLED" "NoRepair" 1
SectionEnd
Section "Uninstall"
 MessageBox MB_OKCANCEL "Fermez Raptor Link via son icone pres de l'horloge avant de poursuivre. Vos configurations seront conservees." IDOK +2
 Abort
 DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "AuroraWLED"
 Delete "$DESKTOP\Raptor Link.lnk"
 RMDir /r "$SMPROGRAMS\Raptor Link"
 RMDir /r "$INSTDIR"
 DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AuroraWLED"
 DeleteRegKey HKCU "Software\AuroraWLED"
SectionEnd
