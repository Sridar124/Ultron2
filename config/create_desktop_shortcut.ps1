$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('C:\Users\ravit\OneDrive\Desktop\Ultron - Premium.lnk')
$Shortcut.TargetPath = 'D:\TiTech Prabha Solution\Ultron AI\Ultron AI\Ultron-AI---Lite-main\Ultron-AI---Lite-main\.venv\Scripts\pythonw.exe'
$Shortcut.Arguments = '"D:\TiTech Prabha Solution\Ultron AI\Ultron AI\Ultron-AI---Lite-main\Ultron-AI---Lite-main\main.py"'
$Shortcut.WorkingDirectory = 'D:\TiTech Prabha Solution\Ultron AI\Ultron AI\Ultron-AI---Lite-main\Ultron-AI---Lite-main'
$Shortcut.WindowStyle = 7
$Shortcut.Description = 'Launch Ultron - Premium'
if ('D:\TiTech Prabha Solution\Ultron AI\Ultron AI\Ultron-AI---Lite-main\Ultron-AI---Lite-main\assets\Ultron_Logo.ico') { $Shortcut.IconLocation = 'D:\TiTech Prabha Solution\Ultron AI\Ultron AI\Ultron-AI---Lite-main\Ultron-AI---Lite-main\assets\Ultron_Logo.ico,0' }
$Shortcut.Save()