# 使用与复现

日常开发可先使用 [固定运行时与导出工具](runtime.md)：自动校验、部署当前插件，拒绝覆盖旧产物并生成运行记录。本页保留 GUI、插件选项和手动 Noesis 命令，方便独立使用。

## 配置与路径

安装步骤见 [README](../README.md)。`NDConfig.json` 放在已安装插件旁边，而不是必须放在本仓库中。

| 设置 | 含义 |
|---|---|
| `defaultGame` / `ND_DEFAULT_GAME` | 无法从路径识别游戏时的默认项 |
| `ND_U4_ROOT` | U4 提取资源根 |
| `ND_TLL_ROOT` | The Lost Legacy 提取资源根 |
| `ND_TLOUP1_ROOT` | TLOU Part I 的 `build/pc/main` |
| `ND_TLOU2_ROOT` | TLOU Part II 的对应资源根 |

环境变量覆盖 JSON。JSON 中的相对根目录相对于配置文件位置解析；正式使用建议填写绝对路径。未知配置键或错误版本会明确报错，不静默吞掉拼写错误。

游戏选择顺序：

1. 命令行 `-ndgame`。
2. 输入文件所属的已配置根目录，优先最具体的路径。
3. 已知游戏标题或目录标记。
4. 默认游戏。

Part II 的 PC 路径先于通用 `pc/main` 分支识别。移动或重命名资源目录时，优先依赖配置根，而不是文件名提示。

## GUI 预览

1. 修改插件或配置后，关闭旧 Noesis 再启动，避免继续使用已加载的 Python 模块。
2. 打开已有蒙皮的模型 PAK，确认 `Game` 与 `Base` 正确，保留 `Load Base`。
3. 在文件列表中双击需要的动画包，确认它进入待加载列表，再选择 Load。
4. 检查日志中的目标 SkelID、过滤数量和实际挂接动作数。
5. 播放、拖动时间轴或切换动作，检查网格是否真正随多根骨骼变形。

打开 `ellie-costume-01-torso.pak` 时，GUI 可自动应用 `NDPartPresets.json` 中的 11 项附加部件；显式 `-modelparts` 优先于该自动预设。

已手动加入的错误部件不会被预设删除。尤其不要再把 costume-07 的裸臂/肩胸网格混入 costume-01 长袖组合。

默认关闭贴图加载，因此灰模本身不是蒙皮或动画故障。

## 命令行：t1x 固定样本

以下从仓库根执行。Noesis 路径与资源路径均是示例，输出放入已忽略的 `artifacts/`。每次使用新的输出名，避免误看旧文件。

```powershell
$Noesis = 'C:\Tools\Noesis\Noesis64.exe'
$env:PYTHONIOENCODING = 'utf-8'
$env:ND_TLOUP1_ROOT = 'D:\Games\TLOUP1\build\pc\main'
$data = Join-Path $env:ND_TLOUP1_ROOT 'sp-common\actor97'
$parts = '@' + (Join-Path (Get-Location).Path 'examples\t1x_ellie_parts.json')
$run = Get-Date -Format 'yyyyMMdd_HHmmss'
New-Item -ItemType Directory -Path artifacts -Force | Out-Null
$output = Join-Path (Get-Location).Path "artifacts\inspect_$run.fbx"
$log = Join-Path (Get-Location).Path "artifacts\inspect_$run.log"
& $Noesis '?cmode' "$data\ellie-costume-01-torso.pak" $output `
  -nodialog -anims -ndgame TLOUP1 -fbxmultitake `
  -modelparts $parts -animfile "$data\anim-t1x-ellie-inspect.pak" `
  -animclip 'ellie-inspect-comic-turn-back' -logfile $log
```

可替换为：

- `ellie-inspect-paper-ground-exit-2`：压缩/PCA 路径样本。
- `ellie-inspect-comic-ground-turn-back`：手腕、上臂和肩带检查样本。

这是导出操作，不是完整正确性验收。检查日志中是否实际挂接所选动作，再检查新生成的文件。

## 命令行：原生失衡样本

该样本的动画和骨架来自 P1；四个模型部件来自 P2，且引用同一个 `AA644C88` 骨架身份。它不构成 P2 动画已支持的证据。

```powershell
$env:ND_TLOU2_ROOT = 'D:\Games\TLOU2\build\pc\main'
$parts = '@' + (Join-Path (Get-Location).Path 'examples\ellie_lose_balance_parts.json')
$output = Join-Path (Get-Location).Path "artifacts\stumble_$run.fbx"
$log = Join-Path (Get-Location).Path "artifacts\stumble_$run.log"
& $Noesis '?cmode' "$data\anim-ellie-lose-balance.pak" $output `
  -nodialog -anims -ndgame TLOUP1 -fbxmultitake `
  -modelparts $parts -animclip 'stumble-90-l-2m' -logfile $log
