-- 添加快捷功能管理页面的菜单和权限

-- 1. 插入菜单（放在"小念念念"旁边）
INSERT INTO `menus` (
    `menu_type`, `menu_name`, `route_name`, `route_path`, `component`,
    `i18n_key`, `icon`, `icon_type`, `parent_id`, `status_type`, `sequence`,
    `constant`, `keep_alive`, `href`, `hide_in_menu`, `active_menu`,
    `multi_tab`, `fixed_index_in_tab`, `query`, `button_code`
)
SELECT
    1, -- menu_type (1=菜单)
    '快捷功能管理', -- menu_name
    'ai_quick-action-manage', -- route_name
    '/ai/quick-action-manage', -- route_path
    'view.ai_quick-action-manage', -- component
    'route.ai_quick-action-manage', -- i18n_key
    'material-symbols:settings-outline', -- icon
    2, -- icon_type (2=iconify)
    parent.id, -- parent_id (小念的父级)
    1, -- status_type (1=启用)
    (SELECT MAX(sequence) + 1 FROM menus WHERE parent_id = parent.id), -- sequence (排在小念后面)
    0, -- constant
    1, -- keep_alive
    NULL, -- href
    0, -- hide_in_menu
    NULL, -- active_menu
    0, -- multi_tab
    NULL, -- fixed_index_in_tab
    NULL, -- query
    NULL  -- button_code
FROM menus parent
WHERE parent.route_name = 'ai_nian' -- 找到"小念念念"的父级菜单
LIMIT 1;

-- 2. 获取新插入的菜单 ID（用于后续授权）
SET @menu_id = LAST_INSERT_ID();

-- 3. 为 R_SUPER 和 R_ADMIN 角色授权
INSERT INTO `roles_menus` (`role_id`, `menu_id`)
SELECT r.id, @menu_id
FROM `roles` r
WHERE r.role_code IN ('R_SUPER', 'R_ADMIN');

-- 4. 插入 API 权限（如果需要）
-- 快捷功能管理相关的 API 都放在 /ai/quick-actions 下
-- 这些 API 会自动注册，这里只是确保权限正确

-- 查看结果
SELECT
    m.id,
    m.menu_name,
    m.route_name,
    m.route_path,
    m.sequence,
    GROUP_CONCAT(r.role_code) as roles
FROM menus m
LEFT JOIN roles_menus rm ON m.id = rm.menu_id
LEFT JOIN roles r ON rm.role_id = r.id
WHERE m.route_name = 'ai_quick-action-manage'
GROUP BY m.id;

-- 如果上面的 SELECT 没有找到父级，可以手动指定 parent_id
-- 先查看 AI 相关菜单的结构
SELECT id, menu_name, route_name, parent_id, sequence
FROM menus
WHERE route_path LIKE '/ai/%'
ORDER BY parent_id, sequence;
