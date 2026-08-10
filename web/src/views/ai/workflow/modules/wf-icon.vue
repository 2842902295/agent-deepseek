<script setup lang="ts">
/**
 * 工作流画布统一图标集：一套线稿绘图风格（24×24 网格 / 1.8 线宽 / 圆头圆角 / currentColor），
 * 替换原先 emoji + 几何字符混用的杂牌图标（✎ ✦ ▦ ◆ 📎 ✋ ▶ ■…）。
 * 节点类型图标与 8 类封闭词表一一对应（传 textNode / taskNode 等类型名即可，自动去 Node 后缀）；
 * 其余为画布通用小图标。颜色一律由外层 color 决定，尺寸走 size 属性。
 */
import {computed} from 'vue';

const props = withDefaults(
  defineProps<{
    /** 图标名：节点类型名（textNode/taskNode/…）或通用名（copy/pencil/flag/…） */
    name: string;
    /** 像素尺寸（宽高同值） */
    size?: number;
  }>(),
  {size: 16}
);

const key = computed(() => props.name.replace(/Node$/, ''));
</script>

<template>
  <svg
    class="wf-ic"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <!-- ── 节点类型 8 件套 ── -->
    <!-- 文本：三行错落的叙述线 -->
    <template v-if="key === 'text'">
      <path d="M4.5 6.5h15" />
      <path d="M4.5 12h15" />
      <path d="M4.5 17.5h9.5" />
    </template>
    <!-- 工作项：圆角勾选框 + 对勾 -->
    <template v-else-if="key === 'task'">
      <rect x="4" y="4" width="16" height="16" rx="4.5" />
      <path d="m8.7 12.4 2.4 2.4 4.5-5.2" />
    </template>
    <!-- 数据：三根柱状条 -->
    <template v-else-if="key === 'data'">
      <path d="M6 19.5v-6.5" />
      <path d="M12 19.5v-13" />
      <path d="M18 19.5v-9.5" />
    </template>
    <!-- 结论：菱形（沿用 ◆ 的识别符号） -->
    <template v-else-if="key === 'conclusion'">
      <path d="M12 3.6 20.4 12 12 20.4 3.6 12Z" />
    </template>
    <!-- 附件：回形针 -->
    <template v-else-if="key === 'file'">
      <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" />
    </template>
    <!-- 人工核查：人像 + 对勾 -->
    <template v-else-if="key === 'review'">
      <circle cx="10" cy="8" r="4.5" />
      <path d="M2.5 20.5a7.5 7.5 0 0 1 12.9-5.2" />
      <path d="m15.5 18.5 2 2 4-4.2" />
    </template>
    <!-- 开始：圆 + 播放三角 -->
    <template v-else-if="key === 'start'">
      <circle cx="12" cy="12" r="8.5" />
      <path d="m10.3 8.8 5.4 3.2-5.4 3.2z" />
    </template>
    <!-- 结束：圆 + 停止方块 -->
    <template v-else-if="key === 'end'">
      <circle cx="12" cy="12" r="8.5" />
      <rect x="9.3" y="9.3" width="5.4" height="5.4" rx="1.4" />
    </template>

    <!-- 镜头（品牌板型：分镜卡——一格三镜的分镜条） -->
    <template v-else-if="key === 'shot'">
      <rect x="3.5" y="5" width="17" height="14" rx="2.5" />
      <path d="M9.2 5v14" />
      <path d="M14.8 5v14" />
    </template>

    <!-- ── 胶囊内的实心图形（渐变底白字上更利落） ── -->
    <template v-else-if="key === 'play'">
      <path d="M7.6 5.2v13.6L18.9 12Z" fill="currentColor" stroke="none" />
    </template>
    <template v-else-if="key === 'stop'">
      <rect x="6" y="6" width="12" height="12" rx="2.6" fill="currentColor" stroke="none" />
    </template>

    <!-- ── 画布通用 ── -->
    <!-- 复制 -->
    <template v-else-if="key === 'copy'">
      <rect x="8" y="8" width="13" height="13" rx="2.5" />
      <path d="M5 16H4.5A1.5 1.5 0 0 1 3 14.5v-10A1.5 1.5 0 0 1 4.5 3h10A1.5 1.5 0 0 1 16 4.5V5" />
    </template>
    <!-- 删除 -->
    <template v-else-if="key === 'trash'">
      <path d="M3.5 6.5h17" />
      <path d="M18.5 6.5V19a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2V6.5" />
      <path d="M8.5 6.5v-2a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v2" />
      <path d="M10 11v5.5" />
      <path d="M14 11v5.5" />
    </template>
    <!-- 铅笔（编辑） -->
    <template v-else-if="key === 'pencil'">
      <path d="M17 3.5a2.47 2.47 0 0 1 3.5 3.5L7.5 20 2.5 21.5 4 16.5Z" />
      <path d="m14.8 5.7 3.5 3.5" />
    </template>
    <!-- 四角星（AI 重整等入口：实心，小尺寸底色上更利落，同 play/stop 处理） -->
    <template v-else-if="key === 'sparkle'">
      <path d="M12 3l2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2Z" fill="currentColor" stroke="none" />
    </template>
    <!-- 小旗（注记） -->
    <template v-else-if="key === 'flag'">
      <path d="M4.5 14.5s1-1 3.8-1 4.7 2 7.4 2 3.8-1 3.8-1v-11s-1 1-3.8 1-4.7-2-7.4-2-3.8 1-3.8 1z" />
      <path d="M4.5 21.5v-7" />
    </template>
    <!-- 警示三角（注意） -->
    <template v-else-if="key === 'warning'">
      <path d="M12 4 21.5 20.5h-19Z" />
      <path d="M12 10.5v4" />
      <path d="M12 17.5h.01" />
    </template>
    <!-- 放大镜（预览） -->
    <template v-else-if="key === 'zoom'">
      <circle cx="11" cy="11" r="7.5" />
      <path d="m16.6 16.6 4.4 4.4" />
    </template>
    <!-- 全屏（四角外扩） -->
    <template v-else-if="key === 'expand'">
      <path d="M3.5 8v-2a2 2 0 0 1 2-2h2" />
      <path d="M16.5 4h2a2 2 0 0 1 2 2v2" />
      <path d="M20.5 16v2a2 2 0 0 1-2 2h-2" />
      <path d="M7.5 20h-2a2 2 0 0 1-2-2v-2" />
    </template>
    <!-- 下载 -->
    <template v-else-if="key === 'download'">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="m7 10 5 5 5-5" />
      <path d="M12 15V3" />
    </template>
    <!-- 锁 -->
    <template v-else-if="key === 'lock'">
      <rect x="3.5" y="10.5" width="17" height="10" rx="2.5" />
      <path d="M7.5 10.5v-3a4.5 4.5 0 0 1 9 0v3" />
    </template>
    <!-- 四向拖拽 -->
    <template v-else-if="key === 'move'">
      <path d="M12 3v18" />
      <path d="M3 12h18" />
      <path d="m9.7 5.3 2.3-2.3 2.3 2.3" />
      <path d="m9.7 18.7 2.3 2.3 2.3-2.3" />
      <path d="m5.3 9.7-2.3 2.3 2.3 2.3" />
      <path d="m18.7 9.7 2.3 2.3-2.3 2.3" />
    </template>
    <!-- 虚线框选 -->
    <template v-else-if="key === 'select'">
      <rect x="4" y="4" width="16" height="16" rx="3" stroke-dasharray="3.2 3.4" />
    </template>
    <!-- 对话气泡 -->
    <template v-else-if="key === 'chat'">
      <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" />
    </template>
    <!-- 下拉箭头 -->
    <template v-else-if="key === 'chevron'">
      <path d="m6.5 9.5 5.5 5.5 5.5-5.5" />
    </template>
    <!-- 自动布局（流程卡） -->
    <template v-else-if="key === 'layout'">
      <rect x="3" y="3" width="8" height="8" rx="2" />
      <path d="M7 11v4a2 2 0 0 0 2 2h4" />
      <rect x="13" y="13" width="8" height="8" rx="2" />
    </template>
    <!-- 适应视图（对角外扩） -->
    <template v-else-if="key === 'fit'">
      <path d="M15 3.5h5.5V9" />
      <path d="M9 20.5H3.5V15" />
      <path d="M20.5 3.5 14 10" />
      <path d="M3.5 20.5 10 14" />
    </template>
    <!-- 撤销 / 重做 -->
    <template v-else-if="key === 'undo'">
      <path d="M9 14 4 9l5-5" />
      <path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5 5.5 5.5 0 0 1-5.5 5.5H11" />
    </template>
    <template v-else-if="key === 'redo'">
      <path d="m15 14 5-5-5-5" />
      <path d="M20 9H9.5A5.5 5.5 0 0 0 4 14.5 5.5 5.5 0 0 0 9.5 20H13" />
    </template>
    <!-- 叉 -->
    <template v-else-if="key === 'x'">
      <path d="m6 6 12 12" />
      <path d="M18 6 6 18" />
    </template>
    <!-- 加 / 减 / 对勾 / 靶心（回显面板操作符） -->
    <template v-else-if="key === 'plus'">
      <path d="M12 5.5v13" />
      <path d="M5.5 12h13" />
    </template>
    <template v-else-if="key === 'minus'">
      <path d="M5.5 12h13" />
    </template>
    <template v-else-if="key === 'check'">
      <path d="m5 12.8 4.3 4.3L19 7.3" />
    </template>
    <template v-else-if="key === 'target'">
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="3.5" />
    </template>
  </svg>
</template>

<style scoped>
.wf-ic {
  display: block;
  flex-shrink: 0;
}
</style>
