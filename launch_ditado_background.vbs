Option Explicit

Dim shell, fileSystem, appRoot, pythonw, appFile, command
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

appRoot = fileSystem.GetParentFolderName(WScript.ScriptFullName)
pythonw = appRoot & "\.venv\Scripts\pythonw.exe"
appFile = appRoot & "\ditado_local.pyw"

If Not fileSystem.FileExists(pythonw) Then
    WScript.Quit 1
End If

shell.CurrentDirectory = appRoot
command = """" & pythonw & """ """ & appFile & """ --background"
shell.Run command, 0, False
