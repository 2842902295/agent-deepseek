interface BrandText {
  workbenchTitle: string;
  workbenchSub: string;
  assistantName: string;
  assistantDesc: string;
  assistantBadge: string;
  footerCapability: (n: number) => string;
  footerPkg: (total: number, discovered: number, derived: number) => string;
  emptyCapability: string;
  emptyPkg: string;
  qaSidebarTitle: string;
  qaWelcomeLine1: string;
  qaWelcomeLine2: string;
  qaWelcomeLine3: string;
  qaWelcomeSub: string;
  qaSuggestions: string[];
  qaCapabilities: { label: string; hint: string; prompt: string }[];
}

const BRAND_STANDARD: BrandText = {
  workbenchTitle: "同道·标准 AI 助理中心",
  workbenchSub: "STANDARD · AI ASSISTANT",
  assistantName: "全能助理",
  assistantDesc: "处理各类文档、检索在线信息、标准查询比对、报告生成…开放问答，能干的远不止这些",
  assistantBadge: "全能助理",
  footerCapability: n => `${n} 个能力 · 专注标准全生命周期的 AI 助理团队`,
  footerPkg: (total, discovered, derived) => `${total} 个技能包 · 发现 ${discovered} · 凝练 ${derived}`,
  emptyCapability: "还没有 AI 助理，先在全能助理这跑一段对话再凝练出一位吧",
  emptyPkg: '还没有技能包，点右上角"发现技能"让 AI 助理帮你找一些',
  qaSidebarTitle: "同道·标准 AI 助理",
  qaWelcomeLine1: "对话即工作",
  qaWelcomeLine2: "所问皆所学",
  qaWelcomeLine3: "所做皆所能",
  qaWelcomeSub: "标准数据库与 AI 助理就绪。聊出来的结论可整理进「知识库」成为你的知识，也可提炼成 @ 调用的技能，下一次直接复用。",
  qaSuggestions: [
    "GB 10631-2025 对运输包装都有哪些具体规定？",
    "对比 GB/T 28590 和 ISO 28590 的指标差异，导出 Excel 报告",
    "解析这份新标准 PDF，抽出所有技术指标做对比表",
    "对\"白酒\"标准池跑一次自动去重，生成疑似重复报告"
  ],
  qaCapabilities: [
    { label: "标准查询", hint: "快速检索标准库", prompt: "GB 10631-2025 对运输包装都有哪些具体规定？" },
    { label: "文档解析", hint: "PDF 提取指标", prompt: "解析这份新标准 PDF，抽出所有技术指标做对比表" },
    { label: "数据分析", hint: "对比与报告", prompt: "对比 GB/T 28590 和 ISO 28590 的指标差异，导出 Excel 报告" },
    { label: "自动去重", hint: "标准池清理", prompt: "对\"白酒\"标准池跑一次自动去重，生成疑似重复报告" }
  ]
};

const BRAND_GENERIC: BrandText = {
  workbenchTitle: "同道 AI 助理中心",
  workbenchSub: "AI · ASSISTANT",
  assistantName: "全能助理",
  assistantDesc: "自由提问，AI 助理自动调用工具完成复杂查询与分析",
  assistantBadge: "全能助理",
  footerCapability: n => `${n} 个能力 · 在全能助理中完成任务后可一键凝练为 AI 助理`,
  footerPkg: (total, discovered, derived) => `${total} 个技能包 · 发现 ${discovered} · 凝练 ${derived}`,
  emptyCapability: "还没有 AI 助理，先在全能助理这跑一段对话再凝练出一位吧",
  emptyPkg: '还没有技能包，点右上角"发现技能"让 AI 助理帮你找一些',
  qaSidebarTitle: "AI 助理",
  qaWelcomeLine1: "对话即工作",
  qaWelcomeLine2: "所问皆所学",
  qaWelcomeLine3: "所做皆所能",
  qaWelcomeSub: "AI 助理就绪。聊出来的结论可整理进「知识库」成为你的知识，也可提炼成 @ 调用的技能，下一次直接复用。",
  qaSuggestions: [
    "解析这份 PDF 并翻译成中文，保留原结构",
    "分析这份 CSV 数据，找出异常值并画散点图",
    "联网查 2026 年最值得关注的 AI 应用，整理成 Markdown 报告",
    "对销售数据做趋势分析，画图预测下季度走向"
  ],
  qaCapabilities: [
    { label: "文档解析", hint: "PDF 翻译保留结构", prompt: "解析这份 PDF 并翻译成中文，保留原结构" },
    { label: "数据分析", hint: "CSV 异常检测", prompt: "分析这份 CSV 数据，找出异常值并画散点图" },
    { label: "联网搜索", hint: "整理为 Markdown", prompt: "联网查 2026 年最值得关注的 AI 应用，整理成 Markdown 报告" },
    { label: "趋势预测", hint: "可视化与展望", prompt: "对销售数据做趋势分析，画图预测下季度走向" }
  ]
};

import { getBrandVariant } from '@/utils/brand-config';

export const brand: BrandText = new Proxy({} as BrandText, {
  get(_, prop: string) {
    const active = getBrandVariant() === 'generic' ? BRAND_GENERIC : BRAND_STANDARD;
    return active[prop as keyof BrandText];
  }
});
