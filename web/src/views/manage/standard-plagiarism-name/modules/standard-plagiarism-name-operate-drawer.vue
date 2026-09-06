<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { useFormRules, useNaiveForm } from '@/hooks/common/form';
import { fetchAddStandardPlagiarismName, fetchUpdateStandardPlagiarismName } from '@/service/api';

defineOptions({
  name: 'StandardPlagiarismNameOperateDrawer'
});

interface Props {
  /** the type of operation */
  operateType: NaiveUI.TableOperateType;
  /** the edit row data */
  rowData?: Api.SystemManage.StandardPlagiarismName | null;
}

const props = defineProps<Props>();

interface Emits {
  (e: 'submitted'): void;
}

const emit = defineEmits<Emits>();

const visible = defineModel<boolean>('visible', {
  default: false
});

const { formRef, validate, restoreValidation } = useNaiveForm();
const { defaultRequiredRule } = useFormRules();

const title = computed(() => {
  const titles: Record<NaiveUI.TableOperateType, string> = {
    add: '新增标准查重',
    edit: '编辑标准查重'
  };
  return titles[props.operateType];
});

const model: Api.SystemManage.StandardPlagiarismNameUpdateParams = reactive(createDefaultModel());

function createDefaultModel(): Api.SystemManage.StandardPlagiarismNameUpdateParams {
  return {
    standardCode: '',
    standardName: '',
    standardType: '',
    mandatoryType: '',
    standardCategory: '',
    issuingDepartment: '',
    industry: '',
    organization: '',
    draftingUnit: '',
    standardStatus: '',
    releaseTime: '',
    releaseYear: null,
    implementationTime: '',
    industry1: '',
    industry2: '',
    industryLabel: '',
    industryLabelReason: '',
    industryLabelResult: '',
    marketLabel: '',
    marketLabelReason: '',
    marketLabelResult: '',
    standardTheme: '',
    keywords: '',
    relatedStandardCode: '',
    relatedStandardName: '',
    standardTitleScore: null,
    standardTitleReason: '',
    scopeScore: null,
    scopeReason: '',
    specialtyScore: null,
    specialtyReason: '',
    standardContentScore: null,
    standardContentReason: '',
    similarityScore: null,
    repeatAnalysis: '',
    similarityMatchScore: null,
    similarityLevelScore: null,
    sourceFile: '',
    importTime: ''
  };
}

type RuleKey = Extract<keyof Api.SystemManage.StandardPlagiarismNameUpdateParams, 'standardCode' | 'standardName'>;

const rules: Record<RuleKey, App.Global.FormRule> = {
  standardCode: defaultRequiredRule,
  standardName: defaultRequiredRule
};

function handleInitModel() {
  Object.assign(model, createDefaultModel());

  if (props.operateType === 'edit' && props.rowData) {
    Object.assign(model, props.rowData);
  }
}

function closeDrawer() {
  visible.value = false;
}

async function handleSubmit() {
  await validate();

  if (props.operateType === 'add') {
    const { error } = await fetchAddStandardPlagiarismName(model);
    if (!error) {
      window.$message?.success('添加成功');
    }
  } else if (props.operateType === 'edit') {
    const { error } = await fetchUpdateStandardPlagiarismName(model);
    if (!error) {
      window.$message?.success('更新成功');
    }
  }

  closeDrawer();
  emit('submitted');
}

watch(visible, () => {
  if (visible.value) {
    handleInitModel();
    restoreValidation();
  }
});
</script>

