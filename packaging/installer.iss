; Inno Setup 脚本：把 dist/WorkingorFishing/ 打成安装版 exe。
; 编译前先跑：pyinstaller packaging/onedir.spec
; 然后用 Inno Setup 6 的 ISCC.exe 编译本脚本。
;
; 可通过命令行覆盖版本号：ISCC /DMyAppVersion=0.2.0 installer.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#define MyAppName "WorkingorFishing"
#define MyAppPublisher "WorkingorFishing"
#define MyAppExeName "WorkingorFishing.exe"

[Setup]
AppId={{B8C2A4E0-3F7B-4D1A-9C5E-7B4E8D2F1A03}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename={#MyAppName}-Setup-{#MyAppVersion}
#if FileExists("..\assets\icon.ico")
SetupIconFile=..\assets\icon.ico
#endif
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"
Name: "autostart"; Description: "开机时自动启动 {#MyAppName}"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
; 把 onedir 打包出来的整个目录全部装入安装目录
Source: "..\dist\WorkingorFishing\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "WorkingorFishing"; ValueData: """{app}\{#MyAppExeName}"""; \
    Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\{#MyAppName}"; \
    Components: ; Tasks: ; Languages:
; 注：上面这条会删除用户数据。如要保留请删除本节。
