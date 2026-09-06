import { request } from '../request';
import { encryptPassword, failResult, type Flat } from './crypto';

/** get role list */
export function fetchGetRoleList(params?: Api.SystemManage.RoleSearchParams) {
  return request<Api.SystemManage.RoleList>({
    url: '/system-manage/roles',
    method: 'get',
    params
  });
}

/** get user list */
export function fetchGetUserList(data?: Api.SystemManage.UserSearchParams) {
  return request<Api.SystemManage.UserList>({
    url: '/system-manage/users/all/',
    method: 'post',
    data
  });
}

/** get menu list */
export function fetchGetMenuList() {
  return request<Api.SystemManage.MenuList>({
    url: '/system-manage/menus',
    method: 'get'
  });
}

/** get all pages */
export function fetchGetAllPages() {
  return request<{ [key: string]: string }[]>({
    url: '/system-manage/menus/pages/',
    method: 'get'
  });
}

/** get menu tree */
export function fetchGetMenuTree() {
  return request<Api.SystemManage.MenuTree[]>({
    url: '/system-manage/menus/tree/',
    method: 'get'
  });
}

/** get menu button tree */
export function fetchGetMenuButtonTree() {
  return request<Api.SystemManage.ButtonTree[]>({
    url: '/system-manage/menus/buttons/tree/',
    method: 'get'
  });
}

/** get log list */
export function fetchGetLogList(data?: Api.SystemManage.LogSearchParams) {
  return request<Api.SystemManage.LogList>({
    url: '/system-manage/logs/all/',
    method: 'post',
    data
  });
}

/** delete log */
export function fetchDeleteLog(data?: Api.SystemManage.CommonDeleteParams) {
  return request<Api.SystemManage.LogList>({
    url: `/system-manage/logs/${data?.id}`,
    method: 'delete'
  });
}

export function fetchBatchDeleteLog(data?: Api.SystemManage.CommonBatchDeleteParams) {
  return request<Api.SystemManage.LogList>({
    url: '/system-manage/logs',
    method: 'delete',
    params: { ids: data?.ids.join(',') }
  });
}
/** update log */
export function fetchUpdateLog(data?: Api.SystemManage.LogUpdateParams) {
  return request<Api.SystemManage.LogList, 'json'>({
    url: `/system-manage/logs/${data?.id}`,
    method: 'patch',
    data
  });
}

/** get api tree */
export function fetchGetApiTree() {
  return request<Api.SystemManage.MenuTree[]>({
    url: '/system-manage/apis/tree/',
    method: 'get'
  });
}

/** refresh api from fastapi */
export function fetchRefreshAPI() {
  return request({
    url: '/system-manage/apis/refresh/',
    method: 'post'
  });
}

/** get api tags */
export function fetchGetApiTagsList() {
  return request({
    url: '/system-manage/apis/tags/all/',
    method: 'post'
  });
}

/** get api list */
export function fetchGetApiList(data?: Api.SystemManage.ApiSearchParams) {
  return request<Api.SystemManage.ApiList>({
    url: '/system-manage/apis/all/',
    method: 'post',
    data
  });
}

/** add api */
export function fetchAddApi(data?: Api.SystemManage.ApiAddParams) {
  return request<Api.SystemManage.ApiList, 'json'>({
    url: '/system-manage/apis',
    method: 'post',
    data
  });
}

/** delete api */
export function fetchDeleteApi(data?: Api.SystemManage.CommonDeleteParams) {
  return request<Api.SystemManage.ApiList>({
    url: `/system-manage/apis/${data?.id}`,
    method: 'delete'
  });
}

export function fetchBatchDeleteApi(data?: Api.SystemManage.CommonBatchDeleteParams) {
  return request<Api.SystemManage.ApiList>({
    url: '/system-manage/apis',
    method: 'delete',
    params: { ids: data?.ids.join(',') }
  });
}
/** update api */
export function fetchUpdateApi(data?: Api.SystemManage.ApiUpdateParams) {
  return request<Api.SystemManage.ApiList, 'json'>({
    url: `/system-manage/apis/${data?.id}`,
    method: 'patch',
    data
  });
}

