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

// "friends-amd" / "public-cpu" from the installed edition.json ('' if there is none yet)
function InstalledEdition(): String;
var
  S: AnsiString;
  P: Integer;
begin
  Result := '';
  if LoadStringFromFile(ExpandConstant('{app}\app\edition.json'), S) then
  begin
    P := Pos('"name": "', S);
    if P > 0 then
    begin
      S := Copy(S, P + 9, 40);
      P := Pos('"', S);
      if P > 0 then
        Result := Copy(S, 1, P - 1);
    end;
  end;
end;

// "AMD" for the uninstall entry; falls back to the version
function EditionName(Param: String): String;
var
  E: String;
  P: Integer;
begin
  E := InstalledEdition();
  P := Pos('-', E);
  if P > 0 then
    Result := Uppercase(Copy(E, P + 1, Length(E)))
  else
    Result := '{#AppVersion}';
end;

// Friends' builds keep the Chinese shortcut names whatever the Windows language
function ChineseNames(): Boolean;
begin
  Result := (Pos('friends', InstalledEdition()) = 1) or (ActiveLanguage() = 'chs');
end;

function ShortcutName(Param: String): String;
begin
  if ChineseNames() then Result := '随堂字幕' else Result := 'Mitschrift Live';
end;

function NotesShortcutName(Param: String): String;
begin
  if ChineseNames() then Result := '随堂字幕 使用说明' else Result := 'Mitschrift Live Quick Start';
end;

function NotesFile(Param: String): String;
begin
  if FileExists(ExpandConstant('{app}\QuickStart.txt')) then Result := 'QuickStart.txt' else Result := '使用说明.txt';
end;

function HasNotes(): Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\') + NotesFile(''));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // usUninstall: after the "remove?" confirmation, before any file is deleted
  if CurUninstallStep = usUninstall then
    StopRunningApp();
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    if MsgBox(FmtMessage(CustomMessage('DeleteSettings'), [ExpandConstant('{%USERPROFILE}') + '\.livecapture']),
              mbConfirmation, MB_YESNO) = IDYES then
      DelTree(ExpandConstant('{%USERPROFILE}') + '\.livecapture', True, True, True);
end;
