import {request} from '../request';
import axios from 'axios';
import {getServiceBaseURL} from '@/utils/service';
import {localStg} from '@/utils/storage';
import {ensureFreshAccessToken, rejectIfJsonError} from '../request/shared';

/**
 * 获取模型预设清单 + 当前选中（仅超管可见）
 */
export function fetchModelConfig() {
  return request<Api.AI.ModelConfig>({
    url: '/ai/model-config',
    method: 'get'
  });
}

/**
 * 切换全局模型（仅超管，切换后对所有用户生效）
 *
 * @param category chat / image / video
 * @param selectedKey 选中的预设块名（如 CHAT_GROK / CHAT_DASHSCOPE / IMAGE_QWEN）
 */
export function fetchSetModelConfig(category: string, selectedKey: string) {
  return request<null>({
    url: '/ai/model-config',
    method: 'put',
    data: {category, selectedKey}
  });
}

/**
 * 按角色模型配置清单（仅超管）：全部角色行 × 三态值 + 三类预设块选项 + 当前全局激活块
 */
export function fetchRoleModelConfig() {
  return request<Api.AI.RoleModelConfigData>({
    url: '/ai/role-model-config',
    method: 'get'
  });
}

/**
 * 保存按角色模型配置（仅超管）；三字段全 null = 删行（回退跟随全局）
 *
 * @param data roleCode + chatBlockKey/imageBlockKey/videoBlockKey（null=跟随全局；image/video 可 "DISABLED"）
 */
export function fetchSetRoleModelConfig(data: Api.AI.RoleModelConfigUpdate) {
  return request<null>({
    url: '/ai/role-model-config',
    method: 'put',
    data
  });
}

// ── 对话模式（DB 驱动可增删改 + 思考强度滑块档位，用户偏好全员可用） ─────────

/**
 * 当前用户的对话模式偏好（含模式清单元数据；每模式带 levels/levelDefault 档位信息）
 */
export function fetchChatModePref() {
  return request<Api.AI.ChatModePref>({
    url: '/ai/chat-mode',
    method: 'get'
  });
}

/**
 * 保存当前用户的对话模式偏好（省略的字段不改；写后旧 agent 实例被弹出，下一条消息生效）
 */
export function fetchSetChatModePref(data: Api.AI.ChatModePrefUpdate) {
  return request<null>({
    url: '/ai/chat-mode',
    method: 'put',
    data
  });
}

/**
 * 对话模式配置清单（仅超管）：模式行（label/note/sortOrder/块指派）+ chat 预设块选项 + 全局兜底块
 */
export function fetchChatModeConfig() {
  return request<Api.AI.ChatModeConfigData>({
    url: '/ai/chat-mode/config',
    method: 'get'
  });
}

/**
 * 保存对话模式配置（仅超管）：改 label/note/块指派/排序（mode 定位行，key 不可改；chatBlockKey null = 走兜底链）
 */
export function fetchSetChatModeConfig(data: Api.AI.ChatModeConfigUpdate) {
  return request<null>({
    url: '/ai/chat-mode/config',
    method: 'put',
    data
  });
}

/**
 * 新增对话模式（仅超管；key 省略自动生成 mode_{n}）
 */
export function fetchCreateChatModeConfig(data: Api.AI.ChatModeConfigCreate) {
  return request<{ key: string }>({
    url: '/ai/chat-mode/config',
    method: 'post',
    data
  });
}

/**
 * 删除对话模式（仅超管；balanced 系统兜底不可删，使用该模式的用户自动回落均衡）
 */
export function fetchDeleteChatModeConfig(key: string) {
  return request<null>({
    url: `/ai/chat-mode/config/${key}`,
    method: 'delete'
  });
}

// ── 用户档位（实体可见性分档：技能/专家/连接器/数据集共用） ─────────────────

/** 用户档位定义（agent_role_tier）：显式白名单口径，tierRank 仅作展示排序 */
export interface RoleTier {
  tierCode: string;
  tierName: string;
  tierRank: number;
}

/** 档位清单 + 当前用户所持档位 code（登录可读；管理员返回空数组=全部可见）；实体表单「可见范围」多选数据源 */
export function fetchRoleTiers() {
  return request<{tiers: RoleTier[]; myCodes: string[]}>({
    url: '/ai/role-tiers',
    method: 'get'
  });
}

// ── 向量库管理（统一向量库体系，/ai/vector-lib） ─────────────────────────────

/** 向量库列表（系统库 + 本人库；超管见全部），含当前激活向量模型块/维度 */
export function fetchVectorLibList() {
  return request<Api.AI.VectorLibListData>({
    url: '/ai/vector-lib',
    method: 'get'
  });
}

// ── 标准库同步（指纹增量，/ai/std-sync，仅管理员） ──────────────────────────

/** 标准库同步状态（运行标记 + 实时进度 + 最近一次记录 + 源库信息 + 定时开关） */
export function fetchStdSyncStatus() {
  return request<Api.AI.StdSyncStatus>({
    url: '/ai/std-sync/status',
    method: 'get'
  });
}

/** 手工触发一轮标准库同步（后台执行，重复触发后端 4000 拒绝） */
export function fetchStdSyncTrigger() {
  return request({
    url: '/ai/std-sync/trigger',
    method: 'post'
  });
}

/** 新建用户向量库（table_sync 来源仅超管） */
export function fetchVectorLibCreate(data: Api.AI.VectorLibCreatePayload) {
  return request<Api.AI.VectorLibRecord>({
    url: '/ai/vector-lib',
    method: 'post',
    data
  });
}

/** 删除向量库（写权限；构建中拒绝） */
export function fetchVectorLibDelete(libraryKey: string) {
  return request<null>({
    url: `/ai/vector-lib/${encodeURIComponent(libraryKey)}`,
    method: 'delete'
  });
}

/** 条目分页列表（keyword 可选：内容/编号模糊过滤；content 全文下发） */
export function fetchVectorLibItems(libraryKey: string, current: number, size: number, keyword?: string) {
  return request<Api.AI.VectorLibItemPage>({
    url: `/ai/vector-lib/${encodeURIComponent(libraryKey)}/items`,
    method: 'get',
    params: {current, size, ...(keyword ? {keyword} : {})}
  });
}

/** 构建进度查询（构建期有效；progress 为 null 表示无构建在跑） */
export function fetchVectorLibProgress(libraryKey: string) {
  return request<{building: boolean; progress: Api.AI.VectorLibProgress | null}>({
    url: `/ai/vector-lib/${encodeURIComponent(libraryKey)}/progress`,
    method: 'get'
  });
}

/** 测试检索：走统一 SearchService，验证召回效果（陈旧库返回 4000 提示） */
export function fetchVectorLibSearch(libraryKey: string, query: string, topK = 5) {
  return request<{records: Api.AI.VectorLibSearchHit[]; total: number}>({
    url: `/ai/vector-lib/${encodeURIComponent(libraryKey)}/search`,
    method: 'get',
    params: {query, topK}
  });
}

/** 批量添加手动条目（三档增量，不删既有条目；单次 ≤500） */
export function fetchVectorLibAddItems(libraryKey: string, items: Api.AI.VectorLibItemAdd[]) {
  return request<Api.AI.VectorLibIngestCounts>({
    url: `/ai/vector-lib/${encodeURIComponent(libraryKey)}/items`,
    method: 'post',
    data: {items}
  });
}

/** 按 itemKey 批量删除条目 */
export function fetchVectorLibDeleteItems(libraryKey: string, itemKeys: string[]) {
  return request<{deleted: number; itemCount: number}>({
    url: `/ai/vector-lib/${encodeURIComponent(libraryKey)}/items`,
    method: 'delete',
    data: {itemKeys}
  });
}

/** 构建/重建向量库（后台执行；陈旧库强制清空重嵌） */
export function fetchVectorLibBuild(libraryKey: string, wipe?: boolean) {
  return request<{wipeFirst: boolean}>({
    url: `/ai/vector-lib/${encodeURIComponent(libraryKey)}/build`,
    method: 'post',
    params: wipe === undefined ? undefined : {wipe}
  });
}

/** 取消构建（批间生效） */
export function fetchVectorLibCancel(libraryKey: string) {
  return request<null>({
    url: `/ai/vector-lib/${encodeURIComponent(libraryKey)}/cancel`,
    method: 'post'
  });
}

/**
 * 文件上传摄入（原生 fetch 绕 axios 拦截器上传 FormData，按红线先 token 预检）。
 * 注意：本端点成功/失败都返回 JSON 信封（非流/二进制端点），不能用 rejectIfJsonError
 * （它把「响应是 JSON」本身当错误），直接解析信封判 code。后台解析，三档增量。
 */
