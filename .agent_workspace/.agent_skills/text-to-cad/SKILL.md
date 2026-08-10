---
name: text-to-cad
description: >-
  Comprehensive CAD, robotics, fabrication, and manufacturing skill suite.
  Use for AutoCAD, 中望CAD, ZWCAD, 画图, 绘图, 制图, 操控CAD软件, drawing, drafting,
  3D modeling, 3D modelling, parametric modeling, STEP/STP, DXF, CNC, G-code,
  3D printing, robot URDF/SRDF, SDF, implicit CAD, OpenSCAD, sheet metal,
  SendCutSend, Bambu Lab, standard parts, and any CAD/CAE/CAM-related task.
  Also triggers on: 机械零件, 装配体, 钣金, 数控加工, 3D打印, 机器人模型,
  标准件, 法兰, 齿轮, 轴承, 外壳, 支架.
---

# CAD Skill Suite

A comprehensive CAD, robotics, fabrication, and manufacturing skill suite.
Source: https://github.com/earthtojake/text-to-cad (MIT License)

---

## ⚠️ MANDATORY RULES — 强制执行，不可跳过

在执行任何 CAD 相关任务之前，**必须**依次完成以下两项检查。违反任何一条即为严重错误。

### 规则 1：环境检查 — CAD 软件检测与联动

**每次触发本 skill 时，第一步必须检查当前系统中是否安装了 AutoCAD 或兼容 AutoCAD 的软件。**

检测方式（按优先级）：

1. **Windows COM 探测**（最可靠）：
   ```python
   import comtypes.client
   for prog_id in [
       'AutoCAD.Application',        # AutoCAD
       'AutoCAD.Application.26',     # AutoCAD 2025
       'AutoCAD.Application.25',     # AutoCAD 2024
       'ZWCAD.Application',          # 中望 CAD
       'BricscadApp.BricscadApplic', # BricsCAD
       'NanoCAD.Application',        # nanoCAD
   ]:
       try:
           app = comtypes.client.GetActiveObject(prog_id)
           print(f"[OK] 已检测到: {prog_id}")
       except Exception:
           pass
   ```

2. **注册表 / 进程探测**（备用）：
   - 检查注册表 `HKLM\SOFTWARE\Autodesk\AutoCAD` 或 `HKLM\SOFTWARE\ZWSOFT\ZWCAD` 路径
   - 检查进程列表中是否有 `acad.exe`、`ZWCAD.exe`、`bricscad.exe` 等

3. **统一连接函数（所有后续代码必须使用此函数，禁止硬编码 ProgID）**：

   ```python
   import comtypes.client

   # 按优先级排列的 ProgID 列表——检测到谁就用谁
   _CAD_PROG_IDS = [
       'AutoCAD.Application',        # AutoCAD（通用）
       'AutoCAD.Application.26',     # AutoCAD 2025
       'AutoCAD.Application.25',     # AutoCAD 2024
       'ZWCAD.Application',          # 中望 CAD
       'BricscadApp.BricscadApplic', # BricsCAD
       'NanoCAD.Application',        # nanoCAD
   ]

   _cad_app = None   # 模块级缓存，避免重复探测
   _cad_name = None  # 人类可读的软件名

   def connect_cad():
       """探测并连接当前运行的 CAD 软件，返回 (app, doc, ms, cad_name)。
       所有 COM 示例代码必须通过此函数获取 app 对象，禁止硬编码 ProgID。"""
       global _cad_app, _cad_name
       if _cad_app is not None:
           try:
               doc = _cad_app.ActiveDocument
               return _cad_app, doc, doc.ModelSpace, _cad_name
           except Exception:
               _cad_app = None  # 连接已断开，重新探测

       for prog_id in _CAD_PROG_IDS:
           try:
               app = comtypes.client.GetActiveObject(prog_id)
               _cad_app = app
               _cad_name = {
                   'ZWCAD.Application': '中望 CAD',
                   'BricscadApp.BricscadApplic': 'BricsCAD',
                   'NanoCAD.Application': 'nanoCAD',
               }.get(prog_id, 'AutoCAD')
               doc = app.ActiveDocument
               print(f"[OK] 已连接: {_cad_name} ({prog_id})")
               return app, doc, doc.ModelSpace, _cad_name
           except Exception:
               continue

       raise RuntimeError(
           "未检测到任何兼容 CAD 软件。请确保 AutoCAD / 中望 CAD / BricsCAD 等已启动。"
       )
   ```

   > **使用规则：** 后续所有方式 A / A+ / B 的代码示例中，获取 `app` / `doc` / `ms` 的方式统一为：
   > ```python
   > app, doc, ms, cad_name = connect_cad()
   > ```
   > 禁止再写 `comtypes.client.CreateObject('AutoCAD.Application.26', ...)` 这类硬编码。

