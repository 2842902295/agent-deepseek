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
 * 重建向量索引
 *
 * @param force_rebuild 是否强制重建
 * @param limit 最多构建多少条标准
 * @param resume 是否从断点继续
 * @param save_interval 增量保存间隔
 */
export function fetchRebuildIndex(data?: {
  force_rebuild?: boolean;
  limit?: number;
  resume?: boolean;
  save_interval?: number;
}) {
  return request<Api.AI.RebuildIndexResponse>({
    url: '/ai/deduplication/rebuild-index',
    method: 'post',
    data
  });
}

/**
 * 获取构建进度
 */
export function fetchBuildProgress() {
  return request<Api.AI.BuildProgressResponse>({
    url: '/ai/deduplication/build-progress',
    method: 'get'
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

/** 通用问答 SSE 事件类型 */
export type QAEvent =
  | {
  type: 'session';
  sessionKey: string | null;
  threadId: string;
  assistantMessageId: number | null;
  userMessageId: number | null
}
  | { type: 'tool_call'; step: number; tool: string; args: Record<string, unknown> }
  | { type: 'tool_result'; step: number; tool: string; content: string }
  | { type: 'thinking'; step: number; content: string }
  | { type: 'answer'; step: number; content: string }
  | { type: 'answer_chunk'; step: number; msg_id: string; content: string }
  | { type: 'reclassify'; step: number; msg_id: string; to: 'thinking'; content: string; subagent?: boolean }
  | { type: 'done'; steps: number }
  | { type: 'aborted' }
  | { type: 'error'; message: string };

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
 */
export async function fetchQAChatStream(
  message: string,
  sessionKey: string | null,
  onEvent: (event: QAEvent) => void,
  signal?: AbortSignal,
  files?: string[],
  workflowKey?: string,
  scopeNodeIds?: string[],
  selectedNodeIds?: string[]
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

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.slice(6)) as QAEvent;
          onEvent(data);
        } catch {
          // 忽略解析异常
        }
      }
    }
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

export type AgentVisibility = 'private' | 'role' | 'public';

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
  /** builtin 内置 / official 官方（超管指定）/ derived 凝练 / curated 收录（上传+发现） */
  source: 'builtin' | 'official' | 'derived' | 'curated';
  originSessionId: number | null;
  userId: number | null;
  isEnabled: boolean;
  visibility: AgentVisibility;
  allowedRoleCodes: string[];
  tags: string[];
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
  is_public?: boolean;
}) {
  return request<{skill?: AgentSkill; message?: string}>({
    url: '/ai/agent/skills/install',
    method: 'post',
    data
  });
}

export async function fetchUploadAgentSkill(
  file: File,
  options: {isPublic?: boolean} = {},
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
        reject(new Error(`上传失败: ${xhr.status}`));
      }
    };
    xhr.onerror = () => reject(new Error('网络错误'));
    xhr.onabort = () => reject(new Error('上传已取消'));

    const formData = new FormData();
    formData.append('file', file);
    formData.append('is_public', String(!!options.isPublic));
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

export function fetchCreateAgentSkill(data: {
  skill_key: string;
  name: string;
  description?: string;
  skill_md: string;
  is_public?: boolean;
}) {
  return request<AgentSkill>({
    url: '/ai/agent/skills',
    method: 'post',
    data
  });
}

export function fetchUpdateAgentSkill(
  skill_id: number,
  data: { name?: string; description?: string; skill_md?: string; is_enabled?: boolean }
) {
  return request<AgentSkill>({
    url: `/ai/agent/skills/${skill_id}`,
    method: 'patch',
    data
  });
}