/** add user */
export async function fetchAddUser(data?: Api.SystemManage.UserUpdateParams): Promise<Flat<Api.SystemManage.UserList>> {
  // password 非空时非对称加密后发送，绝不以明文上链路
  if (data?.password) {
    const enc = await encryptPassword(data.password);
    if (!enc.ok) return failResult<Api.SystemManage.UserList>(enc.error);
    data.password = enc.password;
    data.keyId = enc.keyId;
  }
  return request<Api.SystemManage.UserList, 'json'>({
    url: '/system-manage/users',
    method: 'post',
    data
  });
}

/** update user */
export async function fetchUpdateUser(data?: Api.SystemManage.UserUpdateParams): Promise<Flat<Api.SystemManage.UserList>> {
  // password 非空（管理员要改密）时非对称加密；为空表示不改密，保持原样
  if (data?.password) {
    const enc = await encryptPassword(data.password);
    if (!enc.ok) return failResult<Api.SystemManage.UserList>(enc.error);
    data.password = enc.password;
    data.keyId = enc.keyId;
  }
  return request<Api.SystemManage.UserList, 'json'>({
    url: `/system-manage/users/${data?.id}`,
    method: 'patch',
    data
  });
}

/** delete user */
export function fetchDeleteUser(data?: Api.SystemManage.CommonDeleteParams) {
  return request<Api.SystemManage.UserList>({
    url: `/system-manage/users/${data?.id}`,
    method: 'delete'
  });
}

export function fetchBatchDeleteUser(data?: Api.SystemManage.CommonBatchDeleteParams) {
  return request<Api.SystemManage.UserList>({
    url: '/system-manage/users',
    method: 'delete',
    params: { ids: data?.ids.join(',') }
  });
}

/** add role */
export function fetchAddRole(data?: Api.SystemManage.RoleUpdateParams) {
  return request<Api.SystemManage.RoleList, 'json'>({
    url: '/system-manage/roles',
    method: 'post',
    data
  });
}

/** delete role */
export function fetchDeleteRole(data?: Api.SystemManage.CommonDeleteParams) {
  return request<Api.SystemManage.RoleList>({
    url: `/system-manage/roles/${data?.id}`,
    method: 'delete'
  });
}

export function fetchBatchDeleteRole(data?: Api.SystemManage.CommonBatchDeleteParams) {
  return request<Api.SystemManage.RoleList>({
    url: '/system-manage/roles',
    method: 'delete',
    params: { ids: data?.ids.join(',') }
  });
}

/** update role */
export function fetchUpdateRole(data?: Api.SystemManage.RoleUpdateParams) {
  return request<Api.SystemManage.RoleList, 'json'>({
    url: `/system-manage/roles/${data?.id}`,
    method: 'patch',
    data
  });
}

/** get role menu ids */
export function fetchGetRoleMenu(data?: Api.SystemManage.RoleAuthorizedParams) {
  return request<Api.SystemManage.RoleAuthorizedList>({
    url: `/system-manage/roles/${data?.id}/menus`,
    method: 'get'
  });
}

/** update role menu ids */
export function fetchUpdateRoleMenu(data?: Api.SystemManage.RoleAuthorizedList) {
  return request<Api.SystemManage.RoleAuthorizedList>({
    url: `/system-manage/roles/${data?.id}/menus`,
    method: 'patch',
    data
  });
}

/** get role button ids */
export function fetchGetRoleButton(data?: Api.SystemManage.RoleAuthorizedParams) {
  return request<Api.SystemManage.RoleAuthorizedList>({
    url: `/system-manage/roles/${data?.id}/buttons`,
    method: 'get'
  });
}

