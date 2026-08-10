<script lang="ts" setup>
import {ref, computed} from 'vue'
import {NTag, NGrid, NGi, NSteps, NStep, NSpin, NSpace, NEmpty, NButton} from 'naive-ui'
import {fetchStandardBaseInfoDetail, fetchJghPdfByStandardNo} from '@/service/api'

const props = defineProps<{
  standardId: string
}>()

const emit = defineEmits<{
  (e: 'hasPdf', pdfInfo: { mainTaskId: string; pdfName: string }): void
  (e: 'loaded', info: { hasPdf: boolean }): void
}>()

const loading = ref(true)
const data = ref<Record<string, any>>({})
const mainTaskId = ref<string | null>(null)

// 合并起草单位
const combinedDraftUnit = computed(() => {
  const main = data.value.draft_unit_main || ''
  const unit = data.value.draft_unit || ''
  if (main && unit) return `${main}、${unit}`
  return main || unit || ''
})

// 起草人列表
const draftStaffList = computed(() => {
  const staff = data.value.draft_staff
  if (!staff) return []
  return staff.split(/[;；,，、]/).filter(Boolean)
})

// 标准状态步骤
const currentStateIndex = computed(() => {
  const state = data.value.state
  if (state === '在研') return 1
  if (state === '现行') return 2
  if (state === '拟制') return 3
  return 2
})

async function init() {
  loading.value = true
  let emittedPdf = false
  try {
    const {data: detail} = await fetchStandardBaseInfoDetail(props.standardId)
    if (detail) {
      data.value = detail
    }
    // 尝试查询 JGH PDF
    if (data.value.standard_no) {
      try {
        const {data: pdfInfo} = await fetchJghPdfByStandardNo(data.value.standard_no)
        if (pdfInfo?.main_task_id) {
          mainTaskId.value = pdfInfo.main_task_id
          emit('hasPdf', { mainTaskId: String(pdfInfo.main_task_id), pdfName: pdfInfo.name || '' })
          emittedPdf = true
        }
      } catch {
        // 无 PDF 数据不影响详情展示
      }
    }
  } catch (error) {
    console.error('加载详情失败:', error)
  } finally {
    loading.value = false
    // 无论是否有 PDF，都通知父组件加载已结束，便于「直接进 HTML 正文」等场景做降级
    emit('loaded', { hasPdf: emittedPdf })
  }
}

init()
</script>

<template>
  <NSpin :show="loading">
    <div class="p-4">
      <!-- 标题区 -->
      <div class="font-bold text-20px pb-12px">
        {{ data.cname }}
        <span v-if="data.standard_no" class="color-gray-400 text-14px">({{ data.standard_no }})</span>
      </div>
      <div v-if="data.ename" class="color-gray-500 pb-8px text-13px">
        {{ data.ename }}
      </div>
      <NSpace :size="6" class="pb-16px">
        <NTag v-if="data.std_domain" :bordered="false" size="small" type="info">{{ data.std_domain }}</NTag>
        <NTag v-if="data.std_nature" :bordered="false" size="small" type="warning">{{ data.std_nature }}</NTag>
        <NTag :bordered="false" size="small" :type="data.state === '现行' ? 'success' : data.state === '废止' ? 'error' : 'info'">
          {{ data.state || '现行' }}
        </NTag>
        <NTag v-if="data.security_level" :bordered="false" size="small" type="info">{{ data.security_level }}</NTag>
      </NSpace>

      <!-- 基础信息 -->
      <div class="mb-16px">
        <div class="font-bold text-15px mb-8px border-l-3px border-l-blue-500 pl-8px">基础信息</div>
        <NGrid :cols="2" :x-gap="12" :y-gap="8" responsive="screen" item-responsive>
          <NGi>
            <div class="text-12px"><span class="color-gray-400">适用范围：</span>{{ data.use_range || '-' }}</div>
          </NGi>
          <NGi>
            <div class="text-12px"><span class="color-gray-400">批准单位：</span>{{ data.approval_unit || '-' }}</div>
          </NGi>
          <NGi>
            <div class="text-12px"><span class="color-gray-400">归口单位：</span>{{ data.lead_unit || '-' }}</div>
          </NGi>
          <NGi>
            <div class="text-12px"><span class="color-gray-400">提出单位：</span>{{ data.put_unit || '-' }}</div>
          </NGi>
          <NGi>
            <div class="text-12px"><span class="color-gray-400">发布日期：</span>{{ data.issue_date || '-' }}</div>
          </NGi>
          <NGi>
            <div class="text-12px"><span class="color-gray-400">实施日期：</span>{{ data.act_date || '-' }}</div>
          </NGi>
          <NGi>
            <div class="text-12px"><span class="color-gray-400">标准类型：</span>{{ data.std_type || '-' }}</div>
          </NGi>
          <NGi>
            <div class="text-12px"><span class="color-gray-400">行业：</span>{{ data.industry || '-' }}</div>
          </NGi>
        </NGrid>
      </div>

      <!-- 标准状态 -->
      <div class="mb-16px">
        <div class="font-bold text-15px mb-8px border-l-3px border-l-blue-500 pl-8px">标准状态</div>
        <NSteps :current="currentStateIndex" status="process">
          <NStep title="在研" :description="data.issue_date" />
          <NStep title="现行" :description="data.act_date" />
          <NStep title="拟制" />
        </NSteps>
      </div>

      <!-- 起草单位 -->
      <div v-if="combinedDraftUnit" class="mb-16px">
        <div class="font-bold text-15px mb-8px border-l-3px border-l-blue-500 pl-8px">起草单位</div>
        <div class="flex flex-wrap gap-6px">
          <div
            v-for="item in combinedDraftUnit.split(/[;；,，、]/).filter(Boolean)"
            :key="item"
            class="px-8px py-4px text-12px rounded bg-blue-50 color-blue-600"
          >
            {{ item }}
          </div>
        </div>
      </div>

      <!-- 起草人 -->
      <div v-if="draftStaffList.length" class="mb-16px">
        <div class="font-bold text-15px mb-8px border-l-3px border-l-blue-500 pl-8px">起草人</div>
        <div class="flex flex-wrap gap-6px">
          <div
            v-for="item in draftStaffList"
            :key="item"
            class="px-8px py-4px text-12px rounded bg-blue-50 color-blue-600"
          >
            {{ item }}
          </div>
        </div>
      </div>

      <!-- 其他信息 -->
      <div v-if="data.replace_description || data.replace_stds || data.remark" class="mb-16px">
        <div class="font-bold text-15px mb-8px border-l-3px border-l-blue-500 pl-8px">其他信息</div>
        <NGrid :cols="1" :y-gap="4">
          <NGi v-if="data.replace_description">
            <div class="text-12px"><span class="color-gray-400">替代说明：</span>{{ data.replace_description }}</div>
          </NGi>
          <NGi v-if="data.replace_stds">
            <div class="text-12px"><span class="color-gray-400">替代标准：</span>{{ data.replace_stds }}</div>
          </NGi>
          <NGi v-if="data.remark">
            <div class="text-12px"><span class="color-gray-400">备注：</span>{{ data.remark }}</div>
          </NGi>
        </NGrid>
      </div>
    </div>
  </NSpin>
</template>
