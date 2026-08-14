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
SVC_TIMEOUT="${SVC_TIMEOUT:-10}"

# 带超时执行命令：svc.sh 可能挂起，逐项限时；killer 不持有调用方管道
run_with_timeout() {
    local timeout="$1"
    shift
    local pid rc killer
    "$@" &
    pid=$!
    ( sleep "$timeout"; pkill -P "$pid" 2>/dev/null || true; kill "$pid" 2>/dev/null || true ) >/dev/null 2>&1 &
    killer=$!
    if wait "$pid"; then rc=0; else rc=$?; fi
    pkill -P "$killer" 2>/dev/null || true
    kill "$killer" 2>/dev/null || true
    return "$rc"
}

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
# 包超时：svc.sh 挂起时不无期等待（S1）
run_svc() {
    local sub="$1"
    (cd "$RUNNER_DIR" && run_with_timeout "$SVC_TIMEOUT" ./svc.sh "$sub")
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
        # 服务标记 started ≠ 真实进程存在（issue-24 R3）：
        # Runner.Listener 进程缺失时状态应反映异常，而非误报 running。
        # 限定目标安装目录：避免同主机多个 runner / 测试安装误判。
        if ps -eo args 2>/dev/null | grep -F "$RUNNER_DIR/bin/Runner.Listener" | grep -v grep >/dev/null; then
            print_state "$out" "running"
            return 0
        else
            echo "WARNING: 服务标记 Started 但未找到 Runner.Listener 进程（服务假启动？）" >&2
            print_state "$out" "running-no-process"
            return 1
        fi
    elif [[ "$out" == *"Stopped"* ]]; then
        print_state "$out" "stopped"
        return 1
    fi
    print_state "$out" "unknown"
    return 3
}

