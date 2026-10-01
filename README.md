# 鸣潮 UHD 资源工具 1.1.0

适用于本次核验的 Windows 国服 / WeGame **3.7.0 基础客户端**。为已有 HD 版添加官方 UHD 基础资源，支持暂停续传、回退、缓存清理，以及可选的 **HD 备份、移除与恢复**。

**资源添加完成后，请在 WeGame 的鸣潮启动参数中追加 `-krqlv=uhd`，再从 WeGame 启动。** 用户已于 2026-10-01 在一台电脑上验证 UHD 启动、极致画质切换，以及移除 HD 基础包后正常运行。其他设备和长期兼容性尚未验证，详见 [验证记录](VALIDATION.md)。

[下载 Windows EXE](https://github.com/TianXyousa/wuwa-uhd-tool/releases/download/v1.1.0/WuwaUHDTool.exe) · [发布说明](https://github.com/TianXyousa/wuwa-uhd-tool/releases/tag/v1.1.0) · [下载源码](https://github.com/TianXyousa/wuwa-uhd-tool/archive/refs/heads/main.zip) · [验证记录](VALIDATION.md)

Windows 单文件程序通过 GitHub Release 提供，无需安装 Python。仓库保留源码、构建脚本、测试与必要说明；Release 只提供 EXE，不包含视频或游戏资源。

## 添加 UHD

1. 下载并双击 `WuwaUHDTool.exe`，通常无需管理员权限。源码用户可使用 Python 3.12 运行 `python main.py`。
2. 选择游戏根目录：应包含 `Client`、`Engine` 和 `Wuthering Waves.exe`。默认尝试识别本机 WeGame 安装，也可手动选择。
3. 点击 **只读检查**，核对核心程序 MD5、官方当前资源索引与空间。此步骤不下载游戏包、不创建缓存、不修改游戏。也支持已经移除 HD、仍保留本工具 UHD 安装记录的客户端。
4. 退出游戏及更新程序，点击 **下载 / 恢复 UHD**。首次约需 **66,036,274,508 字节（66.04 GB / 61.50 GiB）**，另预留至少 2 GiB。
5. 全部 100 个文件通过完整 MD5 校验后，工具才将 UHD 目录加入游戏。此操作保留原 HD，不自动启动游戏。
6. 点击 **WeGame 启动说明**。在 **WeGame 中鸣潮的启动参数设置**里保留已有参数，追加并保存：

   ```text
   -krqlv=uhd
   ```

7. 从 **WeGame 启动鸣潮**，完成游戏内资源更新后选择极致画质。本次用户反馈启动后另需热更新约 **7.9 GB 素材**，不包含在上述 66.04 GB 基础包中，实际大小以游戏内提示为准。

工具不会自动修改 WeGame 设置。v1.0.0 的直接启动入口曾出现用户反馈的“参数异常”，现已改为 WeGame 启动说明；该报错的内部原因尚未确认。

## 可选：备份并移除 HD

**先确认 UHD 能正常游玩，再退出游戏与更新程序。** 此功能仅处理 `Client/Content/HD` 中的 100 个基础包文件，约 **45,713,445,823 字节（45.71 GB / 42.57 GiB）**。

1. 点击 **备份并移除 HD**，选择游戏与工具缓存之外的本地备份位置。
2. 优先选另一个磁盘。备份盘需容纳完整 HD 并额外预留 2 GiB；**备份在游戏同一磁盘时，不会节省该磁盘的总空间**。
3. 工具完整校验已安装的 UHD，复制 HD，并以官方 MD5 与独立 SHA256 核对原件和备份。全部通过后，才移除游戏内 HD。
4. 保留生成的整个备份批次目录，其中包含 `HD/` 和 `manifest-sha256.json`。工具不会自动删除这份外部备份。
5. 继续保留 WeGame 的 `-krqlv=uhd` 参数。**移除 HD 后，必须先恢复 HD 备份，才能回退。**

HD / UHD 视频、游戏热更新、配置与存档均不在此次移除范围内。移除只适用于本工具管理的 UHD 安装，并要求版本与资源清单匹配；遇到未知文件、资源损坏、链接路径或运行中的游戏会停止。

完整校验会多次读取大容量资源，请等待操作完成。初次备份复制中断时，原 HD 保留；重新执行会创建新批次，未完成的旧批次不会自动清理。移除或恢复进入事务阶段后中断，再次点击同一操作可继续；恢复时需选择原来的同一份备份。存在未完成的 HD 事务时，其他资源操作会被拦截。请保留备份与工具缓存，不要手动修改操作记录。

## 恢复 HD 与回退

1. 如果移除过 HD，点击 **恢复 HD 备份**，选择包含 `HD/` 与 `manifest-sha256.json` 的备份批次目录。游戏盘需约 45.71 GB 可用空间并另预留 2 GiB。工具验证备份后恢复基础包，拒绝覆盖已存在的 HD 目录；外部备份保留。
2. 点击 **回退到 HD**。工具先完整验证 HD，再将自己管理的 `Client/Content/UHD` 移回缓存。HD 缺失或校验失败时，不会移走当前 UHD。
3. 在 **WeGame 的鸣潮启动参数中移除 `-krqlv=uhd`**，保留其他参数，再从 WeGame 启动 HD。

- HD 恢复只接受同一游戏路径和当前支持版本的备份；升级后的客户端不能直接恢复旧基础包。本次此前生成的兼容 SHA256 备份清单也受支持。
- 回退不要求联网或旧核心程序，但要求 HD 与内置 3.7.0 清单完整匹配；升级后若 HD 已变化，回退也会停止，需重新核验版本。
- 回退保留 UHD 缓存，**不会自动释放约 61.50 GiB 空间**。再次添加 UHD 时会校验并复用缓存。
- 要释放 UHD 缓存空间，回退后点击 **清理已回退缓存**。只删除登记的缓存文件，不递归清理游戏目录，也不删除外部 HD 备份。
- 游戏自行生成的热更新、视频、画质设置或存档不在回退范围内。

## 视频资源说明

本工具下载 UHD **基础包**，独立视频由游戏另行更新。[官方说明](https://mc.kurogames.com/main/news/detail/5478)提到各档资源的视频码率存在差异。

本机曾在 `Client/Saved/Resources/Video/uhd` 检出 60 组视频包及签名，合计约 7.66 GB。对比同名 HD 视频，60 个 UHD 视频码率均更高，其中 55 个分辨率更高；**UHD 视频并不等于所有视频都是 4K**。这些是本机文件核验结果，不代表已确认每段视频的实际播放选择或全部历史视频是否齐全，也不能据此精确拆分用户反馈的约 7.9 GB 热更新。

## 文件边界

- UHD 添加操作新增 `Client/Content/UHD/`：50 个 `.pak`、50 个 `.sig`，以及 `.wuwa-uhd-owner.json` 所有权记录。
- 可选 HD 操作只备份、移除或恢复 `Client/Content/HD/` 中清单列出的基础包。
- 下载断点、回退资源、HD 暂存目录与事务状态位于**游戏同级目录** `.wuwa-uhd-<路径标识>/`；可点击“打开缓存目录”查看。
- HD 外部备份位于用户选择的位置，新批次名为 `Wuwa-HD-日期时间-标识/`。

UHD 完整下载先保存在缓存，再用同盘目录重命名加入游戏，不会产生双份 UHD 占用。不要手动修改 `state.json`、`hd-operation.json`、备份清单或所有权记录，也不要在缓存中保存无关文件。工具不读取登录令牌，不自动更新 WeGame，不修改核心程序、渠道配置、注册表或官方快捷方式。

## 保护机制与限制

- 官方 HTTPS 下载域名白名单，固定 3.7.0 UHD / HD 清单；逐文件完整校验。
- 核心程序、官方版本或清单不匹配时停止安装；HD 备份移除与恢复也检查核心版本，避免混装。
- 拒绝路径越界、重复名称、符号链接、目录联接及硬链接；未知目录不接管，未知文件不删除。
- UHD 支持断点续传、下载长度与 HTTP Range 检查、三条官方 CDN 重试。
- 游戏独占操作锁、运行进程检查、写入前核心与渠道复核、事务日志及中断恢复。
- 操作期间请勿启动游戏、移动目录或同时运行另一更新工具；进程检查无法与外部程序建立共同锁。
- 官方仍处于资源分级测试阶段；本工具不绕过反作弊或修改游戏代码，不代表 WeGame UHD 入口已获官方支持。
- 本版仍为预览版。独立代码审查未发现问题，但架构审查服务不可用，未获得完整独立审查批准。
- v1.0.0 的一次长时间 GUI 下载曾无响应退出，后台续传完成。根因仍未定位，本版不宣称修复该问题；程序未做代码签名。

## 来源

- [官方资源分级说明](https://mc.kurogames.com/main/news/detail/5478)
- [官方新版游戏索引](https://prod-volcdn-gamestarter.kurogame.xyz/launcher/game/10003_oLNgHF1CESo51DGHN2odtp40e3oI1HfZ/G152/official/index.json)
- [UHD 3.7.0 文件清单](https://pcdownload-huoshan.aki-game.com/launcher/game/G152/10003/3.7.0/983fc4b836c240319aa3a0fc2c9f8514/uhd/indexFile.json)，MD5：`bb8104efa17446c5cac8f35f1593fe59`
- [HD 3.7.0 文件清单](https://pcdownload-huoshan.aki-game.com/launcher/game/G152/10003/3.7.0/983fc4b836c240319aa3a0fc2c9f8514/hd/indexFile.json)，MD5：`4f062fb8220bde7ef45707746869750b`

## 开发与验证

运行时仅依赖 Python 标准库。构建依赖安装到项目独立的 `.venv`。

```powershell
python main.py --self-test --report test-report.json
python main.py --ui-smoke-test --report ui-report.json
python main.py --inspect '游戏根目录' --offline --report inspect-report.json
.\build.ps1
```

`--self-test` 仅在临时模拟目录中测试，不访问真实游戏或网络。界面打开仅尝试定位游戏；文件检查与资源操作需手动点击。命令行验证使用 `--report` 保存 JSON，因为窗口版 EXE 不附带控制台。

`build.ps1` 生成 Windows EXE 和本地源码 / 便携压缩包，位于忽略的 `dist/` 目录；公开 Release 仅上传 EXE。含本机路径的原始报告留在本地，公开摘要见 `validation/tool.json`。
