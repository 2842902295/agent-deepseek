<script setup lang="tsx">
import {reactive, ref} from 'vue';
import {NButton, NGrid, NGridItem, NInput, NSpace, NStatistic, NTag} from 'naive-ui';
import {batchDeleteStandardBaseInfo, fetchStandardBaseInfoList, fetchStandardBaseInfoStats} from '@/service/api';
import {useAppStore} from '@/store/modules/app';
import {defaultTransform, useNaivePaginatedTable} from '@/hooks/common/table';
import DataMigrationDrawer from './modules/data-migration-drawer.vue';
import StructuredDataMigrationDrawer from './modules/structured-data-migration-drawer.vue';
import TableFormulaMigrationDrawer from './modules/table-formula-migration-drawer.vue';
import SpecificDataMigrationDrawer from './modules/specific-data-migration-drawer.vue';
import StdDetailDrawer from './modules/std-detail-drawer.vue';

const appStore = useAppStore();

const stats = ref<Api.AI.StandardBaseInfoStatsResponse>({
  total: 0,
  has_standard_no: 0,
  has_use_range: 0,
  no_standard_no: 0,
  no_use_range: 0
});

const checkedRowKeys = ref<string[]>([]);

const searchParams = reactive({
  current: 1,
  size: 10,
  cname: undefined as string | undefined,
  standard_no: undefined as string | undefined
});

const {
  columns,
  columnChecks,
  data,
  loading,
  getData,
  getDataByPage,
  mobilePagination
} = useNaivePaginatedTable({
  api: () => fetchStandardBaseInfoList(searchParams),
  transform: response => defaultTransform(response),
  onPaginationParamsChange: params => {
    searchParams.current = params.page;
    searchParams.size = params.pageSize;
  },
  columns: () => [
    {
      type: 'selection',
      fixed: 'left'
    },
    {
      key: 'index',
      title: '序号',
      width: 64,
      align: 'center',
      render: (_row: any, rowIndex: number) =>
        (searchParams.current - 1) * searchParams.size + rowIndex + 1,
    },
    {
      key: 'standard_no',
      title: '标准编号',
      width: 180,
      fixed: 'left',
      render: row => {
        if (!row.standard_no) {
          return <NTag type="warning" size="small">无编号</NTag>;
        }
        return row.standard_no;
      }
    },
    {
      key: 'cname',
      title: '标准名称',
      minWidth: 300,
      fixed: 'left',
      ellipsis: {
        tooltip: true
      },
      render: row => {
        return row.cname || '-';
      }
    },
    {
      key: 'std_domain',
      title: '标准类型',
      width: 120,
      render: row => {
        return row.std_domain || '-';
      }
    },
    {
      key: 'std_field',
      title: '所属领域',
      width: 150,
      ellipsis: {
        tooltip: true
      },
      render: row => {
        return row.std_field || '-';
      }
    },
    {
      key: 'state',
      title: '状态',
      width: 100,
      render: row => {
        if (!row.state) return '-';
        const stateMap: Record<string, { type: 'success' | 'error' | 'warning' | 'info'; text: string }> = {
          现行: {type: 'success', text: '现行'},
          废止: {type: 'error', text: '废止'},
          即将实施: {type: 'warning', text: '即将实施'}
        };
        const state = stateMap[row.state] || { type: 'info', text: row.state };
        return <NTag type={state.type}>{state.text}</NTag>;
      }
    },
    {
      key: 'issue_date',
      title: '发布日期',
      width: 120,
      render: row => {
        if (!row.issue_date) return '-';
        return row.issue_date;
      }
    },
    {
      key: 'act_date',
      title: '实施日期',
      width: 120,
      render: row => {
        if (!row.act_date) return '-';
        return row.act_date;
      }
    },
    {
      key: 'approval_unit',
      title: '发布单位',
      width: 200,
      ellipsis: {
        tooltip: true
      },
      render: row => {
        return row.approval_unit || '-';
      }
    },
    {
      key: 'lead_unit',
      title: '归口单位',
      width: 200,
      ellipsis: {
        tooltip: true
      },
      render: row => {
        return row.lead_unit || '-';
      }
    },
    {
      key: 'use_range',
      title: '适用范围',
      minWidth: 300,
      ellipsis: {
        tooltip: true
      },
      render: row => {
        if (!row.use_range) {
          return <NTag type="info" size="small">无适用范围</NTag>;
        }
        return row.use_range;
      }
    },
    {
      key: 'security_level',
      title: '密级',
      width: 100,
      render: row => {
        const level = row.security_level || '公开';
        const typeMap: Record<string, 'success' | 'warning' | 'error'> = {
          公开: 'success',
          内部: 'warning',
          秘密: 'error'
        };
        return <NTag type={typeMap[level] || 'info'}>{level}</NTag>;
      }
    },
    {
      key: 'create_time',
      title: '创建时间',
      width: 180,
      render: row => {
        if (!row.create_time) return '-';
        return new Date(row.create_time).toLocaleString('zh-CN');
      }
    },
    {
      key: 'actions',
      title: '操作',
      width: 100,
      fixed: 'right',
      render: (row: any) => (
        <NButton type="primary" size="small" text onClick={() => handleViewDetail(row)}>
          查看详情
        </NButton>
      )
    }
  ]
});

