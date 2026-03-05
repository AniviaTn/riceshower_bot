# 性能分析

在浏览器自动化期间捕获 Chrome DevTools 性能分析数据，用于性能分析。

**相关内容**：[commands.md](commands.md) 了解完整命令参考，[SKILL.md](../SKILL.md) 了解快速入门。

## 目录

- [基本性能分析](#基本性能分析)
- [分析器命令](#分析器命令)
- [类别](#类别)
- [使用场景](#使用场景)
- [输出格式](#输出格式)
- [查看分析结果](#查看分析结果)
- [限制](#限制)

## 基本性能分析

```bash
# 启动分析
agent-browser profiler start

# 执行操作
agent-browser navigate https://example.com
agent-browser click "#button"
agent-browser wait 1000

# 停止并保存
agent-browser profiler stop ./trace.json
```

## 分析器命令

```bash
# 使用默认类别启动分析
agent-browser profiler start

# 使用自定义追踪类别启动
agent-browser profiler start --categories "devtools.timeline,v8.execute,blink.user_timing"

# 停止分析并保存到文件
agent-browser profiler stop ./trace.json
```

## 类别

`--categories` 标志接受逗号分隔的 Chrome 追踪类别列表。默认类别包括：

- `devtools.timeline` -- 标准 DevTools 性能追踪
- `v8.execute` -- JavaScript 执行耗时
- `blink` -- 渲染器事件
- `blink.user_timing` -- `performance.mark()` / `performance.measure()` 调用
- `latencyInfo` -- 输入到延迟的追踪
- `renderer.scheduler` -- 任务调度和执行
- `toplevel` -- 广谱基础事件

还包含若干 `disabled-by-default-*` 类别，用于详细的时间线、调用栈和 V8 CPU 分析数据。

## 使用场景

### 诊断页面加载缓慢

```bash
agent-browser profiler start
agent-browser navigate https://app.example.com
agent-browser wait --load networkidle
agent-browser profiler stop ./page-load-profile.json
```

### 分析用户交互

```bash
agent-browser navigate https://app.example.com
agent-browser profiler start
agent-browser click "#submit"
agent-browser wait 2000
agent-browser profiler stop ./interaction-profile.json
```

### CI 性能回归检查

```bash
#!/bin/bash
agent-browser profiler start
agent-browser navigate https://app.example.com
agent-browser wait --load networkidle
agent-browser profiler stop "./profiles/build-${BUILD_ID}.json"
```

## 输出格式

输出为 Chrome Trace Event 格式的 JSON 文件：

```json
{
  "traceEvents": [
    { "cat": "devtools.timeline", "name": "RunTask", "ph": "X", "ts": 12345, "dur": 100, ... },
    ...
  ],
  "metadata": {
    "clock-domain": "LINUX_CLOCK_MONOTONIC"
  }
}
```

`metadata.clock-domain` 字段根据主机平台设置（Linux 或 macOS）。在 Windows 上省略。

## 查看分析结果

在以下任意工具中加载输出的 JSON 文件：

- **Chrome DevTools**：性能面板 > 加载分析结果（Ctrl+Shift+I > Performance）
- **Perfetto UI**：https://ui.perfetto.dev/ -- 拖放 JSON 文件
- **Trace Viewer**：在任意 Chromium 浏览器中打开 `chrome://tracing`

## 限制

- 仅适用于基于 Chromium 的浏览器（Chrome、Edge）。不支持 Firefox 或 WebKit。
- 分析活跃期间追踪数据会在内存中累积（上限为 500 万事件）。在关注区域完成后请及时停止分析。
- 停止时的数据收集有 30 秒超时。如果浏览器无响应，停止命令可能会失败。