**判定与行为：**

- **如果检测到 CAD 软件** → **必须启用 COM 联动模式，且禁止将文件作为产物登记/保存给用户。**

  **核心原则：用户要的不是一个文件，而是结果直接出现在 CAD 软件里。**

  具体行为要求：
  1. **能实时 COM 操作的，绝不先生成文件再导入**——这是最高优先级原则。凡是 CAD COM API 支持的图元（直线、圆、弧、多段线、3D 实体、块、标注等——AutoCAD 和中望 CAD 的 COM API 高度一致），必须直接通过 `ModelSpace.AddXxx()` 在 CAD 里实时绘制，让用户亲眼看到图形一步步出现。
  2. **只有 COM API 无法完成的操作，才退回到文件导入**——例如放样、变截面扫掠、渐开线齿形、螺纹、非均匀圆角链等 COM API 真正无法表达的几何，才走 build123d 生成 STEP 再通过 `_IMPORT` 导入。布尔运算、拉伸、旋转均可用 COM 完成，不属于此列。
  3. **不要把生成的文件登记为 agent 产物（artifact）**——不要走 qa_agent 那套"保存文件 → 告知用户路径"的流程
  4. **文件保留在磁盘，不要删除**——CAD 软件引用磁盘文件，删除会导致后续编辑出错。保存到工作区或项目目录下的合理位置
  5. 回复用户时，告知"已在 [软件名] 中导入/绘制完成"，而不是"文件已保存到 xxx 路径"

  > ⛔ **严禁用 `_OPEN` 或 `doc.Open()` 打开 STEP/STP/IGES 文件！**
  >
  > AutoCAD / 中望 CAD 的 `_OPEN` / `doc.Open()` 只支持 `.dwg` 和 `.dxf`。对 STEP/STP/IGES/SAT 等交换格式使用 `_OPEN` 会报 **"找不到指定的图形文件"** 错误。
  >
  > **正确的文件打开方式：**
  >
  > | 文件格式 | 正确命令 | 错误命令 |
  > |---|---|---|
  > | `.step` / `.stp` | `_IMPORT` | ~~`_OPEN`~~、~~`doc.Open()`~~ |
  > | `.iges` / `.igs` | `_IMPORT` | ~~`_OPEN`~~ |
  > | `.sat` (ACIS) | `_IMPORT` | ~~`_OPEN`~~ |
  > | `.dwg` | `_OPEN` 或 `doc.Open()` | — |
  > | `.dxf` | `_OPEN` 或 `_DXFIN` | — |
  >
  > **COM 代码对比：**
  > ```python
  > # ✅ 正确：STEP 用 IMPORT
  > doc.SendCommand(f'(command "_IMPORT" "{step_path}")\n')
  >
  > # ❌ 错误：STEP 用 OPEN（会报"找不到指定的图形文件"）
  > doc.SendCommand(f'(command "_OPEN" "{step_path}")\n')
  > doc.Open(step_path)  # 同样错误
  > ```

  **判断标准——何时用分步 COM 绘制，何时退回文件导入：**

  | 任务类型 | 方式 | 示例 COM API |
  |---|---|---|
  | 2D 图元（直线、圆、弧、矩形、多段线） | ✅ 实时 COM | `AddLine`, `AddCircle`, `AddArc`, `AddLightWeightPolyLine` |
  | 标注、文字、图层操作 | ✅ 实时 COM | `AddDimAligned`, `AddText`, `AddLayer` |
  | 块定义与插入 | ✅ 实时 COM | `AddBlock`, `InsertBlock` |
  | 3D 基本体素（长方体、圆柱、球、圆环） | ✅ 分步 COM | `AddBox`, `AddCylinder`, `AddSphere`, `AddTorus` |
  | 3D 拉伸 / 旋转体（等截面特征） | ✅ 分步 COM | `AddExtrudedSolid`, `AddRevolvedSolid`（先建 Region） |
  | 布尔运算组合（并集 / 差集 / 交集：打孔、切槽、凸台） | ✅ 分步 COM | `solid.Boolean(0/1/2, other)` |
  | 复杂曲面（放样、变截面扫掠、渐开线齿形、螺纹、非均匀圆角链） | 📁 STEP 导入 | build123d 生成 STEP → `_IMPORT` |
  | DXF 整图导入 | 📁 文件导入 | ezdxf 生成 → `_DXFIN` |

  > **核心原则：只要零件能用「基本体素 + 拉伸/旋转 + 布尔运算」表达，就必须走分步 COM，禁止直接出 STEP 文件。**
  > 只有放样、变截面扫掠、精密曲线齿形等 COM API 真正无法表达的几何，才允许退回 STEP 导入。

  参见下方「Quick Start: CAD COM 联动」获取 COM 代码示例。