<template>
  <NDrawer v-model:show="visible" :title="title" display-directive="show" :width="800">
    <NDrawerContent :title="title" :native-scrollbar="false" closable>
      <NForm
        ref="formRef"
        :model="model"
        :rules="rules"
        label-placement="left"
        label-width="120"
        require-mark-placement="right-hanging"
      >
        <NTabs type="line" animated>
          <NTabPane name="basic" tab="基本信息">
            <NFlex vertical>
              <NFormItem label="标准号" path="standardCode">
                <NInput v-model:value="model.standardCode" placeholder="请输入标准号" />
              </NFormItem>
              <NFormItem label="标准名称" path="standardName">
                <NInput
                  v-model:value="model.standardName"
                  type="textarea"
                  placeholder="请输入标准名称"
                  :autosize="{ minRows: 2, maxRows: 4 }"
                />
              </NFormItem>
              <NFormItem label="标准类型" path="standardType">
                <NInput v-model:value="model.standardType" placeholder="请输入标准类型" />
              </NFormItem>
              <NFormItem label="强制性" path="mandatoryType">
                <NInput v-model:value="model.mandatoryType" placeholder="请输入强制性" />
              </NFormItem>
              <NFormItem label="标准类别" path="standardCategory">
                <NInput v-model:value="model.standardCategory" placeholder="请输入标准类别" />
              </NFormItem>
            </NFlex>
          </NTabPane>

          <NTabPane name="organization" tab="组织信息">
            <NFlex vertical>
              <NFormItem label="归口司局" path="issuingDepartment">
                <NInput v-model:value="model.issuingDepartment" placeholder="请输入归口司局" />
              </NFormItem>
              <NFormItem label="行业" path="industry">
                <NInput v-model:value="model.industry" placeholder="请输入行业" />
              </NFormItem>
              <NFormItem label="归口单位" path="organization">
                <NInput
                  v-model:value="model.organization"
                  type="textarea"
                  placeholder="请输入归口单位"
                  :autosize="{ minRows: 2, maxRows: 4 }"
                />
              </NFormItem>
              <NFormItem label="起草单位" path="draftingUnit">
                <NInput
                  v-model:value="model.draftingUnit"
                  type="textarea"
                  placeholder="请输入起草单位"
                  :autosize="{ minRows: 2, maxRows: 4 }"
                />
              </NFormItem>
            </NFlex>
          </NTabPane>

          <NTabPane name="time" tab="时间信息">
            <NFlex vertical>
              <NFormItem label="标准状态" path="standardStatus">
                <NInput v-model:value="model.standardStatus" placeholder="请输入标准状态" />
              </NFormItem>
              <NFormItem label="发布时间" path="releaseTime">
                <NInput v-model:value="model.releaseTime" placeholder="请输入发布时间" />
              </NFormItem>
              <NFormItem label="发布年份" path="releaseYear">
                <NInputNumber v-model:value="model.releaseYear" placeholder="请输入发布年份" clearable class="w-full" />
              </NFormItem>
              <NFormItem label="实施时间" path="implementationTime">
                <NInput v-model:value="model.implementationTime" placeholder="请输入实施时间" />
              </NFormItem>
            </NFlex>
          </NTabPane>

          <NTabPane name="label" tab="标签信息">
            <NFlex vertical>
              <NFormItem label="产业和行业标签" path="industryLabel">
                <NInput
                  v-model:value="model.industryLabel"
                  type="textarea"
                  placeholder="请输入产业和行业标签"
                  :autosize="{ minRows: 2, maxRows: 4 }"
                />
              </NFormItem>
              <NFormItem label="标签结果" path="industryLabelResult">
                <NInput v-model:value="model.industryLabelResult" placeholder="请输入标签结果" />
              </NFormItem>
              <NFormItem label="行业和市场标签" path="marketLabel">
                <NInput
                  v-model:value="model.marketLabel"
                  type="textarea"
                  placeholder="请输入行业和市场标签"
                  :autosize="{ minRows: 2, maxRows: 4 }"
                />
              </NFormItem>
              <NFormItem label="市场标签结果" path="marketLabelResult">
                <NInput v-model:value="model.marketLabelResult" placeholder="请输入市场标签结果" />
              </NFormItem>
            </NFlex>
          </NTabPane>

          <NTabPane name="content" tab="主题和关键词">
            <NFlex vertical>
              <NFormItem label="标准主题" path="standardTheme">
                <NInput
                  v-model:value="model.standardTheme"
                  type="textarea"
                  placeholder="请输入标准主题"
                  :autosize="{ minRows: 2, maxRows: 4 }"
                />
              </NFormItem>
              <NFormItem label="关键词" path="keywords">
                <NInput
                  v-model:value="model.keywords"
                  type="textarea"
                  placeholder="请输入关键词"
                  :autosize="{ minRows: 2, maxRows: 4 }"
                />
              </NFormItem>
              <NFormItem label="相关标准号" path="relatedStandardCode">
                <NInput v-model:value="model.relatedStandardCode" placeholder="请输入相关标准号" />
              </NFormItem>
              <NFormItem label="相关标准名称" path="relatedStandardName">
                <NInput
                  v-model:value="model.relatedStandardName"
                  type="textarea"
                  placeholder="请输入相关标准名称"
                  :autosize="{ minRows: 2, maxRows: 4 }"
                />
              </NFormItem>
            </NFlex>
          </NTabPane>

          <NTabPane name="score" tab="评分信息">
            <NFlex vertical>
              <NFormItem label="标准题录相似度" path="standardTitleScore">
                <NInputNumber
                  v-model:value="model.standardTitleScore"
                  placeholder="请输入分数"
                  clearable
                  class="w-full"
                  :min="0"
                  :max="100"
                />
              </NFormItem>
              <NFormItem label="适用范围相似" path="scopeScore">
                <NInputNumber
                  v-model:value="model.scopeScore"
                  placeholder="请输入分数"
                  clearable
                  class="w-full"
                  :min="0"
                  :max="100"
                />
              </NFormItem>
              <NFormItem label="专业领域相似" path="specialtyScore">
                <NInputNumber
                  v-model:value="model.specialtyScore"
                  placeholder="请输入分数"
                  clearable
                  class="w-full"
                  :min="0"
                  :max="100"
                />
              </NFormItem>
              <NFormItem label="标准内容相似" path="standardContentScore">
                <NInputNumber
                  v-model:value="model.standardContentScore"
                  placeholder="请输入分数"
                  clearable
                  class="w-full"
                  :min="0"
                  :max="100"
                />
              </NFormItem>
              <NFormItem label="相似度总分" path="similarityScore">
                <NInputNumber
                  v-model:value="model.similarityScore"
                  placeholder="请输入总分"
                  clearable
                  class="w-full"
                  :min="0"
                  :max="100"
                />
              </NFormItem>
            </NFlex>
          </NTabPane>

          <NTabPane name="analysis" tab="分析信息">
            <NFlex vertical>
              <NFormItem label="重复情况分析" path="repeatAnalysis">
                <NInput
                  v-model:value="model.repeatAnalysis"
                  type="textarea"
                  placeholder="请输入重复情况分析"
                  :autosize="{ minRows: 3, maxRows: 6 }"
                />
              </NFormItem>
              <NFormItem label="来源文件" path="sourceFile">
                <NInput v-model:value="model.sourceFile" placeholder="请输入来源文件" />
              </NFormItem>
            </NFlex>
          </NTabPane>
        </NTabs>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="closeDrawer">取消</NButton>
          <NButton type="primary" @click="handleSubmit">确认</NButton>
        </NSpace>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped></style>
