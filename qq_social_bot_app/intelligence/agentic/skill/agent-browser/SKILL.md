---
name: agent-browser
description: Browser automation CLI for AI agents. Use when the user needs to interact with websites, including navigating pages, filling forms, clicking buttons, taking screenshots, extracting data, testing web apps, or automating any browser task. Triggers include requests to "open a website", "fill out a form", "click a button", "take a screenshot", "scrape data from a page", "test this web app", "login to a site", "automate browser actions", or any task requiring programmatic web interaction.
allowed-tools: Bash(npx agent-browser:*), Bash(agent-browser:*)
---

# 使用 agent-browser 进行浏览器自动化

## 核心工作流程

每个浏览器自动化都遵循此模式：

1. **导航**：`agent-browser open <url>`
2. **快照**：`agent-browser snapshot -i`（获取元素引用，如 `@e1`、`@e2`）
3. **交互**：使用引用进行点击、填写、选择
4. **重新快照**：导航或 DOM 变更后，获取新的引用

```bash
agent-browser open https://example.com/form
agent-browser snapshot -i
# 输出：@e1 [input type="email"], @e2 [input type="password"], @e3 [button] "Submit"

agent-browser fill @e1 "user@example.com"
agent-browser fill @e2 "password123"
agent-browser click @e3
agent-browser wait --load networkidle
agent-browser snapshot -i  # 检查结果
```

## 命令链式调用

命令可以在单次 shell 调用中使用 `&&` 链式执行。浏览器通过后台守护进程保持运行，因此链式调用是安全且高效的。

```bash
# 链式执行打开 + 等待 + 快照
agent-browser open https://example.com && agent-browser wait --load networkidle && agent-browser snapshot -i

# 链式执行多个交互
agent-browser fill @e1 "user@example.com" && agent-browser fill @e2 "password123" && agent-browser click @e3

# 导航并截图
agent-browser open https://example.com && agent-browser wait --load networkidle && agent-browser screenshot page.png
```

**何时使用链式调用：** 当你不需要读取中间命令的输出即可继续时使用 `&&`（例如，打开 + 等待 + 截图）。当你需要先解析输出时（例如，快照获取引用，然后使用这些引用进行交互），则单独运行命令。

## 基本命令

```bash
# 导航
agent-browser open <url>              # 导航到 URL（别名：goto, navigate）
agent-browser close                   # 关闭浏览器

# 快照
agent-browser snapshot -i             # 交互式元素及引用（推荐）
agent-browser snapshot -i -C          # 包含光标可交互元素（带 onclick 的 div、cursor:pointer 等）
agent-browser snapshot -s "#selector" # 限定到 CSS 选择器范围

# 交互（使用快照中的 @引用）
agent-browser click @e1               # 点击元素
agent-browser click @e1 --new-tab     # 点击并在新标签页中打开
agent-browser fill @e2 "text"         # 清空并输入文本
agent-browser type @e2 "text"         # 不清空直接输入
agent-browser select @e1 "option"     # 选择下拉选项
agent-browser check @e1               # 勾选复选框
agent-browser press Enter             # 按键
agent-browser keyboard type "text"    # 在当前焦点位置输入（无选择器）
agent-browser keyboard inserttext "text"  # 插入文本，不触发键盘事件
agent-browser scroll down 500         # 向下滚动页面
agent-browser scroll down 500 --selector "div.content"  # 在指定容器内滚动

# 获取信息
agent-browser get text @e1            # 获取元素文本
agent-browser get url                 # 获取当前 URL
agent-browser get title               # 获取页面标题

# 等待
agent-browser wait @e1                # 等待元素出现
agent-browser wait --load networkidle # 等待网络空闲
agent-browser wait --url "**/page"    # 等待 URL 匹配模式
agent-browser wait 2000               # 等待指定毫秒数

# 下载
agent-browser download @e1 ./file.pdf          # 点击元素触发下载
agent-browser wait --download ./output.zip     # 等待任意下载完成
agent-browser --download-path ./downloads open <url>  # 设置默认下载目录

# 截图
agent-browser screenshot              # 截图到临时目录
agent-browser screenshot --full       # 全页面截图
agent-browser screenshot --annotate   # 带编号元素标签的标注截图
agent-browser pdf output.pdf          # 保存为 PDF

# 差异对比（比较页面状态）
agent-browser diff snapshot                          # 比较当前与上次快照
agent-browser diff snapshot --baseline before.txt    # 比较当前与保存的文件
agent-browser diff screenshot --baseline before.png  # 视觉像素差异对比
agent-browser diff url <url1> <url2>                 # 比较两个页面
agent-browser diff url <url1> <url2> --wait-until networkidle  # 自定义等待策略
agent-browser diff url <url1> <url2> --selector "#main"  # 限定到指定元素
```

