"""TS-018 MC3 测试 — policies 高危deny + 审计 + 滑条降级 + 前台服务协议。

被测代码: F:\\tianshu_dev\\android\\app\\src\\main\\python\\（Chaquopy 手机侧）。
PC 直跑同一份文件（sys.path 注入）——Chaquopy 环境差异只影响 import 桩，
不进被测逻辑。Kotlin 侧（前台服务/通知/Room）由构建+模拟器验收覆盖。
"""

import json
import sys
from pathlib import Path

import pytest

_ANDROID_PY = Path(r"F:\tianshu_dev\android\app\src\main\python")
sys.path.insert(0, str(_ANDROID_PY))

from agent import Agent  # noqa: E402
from policies import (  # noqa: E402
    RISKY_PACKAGES,
    ActionPolicy,
    audit,
    risky_package_list,
    set_audit_sink,
)
from tools import ApprovalLevel, ToolResult  # noqa: E402


# ── ActionPolicy: 高危包名 deny ─────────────────────────────


class TestActionPolicy:
    def setup_method(self):
        self.policy = ActionPolicy()

    def test_alipay_deny(self):
        assert self.policy.check(
            "open_app", {"package": "com.eg.android.AlipayGphone"}
        ) == "deny"

    def test_wechat_deny(self):
        assert self.policy.check("open_app", {"package": "com.tencent.mm"}) == "deny"

    def test_bank_prefix_match(self):
        # 多渠道包名带后缀也拦
        assert self.policy.check(
            "open_app", {"package": "cmb.pb.subchannel"}
        ) == "deny"

    def test_normal_app_auto(self):
        assert self.policy.check(
            "open_app", {"package": "com.android.settings"}
        ) == "auto"

    def test_prefix_not_substring(self):
        # 前缀匹配不吃子串: com.cmbchina.other 不在 cmb.pb 前缀树里
        r = self.policy.check("open_app", {"package": "com.cmbchina.other"})
        # 招行新包名不在清单——不 deny（宁漏勿误伤正常 app）
        assert r == "auto"

    def test_non_open_app_ignores_package(self):
        # tap 不带包名语义——策略不管
        assert self.policy.check("tap", {"x": 1, "y": 2}) == "auto"

    def test_deny_reason(self):
        reason = self.policy.deny_reason(
            "open_app", {"package": "com.icbc"}
        )
        assert reason is not None and "icbc" in reason
        assert self.policy.deny_reason(
            "open_app", {"package": "com.android.settings"}
        ) is None

    def test_risky_list_sorted_for_kotlin(self):
        lst = risky_package_list()
        assert lst == sorted(lst)
        assert set(lst) == RISKY_PACKAGES


# ── 审计: sink 注入 + 异常静默 ──────────────────────────────


class TestAudit:
    def teardown_method(self):
        set_audit_sink(None)

    def test_audit_calls_sink(self):
        calls = []

        def sink(tool, args, result, decision, ts):
            calls.append((tool, args, result, decision, ts))

        set_audit_sink(sink)
        audit("tap", {"x": 5}, {"success": True}, "suggest")
        assert len(calls) == 1
        tool, args, result, decision, ts = calls[0]
        assert tool == "tap"
        assert json.loads(args) == {"x": 5}
        assert decision == "suggest"
        assert ts.isdigit()

    def test_audit_no_sink_silent(self):
        set_audit_sink(None)
        audit("tap", {}, {})  # 不炸即过

    def test_audit_sink_exception_silent(self):
        def boom(*a):
            raise RuntimeError("db down")

        set_audit_sink(boom)
        audit("tap", {}, {})  # 审计故障不中断 Agent


# ── Agent._execute_with_gate: 审计 + REQUIRED 确认 ───────────


class _FakeProvider:
    """provider 替身——chat 永不真调。"""

    def __init__(self, **kw):
        self.is_configured = True


