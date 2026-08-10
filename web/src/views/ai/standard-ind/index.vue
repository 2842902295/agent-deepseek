<script lang="tsx" setup>
import {computed, reactive, ref} from 'vue';
import {NButton, NInput, NModal, NPopconfirm, NSpace, NTag} from 'naive-ui';
import {deleteStandardInd, deleteStandardIndRun, fetchExtractBatchStream, fetchStandardIndList} from '@/service/api';
import {useAppStore} from '@/store/modules/app';
import {defaultTransform, useNaivePaginatedTable} from '@/hooks/common/table';
import IndDetailDrawer from './modules/ind-detail-drawer.vue';

const appStore = useAppStore();

const searchParams = reactive({
  current: 1,
  size: 10,
  standard_no: undefined as string | undefined,
  standard_name: undefined as string | undefined,
});

const drawerShow = ref(false);
const selectedStandardNo = ref('');
const selectedStandardName = ref('');
const selectedRunId = ref('');
const checkedRowKeys = ref<string[]>([]);
const deletingKeys = ref<Set<string>>(new Set());

function openDetail(standardNo: string, standardName: string, runId: string) {
  selectedStandardNo.value = standardNo;
  selectedStandardName.value = standardName;
  selectedRunId.value = runId;
  drawerShow.value = true;
}

const CAT_COLOR: Record<string, string> = {
  规格: '#1e40af',
  性能: '#16a34a',
  检测: '#7c3aed',
  材料: '#c2410c',
  外观: '#0d9488',
  安全: '#be123c',
  配套: '#4338ca',
  包装运输: '#4b5563',
};

const CAT_BG: Record<string, string> = {
  规格: '#eff6ff',
  性能: '#f0fdf4',
  检测: '#f5f3ff',
  材料: '#fff7ed',
  外观: '#f0fdfa',
  安全: '#fff1f2',
  配套: '#eef2ff',
  包装运输: '#f9fafb',
};

const {columns, data, loading, getData, getDataByPage, mobilePagination} = useNaivePaginatedTable({
  api: () => fetchStandardIndList(searchParams),
  transform: response => {
    const d = (response as any).data;
    if (d && Array.isArray(d.list)) {
      return {data: d.list, pageNum: d.current ?? 1, pageSize: d.size ?? 10, total: d.total ?? 0};
    }
    return defaultTransform(response);
  },
  onPaginationParamsChange: params => {
    searchParams.current = params.page;
    searchParams.size = params.pageSize;
  },
  columns: () => [
    {
      type: 'selection' as const,
      key: 'selection',
    },
    {
      key: 'index',
      title: '序号',
      width: 64,
      align: 'center' as const,
      render: (_row: Api.AI.StandardIndRecord, rowIndex: number) =>
        (searchParams.current - 1) * searchParams.size + rowIndex + 1,
    },
    {
      key: 'standard_no',
      title: '标准编号',
      width: 130,
      render: (row: Api.AI.StandardIndRecord) => (
        <span
          style="font-weight:600;color:#1e40af;font-family:'Fira Code',Consolas,monospace;font-size:13px">{row.standard_no}</span>
      ),
    },
    {
      key: 'standard_name',
      title: '标准名称',
      minWidth: 260,
      ellipsis: {tooltip: true},
      render: (row: Api.AI.StandardIndRecord) => row.standard_name || '—',
    },
    {
      key: 'total_count',
      title: '指标总数',
      width: 90,
      align: 'center' as const,
      render: (row: Api.AI.StandardIndRecord) => (
        <span
          style="font-family:'Fira Code',Consolas,monospace;font-size:15px;font-weight:600;color:#374151">{row.total_count}</span>
      ),
    },
    {
      key: 'norm_class',
      title: '规范类别',
      width: 220,
      align: 'center' as const,
      render: (row: Api.AI.StandardIndRecord) => {
        const entries = Object.entries(row.norm_class_counts ?? {});
        if (!entries.length) return <span style="color:#9ca3af;font-size:12px">—</span>;
        return (
          <NSpace size={4} justify="center" wrap>
            {entries.map(([nc, cnt]) => (
              <NTag key={nc} size="small" style="background:#f3f4f6;color:#374151;border:1px solid #d1d5db">
                {nc} {cnt}
              </NTag>
            ))}
          </NSpace>
        );
      },
    },
    {
      key: 'create_time',
      title: '提取时间 / 备注',
      width: 200,
      align: 'center' as const,
      render: (row: Api.AI.StandardIndRecord) => {
        const t = row.create_time;
        const remark = row.run_remark;
        const timeStr = (() => {
          if (!t) return null;
          const d = new Date(t);
          const pad = (n: number) => String(n).padStart(2, '0');
          return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
        })();
        return (
          <div style="display:flex;flex-direction:column;gap:2px;align-items:center">
            <div style="display:flex;align-items:center;gap:4px">
              {timeStr
                ? <span style="font-size:12px;color:#6b7280">{timeStr}</span>
                : <span style="color:#9ca3af">—</span>}
              {row.is_valid
                ? <span
                  style="font-size:10px;padding:1px 5px;border-radius:3px;background:#f0fdf4;color:#16a34a;border:1px solid #86efac">最新</span>
                : <span
                  style="font-size:10px;padding:1px 5px;border-radius:3px;background:#f9fafb;color:#9ca3af;border:1px solid #e5e7eb">历史</span>}
            </div>
            {remark && (
              <span
                style="font-size:11px;color:#9ca3af;background:#f3f4f6;border-radius:3px;padding:1px 6px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                title={remark}>
                {remark}
              </span>
            )}
          </div>
        );
      },
    },
    {
      key: 'actions',
      title: '操作',
      width: 160,
      align: 'center' as const,
      render: (row: Api.AI.StandardIndRecord) => (
        <NSpace size={6} justify="center">
          <NButton size="small" type="primary" ghost
                   onClick={() => openDetail(row.standard_no, row.standard_name, row.run_id)}>
            查看指标
          </NButton>
          <NPopconfirm onPositiveClick={() => handleDeleteRun(row.run_id, row.standard_no)}>
            {{
              default: () => `确定删除「${row.standard_no}」此次提取记录？`,
              trigger: () => (
                <NButton size="small" type="error" ghost
                         loading={deletingKeys.value.has(row.run_id || row.standard_no)}>
                  删除
                </NButton>
              ),
            }}
          </NPopconfirm>
        </NSpace>
      ),
    },
  ],
});

