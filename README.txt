Akagi-NG Desktop
================

Akagi-NG 是本地运行的立直麻将分析与辅助工具。它不会修改游戏客户端；
Windows 版本提供可选的自动打牌，以及雀魂自动加入下一场功能。

Akagi-NG is a local desktop tool for Riichi Mahjong analysis and assistance.
It does not modify the game client. The Windows build provides optional
input-based autoplay and Mahjong Soul auto join.


快速开始 | Quick Start
----------------------

1. 解压下载的发行包 / Extract the downloaded release archive.
2. 双击运行 `Akagi-NG` / Double-click `Akagi-NG`.
3. 在 Dashboard 中启动游戏并按需打开 HUD / Launch the game from the
   Dashboard and open the HUD when needed.

Windows 版本主程序会申请管理员权限，以便直接管理内置 mihomo TUN。
The Windows desktop requests administrator rights so it can manage the bundled
mihomo TUN directly.


运行数据 | Runtime Data
-----------------------

配置、日志与插件保存在操作系统分配的 Electron 用户数据目录，而不是解压目录。
Windows 发行包通常使用：

`%APPDATA%\akagi-ng-desktop`

主要路径：

- `<userData>/config/settings.json`：主配置
- `<userData>/config/plugins.json`：插件启用状态
- `<userData>/config/plugins/<plugin-id>/`：各插件配置
- `<userData>/plugins/<plugin-id>/`：已安装插件代码
- `<userData>/logs/`：运行日志与 FlyA 状态重放诊断

Settings, logs, and plugins live in Electron's operating-system user-data
directory rather than the extracted application directory. The paths above
map to the main settings, plugin state/configuration, installed plugin code,
and runtime logs. Older portable-root settings are migrated on first launch.


插件 | Plugins
--------------

插件可在 Dashboard 的“扩展工具 / Extensions”中从独立仓库安装、更新或导入。
插件版本号与 Akagi-NG 桌面版本相互独立，更新插件不会覆盖插件配置。

一番街本地解锁的当前版本使用 Akagi-NG 已打包的 `ruamel.yaml`，无需安装
PyYAML。如果旧插件仍提示缺少 PyYAML，请先在“扩展工具”中更新并重启程序，
不要在发行包内部运行 `pip install`。

Plugins can be installed, updated, or imported from **Extensions**. Plugin
versions are independent from the Akagi-NG desktop version, and an update does
not overwrite per-plugin configuration. Current Riichi City Local Unlock
releases use the bundled `ruamel.yaml`; do not install PyYAML into the packaged
application.


更新 | Updating
---------------

Akagi-NG 以可解压的发行包提供。更新时关闭程序、替换旧程序目录并启动新版本。
正常情况下，`<userData>` 中的配置、日志与插件会保留。

Akagi-NG is distributed as an extractable release archive. Close the app,
replace the old application directory, and start the new version. Normal
configuration, logs, and plugins under `<userData>` are preserved.


注意 | Notes
------------

- 自动打牌、自动加入及解锁类插件可能违反游戏平台服务条款，请自行了解并承担风险。
- 关闭 TUN 或退出软件时，主进程会终止由其管理的 mihomo 进程。
- 出现启动、插件或 FlyA 问题时，请优先检查 `<userData>/logs/`。

- Autoplay, auto join, and unlock plugins may violate a game's Terms of
  Service. Understand and accept the risk before use.
- The managed mihomo process is terminated when TUN stops or Akagi-NG exits.
- Check `<userData>/logs/` first when startup, plugin, or FlyA problems occur.


GitHub: https://github.com/FanhuaAwA/Akagi-NG
Plugins: https://github.com/FanhuaAwA/Akagi-NG-Plugins
License: AGPL-3.0
