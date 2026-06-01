# WorkingorFishing

[![Build & Release](https://github.com/vvangpc/workingorfishing/actions/workflows/build-release.yml/badge.svg)](https://github.com/vvangpc/workingorfishing/actions/workflows/build-release.yml)
[![Release](https://img.shields.io/github/v/release/vvangpc/workingorfishing?label=release)](https://github.com/vvangpc/workingorfishing/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/vvangpc/workingorfishing/total)](https://github.com/vvangpc/workingorfishing/releases)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Windows 桌面活动追踪工具——回答一个问题：

> 我今天在电脑上到底干了多久正事，又摸了多久鱼？

后台常驻、轻量级，定时采样前台窗口（进程名 / 标题 / 浏览器 URL），按你写的规则分类成 **工作 / 摸鱼 / 中立**，沉淀成日 / 周 / 月统计，配桌面悬浮窗实时显示当前状态。

---

## 功能一览

### 数据采集
- **前台窗口轮询**：默认每 10 秒采样一次进程名、窗口标题
- **浏览器 URL 抓取**：通过 UI Automation 读取 Chrome / Edge / Brave / Firefox / Vivaldi / Opera 的地址栏（含坚果云式 a11y 自动唤醒 + 控件缓存）
- **键鼠空闲检测**：超过阈值（默认 5 分钟）自动标 `idle`，不算工作时长
- **定时自动暂停**：配置每天生效的时间段（如午餐 12:00–13:00），到点自动暂停采样、过点自动恢复，避免固定离开时段被错误记录（支持多时段、跨午夜；设置 → 启动与界面 → 自动暂停设置）

### 分类
- **规则引擎**：`process` / `title_regex` / `url_regex` 三种匹配字段，可组合可优先级排序
- **GUI 规则管理**：增删改、启用/禁用、实时测试匹配——无需编辑 YAML
- **AI 兜底**：对接 OpenAI 兼容协议（OpenAI / DeepSeek / 通义千问 / Ollama / OneAPI 等），LLM 给"未匹配规则"的活动生成归类建议
- **自然语言生成规则**：在新建规则对话框里描述需求（如"把 LinkedIn 都归为工作"），AI 自动填好字段
- **批量自动规则**：一键调 AI 处理所有待确定活动

### 可视化
- **概览页**：当前状态卡 + 今日横条形图（工作 / 摸鱼 / 中立 / 空闲）+ 待确定徽章
- **AI 今日点评**：概览底部卡片，依据今日工作 / 摸鱼占比由大模型生成一句点评，每次打开主窗口刷新（后台隐藏时不调用、省 token）；内置幽默 / 严肃 / 毒舌 / 鼓励多种风格，也可自定义提示词（设置 → 工具 → AI 评语）
- **桌面悬浮窗**：
  - 两套主题：**文字**（彩色圆角条 + 时长）/ **图片**（`assets/floating/float_*.png` 状态贴图）
  - 当前状态色 + 当日累计时长
  - 可调宽高 / 透明度 / 字体颜色（白 / 黑 / **自适应**：采样下方桌面像素自动反色）
  - **鼠标穿透**：开启后点击直接落到下方窗口
  - 自由拖拽，位置 / 透明度 / 主题等所有偏好持久化
- **统计窗口**：日 / 周 / 月切换，饼图 + 柱状图 + **三级层级树**（类别 → 进程 → 标题/URL，浏览器 URL 自动聚合到友好域名，比如 YouTube / 哔哩哔哩 / X / GitHub）
- **就地改分类**：统计树右键 / 双击行 → 弹归类对话框 → 自动生成规则 + 回填历史

### 数据管理
- **本地优先**：SQLite 单文件，默认不联网
- **导出 / 导入**：一键打包活动数据 + 规则 + 设置为 zip
- **WebDAV 同步**：原生支持坚果云 / Nextcloud 等，自动创建 `WorkingorFishing/` 子目录
- **清除统计**：一键删全部活动记录（规则和设置保留）

### 系统集成
- **单实例守护**：再次启动只激活已运行实例
- **托盘 + 自启**：常驻系统托盘，开机自启可选
- **几何记忆**：主窗口 / 弹出卡片的大小位置自动保存恢复
- **关窗最小化到托盘**：永不打扰

---

## 下载安装

到 [Releases](https://github.com/vvangpc/workingorfishing/releases/latest) 选最新版本：

| 版本 | 适用场景 |
| --- | --- |
| **`WorkingorFishing-portable.exe`** | 便携版，单文件双击即用，配置和数据写在 exe 同目录，可放 U 盘带走 |
| **`WorkingorFishing-Setup-x.y.z.exe`** | 安装版，标准 Windows 安装流程，数据落 `%APPDATA%\WorkingorFishing\` |

仅支持 **Windows 10 / 11**。

---

## 快速上手

1. **首次启动**：默认规则已经包含 60+ 条常见软件 / 网站；切到浏览器试试，悬浮窗会显示当前状态
2. **配置 AI（可选但推荐）**：
   - 主窗口 → 设置 → AI 判断
   - Base URL：填 OpenAI 兼容端点（`https://api.openai.com/v1` / `https://api.deepseek.com/v1` / 本地 `http://localhost:11434/v1` 等）
   - API Key + 模型名（如 `deepseek-chat`、`gpt-4o-mini`、本地的 `qwen2:7b`）
   - 勾「启用」+ 保存
3. **训练规则**：
   - 概览右上「待确定」徽章里看 AI 自动归类的建议
   - 单条点 工作/摸鱼/中立 接受，或点 **自动规则** 一键全部处理
4. **查统计**：设置 → 工具 → **统计**

---

## Chromium 浏览器 URL 抓取注意事项

Chrome / Edge / Brave 出于性能考虑**默认不构建无障碍树**，UI Automation 因此读不到地址栏。最稳的解决方法：

**给浏览器快捷方式加 `--force-renderer-accessibility` 启动参数**

1. 右键 Edge / Chrome 快捷方式 → 属性 → 「目标」末尾加空格 + `--force-renderer-accessibility`
2. 完整示例：
   ```
   "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --force-renderer-accessibility
   ```
3. 关掉所有浏览器进程，用新快捷方式启动

副作用：浏览器内存占用增加 5–10%，正常使用无感。

验证：地址栏输入 `edge://accessibility/` 看每个 tab 是否 `Complete accessibility has been enabled`。

抓不到 URL 时程序会**自动退回**到窗口标题匹配，所以即使没加 flag 也能跑，只是部分网站会被归到中立。

---

## WebDAV 同步

支持任何 WebDAV 服务（坚果云 / Nextcloud / 自建 nginx-dav 等）。

### 坚果云配置示例

1. 坚果云 → 账户信息 → **第三方应用管理** 创建应用密码
2. 主窗口 → 设置 → WebDAV 同步：
   - 地址：`https://dav.jianguoyun.com/dav/`
   - 用户名：你的注册邮箱
   - 密码：刚生成的应用密码（**不是登录密码**）
3. 点 **测试连接** 应返回成功
4. **推送到云端**：上传到 `WorkingorFishing/` 子目录（程序自动 MKCOL）
5. 另一台机器 **从云端拉取** 即可同步

同步的三个文件：`activity.db`（SQLite 活动数据）、`settings.yaml`（设置）、`rules.yaml`（规则）。

---

## 从源码运行

需要 Python 3.10+ 和 Windows 系统。

```powershell
git clone https://github.com/vvangpc/workingorfishing.git
cd workingorfishing
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

---

## 从源码打包

### 单文件便携版

```powershell
pip install pyinstaller
pyinstaller --noconfirm packaging/onefile.spec
# 产物：dist/WorkingorFishing-portable.exe
```

### 安装版（需要 [Inno Setup 6](https://jrsoftware.org/isinfo.php)）

```powershell
pyinstaller --noconfirm packaging/onedir.spec
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DMyAppVersion=0.4.0 packaging/installer.iss
# 产物：dist/installer/WorkingorFishing-Setup-0.4.0.exe
```

或者打个 tag 让 GitHub Actions 自动构建并发布：

```powershell
git tag v0.4 && git push origin v0.4
```

---

## 数据 / 配置目录

| 场景 | 位置 |
| --- | --- |
| 源码运行 / 便携版（同目录可写） | exe 或仓库同目录的 `config/`、`data/` |
| 安装版（Inno Setup 安装） | `%APPDATA%\WorkingorFishing\` |

主窗口 → 设置里的「数据管理」组提供导出 / 导入 / 清除按钮。

---

## 已知限制

- **仅 Windows**：依赖 pywin32 / winreg / Windows UI Automation
- **浏览器隐私窗口**：UI Automation 在 InPrivate / 无痕窗口下可能拿不到 URL
- **不抓内容**：只记录窗口元数据（进程名 / 标题 / URL），不截图、不抓键入
- **AI 兜底涉及网络**：启用后未命中规则的活动元数据会发给你配置的 LLM 端点；不放心可关闭

---

## 许可证

[MIT](LICENSE)