export async function fetchVectorLibUpload(libraryKey: string, file: File): Promise<{fileName: string; size: number}> {
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const {baseURL} = getServiceBaseURL(import.meta.env, isHttpProxy);
  const token = await ensureFreshAccessToken();
  const fd = new FormData();
  fd.append('file', file);
  const response = await fetch(`${baseURL}/ai/vector-lib/${encodeURIComponent(libraryKey)}/upload`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`
    },
    body: fd
  });
  if (!response.ok) {
    throw new Error(`上传失败: ${response.status}`);
  }
  const json = (await response.json().catch(() => null)) as {code: string; msg?: string; data?: {fileName?: string; size?: number}} | null;
  if (!json || json.code !== '0000') {
    throw new Error(json?.msg || '上传失败');
  }
  return {fileName: json.data?.fileName || file.name, size: json.data?.size || file.size};
}


/**
 * 批量标准查重（StructuredQAAgent 版本）
 *
 * mode: 'deep'（默认，准确但慢）/ 'fast'（嵌入式向量召回，速度快效果略差，建议批量>200时使用）
 */
export function fetchAgentBatchCheckDeduplication(data: { standard_nos: string[]; pool_id?: number; batch_name?: string; check_existing?: boolean; mode?: 'deep' | 'fast' }) {
  return request<Api.AI.BatchDeduplicationResponse>({
    url: '/ai/deduplication-agent/batch-check',
    method: 'post',
    data
  });
}

/**
 * 批量标准查重
 *
 * @param standard_nos 标准编号列表
 * @param pool_id 查重池ID（可选）
 */
export function fetchBatchCheckDeduplication(data: { standard_nos: string[]; pool_id?: number; batch_name?: string }) {
  return request<Api.AI.BatchDeduplicationResponse>({
    url: '/ai/deduplication/batch-check',
    method: 'post',
    data
  });
}

/**
 * 单个标准查重
 *
 * @param input_name 标准名称
 * @param input_use_range 适用范围
 * @param standard_no 标准编号
 */
export function fetchCheckDeduplication(data: {
  input_name: string;
  input_use_range?: string;
  standard_no?: string;
}) {
  return request<Api.AI.DeduplicationResponse>({
    url: '/ai/deduplication/check',
    method: 'post',
    data
  });
}

/**
 * 获取对象关系图数据
 */
export function fetchObjRelGraph(params: {
  subj_obj: string;
  depth?: number;
  rel_type?: string;
  confidence?: string;
  src_type?: string;
}) {
  return request<{ nodes: Api.AI.ObjRelNode[]; edges: Api.AI.ObjRelEdge[] }>({
    url: '/ai/standard-obj-rel/graph',
    method: 'get',
    params
  });
}

/**
 * 获取批量查重历史记录
 *
 * @param current 页码
 * @param size 每页数量
 */
export function fetchBatchHistory(params?: {
  current?: number;
  size?: number;
  batch_name?: string;
  status?: string;
  pool_id?: number;
  start_time?: string;
  end_time?: string;
}) {
  return request<Api.Common.PaginatingQueryRecord<Api.AI.BatchHistoryRecord>>({
    url: '/ai/deduplication/batch-history',
    method: 'get',
    params
  });
}

export function fetchBatchSummary() {
  return request<Api.AI.BatchSummary>({
    url: '/ai/deduplication/batch-summary',
    method: 'get'
  });
}

/**
 * 获取批量查重详情
 *
 * @param batch_id 批次ID
 * @param current 页码
 * @param size 每页数量
 * @param only_need_attention 只显示需要关注的
 * @param only_found 只显示找到的标准
 * @param filter_tags 标签筛选（逗号分隔）- 显示包含这些标签的标准
 * @param hidden_tags 隐藏标签筛选（逗号分隔）- 隐藏包含这些标签的标准
 * @param filter_tags_mode 标签筛选模式（any: 包含任一，all: 包含全部）
 * @param sort_field 排序字段
 * @param sort_order 排序顺序 (ascend/descend)
 * @param filter_standard_no 标准编号筛选（模糊匹配）
 * @param filter_standard_name 标准名称筛选（模糊匹配）
 * @param has_res_filter 是否有标准原文筛选 (all/has_res/no_res) - 筛选相似标准列表
 * @param main_has_res_filter 主标准是否有原文筛选 (''/has_res/no_res) - 筛选主标准列表
 */
export function fetchBatchDetail(
  batch_id: number,
  current: number = 1,
  size: number = 10,
  only_need_attention: boolean = false,
  only_found: boolean = true,
  filter_tags: string = '',
  hidden_tags: string = '',
  filter_tags_mode: string = 'any',
  sort_field: string | null = null,
  sort_order: string | null = null,
  filter_standard_no: string = '',
  filter_standard_name: string = '',
  has_res_filter: string = 'all',
  main_has_res_filter: string = '',
  filter_std_domain: string = ''
) {
  return request<Api.AI.BatchDetailResponse>({
    url: `/ai/deduplication/batch-detail/${batch_id}`,
    method: 'get',
    params: {
      current,
      size,
      only_need_attention,
      only_found,
      filter_tags,
      hidden_tags,
      filter_tags_mode,
      sort_field,
      sort_order,
      filter_standard_no,
      filter_standard_name,
      has_res_filter,
      main_has_res_filter,
      filter_std_domain: filter_std_domain || undefined
    },
    timeout: 99999999
  });
}

/**
 * 更新相似标准的需要关注标记
 */
export function updateSimilarAttention(
  batch_id: number,
  data: { standard_no: string; similar_standard_no: string; need_attention: boolean }
) {
  return request<Api.Common.CommonRecord<null>>({
    url: `/ai/deduplication/batch/${batch_id}/similar-attention`,
    method: 'patch',
    data
  });
}

/**
 * 根据标准号或记录ID获取相似标准列表（支持筛选/排序/分页）
 * record_id 优先；若只传 standard_no，则取最新一条查重记录
 */
export function fetchSimilarStandardsByNo(params: {
  record_id?: number;
  standard_no?: string;
  filter_tags?: string;
  hidden_tags?: string;
  filter_tags_mode?: 'any' | 'all';
  has_res_filter?: string;
  llm_score_min?: number;
  need_attention_only?: boolean;
  filter_std_domain?: string;
  sort_by?: 'llm_score' | 'tag';
  page?: number;
  page_size?: number;
}) {
  return request<
    Api.Common.CommonRecord<{
      list: any[];
      total: number;
      stats: { total: number; need_attention: number; ignored: number; same_series: number };
    }>
  >({
    url: '/ai/deduplication/similar-standards-by-no',
    method: 'get',
    params
  });
}

/**
 * 导出批次详情到 Excel
 *
 * @param batch_id 批次ID
 */
export async function exportBatchToExcel(batch_id: number) {
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const { baseURL } = getServiceBaseURL(import.meta.env, isHttpProxy);

  // 获取 token
  const token = await ensureFreshAccessToken();
  const Authorization = `Bearer ${token}`;

  const response = await axios.get(`${baseURL}/ai/deduplication/batch-export/${batch_id}`, {
    responseType: 'blob',
    headers: {
      Authorization,
      apifoxToken: 'XL299LiMEDZ0H5h3A29PxwQXdMJqWyY2'
    }
  });

  // 鉴权失败时后端返回 HTTP 200 + JSON 而非文件 blob，主动检测避免下载损坏文件
  if ((response.headers['content-type'] || '').includes('application/json')) {
    const err: {msg?: string} = await response.data.json().catch(() => ({}));
    throw new Error(err.msg || '导出失败');
  }

  return response.data;
}



/**
 * 导出单个标准的比对结果到 Word
 *
 * @param batch_id 批次ID
 * @param standard_no 标准编号
 */
export async function exportStandardToWord(batch_id: number, standard_no: string) {
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const { baseURL } = getServiceBaseURL(import.meta.env, isHttpProxy);

  // 获取 token
  const token = await ensureFreshAccessToken();
  const Authorization = `Bearer ${token}`;

  const response = await axios.get(`${baseURL}/ai/deduplication/export-standard-word`, {
    params: {
      batch_id,
      standard_no
    },
    responseType: 'blob',
    headers: {
      Authorization,
      apifoxToken: 'XL299LiMEDZ0H5h3A29PxwQXdMJqWyY2'
    }
  });

  // 鉴权失败时后端返回 HTTP 200 + JSON 而非文件 blob，主动检测避免下载损坏文件
  if ((response.headers['content-type'] || '').includes('application/json')) {
    const err: {msg?: string} = await response.data.json().catch(() => ({}));
    throw new Error(err.msg || '导出失败');
  }

  return response.data;
}


/**
 * 计算两个标准的全文相似度
 *
 * @param source_standard_no 源标准编号
 * @param target_standard_no 目标标准编号
 */
/** 标准整合评估 SSE 事件类型 */
export type StandardEvaluationEvent =
  | { type: 'tool_call'; step: number; tool: string; tool_display?: string; args: Record<string, unknown>; subagent?: boolean }
  | { type: 'tool_result'; step: number; tool: string; tool_display?: string; content: string; subagent?: boolean }
  | { type: 'thinking'; step: number; content: string }
  | { type: 'conclusion'; step: number; content: string }
  | { type: 'done'; steps: number }
  | { type: 'error'; message: string };

/**
 * 标准整合评估（流式 SSE）
 *
 * @param standard_no  标准号
 * @param onEvent      每收到一个 SSE 事件时回调
 * @param signal       AbortController signal，用于中止请求
 */
export async function fetchStandardEvaluationStream(
  standard_no: string,
  onEvent: (event: StandardEvaluationEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const { baseURL } = getServiceBaseURL(import.meta.env, isHttpProxy);
  const token = await ensureFreshAccessToken();
  const Authorization = `Bearer ${token}`;

  const response = await fetch(`${baseURL}/ai/standard-evaluation/evaluate/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization,
      apifoxToken: 'XL299LiMEDZ0H5h3A29PxwQXdMJqWyY2'
    },
    body: JSON.stringify({ standard_no }),
    signal
  });

  if (!response.ok || !response.body) {
    throw new Error(`请求失败: ${response.status}`);
  }
  await rejectIfJsonError(response);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.slice(6)) as StandardEvaluationEvent;
          onEvent(data);
        } catch {
          // 忽略解析异常
        }
      }
    }
  }
}

export function fetchFullTextSimilarity(data: Api.AI.FullTextSimilarityRequest) {
  return request<Api.AI.FullTextSimilarityResponse>({
    url: '/ai/full-text-similarity/calculate',
    method: 'post',
    data,
    timeout: 99999999
  });
}

/**
 * AI 智能比对两篇标准（表格形式）
 *
 * @param source_standard_no 源标准编号
 * @param target_standard_no 目标标准编号
 * @param force_recalculate 是否强制重新计算
 */
export function fetchAIComparison(data: Api.AI.AIComparisonRequest) {
  return request<Api.AI.AIComparisonResponse>({
    url: '/ai/ai-comparison/compare-smart-v2',
    method: 'post',
    data,
    timeout: 99999999
  });
}

export function fetchAIComparisonSmart(data: Api.AI.AIComparisonRequest) {
  return request<Api.AI.AIComparisonResponse>({
    url: '/ai/ai-comparison/compare-smart-v2',
    method: 'post',
    data,
    timeout: 99999999
  });
}

export function fetchAIComparisonBatch(items: Api.AI.AIComparisonRequest[]) {
  return request<any>({
    url: '/ai/ai-comparison/compare-v3/batch',
    method: 'post',
    data: { items },
    timeout: 99999999
  });
}

export function fetchAIComparisonDetail(params: { source_standard_no: string; target_standard_no: string }) {
  return request<Api.AI.AIComparisonDetailResponse>({
    url: '/ai/ai-comparison/compare-v3/detail',
    method: 'get',
    params
  });
}

export function fetchFullTextSimilarityBatch(items: Api.AI.FullTextSimilarityRequest[]) {
  return request<any>({
    url: '/ai/full-text-similarity/calculate/batch',
    method: 'post',
    data: { items },
    timeout: 99999999
  });
}

/**
 * 获取批次标签统计数据（用于图表展示）
 *
 * @param batch_id 批次ID
 */
export function fetchBatchStats(batch_id: number, filter_std_domain?: string) {
  return request<Api.AI.BatchStatsResponse>({
    url: `/ai/deduplication/batch-stats/${batch_id}`,
    method: 'get',
    params: { filter_std_domain: filter_std_domain || undefined }
  });
}

/**
 * 删除批次记录
 *
 * @param batch_id 批次ID
 */
export function deleteBatchRecord(batch_id: number) {
  return request({
    url: `/ai/deduplication/batch-delete/${batch_id}`,
    method: 'delete'
  });
}

/**
 * 续跑未完成批次
 *
 * @param batch_id 批次ID
 */
export function resumeBatchRecord(batch_id: number) {
  return request({
    url: `/ai/deduplication/batch-resume/${batch_id}`,
    method: 'post'
  });
}

/**
 * 合并多个批次为一个新批次
 *
 * @param data 合并请求
 */
export function mergeBatches(data: {
  source_batch_ids: number[];
  batch_name?: string;
  remark?: string;
}) {
  return request<{ batch_id: number; total: number; source_batch_ids: number[] }>({
    url: '/ai/deduplication/batch-merge',
    method: 'post',
    data
  });
}

// ==================== 查重池管理 API ====================

/**
 * 创建查重池
 *
 * @param data 创建请求数据
 */
export function createDeduplicationPool(data: Api.AI.CreatePoolRequest) {
  return request<Api.AI.PoolDetailResponse>({
    url: '/ai/deduplication-pool/create',
    method: 'post',
    data
  });
}

/**
 * 获取查重池列表
 *
 * @param params 查询参数
 */
export function fetchDeduplicationPoolList(params?: {
  current?: number;
  size?: number;
  pool_name?: string;
  is_active?: boolean;
}) {
  return request<Api.AI.PoolListResponse>({
    url: '/ai/deduplication-pool/list',
    method: 'get',
    params
  });
}

/**
 * 获取查重池详情
 *
 * @param pool_id 查重池ID
 */
export function fetchDeduplicationPoolDetail(pool_id: number) {
  return request<Api.AI.PoolDetailResponse>({
    url: `/ai/deduplication-pool/detail/${pool_id}`,
    method: 'get'
  });
}

/**
 * 更新查重池
 *
 * @param pool_id 查重池ID
 * @param data 更新数据
 */
export function updateDeduplicationPool(pool_id: number, data: Api.AI.UpdatePoolRequest) {
  return request<Api.AI.PoolDetailResponse>({
    url: `/ai/deduplication-pool/update/${pool_id}`,
    method: 'put',
    data
  });
}

/**
 * 删除查重池
 *
 * @param pool_id 查重池ID
 */
export function deleteDeduplicationPool(pool_id: number) {
  return request({
    url: `/ai/deduplication-pool/delete/${pool_id}`,
    method: 'delete'
  });
}

/**
 * 获取所有启用的查重池（用于下拉选择）
 */
export function fetchAllActivePools() {
  return request<Api.AI.AllActivePoolsResponse>({
    url: '/ai/deduplication-pool/all-active',
    method: 'get'
  });
}


// ==================== 标准基础信息 API ====================

/**
 * 获取标准基础信息列表
 *
 * @param params 查询参数
 */
export function fetchStandardBaseInfoList(params?: {
  current?: number;
  size?: number;
  cname?: string;
  standard_no?: string;
}) {
  return request<Api.Common.PaginatingQueryRecord<Api.AI.StandardBaseInfo>>({
    url: '/ai/standard-base-info/list',
    method: 'get',
    params
  });
}

/**
 * 获取标准基础信息详情
 *
 * @param standard_id 标准ID
 */
export function fetchStandardBaseInfoDetail(standard_id: string) {
  return request<Api.AI.StandardBaseInfoDetailResponse>({
    url: `/ai/standard-base-info/detail/${standard_id}`,
    method: 'get'
  });
}

/**
 * 批量校验标准编号是否存在
 *
 * @param standard_nos 标准编号数组
 */
export function fetchStandardBatchVerify(standard_nos: string[]) {
  return request<Record<string, {id: string; exists: boolean}>>({
    url: '/ai/standard-base-info/batch-verify',
    method: 'post',
    data: {standard_nos}
  });
}

/**
 * 获取标准基础信息统计
 */
export function fetchStandardBaseInfoStats() {
  return request<Api.AI.StandardBaseInfoStatsResponse>({
    url: '/ai/standard-base-info/stats',
    method: 'get'
  });
}

/**
 * 根据标准编号获取 JGH PDF 信息
 */
export function fetchJghPdfByStandardNo(standard_no: string) {
  return request<{ id: string; main_task_id: string; standard_no: string; cname: string; name: string } | null>({
    url: `/ai/standard-base-info/jgh-pdf/${encodeURIComponent(standard_no)}`,
    method: 'get'
  });
}

/**
 * 获取标准章节列表
 */
export function fetchJghPdfChapters(main_task_id: string) {
  return request<Array<{ id: string; main_task_id: string; title: string; title_no: string; page: number; word: string }>>({
    url: `/ai/standard-base-info/jgh-pdf-chapters/${main_task_id}`,
    method: 'get'
  });
}

/**
 * 批量删除标准基础信息及关联数据
 *
 * @param ids 要删除的标准ID列表
 */
export function batchDeleteStandardBaseInfo(ids: string[]) {
  return request({
    url: '/ai/standard-base-info/batch-delete',
    method: 'post',
    data: ids
  });
}

// ==================== 数据迁移 API ====================

/**
 * 从 MySQL 导入数据
 *
 * @param config MySQL 连接配置
 */
export function importDataFromMySQL(config: {
  host: string;
  port: number;
  user: string;
  password: string;
  database: string;
}) {
  return request({
    url: '/ai/data-migration/import-from-mysql',
    method: 'post',
    data: config
  });
}

/**
 * 从 MySQL 导入结构化数据
 *
 * @param config MySQL 连接配置和标准号列表
 */
export function importStructuredDataFromMySQL(config: {
  host: string;
  port: number;
  user: string;
  password: string;
  database: string;
  standard_nos: string[];
}) {
  return request({
    url: '/ai/data-migration/import-structured-data',
    method: 'post',
    data: config
  });
}

