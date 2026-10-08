"""Gawain MOD 视觉场景端到端测试：魔网 HUD + 四系仆从召唤 + VFX。

前置：
1. deploy-gawain.ps1 已部署最新 MOD；
2. pip install pillow（本脚本截图落盘依赖）；
3. 游戏带调试 API 启动（STS2_API_PORT=8080），或已运行则直接复用。

流程：bootstrap → 角色选择（Gawain）→ embark → 地图（遗物栏截图）→
      choose_map_node 进入战斗 → 魔网 HUD 截图 → give_card 注入四系召唤牌
      （民兵/骑士/法师/学徒）→ 逐系召唤并截图 → VFX 连拍。

产物：tests/evidence/<task-id>/（test-results.json + 截图，随分支提交可核对）
报告：autotest gen-report --config <产物目录>/test-results.json --output <产物目录>/test-report.html
"""

from __future__ import annotations

import asyncio
import ctypes
import subprocess
import json
import os
import sys
import time
from types import SimpleNamespace
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

os.environ.setdefault("STS2_CLI_PATH", r"D:\STS2-WORKSPACE\.toolchain\sts2-cli-app\sts2.cmd")
os.environ.setdefault("STS2_GAME_DIR", r"D:\SteamLibrary\steamapps\common\Slay the Spire 2")
os.environ.setdefault("STS2_GAME_EXE", os.path.join(os.environ["STS2_GAME_DIR"], "SlayTheSpire2.exe"))

from sts2_autotest.adapters.agent import AgentAdapter  # noqa: E402
from PIL import Image  # noqa: E402  （PrintWindow 捕获用）

# 产物放 tests/evidence/（实测不在 .gitignore 规则内，可随分支提交作为验收证据）；
# OUT 与 TASK_ID 共用同一 RUN_STAMP，避免目录名与 task-id 漂移。
RUN_STAMP = time.strftime("%Y%m%d-%H%M%S")
TASK_ID = f"gawain-visual-scenes-{RUN_STAMP}"
OUT = Path(__file__).resolve().parent / "evidence" / TASK_ID
WINDOW_TITLE = "Slay the Spire 2"
# 四系仆从召唤牌（id 子串 → 展示名）
# 四系召唤牌权威对照表：key（脚本标识）→ 手牌本地化名（zhs）。
# 来源：Gawain/localization/zhs/cards.json 的 GAWAINMOD-<KEY大写>.title；
# 卡 ID = GAWAINMOD-<KEY大写>（give_card 经 card_id_prefixes 映射）。
MINION_NAME_ZH = {
    "emergency_recruit": "紧急征召",
    "cecil_knight": "塞西尔骑士",
    "cecil_mage": "塞西尔法师",
    "magic_apprentice": "魔导学徒",
}
MINION_CARDS = {
    "emergency_recruit": "塞西尔民兵（防御系）",
    "cecil_knight": "塞西尔骑士（进攻系）",
    "cecil_mage": "塞西尔法师（医疗系）",
    "magic_apprentice": "魔导学徒（支援系）",
}
GAWAIN_KEY = "gawain"


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


async def read_context(adapter):
    state = await adapter.get_state()
    actions = await adapter.get_available_actions()
    payload = json.loads(state.model_dump_json())
    return state.screen.value, actions, payload


async def act(adapter, label, action, args=None, *, required=True):
    result = await adapter.act(action, args or {})
    screen, actions, payload = await read_context(adapter)
    print(f"[{label}] {action} -> status={result.status}, screen={screen}", flush=True)
    if result.detail:
        print(f"  detail={result.detail[:160]}", flush=True)
    if required and result.status != "success":
        raise RuntimeError(f"{label} 失败: {action} status={result.status} detail={result.detail}")
    return result, screen, actions, payload


