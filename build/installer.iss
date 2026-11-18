; Inno Setup script for Mitschrift Live (随堂字幕) installers.
; Compiled by build_installer.py:
;   ISCC /DAppVersion=x /DEdition=CPU|NPU|AMD /DSourceDir=... /DOutputDir=... /DNotes=QuickStart.txt [/DFriends] installer.iss
; Public builds: English + Chinese (picked from the Windows language); friends' builds: Chinese.
; All editions share one AppId, so installing one replaces the other. The AppId, the program
; folder of existing installs and ~/.livecapture date from the old name "Live Capture" and
; stay as they are, so older installs upgrade in place.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef Edition
  #define Edition "CPU"
#endif
#ifndef SourceDir
  #error SourceDir must be defined (/DSourceDir=...)
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif
#ifndef Notes
  #define Notes "QuickStart.txt"
#endif

[Setup]
AppId={{6B1E4E5C-2F7A-4C1B-9D3E-6A2B8F1C0D47}
AppName=随堂字幕 Mitschrift Live
AppVersion={#AppVersion}
AppVerName=随堂字幕 Mitschrift Live {#AppVersion} ({#Edition})
AppPublisher=Mitschrift Live
AppPublisherURL=https://github.com/QinBojia/mitschrift-live
; new installs only - an existing install keeps its folder (UsePreviousAppDir)
DefaultDirName={localappdata}\Programs\Mitschrift Live
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=MitschriftLive-{#AppVersion}-{#Edition}-Setup
SetupIconFile={#SourceDir}\live-capture.ico
UninstallDisplayIcon={app}\live-capture.ico
UninstallDisplayName=随堂字幕 Mitschrift Live ({#Edition})
Compression=lzma2/normal
SolidCompression=yes
LZMANumBlockThreads=8
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
CloseApplications=yes
RestartApplications=no
InfoAfterFile={#SourceDir}\{#Notes}
ShowLanguageDialog=no

[Languages]
#ifndef Friends
Name: "en"; MessagesFile: "compiler:Default.isl"
#endif
Name: "chs"; MessagesFile: "lang\ChineseSimplified.isl"

#include "shared_messages.iss"

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:Shortcuts}"

[InstallDelete]
; switching edition (e.g. NPU -> CPU) or updating: start from a clean program folder
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\licenses"
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
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{code:ShortcutName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m livecapture.app"; WorkingDir: "{app}\app"; IconFilename: "{app}\live-capture.ico"; AppUserModelID: "LiveCapture.Desktop"
Name: "{userdesktop}\{code:ShortcutName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m livecapture.app"; WorkingDir: "{app}\app"; IconFilename: "{app}\live-capture.ico"; AppUserModelID: "LiveCapture.Desktop"; Tasks: desktopicon
Name: "{userprograms}\{code:NotesShortcutName}"; Filename: "{app}\{#Notes}"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m livecapture.app"; WorkingDir: "{app}\app"; Description: "{cm:Launch}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; runtime leftovers (__pycache__) in our own folders only - never the whole {app}
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\python"
Type: dirifempty; Name: "{app}"

[Code]
#include "shared_code.iss"

// before [InstallDelete] empties the program folder: a running copy would keep its DLLs locked
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningApp();
  Result := '';
end;