class TestGateAudit:
    def _agent(self, bridge):
        a = Agent.__new__(Agent)  # 绕过 __init__ 的 provider 构造
        a.mode = "agent"
        a.tools = __import__("tools").ToolRegistry()
        a.tool_bridge = bridge
        a.confirm_cb = None
        a.on_event = None
        return a

    def test_deny_path_audited(self):
        audits = []

        def sink(tool, args, result, decision, ts):
            audits.append((tool, decision, json.loads(result)["content"]))

        set_audit_sink(sink)
        try:
            a = self._agent(lambda name, aj: json.dumps({"ok": True}))
            r = a._execute_with_gate("tap", {"x": 1, "y": 2})
            assert r.success
            assert len(audits) == 1
            assert audits[0][0] == "tap"
            assert audits[0][1] == "suggest"
        finally:
            set_audit_sink(None)

    def test_required_denied_no_confirm_cb(self):
        # 无确认回调 → 保守拒绝 + 审计 denied
        audits = []

        def sink(tool, args, result, decision, ts):
            audits.append((tool, decision))

        set_audit_sink(sink)
        try:
            a = self._agent(lambda name, aj: json.dumps({"ok": True}))
            r = a._execute_with_gate("input_text", {"text": "hi"})
            assert not r.success
            assert "拒绝" in r.content
            assert audits[-1] == ("input_text", "denied")
        finally:
            set_audit_sink(None)

    def test_required_confirmed_executes(self):
        a = self._agent(lambda name, aj: json.dumps({"ok": True}))
        a.confirm_cb = lambda action: True
        r = a._execute_with_gate("input_text", {"text": "hi"})
        assert r.success


# ── set_slider 降级链 ──────────────────────────────────────


class TestSliderFallback:
    def _agent(self, bridge):
        a = Agent.__new__(Agent)
        a.mode = "agent"
        a.tools = __import__("tools").ToolRegistry()
        a.tool_bridge = bridge
        a.confirm_cb = None
        a.on_event = None
        return a

    def test_slider_ok_no_fallback(self):
        calls = []

        def bridge(name, args_json):
            calls.append((name, json.loads(args_json)))
            return json.dumps({"ok": True})

        a = self._agent(bridge)
        r = a._execute_tool("set_slider", {"percent": 30})
        assert r.success
        assert calls == [("set_slider", {"percent": 30})]  # 降级未触发

    def test_slider_fallback_to_drag(self):
        calls = []

        def bridge(name, args_json):
            calls.append((name, json.loads(args_json)))
            if name == "set_slider":
                return json.dumps({"error": "no seekbar on screen"})
            if name == "screen_state":
                # MC3 格式: [滑条] 标记 + @(cx,cy) + [l,t,r,b]
                return json.dumps({
                    "content": "包名: com.android.settings\n\n[滑条] @(540,400) [40,380,1040,420]\n"
                })
            if name == "drag":
                return json.dumps({"ok": True})
            return json.dumps({"ok": True})

        a = self._agent(bridge)
        r = a._execute_tool("set_slider", {"percent": 0})
        assert r.success
        assert "降级" in r.content
        names = [c[0] for c in calls]
        # 顺序: set_slider(失败) → screen_state(找滑条) → drag
        assert names == ["set_slider", "screen_state", "drag"]
        drag_args = calls[2][1]
        # bounds [40,380,1040,420]: cy=400, span=1000
        # percent=0 → target_x=40; 起手 x1=1040-83=957
        assert drag_args["y1"] == drag_args["y2"] == 400
        assert drag_args["x2"] == 40
        assert drag_args["x1"] == 1040 - 1000 // 12

    def test_slider_fallback_no_slider_node(self):
        def bridge(name, args_json):
            if name == "set_slider":
                return json.dumps({"error": "no seekbar on screen"})
            if name == "screen_state":
                return json.dumps({"content": "包名: x\n\n设置 @(100,100) [50,80,150,120]\n"})
            return json.dumps({"ok": True})

        a = self._agent(bridge)
        r = a._execute_tool("set_slider", {"percent": 50})
        # 降级不可行 → 原始错误透出
        assert not r.success
        assert "no seekbar" in r.content

    def test_slider_percent_interpolation(self):
        calls = []

        def bridge(name, args_json):
            calls.append((name, json.loads(args_json)))
            if name == "set_slider":
                return json.dumps({"error": "no seekbar"})
            if name == "screen_state":
                return json.dumps({
                    "content": "[滑条] @(540,400) [100,380,1100,420]\n"
                })
            return json.dumps({"ok": True})

        a = self._agent(bridge)
        a._execute_tool("set_slider", {"percent": 50})
        drag_args = calls[2][1]
        # span=1000, target = 100 + 500 = 600
        assert drag_args["x2"] == 600

    def test_slider_percent_clamped(self):
        calls = []

        def bridge(name, args_json):
            calls.append((name, json.loads(args_json)))
            if name == "set_slider":
                return json.dumps({"error": "no seekbar"})
            if name == "screen_state":
                return json.dumps({
                    "content": "[滑条] @(540,400) [100,380,1100,420]\n"
                })
            return json.dumps({"ok": True})

        a = self._agent(bridge)
        a._execute_tool("set_slider", {"percent": 999})
        drag_args = calls[2][1]
        # 越界钳到 100 → x2 = 1100
        assert drag_args["x2"] == 1100


