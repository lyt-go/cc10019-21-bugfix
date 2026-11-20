<template>
  <section class="page" data-module="retention_calc">
    <header class="page-head">
      <div><h2>留存计算</h2><p class="page-desc">维护用户留存率的周期定义、分段维度与计算配置，为留存计算提供可验证的数据工程流程。</p></div>
    </header>
    <div class="analysis-summary">
      <article v-for="m in summaryMetrics" :key="m.label" class="analysis-card" data-testid="kpi-card">
        <span style="font-size:12px;color:var(--muted)">{{ m.label }}</span>
        <strong style="font-size:22px;display:block">{{ m.value }}</strong>
        <span :class="m.trend > 0 ? 'trend-up' : 'trend-down'" style="font-size:12px">{{ m.trend > 0 ? '↑' : '↓' }}{{ Math.abs(m.trend) }}%</span>
      </article>
    </div>
    <form class="analysis-form" @submit.prevent="runAnalysis">
      <label><span>配置编号</span><input v-model="params.configId" placeholder="如 RC-001" /></label>
      <label><span>观察天数（留空按已发生日期）</span><input v-model="params.observedDays" placeholder="如 7" /></label>
      <label><span>入库样本（留空按默认）</span><input v-model="params.sampleSize" placeholder="如 1000" /></label>
      <button class="btn primary" type="submit" data-testid="run-analysis">运行分析</button>
      <button class="btn" type="button" data-testid="confirm-zero" @click="confirmZero">确认零回访</button>
    </form>
    <div v-if="errorMessage" class="error-text">{{ errorMessage }}</div>
    <div v-if="chartData.length" class="analysis-chart">
      <div v-for="(bar, i) in chartData" :key="i" class="analysis-bar" :style="{ height: barHeight(bar.value) + 'px' }"><span>{{ bar.label }}</span></div>
    </div>
    <p v-if="retention?.observation_note" class="notice" data-testid="observation-note">{{ retention.observation_note }}</p>
    <dl v-if="retention" class="kv-grid" data-testid="retention-meta">
      <div><dt>完成版本</dt><dd>{{ retention.completion_version }}</dd></div>
      <div><dt>数据状态</dt><dd>{{ statusLabel(retention.data_status) }}</dd></div>
      <div><dt>已观察日期</dt><dd>{{ (retention.observed_dates || []).join('、') || '暂无' }}</dd></div>
      <div><dt>有效样本</dt><dd>{{ retention.valid_samples }}</dd></div>
      <div><dt>确认零回访</dt><dd>{{ retention.confirmed_zero_revisit ? '是' : '否' }}</dd></div>
      <div><dt>留存窗口</dt><dd>{{ retention.retention_window ?? '—' }}</dd></div>
    </dl>
    <table class="data-table" v-if="results.length">
      <thead><tr><th v-for="c in resultColumns" :key="c">{{ c }}</th></tr></thead>
      <tbody><tr v-for="row in results" :key="row.id"><td v-for="c in resultColumns" :key="c">{{ row[c] ?? '—' }}</td></tr></tbody>
    </table>
    <p v-if="!results.length && !loading" style="text-align:center;color:var(--muted)">请配置参数后运行分析</p>
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request } from '@/api/client'
const ENDPOINT = '/api/retention-calc'
const summaryMetrics = ref<{label:string;value:number;trend:number}[]>([])
const results = ref<any[]>([]); const chartData = ref<{label:string;value:number}[]>([])
const loading = ref(false)
const errorMessage = ref('')
const params = ref<Record<string,string>>({ configId: 'RC-001', observedDays: '', sampleSize: '' })
const retention = ref<any>(null)
const resultColumns = computed(() => ["配置编号", "基准日期", "初始行为", "回访行为", "留存窗口"].slice(0, 5))
function barHeight(v: number) { const max = Math.max(...chartData.value.map(d => d.value), 1); return Math.max(10, (v / max) * 140) }
function statusLabel(status: string) {
  return ({ observed: '已观察', confirmed_zero_revisit: '确认零回访', no_observation: '暂无观察点', no_sample: '样本不足', correcting_enrollment: '更正入组数据中' } as Record<string,string>)[status] || (status || '—')
}
function applyRetention(data: any) {
  retention.value = data
  const rates: number[] = Object.values(data.retention_rates || {})[0] as number[] || []
  chartData.value = rates.map((value, day) => ({ label: `D${day}`, value: Number(value) }))
  results.value = [{
    id: `${data.config_id}-${data.completion_version}`,
    '配置编号': data.config_id,
    '基准日期': data.base_date,
    '初始行为': data.init_event,
    '回访行为': data.return_event,
    '留存窗口': rates.length,
  }]
}
async function fetchSummary() { try { const res = await request(`${ENDPOINT}/summary`); if (res.ok) summaryMetrics.value = (await res.json()).metrics || [] } catch(e) {} }
async function runAnalysis() {
  loading.value = true
  errorMessage.value = ''
  try {
    if (params.value.observedDays !== '' || params.value.sampleSize !== '') {
      const body: Record<string, unknown> = { observation_state: 'observed' }
      if (params.value.observedDays !== '') body.observed_days = Number(params.value.observedDays)
      if (params.value.sampleSize !== '') body.sample_size = Number(params.value.sampleSize)
      const obs = await request(`${ENDPOINT}/${encodeURIComponent(params.value.configId)}/observation`, {
        method: 'POST', body: JSON.stringify(body),
      })
      if (!obs.ok) throw new Error('观察口径更新失败')
    }
    const res = await request(`${ENDPOINT}/${encodeURIComponent(params.value.configId)}/run`, { method: 'POST' })
    if (!res.ok) throw new Error('留存计算失败，请检查配置编号')
    const payload = await res.json()
    applyRetention(payload.entry)
    await fetchSummary()
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '运行失败'
    results.value = []
  }
  loading.value = false
}
async function confirmZero() {
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/${encodeURIComponent(params.value.configId)}/confirm-zero-revisit`, { method: 'POST' })
    if (!res.ok) throw new Error('确认零回访失败')
    const payload = await res.json()
    applyRetention(payload.entry)
    await fetchSummary()
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '确认零回访失败'
  }
}
onMounted(fetchSummary)
</script>

<style scoped>
.notice { background: #fff7e6; border: 1px solid #f5c26b; color: #8a5300; border-radius: 6px; padding: 8px 10px; font-size: 13px; }
.kv-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 16px; margin: 10px 0; padding: 12px; background: #fff; border: 1px solid var(--border); border-radius: 8px; }
.kv-grid dt { font-size: 12px; color: var(--muted); }
.kv-grid dd { margin: 2px 0 0; font-size: 13px; }
</style>