async function loadStats() {
  try {
    const { data: statsData } = await fetchStandardBaseInfoStats();
    if (statsData) {
      stats.value = statsData;
    }
  } catch (error) {
    console.error('加载统计数据失败:', error);
  }
}

const showDataMigration = ref(false);
const showStructuredDataMigration = ref(false);
const showTableFormulaMigration = ref(false);
const showSpecificDataMigration = ref(false);

// 标准详情抽屉
const showStdDetail = ref(false);
const selectedStdId = ref('');

function handleViewDetail(row: any) {
  selectedStdId.value = row.id;
  showStdDetail.value = true;
}

function handleDataMigration() {
  showDataMigration.value = true;
}

function handleStructuredDataMigration() {
  showStructuredDataMigration.value = true;
}

function handleTableFormulaMigration() {
  showTableFormulaMigration.value = true;
}

function handleSpecificDataMigration() {
  showSpecificDataMigration.value = true;
}

function handleMigrationSuccess() {
  loadStats();
  getDataByPage(1);
}

function handleSearch() {
  getDataByPage(1);
}

function handleReset() {
  searchParams.cname = undefined;
  searchParams.standard_no = undefined;
  getDataByPage(1);
}

async function handleBatchDelete() {
  if (checkedRowKeys.value.length === 0) {
    window.$message?.warning('请选择要删除的数据');
    return;
  }

  try {
    await batchDeleteStandardBaseInfo(checkedRowKeys.value);
    window.$message?.success('删除成功');
    checkedRowKeys.value = [];
    loadStats();
    getDataByPage(1);
  } catch (error) {
    window.$message?.error('删除失败');
    console.error('批量删除失败:', error);
  }
}

loadStats();
</script>

<template>
  <div class="min-h-620px flex-col-stretch gap-16px overflow-hidden lt-sm:overflow-auto">
    <!-- 统计卡片 -->
    <NCard title="数据统计" :bordered="false" size="small" class="card-wrapper">
      <NGrid :cols="5" :x-gap="16">
        <NGridItem>
          <NStatistic label="总数" :value="stats.total" />
        </NGridItem>
        <NGridItem>
          <NStatistic label="有标准编号" :value="stats.has_standard_no" />
        </NGridItem>
        <NGridItem>
          <NStatistic label="无标准编号" :value="stats.no_standard_no" />
        </NGridItem>
        <NGridItem>
          <NStatistic label="有适用范围" :value="stats.has_use_range" />
        </NGridItem>
        <NGridItem>
          <NStatistic label="无适用范围" :value="stats.no_use_range" />
        </NGridItem>
      </NGrid>
    </NCard>

    <!-- 数据表格 -->
    <NCard title="标准基础信息" :bordered="false" size="small" class="sm:flex-1-hidden card-wrapper">
      <template #header-extra>
        <NSpace>
          <NInput
            v-model:value="searchParams.standard_no"
            placeholder="标准编号"
            clearable
            :style="{ width: '180px' }"
            @keyup.enter="handleSearch"
          />
          <NInput
            v-model:value="searchParams.cname"
            placeholder="标准名称"
            clearable
            :style="{ width: '200px' }"
            @keyup.enter="handleSearch"
          />
          <NButton type="primary" @click="handleSearch">搜索</NButton>
          <NButton @click="handleReset">重置</NButton>
          <NButton type="warning" @click="handleDataMigration">迁入数据</NButton>
          <NButton type="info" @click="handleStructuredDataMigration">结构化数据迁入</NButton>
          <!-- <NButton type="success" @click="handleTableFormulaMigration">表格公式数据迁入</NButton> -->
          <NButton type="primary" @click="handleSpecificDataMigration">迁入指定数据</NButton>
          <TableHeaderOperation
            v-model:columns="columnChecks"
            :loading="loading"
            table-id="standard-base-info"
            :show-add="false"
            :show-delete="false"
            @refresh="getData"
            @delete="handleBatchDelete"
          />
        </NSpace>
      </template>
      <NDataTable
        v-model:checked-row-keys="checkedRowKeys"
        :columns="columns"
        :data="data"
        size="small"
        :flex-height="!appStore.isMobile"
        :scroll-x="2500"
        :loading="loading"
        remote
        :row-key="row => row.id"
        :pagination="mobilePagination"
        class="sm:h-full"
      />
    </NCard>

    <!-- 数据迁移抽屉 -->
    <DataMigrationDrawer v-model:show="showDataMigration" @success="handleMigrationSuccess" />

    <!-- 结构化数据迁移抽屉 -->
    <StructuredDataMigrationDrawer v-model:show="showStructuredDataMigration" @success="handleMigrationSuccess" />

    <!-- 表格公式数据迁移抽屉 -->
    <TableFormulaMigrationDrawer v-model:show="showTableFormulaMigration" @success="handleMigrationSuccess" />

    <!-- 指定标准数据迁移抽屉 -->
    <SpecificDataMigrationDrawer v-model:show="showSpecificDataMigration" @success="handleMigrationSuccess" />

    <!-- 标准详情抽屉 -->
    <StdDetailDrawer v-model:show="showStdDetail" :standard-id="selectedStdId" />
  </div>
</template>

<style scoped>
.card-wrapper {
  @apply rounded-8px shadow-sm;
}
</style>