# 维护操作标记：stop/start 成功后追加一行到 ops 文件（探针消费，用于四类归因中
# 的「维护操作」类——区分人工停启与意外中断，issue-24 R2/T3）。
# PROBE_OPS_FILE 可覆盖（默认 ~/.sts2-runner-probe/ops.jsonl）。
# 原子追加：同目录临时文件 + 合并 + mv 替换；加锁防并发/中断产生半行
# （探针 tail -1 读取，半行会永久丢失该标记并污染七天归因）。
PROBE_OPS_FILE="${PROBE_OPS_FILE:-$HOME/.sts2-runner-probe/ops.jsonl}"
log_operation() {
    local op="$1"
    local ts dir tmp lock
    ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    dir="$(dirname "$PROBE_OPS_FILE")"
    mkdir -p "$dir" 2>/dev/null || true
    tmp="$dir/.ops.$$.tmp"
    lock="$dir/.ops.lock"
    # 简单文件锁：等待持有者释放（最多 5 秒）。仅在成功创建锁目录后才更新文件；
    # 获取失败（前次中断残留锁或并发超窗）则中止本次写入，避免覆盖/删除他人锁。
    local i=0 holder_pid holder_ts now_ts
    local max_wait="${OPS_LOCK_TIMEOUT:-50}"   # 0.1s × N，默认 5 秒
    local stale_after="${OPS_LOCK_STALE_AFTER:-300}"  # 锁年龄超 300s 视为陈旧（前次中断残留）
    while ! mkdir "$lock" 2>/dev/null; do
        now_ts="$(date +%s)"
        reclaim=""
        orig_inode=""   # 陈旧判定时绑定的锁实例标识（inode，认领前记录）
        # 陈旧判定前先绑定锁实例（inode）：判定与认领始终针对同一实例。
        # 若 A 判定后 B 换新锁，A 的 orig_inode 仍是旧锁 → 认领时校验失败放弃，
        # 不会误删 B 的新锁（TOCTOU）。
        orig_inode="$(stat -f %i "$lock" 2>/dev/null || echo '')"
        if [[ -f "$lock/holder" ]]; then
            # 有持有者标识：仅回收「持有进程已死」的锁。
            # 活进程的锁永不按年龄回收（进程可能因 I/O 卡顿/休眠超时，
            # kill -0 仍成功——按年龄回收会删除有效锁，原持有者恢复后覆盖新记录）。
            holder_pid="$(cat "$lock/holder" 2>/dev/null | cut -d' ' -f1)"
            holder_start="$(cat "$lock/holder" 2>/dev/null | cut -d' ' -f2-)"   # 进程启动时间（epoch）
            # PID + 启动时间双重校验：kill -0 只证明「有进程占着该 PID」；若 PID
            # 被复用（旧持有者死后 PID 分配给无关进程），启动时间必然不同。
            if [[ -n "$holder_pid" ]] && kill -0 "$holder_pid" 2>/dev/null; then
                if [[ -n "$holder_start" ]]; then
                    live_start="$(ps -o lstart= -p "$holder_pid" 2>/dev/null | xargs -I{} date -j -f "%a %b %e %H:%M:%S %Y" "{}" +%s 2>/dev/null || echo '')"
                    if [[ -n "$live_start" ]] && [[ "$live_start" != "$holder_start" ]]; then
                        reclaim="持有进程 ${holder_pid} 的 PID 已被复用（启动时间不符）"
                    elif [[ -z "$live_start" ]]; then
                        # 身份无法验证（lstart 解析失败）→ 保守不回收（防误删活锁）
                        :
                    fi
                fi
            else
                reclaim="持有进程 ${holder_pid:-?} 已不存在"
            fi
        else
            # 无 holder 文件（mkdir 后写 holder 前中断残留）：按锁目录 mtime 安全年龄回收
            lock_age="$(( now_ts - $(stat -f %m "$lock" 2>/dev/null || echo "$now_ts") ))"
            if [[ "$lock_age" -gt "$stale_after" ]]; then
                reclaim="无持有者且目录龄 ${lock_age}s 超过 ${stale_after}s"
            fi
        fi
        if [[ -n "$reclaim" ]]; then
            # 无破坏原子认领：所有竞争者共享单一认领名 .claim（mkdir 原子互斥）。
            # 只有一个进程能成功创建 → 唯一回收者；认领后校验锁 inode 仍是绑定的实例。
            claimant="$lock/.claim"
            # 清理残留认领：仅当 claimant 持有进程已死（claimant 内 PID 校验）。
            # 活进程的认领不按年龄回收（回收者可能因 I/O 卡顿/暂停超过年龄阈值，
            # 按年龄删除会让另一进程误删其有效认领并并发替换日志）。
            if [[ -d "$claimant" ]]; then
                claim_pid="$(cat "$claimant/pid" 2>/dev/null | cut -d' ' -f1 || echo '')"
                claim_start="$(cat "$claimant/pid" 2>/dev/null | cut -d' ' -f2- || echo '')"
                if [[ -n "$claim_pid" ]]; then
                    if ! kill -0 "$claim_pid" 2>/dev/null; then
                        # 持有进程已死 → 清理残留认领
                        rm -rf "$claimant" 2>/dev/null || true
                    elif [[ -n "$claim_start" ]]; then
                        # PID 复用校验：进程启动时间不符 → 视为旧认领残留，可清理
                        claim_live_start="$(ps -o lstart= -p "$claim_pid" 2>/dev/null | xargs -I{} date -j -f "%a %b %e %H:%M:%S %Y" "{}" +%s 2>/dev/null || echo '')"
                        if [[ -n "$claim_live_start" ]] && [[ "$claim_live_start" != "$claim_start" ]]; then
                            rm -rf "$claimant" 2>/dev/null || true
                        fi
                    fi
                else
                    # 空 pid 的 claimant：只可能是 mkdir 后写 pid 前中断残留
                    # （活认领必然已写 pid），按年龄安全回收避免永久阻塞。
                    claim_age="$(( now_ts - $(stat -f %m "$claimant" 2>/dev/null || echo "$now_ts") ))"
                    if [[ "$claim_age" -gt "$stale_after" ]]; then
                        rm -rf "$claimant" 2>/dev/null || true
                    fi
                fi
            fi
            if mkdir "$claimant" 2>/dev/null; then
                # claimant 身份：PID + 进程启动时间（供 PID 复用校验，与主锁 holder 一致）
                claim_start="$(ps -o lstart= -p $$ 2>/dev/null | xargs -I{} date -j -f "%a %b %e %H:%M:%S %Y" "{}" +%s 2>/dev/null || echo '')"
                printf '%s %s\n' "$$" "$claim_start" > "$claimant/pid"
                # 认领成功：重读 inode 确认仍是绑定的陈旧实例（未变才回收）
                curr_inode="$(stat -f %i "$lock" 2>/dev/null || echo '')"
                if [[ -n "$orig_inode" ]] && [[ "$orig_inode" == "$curr_inode" ]]; then
                    echo "WARNING: 回收陈旧 ops 锁（$reclaim）" >&2
                    # claimant 随外层锁一起原子移除（rm -rf lock 含 claimant）：
                    # 不在删除前单独释放 claimant，避免窗口期其他进程重新认领
                    # 并误删本进程随后创建的新锁。
                    rm -rf "$lock"
                    continue
                fi
                # 锁已被他人替换 → 放弃认领，继续等待
                rm -rf "$claimant" 2>/dev/null || true
            fi
            # 认领失败（他人已认领或锁被替换）→ 放弃本次回收，继续等待
        fi
        i=$((i + 1))
        if [[ "$i" -ge "$max_wait" ]]; then
            echo "WARNING: ops 日志锁获取失败（$lock），跳过本次维护标记写入" >&2
            return 1
        fi
        sleep 0.1
    done
    # 持有者标识：PID + 进程启动时间（epoch，供 PID 复用校验）。原子写入：
    # 临时文件 + mv，防半写（半写 holder 会让后续进程误读 PID 错误判定陈旧）。
    holder_tmp="$lock/.holder.$$"
    my_start="$(ps -o lstart= -p $$ 2>/dev/null | xargs -I{} date -j -f "%a %b %e %H:%M:%S %Y" "{}" +%s 2>/dev/null || echo '')"
    printf '%s %s\n' "$$" "$my_start" > "$holder_tmp"
    mv -f "$holder_tmp" "$lock/holder"
    # trap 清理：仅本进程持有期间退出时释放锁（防止中断残留）
    trap 'rm -rf "$lock"' RETURN EXIT
    if [[ -f "$PROBE_OPS_FILE" ]]; then
        # 复制加超时（I/O 卡顿时不无限阻塞）：超时保留原日志、释放锁并告警
        if ! run_with_timeout "${OPS_CP_TIMEOUT:-10}" cp "$PROBE_OPS_FILE" "$tmp" 2>/dev/null; then
            echo "WARNING: ops 日志复制超时，保留原日志并跳过本次写入" >&2
            rm -rf "$lock"
            trap - RETURN EXIT
            return 1
        fi
    fi
    if ! printf '{"ts": "%s", "op": "%s"}\n' "$ts" "$op" >> "$tmp" 2>/dev/null; then
        echo "WARNING: ops 日志追加失败（磁盘满？），本次维护标记未写入" >&2
        rm -rf "$lock"
        trap - RETURN EXIT
        return 1
    fi
    # mv 替换加超时（I/O 卡顿时不无限阻塞）
    if ! run_with_timeout "${OPS_CP_TIMEOUT:-10}" mv -f "$tmp" "$PROBE_OPS_FILE" 2>/dev/null; then
        echo "WARNING: ops 日志替换超时，原日志保留" >&2
        rm -rf "$lock"
        trap - RETURN EXIT
        return 1
    fi
    rm -rf "$lock"
    trap - RETURN EXIT
    return 0
}

cmd_stop() {
    run_svc stop
    # 维护标记写入失败不阻塞服务操作（仅影响归因数据完整性，已有 WARNING）
    log_operation "manual-stop" || true
}

cmd_start() {
    run_svc start
    log_operation "manual-start" || true
}

case "$CMD" in
    status) cmd_status ;;
    stop) cmd_stop ;;
    start) cmd_start ;;
    help|-h|--help) usage ;;
    *) usage >&2; exit 3 ;;
esac
