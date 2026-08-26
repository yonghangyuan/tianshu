# 天枢手机控制 — 技术方案 (TS-018 / AND-002)

> 创建: 2026-08-25 | 状态: 方案定稿，待开工
> 目标: 天枢 Agent 能看懂并操作真实 Android 手机（小米 17 · 澎湃 OS）

---

## 一、定位与验收

**一句话**: 手机是天秤座的第三个终端（CLI/浏览器之后），天枢通过节点树"看"屏幕、通过无障碍服务"动手"。

**MVP 验收（一条命令全自动完成，全程无人工干预）**:
```
用户: 把屏幕亮度调到最低
天枢: 读取屏幕 → 找到"设置"图标 → 点开 → 滚动找到"显示" → 点开
      → 点"亮度" → 拖动滑条 → 读取确认 → 报告完成
```

**二期验收**: 截图+视觉模型路线打通，能操作节点树读不到的自绘界面。

---

## 二、架构

```
┌─────────────── PC (天枢) ───────────────┐
│ AgentCore                                │
│   └─ phone skill (5 工具)                │
│        ├─ screen_state()  ← 感知         │
│        ├─ tap(x,y) / tap_text("显示")    │
│        ├─ input_text("...")              │
│        ├─ scroll(dir, amount)            │
│        └─ nav_back() / home()            │
│ gateway/server.py                        │
│   └─ /ws/phone 通道 (复用现有鉴权)        │
└──────────────┬───────────────────────────┘
               │ WebSocket (局域网 / adb reverse)
┌──────────────┴────────── 手机 (小米17) ───┐
│ TianshuAgentService (AccessibilityService)│
│   ├─ 感知: onAccessibilityEvent → 节点树  │
│   ├─ 动作: dispatchGesture / SET_TEXT     │
│   └─ 全局: back / home / recents          │
│ PhoneBridge (WS 客户端, 前台服务保活)      │
└───────────────────────────────────────────┘
```

**协议**: WS JSON-RPC 风格，与天枢 MCP 同族——手机本质上是一个"物理世界 MCP Server"。

```json
// PC → 手机 (请求)
{"id": 1, "method": "screen_state"}
{"id": 2, "method": "tap", "params": {"x": 540, "y": 1200}}
{"id": 3, "method": "tap_text", "params": {"text": "显示"}}   // 服务端先查节点树解坐标
{"id": 4, "method": "input_text", "params": {"text": "你好"}}
{"id": 5, "method": "scroll", "params": {"dir": "down", "amount": 3}}
{"id": 6, "method": "nav", "params": {"to": "back"}}

// 手机 → PC (响应)
{"id": 1, "result": {"package": "com.miui.home",
  "nodes": [{"text": "设置", "bounds": [80, 900, 200, 990], "clickable": true}, ...]}}
```

**节点树输出**: 扁平化列表（不做深层嵌套，LLM 好读），每节点 `text / desc / bounds / clickable / scrollable`，空文本节点跳过，上限 200 节点。

---

## 三、关键技术决策

### D1: 感知走节点树，不走截图（一期）
- 标准控件 app（设置/微信/支付宝/抖音/淘宝）覆盖率 ~80%+
- 纯文本，deepseek-v4-pro 现成可用，零多模态依赖
- 游戏/Canvas/部分银行 app 读不到 → 明确边界，二期视觉路线补
- 格式与 uiautomator dump 同族（用户曾用 Hermes+Termux 验证过该路线的天花板：adb 权限+慢+断连）

### D2: 动作走 AccessibilityService，不走 root/adb
- `dispatchGesture` 点按滑动（系统级，快于 input tap 起进程）
- `ACTION_SET_TEXT` 整段填入（不模拟逐键）
- `GLOBAL_ACTION_BACK/HOME/RECENTS` 全局导航
- 用户在系统设置手动授权一次，重启不失效（vs 无线 adb 重启失效+耗电+安全风险）

### D3: 通道走 WebSocket 局域网，不依赖腾讯云
- 开发期: `adb reverse tcp:8720 tcp:8720`，手机 localhost 直连 PC
- 使用期: 同 WiFi 直连 PC 局网 IP
- TS-014 服务器不可达完全阻塞不了这条线

