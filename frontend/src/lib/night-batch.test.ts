import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  datetimeLocalToAwareIso,
  defaultTonightDatetimeLocal,
  formatApiError,
  formatBatchCost,
  formatUsdFromMicrounits,
  formatUtcOffset,
  friendlyBatchError,
  isAwareIsoInPast,
  localTimezoneLabelForDatetimeLocal,
  timezoneOffsetForDatetimeLocal,
} from './night-batch.ts'

test('formatUtcOffset formats east and west of UTC', () => {
  assert.equal(formatUtcOffset(480), '+08:00')
  assert.equal(formatUtcOffset(-300), '-05:00')
  assert.equal(formatUtcOffset(-330), '-05:30')
  assert.equal(formatUtcOffset(0), '+00:00')
})

test('datetimeLocalToAwareIso never emits a naive timestamp', () => {
  const iso = datetimeLocalToAwareIso('2026-09-09T23:00')
  assert.match(iso, /^\d{4}-\d{2}-\d{2}T23:00:00[+-]\d{2}:\d{2}$/)
  assert.equal(iso.includes('Z'), false)
})

test('datetimeLocalToAwareIso uses the selected instant offset, not now', (t) => {
  const original = Date.prototype.getTimezoneOffset
  t.after(() => {
    Date.prototype.getTimezoneOffset = original
  })
  Date.prototype.getTimezoneOffset = function getTimezoneOffsetMock() {
    const year = this.getFullYear()
    const month = this.getMonth()
    const day = this.getDate()
    if (year === 2026 && month === 0 && day === 10) return 300
    if (year === 2026 && month === 6 && day === 10) return 240
    return original.call(this)
  }
  assert.equal(datetimeLocalToAwareIso('2026-01-10T23:00'), '2026-01-10T23:00:00-05:00')
  assert.equal(datetimeLocalToAwareIso('2026-07-10T23:00'), '2026-07-10T23:00:00-04:00')
  assert.equal(timezoneOffsetForDatetimeLocal('2026-01-10T23:00:00'), '-05:00')
  assert.equal(localTimezoneLabelForDatetimeLocal('2026-07-10T23:00'), 'UTC-04:00')
})

test('normalize / convert reject empty or invalid wall clocks', () => {
  assert.throws(() => datetimeLocalToAwareIso(''), { message: '请选择开始时间' })
  assert.throws(() => datetimeLocalToAwareIso('tomorrow'), { message: '开始时间格式无效' })
})

test('defaultTonightDatetimeLocal picks today 23:00 or tomorrow when past', () => {
  assert.equal(
    defaultTonightDatetimeLocal(new Date(2026, 8, 9, 22, 0, 0)),
    '2026-09-09T23:00',
  )
  assert.equal(
    defaultTonightDatetimeLocal(new Date(2026, 8, 9, 23, 0, 0)),
    '2026-09-10T23:00',
  )
})

test('isAwareIsoInPast compares the encoded instant', () => {
  const now = new Date('2026-09-09T15:00:00Z')
  assert.equal(isAwareIsoInPast('2026-09-09T23:00:00+08:00', now), true)
  assert.equal(isAwareIsoInPast('2026-09-09T23:00:01+08:00', now), false)
})

test('formatBatchCost does not pretend an incomplete total is final', () => {
  assert.equal(
    formatBatchCost({
      known_cost_microunits: 12300,
      cost_microunits: 12300,
      cost_complete: true,
    }),
    formatUsdFromMicrounits(12300),
  )
  const incomplete = formatBatchCost({
    known_cost_microunits: 5000,
    cost_microunits: null,
    cost_complete: false,
  })
  assert.match(incomplete, /已知/)
  assert.match(incomplete, /不完整/)
  assert.equal(incomplete.includes('（不完整）'), true)
})

test('formatApiError unwraps FastAPI detail', () => {
  assert.equal(
    formatApiError(new Error('409 {"detail":"report abc already has an active audit job"}')),
    'report abc already has an active audit job',
  )
  assert.equal(formatApiError(new Error('boom')), 'boom')
})

test('friendlyBatchError maps conflict ids to file names', () => {
  const names = new Map([['abcdef12', '报告A.pdf']])
  assert.match(
    friendlyBatchError(
      new Error('409 {"detail":"report abcdef12 already has an active audit job"}'),
      names,
    ),
    /报告A\.pdf/,
  )
})
