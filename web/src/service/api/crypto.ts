import { request } from '../request';
import { importRsaPublicKey, rsaOaepEncrypt } from '@/utils/crypto';

/**
 * 后端一次性密码加密公钥
 *
 * - keyId：一次性密钥 ID，回传给后端用于定位私钥解密
 * - publicKey：SPKI(DER) 的 base64，直接喂给 Web Crypto importKey
 * - usage：该 keyId 允许解密的密码个数（登录/注册/管理员建改=1，修改密码=2，旧新共用）
 */
export interface PublicKeyInfo {
  keyId: string;
  publicKey: string;
  alg: string;
}

/** 统一的 flat 返回形态（与 request 的 {data, error, response} 一致，便于加密失败时直接回传） */
export type Flat<T> = { data: T | null; error: any; response: any };

/** 获取一次性密码加密公钥 */
export function fetchPublicKey(usage = 1) {
  return request<PublicKeyInfo>({
    url: '/auth/public-key',
    method: 'get',
    params: { usage }
  });
}

type EncryptOk = { ok: true; ciphertexts: string[]; keyId: string };
type EncryptFail = { ok: false; error: any };

/**
 * 取一次性公钥，用同一公钥加密若干明文密码。
 *
 * - usage：该 keyId 允许后端解密的次数，默认等于明文个数
 * - 成功返回 {ok, ciphertexts[], keyId}；失败返回 {ok:false, error}（公钥请求失败提示由统一拦截器弹窗）
 */
export async function encryptPasswords(plaintexts: string[], usage = plaintexts.length): Promise<EncryptOk | EncryptFail> {
  const { data, error } = await fetchPublicKey(usage);
  if (error) return { ok: false, error };
  try {
    const key = await importRsaPublicKey(data.publicKey);
    const ciphertexts = await Promise.all(plaintexts.map(p => rsaOaepEncrypt(key, p)));
    return { ok: true, ciphertexts, keyId: data.keyId };
  } catch (e) {
    window.$message?.error('密码加密失败，请刷新后重试');
    return { ok: false, error: e };
  }
}

/**
 * 加密单个明文密码（usage=1）的便捷封装，返回 {ok, password, keyId} | {ok:false, error}。
 */
export async function encryptPassword(plain: string): Promise<{ ok: true; password: string; keyId: string } | { ok: false; error: any }> {
  const r = await encryptPasswords([plain]);
  if (!r.ok) return { ok: false, error: r.error };
  return { ok: true, password: r.ciphertexts[0], keyId: r.keyId };
}

/** 公钥取不到或加密失败时，构造与 request 一致的 flat 失败返回 */
export function failResult<T>(error: any): Flat<T> {
  return { data: null, error, response: null };
}
