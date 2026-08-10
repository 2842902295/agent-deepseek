<script lang="ts" setup>
import {ref, watch, computed} from 'vue'
import {NDrawer, NDrawerContent, NTabs, NTabPane} from 'naive-ui'
import StdDetail from './std-detail.vue'
import StdDetailHtml from './std-detail-html.vue'
import StdDetailPdf from './std-detail-pdf.vue'

const props = withDefaults(
  defineProps<{
    show: boolean
    standardId: string
    initialTab?: 'detail' | 'html' | 'pdf'
    chapterNo?: string
  }>(),
  {
    initialTab: 'detail',
    chapterNo: ''
  }
)

const emit = defineEmits<{
  (e: 'update:show', val: boolean): void
}>()

const activeTab = ref('detail')
// 打开时若指定了非 detail 的 initialTab，但 HTML/PDF tab 需等 mainTaskId 才能启用，先暂存于此，待 hasPdf 后切换
const pendingTab = ref<'detail' | 'html' | 'pdf' | null>(null)
const mainTaskId = ref<string | null>(null)
const pdfName = ref('')
const hasPdf = computed(() => mainTaskId.value !== null)

function handleHasPdf(info: { mainTaskId: string; pdfName: string }) {
  mainTaskId.value = info.mainTaskId
  pdfName.value = info.pdfName
  // 有 HTML 正文：tab 已可启用，若打开时指定了 initialTab（如直接进 html/pdf），此时切过去
  if (pendingTab.value) {
    activeTab.value = pendingTab.value
    pendingTab.value = null
  }
}

function handleLoaded(info: { hasPdf: boolean }) {
  // 加载结束仍无 HTML 正文，但本次想直接进 html/pdf → 降级：提示并停留基础信息
  if (pendingTab.value && !info.hasPdf) {
    pendingTab.value = null
    window.$message?.info?.('该标准暂无 HTML 正文，无法定位章节')
  }
}

watch(() => props.show, (val) => {
  if (val) {
    // 打开：先停在基础信息以确保 StdDetail 加载并解析 mainTaskId；
    // 若指定了非 detail 的 initialTab，记入 pendingTab，等 hasPdf 后再切
    activeTab.value = 'detail'
    pendingTab.value = props.initialTab && props.initialTab !== 'detail' ? props.initialTab : null
  } else {
    // 关闭时重置状态
    activeTab.value = 'detail'
    mainTaskId.value = null
    pdfName.value = ''
    pendingTab.value = null
  }
})
</script>

<template>
  <NDrawer :show="show" :width="900" placement="right" @update:show="emit('update:show', $event)">
    <NDrawerContent :title="'标准详情'" closable>
      <NTabs v-model:value="activeTab" type="line" animated>
        <NTabPane name="detail" tab="基础信息">
          <StdDetail :standard-id="standardId" @has-pdf="handleHasPdf" @loaded="handleLoaded" />
        </NTabPane>
        <NTabPane name="html" tab="HTML正文" :disabled="!hasPdf">
          <StdDetailHtml v-if="hasPdf" :standard-id="standardId" :main-task-id="mainTaskId!" :chapter-no="chapterNo" />
        </NTabPane>
        <NTabPane name="pdf" tab="PDF浏览" :disabled="!hasPdf">
          <StdDetailPdf v-if="hasPdf" :pdf-name="pdfName" />
        </NTabPane>
      </NTabs>
    </NDrawerContent>
  </NDrawer>
</template>
