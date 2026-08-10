<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { NDrawer, NDrawerContent, NSpace, NInput, NButton, NCard, NSelect, NRadioGroup, NRadio, NAlert, useMessage } from 'naive-ui';
import { fetchAgentBatchCheckDeduplication, fetchAllActivePools } from '@/service/api';

const message = useMessage();

// Props & Emits
const props = defineProps<{
  show: boolean;
}>();

const emit = defineEmits<{
  'update:show': [value: boolean];
  'success': [batchId: number];
}>();

// 表单数据
const standardNosInput = ref('');
const selectedPoolId = ref<number | null>(null);
const batchName = ref('');
// 查重模式：deep（精细，默认）/ fast（嵌入式快速）
const mode = ref<'deep' | 'fast'>('deep');

// 查重池选项
const poolOptions = ref<Array<{ label: string; value: number }>>([]);

// 解析输入框中的标准编号数量，用于动态提示
const inputCount = computed(() => {
  return standardNosInput.value
    .split('\n')
    .map(line => line.trim())
    .filter(line => line.length > 0).length;
});

const RECOMMEND_FAST_THRESHOLD = 200;

const showFastRecommend = computed(
  () => inputCount.value > RECOMMEND_FAST_THRESHOLD && mode.value === 'deep'
);

// 加载查重池选项
async function loadPoolOptions() {
  try {
    const res = await fetchAllActivePools();
    if (res.data && res.data.pools) {
      poolOptions.value = res.data.pools.map(pool => ({
        label: `${pool.pool_name} (${pool.standard_count}个标准)`,
        value: pool.id
      }));

      // 如果没有选中的查重池，默认选中第一个（如果有）
      if (poolOptions.value.length > 0 && selectedPoolId.value === null) {
        selectedPoolId.value = poolOptions.value[0].value;
      }
    }
  } catch (error: any) {
    message.error(`加载查重池失败: ${error.message || '未知错误'}`);
  }
}

// 提交查重
async function handleSubmit() {
  const standardNos = standardNosInput.value
    .split('\n')
    .map(line => line.trim())
    .filter(line => line.length > 0);

  if (standardNos.length === 0) {
    message.warning('请输入至少一个标准编号');
    return;
  }

  // 立即显示提示并关闭抽屉
  const modeLabel = mode.value === 'fast' ? '快速' : '精细';
  message.success(`已提交 ${standardNos.length} 个标准（${modeLabel}模式）进行分析，请稍后在历史记录中查看结果`);

  // 清空输入并关闭抽屉
  const inputNos = standardNos.slice(); // 保存一份副本用于后台请求
  const poolId = selectedPoolId.value;
  const inputBatchName = batchName.value;
  const submitMode = mode.value;
  standardNosInput.value = '';
  batchName.value = '';
  emit('update:show', false);

  // 后台异步执行查重任务
  (async () => {
    try {
      const response = await fetchAgentBatchCheckDeduplication({
        standard_nos: inputNos,
        pool_id: poolId || undefined,
        batch_name: inputBatchName || undefined,
        mode: submitMode
      });

      // 查重成功后，触发父组件刷新并打开详情
      const batchId = response.data?.batch_id;
      if (batchId) {
        // 短暂延迟后触发成功事件，让父组件刷新列表并打开详情
        setTimeout(() => {
          emit('success', batchId);
          message.success(`分析完成！共处理 ${response.data?.processed}/${response.data?.total} 个标准`);
        }, 500);
      }
    } catch (error: any) {
      // 后台失败时也提示用户
      message.error(`分析失败: ${error.message || '未知错误'}，请重试`);
    }
  })();
}

// 示例标准编号
const exampleStandards = `GB/T 11313.24-2009
GB/T 27001-2022
GB/T 28001-2011`;

function loadExample() {
  standardNosInput.value = exampleStandards;
}

// 组件挂载时加载查重池选项
onMounted(() => {
  loadPoolOptions();
});
</script>

