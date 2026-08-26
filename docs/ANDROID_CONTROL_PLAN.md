# 天枢手机助手 — 技术方案 (TS-018 / AND-002)

> 创建: 2026-08-25 | 修订: 2026-08-26（v2：架构反转，遥控器→全内置）
> 目标: 手机上的智能助手产品——对话即操作手机（小米 17 · 澎湃 OS）
> 状态: M1 遥控器版代码已产出（作废，详见 §七）；v2 开工前

---

## 〇、v2 修订记录（为什么推翻 v1）

**v1（遥控器模式）**: 手机哑终端 ←WS→ PC 天枢决策。
**用户定调**: 不要遥控器，要"像 DeepSeek 手机端那样打开即用"的独立助手，
大模型走 API（DeepSeek 等），操作能力全在手机本地。

架构反转的本质: **决策从 PC 搬进手机**。v1 已写的 AgentService.kt
（无障碍感知+动作）全部保留复用，作废的只是 PhoneBridge 的 WS 远程通道。

**参考资产（F:\hermes\deepdone\）**——用户此前写的手机 Agent 项目:
- `cli_proto/` 956 行 Python **可跑的 Agent 循环**: agent.py(消息→LLM→工具
  →回喂, max_iterations=20) + tools.py(ApprovalLevel 三级闸门
  AUTO/SUGGEST/REQUIRED + ToolResult 结构) + plan/agent/yolo 三模式
- `android/` Compose 壳 + **Chaquopy 嵌 Python 运行时**先例 + DI 容器
  （屏幕层空置，仅 284 行骨架）

---

## 一、定位与验收

**一句话**: 天枢手机助手 = 对话入口 + 手机操作 + 三爻闸门，全部装进一个 APK；
大模型经 API 接入（DeepSeek 主力，可配其他），不依赖任何电脑/服务器。

**MVP 验收（手机上独立完成，飞行模式下仅 API 断网不可用）**:
```
用户(在 app 里): 把屏幕亮度调到最低
助手: 读屏 → 开设置 → 显示 → 亮度 → 拖滑条 → 读屏确认 → 报告"已调到最低"
```

**终态验收**: 微信发消息给某人 / 打开抖音搜某话题 / 设置里开关任一系统项，
全程 app 内对话驱动，高危操作有确认。

---

## 二、架构（v2 全内置）

```
┌────────────── APK (com.tianshu.app) ──────────────────┐
│ ChatUI (Compose, 对话界面+模式切换+确认卡片)             │
│    │                                                   │
│    ▼                                                   │
│ AgentLoop ────────────── 决策核心                       │
│  (Kotlin 或 Python-in-Chaquopy, §三定夺)                │
│    │  tool_call (JSON)                                 │
│    ▼                                                   │
│ ToolRegistry                                           │
│  ├─ PhoneTools: screen_state/tap/tap_text/             │
│  │   input_text/scroll/nav  → AgentService (无障碍)    │
│  ├─ LlmClient: DS API (streaming) ──► api.deepseek.com│
│  └─ (后续) file/memory 工具                             │
│    │                                                   │
│    ▼                                                   │
│ TrigramGate (天爻闸门)                                  │
│  ApprovalLevel: AUTO(读屏) / SUGGEST(点按) /           │
│  REQUIRED(输入文本/高危app) → 确认卡片                   │
│    │                                                   │
│    ▼                                                   │
│ MemoryStore (Room: 会话历史+事实记忆)                    │
└─────────────────────────────────────────────────────────┘
```

**不变量**（从天枢/DeepDone 继承的魂）:
- 三级闸门语义 = DeepDone ApprovalLevel（即天枢三爻的手机形态）
- plan/agent/yolo 三模式 = 天枢 normal/auto/plan
- max_iterations=20 防工具循环失控
- 审计: 每次动作入库（Room），六问字段精简为 手机版三问（做了什么/结果/何时）

---

## 三、关键技术决策（v2）

### D1: AgentLoop 用 Kotlin 还是 Python(Chaquopy)？—— MC0 实测定夺
| | 纯 Kotlin | Chaquopy 嵌 Python |
|---|---|---|
| 代码量 | ~1300 行（重写 agent.py 逻辑） | 移植 cli_proto ~900 行 + 30 行桥 |
| APK 体积 | 基线 | +40MB（Python 运行时+依赖） |
| 工具桥 | 天然（同进程） | Chaquopy API 跨语言调用 AgentService |
| 风险 | 无 | Chaquopy 收购后 license/兼容性漂移 |
| 维护 | 两套语言（PC Python/手机 Kotlin） | 手机也用 Python，与天枢同族 |

**判定标准**: MC0 构建 deepdone android 工程——Chaquopy 在当前
AGP/Gradle 下能跑通 hello → 选 Python；跑不通/license 阻碍 → Kotlin。
（倾向 Python：Agent 循环逻辑与天枢同语言，未来 PC/手机共享 skill 代码。）

