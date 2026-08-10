<script setup lang="ts">
/**
 * 块级 Markdown 编辑器
 *
 * 把 markdown 拆成"按段编辑"的块（标题/段落/列表/引用/代码/分隔/表格），
 * 渲染态用 marked 显示，编辑态把渲染后的 div 自身切成 contenteditable，
 * 退出编辑时用 turndown 把 innerHTML 转回 md。元素本身不换，所以编辑切换
 * 没有尺寸/字体抖动。
 *
 * 知识（knowledge）和灵感（idea）共用此组件——区别只在外面那一层装饰
 * （比如 idea 的紫色引语 frame）。
 */
import {nextTick, ref, watch} from 'vue';
import {marked} from 'marked';
import type {AgentArtifact} from '@/service/api';
import {parseBlocks, stringifyBlocks, htmlToMarkdown, type MdBlock} from '../composables/useMdBlocks';
import ArtifactList from '@/views/ai/qa-glass/components/artifact-list.vue';

const props = withDefaults(
  defineProps<{
    modelValue: string;
    /** 用于跨条目切换时强制重置 block state（避免上一条的编辑态污染） */
    resetKey?: string | number | null;
    /** 空态时的占位文案 */
    emptyHint?: string;
    /** "追加一段" 按钮文案 */
    addLabel?: string;
    /** 是否隐藏"追加一段"按钮 + 空态 CTA（仅渲染） */
    readonly?: boolean;
    /** 产物列表（用于内联渲染 [artifact:ID] 块） */
    artifacts?: AgentArtifact[];
  }>(),
  {
    resetKey: null,
    emptyHint: '点此撰写正文',
    addLabel: '+ 追加一段',
    readonly: false,
    artifacts: () => []
  }
);

const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void;
}>();

const blocks = ref<MdBlock[]>([]);
const editingBlockId = ref<string | null>(null);

function rebuildBlocks() {
  blocks.value = parseBlocks(props.modelValue || '');
}

// 切换到不同条目时强制重置（外部传 resetKey 即可）
watch(() => props.resetKey, () => rebuildBlocks(), {immediate: true});

// 外部源变了（比如 agent 改写后回流），且当前没在编辑，重建块
watch(
  () => props.modelValue,
  (v, old) => {
    if (editingBlockId.value) return;
    if (v === old) return;
    rebuildBlocks();
  }
);

function renderBlockHtml(b: MdBlock): string {
  return marked.parse(b.source) as string;
}

async function enterBlockEdit(id: string, ev?: MouseEvent) {
  if (props.readonly) return;
  if (editingBlockId.value === id) return;
  if (editingBlockId.value) {
    finalizeEditing();
  }
  // 表格：切换前先量一下渲染态高度，避免坍塌
  const block = blocks.value.find((b) => b.id === id);
  let tableHeight = 0;
  if (block?.type === 'table') {
    const renderEl = document.querySelector(`[data-block-id="${id}"] .md-block-table-render`) as HTMLElement | null;
    tableHeight = renderEl?.offsetHeight || 0;
  }
  editingBlockId.value = id;
  await nextTick();
  if (block?.type === 'table') {
    const el = document.querySelector(`[data-block-id="${id}"] .table-edit-ta`) as HTMLTextAreaElement | null;
    if (el) {
      if (tableHeight > 0) el.style.minHeight = `${Math.max(tableHeight - 5, 60)}px`;
      el.focus();
      autoGrowTa(el);
    }
    return;
  }
  const el = (ev?.currentTarget as HTMLElement) || null;
  if (el && document.activeElement !== el) el.focus();
}

function autoGrowTa(el: HTMLTextAreaElement) {
  el.style.height = 'auto';
  el.style.height = `${Math.max(el.scrollHeight, 80)}px`;
}

function onTableInput(e: Event) {
  autoGrowTa(e.target as HTMLTextAreaElement);
}

function finalizeEditing() {
  const id = editingBlockId.value;
  if (!id) return;
  const idx = blocks.value.findIndex((b) => b.id === id);
  if (idx < 0) {
    editingBlockId.value = null;
    return;
  }
  const block = blocks.value[idx];
  let md = '';
  if (block.type === 'table') {
    const ta = document.querySelector(`[data-block-id="${id}"] .table-edit-ta`) as HTMLTextAreaElement | null;
    md = ta ? ta.value.trim() : block.source;
  } else {
    const el = document.querySelector(`[data-block-id="${id}"]`) as HTMLElement | null;
    if (!el) {
      editingBlockId.value = null;
      return;
    }
    const html = el.innerHTML;
    md = htmlToMarkdown(html);
  }
  if (!md.trim()) {
    blocks.value.splice(idx, 1);
  } else {
    blocks.value[idx] = {...blocks.value[idx], source: md};
  }
  editingBlockId.value = null;
  const joined = stringifyBlocks(blocks.value);
  if (joined !== props.modelValue) {
    emit('update:modelValue', joined);
  }
}

