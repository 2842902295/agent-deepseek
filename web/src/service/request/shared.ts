import { useAuthStore } from '@/store/modules/auth';
import { localStg } from '@/utils/storage';
import { fetchRefreshToken } from '../api';
import type { RequestInstanceState } from './type';

export function getAuthorization() {
  const token = localStg.get('token');
  const Authorization = token ? `Bearer ${token}` : null;

  return Authorization;
}

/** refresh token */
async function handleRefreshToken() {
  const { resetStore } = useAuthStore();

  const rToken = localStg.get('refreshToken') || '';
  const { error, data } = await fetchRefreshToken(rToken);
  if (!error) {
    localStg.set('token', data.token);
    localStg.set('refreshToken', data.refreshToken);
    return true;
  }

  resetStore();

  return false;
}

/** 解析 JWT 的 exp 声明（毫秒时间戳），解析失败返回 0 */
function readTokenExpMs(token: string): number {
  try {
    const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
    const payload = JSON.parse(new TextDecoder().decode(bytes)) as { exp?: number };
    return typeof payload.exp === 'number' ? payload.exp * 1000 : 0;
  } catch {
    return 0;
  }
}

/** accessToken 剩余有效期低于该阈值时主动刷新 */
const REFRESH_MARGIN_MS = 60_000;

let ensureRefreshPromise: Promise<boolean> | null = null;

/**
 * 确保拿到可用的 accessToken。
 *
 * 供 SSE 流式、文件上传/下载等「绕过 axios 拦截器」的原生 fetch/XHR 场景使用：
 * 这类请求不经过 onBackendFail，后端返回 4010 时无法自动刷新重试，只能在发请求前预检。
 * - token 仍在有效期（距过期大于阈值）：直接返回
 * - 已过期 / 即将过期 / 不存在：复用 handleRefreshToken 静默刷新一次（互斥去重，避免并发流同时触发多次刷新）
 * - 刷新失败：handleRefreshToken 内部已 resetStore 跳转登录页，本函数抛错中断本次请求
 */
export async function ensureFreshAccessToken(): Promise<string> {
  const token = localStg.get('token') || '';
  if (token && readTokenExpMs(token) - Date.now() > REFRESH_MARGIN_MS) {
    return token;
  }

  if (!ensureRefreshPromise) {
    ensureRefreshPromise = handleRefreshToken().finally(() => {
      setTimeout(() => {
        ensureRefreshPromise = null;
      }, 1000);
    });
  }

  const success = await ensureRefreshPromise;
  if (!success) {
    throw new Error('登录已过期，请重新登录');
  }

  return localStg.get('token') || '';
}

/**
 * 流式 / 二进制端点鉴权失败兜底：后端返回 HTTP 200 + JSON（{code,msg}）而非 SSE 流 / 文件，
 * `response.ok` 恒为 true。不主动检测，Promise 会以零事件 / 坏文件「成功」，错误被静默吞掉。
 * 供所有绕过 axios 拦截器的原生 fetch 请求在 `!response.ok` 检查后调用。
 */
export async function rejectIfJsonError(response: Response): Promise<void> {
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    const data: {msg?: string} | null = await response.json().catch(() => null);
    throw new Error(data?.msg || '请求失败');
  }
}

export async function handleExpiredRequest(state: RequestInstanceState) {
  if (!state.refreshTokenPromise) {
    state.refreshTokenPromise = handleRefreshToken();
  }

  const success = await state.refreshTokenPromise;

  setTimeout(() => {
    state.refreshTokenPromise = null;
  }, 1000);

  return success;
}

export function showErrorMsg(state: RequestInstanceState, message: string) {
  if (!state.errMsgStack?.length) {
    state.errMsgStack = [];
  }

  const isExist = state.errMsgStack.includes(message);

  if (!isExist) {
    state.errMsgStack.push(message);

    window.$message?.error(message, {
      onLeave: () => {
        state.errMsgStack = state.errMsgStack.filter(msg => msg !== message);

        setTimeout(() => {
          state.errMsgStack = [];
        }, 5000);
      }
    });
  }
}