<template>
  <NDrawer :show="show" :width="800" @update:show="emit('update:show', $event)">
    <NDrawerContent title="批量相似度分析" closable>
      <NSpace vertical :size="16">
        <!-- 查重池选择 -->
        <NCard title="选择查重池" :bordered="false" size="small">
          <NSelect
            v-model:value="selectedPoolId"
            :options="poolOptions"
            placeholder="请选择查重池"
            clearable
          />
          <div class="mt-2 text-gray-600 text-sm">
            提示：选择一个查重池可以将查重范围限定在该池内的标准中。如果不选择，则在全库范围内查重。
          </div>
        </NCard>

        <!-- 分析模式 -->
        <NCard title="分析模式" :bordered="false" size="small">
          <NRadioGroup v-model:value="mode">
            <NSpace vertical :size="6">
              <NRadio value="deep">
                <span class="font-medium">精细模式（默认）</span>
                <span class="text-gray-500 text-sm ml-2">
                  Agent 自主多维度召回 + 推理打标，结果更准确，适合 ≤ {{ RECOMMEND_FAST_THRESHOLD }} 条
                </span>
              </NRadio>
              <NRadio value="fast">
                <span class="font-medium">快速模式</span>
                <span class="text-gray-500 text-sm ml-2">
                  纯嵌入式向量召回 + 启发式打标，速度大幅提升、效果略差，适合大批量（&gt; {{ RECOMMEND_FAST_THRESHOLD }} 条）
                </span>
              </NRadio>
            </NSpace>
          </NRadioGroup>

          <NAlert
            v-if="showFastRecommend"
            type="warning"
            class="mt-3"
            :show-icon="true"
          >
            当前已输入 {{ inputCount }} 条标准，超过 {{ RECOMMEND_FAST_THRESHOLD }} 条建议切换至「快速模式」以避免长时间等待。
          </NAlert>
        </NCard>

        <!-- 批次名称 -->
        <NCard title="批次名称（可选）" :bordered="false" size="small">
          <NInput
            v-model:value="batchName"
            placeholder="请输入批次名称，方便后续识别（如：2024年第一批次）"
            maxlength="200"
            show-count
            clearable
          />
        </NCard>

        <!-- 输入区 -->
        <NCard title="输入标准编号" :bordered="false" size="small">
          <NSpace vertical :size="12">
            <div>
              <div class="mb-2 flex items-center justify-between">
                <span>标准编号（每行一个）：当前 {{ inputCount }} 条</span>
                <NButton text type="primary" size="small" @click="loadExample">
                  加载示例
                </NButton>
              </div>
              <NInput
                v-model:value="standardNosInput"
                type="textarea"
                :rows="10"
                placeholder="请输入标准编号，每行一个&#10;例如：&#10;GB/T 11313.24-2009&#10;GB/T 27001-2022"
              />
            </div>

            <NSpace>
              <NButton type="primary" @click="handleSubmit">
                开始分析
              </NButton>
              <NButton @click="standardNosInput = ''">
                清空输入
              </NButton>
            </NSpace>
          </NSpace>
        </NCard>

        <!-- 提示信息 -->
        <NCard :bordered="false" size="small">
          <div class="text-gray-600 text-sm">
            <div class="mb-2">
              <icon-mdi:information-outline class="inline-block mr-1" />
              提示：
            </div>
            <ul class="list-disc list-inside space-y-1">
              <li>点击"开始分析"后，任务将在后台执行</li>
              <li>分析完成后会自动弹出通知</li>
              <li>您可以在"批量相似度分析历史"中查看结果</li>
              <li>大批量（&gt; {{ RECOMMEND_FAST_THRESHOLD }} 条）建议使用「快速模式」</li>
            </ul>
          </div>
        </NCard>
      </NSpace>

      <template #footer>
        <NSpace justify="end">
          <NButton @click="emit('update:show', false)">
            关闭
          </NButton>
        </NSpace>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>
