# WorkingorFishing

[![Build & Release](https://github.com/vvangpc/workingorfishing/actions/workflows/build-release.yml/badge.svg)](https://github.com/vvangpc/workingorfishing/actions/workflows/build-release.yml)
[![Release](https://img.shields.io/github/v/release/vvangpc/workingorfishing?label=release)](https://github.com/vvangpc/workingorfishing/releases/latest)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Windows 桌面活动监控工具：定时采样当前前台窗口的进程名、标题、浏览器活动标签 URL，按规则把活动分类为 **工作 / 摸鱼 / 中立 / 未知 / 空闲**，长期沉淀为日/周/月统计，回答一个问题——

> 我今天在电脑上到底干了多久正事？

## 下载

从 [Releases](https://github.com/vvangpc/workingorfishing/releases) 选最新版：

- **`WorkingorFishing-portable.exe`** — 单文件便携版，双击即用，配置和数据落在同目录
- **`WorkingorFishing-Setup-x.y.z.exe`** — 安装版，数据落 `%APPDATA%\WorkingorFishing\`

## 功能

- **首页主窗口**（两个顶层 Tab）：
  - **概览**：当前状态、今日饼图、待确认（未匹配规则）列表。底部带【自动规则】按钮，一键让 AI 对所有未确认活动批量判断并生成规则
  - **设置**：内含三个子 Tab
    - **通用**：采样间隔 / 空闲阈值 / 开机自启 / 悬浮窗 / 数据目录
    - **规则**：表格 GUI 增删改规则，无需手动改 YAML；支持启用/禁用、优先级、测试匹配
    - **AI 判断**：对接任意 OpenAI 兼容协议（OpenAI / DeepSeek / 通义千问 / Ollama / OneAPI 等）让 LLM 给"未知"活动归类，结果出现在概览待确认列表，由你一键确认入库
- 系统托盘常驻 + 状态色图标（绿=工作 / 红=摸鱼 / 蓝=中立 / 黄=未知 / 灰=空闲）
- 桌面置顶悬浮窗，可拖拽、半透明
- **浏览器 URL 识别**：Chrome / Edge / Brave / Vivaldi / Opera / Firefox
- 空闲检测（键鼠空闲超过阈值自动归 idle，不算工作时长）
- 统计窗口：日 / 周 / 月，饼图 + 柱状图 + Top 进程/URL 表
  - **行右键 / 双击** 可以直接把某个进程或 URL 改类别 → 生成规则 + 回填历史
- 数据本地化：SQLite，**不上传任何数据**（除非你启用了 AI 判断，那部分活动元数据会发给你配置的 LLM 端点）

## 开发模式运行

需要 Python 3.10+。

```powershell
pip install -r requirements.txt
python run.py
```

首次运行：
- `config/rules.yaml` 自动从 `src/default_rules.yaml` 复制
- `config/settings.yaml` 自动创建（采样间隔默认 10 秒，空闲阈值默认 5 分钟，AI 判断默认关闭）
- `data/activity.db` 在数据目录下创建

> 旧版（`categories: { work: [...] }`）格式的 `rules.yaml` 会**自动迁移**到新版（`rules: [...]` 扁平列表 + uuid + 优先级），无需手动操作。

## 规则与分类

从 v0.2 起，**规则不再用文本编辑器维护**，全部在主窗口的「规则」Tab 增删改：

| 字段 | 说明 |
| --- | --- |
| 启用 | 取消勾选可临时禁用规则 |
| 优先级 | 数字小的先匹配 |
| 类别 | work / fishing / neutral |
| 进程 | 进程名精确匹配（大小写不敏感） |
| 标题正则 | Python `re`，建议加 `(?i)` 忽略大小写 |
| URL 正则 | 浏览器活动标签 URL；非浏览器或抓不到 URL 时不参与匹配 |
| 备注 | 自由文本，方便日后维护 |

匹配规则：按优先级遍历启用规则，**所有字段都满足才算命中**。任何规则都不命中时归 `unknown`，等你在概览 Tab 或统计窗口里手动归类，或让 AI 判断给建议。

## AI 判断分类

打开主窗口 → AI Tab：
- Base URL：默认 `https://api.openai.com/v1`，可改成 DeepSeek、千问、Ollama 本地端点等
- API Key、Model（自由填，比如 `gpt-4o-mini` / `deepseek-chat` / `qwen-plus`）
- 启用后，所有未命中规则的活动会进入后台队列，定时调 LLM 让其判断「工作 / 摸鱼 / 中立」并给出一条建议规则
- 结果显示在概览 Tab 的「待确认」列表，你点击「工作 / 摸鱼 / 中立」按钮，会弹归类对话框（默认勾选「保存为规则 + 回填历史 unknown 记录」），确认后规则入库、历史数据同步更新

关掉 AI 也能正常工作——`unknown` 会保留，等你在概览或统计里手动归类。

## 数据目录在哪

| 场景 | `config/` 与 `data/` 位置 |
| --- | --- |
| 开发模式（`python run.py`） | 仓库根目录 |
| 单文件便携版（onefile exe） | exe 所在目录（同目录可写时） |
| 安装版（Inno Setup 安装） | `%APPDATA%\WorkingorFishing\` |

主窗口 → 设置 Tab 有「打开配置目录 / 打开数据目录」按钮。

## 打包

需要：

```powershell
pip install pyinstaller
```

### 单文件便携版

```powershell
.\packaging\build_onefile.bat
```

产物：`dist\WorkingorFishing-portable.exe`，双击即用，配置和数据写在 exe 同目录，可放 U 盘带走。

### 安装版

需要先安装 [Inno Setup 6](https://jrsoftware.org/isinfo.php)。

```powershell
.\packaging\build_installer.bat
```

产物：`dist\installer\WorkingorFishing-Setup-x.y.z.exe`。安装时可勾选「开机自启」，数据放 `%APPDATA%\WorkingorFishing\`。

## 已知限制

- **仅支持 Windows**（依赖 `pywin32` / `winreg` / Windows UI Automation）
- 浏览器无痕模式可能拿不到 URL（UI Automation 在隐私窗口受限）
- UI Automation 调用偶发会阻塞，已加 1.5 秒超时保护
- 不抓键入内容、不抓截图，只记录窗口进程名/标题/URL
- 启用 AI 判断时，未命中规则的活动元数据（进程名、窗口标题、URL）会发送给你配置的 LLM 端点，请自行评估隐私风险

## 验证清单

每次改完核心逻辑可以走一遍：

1. 切到 VS Code、浏览器、记事本，看 `sqlite3 data\activity.db "SELECT process_name, window_title, url, category, rule_id FROM activity_log ORDER BY id DESC LIMIT 10"`
2. 在 Chrome 切到不同标签（GitHub / B 站 / 某个不在规则里的新网站），看 `url` 列是否准确，分类是否合理
3. 主窗口 → 规则 → 新建一条规则，确认下一次采样按新规则归类
4. 主窗口 → 规则 → 取消某条规则的「启用」勾选，确认对应进程被归到 unknown 或下一条匹配的规则
5. 主窗口 → 概览 → 等待若干 unknown 记录出现 → 点击「工作 / 摸鱼 / 中立」→ 确认对话框 → 看历史 unknown 是否回填
6. 主窗口 → AI Tab → 配置 base_url + api_key + model → 点「发送测试请求」拿到一个 JSON 建议
7. 统计窗口 → 在 Top 进程表上某行右键 → 「归类为 工作 / 摸鱼 / 中立…」→ 确认 → 图表与表立刻刷新
8. 把空闲阈值临时改成 30 秒，离开键鼠 40 秒，看新记录 `is_idle=1` 且悬浮窗显示「空闲中」
9. 拖动悬浮窗到屏幕角落，重启程序看位置恢复
10. 托盘双击 / 右键「打开主窗口（首页）」
