<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { NDrawer, NDrawerContent, NSpace, NCard, NStatistic, NSpin, NEmpty, NProgress, NTag, NAlert } from 'naive-ui';
import { fetchFullTextSimilarity } from '@/service/api';
import { useMessage } from 'naive-ui';

const message = useMessage();

// Props & Emits
const props = defineProps<{
  show: boolean;
  sourceStandardNo: string;  // 源标准编号
  targetStandardNo: string;  // 目标标准编号
}>();

const emit = defineEmits<{
  'update:show': [value: boolean];
}>();

const loading = ref(false);
const similarityData = ref<Api.AI.FullTextSimilarityResponse | null>(null);
const error = ref<string | null>(null); // 错误信息

// 监听打开事件，自动加载数据
async function loadSimilarityData() {
  if (!props.sourceStandardNo || !props.targetStandardNo) {
    console.warn('缺少必要参数', {
      sourceStandardNo: props.sourceStandardNo,
      targetStandardNo: props.targetStandardNo
    });
    return;
  }

  loading.value = true;
  error.value = null; // 清除之前的错误
  try {
    console.log('开始加载全文相似度', {
      sourceStandardNo: props.sourceStandardNo,
      targetStandardNo: props.targetStandardNo
    });

    const response = await fetchFullTextSimilarity({
      source_standard_no: props.sourceStandardNo,
      target_standard_no: props.targetStandardNo
    });

    console.log('全文相似度响应', response);

    // 检查响应数据是否为空（接口虽然成功，但data为null表示有错误）
    if (!response.data) {
      error.value = (response as any).msg || '数据加载失败';
      similarityData.value = null;
    } else {
      similarityData.value = response.data;
      error.value = null;
    }
  } catch (err: any) {
    console.error('加载全文相似度失败', err);
    // 保存错误信息，不关闭抽屉
    error.value = err.message || '未知错误';
    similarityData.value = null;
  } finally {
    loading.value = false;
  }
}

// 监听 show 属性变化
watch(() => props.show, (newVal) => {
  if (newVal) {
    loadSimilarityData();
  } else {
    similarityData.value = null;
    error.value = null; // 清除错误
  }
});

// 监听打开状态（用户手动关闭）
function handleUpdateShow(value: boolean) {
  emit('update:show', value);
}

// 获取相似度等级
const similarityLevel = computed(() => {
  if (!similarityData.value) return { text: '未知', type: 'default' as const };

  const percentage = similarityData.value.similarity_percentage;

  if (percentage >= 80) {
    return { text: '高度相似', type: 'error' as const };
  } else if (percentage >= 50) {
    return { text: '中度相似', type: 'warning' as const };
  } else if (percentage >= 20) {
    return { text: '低度相似', type: 'info' as const };
  } else {
    return { text: '基本不相似', type: 'success' as const };
  }
});

// 高亮文本片段
interface TextSegment {
  text: string;
  highlight: boolean;
}

function highlightText(text: string, matchingBlocks: any[], isSource: boolean): TextSegment[] {
  if (!matchingBlocks || matchingBlocks.length === 0) {
    return [{ text, highlight: false }];
  }

  const segments: TextSegment[] = [];
  let lastEnd = 0;

  // 根据是源句子还是目标句子，使用不同的起止位置
  const blocks = matchingBlocks.map(block => ({
    start: isSource ? block.source_start : block.target_start,
    end: isSource ? block.source_end : block.target_end
  })).sort((a, b) => a.start - b.start);

  for (const block of blocks) {
    // 添加非高亮部分
    if (block.start > lastEnd) {
      segments.push({
        text: text.substring(lastEnd, block.start),
        highlight: false
      });
    }

    // 添加高亮部分
    segments.push({
      text: text.substring(block.start, block.end),
      highlight: true
    });

    lastEnd = block.end;
  }

  // 添加最后的非高亮部分
  if (lastEnd < text.length) {
    segments.push({
      text: text.substring(lastEnd),
      highlight: false
    });
  }

  return segments;
}
</script>

