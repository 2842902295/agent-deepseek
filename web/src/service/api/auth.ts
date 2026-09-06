import { request } from '../request';
import { encryptPasswords, failResult, type Flat } from './crypto';

/**
 * login
 *
 * @param userName user name
 * @param password 明文密码（service 内部非对称加密后发送，绝不以明文上链路）
 */
export async function fetchLogin(userName: string, password: string): Promise<Flat<Api.Auth.LoginToken>> {
  const enc = await encryptPasswords([password]);
  if (!enc.ok) return failResult<Api.Auth.LoginToken>(enc.error);
  return request<Api.Auth.LoginToken>({
    url: '/auth/login',
    method: 'post',
    data: { userName, password: enc.ciphertexts[0], keyId: enc.keyId }
  });
}

/**
 * register a new user (account = phone number, default role R_USER)
 *
 * @param userPhone phone number (also used as account)
 * @param password 明文密码（service 内部非对称加密后发送）
 * @param nickName user's display name
 */
export async function fetchRegister(userPhone: string, password: string, nickName: string): Promise<Flat<Api.Auth.LoginToken>> {
  const enc = await encryptPasswords([password]);
  if (!enc.ok) return failResult<Api.Auth.LoginToken>(enc.error);
  return request<Api.Auth.LoginToken>({
    url: '/auth/register',
    method: 'post',
    data: { userPhone, password: enc.ciphertexts[0], keyId: enc.keyId, nickName }
  });
}

/**
 * change password（已登录用户修改自己的密码）
 *
 * @param oldPassword 明文旧密码
 * @param newPassword 明文新密码
 *
 * 旧/新共用同一个一次性 keyId（usage=2）。
 */
export async function fetchChangePassword(oldPassword: string, newPassword: string): Promise<Flat<null>> {
  const enc = await encryptPasswords([oldPassword, newPassword], 2);
  if (!enc.ok) return failResult<null>(enc.error);
  return request<null>({
    url: '/auth/change-password',
    method: 'post',
    data: { oldPassword: enc.ciphertexts[0], newPassword: enc.ciphertexts[1], keyId: enc.keyId }
  });
}

/** get user info */
export function fetchGetUserInfo() {
  return request<Api.Auth.UserInfo>({ url: '/auth/user-info' });
}

/**
 * 自助更新个人资料（昵称/性别/邮箱/手机号）
 *
 * 返回更新后的字段，前端用于回填 authStore.userInfo（GET /user-info 有 60s 缓存，不能靠重拉刷新）。
 */
export function fetchUpdateMyProfile(data: { nickName: string; userGender: string; userEmail?: string | null; userPhone?: string | null }) {
  return request<Api.Auth.UserInfo>({
    url: '/auth/profile',
    method: 'post',
    data
  });
}

/**
 * refresh token
 *
 * @param refreshToken refresh token
 */
export function fetchRefreshToken(refreshToken: string) {
  return request<Api.Auth.LoginToken>({
    url: '/auth/refresh-token',
    method: 'post',
    data: {
      refreshToken
    }
  });
}

/**
 * return custom backend error
 *
 * @param code error code
 * @param msg error message
 */
export function fetchCustomBackendError(code: string, msg: string) {
  return request({ url: '/auth/error', params: { code, msg } });
}

/** send SMS verification code (generic mode) */
export function fetchSendSmsCode(userPhone: string) {
  return request<null>({
    url: '/auth/sms-code',
    method: 'post',
    data: { userPhone }
  });
}

/**
 * SMS login (generic mode)
 * - existing user: returns token directly
 * - new user without nickName: returns { isNewUser: true }
 * - new user with nickName: creates account and returns token
 */
export function fetchSmsLogin(userPhone: string, code: string, nickName?: string) {
  return request<Api.Auth.LoginToken & { isNewUser?: boolean }>({
    url: '/auth/sms-login',
    method: 'post',
    data: { userPhone, code, ...(nickName ? { nickName } : {}) }
  });
}