async function handleDeleteRun(runId: string, standardNo: string) {
  const key = runId || standardNo;
  deletingKeys.value = new Set([...deletingKeys.value, key]);
  try {
    if (runId) {
      await deleteStandardIndRun(runId);
    } else {
      await deleteStandardInd([standardNo]);
    }
    getData();
  } finally {
    deletingKeys.value.delete(key);
    deletingKeys.value = new Set(deletingKeys.value);
  }
}

async function handleDelete(runIds: string[]) {
  runIds.forEach(k => deletingKeys.value.add(k));
  deletingKeys.value = new Set(deletingKeys.value);
  try {
    await Promise.all(runIds.map(id => deleteStandardIndRun(id)));
    checkedRowKeys.value = [];
    getData();
  } finally {
    runIds.forEach(k => deletingKeys.value.delete(k));
    deletingKeys.value = new Set(deletingKeys.value);
  }
}

const hasChecked = computed(() => checkedRowKeys.value.length > 0);

function handleSearch() {
  getDataByPage(1);
}

function handleReset() {
  searchParams.standard_no = undefined;
  searchParams.standard_name = undefined;
  getDataByPage(1);
}

// ── 指标拆解 ──────────────────────────────────────────────────────────────

const extractModalShow = ref(false);
const extractInput = ref('');
const extractRemark = ref('');
const extractRunning = ref(false);
const extractAbortCtrl = ref<AbortController | null>(null);

type ItemStatus = 'pending' | 'processing' | 'done' | 'error';

interface ExtractItem {
  standard_no: string;
  status: ItemStatus;
  count: number;
  message: string;
}

const extractItems = ref<ExtractItem[]>([]);
const extractSummary = ref<{ success: number; failed: number } | null>(null);

function openExtractModal() {
  extractModalShow.value = true;
  extractItems.value = [];
  extractSummary.value = null;
  extractRemark.value = '';
}

function stopExtract() {
  extractAbortCtrl.value?.abort();
  extractAbortCtrl.value = null;
  extractRunning.value = false;
}

async function startExtract() {
  const nos = extractInput.value
    .split('\n')
    .map(s => s.trim())
    .filter(Boolean);
  if (!nos.length) return;

  extractItems.value = nos.map(sno => ({standard_no: sno, status: 'pending', count: 0, message: ''}));
  extractSummary.value = null;
  extractRunning.value = true;

  const ctrl = new AbortController();
  extractAbortCtrl.value = ctrl;

  try {
    await fetchExtractBatchStream(
      nos,
      event => {
        if (event.type === 'processing') {
          const item = extractItems.value.find(i => i.standard_no === event.standard_no);
          if (item) item.status = 'processing';
        } else if (event.type === 'progress') {
          const item = extractItems.value.find(i => i.standard_no === event.standard_no);
          if (item) {
            item.status = 'done';
            item.count = event.count;
          }
        } else if (event.type === 'error') {
          const item = extractItems.value.find(i => i.standard_no === event.standard_no);
          if (item) {
            item.status = 'error';
            item.message = event.message;
          }
        } else if (event.type === 'done') {
          extractSummary.value = {success: event.success, failed: event.failed};
          extractRunning.value = false;
          getData();
        }
      },
      ctrl.signal,
      extractRemark.value.trim() || undefined
    );
  } catch (e: any) {
    if (e?.name !== 'AbortError') {
      extractSummary.value = {success: 0, failed: nos.length};
    }
    extractRunning.value = false;
  }
}
</script>

