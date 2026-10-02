Unicode True
!include "FileFunc.nsh"
Name "Raptor Link"
OutFile "../RaptorLink/RaptorLink.exe"
Icon "../RaptorLink/icon.ico"
RequestExecutionLevel user
SilentInstall silent
AutoCloseWindow true
Section
  ${GetParameters} $R0
  SetOutPath "$EXEDIR"
  Exec '"$EXEDIR\runtime\pythonw.exe" "$EXEDIR\app.py" $R0'
SectionEnd