/**
 * 从 MySQL 只导入表格和公式数据
 *
 * @param config MySQL 连接配置
 */
export function importTableFormulaFromMySQL(config: {
  host: string;
  port: number;
  user: string;
  password: string;
  database: string;
}) {
  return request({
    url: '/ai/data-migration/import-table-formula-from-mysql',
    method: 'post',
    data: config
  });
}

/**
 * 按标准号迁入指定标准数据
 *
 * @param config MySQL 连接配置和标准号列表
 */
export function importSpecificStandardsFromMySQL(config: {
  host: string;
  port: number;
  user: string;
  password: string;
  database: string;
  standard_nos: string[];
}) {
  return request({
    url: '/ai/data-migration/import-specific-standards',
    method: 'post',
    data: config
  });
}

/** 交互式问卷选项（```questionnaire 围栏载荷；后端 _parse_questionnaire 规整后的形状） */
export interface QuestionnaireOption {
  label: string;
  description?: string;
}

/** 交互式问卷题目（1~5 题；每题 2~6 选项；前端按 tab 逐题切换） */
export interface QuestionnaireQuestion {
  id: string;
  /** 2~6 字短标题（tab 名） */
  title: string;
  question: string;
  multiSelect: boolean;
  options: QuestionnaireOption[];
}

/** 过程时间线条目（SSE process 事件与落库 process_json 共用形状） */
export interface ProcessStep {
  id: string;
  kind: 'reasoning' | 'text' | 'tool_call' | 'tool_result' | 'todo' | 'compaction' | 'questionnaire';
  content?: string;
  tool?: string;
  tool_display?: string;
  args?: Record<string, unknown>;
  is_subagent?: boolean;
  /** 该条目产生于子代理的子会话内部（区别于 is_subagent=委派动作本身），时间线内缩进展示 */
  in_subagent?: boolean;
  is_error?: boolean;
  todos?: Array<{ content: string; status: string; activeForm?: string }>;
  /** kind=questionnaire 时的问卷载荷（item_id 形如 qn1/qn2；正文以 [questionnaire:<id>] 占位符定位） */
  questions?: QuestionnaireQuestion[];
}

/** 通用问答 SSE 事件类型（dsh 三段式改造后：过程时间线 + 结果 两段式） */
export type QAEvent =
  | {
  type: 'session';
  sessionKey: string | null;
  threadId: string;
  assistantMessageId: number | null;
  userMessageId: number | null;
  /** 会话驻留专家回显（@召唤改绑随首事件送达） */
  expertKey?: string | null;
  expertName?: string | null;
  expertIcon?: string | null;
  /** 本条消息是否 @召唤改绑了专家（前端提示用） */
  expertSummoned?: boolean
}
  | { type: 'process'; step: number; kind: 'reasoning'; item_id: string; content: string }
  | { type: 'process'; step: number; kind: 'text'; item_id: string; content: string; replace: boolean }
  | { type: 'process'; step: number; kind: 'tool_call'; item_id: string; tool: string; tool_display: string; args: Record<string, unknown>; is_subagent: boolean; in_subagent?: boolean }
  | { type: 'process'; step: number; kind: 'tool_result'; item_id: string; tool: string; tool_display: string; content: string; is_error: boolean; in_subagent?: boolean }
  | { type: 'process'; step: number; kind: 'todo'; item_id: string; todos: Array<{ content: string; status: string; activeForm?: string }> }
  | { type: 'process'; step: number; kind: 'compaction'; item_id: string }
  | { type: 'process'; step: number; kind: 'questionnaire'; item_id: string; questions: QuestionnaireQuestion[] }
  | { type: 'process'; step: number; kind: 'reset' }
  | { type: 'done'; steps: number; promoted: string[] }
  | { type: 'aborted' }
  | { type: 'error'; message: string }
  | { type: 'moderated'; message: string }
  | { type: 'quota_exceeded'; quota?: number | null; used?: number | null; remaining?: number | null; message: string };

/**
 * 停止对话流（用户主动停止）
 * @param sessionKey 会话 key
 */
export async function fetchQAStop(sessionKey: string) {
  return request({
    url: `/ai/qa/stop?session_key=${encodeURIComponent(sessionKey)}`,
    method: 'post'
  });
}

/**
 * 通用标准问答（流式 SSE）
 *
 * @param message     用户消息
 * @param sessionKey  会话 key；传 null 则由后端自动建会话（首个 session 事件返回）
 * @param onEvent     SSE 事件回调
 * @param signal      AbortController signal
 * @param workflowKey 用户当前在工作流画板打开的工作流 key（可选，注入 Agent 上下文）
 * @param scopeNodeIds 迷你协作可编辑范围（焦点卡 + 一跳邻居，首元素为焦点卡；可选，仅画布底部输入栏用）
 * @param sustainedWork 持续精造模式（可选，仅管理员可开启；复杂创作任务多轮自我精进）
 */
export async function fetchQAChatStream(
  message: string,
  sessionKey: string | null,
  onEvent: (event: QAEvent) => void,
  signal?: AbortSignal,
  files?: string[],
  workflowKey?: string,
  scopeNodeIds?: string[],
  selectedNodeIds?: string[],
  expertKey?: string,
  sustainedWork?: boolean
): Promise<void> {
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const { baseURL } = getServiceBaseURL(import.meta.env, isHttpProxy);
  const token = await ensureFreshAccessToken();
  const Authorization = `Bearer ${token}`;

  const body: Record<string, unknown> = {message, session_key: sessionKey};
  if (files && files.length > 0) body.files = files;
  if (workflowKey) body.workflow_key = workflowKey;
  if (scopeNodeIds && scopeNodeIds.length > 0) body.scope_node_ids = scopeNodeIds;
  if (selectedNodeIds && selectedNodeIds.length > 0) body.selected_node_ids = selectedNodeIds;
  if (expertKey) body.expert_key = expertKey;
  if (sustainedWork) body.sustained_work = true;

  const response = await fetch(`${baseURL}/ai/qa/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization,
      apifoxToken: 'XL299LiMEDZ0H5h3A29PxwQXdMJqWyY2'
    },
    body: JSON.stringify(body),
    signal
  });

  if (!response.ok || !response.body) {
    throw new Error(`请求失败: ${response.status}`);
  }
  await rejectIfJsonError(response);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  // 终态事件跟踪：后端异常路径（落库失败/进程重启/网络中断）可能让 SSE 干净 EOF
  // 而没有任何终态事件——旧实现直接 return，调用方无从分辨「正常结束」与「断流」，
  // 气泡永久停在生成中只能刷新页面。EOF 未见终态即抛错，交给调用方恢复逻辑。
  let sawTerminal = false;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        let data: QAEvent;
        try {
          data = JSON.parse(line.slice(6)) as QAEvent;
        } catch {
          // 仅吞 JSON 解析异常
          continue;
        }
        // onEvent 移出 try：回调里的异常必须冒泡给调用方，不能被当成解析错误静默吞掉
        if (data.type === 'done' || data.type === 'error' || data.type === 'aborted' || data.type === 'quota_exceeded') {
          sawTerminal = true;
        }
        onEvent(data);
      }
    }
  }

  if (!sawTerminal) {
    throw new Error('stream-ended-without-terminal-event');
  }
}

export interface UploadFileResult {
  filename: string;
  path: string;
  size: number;
}

export async function fetchUploadFile(
  file: File,
  sessionKey: string,
  onProgress?: (percent: number) => void
): Promise<UploadFileResult> {
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const {baseURL} = getServiceBaseURL(import.meta.env, isHttpProxy);
  const token = await ensureFreshAccessToken();

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${baseURL}/ai/upload/file`);
    xhr.setRequestHeader('Authorization', `Bearer ${token}`);

    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
      };
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const res = JSON.parse(xhr.responseText);
          if (res.code === '0000' && res.data) {
            resolve(res.data as UploadFileResult);
          } else {
            reject(new Error(res.msg || '上传失败'));
          }
        } catch {
          reject(new Error('响应解析失败'));
        }
      } else {
        reject(new Error(`上传失败: ${xhr.status}`));
      }
    };

    xhr.onerror = () => reject(new Error('网络错误'));
    xhr.onabort = () => reject(new Error('上传已取消'));

    const formData = new FormData();
    formData.append('file', file);
    formData.append('session_key', sessionKey);
    xhr.send(formData);
  });
}

/** 重置问答对话历史 */
export function resetQAChat(threadId: string) {
  return request({
    url: '/ai/qa/reset',
    method: 'post',
    data: { thread_id: threadId }
  });
}

// ==================== 标准指标缓存查询 API ====================

/**
 * 获取已提取指标的标准列表（从 standard_cache_ind 分组统计）
 */
export function fetchStandardIndList(params?: {
  current?: number;
  size?: number;
  standard_no?: string;
  standard_name?: string;
}) {
  return request<Api.AI.StandardIndListResponse>({
    url: '/ai/standard-ind/list',
    method: 'get',
    params
  });
}

/**
 * 获取全量指标分页列表
 */
export function fetchAllIndList(params?: {
  current?: number;
  size?: number;
  standard_no?: string;
  norm_class?: string;
  indicator_category?: string;
  keyword?: string;
  applicable_object?: string;
  standard_object?: string;
}) {
  return request<Api.AI.AllIndListResponse>({
    url: '/ai/standard-ind/all-indicators',
    method: 'get',
    params
  });
}

/**
 * 删除标准指标缓存（支持批量，按 standard_no 删全部）
 */
export function deleteStandardInd(standard_nos: string[]) {
  return request<{ deleted: number }>({
    url: '/ai/standard-ind/delete',
    method: 'delete',
    data: {standard_nos}
  });
}

/**
 * 删除单次提取记录（按 run_id）
 */
export function deleteStandardIndRun(run_id: string) {
  return request<{ deleted: number }>({
    url: '/ai/standard-ind/delete-run',
    method: 'delete',
    data: {run_id}
  });
}

/**
 * 获取指定标准的全量指标（从 standard_cache_ind 查询）
 */
export function fetchStandardIndDetail(standard_no: string, run_id?: string) {
  return request<Api.AI.StandardIndDetailResponse>({
    url: '/ai/standard-ind/indicators',
    method: 'get',
    params: {standard_no, ...(run_id ? {run_id} : {})}
  });
}

/**
 * 获取指定标准的全量试验（从 standard_cache_test 查询）
 */
export function fetchStandardTests(standard_no: string, run_id?: string) {
  return request<{ standard_no: string; standard_name: string; run_id: string; tests: Api.AI.StandardTestItem[] }>({
    url: '/ai/standard-ind/tests',
    method: 'get',
    params: {standard_no, ...(run_id ? {run_id} : {})}
  });
}

/**
 * 获取指标分类体系枚举（来自后端 mind_map parser，不要在前端硬编码）
 */
export function fetchIndTaxonomy() {
  return request<Api.AI.IndTaxonomy>({
    url: '/ai/standard-ind/taxonomy',
    method: 'get',
  });
}

/**
 * 批量提取标准指标（流式 SSE）
 *
 * @param standard_nos 标准编号列表
 * @param onEvent      每收到一个 SSE 事件时回调
 * @param signal       AbortController signal，用于中止请求
 */
export async function fetchExtractBatchStream(
  standard_nos: string[],
  onEvent: (event: Api.AI.ExtractBatchEvent) => void,
  signal?: AbortSignal,
  run_remark?: string
): Promise<void> {
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const {baseURL} = getServiceBaseURL(import.meta.env, isHttpProxy);
  const token = await ensureFreshAccessToken();
  const Authorization = `Bearer ${token}`;

  const response = await fetch(`${baseURL}/ai/standard-ind/extract-batch-fast`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization,
      apifoxToken: 'XL299LiMEDZ0H5h3A29PxwQXdMJqWyY2'
    },
    body: JSON.stringify({standard_nos, run_remark: run_remark || ''}),
    signal
  });

  if (!response.ok || !response.body) {
    throw new Error(`请求失败: ${response.status}`);
  }
  await rejectIfJsonError(response);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, {stream: true});
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.slice(6)) as Api.AI.ExtractBatchEvent;
          onEvent(data);
        } catch {
          // 忽略解析异常
        }
      }
    }
  }
}

// ==================== 标准化对象视角 API ====================

/**
 * 按标准化对象分组统计
 */
export function fetchStandardObjList(params?: {
  current?: number;
  size?: number;
  keyword?: string;
  norm_class?: string;
  indicator_category?: string;
}) {
  return request<Api.AI.StandardObjListResponse>({
    url: '/ai/standard-obj/list',
    method: 'get',
    params
  });
}

/**
 * 获取指定标准化对象的全量指标（跨标准聚合）
 */
export function fetchStandardObjIndicators(standard_object: string) {
  return request<Api.AI.StandardIndDetailResponse>({
    url: '/ai/standard-obj/indicators',
    method: 'get',
    params: {standard_object}
  });
}

// ==================== Agent 技能 ====================