- **如果未检测到 CAD 软件** → 告知用户未检测到兼容 CAD 软件，建议安装以启用联动。退回到纯文件生成模式（生成 STEP/DXF 等文件并登记为产物），但必须明确告知用户结果只是文件。
- **无论如何都不可静默跳过此检查。**

### 规则 2：build123d 依赖 — 必须安装

**build123d 是本 skill 的核心 CAD 内核，凡是需要生成 STEP 文件的任务（方式 B、无 CAD 软件时的纯文件模式）绝对不可跳过。**

> **例外**：纯 COM 绘制（方式 A / A+）不涉及 build123d，此时可跳过此检查。但只要任务需要生成 STEP 文件，build123d 就必须可用。

每次触发本 skill 时，若任务需要生成 STEP，必须验证 build123d 可用：

```bash
python -c "import build123d; print('build123d OK, version:', build123d.__version__)"
```

- **如果导入成功** → 继续。
- **如果导入失败** → **立即安装，不可继续执行任何建模任务**：
  ```bash
  pip install build123d
  ```
  安装后再次验证导入。如果安装失败，必须向用户报告错误并停止，**不得尝试用替代方案绕过 build123d**。

> **为什么不可跳过：** 所有 3D 参数化建模（STEP 生成、布尔运算、倒角、装配）都依赖 build123d 底层的 OpenCascade 内核。没有它，所有建模命令都会直接报错。

---

## How This Skill Works

This is a **router skill**. When the user asks about CAD/robotics/fabrication,
read the matching sub-skill's `SKILL.md` from the path below, then follow
its instructions. Only load the sub-skill that matches the task — do not
load all of them at once.

**CAD COM 联动层（本 SKILL.md 独有，子 skill 中没有）：**

本 SKILL.md 在子 skill 工作流之上叠加了一个 CAD COM 交付层（支持 AutoCAD、中望 CAD、BricsCAD 等兼容 COM 接口的软件）。两者的关系是：

- **子 skill 负责建模**：CAD 简报、build123d 源码、`scripts/step` 生成、`scripts/inspect` 验证、`scripts/snapshot` 截图——这套流程不变。
- **本 SKILL.md 负责交付**：检测到 CAD 软件后，把建模结果通过 COM 呈现给用户（分步绘制或 `_IMPORT` 导入），而不是把文件路径作为主要交付物告知用户。
- **方式 A / A+（纯 COM）**：几何完全可用 COM 表达时，直接在 CAD 里画，不生成 STEP，子 skill 的 `scripts/*` 流程跳过。
- **方式 B（STEP 导入）**：几何需要 build123d 时，完整走子 skill 流程生成并验证 STEP，再用 `_IMPORT` 导入 CAD 软件。STEP 文件保留在磁盘供验证和 `$cad-viewer` 使用，但**不向用户报告文件路径**。

## Sub-Skills

