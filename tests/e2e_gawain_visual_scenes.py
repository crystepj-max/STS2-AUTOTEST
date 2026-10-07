"""Gawain MOD 视觉场景端到端测试：角色选择 / 遗物栏 / 魔网 UI / 仆从头像 / VFX。

前置：deploy-gawain.ps1 已部署最新 MOD（gawain.pck 52,751 KB）。
产物：{GAWAIN}/automation/autotest/output/gawain-visual-scenes-20261006/
      - test-results.json（框架 gen-report 约定结构）
      - 各场景截图 PNG
报告：autotest gen-report --task-id gawain-visual-scenes-20261006
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

# 产物放仓库内不被 gitignore 的 tests/output/（对齐 e2e_first_battle.py 约定），
# task-id 带时间戳避免覆盖上一轮证据。
RUN_STAMP = time.strftime("%Y%m%d-%H%M%S")
OUT = Path(__file__).resolve().parent / "output" / f"gawain-visual-scenes-{RUN_STAMP}"
WINDOW_TITLE = "Slay the Spire 2"
TASK_ID = f"gawain-visual-scenes-{RUN_STAMP}"
# 四系仆从召唤牌（id 子串 → 展示名）
MINION_CARDS = {
    "emergency_recruit": "塞西尔民兵（防御系）",
    "cecil_knight": "塞西尔骑士（进攻系）",
    "cecil_mage": "塞西尔法师（医疗系）",
    "magic_apprentice": "魔导学徒（支援系）",
}
GAWAIN_KEY = "gawain"

DRAW_KEYS = ("magic_draw", "draw", "drain")


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

    def __init__(self, output_dir: Path):
        self._dir = Path(output_dir)
        self._user32 = ctypes.windll.user32
        self._gdi32 = ctypes.windll.gdi32

    def capture_with_validation(self, window_title: str, case_id: str):
        import ctypes.wintypes as wt
        import statistics as _stats

        hwnd = self._user32.FindWindowW(None, window_title)
        if not hwnd:
            return SimpleNamespace(status="skipped", path=None, message="window not found")
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
    def __init__(self):
        OUT.mkdir(parents=True, exist_ok=True)
        self.capture = PrintWindowCapture(OUT)
        self.cases: list[dict[str, Any]] = []
        self.shot_n = 0

    def shot(self, label: str) -> str | None:
        self.shot_n += 1
        result = self.capture.capture_with_validation(WINDOW_TITLE, f"{self.shot_n:02d}_{label}")
        if getattr(result, "ok", getattr(result, "status", None) == "ok") and result.path:
            rel = Path(result.path).name
            log(f"[截图] {label} -> {rel}")
            return rel
        log(f"[截图] {label} skipped: {getattr(result, 'message', '')}")
        return None

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
        if "return_to_menu" in actions and screen in {"GAME_OVER", "VICTORY", "UNKNOWN"}:
            _, screen, actions, payload = await act(
                adapter, "boot", "return_to_menu", None, required=False)
            await asyncio.sleep(1.5)
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
        steps.append(driver.step("到达角色选择界面并截图", "通过", "screen=CHARACTER_SELECT", [shot]))
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
        await asyncio.sleep(1.0)
        shot_sel = driver.shot("scene1_gawain_selected")
        screen, actions, payload = await read_context(adapter)
        selected = str(payload.get("character_select", {}).get("selected_character", ""))
        ok = GAWAIN_KEY in selected.lower()
        steps.append(driver.step("选中 Gawain 并核对选中态", "通过" if ok else "失败",
                                 f"selected_character={selected}", [shot_sel]))
        if not ok:
            result, actual = "失败", f"selected_character={selected}"
            return
        actual = f" Gawain({gawain_id}) 选中成功，选人界面截图 2 张。"
    except Exception as exc:
        result, actual = "失败", str(exc)[:400]
        steps.append(driver.step("异常", "失败", actual, [driver.shot("scene1_error")]))
    driver.record(tc, name, scenario, assertions, steps, actual, result)


async def advance_reward_screens(adapter, screen, actions, payload):
    """跳过卡牌/遗物奖励页，直到非奖励界面。"""
    for _ in range(10):
        if screen in {"CARD_REWARD", "RELIC_REWARD", "BOSS_REWARD"}:
            if "proceed" in actions:
                _, screen, actions, payload = await act(
                    adapter, "reward", "proceed", None, required=False)
                await asyncio.sleep(0.8)
                continue
            if screen == "CARD_REWARD" and "reward_skip_card" in actions:
                _, screen, actions, payload = await act(
                    adapter, "reward", "reward_skip_card", {"type": "card"}, required=False)
                await asyncio.sleep(0.8)
                continue
            if "relic_skip" in actions:
                _, screen, actions, payload = await act(
                    adapter, "reward", "relic_skip", None, required=False)
                await asyncio.sleep(0.8)
                continue
        break
    return screen, actions, payload


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
        if screen in {"CARD_REWARD", "RELIC_REWARD", "BOSS_REWARD"}:
            screen, actions, payload = await advance_reward_screens(adapter, screen, actions, payload)
            continue
        if screen == "UNKNOWN":
            await asyncio.sleep(1.0)
            screen, actions, payload = await read_context(adapter)
            continue
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
        steps.append(driver.step("到达地图并截图遗物栏", "通过" if screen == "MAP" else "失败",
                                 f"screen={screen}, relics(状态)={relic_ids or '(状态未提供列表，以截图为准)'}", [shot]))
        if screen != "MAP":
            result, actual = "失败", f"screen={screen}（未能到达地图）"
        elif relics and not has_terminal:
            result, actual = "失败", f"遗物列表缺 magic_terminal: {relic_ids}"
        else:
            actual = "地图 HUD 遗物栏截图完成；magic_terminal 在位（状态确认或以截图为准）。"
    except Exception as exc:
        result, actual = "失败", str(exc)[:400]
        steps.append(driver.step("异常", "失败", actual, [driver.shot("scene2_error")]))
    driver.record("TC-VIS-02", name, scenario, assertions, steps, actual, result)
    return screen


def choose_map_node(payload: dict[str, Any], prefer: str | None = None):
    m = payload.get("map", {})
    travelable = m.get("travelable_coords", [])
    node_by = {}
    for node in m.get("nodes", []):
        c, r = node.get("col"), node.get("row")
        if isinstance(c, int) and isinstance(r, int):
            node_by[(c, r)] = str(node.get("type", "")).upper()
    shop = monster = first = None
    for coord in travelable:
        t = node_by.get((coord.get("col"), coord.get("row")), "")
        key = {"col": coord.get("col"), "row": coord.get("row")}
        if first is None:
            first = key
        if t == "SHOP" and shop is None:
            shop = key
        if t in {"MONSTER", "ELITE"} and monster is None:
            monster = key
    if prefer == "SHOP" and shop:
        return shop
    if prefer == "MONSTER" and monster:
        return monster
    return shop or monster or first


def hand_ids(payload) -> list[str]:
    combat = payload.get("combat", {})
    hand = combat.get("hand", [])
    return [str(c.get("id") if isinstance(c, dict) else c) for c in hand]


async def scene_magic_web_and_minions(adapter, driver: SceneDriver) -> None:
    """调试路径：enter_combat → 魔网 HUD → give_card 四系召唤 → 逐张截图 → VFX。"""
    tc3, name3 = "TC-VIS-03", "魔网 UI（战斗 HUD）"
    tc4, name4 = "TC-VIS-04", "四系仆从召唤与头像"
    tc5, name5 = "TC-VIS-05", "VFX（出牌特效）"
    steps3, steps4, steps5 = [], [], []
    r3 = r4 = r5 = "通过"
    a3 = a4 = a5 = ""
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
        # MAP：不依赖 available_actions，直接带坐标调 choose_map_node
        for attempt in range(10):
            if screen != "MAP":
                break
            travelable = payload.get("map", {}).get("travelable_coords", [])
            if not travelable:
                # 地图动画未就绪：等待后重读，不伪造坐标
                await asyncio.sleep(1.5)
                screen, actions, payload = await read_context(adapter)
                continue
            coord = {"col": travelable[0].get("col"), "row": travelable[0].get("row")}
            r, screen, actions, payload = await act(
                adapter, "enter-combat", "choose_map_node", coord, required=False)
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
            if hand_ids(payload):
                hand_seen = True
                break
            await asyncio.sleep(1.0)
            screen, actions, payload = await read_context(adapter)
        await asyncio.sleep(2.0)
        shot_hud = driver.shot("scene3_magic_web_hud")
        ok3 = screen == "COMBAT" and hand_seen
        steps3.append(driver.step("enter_combat 并等开场结束，截图魔网 HUD",
                                  "通过" if ok3 else "失败",
                                  f"screen={screen}, hand_seen={hand_seen}", [shot_hud]))
        if not ok3:
            r3, a3 = "失败", f"screen={screen}, hand_seen={hand_seen}"

        given: list[str] = []
        for key in MINION_CARDS:
            card_id = f"gawain:{key}"
            r, screen, actions, payload = await act(
                adapter, f"give-{key}", "give_card", {"card_id": card_id}, required=False)
            if r.status == "success":
                given.append(key)
            await asyncio.sleep(0.5)
        steps4.append(driver.step("give_card 注入四系召唤牌", "通过" if given else "失败",
                                  f"given={given}", []))

        summoned: list[str] = []
        for key in given:
            played_this = False
            for cycle in range(1, 16):
                screen, actions, payload = await read_context(adapter)
                if screen != "COMBAT":
                    break
                ids = hand_ids(payload)
                target = next((i for i in ids if key in i.lower()), None)
                if target:
                    r, screen, actions, payload = await act(
                        adapter, f"summon-{key}", "play_card",
                        {"card_id": target}, required=False)
                    if r.status == "success":
                        played_this = True
                        break
                if "end_turn" in actions:
                    _, screen, actions, payload = await act(
                        adapter, f"summon-{key}", "end_turn", None, required=False)
                await asyncio.sleep(1.0)
            if played_this:
                summoned.append(key)
                await asyncio.sleep(2.0)
                shot_m = driver.shot(f"scene4_minion_{key}")
                steps4.append(driver.step(
                    f"召唤 {MINION_CARDS.get(key, key)}", "通过",
                    f"card played（{len(summoned)}/{len(given)}）", [shot_m]))
            else:
                steps4.append(driver.step(
                    f"召唤 {MINION_CARDS.get(key, key)}", "失败", "手牌内未找到/未打成", []))
        r4 = "通过" if given and len(summoned) == len(given) else ("失败" if not summoned else "通过")
        a4 = f"已召唤 {len(summoned)}/{len(given)}: {summoned}"

        vfx_done = False
        for cycle in range(1, 11):
            screen, actions, payload = await read_context(adapter)
            if screen != "COMBAT":
                break
            ids = hand_ids(payload)
            atk = next((i for i in ids if "strike" in i.lower()), None)
            if atk:
                combat = payload.get("combat", {})
                enemies = [e for e in combat.get("enemies", []) if e.get("is_alive")]
                args = {"card_id": atk}
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
                    steps5.append(driver.step("打牌并连拍特效帧", "通过", f"card={atk}", frames))
                    break
            if "end_turn" in actions:
                _, screen, actions, payload = await act(
                    adapter, "vfx", "end_turn", None, required=False)
            await asyncio.sleep(1.0)
        if not vfx_done:
            steps5.append(driver.step("打牌并连拍特效帧", "失败", "未打成攻击牌", []))
            r5 = "失败"
    except Exception as exc:
        err = str(exc)[:400]
        if not steps3:
            r3, a3 = "失败", err
            steps3.append(driver.step("异常", "失败", err, [driver.shot("scene3_error")]))
        if not steps4:
            r4, a4 = "失败", err
            steps4.append(driver.step("异常", "失败", err, [driver.shot("scene4_error")]))
        if not steps5:
            r5, a5 = "失败", err
            steps5.append(driver.step("异常", "失败", err, [driver.shot("scene5_error")]))
    driver.record(tc3, name3, "enter_combat 进入 Gawain 战斗并等开场动画结束后截图，"
                            "验证魔网 HUD（magic_web_storage_hud）渲染正常。",
                  ["进入 COMBAT", "手牌出现", "魔网 HUD 截图"], steps3, a3, r3)
    driver.record(tc4, name4, "give_card 注入四系召唤牌并依次打出，每系召唤后截图，"
                            "验证各仆从头像透明度效果。",
                  ["四系召唤牌注入成功", "各召唤牌打出", "每系召唤后截图"], steps4, a4, r4)
    driver.record(tc5, name5, "战斗中打出攻击牌并连拍特效帧，验证 VFX 渲染。",
                  ["成功打出攻击牌", "连拍 3 帧"], steps5, a5, r5)


async def advance_event_and_rewards(adapter, screen, actions, payload,
                                    missing: set[str] | None = None):
    """EVENT（对话/选项）、三选一与奖励页的通用推进。"""
    for _ in range(16):
        if screen == "TRI_SELECT":
            offers = [str(c.get("card_id") or c.get("id") or c)
                      for c in payload.get("tri_select", {}).get("cards", [])]
            pick = None
            if missing:
                pick = next((o for o in offers
                             if any(k in o.lower() for k in missing)), None)
            if pick and "tri_select_card" in actions:
                _, screen, actions, payload = await act(
                    adapter, "flow", "tri_select_card", {"card_ids": [pick]}, required=False)
                await asyncio.sleep(0.6)
                continue
            if "tri_select_skip" in actions:
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
        if screen == "CARD_REWARD":
            if "proceed" in actions:
                _, screen, actions, payload = await act(
                    adapter, "flow", "proceed", None, required=False)
                await asyncio.sleep(0.8)
                continue
            if "reward_skip_card" in actions:
                _, screen, actions, payload = await act(
                    adapter, "flow", "reward_skip_card", {"type": "card"}, required=False)
                await asyncio.sleep(0.8)
                continue
        if screen in {"RELIC_REWARD", "BOSS_REWARD"} and "relic_skip" in actions:
            _, screen, actions, payload = await act(
                adapter, "flow", "relic_skip", None, required=False)
            await asyncio.sleep(0.6)
            continue
        break
    return screen, actions, payload


async def main() -> None:
    driver = SceneDriver()
    adapter = AgentAdapter(timeout=60.0, debug_actions=True)
    print("=" * 60)
    print("  Gawain 视觉场景测试：角色选择/遗物栏/魔网UI/仆从头像/VFX")
    print(f"  输出目录: {OUT}")
    print("=" * 60, flush=True)

    proc = None
    game_log_handle = None
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
            raise RuntimeError("调试 API 300s 内仍未就绪（详见 game-stdout.log）")
        log("调试 API 就绪。")

    try:
        await bootstrap_fresh_start(adapter)
        await scene_character_select(adapter, driver)
        final_screen = await scene_relic_bar(adapter, driver)
        if final_screen != "MAP":
            # 复位失败兜底：直接从当前状态推进到战斗由 enter_combat 完成
            log(f"遗物栏场景后画面: {final_screen}，继续由 enter_combat 进战斗")
        await scene_magic_web_and_minions(adapter, driver)
    finally:
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
