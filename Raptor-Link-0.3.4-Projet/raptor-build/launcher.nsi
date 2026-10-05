Unicode True
!include "FileFunc.nsh"
!include "LogicLib.nsh"
Name "Raptor Link"
OutFile "../RaptorLink/RaptorLink.exe"
Icon "../RaptorLink/icon.ico"
RequestExecutionLevel user
SilentInstall silent
AutoCloseWindow true

Section
  ${GetParameters} $R0
  SetOutPath "$EXEDIR"
  StrCpy $R1 0

retry:
  ExecWait '"$EXEDIR\runtime\pythonw.exe" "$EXEDIR\app.py" $R0' $R2
  ${If} $R2 == 0
    Goto done
  ${EndIf}

  IntOp $R1 $R1 + 1
  ${If} $R1 >= 3
    Goto done
  ${EndIf}

  Sleep 2000
  Goto retry

done:
SectionEnd
