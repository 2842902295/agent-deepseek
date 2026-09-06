' Silent launcher entry for the desktop edition (no console window).
' Real logic lives in launcher.pyw, executed by the bundled pythonw.exe.
' Keep this file pure ASCII on purpose (VBS encoding pitfalls).
Set fso = CreateObject("Scripting.FileSystemObject")
Set ws = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
ws.Run """" & root & "\runtime\pythonw.exe"" """ & root & "\launcher.pyw""", 0, False