<template>
  <NDrawer :show="show" width="100%" @update:show="handleUpdateShow">
    <NDrawerContent :title="`全文相似度分析`" >
      <NSpin :show="loading">
        <!-- 错误提示 -->
        <div v-if="error && !loading" class="error-container">
          <NEmpty description="">
            <template #icon>
              <svg class="error-icon w-16 h-16 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </template>
            <template #extra>
              <div class="error-content">
                <div class="error-title">无法加载全文相似度</div>
                <div class="error-message">{{ error }}</div>
                <NAlert type="info" :bordered="false" class="mt-16px">
                  <template #header>
                    <div class="flex items-center gap-2">
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      <span>可能的原因</span>
                    </div>
                  </template>
                  <ul class="error-reasons">
                    <li>标准在数据库中只有题录信息，缺少全文内容</li>
                    <li>标准文件尚未上传或处理</li>
                    <li>标准编号错误或不存在</li>
                  </ul>
                </NAlert>
                <div class="error-info mt-16px">
                  <div class="info-item">
                    <span class="info-label">源标准：</span>
                    <span class="info-value">{{ sourceStandardNo }}</span>
                  </div>
                  <div class="info-item">
                    <span class="info-label">目标标准：</span>
                    <span class="info-value">{{ targetStandardNo }}</span>
                  </div>
                </div>
              </div>
            </template>
          </NEmpty>
        </div>

        <!-- 正常数据显示 -->
        <div v-else-if="similarityData" class="flex flex-col gap-16px">
          <!-- 概览信息 -->
          <NCard title="相似度概览" :bordered="false" size="small">
            <NSpace vertical :size="16">
              <!-- 标准信息 -->
              <div class="flex items-center justify-between">
                <div class="flex-1 standard-info-source">
                  <div class="text-sm text-gray-600 mb-1">源标准</div>
                  <div class="font-medium">{{ similarityData.source_standard_no }}</div>
                  <div class="text-sm text-gray-500">{{ similarityData.source_standard_name }}</div>
                </div>
                <div class="px-16px flex flex-col items-center gap-1">
                  <svg class="w-6 h-6 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6" />
                  </svg>
                  <div class="similarity-percentage" :class="{
                    'high': similarityData.similarity_percentage >= 80,
                    'medium': similarityData.similarity_percentage >= 50 && similarityData.similarity_percentage < 80,
                    'low': similarityData.similarity_percentage >= 20 && similarityData.similarity_percentage < 50,
                    'very-low': similarityData.similarity_percentage < 20
                  }">
                    {{ similarityData.similarity_percentage.toFixed(1) }}%
                  </div>
                </div>
                <div class="flex-1 standard-info-target">
                  <div class="text-sm text-gray-600 mb-1">目标标准</div>
                  <div class="font-medium">{{ similarityData.target_standard_no }}</div>
                  <div class="text-sm text-gray-500">{{ similarityData.target_standard_name }}</div>
                </div>
              </div>

              <!-- 相似度指标 -->
              <div class="grid grid-cols-4 gap-16px">
                <NCard embedded>
                  <NStatistic label="相似度" :value="similarityData.similarity_percentage" suffix="%">
                    <template #prefix>
                      <NTag :type="similarityLevel.type" size="small" :bordered="false">
                        {{ similarityLevel.text }}
                      </NTag>
                    </template>
                  </NStatistic>
                  <NProgress
                    type="line"
                    :percentage="similarityData.similarity_percentage"
                    :color="similarityLevel.type === 'error' ? '#d03050' : similarityLevel.type === 'warning' ? '#f0a020' : '#18a058'"
                    :show-indicator="false"
                    class="mt-8px"
                  />
                </NCard>

                <NCard embedded>
                  <NStatistic label="对应句子数" :value="similarityData.matched_sentence_count" />
                </NCard>

                <NCard embedded>
                  <NStatistic label="源标准总句子数" :value="similarityData.source_total_sentence_count" />
                </NCard>

                <NCard embedded>
                  <NStatistic label="目标标准总句子数" :value="similarityData.target_total_sentence_count" />
                </NCard>
              </div>

              <!-- 提示信息 -->
              <NAlert v-if="similarityData.similarity_percentage >= 50" type="warning" :bordered="false">
                两个标准存在较高的文本相似度，建议进一步人工审核是否存在内容重复或引用关系。
              </NAlert>
            </NSpace>
          </NCard>

          <!-- 匹配详情 -->
          <NCard title="对应句子详情" :bordered="false" size="small">
            <div v-if="similarityData.matches.length > 0" class="flex flex-col gap-12px">
              <div
                v-for="(match, index) in similarityData.matches"
                :key="index"
                class="match-item"
              >
                <div class="match-header">
                  <span class="match-index">#{{ index + 1 }}</span>
                  <NTag type="info" size="small" :bordered="false">
                    相似度: {{ match.similarity.toFixed(2) }}%
                  </NTag>
                </div>

                <div class="match-content">
                  <!-- 源句子 -->
                  <div class="match-source">
                    <div class="match-meta">
                      <span class="meta-item">{{ match.source_chapter_title }}</span>
                      <span v-if="match.source_page" class="meta-item">第 {{ match.source_page }} 页</span>
                    </div>
                    <div class="match-text">
                      <template v-for="(segment, segIdx) in highlightText(match.source_sentence_text, match.matching_blocks, true)" :key="`source-${index}-${segIdx}`">
                        <mark v-if="segment.highlight" class="highlight">{{ segment.text }}</mark>
                        <span v-else>{{ segment.text }}</span>
                      </template>
                    </div>
                  </div>

                  <!-- 箭头 -->
                  <div class="match-arrow">
                    <svg class="w-6 h-6 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6" />
                    </svg>
                  </div>

                  <!-- 目标句子 -->
                  <div class="match-target">
                    <div class="match-meta">
                      <span class="meta-item">{{ match.target_chapter_title }}</span>
                      <span v-if="match.target_page" class="meta-item">第 {{ match.target_page }} 页</span>
                    </div>
                    <div class="match-text">
                      <template v-for="(segment, segIdx) in highlightText(match.target_sentence_text, match.matching_blocks, false)" :key="`target-${index}-${segIdx}`">
                        <mark v-if="segment.highlight" class="highlight">{{ segment.text }}</mark>
                        <span v-else>{{ segment.text }}</span>
                      </template>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <NEmpty v-else description="未找到匹配的句子" class="py-32px" />
          </NCard>
        </div>
      </NSpin>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
