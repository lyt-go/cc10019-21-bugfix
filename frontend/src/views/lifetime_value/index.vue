<template>
  <section class="page" data-module="lifetime_value">
    <header class="page-head">
      <div><h2>生命周期价值</h2><p class="page-desc">维护用户生命周期价值的预测模型、参数配置与校准管理，为生命周期价值提供可验证的数据工程流程。</p></div>
    </header>
    <div class="analysis-summary">
      <article v-for="m in summaryMetrics" :key="m.label" class="analysis-card" data-testid="kpi-card">
        <span style="font-size:12px;color:var(--muted)">{{ m.label }}</span>
        <strong style="font-size:22px;display:block">{{ m.value }}</strong>
        <span :class="m.trend > 0 ? 'trend-up' : 'trend-down'" style="font-size:12px">{{ m.trend > 0 ? '↑' : '↓' }}{{ Math.abs(m.trend) }}%</span>
      </article>
    </div>

    <form class="analysis-form" @submit.prevent="runAnalysis">
      <label><span>模型编号</span><input v-model="form.modelId" placeholder="如 LTV-001" /></label>
      <label><span>留存配置</span><input v-model="form.retentionConfigId" placeholder="如 RC-001" /></label>
      <label><span>跟进门槛</span><input v-model="form.threshold" placeholder="如 100" /></label>
      <button class="btn primary" type="submit" data-testid="run-analysis">运行分析</button>
    </form>

    <div v-if="errorMessage" class="error-text">{{ errorMessage }}</div>

    <div v-if="chartData.length" class="analysis-chart">
      <div v-for="(bar, i) in chartData" :key="i" class="analysis-bar" :style="{ height: barHeight(bar.value) + 'px' }"><span>{{ bar.label }}</span></div>
    </div>

    <!-- 价值校准：观察来源 + 校准系数 + 原始估值 -->
    <section v-if="calibration" class="panel" data-testid="calibration-panel">
      <div class="panel-head">
        <h3>价值校准 {{ calibration.calibration_id }} <span :class="['badge', calibration.status]">{{ statusLabel(calibration.status) }}</span></h3>
        <span class="muted">报告版本 v{{ calibration.report_version }} · {{ calibration.completion_version || '—' }}</span>
      </div>
      <p v-if="calibration.reason" class="notice" data-testid="calibration-reason">{{ calibration.reason }}</p>
      <dl class="kv-grid">
        <div><dt>观察来源</dt><dd>{{ calibration.retention_config_id }}</dd></div>
        <div><dt>已观察日期</dt><dd>{{ (calibration.observed_dates || []).join('、') || '暂无' }}</dd></div>
        <div><dt>有效样本</dt><dd>{{ calibration.valid_samples }}</dd></div>
        <div><dt>完成版本</dt><dd>{{ calibration.completion_version || '—' }}</dd></div>
        <div><dt>原始预测均值</dt><dd>{{ calibration.original_avg_ltv }}</dd></div>
        <div><dt>校准系数</dt><dd>{{ calibration.calibration_coefficient ?? '不生成' }}</dd></div>
        <div><dt>校准后均值</dt><dd>{{ calibration.calibrated_avg_ltv ?? '沿用原始预测' }}</dd></div>
        <div><dt>确认零回访</dt><dd>{{ calibration.confirmed_zero_revisit ? '是' : '否' }}</dd></div>
      </dl>
      <table class="data-table" v-if="(calibration.observed_retention_curve || []).length">
        <thead><tr><th>队列</th><th>第几天</th><th>日期</th><th>留存率</th><th>样本</th></tr></thead>
        <tbody>
          <tr v-for="(point, i) in calibration.observed_retention_curve" :key="i">
            <td>{{ point.cohort }}</td><td>{{ point.day }}</td><td>{{ point.date }}</td>
            <td>{{ point.retention_rate }}</td><td>{{ point.sample }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 跟进筛选 -->
    <section class="panel" data-testid="followup-panel">
      <div class="panel-head">
        <h3>跟进筛选</h3>
        <div class="row-actions">
          <button class="btn" type="button" data-testid="load-zero" @click="queryFollowup('confirmed_zero_revisit')">确认零回访名单</button>
          <button class="btn" type="button" data-testid="load-failed" @click="queryFollowup('calibration_failed')">校准失败名单</button>
          <button class="btn primary" type="button" data-testid="create-filter" @click="createFilter">生成筛选记录</button>
        </div>
      </div>
      <div v-if="followup">
        <p class="muted">
          场景 {{ caseLabel(followup.case) }} · 估值类型 {{ valuationLabel(followup.valuation_type) }} ·
          门槛 {{ followup.threshold }} · 可跟进 {{ followup.ready_count }} / 暂不跟进 {{ followup.blocked_count }}
        </p>
        <p v-if="followup.reason" class="notice">{{ followup.reason }}</p>
        <table class="data-table">
          <thead><tr><th>客户</th><th>原始预测</th><th>采用估值</th><th>估值类型</th><th>跟进状态</th><th>说明</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="item in visibleCustomers" :key="item.user_id">
              <td>{{ item.user_id }}</td>
              <td>{{ item.predicted_value }}</td>
              <td>{{ item.valuation }}</td>
              <td>{{ valuationLabel(item.valuation_type) }}</td>
              <td>{{ item.followup_state || item.status }}</td>
              <td>{{ item.reason }}</td>
              <td>
                <button class="link" type="button" v-if="followup.filter_id" @click="markState(item)">安排跟进</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="!visibleCustomers.length" class="muted">当前场景没有符合条件的客户</p>
      </div>
      <p v-else class="muted">选择上方场景即可查询跟进名单；生成筛选记录后可按客户标记安排跟进。</p>
    </section>

    <!-- 历史校准报告 -->
    <section class="panel" data-testid="calibration-history">
      <div class="panel-head">
        <h3>历史价值校准</h3>
        <span class="muted">共 {{ calibrations.length }} 条</span>
      </div>
      <table class="data-table" v-if="calibrations.length">
        <thead><tr><th>校准编号</th><th>版本</th><th>留存配置</th><th>状态</th><th>完成版本</th><th>原始预测</th><th>系数</th><th>原始曲线</th></tr></thead>
        <tbody>
          <tr v-for="row in calibrations" :key="row.calibration_id" :data-testid="`calibration-${row.calibration_id}`" @click="openCalibration(row.calibration_id)">
            <td>{{ row.calibration_id }}</td><td>v{{ row.report_version }}</td>
            <td>{{ row.retention_config_id }}</td><td>{{ statusLabel(row.status) }}</td>
            <td>{{ row.completion_version || '—' }}</td><td>{{ row.original_avg_ltv }}</td>
            <td>{{ row.calibration_coefficient ?? '不生成' }}</td>
            <td>{{ (row.original_curve || []).length }} 条</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">暂无校准记录，先运行分析生成价值校准数据。</p>
    </section>

    <table class="data-table" v-if="results.length">
      <thead><tr><th v-for="c in resultColumns" :key="c">{{ c }}</th></tr></thead>
      <tbody><tr v-for="row in results" :key="row.id"><td v-for="c in resultColumns" :key="c">{{ row[c] ?? '—' }}</td></tr></tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request } from '@/api/client'