/** update role button ids */
export function fetchUpdateRoleButton(data?: Api.SystemManage.RoleAuthorizedList) {
  return request<Api.SystemManage.RoleAuthorizedList>({
    url: `/system-manage/roles/${data?.id}/buttons`,
    method: 'patch',
    data
  });
}

/** get role api ids */
export function fetchGetRoleApi(data?: Api.SystemManage.RoleAuthorizedParams) {
  return request<Api.SystemManage.RoleAuthorizedList>({
    url: `/system-manage/roles/${data?.id}/apis`,
    method: 'get'
  });
}

/** update role api ids */
export function fetchUpdateRoleApi(data?: Api.SystemManage.RoleAuthorizedList) {
  return request<Api.SystemManage.RoleAuthorizedList>({
    url: `/system-manage/roles/${data?.id}/apis`,
    method: 'patch',
    data
  });
}

/** add menu */
export function fetchAddMenu(data?: Api.SystemManage.MenuAddParams) {
  return request<Api.SystemManage.MenuList, 'json'>({
    url: '/system-manage/menus',
    method: 'post',
    data
  });
}

/** delete menu */
export function fetchDeleteMenu(data?: Api.SystemManage.CommonDeleteParams) {
  return request<Api.SystemManage.MenuList>({
    url: `/system-manage/menus/${data?.id}`,
    method: 'delete'
  });
}

export function fetchBatchDeleteMenu(data?: Api.SystemManage.CommonBatchDeleteParams) {
  return request<Api.SystemManage.MenuList>({
    url: '/system-manage/menus',
    method: 'delete',
    params: { ids: data?.ids.join(',') }
  });
}

/** update menu */
export function fetchUpdateMenu(data?: Api.SystemManage.MenuUpdateParams) {
  return request<Api.SystemManage.MenuList, 'json'>({
    url: `/system-manage/menus/${data?.id}`,
    method: 'patch',
    data
  });
}

/** get standard plagiarism name list */
export function fetchGetStandardPlagiarismNameList(data?: Api.SystemManage.StandardPlagiarismNameSearchParams) {
  return request<Api.SystemManage.StandardPlagiarismNameList>({
    url: '/standard/standard-plagiarism-names/all/',
    method: 'post',
    data
  });
}

/** get standard plagiarism name detail */
export function fetchGetStandardPlagiarismName(data?: Api.SystemManage.CommonDeleteParams) {
  return request<Api.SystemManage.StandardPlagiarismName>({
    url: `/standard/standard-plagiarism-names/${data?.id}`,
    method: 'get'
  });
}

/** add standard plagiarism name */
export function fetchAddStandardPlagiarismName(data?: Api.SystemManage.StandardPlagiarismNameAddParams) {
  return request<Api.SystemManage.StandardPlagiarismName, 'json'>({
    url: '/standard/standard-plagiarism-names',
    method: 'post',
    data
  });
}

/** update standard plagiarism name */
export function fetchUpdateStandardPlagiarismName(data?: Api.SystemManage.StandardPlagiarismNameUpdateParams) {
  return request<Api.SystemManage.StandardPlagiarismName, 'json'>({
    url: `/standard/standard-plagiarism-names/${data?.id}`,
    method: 'patch',
    data
  });
}

/** delete standard plagiarism name */
export function fetchDeleteStandardPlagiarismName(data?: Api.SystemManage.CommonDeleteParams) {
  return request<Api.SystemManage.StandardPlagiarismName>({
    url: `/standard/standard-plagiarism-names/${data?.id}`,
    method: 'delete'
  });
}

/** batch delete standard plagiarism name */
export function fetchBatchDeleteStandardPlagiarismName(data?: Api.SystemManage.CommonBatchDeleteParams) {
  return request<Api.SystemManage.StandardPlagiarismName>({
    url: '/standard/standard-plagiarism-names',
    method: 'delete',
    params: { ids: data?.ids.join(',') }
  });
}
