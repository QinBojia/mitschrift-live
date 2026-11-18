; Small update for an installed Mitschrift Live / 随堂字幕 (any edition, also installs made under
; the old name "Live Capture"): replaces the program code (app\livecapture, app\ui) and nothing
; else - Python, packages, models and edition.json stay. Also renames old "Live Capture" shortcuts.
; Compiled by build_friends.py --patch:  ISCC /DAppVersion=x /DSourceDir=... /DOutputDir=... update.iss
; Updates that need new packages or models still need the full installer.

#ifndef AppVersion
  #define AppVersion "0.1.1"
#endif
#ifndef SourceDir
  #error SourceDir must be defined (/DSourceDir=...)
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif
#define AppIdGuid "{6B1E4E5C-2F7A-4C1B-9D3E-6A2B8F1C0D47}"

[Setup]
; same AppId as installer.iss: installs into the existing folder and updates its uninstall entry
AppId={{6B1E4E5C-2F7A-4C1B-9D3E-6A2B8F1C0D47}
AppName=随堂字幕 Mitschrift Live
AppVersion={#AppVersion}
AppVerName=随堂字幕 Mitschrift Live {#AppVersion} 更新
AppPublisher=Mitschrift Live
DefaultDirName={localappdata}\Programs\Mitschrift Live
UsePreviousAppDir=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=MitschriftLive-{#AppVersion}-Update
SetupIconFile={#SourceDir}\live-capture.ico
UninstallDisplayIcon={app}\live-capture.ico
UninstallDisplayName=随堂字幕 Mitschrift Live ({code:EditionName})
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
CloseApplications=no

[Languages]
Name: "chs"; MessagesFile: "lang\ChineseSimplified.isl"

[Messages]
chs.FinishedHeadingLabel=随堂字幕已更新到 {#AppVersion}

[InstallDelete]
; fresh code folders (drops modules an update removed, and stale __pycache__)
Type: filesandordirs; Name: "{app}\app\livecapture"
Type: filesandordirs; Name: "{app}\app\ui"
; shortcuts under the old name "Live Capture" (recreated below under the new name)
Type: files; Name: "{userprograms}\Live Capture.lnk"
Type: files; Name: "{userprograms}\Live Capture 使用说明.lnk"
Type: files; Name: "{userdesktop}\Live Capture.lnk"

[Files]
Source: "{#SourceDir}\app\livecapture\*"; DestDir: "{app}\app\livecapture"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\app\ui\*"; DestDir: "{app}\app\ui"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\随堂字幕"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m livecapture.app"; WorkingDir: "{app}\app"; IconFilename: "{app}\live-capture.ico"; AppUserModelID: "LiveCapture.Desktop"
Name: "{userprograms}\随堂字幕 使用说明"; Filename: "{app}\使用说明.txt"
; a desktop shortcut only where there was one before
Name: "{userdesktop}\随堂字幕"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m livecapture.app"; WorkingDir: "{app}\app"; IconFilename: "{app}\live-capture.ico"; AppUserModelID: "LiveCapture.Desktop"; Check: HadDesktopIcon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m livecapture.app"; WorkingDir: "{app}\app"; Description: "启动随堂字幕"; Flags: nowait postinstall skipifsilent

[Code]
#include "shared_code.iss"

const
  UninstKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppIdGuid}_is1';
var
  HadDesktop: Boolean;

function InitializeSetup(): Boolean;
var
  Dir: String;
begin
  Result := RegQueryStringValue(HKCU, UninstKey, 'InstallLocation', Dir)
            and FileExists(AddBackslash(Dir) + 'python\pythonw.exe')
            and FileExists(AddBackslash(Dir) + 'app\edition.json');
  if not Result then
    SuppressibleMsgBox('没有找到已安装的随堂字幕（旧名 Live Capture）。' + #13#10 +
                       '请先用完整安装包（MitschriftLive-...-Setup.exe）安装，再运行这个更新。',
                       mbError, MB_OK, IDOK);
end;

// "friends-amd" -> "AMD", read from the installed edition.json (the update itself has none)
function EditionName(Param: String): String;
var
  S: AnsiString;
  P: Integer;
begin
  Result := '';
  if LoadStringFromFile(ExpandConstant('{app}\app\edition.json'), S) then
  begin
    P := Pos('"friends-', S);
    if P > 0 then
    begin
      S := Copy(S, P + 9, 8);
      P := Pos('"', S);
      if P > 0 then
        Result := Uppercase(Copy(S, 1, P - 1));
    end;
  end;
  if Result = '' then
    Result := '{#AppVersion}';
end;

function HadDesktopIcon(): Boolean;
begin
  Result := HadDesktop;
end;

// Runs before [InstallDelete]: note whether a desktop shortcut exists (old or new name), then
// close the running app - its code is loaded in memory.
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  HadDesktop := FileExists(ExpandConstant('{userdesktop}\Live Capture.lnk'))
                or FileExists(ExpandConstant('{userdesktop}\随堂字幕.lnk'));
  StopRunningApp();
  Result := '';
end;
