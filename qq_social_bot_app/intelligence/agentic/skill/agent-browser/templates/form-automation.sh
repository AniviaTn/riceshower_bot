#!/bin/bash
# 模板：表单自动化工作流程
# 用途：填写和提交网页表单并进行验证
# 使用方法：./form-automation.sh <表单URL>
#
# 此模板演示快照-交互-验证模式：
# 1. 导航到表单
# 2. 快照获取元素引用
# 3. 使用引用填写字段
# 4. 提交并验证结果
#
# 自定义：根据你的表单快照输出更新引用（@e1、@e2 等）

set -euo pipefail

FORM_URL="${1:?使用方法: $0 <表单URL>}"

echo "表单自动化：$FORM_URL"

# 步骤 1：导航到表单
agent-browser open "$FORM_URL"
agent-browser wait --load networkidle

# 步骤 2：快照以发现表单元素
echo ""
echo "表单结构："
agent-browser snapshot -i

# 步骤 3：填写表单字段（根据快照输出自定义这些引用）
#
# 常见字段类型：
#   agent-browser fill @e1 "John Doe"           # 文本输入框
#   agent-browser fill @e2 "user@example.com"   # 邮箱输入框
#   agent-browser fill @e3 "SecureP@ss123"      # 密码输入框
#   agent-browser select @e4 "Option Value"     # 下拉菜单
#   agent-browser check @e5                     # 复选框
#   agent-browser click @e6                     # 单选按钮
#   agent-browser fill @e7 "Multi-line text"   # 文本域
#   agent-browser upload @e8 /path/to/file.pdf # 文件上传
#
# 取消注释并修改：
# agent-browser fill @e1 "Test User"
# agent-browser fill @e2 "test@example.com"
# agent-browser click @e3  # 提交按钮

# 步骤 4：等待提交完成
# agent-browser wait --load networkidle
# agent-browser wait --url "**/success"  # 或等待重定向

# 步骤 5：验证结果
echo ""
echo "结果："
agent-browser get url
agent-browser snapshot -i

# 可选：截图留存证据
agent-browser screenshot /tmp/form-result.png
echo "截图已保存：/tmp/form-result.png"

# 清理
agent-browser close
echo "完成"
