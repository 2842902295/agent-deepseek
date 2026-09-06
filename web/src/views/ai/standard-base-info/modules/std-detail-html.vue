<script lang="ts" setup>
import {ref, nextTick, watch} from 'vue'
import {NSpin, NEmpty, NPopover, NImage, NImageGroup} from 'naive-ui'
import {DynamicScroller, DynamicScrollerItem, RecycleScroller} from 'vue-virtual-scroller'
import 'vue-virtual-scroller/index.css'
import {fetchJghPdfChapters, fetchStandardBaseInfoDetail} from '@/service/api'

const props = defineProps<{
  standardId: string
  mainTaskId: string
  chapterNo?: string
}>()

const loading = ref(true)
const chapters = ref<Array<{
  id: string
  title: string
  title_no: string
  page: number
  word: string
  level: number
  fullTitle: string
}>>([])
const stdInfo = ref<Record<string, any>>({})
const activeId = ref<string>('')

const sidebarScrollerRef = ref<InstanceType<typeof RecycleScroller> | null>(null)
const contentScrollerRef = ref<InstanceType<typeof DynamicScroller> | null>(null)

// 标准文档图片 URL 前缀
const imagePrefix = import.meta.env.VITE_STANDARD_IMAGE_PREFIX || ''

interface TextSegment { type: 'text'; html: string }
interface ImageSegment { type: 'image'; src: string }
type WordSegment = TextSegment | ImageSegment

// 按章节 id 缓存解析结果，避免虚拟滚动重复解析
const segmentCache = new Map<string, WordSegment[]>()

/** 将章节 HTML 按 <img> 标签拆分为 文字段 + 图片段，图片 src 统一拼前缀 */
function parseWordSegments(word: string, itemId: string): WordSegment[] {
  const cached = segmentCache.get(itemId)
  if (cached) return cached

  if (!word) {
    segmentCache.set(itemId, [])
    return []
  }

  const segments: WordSegment[] = []
  const imgRegex = /<img\s+([^>]*)>/g
  let lastIndex = 0
  let match

  while ((match = imgRegex.exec(word)) !== null) {
    // img 前的文字作为 HTML 段
    if (match.index > lastIndex) {
      segments.push({type: 'text', html: word.slice(lastIndex, match.index)})
    }
    // 提取 src 并拼前缀
    const attrs = match[1]
    const srcMatch = attrs.match(/src="([^"]*)"/)
    if (srcMatch) {
      const src = srcMatch[1]
      const filename = src.split('/').pop() || src
      segments.push({type: 'image', src: imagePrefix + filename})
    }
    lastIndex = match.index + match[0].length
  }
  // 剩余文字
  if (lastIndex < word.length) {
    segments.push({type: 'text', html: word.slice(lastIndex)})
  }

  segmentCache.set(itemId, segments)
  return segments
}

// 特殊章节（不需要添加序号）
const noNumberSections = ['封面', '前言', '目录', '目次', '引言', '致谢', '参考文献', '附录', '附加说明']

function isSpecialSection(title: string): boolean {
  if (!title) return false
  const trimmed = title.trim()
  return noNumberSections.some(s => trimmed === s || trimmed.startsWith(s))
}

function handleTitle(item: {title: string; title_no: string}): string {
  const {title, title_no} = item
  if (isSpecialSection(title)) return title
  if (!title_no || title.includes(title_no)) return title
  return `${title_no} ${title}`
}

const regx = /\b\d+(\.\d+)*\b/g

async function getData() {
  loading.value = true
  try {
    const [chaptersRes, detailRes] = await Promise.all([
      fetchJghPdfChapters(props.mainTaskId),
      fetchStandardBaseInfoDetail(props.standardId)
    ])
    if (detailRes.data) {
      stdInfo.value = detailRes.data
    }
    if (chaptersRes.data?.length) {
      chapters.value = chaptersRes.data
        .filter(item => item.title && item.title !== '目次' && item.title !== '封面')
        .map(item => {
          const fullTitle = handleTitle(item)
          const matches = fullTitle.match(regx)
          let level = 0
          if (matches?.length) {
            const arr = matches[0].split('.')
            if (arr.length > 1) level = arr.length - 1
          }
          return {
            ...item,
            fullTitle,
            level
          }
        })
    }
    // 章节就绪后，若外部指定了章节号（如从比对页点章节号进来），自动定位
    if (props.chapterNo) tryGotoChapter(props.chapterNo)
  } catch (error) {
    console.error('获取章节数据失败:', error)
  } finally {
    loading.value = false
  }
}

