/**
 * 品牌专属板型注册表（扩展点）——工作流画板体系内的品牌差异化，按 BRAND_VARIANT 配置文件区分。
 *
 * 通用 8 类节点（text/task/data/conclusion/file/review/start/end）全品牌共有；
 * 这里登记**某品牌独有**的节点类型，画布 / 添加节点菜单 / 右键菜单 / 拖线菜单按注册表自动长出对应项。
 * 板子本身仍是普通工作流板（新建入口不变），品牌卡型由 agent 播种或用户从菜单添加。
 *
 * 以后为某品牌加一种独特卡型只需：
 * 1. 在 BRAND_BOARDS 对应品牌的 nodeTypes 里加一条；
 * 2. wf-node.vue 的 variant 分支加该卡型渲染（computed 映射 + 模板 + 样式）；
 * 3. wf-icon.vue 加同名图标；
 * 4. 后端 workflow_tools.py 按品牌条件补该卡型的词表说明 + 三处节点标签分支（agent 才会用）。
 * 不动路由、不动菜单表、不建新表——实例照旧落 agent_workflow。
 */
import {getBrandVariant} from '@/utils/brand-config';

/** 品牌独有节点类型定义（菜单项 / 新建默认值 / 布局估值都从这取） */
export interface BrandNodeTypeDef {
  /** 节点类型 key（= nodeTypes 注册 key = wf-node.vue variant） */
  nodeType: string;
  /** 中文名（菜单 / 回显标签用） */
  zh: string;
  /** 添加节点菜单里的提示语 */
  tip?: string;
  /** wf-icon.vue 图标名 */
  icon: string;
  /** 菜单图标颜色 */
  color: string;
  /** 新建该卡时的默认 data */
  newData: Record<string, any>;
  /** 自动布局尺寸估值 [w, h] */
  sizeHint: [number, number];
  /** 回显标签的「当家字段」提取（nodeLabel 用） */
  labelOf?: (data: Record<string, any>) => string;
  /** 增删节点时的内容速览（nodeBrief 用） */
  briefOf?: (data: Record<string, any>) => string;
}

interface BrandBoardDef {
  nodeTypes: BrandNodeTypeDef[];
}

const BRAND_BOARDS: Record<'standard' | 'generic', BrandBoardDef> = {
  // standard 版：预留——后续 standard 独特卡型在此追加
  standard: {
    nodeTypes: []
  },
  // generic 版：创意生产向——首发「分镜段卡」（segNode，分镜板用：一卡一场戏，场内承载多条分镜）
  generic: {
    nodeTypes: [
      {
        nodeType: 'segNode',
        zh: '分镜段',
        tip: '一场戏一张卡：情绪 / 场景 / 状态 + 逐条分镜',
        icon: 'shot',
        color: '#0e7490',
        newData: {seg: '', duration: '', emotion: '', scene: '', state: '', shots: [], note: ''},
        sizeHint: [292, 320],
        labelOf: d => [d.seg, d.duration].filter(Boolean).join(' '),
        briefOf: d => `${Array.isArray(d.shots) ? d.shots.length : 0} 条分镜`
      }
    ]
  }
};

/** 当前品牌（BRAND_VARIANT 配置）的独有节点类型 */
export function brandNodeTypes(): BrandNodeTypeDef[] {
  return BRAND_BOARDS[getBrandVariant()]?.nodeTypes ?? [];
}