export interface AgentSkill {
  id: number;
  skillKey: string;
  name: string;
  description: string | null;
  /** SKILL.md 主文件全文（含 YAML frontmatter） */
  skillMd: string;
  skillPkgKeys: string[];
  hasFiles: boolean;
  fileCount: number;
  version: string | null;
  sourceUrl: string | null;
  /** builtin 内置 / derived 凝练 / curated 收录（上传+发现）；「官方」分类已废除 */
  source: 'builtin' | 'derived' | 'curated';
  originSessionId: number | null;
  userId: number | null;
  /** 原作者展示名：昵称优先、缺省回落脱敏手机号；无属主公共技能为「官方」 */
  author: string | null;
  /** 商店上架状态（全局）：false=已下架（仅管理者/作者可见该行） */
  isEnabled: boolean;
  /** 个人启用/禁用（只影响自己）；禁用=完全不加载、@ 不可调用；无记录默认 true */
  userEnabled: boolean;
  /** 是否已添加到「我的技能」（只影响自己）；false=已移除，商店里可重新添加；无记录默认 true */
  isAdded: boolean;
  tags: string[];
  /** 图标：单个 <svg> 元素源码或图片 data URI；null=前端显示兜底图标 */
  icon: string | null;
  /** 商店分类（词表见 skill-categories.ts）；null=按「其他」展示 */
  category: string | null;
  /** 精选技能：商店顶部精选区展示，仅管理员可设置 */
  isFeatured: boolean;
  /** 可见档位白名单（显式多选，无包含关系）；null/空数组=全员可见，仅管理员可改 */
  minTierCode: string[] | null;
  /** 快捷提问：卡片展示，用户点击即添加该技能并把问题填入输入框 */
  exampleQuestions?: string[];
  /** 「加入我的技能」时间（ms 时间戳）：个人偏好最后更新时间；本人创建的技能回落创建时间；null=未添加 */
  addedAt: number | null;
  createdAt: number | null;
  updatedAt: number | null;
}

export interface DiscoveredSkillCandidate {
  name: string;
  description: string | null;
  source_url: string;
  version?: string | null;
}

export interface AgentSkillVersion {
  version: string;
  fileCount: number;
  size: number;
  isActive: boolean;
}

/** 上传技能包后的变动情况：新增 or 升级版本 */
export interface UploadSkillChange {
  action: 'created' | 'upgraded' | 'failed';
  skillKey: string | null;
  name: string | null;
  oldVersion: string | null;
  newVersion: string | null;
  fileCount: number;
}

// ── 安装 / 上传 / 下载 / 版本 / 发现 ────────────────────────────────────────

export function fetchInstallAgentSkill(data: {
  source_url: string;
  suggested_key?: string;
}) {
  return request<{skill?: AgentSkill; message?: string}>({
    url: '/ai/agent/skills/install',
    method: 'post',
    data
  });
}

export async function fetchUploadAgentSkill(
  file: File,
  onProgress?: (percent: number) => void
): Promise<{skill?: AgentSkill; message?: string; change?: UploadSkillChange}> {
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const {baseURL} = getServiceBaseURL(import.meta.env, isHttpProxy);
  const token = await ensureFreshAccessToken();

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${baseURL}/ai/agent/skills/upload`);
    xhr.setRequestHeader('Authorization', `Bearer ${token}`);

    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
      };
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const res = JSON.parse(xhr.responseText);
          if (res.code === '0000' && res.data) {
            resolve(res.data);
          } else {
            reject(new Error(res.msg || '上传失败'));
          }
        } catch {
          reject(new Error('响应解析失败'));
        }
      } else {
        // 非 2xx 也尝试从响应体取错误详情：后端业务错误 {msg}、FastAPI 422 {detail}；
        // 网关 502/504 可能是 HTML，解析失败回落状态码
        let detail = '';
        try {
          const body = JSON.parse(xhr.responseText);
          detail =
            (typeof body?.msg === 'string' && body.msg) ||
            (typeof body?.detail === 'string' ? body.detail : Array.isArray(body?.detail) ? '请求参数不合法' : '');
        } catch {
          detail = '';
        }
        reject(new Error(detail || `上传失败: ${xhr.status}`));
      }
    };
    xhr.onerror = () => reject(new Error('网络错误'));
    xhr.onabort = () => reject(new Error('上传已取消'));

    const formData = new FormData();
    formData.append('file', file);
    xhr.send(formData);
  });
}

export async function fetchDownloadAgentSkill(skillId: number, filename = 'skill.zip') {
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const {baseURL} = getServiceBaseURL(import.meta.env, isHttpProxy);
  const token = await ensureFreshAccessToken();
  const resp = await fetch(`${baseURL}/ai/agent/skills/${skillId}/download`, {
    headers: {Authorization: `Bearer ${token}`}
  });
  if (!resp.ok) throw new Error(`下载失败: ${resp.status}`);
  await rejectIfJsonError(resp);
  const blob = await resp.blob();
  const a = document.createElement('a');
  const objUrl = URL.createObjectURL(blob);
  a.href = objUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objUrl);
}

export function fetchAgentSkillVersions(skillId: number) {
  return request<AgentSkillVersion[]>({
    url: `/ai/agent/skills/${skillId}/versions`,
    method: 'get'
  });
}

export function fetchActivateAgentSkillVersion(skillId: number, version: string) {
  return request<AgentSkill>({
    url: `/ai/agent/skills/${skillId}/versions/${encodeURIComponent(version)}/activate`,
    method: 'post'
  });
}

export function fetchDeleteAgentSkillVersion(skillId: number, version: string) {
  return request<null>({
    url: `/ai/agent/skills/${skillId}/versions/${encodeURIComponent(version)}`,
    method: 'delete'
  });
}

export type DiscoverEvent =
  | {type: 'started'}
  | {type: 'heartbeat'}
  | {type: 'candidates'; items: DiscoveredSkillCandidate[]}
  | {type: 'done'}
  | {type: 'error'; message: string};

export async function fetchDiscoverSkillStream(
  query: string,
  onEvent: (ev: DiscoverEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const {baseURL} = getServiceBaseURL(import.meta.env, isHttpProxy);
  const token = await ensureFreshAccessToken();
  const Authorization = `Bearer ${token}`;

  const response = await fetch(`${baseURL}/ai/agent/skills/discover/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization
    },
    body: JSON.stringify({query}),
    signal
  });
  if (!response.ok || !response.body) throw new Error(`请求失败: ${response.status}`);
  await rejectIfJsonError(response);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, {stream: true});
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          onEvent(JSON.parse(line.slice(6)) as DiscoverEvent);
        } catch {
          // ignore
        }
      }
    }
  }
}

export function fetchAgentSkills(include_disabled = false) {
  return request<AgentSkill[]>({
    url: '/ai/agent/skills',
    method: 'get',
    params: {include_disabled}
  });
}

/** 上架管理状态筛选（服务端筛选口径，技能/连接器/数据集/专家共用；精选仅技能有效） */
export type ManageStatus = 'all' | 'enabled' | 'disabled' | 'featured';

/** 上架管理分页查询参数（仅管理员 + include_disabled 时走服务端分页，否则后端回落全量） */
export interface ManageListQuery {
  current: number;
  size: number;
  keyword?: string;
  /** 仅技能：分类词表项或「其他」桶 */
  category?: string;
  status?: ManageStatus;
  /** 作者筛选：0=官方（user_id 为空），其余为具体用户 id */
  user_id?: number;
  /** 仅连接器：connector / dataset（共表，上架管理两个 tab 靠它区分） */
  kind?: 'connector' | 'dataset';
}

/** 上架管理作者聚合项（作者筛选下拉源） */
export interface AuthorFacet {
  /** null=官方 */
  userId: number | null;
  author: string;
  count: number;
}

/** 上架管理筛选变化载荷（列表视图 → 面板 pager）：只带变化字段；userId=null 表示清空作者筛选 */
export interface ManageFilterPatch {
  keyword?: string;
  category?: string;
  status?: ManageStatus;
  userId?: number | null;
}

/** 技能上架管理分页查询（仅管理员走分页路径；信封 {records, total, current, size}） */
export function fetchAgentSkillsPaged(params: ManageListQuery) {
  return request<Api.Common.PaginatingQueryRecord<AgentSkill>>({
    url: '/ai/agent/skills',
    method: 'get',
    params: {...params, include_disabled: true}
  });
}

/** 技能上架管理作者清单（仅管理员） */
export function fetchAgentSkillAuthorFacets() {
  return request<AuthorFacet[]>({
    url: '/ai/agent/skills/manage-authors',
    method: 'get'
  });
}

/** 批量设置技能个人偏好（只影响当前用户，不影响全局 isEnabled 商店上架状态）。
 *  isEnabled：启用/禁用（禁用=完全不加载、@ 不可调用）；isAdded：是否已添加到「我的技能」。
 *  两个状态正交，传哪个改哪个，至少传一个 */
export function fetchBatchAgentSkillPrefs(skill_keys: string[], opts: {isEnabled?: boolean; isAdded?: boolean}) {
  return request<{updated: string[]}>({
    url: '/ai/agent/skills/prefs',
    method: 'put',
    data: {skill_keys, is_enabled: opts.isEnabled, is_added: opts.isAdded}
  });
}

/** 批量管理技能（上架管理页，仅管理员）：is_enabled 批量上/下架、is_featured 批量精选，传哪个改哪个。
 *  返回 updated=生效的 key，skipped=被跳过的 key（builtin/不存在） */
export function fetchBatchManageAgentSkills(
  skill_keys: string[],
  opts: {isEnabled?: boolean; isFeatured?: boolean}
) {
  return request<{updated: string[]; skipped: string[]}>({
    url: '/ai/agent/skills/manage',
    method: 'put',
    data: {skill_keys, is_enabled: opts.isEnabled, is_featured: opts.isFeatured}
  });
}

/** 批量删除技能（上架管理页；管理员可删全部，作者可删自己的） */
export function fetchBatchDeleteAgentSkills(skill_keys: string[]) {
  return request<{updated: string[]; skipped: string[]}>({
    url: '/ai/agent/skills/manage-delete',
    method: 'post',
    data: {skill_keys}
  });
}

export function fetchCreateAgentSkill(data: {
  skill_key: string;
  name: string;
  description?: string;
  skill_md: string;
  example_questions?: string[];
}) {
  return request<AgentSkill>({
    url: '/ai/agent/skills',
    method: 'post',
    data
  });
}

export function fetchUpdateAgentSkill(
  skill_id: number,
  data: {
    name?: string;
    description?: string;
    skill_md?: string;
    is_enabled?: boolean;
    icon?: string;
    category?: string;
    is_featured?: boolean;
    /** 可见档位白名单（仅管理员）：空数组=全员可见；档位 code 见 fetchRoleTiers；不传=不改 */
    min_tier_code?: string[] | null;
    example_questions?: string[];
  }
) {
  return request<AgentSkill>({
    url: `/ai/agent/skills/${skill_id}`,
    method: 'patch',
    data
  });
}

export function fetchUpdateAgentSkillTags(skill_id: number, tags: string[]) {
  return request<AgentSkill>({
    url: `/ai/agent/skills/${skill_id}/tags`,
    method: 'patch',
    data: {tags}
  });
}

/** 商店分类词表条目（运行时真相源 = DB，管理员/system-admin 可配） */
export interface AgentSkillCategory {
  id: number;
  name: string;
  sortOrder: number;
  /** 分类图标：svg 源码 / 图片 data URI（DB agent_skill_category.icon），空=前端兜底图标 */
  icon?: string;
}

export function fetchAgentSkillCategories() {
  return request<AgentSkillCategory[]>({
    url: '/ai/agent/skills/categories',
    method: 'get'
  });
}

/** 新增分类（仅管理员） */
export function fetchAddAgentSkillCategory(data: {name: string; sort_order?: number; icon?: string}) {
  return request<AgentSkillCategory>({
    url: '/ai/agent/skills/categories',
    method: 'post',
    data
  });
}

/** 重命名/排序/换图标分类（仅管理员；重命名级联更新引用技能；icon 传空串=清除，不传=不改） */
export function fetchUpdateAgentSkillCategory(cat_id: number, data: {name: string; sort_order?: number; icon?: string}) {
  return request<AgentSkillCategory>({
    url: `/ai/agent/skills/categories/${cat_id}`,
    method: 'patch',
    data
  });
}

/** 删除分类（仅管理员；该分类下技能回落「其他」） */
export function fetchDeleteAgentSkillCategory(cat_id: number) {
  return request<null>({
    url: `/ai/agent/skills/categories/${cat_id}`,
    method: 'delete'
  });
}

/** 技能案例（技能整合后按 skill_key 挂载，仅管理员维护） */
export interface SkillExample {
  id: number;
  actionId: number;
  title: string;
  description?: string;
  conversationData: Array<Record<string, unknown>>;
  previewImage?: string;
  previewImages?: string[];
  previewHtml?: string;
  sortOrder: number;
  /** 1 启用 0 停用（停用不在橱窗展示） */
  isEnabled?: number;
}

export function fetchSkillExamples(skill_id: number) {
  return request<SkillExample[]>({
    url: `/ai/agent/skills/${skill_id}/examples`,
    method: 'get'
  });
}

/** 从会话提取案例：title/description 不传默认取会话标题 */
export function fetchCreateSkillExampleFromSession(
  skill_id: number,
  data: {sessionKey: string; title?: string; description?: string; previewImages?: string[]; sortOrder?: number}
) {
  return request<SkillExample>({
    url: `/ai/agent/skills/${skill_id}/examples/from-session`,
    method: 'post',
    data
  });
}

export function fetchUpdateSkillExample(
  example_id: number,
  data: {title?: string; description?: string; previewImages?: string[]; sortOrder?: number; isEnabled?: number}
) {
  return request<SkillExample>({
    url: `/ai/agent/skills/examples/${example_id}`,
    method: 'put',
    data
  });
}

