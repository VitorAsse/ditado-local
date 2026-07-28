Option Explicit

Dim shell, fileSystem, appRoot, pythonw, appFile, command
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

appRoot = fileSystem.GetParentFolderName(WScript.ScriptFullName)
pythonw = appRoot & "\.venv\Scripts\pythonw.exe"
appFile = appRoot & "\ditado_local.pyw"

If Not fileSystem.FileExists(pythonw) Then
    MsgBox "A instalacao do Ditado Local nao foi encontrada. Execute install.ps1.", 16, "Ditado Local"
    WScript.Quit 1
End If

shell.CurrentDirectory = appRoot
command = """" & pythonw & """ """ & appFile & """"
shell.Run command, 1, False
