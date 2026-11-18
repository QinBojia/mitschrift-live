; Small update for an installed Mitschrift Live / 随堂字幕 (any edition, public or friends, also
; installs made under the old name "Live Capture"): replaces the program code (app\livecapture,
; app\ui) and nothing else - Python, packages, models, edition.json and notes stay. Shortcuts
; from earlier names are renamed. One package for all editions, so it has both languages.
; Compiled by build_installer.py --patch:  ISCC /DAppVersion=x /DSourceDir=... /DOutputDir=... update.iss
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
AppVerName=随堂字幕 Mitschrift Live {#AppVersion}
AppPublisher=Mitschrift Live
AppPublisherURL=https://github.com/QinBojia/mitschrift-live
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
ShowLanguageDialog=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "chs"; MessagesFile: "lang\ChineseSimplified.isl"

#include "shared_messages.iss"

[Messages]
en.FinishedHeadingLabel=Mitschrift Live has been updated to {#AppVersion}
chs.FinishedHeadingLabel=随堂字幕已更新到 {#AppVersion}

[InstallDelete]
; fresh code folders (drops modules an update removed, and stale __pycache__)
Type: filesandordirs; Name: "{app}\app\livecapture"
Type: filesandordirs; Name: "{app}\app\ui"
; shortcuts under any earlier name (recreated below)
Type: files; Name: "{userprograms}\Live Capture.lnk"
Type: files; Name: "{userprograms}\Live Capture 使用说明.lnk"
Type: files; Name: "{userdesktop}\Live Capture.lnk"
Type: files; Name: "{userprograms}\随堂字幕.lnk"
Type: files; Name: "{userprograms}\随堂字幕 使用说明.lnk"
Type: files; Name: "{userdesktop}\随堂字幕.lnk"
Type: files; Name: "{userprograms}\Mitschrift Live.lnk"
Type: files; Name: "{userprograms}\Mitschrift Live Quick Start.lnk"
Type: files; Name: "{userdesktop}\Mitschrift Live.lnk"

[Files]
Source: "{#SourceDir}\app\livecapture\*"; DestDir: "{app}\app\livecapture"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\app\ui\*"; DestDir: "{app}\app\ui"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{code:ShortcutName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m livecapture.app"; WorkingDir: "{app}\app"; IconFilename: "{app}\live-capture.ico"; AppUserModelID: "LiveCapture.Desktop"
Name: "{userprograms}\{code:NotesShortcutName}"; Filename: "{app}\{code:NotesFile}"; Check: HasNotes
; a desktop shortcut only where there was one before
Name: "{userdesktop}\{code:ShortcutName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m livecapture.app"; WorkingDir: "{app}\app"; IconFilename: "{app}\live-capture.ico"; AppUserModelID: "LiveCapture.Desktop"; Check: HadDesktopIcon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m livecapture.app"; WorkingDir: "{app}\app"; Description: "{cm:Launch}"; Flags: nowait postinstall skipifsilent

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
    SuppressibleMsgBox(CustomMessage('NotInstalled'), mbError, MB_OK, IDOK);
end;

function HadDesktopIcon(): Boolean;
begin
  Result := HadDesktop;
end;

// Runs before [InstallDelete]: note whether a desktop shortcut exists (any name), then close
// the running app - its code is loaded in memory.
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  HadDesktop := FileExists(ExpandConstant('{userdesktop}\Live Capture.lnk'))
                or FileExists(ExpandConstant('{userdesktop}\随堂字幕.lnk'))
                or FileExists(ExpandConstant('{userdesktop}\Mitschrift Live.lnk'));
  StopRunningApp();
  Result := '';
end;
