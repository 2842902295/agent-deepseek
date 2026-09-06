; Agent DeepSeek 桌面单机版安装包（Inno Setup 6）
; 由 pack_desktop.py 调用 ISCC.exe 编译；pack_desktop.py 会先按用户输入的应用名
; 渲染出 installer.generated.iss（替换 MyAppName/SourceDir/DefaultDirName/OutputBaseFilename），
; 本文件是可独立编译的模板（默认应用名）。源目录为其暂存好的完整应用树。
; 本文件必须保存为 UTF-8（带 BOM），否则 ISCC 按 ANSI 解析中文会乱码。
#define MyAppName "agent-deepseek桌面版"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "CESI"
#define SourceDir "..\..\desktop_dist\agent-deepseek桌面版"

[Setup]
AppId={{B7E2C1A4-3F5D-4A6B-9C8E-1D2F3A4B5C6D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppComments=Agent DeepSeek 桌面单机版：本地运行，数据库与大模型沿用远程服务
DefaultDirName={localappdata}\Programs\agent-deepseek桌面版
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=..\..\desktop_dist
OutputBaseFilename=agent-deepseek桌面版安装包
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
DisableProgramGroupPage=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
; 简体中文（语言文件随仓库分发，不依赖打包机 Inno Setup 安装内容）
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 单图标生命周期（对齐普通应用）：快捷方式指向 pythonw.exe + launcher.pyw，无控制台黑框。
; launcher.pyw 秒开 Edge 应用窗口（先本地 loading 页，就绪自动跳转）；关窗即停后端与 Redis，
; 因此不再提供单独的"停止"快捷方式（stop.bat/stop.pyw 仅作为调试/应急手段留在目录里）。
; （图标参数是 IconFilename；start.bat 仅保留为带控制台的调试入口。）
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\favicon.ico"
Name: "{group}\{#MyAppName}"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\favicon.ico"

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; Description: "启动 {#MyAppName}"; Flags: postinstall nowait skipifsilent