## 常见模式

### 表单提交

```bash
agent-browser open https://example.com/signup
agent-browser snapshot -i
agent-browser fill @e1 "Jane Doe"
agent-browser fill @e2 "jane@example.com"
agent-browser select @e3 "California"
agent-browser check @e4
agent-browser click @e5
agent-browser wait --load networkidle
```

### 使用凭据保险库认证（推荐）

```bash
# 一次性保存凭据（使用 AGENT_BROWSER_ENCRYPTION_KEY 加密）
# 推荐：通过 stdin 管道传递密码以避免 shell 历史记录泄露
echo "pass" | agent-browser auth save github --url https://github.com/login --username user --password-stdin

# 使用保存的配置文件登录（LLM 永远看不到密码）
agent-browser auth login github

# 列出/查看/删除配置文件
agent-browser auth list
agent-browser auth show github
agent-browser auth delete github
```

### 使用状态持久化认证

```bash
# 登录一次并保存状态
agent-browser open https://app.example.com/login
agent-browser snapshot -i
agent-browser fill @e1 "$USERNAME"
agent-browser fill @e2 "$PASSWORD"
agent-browser click @e3
agent-browser wait --url "**/dashboard"
agent-browser state save auth.json

# 在后续会话中复用
agent-browser state load auth.json
agent-browser open https://app.example.com/dashboard
```

### 会话持久化

```bash
# 跨浏览器重启自动保存/恢复 cookies 和 localStorage
agent-browser --session-name myapp open https://app.example.com/login
# ... 登录流程 ...
agent-browser close  # 状态自动保存到 ~/.agent-browser/sessions/

# 下次启动时，状态自动加载
agent-browser --session-name myapp open https://app.example.com/dashboard

# 加密静态状态
export AGENT_BROWSER_ENCRYPTION_KEY=$(openssl rand -hex 32)
agent-browser --session-name secure open https://app.example.com

# 管理已保存的状态
agent-browser state list
agent-browser state show myapp-default.json
agent-browser state clear myapp
agent-browser state clean --older-than 7
```

### 数据提取

```bash
agent-browser open https://example.com/products
agent-browser snapshot -i
agent-browser get text @e5           # 获取指定元素文本
agent-browser get text body > page.txt  # 获取所有页面文本

# JSON 格式输出便于解析
agent-browser snapshot -i --json
agent-browser get text @e1 --json
```

### 并行会话

```bash
agent-browser --session site1 open https://site-a.com
agent-browser --session site2 open https://site-b.com

agent-browser --session site1 snapshot -i
agent-browser --session site2 snapshot -i

agent-browser session list
```

### 连接到已运行的 Chrome

```bash
# 自动发现启用了远程调试的 Chrome 实例
agent-browser --auto-connect open https://example.com
agent-browser --auto-connect snapshot

# 或指定 CDP 端口
agent-browser --cdp 9222 snapshot
```

### 颜色方案（深色模式）

```bash
# 通过标志持久化深色模式（适用于所有页面和新标签页）
agent-browser --color-scheme dark open https://example.com

# 或通过环境变量
AGENT_BROWSER_COLOR_SCHEME=dark agent-browser open https://example.com

# 或在会话期间设置（对后续命令持续生效）
agent-browser set media dark
```

