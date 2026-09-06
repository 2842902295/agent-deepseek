/** 技能商店分类（词表运行时真相源 = DB agent_skill_category，管理员/system-admin 可配）。
 * 后端 app/api/v1/ai/agent_skill.py::SKILL_CATEGORIES 仅是首启播种基线，前端不再硬编码词表。 */

/** 「其他」= 永久兜底桶：未设置/词表外取值都落这里，不参与 DB 词表增删 */
export const OTHER_CATEGORY = '其他';

/** 分类归一：不在当前词表内的取值（含空）一律落「其他」，与后端 _norm_category 同口径 */
export function displayCategory(category: string | null | undefined, categories: string[]): string {
  const c = (category || '').trim();
  return c && categories.includes(c) ? c : OTHER_CATEGORY;
}