| Trigger Keywords | Sub-Skill | Path | Description |
|---|---|---|---|
| 3D part, STEP, STP, build123d, assembly, extrude, hole, fillet, chamfer, enclosure, bracket, gear, shaft | **CAD (core)** | `references/cad/SKILL.md` | Parametric 3D modeling with build123d. STEP-first workflow. Assemblies with joints and mating. |
| 2D drawing, DXF, floor plan, layout, technical drawing, sheet metal flat pattern | **DXF** | `references/dxf/SKILL.md` | 2D DXF drawing generation. Layers, dimensions, annotations. |
| G-code, CNC, toolpath, milling, turning, 3D print slice, feed rate, spindle | **G-code** | `references/gcode/SKILL.md` | CNC toolpath generation and 3D print slicing. G-code validation. |
| view, preview, render, 3D viewer, GLB, snapshot, screenshot | **CAD Viewer** | `references/cad-viewer/SKILL.md` | 3D visualization and review. GLB/topology rendering. |
| SDF, implicit surface, metaball, signed distance, mathematical surface, gyroid, lattice infill | **SDF** | `references/sdf/SKILL.md` | Signed Distance Field modeling. Mathematical surfaces and volumetric design. |
| implicit CAD, OpenSCAD, CSG, constructive solid geometry | **Implicit CAD** | `references/implicit-cad/SKILL.md` | Implicit/CSG modeling with ImplicitCAD. |
| URDF, robot description, robot model, joint limits, link, inertial | **URDF** | `references/urdf/SKILL.md` | Robot URDF model generation. Links, joints, inertials, collision geometry. |
| SRDF, semantic robot, self-collision, planning group, end effector, gripper | **SRDF** | `references/srdf/SKILL.md` | Robot SRDF semantic descriptions. Planning groups, disabled collisions. |
| standard part, bolt, screw, bearing, nut, washer, off-the-shelf, purchasable component | **STEP Parts** | `references/step-parts/SKILL.md` | Search and import standard purchasable components from STEP libraries. |
| SendCutSend, sheet metal, laser cut, bend, quote, manufacturing cost, CNC cut | **SendCutSend** | `references/sendcutsend/SKILL.md` | Sheet metal design and online manufacturing quoting via SendCutSend API. |
| Bambu Lab, 3D printer, FTPS, MQTT, print job, slicer, filament | **Bambu Labs** | `references/bambu-labs/SKILL.md` | Bambu Lab 3D printer LAN control. Upload and start print jobs. |

## Shared Python Packages

All sub-skills share the `cadpy` package for STEP generation, assembly,
inspection, and export:

- **cadpy source**: `references/cad/scripts/packages/cadpy/`
- **Install**: `pip install -e references/cad/scripts/packages/cadpy`
- **Key modules**:
  - `cadpy.assembly` — AssemblyHelper, joints, mating
  - `cadpy.generation` — STEP generation pipeline
  - `cadpy.step_export` — STEP file export
  - `cadpy.catalog` — Source file parsing and target resolution
  - `cadpy.render` — GLB/mesh rendering

## External Dependencies

- **build123d** (`pip install build123d`) — Parametric 3D CAD kernel (OpenCascade)
- **ezdxf** (`pip install ezdxf`) — DXF file generation (bundled with build123d)
- **comtypes** (`pip install comtypes`) — Windows COM automation (AutoCAD / 中望 CAD / BricsCAD 控制)
- **playwright** (`pip install playwright`) — Browser-based 3D snapshots

## Quick Start: 3D Part

1. Read `references/cad/SKILL.md` for the full workflow
2. Read `references/cad/references/build123d-modeling.md` for API patterns
3. Write a CAD brief per `references/cad/references/cad-brief.md`（建模前必须，不可跳过）
4. Write a Python script with `def gen_step():` returning a build123d shape
5. Export via `python scripts/step path/to/part.py`
6. Validate with `python scripts/inspect refs model.step --facts --planes --positioning`
7. **Snapshot（强制，不可跳过）**：`python scripts/snapshot model.step` — 即使验证全部通过也必须执行
8. Hand off STEP path to `$cad-viewer`（若已安装）

## Quick Start: CAD COM 联动（AutoCAD / 中望 CAD / BricsCAD）

Use Python COM automation to control a running CAD instance (AutoCAD, ZWCAD, BricsCAD, etc.).
**优先级：能用 COM 实时绘制的就直接画，不能的才生成文件再导入。不登记为 artifact。**

