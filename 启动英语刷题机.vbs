' English Practice Machine launcher (no console window)
Set fso = CreateObject("Scripting.FileSystemObject")
projectRoot = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = projectRoot & "\.venv\Scripts\pythonw.exe"
launcher = projectRoot & "\desktop_app.py"
If Not fso.FileExists(pythonw) Then
    MsgBox "Environment not found. Run setup.ps1 first.", vbExclamation, "English Practice Machine"
    WScript.Quit 1
End If
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = projectRoot
shell.Run chr(34) & pythonw & chr(34) & " " & chr(34) & launcher & chr(34), 0, False
