# 快照与引用

紧凑的元素引用，大幅减少 AI 代理的上下文消耗。

**相关内容**：[commands.md](commands.md) 了解完整命令参考，[SKILL.md](../SKILL.md) 了解快速入门。

## 目录

- [引用的工作原理](#引用的工作原理)
- [快照命令](#快照命令)
- [使用引用](#使用引用)
- [引用生命周期](#引用生命周期)
- [最佳实践](#最佳实践)
- [引用符号详解](#引用符号详解)
- [故障排除](#故障排除)

## 引用的工作原理

传统方式：
```
完整 DOM/HTML → AI 解析 → CSS 选择器 → 操作（约 3000-5000 个 token）
```

agent-browser 方式：
```
紧凑快照 → 分配 @引用 → 直接交互（约 200-400 个 token）
```

## 快照命令

```bash
# 基本快照（显示页面结构）
agent-browser snapshot

# 交互式快照（-i 标志）- 推荐
agent-browser snapshot -i
```

### 快照输出格式

```
Page: Example Site - Home
URL: https://example.com

@e1 [header]
  @e2 [nav]
    @e3 [a] "Home"
    @e4 [a] "Products"
    @e5 [a] "About"
  @e6 [button] "Sign In"

@e7 [main]
  @e8 [h1] "Welcome"
  @e9 [form]
    @e10 [input type="email"] placeholder="Email"
    @e11 [input type="password"] placeholder="Password"
    @e12 [button type="submit"] "Log In"

@e13 [footer]
  @e14 [a] "Privacy Policy"
```

## 使用引用

获取引用后，直接进行交互：

```bash
# 点击 "Sign In" 按钮
agent-browser click @e6

# 填写邮箱输入框
agent-browser fill @e10 "user@example.com"

# 填写密码
agent-browser fill @e11 "password123"

# 提交表单
agent-browser click @e12
```

## 引用生命周期

**重要**：页面变更时引用会失效！

```bash
# 获取初始快照
agent-browser snapshot -i
# @e1 [button] "Next"

# 点击触发页面变更
agent-browser click @e1

# 必须重新快照以获取新引用！
agent-browser snapshot -i
# @e1 [h1] "Page 2"  ← 现在是不同的元素！
```

## 最佳实践

### 1. 交互前始终先快照

```bash
# 正确做法
agent-browser open https://example.com
agent-browser snapshot -i          # 先获取引用
agent-browser click @e1            # 使用引用

# 错误做法
agent-browser open https://example.com
agent-browser click @e1            # 引用尚不存在！
```

### 2. 导航后重新快照

```bash
agent-browser click @e5            # 导航到新页面
agent-browser snapshot -i          # 获取新引用
agent-browser click @e1            # 使用新引用
```

### 3. 动态变更后重新快照

```bash
agent-browser click @e1            # 打开下拉菜单
agent-browser snapshot -i          # 查看下拉项
agent-browser click @e7            # 选择项目
```

### 4. 快照特定区域

对于复杂页面，快照指定区域：

```bash
# 仅快照表单部分
agent-browser snapshot @e9
```

## 引用符号详解

```
@e1 [tag type="value"] "text content" placeholder="hint"
│    │   │             │               │
│    │   │             │               └─ 附加属性
│    │   │             └─ 可见文本
│    │   └─ 显示的关键属性
│    └─ HTML 标签名
└─ 唯一引用 ID
```

### 常见模式

```
@e1 [button] "Submit"                    # 带文本的按钮
@e2 [input type="email"]                 # 邮箱输入框
@e3 [input type="password"]              # 密码输入框
@e4 [a href="/page"] "Link Text"         # 锚点链接
@e5 [select]                             # 下拉菜单
@e6 [textarea] placeholder="Message"     # 文本域
@e7 [div class="modal"]                  # 容器（相关时显示）
@e8 [img alt="Logo"]                     # 图片
@e9 [checkbox] checked                   # 已勾选的复选框
@e10 [radio] selected                    # 已选中的单选按钮
```

## 故障排除

### "Ref not found" 错误

```bash
# 引用可能已变更——重新快照
agent-browser snapshot -i
```

### 元素在快照中不可见

```bash
# 向下滚动以显示元素
agent-browser scroll down 1000
agent-browser snapshot -i

# 或等待动态内容加载
agent-browser wait 1000
agent-browser snapshot -i
```

### 元素过多

```bash
# 快照指定容器
agent-browser snapshot @e5

# 或使用 get text 仅提取文本内容
agent-browser get text @e5
```