/* 错误容器 */
.error-container {
  padding: 40px 20px;
  min-height: 400px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.error-icon {
  max-width: 40px;
  max-height: 40px;
  display: block;
}

.error-content {
  max-width: 600px;
  text-align: center;
}

.error-title {
  font-size: 18px;
  font-weight: 600;
  color: #333;
  margin-bottom: 8px;
}

.error-message {
  font-size: 14px;
  color: #d03050;
  padding: 8px 16px;
  background: #fef0f0;
  border-radius: 4px;
  margin-bottom: 8px;
  word-break: break-word;
}

.error-reasons {
  list-style: disc;
  text-align: left;
  padding-left: 24px;
  margin: 8px 0 0 0;
  line-height: 1.8;
}

.error-reasons li {
  color: #666;
  font-size: 13px;
}

.error-info {
  background: #f5f5f5;
  padding: 12px 16px;
  border-radius: 6px;
  text-align: left;
}

.info-item {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.info-item:last-child {
  margin-bottom: 0;
}

.info-label {
  font-size: 13px;
  color: #666;
  font-weight: 500;
}

.info-value {
  font-size: 13px;
  color: #333;
  font-family: monospace;
}

/* 原有样式 */
/* 标准信息区域背景色 */
.standard-info-source {
  padding: 12px;
  background: linear-gradient(135deg, #f0fdf4 0%, #dcfce7 100%);
  border-radius: 8px;
  border: 2px solid #18a058;
}

.standard-info-target {
  padding: 12px;
  background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%);
  border-radius: 8px;
  border: 2px solid #2080f0;
}

.match-item {
  padding: 16px;
  background: #fafafa;
  border-radius: 8px;
  border: 1px solid #f0f0f0;
}

.match-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #e0e0e0;
}

.match-index {
  font-weight: 600;
  color: #666;
}

.match-content {
  display: flex;
  flex-direction: row;
  align-items: stretch;
  gap: 16px;
}

.match-source,
.match-target {
  flex: 1;
  padding: 12px;
  background: white;
  border-radius: 6px;
  border: 1px solid #e8e8e8;
}

.match-source {
  border-left: 3px solid #18a058;
}

.match-target {
  border-left: 3px solid #2080f0;
}

.match-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.meta-item {
  font-size: 12px;
  color: #999;
  padding: 2px 8px;
  background: #f5f5f5;
  border-radius: 4px;
}

.match-text {
  font-size: 14px;
  line-height: 1.6;
  color: #333;
}

.match-text .highlight {
  background-color: #ffe58f;
  padding: 2px 4px;
  border-radius: 2px;
  font-weight: 500;
}

.match-arrow {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.similarity-percentage {
  font-size: 18px;
  font-weight: 700;
  font-family: monospace;
  padding: 2px 10px;
  border-radius: 6px;
  line-height: 1.4;
}

.similarity-percentage.high {
  color: #d03050;
  background: #fde8ec;
}

.similarity-percentage.medium {
  color: #f0a020;
  background: #fef6e7;
}

.similarity-percentage.low {
  color: #2080f0;
  background: #e8f3ff;
}

.similarity-percentage.very-low {
  color: #18a058;
  background: #e8f8ef;
}
</style>