```

此段沿用上一节的 `$Noesis`、`$data`、`$run` 和输出目录。去掉 `-animclip` 可加载目标包的 22 条动作，但已知多 take 时间范围问题尚未解决；优先从单动作验证开始。

清单中的 `${TLOU2}` 由插件解析，不是要求 PowerShell 替换 JSON 文件内容。跨游戏材质/贴图库混合尚未随此灰模样本验证。

## 部件清单

普通相对路径以主输入 PAK 所在目录为基准，不以 JSON 文件目录为基准。

```json
{
  "version": 1,
  "parts": [
    "head.pak",
    {
      "file": "hair-cloth.pak",
      "meshPrefixes": ["hair_base_"],
      "boneSuffix": "_hair_cloth",
      "attachTo": "headb"
    }
  ]
}
```

`meshPrefixes` 筛选网格前缀；`attachTo` 指定主骨架中的锚点；`boneSuffix` 用于识别附挂骨架的对应驱动骨。支持最多 64 项，字段拼错、无效筛选或缺失附件会明确报错。

跨游戏路径可写 `${TLOU2}/sp-common/actor97/example.pak`，但根目录必须配置，且不能用 `..` 逃离该根目录。

## 贴图与缓存

- GUI 的 `Load Textures` 开关控制贴图加载，默认关闭；命令行可用 `-ndtextures` 开启。
- 首次开启贴图加载时，如果当前游戏没有索引，会自动从配置的游戏资源根建立索引；关闭贴图加载时不会触发扫描。
- `NDTextureHashes.json` 与 `NDRawDataStarts.json` 保存在当前 Noesis 的 `plugins/python/`，是本地生成缓存，不随仓库发布。它们记录哈希、字典位置和数据偏移，不包含贴图像素。
- P1/P2 扫描资源根下各分区的 `texturedict3`；U4/TLL 使用 `texturedict2`。
- 索引只读取字典包的元数据，不把几十 GB 的纹理像素载入内存；实际使用某张贴图时才读取对应的像素数据。
- 更换游戏数据版本后，应在一次导入/转换中加 `-ndrebuildtextures` 强制更新该游戏索引，不假设旧偏移仍然有效，也不需要复制其他人的缓存。

`Load All Textures` 是额外加载其他贴图通道，并不是高清开关。没有可用的外部字典或索引时，只能使用模型包内的贴图；包内贴图不一定都是低清，应按日志中的实际 `WxH` 判断，不能把每次本地读取都认定为错误。

自动建立索引失败会输出 `[NDTextures] high-resolution index unavailable; using local textures` 及原因，并退回包内贴图；每次导入对同一游戏根只尝试一次，避免每个部件重复失败。显式 `-ndrebuildtextures` 失败则直接报错，且该选项本身不会自动开启贴图加载。

排查顺序：确认 `NDConfig.json` 的根目录下存在 `sp-common/texturedict3` 等目录 → 确认当前 Noesis 的 `plugins/python/` 可写 → 查看日志是否出现 `rebuilt ... dictionary indexes` 和 `VRAM texture hash found! ... WxH=`。后者表示从外部字典读取了指定尺寸的贴图；不能仅凭索引文件存在就认定材质视觉效果已验收。

## 参数速查

| 参数 | 用途 |
|---|---|
| `-nodialog` | 无导入对话框 |
| `-ndgame <ID>` | 显式选择 U4、TLL、TLOUP1、TLOU2 |
| `-ndtextures` | 开启贴图加载，缺失当前游戏索引时自动建立 |
| `-ndrebuildtextures` | 重建当前游戏的本地贴图库索引 |
| `-anims` | 请求动画加载并输出相关诊断 |
| `-animfile <pak>` | 从另一个包加载动画 |
| `-animclip <name>` | 精确动作名过滤，大小写不敏感 |
| `-modelparts <...>` | 分号路径列表，或 `@清单.json` |
| `-animadditive` | 实验性绑定姿势参考叠加，默认关闭 |
| `-ndlegacyhelpers` | 旧辅助骨行为对照，不用于声称新修复正确 |
| `-lods` | 包含更多 LOD，亦影响旧 PAK 注入路径 |
| `-meshfile` / `-texfolder` / `-t` / `-bones` | 旧 PAK 注入参数，不是动画编码器 |

`-fbxmultitake` 和 `-logfile` 是所用 Noesis 命令行中的宿主选项，不由本插件实现。导出动画时不要加 `-noanims`。

## 检查已导出的文件

```powershell
python -B tools/check_fbx_animation.py <新FBX路径> --animation ellie-inspect-comic-turn-back
python -B tools/fbx_skin_audit.py <新FBX路径> --filter shirt --top 12
```

第一项检查网格、多骨蒙皮、动作名及有限曲线；第二项列出权重关联和骨链。**二者都不替代源末帧、take 范围或逐帧姿态验收。**
