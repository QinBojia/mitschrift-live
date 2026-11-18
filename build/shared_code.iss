// Shared by installer.iss and update.iss (#include in [Code]). Both need it because an
// uninstall runs the [Code] of whichever setup ran last - a full installer or an update.

// The app keeps running in the tray after its window is closed, holding Python's DLLs open:
// close it (only pythonw / python started from the install folder) before files are touched.
procedure StopRunningApp();
var
  RC: Integer;
begin
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
       '-NoProfile -ExecutionPolicy Bypass -Command "Get-Process pythonw,python -ErrorAction SilentlyContinue | ' +
       'Where-Object { $_.Path -like ''' + ExpandConstant('{app}') + '\*'' } | Stop-Process -Force; Start-Sleep -Milliseconds 800"',
       '', SW_HIDE, ewWaitUntilTerminated, RC);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // usUninstall: after the "remove?" confirmation, before any file is deleted
  if CurUninstallStep = usUninstall then
    StopRunningApp();
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    if MsgBox('是否同时删除设置和日志（' + ExpandConstant('{%USERPROFILE}') + '\.livecapture）？' + #13#10 +
              '录音文件（文档\Mitschrift Live，以及旧版的 文档\Live Capture）不会被删除。', mbConfirmation, MB_YESNO) = IDYES then
      DelTree(ExpandConstant('{%USERPROFILE}') + '\.livecapture', True, True, True);
end;
