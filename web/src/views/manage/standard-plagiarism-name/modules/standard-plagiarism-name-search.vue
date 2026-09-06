<script setup lang="ts">
defineOptions({
  name: 'StandardPlagiarismNameSearch'
});

interface Emits {
  (e: 'reset'): void;
  (e: 'search'): void;
}

const emit = defineEmits<Emits>();

const model = defineModel<Api.SystemManage.StandardPlagiarismNameSearchParams>('model', { required: true });

async function reset() {
  await handleReset();
  emit('reset');
}

async function handleReset() {
  Object.assign(model.value, createDefaultModel());
}

function createDefaultModel(): Api.SystemManage.StandardPlagiarismNameSearchParams {
  return {
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
  };
}

function handleSearch() {
  emit('search');
}
</script>

<template>
  <NCard title="搜索" :bordered="false" size="small" class="card-wrapper">
    <NForm :model="model" label-placement="left" :label-width="80">
      <NGrid responsive="screen" item-responsive>
        <NFormItemGi span="24 s:12 m:6" label="标准号" path="standardCode" class="pr-24px">
          <NInput v-model:value="model.standardCode" placeholder="请输入标准号" />
        </NFormItemGi>
        <NFormItemGi span="24 s:12 m:6" label="标准名称" path="standardName" class="pr-24px">
          <NInput v-model:value="model.standardName" placeholder="请输入标准名称" />
        </NFormItemGi>
        <NFormItemGi span="24 s:12 m:6" label="标准类型" path="standardType" class="pr-24px">
          <NInput v-model:value="model.standardType" placeholder="请输入标准类型" />
        </NFormItemGi>
        <NFormItemGi span="24 s:12 m:6" label="行业" path="industry" class="pr-24px">
          <NInput v-model:value="model.industry" placeholder="请输入行业" />
        </NFormItemGi>
        <NFormItemGi span="24 s:12 m:6" label="归口单位" path="organization" class="pr-24px">
          <NInput v-model:value="model.organization" placeholder="请输入归口单位" />
        </NFormItemGi>
        <NFormItemGi span="24 s:12 m:6" label="发布年份" path="releaseYear" class="pr-24px">
          <NInputNumber v-model:value="model.releaseYear" placeholder="请输入发布年份" clearable class="w-full" />
        </NFormItemGi>
        <NFormItemGi span="24 s:12 m:6" label="关键词" path="keywords" class="pr-24px">
          <NInput v-model:value="model.keywords" placeholder="请输入关键词" />
        </NFormItemGi>
        <NFormItemGi span="24 s:12 m:6" label="相似度总分" path="similarityScore" class="pr-24px">
          <NInputNumber
            v-model:value="model.similarityScore"
            placeholder="最小相似度"
            clearable
            class="w-full"
            :min="0"
            :max="100"
          />
        </NFormItemGi>
        <NFormItemGi span="24 m:12" class="pr-24px">
          <NSpace class="w-full" justify="end">
            <NButton @click="reset">
              <icon-ic-round-refresh class="text-icon" />
              重置
            </NButton>
            <NButton type="primary" ghost @click="handleSearch">
              <icon-ic-round-search class="text-icon" />
              搜索
            </NButton>
          </NSpace>
        </NFormItemGi>
      </NGrid>
    </NForm>
  </NCard>
</template>

<style scoped></style>
