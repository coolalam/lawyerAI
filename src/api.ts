export async function api(path: string, options: RequestInit = {}) {
  const response = await fetch('/api' + path, {
    credentials: 'same-origin', ...options,
    headers: options.body instanceof FormData ? options.headers : { 'Content-Type': 'application/json', ...options.headers },
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const message = Array.isArray(data.detail) ? data.detail.map((x: any) => x.msg).join('；') : data.detail
    throw new Error(message || `请求失败 (${response.status})`)
  }
  return data
}
export const post = (path: string, body: any) => api(path, { method: 'POST', body: JSON.stringify(body) })
export const patch = (path: string, body: any) => api(path, { method: 'PATCH', body: JSON.stringify(body) })
export function currency(value: any, unit = 'CNY') {
  if (value === null) return '待核对'
  const raw = String(value || '0')
  if (!/^-?\d+(\.\d+)?$/.test(raw)) return '待核对'
  const negative = raw.startsWith('-')
  const [whole, fraction = ''] = raw.replace(/^-/, '').split('.')
  const symbols: Record<string, string> = { CNY: '¥', USD: 'US$', HKD: 'HK$', EUR: '€', JPY: 'JP¥' }
  // Never convert exact monetary strings to a JS Number (unsafe above 2^53).
  return `${negative ? '-' : ''}${symbols[unit] || unit + ' '}${BigInt(whole).toLocaleString('zh-CN')}.${fraction.padEnd(2, '0').slice(0, 2)}`
}
export const dateText = (value: string) => value ? new Date(value).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) : '—'
export const dateFull = (value: string) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
