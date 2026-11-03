# fmt_nd_pak

Naughty Dog `.pak` model and animation plugin for Rich Whitehouse's Noesis.

基于 alphaZomega 的 v1.53 插件继续开发，由 Inge 维护。当前重点是《最后生还者 Part I》的动画解码、原生骨架与模型部件组合，以及 FBX 正确性验证。

> **开发中，非全资源兼容承诺。** 受测动画已能驱动蒙皮模型，但 FBX 末帧和多 take 时间范围仍有未解决的验收问题。辅助骨采用兼容烘焙，不是原游戏 RBF、IK 或布料求解器。完整边界见 [开发状态](docs/status.md)。

## 仓库内容

| 路径 | 内容 |
|---|---|
| `fmt_nd_pak.py` | Noesis 入口、PAK/模型/材质读取、GUI 和既有 PAK 回写路径 |
| `inc_nd_anim.py` | 动画资源、命令流、KeyCache 和关键帧构建 |
| `inc_nd_skeleton.py` | 逆绑定矩阵与辅助骨兼容烘焙 |
| `inc_nd_modelparts.py` | 部件清单、网格过滤和附挂骨架 |
| `inc_nd_config.py` | 本地配置、游戏识别和路径解析 |
| `NDPartPresets.json` | 可发布的 GUI 部件预设，仅含名称与规则 |
| `examples/` | 命令行部件清单，不含模型数据 |
| `tests/` | 合成测试及可选的本地真实样本回归 |
| `tools/` | 通用 FBX 结构与蒙皮检查工具 |
| `runtime.lock.json` | 固定 Noesis 环境的版本和文件指纹，不含二进制 |
| `runtime/` | 本机可安装的运行时，整个目录不入 Git |
| `docs/` | 使用、格式、证据、开发状态与 AI 辅助逆向案例 |

**Git 发布内容不包含** Noesis 本体、`inc_noesis.py`、`noewin.py`、游戏 PAK/PSARC、FBX、贴图、内存转储、临时实验脚本或个人配置。真实样本由使用者在仓库外自行准备；运行时和产物可以放在工程内的忽略目录。

## 支持范围

| 范围 | 当前定位 |
|---|---|
| TLOU Part I PC | 主要开发与回归对象；覆盖文档列出的模型及动画样本 |
| TLOU Part II | 保留既有读取路径，增加特定 v57 模型/蒙皮样本验证；不承诺动画全面支持 |
| Uncharted 4 / The Lost Legacy | 保留上游功能；本轮没有重新完成这些游戏的真实资源验收 |
| FBX 导出 | 由 Noesis 宿主写出；结构通过不代表时序或姿态正确 |
| PAK 回写 | 独立的旧功能路径；v57 明确禁止回写，不等于 FBX 导出被禁用 |

## 本地运行时

开发时建议使用仓库内的固定环境，不把宿主二进制提交到 Git：

```powershell
python -B tools/setup_runtime.py --source 'C:\Tools\Noesis'
python -B tools/setup_runtime.py --check
```

把示例路径换成已有的完整 Noesis 4.474 目录，文件指纹必须匹配。工具会安装到 `runtime/noesis/`，部署当前插件，并保护已有本地修改。它不会下载或自动接受其他版本。

本机资源路径可放在根目录的 `NDConfig.json`，该文件由 Git 忽略并随插件部署；首次从模板创建，已有配置不要覆盖。以后用 `tools/export_animation.py` 启动新宿主导出、保存日志与结构检查结果。

首次 GUI 预览勾选 `Load Textures` 后，插件会从配置的游戏资源根自动建立缺失的高清贴图索引，不需要复制旧环境缓存。`Load All Textures` 控制额外贴图通道，不是高清开关；排查方法见 [贴图与缓存](docs/usage.md#贴图与缓存)。

完整步骤和命令见 [固定运行时与导出](docs/runtime.md)。不采用这套工具时，也可以按下一节手动安装。

## 手动安装

1. 单独准备 [Noesis](https://richwhitehouse.com/index.php?content=inc_projects.php&showproject=91)。本项目历史宿主验证使用 4.474，所附 Python 运行时为 3.2.1。
2. 关闭 Noesis，备份原有同名插件。
3. 将五个根目录 Python 文件及 `NDPartPresets.json` 复制到 Noesis 的 `plugins/python/`：`fmt_nd_pak.py`、`inc_nd_anim.py`、`inc_nd_skeleton.py`、`inc_nd_modelparts.py`、`inc_nd_config.py`。
4. 将 `NDConfig.example.json` 复制到同一目录并命名为 `NDConfig.json`，填写自己的资源根目录。不要覆盖已有个人配置，也不要提交它。
5. 重启 Noesis。不要把 `tests/`、`tools/` 或整个仓库直接塞进插件加载目录。

配置示例：

```json
{
  "version": 1,
  "defaultGame": "TLOUP1",
  "baseDirectories": {
    "U4": "",
    "TLL": "",
    "TLOUP1": "D:/Games/TLOUP1/build/pc/main",
    "TLOU2": "D:/Games/TLOU2/build/pc/main"
  }
}
```

这里是包含 `sp-common` 等目录的资源根，不是 `actor97`，也不是游戏可执行文件所在目录。插件输入是已提取的 PAK，本仓库不附带 PSARC 解包工具。

也可以只设置启动 Noesis 的进程环境：

```powershell
$env:ND_TLOUP1_ROOT = 'D:\Games\TLOUP1\build\pc\main'
$env:ND_TLOU2_ROOT = 'D:\Games\TLOU2\build\pc\main'
```

环境变量覆盖 JSON；`-ndgame` 可显式指定游戏，避免依赖文件名猜测。更多选项、缓存重建和预览步骤见 [使用说明](docs/usage.md)。

## 开发验证

单元测试与文件检查工具使用系统 Python 3.8+ 和标准库，不需要安装 Noesis，也不需要下载第三方测试依赖；真实导出工具需要 Windows 和锁定的本地宿主。运行时插件仍以旧宿主 Python 为目标，不要把工具使用的现代语法带进插件。单元测试与宿主验收是不同层次。

```powershell
python -B -m unittest discover -s tests -v
```

不设置样本根时，真实资源测试会明确跳过；这不能写成“真实样本全部通过”。

```powershell
$env:ND_ANIM_TEST_ROOT = 'D:\Games\TLOUP1\build\pc\main\sp-common\actor97'
$env:ND_MODEL_TEST_ROOT = 'D:\Games\TLOU2\build\pc\main\sp-common\actor97'
python -B -m unittest discover -s tests -v
```

当前受测动作集为 157 条 t1x 普通检视动作与 22 条失衡动作。测试层次、样本名称与结果口径见 [验证说明](docs/validation.md) 和 [证据索引](docs/evidence.md)。

## 文档导航

- [架构与调用链](docs/architecture.md)
- [固定运行时与导出](docs/runtime.md)
- [动画格式与已确认规则](docs/animation-format.md)
- [开发状态与待办](docs/status.md)
- [AI 辅助逆向案例稿](docs/case-study.md)
- [研究方法与实验记录模板](docs/research-methods.md)
- [此次工程整理说明](docs/migration.md)
- [贡献与发布检查](CONTRIBUTING.md)

源码沿用的上游署名、宿主边界与许可待确认事项见 [NOTICE](NOTICE.md)。本次整理不替上游代码或第三方素材授予新许可证。
