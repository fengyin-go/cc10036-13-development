import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---- 交付链路：报表订阅 → 交付受众 → 即席导出 → 交付收据 ----

export type DeliveryPushResult = {
  ok: boolean
  message: string
  subscription_id: number
  report_object: string
  period: string
  audience_count: number
  delivery_count: number
  deduplicated_count: number
  receipt_count: number
  domain_totals: Record<string, Record<string, string>>
  deliveries: Array<Record<string, unknown>>
  receipts: Array<EntryRow>
}

export async function pushSubscription(subscriptionId: number): Promise<DeliveryPushResult> {
  const response = await request(`/api/delivery/subscriptions/${subscriptionId}/push`, {
    method: 'POST',
  })
  if (!response.ok) throw new Error('模拟推送失败')
  return await response.json()
}

export async function listDeliveryReceipts(period: string): Promise<PageResult> {
  const query = new URLSearchParams({ period }).toString()
  const response = await request(`/api/delivery/receipts?${query}`)
  if (!response.ok) throw new Error('交付收据读取失败')
  return await response.json()
}
