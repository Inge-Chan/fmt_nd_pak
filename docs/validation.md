# 验证层次与本地回归

## 不同层次不能互相替代

| 层 | 检查对象 | 当前入口 |
|---|---|---|
| 合成规则 | 分量位宽、PCA、swap、坏 clip 隔离、配置等 | `python -B -m unittest discover -s tests -v` |
| 真实输入 | 本地 PAK、骨架、蒙皮和确定的动作集合 | 设置样本环境变量后运行同一命令 |
| 迁移等价 | 新旧解码器的时间、键值和骨号 | 本次迁移对照记录，见证据 E07 |
| 宿主接线 | 真正的 Noesis 模型构建与写出 | `tools/export_animation.py` 与固定本地运行时；不由 stub 测试覆盖 |
| FBX 结构 | 网格、多骨蒙皮、动作名、有限曲线 | `tools/check_fbx_animation.py` |
| 蒙皮关联 | 真实 FBX 权重和骨链 | `tools/fbx_skin_audit.py` |
| 时序与姿态 | 源末采样、take 范围、独立求值和运行时对照 | 仍需继续完善，不能被结构 PASS 代替 |

## 无游戏资源

从仓库根执行，系统 Python 3.8+，仅使用标准库：

```powershell
python -B -m unittest discover -s tests -v
```

没有配置资源根时，真实资源测试使用 unittest 的 skip 机制；不是自动下载样本，也不从固定个人目录偷取资源。

## 有本地真实资源

```powershell
$env:ND_ANIM_TEST_ROOT = 'D:\Games\TLOUP1\build\pc\main\sp-common\actor97'
$env:ND_MODEL_TEST_ROOT = 'D:\Games\TLOU2\build\pc\main\sp-common\actor97'
python -B -m unittest discover -s tests -v
```

测试根是 `actor97`，与安装配置的 `build/pc/main` 资源根层级不同。资源目录可以放在任何位置，但文件名与受测布局需要匹配。

设置了环境变量但缺少资源会报错，而不是把错误算成跳过。

## 样本依赖

### P1 动画与骨架

- `anim-t1x-ellie-inspect.pak`
- `t1x-ellie-skel.pak`
- `anim-ellie-lose-balance.pak`
- `ellie-skel.pak`

检视集合先过滤 `1AE94937` 且排除增量动作，期望 157 条。失衡目标包期望 22 条，全部为 `AA644C88`，其中 4 条 15 fps、18 条 30 fps。

### P1 服装检查

- `ellie-costume-01-torso.pak`
- `ellie-shirt-01-u1-g3-v1.pak`
- `ellie-arms-01-u1-g1-v1.pak`
- `ellie-body-01-u1-g2-v1.pak`

这四个资产测试验证配套上衣、长袖、双手和腰部补片，不等于读取并验收了完整 GUI 预设里的每一个部件。

### P2 原生失衡模型

- `ellie-farm-explore-tshirt-body.pak`
- `ellie-farm-explore-tshirt.pak`
- `ellie-farm-head.pak`
- `ellie-patrol-hair.pak`

筛选规则来自 [部件清单](../examples/ellie_lose_balance_parts.json)。测试核对 v57、SkelID、网格字段、原始权重和法线重算指针。

## 测试清单

| 文件 | 项数 | 范围 |
|---|---:|---|
| `test_animation_pipeline.py` | 17 | 编码、通道、过滤、持续时间与 2 项真实动作集合检查 |
| `test_deformation_helpers.py` | 18 | 矩阵、绑定、twist、肩带及 4 项真实骨架/动画检查 |
| `test_lose_balance_sample.py` | 7 | v55/v57 布局及 4 项真实模型/动作检查 |
| `test_outfit_parts.py` | 5 | 真实服装资产身份、覆盖区域与权重 |
| `test_config.py` | 15 | 配置、游戏路径与跨游戏部件根解析 |
| `test_part_presets.py` | 8 | GUI/CLI 预设一致、去重与清单校验 |
| `test_texture_index.py` | 4 | 不依赖宿主的合成字典索引接线检查 |
| `test_noesis_runtime.py` | 16 | 合成发行文件、指纹、部署、备份、路径边界和受管字节码清理 |
| `test_noesis_export.py` | 12 | 输出保护、日志/数量检查、失败记录、进程超时和导出驱动接线 |

当前共 102 项，其中 15 项需要本地真实资源。首次工程迁移的 74 项记录保留为历史批次；加入运行时工具后的结果与实际 Noesis 导出记录见 [证据 E10](evidence.md)。

`test_texture_index.py` 使用临时合成字典替身，证明缓存组织和错误处理，不证明真实纹理已正确解码。

两组 `test_noesis_*` 也使用合成运行时和受控进程替身，不执行假冒的 Noesis 二进制，不需要下载宿主。真实 Noesis 验证必须单独运行下述工具。

## 固定宿主的真实验证

```powershell
python -B tools/setup_runtime.py --check
python -B tools/export_animation.py --help
```

按 [运行时说明](runtime.md) 提供真实模型、动画与部件清单，工具会启动新的隐藏 Noesis 子进程，输出独立 FBX、日志及 `*.noesis-run.json`。

应同时核对记录中的发行指纹、当前插件/驱动脚本哈希、声明输入、动画名和数量。`structure_passed` 只补上宿主导出与结构这一层，不会自动将源时序和姿态标为通过。

## 检查已有 FBX

```powershell
python -B tools/check_fbx_animation.py <FBX路径> --animation stumble-90-l-2m
python -B tools/fbx_skin_audit.py <FBX路径> --filter strap --top 12
```

结构工具的 PASS 仅表示有网格、多骨蒙皮、命名动作与有限曲线。它没有把每条实际曲线与源 PAK 的末时间、末值或完整采样数对应起来。

## 下一阶段：时序回归

最小建议集合：

- 单独导出 `stumble-90-l-2m`，核对源第 48 帧对应的 1.6 s。
- 15 fps 与 30 fps 动作混合导出，分别核对 fps 和时间范围。
- 长动作接短动作、短动作接长动作，交换顺序后重复。
- 只含常量通道的动作，核对其持续区间。
- 同一个动作单独导出与整包导出，比较末时间和独立求值。

分别记录 source、实际 `KeyTime`、take 元数据和独立导入结果。不能把“增加 take 长度”作为修复丢帧的唯一验收条件。

## 新增样本的原则

先确认身份和期望值来源，再写断言。测试数量、曲线数量、合法四元数比例和可见改善不能单独充当“正确率”。

真实输入留在仓库外；只提交最小合成反例、可复用测试代码和脱敏摘要。出现新游戏版本差异时，单独声明样本集合，不把它误报成旧算法必然错误。
