const STORAGE_KEY = 'app_brand';

type BrandVariant = 'standard' | 'generic';

function readCache(): BrandVariant {
  const v = localStorage.getItem(STORAGE_KEY);
  return v === 'generic' ? 'generic' : 'standard';
}

let _variant: BrandVariant = readCache();

export function getBrandVariant(): BrandVariant {
  return _variant;
}

export async function initBrandConfig(): Promise<void> {
  const base = (import.meta.env.VITE_SERVICE_BASE_URL as string | undefined) ?? '/api/v1';
  try {
    const res = await fetch(`${base}/ai/agent/app-config`, { signal: AbortSignal.timeout(10000) });
    const json = await res.json();
    const v: BrandVariant = json?.data?.brand_variant === 'generic' ? 'generic' : 'standard';
    localStorage.setItem(STORAGE_KEY, v);
    _variant = v;
  } catch {
    // 超时或失败：保持缓存值，不阻塞页面
  }
}
