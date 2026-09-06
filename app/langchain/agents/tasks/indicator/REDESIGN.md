# 指标提取系统重设计方案

## 背景与核心问题

当前系统将所有提取结果统一写入 `standard_cache_ind`，用 `indicator_type=static/dynamic` 区分。
这在面对"纯试验方法类"标准时无法正确处理——该类标准没有指标，只有试验规程，强行提取会产生脏数据或空结果。

核心诉求：**指标是指标，试验是试验，分开存放；同时在提取前先判断标准性质，决定走哪条提取路径。**

---

## 一、标准性质分类

分类的本质是**内容结构**，即"有没有指标"、"有没有试验"、"两者关系如何"，而非标准所属行业或领域。
提取前先对标准做性质判断，结果决定后续流程。

### 1.1 分类定义

| 类型值                    | 内容结构                            | 典型例子            | 提取策略                 |
|------------------------|---------------------------------|-----------------|----------------------|
| `has_ind_and_test`     | 有指标，有试验，试验是验证手段，两者可分离           | 大多数产品规范、材料标准    | 提取指标 + 提取试验 + 建立关联   |
| `has_ind_only`         | 只有指标，无试验方法章节                    | 尺寸规格标准、外观标准     | 只提取指标                |
| `has_test_only`        | 只有试验规程，无判定准则，被其他标准引用            | 老化试验方法、拉伸试验方法   | 只提取试验，指标表为空          |
| `ind_embedded_in_test` | 指标和试验写在一起无法分离，试验即指标             | 锂电池安全规范、危险品运输规范 | 提取指标（试验上下文内嵌），同时提取试验 |
| `not_extractable`      | 无可提取的技术指标或试验，原因记在 `skip_reason` | 见下方说明           | 跳过，记录原因              |

### 1.2 `not_extractable` 的常见原因

以下情况均归入 `not_extractable`，通过 `skip_reason` 字段说明具体原因：

- **术语定义类**：全篇是术语、定义、符号、缩略语，无技术要求也无试验（如"XX领域术语"）
- **管理/程序类**：规定流程、职责、文件要求，是管理性要求而非技术指标（如"质量管理体系"、"认证程序"）
- **检验规则类**：抽样方案、批次判定逻辑，章节结构与技术要求相似但内容不是技术指标
- **全引用类**：技术要求章节存在，但内容全是"按GB/T XXXX执行"，自身无具体值，且引用标准不在库中无法补全
- **图样/图纸类**：技术要求全在图纸中，正文只有少量文字，大量数据依赖图片识别，提取质量极差

### 1.3 判断依据

读目录（`toc_only=true`）+ 适用范围章节，不需要读全文。主 agent 在步骤 2 完成后输出 `standard_structure_type` 和
`skip_reason`。

判断规则（按优先级）：

1. 目录中无技术要求、无试验方法、无性能要求类章节 → 判断是否为术语/管理/检验规则类 → `not_extractable`
2. 目录中只有试验方法章节，无技术要求 → `has_test_only`
3. 目录中有技术要求章节，无试验方法章节 → `has_ind_only`
4. 目录中安全要求与试验方法混写在同一章节，无独立技术要求章节 → `ind_embedded_in_test`
5. 目录中技术要求和试验方法均存在且独立 → `has_ind_and_test`

---

## 二、数据模型变更

### 2.1 新增表：`standard_cache_test`（试验缓存表）

```sql
CREATE TABLE IF NOT EXISTS `standard_cache_test`
(
    `id`
    INT
    NOT
    NULL
    AUTO_INCREMENT,
    `standard_no`
    VARCHAR
(
    100
) NOT NULL COMMENT '标准编号',
    `standard_name` VARCHAR
(
    500
) DEFAULT NULL,
    `run_id` VARCHAR
(
    64
) DEFAULT NULL COMMENT '与 standard_cache_ind 共享同一 run_id',
    `run_remark` VARCHAR
(
    500
) DEFAULT NULL,

    -- 试验本体（对应 GB/T 20001.4 各节）
    `test_name` VARCHAR
(
    200
) NOT NULL COMMENT '试验名称（不含"试验"后缀）',
    `method_desc` TEXT DEFAULT NULL COMMENT '试验方法概述（试样制备、计算方法等）',
    `conditions` TEXT DEFAULT NULL COMMENT '试验条件（温度、湿度、电压等环境参数）',
    `preparation` TEXT DEFAULT NULL COMMENT '试验准备（试样处理、仪器校准、系统搭建）',
    `procedure` TEXT DEFAULT NULL COMMENT '试验过程（按逻辑次序的操作步骤）',
    `acceptance` TEXT DEFAULT NULL COMMENT '试验要求/合格判据（为空表示纯方法类，无判定准则）',
    `report_items` TEXT DEFAULT NULL COMMENT '报告要点（主要用于纯试验方法类标准）',
    `source_clause` VARCHAR
(
    200
) DEFAULT NULL COMMENT '来源条款',

    -- 分类（与指标共用体系）
    `standard_object` VARCHAR
(
    200
) DEFAULT NULL,
    `applicable_object` VARCHAR
(
    200
) DEFAULT NULL,
    `object_type` VARCHAR
(
    50
) DEFAULT NULL,
    `indicator_category` VARCHAR
(
    50
) DEFAULT NULL,

    -- 元信息
    `is_valid` TINYINT
(
    1
) NOT NULL DEFAULT 1,
    `algorithm_version` VARCHAR
(
    50
) NOT NULL DEFAULT 'v3',
    `create_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
),
    `update_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
) ON UPDATE CURRENT_TIMESTAMP
(
    6
),
    PRIMARY KEY
(
    `id`
),
    INDEX `idx_standard_no`
(
    `standard_no`
),
    INDEX `idx_run_id`
(
    `run_id`
),
    INDEX `idx_is_valid`
(
    `is_valid`
)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标准试验提取缓存表';
```