> **与建模工作流的关系（必读）**
>
> CAD COM 是**交付/展示层**，不替代子 skill 的建模流程。两种方式分工如下：
>
> | | 方式 A / A+（纯 COM 绘制） | 方式 B（STEP 导入） |
> |---|---|---|
> | 适用 | 几何完全可用 COM 基本操作表达 | 放样、变截面扫掠、渐开线等 COM 无法表达 |
> | CAD 简报 | ✅ 仍需先写（规划尺寸和步骤） | ✅ 必须先写 |
> | build123d / scripts/step | 不需要 | ✅ 必须执行 |
> | scripts/inspect 验证 | 不需要（视觉确认即可） | ✅ 必须执行 |
> | scripts/snapshot | 不需要 | ✅ **强制，不可跳过** |
> | $cad-viewer handoff | 不需要 | ✅ 仍需 handoff STEP 路径 |
> | 用户看到的 | 图形在 CAD 中逐步出现 | STEP 导入 CAD，一步到位 |
>
> **方式 B 完整流程**：CAD 简报 → build123d `gen_step()` 源码 → `python scripts/step part.py` → `python scripts/inspect refs model.step --facts --planes --positioning` → `python scripts/snapshot model.step`（强制）→ COM `_IMPORT` 导入 → 回复"已在 [检测到的 CAD 软件名] 中导入完成"。STEP 文件保留在磁盘供验证和 cad-viewer 使用，**不向用户报告文件路径**。

### 方式 A：实时 COM 操作（优先）

用户会亲眼看到图形在 CAD 中逐步出现——这才是真正的智能体验。

```python
import array

# connect_cad() 自动探测已运行的 CAD 软件（AutoCAD / 中望 / BricsCAD 等）
app, doc, ms, cad_name = connect_cad()
app.Visible = True

# 画一条直线
start = array.array('d', [0.0, 0.0, 0.0])
end = array.array('d', [100.0, 0.0, 0.0])
line = ms.AddLine(start, end)

# 画一个圆
center = array.array('d', [50.0, 50.0, 0.0])
circle = ms.AddCircle(center, 25.0)

# 画矩形（轻量多段线）
pts = array.array('d', [0,0, 100,0, 100,50, 0,50])  # x,y 交替
rect = ms.AddLightWeightPolyline(pts)
rect.Closed = True

# 刷新视图
doc.Regen(1)  # acAllViewports
app.ZoomExtents()

# 回复用户："已在 {cad_name} 中绘制完成"
```

### 方式 A+：分步 COM 3D 建模（推荐，大多数 3D 零件走这条路）

**将模型分解为「基本体素 → 拉伸/旋转特征 → 布尔运算」的步骤序列，逐步在 CAD 中构建，每步 `Regen` 刷新，让用户实时看着零件一步步成形——比一次性导入 STEP 体验好得多，速度也更快（无文件 I/O 和 STEP 解析开销）。**

#### 可用的 COM 3D 操作

| 操作 | COM 方法 | 说明 |
|---|---|---|
| 基本体素 | `AddBox` / `AddCylinder` / `AddSphere` / `AddTorus` / `AddEllipticalCone` | 直接创建 |
| 拉伸体 | `AddExtrudedSolid(region, height, taper)` | 需先将封闭多段线转为 Region |
| 旋转体 | `AddRevolvedSolid(region, axisPt, axisDir, angle)` | 截面绕轴旋转 |
| 布尔并集 | `solid1.Boolean(0, solid2)` | acUnion = 0 |
| 布尔差集 | `solid1.Boolean(2, solid2)` | acSubtraction = 2 |
| 布尔交集 | `solid1.Boolean(1, solid2)` | acIntersection = 1 |
| Polyline → Region | `ms.AddRegion(curveArray)` | 封闭曲线 → 截面域，供拉伸/旋转用 |

#### 建模步骤设计原则（必须遵守）

1. **先分解再动手**：开始绘制前，先在回复中列出建模步骤（如「① 底板 → ② 圆柱凸台 → ③ 打中心孔 → ④ 打螺栓孔」），让用户知道接下来会看到什么
2. **每步 `doc.Regen(1)`**：每完成一步就刷新视图，用户才能看到渐进过程
3. **布尔顺序：先并集后差集**：避免几何退化
4. **进度反馈**：每步完成后告知用户当前进度（「第 2/4 步完成：凸台 Ø40×15」）

