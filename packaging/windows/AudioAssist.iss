#define MyAppName "AudioAssist"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-local"
#endif
#ifndef MyAppPublisher
  #define MyAppPublisher "AudioAssist"
#endif
#ifndef MySourceDir
  #error MySourceDir must point to the built PyInstaller onedir directory.
#endif
#ifndef MyWebView2Bootstrapper
  #error MyWebView2Bootstrapper must point to MicrosoftEdgeWebView2Setup.exe.
#endif

[Setup]
AppId={{9E4D6A70-A6A2-4A77-9D9D-313334A1E635}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma
SolidCompression=yes
WizardStyle=modern
OutputBaseFilename=AudioAssist-Setup-x64
SetupIconFile=
UninstallDisplayIcon={app}\AudioAssist.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MyWebView2Bootstrapper}"; DestDir: "{tmp}"; DestName: "MicrosoftEdgeWebView2Setup.exe"; Flags: deleteafterinstall

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\AudioAssist.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\AudioAssist.exe"; Tasks: desktopicon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebView2Setup.exe"; Parameters: "/silent /install"; Flags: runhidden waituntilterminated; StatusMsg: "Installing Microsoft Edge WebView2 Runtime..."
Filename: "{app}\AudioAssist.exe"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
