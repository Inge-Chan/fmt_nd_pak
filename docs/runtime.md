# 本地 Noesis 运行时与固定环境导出

这层工具让仓库内的源码可以直接部署、执行和留下验收记录，但不把 Noesis 二进制加入公开 Git。

## 目录与发布边界

```text
runtime.lock.json                         Git：版本、发行指纹和关键文件哈希
tools/noesis_runtime.py                   Git：校验、复制和部署逻辑
tools/setup_runtime.py                    Git：安装/校验入口
tools/export_animation.py                 Git：固定宿主导出入口
NDConfig.example.json                    Git：空路径配置模板
NDConfig.json                            本地忽略：本机资源目录
runtime/noesis/                           本地忽略：完整运行时与部署后的插件
runtime/state.json                       本地忽略：插件部署哈希记录
runtime/backups/                         本地忽略：显式覆盖前的插件备份
artifacts/noesis/<运行编号>/              本地忽略：FBX、日志和运行记录
```

当前锁定的是本项目已有的 Noesis 4.474 本地发行基线，不是“自动采用官网最新版本”。安装过程不下载软件、不改系统环境变量、不覆盖旧工作区，也不修改原始游戏资源。

## 首次安装

在仓库根运行，`--source` 指向已存在的完整 Noesis 文件夹：

```powershell
python -B tools/setup_runtime.py --source 'C:\Tools\Noesis'
python -B tools/setup_runtime.py --check
```

请把示例路径换成你自己的源目录。源目录中的 Noesis、DLL、Python 库和附带目录必须与锁文件匹配；只复制一个 EXE 不够。

工具只复制选定的发行根文件及 `noex64`、`plugins`、`optionalplugins`、`scenes`，不会把旧目录的研究文件、游戏资源、旧 ND 插件或生成缓存一起搬入。

首次复制在 `runtime/` 下的独立暂存目录进行，校验完成后才移动为 `runtime/noesis/`。已有运行时不会被静默替换；版本或文件不匹配会停止。

## 锁定的具体范围

`runtime.lock.json` 固定 255 个发行文件，共 110798302 字节，并单列以下关键组件的哈希：

- `Noesis64.exe` 与原始 `ReadMe.txt`。
- x64 主库、OpenGL 库、Python 插件、FBX 插件和通用扩展插件。
- `core321.zip`、`inc_noesis.py`、`noewin.py`。

整体指纹由按路径排序的 `[相对路径, 字节数, 文件SHA256]` 列表生成：使用 ASCII JSON、无额外空白，再对其字节计算 SHA-256。规则标识为 `noesis-distribution-v1`。

项目管理的插件、个人 ND 配置、两个 ND 生成缓存、`__pycache__` 和 `.pyc/.pyo` 不属于发行指纹；项目插件另按当前源码哈希校验。发行目录中额外加入的插件也会造成指纹不匹配。

这是本项目现有本地基线的指纹，不声称任意来源的同名 4.474 压缩包都相同。升级宿主需要单独审核、建立新锁并重跑验证；工具不会自动“学习”未知二进制。

该锁也不是完整的操作系统、系统 DLL、GPU 驱动或游戏资产环境锁。

## 本机配置

首次可把 `NDConfig.example.json` 复制为仓库根的 `NDConfig.json`，填写本机资源根；**已有配置不要覆盖**。

根目录有 `NDConfig.json` 时，部署工具会把它作为管理文件复制到运行时的 `plugins/python/NDConfig.json`。建议使用绝对路径；相对路径仍按 Noesis 原生行为，以部署后的配置所在目录为基准。

根目录没有该文件时，部署工具保留运行时中用户已有的 ND 配置。`ND_TLOUP1_ROOT` 等进程环境变量仍按原配置规则覆盖 JSON，详见 [使用说明](usage.md)。

本机配置不进入 Git，也不写入公开的 `runtime.lock.json`。

## 首次加载高清贴图

安装工具不搬运旧环境的 `NDTextureHashes.json`、`NDRawDataStarts.json`。新运行时首次预览模型时，勾选插件对话框中的 `Load Textures`，再确认加载；插件会按本机资源根自动建立缺失的游戏索引。

生成位置是 `runtime/noesis/plugins/python/`，不是仓库根目录。索引只扫描字典元数据，实际高清像素仍从仓库外的游戏资源读取。已有索引会复用；安装/部署检查本身不扫描游戏，也不说明贴图已加载。