#### 示例：带孔法兰盘（4 步分步绘制）

```python
import array
import math

app, doc, ms, cad_name = connect_cad()
app.Visible = True

def pt3(x, y, z):
    return array.array('d', [x, y, z])

# ── 第 1 步：底板（圆柱基体）──────────────────
base = ms.AddCylinder(pt3(0, 0, 0), 50.0, 10.0)   # R50, H10
doc.Regen(1)
# → 回复："第 1/4 步完成：底板 Ø100×10"

# ── 第 2 步：中心凸台（并集）───────────────────
boss = ms.AddCylinder(pt3(0, 0, 10), 20.0, 15.0)  # R20, H15，从底板顶面起
base.Boolean(0, boss)                               # acUnion
doc.Regen(1)
# → 回复："第 2/4 步完成：中心凸台 Ø40×15"

# ── 第 3 步：中心通孔（差集）───────────────────
hole = ms.AddCylinder(pt3(0, 0, -1), 8.0, 27.0)  # R8，贯穿全高
base.Boolean(2, hole)                               # acSubtraction
doc.Regen(1)
# → 回复："第 3/4 步完成：中心通孔 Ø16"

# ── 第 4 步：4×螺栓孔（循环差集）───────────────
for i in range(4):
    ang = math.radians(i * 90 + 45)
    bolt = ms.AddCylinder(pt3(35 * math.cos(ang), 35 * math.sin(ang), -1), 4.0, 12.0)
    base.Boolean(2, bolt)
doc.Regen(1)
# → 回复："第 4/4 步完成：4×Ø8 螺栓孔，绘制完毕"

# 切换到等轴测视图
doc.SendCommand('VPOINT 1,-1,1 \n')
app.ZoomExtents()
doc.SendCommand('_VSCURRENT Conceptual \n')
# → 回复："已在 {cad_name} 中分步绘制完成法兰盘"
```

#### 示例：L 型支架（封闭截面 → Region → 拉伸）

```python
# 假设 app, doc, ms 已通过 connect_cad() 获取

# 先画封闭 L 型截面
pts = array.array('d', [0,0, 40,0, 40,10, 10,10, 10,30, 0,30])
profile = ms.AddLightWeightPolyline(pts)
profile.Closed = True

# Polyline → Region（AddRegion 接收 COM 对象数组）
import comtypes
curves = (comtypes.pointer(profile),)  # 或用 array.array('O', [profile])
regions = ms.AddRegion(curves)          # 返回 Region 元组

# 拉伸高度 20，锥度 0
solid = ms.AddExtrudedSolid(regions[0], 20.0, 0.0)
profile.Delete()   # 清理辅助截面线
doc.Regen(1)
# → 回复："L 型支架拉伸完成"
```

### 方式 B：文件导入（真正的最后手段）

仅用于 COM API 真正无法表达的几何：放样、变截面扫掠、渐开线齿形、螺纹、非均匀圆角链等。**布尔运算、拉伸、旋转不属于此列，那些必须走方式 A+。**

```python
import os

app, doc, ms, cad_name = connect_cad()
app.Visible = True

# 生成 STEP 到工作区目录（保留文件，不登记为产物）
workspace_dir = os.path.join(os.getcwd(), "cad_output")
os.makedirs(workspace_dir, exist_ok=True)
step_path = os.path.join(workspace_dir, "model.step").replace("/", "\\")

# ── build123d 完整工作流（在 COM 导入之前必须完成）──────────────
# 1. 写 CAD 简报（references/cad/references/cad-brief.md）
# 2. 写 build123d 源码，定义 def gen_step(): ...
# 3. python scripts/step path/to/part.py -o cad_output/model.step
# 4. python scripts/inspect refs cad_output/model.step --facts --planes --positioning
# 5. python scripts/snapshot cad_output/model.step   ← 强制，不可跳过
# 6. 验证通过后再执行下方 COM 导入

# 通过 COM 在 CAD 中直接导入（注意：STEP 必须用 _IMPORT，不可用 _OPEN）
# FILEDIA 0 禁用文件对话框，防止弹出"观察角度"等交互式输入提示阻塞流程
doc.SendCommand('FILEDIA 0\n')
doc.SendCommand(f'(command "_IMPORT" "{step_path}")\n')
doc.SendCommand('\n')  # 接受剩余命令行提示的默认值（观察角度等）
doc.SendCommand('FILEDIA 1\n')

# 切换到 3D 视图
doc.SendCommand('VPOINT 1,-1,1 \n')
app.ZoomExtents()
doc.SendCommand('_VSCURRENT Conceptual \n')

# 文件保留在磁盘，用户可在 CAD 中继续编辑
# 回复用户："已在 {cad_name} 中导入模型"
```