getData()

const gotoContent = (item: typeof chapters.value[0]) => {
  activeId.value = item.id
  const index = chapters.value.findIndex(c => c.id === item.id)
  if (index >= 0) {
    contentScrollerRef.value?.scrollToItem(index, {align: 'start'})
    sidebarScrollerRef.value?.scrollToItem(index, {align: 'center'})
  }
}

const gotoMenu = () => {
  const index = chapters.value.findIndex(c => c.id === activeId.value)
  if (index >= 0) {
    sidebarScrollerRef.value?.scrollToItem(index, {align: 'center'})
  }
}

// 归一化章节号（去空白），容错 "6.1.14" / "6. 1.14" 之类格式差异
function normNo(s?: string): string {
  return (s || '').replace(/\s+/g, '')
}

// 按章节号查找章节：精确匹配优先；否则退到「最近的上级章节」（切分粒度比 clause 粗时把用户带到附近）
function findChapter(no: string) {
  const n = normNo(no)
  if (!n) return null
  const exact = chapters.value.find(c => normNo(c.title_no) === n)
  if (exact) return exact
  const ancestors = chapters.value.filter(c => {
    const t = normNo(c.title_no)
    return !!t && n.startsWith(t + '.')
  })
  if (!ancestors.length) return null
  return ancestors.sort((a, b) => normNo(b.title_no).length - normNo(a.title_no).length)[0]
}

// 跳转到指定章节号：立即高亮，等虚拟滚动 layout 后再滚动定位
async function tryGotoChapter(no: string) {
  const hit = findChapter(no)
  if (!hit) {
    window.$message?.warning?.(`未在该标准正文中定位到章节 ${no}`)
    return
  }
  activeId.value = hit.id
  await nextTick()
  setTimeout(() => {
    const index = chapters.value.findIndex(c => c.id === hit.id)
    if (index < 0) return
    contentScrollerRef.value?.scrollToItem(index, {align: 'start'})
    sidebarScrollerRef.value?.scrollToItem(index, {align: 'center'})
  }, 80)
}

// 兜底：组件已挂载、章节已就绪后 chapterNo 才变化（或父组件后传）时也跳一次
watch(() => props.chapterNo, (no) => {
  if (no && chapters.value.length) tryGotoChapter(no)
})
</script>

