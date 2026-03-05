#!/bin/bash
# 模板：认证会话工作流程
# 用途：一次登录，保存状态，后续运行复用
# 使用方法：./authenticated-session.sh <登录URL> [状态文件]
#
# 推荐：使用凭据保险库替代此模板：
#   echo "<密码>" | agent-browser auth save myapp --url <登录URL> --username <用户名> --password-stdin
#   agent-browser auth login myapp
# 凭据保险库安全地存储凭据，LLM 永远看不到密码。
#
# 环境变量：
#   APP_USERNAME - 登录用户名/邮箱
#   APP_PASSWORD - 登录密码
#
# 两种模式：
#   1. 发现模式（默认）：显示表单结构，以便你识别引用
#   2. 登录模式：在你更新引用后执行实际登录
#
# 设置步骤：
#   1. 首次运行查看表单结构（发现模式）
#   2. 更新下方"登录流程"部分中的引用
#   3. 设置 APP_USERNAME 和 APP_PASSWORD
#   4. 删除"发现模式"部分

set -euo pipefail

LOGIN_URL="${1:?使用方法: $0 <登录URL> [状态文件]}"
STATE_FILE="${2:-./auth-state.json}"

echo "认证工作流程：$LOGIN_URL"

# ================================================================
# 已保存状态：如果存在有效的已保存状态则跳过登录
# ================================================================
if [[ -f "$STATE_FILE" ]]; then
    echo "正在从 $STATE_FILE 加载已保存的状态..."
    if agent-browser --state "$STATE_FILE" open "$LOGIN_URL" 2>/dev/null; then
        agent-browser wait --load networkidle

        CURRENT_URL=$(agent-browser get url)
        if [[ "$CURRENT_URL" != *"login"* ]] && [[ "$CURRENT_URL" != *"signin"* ]]; then
            echo "会话恢复成功"
            agent-browser snapshot -i
            exit 0
        fi
        echo "会话已过期，正在执行全新登录..."
        agent-browser close 2>/dev/null || true
    else
        echo "加载状态失败，正在重新认证..."
    fi
    rm -f "$STATE_FILE"
fi

# ================================================================
# 发现模式：显示表单结构（设置完成后删除此部分）
# ================================================================
echo "正在打开登录页面..."
agent-browser open "$LOGIN_URL"
agent-browser wait --load networkidle

echo ""
echo "登录表单结构："
echo "---"
agent-browser snapshot -i
echo "---"
echo ""
echo "后续步骤："
echo "  1. 记录引用：用户名=@e?，密码=@e?，提交=@e?"
echo "  2. 用你的引用更新下方的"登录流程"部分"
echo "  3. 设置：export APP_USERNAME='...' APP_PASSWORD='...'"
echo "  4. 删除此"发现模式"部分"
echo ""
agent-browser close
exit 0

# ================================================================
# 登录流程：发现完成后取消注释并自定义
# ================================================================
# : "${APP_USERNAME:?请设置 APP_USERNAME 环境变量}"
# : "${APP_PASSWORD:?请设置 APP_PASSWORD 环境变量}"
#
# agent-browser open "$LOGIN_URL"
# agent-browser wait --load networkidle
# agent-browser snapshot -i
#
# # 填写凭据（根据你的表单更新引用）
# agent-browser fill @e1 "$APP_USERNAME"
# agent-browser fill @e2 "$APP_PASSWORD"
# agent-browser click @e3
# agent-browser wait --load networkidle
#
# # 验证登录成功
# FINAL_URL=$(agent-browser get url)
# if [[ "$FINAL_URL" == *"login"* ]] || [[ "$FINAL_URL" == *"signin"* ]]; then
#     echo "登录失败——仍在登录页面"
#     agent-browser screenshot /tmp/login-failed.png
#     agent-browser close
#     exit 1
# fi
#
# # 保存状态以供后续运行使用
# echo "正在将状态保存到 $STATE_FILE"
# agent-browser state save "$STATE_FILE"
# echo "登录成功"
# agent-browser snapshot -i
