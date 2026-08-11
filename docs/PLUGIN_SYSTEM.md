# Akagi-NG 插件系统

Akagi-NG 只保留稳定的插件 API、安装器和流量钩子。具体插件独立存放在 [Akagi-NG-Plugins](https://github.com/FanhuaAwA/Akagi-NG-Plugins)，插件发布不再依赖 Akagi-NG 桌面程序发版。

## 用户入口与目录

Dashboard 顶栏的“扩展工具”面板支持两种安装方式：

- 从官方仓库的 `index.json` 安装或更新；
- 从本地导入 `.zip` / `.akagi-plugin.zip`。

目录约定：

- 插件代码：`<userData>/plugins/<plugin-id>/`
- 插件配置：`<userData>/config/plugins/<plugin-id>/`
- 启用状态：`<userData>/config/plugins.json`

代码与配置分离，更新插件包不会覆盖用户设置。同一插件 ID 只能对应一个安装目录；本地重复导入会返回冲突，远程更新会在插件禁用时原子替换旧版本。

## 版本与 Python 依赖

插件版本号与 Akagi-NG 桌面版本相互独立。`index.json` 中同一插件的版本发生变化时，管理器只会将其标记为可更新；不会改变桌面程序版本，也不会覆盖插件配置。当前清单格式不自动判断插件 API 兼容性，因此插件发布者必须在发布说明中注明所需的最低 Akagi-NG 版本，并在隔离的发行包运行时中测试。

发行包不提供通用顶层 `yaml` / PyYAML 模块。插件不得依赖开发机上偶然安装的 PyYAML；应明确使用 Akagi-NG 已打包的 `ruamel.yaml`，或在插件包中使用不会污染全局模块名的私有依赖。一番街本地解锁 `v0.1.8` 已改用 `ruamel.yaml`，用户无需运行 `pip install`。

## 安全边界

插件是本机 Python 代码，只应安装可信来源。安装器会执行以下验证：

- 插件 ID、版本、入口文件和清单 schema；
- ZIP 路径穿越、绝对路径、符号链接、加密 ZIP、文件数和解压大小限制；
- 远程包 SHA-256；
- 清单与运行时 `PluginMetadata` 的 ID、版本、互斥声明一致性；
- 重复 ID 检测和单目录原子安装。

## 包清单

ZIP 根目录必须包含 `akagi-plugin.json`：

```json
{
  "schema_version": 1,
  "id": "example-plugin",
  "version": "v1.0.0",
  "entrypoint": "plugin.py:ExamplePlugin",
  "conflicts": []
}
```

入口必须是可调用对象，并返回 `akagi_ng.plugins.base.AkagiPlugin` 实例。

## 生命周期和流量钩子

插件可实现：

- `on_enable()` / `on_disable()`
- `request(flow)` / `response(flow)`
- `websocket_start(flow, bridge)`
- `websocket_message(flow, bridge)`
- `websocket_end(flow, bridge)`

所有插件与 AI 共用 Akagi-NG 的单个 mitmproxy，不应启动第二个代理。处理顺序为：

```text
游戏 -> Akagi-NG MITM -> 已启用插件 -> 平台 Bridge / AI -> 游戏服务端
```

插件异常会被管理器捕获并显示为运行错误，不会让主 Bridge 线程退出。

## 互斥规则

插件通过 `PluginMetadata.conflicts` 和包清单 `conflicts` 声明冲突。后端会双向检查冲突，而不是依赖前端状态。

官方的 `majsoul-max` 与 `riichicity-local-unlock` 同时只能启用一个。即使手工修改 `plugins.json`，启动恢复阶段也只会启用第一个合法插件，并拒绝冲突项。

## HTTP API

- `GET /api/plugins`：已安装插件及运行状态
- `GET /api/plugins/catalog`：远程仓库插件列表
- `POST /api/plugins/install/remote`：按插件 ID 安装/更新远程包
- `POST /api/plugins/install/local`：上传并导入本地包
- `POST /api/plugins/{plugin-id}`：启用或禁用插件

默认目录地址可通过 `AKAGI_PLUGIN_CATALOG_URL` 环境变量覆盖，但必须是无内嵌凭据的 HTTPS URL。
