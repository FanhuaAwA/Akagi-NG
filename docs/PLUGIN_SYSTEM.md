# Akagi-NG 插件系统

插件系统用于把可选的本地扩展接入 Akagi-NG，而不让扩展逻辑侵入核心 Bridge。当前首个内置插件是“雀魂 MAX”。

## 用户入口

Dashboard 顶栏的拼图按钮会打开“扩展工具”面板。插件启用状态保存在操作系统用户数据目录的 `config/plugins.json`，插件自己的配置保存在同一目录下的 `config/plugins/<plugin-id>/`。升级时会从旧版便携程序根目录迁移现有配置。

需要 MITM 的插件启用后，后端会自动启动 Akagi-NG 自带的 MITM；从 Dashboard 启动的游戏窗口也会自动使用该代理。外部雀魂客户端仍需启用正确的证书信任和流量分流，必要时可使用 NG 已有的 mihomo TUN 功能。

## 扩展接口

插件继承 `akagi_ng.plugins.base.AkagiPlugin` 并声明 `PluginMetadata`。当前生命周期和流量钩子为：

- `on_enable()`：启用时初始化资源；抛出异常会回滚启用状态。
- `on_disable()`：禁用时释放插件自己的资源。
- `websocket_message(flow, bridge)`：平台 Bridge 解析前观察、丢弃、注入或改写 WebSocket 帧。
- `request(flow)`：平台 Bridge 处理前观察或改写 HTTP 请求。

在 `PluginManager` 的默认内置插件列表注册新实例后，插件会自动出现在 `/api/plugins` 和前端扩展工具面板中。插件异常由管理器捕获并记录到运行状态，不会让主 Bridge 或代理线程退出。

## 单代理流量顺序

需要查看或改写游戏流量的插件与 AI 共用 Akagi-NG 的一个 mitmproxy 监听器，不应为插件再启动第二个代理进程。WebSocket 帧在同一条连接中的固定处理顺序是：

```text
游戏 -> Akagi-NG MITM -> 已启用插件（如雀魂 MAX）-> 平台 Bridge / AI -> 游戏服务器
```

服务端返回的帧进入 Akagi-NG 后也先经过插件，再交给平台 Bridge / AI 解析。这样插件重写后的登录、角色和牌谱消息仍由 AI 在同一条连接、同一个请求 ID 上消费，避免两个串联代理在入站和出站方向顺序相反，也避免重复 TLS 解密、证书和端口冲突。

启用或禁用插件时，只有“是否需要 MITM”的总状态发生 `0 -> 1` 或 `1 -> 0` 变化才会启动或停止代理。总状态保持为 `1` 时只热切换插件，不重启 MITM，也不会主动断开现有游戏 WebSocket。修改 MITM 主机、端口或上游代理设置仍会强制重载监听器。

若总状态确实发生 `0 <-> 1`，已打开的 Electron 游戏窗口仍绑定旧代理路径。Dashboard 会关闭该窗口并提示重新启动游戏；新窗口随后使用正确路径。外部客户端需要由使用者同步调整分流。

## 雀魂 MAX 适配

适配层复用 `MajsoulMax-main` 的消息改写逻辑和 `max_data.yaml`，但不再启动第二个 mitmproxy 进程。它通过兼容层使用 NG 当前的动态 `liqi.json` 描述池，避免旧项目生成式 `liqi_pb2.py` 与新版 protobuf 的 API 冲突。

第三方源码与数据的原许可证位于 `assets/plugins/majsoul-max/LICENSE`。该插件只改变本机看到的内容，可能违反游戏服务条款并带来账号处罚风险。
