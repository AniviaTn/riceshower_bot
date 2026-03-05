# 视频录制

将浏览器自动化过程录制为视频，用于调试、文档记录或验证。

**相关内容**：[commands.md](commands.md) 了解完整命令参考，[SKILL.md](../SKILL.md) 了解快速入门。

## 目录

- [基本录制](#基本录制)
- [录制命令](#录制命令)
- [使用场景](#使用场景)
- [最佳实践](#最佳实践)
- [输出格式](#输出格式)
- [限制](#限制)

## 基本录制

```bash
# 开始录制
agent-browser record start ./demo.webm

# 执行操作
agent-browser open https://example.com
agent-browser snapshot -i
agent-browser click @e1
agent-browser fill @e2 "test input"

# 停止并保存
agent-browser record stop
```

## 录制命令

```bash
# 开始录制到文件
agent-browser record start ./output.webm

# 停止当前录制
agent-browser record stop

# 重新开始录制到新文件（停止当前 + 开始新录制）
agent-browser record restart ./take2.webm
```

## 使用场景

### 调试失败的自动化

```bash
#!/bin/bash
# 录制自动化过程以便调试

agent-browser record start ./debug-$(date +%Y%m%d-%H%M%S).webm

# 运行你的自动化
agent-browser open https://app.example.com
agent-browser snapshot -i
agent-browser click @e1 || {
    echo "点击失败——请查看录制"
    agent-browser record stop
    exit 1
}

agent-browser record stop
```

### 生成文档

```bash
#!/bin/bash
# 录制工作流程用于文档

agent-browser record start ./docs/how-to-login.webm

agent-browser open https://app.example.com/login
agent-browser wait 1000  # 暂停以便观看

agent-browser snapshot -i
agent-browser fill @e1 "demo@example.com"
agent-browser wait 500

agent-browser fill @e2 "password"
agent-browser wait 500

agent-browser click @e3
agent-browser wait --load networkidle
agent-browser wait 1000  # 展示结果

agent-browser record stop
```

### CI/CD 测试证据

```bash
#!/bin/bash
# 录制端到端测试运行作为 CI 产物

TEST_NAME="${1:-e2e-test}"
RECORDING_DIR="./test-recordings"
mkdir -p "$RECORDING_DIR"

agent-browser record start "$RECORDING_DIR/$TEST_NAME-$(date +%s).webm"

# 运行测试
if run_e2e_test; then
    echo "测试通过"
else
    echo "测试失败——录制已保存"
fi

agent-browser record stop
```

## 最佳实践

### 1. 添加暂停以提高清晰度

```bash
# 放慢速度便于人工观看
agent-browser click @e1
agent-browser wait 500  # 让观看者看到结果
```

### 2. 使用描述性文件名

```bash
# 在文件名中包含上下文
agent-browser record start ./recordings/login-flow-2024-01-15.webm
agent-browser record start ./recordings/checkout-test-run-42.webm
```

### 3. 在错误情况下处理录制

```bash
#!/bin/bash
set -e

cleanup() {
    agent-browser record stop 2>/dev/null || true
    agent-browser close 2>/dev/null || true
}
trap cleanup EXIT

agent-browser record start ./automation.webm
# ... 自动化步骤 ...
```

### 4. 结合截图使用

```bash
# 录制视频的同时截取关键帧
agent-browser record start ./flow.webm

agent-browser open https://example.com
agent-browser screenshot ./screenshots/step1-homepage.png

agent-browser click @e1
agent-browser screenshot ./screenshots/step2-after-click.png

agent-browser record stop
```

## 输出格式

- 默认格式：WebM（VP8/VP9 编解码器）
- 兼容所有现代浏览器和视频播放器
- 压缩但高质量

## 限制

- 录制会对自动化产生轻微的性能开销
- 较长的录制可能消耗大量磁盘空间
- 某些无头环境可能存在编解码器限制
