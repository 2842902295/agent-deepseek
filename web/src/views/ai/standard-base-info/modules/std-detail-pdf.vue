<script lang="ts" setup>
import {computed} from 'vue'
import {NEmpty} from 'naive-ui'

const props = defineProps<{
  pdfName: string
}>()

const PDF_BASE_URL = import.meta.env.VITE_STANDARD_PDF_PREFIX || ''

const pdfUrl = computed(() => {
  if (!props.pdfName) return ''
  return `${PDF_BASE_URL}${props.pdfName}`
})
</script>

<template>
  <div class="pdf-viewer">
    <div v-if="!pdfUrl" class="py-10">
      <NEmpty description="暂无PDF文件" />
    </div>
    <iframe
      v-else
      :src="pdfUrl"
      class="pdf-iframe"
      frameborder="0"
      allowfullscreen
    />
  </div>
</template>

<style scoped>
.pdf-viewer {
  width: 100%;
  height: calc(100vh - 180px);
}
.pdf-iframe {
  width: 100%;
  height: 100%;
  border: none;
}
</style>
