' 无窗口启动（pythonw），供快捷方式或启动项调用。
' 用法：把本文件加入「启动」文件夹，或建快捷方式指向 wscript.exe "本文件路径"
Option Explicit
Dim sh, fso, base
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
sh.Run "pythonw.exe """ & base & "\main.py""", 0, False
