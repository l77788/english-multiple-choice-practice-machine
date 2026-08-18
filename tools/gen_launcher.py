# -*- coding: utf-8 -*-
"""Generate the double-click launcher into the portable build dir."""
import os
import sys

VBS = """\
' English Practice Machine portable launcher - double-click to run
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = appDir & "\\pythonw.exe"
launcher = appDir & "\\desktop_app.py"
If Not fso.FileExists(pythonw) Then
    MsgBox "Portable runtime is missing. Please redownload the package.", vbExclamation, "English Practice Machine"
    WScript.Quit 1
End If
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = appDir
shell.Run chr(34) & pythonw & chr(34) & " " & chr(34) & launcher & chr(34), 0, False
"""


def main() -> int:
    build = os.environ.get("EPM_BUILD")
    if not build or not os.path.isdir(build):
        print("EPM_BUILD not set or not a directory", file=sys.stderr)
        return 1
    name = "\u542f\u52a8\u82f1\u8bed\u5237\u9898\u673a.vbs"  # 启动英语刷题机.vbs
    path = os.path.join(build, name)
    with open(path, "w", encoding="ascii") as f:
        f.write(VBS)
    print("launcher written:", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())