### 2.2 新增表：`standard_cache_ind_test_rel`（指标-试验关联表）

```sql
CREATE TABLE IF NOT EXISTS `standard_cache_ind_test_rel`
(
    `id`
    INT
    NOT
    NULL
    AUTO_INCREMENT,
    `ind_id`
    INT
    NOT
    NULL
    COMMENT
    '关联 standard_cache_ind.id',
    `test_id`
    INT
    NOT
    NULL
    COMMENT
    '关联 standard_cache_test.id',
    `run_id`
    VARCHAR
(
    64
) DEFAULT NULL,
    PRIMARY KEY
(
    `id`
),
    INDEX `idx_ind_id`
(
    `ind_id`
),
    INDEX `idx_test_id`
(
    `test_id`
)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='指标-试验关联表';
```

### 2.3 `standard_cache_ind` 变更

新增字段：

- `standard_structure_type VARCHAR(30)` — 所属标准的性质（冗余存储，方便查询过滤）

无需改动现有字段，`static/dynamic` 区分保留不变。

**`indicator_type` 判断规则调整**：不再由 LLM 在提取时直接判断，改为从 `norm_class` 推导：

- `norm_class = 基础类 | 规范类` → `indicator_type = static`
- `norm_class = 方法类` → `indicator_type = dynamic`

这样 LLM 只需判断 `norm_class`，`indicator_type` 由程序在分类 Pass 完成后自动写入，减少一个需要 LLM 判断的字段。

---

## 三、Agent 流程变更

### 3.1 `ExtractionSummary` 新增字段

```python
class ExtractionSummary(BaseModel):
    standard_structure_type: str  # has_ind_and_test | has_ind_only | has_test_only | ind_embedded_in_test | not_extractable
    skip_reason: str  # 跳过时说明原因，正常提取时为空
    standard_object: str
    applicable_object: str
    common_test_conditions: str
    group_count: int
    total_submitted: int
    test_submitted: int  # 通过 submit_group_tests 提交的试验数
```

### 3.2 主 Agent Prompt 变更

在步骤 2（读适用范围）完成后，增加**步骤 2.5：判断标准性质**，按第一节的判断规则输出 `standard_structure_type`：

```
2.5 根据目录章节标题和适用范围，判断 standard_structure_type（参见分类定义）：
    - not_extractable / has_test_only → 记录 skip_reason，跳转到步骤 6 直接提交摘要（不提取指标）
    - has_ind_only → 步骤 5 只提取指标，不提取试验
    - has_ind_and_test / ind_embedded_in_test → 步骤 5 同时提取指标和试验
```

### 3.3 新增提交工具：`submit_group_tests`

与 `submit_group_indicators` 并列，extractor 子 agent 提取试验时调用：

```python
@tool
def submit_group_tests(group_name: str, tests_json: str) -> str:
    """提交一组提取完成的试验到累积器。"""
```

### 3.4 `ExtractionOutput` 新增字段

```python
class ExtractionOutput(BaseModel):
    standard_structure_type: str
    skip_reason: str
    indicators: List[IndicatorItem]
    tests: List[TestItem]  # 新增
    ind_test_links: List[tuple[int, int]]  # (indicator_index, test_index) 关联
```

### 3.5 新增 Schema：`TestItem`

字段设计参照 GB/T 20001.4-2015（试验方法标准编写规则）和 GB/T 20001.10-2014（产品标准中试验方法章节规范）：

