<template>
  <section class="page" data-module="query">
    <header class="page-head">
      <div>
        <h2>交互查询</h2>
        <p class="page-desc">
          返回字段只能从当前身份的可读列里选择；执行时服务端按最新范围重新裁决，
          手填越权字段会被拒绝并留痕。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记查询语句</button>
        <button class="btn" type="button" @click="exportRows">导出查询清单</button>
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
        <span>语句编号</span>
        <input v-model="filters.keyword" placeholder="按语句编号检索" />
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

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>准入范围</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <span v-if="row.admitted_columns?.length" class="column-list">
              <span
                v-for="column in row.admitted_columns"
                :key="column"
                class="column-chip granted"
              >{{ column }}</span>
            </span>
            <span v-else class="hint-text">尚未建立准入证据</span>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :disabled="row.status === '已完成' && action !== '保存结果'"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="viewResult(row)">查看结果</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无查询语句，可先登记一条</td>
        </tr>
      </tbody>
    </table>

    <section v-if="showCreate" class="panel">
      <header class="panel-head">
        <h3>登记查询语句</h3>
        <button class="link" type="button" @click="showCreate = false">收起</button>
      </header>
      <div class="form-row">
        <label>
          语句编号
          <input v-model="createForm.code" placeholder="QUER-0010" />
        </label>
        <label>
          目标数据集
          <input v-model="createForm.dataset" placeholder="DATA-0003 或数据集名称" />
        </label>
        <label>
          查询表达式
          <input v-model="createForm.expression" placeholder="SELECT ..." />
        </label>
      </div>
      <div class="form-row">
        <label>
          返回字段（只列当前可读列）
          <select v-model="createForm.columns" multiple size="6">
            <option v-for="column in createColumns" :key="column" :value="column">{{ column }}</option>
          </select>
        </label>
        <button class="btn ghost" type="button" @click="loadCreateColumns">刷新可读列</button>
        <button class="btn primary" type="button" @click="createQuery">登记</button>
      </div>
      <p class="hint-text">
        {{ createHint }}
      </p>
    </section>

    <section v-if="result" class="panel" data-panel="result">
      <header class="panel-head">
        <h3>查询 #{{ result.query_id }} 结果</h3>
        <button class="link" type="button" @click="result = null">关闭</button>
      </header>
      <p class="hint-text">{{ result.note }}</p>
      <div class="panel-grid">
        <div>
          <h4>完成时准入字段</h4>
          <div class="column-list">
            <span v-for="column in result.admitted_columns" :key="column" class="column-chip">
              {{ column }}
            </span>
          </div>
          <h4>本次实际返回字段</h4>
          <div class="column-list">
            <span
              v-for="column in result.served_columns"
              :key="column"
              class="column-chip granted"
            >{{ column }}</span>
            <span
              v-for="column in result.withheld_columns"
              :key="column"
              class="column-chip denied"
            >{{ column }}（已收回）</span>
          </div>
        </div>
        <div>
          <h4>准入证据</h4>
          <ul class="timeline">
            <li class="grant">
              角色 {{ result.evidence?.role }} · 版本 {{ result.evidence?.revision }} ·
              决定 {{ result.evidence?.decision_ids?.join('、') || '公开字段/所有者' }}
              <div class="hint-text">摘要 {{ result.evidence?.digest }}</div>
              <div class="hint-text">评估时间 {{ result.evidence?.evaluated_at }}</div>
            </li>
          </ul>
        </div>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in result.served_columns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in result.rows" :key="index">
            <td v-for="column in result.served_columns" :key="column">{{ row[column] ?? '—' }}</td>
          </tr>
          <tr v-if="!result.rows.length">
            <td :colspan="result.served_columns.length || 1" class="empty-state">
              当前没有可下发的行
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条查询记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="okMessage" class="ok-text">{{ okMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { readError, request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/query'
const columns = ['语句编号', '查询表达式', '目标数据集', '返回字段', '排序条件', '分页大小', '最大行数', '超时秒数']
const actions = ['提交执行', '启动查询', '终止查询', '保存结果']
const statuses = ['草稿', '待执行', '执行中', '已完成', '已超时']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const okMessage = ref('')
const filters = reactive({ keyword: '', status: '' })
const stats = ref([
  { label: '查询语句总数', value: 0 },
  { label: '已完成查询', value: 0 },
  { label: '执行中查询', value: 0 },
])
const showCreate = ref(false)
const createForm = reactive({ code: '', dataset: '', expression: '', columns: [] as string[] })
const createColumns = ref<string[]>([])
const createHint = ref('选择目标数据集后按“刷新可读列”获取服务端可读字段')
const result = ref<any | null>(null)

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
  showCreate.value = true
}

async function loadCreateColumns() {
  createColumns.value = []
  createHint.value = '正在读取服务端可读列…'
  if (!createForm.dataset.trim()) {
    createHint.value = '先填写目标数据集编号或名称'
    return
  }
  const list = await request('/api/datastore').then((r) => r.json())
  const match = (list.items ?? []).find(
    (item: Row) =>
      String(item.id) === createForm.dataset.trim() ||
      item['数据集编号'] === createForm.dataset.trim() ||
      item['数据集名称'] === createForm.dataset.trim()
  )
  if (!match) {
    createHint.value = '当前身份看不到这个数据集，或是编号/名称不匹配'
    return
  }
  const permission = await request(`/api/datastore/${match.id}/access/permission`).then((r) => r.json())
  createColumns.value = permission.can_manage
    ? permission.all_columns ?? []
    : permission.readable_columns ?? []
  createHint.value = createColumns.value.length
    ? `当前可读：${createColumns.value.join('、')}`
    : '当前身份没有任何可读字段，执行会被拒绝'
}

async function createQuery() {
  errorMessage.value = ''
  okMessage.value = ''
  const response = await request(ENDPOINT, {
    method: 'POST',
    body: JSON.stringify({
      values: {
        语句编号: createForm.code,
        查询表达式: createForm.expression,
        目标数据集: createForm.dataset,
        返回字段: createForm.columns.join(', '),
      },
    }),
  })
  const payload = await response.json()
  if (!response.ok || payload.ok === false) {
    errorMessage.value = payload.detail ?? payload.message ?? '查询语句登记失败'
    return
  }
  okMessage.value = '查询语句已登记'
  showCreate.value = false
  createForm.code = ''
  createForm.expression = ''
  await reload()
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  okMessage.value = ''
  const response = await request(`${ENDPOINT}/${row.id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ values: { action } }),
  })
  const payload = await response.json()
  if (!response.ok || payload.ok === false) {
    errorMessage.value = payload.detail ?? payload.message ?? '查询动作未生效'
    return
  }
  okMessage.value = `已${action}`
  await reload()
  if (action === '保存结果') await viewResult(row)
}

async function viewResult(row: Row) {
  const response = await request(`${ENDPOINT}/${row.id}/result`)
  const payload = await response.json()
  if (!response.ok) {
    errorMessage.value = payload.detail ?? '结果读取失败'
    return
  }
  result.value = payload
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams({ keyword: filters.keyword, status: filters.status }).toString()
  const response = await request(`${ENDPOINT}?${query}`)
  if (!response.ok) {
    errorMessage.value = await readError(response, '查询语句列表读取失败')
    return
  }
  const payload = await response.json()
  rows.value = payload.items ?? []
  total.value = payload.total ?? rows.value.length
  stats.value = [
    { label: '查询语句总数', value: total.value },
    { label: '已完成查询', value: rows.value.filter((row) => row.status === '已完成').length },
    { label: '执行中查询', value: rows.value.filter((row) => row.status === '执行中').length },
  ]
}

onMounted(reload)
</script>
