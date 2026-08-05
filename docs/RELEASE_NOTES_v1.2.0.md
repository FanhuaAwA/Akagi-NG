# Akagi-NG v1.2.0 Release Notes

## 简体中文

### 本次更新重点

- **推理状态可见**：AkagiOT Legacy、OT3 和 FlyA 的请求、成功、失败状态会通过 SSE 实时显示在 Dashboard 顶栏与 HUD 中，并附带提供方、模型和耗时信息。新连接也能收到最近一次状态，不再需要根据界面是否更新来猜测 AI 是否正在工作。
- **雀魂 MAX 与 AI 可同时工作**：皮肤解锁不再启动或依赖第二个代理。游戏流量在 Akagi-NG 的同一个 MITM 连接中固定按“雀魂 MAX 插件 -> 平台 Bridge / AI”处理，服务端返回帧也遵循相同的显式顺序。
- **插件入口**：Dashboard 新增扩展工具面板，可查看插件能力、运行状态、MITM 要求和风险提示，并持久化启用状态。当前内置插件为“雀魂 MAX”。
- **启动与退出提速**：删除启动阶段的全量资源清单、签名、遍历和 SHA-256 校验，只保留原生库与模型目录的毫秒级可用性检查。Dashboard 不再等待 Python 后端，HUD 改为首次使用时创建，后端与 mihomo 的强制退出上限也由 5 秒缩短为 2 秒。

### 皮肤解锁 / AI 串联修复

v1.2.0 使用一个 mitmproxy 监听器完成插件改写与 AI 解析：

```text
游戏或 mihomo TUN
  -> Akagi-NG MITM
  -> 雀魂 MAX（可选、先改写）
  -> 雀魂平台 Bridge / AI（再解析）
  -> 游戏服务器
```

- Liqi 响应现在按 protobuf 字段编号解析，不再假设空的 `method_name` 一定被序列化。雀魂 MAX 改写 `.lq.FastTest.authGame` 等响应后，即使 protobuf 省略空字段，AI 仍能恢复座位、模式和开局状态。
- 手动下载的 Liqi 协议会先完整构建描述符并验证，再进行原子替换；无效更新会保留此前可用的覆盖文件。
- 开关插件时，只在“是否需要 MITM”的总状态发生 `0 -> 1` 或 `1 -> 0` 变化时启动或停止代理。AI 或其他 MITM 功能已在使用代理时，热切换雀魂 MAX 不会主动断开现有游戏 WebSocket。
- 仅由插件触发的 MITM 需求现在也会被内置 mihomo TUN 正确识别。若总需求发生 `0 <-> 1` 且内置游戏窗口已经打开，软件会关闭旧窗口并提示重新启动游戏，避免旧连接继续走直连或失效端口。
- 修改 MITM 主机、端口或上游代理仍会进行受控重启。代理新增就绪/失败握手，端口占用或绑定失败会明确返回，而不会把“线程已创建”误报为“代理可用”。
- Electron 游戏会话在关闭 MITM 后显式恢复直连；监听地址为 `0.0.0.0` 或 `::` 时，客户端连接地址会规范为 `127.0.0.1`。

### 推理状态

- 状态阶段：正在请求、成功、失败。
- 覆盖路径：AkagiOT Legacy、本地逻辑触发的 OT3 在线请求、FlyA 在线决策。
- 展示位置：Dashboard 顶栏与 HUD。
- 展示信息：提供方、模型（可用时）、实时/最终耗时和请求标识。
- 在线推理原有的绝对截止时间、容量限制、取消与本地回退语义保持不变；过期工作不会覆盖新一局的状态。

### 性能与生命周期