export function fetchDeleteSkillExample(example_id: number) {
  return request<null>({
    url: `/ai/agent/skills/examples/${example_id}`,
    method: 'delete'
  });
}

/** 上传案例预览图（FormData 绕过 axios 拦截器，按红线走 token 预检 + JSON 错误兜底），返回公开访问路径 */
export async function fetchUploadSkillExampleImage(file: File): Promise<string> {
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const {baseURL} = getServiceBaseURL(import.meta.env, isHttpProxy);
  const token = await ensureFreshAccessToken();
  const fd = new FormData();
  fd.append('file', file);
  const response = await fetch(`${baseURL}/ai/upload/quick-action-image`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      apifoxToken: 'XL299LiMEDZ0H5h3A29PxwQXdMJqWyY2'
    },
    body: fd
  });
  if (!response.ok) {
    throw new Error(`上传失败: ${response.status}`);
  }
  await rejectIfJsonError(response);
  const json = (await response.json()) as {code: string; msg?: string; data?: {path: string}};
  if (json.code !== '0000' || !json.data?.path) {
    throw new Error(json.msg || '上传失败');
  }
  return json.data.path;
}

export function fetchDeleteAgentSkill(skill_id: number) {
  return request<null>({
    url: `/ai/agent/skills/${skill_id}`,
    method: 'delete'
  });
}

export function fetchDistillSkillFromSession(data: {
  session_key: string;
  suggested_key?: string;
}) {
  return request<{ skill: AgentSkill; draft: Record<string, unknown> }>({
    url: '/ai/agent/skills/from-session',
    method: 'post',
    data
  });
}

export type DistillEvent =
  | { type: 'started' }
  | { type: 'heartbeat' }
  | { type: 'done'; skill: AgentSkill; draft: Record<string, unknown> }
  | { type: 'error'; message: string };

/** SSE 版凝练：长耗时不会被 HTTP 超时掐断 */
export async function fetchDistillSkillStream(
  data: { session_key: string; suggested_key?: string },
  onEvent: (ev: DistillEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const {baseURL} = getServiceBaseURL(import.meta.env, isHttpProxy);
  const token = await ensureFreshAccessToken();
  const Authorization = `Bearer ${token}`;

  const response = await fetch(`${baseURL}/ai/agent/skills/from-session/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization,
      apifoxToken: 'XL299LiMEDZ0H5h3A29PxwQXdMJqWyY2'
    },
    body: JSON.stringify(data),
    signal
  });

  if (!response.ok || !response.body) {
    throw new Error(`请求失败: ${response.status}`);
  }
  await rejectIfJsonError(response);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, {stream: true});
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          onEvent(JSON.parse(line.slice(6)) as DistillEvent);
        } catch {
          // ignore
        }
      }
    }
  }
}


export interface AgentSession {
  sessionKey: string;
  title: string;
  threadId: string;
  messageCount: number;
  isStarred: number;
  /** 会话来源：qa=普通问答 workflow=工作流画板 */
  source?: 'qa' | 'workflow';
  /** 关联工作流 key（画板内发起的会话）；空=通用问答 */
  workflowKey?: string | null;
  /** 关联板的板型（后端 join agent_workflow 附带；板被软删时缺省）：board=流程编排 html=应用制作 */
  boardType?: 'board' | 'html';
  /** 会话驻留专家（@召唤绑定；专家被删时后端置空） */
  expertKey?: string | null;
  /** 列表/更新回显附带（专家被删时缺省） */
  expertName?: string;
  expertIcon?: string | null;
  createdAt: number;
  updatedAt: number;
}


export interface AgentArtifact {
  id: number;
  artifactType: 'md' | 'pdf' | 'zip' | 'xlsx' | 'csv' | 'json' | 'image' | 'chart' | 'excalidraw' | 'other' | string;
  name: string;
  description?: string | null;
  path?: string | null;
  size?: number | null;
  chartSpec?: Record<string, unknown> | null;
  messageId?: number | null;
  batchItemId?: number | null;
  downloadUrl?: string | null;
  createdAt?: number | null;
}

export interface AgentToolStep {
  id: number;
  type: 'tool_call' | 'tool_result';
  tool: string;
  tool_display?: string;
  args?: Record<string, unknown>;
  content?: string;
}

export interface AgentMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  /** 历史字段（deepagents 时代）；新消息恒为空串，保留仅为旧数据回放兼容 */
  thinking: string;
  /** 历史字段（deepagents 时代）；新消息恒为空数组，保留仅为旧数据回放兼容 */
  toolSteps: AgentToolStep[];
  /** 过程时间线条目（dsh 三段式改造后的主通道；已提升为答案的尾部文本不在其中） */
  processSteps?: ProcessStep[];
  status: 'streaming' | 'done' | 'error' | 'aborted';
  error: string | null;
  createdAt: number;
  artifacts?: AgentArtifact[];
  attachments?: Array<{ name: string; path: string; size: number; isImage: boolean }>;
}

/** 会话列表 */
export function fetchAgentSessions(limit = 100, search?: { keyword?: string; startDate?: string; endDate?: string; workflowKey?: string }) {
  return request<AgentSession[]>({
    url: '/ai/agent/sessions',
    method: 'get',
    params: {limit, keyword: search?.keyword || undefined, start_date: search?.startDate || undefined, end_date: search?.endDate || undefined, workflow_key: search?.workflowKey || undefined}
  });
}

/** 新建会话（expertKey：专家面板「和 TA 对话」直接建专家会话） */
export function fetchCreateAgentSession(title?: string, expertKey?: string) {
  return request<AgentSession>({
    url: '/ai/agent/sessions',
    method: 'post',
    data: {title, expertKey}
  });
}

/** 更新会话（标题/收藏/专家绑定；expertKey 传 null 或空串=移除专家） */
export function fetchUpdateAgentSession(session_key: string, data: { title?: string; is_starred?: number; touch?: boolean; expertKey?: string | null }) {
  return request<AgentSession>({
    url: `/ai/agent/sessions/${session_key}`,
    method: 'patch',
    data
  });
}

/** 软删会话 */
export function fetchDeleteAgentSession(session_key: string) {
  return request<null>({
    url: `/ai/agent/sessions/${session_key}`,
    method: 'delete'
  });
}

/** 截断会话：删除指定消息（含）及之后的全部内容 */
export function fetchTruncateAgentSession(session_key: string, truncate_from_message_id: number) {
  return request<AgentSession>({
    url: `/ai/agent/sessions/${session_key}/truncate`,
    method: 'post',
    data: {truncate_from_message_id}
  });
}

/** 会话消息 */
export function fetchAgentMessages(session_key: string) {
  return request<AgentMessage[]>({
    url: `/ai/agent/sessions/${session_key}/messages`,
    method: 'get'
  });
}

/** 覆盖保存 excalidraw 产物：写入新 JSON 并重生成同名 SVG */
export function saveExcalidrawArtifact(artifact_id: number, sceneJson: string) {
  return request<{ id: number; size: number; svg: { id: number; size: number } | null }>({
    url: `/ai/agent/artifacts/${artifact_id}/excalidraw`,
    method: 'put',
    data: {sceneJson}
  });
}

// ─── 个人知识库 ────────────────────────────────────────────────────────────

export type NianEntryType = 'knowledge' | 'idea' | 'todo';
export type NianTodoStatus = 'pending' | 'done' | 'overdue';
export type NianIdeaStatus = 'active' | 'digested';

export interface KBAttachment {
  name: string;
  path: string;
  size: number;
  artifactId?: number | null;
  isImage: boolean;
  /** 后端在 list/get/feed 时联表取出 */
  artifact?: AgentArtifact | null;
}

export interface KBEntry {
  id: string;
  title: string;
  summary: string;
  content: string;
  tags: string[];
  userId: number | null;
  visibility: 'private' | 'role' | 'public';
  allowedRoleCodes: string[];
  sources: any[];
  groupTag: string | null;
  isArchived: boolean;
  hitCount: number;
  lastHitAt: number | null;
  createdAt: number | null;
  updatedAt: number | null;
  distance?: number;
  // ── 知识库扩展字段 ─────────────────────────────────────────
  entryType: NianEntryType;
  meta: Record<string, any>;
  parentId: string | null;
  dismissedUntil: number | null;
  lastFeedRank: number | null;
  lastFeedReason: string | null;
  // ── 类型专属顶层字段 ─────────────────────────────────────
  dueAt: number | null;
  todoStatus: NianTodoStatus | null;
  doneAt: number | null;
  primaryArtifactId: number | null;
  svgArtifactId: number | null;
  ideaStatus: NianIdeaStatus | null;
  digestedAt: number | null;
  /** 后端在 list/get/feed 时联表取出，前端可直接渲染 */
  primaryArtifact?: AgentArtifact | null;
  svgArtifact?: AgentArtifact | null;
  // ── 附件（任何类型都可携带）──────────────────────────────
  attachments?: KBAttachment[];
}

export function fetchKbEntries(params: {
  keyword?: string;
  tag?: string;
  include_archived?: boolean;
  limit?: number;
  offset?: number;
} = {}) {
  return request<{ items: KBEntry[]; total: number }>({
    url: '/ai/agent/kb',
    method: 'get',
    params
  });
}

export function fetchKbTagsStats(params?: { entry_type?: string; limit?: number }) {
  return request<Array<{ tag: string; count: number }>>({
    url: '/ai/agent/kb/tags-stats',
    method: 'get',
    params
  });
}

export interface TagCluster {
  canonical: string;
  count: number;
  members: string[];
  memberCounts?: Record<string, number>;
  size: number;
}

export function fetchKbTagsClustered(params?: { entry_type?: string; threshold?: number; limit?: number }) {
  return request<TagCluster[]>({
    url: '/ai/agent/kb/tags-clustered',
    method: 'get',
    params
  });
}

export function fetchKbArtifactsLookup(ids: number[]) {
  return request<Record<number, AgentArtifact>>({
    url: '/ai/agent/kb/artifacts/lookup',
    method: 'get',
    params: {ids: ids.join(',')}
  });
}

export function fetchKbEntry(id: string) {
  return request<KBEntry>({
    url: `/ai/agent/kb/${id}`,
    method: 'get'
  });
}

export function fetchKbCreate(data: {
  title: string;
  summary?: string;
  content?: string;
  tags?: string[];
  visibility?: 'private' | 'role' | 'public';
  allowedRoleCodes?: string[];
  groupTag?: string;
  entryType?: NianEntryType;
  meta?: Record<string, any>;
  parentId?: string;
  dueAt?: number | null;
  todoStatus?: NianTodoStatus | null;
  doneAt?: number | null;
  primaryArtifactId?: number | null;
  svgArtifactId?: number | null;
  ideaStatus?: NianIdeaStatus | null;
  digestedAt?: number | null;
}) {
  return request<KBEntry>({
    url: '/ai/agent/kb',
    method: 'post',
    data
  });
}

export function fetchKbUpdate(id: string, data: Partial<{
  title: string;
  summary: string;
  content: string;
  tags: string[];
  visibility: 'private' | 'role' | 'public';
  allowedRoleCodes: string[];
  groupTag: string;
  isArchived: boolean;
  appendSource: any;
  entryType: NianEntryType;
  meta: Record<string, any>;
  parentId: string | null;
  dueAt: number | null;
  todoStatus: NianTodoStatus | null;
  doneAt: number | null;
  primaryArtifactId: number | null;
  svgArtifactId: number | null;
  ideaStatus: NianIdeaStatus | null;
  digestedAt: number | null;
}>) {
  return request<KBEntry>({
    url: `/ai/agent/kb/${id}`,
    method: 'patch',
    data
  });
}

export function fetchKbDelete(id: string) {
  return request({
    url: `/ai/agent/kb/${id}`,
    method: 'delete'
  });
}

export function fetchKbSearch(query: string, top_k = 5, max_distance?: number) {
  return request<KBEntry[]>({
    url: '/ai/agent/kb/search/hybrid',
    method: 'get',
    params: {query, top_k, ...(max_distance !== undefined && {max_distance})}
  });
}

export function fetchKbMerge(data: {
  sourceIds: string[];
  targetTitle: string;
  targetSummary?: string;
  targetContent: string;
  targetTags?: string[];
}) {
  return request<KBEntry>({
    url: '/ai/agent/kb/merge',
    method: 'post',
    data
  });
}

export function fetchKbSplit(data: {
  sourceId: string;
  parts: Array<{
    title: string;
    summary?: string;
    content: string;
    tags?: string[];
    visibility?: 'private' | 'role' | 'public';
    allowedRoleCodes?: string[];
    groupTag?: string;
  }>;
  deleteSource?: boolean;
}) {
  return request<KBEntry[]>({
    url: '/ai/agent/kb/split',
    method: 'post',
    data
  });
}

export interface KBSedimentResult {
  candidates: number;
  summary?: string;
  results: Array<{
    action: 'created' | 'updated' | 'skipped' | string;
    entry_id?: string;
    title?: string;
    note?: string;
  }>;
  msg?: string;
}

export function fetchKbSediment(messageId: number, source: 'button' | 'instruction' = 'button') {
  return request<KBSedimentResult>({
    url: '/ai/agent/kb/sediment',
    method: 'post',
    data: {messageId, source}
  });
}

export type KBSedimentSessionEvent =
  | { type: 'started' }
  | { type: 'heartbeat' }
  | { type: 'done'; result: KBSedimentResult }
  | { type: 'quota_exceeded'; message?: string }
  | { type: 'error'; message: string };

/** SSE 版整段会话沉淀进知识库 */
export async function fetchKbSedimentSessionStream(
  data: { session_key: string },
  onEvent: (ev: KBSedimentSessionEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const {baseURL} = getServiceBaseURL(import.meta.env, isHttpProxy);
  const token = await ensureFreshAccessToken();
  const Authorization = `Bearer ${token}`;

  const response = await fetch(`${baseURL}/ai/agent/kb/sediment-session/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization,
      apifoxToken: 'XL299LiMEDZ0H5h3A29PxwQXdMJqWyY2'
    },
    body: JSON.stringify(data),
    signal
  });

  if (!response.ok || !response.body) {
    throw new Error(`请求失败: ${response.status}`);
  }
  await rejectIfJsonError(response);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, {stream: true});
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          onEvent(JSON.parse(line.slice(6)) as KBSedimentSessionEvent);
        } catch {
          // ignore
        }
      }
    }
  }
}