<template>
  <NSpin :show="loading">
    <div class="flex pt-4" style="height: calc(100vh - 180px)">
      <!-- 左侧栏：标准信息 + 章节导航 -->
      <div class="lg:flex hidden flex-[1] mr-8 border-r border-r-gray-200">
        <div class="flex flex-col h-full w-full">
          <!-- 标准信息（紧凑卡片，替代右侧大面积的 StdBaseInfo） -->
          <div v-if="stdInfo.cname" class="shrink-0 px-3 py-2 mb-2 bg-gray-50 rounded mx-2 mt-2">
            <div class="text-13px font-bold leading-5 line-clamp-2">{{ stdInfo.cname }}</div>
            <div v-if="stdInfo.standard_no" class="text-11px color-gray-400 mt-0.5">{{ stdInfo.standard_no }}</div>
            <div class="flex gap-1 mt-1 flex-wrap">
              <span v-if="stdInfo.state" class="text-11px px-1.5 rounded" :class="stdInfo.state === '现行' ? 'bg-green-50 text-green-600' : stdInfo.state === '废止' ? 'bg-red-50 text-red-500' : 'bg-yellow-50 text-yellow-600'">{{ stdInfo.state }}</span>
              <span v-if="stdInfo.std_domain" class="text-11px px-1.5 rounded bg-blue-50 text-blue-500">{{ stdInfo.std_domain }}</span>
            </div>
          </div>

          <h5 class="text-sm font-bold pl-6 py-2 mb-1 bg-gray-50 shrink-0">目录导航</h5>

          <!-- 章节列表（RecycleScroller 虚拟滚动） -->
          <div class="flex-1 min-h-0">
            <RecycleScroller
              ref="sidebarScrollerRef"
              class="h-full"
              :items="chapters"
              :item-size="36"
              key-field="id"
            >
              <template #default="{item}">
                <div
                  :style="{paddingLeft: `${item.level * 16 + 24}px`}"
                  :class="{'bg-blue-50': activeId === item.id}"
                  class="py-2 cursor-pointer pr-2 hover:bg-blue-50 text-13px flex items-center"
                  style="height: 36px; box-sizing: border-box"
                  @click="gotoContent(item)"
                >
                  <span class="truncate">{{ item.fullTitle }}</span>
                </div>
              </template>
            </RecycleScroller>
          </div>
        </div>
      </div>

      <!-- 右侧：纯正文区域（最大化可视空间） -->
      <div class="flex-[2] w-full flex flex-col min-h-0">
        <!-- 极简顶栏：标题 + 目次弹出按钮 -->
        <div class="shrink-0 flex items-center justify-between px-4 py-2 bg-gray-50 rounded-t-8px border-b border-gray-200 mb-2">
          <div class="font-bold text-14px truncate flex-1">
            {{ stdInfo.cname || '正文' }}
          </div>
          <div class="flex items-center gap-2 shrink-0 ml-3">
            <NPopover
              v-if="chapters.length"
              trigger="click"
              placement="bottom-end"
              :width="420"
              scrollable
            >
              <template #trigger>
                <div class="cursor-pointer text-13px color-blue-500 hover:color-blue-700 flex items-center gap-1 select-none">
                  <span>目次</span>
                  <span class="text-11px">▼</span>
                </div>
              </template>
              <div style="max-height: 60vh; overflow-y: auto">
                <div
                  v-for="item in chapters"
                  :key="'toc_' + item.id"
                  :style="{paddingLeft: `${item.level * 12 + 8}px`}"
                  class="py-1.5 text-13px cursor-pointer hover:color-blue-500 hover:bg-blue-50 rounded px-2"
                  :class="{'color-blue-600 font-bold': activeId === item.id}"
                  @click="gotoContent(item)"
                >
                  {{ item.fullTitle }}
                </div>
              </div>
            </NPopover>
            <div
              v-if="activeId"
              class="cursor-pointer text-12px color-gray-400 hover:color-blue-500 select-none"
              @click="gotoMenu"
            >
              定位当前
            </div>
          </div>
        </div>

        <!-- 正文虚拟滚动区域（占据几乎全部剩余空间） -->
        <div v-if="chapters.length === 0 && !loading" class="p-6">
          <NEmpty description="暂无章节数据" />
        </div>
        <DynamicScroller
          v-else
          ref="contentScrollerRef"
          class="flex-1 min-h-0"
          :items="chapters"
          :min-item-size="120"
          key-field="id"
        >
          <template #default="{item, active}">
            <DynamicScrollerItem
              :item="item"
              :active="active"
              :watch-data="false"
            >
              <div
                :id="'content_' + item.id"
                :class="{'bg-blue-50': activeId === item.id}"
                class="mb-2 py-2 px-3 rounded-4px"
              >
                <p class="text-15px font-bold mb-3">{{ item.fullTitle }}</p>
                <div
                  class="pl-3 text-13px leading-7 color-gray-700 break-words"
                >
                  <NImageGroup>
                    <template v-for="(seg, idx) in parseWordSegments(item.word, item.id)" :key="idx">
                      <span v-if="seg.type === 'text'" class="word-text" v-html="seg.html" />
                      <div v-else class="word-img">
                        <NImage
                          :src="seg.src"
                          :style="{width: '100%', maxHeight: '400px', objectFit: 'contain'}"
                          lazy
                        />
                      </div>
                    </template>
                  </NImageGroup>
                </div>
              </div>
            </DynamicScrollerItem>
          </template>
        </DynamicScroller>
      </div>
    </div>
  </NSpin>
</template>

<style scoped>
.word-text {
  white-space: pre-wrap;
}

.word-img {
  display: block;
  margin: 8px 0;
  cursor: zoom-in;
}

.word-img :deep(.n-image) {
  display: block;
}
</style>