---

## 中望 CAD（ZWCAD）兼容性说明

中望 CAD 的 COM API 与 AutoCAD 高度兼容（基于相同的 ActiveX 接口规范），绝大多数操作可以直接复用。以下是需要注意的差异点：

### 完全兼容的操作（无需任何修改）

| 类别 | 方法 | 说明 |
|---|---|---|
| 2D 图元 | `AddLine`, `AddCircle`, `AddArc`, `AddLightWeightPolyline`, `AddSpline` | 参数签名完全一致 |
| 标注 / 文字 | `AddDimAligned`, `AddDimDiametric`, `AddText`, `AddMText` | 完全一致 |
| 图层 | `doc.Layers.Add()`, `layer.Color` | 完全一致 |
| 块操作 | `AddBlock`, `InsertBlock` | 完全一致 |
| 3D 体素 | `AddBox`, `AddCylinder`, `AddSphere`, `AddTorus` | 完全一致 |
| 拉伸 / 旋转 | `AddExtrudedSolid`, `AddRevolvedSolid` | 完全一致 |
| 布尔运算 | `solid.Boolean(0/1/2, other)` | 完全一致 |
| 视图控制 | `doc.Regen()`, `app.ZoomExtents()`, `VPOINT` | 完全一致 |

### 需要注意的差异

| 差异点 | AutoCAD | 中望 CAD | 处理方式 |
|---|---|---|---|
| **ProgID** | `AutoCAD.Application.*` | `ZWCAD.Application` | ✅ `connect_cad()` 已自动处理 |
| **`_VSCURRENT` 视觉样式** | 支持 `Conceptual` / `Realistic` / `Shaded` 等 | 部分旧版本仅支持 `2D Wireframe` / `3D Wireframe` / `Hidden` | 如果 `_VSCURRENT Conceptual` 报错，改用 `_VSCURRENT Shaded` 或 `_VSCURRENT 3D Wireframe` |
| **STEP 导入** | `_IMPORT` 原生支持 STEP | 专业版支持 `_IMPORT`；标准版可能不支持 3D 实体导入 | 若 `_IMPORT` 失败，退回到 DXF 导入或提示用户升级版本 |
| **`SendCommand` 异步** | 命令通过消息队列异步执行 | 行为一致，但部分版本命令执行速度更快 | 无需特殊处理 |
| **COM 事件回调** | 支持 `ObjectModified` 等事件监听 | 部分事件可能不支持 | 本 skill 不使用事件回调，无影响 |

### 中望 CAD 版本建议

- **ZWCAD 2024+**：COM API 完整度最高，3D 实体操作、视觉样式、STEP 导入均稳定
- **ZWCAD 2022~2023**：2D 和基础 3D 操作稳定，`_VSCURRENT` 视觉样式可能有限
- **ZWCAD 标准版**：不含 3D 实体功能，方式 A+ 的 3D 建模不可用，需退回 DXF 2D 或提示用户

### 错误处理建议

当 `connect_cad()` 返回 `cad_name == '中望 CAD'` 时，建议在 3D 操作前加一个轻量探测：

```python
if cad_name == '中望 CAD':
    try:
        # 试探性创建一个临时圆柱并立即删除，验证 3D 实体是否可用
        test = ms.AddCylinder(pt3(0, 0, 0), 1.0, 1.0)
        test.Delete()
    except Exception:
        print("[WARN] 当前中望 CAD 版本可能不支持 3D 实体操作，将退回 2D DXF 模式")
        # 切换到 DXF 文件生成模式
```