function onBlockBlur() {
  finalizeEditing();
}

function onBlockKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    (e.target as HTMLElement).blur();
    e.preventDefault();
  }
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
    (e.target as HTMLElement).blur();
    e.preventDefault();
  }
}

function addBlankBlock() {
  if (props.readonly) return;
  const id = `b_new_${Date.now()}`;
  blocks.value.push({id, type: 'paragraph', source: ''});
  enterBlockEdit(id);
}
</script>

<template>
  <div class="md-blocks">
    <div
      v-for="b in blocks"
      :key="b.id"
      :data-block-id="b.id"
      :class="['md-block', `md-block-${b.type}`, editingBlockId === b.id && 'md-block-editing']"
    >
      <!-- 表格：编辑时用 textarea 改源码，渲染时正常显示 -->
      <template v-if="b.type === 'table'">
        <div
          v-if="editingBlockId !== b.id"
          class="md-block-table-render"
          role="button"
          tabindex="0"
          @click="enterBlockEdit(b.id)"
          @keydown.enter.prevent="enterBlockEdit(b.id)"
          v-html="renderBlockHtml(b)"
        />
        <textarea
          v-else
          class="table-edit-ta"
          :value="b.source"
          spellcheck="false"
          @input="onTableInput"
          @blur="onBlockBlur"
          @keydown="onBlockKeydown"
        />
      </template>
      <!-- 产物块：不可编辑，直接渲染 ArtifactList -->
      <div v-else-if="b.type === 'artifact'" class="md-block-artifact">
        <ArtifactList
          v-if="b.artifactId != null && artifacts.find(a => a.id === b.artifactId)"
          :artifacts="[artifacts.find(a => a.id === b.artifactId)!]"
          :inline="true"
        />
      </div>
      <!-- 其他块：contenteditable -->
      <div
        v-else
        :contenteditable="editingBlockId === b.id ? 'true' : 'false'"
        spellcheck="false"
        role="button"
        tabindex="0"
        @mousedown="(e) => editingBlockId !== b.id && enterBlockEdit(b.id, e)"
        @keydown="(e) => { if (editingBlockId === b.id) onBlockKeydown(e); else if (e.key === 'Enter') { e.preventDefault(); enterBlockEdit(b.id); } }"
        @blur="onBlockBlur"
        v-html="renderBlockHtml(b)"
      />
    </div>
    <div
      v-if="!blocks.length && !readonly"
      class="md-empty-cta"
      role="button"
      tabindex="0"
      @click="addBlankBlock"
      @keydown.enter.prevent="addBlankBlock"
    >
      <span class="cec-glyph">¶</span>
      <span>{{ emptyHint }}</span>
    </div>
    <button
      v-else-if="blocks.length && !readonly"
      class="md-add-trail"
      type="button"
      :title="addLabel"
      @click="addBlankBlock"
    >{{ addLabel }}</button>
  </div>
</template>

