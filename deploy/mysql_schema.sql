-- MySQL 表结构还原脚本
-- 根据 app/models/standard/ 代码生成
-- 生成时间: 2026-04-17

SET NAMES utf8mb4;
SET
FOREIGN_KEY_CHECKS = 0;

-- ----------------------------
-- 1. standard_base_info 标准基础信息表
-- ----------------------------
CREATE TABLE IF NOT EXISTS `standard_base_info`
(
    `id`
    VARCHAR
(
    64
) NOT NULL COMMENT 'MD5主键',
    `standard_no` VARCHAR
(
    200
) DEFAULT NULL COMMENT '标准编号',
    `cname` TEXT DEFAULT NULL COMMENT '标准名称',
    `ename` TEXT DEFAULT NULL COMMENT '英文名称',
    `use_range` TEXT DEFAULT NULL COMMENT '适用范围',
    `intl_cat` VARCHAR
(
    200
) DEFAULT NULL COMMENT '国际分类号',
    `nat_cat` VARCHAR
(
    200
) DEFAULT NULL COMMENT '国内分类号',
    `std_domain` VARCHAR
(
    200
) DEFAULT NULL,
    `std_field` VARCHAR
(
    200
) DEFAULT NULL,
    `std_year` VARCHAR
(
    10
) DEFAULT NULL,
    `std_obj` TEXT DEFAULT NULL,
    `issue_date` DATE DEFAULT NULL COMMENT '发布日期',
    `act_date` DATE DEFAULT NULL COMMENT '实施日期',
    `annul_date` DATE DEFAULT NULL COMMENT '废止日期',
    `approval_unit` TEXT DEFAULT NULL COMMENT '批准单位',
    `put_unit` TEXT DEFAULT NULL COMMENT '发布单位',
    `lead_unit` TEXT DEFAULT NULL COMMENT '归口单位',
    `draft_unit_main` TEXT DEFAULT NULL COMMENT '主要起草单位',
    `draft_unit` TEXT DEFAULT NULL COMMENT '起草单位',
    `draft_staff` TEXT DEFAULT NULL COMMENT '起草人',
    `chief_unit` TEXT DEFAULT NULL COMMENT '主管部门',
    `mgr_dept` TEXT DEFAULT NULL COMMENT '管理部门',
    `is_secret` TINYINT
(
    1
) DEFAULT NULL COMMENT '是否保密',
    `std_nature` VARCHAR
(
    50
) DEFAULT NULL COMMENT '标准性质',
    `mandatory_clause` TEXT DEFAULT NULL COMMENT '强制性条款',
    `patent_info` TEXT DEFAULT NULL COMMENT '专利信息',
    `state` VARCHAR
(
    50
) DEFAULT NULL COMMENT '状态',
    `security_level` VARCHAR
(
    50
) DEFAULT NULL COMMENT '密级',
    `release_history` TEXT DEFAULT NULL COMMENT '发布历史',
    `release_std_no` VARCHAR
(
    200
) DEFAULT NULL COMMENT '发布标准号',
    `replace_description` TEXT DEFAULT NULL COMMENT '替代说明',
    `replace_stds` TEXT DEFAULT NULL COMMENT '替代标准',
    `target_stds` TEXT DEFAULT NULL COMMENT '被替代标准',
    `adopt_situation` TEXT DEFAULT NULL COMMENT '采标情况',
    `adopt_std_no` VARCHAR
(
    200
) DEFAULT NULL COMMENT '采标编号',
    `adopt_text` TEXT DEFAULT NULL COMMENT '采标文本',
    `adopt_level` VARCHAR
(
    50
) DEFAULT NULL COMMENT '采标程度',
    `adopt_type` VARCHAR
(
    50
) DEFAULT NULL COMMENT '采标类型',
    `adopt_no` VARCHAR
(
    200
) DEFAULT NULL COMMENT '采标号',
    `adopt_name` TEXT DEFAULT NULL COMMENT '采标名称',
    `gjb_no` VARCHAR
(
    200
) DEFAULT NULL COMMENT 'GJB编号',
    `std_type` VARCHAR
(
    50
) DEFAULT NULL COMMENT '标准类型',
    `industry` VARCHAR
(
    200
) DEFAULT NULL COMMENT '行业',
    `remark` TEXT DEFAULT NULL COMMENT '备注',
    `creator` VARCHAR
(
    64
) DEFAULT NULL,
    `updater` VARCHAR
(
    64
) DEFAULT NULL,
    `deleted` TINYINT
(
    1
) NOT NULL DEFAULT 0 COMMENT '软删除',
    `create_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
),
    `update_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
) ON UPDATE CURRENT_TIMESTAMP
(
    6
),
    PRIMARY KEY
