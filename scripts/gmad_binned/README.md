# 六质量区间 gMAD 候选对搜索

这是位于 `scripts/gmad_binned/` 的独立工具，不修改 `pyiqa` 框架代码、依赖或原有 `pyiqa-gmad` 命令。它按用户指定的六个质量区间进行双向攻击/防守选对，复用现有 CSV 读取、分数方向识别和 NR 模型推理功能。

## 选择规则

1. 将防守模型的分数范围等宽划分为 **6 个区间**，从低质量到高质量排列。
2. 在每个区间内，选出攻击模型评价最低和最高的两张图，使攻击模型的分差达到该区间内的最大值。
3. 交换两个模型的攻击/防守角色，再选择一次。

默认最多 **6 × 2 = 12 对**；区间内图片少于两张时，该区间的 `pair` 为 `null`。同一方向的不同区间不会共用图片；两个方向可能共用图片，甚至选中同一对。`pair_count` 按方向统计，`unique_pair_count` 是去除反向和重复后的图片对数量。

每个模型独立映射到“越高质量越好”的 `[0,1]` 分数：

- 越高越好：`q = (score - min) / (max - min)`。
- 越低越好：`q = (max - score) / (max - min)`。

默认使用当前候选池的实际最小值和最大值。`--range-a MIN MAX`、`--range-b MIN MAX` 可指定模型的固定原始分数范围，例如 `0 100`；端点始终按原始数值从小到大填写。超出指定范围会报错，不会默默截断或丢弃图片。固定范围有助于跨候选池比较区间；不指定时，候选池变化也会改变区间。异常极值可能造成区间拥挤或空缺。

默认的“低质量/高质量”是相对当前候选池而言，不等同于经过人类主观分数标定的绝对质量等级。

六个归一化区间是 `[0,1/6)`、`[1/6,2/6)`、…、`[5/6,1]`。边界归入质量更高的区间；相同分数始终属于同一区间。输出同时给出原始分数区间及其开闭边界。不会额外套用原工具的 `0.1` 个标准差阈值，**“质量接近”的条件就是属于防守模型的同一个区间**。

当分数范围为零时，所有图片统一映射到 `q=0.5`，并输出警告，原始区间为 `null`；这不能说明其绝对质量处于中等水平。若某个区间内攻击模型的分数全部相等，仍返回两张不同图片，标记 `zero_attacker_gap`，方便区分“没有区分能力”和“图片不足”。最优解并列时按输入顺序稳定选取。

这实现了六区间内的自动候选对筛选。它没有收集人工偏好，也未复现原始论文的完整实验数据、标定和主观评价流程，不能凭模型分歧直接认定哪一方更准确。

## 使用已有的 1 万张图片分数

先按仓库说明安装 `pip install -e .`，以下命令均从仓库根目录运行，无需安装额外依赖：

```bash
python scripts/gmad_binned/gmad_binned.py \
  --scores-csv /path/to/scores.csv \
  --metric-a model_a --metric-b model_b \
  --direction-a higher --direction-b lower \
  --bins 6 \
  --output gmad-binned-results.json
```

CSV 示例：

```csv
image,model_a,model_b
images/a.png,75.2,20.3
images/b.png,76.0,55.8
```

图片必须存在；默认相对于 CSV 所在目录解析图片路径，也可用 `--image-root /path/to/images` 指定根目录。已知 pyiqa 指标会自动识别高分/低分方向，自定义列名需要给出两个 `--direction-*` 参数。

如果两个模型原本都输出 `[0,100]`，需要固定区间：

```bash
python scripts/gmad_binned/gmad_binned.py \
  --scores-csv /path/to/scores.csv \
  --metric-a model_a --metric-b model_b \
  --direction-a higher --direction-b lower \
  --range-a 0 100 --range-b 0 100 \
  --output gmad-binned-results.json
```

也支持直接对图片目录评分，需要对应模型权重已缓存或下载网络可用：

```bash
python scripts/gmad_binned/gmad_binned.py \
  --image-dir /path/to/images \
  --metric-a musiq --metric-b brisque \
  --device cpu --bins 6 \
  --output gmad-binned-results.json
```

## 输出与复用

JSON 的 `directions` 有两个元素，分别列出防守模型、攻击模型，以及按低到高排列的 6 个 `levels`。每个 level 包含区间边界、图片数量、状态和最大分差的图片对。图片保留两个模型的原始分数和归一化质量分数。配对中的第一张是攻击模型认为质量较低的图片，第二张是较高的图片。

纯 Python API 为 `select_binned_pairs(...)`；加载分数和推理的适配层是 `run_binned_gmad(...)`。纯选择逻辑只使用标准库，不依赖 Torch。适配层使用当前 IQA-PyTorch 的私有辅助函数；若未来框架更新这些函数的接口，只需调整这个外部适配层。当前兼容的仓库版本为 `a3eecfbf33bd9313ac613df9bc80ccbba94f28e5`。

选择阶段逐张分桶并寻找极值，不枚举所有图片对；6 个区间时为线性时间和空间，适合 1 万张及以上候选图片。

```bash
python -m pytest scripts/gmad_binned/test_gmad_binned.py -q
```

## 已运行的验证与示例

- 34 项测试通过，包括与穷举结果核对、区间边界、低分更优的方向反转、相同分数、缺失图片、CSV 命令行和 1 万张图片规模的选择。
- 1 万条分数的选择阶段在当前实例约耗时 0.12 秒；不包含模型推理和加载图片。
- `examples/synthetic-scores.csv` 是 12 张简单色块图片的**人工示例分数**，不代表模型实际预测。`examples/synthetic-results.json` 在两个模型的 6 个区间各含 2 张图，输出 12 对、12 个不同的图片对。
- `examples/real-image-results.json` 是对仓库自带的 5 张真实样例图片实际运行 `entropy` 和 `brisque` 后的结果。图片较少，两个方向合计输出 2 对，其余区间明确标记图片不足。
- 仅新增独立工具目录；`pyiqa` 框架代码、原有测试和依赖声明保持不变。

复现六区间示例：

```bash
python scripts/gmad_binned/gmad_binned.py \
  --scores-csv scripts/gmad_binned/examples/synthetic-scores.csv \
  --metric-a model_a --metric-b model_b \
  --direction-a higher --direction-b lower \
  --range-a 0 60 --range-b 0 60 \
  --bins 6 --output gmad-binned-demo.json
```

仓库内附带的示例 JSON 使用相对于仓库根目录的图片路径，便于克隆后查看。实际运行时，工具输出解析后的绝对图片路径。示例 JSON 是验证记录，可由上述命令重新生成。
