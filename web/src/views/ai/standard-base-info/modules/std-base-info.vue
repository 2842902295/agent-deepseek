<script lang="ts" setup>
import {NTag} from 'naive-ui'

const props = defineProps<{
  data: Record<string, any>
}>()
</script>

<template>
  <div class="w-full gap-6 flex justify-between px-3 py-5 border mb-5">
    <div class="w-full flex flex-col gap-2">
      <div class="font-bold text-16px">
        {{ props.data.cname }}
        <span v-if="props.data.standard_no" class="color-gray-400 text-14px">（{{ props.data.standard_no }}）</span>
      </div>
      <div v-if="props.data.ename" class="color-gray-500 text-13px">
        {{ props.data.ename }}
      </div>
      <div>
        <NTag size="small" type="warning">{{ props.data.std_domain || props.data.std_field || '标准' }}</NTag>
        <NTag v-if="props.data.std_nature" size="small" type="info" class="ml-2">{{ props.data.std_nature }}</NTag>
        <NTag v-if="props.data.state" size="small" :type="props.data.state === '现行' ? 'success' : props.data.state === '废止' ? 'error' : 'warning'" class="ml-2">
          {{ props.data.state }}
        </NTag>
      </div>
      <div class="flex gap-13px text-12px color-gray-500">
        <span v-if="props.data.issue_date">发布于：{{ props.data.issue_date }}</span>
        <span v-if="props.data.act_date">实施于：{{ props.data.act_date }}</span>
      </div>
    </div>
  </div>
</template>