如果日志提示退回 `local textures`，检查游戏路径和缓存写入权限。`Load All Textures` 不是高清开关；更换游戏资源版本后使用 `-ndrebuildtextures` 更新缓存。详见 [贴图与缓存](usage.md#贴图与缓存)。

修改源码后运行下面的部署命令，再重启 Noesis；已经打开的 GUI 可能仍持有旧插件和旧索引。

## 日常部署与只读检查

```powershell
python -B tools/setup_runtime.py
python -B tools/setup_runtime.py --check
```

第一条先校验发行指纹，再同步当前项目插件；第二条只检查，不更新部署记录。

正常部署还会清除当前管理模块的 `.pyc/.pyo` 缓存，即使源码本轮未改变。仅设置禁止写字节码并不能阻止读取旧缓存，这一步用于避免 GUI 或旧运行留下的缓存导致实际执行的不是当前源码；不会删除无关模块的缓存。

如果运行时里的插件曾被手工修改，默认部署会拒绝覆盖。先把需要保留的修改移回项目源码；或者明确选择：

```powershell
python -B tools/setup_runtime.py --force
```

`--force` 仅允许在备份冲突插件后覆盖管理文件，**不能绕过 Noesis 发行指纹**。备份保留在 `runtime/backups/`，不删除旧工作区或已有运行时。

修改源码后不要依赖旧 GUI 进程内已经加载的 Python 模块。导出工具每次启动一个新的 Noesis 子进程；手动预览前应关闭旧实例并重新部署。

## 导出固定样本

以下从仓库根执行，资源路径是需要自行替换的示例。

### 完整 t1x 人物

```powershell
$P1 = 'D:\Games\TLOUP1\build\pc\main\sp-common\actor97'
python -B tools/export_animation.py `
  --input "$P1\ellie-costume-01-torso.pak" `
  --animation-pak "$P1\anim-t1x-ellie-inspect.pak" `
  --parts examples/t1x_ellie_parts.json `
  --clip ellie-inspect-comic-turn-back --game TLOUP1
```

### 原生失衡样本

先配置 P2 资源根，让 `${TLOU2}` 部件路径能够解析：

```powershell
python -B tools/export_animation.py `
  --input "$P1\anim-ellie-lose-balance.pak" `
  --parts examples/ellie_lose_balance_parts.json `
  --clip stumble-90-l-2m --game TLOUP1
```

它的主输入仍是 P1 动画；P2 只提供文档列出的同骨架模型部件。该组合不意味着 P2 动画已完整支持。

去掉 `--clip` 并加 `--expected-animations 22` 可检查目标包的动作数量，但不会修复或验证已知的多 take 时序问题。

## 导出行为与产物

导出工具会：

1. 检查输入和输出路径，拒绝覆盖已有 FBX 或其任何日志/记录文件。
2. 校验固定运行时并部署当前插件；不会覆盖冲突的本地插件修改。
3. 检查清单中显式声明的部件文件，并记录输入、清单、部件和插件的哈希。
4. 使用参数列表而非 shell 命令启动隐藏的 Noesis 子进程。
5. 清除子进程的 `PYTHONHOME`、`PYTHONPATH`、`PYTHONSTARTUP`，设置 UTF-8 标准流及禁止写 Python 字节缓存；不修改系统环境。
6. 检查退出码、实际动画挂接日志和 FBX 结构，写入最终结果。

每次默认生成独立的运行目录：

- `export.fbx`：新导出文件。
- `export.noesis.log`：Noesis 的加载与导出日志。
- `export.console.log`：子进程标准输出/错误捕获。
- `export.noesis-run.json`：命令、时间、发行指纹、插件及驱动脚本哈希、声明输入、配置哈希、结果。

记录只收集相关 ND/Python 环境设置，不转储完整环境变量。它包含本机路径，应留在本地；即使指定其他输出目录，`*.noesis-run.json` 也由 Git 忽略。

记录的是**显式声明的输入集合**，不是全部隐式资产依赖。自动查找的骨架、外部贴图库和系统 DLL 仍需结合 Noesis 日志及独立记录核对。

`--timeout` 默认为 180 秒。超时或中断只停止本次启动的进程，不按进程名关闭用户其他 Noesis；失败产物保留用于排查，不拿旧文件冒充成功。

## 结果不等于动画完全正确

成功状态是 `structure_passed`，检查网格、多骨蒙皮、有限曲线、动作名称与数量。`--clip` 默认要求恰好一个动作；没有 clip 也没有数量要求时，只保证至少挂接一个动作，不证明整个包无跳过。

运行记录始终区分：

```json
{
  "status": "structure_passed",
  "timing_validation": "not_checked",
  "pose_validation": "not_checked"
}
```

这里不检查源末帧、take 时间范围、游戏运行时姿态、布料或 RBF。后续正确性工作仍按 [开发状态](status.md) 推进。固定样本的实际宿主结果见 [证据 E10](evidence.md)。

## 发布前

```powershell
git status --short
git check-ignore -- runtime/noesis/Noesis64.exe NDConfig.json artifacts/noesis/example/export.fbx
```

不要直接压缩整个本地工作目录：它现在确实包含被 Git 忽略的运行时和游戏导出产物。审核并提交工程文件后，发布应基于选定的 Git 跟踪文件；不要用强制添加绕过忽略规则。

随包 `ReadMe.txt` 保持原样并受哈希校验。Noesis 的使用和再分发条件仍需单独确认，详见 [NOTICE](../NOTICE.md)。
