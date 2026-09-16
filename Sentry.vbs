Option Explicit

Dim shell, files, root, pythonw, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")

root = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root

pythonw = root & "\.venv\Scripts\pythonw.exe"
If Not files.FileExists(pythonw) Then
    pythonw = shell.ExpandEnvironmentStrings("%USERPROFILE%") & _
        "\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
End If

If files.FileExists(pythonw) Then
    command = """" & pythonw & """ -m app.main"
Else
    command = "pythonw.exe -m app.main"
End If

' pythonw.exe has no console; normal window style lets Qt control its own main window.
shell.Run command, 1, False