### D4: 手机侧是"哑终端"，决策全在 PC
- 手机只做感知/动作/转发，零 LLM 依赖，APK 不膨胀
- 好处: 模型升级（换 qwen3/deepseek）不用动手机侧

### D5: 安全——三爻闸门全量适用
手机操作 = 高危工具类，天枢本行:
- **策略引擎**: 每类动作声明式管控（tap 白名单包名可配；input_text 禁止密码字段）
- **确认闸门**: 高危动作（支付/转账/删除/发送消息类 app 内操作）→ 手机上弹原生确认对话框，用户点允许才执行
- **审计**: 每次动作记录决策六问，屏幕快照（节点树文本）入审计
- **红线**: 锁屏状态下不操作；金融类 app 默认 deny（策略可放开）

---

## 四、小米 17 / 澎湃 OS 已知坑（提前备案）

| 坑 | 对策 |
|---|---|
| 无障碍授权被系统"自动撤销"（MIUI 系传统） | 设置→应用管理→天枢→省电策略=无限制；开发者选项锁定后台；文档写明 |
| 前台服务通知必须常驻（Android 10+） | 前台服务+常驻通知，用户可感知（这本身是安全特性） |
| 澎湃 OS 输入法接管 SET_TEXT 异常 | 备选: clipBoard + ACTION_PASTE |
| MIUI 光明山脉（部分机型禁 adb 安装） | 用正式签名 release 包；开发期 debug 包开"USB 安装" |

---

## 五、里程碑

### M1 — 通道+感知（1 个会话）
- [ ] Android: AccessibilityService 骨架 + 节点树 dump
- [ ] Android: WS 客户端 + adb reverse 连通 PC 8720
- [ ] PC: server.py `/ws/phone` 端点 + screen_state 工具
- [ ] 验收: CLI 里 `screen_state` 打印手机当前屏幕节点树

### M2 — 动作闭环（1 个会话）
- [ ] tap / input_text / scroll / nav 四动作工具
- [ ] tap_text（文本→坐标解析，服务端做）
- [ ] 验收: **"调亮度到最低"全自动** ← MVP 达成
- [ ] 策略引擎: 手机动作类策略 + 金融 app deny 默认

### M3 — 稳定性+安全（1 个会话）
- [ ] 高危动作手机端确认对话框
- [ ] 澎湃保活全套（前台服务+省电白名单）
- [ ] 动作间隔/重试/超时；屏幕变化前后对比
- [ ] 验收: 连续 20 个真实任务零崩溃；断连自动重连
- [ ] 真机长测：锁屏不操作、金融 app 拒绝

### M4 — 视觉路线（二期，独立立项 TS-019）
- [ ] 截图（AccessibilityService takeScreenshot / MediaProjection）
- [ ] 多模态接入（豆包视觉 / GLM-4V，闭合 TS-004）
- [ ] 节点树+截图混合感知
- [ ] 验收: 操作一个自绘界面 app（如某游戏菜单）

---

## 六、代码落点

| 位置 | 内容 |
|---|---|
| `F:\tianshu_dev\android\app\src\main\java\com\tianshu\app\` | `AgentService.kt`(无障碍) `PhoneBridge.kt`(WS) `ConfirmDialog.kt`(M3) |
| `F:\tianshu\src\tianshu\renyao\skills\phone.py` | 5 工具 skill（screen_state/tap/input_text/scroll/nav） |
| `F:\tianshu\src\tianshu\gateway\server.py` | `/ws/phone` WS 端点（复用 require_auth） |
| `F:\tianshu\config\policy.yaml` | 手机动作策略（高危 app deny 清单） |
| `F:\tianshu\tests\test_phone.py` | 协议解析/工具注册测试（mock WS） |

依赖: 无新增 pip 依赖（websockets 已有）；Android 侧 OkHttp WS（或 Ktor，工程已有 Gradle KTS）。

---

## 七、风险与放弃条件

- **无障碍被澎湃收紧**（安卓每次大版本都在限）: 若 17 上 dispatchGesture 被限且无白名单路径 → 降级 adb 路线（M1 感知层复用）
- **节点树质量差于预期**（某些 app 控件无 text/desc）: tap_text 失败率 >30% → 提前启动 M4 视觉
- **延迟**: 局域网 WS + 节点树路径预期 <500ms/步；若 >2s → 检查 dump 策略（事件驱动 vs 全量重dump）