(
    `id`
)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标准基础信息表';

-- ----------------------------
-- 2. standard_jgh_pdf 标准PDF基础信息表
-- ----------------------------
CREATE TABLE IF NOT EXISTS `standard_jgh_pdf`
(
    `id`
    BIGINT
    NOT
    NULL
    COMMENT
    '主键',
    `main_task_id`
    INT
    DEFAULT
    NULL
    COMMENT
    '主任务ID',
    `file_uuid`
    VARCHAR
(
    64
) DEFAULT NULL COMMENT '文件MD5',
    `standard_no` VARCHAR
(
    255
) DEFAULT NULL COMMENT '标准编号',
    `cname` TEXT DEFAULT NULL COMMENT '标准名称',
    PRIMARY KEY
(
    `id`
)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标准PDF基础信息表';

-- ----------------------------
-- 3. standard_jgh_pdf_chapter 标准PDF章节表
-- ----------------------------
CREATE TABLE IF NOT EXISTS `standard_jgh_pdf_chapter`
(
    `id`
    BIGINT
    NOT
    NULL
    COMMENT
    '主键',
    `main_task_id`
    INT
    DEFAULT
    NULL
    COMMENT
    '主任务ID',
    `title`
    VARCHAR
(
    500
) DEFAULT NULL COMMENT '章节标题',
    `title_no` VARCHAR
(
    100
) DEFAULT NULL COMMENT '章节编号',
    `page` INT DEFAULT NULL COMMENT '页码',
    `word` TEXT DEFAULT NULL COMMENT '章节文本内容',
    PRIMARY KEY
(
    `id`
),
    INDEX `idx_main_task_id`
(
    `main_task_id`
)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标准PDF章节表';

-- ----------------------------
-- 4. standard_cache_ind 标准指标提取缓存表
-- ----------------------------
CREATE TABLE IF NOT EXISTS `standard_cache_ind`
(
    `id`
    INT
    NOT
    NULL
    AUTO_INCREMENT,
    `standard_no`
    VARCHAR
(
    100
) NOT NULL COMMENT '标准编号',
    `standard_name` VARCHAR
(
    500
) DEFAULT NULL COMMENT '标准名称',
    `run_id` VARCHAR
(
    64
) DEFAULT NULL COMMENT '提取批次ID（同一次提取共享）',
    `run_remark` VARCHAR
(
    500
) DEFAULT NULL COMMENT '提取备注（如模型名称、实验说明等）',
    `standard_structure_type` VARCHAR
(
    30
) DEFAULT NULL COMMENT '标准性质：has_ind_and_test/has_ind_only/has_test_only/ind_embedded_in_test/not_extractable',
    `indicator_type` VARCHAR
(
    20
) NOT NULL COMMENT 'static | dynamic（由 norm_class 推导）',
    `standard_object` VARCHAR
(
    200
) DEFAULT NULL COMMENT '标准对象',
    `applicable_object` VARCHAR
(
    200
) DEFAULT NULL COMMENT '适用对象',
    `object_type` VARCHAR
(
    50
) DEFAULT NULL COMMENT '标准化对象类型：产品类对象/服务类对象/过程类对象',
    `indicator_category` VARCHAR
(
    50
) DEFAULT NULL COMMENT '指标类别',
    `norm_class` VARCHAR
(
    20
) DEFAULT NULL COMMENT '规范类别',
    `indicator_object` VARCHAR
(
    200
) DEFAULT NULL COMMENT '[静态]指标对象',
    `source_value` TEXT DEFAULT NULL COMMENT '[静态]指标值',
    `experiment_name` VARCHAR
(
    200
) DEFAULT NULL COMMENT '[动态]试验名称',
    `source_input_params` TEXT DEFAULT NULL COMMENT '[动态]输入参数',
    `source_process_logic` TEXT DEFAULT NULL COMMENT '[动态]过程逻辑',
    `source_result` TEXT DEFAULT NULL COMMENT '[动态]结果',
    `source_clause` VARCHAR
(
    200
) DEFAULT NULL COMMENT '来源条款',
    `extraction_time` FLOAT DEFAULT NULL COMMENT '提取耗时(秒)',
    `is_valid` TINYINT
(
    1
) NOT NULL DEFAULT 1 COMMENT '缓存是否有效',
    `algorithm_version` VARCHAR
(
    50
) NOT NULL DEFAULT 'v1' COMMENT '算法版本',
    `create_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
),
    `update_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
) ON UPDATE CURRENT_TIMESTAMP
(
    6
),
    PRIMARY KEY
(
    `id`
),
    INDEX `idx_standard_no`
(
    `standard_no`
),
    INDEX `idx_is_valid`
(
    `is_valid`
)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标准指标提取缓存表';

-- ----------------------------
-- 5. standard_cache_ai AI智能比对缓存表
-- ----------------------------
CREATE TABLE IF NOT EXISTS `standard_cache_ai`
(
    `id`
    INT
    NOT
    NULL
    AUTO_INCREMENT,
    `source_standard_no`
    VARCHAR
(
    100
) NOT NULL COMMENT '源标准编号',
    `target_standard_no` VARCHAR
(
    100
) NOT NULL COMMENT '目标标准编号',
    `source_ind_id` INT DEFAULT NULL COMMENT '源指标ID',
    `target_ind_id` INT DEFAULT NULL COMMENT '目标指标ID',
    `comparison_type` VARCHAR
(
    20
) NOT NULL COMMENT 'matched | source_only | target_only',
    `change_analysis` TEXT DEFAULT NULL COMMENT '变化分析',
    `is_valid` TINYINT
(
    1
) NOT NULL DEFAULT 1 COMMENT '缓存是否有效',
    `algorithm_version` VARCHAR
(
    50
) NOT NULL DEFAULT 'v3_agent' COMMENT '算法版本',
    `create_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
),
    `update_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
) ON UPDATE CURRENT_TIMESTAMP
(
    6
),
    PRIMARY KEY
(
    `id`
),
    INDEX `idx_source_target`
(
    `source_standard_no`,
    `target_standard_no`
),
    INDEX `idx_is_valid`
(
    `is_valid`
)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI智能比对缓存表';

-- ----------------------------
-- 6. standard_cache_ai_html AI比对结果HTML缓存表
-- ----------------------------
CREATE TABLE IF NOT EXISTS `standard_cache_ai_html`
(
    `id`
    INT
    NOT
    NULL
    AUTO_INCREMENT,
    `source_standard_no`
    VARCHAR
(
    100
) NOT NULL COMMENT '源标准编号',
    `target_standard_no` VARCHAR
(
    100
) NOT NULL COMMENT '目标标准编号',
    `html_content` TEXT NOT NULL COMMENT '大模型生成的匹配指标HTML片段',
    `relationship` VARCHAR
(
    100
) DEFAULT NULL COMMENT '标准关系',
    `overall_assessment` TEXT DEFAULT NULL COMMENT '综合评价',
    `calculation_time` FLOAT DEFAULT NULL COMMENT '比对耗时(秒)',
    `generation_time` FLOAT DEFAULT NULL COMMENT 'HTML生成耗时(秒)',
    `is_valid` TINYINT
(
    1
) NOT NULL DEFAULT 1 COMMENT '缓存是否有效',
    `algorithm_version` VARCHAR
(
    50
) NOT NULL DEFAULT 'v3_html' COMMENT '算法版本',
    `create_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
),
    `update_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
) ON UPDATE CURRENT_TIMESTAMP
(
    6
),
    PRIMARY KEY
(
    `id`
),
    INDEX `idx_source_target`
(
    `source_standard_no`,
    `target_standard_no`
),
    INDEX `idx_is_valid`
(
    `is_valid`
),
    INDEX `idx_create_time`
(
    `create_time`
)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI比对结果HTML展示缓存表';

-- ----------------------------
-- 7. standard_cache_sim 全文相似度计算缓存表
-- ----------------------------
CREATE TABLE IF NOT EXISTS `standard_cache_sim`
(
    `id`
    INT
    NOT
    NULL
    AUTO_INCREMENT,
    `source_standard_no`
    VARCHAR
(
    100
) NOT NULL COMMENT '源标准编号',
    `source_standard_name` VARCHAR
(
    500
) DEFAULT NULL COMMENT '源标准名称',
    `target_standard_no` VARCHAR
(
    100
) NOT NULL COMMENT '目标标准编号',
    `target_standard_name` VARCHAR
(
    500
) DEFAULT NULL COMMENT '目标标准名称',
    `similarity_percentage` FLOAT NOT NULL COMMENT '相似度百分比',
    `matched_sentence_count` INT NOT NULL COMMENT '匹配句子数',
    `source_total_sentence_count` INT NOT NULL COMMENT '源标准总句子数',
    `target_total_sentence_count` INT NOT NULL COMMENT '目标标准总句子数',
    `matches` JSON NOT NULL COMMENT '匹配详情列表',
    `calculation_time` FLOAT DEFAULT NULL COMMENT '计算耗时(秒)',
    `threshold` FLOAT NOT NULL DEFAULT 0.70 COMMENT '相似度阈值',
    `algorithm_version` VARCHAR
(
    50
) NOT NULL DEFAULT 'ngram_v1' COMMENT '算法版本',
    `is_valid` TINYINT
(
    1
) NOT NULL DEFAULT 1 COMMENT '缓存是否有效',
    `expire_time` DATETIME DEFAULT NULL COMMENT '过期时间',
    `create_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
),
    `update_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
) ON UPDATE CURRENT_TIMESTAMP
(
    6
),
    PRIMARY KEY
(
    `id`
),
    UNIQUE KEY `uq_source_target_valid`
(
    `source_standard_no`,
    `target_standard_no`,
    `is_valid`
),
    INDEX `idx_create_time`
(
    `create_time`
),
    INDEX `idx_is_valid`
(
    `is_valid`
)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='全文相似度计算缓存表';

-- ----------------------------
-- 8. standard_duplicate_batch 批量查重批次表
-- ----------------------------
CREATE TABLE IF NOT EXISTS `standard_duplicate_batch`
(
    `id`
    INT
    NOT
    NULL
    AUTO_INCREMENT,
    `batch_name`
    VARCHAR
(
    200
) DEFAULT NULL COMMENT '批次名称',
    `total_count` INT NOT NULL DEFAULT 0 COMMENT '总数',
    `success_count` INT NOT NULL DEFAULT 0 COMMENT '成功数',
    `failed_count` INT NOT NULL DEFAULT 0 COMMENT '失败数',
    `duplicate_count` INT NOT NULL DEFAULT 0 COMMENT '高相似数',
    `status` VARCHAR
(
    20
) NOT NULL DEFAULT 'completed' COMMENT 'processing|completed|failed',
    `remark` TEXT DEFAULT NULL COMMENT '备注',
    `pool_id` INT DEFAULT NULL COMMENT '-1=全库, null=未指定, 正整数=具体池',
    `create_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
),
    `update_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
) ON UPDATE CURRENT_TIMESTAMP
(
    6
),
    PRIMARY KEY
(
    `id`
)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='批量标准查重批次表';

-- ----------------------------
-- 9. standard_duplicate_name 查重结果记录表
-- ----------------------------
CREATE TABLE IF NOT EXISTS `standard_duplicate_name`
(
    `id`
    INT
    NOT
    NULL
    AUTO_INCREMENT,
    `batch_id`
    INT
    NOT
    NULL
    COMMENT
    '关联批次ID',
    `standard_no`
    VARCHAR
(
    200
) NOT NULL COMMENT '标准编号',
    `standard_name` VARCHAR
(
    500
) DEFAULT NULL COMMENT '标准名称',
    `use_range` TEXT DEFAULT NULL COMMENT '适用范围',
    `found` TINYINT
(
    1
) NOT NULL DEFAULT 0 COMMENT '是否找到相似标准',
    `error` VARCHAR
(
    500
) DEFAULT NULL COMMENT '错误信息',
    `need_attention` TINYINT
(
    1
) NOT NULL DEFAULT 0 COMMENT '是否需要关注',
    `general_evaluation` TEXT DEFAULT NULL COMMENT '综合评价',
    `suggestion` TEXT DEFAULT NULL COMMENT '建议',
    `similar_standards` JSON DEFAULT NULL COMMENT '相似标准列表',
    `create_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
),
    `update_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
) ON UPDATE CURRENT_TIMESTAMP
(
    6
),
    PRIMARY KEY
(
    `id`
),
    INDEX `idx_batch_id`
(
    `batch_id`
)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标准查重结果记录表';

-- ----------------------------
-- 10. standard_record_batch 批量查重记录表（旧版）
-- ----------------------------
CREATE TABLE IF NOT EXISTS `standard_record_batch`
(
    `id`
    INT
    NOT
    NULL
    AUTO_INCREMENT,
    `batch_name`
    VARCHAR
(
    200
) DEFAULT NULL COMMENT '批次名称',
    `input_standard_nos` JSON NOT NULL COMMENT '输入的标准编号列表',
    `total_count` INT NOT NULL DEFAULT 0 COMMENT '总数',
    `success_count` INT NOT NULL DEFAULT 0 COMMENT '成功数',
    `failed_count` INT NOT NULL DEFAULT 0 COMMENT '失败数',
    `duplicate_count` INT NOT NULL DEFAULT 0 COMMENT '高相似数',
    `results` JSON DEFAULT NULL COMMENT '查重结果详情',
    `status` VARCHAR
(
    20
) NOT NULL DEFAULT 'completed' COMMENT 'processing|completed|failed',
    `remark` TEXT DEFAULT NULL COMMENT '备注',
    `create_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
),
    `update_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
) ON UPDATE CURRENT_TIMESTAMP
(
    6
),
    PRIMARY KEY
(
    `id`
),
    INDEX `idx_create_time`
(
    `create_time`
),
    INDEX `idx_status`
(
    `status`
),
    INDEX `idx_total_count`
(
    `total_count`
)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='批量标准查重记录表（旧版）';

-- ----------------------------
-- 11. standard_pool_check 标准池表
-- ----------------------------
CREATE TABLE IF NOT EXISTS `standard_pool_check`
(
    `id`
    INT
    NOT
    NULL
    AUTO_INCREMENT,
    `pool_name`
    VARCHAR
(
    200
) NOT NULL COMMENT '池名称',
    `description` TEXT DEFAULT NULL COMMENT '描述',
    `standard_nos` JSON NOT NULL COMMENT '标准编号列表',
    `standard_count` INT NOT NULL DEFAULT 0 COMMENT '标准数量',
    `is_default` TINYINT
(
    1
) NOT NULL DEFAULT 0 COMMENT '是否全库标志',
    `is_active` TINYINT
(
    1
) NOT NULL DEFAULT 1 COMMENT '是否启用',
    `create_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
),
    `update_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
) ON UPDATE CURRENT_TIMESTAMP
(
    6
),
    PRIMARY KEY
(
    `id`
)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标准池表';

-- ----------------------------
-- 12. standard_obj_rel 标准化对象关系表
-- ----------------------------
CREATE TABLE IF NOT EXISTS `standard_obj_rel`
(
    `id`
    BIGINT
    NOT
    NULL
    AUTO_INCREMENT,
    `subj_obj`
    VARCHAR
(
    200
) NOT NULL COMMENT '主体对象',
    `rel_type` VARCHAR
(
    50
) NOT NULL COMMENT '包含|属于|细分|归属|配套',
    `obj_obj` VARCHAR
(
    200
) NOT NULL COMMENT '客体对象',
    `rel_desc` VARCHAR
(
    500
) DEFAULT NULL COMMENT '关系描述',
    `src_type` VARCHAR
(
    20
) NOT NULL DEFAULT 'agent' COMMENT 'agent|seed',
    `src_standard_no` VARCHAR
(
    100
) NOT NULL COMMENT '来源标准编号',
    `src_clause_id` BIGINT DEFAULT NULL COMMENT '来源章节ID',
    `confidence` VARCHAR
(
    10
) NOT NULL DEFAULT 'high' COMMENT 'high|medium|low',
    `deleted` TINYINT
(
    1
) NOT NULL DEFAULT 0 COMMENT '软删除',
    `create_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
),
    `update_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
) ON UPDATE CURRENT_TIMESTAMP
(
    6
),
    PRIMARY KEY
(
    `id`
),
    INDEX `idx_subj_obj`
(
    `subj_obj`
),
    INDEX `idx_obj_obj`
(
    `obj_obj`
),
    INDEX `idx_src_standard_no`
(
    `src_standard_no`
)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标准化对象关系表';

-- ----------------------------
-- 13. standard_cache_test 标准试验提取缓存表
-- ----------------------------
CREATE TABLE IF NOT EXISTS `standard_cache_test`
(
    `id`
    INT
    NOT
    NULL
    AUTO_INCREMENT,
    `standard_no`
    VARCHAR
(
    100
) NOT NULL COMMENT '标准编号',
    `standard_name` VARCHAR
(
    500
) DEFAULT NULL COMMENT '标准名称',
    `run_id` VARCHAR
(
    64
) DEFAULT NULL COMMENT '与 standard_cache_ind 共享同一 run_id',
    `run_remark` VARCHAR
(
    500
) DEFAULT NULL COMMENT '提取备注',
    `test_name` VARCHAR
(
    200
) NOT NULL COMMENT '试验名称（不含"试验"后缀）',
    `method_desc` TEXT DEFAULT NULL COMMENT '试验方法概述（试样制备、计算方法等）',
    `conditions` TEXT DEFAULT NULL COMMENT '试验条件（温度、湿度、电压等环境参数）',
    `preparation` TEXT DEFAULT NULL COMMENT '试验准备（试样处理、仪器校准、系统搭建）',
    `procedure` TEXT DEFAULT NULL COMMENT '试验过程（按逻辑次序的操作步骤）',
    `acceptance` TEXT DEFAULT NULL COMMENT '合格判据（为空表示纯方法类，无判定准则）',
    `report_items` TEXT DEFAULT NULL COMMENT '报告要点（主要用于纯试验方法类标准）',
    `source_clause` VARCHAR
(
    200
) DEFAULT NULL COMMENT '来源条款',
    `standard_object` VARCHAR
(
    200
) DEFAULT NULL COMMENT '标准化对象',
    `applicable_object` VARCHAR
(
    200
) DEFAULT NULL COMMENT '适用对象',
    `object_type` VARCHAR
(
    50
) DEFAULT NULL COMMENT '标准化对象类型',
    `indicator_category` VARCHAR
(
    50
) DEFAULT NULL COMMENT '指标类别',
    `is_valid` TINYINT
(
    1
) NOT NULL DEFAULT 1 COMMENT '缓存是否有效',
    `algorithm_version` VARCHAR
(
    50
) NOT NULL DEFAULT 'v3' COMMENT '算法版本',
    `create_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
),
    `update_time` DATETIME
(
    6
) NOT NULL DEFAULT CURRENT_TIMESTAMP
(
    6
) ON UPDATE CURRENT_TIMESTAMP
(
    6
),
    PRIMARY KEY
(
    `id`
),
    INDEX `idx_standard_no`
(
    `standard_no`
),
    INDEX `idx_run_id`
(
    `run_id`
),
    INDEX `idx_is_valid`
(
    `is_valid`
)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标准试验提取缓存表';

-- ----------------------------
-- 14. standard_cache_ind_test_rel 指标-试验关联表
-- ----------------------------
CREATE TABLE IF NOT EXISTS `standard_cache_ind_test_rel`
(
    `id`
    INT
    NOT
    NULL
    AUTO_INCREMENT,
    `ind_id`
    INT
    NOT
    NULL
    COMMENT
    '关联 standard_cache_ind.id',
    `test_id`
    INT
    NOT
    NULL
    COMMENT
    '关联 standard_cache_test.id',
    `run_id`
    VARCHAR
(
    64
) DEFAULT NULL COMMENT '所属提取批次',
    PRIMARY KEY
(
    `id`
),
    INDEX `idx_ind_id`
(
    `ind_id`
),
    INDEX `idx_test_id`
(
    `test_id`
)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='指标-试验关联表';

SET
FOREIGN_KEY_CHECKS = 1;
