; OmicChain Windows installer (Inno Setup 6). Built by installer\build.ps1.
#ifndef AppVersion
  #define AppVersion "dev"
#endif

[Setup]
AppId={{6F2B8C1E-4D7A-4E59-9B3C-0A51C7E2D8F4}
AppName=OmicChain
AppVersion={#AppVersion}
AppVerName=OmicChain {#AppVersion}
AppPublisher=UR24144 ProDiCeT, University of Poitiers
AppPublisherURL=https://github.com/UR24144-ProDiCeT/OmicChain
AppSupportURL=https://github.com/UR24144-ProDiCeT/OmicChain/issues
DefaultDirName={localappdata}\Programs\OmicChain
DefaultGroupName=OmicChain
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutDir}
OutputBaseFilename=OmicChain-{#AppVersion}-windows-setup
SetupIconFile={#Bundle}\omicchain.ico
UninstallDisplayIcon={app}\omicchain.ico
LicenseFile={#Root}\LICENSE.txt
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#Bundle}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\OmicChain"; Filename: "{app}\OmicChain.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\omicchain.ico"
Name: "{autodesktop}\OmicChain"; Filename: "{app}\OmicChain.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\omicchain.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\OmicChain.cmd"; Description: "Start OmicChain"; Flags: postinstall nowait skipifsilent shellexec

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Messages]
FinishedLabel=OmicChain is installed. Your analyses will be stored in the folder OmicChain of your user folder, which is kept if you uninstall OmicChain.