### 可视化浏览器（调试用）

```bash
agent-browser --headed open https://example.com
agent-browser highlight @e1          # 高亮元素
agent-browser record start demo.webm # 录制会话
agent-browser profiler start         # 启动 Chrome DevTools 性能分析
agent-browser profiler stop trace.json # 停止并保存分析结果（路径可选）
```

使用 `AGENT_BROWSER_HEADED=1` 通过环境变量启用有头模式。浏览器扩展在有头和无头模式下均可使用。

### 本地文件（PDF、HTML）

```bash
# 使用 file:// URL 打开本地文件
agent-browser --allow-file-access open file:///path/to/document.pdf
agent-browser --allow-file-access open file:///path/to/page.html
agent-browser screenshot output.png
```

### iOS 模拟器（移动端 Safari）

```bash
# 列出可用的 iOS 模拟器
agent-browser device list

# 在指定设备上启动 Safari
agent-browser -p ios --device "iPhone 16 Pro" open https://example.com

# 与桌面端相同的工作流程——快照、交互、重新快照
agent-browser -p ios snapshot -i
agent-browser -p ios tap @e1          # 点击（click 的别名）
agent-browser -p ios fill @e2 "text"
agent-browser -p ios swipe up         # 移动端特有手势

# 截图
agent-browser -p ios screenshot mobile.png

# 关闭会话（关闭模拟器）
agent-browser -p ios close
```

**前提条件：** macOS 上安装 Xcode，以及 Appium（`npm install -g appium && appium driver install xcuitest`）

**真机：** 如果预先配置好，可在物理 iOS 设备上使用。使用 `--device "<UDID>"`，UDID 可通过 `xcrun xctrace list devices` 获取。

## 安全性

所有安全功能均为可选启用。默认情况下，agent-browser 不对导航、操作或输出施加任何限制。

### 内容边界（推荐用于 AI 代理）

启用 `--content-boundaries` 为页面来源的输出添加标记，帮助 LLM 区分工具输出和不受信任的页面内容：

```bash
export AGENT_BROWSER_CONTENT_BOUNDARIES=1
agent-browser snapshot
# 输出：
# --- AGENT_BROWSER_PAGE_CONTENT nonce=<hex> origin=https://example.com ---
# [无障碍树]
# --- END_AGENT_BROWSER_PAGE_CONTENT nonce=<hex> ---
```

### 域名白名单

将导航限制在受信任的域名。`*.example.com` 等通配符也匹配裸域名 `example.com`。子资源请求、WebSocket 和 EventSource 连接到非白名单域名也会被阻止。请包含目标页面依赖的 CDN 域名：

```bash
export AGENT_BROWSER_ALLOWED_DOMAINS="example.com,*.example.com"
agent-browser open https://example.com        # 允许
agent-browser open https://malicious.com       # 阻止
```

### 操作策略

使用策略文件控制破坏性操作：

```bash
export AGENT_BROWSER_ACTION_POLICY=./policy.json
```

示例 `policy.json`：
```json
{"default": "deny", "allow": ["navigate", "snapshot", "click", "scroll", "wait", "get"]}
```

凭据保险库操作（`auth login` 等）绕过操作策略，但域名白名单仍然适用。

### 输出限制

防止大型页面导致上下文溢出：

```bash
export AGENT_BROWSER_MAX_OUTPUT=50000
```

## 差异对比（验证变更）

在执行操作后使用 `diff snapshot` 来验证操作是否产生了预期效果。此命令将当前无障碍树与会话中上次快照进行比较。

```bash
# 典型工作流程：快照 -> 操作 -> 差异对比
agent-browser snapshot -i          # 获取基准快照
agent-browser click @e2            # 执行操作
agent-browser diff snapshot        # 查看变更内容（自动与上次快照比较）
```

用于视觉回归测试或监控：

