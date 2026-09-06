declare namespace Api {
  /**
   * namespace SystemManage
   *
   * backend api module: "systemManage"
   */
  namespace SystemManage {
    type CommonSearchParams = Pick<Common.PaginatingCommonParams, 'current' | 'size'>;

    /** common delete params */
    type CommonDeleteParams = { id: number | string };

    /** common batch delete params */
    type CommonBatchDeleteParams = { ids: string[] };

    /** role */
    type Role = Common.CommonRecord<{
      /** role name */
      roleName: string;
      /** role code */
      roleCode: string;
      /** role description */
      roleDesc: string;
      /** role home */
      byRoleHomeId: number;
    }>;

    /** role add params */
    type RoleAddParams = Pick<
      Api.SystemManage.Role,
      'roleName' | 'roleCode' | 'roleDesc' | 'byRoleHomeId' | 'statusType'
    >;

    /** role update params */
    type RoleUpdateParams = CommonType.RecordNullable<Pick<Api.SystemManage.Role, 'id'>> & RoleAddParams;

    /** role search params */
    type RoleSearchParams = CommonType.RecordNullable<
      Pick<Api.SystemManage.Role, 'roleName' | 'roleCode' | 'statusType'> & CommonSearchParams
    >;

    /** role list */
    type RoleList = Common.PaginatingQueryRecord<Role>;

    /** role authorized */
    type RoleAuthorized = Api.SystemManage.Role & {
      byRoleMenuIds: number[];
      byRoleApiIds: number[];
      byRoleButtonIds: number[];
    };

    /** get role authorized params */
    type RoleAuthorizedParams = Pick<Api.SystemManage.RoleAuthorized, 'id'>;

    /** role authorized list */
    type RoleAuthorizedList = CommonType.RecordNullable<RoleAuthorized>;

    /** all role */
    type AllRole = Pick<Role, 'id' | 'roleName' | 'roleCode'>;

    /**
     * api method
     *
     * - "1": "GET"
     * - "2": "POST"
     * - "3": "PUT"
     * - "4": "PATCH"
     * - "5": "DELETE"
     */
    type methods = 'get' | 'post' | 'put' | 'patch' | 'delete';

    /** api */
    type Api = Common.CommonRecord<{
      /** api path */
      apiPath: string;
      /** api method */
      apiMethod: methods;
      /** api summary */
      summary: string;
      /** api tags name */
      tags: string[];
    }>;

    /** api add params */
    type ApiAddParams = Pick<Api.SystemManage.Api, 'apiPath' | 'apiMethod' | 'summary' | 'tags' | 'statusType'>;

    /** api update params */
    type ApiUpdateParams = CommonType.RecordNullable<Pick<Api.SystemManage.Api, 'id'>> & ApiAddParams;

    /** api search params */
    type ApiSearchParams = CommonType.RecordNullable<
      Pick<Api.SystemManage.Api, 'apiPath' | 'apiMethod' | 'summary' | 'tags' | 'statusType'> & CommonSearchParams
    >;

    /** api list */
    type ApiList = Common.PaginatingQueryRecord<Api>;

    /**
     * log type
     *
     * - "1": "ApiLog"
     * - "2": "UserLog"
     * - "3": "AdminLog"
     * - "4": "SystemLog"
     */
    type logTypes = '1' | '2' | '3' | '4';

    /**
     * api method
     *
     * - "1": "GET"
     * - "2": "POST"
     * - "3": "PUT"
     * - "4": "PATCH"
     * - "5": "DELETE"
     */
    type logDetailTypes =
      | '1101'
      | '1102'
      | '1201'
      | '1202'
      | '1203'
      | '1211'
      | '1212'
      | '1213'
      | '1301'
      | '1302'
      | '1303'
      | '1311'
      | '1312'
      | '1313'
      | '1314'
      | '1315'
      | '1401'
      | '1402'
      | '1403'
      | '1404'
      | '1411'
      | '1412'
      | '1413'
      | '1414'
      | '1415'
      | '1501'
      | '1502'
      | '1503'
      | '1504'
      | '1505'
      | '1506'
      | '1507'
      | '1511'
      | '1512'
      | '1513'
      | '1514'
      | '1515'
      | '1601'
      | '1611'
      | '1612'
      | '1613'
      | '1614'
      | '1615';

    /** log */
    type Log = Common.CommonRecord<{
      /** log type */
      logType: logTypes;
      /** log detail */
      logDetailType: logDetailTypes | null;
      /** create time */
      createTime: number;
      /** format create time */

      /** request domain */
      requestDomain: string;
      /** request path */
      requestPath: string;
      /** create time */
      responseCode: string;
      /** x-request-id */
      xRequestId: string;
      /** request params */
      requestParams: string;
      /** response data */
      responseData: string;
      /** user agent */
      userAgent: string;
      /** process time */
      processTime: string;
      /** ip address */
      ipAddress: string;

      /** by user id */
      byUser: string;
      /** user info */
      byUserInfo: User;
    }>;

    /** log add params */
    type LogAddParams = Pick<
      Api.SystemManage.Log,
      | 'logType'
      | 'logDetailType'
      | 'createTime'
      | 'byUser'
      | 'requestDomain'
      | 'requestPath'
      | 'responseCode'
      | 'xRequestId'
      | 'requestParams'
      | 'responseData'
      | 'userAgent'
      | 'processTime'
      | 'ipAddress'
    >;

    /** log update params */
    type LogUpdateParams = CommonType.RecordNullable<Pick<Api.SystemManage.Log, 'id'>> & Api.SystemManage.LogAddParams;

    /** log search params */
    type LogSearchParams = CommonType.RecordNullable<
      Pick<
        Api.SystemManage.Log,
        | 'logType'
        | 'logDetailType'
        | 'requestDomain'
        | 'requestPath'
        | 'createTime'
        | 'responseCode'
        | 'byUser'
        | 'xRequestId'
      > &
        CommonSearchParams & { timeRange: [number, number] }
    >;

    /** log list */
    type LogList = Common.PaginatingQueryRecord<Log>;

    /**
     * user gender
     *
     * - "1": "male"
     * - "2": "female"
     * - "3": "unknow"
     */
    type UserGender = '1' | '2' | '3';

    /** user */
    type User = Common.CommonRecord<{
      /** user name */
      userName: string;
      /** password */
      password: string;
      /** user gender */
      userGender: UserGender | null;
      /** user nick name */
      nickName: string;
      /** user phone */
      userPhone: string;
      /** user email */
      userEmail: string;
      /** user role code collection */
      byUserRoleCodeList: string[];
    }>;

    /** user add params */
    type UserAddParams = Pick<
      Api.SystemManage.User,
      | 'userName'
      | 'password'
      | 'userGender'
      | 'nickName'
      | 'userPhone'
      | 'userEmail'
      | 'byUserRoleCodeList'
      | 'statusType'
    > & {
      /** 一次性密码加密密钥 ID（password 非空时必填，后端凭此解密密文） */
      keyId?: string | null;
    };

    /** user update params */
    type UserUpdateParams = CommonType.RecordNullable<Pick<Api.SystemManage.User, 'id'> & UserAddParams>;

    /** user search params */
    type UserSearchParams = CommonType.RecordNullable<
      Pick<
        Api.SystemManage.User,
        | 'userName'
        | 'password'
        | 'userGender'
        | 'nickName'
        | 'userPhone'
        | 'userEmail'
        | 'statusType'
        | 'byUserRoleCodeList'
      > &
        CommonSearchParams
    >;

    /** user list */
    type UserList = Common.PaginatingQueryRecord<User>;

    /**
     * menu type
     *
     * - "1": directory
     * - "2": menu
     */
    type MenuType = '1' | '2';

    type MenuButton = {
      /**
       * button code
       *
       * it can be used to control the button permission
       */
      buttonCode: string;
      /** button description */
      buttonDesc: string;
    };

    /**
     * icon type
     *
     * - "1": iconify icon
     * - "2": local icon
     */
    type IconType = '1' | '2';

    type MenuPropsOfRoute = Pick<
      import('vue-router').RouteMeta,
      | 'i18nKey'
      | 'keepAlive'
      | 'constant'
      | 'order'
      | 'href'
      | 'hideInMenu'
      | 'activeMenu'
      | 'multiTab'
      | 'fixedIndexInTab'
      | 'query'
    >;

    type Menu = Common.CommonRecord<{
      /** parent menu id */
      parentId: number;
      /** menu type */
      menuType: MenuType;
      /** menu name */
      menuName: string;
      /** route name */
      routeName: string;
      /** route path */
      routePath: string;
      /** component */
      component?: string;
      /** iconify icon name or local icon name */
      icon: string;
      /** icon type */
      iconType: IconType;
      /** buttons */
      buttons?: MenuButton[] | null;
      /** children menu */
      children?: Menu[] | null;
    }> &
      MenuPropsOfRoute;

    /** menu add params */
    // type MenuAddParams = Pick<
    //   Api.SystemManage.Menu,
    //   | 'parentId'
    //   | 'menuType'
    //   | 'menuName'
    //   | 'routeName'
    //   | 'routePath'
    //   | 'component'
    //   | 'icon'
    //   | 'iconType'
    //   | 'buttons'
    //   | 'children'
    // >;
    type MenuAddParams = Pick<
      Api.SystemManage.Menu,
      | 'menuType'
      | 'menuName'
      | 'routeName'
      | 'routePath'
      | 'component'
      | 'order'
      | 'i18nKey'
      | 'icon'
      | 'iconType'
      | 'statusType'
      | 'parentId'
      | 'keepAlive'
      | 'constant'
      | 'href'
      | 'hideInMenu'
      | 'activeMenu'
      | 'multiTab'
      | 'fixedIndexInTab'
    > & {
      query: NonNullable<Api.SystemManage.Menu['query']>;
      buttons: NonNullable<Api.SystemManage.Menu['buttons']>;
      layout: string;
      page: string;
      pathParam: string;
    };

    /** menu update params */
    type MenuUpdateParams = CommonType.RecordNullable<Pick<Api.SystemManage.Menu, 'id'>> & MenuAddParams;

    /** menu list */
    type MenuList = Common.PaginatingQueryRecord<Menu>;

    type MenuTree = {
      id: number;
      label: string;
      pId: number;
      children?: MenuTree[];
    };

    type ButtonTree = {
      id: number;
      label: string;
      pId: number;
      children?: ButtonTree[];
    };
  }

  /**
   * namespace AI
   *
   * backend api module: "ai"
   */
  namespace AI {
    /** 模型切换：单个类别的可选预设 + 当前选中块名 */
    interface ModelConfigCategory {
      options: { key: string; label: string }[];
      current: string;
    }

    /** 模型切换：四类模型（GET /ai/model-config 返回值） */
    interface ModelConfig {
      chat: ModelConfigCategory;
      image: ModelConfigCategory;
      video: ModelConfigCategory;
      /** 向量模型（切换后既有向量库 / 知识库标记陈旧，需重建） */
      embed: ModelConfigCategory;
    }

    /** 按角色模型配置：单个角色行（块字段三态：null=跟随全局 | 块名 | DISABLED） */
    interface RoleModelConfigRow {
      roleCode: string;
      roleName: string;
      chatBlockKey: string | null;
      imageBlockKey: string | null;
      videoBlockKey: string | null;
      chatBlockValid: boolean;
      imageBlockValid: boolean;
      videoBlockValid: boolean;
    }

    /** 按角色模型配置：GET /ai/role-model-config 返回值 */
    interface RoleModelConfigData {
      roles: RoleModelConfigRow[];
      chatOptions: { key: string; label: string }[];
      imageOptions: { key: string; label: string }[];
      videoOptions: { key: string; label: string }[];
      globalKeys: { chat: string; image: string; video: string };
    }

    /** 按角色模型配置：PUT /ai/role-model-config 入参 */
    interface RoleModelConfigUpdate {
      roleCode: string;
      chatBlockKey: string | null;
      imageBlockKey: string | null;
      videoBlockKey: string | null;
    }

    /** 对话模式：用户偏好里单个模式的元数据（blockLabel 供 composer 弹层小字） */
    interface ChatModeOption {
      key: string;
      label: string;
      /** 弹层小字说明（一句话描述适用场景） */
      note: string;
      /** 该模式指派的 chat 预设块（未配置/失效为 null，走兜底链） */
      blockKey: string | null;
      blockLabel: string | null;
      /** 块是否有效（false = 走兜底：角色配置→全局激活块） */
      effective: boolean;
      /** 该模式有效块的思考强度档位白名单（wire 值序 = 滑块序；空 = 不展示滑块） */
      levels: { key: string; label: string }[];
      /** 存量偏好平滑迁移到该模式有效块后的当前生效档（滑块初始位；无档位块为 null） */
      levelDefault: string | null;
    }

    /** 对话模式：GET /ai/chat-mode 返回值（当前用户偏好 + 模式清单元数据） */
    interface ChatModePref {
      mode: string;
      /** 当前模式生效档（wire 值 none/low/medium/high/max；无档位块为 null） */
      thinkingLevel: string | null;
      /** 流式输出时自动展开工具调用过程（false=一直收折，默认；true=输出时展开、完成后收折） */
      toolProcessExpand: boolean;
      modes: ChatModeOption[];
    }

    /** 对话模式：PUT /ai/chat-mode 入参（各字段均可省略，省略=不改该项） */
    interface ChatModePrefUpdate {
      mode?: string;
      thinkingLevel?: string;
      toolProcessExpand?: boolean;
    }

    /** 对话模式配置：超管视角单个模式行（GET /ai/chat-mode/config） */
    interface ChatModeConfigRow {
      key: string;
      label: string;
      note: string;
      sortOrder: number;
      chatBlockKey: string | null;
      blockValid: boolean;
    }

    /** 对话模式配置：GET /ai/chat-mode/config 返回值 */
    interface ChatModeConfigData {
      modes: ChatModeConfigRow[];
      chatOptions: { key: string; label: string; levels?: string[] }[];
      /** 全局激活 chat 块（兜底链末端，前端「跟随默认」展示用） */
      globalChatKey: string;
    }

    /** 对话模式配置：PUT /ai/chat-mode/config 入参（mode 定位行；其余省略=不改，chatBlockKey null=走兜底链） */
    interface ChatModeConfigUpdate {
      mode: string;
      label?: string;
      note?: string;
      chatBlockKey?: string | null;
      sortOrder?: number;
    }

    /** 对话模式配置：POST /ai/chat-mode/config 入参（key 省略自动生成 mode_{n}） */
    interface ChatModeConfigCreate {
      key?: string;
      label: string;
      note?: string;
      chatBlockKey?: string | null;
    }

    /** 向量库：派生状态（ready 由条目数派生，不存库） */
    type VectorLibState = 'building' | 'error' | 'stale' | 'ready' | 'empty';

    /** 向量库：注册行视图（GET /ai/vector-lib 列表项） */
    interface VectorLibRecord {
      libraryKey: string;
      name: string;
      description: string | null;
      scope: 'system' | 'user';
      sourceType: 'manual' | 'file' | 'table_sync' | 'system_sync';
      /** 同步源表名（表来源才有：table_sync 读建库配置，系统库为固定源表） */
      sourceTable: string | null;
      itemCount: number;
      /** 构建快照：构建时使用的向量模型块（与当前激活块不等即陈旧） */
      embedBlock: string | null;
      /** 构建快照块的显示名（block_key → label；未解析到回退块名） */
      embedBlockLabel: string | null;
      embedDim: number | null;
      state: VectorLibState;
      /** 快照陈旧判定（纯快照比较，独立于 state：error 态的库也可能陈旧） */
      isStale: boolean;
      lastError: string | null;
      lastBuiltAt: string | null;
      isBuilding: boolean;
      /** 当前用户对该库是否有写权限（前端控按钮） */
      canWrite: boolean;
      /** 用户库拥有者显示名（超管看他人库时标识归属；系统库/本人库前端不显示） */
      ownerName: string | null;
      /** 嵌入失败待重试条数（>0 时管理面提示） */
      failedCount: number;
      createTime: string | null;
    }

    /** 构建进度（GET /ai/vector-lib/{key}/progress） */
    interface VectorLibProgress {
      /** 已处理条数（嵌入/删除按阶段累计） */
      processed: number;
      /** 总条数；SQL 下推同步源工作集总量不可预知 → null（前端走不定长进度条） */
      total: number | null;
      /** preparing / diffing / embedding / deleting / merging */
      phase: string;
      startedAt: string | null;
    }

    /** 测试检索命中行（GET /ai/vector-lib/{key}/search） */
    interface VectorLibSearchHit {
      itemKey: string;
      refKey: string | null;
      payload: Record<string, unknown> | null;
      /** 1 - cosine 距离（越大越相似） */
      score: number;
      contentPreview: string | null;
    }

    /** 向量库列表：GET /ai/vector-lib 返回值 */
    interface VectorLibListData {
      records: VectorLibRecord[];
      /** 当前激活的向量模型块与维度（陈旧判定的参照系） */
      activeEmbedBlock: string | null;
      /** 当前激活向量模型块的显示名（block_key → label） */
      activeEmbedBlockLabel: string | null;
      activeEmbedDim: number | null;
    }

    /** 建库入参：POST /ai/vector-lib */
    interface VectorLibCreatePayload {
      name: string;
      description?: string | null;
      /** manual 手动条目 / file 文件上传 / table_sync 数据表同步（仅超管） */
      sourceType?: 'manual' | 'file' | 'table_sync';
      sourceConfig?: Record<string, unknown> | null;
    }

    /** 向量库条目：分页列表项（GET /ai/vector-lib/{key}/items；content 全文，前端预览截断） */
    interface VectorLibItemRecord {
      itemKey: string;
      content: string;
      payload: Record<string, unknown> | null;
      refKey: string | null;
      updatedAt: string | null;
    }

    /** 向量库条目分页返回值 */
    interface VectorLibItemPage {
      records: VectorLibItemRecord[];
      total: number;
      current: number;
      size: number;
    }

    /** 手动条目（POST /ai/vector-lib/{key}/items 的单项） */
    interface VectorLibItemAdd {
      itemKey?: string | null;
      content: string;
      payload?: Record<string, unknown> | null;
      refKey?: string | null;
    }

    /** 摄入计数（构建 / 加条目 / 上传摄入共用口径） */
    interface VectorLibIngestCounts {
      added?: number;
      reembedded?: number;
      payload_only?: number;
      unchanged?: number;
      deleted?: number;
      failed?: number;
      completed?: number;
    }

    /** 标准库同步：单表 diff 计数（GET /ai/std-sync/status） */
    interface StdSyncTableStats {
      added: number;
      updated: number;
      deleted: number;
      unchanged: number;
    }

    /** 标准库同步：实时进度（运行期有效，轮询用） */
    interface StdSyncProgress {
      /** 当前表名 */
      table: string;
      /** diff 指纹比对 / upsert 回写 / delete 清理 / done */
      phase: string;
      detail: string;
    }

    /** 标准库同步：状态（GET /ai/std-sync/status） */
    interface StdSyncStatus {
      running: boolean;
      progress: StdSyncProgress | null;
      /** 同步的表清单（顺序即执行顺序） */
      tables: Array<{ name: string; label: string }>;
      /** idle / running / done / error（状态表落库值） */
      status: string;
      /** manual / cron（最近一次触发方式） */
      trigger: string | null;
      startedAt: string | null;
      finishedAt: string | null;
      durationSec: number | null;
      stats: Record<string, StdSyncTableStats> | null;
      error: string | null;
      /** 源库连接信息（不含密码） */
      source: { host: string; port: number; database: string };
      /** 每日 02:30 定时同步是否启用 */
      dailyEnabled: boolean;
    }

    interface SimilarityTags {
      同系列标准: boolean;
      标准化对象一致: boolean;
      通用专用关系: boolean;
      适用范围重叠: boolean;
    }

    interface SimilarStandard {
      id: string;
      cname: string;
      use_range: string | null;
      standard_no: string | null;
      vector_score: number;
      tags: SimilarityTags;
      relation_desc: string;
      has_ai_comparison_cache?: boolean;
      cache_time?: string | null;
      has_res?: boolean;
    }

    interface DeduplicationResponse {
      success: boolean;
      result_id: number | null;
      need_attention: boolean;
      similar_standards: SimilarStandard[];
      error: string | null;
    }

    interface BatchDeduplicationItem {
      standard_no: string;
      standard_name: string | null;
      use_range: string | null;
      found: boolean;
      need_attention: boolean | null;
      similar_standards: SimilarStandard[] | null;
      error: string | null;
    }

    interface BatchDeduplicationResponse {
      success: boolean;
      batch_id: number | null;
      total: number;
      processed: number;
      results: BatchDeduplicationItem[];
    }

    interface BatchHistoryRecord {
      id: number;
      batch_name: string | null;
      create_time: string | null;
      update_time: string | null;
      total_count: number;
      success_count: number;
      failed_count: number;
      duplicate_count: number;
      status: string;
      pool_id: number | null;
      pool_name: string;
      remark: string | null;
      input_standard_nos: string[];
      progress?: {
        pending: number;
        running: number;
        done: number;
        failed: number;
      } | null;
    }

    interface BatchSummary {
      total_batches: number;
      recent_7d_batches: number;
      total_duplicate: number;
      total_analyzed: number;
    }

    interface BatchDetailResponse {
      record: {
        id: number;
        create_time: string | null;
        total_count: number;
        success_count: number;
        failed_count: number;
        duplicate_count: number;
        status: string;
        results: BatchDeduplicationItem[];
        remark: string | null;
      };
      pagination: {
        current: number;
        size: number;
        total: number;
        pages: number;
      };
    }

    interface FullTextSimilarityRequest {
      source_standard_no: string;
      target_standard_no: string;
    }

    interface MatchingBlock {
      text: string;
      source_start: number;
      source_end: number;
      target_start: number;
      target_end: number;
    }

    interface ChapterMatch {
      source_chapter_id: number;
      source_chapter_title: string;
      source_chapter_no: string;
      source_page: number | null;
      source_sentence_index: number;
      source_sentence_text: string;
      target_chapter_id: number;
      target_chapter_title: string;
      target_chapter_no: string;
      target_page: number | null;
      target_sentence_index: number;
      target_sentence_text: string;
      similarity: number;
      matching_blocks: MatchingBlock[];
    }

    interface FullTextSimilarityResponse {
      success: boolean;
      source_standard_no: string;
      source_standard_name: string;
      target_standard_no: string;
      target_standard_name: string;
      similarity_percentage: number;
      matched_sentence_count: number;
      source_total_sentence_count: number;
      target_total_sentence_count: number;
      matches: ChapterMatch[];
    }

    /** AI比对请求 */
    interface AIComparisonRequest {
      source_standard_no: string;
      target_standard_no: string;
      force_recalculate?: boolean;
    }

    /** 静态指标（直接规定值） */
    interface StaticIndicator {
      comparison_type: 'matched' | 'source_only' | 'target_only';
      indicator_object: string;
      source_value: string;
      source_clause: string;
      target_value: string;
      target_clause: string;
      change_analysis: string;
    }

    /** 动态指标（实验规定值） */
    interface DynamicIndicator {
      comparison_type: 'matched' | 'source_only' | 'target_only';
      experiment_name: string;
      source_input_params: string;
      source_process_logic: string;
      source_result: string;
      source_clause: string;
      target_input_params: string;
      target_process_logic: string;
      target_result: string;
      target_clause: string;
      change_analysis: string;
    }

    /** 匹配的指标对（旧版，保持兼容） */
    interface MatchedIndicator {
      category: string;
      name: string;
      source_requirement: string;
      target_requirement: string;
      change_analysis: string;
      source_chapter?: string;
      target_chapter?: string;
    }

    /** 独有指标（旧版，保持兼容） */
    interface UniqueIndicator {
      category: string;
      name: string;
      requirement: string;
      chapter_info?: string;
    }

    /** 指标分组（按 comparison_type 分） */
    interface IndicatorGroup<T> {
      matched: T[];
      source_only: T[];
      target_only: T[];
    }

    /** 独有指标（静态或动态通用） */
    interface UniqueIndicator {
      indicator_type: 'static' | 'dynamic';
      standard_object?: string;
      // 静态
      indicator_object?: string;
      source_value?: string;
      source_clause?: string;
      // 动态
      experiment_name?: string;
      source_input_params?: string;
      source_process_logic?: string;
      source_result?: string;
    }

    /** 全量指标（含 comparison_type，前端直接渲染） */
    interface FullIndicator {
      comparison_type: 'matched' | 'source_only' | 'target_only';
      source_indicator_type: string;
      target_indicator_type: string;
      standard_object?: string;
      applicable_object?: string;
      indicator_category?: string;
      change_analysis?: string;
      // 静态指标（source 侧）
      source_indicator_object?: string;
      source_value?: string;
      source_clause?: string;
      // 静态指标（target 侧）
      target_indicator_object?: string;
      target_value?: string;
      target_clause?: string;
      // 动态指标（source 侧）
      source_experiment_name?: string;
      source_input_params?: string;
      source_process_logic?: string;
      source_result?: string;
      // 动态指标（target 侧）
      target_experiment_name?: string;
      target_input_params?: string;
      target_process_logic?: string;
      target_result?: string;
    }

    /** AI比对试验明细条目 */
    interface ComparisonTestItem {
      test_name: string;
      method_desc?: string;
      conditions?: string;
      preparation?: string;
      procedure?: string;
      acceptance?: string;
      report_items?: string;
      source_clause?: string;
      standard_object?: string;
    }

    /** 元素统计（norm_class 分类） */
    interface ElementStats {
      total: number;
      source_test_count: number;
      target_test_count: number;
      by_norm_class: Record<string, number>;
    }

    /** AI比对响应 */
    interface AIComparisonResponse {
      success: boolean;
      from_cache?: boolean;
      source_standard_no: string;
      source_standard_name: string;
      target_standard_no: string;
      target_standard_name: string;
      matched_html?: string;
      all_indicators?: FullIndicator[];
      source_tests?: ComparisonTestItem[];
      target_tests?: ComparisonTestItem[];
      element_stats?: ElementStats;
      relationship?: string;
      overall_assessment?: string;
      calculation_time?: number;
      path_taken?: string;
      stats?: { matched: number; source_only: number; target_only: number };
    }

    /** AI比对缓存详情响应 */
    interface AIComparisonDetailResponse {
      source_standard_no: string;
      target_standard_no: string;
      matched_html?: string;
      all_indicators?: FullIndicator[];
      relationship?: string;
      overall_assessment?: string;
      calculation_time?: number;
      generation_time?: number;
      create_time?: string;
      stats?: { matched: number; source_only: number; target_only: number };
    }

    // 批次标签统计数据
    interface BatchStatsResponse {
      success: boolean;
      data: {
        high_risk_stats: {
          need_attention_count: number;
          total_count: number;
          high_risk_tag_count: number;
        };
        tag_ranking: Array<{
          tag: string;
          count: number;
          percentage: number;
        }>;
        tag_combinations: {
          single_tag: number;
          double_tag: number;
          triple_plus: number;
        };
        tag_distribution: Record<string, number>;
      };
      msg: string;
    }

    // 查重池相关类型定义
    /** 查重池 */
    interface DeduplicationPool {
      id: number;
      pool_name: string;
      description: string | null;
      standard_nos?: string[];
      standard_count: number;
      is_default: boolean;
      is_active: boolean;
      create_time: string | null;
      update_time: string | null;
    }

    /** 创建查重池请求 */
    interface CreatePoolRequest {
      pool_name: string;
      description?: string;
      standard_nos: string[];
    }

    /** 更新查重池请求 */
    interface UpdatePoolRequest {
      pool_name?: string;
      description?: string;
      standard_nos?: string[];
      is_active?: boolean;
    }

    /** 查重池列表响应 */
    interface PoolListResponse {
      success: boolean;
      data: {
        records: DeduplicationPool[];
      };
      total: number;
      current: number;
      size: number;
      msg: string;
    }

    /** 查重池详情响应 */
    interface PoolDetailResponse {
      success: boolean;
      data: DeduplicationPool;
      msg: string;
    }

    /** 所有启用查重池响应 */
    interface AllActivePoolsResponse {
      success: boolean;
      data: {
        pools: Array<{
          id: number;
          pool_name: string;
          standard_count: number;
          is_default: boolean;
        }>;
      };
      msg: string;
    }


    // 标准基础信息
    interface StandardBaseInfo {
      id: string;
      standard_no: string;
      cname: string | null;
      ename: string | null;
      use_range: string | null;
      intl_cat: string | null;
      nat_cat: string | null;
      std_domain: string | null;
      std_field: string | null;
      std_year: string | null;
      std_obj: string | null;
      issue_date: string | null;
      act_date: string | null;
      annul_date: string | null;
      approval_unit: string | null;
      put_unit: string | null;
      lead_unit: string | null;
      draft_unit: string | null;
      draft_staff: string | null;
      chief_unit: string | null;
      mgr_dept: string | null;
      is_secret: string | null;
      std_nature: string | null;
      mandatory_clause: string | null;
      patent_info: string | null;
      state: string | null;
      security_level: string | null;
      release_history: string | null;
      release_std_no: string | null;
      replace_description: string | null;
      replace_stds: string | null;
      target_stds: string | null;
      adopt_situation: string | null;
      adopt_std_no: string | null;
      adopt_text: string | null;
      adopt_level: string | null;
      adopt_type: string | null;
      adopt_no: string | null;
      adopt_name: string | null;
      gjb_no: string | null;
      std_type: string | null;
      industry: string | null;
      remark: string | null;
      creator: string | null;
      updater: string | null;
      create_time: string | null;
      update_time: string | null;
    }

    interface StandardBaseInfoDetailResponse {
      id: string;
      standard_no: string;
      cname: string | null;
      ename: string | null;
      use_range: string | null;
      intl_cat: string | null;
      nat_cat: string | null;
      std_domain: string | null;
      std_field: string | null;
      std_year: string | null;
      std_obj: string | null;
      issue_date: string | null;
      act_date: string | null;
      annul_date: string | null;
      approval_unit: string | null;
      put_unit: string | null;
      lead_unit: string | null;
      draft_unit: string | null;
      draft_staff: string | null;
      chief_unit: string | null;
      mgr_dept: string | null;
      is_secret: string | null;
      std_nature: string | null;
      mandatory_clause: string | null;
      patent_info: string | null;
      state: string | null;
      security_level: string | null;
      release_history: string | null;
      release_std_no: string | null;
      replace_description: string | null;
      replace_stds: string | null;
      target_stds: string | null;
      adopt_situation: string | null;
      adopt_std_no: string | null;
      adopt_text: string | null;
      adopt_level: string | null;
      adopt_type: string | null;
      adopt_no: string | null;
      adopt_name: string | null;
      gjb_no: string | null;
      std_type: string | null;
      industry: string | null;
      remark: string | null;
      creator: string | null;
      updater: string | null;
      create_time: string | null;
      update_time: string | null;
    }

    interface StandardBaseInfoStatsResponse {
      total: number;
      has_standard_no: number;
      has_use_range: number;
      no_standard_no: number;
      no_use_range: number;
    }

    /** 标准指标缓存列表项 */
    interface StandardIndRecord {
      standard_no: string;
      standard_name: string;
      total_count: number;
      norm_class_counts: Record<string, number>;
      categories: string[];
      algorithm_version: string;
      run_id: string;
      run_remark: string;
      is_valid: boolean;
      create_time: string | null;
    }

    /** 标准指标列表响应 */
    interface StandardIndListResponse {
      list: StandardIndRecord[];
      total: number;
      current: number;
      size: number;
    }

    /** 关联试验摘要（挂在指标上） */
    interface LinkedTestItem {
      id: number;
      test_name: string;
      method_desc: string;
      conditions: string;
      preparation: string;
      procedure: string;
      acceptance: string;
      report_items: string;
      source_clause: string;
    }

    /** 单个指标（来自 standard_cache_ind） */
    interface StandardIndItem {
      id: number;
      indicator_type: 'static' | 'dynamic';
      standard_no?: string;
      standard_object: string;
      applicable_object: string;
      object_type?: string;
      indicator_category: string;
      norm_class?: string;
      source_clause: string;
      algorithm_version: string;
      linked_tests: LinkedTestItem[];
      // 静态指标
      indicator_object?: string;
      source_value?: string;
      // 动态指标
      experiment_name?: string;
      source_input_params?: string;
      source_process_logic?: string;
      source_result?: string;
    }

    /** 单条试验（来自 standard_cache_test） */
    interface StandardTestItem {
      id: number;
      test_name: string;
      method_desc: string;
      conditions: string;
      preparation: string;
      procedure: string;
      acceptance: string;
      report_items: string;
      source_clause: string;
      standard_object: string;
      applicable_object: string;
      object_type: string;
      indicator_category: string;
      linked_indicators: {
        id: number;
        indicator_type: string;
        indicator_object: string;
        experiment_name: string;
        source_clause: string
      }[];
    }

    /** 标准指标详情响应 */
    interface StandardIndDetailResponse {
      standard_no: string;
      standard_name: string;
      standard_structure_type?: string;
      indicators: StandardIndItem[];
      tests?: StandardTestItem[];
    }

    /** 全量指标列表项 */
    interface AllIndItem {
      id: number;
      standard_no: string;
      standard_name: string;
      indicator_type: 'static' | 'dynamic';
      object_type?: string;
      indicator_category: string;
      norm_class?: string;
      standard_object: string;
      applicable_object: string;
      source_clause: string;
      algorithm_version: string;
      // 静态
      indicator_object?: string;
      source_value?: string;
      // 动态
      experiment_name?: string;
      source_result?: string;
      source_input_params?: string;
    }

    /** 全量指标分页响应 */
    interface AllIndListResponse {
      list: AllIndItem[];
      total: number;
      current: number;
      size: number;
    }

    /** 标准化对象列表项 */
    interface StandardObjRecord {
      standard_object: string;
      applicable_object: string;
      standard_count: number;
      standard_nos: string[];
      total_count: number;
      norm_class_counts: Record<string, number>;
      categories: string[];
    }

    /** 标准化对象列表响应 */
    interface StandardObjListResponse {
      list: StandardObjRecord[];
      total: number;
      current: number;
      size: number;
    }

    /** 指标分类体系枚举（来自后端 mind_map parser） */
    interface IndTaxonomy {
      object_types: string[];
      categories_by_type: Record<string, string[]>;
      all_categories: string[];
      norm_classes: string[];
    }

    /** 批量指标拆解 SSE 事件 */
    type ExtractBatchEvent =
      | { type: 'start'; total: number }
      | { type: 'processing'; standard_no: string; index: number; total: number }
      | { type: 'progress'; standard_no: string; index: number; total: number; count: number }
      | { type: 'error'; standard_no: string; index: number; total: number; message: string }
      | { type: 'done'; total: number; success: number; failed: number };

    /** 对象关系图节点 */
    interface ObjRelNode {
      id: string;
      label: string;
      isRoot: boolean;
    }

    /** 对象关系图边 */
    interface ObjRelEdge {
      id: string;
      source: string;
      target: string;
      label: string;
      confidence: string;
    }

    interface DashboardOverview {
      activeUsers: number;
      qaSessionCount: number;
      nianSessionCount: number;
      sessionCount: number;
      messageCount: number;
      errorMessageCount: number;
      skillCount: number;
      skillPkgCount: number;
      totalYuan: number;
      totalCredits: number;
      costByModule: Record<string, { yuan: number; credits: number }>;
      rangeStart: string;
      rangeEnd: string;
    }

    interface DashboardTrendPoint {
      date: string;
      value: number;
    }

    interface DashboardTrend {
      metric: string;
      points: DashboardTrendPoint[];
    }

    interface DashboardUserRecord {
      userId: number;
      userName: string;
      nickName: string;
      sessionCount: number;
      messageCount: number;
      skillCount: number;
      costYuan: number;
      credits: number;
      lastActiveAt: number | null;
    }

    interface DashboardUserSession {
      id: number;
      sessionKey: string;
      title: string;
      messageCount: number;
      createdAt: number | null;
      updatedAt: number | null;
    }

    interface DashboardUserSkill {
      id: number;
      skillKey: string;
      name: string;
      source: string;
      visibility: string;
      isEnabled: boolean;
      createdAt: number | null;
    }

    interface DashboardUserCostSummary {
      totalYuan: number;
      totalCredits: number;
      byModule: Record<string, { yuan: number; credits: number; count: number }>;
      rawUnits: Record<string, number>;
    }

    interface DashboardUserDetail {
      user: { userId: number; userName: string; nickName: string };
      overview: {
        sessionCount: number;
        messageCount: number;
        skillCount: number;
        skillPkgCount: number;
      };
      costSummary: DashboardUserCostSummary;
      sessions: DashboardUserSession[];
      skills: DashboardUserSkill[];
      rangeStart: string;
      rangeEnd: string;
    }

    interface DashboardCostMeta {
      creditRateYuan: number;
      creditsPerYuan: number;
      creditName: string;
      currencyName: string;
    }

    interface DashboardUsageRecord {
      id: number;
      createdAt: number | null;
      userId: number | null;
      userName: string | null;
      nickName: string | null;
      module: string;
      bizEntry: string | null;
      provider: string;
      model: string | null;
      units: Record<string, number>;
      costYuan: number;
      credits: number;
      sessionId: number | null;
      refType: string | null;
      refId: number | null;
    }

    interface DashboardPricingItem {
      id: number;
      provider: string;
      model: string;
      unitType: string;
      priceYuan: number;
      creditsPerUnit: number;
      note: string | null;
      effectiveFrom: number | null;
    }

    interface DashboardPricingList {
      items: DashboardPricingItem[];
      creditRateYuan: number;
    }

    interface DashboardPricingHistoryItem {
      id: number;
      priceYuan: number;
      note: string | null;
      effectiveFrom: number | null;
      effectiveTo: number | null;
      isCurrent: boolean;
    }

    interface DashboardPricingHistory {
      provider: string;
      model: string;
      unitType: string;
      items: DashboardPricingHistoryItem[];
    }

    interface UserCreditQuotaRecord {
      userId: number;
      userName: string;
      nickName: string;
      quota: number;
      used: number;
      remaining: number;
    }

    interface MyCreditBalance {
      quota: number;
      used: number;
      remaining: number;
      isUnlimited: boolean;
    }

    interface DashboardSessionRecord {
      id: number;
      sessionKey: string;
      title: string;
      messageCount: number;
      createdAt: number | null;
      updatedAt: number | null;
    }

    interface DashboardMessage {
      id: number;
      role: 'user' | 'assistant';
      content: string;
      thinking: string | null;
      toolSteps: {id: number; type: string; tool: string; args?: Record<string, unknown>; content?: string}[];
      status: string;
      error: string | null;
      createdAt: number | null;
    }

    interface DashboardSessionMessages {
      sessionKey: string;
      title: string;
      messageCount: number;
      createdAt: number | null;
      updatedAt: number | null;
      messages: DashboardMessage[];
    }
  }
}
