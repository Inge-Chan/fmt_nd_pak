# 架构与调用链

## 工程边界

这是 Noesis 的 PAK 插件，不是独立渲染器或通用游戏解包器。系统 Python 下的测试可以验证解码和部分接线，但真实模型构建与 FBX 写出仍依赖 Noesis。

```text
Noesis 打开 PAK
  registerNoesisTypes / pakCheckType
  pakLoadModel
    PakFile.readPakHeader
      页表 / 指针修复 / 资源项
    PakFile.readPak
      JOINT_HIERARCHY / GEOMETRY_1 / 材质描述
    NDAnimReader.read
      嵌套 ANIM 资源 / 目标 SkelID / 分段命令流
    骨架选择 + 模型部件清单
    PakFile.loadGeometry
      顶点 / 索引 / 蒙皮 / 可选贴图
    buildNoesisAnims
      decodeSegmentFrames → boneMap → 父局部 TRS
      DeformationRig.bake → 辅助骨键值
    NoeModel + NoeKeyFramedAnim
  Noesis 预览 / 宿主 FBX 写出
```

## 模块

### `fmt_nd_pak.py`

Noesis 注册、GUI、路径配置接入、模型加载和旧 PAK 回写集中在这里。

- `readPakHeader()` 建立页表、指针修复表并定位资源。
- `checkResItem()` 识别骨架、几何与 VRAM 描述。
- `readPak()` 读取骨架、网格描述符和材质。
- `loadGeometry()` 将顶点流、索引和权重交给 `rapi.rpg*`。
- `pakLoadModel()` 组织主包、基础骨架、附加部件与动画包。
- `pakWriteModel()` 是 PAK 注入路径，**不是 FBX 导出器**。

游戏识别用于选择资源根目录；二进制布局还由包头和资源版本判断。不能用游戏路径的名字代替二进制版本识别。

### `inc_nd_anim.py`

活动路径只有一套分段解码：

```text
read → readClipHeader → readSegments
     → decodeSegmentFrames
     → _keyFramedBonesFromDecode → buildNoesisAnims
```

`NDAnimClip` 保存 clip 元数据及分段；`NDAnimSegment` 保存 KeyCache 位宽、解码操作和散射目标。早期的单槽解码、位宽三等分和扫描绑定姿势猜测路径已删除。

ANIM 可能嵌套在顶层资源内部，因此动画读取使用经过多个字段约束的资源头哨兵扫描，不只遍历 PAK 顶层 login table。

### `inc_nd_skeleton.py`

从受支持的骨架头读取全量逆绑定矩阵，恢复绑定空间，建立 twist 与肩带兼容约束。

肩带可根据附近身体顶点的实际蒙皮权重选择锚点，再烘焙成普通局部 TRS。它不运行游戏的 RBF、IK、碰撞、惯性或布料系统。

### `inc_nd_modelparts.py`

部件清单支持 `file`、`meshPrefixes`、`boneSuffix`、`attachTo`。显式附挂部件会保留自己的骨架并连接到主模型，不能把独立布料骨号直接当主骨架骨号使用。

### `inc_nd_config.py`

不依赖 Noesis，可独立测试。负责本地 JSON/环境变量、配置根优先的游戏识别，以及 `${TLOU2}/...` 等部件路径解析。

## 关键不变式

1. 带 fixup 的零偏移是合法的页起始地址，不是空指针。
2. KeyCache 槽、段内关节号、全局动画关节号与模型骨号是不同概念。
3. `boneMap` 将动画 xform 顺序映射到模型骨列表。
4. 动画 TRS 是父局部空间；骨列表经过 `multiplyBones` 后是世界空间。
5. 文件位置、模型变换与关键帧必须使用一致的 `GlobalScale`。
6. 动画 SkelID 必须与实际目标骨架匹配；部件本身也需要独立核对兼容性。
7. 辅助骨兼容层不能通过修改主骨键值或原始权重掩盖错误。

## 验证适配层

`tests/support/pak_reader.py` 与 `skeleton_reader.py` 是独立的最小读取器，用于读取本地样本和核对绑定数据；`noesis_stub.py` 仅模拟关键帧容器。

这种隔离让测试不依赖 Noesis 二进制，但不覆盖宿主采样、GUI 和真实 FBX 写出。`tools/` 从已有 FBX 检查另一层事实，同样不能替代游戏运行时真值。

## 本地宿主驱动

`tools/noesis_runtime.py` 管理固定发行文件与项目插件这两种不同的哈希边界：发行文件匹配 `runtime.lock.json`，插件匹配当前工作区源码。本机配置、缓存、部署记录与备份留在忽略目录。

`tools/setup_runtime.py` 负责受控安装和部署，`tools/export_animation.py` 启动新 Noesis 子进程，保存声明输入/工具版本及实际导出结果。工具运行在系统 Python，不属于内嵌 Python 3.2 的插件代码。

该层只报告 `structure_passed`，将源时序和姿态标记为 `not_checked`。操作说明见 [运行时文档](runtime.md)。

## 暂不重构的部分

上游模型读取和 PAK 注入仍有较大的单文件实现；`dialogOptions`、`gameName`、`ResItemPaddingSz` 等仍是全局状态。此次没有在缺少 U4/TLL 广泛回归时重写这些分支。

后续可逐步把每个包的布局状态移入实例，但应先补混合包与宿主回归，再拆模块，避免工程整理与算法修改混在一次变更中。