const ENDPOINT = '/api/lifetime-value'
const summaryMetrics = ref<{label:string;value:number;trend:number}[]>([])
const results = ref<any[]>([]); const chartData = ref<{label:string;value:number}[]>([])
const loading = ref(false)
const errorMessage = ref('')
const form = ref({ modelId: 'LTV-001', retentionConfigId: 'RC-001', threshold: '100' })
const calibration = ref<any>(null)
const followup = ref<any>(null)
const calibrations = ref<any[]>([])
const resultColumns = computed(() => ["模型编号", "用户分群", "价值指标", "预测窗口", "衰减因子"].slice(0, 5))
const visibleCustomers = computed(() => (followup.value?.items || []).slice(0, 40))
function barHeight(v: number) { const max = Math.max(...chartData.value.map(d => d.value), 1); return Math.max(10, (v / max) * 140) }
function statusLabel(status: string) {
  return ({ calibrated: '已校准', calibration_failed: '校准失败', observed: '已观察' } as Record<string,string>)[status] || status
}
function caseLabel(value: string) {
  return ({ confirmed_zero_revisit: '确认零回访', calibration_failed: '校准失败', retention_calibrated: '留存校准' } as Record<string,string>)[value] || value
}
function valuationLabel(value: string) {
  return ({ retention_calibrated: '留存校准估值', original_prediction: '原始预测' } as Record<string,string>)[value] || value
}

