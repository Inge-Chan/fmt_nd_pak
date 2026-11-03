# 贡献与发布检查

## 工作方式

1. 先阅读 [开发状态](docs/status.md) 和 [架构](docs/architecture.md)，不要根据旧实验注释推断当前能力。
2. 先复现，再修改。记录动作来源、SkelID、帧数、帧率和失败层次。
3. 每次只改变一个主要假设，补上能区分旧规则与新规则的合成回归。
4. 对真实样本进行显式的本地回归，不把跳过项计为通过。
5. 修改导出链路时，分别验证源采样、实际 FBX 曲线、take 范围和独立导入结果。

## 不应破坏的不变式

- 常量与逐帧通道按原始散射映射落到正确骨骼。
- 关键帧是父局部空间；不能把世界骨矩阵填作缺省局部关键帧。
- 模型与动画使用一致的比例和旋转约定。
- 辅助骨修复不改原网格、三角形、权重和主动画键值。
- 未支持布局应有明确诊断；不得用补零或任意姿势把错误伪装成成功。
- 不通过文件名或相似骨名自动跨 SkelID 重定向。

## 测试

从仓库根运行：

```powershell
python -B -m unittest discover -s tests -v
```

可选真实样本通过 `ND_ANIM_TEST_ROOT` 和 `ND_MODEL_TEST_ROOT` 指定，见 [验证说明](docs/validation.md)。不要把真实资源复制进测试目录。

插件模块面向旧 Noesis Python 运行时；测试和 `tools/` 面向系统 Python。不要在插件模块中引入 f-string、`pathlib`、现代类型注解、数据类或 `struct` 的半精度 `e` 格式。

`tests/support/` 是隔离的验证适配层，不是 Noesis 替代实现，不能据其通过宣称宿主导出已经通过。

## 固定宿主验证

运行 `python -B tools/setup_runtime.py --check` 检查本地运行时与部署是否匹配；源码变更后用 `tools/setup_runtime.py` 部署，或由 `tools/export_animation.py` 在导出前自动部署。

只在明确需要保留并覆盖运行时中的手工修改时使用 `--force`，备份留在忽略目录。不要绕过发行指纹、上传宿主或把旧输出覆盖成“新结果”。

真实宿主记录见 [运行时说明](docs/runtime.md)。结构通过后，仍要独立核对源采样、take 范围和姿态。

## AI 辅助研究记录

采用 [实验模板](docs/research-methods.md)，区分：

- 文件或运行时直接观测到的事实。
- 由事实推导出的候选解释。
- 由对照或真值验证的规则。
- 仅改善固定样本表现的兼容方案。
- 尚未验证的推测。

发布案例时，历史指标必须带日期、样本范围、度量定义和证据状态。模型身份不能由代码风格或文档中出现过的产品名反推。

## 发布前

- 核对 `git status --short`，确认没有样本、FBX、贴图、日志、转储、缓存、个人配置或临时脚本。
- 确认 `runtime/`、`artifacts/`、`NDConfig.json` 和 `*.noesis-run.json` 均未被强制加入；不要直接压缩整个工作目录发布。
- 检查 Markdown 本地链接；不链接到未随仓库发布的旧研究目录或私有素材。
- 同步 `docs/status.md`、`docs/validation.md` 和 `docs/evidence.md`。
- 核对 [NOTICE](NOTICE.md) 中的来源及许可边界。
- 另行审核历史提交；`.gitignore` 不会清除已经提交的历史内容。

不要为了“清理”擅自重写 Git 历史、移除上游署名、提交或推送其他人的工作。