// ─── 知识库 · 万用收件箱 / 卡片操作 / 每日 feed ─────────────────────────────────

export function fetchNianInboxCommit(text: string, sourceHint?: string) {
  return request<KBSedimentResult>({
    url: '/ai/agent/kb/inbox/commit',
    method: 'post',
    data: {text, sourceHint}
  });
}

export type NianInboxCommitEvent =
  | { type: 'started' }
  | { type: 'heartbeat' }
  | { type: 'done'; result: KBSedimentResult }
  | { type: 'error'; message: string };

/** SSE 版万用收件箱：长链路 agent 不会被反代/浏览器 60s 超时掐断 */
export async function fetchNianInboxCommitStream(
  data: {
    text: string;
    sourceHint?: string;
    attachments?: Array<{ name: string; path: string; size: number; isImage: boolean }>;
  },
  onEvent: (ev: NianInboxCommitEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const {baseURL} = getServiceBaseURL(import.meta.env, isHttpProxy);
  const token = await ensureFreshAccessToken();
  const Authorization = `Bearer ${token}`;

  const response = await fetch(`${baseURL}/ai/agent/kb/inbox/commit/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization,
      apifoxToken: 'XL299LiMEDZ0H5h3A29PxwQXdMJqWyY2'
    },
    body: JSON.stringify(data),
    signal
  });

  if (!response.ok || !response.body) {
    throw new Error(`请求失败: ${response.status}`);
  }
  await rejectIfJsonError(response);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, {stream: true});
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          onEvent(JSON.parse(line.slice(6)) as NianInboxCommitEvent);
        } catch {
          // ignore
        }
      }
    }
  }
}

export function fetchNianDismiss(id: string, untilTs: number) {
  return request<KBEntry>({
    url: `/ai/agent/kb/${id}/dismiss`,
    method: 'post',
    data: {untilTs}
  });
}

export function fetchNianTrack(id: string, action: 'opened' | 'double_tap') {
  return request<{ ok: boolean }>({
    url: `/ai/agent/kb/${id}/track`,
    method: 'post',
    data: {action}
  });
}

export function fetchNianIdeaStatus(id: string, status: NianIdeaStatus) {
  return request<KBEntry>({
    url: `/ai/agent/kb/${id}/idea-status`,
    method: 'post',
    data: {status}
  });
}

export interface NianFeedItem extends KBEntry {
  feedConfidence?: number | null;
}

export interface NianFeedToday {
  items: NianFeedItem[];
  brief: string;
  generatedAt: number | null;
}

export function fetchNianFeedToday() {
  return request<NianFeedToday>({
    url: '/ai/agent/kb/feed/today',
    method: 'get'
  });
}

export function fetchNianFeedRerun() {
  return request<{ writtenItems: number }>({
    url: '/ai/agent/kb/feed/rerun',
    method: 'post'
  });
}

// ── AI 用户使用看板（仅管理员）──────────────────────────────────────────────

export function fetchDashboardOverview(params?: { start?: string; end?: string; modules?: string }) {
  return request<Api.AI.DashboardOverview>({
    url: '/ai/dashboard/overview',
    method: 'get',
    params
  });
}

export function fetchDashboardTrend(params: {
  start?: string;
  end?: string;
  metric: 'message' | 'session' | 'batch' | 'activeUser' | 'credit' | 'yuan';
}) {
  return request<Api.AI.DashboardTrend>({
    url: '/ai/dashboard/trend',
    method: 'get',
    params
  });
}

export function fetchDashboardUsers(params?: {
  start?: string;
  end?: string;
  keyword?: string;
  current?: number;
  size?: number;
  order_by?: 'credits' | 'costYuan' | 'messageCount' | 'sessionCount' | 'batchCount' | 'skillCount' | 'lastActiveAt';
}) {
  return request<Api.Common.PaginatingQueryRecord<Api.AI.DashboardUserRecord>>({
    url: '/ai/dashboard/users',
    method: 'get',
    params
  });
}

export function fetchDashboardUserDetail(
  userId: number,
  params?: { start?: string; end?: string; limit?: number }
) {
  return request<Api.AI.DashboardUserDetail>({
    url: `/ai/dashboard/users/${userId}`,
    method: 'get',
    params
  });
}

export function fetchDashboardCostMeta() {
  return request<Api.AI.DashboardCostMeta>({
    url: '/ai/dashboard/cost-meta',
    method: 'get'
  });
}

export function fetchDashboardUsageRecords(params: {
  start?: string;
  end?: string;
  user_id?: number;
  module?: string;
  biz_entry?: string;
  model?: string;
  provider?: string;
  min_credits?: number;
  current?: number;
  size?: number;
}) {
  return request<Api.Common.PaginatingQueryRecord<Api.AI.DashboardUsageRecord>>({
    url: '/ai/dashboard/usage-records',
    method: 'get',
    params
  });
}

export function fetchDashboardPricing(params?: { keyword?: string }) {
  return request<Api.AI.DashboardPricingList>({
    url: '/ai/dashboard/pricing',
    method: 'get',
    params
  });
}

export function upsertDashboardPricing(payload: {
  provider: string;
  model: string;
  unitType: string;
  priceYuan: number | string;
  note?: string;
}) {
  return request<unknown>({
    url: '/ai/dashboard/pricing/upsert',
    method: 'post',
    data: payload
  });
}

export function fetchDashboardPricingHistory(params: {
  provider: string;
  model: string;
  unitType: string;
}) {
  return request<Api.AI.DashboardPricingHistory>({
    url: '/ai/dashboard/pricing/history',
    method: 'get',
    params
  });
}

export function fetchDashboardCreditQuotas(params?: { keyword?: string; current?: number; size?: number }) {
  return request<Api.Common.PaginatingQueryRecord<Api.AI.UserCreditQuotaRecord>>({
    url: '/ai/dashboard/credit-quotas',
    method: 'get',
    params
  });
}

export function fetchSetUserCreditQuota(userId: number, quota: number) {
  return request({
    url: `/ai/dashboard/credit-quotas/${userId}`,
    method: 'post',
    data: {quota}
  });
}

export function fetchMyCreditBalance() {
  return request<Api.AI.MyCreditBalance>({
    url: '/ai/dashboard/my-credit',
    method: 'get'
  });
}

export function fetchDashboardUserSessions(
  userId: number,
  params?: { keyword?: string; current?: number; size?: number }
) {
  return request<Api.Common.PaginatingQueryRecord<Api.AI.DashboardSessionRecord>>({
    url: `/ai/dashboard/users/${userId}/sessions`,
    method: 'get',
    params
  });
}

export function fetchDashboardSessionMessages(sessionKey: string) {
  return request<Api.AI.DashboardSessionMessages>({
    url: `/ai/dashboard/sessions/${sessionKey}/messages`,
    method: 'get'
  });
}

/**
 * 每日简报 SSE 事件类型
 */
export type DailyBriefEvent =
  | { type: 'cached'; brief_date: string }
  | { type: 'generating'; brief_date: string }
  | { type: 'section'; name: 'top' | 'middle'; html: string }
  | { type: 'skills'; items: Array<{ display: string; prompt: string }> }
  | { type: 'done' }
  | { type: 'error'; message: string };

/**
 * 每日简报流式生成（SSE）
 */
export async function fetchDailyBriefStream(
  onEvent: (event: DailyBriefEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const { baseURL } = getServiceBaseURL(import.meta.env, isHttpProxy);
  const token = await ensureFreshAccessToken();
  const Authorization = `Bearer ${token}`;

  const response = await fetch(`${baseURL}/ai/qa/daily-brief/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization,
      apifoxToken: 'XL299LiMEDZ0H5h3A29PxwQXdMJqWyY2'
    },
    signal
  });

  if (!response.ok || !response.body) {
    throw new Error(`请求失败: ${response.status}`);
  }
  await rejectIfJsonError(response);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const event = JSON.parse(line.slice(6)) as DailyBriefEvent;
            onEvent(event);
          } catch (e) {
            console.warn('解析 SSE 事件失败:', e);
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// ==================== Agent 连接器（MCP 服务连接器） ====================

export interface AgentConnector {
  id: number;
  connectorKey: string;
  name: string;
  description: string | null;
  /** 图标：svg 源码或图片 data URI；null=兜底图标 */
  icon: string | null;
  /** 类别：connector 通用连接器 / dataset 数据集（均经 MCP 接入，产品上独立维度展示） */
  kind: 'connector' | 'dataset';
  /** MCP 传输方式 */
  transport: 'sse' | 'streamable_http';
  url: string;
  userId: number | null;
  /** 作者展示名 */
  author: string | null;
  /** 商店上架状态（全局，仅管理员可改）：false=未上架（仅创建者可见） */
  isEnabled: boolean;
  /** 可见档位白名单（显式多选，无包含关系）；null/空数组=全员可见，仅管理员可改 */
  minTierCode: string[] | null;
  /** 个人启用/禁用（只影响自己的 agent 工具加载）；无记录默认 true */
  userEnabled: boolean;
  /** 是否已添加到「我的连接器」；无记录默认未添加（本人创建除外） */
  isAdded: boolean;
  /** 凭据类型：none 无需凭据 / shared 共享凭据 / personal 用户自带 */
  credentialMode: 'none' | 'shared' | 'personal';
  /** 是否配置了共享凭据（仅 shared 类型；api_key 永不回显） */
  hasSharedKey: boolean;
  /** 我是否配置了个人凭据（仅 personal 类型，一人一份） */
  hasMyKey: boolean;
  /** 快捷提问：卡片展示，用户点击即添加该连接器/数据集并把问题填入输入框 */
  exampleQuestions?: string[];
  addedAt: number | null;
  createdAt: number | null;
  updatedAt: number | null;
}

export interface ConnectorTestResult {
  tools: {name: string; description: string | null}[];
  toolCount: number;
  /** 本次试连实际使用的凭据来源：given=显式传入 mine=我的个人凭据 shared=共享凭据 none=无凭据 */
  keySource?: 'given' | 'mine' | 'shared' | 'none';
}

export function fetchAgentConnectors(include_disabled = false) {
  return request<AgentConnector[]>({
    url: '/ai/agent/connectors',
    method: 'get',
    params: {include_disabled}
  });
}

/** 连接器/数据集上架管理分页查询（仅管理员走分页路径；信封 {records, total, current, size}） */
export function fetchAgentConnectorsPaged(params: ManageListQuery) {
  return request<Api.Common.PaginatingQueryRecord<AgentConnector>>({
    url: '/ai/agent/connectors',
    method: 'get',
    params: {...params, include_disabled: true}
  });
}

/** 连接器/数据集上架管理作者清单（仅管理员；kind 区分连接器与数据集，不传=合计） */
export function fetchAgentConnectorAuthorFacets(kind?: 'connector' | 'dataset') {
  return request<AuthorFacet[]>({
    url: '/ai/agent/connectors/manage-authors',
    method: 'get',
    params: {kind}
  });
}

export function fetchCreateAgentConnector(data: {
  name: string;
  description?: string;
  /** 图标：svg 源码或图片 data URI */
  icon?: string;
  /** 类别：connector 连接器 / dataset 数据集（默认 connector） */
  kind?: 'connector' | 'dataset';
  transport: 'sse' | 'streamable_http';
  url: string;
  /** 我的凭据（可选）：存为本人个人凭据 */
  api_key?: string;
  /** 快捷提问（卡片展示一键提问） */
  example_questions?: string[];
}) {
  return request<AgentConnector>({
    url: '/ai/agent/connectors',
    method: 'post',
    data
  });
}

export function fetchUpdateAgentConnector(
  connector_id: number,
  data: {
    name?: string;
    description?: string;
    /** 图标：不传=不改；空串=清除 */
    icon?: string;
    /** 类别：connector / dataset；不传=不改 */
    kind?: 'connector' | 'dataset';
    transport?: 'sse' | 'streamable_http';
    url?: string;
    /** 我的凭据：不传=不改；空串=清除；传值=替换（存本人个人凭据） */
    api_key?: string;
    /** 共享凭据（仅管理员）：传值=设为共享；空串=清除回到各自填写；不传=不动 */
    shared_api_key?: string;
    is_enabled?: boolean;
    /** 可见档位白名单（仅管理员）：空数组=全员可见；档位 code 见 fetchRoleTiers；不传=不改 */
    min_tier_code?: string[] | null;
    /** 仅上架时：mine=带上我的凭据 / keep=保留已有共享凭据 / none(默认)=用户各自填写 */
    with_credential?: 'mine' | 'keep' | 'none';
    /** 快捷提问；空数组=清除；不传=不改 */
    example_questions?: string[];
  }
) {
  return request<AgentConnector>({
    url: `/ai/agent/connectors/${connector_id}`,
    method: 'patch',
    data
  });
}

export function fetchDeleteAgentConnector(connector_id: number) {
  return request<{deleted: number}>({
    url: `/ai/agent/connectors/${connector_id}`,
    method: 'delete'
  });
}

export function fetchBatchAgentConnectorPrefs(
  connector_keys: string[],
  opts: {isEnabled?: boolean; isAdded?: boolean}
) {
  return request<{updated: string[]}>({
    url: '/ai/agent/connectors/prefs',
    method: 'put',
    data: {connector_keys, is_enabled: opts.isEnabled, is_added: opts.isAdded}
  });
}

/** 批量管理连接器（上架管理页，仅管理员）：is_enabled 批量上/下架。
 *  返回 updated=生效的 key，skipped=被跳过的 key（不存在） */
export function fetchBatchManageAgentConnectors(connector_keys: string[], opts: {isEnabled?: boolean}) {
  return request<{updated: string[]; skipped: string[]}>({
    url: '/ai/agent/connectors/manage',
    method: 'put',
    data: {connector_keys, is_enabled: opts.isEnabled}
  });
}

/** 批量删除连接器（上架管理页；管理员可删全部，作者可删自己的） */
export function fetchBatchDeleteAgentConnectors(connector_keys: string[]) {
  return request<{updated: string[]; skipped: string[]}>({
    url: '/ai/agent/connectors/manage-delete',
    method: 'post',
    data: {connector_keys}
  });
}

/** 设置我的个人凭据（一人一份；空串=清除）。加载/试连解析顺序：本人凭据 > 共享凭据 */
export function fetchSetAgentConnectorCredential(connector_id: number, api_key: string) {
  return request<AgentConnector>({
    url: `/ai/agent/connectors/${connector_id}/credential`,
    method: 'put',
    data: {api_key}
  });
}

/** 试连 MCP server：传 id 用库里的连接器（凭据按 本人>共享 解析）；否则传 transport/url/api_key 保存前试连 */
export function fetchTestAgentConnector(
  data: {id: number} | {transport: 'sse' | 'streamable_http'; url: string; api_key?: string}
) {
  return request<ConnectorTestResult>({
    url: '/ai/agent/connectors/test',
    method: 'post',
    data,
    // 后端探针超时 15s：覆盖全局 10s axios 超时，保证后端的分类报错文案（协议选错提示等）能送达
    timeout: 20000
  });
}

// ==================== 快捷功能 API ====================

export interface QuickActionExample {
  id: number;
  actionId: number;
  title: string;
  description?: string;
  conversationData: Array<{
    role: string;
    content: string;
    thinking?: string;
    attachments?: any[];
  }>;
  /** 用户端列表为摘要载荷：conversationData 仅回前 2 条预览，消息总条数以本字段为准 */
  conversationCount?: number;
  previewImage?: string;
  previewImages?: string[];
  previewHtml?: string;
  sortOrder: number;
}

export interface QuickAction {
  id: number;
  name: string;
  skillKey?: string;
  /** 图标：技能 svg 源码 / 图片 data URI（技能派生），旧数据可能是 iconify 名 */
  icon?: string;
  description?: string;
  /** 所属分类 id 列表（技能分类词表；0=「其他」兜底组） */
  categoryIds: number[];
  sortOrder: number;
  /** 管理端列表附带（用户端不返回） */
  isEnabled?: number;
  /** 精选技能（商店橱窗置顶一批） */
  isFeatured?: boolean;
  /** 当前用户是否已添加该技能 */
  isAdded?: boolean;
  /** 案例已随技能整合退役，恒为空数组（保留字段兼容渲染逻辑） */
  examples: QuickActionExample[];
}

/** 快捷功能展示类型：用户页橱窗的章节 */
export interface QuickActionCategory {
  id: number;
  name: string;
  sortOrder: number;
  /** 分类图标：svg 源码 / 图片 data URI（agent_skill_category.icon），空=前端兜底图标 */
  icon?: string;
  /** 管理端列表附带（用户端不返回） */
  isEnabled?: number;
  /** GET /categories 附带：类型下功能数量 */
  actionCount?: number;
}

/** 类型下的功能分组（actionIds 为类型内排序） */
export interface QuickActionGroup {
  id: number;
  name: string;
  /** 分类图标：svg 源码 / 图片 data URI（「其他」虚拟组无图标，前端兜底） */
  icon?: string;
  actionIds: number[];
}

/** 快捷功能列表数据：类型（章节顺序）+ 功能（全局序）+ 分组（类型内序） */
export interface QuickActionData {
  categories: QuickActionCategory[];
  actions: QuickAction[];
  groups: QuickActionGroup[];
}

/** 获取所有启用的快捷功能及案例（用户可见） */
export function fetchQuickActions() {
  return request<QuickActionData>({
    url: '/ai/quick-actions',
    method: 'get'
  });
}

/** 获取全量快捷功能及案例（管理端，含停用，附 isEnabled） */
export function fetchManageQuickActions() {
  return request<QuickActionData>({
    url: '/ai/quick-actions/manage',
    method: 'get'
  });
}

/** 创建快捷功能（管理员） */
export function fetchCreateQuickAction(data: {
  name: string;
  skillKey?: string;
  icon?: string;
  description?: string;
  categoryIds?: number[];
  sortOrder?: number;
  visibility?: string;
  allowedRoleCodes?: string[];
}) {
  return request<QuickAction>({
    url: '/ai/quick-actions',
    method: 'post',
    data
  });
}

/** 更新快捷功能（管理员） */
export function fetchUpdateQuickAction(id: number, data: {
  name?: string;
  skillKey?: string;
  icon?: string;
  description?: string;
  categoryIds?: number[];
  sortOrder?: number;
  isEnabled?: number;
  visibility?: string;
  allowedRoleCodes?: string[];
}) {
  return request<QuickAction>({
    url: `/ai/quick-actions/${id}`,
    method: 'put',
    data
  });
}

/** 删除快捷功能（管理员） */
export function fetchDeleteQuickAction(id: number) {
  return request<null>({
    url: `/ai/quick-actions/${id}`,
    method: 'delete'
  });
}

/** 快捷功能排序（管理员）：categoryId 为空 = 未分组区/全局排序，否则 = 类型内排序 */
export function fetchSortQuickActions(data: { categoryId: number | null; actionIds: number[] }) {
  return request<null>({
    url: '/ai/quick-actions/sort',
    method: 'put',
    data
  });
}

// ── 快捷功能类型（橱窗章节）───────────────────────────────────────────────────

/** 获取类型列表（含各类型下功能数量，管理端） */
export function fetchQuickActionCategories() {
  return request<QuickActionCategory[]>({
    url: '/ai/quick-actions/categories',
    method: 'get'
  });
}

/** 创建类型（管理员） */
export function fetchCreateQuickActionCategory(name: string) {
  return request<QuickActionCategory>({
    url: '/ai/quick-actions/categories',
    method: 'post',
    data: { name }
  });
}

/** 更新类型（管理员）：改名 / 启停 */
export function fetchUpdateQuickActionCategory(id: number, data: { name?: string; isEnabled?: number }) {
  return request<QuickActionCategory>({
    url: `/ai/quick-actions/categories/${id}`,
    method: 'put',
    data
  });
}

/** 删除类型（管理员）：仅解除关联，功能保留 */
export function fetchDeleteQuickActionCategory(id: number) {
  return request<null>({
    url: `/ai/quick-actions/categories/${id}`,
    method: 'delete'
  });
}

/** 类型排序（管理员）：按传入的 id 顺序重排章节 */
export function fetchSortQuickActionCategories(ids: number[]) {
  return request<null>({
    url: '/ai/quick-actions/categories/sort',
    method: 'put',
    data: { ids }
  });
}

/** 为快捷功能添加案例（管理员） */
export function fetchCreateQuickActionExample(actionId: number, data: {
  title: string;
  description?: string;
  conversationData: any[];
  previewImage?: string;
  previewImages?: string[];
  previewHtml?: string;
  sourceSessionId?: number;
  sourceMessageIds?: number[];
  sortOrder?: number;
}) {
  return request<QuickActionExample>({
    url: `/ai/quick-actions/${actionId}/examples`,
    method: 'post',
    data
  });
}

/** 从会话创建案例（管理员） */
export function fetchCreateQuickActionExampleFromSession(actionId: number, data: {
  sessionKey: string;
  title?: string;
  description?: string;
  previewImages?: string[];
  sortOrder?: number;
}) {
  return request<QuickActionExample>({
    url: `/ai/quick-actions/${actionId}/examples/from-session`,
    method: 'post',
    data
  });
}

/** 更新案例（管理员） */
export function fetchUpdateQuickActionExample(exampleId: number, data: {
  title?: string;
  description?: string;
  conversationData?: any[];
  previewImage?: string;
  previewImages?: string[];
  previewHtml?: string;
  sortOrder?: number;
  isEnabled?: number;
}) {
  return request<QuickActionExample>({
    url: `/ai/quick-actions/examples/${exampleId}`,
    method: 'put',
    data
  });
}

/** 删除案例（管理员） */
export function fetchDeleteQuickActionExample(exampleId: number) {
  return request<null>({
    url: `/ai/quick-actions/examples/${exampleId}`,
    method: 'delete'
  });
}

/** 将快捷功能案例 fork 为新的持久化会话 */
export function fetchForkQuickActionExample(exampleId: number) {
  return request<AgentSession>({
    url: `/ai/quick-actions/examples/${exampleId}/fork`,
    method: 'post'
  });
}

// ==================== 专家 / 新手引导 / 用户订阅 API ====================

/** 专家：「人设 + 方法论 + 技能包 + 连接器」封装体，会话级召唤（@专家名 驻留绑定） */
export interface AgentExpert {
  id: number;
  expertKey: string;
  name: string;
  icon?: string | null;
  description?: string | null;
  /** 人设与方法论提示词（注入系统提示词） */
  instructions?: string | null;
  /** 绑定会话首屏欢迎语（纯前端渲染） */
  welcomeMessage?: string | null;
  /** 绑定技能 key 列表（专家会话运行时并入生效集） */
  skillKeys: string[];
  /** 绑定连接器 key 列表（同上） */
  connectorKeys: string[];
  category?: string | null;
  /** 创建者（null=官方预设专家） */
  userId?: number | null;
  author?: string | null;
  sortOrder: number;
  /** 商店上架 */
  isEnabled: boolean;
  /** 可见档位白名单（显式多选，无包含关系）；null/空数组=全员可见，仅管理员可改 */
  minTierCode: string[] | null;
  /** 个人启用（禁=不可召唤） */
  userEnabled: boolean;
  /** 是否已加入「我的专家」 */
  isAdded: boolean;
  /** 快捷提问：卡片展示，用户点击即添加该专家并把问题填入输入框 */
  exampleQuestions?: string[];
  addedAt?: number | null;
  createdAt?: number | null;
  updatedAt?: number | null;
}

/** 专家引导数据 */
export interface ExpertOnboardingData extends QuickActionData {
  needOnboarding: boolean;
  experts: AgentExpert[];
  /** 回显：我的专家 key + 已添加技能 id */
  current: { expertKeys: string[]; actionIds: number[] } | null;
}

/** 我的订阅功能数据（职业字段已随专家体系移除） */
export interface MyActionData {
  onboarded: boolean;
  actionIds: number[];
  actions: QuickAction[];
  categories: QuickActionCategory[];
}

/** 专家列表（商店口径同技能/连接器） */
export function fetchAgentExperts(includeDisabled = false) {
  return request<AgentExpert[]>({
    url: '/ai/agent/experts',
    method: 'get',
    params: {include_disabled: includeDisabled}
  });
}

/** 专家上架管理分页查询（仅管理员走分页路径；信封 {records, total, current, size}） */
export function fetchAgentExpertsPaged(params: ManageListQuery) {
  return request<Api.Common.PaginatingQueryRecord<AgentExpert>>({
    url: '/ai/agent/experts',
    method: 'get',
    params: {...params, include_disabled: true}
  });
}

/** 专家上架管理作者清单（仅管理员） */
export function fetchAgentExpertAuthorFacets() {
  return request<AuthorFacet[]>({
    url: '/ai/agent/experts/manage-authors',
    method: 'get'
  });
}

/** 新建专家（默认未上架，仅创建者可见） */
export function fetchCreateAgentExpert(data: {
  name: string;
  icon?: string | null;
  description?: string | null;
  instructions?: string | null;
  welcomeMessage?: string | null;
  skillKeys?: string[];
  connectorKeys?: string[];
  category?: string | null;
  /** 快捷提问（卡片展示一键提问） */
  exampleQuestions?: string[];
}) {
  return request<AgentExpert>({
    url: '/ai/agent/experts',
    method: 'post',
    data
  });
}

/** 更新专家（创建者/管理员；上下架仅管理员） */
export function fetchUpdateAgentExpert(
  id: number,
  data: {
    name?: string;
    icon?: string | null;
    description?: string | null;
    instructions?: string | null;
    welcomeMessage?: string | null;
    skillKeys?: string[];
    connectorKeys?: string[];
    category?: string | null;
    sortOrder?: number;
    isEnabled?: boolean;
    /** 可见档位白名单（仅管理员）：空数组=全员可见；档位 code 见 fetchRoleTiers；不传=不改 */
    minTierCode?: string[] | null;
    /** 快捷提问；空数组=清除；不传=不改 */
    exampleQuestions?: string[];
  }
) {
  return request<AgentExpert>({
    url: `/ai/agent/experts/${id}`,
    method: 'patch',
    data
  });
}

/** 删除专家（创建者/管理员；引用会话优雅降级为通用会话） */
export function fetchDeleteAgentExpert(id: number) {
  return request<{ deleted: number }>({
    url: `/ai/agent/experts/${id}`,
    method: 'delete'
  });
}

/** 批量设置专家个人偏好（添加/移除、启用/禁用，只影响当前用户） */
export function fetchBatchAgentExpertPrefs(
  expertKeys: string[],
  flags: { isEnabled?: boolean; isAdded?: boolean }
) {
  return request<{ updated: string[] }>({
    url: '/ai/agent/experts/prefs',
    method: 'put',
    data: {expertKeys, ...flags}
  });
}

/** 批量管理专家（上架/下架，仅管理员） */
export function fetchBatchManageAgentExperts(expertKeys: string[], isEnabled: boolean) {
  return request<{ updated: string[]; skipped: string[] }>({
    url: '/ai/agent/experts/manage',
    method: 'put',
    data: {expertKeys, isEnabled}
  });
}

/** 批量删除专家（管理员可删全部；作者可删自己的） */
export function fetchBatchDeleteAgentExperts(expertKeys: string[]) {
  return request<{ updated: string[]; skipped: string[] }>({
    url: '/ai/agent/experts/manage-delete',
    method: 'post',
    data: {expertKeys}
  });
}

/** 获取专家引导数据（专家列表 + 全量可见技能 + 回显） */
export function fetchExpertOnboarding() {
  return request<ExpertOnboardingData>({
    url: '/ai/agent/experts/onboarding',
    method: 'get'
  });
}

/** 完成新手引导：勾选专家（多选，进我的专家）+ 勾选技能 */
export function fetchCompleteExpertOnboarding(data: { expertKeys: string[]; actionIds: number[] }) {
  return request<{ expertKeys: string[]; actionIds: number[] }>({
    url: '/ai/agent/experts/onboarding/complete',
    method: 'post',
    data
  });
}

/** 获取当前用户订阅的功能（首屏橱窗 / 对话框优先渲染） */
export function fetchMyActions() {
  return request<MyActionData>({
    url: '/ai/quick-actions/my',
    method: 'get'
  });
}

/** 修改个人订阅功能 */
export function fetchUpdateMyActions(data: { actionIds: number[] }) {
  return request<{ actionIds: number[] }>({
    url: '/ai/quick-actions/my/actions',
    method: 'put',
    data
  });
}

// ── 定时任务管理 ─────────────────────────────────────────────────────────────

export interface ScheduledTaskRun {
  id: number;
  taskId: number;
  sessionKey: string | null;
  status: 'done' | 'error';
  resultSummary: string | null;
  error: string | null;
  durationMs: number | null;
  fmtCreateTime: string;
  createTime: number;
}

export interface ScheduledTask {
  id: number;
  taskKey: string;
  title: string;
  prompt: string;
  cronExpr: string;
  timezone: string;
  status: 'active' | 'paused' | 'canceled';
  lastRunAt: number | null;
  lastSessionKey: string | null;
  runCount: number;
  fmtCreateTime: string;
  createTime: number;
  recentRuns: ScheduledTaskRun[];
}

/** 查询定时任务列表 */
export function fetchScheduledTasks(params?: { status?: string; current?: number; size?: number }) {
  return request<{ items: ScheduledTask[]; total: number }>({
    url: '/ai/task/scheduled/list',
    method: 'get',
    params
  });
}

/** 暂停定时任务 */
export function fetchPauseScheduledTask(taskId: number) {
  return request<{ taskKey: string; status: string }>({
    url: `/ai/task/scheduled/${taskId}/pause`,
    method: 'post'
  });
}

/** 恢复定时任务 */
export function fetchResumeScheduledTask(taskId: number) {
  return request<{ taskKey: string; status: string }>({
    url: `/ai/task/scheduled/${taskId}/resume`,
    method: 'post'
  });
}

/** 删除定时任务 */
export function fetchDeleteScheduledTask(taskId: number) {
  return request<{ taskKey: string }>({
    url: `/ai/task/scheduled/${taskId}`,
    method: 'delete'
  });
}

// ─── 共享工作流 ──────────────────────────────────────────────────────────────

/** 工作流数据结构 */
export interface WorkflowData {
  id: number;
  workflowKey: string;
  sessionKey?: string;
  title: string;
  /** 板型：board 节点连线流程编排（默认，缺省按 board）/ html 应用制作（agent 开发的多文件 HTML 应用） */
  boardType?: 'board' | 'html';
  /** 仅 html 型：入口 index.html 是否已发布（前端画布据此决定渲染 iframe 还是占位） */
  entryReady?: boolean;
  /** 仅 html 型：分享开关（访客经 /share/{workflowKey} 打开使用，不能编辑板本身） */
  shareOn?: boolean;
  /** 仅 html 型：分享模式（shareOn 开启时有效）：true=免登录公开，false=仅登录用户可打开 */
  sharePublic?: boolean;
  nodes?: any[];
  edges?: any[];
  viewport?: { x: number; y: number; zoom: number };
  version: number;
  updateTime?: number;
  /** 人最近一次改动的详尽简报（字段级旧→新 + 连线端点标签；agent 读板时消费，前端开板时播种「Agent 待办」回显） */
  humanEdit?: {
    added?: {id: string; type?: string; label: string; brief?: string}[];
    edited?: {id: string; label: string; changes?: string[]}[];
    removed?: {id: string; label: string; brief?: string}[];
    edgesAdded?: {id: string; label: string}[];
    edgesRemoved?: {id: string; label: string}[];
    title?: {from: string; to: string};
  };
  /** 上一次写入者：human / agent */
  editor?: string;
  /** 节点徽标（临时协作态）：new=agent 本轮新增 / agent=agent 改过 / human=人改过；edges 仅 new（新连线高亮）。
   *  agent 每次编辑全量重建（旧徽标清零），避免堆积 */
  marks?: BoardMarks;
}

/** 画板徽标：nodes 节点徽标 + edges 新连线高亮，均为临时协作态 */
export interface BoardMarks {
  nodes?: Record<string, {t: 'new' | 'human' | 'agent'}>;
  edges?: Record<string, {t: 'new'}>;
}

/** 工作流列表项 */
export interface WorkflowListItem {
  workflowKey: string;
  title: string;
  /** 板型：board 流程编排（默认，缺省按 board）/ html 应用制作 */
  boardType?: 'board' | 'html';
  version: number;
  updateTime?: number;
  /** 最近一次在本任务内对话的时间（缺省=从未对话）；对话过的任务由后端排在列表最前 */
  lastChatTime?: number;
  nodeCount?: number;
  edgeCount?: number;
  preview?: {label: string; type: string}[];
  /** 应用制作列表卡预览素材（发布时从 index.html 提取）：应用标题 / 标语 / 主题色 */
  htmlPreview?: {title?: string; tagline?: string; accent?: string};
  /** 仅 html 型：分享开关 */
  shareOn?: boolean;
  /** 仅 html 型：分享模式：true=免登录公开，false=仅登录用户 */
  sharePublic?: boolean;
}

/** 创建工作流（boardType：'board' 流程编排（默认）/ 'html' 应用制作） */
export function fetchCreateWorkflow(data: { title: string; sessionKey?: string; boardType?: string; nodes?: any[]; edges?: any[] }) {
  return request<WorkflowData>({
    url: '/ai/agent-workflows',
    method: 'post',
    data
  });
}

/** 读取工作流（可选部分节点：?node_ids=1,2,3） */
export function fetchWorkflow(workflowKey: string, nodeIds?: string) {
  return request<WorkflowData>({
    url: `/ai/agent-workflows/${workflowKey}`,
    method: 'get',
    params: nodeIds ? { node_ids: nodeIds } : undefined
  });
}

/** 工作流轻量元信息（高频轮询专用：不含 nodes/edges，板子再大也是小载荷）；
 *  silentError：轮询偶发失败下一轮自愈，不弹全局提示（否则拥塞时超时弹窗刷屏） */
export function fetchWorkflowMeta(workflowKey: string) {
  return request<{ version: number; title: string; boardType: 'board' | 'html'; entryReady: boolean }>({
    url: `/ai/agent-workflows/${workflowKey}/meta`,
    method: 'get',
    silentError: true
  });
}

/** 整体更新工作流（humanEdit：本次人改动简报，供 Agent 感知；marks：徽标原样回传） */
export function fetchUpdateWorkflow(
  workflowKey: string,
  data: { title?: string; nodes?: any[]; edges?: any[]; viewport?: any; humanEdit?: any; marks?: BoardMarks }
) {
  return request<WorkflowData>({
    url: `/ai/agent-workflows/${workflowKey}`,
    method: 'put',
    data
  });
}

/** 部分合并节点（按 ID） */
export function fetchPatchWorkflowNodes(workflowKey: string, nodes: any[], humanEdit?: any) {
  return request<WorkflowData>({
    url: `/ai/agent-workflows/${workflowKey}/nodes`,
    method: 'patch',
    data: { nodes, humanEdit }
  });
}

/** 删除工作流 */
export function fetchDeleteWorkflow(workflowKey: string) {
  return request<null>({
    url: `/ai/agent-workflows/${workflowKey}`,
    method: 'delete'
  });
}

/** 列出当前用户的工作流 */
export function fetchListWorkflows(keyword?: string) {
  return request<WorkflowListItem[]>({
    url: '/ai/agent-workflows/list',
    method: 'get',
    params: keyword ? { keyword } : undefined
  });
}

/** 签发应用制作托管 token（iframe 以 /api/v1/ai/html-app/{token}/index.html 为 src；
 *  页面内相对引用资源与 json 写回都走同一 token 前缀） */
export function fetchSignHtmlAppToken(workflowKey: string) {
  return request<{ token: string; entryReady: boolean; expiresIn: number }>({
    url: `/ai/agent-workflows/${workflowKey}/html-token`,
    method: 'post'
  });
}

/** 应用制作分享开关（仅板主）：on=true 开启（需已发布），isPublic 选择模式——
 *  true=免登录公开（默认，任意访客匿名打开）/ false=仅登录用户可打开；关闭后 share-view 不再签发新 token */
export function fetchSetWorkflowShare(workflowKey: string, on: boolean, isPublic = true) {
  return request<{ shareOn: boolean; sharePublic: boolean }>({
    url: `/ai/agent-workflows/${workflowKey}/share`,
    method: 'put',
    data: { on, public: isPublic }
  });
}

/** 访客打开分享的应用制作（未开启分享 / 未发布时后端返回 4000；仅登录模式下的匿名访客
 *  返回 4000 + data.needLogin，前端据此引导登录）：返回 share 态托管 token，页面经 /share/{workflowKey} 路由以 iframe 渲染 */
export function fetchShareViewToken(workflowKey: string) {
  return request<{ token: string; title: string; expiresIn: number }>({
    url: `/ai/agent-workflows/${workflowKey}/share-view`,
    method: 'get'
  });
}

/** 工作流版本存档项（两板型统一字段）：html 板 = 用户点「发布」固化的版本（agent_app_file），节点板 = 每次写入的 DB 快照 */
export interface WorkflowVersionItem {
  version: number;
  /** 存档时间（ms 时间戳） */
  archivedAt?: number | null;
  /** 固化该版的写入者（human / agent；切换前自动备份继承当时的改动者；存量存档为 null） */
  editor?: string | null;
  /** 当前正在查看的版本（html 板 = app_version 指针；节点板 = 最新写入） */
  current?: boolean;
  /** html 板：存档文件数 */
  fileCount?: number | null;
  /** html 板：存档总字节数 */
  totalBytes?: number | null;
  /** 节点板：快照节点数 */
  nodeCount?: number | null;
  /** 节点板：快照连线数 */
  edgeCount?: number | null;
}

/** 工作流版本存档清单（降序，最新在前） */
export function fetchWorkflowVersions(workflowKey: string) {
  return request<WorkflowVersionItem[]>({
    url: `/ai/agent-workflows/${workflowKey}/versions`,
    method: 'get'
  });
}

/** 回滚/切换工作流到指定存档版本（时点全量还原）。
 *  html 板：目标版文件还原 + 指针改指（仅未固化改动才自动备份，不复制新版本）；节点板：整板还原 */
export function fetchRollbackWorkflowVersion(workflowKey: string, version: number) {
  return request<{ version: number; newVersion: number }>({
    url: `/ai/agent-workflows/${workflowKey}/versions/${version}/rollback`,
    method: 'post'
  });
}

/** 应用制作发布：把当前状态固化为新版本（版本只在这里产生；内容与既有存档相同则不重复建版） */
export function fetchPublishWorkflowVersion(workflowKey: string) {
  return request<{ version: number; unchanged?: boolean; skipped?: string[] }>({
    url: `/ai/agent-workflows/${workflowKey}/publish`,
    method: 'post'
  });
}
