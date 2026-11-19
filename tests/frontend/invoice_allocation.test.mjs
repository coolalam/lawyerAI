import { test } from 'node:test'
import assert from 'node:assert/strict'
import { calculateAllocation, switchMethod } from '../../src/invoiceAllocation.ts'

test('ratio allocation assigns the last cent deterministically', () => {
  const result = calculateAllocation({ method: 'ratio', rows: [{ lawyer_id: 'b', ratio: '50' }, { lawyer_id: 'a', ratio: '50' }] }, '0.01')
  assert.equal(result.error, '')
  assert.deepEqual(result.rows.map(row => row.amount), ['0.00', '0.01'])
})

test('large monetary values retain exact cents', () => {
  const result = calculateAllocation({ method: 'ratio', rows: [{ lawyer_id: 'a', ratio: '50' }, { lawyer_id: 'b', ratio: '50' }] }, '9007199254740993.01')
  assert.equal(result.error, '')
  assert.deepEqual(result.rows.map(row => row.amount), ['4503599627370496.51', '4503599627370496.50'])
})

test('fixed allocations validate totals and method switching preserves a valid plan', () => {
  const plan = { method: 'fixed', rows: [{ lawyer_id: 'a', amount: '0.01' }, { lawyer_id: 'b', amount: '0.01' }, { lawyer_id: 'c', amount: '0.01' }] }
  const result = calculateAllocation(plan, '0.03')
  assert.equal(result.error, '')
  assert.deepEqual(result.rows.map(row => row.ratio), ['33.3333', '33.3333', '33.3333'])
  const ratio = switchMethod(plan, '0.03', 'ratio')
  assert.equal(calculateAllocation(ratio, '0.03').error, '')
  assert.deepEqual(ratio.rows.map(row => row.ratio), ['33.3334', '33.3333', '33.3333'])
  assert.deepEqual(calculateAllocation(ratio, '0.03').rows.map(row => row.amount), ['0.01', '0.01', '0.01'])
  assert.match(calculateAllocation(plan, '0.04').error, /合计/)
})

test('blank, negative and excessive precision inputs cannot produce a valid plan', () => {
  for (const ratio of ['', '-1', '100.00001', 'NaN']) {
    assert.notEqual(calculateAllocation({ method: 'ratio', rows: [{ lawyer_id: 'a', ratio }] }, '1').error, '')
  }
  assert.equal(calculateAllocation({ method: 'ratio', rows: [{ lawyer_id: 'a', ratio: '0' }, { lawyer_id: 'b', ratio: '100' }] }, '1').error, '')
})

test('incomplete totals still preview entered row amounts and percentages', () => {
  const ratio = calculateAllocation({ method: 'ratio', rows: [{ lawyer_id: 'a', ratio: '30' }, { lawyer_id: 'b', ratio: '' }] }, '1000')
  assert.notEqual(ratio.error, '')
  assert.equal(ratio.rows[0].amount, '300.00')
  assert.equal(ratio.rows[1].amount, undefined)
  const fixed = calculateAllocation({ method: 'fixed', rows: [{ lawyer_id: 'a', amount: '200' }, { lawyer_id: 'b', amount: '500' }] }, '1000')
  assert.notEqual(fixed.error, '')
  assert.deepEqual(fixed.rows.map(row => row.ratio), ['20.0000', '50.0000'])
})
