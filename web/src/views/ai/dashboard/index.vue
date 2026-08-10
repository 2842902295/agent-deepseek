<script setup lang="ts">
import {computed, h, onMounted, reactive, ref, watch} from 'vue';
import {useRouter} from 'vue-router';
import {NButton, NTag, NInputNumber, NModal, useMessage} from 'naive-ui';
import {useEcharts} from '@/hooks/common/echarts';
import {useAuthStore} from '@/store/modules/auth';
import {
  fetchDashboardCostMeta,
  fetchDashboardOverview,
  fetchDashboardTrend,
  fetchDashboardUsageRecords,
  fetchDashboardUserDetail,
  fetchDashboardUserSessions,
  fetchDashboardSessionMessages,
  fetchDashboardUsers,
  fetchDashboardCreditQuotas,
  fetchSetUserCreditQuota
} from '@/service/api';
import PricingDrawer from './components/pricing-drawer.vue';

const message = useMessage();
const router = useRouter();
const authStore = useAuthStore();

const isSuper = computed(() => authStore.userInfo.roles.includes('R_SUPER'));

onMounted(() => {
  if (!isSuper.value) {
    message.error('仅管理员可访问该页面');
    router.replace('/403');
    return;
  }
  loadCostMeta();
  loadAll();
});

type Preset = 'today' | '7d' | '30d' | 'custom';
const presetMap: Record<Exclude<Preset, 'custom'>, number> = {today: 1, '7d': 7, '30d': 30};
const preset = ref<Preset>('30d');
const customRange = ref<[number, number] | null>(null);

const moduleOptions = [
  {label: 'QA', value: 'qa'},
  {label: 'Nian', value: 'nian'},
  {label: 'Workbench', value: 'workbench'}
];
const selectedModules = ref<string[]>(['qa', 'nian', 'workbench']);

function pad2(n: number) {
  return n < 10 ? `0${n}` : `${n}`;
}

function toIso(d: Date) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

const range = computed<{ start: string; end: string }>(() => {
  const now = new Date();
  if (preset.value === 'custom' && customRange.value) {
    return {start: toIso(new Date(customRange.value[0])), end: toIso(new Date(customRange.value[1]))};
  }
  if (preset.value === 'today') {
    const start = new Date(now);
    start.setHours(0, 0, 0, 0);
    return {start: toIso(start), end: toIso(now)};
  }
  const days = presetMap[preset.value as Exclude<Preset, 'custom'>] || 30;
  const start = new Date(now.getTime() - days * 24 * 3600 * 1000);
  return {start: toIso(start), end: toIso(now)};
});

const modulesParam = computed(() =>
  selectedModules.value.length === 3 ? undefined : selectedModules.value.join(',') || undefined
);

const today = new Date();
const dateStr = computed(() => {
  const yy = today.getFullYear();
  const mm = String(today.getMonth() + 1).padStart(2, '0');
  const dd = String(today.getDate()).padStart(2, '0');
  const wk = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][today.getDay()];
  return `${yy}.${mm}.${dd} · ${wk}`;
});

// 计费口径元信息（汇率、显示名）
const costMeta = reactive<Api.AI.DashboardCostMeta>({
  creditRateYuan: 0.001,
  creditsPerYuan: 1000,
  creditName: '积分',
  currencyName: '元'
});

async function loadCostMeta() {
  const {data, error} = await fetchDashboardCostMeta();
  if (!error && data) Object.assign(costMeta, data);
}

const overview = reactive<Api.AI.DashboardOverview>({
  activeUsers: 0,
  qaSessionCount: 0,
  nianSessionCount: 0,
  sessionCount: 0,
  messageCount: 0,
  errorMessageCount: 0,
  batchCount: 0,
  batchItemCount: 0,
  skillCount: 0,
  skillPkgCount: 0,
  totalYuan: 0,
  totalCredits: 0,
  costByModule: {},
  rangeStart: '',
  rangeEnd: ''
});
const overviewLoading = ref(false);

async function loadOverview() {
  overviewLoading.value = true;
  try {
    const {data, error} = await fetchDashboardOverview({
      start: range.value.start,
      end: range.value.end,
      modules: modulesParam.value
    });
    if (!error && data) Object.assign(overview, data);
  } finally {
    overviewLoading.value = false;
  }
}