async function fetchSummary() {
  try { const res = await request(`${ENDPOINT}/summary`); summaryMetrics.value = (await res.json()).metrics || [] } catch(e) {}
}
async function loadHistory() {
  try {
    const res = await request(`${ENDPOINT}/calibrations?model_id=${encodeURIComponent(form.value.modelId)}`)
    if (res.ok) calibrations.value = (await res.json()).items || []
  } catch(e) {}
}
async function openCalibration(calibrationId: string) {
  try {
    const res = await request(`${ENDPOINT}/calibrations/${encodeURIComponent(calibrationId)}`)
    if (res.ok) calibration.value = await res.json()
  } catch(e) {}
}
async function runAnalysis() {
  loading.value = true
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/${encodeURIComponent(form.value.modelId)}/calibrate`, {
      method: 'POST',
      body: JSON.stringify({ retention_config_id: form.value.retentionConfigId }),
    })
    if (!res.ok) throw new Error('价值校准失败，请检查模型与留存配置')
    const payload = await res.json()
    calibration.value = payload.entry
    const curve = payload.entry?.observed_retention_curve || []
    chartData.value = curve.slice(0, 7).map((p: any) => ({ label: `D${p.day}`, value: Number(p.retention_rate) }))
    results.value = payload.entry?.original_curve?.slice(0, 20) || []
    await loadHistory()
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '运行失败'
    results.value = []
  }
  loading.value = false
}
async function createFilter() {
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/${encodeURIComponent(form.value.modelId)}/followup-filter`, {
      method: 'POST',
      body: JSON.stringify({ threshold: Number(form.value.threshold), retention_config_id: form.value.retentionConfigId }),
    })
    if (!res.ok) throw new Error('筛选记录生成失败')
    followup.value = (await res.json()).entry
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '筛选失败'
  }
}
async function queryFollowup(scene: string) {
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/${encodeURIComponent(form.value.modelId)}/followup?threshold=${Number(form.value.threshold)}&case=${scene}`)
    if (!res.ok) throw new Error('该场景暂无可用估值')
    const data = await res.json()
    followup.value = { ...data, threshold: Number(form.value.threshold), ready_count: data.total, blocked_count: 0 }
  } catch (e) {
    followup.value = null
    errorMessage.value = e instanceof Error ? e.message : '查询失败'
  }
}
async function markState(item: any) {
  if (!followup.value?.filter_id) return
  try {
    const res = await request(`${ENDPOINT}/followups/${encodeURIComponent(followup.value.filter_id)}/customers/${encodeURIComponent(item.user_id)}/state`, {
      method: 'POST',
      body: JSON.stringify({ state: item.ready ? '可安排跟进' : '暂不跟进' }),
    })
    if (!res.ok) throw new Error('安排跟进失败')
    followup.value = (await res.json()).entry
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '安排跟进失败'
  }
}
onMounted(() => { fetchSummary(); loadHistory() })
</script>

<style scoped>
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin: 12px 0; }
.panel-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.panel-head h3 { font-size: 15px; margin: 0; }
.muted { color: var(--muted); font-size: 12px; }
.notice { background: #fff7e6; border: 1px solid #f5c26b; color: #8a5300; border-radius: 6px; padding: 8px 10px; font-size: 13px; }
.badge { font-size: 12px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); }
.badge.calibrated { background: #e7f7ed; border-color: #86d3a3; color: #116b33; }
.badge.calibration_failed { background: #fdecec; border-color: #f0a3a3; color: #a12020; }
.kv-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px 16px; margin: 10px 0; }
.kv-grid dt { font-size: 12px; color: var(--muted); }
.kv-grid dd { margin: 2px 0 0; font-size: 13px; }
</style>