### D2: 感知=节点树（无障碍），动作=dispatchGesture/SET_TEXT
同 v1。标准控件 app 覆盖 ~80%；自绘界面盲区留给 M4 视觉路线。

### D3: LLM 接入 = 纯 API 客户端，手机不做 Provider 注册表
只接 OpenAI 兼容 chat/completions（DS/后续 GLM/qwen 同协议），
key 存 Android Keystore 加密的 SharedPreferences。
手机不是"多 Provider 路由"场景——一个可配置的 API 端点+key 足够。

### D4: 安全=闸门全本地
- 高危包名清单（金融/支付）内置 REQUIRED + 可配 deny
- 锁屏状态拒绝动作（AgentService isScreenLocked 检查 KeyguardManager）
- 输入类动作一律 REQUIRED（确认卡片显示将输入的文本）
- 每动作审计入 Room

### D5: v1 成果的处置
- AgentService.kt: **原样复用**（感知+动作+tap_text 查找逻辑已完备）
- PhoneBridge.kt: WS 远程通道**作废**，改为（若 Python 路线）Chaquopy 桥
  或（若 Kotlin 路线）直接删；/ws/phone PC 端点保留作调试后门（可选）

---

## 四、小米 17 / 澎湃 OS 已知坑（同 v1，保留）

| 坑 | 对策 |
|---|---|
| 无障碍授权被"自动撤销" | 省电策略=无限制+后台锁定；文档写明 |
| 前台服务通知常驻 | 无障碍服务本身即常驻，不再需要额外前台服务（v1 PhoneBridge 才需要） |
| SET_TEXT 被输入法接管 | 备选 clipBoard+PASTE |
| 光明山脉禁 adb 安装 | release 签名包；开发期开"USB 安装" |

---

## 五、里程碑（v2）

### MC0 — 技术验证（半个会话，先于一切）
- [ ] deepdone android 工程在当前工具链下构建
- [ ] Chaquopy hello 跑通（Python↔Kotlin 互调）
- [ ] **判定 D1**: Kotlin or Python，记入本节
- 验收: 构建日志+判定结论

### MC1 — 最小对话循环（1 会话）
- [ ] AgentLoop 移植/重写（消息→DS API→tool_call 解析→执行→回喂）
- [ ] ChatUI 最小版（输入框+消息列表+流式输出）
- [ ] LlmClient（OkHttp SSE 流式；key 设置页，Keystore 加密）
- [ ] 验收: app 内与 DS 对话正常（无工具）

### MC2 — 工具闭环（1 会话）
- [ ] PhoneTools 5 工具接 AgentService
- [ ] 节点树→LLM 紧凑文本（复用 v1 phone.py 格式化逻辑）
- [ ] 三级闸门（AUTO 读屏/SUGGEST 点按/REQUIRED 输入）
- [ ] **验收: "调亮度到最低" app 内全自动** ← MVP 达成
- [ ] max_iterations 防失控 + 锁屏拒绝

### MC3 — 产品化（1 会话）
- [ ] 确认卡片 UI（REQUIRED 级动作弹卡片，显示将执行的操作）
- [ ] 高危包名清单 + deny 默认（金融类）
- [ ] Room: 会话历史+动作审计
- [ ] 澎湃保活全套
- [ ] 验收: 连续 20 真实任务零崩溃；锁屏不动作；金融 app 拒绝

### MC4 — 增强（二期，按需）
- 截图+视觉（触发 TS-019 多模态）/ 语音输入 / 桌面小组件 /
  记忆系统（对标天枢 L2-L5 精简）/ 多模型 key 可配

---

## 六、代码落点

| 位置 | 内容 |
|---|---|
| `F:\tianshu_dev\android\`（继续用此工程） | v2 全部手机侧代码 |
| `app/src/main/java/com/tianshu/app/AgentService.kt` | ✅ 已有，复用 |
| `app/src/main/java/com/tianshu/app/AgentLoop.kt` 或 `app/src/main/python/agent.py` | MC0 定 |
| `app/src/main/java/com/tianshu/app/PhoneTools.kt` | 工具注册（Kotlin 路线） |
| `app/src/main/java/com/tianshu/app/ChatActivity.kt` | 对话 UI |
| `F:\hermes\deepdone\cli_proto\` | 只读参考：agent.py/tools.py 移植源 |
| `F:\tianshu\src\tianshu\gateway\phone_ws.py` + `/ws/phone` | 保留为调试后门（可选） |

---

## 七、v1（遥控器模式）处置备案

v1 M1 代码 6df2923 已产出: AgentService.kt + PhoneBridge.kt(WS) +
PC 侧 phone_ws.py + phone skill + 10 测试（316 全绿）。
**架构反转后**:
- AgentService.kt 无改动复用
- PhoneBridge WS 逻辑不再需要（保活/重连部分对 v2 无用，Chaquopy 桥或进程内直调取代）
- PC 侧 /ws/phone + phone skill 保留（真机调试时直接看节点树有用，维护成本≈0）
- 真机联调（原 Task#4）取消，由 MC1/MC2 的 app 内验收取代
