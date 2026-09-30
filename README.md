# 鸣潮 UHD 资源工具 1.0.0

适用于本次核验的 Windows 国服 / WeGame **3.7.0 基础客户端**。为已有 HD 版添加官方 UHD 基础资源，支持暂停续传、回退和缓存清理。

**工具已完成模拟测试；尚未在真实游戏上验证 WeGame 登录、UHD 画质或热更新兼容性。**

[下载 Windows 便携包](https://github.com/TianXyousa/wuwa-uhd-tool/releases/tag/v1.0.0) · [下载介绍视频](https://github.com/TianXyousa/wuwa-uhd-tool/releases/download/v1.0.0/WuwaUHDTool-Intro-1080p.mp4) · [验证记录](VALIDATION.md)

![工具介绍：官方资源下载、校验、可回退；实机效果待验证](media/assets/cover.jpg)

介绍视频约 2 分 54 秒，含中文 AI 配音和字幕。全部操作画面使用临时模拟目录，不代表真实 WeGame / UHD 运行测试。视频、字幕及封面可在同一 Release 下载；制作方法见 [media/README.md](media/README.md)。仓库不包含游戏程序或资源包。

## 使用

1. 解压便携包，双击 `WuwaUHDTool.exe`。无需安装 Python，无需管理员权限。
2. 选择游戏根目录：应包含 `Client`、`Engine` 和 `Wuthering Waves.exe`。默认尝试识别本机 WeGame 安装。
3. 点击 **只读检查**。核对游戏核心程序的 MD5、官方当前资源索引和可用空间。此步骤不下载游戏包、不创建缓存、不修改游戏。
4. 退出游戏及库洛更新程序，点击 **下载 / 恢复 UHD**。首次约需 **66,036,274,508 字节（66.04 GB / 61.50 GiB）**，另预留至少 2 GiB。后续热更新和 UHD 视频不包含在此数字中。
5. 全部 100 个文件通过完整 MD5 后，工具才将 UHD 目录加入游戏。工具不会自动启动游戏。
6. 可单独点击 **以 UHD 启动（待验证）**。该按钮通过原 `Wuthering Waves.exe -krqlv=uhd` 请求启动；保持 WeGame 登录。此启动路径尚未验证真实游戏兼容性。如登录失败，请退出并回退，不要更改渠道配置。

## 回退

退出游戏后，点击 **回退到 HD**。仅把本工具添加的 `Client/Content/UHD` 移回缓存，然后按原来的 WeGame 方式启动游戏即可。

- 原 HD 资源、核心程序、SDK DLL、渠道配置、启动快捷方式均未被替换，不需要覆盖恢复。
- 回退不要求联网；即使核心游戏后来更新，仍允许移动本工具拥有的 UHD 目录。
- 回退保留 UHD 下载缓存，**不会自动释放约 61.50 GiB 空间**。恢复时会重新校验并复用缓存。
- 要释放空间，回退后点击 **清理已回退缓存**。只删除已登记的工具缓存文件，不递归清理游戏目录。
- 工具不能撤销游戏自行生成的热更新、视频、图形设置或存档。它们不在资源回退范围内。

## 文件边界

游戏目录中只会新增：

```text
Client/Content/UHD/
  50 个 .pak + 50 个 .sig
  .wuwa-uhd-owner.json    工具所有权记录
```

下载断点、回退资源和事务状态存放在**游戏的同级目录**：`.wuwa-uhd-<路径标识>/`。点击“打开缓存目录”可以查看。不要手动修改 `state.json` 或所有权记录，也不要在此目录保存无关文件。

完整下载先保存在缓存，再用同盘目录重命名加入游戏，不会产生双份 UHD 占用。现有 HD 保留。工具不读取登录令牌，不自动更新 WeGame，不修改注册表或官方快捷方式。

## 保护机制与限制

- 官方 HTTPS 下载域名白名单；仅允许已核验的 3.7.0 UHD 清单。
- 三个核心程序的大小和 MD5 必须匹配；官方版本或清单变化则停止安装，避免混装版本。
- 只允许 UHD 目录中的 `.pak` / `.sig` 文件；拒绝路径越界、重复名称、符号链接、目录联接及硬链接。
- 文件完整 MD5 校验、断点续传、下载长度与 HTTP Range 检查、三条官方 CDN 自动重试。
- 检测游戏和相关更新进程；写入前再次核对核心和渠道文件，运行中不安装或回退。
- 每个游戏独占操作锁；安装 / 回退记录采用先记事务再移动目录，支持中断后恢复。
- 已有的未知 UHD 目录不会被接管、覆盖或删除。工具发现未知缓存文件也会停止。
- 不绕过反作弊或修改游戏代码，不保证 WeGame 的 UHD 入口已获官方支持。官方仍处于资源分级测试阶段。
- 操作期间请不要手动启动游戏、移动目录或让另一更新工具同时处理该游戏。进程检查无法与外部程序建立共同锁。
- 游戏升级到不匹配的版本后，本工具会停止安装；需更新工具重新核验。旧版本的回退功能仍可用。
- 程序尚未做代码签名。

## 来源

- [官方资源分级说明](https://wutheringwaves.kurogames.com/zh-tw/main/news/detail/5480)
- [官方新版游戏索引](https://prod-volcdn-gamestarter.kurogame.xyz/launcher/game/10003_oLNgHF1CESo51DGHN2odtp40e3oI1HfZ/G152/official/index.json)
- [UHD 3.7.0 文件清单](https://pcdownload-huoshan.aki-game.com/launcher/game/G152/10003/3.7.0/983fc4b836c240319aa3a0fc2c9f8514/uhd/indexFile.json)

内置清单 MD5：`bb8104efa17446c5cac8f35f1593fe59`。每个游戏文件另有独立 MD5；工具会核对实际下载内容，而非仅检查 HTTP 状态码。

## 开发与验证

运行时仅依赖 Python 标准库。构建依赖安装到项目独立的 `.venv`，不改动系统 Python 环境。

```powershell
python main.py --self-test --report test-report.json
python main.py --ui-smoke-test --report ui-report.json
python main.py --inspect '游戏根目录' --offline --report inspect-report.json
.\build.ps1
```

`--self-test` 在新建临时目录中模拟下载、续传、异常和回退，不访问真实游戏或网络。正常界面打开不会自动检测、下载或运行游戏。

便携包为单文件 Windows GUI 程序。命令行验证使用 `--report` 保存 JSON 结果，因为窗口版 EXE 不附带控制台。源代码包不包含虚拟环境、游戏文件或下载缓存。
