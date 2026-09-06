/**
 * 前端非对称加密原语。
 *
 * 与后端 `app/utils/asymcrypto.py` 配对：后端下发 SPKI(DER) base64 公钥，
 * 前端用 RSA-OAEP（OAEP 摘要与 MGF1 均为 SHA-256，label=None）加密明文密码，密文以 base64 回传。
 *
 * 双实现策略（关键）：
 * - 优先用 Web Crypto API（`crypto.subtle`），原生、快。
 * - 但 `crypto.subtle` 仅在安全上下文（https 或 localhost）存在；内网常用纯 http + IP 访问，
 *   此时它恒为 undefined。为兼容该场景，自动回退到纯 JS 的 `node-forge`，
 *   产出与 Web Crypto 完全等价的密文，后端解密无感知。
 * - node-forge 走动态 import 按需加载：安全上下文下根本不会下载该 chunk，不增加 https 用户体积。
 */

import type * as forge from 'node-forge';

/** 公钥句柄：安全上下文下为原生 CryptoKey，否则为 node-forge 公钥对象 */
export type RsaPublicKey = CryptoKey | forge.pki.rsa.PublicKey;

/** 探测 Web Crypto 是否可用（不可用时不抛错，改走 node-forge 回退） */
function getSubtle(): SubtleCrypto | null {
  return typeof crypto !== 'undefined' && crypto.subtle ? crypto.subtle : null;
}

/** 按需加载 node-forge（仅非安全上下文回退时触发） */
async function loadForge() {
  const mod = await import('node-forge');
  return mod.default;
}

export function base64ToUint8(b64: string): Uint8Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) {
    bytes[i] = bin.charCodeAt(i);
  }
  return bytes;
}

export function uint8ToBase64(bytes: Uint8Array): string {
  let bin = '';
  for (let i = 0; i < bytes.length; i += 1) {
    bin += String.fromCharCode(bytes[i]);
  }
  return btoa(bin);
}

/** 导入后端下发的 SPKI(DER, base64) 公钥，用于 RSA-OAEP-SHA256 加密。 */
export async function importRsaPublicKey(spkiBase64: string): Promise<RsaPublicKey> {
  const subtle = getSubtle();
  if (subtle) {
    return subtle.importKey('spki', base64ToUint8(spkiBase64), { name: 'RSA-OAEP', hash: 'SHA-256' }, false, ['encrypt']);
  }
  const fg = await loadForge();
  const asn1 = fg.asn1.fromDer(fg.util.decode64(spkiBase64));
  return fg.pki.publicKeyFromAsn1(asn1);
}

/** 用公钥加密明文密码，返回 base64 密文。 */
export async function rsaOaepEncrypt(key: RsaPublicKey, plain: string): Promise<string> {
  const subtle = getSubtle();
  if (subtle) {
    const data = new TextEncoder().encode(plain);
    const cipher = await subtle.encrypt({ name: 'RSA-OAEP' }, key as CryptoKey, data);
    return uint8ToBase64(new Uint8Array(cipher));
  }
  const fg = await loadForge();
  const cipher = (key as forge.pki.rsa.PublicKey).encrypt(fg.util.encodeUtf8(plain), 'RSA-OAEP', {
    md: fg.md.sha256.create(),
    mgf1: { md: fg.md.sha256.create() }
  });
  return fg.util.encode64(cipher);
}