# ── screen_state 格式契约（Kotlin 侧 screenState 对齐）──────


class TestScreenFormatContract:
    def test_node_line_regex_against_kotlin_format(self):
        """Kotlin screenState 输出行: "[可点] 文本 @(cx,cy) [l,t,r,b]"
        Python 滑条降级正则必须吃下这个格式。"""
        import re
        kotlin_line = "[滑条] 亮度 @(540,399) [42,379,1038,419]\n"
        m = re.search(
            r"\[滑条\][^@\n]*@\((\d+),(\d+)\)\s*\[(\d+),(\d+),(\d+),(\d+)\]",
            kotlin_line,
        )
        assert m is not None
        assert m.group(3) == "42" and m.group(6) == "419"

    def test_flags_before_slider_marker(self):
        import re
        line = "[可点][滑条] 音量 @(540,500) [42,480,1038,520]\n"
        m = re.search(
            r"\[滑条\][^@\n]*@\((\d+),(\d+)\)\s*\[(\d+),(\d+),(\d+),(\d+)\]",
            line,
        )
        assert m is not None


class TestAuditSinkKotlinObject:
    """2026-09-02 模拟器验收发现的生产 bug 回归锁。

    Kotlin 侧曾把裸 KotlinCallbacks 对象传给 set_audit_sink——
    Chaquopy Java 对象没有 __call__，policies.audit 里
    _audit_sink(...) 直接 TypeError，被 except 静默吞掉，
    actions 审计表在生产环境永远为空（test_audit_calls_sink 用
    Python 函数 sink 所以没抓住）。修复：kotlin_bridge.install
    内部经 _wrap 绑定 audit 方法。
    """

    def test_java_style_object_sink_needs_wrap(self):
        """裸 Java 式对象（方法可 getattr，无 __call__）直接当 sink 必失败。"""
        class JavaStyleCallbacks:
            def audit(self, tool, args, result, decision, ts):
                return "ok"

        bare = JavaStyleCallbacks()
        with pytest.raises(TypeError):
            bare("tap", "{}", "{}", "auto", "0")  # Java 对象不可直接调用

    def test_install_binds_audit_via_wrap(self, tmp_path):
        """install() 必须把 audit 方法经 _wrap 装进 policies（而非裸对象）。"""
        import importlib.util as _ilu
        import sys

        dev_root = Path("F:/tianshu_dev/android/app/src/main/python")
        if not (dev_root / "kotlin_bridge.py").exists():
            pytest.skip("tianshu_dev 手机侧源码不在本机")

        # 在隔离世加载 kotlin_bridge（不碰全局 policies）
        saved = {}
        for mod_name in ("kotlin_bridge", "policies", "tools", "agent"):
            saved[mod_name] = sys.modules.pop(mod_name, None)
        try:
            for mod_name in ("policies", "tools", "agent", "kotlin_bridge"):
                spec = _ilu.spec_from_file_location(
                    mod_name, dev_root / f"{mod_name}.py"
                )
                sys.modules[mod_name] = module = _ilu.module_from_spec(spec)
                spec.loader.exec_module(module)

            calls = []

            class Agent:
                pass

            class Callbacks:
                def audit(self, tool, args, result, decision, ts):
                    calls.append((tool, decision))
                    return None

            import policies as fresh_policies
            fresh_policies.set_audit_sink(None)
            a = Agent()
            sys.modules["kotlin_bridge"].install(a, Callbacks())

            # install 后 audit() 应真实到达 Callbacks.audit
            fresh_policies.audit("tap", {"x": 1}, {"success": True}, "auto")
            assert calls == [("tap", "auto")]
        finally:
            for mod_name, mod in saved.items():
                if mod is not None:
                    sys.modules[mod_name] = mod
                else:
                    sys.modules.pop(mod_name, None)
            import policies as prod_policies
            prod_policies.set_audit_sink(None)
