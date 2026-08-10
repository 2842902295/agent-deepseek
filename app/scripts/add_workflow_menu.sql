-- 添加工作流画板页面的菜单和权限（hide_in_menu=1，仅通过侧栏按钮进入）
-- 注意：menus 表列名为 `order`（非 sequence），roles_menus 表列名为 roles_id（非 role_id）

-- 1. 插入菜单（放在"小念念念"旁边，隐藏不显示在菜单栏）
INSERT INTO `menus` (
    `menu_type`, `menu_name`, `route_name`, `route_path`, `component`,
    `i18n_key`, `icon`, `icon_type`, `parent_id`, `status_type`, `order`,
    `constant`, `keep_alive`, `hide_in_menu`, `multi_tab`
)
SELECT
    '1', -- menu_type
    '工作流画板', -- menu_name
    'ai_workflow', -- route_name
    '/ai/workflow', -- route_path
    'view.ai_workflow', -- component
    'route.ai_workflow', -- i18n_key
    'material-symbols:account-tree-outline', -- icon
    '2', -- icon_type (2=iconify)
    parent.parent_id, -- parent_id (与 nian 同级)
    '1', -- status_type (1=启用)
    (SELECT IFNULL(MAX(`order`), 0) + 1 FROM menus WHERE parent_id = parent.parent_id), -- order
    0, -- constant
    0, -- keep_alive
    1, -- hide_in_menu (隐藏)
    0  -- multi_tab
FROM menus parent
WHERE parent.route_name = 'ai_nian'
LIMIT 1;

-- 2. 获取新插入的菜单 ID
SET @menu_id = LAST_INSERT_ID();

-- 3. 为 R_SUPER、R_ADMIN 和 R_USER 角色授权
INSERT INTO `roles_menus` (`roles_id`, `menu_id`)
SELECT r.id, @menu_id
FROM `roles` r
WHERE r.role_code IN ('R_SUPER', 'R_ADMIN', 'R_USER');

-- 查看结果
SELECT
    m.id, m.menu_name, m.route_name, m.route_path, m.hide_in_menu,
    GROUP_CONCAT(r.role_code) as roles
FROM menus m
LEFT JOIN roles_menus rm ON m.id = rm.menu_id
LEFT JOIN roles r ON rm.roles_id = r.id
WHERE m.route_name = 'ai_workflow'
GROUP BY m.id;