async def wait_for_screen(adapter, target, timeout=30.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await read_context(adapter)
        if last[0] == target:
            return last
        await asyncio.sleep(0.5)
    return last


async def settle_unknown(adapter, timeout=10.0):
    deadline = time.monotonic() + timeout
    last = await read_context(adapter)
    while time.monotonic() < deadline and last[0] == "UNKNOWN":
        await asyncio.sleep(0.5)
        last = await read_context(adapter)
    return last


class PrintWindowCapture:
    """基于 Win32 PrintWindow 的窗口捕获。

    本机为高分屏 + D3D12/HDR 渲染，框架默认的 mss 屏幕拷贝会得到灰噪图，
    PrintWindow(PW_RENDERFULLCONTENT) 可直接从 DWM 取窗口内容，实测有效。
    """

    def __init__(self, output_dir: Path, pid: int | None = None):
        # PID 来源：STS2-Agent v0.16.2 的 GET /health 返回 data.process_id
        # （本机游戏 v0.111.0 + Agent v0.16.2 实测存在）。取不到时为 None，
        # 退回标题匹配首个可见窗口，并在日志注明「未绑定实例」。
        self._dir = Path(output_dir)
        self._pid = pid
        self._user32 = ctypes.windll.user32
        self._gdi32 = ctypes.windll.gdi32

    def _find_hwnd(self, window_title: str) -> int:
        """按标题枚举顶层可见窗口；指定 PID 时只匹配该进程。

        指定 PID 且无匹配时返回 0（让该步记「阻塞」），绝不抓同标题的
        其他实例窗口——抓错窗口的截图会以受控实例名义进报告。
        """
        import ctypes.wintypes as wt

        user32 = self._user32
        found = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def _cb(hwnd, _lparam):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value == window_title and user32.IsWindowVisible(hwnd):
                pid = wt.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found.append((hwnd, pid.value))
            return True

        user32.EnumWindows(_cb, 0)
        if self._pid:
            for hwnd, pid in found:
                if pid == self._pid:
                    return hwnd
            return 0
        return found[0][0] if found else 0

    def capture_with_validation(self, window_title: str, case_id: str):
        import ctypes.wintypes as wt
        import statistics as _stats

        hwnd = self._find_hwnd(window_title)
        if not hwnd:
            reason = (f"no visible window of controlled instance (PID {self._pid})"
                      if self._pid else "window not found")
            return SimpleNamespace(status="skipped", path=None, message=reason)
        # 失焦窗口的 PrintWindow 会取到过时帧（真机实测），先拉前台再截
        self._user32.SetForegroundWindow(hwnd)
        self._user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        time.sleep(1.2)
        rect = wt.RECT()
        self._user32.GetClientRect(hwnd, ctypes.byref(rect))
        w, h = rect.right, rect.bottom
        if w <= 0 or h <= 0:
            return SimpleNamespace(status="skipped", path=None, message="empty client rect")
        PW_RENDERFULLCONTENT = 0x2
        hdc = self._user32.GetWindowDC(hwnd)
        mem = self._gdi32.CreateCompatibleDC(hdc)
        bmp = self._gdi32.CreateCompatibleBitmap(hdc, w, h)
        self._gdi32.SelectObject(mem, bmp)
        ok = self._user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)

        class BMIH(ctypes.Structure):
            _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                        ("biClrImportant", ctypes.c_uint32)]

        bmi = BMIH(40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        self._gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
        self._user32.ReleaseDC(hwnd, hdc)
        self._gdi32.DeleteObject(bmp)
        self._gdi32.DeleteDC(mem)
        if not ok:
            return SimpleNamespace(status="skipped", path=None, message="PrintWindow failed")
        img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1).convert("RGB")
        gray = img.convert("L").resize((64, 36))
        px = list(gray.getdata())
        if _stats.pstdev(px) < 2.0:
            return SimpleNamespace(status="skipped", path=None,
                                   message="capture looks blank (low variance)")
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{case_id}_{time.strftime('%Y%m%dT%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.png"
        img.save(path)
        return SimpleNamespace(status="ok", path=str(path),
                               message=f"PrintWindow {w}x{h}")


class SceneDriver:
    def __init__(self, pid: int | None = None):
        OUT.mkdir(parents=True, exist_ok=True)
        self.capture = PrintWindowCapture(OUT, pid=pid)
        self.cases: list[dict[str, Any]] = []
        self.shot_n = 0

    def shot(self, label: str) -> str | None:
        self.shot_n += 1
        result = self.capture.capture_with_validation(WINDOW_TITLE, f"{self.shot_n:02d}_{label}")
        if getattr(result, "ok", getattr(result, "status", None) == "ok") and result.path:
            self.last_skip_reason = None
            rel = Path(result.path).name
            log(f"[截图] {label} -> {rel}")
            return rel
        self.last_skip_reason = getattr(result, "message", "capture skipped")
        log(f"[截图] {label} skipped: {self.last_skip_reason}")
        return None

    @staticmethod
    def with_shot(shot_path: str | None, step_result: str):
        """视觉证据缺失时把「通过」降级为「阻塞」（仓库红线：无截图不得 PASSED）。"""
        if shot_path or step_result != "通过":
            return step_result
        return "阻塞"

    def skip_note(self) -> str:
        reason = getattr(self, "last_skip_reason", None)
        return f"（截图缺失：{reason}）" if reason else "（截图缺失）"

    def record(self, tc_id: str, name: str, scenario: str, assertions: list[str],
               steps: list[dict], actual: str, result: str) -> None:
        self.cases.append({
            "id": tc_id, "name": name, "scenario": scenario,
            "assertions": assertions, "steps": steps, "actual": actual, "result": result,
        })

    def step(self, name: str, result: str, detail: str, images: list[str | None]) -> dict:
        return {"name": name, "result": result, "detail": detail,
                "image_paths": [i for i in images if i]}


async def bootstrap_fresh_start(adapter):
    """动作驱动式 bootstrap：UNKNOWN/MODAL 等未映射画面也按可用动作推进。"""
    screen, actions, payload = await read_context(adapter)
    log(f"当前画面: {screen}, 动作={actions[:8]}")
    deadline = time.monotonic() + 90.0
    while time.monotonic() < deadline:
        if screen == "CHARACTER_SELECT":
            return screen, actions, payload
        # 已在局内（MAP/COMBAT 等）：存档退出回菜单，弃档重开，保证从选人开始
        if screen in {"MAP", "COMBAT", "REST", "SHOP", "EVENT"} and "save_and_quit" in actions:
            _, screen, actions, payload = await act(
                adapter, "boot", "save_and_quit", None, required=False)
            await asyncio.sleep(1.5)
            continue
        # 卡包选择页（Scroll Boxes 遗物残留）：先 choose_bundle 选中卡包，
        # 待其 pending 完成后再 confirm_bundle 确认领取（顺序颠倒会卡页）。
        # 两个动作均不在 AgentAdapter 分支表，直接 HTTP 调用。
        if screen in {"BUNDLE_SELECTION", "BUNDLE_SELECT", "CONFIRM_BUNDLE"}:
            import httpx as _hx
            async with _hx.AsyncClient(timeout=30.0) as _c:
                await _c.post("http://127.0.0.1:8080/action",
                              json={"action": "choose_bundle", "option_index": 0})
            await asyncio.sleep(2.5)
            async with _hx.AsyncClient(timeout=30.0) as _c:
                await _c.post("http://127.0.0.1:8080/action",
                              json={"action": "confirm_bundle"})
            await asyncio.sleep(1.5)
            continue
        # 中途奖励页（复用上一局残留时）：同真机序列跳过并前进
        if screen in {"CARD_REWARD", "REWARD"}:
            _, screen, actions, payload = await act(
                adapter, "boot", "skip_reward_cards", None, required=False)
            await asyncio.sleep(0.8)
            _, screen, actions, payload = await act(
                adapter, "boot", "collect_rewards_and_proceed", None, required=False)
            await asyncio.sleep(1.2)
            continue
        if screen in {"GAME_OVER", "VICTORY"}:
            # 本 Agent build：continue_game_over → return_to_main_menu
            # （return_to_menu 透传会报 not supported）
            for cand in ("continue_game_over", "return_to_main_menu", "return_to_menu"):
                if cand in actions:
                    _, screen, actions, payload = await act(
                        adapter, "boot", cand, None, required=False)
                    await asyncio.sleep(1.5)
                    break
            continue
        if "start_new_run" in actions or "new_run" in actions:
            name = "start_new_run" if "start_new_run" in actions else "new_run"
            _, screen, actions, payload = await act(
                adapter, "boot", name, None, required=False)
            await asyncio.sleep(1.5)
            continue
        if "choose_game_mode" in actions:
            _, screen, actions, payload = await act(
                adapter, "boot", "choose_game_mode", {"mode": "standard"}, required=False)
            await asyncio.sleep(1.5)
            continue
        await asyncio.sleep(1.0)
        screen, actions, payload = await read_context(adapter)
    raise RuntimeError(f"bootstrap 未到达 CHARACTER_SELECT：screen={screen}, actions={actions}")


async def scene_character_select(adapter, driver: SceneDriver) -> None:
    tc = "TC-VIS-01"
    name = "角色选择界面（Gawain MOD）"
    scenario = ("部署最新 MOD 后从主菜单进入角色选择，验证 Gawain 出现在可选角色中、"
                "选人背景与立绘（char_select_bg / char_select 小图）渲染正常。")
    assertions = [
        "到达 CHARACTER_SELECT 界面",
        "可选角色列表中包含 gawain（不区分大小写）",
        "选中 Gawain 后界面状态同步（selected_character 含 gawain）",
    ]
    steps: list[dict] = []
    result = "通过"
    actual = ""
    try:
        shot = driver.shot("scene1_character_select")
        steps.append(driver.step("到达角色选择界面并截图",
                                 driver.with_shot(shot, "通过"),
                                 "screen=CHARACTER_SELECT" + (driver.skip_note() if not shot else ""),
                                 [shot]))
        screen, actions, payload = await read_context(adapter)
        cs = payload.get("character_select", {})
        chars = cs.get("available_characters") or cs.get("characters") or []
        ids = [str(c.get("character_id") or c.get("id") or c) for c in chars] if chars else []
        gawain_id = next((i for i in ids if GAWAIN_KEY in i.lower()), None)
        if gawain_id is None:
            # 可选列表没有 gawain：MOD 可能未加载。仍按 MOD 声明 id 尝试选择，
            # 但本步记阻塞；最终判定只看 selected_character，避免假通过。
            gawain_id = "gawain:character"
            steps.append(driver.step(
                "在可选角色列表中查找 gawain", "阻塞",
                f"列表未出现 gawain（available={ids or '(空)'}），MOD 可能未加载；"
                f"仍按 MOD 声明 id 尝试选择", []))
        else:
            steps.append(driver.step("在可选角色列表中查找 gawain", "通过",
                                     f"gawain_id={gawain_id}", []))
        _, screen, actions, payload = await act(adapter, "select", "select_character",
                                                {"character_id": gawain_id})
        # 选中态回填可能有延迟：最多重读 5s，仍未回填则重选一次
        selected = ""
        for _ in range(5):
            screen, actions, payload = await read_context(adapter)
            selected = str(payload.get("character_select", {}).get("selected_character", ""))
            if selected:
                break
            await asyncio.sleep(1.0)
        if not selected:
            _, screen, actions, payload = await act(adapter, "select", "select_character",
                                                    {"character_id": gawain_id}, required=False)
            await asyncio.sleep(2.0)
            screen, actions, payload = await read_context(adapter)
            selected = str(payload.get("character_select", {}).get("selected_character", ""))
        shot_sel = driver.shot("scene1_gawain_selected")
        ok = GAWAIN_KEY in selected.lower()
        step_res = driver.with_shot(shot_sel, "通过" if ok else "失败")
        steps.append(driver.step("选中 Gawain 并核对选中态", step_res,
                                 f"selected_character={selected}"
                                 + (driver.skip_note() if not shot_sel else ""), [shot_sel]))
        if not ok:
            # 本 Agent build 在 select 成功后也可能不回填 selected_character：
            # 记「阻塞」（选人动作成功 + 截图为证），不判失败——
            # embark 后魔网 HUD 激活是 Gawain 生效的强证据。
            result, actual = ("阻塞", "select 动作成功但 selected_character 未回填"
                              "（Agent 形状），以截图与后续魔网 HUD 激活为证")
        elif step_res != "通过":
            result, actual = step_res, "选中成功但视觉证据缺失，无法核对选人立绘。"
        elif any(s["result"] == "阻塞" for s in steps):
            result = "阻塞"
            actual = "选中成功，但可选列表未出现 gawain（MOD 加载存疑），需人工复核。"
        else:
            actual = f" Gawain({gawain_id}) 选中成功，选人界面截图 2 张。"
    except Exception as exc:
        result, actual = "失败", str(exc)[:400]
        steps.append(driver.step("异常", "失败", actual, [driver.shot("scene1_error")]))
    driver.record(tc, name, scenario, assertions, steps, actual, result)


async def scene_embark_to_map(adapter, driver: SceneDriver) -> str:
    """embark 并推进到 MAP，返回当前 screen。"""
    _, screen, actions, payload = await act(adapter, "embark", "embark", None, required=False)
    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        if screen in {"MAP", "COMBAT"}:
            return screen
        if screen == "EVENT":
            if "choose_event" in actions:
                _, screen, actions, payload = await act(
                    adapter, "map-progress", "choose_event", {"option_index": 0}, required=False)
                await asyncio.sleep(0.6)
                continue
            if "advance_dialogue" in actions:
                _, screen, actions, payload = await act(adapter, "map-progress", "advance_dialogue")
                await asyncio.sleep(0.6)
                continue
        before = screen
        screen, actions, payload = await advance_event_and_rewards(
            adapter, screen, actions, payload)
        if screen == before and screen != "UNKNOWN":
            # 本轮没有可发动作（页面动画/等待）：重读 + 让出，避免忙等
            await asyncio.sleep(1.5)
            screen, actions, payload = await read_context(adapter)
        if screen == "UNKNOWN":
            await asyncio.sleep(1.0)
            screen, actions, payload = await read_context(adapter)
    return screen


async def scene_relic_bar(adapter, driver: SceneDriver) -> str:
    name = "遗物栏（地图 HUD）"
    scenario = ("选中 Gawain 并 embark 后到达地图，验证初始遗物「魔网终端」（magic_terminal）"
                "出现在遗物栏且渲染正常。")
    assertions = ["到达 MAP 界面", "状态/画面中可见初始遗物 magic_terminal"]
    steps: list[dict] = []
    result = "通过"
    actual = ""
    screen = "UNKNOWN"
    try:
        screen = await scene_embark_to_map(adapter, driver)
        if screen != "MAP":
            screen, actions, payload = await wait_for_screen(adapter, "MAP", timeout=20.0)
        else:
            screen, actions, payload = await read_context(adapter)
        shot = driver.shot("scene2_relic_bar_on_map")
        relics = payload.get("relics") or payload.get("player", {}).get("relics") or []
        relic_ids = [str(r.get("relic_id") or r.get("id") or r) for r in relics] if relics else []
        has_terminal = any("magic_terminal" in rid.lower() for rid in relic_ids)
        step_res = driver.with_shot(shot, "通过" if screen == "MAP" else "失败")
        steps.append(driver.step("到达地图并截图遗物栏", step_res,
                                 f"screen={screen}, relics(状态)={relic_ids or '(状态未提供列表)'}"
                                 + (driver.skip_note() if not shot else ""), [shot]))
        if screen != "MAP":
            result, actual = "失败", f"screen={screen}（未能到达地图）"
        elif step_res != "通过":
            result, actual = "阻塞", "已到地图但遗物栏截图缺失，视觉验收未闭环。" + driver.skip_note()
        elif not relics:
            result, actual = "阻塞", "状态数据未提供遗物列表（无法核对 magic_terminal，仅截图为证）"
        elif not has_terminal:
            result, actual = "失败", f"遗物列表缺 magic_terminal: {relic_ids}"
        else:
            actual = "遗物列表含 magic_terminal，遗物栏截图完成。"
    except Exception as exc:
        result, actual = "失败", str(exc)[:400]
        steps.append(driver.step("异常", "失败", actual, [driver.shot("scene2_error")]))
    driver.record("TC-VIS-02", name, scenario, assertions, steps, actual, result)
    return screen


def hand_entries(payload) -> list[dict]:
    """战斗手牌条目。本机 Agent build（v0.16.2）手牌条目 id 为 null，
    只有 name（本地化名）/ index / playable。"""
    combat = payload.get("combat", {})
    return [c for c in (combat.get("hand") or []) if isinstance(c, dict)]


def find_playable_index(payload, zh_name: str) -> int | None:
    """按本地化名找第一张可打的手牌，返回 card_index。"""
    for c in hand_entries(payload):
        if c.get("name") == zh_name and c.get("playable") and c.get("index") is not None:
            return c["index"]
    return None


async def scene_magic_web_and_minions(adapter, driver: SceneDriver) -> None:
    """调试路径：enter_combat → 魔网 HUD → give_card 四系召唤 → 逐张截图 → VFX。"""
    tc3, name3 = "TC-VIS-03", "魔网 UI（战斗 HUD）"
    tc4, name4 = "TC-VIS-04", "四系仆从召唤与头像"
    tc5, name5 = "TC-VIS-05", "VFX（出牌特效）"
    # 初值「阻塞」：执行中断（异常/超时）时用例按阻塞呈现，不泄漏「通过」
    steps3, steps4, steps5 = [], [], []
    r3 = r4 = r5 = "阻塞"
    a3 = a4 = a5 = "执行中断，未判定完成"
    screen = "UNKNOWN"
    try:
        # 先从 Neow 事件推进到 MAP，再 enter_combat（事件期间调试进战斗会被拒）
        screen, actions, payload = await read_context(adapter)
        nav_deadline = time.monotonic() + 120.0
        while time.monotonic() < nav_deadline and screen not in {"MAP", "COMBAT"}:
            screen, actions, payload = await advance_event_and_rewards(
                adapter, screen, actions, payload)
            if screen in {"MAP", "COMBAT"}:
                break
            if screen == "UNKNOWN":
                screen, actions, payload = await settle_unknown(adapter, timeout=8.0)
            await asyncio.sleep(1.0)
            screen, actions, payload = await read_context(adapter)
        # MAP：Agent 适配器的节点在 map.available_nodes（自带 index，
        # 适配器把 index 翻译成 option_index）；CLI 形状回落 map.travelable_coords。
        # 两个列表都为空（地图动画未就绪）时等待重读，不伪造坐标。
        for attempt in range(10):
            if screen != "MAP":
                break
            m = payload.get("map", {})
            nodes = m.get("available_nodes") or []
            if nodes and isinstance(nodes[0], dict) and nodes[0].get("index") is not None:
                args = {"option_index": nodes[0]["index"]}
            else:
                travelable = m.get("travelable_coords", [])
                if not travelable:
                    await asyncio.sleep(1.5)
                    screen, actions, payload = await read_context(adapter)
                    continue
                args = {"col": travelable[0].get("col"), "row": travelable[0].get("row")}
            r, screen, actions, payload = await act(
                adapter, "enter-combat", "choose_map_node", args, required=False)
            if screen == "MAP":
                await asyncio.sleep(1.0)
                screen, actions, payload = await read_context(adapter)
        if screen != "COMBAT":
            screen, actions, payload = await wait_for_screen(adapter, "COMBAT", timeout=30.0)
        if screen != "COMBAT":
            screen, actions, payload = await settle_unknown(adapter, timeout=8.0)
            screen, actions, payload = await wait_for_screen(adapter, "COMBAT", timeout=15.0)
        hand_seen = False
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and screen == "COMBAT":
            entries = hand_entries(payload)
            if entries and all(c.get("name") for c in entries):
                hand_seen = True
                break
            await asyncio.sleep(1.0)
            screen, actions, payload = await read_context(adapter)
        await asyncio.sleep(2.0)
        shot_hud = driver.shot("scene3_magic_web_hud")
        ok3 = screen == "COMBAT" and hand_seen
        if ok3:
            a3 = "战斗开场结束、手牌就绪后魔网 HUD 截图完成。"
        res3 = driver.with_shot(shot_hud, "通过" if ok3 else "失败")
        steps3.append(driver.step("走地图节点进战斗并等开场结束，截图魔网 HUD", res3,
                                  f"screen={screen}, hand_seen={hand_seen}"
                                  + (driver.skip_note() if not shot_hud else ""), [shot_hud]))
        if not ok3:
            r3, a3 = "失败", f"screen={screen}, hand_seen={hand_seen}"
        elif res3 != "通过":
            r3, a3 = "阻塞", "战斗状态就绪但魔网 HUD 截图缺失，无法核对。" + driver.skip_note()

        given: list[str] = []
        for key in MINION_CARDS:
            # 走适配器 give_card：gawain:<id> 经 card_id_prefixes 翻译为
            # GAWAINMOD-<ID>（等价于原生调试命令 card <id> hand；
            # 直接透传 gawain:xxx 会报 GAWAIN:XXX not found）。
            r, screen, actions, payload = await act(
                adapter, f"give-{key}", "give_card",
                {"card_id": f"gawain:{key}"}, required=False)
            if r.status == "success":
                given.append(key)
            await asyncio.sleep(0.5)
        steps4.append(driver.step("give_card 注入四系召唤牌",
                                  "通过" if given else "失败",
                                  f"given={given}", []))

        summoned: list[str] = []
        for key in given:
            played_this = False
            expected_name = MINION_NAME_ZH.get(key, "")
            # 时间预算制：敌方回合（无 end_turn 可用、hand 为空）等待重读，
            # 不消耗尝试机会；总预算 120s/张。
            deadline = time.monotonic() + 120.0
            end_turns = 0
            while time.monotonic() < deadline:
                screen, actions, payload = await read_context(adapter)
                if screen != "COMBAT":
                    break
                combat = payload.get("combat", {})
                hand = combat.get("hand", []) or []
                # Agent 手牌条目 id 为 null，只有 name（本地化）与 index
                entry = next((c for c in hand
                              if isinstance(c, dict) and c.get("name") == expected_name
                              and c.get("playable")), None)
                if entry is not None and entry.get("index") is not None:
                    r, screen, actions, payload = await act(
                        adapter, f"summon-{key}", "play_card",
                        {"card_id": expected_name, "card_index": entry["index"]},
                        required=False)
                    if r.status == "success":
                        played_this = True
                        break
                if "end_turn" in actions and end_turns < 4:
                    _, screen, actions, payload = await act(
                        adapter, f"summon-{key}", "end_turn", None, required=False)
                    end_turns += 1
                await asyncio.sleep(2.0)
            if played_this:
                summoned.append(key)
                await asyncio.sleep(2.0)
                shot_m = driver.shot(f"scene4_minion_{key}")
                res_m = driver.with_shot(shot_m, "通过")
                steps4.append(driver.step(
                    f"召唤 {MINION_CARDS.get(key, key)}", res_m,
                    f"card played（{len(summoned)}/{len(given)}）"
                    + (driver.skip_note() if not shot_m else ""), [shot_m]))
            else:
                steps4.append(driver.step(
                    f"召唤 {MINION_CARDS.get(key, key)}", "失败",
                    f"手牌内未找到「{expected_name}」/未打成", []))
        if given and len(summoned) == len(given):
            shots_missing = sum(1 for s in steps4 if s["result"] == "阻塞")
            if shots_missing:
                r4 = "阻塞"
                a4 = (f"已召唤 {len(summoned)}/{len(given)}，但 {shots_missing} 张头像截图缺失，"
                      f"视觉验收无法闭环: {summoned}")
            else:
                r4 = "通过"
                a4 = f"四系仆从全部召唤并截图：{summoned}"
        else:
            r4 = "失败"
            a4 = f"已召唤 {len(summoned)}/{len(given) or 0}: {summoned}"

        vfx_done = False
        end_turns = 0
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline and not vfx_done:
            screen, actions, payload = await read_context(adapter)
            if screen != "COMBAT":
                break
            idx = find_playable_index(payload, "打击")
            if idx is not None:
                combat = payload.get("combat", {})
                enemies = [e for e in combat.get("enemies", []) if e.get("is_alive")]
                args = {"card_index": idx}
                if enemies and isinstance(enemies[0].get("combat_id"), int):
                    args["target"] = enemies[0]["combat_id"]
                r, screen, actions, payload = await act(
                    adapter, "vfx", "play_card", args, required=False)
                if r.status == "success":
                    vfx_done = True
                    frames = []
                    for j, delay in enumerate((0.3, 0.8, 1.5)):
                        await asyncio.sleep(delay)
                        frames.append(driver.shot(f"scene5_vfx_frame{j + 1}"))
                    got_frames = [f for f in frames if f]
                    if got_frames:
                        res5 = "通过"
                        a5 = f"攻击牌（手牌第 {idx} 张）打出后连拍 3 帧完成。"
                    else:
                        res5 = "阻塞"
                        r5 = "阻塞"
                        a5 = "特效牌打出成功但连拍全部缺失，无法核对。" + driver.skip_note()
                    steps5.append(driver.step("打牌并连拍特效帧", res5,
                                              f"card=打击 index={idx}"
                                              + (driver.skip_note() if not got_frames else ""), frames))
                    break
            # 找不到可打牌时只等待重读；end_turn 限次（避免把战局推完）
            if "end_turn" in actions and end_turns < 4:
                _, screen, actions, payload = await act(
                    adapter, "vfx", "end_turn", None, required=False)
                end_turns += 1
            await asyncio.sleep(2.0)
        if not vfx_done:
            steps5.append(driver.step("打牌并连拍特效帧", "失败", "预算时间内未打成攻击牌", []))
            r5 = "失败"
    except Exception as exc:
        err = str(exc)[:400]
        # 中断点之前已记步骤的用例：结果降「阻塞」（部分完成，未判定完）；
        # 一条都没记的：记「失败」+ 异常步骤。
        driver.shot("scene_error")
        if not steps3:
            r3, a3 = "失败", err
        elif r3 == "通过":
            r3, a3 = "阻塞", f"用例完成后段中断：{err}"
        if not steps4:
            r4, a4 = "失败", err
        elif r4 == "通过":
            r4, a4 = "阻塞", f"用例完成后段中断：{err}"
        if not steps5:
            r5, a5 = "失败", err
        elif r5 == "通过":
            r5, a5 = "阻塞", f"用例完成后段中断：{err}"
    driver.record(tc3, name3, "enter_combat 进入 Gawain 战斗并等开场动画结束后截图，"
                            "验证魔网 HUD（magic_web_storage_hud）渲染正常。",
                  ["到达 MAP 并走节点进战斗", "手牌出现", "魔网 HUD 截图"], steps3, a3, r3)
    driver.record(tc4, name4, "give_card 注入四系召唤牌并依次打出，每系召唤后截图，"
                            "验证各仆从头像透明度效果。",
                  ["四系召唤牌注入成功", "各召唤牌打出", "每系召唤后截图"], steps4, a4, r4)
    driver.record(tc5, name5, "战斗中打出攻击牌并连拍特效帧，验证 VFX 渲染。",
                  ["成功打出攻击牌", "连拍 3 帧"], steps5, a5, r5)


async def advance_event_and_rewards(adapter, screen, actions, payload,
                                    missing: set[str] | None = None):
    """EVENT（对话/选项）、三选一与奖励页的通用推进。"""
    for _ in range(16):
        # 注意：Agent 在 CARD_REWARD / TRI_SELECT 等页面可能不下发
        # available_actions（实测为空），因此这些页面不 gating 动作列表，
        # 直接尝试标准动作（required=False，失败无害继续）。
        if screen == "TRI_SELECT":
            offers = [str(c.get("card_id") or c.get("id") or c)
                      for c in payload.get("tri_select", {}).get("cards", [])]
            pick = None
            if missing:
                pick = next((o for o in offers
                             if any(k in o.lower() for k in missing)), None)
            if pick:
                _, screen, actions, payload = await act(
                    adapter, "flow", "tri_select_card", {"card_ids": [pick]}, required=False)
                await asyncio.sleep(0.6)
                continue
            _, screen, actions, payload = await act(
                adapter, "flow", "tri_select_skip", None, required=False)
            await asyncio.sleep(0.6)
            continue
        if screen == "EVENT":
            if "choose_event" in actions:
                _, screen, actions, payload = await act(
                    adapter, "flow", "choose_event", {"option_index": 0}, required=False)
                await asyncio.sleep(0.6)
                continue
            if "advance_dialogue" in actions:
                _, screen, actions, payload = await act(
                    adapter, "flow", "advance_dialogue", None, required=False)
                await asyncio.sleep(0.6)
                continue
        if screen in {"BUNDLE_SELECTION", "BUNDLE_SELECT", "CONFIRM_BUNDLE"}:
            # 先 choose_bundle 选中卡包再 confirm_bundle 确认（顺序颠倒会卡页）；
            # 两动作均不在 AgentAdapter 分支表，直接 HTTP 调用。
            import httpx as _hx
            async with _hx.AsyncClient(timeout=30.0) as _c:
                await _c.post("http://127.0.0.1:8080/action",
                              json={"action": "choose_bundle", "option_index": 0})
            await asyncio.sleep(2.5)
            async with _hx.AsyncClient(timeout=30.0) as _c:
                await _c.post("http://127.0.0.1:8080/action",
                              json={"action": "confirm_bundle"})
            await asyncio.sleep(1.5)
            continue
        if screen == "CARD_REWARD":
            # 真机实测（Agent build v0.16.2 + 游戏 v0.111）：
            # 该页可用动作为 resolve_rewards / collect_rewards_and_proceed / claim_reward /
            # skip_reward_cards，但单独 skip_reward_cards 不离开页面；
            # 有效序列是先 skip（弃卡）再 collect_rewards_and_proceed（收下并前进）。
            _, screen, actions, payload = await act(
                adapter, "flow", "skip_reward_cards", None, required=False)
            await asyncio.sleep(0.8)
            _, screen, actions, payload = await act(
                adapter, "flow", "collect_rewards_and_proceed", None, required=False)
            await asyncio.sleep(1.2)
            continue
        if screen in {"RELIC_REWARD", "BOSS_REWARD"}:
            _, screen, actions, payload = await act(
                adapter, "flow", "relic_skip", None, required=False)
            await asyncio.sleep(0.8)
            continue
        break
    return screen, actions, payload


async def main() -> None:
    driver = SceneDriver()
    adapter = AgentAdapter(timeout=60.0, debug_actions=True,
                           card_id_prefixes={"gawain": "GAWAINMOD-"})
    print("=" * 60)
    print("  Gawain 视觉场景测试：角色选择/遗物栏/魔网UI/仆从头像/VFX")
    print(f"  输出目录: {OUT}")
    print("=" * 60, flush=True)

    proc = None
    game_log_handle = None
    boot_error: str | None = None
    try:
        # 启动阶段并入主 try：任何失败都要落报告并回收进程（阻断 7）。
        try:
            screen, actions, payload = await read_context(adapter)
            log(f"游戏已在运行，直接复用（screen={screen}）。")
        except Exception:
            # 复用失败 → 自行启动游戏。注意：游戏为 Godot 控制台变体，
            # stdout 必须重定向到文件（DEVNULL/DETACHED 会在首次日志写入时崩溃）。
            game_dir = os.environ["STS2_GAME_DIR"]
            game_exe = os.environ["STS2_GAME_EXE"]
            game_log = OUT / "game-stdout.log"
            env = dict(os.environ)
            env["STS2_API_PORT"] = "8080"
            env["STS2_ENABLE_DEBUG_ACTIONS"] = "1"
            game_log_handle = open(game_log, "ab", buffering=0)
            proc = subprocess.Popen([game_exe], cwd=game_dir, env=env,
                                    stdout=game_log_handle,
                                    stderr=subprocess.STDOUT)
            log(f"游戏已启动 PID={proc.pid}，等待调试 API...")
            api_ok = False
            deadline = time.monotonic() + 300.0
            while time.monotonic() < deadline:
                try:
                    await read_context(adapter)
                    api_ok = True
                    break
                except Exception:
                    if proc.poll() is not None:
                        break
                    await asyncio.sleep(3.0)
            if not api_ok:
                boot_error = "调试 API 300s 内仍未就绪（详见 game-stdout.log）"
                raise RuntimeError(boot_error)
            log("调试 API 就绪。")

        # 从 /health 取受控实例 PID，截图按 PID 匹配窗口（防多实例抓错）
        import httpx as _hx
        try:
            h = await _hx.AsyncClient(timeout=10.0).get("http://127.0.0.1:8080/health")
            game_pid = int(h.json()["data"]["process_id"])
            driver.capture = PrintWindowCapture(OUT, pid=game_pid)
            log(f"受控游戏实例 PID={game_pid}（截图已绑定该实例）")
        except Exception:
            log("无法从 /health 取 PID，截图退回标题匹配")

        await bootstrap_fresh_start(adapter)
        await scene_character_select(adapter, driver)
        final_screen = await scene_relic_bar(adapter, driver)
        if final_screen != "MAP":
            log(f"遗物栏场景后画面: {final_screen}，继续走地图节点进战斗")
        await scene_magic_web_and_minions(adapter, driver)
    finally:
        if boot_error is not None:
            driver.record("TC-BOOT", "启动游戏并就绪调试 API",
                          "复用或启动游戏、等待 STS2-Agent 调试 API 就绪。",
                          ["游戏进程存活", "API 就绪"], [],
                          boot_error, "阻塞")
        results = {
            "test_run_id": TASK_ID,
            "metadata": {
                "mod": "gawain (v0.0.7, 已部署最新透明度整改素材)",
                "game": "Slay the Spire 2 v0.111.0",
                "date": time.strftime("%Y-%m-%d %H:%M:%S"),
                "purpose": "透明度整改素材的游戏内视觉验收：角色选择/遗物栏/魔网UI/仆从头像/VFX",
            },
            "test_cases": driver.cases,
        }
        (OUT / "test-results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"结果 JSON 已写入 {OUT / 'test-results.json'}")
        if proc is not None and proc.poll() is None:
            proc.kill()
            log("游戏已关闭。")
        if game_log_handle is not None:
            game_log_handle.close()

    passed = sum(1 for c in driver.cases if c["result"] == "通过")
    print(f"\n场景通过 {passed}/{len(driver.cases)}")
    sys.exit(0 if passed == len(driver.cases) and driver.cases else 1)


if __name__ == "__main__":
    asyncio.run(main())
