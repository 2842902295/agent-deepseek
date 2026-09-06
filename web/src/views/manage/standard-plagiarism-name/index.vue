<script setup lang="tsx">
import {reactive} from 'vue';
import {NButton, NPopconfirm, NTag} from 'naive-ui';
import {
  fetchBatchDeleteStandardPlagiarismName,
  fetchDeleteStandardPlagiarismName,
  fetchGetStandardPlagiarismNameList
} from '@/service/api';
import {useAppStore} from '@/store/modules/app';
import {defaultTransform, useNaivePaginatedTable, useTableOperate} from '@/hooks/common/table';
import StandardPlagiarismNameOperateDrawer from './modules/standard-plagiarism-name-operate-drawer.vue';
import StandardPlagiarismNameSearch from './modules/standard-plagiarism-name-search.vue';

const appStore = useAppStore();

const searchParams = reactive({
  current: 1,
  size: 10,
  standardCode: null,
  standardName: null,
  standardType: null,
  industry: null,
  organization: null,
  releaseYear: null,
  keywords: null,
  standardStatus: null,
  similarityScore: null
});

const {
  columns,
  columnChecks,
  data,
  getData,
  getDataByPage,
  loading,
  mobilePagination
} = useNaivePaginatedTable({
  api: () => fetchGetStandardPlagiarismNameList(searchParams),
  transform: response => defaultTransform(response),
  showTotal: true,
  onPaginationParamsChange: params => {
    searchParams.current = params.page;
    searchParams.size = params.pageSize;
  },
  columns: () => [
    {
      type: 'selection',
      align: 'center',
      width: 48
    },
    {
      key: 'index',
      title: '序号',
      align: 'center',
      width: 64
    },
    {
      key: 'standardCode',
      title: '标准号',
      align: 'center',
      minWidth: 120
    },
    {
      key: 'standardName',
      title: '标准名称',
      align: 'center',
      minWidth: 250,
      ellipsis: {
        tooltip: true
      }
    },
    {
      key: 'standardType',
      title: '标准类型',
      align: 'center',
      width: 120
    },
    {
      key: 'industry',
      title: '行业',
      align: 'center',
      width: 100
    },
    {
      key: 'releaseYear',
      title: '发布年份',
      align: 'center',
      width: 100
    },
    {
      key: 'similarityScore',
      title: '相似度总分',
      align: 'center',
      width: 120,
      render: row => {
        if (row.similarityScore === null || row.similarityScore === undefined) {
          return null;
        }

        const getTagType = (score: number): NaiveUI.ThemeColor => {
          if (score >= 70) return 'error';
          if (score >= 50) return 'warning';
          return 'success';
        };

        return <NTag type={getTagType(row.similarityScore)}>{row.similarityScore}</NTag>;
      }
    },
    {
      key: 'keywords',
      title: '关键词',
      align: 'center',
      minWidth: 200,
      ellipsis: {
        tooltip: true
      }
    },
    {
      key: 'standardStatus',
      title: '标准状态',
      align: 'center',
      width: 100
    },
    {
      key: 'sourceFile',
      title: '来源文件',
      align: 'center',
      minWidth: 200,
      ellipsis: {
        tooltip: true
      }
    },
    {
      key: 'actions',
      title: '操作',
      align: 'center',
      width: 160,
      render: row => (
        <div class="flex-center gap-8px">
          <NButton type="primary" ghost size="small" onClick={() => handleEdit(row.id)}>
            编辑
          </NButton>
          <NPopconfirm onPositiveClick={() => handleDelete(row.id)}>
            {{
              default: () => '确认删除？',
              trigger: () => (
                <NButton type="error" ghost size="small">
                  删除
                </NButton>
              )
            }}
          </NPopconfirm>
        </div>
      )
    }
  ]
});

const {
  drawerVisible,
  operateType,
  editingData,
  handleAdd,
  handleEdit,
  checkedRowKeys,
  onBatchDeleted,
  onDeleted
} = useTableOperate(data, 'id', getData);

function resetSearchParams() {
  Object.assign(searchParams, {
    current: 1,
    size: 10,
    standardCode: null,
    standardName: null,
    standardType: null,
    industry: null,
    organization: null,
    releaseYear: null,
    keywords: null,
    standardStatus: null,
    similarityScore: null
  });
  getDataByPage(1);
}

async function handleBatchDelete() {
  const {error} = await fetchBatchDeleteStandardPlagiarismName({
    ids: checkedRowKeys.value.map(key => String(key))
  });
  if (!error) {
    onBatchDeleted();
  }
}

async function handleDelete(id: number) {
  const {error} = await fetchDeleteStandardPlagiarismName({id});
  if (!error) {
    onDeleted();
  }
}
</script>

<template>
  <div class="min-h-500px flex-col-stretch gap-16px overflow-hidden lt-sm:overflow-auto">
    <StandardPlagiarismNameSearch v-model:model="searchParams" @reset="resetSearchParams" @search="getDataByPage" />
    <NCard :title="'标准查重管理'" :bordered="false" size="small" class="sm:flex-1-hidden card-wrapper">
      <template #header-extra>
        <TableHeaderOperation
          v-model:columns="columnChecks"
          :disabled-delete="checkedRowKeys.length === 0"
          :loading="loading"
          @add="handleAdd"
          @delete="handleBatchDelete"
          @refresh="getData"
        />
      </template>
      <NDataTable
        v-model:checked-row-keys="checkedRowKeys"
        :columns="columns"
        :data="data"
        size="small"
        :flex-height="!appStore.isMobile"
        :scroll-x="962"
        :loading="loading"
        remote
        :row-key="row => row.id"
        :pagination="mobilePagination"
        class="sm:h-full"
      />
      <StandardPlagiarismNameOperateDrawer
        v-model:visible="drawerVisible"
        :operate-type="operateType"
        :row-data="editingData"
        @submitted="getDataByPage"
      />
    </NCard>
  </div>
</template>

<style scoped></style>