```bash
# 保存基准截图，稍后进行比较
agent-browser screenshot baseline.png
# ... 经过一段时间或进行了更改 ...
agent-browser diff screenshot --baseline baseline.png

# 比较预发布环境与生产环境
agent-browser diff url https://staging.example.com https://prod.example.com --screenshot
```

`diff snapshot` 输出使用 `+` 表示新增，`-` 表示删除，类似于 git diff。`diff screenshot` 生成差异图像，变更像素以红色高亮显示，并附带不匹配百分比。

## 超时和慢速页面

本地浏览器的默认 Playwright 超时为 25 秒。可通过 `AGENT_BROWSER_DEFAULT_TIMEOUT` 环境变量覆盖（值为毫秒）。对于慢速网站或大型页面，使用显式等待而非依赖默认超时：

```bash
# 等待网络活动稳定（最适合慢速页面）
agent-browser wait --load networkidle

# 等待特定元素出现
agent-browser wait "#content"
agent-browser wait @e1

# 等待特定 URL 模式（重定向后很有用）
agent-browser wait --url "**/dashboard"

# 等待 JavaScript 条件满足
agent-browser wait --fn "document.readyState === 'complete'"

# 等待固定时长（毫秒），作为最后手段
agent-browser wait 5000
```

处理持续慢速的网站时，在 `open` 后使用 `wait --load networkidle` 确保页面完全加载后再进行快照。如果某个特定元素渲染较慢，直接使用 `wait <selector>` 或 `wait @ref` 等待它。

## 会话管理和清理

同时运行多个代理或自动化时，始终使用命名会话以避免冲突：

```bash
# 每个代理拥有独立的隔离会话
agent-browser --session agent1 open site-a.com
agent-browser --session agent2 open site-b.com

# 检查活跃会话
agent-browser session list
```

完成后务必关闭浏览器会话以避免进程泄露：

```bash
agent-browser close                    # 关闭默认会话
agent-browser --session agent1 close   # 关闭指定会话
```

如果之前的会话未正常关闭，守护进程可能仍在运行。使用 `agent-browser close` 在开始新工作前清理它。

## 引用生命周期（重要）

引用（`@e1`、`@e2` 等）在页面变更时会失效。以下情况后必须重新快照：

- 点击会导航的链接或按钮
- 表单提交
- 动态内容加载（下拉菜单、模态框）

```bash
agent-browser click @e5              # 导航到新页面
agent-browser snapshot -i            # 必须重新快照
agent-browser click @e1              # 使用新引用
```

## 标注截图（视觉模式）

使用 `--annotate` 截图时会在交互元素上叠加编号标签。每个标签 `[N]` 对应引用 `@eN`。此操作同时缓存引用，因此无需单独快照即可直接与元素交互。

```bash
agent-browser screenshot --annotate
# 输出包含图片路径和图例：
#   [1] @e1 button "Submit"
#   [2] @e2 link "Home"
#   [3] @e3 textbox "Email"
agent-browser click @e2              # 使用标注截图中的引用点击
```

在以下情况使用标注截图：
- 页面有无标签的图标按钮或纯视觉元素
- 需要验证视觉布局或样式
- 存在 Canvas 或图表元素（文本快照看不到）
- 需要对元素位置进行空间推理

## 语义定位器（引用的替代方案）

当引用不可用或不可靠时，使用语义定位器：

```bash
agent-browser find text "Sign In" click
agent-browser find label "Email" fill "user@test.com"
agent-browser find role button click --name "Submit"
agent-browser find placeholder "Search" type "query"
agent-browser find testid "submit-btn" click
```

## JavaScript 执行（eval）

使用 `eval` 在浏览器上下文中运行 JavaScript。**Shell 引号可能会破坏复杂表达式**——使用 `--stdin` 或 `-b` 来避免问题。