- 启动阶段不再遍历或哈希整个安装目录，只检查两个必需原生库以及是否存在本地模型文件。
- Dashboard 使用本地启动快照直接创建；依赖后端的按钮会在服务就绪前保持禁用，并在主界面显示启动进度或错误。
- 后端启动失败会立即结束就绪等待，轮询从较短间隔开始并退避，避免无意义的固定超时。
- HUD 不再随 Dashboard 预创建，减少普通启动时的第二个渲染器和 SSE 连接。
- 启动、退出过渡缩短为约 300 ms；退出动画与清理并行进行。
- Electron 的退出入口复用同一个幂等清理任务，并继续保持 `mihomo -> 后端` 的停机顺序。后端会主动唤醒 SSE 客户端并进行有界关闭；后端和 mihomo 的强制退出上限均由 5 秒缩短为 2 秒。
- 设置、密钥、日志、插件状态/配置和手动下载的 Liqi 协议现写入操作系统用户数据目录；首次运行会迁移旧便携目录中的配置。Linux AppImage 与 macOS `.app` 不再尝试修改只读包体。

### 发布前验证范围

本版本的发布门禁覆盖以下范围；GitHub Release 页面中的 Actions 结果是正式构建的最终依据：

- Python：推理状态生命周期、SSE 缓存/重放、在线推理取消与截止时间、插件持久化和异常隔离、Liqi 字段解析、插件到 AI 的端到端顺序、MITM 就绪/失败与热切换生命周期。
- 前端：TypeScript 类型检查、目标 ESLint 检查、生产构建，以及 Dashboard/HUD 推理状态消费。
- Electron：启动顺序、快速资源可用性检查、后端快速失败、延迟创建 HUD、代理直连复位、退出单例化和权限边界回归。
- 发布链：`vX.Y.Z` 标签必须与根 `package.json` 一致；同步脚本同时校验后端、前端、Electron 和 `package-lock.json` 的根/工作区版本。
- 发行产物：Windows、Linux、macOS 三个平台构建全部成功后才一次性创建公开 Release，避免残缺发布。

### 重要风险边界

- 雀魂 MAX **默认不启用**，只改变本机客户端看到的内容，不会授予服务器端物品、权益或所有权。
- 启用皮肤解锁可能违反游戏服务条款，并可能带来账号限制或处罚。启用即表示使用者理解并自行承担风险。
- 外部游戏客户端仍需正确安装/信任本机 mitmproxy CA，并把目标流量路由到 Akagi-NG MITM；需要时可使用内置 mihomo TUN。不要为雀魂 MAX 再串联第二个 mitmproxy。
- 插件异常会被隔离并记录，但第三方改写逻辑仍可能随游戏协议更新失效。遇到异常时先禁用插件，以区分插件问题、代理问题和 AI 服务问题。
- 雀魂 MAX 适配代码与数据的第三方许可证随包保存在 `assets/plugins/majsoul-max/LICENSE`。
- 当前仓库没有 Windows Authenticode 或 Apple Developer ID/公证凭据，因此 Windows/macOS 产物**没有可信的操作系统发布者签名**，可能触发 SmartScreen 或 Gatekeeper。

---

## English

### Highlights

- **Visible inference lifecycle**: request, success, and failure states for AkagiOT Legacy, OT3, and FlyA are streamed over SSE to both the Dashboard header and HUD, including provider, model, and elapsed-time context.
- **MajsoulMax and AI now work together**: skin rewriting and AI parsing share one Akagi-NG MITM connection. Frames are processed deterministically by the MajsoulMax plugin first and by the platform bridge/AI second in both directions.
- **Plugin UI**: the Dashboard extension panel exposes plugin capabilities, runtime health, MITM requirements, risk notices, and persisted enablement. MajsoulMax is the first built-in plugin.
- **Faster startup and shutdown**: the full resource manifest, signature, inventory, and SHA-256 startup pass are removed. Startup performs only bounded native-library/model availability checks, the Dashboard no longer waits for Python, HUD creation is lazy, and backend/mihomo forced-shutdown ceilings are reduced from five to two seconds.

### MajsoulMax / AI pipeline fix

v1.2.0 uses one mitmproxy listener for both rewriting and AI consumption:

```text
Game or mihomo TUN
  -> Akagi-NG MITM
  -> MajsoulMax (optional rewrite first)
  -> Mahjong platform bridge / AI (parse second)
  -> Game server
```