<template>
  <div class="min-h-620px flex-col-stretch gap-16px overflow-hidden lt-sm:overflow-auto">
    <NCard :bordered="false" class="sm:flex-1-hidden card-wrapper" size="small" title="标准指标缓存">
      <template #header-extra>
        <NSpace>
          <NInput
            v-model:value="searchParams.standard_no"
            :style="{ width: '180px' }"
            clearable
            placeholder="标准编号"
            @keyup.enter="handleSearch"
          />
          <NInput
            v-model:value="searchParams.standard_name"
            :style="{ width: '220px' }"
            clearable
            placeholder="标准名称"
            @keyup.enter="handleSearch"
          />
          <NButton type="primary" @click="handleSearch">搜索</NButton>
          <NButton @click="handleReset">重置</NButton>
          <NPopconfirm v-if="hasChecked" @positive-click="handleDelete(checkedRowKeys)">
            <template #trigger>
              <NButton :loading="deletingKeys.size > 0" type="error">
                删除所选 ({{ checkedRowKeys.length }})
              </NButton>
            </template>
            确定删除所选 {{ checkedRowKeys.length }} 条提取记录？
          </NPopconfirm>
          <NButton type="warning" @click="openExtractModal">指标拆解</NButton>
        </NSpace>
      </template>
      <NDataTable
        v-model:checked-row-keys="checkedRowKeys"
        :columns="columns"
        :data="data"
        :flex-height="!appStore.isMobile"
        :loading="loading"
        :pagination="mobilePagination"
        :row-key="(row: Api.AI.StandardIndRecord) => row.run_id || row.standard_no"
        :scroll-x="1000"
        class="sm:h-full"
        remote
        size="small"
      />
    </NCard>

    <IndDetailDrawer
      v-model:show="drawerShow"
      :standard-name="selectedStandardName"
      :standard-no="selectedStandardNo"
      :run-id="selectedRunId"
    />

    <!-- 指标拆解弹窗 -->
    <NModal
      v-model:show="extractModalShow"
      :mask-closable="!extractRunning"
      preset="card"
      style="width: 560px"
      title="指标拆解"
    >
      <div class="extract-body">
        <div class="extract-hint">输入标准编号，每行一个：</div>
        <NInput
          v-model:value="extractInput"
          :disabled="extractRunning"
          :rows="6"
          placeholder="每行输入一个标准编号"
          type="textarea"
        />
        <NInput
          v-model:value="extractRemark"
          :disabled="extractRunning"
          clearable
          placeholder="备注（可选）：如模型名称、实验说明等"
        />

        <NSpace class="extract-actions" justify="end">
          <NButton v-if="extractRunning" type="error" @click="stopExtract">终止</NButton>
          <NButton :disabled="extractRunning || !extractInput.trim()" type="primary" @click="startExtract">
            {{ extractRunning ? '拆解中…' : '开始拆解' }}
          </NButton>
        </NSpace>

        <!-- 进度列表 -->
        <div v-if="extractItems.length" class="extract-list">
          <div v-for="item in extractItems" :key="item.standard_no" class="extract-row">
            <span class="extract-no">{{ item.standard_no }}</span>
            <span v-if="item.status === 'pending'" class="extract-tag extract-tag--pending">等待中</span>
            <span v-else-if="item.status === 'processing'" class="extract-tag extract-tag--processing">处理中…</span>
            <span v-else-if="item.status === 'done'" class="extract-tag extract-tag--done">
              完成 · {{ item.count }} 条
            </span>
            <span v-else :title="item.message" class="extract-tag extract-tag--error">失败</span>
          </div>
        </div>

        <!-- 汇总 -->
        <div v-if="extractSummary" class="extract-summary">
          全部完成：成功 <b>{{ extractSummary.success }}</b> 个，失败 <b>{{ extractSummary.failed }}</b> 个
        </div>
      </div>
    </NModal>
  </div>
</template>

<style scoped>
.extract-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.extract-hint {
  font-size: 13px;
  color: #6b7280;
}

.extract-actions {
  margin-top: 4px;
}

.extract-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 280px;
  overflow-y: auto;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 8px 12px;
}

.extract-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 13px;
}

.extract-no {
  font-family: 'Fira Code', Consolas, monospace;
  font-size: 12px;
  color: #1e40af;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.extract-tag {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  white-space: nowrap;
  flex-shrink: 0;
}

.extract-tag--pending {
  background: #f3f4f6;
  color: #9ca3af;
}

.extract-tag--processing {
  background: #eff6ff;
  color: #3b82f6;
}

.extract-tag--done {
  background: #f0fdf4;
  color: #16a34a;
}

.extract-tag--error {
  background: #fff1f2;
  color: #be123c;
  cursor: help;
}

.extract-summary {
  font-size: 13px;
  color: #374151;
  padding: 8px 12px;
  background: #f8fafc;
  border-radius: 6px;
  border: 1px solid #e5e7eb;
}
</style>
