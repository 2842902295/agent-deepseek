; CesiFastAdmin 桌面单机版安装包（Inno Setup 6）
; 由 pack_desktop.py 调用 ISCC.exe 编译；源目录为其暂存好的完整应用树。
#define MyAppName "CesiFastAdmin"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "CESI"
#define SourceDir "..\..\desktop_dist\CesiFastAdmin"

[Setup]
AppId={{B7E2C1A4-3F5D-4A6B-9C8E-1D2F3A4B5C6D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=..\..\desktop_dist
OutputBaseFilename=CesiFastAdmin-setup
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
DisableProgramGroupPage=yes
WizardStyle=modern
CloseApplications=yes

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\start.bat"; WorkingDir: "{app}"
Name: "{group}\{#MyAppName}"; Filename: "{app}\start.bat"; WorkingDir: "{app}"
Name: "{group}\停止 {#MyAppName}"; Filename: "{app}\stop.bat"; WorkingDir: "{app}"

[Run]
Filename: "{app}\start.bat"; Description: "启动 {#MyAppName}"; Flags: postinstall nowait skipifsilent