export function fetchUpdateAgentSkillVisibility(
  skill_id: number,
  data: {visibility: AgentVisibility; allowed_role_codes?: string[]}
) {
  return request<AgentSkill>({
    url: `/ai/agent/skills/${skill_id}/visibility`,
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

/** 设置技能来源（仅超管）：official 官方 / curated 收录 */
export function fetchSetAgentSkillSource(skill_id: number, source: 'official' | 'curated') {
  return request<AgentSkill>({
    url: `/ai/agent/skills/${skill_id}/source`,
    method: 'put',
    data: {source}
  });
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
  is_public?: boolean;
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
  data: { session_key: string; suggested_key?: string; is_public?: boolean },
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
  thinking: string;
  toolSteps: AgentToolStep[];
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

/** 新建会话 */
export function fetchCreateAgentSession(title?: string) {
  return request<AgentSession>({
    url: '/ai/agent/sessions',
    method: 'post',
    data: {title}
  });
}

/** 更新会话（标题/收藏） */
export function fetchUpdateAgentSession(session_key: string, data: { title?: string; is_starred?: number; touch?: boolean }) {
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
  icon?: string;
  description?: string;
  /** 所属类型 id 列表（按章节顺序），来自关联表 */
  categoryIds: number[];
  sortOrder: number;
  /** 管理端列表附带（用户端不返回） */
  isEnabled?: number;
  examples: QuickActionExample[];
}

/** 快捷功能展示类型：用户页橱窗的章节 */
export interface QuickActionCategory {
  id: number;
  name: string;
  sortOrder: number;
  /** 管理端列表附带（用户端不返回） */
  isEnabled?: number;
  /** GET /categories 附带：类型下功能数量 */
  actionCount?: number;
}

/** 类型下的功能分组（actionIds 为类型内排序） */
export interface QuickActionGroup {
  id: number;
  name: string;
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

// ==================== 新手引导 / 用户订阅 API ====================

/** 职业：引导新用户选择身份，并按职业推荐一批快捷功能 */
export interface Profession {
  id: number;
  name: string;
  icon?: string;
  description?: string;
  recommendedActionIds: number[];
  sortOrder: number;
  /** 管理端列表附带 */
  isEnabled?: number;
  /** 管理端列表附带：选择该职业的用户数 */
  userCount?: number;
}

/** 新手引导数据 */
export interface OnboardingData extends QuickActionData {
  needOnboarding: boolean;
  professions: Profession[];
  /** 已引导用户的当前职业与订阅，供「我的功能设置」回显 */
  current: { professionId: number | null; actionIds: number[] } | null;
}

/** 我的订阅功能数据 */
export interface MyActionData {
  professionId: number | null;
  professionName: string | null;
  onboarded: boolean;
  actionIds: number[];
  actions: QuickAction[];
  categories: QuickActionCategory[];
}

/** 获取新手引导数据（职业 + 全量可见功能 + 当前订阅回显） */
export function fetchOnboarding() {
  return request<OnboardingData>({
    url: '/ai/quick-actions/onboarding',
    method: 'get'
  });
}

/** 完成新手引导：选定职业 + 勾选功能 */
export function fetchCompleteOnboarding(data: { professionId: number; actionIds: number[] }) {
  return request<{ professionId: number; actionIds: number[] }>({
    url: '/ai/quick-actions/onboarding/complete',
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

/** 更换职业（订阅重置为新职业推荐） */
export function fetchUpdateMyProfession(data: { professionId: number }) {
  return request<{ professionId: number; actionIds: number[] }>({
    url: '/ai/quick-actions/my/profession',
    method: 'put',
    data
  });
}

/** 职业列表（管理端，含停用） */
export function fetchProfessions() {
  return request<Profession[]>({
    url: '/ai/quick-actions/professions',
    method: 'get'
  });
}

/** 新建职业 */
export function fetchCreateProfession(data: {
  name: string;
  icon?: string;
  description?: string;
  recommendedActionIds?: number[];
  sortOrder?: number;
  isEnabled?: number;
}) {
  return request<Profession>({
    url: '/ai/quick-actions/professions',
    method: 'post',
    data
  });
}

/** 更新职业 */
export function fetchUpdateProfession(
  id: number,
  data: {
    name?: string;
    icon?: string;
    description?: string;
    recommendedActionIds?: number[];
    sortOrder?: number;
    isEnabled?: number;
  }
) {
  return request<Profession>({
    url: `/ai/quick-actions/professions/${id}`,
    method: 'put',
    data
  });
}

/** 删除职业 */
export function fetchDeleteProfession(id: number) {
  return request<null>({
    url: `/ai/quick-actions/professions/${id}`,
    method: 'delete'
  });
}

/** 职业排序（管理员）：按传入的 id 顺序重排 */
export function fetchSortProfessions(ids: number[]) {
  return request<null>({
    url: '/ai/quick-actions/professions/sort',
    method: 'put',
    data: { ids }
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
  /** 板型：board 节点连线流程板（默认，缺省按 board）/ html HTML看板（agent 开发的多文件 HTML 应用） */
  boardType?: 'board' | 'html';
  /** 仅 html 型：入口 index.html 是否已发布（前端画布据此决定渲染 iframe 还是占位） */
  entryReady?: boolean;
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
  /** 板型：board 流程板（默认，缺省按 board）/ html HTML看板 */
  boardType?: 'board' | 'html';
  version: number;
  updateTime?: number;
  nodeCount?: number;
  edgeCount?: number;
  preview?: {label: string; type: string}[];
}

/** 创建工作流（boardType：'board' 流程板（默认）/ 'html' HTML看板） */
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

/** 签发 HTML 看板托管 token（iframe 以 /api/v1/ai/html-app/{token}/index.html 为 src；
 *  页面内相对引用资源与 json 写回都走同一 token 前缀） */
export function fetchSignHtmlAppToken(workflowKey: string) {
  return request<{ token: string; entryReady: boolean; expiresIn: number }>({
    url: `/ai/agent-workflows/${workflowKey}/html-token`,
    method: 'post'
  });
}
