/**
 * 开发者面板 / 快捷切换账号使用的演示口令
 *
 * 真实口令写在 web/.env 的 VITE_DEV_QUICK_LOGIN_PWD（.env 系列不进公开仓库），
 * 代码里只保留与后端 init_app.py 播种账号一致的默认值，避免口令随仓库公开。
 */
const FALLBACK_PASSWORD = '123456';

export function getDevQuickLoginPassword(): string {
  const configured = String(import.meta.env.VITE_DEV_QUICK_LOGIN_PWD ?? '').trim();
  return configured || FALLBACK_PASSWORD;
}