<style scoped>
.md-blocks {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-left: 4px;
  border-left: 3px solid color-mix(in srgb, var(--accent, #1e40af) 35%, transparent);
}

.md-block {
  position: relative;
  padding: 6px 12px;
  border-radius: 8px;
  cursor: text;
  transition: background 0.15s ease, box-shadow 0.15s ease;
  min-height: 1.6em;
  outline: none;
}
.md-block,
.md-block * {
  cursor: inherit;
}
.md-block:hover {
  background: color-mix(in srgb, var(--accent, #1e40af) 5%, transparent);
}
.md-block-editing,
.md-block-editing:hover {
  background: rgba(255, 255, 255, 0.85);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent, #1e40af) 30%, transparent);
  cursor: text;
}
.md-block:focus-visible {
  background: color-mix(in srgb, var(--accent, #1e40af) 7%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent, #1e40af) 25%, transparent);
}

.md-block :deep(p),
.md-block :deep(ul),
.md-block :deep(ol),
.md-block :deep(blockquote),
.md-block :deep(pre),
.md-block :deep(table),
.md-block :deep(h1),
.md-block :deep(h2),
.md-block :deep(h3),
.md-block :deep(h4),
.md-block :deep(h5),
.md-block :deep(h6) { margin: 0; }

.md-block :deep(h1) { font-family: 'Plus Jakarta Sans', sans-serif; font-size: 22px; font-weight: 800; letter-spacing: -0.01em; color: #0f172a; line-height: 1.3; }
.md-block :deep(h2) { font-size: 18px; font-weight: 800; letter-spacing: -0.01em; line-height: 1.35; }
.md-block :deep(h3) { font-size: 16px; font-weight: 700; line-height: 1.4; }
.md-block :deep(h4), .md-block :deep(h5), .md-block :deep(h6) { font-size: 14.5px; font-weight: 700; }

.md-block :deep(ul), .md-block :deep(ol) { padding-left: 1.4em; }
.md-block :deep(li) { margin: 2px 0; }

.md-block :deep(code) {
  font-family: 'JetBrains Mono', monospace; font-size: 12.5px;
  background: color-mix(in srgb, var(--accent, #1e40af) 10%, transparent);
  color: var(--accent, #1e40af);
  padding: 1.5px 6px; border-radius: 6px; font-weight: 500;
}
.md-block :deep(pre) {
  background: #0f172a; color: #e2e8f0;
  padding: 14px 16px; overflow-x: auto;
  border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--accent, #1e40af) 22%, transparent);
}
.md-block :deep(pre code) { background: transparent; color: inherit; padding: 0; font-size: 12px; }

.md-block :deep(a) {
  color: var(--accent, #1e40af); text-decoration: underline;
  text-decoration-color: color-mix(in srgb, var(--accent, #1e40af) 35%, transparent);
  text-underline-offset: 3px;
}
.md-block :deep(a:hover) { text-decoration-color: var(--accent, #1e40af); }

.md-block :deep(blockquote) {
  padding: 6px 14px;
  border-left: 3px solid var(--accent, #1e40af);
  background: color-mix(in srgb, var(--accent, #1e40af) 6%, transparent);
  color: #334155;
  border-radius: 0 8px 8px 0;
}

.md-block :deep(table) {
  border-collapse: collapse; font-size: 13px;
}
.md-block :deep(th), .md-block :deep(td) {
  border: 1px solid color-mix(in srgb, var(--accent, #1e40af) 20%, transparent);
  padding: 6px 10px;
}
.md-block :deep(th) { background: color-mix(in srgb, var(--accent, #1e40af) 10%, transparent); font-weight: 700; }

.md-block :deep(hr) {
  border: none;
  border-top: 1px dashed color-mix(in srgb, var(--accent, #1e40af) 32%, transparent);
  margin: 4px 0;
}

.md-block-heading { padding-top: 10px; }

/* artifact 块：不可编辑，内联渲染产物 */
.md-block-artifact {
  cursor: default;
  pointer-events: auto;
}
.md-block-artifact:hover {
  background: transparent;
  outline: none;
}

.md-block-table-render {
  cursor: text;
  outline: none;
}
.md-block-table-render :deep(table),
.md-block-table-render :deep(th),
.md-block-table-render :deep(td) {
  cursor: text;
}

.table-edit-ta {
  width: 100%;
  min-height: 80px;
  padding: 8px 12px;
  border: 1px solid color-mix(in srgb, var(--accent, #1e40af) 30%, transparent);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.95);
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #1e293b;
  outline: none;
  resize: vertical;
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--accent, #1e40af) 6%, transparent),
    0 4px 14px -6px var(--accent-glow, rgba(30, 64, 175, 0.25));
}
.table-edit-ta:focus {
  border-color: var(--accent, #1e40af);
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--accent, #1e40af) 35%, transparent),
    0 6px 18px -6px var(--accent-glow, rgba(30, 64, 175, 0.25));
}

.md-empty-cta {
  padding: 60px 0;
  text-align: center;
  display: flex; flex-direction: column; align-items: center; gap: 12px;
  color: #64748b;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px; letter-spacing: 0.06em;
  font-style: italic;
  cursor: pointer;
  border-radius: 10px;
  outline: none;
  transition: background 0.18s ease;
}
.md-empty-cta:hover, .md-empty-cta:focus-visible {
  background: color-mix(in srgb, var(--accent, #1e40af) 5%, transparent);
}
.cec-glyph {
  font-size: 36px; font-style: normal;
  color: var(--accent, #1e40af);
  opacity: 0.5;
}

.md-add-trail {
  align-self: flex-start;
  margin-top: 8px;
  padding: 5px 12px;
  background: transparent;
  border: 1px dashed color-mix(in srgb, var(--accent, #1e40af) 24%, transparent);
  border-radius: 999px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px; font-weight: 700; letter-spacing: 0.08em;
  color: #64748b;
  cursor: pointer;
  transition: all 0.18s ease;
}
.md-add-trail:hover {
  border-color: var(--accent, #1e40af);
  color: var(--accent, #1e40af);
  background: color-mix(in srgb, var(--accent, #1e40af) 6%, transparent);
}
</style>
