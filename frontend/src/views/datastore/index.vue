<template>
  <section class="page" data-module="datastore">
    <header class="page-head">
      <div>
        <h2>数据集管理</h2>
        <p class="page-desc">
          维护数据集定义与列级借阅：所有者、借阅者与公开浏览者按实际归属裁决，
          前端只渲染服务端返回的可读字段。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记数据集定义</button>
        <button class="btn" type="button" @click="exportRows">导出可读清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>数据集编号 / 名称</span>
        <input v-model="filters.keyword" placeholder="按编号或名称检索" />
      </label>
      <label class="filter-item">
        <span>状态</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <p class="hint-text">
      当前身份：{{ actorLabel }}。可读字段由服务端按归属裁决；表格里没有的列，前端没有隐藏它的能力。
    </p>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in visibleColumns" :key="column">{{ column }}</th>
          <th>归属 / 角色</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ selected: selectedId === row.id }">
          <td v-for="column in visibleColumns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <span class="badge" :class="roleClass(row)">{{ row.owner_name || '无所有者' }}</span>
            <div class="hint-text">{{ row._role }}</div>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="selectRow(row)">借阅详情</button>
            <button
              v-for="action in ownerActions"
              :key="action"
              class="link"
              type="button"
              :disabled="!row.can_manage"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button
              v-if="row.ownerless"
              class="link"
              type="button"
              :disabled="!canClaim"
              @click="claim(row)"
            >
              认领数据集
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="visibleColumns.length + 2" class="empty-state">暂无可读数据集，可先登记数据集定义</td>
        </tr>
      </tbody>
    </table>
    <p class="table-note">
      表格列只是当前身份可读字段的并集；某个数据集上不可读的字段不会出现值，也不会出现字段名。
    </p>

    <section v-if="selected" class="panel" data-panel="borrow">
      <header class="panel-head">
        <h3>列级借阅 · {{ selected.dataset_name || selected.dataset_code || `数据集 ${selectedId}` }}</h3>
        <span class="badge" :class="selected.can_manage ? 'owner' : 'public'">
          {{ selected.viewer_role }} · 版本 {{ selected.current_revision }}
        </span>
      </header>

      <div class="panel-grid">
        <div>
          <h4>可读字段</h4>
          <div v-if="columnNames.length" class="column-list">
            <span
              v-for="column in columnNames"
              :key="column"
              class="column-chip"
              :class="columnState(column)"
            >
              {{ column }}
            </span>
          </div>
          <p v-else class="hint-text">
            当前没有任何可读字段。{{ selected.ownerless ? '数据集尚未认领，由维护者认领后开放。' : '可提交列级借阅申请。' }}
          </p>
          <p class="hint-text">
            公开字段：{{ selected.public_columns.join('、') || '无' }}；获准字段：{{ selected.granted_columns.join('、') || '无' }}
          </p>
          <ul v-if="selected.conflicts.length" class="timeline">
            <li v-for="conflict in selected.conflicts" :key="conflict.column" class="pending">
              字段「{{ conflict.column }}」有 {{ conflict.candidates.length }} 份授权冲突 →
              按优先级胜出：决定 {{ conflict.winner.decision_id }}（来源 {{ conflict.winner.source }}，
              优先级 {{ conflict.winner.priority }}，版本 {{ conflict.winner.revision }}）
            </li>
          </ul>
        </div>

        <div v-if="selected.can_borrow">
          <h4>申请借阅</h4>
          <div class="form-row">
            <label>
              申请字段
              <select v-model="applyForm.column" multiple size="6">
                <option v-for="column in selected.requestable_columns" :key="column" :value="column">
                  {{ column }}
                </option>
              </select>
            </label>
          </div>
          <div class="form-row">
            <label>
              用途
              <input v-model="applyForm.purpose" placeholder="说明使用场景" />
            </label>
            <button class="btn primary" type="button" @click="apply">提交申请</button>
          </div>
          <p v-if="applyResult" class="hint-text">{{ applyResult }}</p>
        </div>

        <div v-else-if="selected.ownerless">
          <h4>无所有者数据</h4>
          <p class="hint-text">
            这条样例数据还没有归属。可浏览但不能修改；维护者认领后才会开放借阅申请。
          </p>
          <button v-if="canClaim" class="btn primary" type="button" @click="claim({ id: selectedId })">
            认领为我的数据集
          </button>
        </div>

        <div v-else-if="selected.is_owner || selected.viewer_role === '平台管理员'">
          <h4>借阅配置</h4>
          <div class="form-row">
            <label>
              公开字段
              <select v-model="configForm.publicColumns" multiple size="6">
                <option v-for="column in selected.all_columns ?? []" :key="column" :value="column">
                  {{ column }}
                </option>
              </select>
            </label>
            <label>
              开放申请
              <select v-model="configForm.borrowEnabled">
                <option :value="true">是</option>
                <option :value="false">否</option>
              </select>
            </label>
            <button class="btn" type="button" @click="saveConfig">保存配置</button>
          </div>
        </div>
      </div>

      <div v-if="selected.can_manage" class="panel-grid" style="margin-top: 12px">
        <div>
          <h4>待审批申请</h4>
          <ul v-if="pendingRequests.length" class="timeline">
            <li v-for="request in pendingRequests" :key="request.id" class="pending">
              #{{ request.id }} {{ request.requester_name }} 申请
              {{ request.columns.join('、') }} · 版本 {{ request.base_revision }}
              <div class="hint-text">{{ request.purpose || '未填写用途' }}</div>
              <div class="form-row" style="margin-top: 6px">
                <label>
                  批准字段
                  <select v-model="decisionForm[request.id].columns" multiple size="4">
                    <option v-for="column in request.columns" :key="column" :value="column">
                      {{ column }}
                    </option>
                  </select>
                </label>
                <label>
                  到期时间（可空）
                  <input v-model="decisionForm[request.id].expiresAt" type="datetime-local" />
                </label>
                <button class="btn primary" type="button" @click="decide(request, '批准')">批准</button>
                <button class="btn" type="button" @click="decide(request, '拒绝')">拒绝</button>
              </div>
            </li>
          </ul>
          <p v-else class="hint-text">暂无待审批申请。</p>
        </div>

        <div>
          <h4>直接授权 / 显式撤销</h4>
          <div class="form-row">
            <label>
              使用方
              <select v-model="grantForm.subjectId">
                <option v-for="item in borrowers" :key="item.id" :value="item.id">{{ item.name }}</option>
              </select>
            </label>
            <label>
              字段
              <select v-model="grantForm.columns" multiple size="5">
                <option v-for="column in selected.all_columns ?? []" :key="column" :value="column">
                  {{ column }}
                </option>
              </select>
            </label>
            <label>
              到期时间（可空）
              <input v-model="grantForm.expiresAt" type="datetime-local" />
            </label>
            <label>
              优先级
              <input v-model="grantForm.priority" type="number" min="1" max="99" />
            </label>
            <button class="btn primary" type="button" @click="grant">授权</button>
          </div>
          <div class="form-row">
            <label>
              撤销字段（不选表示全部有效字段）
              <select v-model="grantForm.revokeColumns" multiple size="5">
                <option v-for="column in selected.all_columns ?? []" :key="column" :value="column">
                  {{ column }}
                </option>
              </select>
            </label>
            <label>
              撤销原因
              <input v-model="grantForm.reason" placeholder="收回原因" />
            </label>
            <button class="btn" type="button" @click="revoke">显式撤销</button>
          </div>
          <p class="hint-text">
            提交时带上当前版本 {{ selected.current_revision }}；版本变化会被服务端拒绝，
            防止过期客户端继续追加授权。
          </p>
        </div>
      </div>

      <div class="panel-grid" style="margin-top: 12px">
        <div>
          <h4>授权决定链</h4>
          <ul v-if="decisions.length" class="timeline">
            <li
              v-for="item in decisions"
              :key="item.id"
              :class="item.decision_type === '撤销' ? 'revoke' : item.status === '有效' ? 'grant' : 'expire'"
            >
              #{{ item.id }} {{ item.decision_type }} · {{ item.columns.join('、') }} ·
              {{ item.source }} · 优先级 {{ item.priority }} · 版本 {{ item.revision }} ·
              {{ item.status }} · 到期 {{ item.expires_at || '长期' }}
            </li>
          </ul>
          <p v-else class="hint-text">暂无可见授权决定。</p>
        </div>
        <div>
          <h4>审计时间线</h4>
          <ul v-if="events.length" class="timeline">
            <li
              v-for="item in events"
              :key="item.id"
              :class="item.action === '撤销' ? 'revoke' : item.action === '授权到期' ? 'expire' : item.action === '申请' ? 'pending' : 'grant'"
            >
              {{ item.at }} · {{ item.action }} · {{ item.subject_id || item.actor_name }} ·
              {{ item.columns.join('、') || '—' }}
              <div class="hint-text">{{ item.detail || item.outcome }}</div>
            </li>
          </ul>
          <p v-else class="hint-text">暂无可见审计记录。</p>
        </div>
      </div>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据集管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="okMessage" class="ok-text">{{ okMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { ACTORS, currentActorId, readError, request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/datastore'
const fixedColumns = ['数据集编号', '数据集名称', '来源表名', '字段列表', '更新策略', '数据采样比例', '生命周期天数', '缓存开关']
const statuses = ['草稿', '待审核', '已生效', '已废弃']
const ownerActions = ['提交审核', '生效发布', '废弃下线']
const actorId = currentActorId()
const actorLabel = ACTORS.find((item) => item.id === actorId)?.name ?? '公开浏览者'
const canClaim = ['owner-alpha', 'owner-beta', 'platform-admin'].includes(actorId)
const borrowers = ACTORS.filter((item) => item.id && !['owner-alpha', 'owner-beta', 'platform-admin'].includes(item.id))

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const okMessage = ref('')
const filters = reactive({ keyword: '', status: '' })

const selectedId = ref<number | null>(null)
const selected = ref<any | null>(null)
const decisions = ref<any[]>([])
const events = ref<any[]>([])
const pendingRequests = ref<any[]>([])
const applyResult = ref('')
const applyForm = reactive({ column: [] as string[], purpose: '' })
const configForm = reactive({ publicColumns: [] as string[], borrowEnabled: false })
const grantForm = reactive({
  subjectId: borrowers[0]?.id ?? '',
  columns: [] as string[],
  expiresAt: '',
  priority: '60',
  revokeColumns: [] as string[],
  reason: '',
})
const decisionForm = reactive<Record<number, { columns: string[]; expiresAt: string }>>({})
const stats = ref([
  { label: '可读数据集', value: 0 },
  { label: '我可管理', value: 0 },
  { label: '待审批申请', value: 0 },
])
const visibleColumns = computed(() => {
  const seen = new Set<string>()
  for (const row of rows.value) {
    for (const column of fixedColumns) {
      if (row[column] !== undefined) seen.add(column)
    }
  }
  return fixedColumns.filter((column) => seen.has(column))
})
const columnNames = computed<string[]>(() => {
  if (!selected.value) return []
  if (selected.value.can_manage) return selected.value.all_columns ?? []
  const names = new Set<string>()
  for (const column of fixedColumns) {
    if (selected.value.readable_columns?.includes(column) || selected.value.requestable_columns?.includes(column)) {
      names.add(column)
    }
  }
  for (const column of selected.value.requestable_columns ?? []) names.add(column)
  return fixedColumns.filter((column) => names.has(column))
})

function columnState(column: string): string {
  if (!selected.value) return ''
  if (selected.value.readable_columns.includes(column)) {
    return selected.value.granted_columns.includes(column) ? 'granted' : 'readable'
  }
  return 'denied'
}

function roleClass(row: Row): string {
  if (row._role === '所有者' || row._role === '平台管理员') return 'owner'
  if (row._role === '借阅者') return 'readable'
  return 'public'
}

function resetFilters() {
  filters.keyword = ''
  filters.status = ''
  void reload()
}

function exportRows() {
  void request(`${ENDPOINT}/export`).then((response) => response.json()).then((payload) => {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    window.open(URL.createObjectURL(blob), '_blank')
  })
}

function openCreate() {
  okMessage.value = '登记入口：使用 POST /api/datastore，登记人自动成为所有者。'
}

async function claim(row: Row) {
  await post(`${ENDPOINT}/${row.id}/access/claim`, {}, '数据集已认领')
}

async function runAction(action: string, row: Row) {
  await post(`${ENDPOINT}/${row.id}/actions`, { action }, `已${action}`)
}

function selectRow(row: Row) {
  selectedId.value = Number(row.id)
  void loadDetail(Number(row.id))
}

async function loadDetail(datasetId: number) {
  const [permission, decisionPayload, eventPayload, requestPayload] = await Promise.all([
    request(`${ENDPOINT}/${datasetId}/access/permission`).then((r) => r.json()),
    request(`${ENDPOINT}/${datasetId}/access/decisions`).then((r) => r.json()).catch(() => ({ items: [] })),
    request(`${ENDPOINT}/${datasetId}/access/events?size=50`).then((r) => r.json()).catch(() => ({ items: [] })),
    request(`${ENDPOINT}/access/requests?dataset_id=${datasetId}&size=50`).then((r) => r.json()).catch(() => ({ items: [] })),
  ])
  selected.value = permission
  decisions.value = decisionPayload.items ?? []
  events.value = eventPayload.items ?? []
  pendingRequests.value = (requestPayload.items ?? []).filter((item: Row) => item.status === '待审批')
  for (const item of pendingRequests.value) {
    if (!decisionForm[item.id]) {
      decisionForm[item.id] = { columns: [...(item.columns ?? [])], expiresAt: '' }
    }
  }
  configForm.publicColumns = [...(permission.public_columns ?? [])]
  configForm.borrowEnabled = Boolean(permission.borrow_enabled)
  grantForm.columns = []
  grantForm.revokeColumns = []
  applyForm.column = []
  applyResult.value = ''
}

async function apply() {
  if (!selected.value) return
  const response = await request(`${ENDPOINT}/${selected.value.dataset_id}/access/requests`, {
    method: 'POST',
    body: JSON.stringify({
      values: { columns: applyForm.column, purpose: applyForm.purpose, expected_revision: selected.value.current_revision },
    }),
  })
  const payload = await response.json()
  if (!response.ok || payload.ok === false) {
    errorMessage.value = payload.detail ?? payload.message ?? '申请未提交'
    return
  }
  okMessage.value = '借阅申请已提交，等待所有者审批'
  applyResult.value = `申请 #${payload.entry.id}：${payload.entry.status}`
  await loadDetail(selected.value.dataset_id)
}

async function decide(request_: Row, decision: string) {
  const form = decisionForm[request_.id]
  const response = await request(`${ENDPOINT}/access/requests/${request_.id}/decision`, {
    method: 'POST',
    body: JSON.stringify({
      values: {
        decision,
        columns: decision === '批准' ? form.columns : [],
        expires_at: form.expiresAt ? toIso(form.expiresAt) : null,
      },
    }),
  })
  const payload = await response.json()
  if (!response.ok || payload.ok === false) {
    errorMessage.value = payload.detail ?? payload.message ?? '裁决未生效'
    return
  }
  okMessage.value = decision === '批准' ? '已批准，授权按到期时间生效' : '已拒绝申请'
  await loadDetail(selected.value.dataset_id)
}

async function saveConfig() {
  const response = await request(`${ENDPOINT}/${selected.value.dataset_id}/access/config`, {
    method: 'POST',
    body: JSON.stringify({
      values: {
        public_columns: configForm.publicColumns,
        borrow_enabled: configForm.borrowEnabled,
      },
    }),
  })
  const payload = await response.json()
  if (!payload.ok) {
    errorMessage.value = payload.message
    return
  }
  okMessage.value = '借阅配置已更新'
  await loadDetail(selected.value.dataset_id)
}

async function grant() {
  const response = await request(`${ENDPOINT}/${selected.value.dataset_id}/access/decisions`, {
    method: 'POST',
    body: JSON.stringify({
      values: {
        subject_id: grantForm.subjectId,
        columns: grantForm.columns,
        expires_at: grantForm.expiresAt ? toIso(grantForm.expiresAt) : null,
        priority: Number(grantForm.priority || 60),
        expected_revision: selected.value.current_revision,
      },
    }),
  })
  const payload = await response.json()
  if (!payload.ok) {
    errorMessage.value = payload.message
    return
  }
  okMessage.value = '列级授权已生效'
  await loadDetail(selected.value.dataset_id)
}

async function revoke() {
  const response = await request(`${ENDPOINT}/${selected.value.dataset_id}/access/revoke`, {
    method: 'POST',
    body: JSON.stringify({
      values: {
        subject_id: grantForm.subjectId,
        columns: grantForm.revokeColumns,
        expected_revision: selected.value.current_revision,
        reason: grantForm.reason,      },
    }),
  })
  const payload = await response.json()
  if (!payload.ok) {
    errorMessage.value = payload.message
    return
  }
  okMessage.value = payload.message
  await loadDetail(selected.value.dataset_id)
}

async function post(path: string, values: Record<string, unknown>, okText: string) {
  errorMessage.value = ''
  okMessage.value = ''
  const response = await request(path, { method: 'POST', body: JSON.stringify({ values }) })
  const payload = await response.json().catch(() => ({ ok: false, message: '响应不可解析' }))
  if (!response.ok || payload.ok === false) {
    errorMessage.value = payload.detail ?? payload.message ?? await readError(response, '操作未生效')
    return
  }
  okMessage.value = okText
  await reload()
  if (selectedId.value) await loadDetail(selectedId.value)
}

function toIso(value: string): string {
  return new Date(value).toISOString()
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams({ keyword: filters.keyword, status: filters.status }).toString()
  const response = await request(`${ENDPOINT}?${query}`)
  if (!response.ok) {
    errorMessage.value = await readError(response, '数据集定义列表读取失败')
    return
  }
  const payload = await response.json()
  rows.value = payload.items ?? []
  total.value = payload.total ?? rows.value.length
  stats.value = [
    { label: '可读数据集', value: rows.value.length },
    { label: '我可管理', value: rows.value.filter((row) => row.can_manage).length },
    { label: '待审批申请', value: 0 },
  ]
  if (selectedId.value && rows.value.some((row) => row.id === selectedId.value)) {
    await loadDetail(selectedId.value)
  }
}

onMounted(reload)
</script>
