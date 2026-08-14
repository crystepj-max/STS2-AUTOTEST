#!/usr/bin/env bash
# runner-ctl.sh — 自托管 GitHub Actions Runner 统一状态/停止/启动入口（issue-24 T2）
#
# 背景：svc.sh 强依赖「从 runner 根目录运行」，且脚本/文档曾与实际安装漂移
# （setup-mac-runner.sh 曾指向不存在的 ~/actions-runner-autotest）。
# 本脚本统一封装真实安装（默认 ~/actions-runner）下的 svc.sh 操作，
# 保证 status/stop/start 反映真实 launchd 服务状态。
#
# 用法：
#   runner-ctl.sh status    # 打印真实状态；0=RUNNING 1=STOPPED 2=NOT_INSTALLED
#   runner-ctl.sh stop      # 停止服务（svc.sh stop，launchctl unload）
#   runner-ctl.sh start     # 启动服务（svc.sh start，launchctl load -w）
#   runner-ctl.sh help
#
# 环境变量：RUNNER_DIR 覆盖安装目录（默认 $HOME/actions-runner；测试用）
#
# 退出码：0=RUNNING 1=STOPPED 2=NOT_INSTALLED 3=USAGE
set -euo pipefail

RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
CMD="${1:-}"
SVC_SCRIPT="$RUNNER_DIR/svc.sh"

usage() {
    cat <<'EOF'
usage 用法：runner-ctl.sh <status|stop|start|help>
  status  查看真实服务状态（0=RUNNING 1=STOPPED 2=NOT_INSTALLED）
  stop    停止 runner 服务（等价于在 runner 目录执行 ./svc.sh stop）
  start   启动 runner 服务（等价于在 runner 目录执行 ./svc.sh start）
环境变量：RUNNER_DIR 覆盖安装目录（默认 $HOME/actions-runner）
EOF
}

# 打印格式化状态行；解析 svc.sh status 输出（其 exit 恒为 0，只能解析文本）
print_state() {
    local out="$1" state="$2"
    echo "---"
    echo "$out"
    echo "---"
    echo "state: $state"
}

# 执行 svc.sh 子命令（强制在 RUNNER_DIR 内运行，svc.sh 依赖 cwd）
run_svc() {
    local sub="$1"
    (cd "$RUNNER_DIR" && ./svc.sh "$sub")
}

cmd_status() {
    local out state
    if [[ ! -d "$RUNNER_DIR" ]]; then
        echo "ERROR: runner 安装目录未找到（not found）：$RUNNER_DIR" >&2
        echo "state: not-installed"
        return 2
    fi
    if [[ ! -f "$SVC_SCRIPT" ]]; then
        echo "ERROR: $SVC_SCRIPT 不存在，安装可能损坏" >&2
        echo "state: not-installed"
        return 2
    fi
    out="$(run_svc status)"
    if [[ "$out" == *"not installed"* ]]; then
        print_state "$out" "not-installed"
        return 2
    elif [[ "$out" == *"Started:"* ]]; then
        print_state "$out" "running"
        return 0
    elif [[ "$out" == *"Stopped"* ]]; then
        print_state "$out" "stopped"
        return 1
    fi
    print_state "$out" "unknown"
    return 3
}

cmd_stop() {
    run_svc stop
}

cmd_start() {
    run_svc start
}

case "$CMD" in
    status) cmd_status ;;
    stop) cmd_stop ;;
    start) cmd_start ;;
    help|-h|--help) usage ;;
    *) usage >&2; exit 3 ;;
esac