function fmtCredits(v: number | null | undefined) {
  if (!v && v !== 0) return '-';
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)}亿`;
  if (v >= 1e4) return `${(v / 1e4).toFixed(2)}万`;
  return v.toLocaleString(undefined, {maximumFractionDigits: 2});
}

function fmtYuan(v: number | null | undefined) {
  if (!v && v !== 0) return '¥0';
  return `¥${v.toLocaleString(undefined, {maximumFractionDigits: 2})}`;
}

const cards = computed(() => [
  {
    label: 'Credits',
    zh: '消费总额',
    value: fmtCredits(overview.totalCredits),
    accent: 'gold',
    suffix: fmtYuan(overview.totalYuan)
  },
  {
    label: 'Active Users',
    zh: '活跃用户',
    value: overview.activeUsers.toLocaleString(),
    accent: 'blue',
    suffix: ''
  },
  {
    label: 'Sessions',
    zh: '会话总数',
    value: overview.sessionCount.toLocaleString(),
    accent: 'cyan',
    suffix: `QA ${overview.qaSessionCount} · Nian ${overview.nianSessionCount}`
  },
  {
    label: 'Messages',
    zh: '消息总量',
    value: overview.messageCount.toLocaleString(),
    accent: 'sky',
    suffix: overview.errorMessageCount ? `错误 ${overview.errorMessageCount}` : ''
  },
  {
    label: 'Batches',
    zh: '批次任务',
    value: overview.batchCount.toLocaleString(),
    accent: 'violet',
    suffix: `子任务 ${overview.batchItemCount}`
  },
  {
    label: 'Skills',
    zh: '技能凝练',
    value: overview.skillCount.toLocaleString(),
    accent: 'mint',
    suffix: `技能包 ${overview.skillPkgCount}`
  }
]);

// 消费构成：5 个资源类型固定排序
const COST_MODULES: { key: string; label: string; color: string }[] = [
  {key: 'chat', label: 'Chat', color: '#1e40af'},
  {key: 'vision', label: 'Vision', color: '#0891b2'},
  {key: 'embed', label: 'Embed', color: '#0ea5e9'},
  {key: 'mcp', label: 'MCP', color: '#4f46e5'},
  {key: 'video', label: 'Video', color: '#10b981'}
];

const costBreakdown = computed(() => {
  const total = overview.totalCredits || 0;
  return COST_MODULES.map(m => {
    const item = overview.costByModule[m.key] || {yuan: 0, credits: 0};
    return {
      ...m,
      credits: item.credits || 0,
      yuan: item.yuan || 0,
      pct: total > 0 ? (item.credits / total) * 100 : 0
    };
  });
});

type Metric = 'credit' | 'message' | 'session' | 'batch' | 'activeUser';
const metric = ref<Metric>('credit');
const metricOptions: { value: Metric; label: string }[] = [
  {value: 'credit', label: '积分'},
  {value: 'message', label: '消息'},
  {value: 'session', label: '会话'},
  {value: 'batch', label: '批次'},
  {value: 'activeUser', label: '活跃用户'}
];
const metricLabelMap: Record<Metric, string> = {
  credit: '每日消费（积分）',
  message: '每日消息数',
  session: '每日新会话',
  batch: '每日批次',
  activeUser: '每日活跃用户'
};
const metricSeriesColorMap: Record<Metric, string> = {
  credit: '#b08900',
  message: '#1e40af',
  session: '#0891b2',
  batch: '#4f46e5',
  activeUser: '#0ea5e9'
};

const {domRef: trendRef, updateOptions: updateTrend} = useEcharts(() => ({
  tooltip: {
    trigger: 'axis',
    backgroundColor: 'rgba(255,255,255,0.95)',
    borderColor: 'rgba(30,64,175,0.18)',
    textStyle: {color: '#0f172a', fontFamily: 'Plus Jakarta Sans'},
    extraCssText: 'backdrop-filter: blur(20px); box-shadow: 0 12px 32px -12px rgba(30,64,175,0.28)'
  },
  grid: {left: '3%', right: '4%', bottom: '8%', top: '12%', containLabel: true},
  xAxis: {
    type: 'category',
    boundaryGap: false,
    data: [] as string[],
    axisLine: {lineStyle: {color: 'rgba(30,64,175,0.18)'}},
    axisLabel: {color: '#64748b', fontFamily: 'JetBrains Mono', fontSize: 11}
  },
  yAxis: {
    type: 'value',
    splitLine: {lineStyle: {color: 'rgba(30,64,175,0.08)', type: 'dashed'}},
    axisLabel: {color: '#64748b', fontFamily: 'JetBrains Mono', fontSize: 11}
  },
  series: [
    {
      name: '消息',
      type: 'line',
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      lineStyle: {width: 2.5, color: '#1e40af'},
      itemStyle: {color: '#1e40af', borderWidth: 2, borderColor: '#fff'},
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            {offset: 0, color: 'rgba(30, 64, 175, 0.28)'},
            {offset: 1, color: 'rgba(8, 145, 178, 0.02)'}
          ]
        }
      },
      data: [] as number[]
    }
  ]
}));

async function loadTrend() {
  const {data, error} = await fetchDashboardTrend({
    start: range.value.start,
    end: range.value.end,
    metric: metric.value
  });
  if (error || !data) return;
  const color = metricSeriesColorMap[metric.value];
  updateTrend(opts => {
    (opts as any).xAxis.data = data.points.map(p => p.date);
    (opts as any).series[0].data = data.points.map(p => p.value);
    (opts as any).series[0].name = metricLabelMap[metric.value];
    (opts as any).series[0].lineStyle.color = color;
    (opts as any).series[0].itemStyle.color = color;
    return opts;
  });
}

watch(metric, () => loadTrend());

const userKeyword = ref('');
const userOrderBy = ref<
  'credits' | 'costYuan' | 'messageCount' | 'sessionCount' | 'batchCount' | 'skillCount' | 'lastActiveAt'
>('credits');
const userPage = reactive({current: 1, size: 20, total: 0});
const userRows = ref<Api.AI.DashboardUserRecord[]>([]);
const userLoading = ref(false);

async function loadUsers() {
  userLoading.value = true;
  try {
    const {data, error} = await fetchDashboardUsers({
      start: range.value.start,
      end: range.value.end,
      keyword: userKeyword.value || undefined,
      current: userPage.current,
      size: userPage.size,
      order_by: userOrderBy.value
    });
    if (!error && data) {
      userRows.value = data.records || [];
      userPage.total = data.total || 0;
      await loadQuotas(userRows.value.map(r => r.userId));
    }
  } finally {
    userLoading.value = false;
  }
}

function fmtTime(ms: number | null | undefined) {
  if (!ms) return '-';
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

// ── 配额编辑 ──────────────────────────────────────────────────────────────────
const quotaMap = ref<Record<number, {quota: number; used: number; remaining: number}>>({});

async function loadQuotas(userIds: number[]) {
  if (!userIds.length) return;
  const {data, error} = await fetchDashboardCreditQuotas({current: 1, size: 200});
  if (!error && data) {
    const map: Record<number, {quota: number; used: number; remaining: number}> = {};
    for (const r of (data.records || [])) {
      map[r.userId] = {quota: r.quota, used: r.used, remaining: r.remaining};
    }
    quotaMap.value = map;
  }
}

const quotaEditVisible = ref(false);
const quotaEditUser = ref<{userId: number; userName: string; nickName: string} | null>(null);
const quotaEditValue = ref<number>(200000);
const quotaEditSaving = ref(false);

function openQuotaEdit(row: Api.AI.DashboardUserRecord) {
  quotaEditUser.value = {userId: row.userId, userName: row.userName, nickName: row.nickName};
  const current = quotaMap.value[row.userId];
  quotaEditValue.value = current ? current.quota : 200000;
  quotaEditVisible.value = true;
}

async function saveQuota() {
  if (!quotaEditUser.value) return;
  quotaEditSaving.value = true;
  try {
    const {error} = await fetchSetUserCreditQuota(quotaEditUser.value.userId, quotaEditValue.value);
    if (!error) {
      message.success('配额已更新');
      quotaEditVisible.value = false;
      await loadQuotas(userRows.value.map(r => r.userId));
    }
  } finally {
    quotaEditSaving.value = false;
  }
}

const userColumns = computed(() => [
  {
    title: '用户', key: 'userName', width: 180, render: (row: Api.AI.DashboardUserRecord) =>
      h('div', {class: 'user-cell'}, [
        h('div', {class: 'user-cell-nick'}, row.nickName || row.userName),
        h('div', {class: 'user-cell-name'}, `@${row.userName}`)
      ])
  },
  {
    title: '消费', key: 'credits', width: 140, sorter: 'default', className: 'num-col',
    render: (row: Api.AI.DashboardUserRecord) =>
      h('div', {class: 'cost-cell'}, [
        h('div', {class: 'cost-cell-credits'}, fmtCredits(row.credits)),
        h('div', {class: 'cost-cell-yuan'}, fmtYuan(row.costYuan))
      ])
  },
  {title: '会话', key: 'sessionCount', width: 80, sorter: 'default', className: 'num-col'},
  {title: '消息', key: 'messageCount', width: 80, sorter: 'default', className: 'num-col'},
  {title: '批次', key: 'batchCount', width: 80, sorter: 'default', className: 'num-col'},
  {title: '技能', key: 'skillCount', width: 80, sorter: 'default', className: 'num-col'},
  {
    title: '积分余额', key: 'quota', width: 160,
    render: (row: Api.AI.DashboardUserRecord) => {
      const q = quotaMap.value[row.userId];
      if (!q) return h('span', {class: 'dim'}, '-');
      const pct = q.quota > 0 ? Math.min(100, (q.remaining / q.quota) * 100) : 0;
      const color = pct <= 10 ? '#ef4444' : pct <= 30 ? '#f59e0b' : '#10b981';
      return h('div', {class: 'quota-cell'}, [
        h('div', {class: 'quota-bar-wrap'}, [
          h('div', {class: 'quota-bar-fill', style: {width: `${pct}%`, background: color}})
        ]),
        h('div', {class: 'quota-cell-text'}, [
          h('span', {class: 'mono', style: {color}}, fmtCredits(q.remaining)),
          h('span', {class: 'dim'}, ` / ${fmtCredits(q.quota)}`)
        ])
      ]);
    }
  },
  {
    title: '最后活跃', key: 'lastActiveAt', width: 160, sorter: 'default',
    render: (row: Api.AI.DashboardUserRecord) =>
      h('span', {class: 'time-cell'}, fmtTime(row.lastActiveAt))
  },
  {
    title: '', key: 'actions', width: 140,
    render: (row: Api.AI.DashboardUserRecord) =>
      h('div', {style: {display: 'flex', gap: '8px', alignItems: 'center'}}, [
        h(NButton, {size: 'tiny', text: true, onClick: () => openQuotaEdit(row)}, {default: () => '调配额'}),
        h(NButton, {size: 'tiny', text: true, type: 'primary', onClick: () => openDetail(row.userId)}, {default: () => '明细 →'})
      ])
  }
]);

function handleUserSorterChange(sorter: any) {
  if (!sorter) return;
  const map: Record<string, typeof userOrderBy.value> = {
    credits: 'credits',
    sessionCount: 'sessionCount',
    messageCount: 'messageCount',
    batchCount: 'batchCount',
    skillCount: 'skillCount',
    lastActiveAt: 'lastActiveAt'
  };
  if (sorter.columnKey && map[sorter.columnKey]) {
    userOrderBy.value = map[sorter.columnKey];
    userPage.current = 1;
    loadUsers();
  }
}

function handlePageChange(page: number) {
  userPage.current = page;
  loadUsers();
}

function handlePageSizeChange(size: number) {
  userPage.size = size;
  userPage.current = 1;
  loadUsers();
}

const detailVisible = ref(false);
const detailLoading = ref(false);
const detail = ref<Api.AI.DashboardUserDetail | null>(null);
const detailUserId = ref<number | null>(null);

// 会话分页
const sessionsLoading = ref(false);
const sessionsRows = ref<Api.AI.DashboardSessionRecord[]>([]);
const sessionsPage = reactive({current: 1, size: 20, total: 0});
const sessionsKeyword = ref('');

async function loadDetailSessions(uid: number, page = 1) {
  sessionsLoading.value = true;
  sessionsPage.current = page;
  try {
    const {data, error} = await fetchDashboardUserSessions(uid, {
      keyword: sessionsKeyword.value || undefined,
      current: page,
      size: sessionsPage.size
    });
    if (!error && data) {
      sessionsRows.value = data.records || [];
      sessionsPage.total = data.total || 0;
    }
  } finally {
    sessionsLoading.value = false;
  }
}

async function openDetail(uid: number) {
  detailVisible.value = true;
  detailLoading.value = true;
  detailUserId.value = uid;
  detail.value = null;
  sessionsRows.value = [];
  sessionsPage.current = 1;
  sessionsPage.total = 0;
  sessionsKeyword.value = '';
  // 重置消息层
  msgViewVisible.value = false;
  msgViewData.value = null;
  try {
    const {data, error} = await fetchDashboardUserDetail(uid, {
      start: range.value.start,
      end: range.value.end,
      limit: 20
    });
    if (!error) detail.value = data || null;
  } finally {
    detailLoading.value = false;
  }
  loadDetailSessions(uid, 1);
}

// 会话消息查看层
const msgViewVisible = ref(false);
const msgViewLoading = ref(false);
const msgViewData = ref<Api.AI.DashboardSessionMessages | null>(null);

async function openSessionMessages(sessionKey: string) {
  msgViewVisible.value = true;
  msgViewLoading.value = true;
  msgViewData.value = null;
  try {
    const {data, error} = await fetchDashboardSessionMessages(sessionKey);
    if (!error && data) msgViewData.value = data;
  } finally {
    msgViewLoading.value = false;
  }
}

function closeMsgView() {
  msgViewVisible.value = false;
  msgViewData.value = null;
}

const detailCostBreakdown = computed(() => {
  if (!detail.value?.costSummary) return [];
  const cs = detail.value.costSummary;
  const total = cs.totalCredits || 0;
  return COST_MODULES.map(m => {
    const item = cs.byModule[m.key] || {yuan: 0, credits: 0, count: 0};
    return {
      ...m,
      credits: item.credits || 0,
      yuan: item.yuan || 0,
      count: item.count || 0,
      pct: total > 0 ? (item.credits / total) * 100 : 0
    };
  }).filter(x => x.credits > 0 || x.count > 0);
});

const detailRawUnits = computed(() => {
  const u = detail.value?.costSummary?.rawUnits || {};
  // 选择关键字段做展示
  const order = ['token_in', 'token_out', 'token_cached', 'video_sec_480', 'video_sec_720', 'video_sec_1080', 'mcp_call'];
  const labels: Record<string, string> = {
    token_in: '输入 Tokens',
    token_out: '输出 Tokens',
    token_cached: '缓存 Tokens',
    video_sec_480: '视频(480P)秒数',
    video_sec_720: '视频(720P)秒数',
    video_sec_1080: '视频(1080P)秒数',
    mcp_call: 'MCP 调用次'
  };
  return order
    .filter(k => (u[k] || 0) > 0)
    .map(k => ({key: k, label: labels[k] || k, value: u[k]}));
});

const batchStatusType: Record<string, 'success' | 'error' | 'warning' | 'info' | 'default'> = {
  done: 'success',
  partial_failed: 'warning',
  aborted: 'warning',
  running: 'info',
  pending: 'default'
};

// ── 明细流水 ─────────────────────────────────────────────────────────────────
const usageFilters = reactive({
  module: null as string | null,
  bizEntry: null as string | null,
  model: '',
  minCredits: null as number | null
});
const usagePage = reactive({current: 1, size: 20, total: 0});
const usageRows = ref<Api.AI.DashboardUsageRecord[]>([]);
const usageLoading = ref(false);

const moduleSelectOptions = [
  {label: '全部', value: null},
  {label: 'chat', value: 'chat'},
  {label: 'vision', value: 'vision'},
  {label: 'embed', value: 'embed'},
  {label: 'mcp', value: 'mcp'},
  {label: 'video', value: 'video'}
];
const bizEntrySelectOptions = [
  {label: '全部', value: null},
  {label: 'qa', value: 'qa'},
  {label: 'qa-batch', value: 'qa-batch'},
  {label: 'nian', value: 'nian'},
  {label: 'workbench', value: 'workbench'}
];
const moduleTagType: Record<string, 'info' | 'success' | 'warning' | 'error' | 'default'> = {
  chat: 'info',
  vision: 'success',
  embed: 'default',
  mcp: 'warning',
  video: 'error'
};

async function loadUsage() {
  usageLoading.value = true;
  try {
    const {data, error} = await fetchDashboardUsageRecords({
      start: range.value.start,
      end: range.value.end,
      module: usageFilters.module || undefined,
      biz_entry: usageFilters.bizEntry || undefined,
      model: usageFilters.model || undefined,
      min_credits: usageFilters.minCredits ?? undefined,
      current: usagePage.current,
      size: usagePage.size
    });
    if (!error && data) {
      usageRows.value = data.records || [];
      usagePage.total = data.total || 0;
    }
  } finally {
    usageLoading.value = false;
  }
}

function handleUsagePageChange(page: number) {
  usagePage.current = page;
  loadUsage();
}

function handleUsagePageSize(size: number) {
  usagePage.size = size;
  usagePage.current = 1;
  loadUsage();
}

function fmtUnits(row: Api.AI.DashboardUsageRecord): string {
  const u = row.units || {};
  if (row.module === 'chat' || row.module === 'vision') {
    const inT = (u.token_in || 0) + (u.token_cached || 0);
    const out = u.token_out || 0;
    return `${inT.toLocaleString()} in / ${out.toLocaleString()} out`;
  }
  if (row.module === 'embed') {
    return `${(u.token_in || 0).toLocaleString()} tokens`;
  }
  if (row.module === 'video') {
    if (u.video_sec_1080) return `${u.video_sec_1080}s · 1080P`;
    if (u.video_sec_720) return `${u.video_sec_720}s · 720P`;
    if (u.video_sec_480) return `${u.video_sec_480}s · 480P`;
    return '-';
  }
  if (row.module === 'mcp') {
    return `${u.mcp_call || 1} call`;
  }
  return Object.entries(u).map(([k, v]) => `${k}=${v}`).join(' · ') || '-';
}

function fmtUsageTime(ms: number | null) {
  if (!ms) return '-';
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

const usageColumns = computed(() => [
  {
    title: '时间', key: 'createdAt', width: 160,
    render: (row: Api.AI.DashboardUsageRecord) =>
      h('span', {class: 'mono usage-time'}, fmtUsageTime(row.createdAt))
  },
  {
    title: '用户', key: 'userName', width: 140,
    render: (row: Api.AI.DashboardUsageRecord) =>
      h('div', {class: 'user-cell'}, [
        h('div', {class: 'user-cell-nick'}, row.nickName || row.userName || '-'),
        row.userName ? h('div', {class: 'user-cell-name'}, `@${row.userName}`) : null
      ])
  },
  {
    title: '模块', key: 'module', width: 80,
    render: (row: Api.AI.DashboardUsageRecord) =>
      h(NTag, {size: 'small', type: moduleTagType[row.module] || 'default', round: true},
        {default: () => row.module})
  },
  {
    title: '入口', key: 'bizEntry', width: 90,
    render: (row: Api.AI.DashboardUsageRecord) =>
      row.bizEntry
        ? h(NTag, {size: 'small', round: true}, {default: () => row.bizEntry})
        : h('span', {class: 'dim'}, '-')
  },
  {
    title: '模型', key: 'model', width: 200,
    render: (row: Api.AI.DashboardUsageRecord) =>
      h('span', {class: 'mono usage-model', title: `${row.provider} / ${row.model || ''}`},
        row.model || row.provider)
  },
  {
    title: '用量', key: 'units', width: 200,
    render: (row: Api.AI.DashboardUsageRecord) =>
      h('span', {class: 'mono usage-units'}, fmtUnits(row))
  },
  {
    title: '消费', key: 'credits', width: 130, sorter: false,
    render: (row: Api.AI.DashboardUsageRecord) =>
      h('div', {class: 'cost-cell'}, [
        h('div', {class: 'cost-cell-credits'}, fmtCredits(row.credits)),
        h('div', {class: 'cost-cell-yuan'}, fmtYuan(row.costYuan))
      ])
  }
]);

const pricingDrawerVisible = ref(false);

async function loadAll() {
  userPage.current = 1;
  usagePage.current = 1;
  await Promise.all([loadOverview(), loadTrend(), loadUsers(), loadUsage()]);
}

function handleSearch() {
  loadAll();
}

function handleUsageSearch() {
  usagePage.current = 1;
  loadUsage();
}

function goBack() {
  router.back();
}
</script>

<template>
  <div class="dashboard-page">
    <div class="aurora" aria-hidden="true">
      <div class="aurora-orb aurora-1" />
      <div class="aurora-orb aurora-2" />
      <div class="aurora-orb aurora-3" />
      <div class="aurora-orb aurora-4" />
      <div class="aurora-grain" />
    </div>

    <header class="topbar">
      <div class="topbar-inner">
        <div class="brand">
          <div class="brand-mark">
            <span class="bm-glyph">
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" width="16" height="16">
                <path d="M3 3h6v8H3zM11 3h6v5h-6zM11 10h6v7h-6zM3 13h6v4H3z" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </span>
            <span class="bm-halo" />
          </div>
          <div class="brand-text">
            <span class="brand-zh">Dashboard</span>
            <span class="brand-en">{{ dateStr }}</span>
          </div>
          <div class="agent-badge">
            <span class="ab-pulse" />
            <span class="ab-text">ADMIN · 看板</span>
          </div>
        </div>
        <div class="topbar-actions">
          <button class="action-nav" title="返回" @click="goBack">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14">
              <path d="M12 5l-5 5 5 5" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <span class="action-nav-text">返回</span>
          </button>
          <button class="action-nav" title="单价管理" @click="pricingDrawerVisible = true">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14">
              <circle cx="10" cy="10" r="2.5" />
              <path d="M10 1.5v3M10 15.5v3M3.5 10h-2M18.5 10h-2M5 5l-1.5-1.5M16.5 16.5L15 15M5 15l-1.5 1.5M16.5 3.5L15 5" stroke-linecap="round" />
            </svg>
            <span class="action-nav-text">单价</span>
          </button>
          <button class="action action-primary" :disabled="overviewLoading" @click="handleSearch">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
              <path d="M3 10a7 7 0 0 1 12-4.95M17 10a7 7 0 0 1-12 4.95" stroke-linecap="round" />
              <path d="M15 2v4h-4M5 18v-4h4" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <span class="action-text">刷新</span>
          </button>
        </div>
      </div>
    </header>

    <main class="main">
      <section class="filter-bar">
        <div class="filter-inner">
          <div class="filter-group">
            <span class="filter-label">时间</span>
            <div class="seg">
              <button
                v-for="p in (['today','7d','30d','custom'] as Preset[])"
                :key="p"
                class="seg-btn"
                :class="preset === p && 'seg-btn-active'"
                @click="preset = p"
              >
                {{ p === 'today' ? '今天' : p === '7d' ? '近 7 天' : p === '30d' ? '近 30 天' : '自定义' }}
              </button>
            </div>
            <NDatePicker
              v-if="preset === 'custom'"
              v-model:value="customRange"
              type="datetimerange"
              clearable
              size="small"
              class="custom-range"
            />
          </div>

          <div class="filter-divider" />

          <div class="filter-group">
            <span class="filter-label">模块</span>
            <div class="chip-row">
              <button
                v-for="m in moduleOptions"
                :key="m.value"
                class="chip"
                :class="selectedModules.includes(m.value) && 'chip-active'"
                @click="
                  selectedModules.includes(m.value)
                    ? (selectedModules = selectedModules.filter(x => x !== m.value))
                    : selectedModules.push(m.value)
                "
              >
                {{ m.label }}
              </button>
            </div>
          </div>
        </div>
      </section>

      <section class="cards-row">
        <div
          v-for="c in cards"
          :key="c.label"
          class="stat-card"
          :class="`stat-${c.accent}`"
        >
          <div class="stat-head">
            <span class="stat-label-en">{{ c.label }}</span>
            <span class="stat-label-zh">{{ c.zh }}</span>
          </div>
          <div class="stat-value">{{ c.value }}</div>
          <div v-if="c.suffix" class="stat-suffix">{{ c.suffix }}</div>
          <div class="stat-glow" />
        </div>
      </section>

      <section class="glass-card cost-card">
        <header class="card-header">
          <div class="card-title">
            <span class="ct-zh">消费构成</span>
            <span class="ct-en">Cost Breakdown · 1 元 = {{ costMeta.creditsPerYuan }} 积分</span>
          </div>
          <div class="cost-total-tag">
            <span class="ctt-credits">{{ fmtCredits(overview.totalCredits) }}</span>
            <span class="ctt-yuan">{{ fmtYuan(overview.totalYuan) }}</span>
          </div>
        </header>
        <div class="cost-body">
          <div class="cost-bar">
            <div
              v-for="seg in costBreakdown"
              :key="seg.key"
              class="cost-bar-seg"
              :style="{ width: seg.pct + '%', background: seg.color }"
              :title="`${seg.label} · ${fmtCredits(seg.credits)} (${seg.pct.toFixed(1)}%)`"
            />
          </div>
          <ul class="cost-legend">
            <li v-for="seg in costBreakdown" :key="seg.key" class="cost-legend-item">
              <span class="cl-dot" :style="{ background: seg.color }" />
              <span class="cl-label">{{ seg.label }}</span>
              <span class="cl-credits">{{ fmtCredits(seg.credits) }}</span>
              <span class="cl-pct">{{ seg.pct.toFixed(1) }}%</span>
            </li>
          </ul>
        </div>
      </section>

      <section class="glass-card trend-card">
        <header class="card-header">
          <div class="card-title">
            <span class="ct-zh">使用趋势</span>
            <span class="ct-en">{{ metricLabelMap[metric] }}</span>
          </div>
          <div class="seg">
            <button
              v-for="m in metricOptions"
              :key="m.value"
              class="seg-btn"
              :class="metric === m.value && 'seg-btn-active'"
              @click="metric = m.value"
            >
              {{ m.label }}
            </button>
          </div>
        </header>
        <div ref="trendRef" class="trend-chart" />
      </section>

      <section class="glass-card users-card">
        <header class="card-header">
          <div class="card-title">
            <span class="ct-zh">用户排行</span>
            <span class="ct-en">Top Users · {{ userPage.total }} total</span>
          </div>
          <div class="search-wrap">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14">
              <circle cx="9" cy="9" r="6" />
              <path d="M14 14l3 3" stroke-linecap="round" />
            </svg>
            <input
              v-model="userKeyword"
              class="search-input"
              placeholder="搜索用户名 / 昵称"
              @keydown.enter="loadUsers"
            />
          </div>
        </header>

        <NDataTable
          remote
          size="small"
          :loading="userLoading"
          :columns="userColumns"
          :data="userRows"
          :row-key="(row: Api.AI.DashboardUserRecord) => row.userId"
          :pagination="{
            page: userPage.current,
            pageSize: userPage.size,
            itemCount: userPage.total,
            showSizePicker: true,
            pageSizes: [10, 20, 50, 100],
            onChange: handlePageChange,
            onUpdatePageSize: handlePageSizeChange
          }"
          class="glass-table"
          @update:sorter="handleUserSorterChange"
        />
      </section>

      <section class="glass-card usage-card">
        <header class="card-header">
          <div class="card-title">
            <span class="ct-zh">明细流水</span>
            <span class="ct-en">Usage Records · {{ usagePage.total }} total</span>
          </div>
          <div class="usage-filters">
            <NSelect
              v-model:value="usageFilters.module"
              :options="moduleSelectOptions"
              size="small"
              placeholder="模块"
              clearable
              style="width: 110px"
              @update:value="handleUsageSearch"
            />
            <NSelect
              v-model:value="usageFilters.bizEntry"
              :options="bizEntrySelectOptions"
              size="small"
              placeholder="入口"
              clearable
              style="width: 130px"
              @update:value="handleUsageSearch"
            />
            <NInput
              v-model:value="usageFilters.model"
              placeholder="模型名"
              size="small"
              clearable
              style="width: 160px"
              @keydown.enter="handleUsageSearch"
              @clear="handleUsageSearch"
            />
            <NInputNumber
              v-model:value="usageFilters.minCredits"
              placeholder="最低积分"
              size="small"
              :min="0"
              clearable
              style="width: 130px"
              @keydown.enter="handleUsageSearch"
              @clear="handleUsageSearch"
            />
            <NButton size="small" type="primary" @click="handleUsageSearch">查询</NButton>
          </div>
        </header>

        <NDataTable
          remote
          size="small"
          :loading="usageLoading"
          :columns="usageColumns"
          :data="usageRows"
          :row-key="(row: Api.AI.DashboardUsageRecord) => row.id"
          :pagination="{
            page: usagePage.current,
            pageSize: usagePage.size,
            itemCount: usagePage.total,
            showSizePicker: true,
            pageSizes: [10, 20, 50, 100],
            onChange: handleUsagePageChange,
            onUpdatePageSize: handleUsagePageSize
          }"
          class="glass-table"
        />
      </section>
    </main>

    <PricingDrawer v-model:show="pricingDrawerVisible" />

    <!-- 配额编辑 Modal -->
    <NModal v-model:show="quotaEditVisible" preset="card" :title="quotaEditUser ? `调整配额：${quotaEditUser.nickName || quotaEditUser.userName}` : '调整配额'" style="max-width: 400px">
      <div style="padding: 8px 0">
        <div style="margin-bottom: 8px; font-size: 13px; color: #64748b">积分配额（0 = 立即冻结）</div>
        <NInputNumber
          v-model:value="quotaEditValue"
          :min="0"
          :step="10000"
          style="width: 100%"
          placeholder="输入积分配额"
        />
        <div style="margin-top: 8px; font-size: 12px; color: #94a3b8">默认 20 万积分 · 1 元 ≈ 1000 积分（按实际单价计）</div>
      </div>
      <template #footer>
        <div style="display:flex; justify-content:flex-end; gap:8px">
          <NButton @click="quotaEditVisible = false">取消</NButton>
          <NButton type="primary" :loading="quotaEditSaving" @click="saveQuota">保存</NButton>
        </div>
      </template>
    </NModal>

    <NDrawer v-model:show="detailVisible" :width="640" placement="right">
      <NDrawerContent
        :title="detail ? `${detail.user.nickName || detail.user.userName} 的使用明细` : '加载中...'"
        closable
      >
        <NSpin :show="detailLoading">
          <div v-if="detail" class="detail-body">
            <div class="detail-grid">
              <div class="detail-item">
                <span class="di-label">用户</span>
                <span class="di-value">{{ detail.user.nickName || detail.user.userName }}</span>
              </div>
              <div class="detail-item">
                <span class="di-label">账号</span>
                <span class="di-value mono">@{{ detail.user.userName }}</span>
              </div>
              <div class="detail-item detail-item-cost">
                <span class="di-label">消费</span>
                <span class="di-value mono">{{ fmtCredits(detail.costSummary?.totalCredits || 0) }}</span>
                <span class="di-sub">{{ fmtYuan(detail.costSummary?.totalYuan || 0) }}</span>
              </div>
              <div class="detail-item">
                <span class="di-label">会话</span>
                <span class="di-value mono">{{ detail.overview.sessionCount }}</span>
              </div>
              <div class="detail-item">
                <span class="di-label">消息</span>
                <span class="di-value mono">{{ detail.overview.messageCount }}</span>
              </div>
              <div class="detail-item">
                <span class="di-label">批次</span>
                <span class="di-value mono">{{ detail.overview.batchCount }}</span>
              </div>
              <div class="detail-item">
                <span class="di-label">技能 / 包</span>
                <span class="di-value mono">{{ detail.overview.skillCount }} / {{ detail.overview.skillPkgCount }}</span>
              </div>
            </div>

            <div v-if="detailCostBreakdown.length" class="detail-cost-section">
              <div class="dcs-title">消费分项</div>
              <ul class="dcs-list">
                <li v-for="seg in detailCostBreakdown" :key="seg.key" class="dcs-item">
                  <span class="dcs-dot" :style="{ background: seg.color }" />
                  <span class="dcs-label">{{ seg.label }}</span>
                  <span class="dcs-credits mono">{{ fmtCredits(seg.credits) }}</span>
                  <span class="dcs-pct mono">{{ seg.pct.toFixed(1) }}%</span>
                  <span class="dcs-count">·  {{ seg.count }} 次</span>
                </li>
              </ul>
            </div>

            <div v-if="detailRawUnits.length" class="detail-units-section">
              <div class="dcs-title">原始量纲</div>
              <div class="dus-grid">
                <div v-for="u in detailRawUnits" :key="u.key" class="dus-cell">
                  <div class="dus-key">{{ u.label }}</div>
                  <div class="dus-val mono">{{ u.value.toLocaleString(undefined, { maximumFractionDigits: 0 }) }}</div>
                </div>
              </div>
            </div>

            <NTabs type="line" animated class="detail-tabs">
              <NTabPane name="sessions" :tab="`全部会话 (${sessionsPage.total})`">
                <div class="sessions-search-wrap">
                  <input
                    v-model="sessionsKeyword"
                    class="sessions-search-input"
                    placeholder="搜索会话标题"
                    @keydown.enter="detailUserId && loadDetailSessions(detailUserId, 1)"
                  />
                </div>
                <NSpin :show="sessionsLoading">
                  <div v-if="!sessionsLoading && !sessionsRows.length" class="empty-tip">无会话数据</div>
                  <ul v-else class="detail-list">
                    <li
                      v-for="s in sessionsRows"
                      :key="s.id"
                      class="detail-list-item detail-list-item-clickable"
                      @click="openSessionMessages(s.sessionKey)"
                    >
                      <div class="dli-row">
                        <div class="dli-title">{{ s.title }}</div>
                        <span class="dli-arrow">›</span>
                      </div>
                      <div class="dli-meta">
                        <span>消息 <b class="mono">{{ s.messageCount }}</b></span>
                        <span class="dot-sep">·</span>
                        <span>{{ fmtTime(s.updatedAt) }}</span>
                      </div>
                    </li>
                  </ul>
                </NSpin>
                <div v-if="sessionsPage.total > sessionsPage.size" class="sessions-pagination">
                  <NPagination
                    :page="sessionsPage.current"
                    :page-size="sessionsPage.size"
                    :item-count="sessionsPage.total"
                    simple
                    @update:page="(p) => detailUserId && loadDetailSessions(detailUserId, p)"
                  />
                </div>
              </NTabPane>

              <NTabPane name="batches" :tab="`最近批次 (${detail.batches.length})`">
                <div v-if="!detail.batches.length" class="empty-tip">无数据</div>
                <ul v-else class="detail-list">
                  <li v-for="b in detail.batches" :key="b.id" class="detail-list-item">
                    <div class="dli-title">{{ b.skillName || b.batchKey }}</div>
                    <div class="dli-meta">
                      <NTag size="small" :type="batchStatusType[b.status] || 'default'" round>{{ b.status }}</NTag>
                      <span>总 <b class="mono">{{ b.total }}</b></span>
                      <span>成 <b class="mono">{{ b.succeeded }}</b></span>
                      <span>败 <b class="mono">{{ b.failed }}</b></span>
                      <span class="dot-sep">·</span>
                      <span>{{ fmtTime(b.createdAt) }}</span>
                    </div>
                  </li>
                </ul>
              </NTabPane>

              <NTabPane name="skills" :tab="`凝练技能 (${detail.skills.length})`">
                <div v-if="!detail.skills.length" class="empty-tip">无数据</div>
                <ul v-else class="detail-list">
                  <li v-for="sk in detail.skills" :key="sk.id" class="detail-list-item">
                    <div class="dli-title">{{ sk.name }}</div>
                    <div class="dli-meta">
                      <NTag size="small" round>{{ sk.source }}</NTag>
                      <NTag size="small" :type="sk.isEnabled ? 'success' : 'default'" round>
                        {{ sk.isEnabled ? '启用' : '停用' }}
                      </NTag>
                      <span>{{ sk.visibility }}</span>
                      <span class="dot-sep">·</span>
                      <span>{{ fmtTime(sk.createdAt) }}</span>
                    </div>
                  </li>
                </ul>
              </NTabPane>
            </NTabs>
          </div>
        </NSpin>
      </NDrawerContent>
    </NDrawer>

    <!-- 会话消息详情抽屉 -->
    <NDrawer v-model:show="msgViewVisible" :width="680" placement="right">
      <NDrawerContent closable @close="closeMsgView">
        <template #header>
          <div class="msg-drawer-header">
            <button class="msg-back-btn" @click="closeMsgView">
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14">
                <path d="M12 5l-5 5 5 5" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </button>
            <span class="msg-drawer-title">{{ msgViewData?.title || '会话消息' }}</span>
          </div>
        </template>
        <NSpin :show="msgViewLoading">
          <div v-if="msgViewData" class="msg-list">
            <div
              v-for="msg in msgViewData.messages"
              :key="msg.id"
              class="msg-bubble-wrap"
              :class="msg.role === 'user' ? 'msg-user' : 'msg-assistant'"
            >
              <div class="msg-role-label">{{ msg.role === 'user' ? '用户' : 'AI' }}</div>
              <div class="msg-bubble" :class="{'msg-bubble-error': msg.status === 'error'}">
                <div v-if="msg.content" class="msg-content">{{ msg.content }}</div>
                <div v-if="msg.status === 'error' && msg.error" class="msg-error-text">{{ msg.error }}</div>
                <div v-if="msg.toolSteps && msg.toolSteps.length" class="msg-tools">
                  <div
                    v-for="step in msg.toolSteps.filter(s => s.type === 'tool_call')"
                    :key="step.id"
                    class="msg-tool-call"
                  >
                    <span class="tc-icon">⚙</span>
                    <span class="tc-name">{{ step.tool }}</span>
                  </div>
                </div>
                <div class="msg-time">{{ fmtTime(msg.createdAt) }}</div>
              </div>
            </div>
            <div v-if="!msgViewData.messages.length" class="empty-tip">该会话暂无消息</div>
          </div>
        </NSpin>
      </NDrawerContent>
    </NDrawer>
  </div>
</template>

<style scoped>

.dashboard-page {
  --bg: #f5f7fb;
  --bg-deep: #eaf0f9;

  --surface: rgba(255, 255, 255, 0.42);
  --surface-strong: rgba(255, 255, 255, 0.62);
  --surface-deep: rgba(255, 255, 255, 0.78);
  --highlight: rgba(255, 255, 255, 0.95);
  --inner-shadow: rgba(30, 64, 175, 0.04);

  --border: rgba(30, 64, 175, 0.1);
  --border-strong: rgba(30, 64, 175, 0.18);
  --border-glow: rgba(30, 64, 175, 0.25);

  --ink: #0f172a;
  --ink-soft: #334155;
  --ink-mute: #64748b;
  --ink-faint: #94a3b8;

  --c-deep: #1e3a8a;
  --c-blue: #1e40af;
  --c-blue-2: #2563eb;
  --c-sky: #0ea5e9;
  --c-cyan: #0891b2;
  --c-teal: #0e7490;
  --c-violet: #4f46e5;
  --c-mint: #10b981;

  --aurora: linear-gradient(110deg, var(--c-blue) 0%, var(--c-blue-2) 35%, var(--c-sky) 70%, var(--c-cyan) 100%);

  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04), 0 4px 16px -8px rgba(30, 64, 175, 0.12);
  --shadow-md: 0 1px 2px rgba(15, 23, 42, 0.05), 0 12px 32px -12px rgba(30, 64, 175, 0.18);
  --shadow-lg: 0 1px 2px rgba(15, 23, 42, 0.05), 0 24px 64px -20px rgba(30, 64, 175, 0.28);
  --shadow-glow: 0 8px 32px -10px rgba(30, 64, 175, 0.45);

  min-height: 100vh;
  position: relative;
  overflow-x: hidden;
  font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
  color: var(--ink);
  background: var(--bg);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

.aurora {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  overflow: hidden;
}
.aurora-orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(120px);
  will-change: transform;
}
.aurora-1 { width: 680px; height: 680px; top: -200px; right: -160px; background: radial-gradient(circle, var(--c-blue) 0%, transparent 65%); opacity: 0.32; animation: aurora-drift-1 26s ease-in-out infinite; }
.aurora-2 { width: 720px; height: 720px; bottom: -260px; left: -200px; background: radial-gradient(circle, var(--c-cyan) 0%, transparent 65%); opacity: 0.30; animation: aurora-drift-2 32s ease-in-out infinite; }
.aurora-3 { width: 480px; height: 480px; top: 36%; right: 14%; background: radial-gradient(circle, var(--c-sky) 0%, transparent 65%); opacity: 0.28; animation: aurora-drift-3 28s ease-in-out infinite; }
.aurora-4 { width: 420px; height: 420px; top: 18%; left: -60px; background: radial-gradient(circle, var(--c-violet) 0%, transparent 65%); opacity: 0.20; animation: aurora-drift-1 30s ease-in-out infinite reverse; }
.aurora-grain {
  position: absolute; inset: 0;
  background-image: radial-gradient(circle at 1px 1px, rgba(15, 23, 42, 0.05) 1px, transparent 0);
  background-size: 3px 3px;
  opacity: 0.4;
  mix-blend-mode: multiply;
}
@keyframes aurora-drift-1 { 0%,100%{transform:translate(0,0) scale(1);} 50%{transform:translate(60px,40px) scale(1.08);} }
@keyframes aurora-drift-2 { 0%,100%{transform:translate(0,0) scale(1);} 50%{transform:translate(-40px,-60px) scale(1.05);} }
@keyframes aurora-drift-3 { 0%,100%{transform:translate(0,0) scale(1);} 33%{transform:translate(-50px,30px) scale(0.96);} 66%{transform:translate(40px,-25px) scale(1.04);} }
@media (prefers-reduced-motion: reduce) { .aurora-orb { animation: none !important; } }

.topbar {
  position: sticky;
  top: 16px;
  z-index: 30;
  margin: 16px auto 0;
  max-width: 1280px;
  padding: 0 24px;
}
.topbar-inner {
  background: var(--surface);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 9px 11px 9px 13px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 8px 24px -8px rgba(30, 64, 175, 0.18);
}
.brand { display: flex; align-items: center; gap: 12px; }
.brand-mark {
  position: relative;
  width: 34px; height: 34px;
  border-radius: 11px;
  background: var(--aurora);
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  color: #fff;
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,0.4),
    0 4px 12px -4px rgba(30,64,175,0.5);
}
.bm-glyph { display: flex; }
.bm-halo {
  position: absolute; inset: -6px;
  border-radius: 14px;
  background: var(--aurora);
  opacity: 0.35;
  filter: blur(8px);
  z-index: -1;
}
.brand-text { display: flex; flex-direction: column; line-height: 1.15; }
.brand-zh { font-weight: 700; font-size: 15px; color: var(--ink); letter-spacing: 0.005em; }
.brand-en { font-family: 'JetBrains Mono', monospace; font-size: 10px; color: var(--ink-mute); font-weight: 600; }
.agent-badge {
  display: inline-flex; align-items: center; gap: 6px;
  height: 22px; padding: 0 10px;
  border-radius: 999px;
  background: rgba(30,64,175,0.08);
  border: 1px solid rgba(30,64,175,0.18);
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px; font-weight: 700; letter-spacing: 0.04em;
  color: var(--c-blue);
}
.ab-pulse {
  width: 6px; height: 6px;
  border-radius: 50%;
  background: var(--c-blue);
  box-shadow: 0 0 0 3px rgba(30,64,175,0.2);
  animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse { 0%,100%{opacity:1;transform:scale(1);} 50%{opacity:0.6;transform:scale(0.85);} }

.topbar-actions { display: flex; align-items: center; gap: 8px; }
.action-nav, .action {
  display: inline-flex; align-items: center; gap: 6px;
  height: 34px; padding: 0 12px;
  border: 1px solid var(--border);
  background: var(--surface-strong);
  color: var(--ink-soft);
  cursor: pointer;
  border-radius: 11px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 12px; font-weight: 600;
  box-shadow: inset 0 1px 0 var(--highlight), inset 0 0 0 1px rgba(255,255,255,0.4);
  transition: all 0.2s ease;
}
.action-nav:hover, .action:hover {
  color: var(--c-blue);
  border-color: var(--border-glow);
  box-shadow: inset 0 1px 0 var(--highlight), var(--shadow-glow);
  transform: translateY(-1px);
}
.action-primary {
  background: var(--aurora); color: #fff;
  border-color: transparent;
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,0.4),
    0 6px 16px -4px rgba(30,64,175,0.45);
}
.action-primary:hover { color: #fff; box-shadow: inset 0 1px 0 rgba(255,255,255,0.4), 0 10px 24px -4px rgba(30,64,175,0.55); }
.action-primary:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }

.main {
  position: relative;
  z-index: 1;
  max-width: 1280px;
  margin: 24px auto 64px;
  padding: 0 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.filter-bar {
  background: var(--surface);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border);
  border-radius: 18px;
  box-shadow: inset 0 1px 0 var(--highlight), var(--shadow-sm);
}
.filter-inner {
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  padding: 14px 18px;
}
.filter-group { display: flex; align-items: center; gap: 10px; }
.filter-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px; font-weight: 700; letter-spacing: 0.06em;
  color: var(--ink-mute); text-transform: uppercase;
}
.filter-divider { width: 1px; height: 22px; background: var(--border); }

.seg {
  display: inline-flex; gap: 2px;
  padding: 3px;
  background: rgba(30,64,175,0.05);
  border: 1px solid var(--border);
  border-radius: 11px;
}
.seg-btn {
  height: 26px; padding: 0 12px;
  border: 0; background: transparent;
  border-radius: 8px; cursor: pointer;
  font-family: inherit; font-size: 12px; font-weight: 600;
  color: var(--ink-mute);
  transition: all 0.18s ease;
}
.seg-btn:hover { color: var(--c-blue); }
.seg-btn-active {
  background: #fff; color: var(--c-blue);
  box-shadow: 0 1px 2px rgba(15,23,42,0.06), 0 4px 8px -4px rgba(30,64,175,0.18);
}

.chip-row { display: flex; gap: 6px; }
.chip {
  height: 28px; padding: 0 12px;
  border: 1px solid var(--border);
  background: var(--surface-strong);
  border-radius: 999px;
  font-family: inherit; font-size: 11px; font-weight: 600;
  color: var(--ink-mute);
  cursor: pointer;
  transition: all 0.18s ease;
}
.chip:hover { color: var(--c-blue); border-color: var(--border-glow); }
.chip-active {
  background: var(--aurora); color: #fff;
  border-color: transparent;
  box-shadow: 0 4px 12px -4px rgba(30,64,175,0.45);
}

.cards-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
}
.stat-card {
  position: relative;
  background: var(--surface);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 18px 20px 20px;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255,255,255,0.35),
    var(--shadow-sm);
  overflow: hidden;
  transition: transform 0.25s ease, box-shadow 0.25s ease;
}
.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: inset 0 1px 0 var(--highlight), inset 0 0 0 1px rgba(255,255,255,0.35), var(--shadow-md);
}
.stat-head { display: flex; flex-direction: column; gap: 2px; margin-bottom: 10px; }
.stat-label-en {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px; font-weight: 700; letter-spacing: 0.08em;
  color: var(--ink-mute); text-transform: uppercase;
}
.stat-label-zh { font-size: 13px; font-weight: 600; color: var(--ink-soft); }
.stat-value {
  font-family: 'JetBrains Mono', monospace;
  font-size: 32px; font-weight: 700;
  color: var(--ink); line-height: 1.1;
  letter-spacing: -0.02em;
}
.stat-suffix {
  margin-top: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px; font-weight: 600;
  color: var(--ink-mute);
}
.stat-glow {
  position: absolute;
  inset: -40% -20% auto auto;
  width: 120px; height: 120px;
  border-radius: 50%;
  filter: blur(40px);
  opacity: 0.5;
  pointer-events: none;
}
.stat-blue .stat-glow { background: radial-gradient(circle, var(--c-blue) 0%, transparent 70%); }
.stat-cyan .stat-glow { background: radial-gradient(circle, var(--c-cyan) 0%, transparent 70%); }
.stat-sky  .stat-glow { background: radial-gradient(circle, var(--c-sky) 0%, transparent 70%); }
.stat-violet .stat-glow { background: radial-gradient(circle, var(--c-violet) 0%, transparent 70%); }
.stat-mint .stat-glow { background: radial-gradient(circle, var(--c-mint) 0%, transparent 70%); }
.stat-gold .stat-glow { background: radial-gradient(circle, #f59e0b 0%, transparent 70%); }
.stat-gold .stat-value { color: #b08900; }

/* ── 消费构成卡 ────────────────────────────────────────────────────── */
.cost-card { padding-bottom: 14px; }
.cost-total-tag {
  display: inline-flex; flex-direction: column; align-items: flex-end; gap: 1px;
  padding: 4px 12px;
  border-radius: 10px;
  background: linear-gradient(135deg, rgba(245,158,11,0.12), rgba(176,137,0,0.08));
  border: 1px solid rgba(245,158,11,0.2);
}
.ctt-credits {
  font-family: 'JetBrains Mono', monospace;
  font-size: 16px; font-weight: 700;
  color: #b08900;
  line-height: 1.1;
}
.ctt-yuan {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px; font-weight: 600;
  color: var(--ink-mute);
}
.cost-body { padding: 14px 20px 6px; }
.cost-bar {
  display: flex;
  height: 12px;
  border-radius: 6px;
  overflow: hidden;
  background: rgba(30,64,175,0.06);
  border: 1px solid var(--border);
}
.cost-bar-seg {
  height: 100%;
  transition: width 0.4s ease;
}
.cost-bar-seg + .cost-bar-seg { box-shadow: inset 1px 0 0 rgba(255,255,255,0.4); }
.cost-legend {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px;
}
.cost-legend-item {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--surface-strong);
  font-size: 12px;
}
.cl-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.cl-label { color: var(--ink-soft); font-weight: 600; flex: 1; }
.cl-credits {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px; font-weight: 700;
  color: var(--ink);
}
.cl-pct {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--ink-mute);
  min-width: 42px; text-align: right;
}

/* ── 排行榜消费列 ────────────────────────────────────────────────── */
:deep(.cost-cell) {
  display: flex; flex-direction: column;
  font-family: 'JetBrains Mono', monospace;
}
:deep(.cost-cell-credits) {
  font-size: 13px; font-weight: 700;
  color: #b08900;
  line-height: 1.15;
}
:deep(.cost-cell-yuan) {
  font-size: 10px; color: var(--ink-mute);
}

/* ── 明细流水卡 ──────────────────────────────────────────────── */
.usage-card { padding-bottom: 4px; }
.usage-filters {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
:deep(.usage-time) {
  font-size: 11px;
  color: var(--ink-mute);
}
:deep(.usage-model) {
  font-size: 12px;
  color: var(--ink-soft);
  display: inline-block;
  max-width: 200px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
:deep(.usage-units) {
  font-size: 12px;
  color: var(--ink);
  font-weight: 600;
}
:deep(.dim) { color: var(--ink-faint); }

/* ── 抽屉：消费分项 + 原始量纲 ──────────────────────────────────── */
.detail-item-cost { grid-column: span 2; }
.detail-item-cost .di-value { color: #b08900; font-size: 18px; }
.di-sub {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--ink-mute);
  margin-top: 1px;
}

.detail-cost-section, .detail-units-section { margin-bottom: 18px; }
.dcs-title {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px; font-weight: 700; letter-spacing: 0.06em;
  color: var(--ink-mute); text-transform: uppercase;
  margin: 0 0 8px;
}
.dcs-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 4px; }
.dcs-item {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-strong);
  font-size: 12px;
}
.dcs-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.dcs-label { color: var(--ink-soft); font-weight: 600; min-width: 64px; }
.dcs-credits { color: var(--ink); font-weight: 700; flex: 1; text-align: right; }
.dcs-pct { color: var(--ink-mute); min-width: 50px; text-align: right; }
.dcs-count { color: var(--ink-faint); font-size: 11px; }

.dus-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 6px;
}
.dus-cell {
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: rgba(30,64,175,0.04);
}
.dus-key {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px; font-weight: 600; letter-spacing: 0.04em;
  color: var(--ink-mute); text-transform: uppercase;
}
.dus-val { font-size: 14px; font-weight: 700; color: var(--ink); margin-top: 2px; }

.glass-card {
  background: var(--surface);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border);
  border-radius: 18px;
  box-shadow: inset 0 1px 0 var(--highlight), inset 0 0 0 1px rgba(255,255,255,0.35), var(--shadow-sm);
  overflow: hidden;
}
.card-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
  gap: 12px;
}
.card-title { display: flex; flex-direction: column; gap: 2px; }
.ct-zh { font-size: 14px; font-weight: 700; color: var(--ink); }
.ct-en {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px; font-weight: 600; letter-spacing: 0.04em;
  color: var(--ink-mute); text-transform: uppercase;
}
.trend-chart { height: 320px; padding: 8px 12px 12px; }

.search-wrap {
  display: inline-flex; align-items: center; gap: 6px;
  height: 30px; padding: 0 12px;
  border: 1px solid var(--border);
  background: var(--surface-strong);
  border-radius: 10px;
  color: var(--ink-mute);
  transition: border-color 0.18s ease;
}
.search-wrap:focus-within { border-color: var(--border-glow); color: var(--c-blue); }
.search-input {
  border: 0; background: transparent; outline: none;
  font-family: inherit; font-size: 12px; color: var(--ink);
  width: 200px;
}
.search-input::placeholder { color: var(--ink-faint); }

.users-card { padding-bottom: 4px; }
.glass-table { padding: 6px; }
:deep(.glass-table .n-data-table) {
  background: transparent;
}
:deep(.glass-table .n-data-table-thead .n-data-table-th) {
  background: rgba(30, 64, 175, 0.04) !important;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--ink-mute);
  text-transform: uppercase;
  border-bottom: 1px solid var(--border);
}
:deep(.glass-table .n-data-table-td) {
  background: transparent !important;
  border-bottom-color: var(--border) !important;
  color: var(--ink);
  font-size: 13px;
}
:deep(.glass-table .n-data-table-tr:hover .n-data-table-td) {
  background: rgba(30,64,175,0.04) !important;
}
:deep(.glass-table .num-col) {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 600;
}
:deep(.user-cell-nick) { font-weight: 600; color: var(--ink); }
:deep(.user-cell-name) {
  margin-top: 2px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px; color: var(--ink-mute);
}
:deep(.time-cell) {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px; color: var(--ink-mute);
}
:deep(.glass-table .n-pagination) { padding: 12px 16px 6px; }

.custom-range :deep(.n-input) { border-radius: 10px; }

:deep(.quota-cell) { display: flex; flex-direction: column; gap: 3px; }
:deep(.quota-bar-wrap) {
  height: 4px; border-radius: 2px; background: rgba(30,64,175,0.1); overflow: hidden;
}
:deep(.quota-bar-fill) { height: 100%; border-radius: 2px; transition: width 0.3s ease; }
:deep(.quota-cell-text) { font-size: 11px; }
:deep(.quota-cell-text .mono) { font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: 12px; }
:deep(.quota-cell-text .dim) { color: var(--ink-faint); }

.detail-body { padding: 4px; }
.detail-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
  margin-bottom: 18px;
  padding: 14px;
  background: rgba(30,64,175,0.04);
  border: 1px solid var(--border);
  border-radius: 14px;
}
.detail-item { display: flex; flex-direction: column; gap: 2px; }
.di-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px; font-weight: 700; letter-spacing: 0.06em;
  color: var(--ink-mute); text-transform: uppercase;
}
.di-value { font-size: 14px; font-weight: 600; color: var(--ink); }
.di-value.mono { font-family: 'JetBrains Mono', monospace; }
.mono { font-family: 'JetBrains Mono', monospace; }

.detail-tabs :deep(.n-tabs-nav) { padding: 0 4px; }
.detail-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 4px; }
.detail-list-item {
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 11px;
  background: var(--surface-strong);
  transition: all 0.18s ease;
}
.detail-list-item:hover {
  border-color: var(--border-glow);
  box-shadow: var(--shadow-sm);
}
.dli-title { font-size: 13px; font-weight: 600; color: var(--ink); margin-bottom: 4px; }
.dli-meta {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-size: 12px; color: var(--ink-mute);
}
.dli-meta .mono, .dli-meta b.mono { color: var(--ink); }
.dot-sep { color: var(--ink-faint); }

.empty-tip { text-align: center; color: var(--ink-faint); padding: 32px 0; font-size: 13px; }

.sessions-search-wrap {
  padding: 8px 0 10px;
}
.sessions-search-input {
  width: 100%;
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-strong);
  font-family: inherit;
  font-size: 12px;
  color: var(--ink);
  outline: none;
  transition: border-color 0.18s ease;
}
.sessions-search-input:focus { border-color: var(--border-glow); }
.sessions-search-input::placeholder { color: var(--ink-faint); }
.sessions-pagination {
  padding: 10px 0 4px;
  display: flex;
  justify-content: center;
}

.detail-list-item-clickable {
  cursor: pointer;
}
.detail-list-item-clickable:hover {
  border-color: var(--border-glow);
  background: rgba(30,64,175,0.04);
}
.dli-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.dli-arrow {
  font-size: 18px;
  color: var(--ink-faint);
  line-height: 1;
  flex-shrink: 0;
}

/* ── 消息抽屉 ──────────────────────────────────────────────────── */
.msg-drawer-header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.msg-back-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-strong);
  cursor: pointer;
  color: var(--ink-mute);
  transition: all 0.18s ease;
  flex-shrink: 0;
}
.msg-back-btn:hover { color: var(--c-blue); border-color: var(--border-glow); }
.msg-drawer-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.msg-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 4px 0;
}
.msg-bubble-wrap {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.msg-user { align-items: flex-end; }
.msg-assistant { align-items: flex-start; }

.msg-role-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--ink-mute);
  text-transform: uppercase;
  padding: 0 4px;
}
.msg-bubble {
  max-width: 86%;
  padding: 10px 14px;
  border-radius: 14px;
  border: 1px solid var(--border);
  font-size: 13px;
  line-height: 1.65;
  color: var(--ink);
  position: relative;
}
.msg-user .msg-bubble {
  background: rgba(30,64,175,0.07);
  border-color: rgba(30,64,175,0.18);
  border-bottom-right-radius: 4px;
}
.msg-assistant .msg-bubble {
  background: var(--surface-strong);
  border-bottom-left-radius: 4px;
}
.msg-bubble-error {
  border-color: rgba(239,68,68,0.3) !important;
  background: rgba(239,68,68,0.05) !important;
}
.msg-content {
  white-space: pre-wrap;
  word-break: break-word;
}
.msg-error-text {
  color: #ef4444;
  font-size: 12px;
  margin-top: 4px;
  font-style: italic;
}
.msg-tools {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
}
.msg-tool-call {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(79,70,229,0.08);
  border: 1px solid rgba(79,70,229,0.2);
  font-size: 11px;
  color: #4f46e5;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 600;
}
.tc-icon { font-style: normal; }
.msg-time {
  margin-top: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: var(--ink-faint);
  text-align: right;
}

@media (max-width: 920px) {
  .topbar, .main { padding: 0 16px; }
  .filter-divider { display: none; }
  .stat-value { font-size: 26px; }
}
@media (max-width: 560px) {
  .action-nav-text, .action-text { display: none; }
  .cards-row { grid-template-columns: repeat(2, 1fr); }
  .detail-grid { grid-template-columns: 1fr; }
}
</style>