```python
class TestItem(BaseModel):
    test_name: str = Field(description="试验名称，不含'试验'后缀，如：热斑耐久、湿漏电流、振动")

    # 试验方法（GB/T 20001.10）
    method_desc: str = Field(default="", description="试验方法概述：试样制备、保存方式、计算方法、测量不确定度等")

    # 试验条件（GB/T 20001.4 §试验条件）
    conditions: str = Field(default="", description="试验条件：温度、湿度、气压、电压、频率等环境参数")

    # 试验准备
    preparation: str = Field(default="", description="试验前的预备工作：试样处理、仪器校准、系统搭建等")

    # 试验过程（GB/T 20001.4 §试验步骤）
    procedure: str = Field(default="", description="试验步骤：按逻辑次序描述的操作序列")

    # 试验要求（GB/T 20001.4 §试验要求）
    acceptance: str = Field(default="", description="合格判据：试验完成后判断是否通过的技术指标或接受准则")

    # 报告（可选）
    report_items: str = Field(default="", description="试验报告应包含的内容要点，无则留空")

    # 公共字段
    source_clause: str = Field(default="", description="来源条款编号")
    standard_object: str = Field(default="")
    applicable_object: str = Field(default="")
    indicator_category: str = Field(default="")
```

**字段填写原则：**

- `conditions` 和 `preparation` 对应通用试验条件章节内容，若标准有独立的"通用试验条件"章节，各试验可引用而非重复填写
- `acceptance` 为空表示该试验本身不规定判定准则（纯试验方法类标准的典型情况）
- `report_items` 通常只在 `has_test_only` 类标准中填写，产品规范类标准一般不需要

### 3.6 Extractor 子 Agent Prompt 变更

在现有提取逻辑基础上，增加试验提取规则：

- 读完章节后，**先提取指标**（现有逻辑不变），**再提取试验**
- 试验提取规则：
    - 每个独立的试验方法（有名称、有操作步骤）提取为一条 `TestItem`
    - `acceptance` 字段：若试验章节本身规定了判定准则则填写，否则留空
    - 若某试验对应某指标（通过名称匹配），在 `ind_test_links` 中记录关联
- 对于 `test_method` 类标准：只提取试验，不提取指标

---

## 四、后端 API 变更（`standard_ind.py`）

### 4.1 写入逻辑变更（`extract-batch` 端点）

```python
# 写入指标（现有逻辑）
ind_id_map: dict[int, int] = {}  # indicator_index → db_id
for i, ind in enumerate(result.indicators):
    obj = await StandardCacheInd.create(...)
    ind_id_map[i] = obj.id

# 写入试验（新增）
test_id_map: dict[int, int] = {}  # test_index → db_id
for i, test in enumerate(result.tests):
    obj = await StandardCacheTest.create(
        standard_no=standard_no,
        run_id=run_id,
        test_name=test.test_name,
        conditions=test.conditions or None,
        procedure=test.procedure or None,
        acceptance=test.acceptance or None,
        source_clause=test.source_clause or None,
        standard_object=test.standard_object or None,
        applicable_object=test.applicable_object or None,
        indicator_category=test.indicator_category or None,
        is_valid=True,
        algorithm_version="v3",
    )
    test_id_map[i] = obj.id

# 写入关联（新增）
for ind_idx, test_idx in result.ind_test_links:
    if ind_idx in ind_id_map and test_idx in test_id_map:
        await StandardCacheIndTestRel.create(
            ind_id=ind_id_map[ind_idx],
            test_id=test_id_map[test_idx],
            run_id=run_id,
        )
```

### 4.2 SSE progress 事件新增字段

```json
{
  "type": "progress",
  "standard_no": "...",
  "standard_structure_type": "has_ind_and_test",
  "count": 12,
  "test_count": 8,
  "run_id": "..."
}
```

### 4.3 新增接口：`GET /standard-ind/tests`

查询指定标准的全量试验列表，结构与 `/indicators` 类似。

### 4.4 `/list` 接口新增统计字段

返回每个标准的 `test_count`（试验数）和 `standard_structure_type`。

---

## 五、实施顺序

1. **数据库**：新建 `standard_cache_test` 和 `standard_cache_ind_test_rel` 表，`standard_cache_ind` 加
   `standard_structure_type` 字段
2. **ORM 模型**：新建 `app/models/standard/cache_test.py` 和 `cache_ind_test_rel.py`
3. **Agent Schema**：`indicator_extraction_agent.py` 新增 `TestItem`、`ExtractionOutput.tests`、
   `ExtractionOutput.standard_structure_type`
4. **主 Agent Prompt**：加入标准性质判断步骤（步骤 2.5）
5. **Extractor 子 Agent Prompt**：加入试验提取规则
6. **`submit_group_tests` 工具**：新增提交工具
7. **`ExtractionSummary`**：新增 `standard_structure_type`、`skip_reason`、`test_submitted`
8. **`extract_indicators` 函数**：处理 `standard_structure_type` 判断结果，跳过逻辑
9. **后端 API**：写入逻辑、新接口、SSE 事件字段
