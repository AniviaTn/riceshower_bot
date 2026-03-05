#!/bin/bash
# 模板：内容捕获工作流程
# 用途：从网页提取内容（文本、截图、PDF）
# 使用方法：./capture-workflow.sh <url> [输出目录]
#
# 输出：
#   - page-full.png：全页面截图
#   - page-structure.txt：带引用的页面元素结构
#   - page-text.txt：所有文本内容
#   - page.pdf：PDF 版本
#
# 可选：加载认证状态用于受保护的页面

set -euo pipefail

TARGET_URL="${1:?使用方法: $0 <url> [输出目录]}"
OUTPUT_DIR="${2:-.}"

echo "正在捕获：$TARGET_URL"
mkdir -p "$OUTPUT_DIR"

# 可选：加载认证状态
# if [[ -f "./auth-state.json" ]]; then
#     echo "正在加载认证状态..."
#     agent-browser state load "./auth-state.json"
# fi

# 导航到目标页面
agent-browser open "$TARGET_URL"
agent-browser wait --load networkidle

# 获取元数据
TITLE=$(agent-browser get title)
URL=$(agent-browser get url)
echo "标题：$TITLE"
echo "URL：$URL"

# 捕获全页面截图
agent-browser screenshot --full "$OUTPUT_DIR/page-full.png"
echo "已保存：$OUTPUT_DIR/page-full.png"

# 获取带引用的页面结构
agent-browser snapshot -i > "$OUTPUT_DIR/page-structure.txt"
echo "已保存：$OUTPUT_DIR/page-structure.txt"

# 提取所有文本内容
agent-browser get text body > "$OUTPUT_DIR/page-text.txt"
echo "已保存：$OUTPUT_DIR/page-text.txt"

# 保存为 PDF
agent-browser pdf "$OUTPUT_DIR/page.pdf"
echo "已保存：$OUTPUT_DIR/page.pdf"

# 可选：使用结构中的引用提取特定元素
# agent-browser get text @e5 > "$OUTPUT_DIR/main-content.txt"

# 可选：处理无限滚动页面
# for i in {1..5}; do
#     agent-browser scroll down 1000
#     agent-browser wait 1000
# done
# agent-browser screenshot --full "$OUTPUT_DIR/page-scrolled.png"

# 清理
agent-browser close

echo ""
echo "捕获完成："
ls -la "$OUTPUT_DIR"
