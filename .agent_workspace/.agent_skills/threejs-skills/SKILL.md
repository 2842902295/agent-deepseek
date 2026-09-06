---
name: threejs-skills
description: Three.js/WebGL/3D 场景开发全套技能包，覆盖场景搭建、几何体、材质、纹理、光照、动画、交互、加载器、着色器、后处理、渲染优化及 Rapier 物理与 gameWIZARD 集成。当用户要做 Three.js 开发、WebGL 3D 场景、模型加载、Shader 编写、物理仿真或 gameWIZARD 引擎相关工作时触发。
---

# Three.js Skills Suite

本技能是 **Three.js / WebGL / 3D 开发** 的套件入口，内嵌 13 个专项子技能，按需读取对应子技能的 SKILL.md 后再按其规范执行。所有子技能位于本技能目录下的 `subskills/<子技能名>/SKILL.md`。

## 子技能清单与适用情形

| 子技能 | 何时使用 |
| --- | --- |
| `threejs-fundamentals` | 场景初始化、相机、渲染器、Object3D 层级、坐标系、变换 |
| `threejs-geometry` | 内置几何体、BufferGeometry、自定义网格、InstancedMesh |
| `threejs-materials` | PBR / Basic / Phong / ShaderMaterial、材质属性与性能优化 |
| `threejs-textures` | 纹理加载、UV、Cubemap、HDR 环境贴图、纹理参数 |
| `threejs-lighting` | 各类光源、阴影、IBL、光照性能 |
| `threejs-animation` | 关键帧、骨骼、Morph Target、AnimationMixer、程序化动画 |
| `threejs-interaction` | Raycaster、OrbitControls、鼠标/触摸、拾取与交互 |
| `threejs-loaders` | GLTF/GLB、纹理、HDR、加载进度与异步模式 |
| `threejs-shaders` | GLSL、ShaderMaterial、uniforms、自定义顶点/片元效果 |
| `threejs-postprocessing` | EffectComposer、Bloom、DOF、屏幕空间特效、调色 |
| `threejs-rendering` | gameWIZARD 渲染管线、视觉优化、确定性约束下的渲染改进 |
| `rapier-physics` | Rapier 物理世界、刚体/碰撞体、角色约束、确定性步进 |
| `gamewizard-integration` | gameWIZARD 架构集成、GameSpec→GameBuilder 契约、跨技能协调 |

## 使用流程

1. **识别任务所属子领域**：根据上表匹配最相关的子技能；跨领域任务可组合多个子技能。
2. **读取对应 SKILL.md**：用相对路径读取，例如：
   - `read_file("subskills/threejs-fundamentals/SKILL.md")`
   - `read_file("subskills/rapier-physics/SKILL.md")`
3. **严格按子技能规范执行**：每个子技能都有自己的 Quick Start、约束与边界说明，不要凭印象写代码。
4. **跨技能协作**：若任务同时涉及渲染+物理+集成，分别读取对应子技能，按各自边界分工；集成层以 `gamewizard-integration` 为最终安全把关。
5. **非 Three.js 任务不要触发本技能**：纯 2D Canvas、CSS 动画、非 3D 的前端样式不属于本套件范围。

## 注意事项

- 子技能中凡标注 "gameWIZARD" 的内容（`threejs-rendering` / `rapier-physics` / `gamewizard-integration`）专用于 gameWIZARD 引擎项目，普通 Three.js 项目优先使用其它通用子技能。
- 修改或扩展子技能时，保持其独立 SKILL.md 结构不变，便于后续单独升级。
- 所有代码示例以 ES Module 导入风格为准（`import * as THREE from "three"`）。