- Liqi responses are decoded by protobuf field number instead of assuming that an empty `method_name` field is serialized. AI state recovery therefore continues after MajsoulMax rewrites responses such as `.lq.FastTest.authGame`.
- Manually downloaded Liqi protocols are fully descriptor-validated before an atomic replacement, so malformed updates leave the previous working override intact.
- Plugin toggles only start or stop MITM when the aggregate MITM requirement changes between zero and one. Toggling MajsoulMax while AI already requires MITM does not intentionally disconnect the active game WebSocket.
- Plugin-only MITM demand is also recognized by the bundled mihomo TUN. If aggregate demand changes between zero and one while the embedded game is open, the stale window is closed with a relaunch notice so it cannot remain on a direct or dead proxy route.
- Host, port, and upstream changes still trigger a controlled restart. A new readiness/failure handshake reports bind errors instead of treating a created thread as a usable proxy.
- Electron game sessions explicitly return to direct mode when MITM is disabled, and wildcard listen addresses are normalized to loopback for client connections.

### Inference status

- Phases: requesting, success, and error.
- Paths: AkagiOT Legacy, OT3 online inference, and FlyA online decisions.
- Surfaces: Dashboard header and HUD.
- Context: provider, model when available, live/final elapsed time, and request identifier.
- Existing absolute deadlines, bounded capacity, cancellation, and local fallback semantics remain intact; stale work cannot overwrite a newer game generation.

### Performance and lifecycle

- Startup no longer enumerates or hashes the packaged tree; it checks only the two required native libraries and whether a local model file is available.
- The Dashboard renders from a local startup snapshot. Backend-dependent controls remain disabled until the service is ready, and startup progress or failures are shown in the main interface.
- Backend startup failures terminate readiness waits immediately, while polling starts quickly and backs off.
- The HUD is created only on first use, avoiding a second renderer and SSE connection during ordinary Dashboard startup.
- Startup and exit transitions are approximately 300 ms, with exit animation and cleanup running concurrently.
- All Electron quit paths share one idempotent cleanup promise and retain `mihomo -> backend` shutdown ordering. Backend SSE clients are actively woken for a bounded graceful close, and backend/mihomo forced-shutdown ceilings are reduced from five to two seconds.
- Settings, secrets, logs, plugin state/configuration, and downloaded Liqi overrides now live in the OS user-data directory, with first-run migration from the old portable-root configuration. Linux AppImage and macOS `.app` bundles are no longer treated as writable storage.

### Release verification scope

The release gates cover the following areas; the Actions status attached to the GitHub Release is authoritative for the published artifacts:

- Python inference telemetry and SSE replay; online deadline/cancellation behavior; plugin persistence and hook isolation; Liqi field compatibility; end-to-end plugin-to-AI ordering; MITM readiness, failure, and hot-toggle lifecycle.
- Frontend TypeScript checks, targeted ESLint checks, production build, and inference-status consumption in Dashboard/HUD.
- Electron startup ordering, fast resource availability, backend fast failure, lazy HUD creation, proxy reset, single-path shutdown, and privilege-boundary regressions.
- Release metadata: a `vX.Y.Z` tag must match root `package.json`; the synchronization script verifies backend, frontend, Electron, and root/workspace lockfile versions.
- Release artifacts: the public Release is created only after every Windows, Linux, and macOS build succeeds.

### Important risk boundaries

- MajsoulMax is **disabled by default**. It only changes content rendered by the local client and does not grant server-side items, entitlement, or ownership.
- Skin unlocking may violate the game's terms of service and may result in account restriction or punishment. Users enable it at their own risk.
- External game clients still need to trust the local mitmproxy CA and route the target traffic through Akagi-NG MITM, optionally via the bundled mihomo TUN. Do not add a second mitmproxy for MajsoulMax.
- Plugin failures are isolated and reported, but third-party rewriting logic can still break after protocol changes. Disable the plugin first when separating plugin, proxy, and AI-service failures.
- The bundled third-party license is available at `assets/plugins/majsoul-max/LICENSE`.
- This repository currently has no Windows Authenticode or Apple Developer ID/notarization credentials. Windows/macOS artifacts therefore **do not carry a trusted OS publisher signature** and may trigger SmartScreen or Gatekeeper.

**Full Changelog**: https://github.com/FanhuaAwA/Akagi-NG/compare/v1.1.2...v1.2.0