```bash
# 简单表达式可以使用普通引号
agent-browser eval 'document.title'
agent-browser eval 'document.querySelectorAll("img").length'

# 复杂 JS：使用 --stdin 配合 heredoc（推荐）
agent-browser eval --stdin <<'EVALEOF'
JSON.stringify(
  Array.from(document.querySelectorAll("img"))
    .filter(i => !i.alt)
    .map(i => ({ src: i.src.split("/").pop(), width: i.width }))
)
EVALEOF

# 替代方案：base64 编码（完全避免 shell 转义问题）
agent-browser eval -b "$(echo -n 'Array.from(document.querySelectorAll("a")).map(a => a.href)' | base64)"
```

**为什么这很重要：** 当 shell 处理你的命令时，内部双引号、`!` 字符（历史展开）、反引号和 `$()` 都可能在 JavaScript 到达 agent-browser 之前将其破坏。`--stdin` 和 `-b` 标志完全绕过 shell 解释。

**经验法则：**
- 单行，无嵌套引号 -> 使用单引号 `eval 'expression'` 即可
- 嵌套引号、箭头函数、模板字面量或多行 -> 使用 `eval --stdin <<'EVALEOF'`
- 程序化/生成的脚本 -> 使用 `eval -b` 配合 base64

## 配置文件

在项目根目录创建 `agent-browser.json` 用于持久化设置：

```json
{
  "headed": true,
  "proxy": "http://localhost:8080",
  "profile": "./browser-data"
}
```

优先级（从低到高）：`~/.agent-browser/config.json` < `./agent-browser.json` < 环境变量 < CLI 标志。使用 `--config <path>` 或 `AGENT_BROWSER_CONFIG` 环境变量指定自定义配置文件（文件缺失/无效时报错退出）。所有 CLI 选项映射为驼峰命名键（例如 `--executable-path` -> `"executablePath"`）。布尔标志接受 `true`/`false` 值（例如 `--headed false` 覆盖配置）。用户和项目配置中的扩展会合并而非替换。

## 深入文档

| 参考文档 | 使用场景 |
|-----------|-------------|
| [references/commands.md](references/commands.md) | 包含所有选项的完整命令参考 |
| [references/snapshot-refs.md](references/snapshot-refs.md) | 引用生命周期、失效规则、故障排除 |
| [references/session-management.md](references/session-management.md) | 并行会话、状态持久化、并发抓取 |
| [references/authentication.md](references/authentication.md) | 登录流程、OAuth、2FA 处理、状态复用 |
| [references/video-recording.md](references/video-recording.md) | 用于调试和文档的录制工作流程 |
| [references/profiling.md](references/profiling.md) | Chrome DevTools 性能分析 |
| [references/proxy-support.md](references/proxy-support.md) | 代理配置、地理测试、轮转代理 |

## 实验性功能：原生模式

agent-browser 有一个实验性的原生 Rust 守护进程，通过 CDP 直接与 Chrome 通信，完全绕过 Node.js 和 Playwright。此功能为可选启用，尚不建议用于生产环境。

```bash
# 通过标志启用
agent-browser --native open example.com

# 通过环境变量启用（避免每次传递 --native）
export AGENT_BROWSER_NATIVE=1
agent-browser open example.com
```

原生守护进程支持 Chromium 和 Safari（通过 WebDriver）。Firefox 和 WebKit 尚不支持。所有核心命令（navigate、snapshot、click、fill、screenshot、cookies、storage、tabs、eval 等）在原生模式下的工作方式完全相同。在同一会话中切换原生模式和默认模式前，请先使用 `agent-browser close`。

## 即用模板

| 模板 | 说明 |
|----------|-------------|
| [templates/form-automation.sh](templates/form-automation.sh) | 带验证的表单填写 |
| [templates/authenticated-session.sh](templates/authenticated-session.sh) | 一次登录，复用状态 |
| [templates/capture-workflow.sh](templates/capture-workflow.sh) | 带截图的内容提取 |

```bash
./templates/form-automation.sh https://example.com/form
./templates/authenticated-session.sh https://app.example.com/login
./templates/capture-workflow.sh https://example.com ./output
```
