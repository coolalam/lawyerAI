export type AllocationRow = { lawyer_id: string; lawyer_name?: string; role?: string; ratio?: string; amount?: string }
export type AllocationPlan = { method: 'ratio' | 'fixed'; rows: AllocationRow[] }
export function units(value: unknown, places: number): bigint | null {
  const match = String(value ?? '').match(new RegExp(`^(\\d+)(?:\\.(\\d{0,${places}}))?$`))
  return match ? BigInt(match[1]) * 10n ** BigInt(places) + BigInt((match[2] || '').padEnd(places, '0')) : null
}
export function decimal(value: bigint, places: number): string {
  const scale = 10n ** BigInt(places)
  return `${value / scale}.${String(value % scale).padStart(places, '0')}`
}
function distribute(total: bigint, weights: bigint[], denominator: bigint, rows: AllocationRow[]) {
  const amounts = weights.map(weight => total * weight / denominator)
  let remainder = total - amounts.reduce((a, b) => a + b, 0n)
  const order = weights.map((weight, i) => ({ i, rest: total * weight % denominator }))
    .sort((a, b) => a.rest === b.rest ? (rows[a.i].lawyer_id < rows[b.i].lawyer_id ? -1 : 1) : a.rest > b.rest ? -1 : 1)
  for (const row of order) { if (remainder <= 0n) break; amounts[row.i] = amounts[row.i]! + 1n; remainder-- }
  return amounts
}
export function calculateAllocation(plan: AllocationPlan, amount: unknown) {
  const total = units(amount, 2), key = plan.method === 'ratio' ? 'ratio' : 'amount'
  const values = plan.rows.map(row => units(row[key], key === 'ratio' ? 4 : 2))
  const sum = values.reduce<bigint>((a, b) => a + (b ?? 0n), 0n)
  let error = ''
  if (!total || total >= 1000000000000000000n) error = '请先填写有效的开票金额'
  else if (!plan.rows.length) error = '案件暂无律师，请先设置案件承办团队'
  else if (values.some(value => value === null)) error = '请填写每位律师的分配数值（无分配可填 0）'
  else if (sum !== (key === 'ratio' ? 1000000n : total)) error = key === 'ratio' ? '比例合计须等于 100%' : '分配金额合计须等于开票金额'
  const counterpart = key === 'ratio' ? 'amount' : 'ratio'
  let rows = plan.rows.map(row => ({ ...row, [counterpart]: undefined }))
  if (total && total > 0n) {
    // Preview each entered value immediately; reconcile cents once the ratio totals 100%.
    const computed = key === 'ratio' && !error
      ? distribute(total, values as bigint[], 1000000n, rows)
      : values.map(value => value === null ? null : key === 'ratio'
        ? (total * value + 500000n) / 1000000n
        : (value * 1000000n + total / 2n) / total)
    rows = rows.map((row, i) => ({ ...row, [counterpart]: computed[i] == null ? undefined : decimal(computed[i]!, key === 'ratio' ? 2 : 4) }))
  }
  return { rows, error, total: decimal(sum, key === 'ratio' ? 4 : 2) }
}
export function switchMethod(plan: AllocationPlan, amount: unknown, method: AllocationPlan['method']): AllocationPlan {
  const result = calculateAllocation(plan, amount)
  const rows = result.rows.map(row => ({ ...row, ...(result.error ? { [method === 'fixed' ? 'amount' : 'ratio']: '' } : {}) }))
  const total = units(amount, 2)
  if (!result.error && method === 'ratio' && total) {
    const weights = rows.map(row => units(row.amount, 2)!)
    const ratios = distribute(1000000n, weights, total, rows)
    rows.forEach((row, i) => { row.ratio = decimal(ratios[i]!, 4) })
  }
  return { method, rows }
}
