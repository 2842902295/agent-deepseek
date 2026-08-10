import type { InjectionKey } from 'vue';

/** Inject token: 在登录页内切换 module（pwd-login / register / ...），不走路由 */
export const SWITCH_LOGIN_MODULE: InjectionKey<(key: UnionKey.LoginModule) => void> =
  Symbol('switchLoginModule');
