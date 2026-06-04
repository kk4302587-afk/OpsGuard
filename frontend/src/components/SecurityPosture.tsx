import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Empty, Space, Spin, Table, Tag, Timeline, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  AlertOutlined,
  BugOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloudServerOutlined,
  EyeOutlined,
  FireOutlined,
  LockOutlined,
  RadarChartOutlined,
  ReloadOutlined,
  SafetyOutlined,
  ThunderboltOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import ReactECharts from 'echarts-for-react'
import { useChatStore } from '../stores/chatStore'

const { Title, Text, Paragraph } = Typography

interface SecurityRisk {
  id: string
  title: string
  category: string
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info'
  status: string
  score_impact: number
  summary: string
  recommendations: string[]
  remediation_actions?: RemediationAction[]
}

interface AttackSource {
  ip: string
  severity: string
  attack_types: string[]
  failed_logins: number
  successful_logins: number
  web_scan_hits: number
  total_events: number
  recommendation: string
}

interface ExposedService {
  port: number
  protocol: string
  listen_address: string
  pid?: number
  process: string
  service: string
  risk: string
  reason: string
}

interface BaselineCheck {
  id: string
  title: string
  category: string
  status: 'passed' | 'warning' | 'failed' | 'unknown'
  severity: string
  evidence: string
  recommendation: string
}

interface SecurityEvent {
  timestamp: string
  type: string
  title: string
  source: string
  severity: string
  detail: string
}

interface SecurityFinding {
  id: string
  type: string
  severity: string
  summary: string
  evidence?: string
  recommendation?: string
  path?: string
  pid?: number
  user?: string
  ip?: string
}

interface RemediationAction {
  type: string
  label: string
  prompt: string
  risk: 'read' | 'write' | 'destructive'
  tool_name?: string
  target?: string
  requires_approval?: boolean
}

interface SecurityPostureData {
  scan_id: string
  generated_at: string
  hostname: string
  os: string
  security_score: number
  risk_level: 'healthy' | 'attention' | 'warning' | 'critical'
  summary: string
  metrics: Record<string, number>
  risks: SecurityRisk[]
  attack_sources: AttackSource[]
  exposed_services: ExposedService[]
  baseline_checks: BaselineCheck[]
  intrusion_findings: SecurityFinding[]
  suspicious_persistence: SecurityFinding[]
  suspicious_processes: SecurityFinding[]
  suspicious_files: SecurityFinding[]
  timeline: SecurityEvent[]
  data_sources: string[]
  errors: string[]
  scan_status: 'success' | 'partial' | 'failed'
}

const riskLevelConfig = {
  healthy: { label: '健康', color: '#34d399', tag: 'green' },
  attention: { label: '关注', color: '#60a5fa', tag: 'blue' },
  warning: { label: '警告', color: '#fbbf24', tag: 'orange' },
  critical: { label: '危险', color: '#f87171', tag: 'red' },
}

const severityConfig: Record<string, { label: string; color: string }> = {
  critical: { label: '严重', color: 'red' },
  high: { label: '高危', color: 'volcano' },
  medium: { label: '中危', color: 'orange' },
  low: { label: '低危', color: 'blue' },
  info: { label: '信息', color: 'default' },
}

const categoryLabels: Record<string, string> = {
  attack_source: '攻击来源',
  exposure: '暴露面',
  intrusion: '入侵迹象',
  account: '账号权限',
  baseline: '基线',
  persistence: '持久化',
  suspicious_process: '可疑进程',
  suspicious_file: '可疑文件',
}

const findingTypeLabels: Record<string, string> = {
  login_correlation: '爆破后登录',
  ssh_bruteforce: 'SSH 爆破',
  uid0_user: 'UID 0 用户',
  suspicious_process: '可疑进程',
  process: '可疑进程',
  cron: '可疑 Cron',
  systemd: '可疑服务',
  authorized_keys: 'SSH 公钥',
}

const baselineStatusConfig: Record<string, { label: string; color: string; icon: JSX.Element }> = {
  passed: { label: '通过', color: 'green', icon: <CheckCircleOutlined /> },
  warning: { label: '警告', color: 'orange', icon: <WarningOutlined /> },
  failed: { label: '失败', color: 'red', icon: <AlertOutlined /> },
  unknown: { label: '未知', color: 'default', icon: <EyeOutlined /> },
}

function SecurityPosture() {
  const { sendMessage, setInputValue } = useChatStore()
  const [data, setData] = useState<SecurityPostureData | null>(null)
  const [loading, setLoading] = useState(false)
  const [initialLoading, setInitialLoading] = useState(true)

  useEffect(() => {
    loadLatest()
  }, [])

  const loadLatest = async () => {
    setInitialLoading(true)
    try {
      const res = await fetch('/api/security-posture/latest')
      if (res.ok) {
        setData(await res.json())
      } else if (res.status !== 404) {
        console.error('Failed to load latest security posture:', res.status)
      }
    } catch (err) {
      console.error('Failed to load latest security posture:', err)
    } finally {
      setInitialLoading(false)
    }
  }

  const runScan = async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/security-posture/scan')
      if (res.ok) {
        setData(await res.json())
      }
    } catch (err) {
      console.error('Failed to run security posture scan:', err)
    } finally {
      setLoading(false)
      setInitialLoading(false)
    }
  }

  const startAgentAction = (action: RemediationAction, sendNow = true) => {
    window.dispatchEvent(new CustomEvent('opsguard:navigate', { detail: 'chat' }))
    if (sendNow) {
      setTimeout(() => sendMessage(action.prompt), 120)
    } else {
      setInputValue(action.prompt)
    }
  }

  const riskDistribution = useMemo(() => {
    if (!data) return []
    return [
      { name: '严重', value: data.metrics.critical || 0, itemStyle: { color: '#f87171' } },
      { name: '高危', value: data.metrics.high || 0, itemStyle: { color: '#fb923c' } },
      { name: '中危', value: data.metrics.medium || 0, itemStyle: { color: '#fbbf24' } },
      { name: '低危', value: data.metrics.low || 0, itemStyle: { color: '#60a5fa' } },
    ].map((item) => ({
      ...item,
      label: { show: item.value > 0 },
      labelLine: { show: item.value > 0 },
    }))
  }, [data])

  const categoryDistribution = useMemo(() => {
    if (!data) return []
    const counts = data.risks.reduce<Record<string, number>>((acc, risk) => {
      acc[risk.category] = (acc[risk.category] || 0) + 1
      return acc
    }, {})
    return Object.entries(counts).map(([key, value]) => ({
      name: categoryLabels[key] || key,
      value,
    }))
  }, [data])

  const scoreOption = useMemo(() => {
    const score = data?.security_score || 0
    const cfg = data ? riskLevelConfig[data.risk_level] : riskLevelConfig.healthy
    return {
      series: [{
        type: 'gauge',
        startAngle: 220,
        endAngle: -40,
        min: 0,
        max: 100,
        radius: '96%',
        progress: { show: true, width: 16, roundCap: true, itemStyle: { color: cfg.color } },
        pointer: { show: false },
        axisLine: { lineStyle: { width: 16, color: [[1, '#e2e8f0']] } },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        title: { show: true, offsetCenter: [0, '62%'], fontSize: 12, color: '#6b7280' },
        detail: {
          valueAnimation: true,
          offsetCenter: [0, '12%'],
          fontSize: 34,
          fontWeight: 700,
          formatter: '{value}',
          color: cfg.color,
        },
        data: [{ value: score, name: '安全评分' }],
      }],
    }
  }, [data])

  const riskPieOption = {
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, textStyle: { color: '#4b5563', fontSize: 11 } },
    series: [{
      type: 'pie',
      radius: ['42%', '64%'],
      center: ['50%', '48%'],
      avoidLabelOverlap: true,
      label: {
        color: '#1f2937',
        formatter: '{b}: {c}',
        fontSize: 12,
        margin: 8,
      },
      labelLine: {
        length: 12,
        length2: 14,
        lineStyle: { color: '#c8d2df' },
      },
      data: riskDistribution,
    }],
  }

  const categoryBarOption = {
    grid: { top: 20, right: 12, bottom: 28, left: 48 },
    xAxis: {
      type: 'category',
      data: categoryDistribution.map(item => item.name),
      axisLabel: { color: '#4b5563', fontSize: 11 },
      axisLine: { lineStyle: { color: '#c8d2df' } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#4b5563', fontSize: 11 },
      splitLine: { lineStyle: { color: '#e2e8f0' } },
    },
    series: [{
      type: 'bar',
      data: categoryDistribution.map(item => item.value),
      itemStyle: { color: '#059669', borderRadius: [4, 4, 0, 0] },
      barMaxWidth: 28,
    }],
  }

  const attackColumns: ColumnsType<AttackSource> = [
    {
      title: '来源 IP',
      dataIndex: 'ip',
      width: 130,
      render: (ip) => <Text code style={{ fontSize: 12 }}>{ip}</Text>,
    },
    {
      title: '类型',
      dataIndex: 'attack_types',
      render: (items: string[]) => <Space size={4} wrap>{items.map(item => <Tag key={item} color="red">{item}</Tag>)}</Space>,
    },
    {
      title: '失败/成功',
      width: 96,
      render: (_, row) => <Text style={{ fontSize: 12 }}>{row.failed_logins} / {row.successful_logins}</Text>,
    },
    {
      title: '总事件',
      dataIndex: 'total_events',
      width: 76,
      sorter: (a, b) => a.total_events - b.total_events,
    },
    {
      title: '等级',
      dataIndex: 'severity',
      width: 72,
      render: (severity) => <Tag color={severityConfig[severity]?.color || 'default'}>{severityConfig[severity]?.label || severity}</Tag>,
    },
  ]

  const portColumns: ColumnsType<ExposedService> = [
    {
      title: '端口',
      dataIndex: 'port',
      width: 76,
      render: (port) => <Text code>{port}</Text>,
    },
    {
      title: '监听地址',
      dataIndex: 'listen_address',
      width: 112,
      render: (value) => <Text code style={{ fontSize: 12 }}>{value}</Text>,
    },
    {
      title: '进程',
      dataIndex: 'process',
      ellipsis: true,
    },
    {
      title: '风险',
      dataIndex: 'risk',
      width: 72,
      render: (risk) => <Tag color={risk === 'high' ? 'red' : risk === 'medium' ? 'orange' : 'blue'}>{risk === 'high' ? '高' : risk === 'medium' ? '中' : '低'}</Tag>,
    },
  ]

  const riskColumns: ColumnsType<SecurityRisk> = [
    {
      title: '风险',
      dataIndex: 'title',
      render: (_, row) => (
        <div>
          <Text strong style={{ fontSize: 13 }}>{row.title}</Text>
          <Text style={{ display: 'block', color: 'var(--text-muted)', fontSize: 12 }} ellipsis={{ tooltip: row.summary }}>
            {row.summary}
          </Text>
        </div>
      ),
    },
    {
      title: '分类',
      dataIndex: 'category',
      width: 92,
      render: (category) => <Tag>{categoryLabels[category] || category}</Tag>,
    },
    {
      title: '等级',
      dataIndex: 'severity',
      width: 76,
      render: (severity) => <Tag color={severityConfig[severity]?.color || 'default'}>{severityConfig[severity]?.label || severity}</Tag>,
    },
    {
      title: '建议',
      dataIndex: 'recommendations',
      render: (items: string[], row) => {
        const actions = row.remediation_actions || []
        const investigate = actions.find(action => action.risk === 'read') || actions[0]
        const writeAction = actions.find(action => action.requires_approval)
        return (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
            <Text style={{ fontSize: 12, minWidth: 0, flex: 1 }} ellipsis={{ tooltip: items?.[0] }}>
              {items?.[0] || '建议调查'}
            </Text>
            <Space size={6} style={{ flexShrink: 0 }}>
              {investigate && (
                <Button size="small" icon={<EyeOutlined />} onClick={() => startAgentAction(investigate)}>
                  调查
                </Button>
              )}
              {writeAction && (
                <Button size="small" danger icon={<ThunderboltOutlined />} onClick={() => startAgentAction(writeAction)}>
                  处置
                </Button>
              )}
            </Space>
          </div>
        )
      },
    },
  ]

  const findingColumns: ColumnsType<SecurityFinding> = [
    {
      title: '类型',
      dataIndex: 'type',
      width: 96,
      render: (type) => <Tag>{findingTypeLabels[type] || type}</Tag>,
    },
    {
      title: '发现',
      dataIndex: 'summary',
      render: (_, row) => (
        <div>
          <Text strong style={{ fontSize: 13 }}>{row.summary}</Text>
          <Text style={{ display: 'block', color: 'var(--text-muted)', fontSize: 12 }} ellipsis={{ tooltip: row.evidence || row.path || '' }}>
            {row.evidence || row.path || row.recommendation || '暂无证据摘要'}
          </Text>
        </div>
      ),
    },
    {
      title: '对象',
      width: 118,
      render: (_, row) => {
        const target = row.ip || row.user || row.pid || row.path || '-'
        return <Text code style={{ fontSize: 11 }} ellipsis={{ tooltip: String(target) }}>{target}</Text>
      },
    },
    {
      title: '等级',
      dataIndex: 'severity',
      width: 76,
      render: (severity) => <Tag color={severityConfig[severity]?.color || 'default'}>{severityConfig[severity]?.label || severity}</Tag>,
    },
  ]

  const compactFindingColumns: ColumnsType<SecurityFinding> = [
    {
      title: '对象',
      render: (_, row) => (
        <div>
          <Text strong style={{ fontSize: 12 }}>{findingTypeLabels[row.type] || row.type}</Text>
          <Text style={{ display: 'block', color: 'var(--text-muted)', fontSize: 11 }} ellipsis={{ tooltip: row.path || row.evidence || row.summary }}>
            {row.path || row.evidence || row.summary}
          </Text>
        </div>
      ),
    },
    {
      title: '等级',
      dataIndex: 'severity',
      width: 72,
      render: (severity) => <Tag color={severityConfig[severity]?.color || 'default'}>{severityConfig[severity]?.label || severity}</Tag>,
    },
  ]

  const renderMetricCard = (title: string, value: number, icon: JSX.Element, color: string, subtitle: string) => (
    <Card size="small" style={{ minHeight: 104 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div>
          <Text style={{ color: 'var(--text-muted)', fontSize: 12 }}>{title}</Text>
          <div style={{ color, fontSize: 28, lineHeight: '36px', fontWeight: 700, marginTop: 4 }}>{value}</div>
          <Text style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{subtitle}</Text>
        </div>
        <div style={{ color, fontSize: 22, background: 'var(--bg-secondary)', width: 38, height: 38, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 6 }}>
          {icon}
        </div>
      </div>
    </Card>
  )

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <Title level={4} style={{ color: 'var(--text-primary)', margin: 0 }}>
            <RadarChartOutlined style={{ marginRight: 8, color: 'var(--accent-green)' }} />
            安全态势
          </Title>
          <Paragraph style={{ color: 'var(--text-secondary)', margin: '4px 0 0' }}>
            主机安全评分、攻击来源、暴露面、入侵迹象和基线风险的只读监控视图。
          </Paragraph>
        </div>
        <Space>
          {data && (
            <Tag color={data.scan_status === 'success' ? 'green' : 'orange'}>
              {data.scan_status === 'success' ? '扫描完成' : '部分完成'}
            </Tag>
          )}
          <Button type="primary" icon={<ReloadOutlined />} onClick={runScan} loading={loading}>
            {data ? '重新扫描' : '开始扫描'}
          </Button>
        </Space>
      </div>

      {(loading || initialLoading) && (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 320 }}>
          <Spin tip={loading ? '正在执行只读安全扫描...' : '正在加载最近一次安全扫描...'} size="large" />
        </div>
      )}

      {!loading && !initialLoading && !data && (
        <Card style={{ textAlign: 'center', padding: 42 }}>
          <SafetyOutlined style={{ fontSize: 48, color: 'var(--text-muted)', marginBottom: 16 }} />
          <Paragraph style={{ color: 'var(--text-secondary)' }}>
            暂无安全态势扫描结果，点击“开始扫描”生成第一份安全大屏数据。
          </Paragraph>
          <Button type="primary" icon={<ReloadOutlined />} onClick={runScan}>开始扫描</Button>
        </Card>
      )}

      {data && !initialLoading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '260px repeat(5, minmax(140px, 1fr))', gap: 12 }}>
            <Card size="small" style={{ minHeight: 162 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                <Text strong>整体安全</Text>
                <Tag color={riskLevelConfig[data.risk_level].tag}>{riskLevelConfig[data.risk_level].label}</Tag>
              </div>
              <ReactECharts option={scoreOption} style={{ height: 126 }} opts={{ renderer: 'svg' }} />
            </Card>
            {renderMetricCard('严重风险', (data.metrics.critical || 0) + (data.metrics.high || 0), <FireOutlined />, '#f87171', '需要优先调查')}
            {renderMetricCard('攻击 IP', data.metrics.attack_ips || 0, <BugOutlined />, '#fb923c', '失败登录或扫描来源')}
            {renderMetricCard('暴露端口', data.metrics.exposed_ports || 0, <CloudServerOutlined />, '#60a5fa', '当前监听服务')}
            {renderMetricCard('入侵迹象', data.metrics.intrusion_findings || 0, <ThunderboltOutlined />, '#fbbf24', '登录关联 / 可疑进程')}
            {renderMetricCard('可疑对象', (data.metrics.suspicious_persistence || 0) + (data.metrics.suspicious_files || 0), <LockOutlined />, '#a78bfa', '持久化和文件')}
          </div>

          <Card size="small">
            <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <Text strong style={{ fontSize: 15 }}>扫描摘要</Text>
                <Paragraph style={{ margin: '6px 0 0', color: 'var(--text-secondary)' }}>{data.summary}</Paragraph>
              </div>
              <Space size={18} wrap>
                <Text style={{ color: 'var(--text-muted)', fontSize: 12 }}>主机 <Text code>{data.hostname}</Text></Text>
                <Text style={{ color: 'var(--text-muted)', fontSize: 12 }}>系统 <Text code>{data.os}</Text></Text>
                <Text style={{ color: 'var(--text-muted)', fontSize: 12 }}>扫描时间 <Text code>{formatDate(data.generated_at)}</Text></Text>
              </Space>
            </div>
            {data.errors.length > 0 && (
              <div style={{ marginTop: 10 }}>
                {data.errors.slice(0, 3).map(error => <Tag key={error} color="orange">{error}</Tag>)}
              </div>
            )}
          </Card>

          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 360px', gap: 16 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <Card size="small" title={<Space><AlertOutlined />风险等级分布</Space>}>
                  <ReactECharts option={riskPieOption} style={{ height: 230 }} opts={{ renderer: 'svg' }} />
                </Card>
                <Card size="small" title={<Space><RadarChartOutlined />风险分类</Space>}>
                  <ReactECharts option={categoryBarOption} style={{ height: 230 }} opts={{ renderer: 'svg' }} />
                </Card>
              </div>

              <Card size="small" title={<Space><FireOutlined />高优先级风险</Space>}>
                <Table
                  size="small"
                  rowKey="id"
                  columns={riskColumns}
                  dataSource={data.risks.slice(0, 8)}
                  pagination={false}
                  locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无风险" /> }}
                />
              </Card>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <Card size="small" title={<Space><BugOutlined />攻击来源</Space>}>
                  <Table
                    size="small"
                    rowKey="ip"
                    columns={attackColumns}
                    dataSource={data.attack_sources}
                    pagination={false}
                    locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无攻击来源" /> }}
                  />
                </Card>
                <Card size="small" title={<Space><CloudServerOutlined />暴露服务</Space>}>
                  <Table
                    size="small"
                    rowKey={(row) => `${row.listen_address}:${row.port}:${row.pid || ''}`}
                    columns={portColumns}
                    dataSource={data.exposed_services.slice(0, 10)}
                    pagination={false}
                    locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无监听服务" /> }}
                  />
                </Card>
              </div>

              <Card size="small" title={<Space><ThunderboltOutlined />入侵迹象</Space>}>
                <Table
                  size="small"
                  rowKey="id"
                  columns={findingColumns}
                  dataSource={data.intrusion_findings}
                  pagination={false}
                  locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无入侵迹象" /> }}
                />
              </Card>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <Card size="small" title={<Space><ThunderboltOutlined />可疑持久化</Space>}>
                  <Table
                    size="small"
                    rowKey="id"
                    columns={compactFindingColumns}
                    dataSource={data.suspicious_persistence.slice(0, 8)}
                    pagination={false}
                    locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可疑持久化" /> }}
                  />
                </Card>
                <Card size="small" title={<Space><LockOutlined />可疑进程/文件</Space>}>
                  <Table
                    size="small"
                    rowKey={(row) => row.id || row.path || String(row.pid)}
                    columns={compactFindingColumns}
                    dataSource={[...data.suspicious_processes, ...data.suspicious_files].slice(0, 8)}
                    pagination={false}
                    locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可疑进程或文件" /> }}
                  />
                </Card>
              </div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
              <Card size="small" title={<Space><CheckCircleOutlined />基线检查</Space>}>
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  {data.baseline_checks.map(check => {
                    const cfg = baselineStatusConfig[check.status] || baselineStatusConfig.unknown
                    return (
                      <div key={check.id} style={{ padding: '8px 0', borderBottom: '1px solid var(--border-color)' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                          <Text strong style={{ fontSize: 13 }}>{check.title}</Text>
                          <Tag color={cfg.color}>{cfg.icon} {cfg.label}</Tag>
                        </div>
                        <Text style={{ display: 'block', color: 'var(--text-muted)', fontSize: 12, marginTop: 3 }}>
                          {check.evidence}
                        </Text>
                      </div>
                    )
                  })}
                </Space>
              </Card>

              <Card size="small" title={<Space><ClockCircleOutlined />安全事件时间线</Space>}>
                {data.timeline.length === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无近期安全事件" />
                ) : (
                  <Timeline
                    items={data.timeline.slice(0, 10).map(event => ({
                      color: event.severity === 'medium' ? 'orange' : event.severity === 'info' ? 'blue' : 'red',
                      children: (
                        <div>
                          <Text strong style={{ fontSize: 12 }}>{event.title}</Text>
                          <Text style={{ display: 'block', color: 'var(--text-muted)', fontSize: 11 }}>
                            {event.timestamp} · {event.source}
                          </Text>
                          <Text style={{ display: 'block', color: 'var(--text-secondary)', fontSize: 12 }} ellipsis={{ tooltip: event.detail }}>
                            {event.detail}
                          </Text>
                        </div>
                      ),
                    }))}
                  />
                )}
              </Card>

              <Card size="small" title={<Space><EyeOutlined />Agent 研判入口</Space>}>
                <Paragraph style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 10 }}>
                  风险项支持一键调查和一键发起处置请求。封禁 IP、锁用户、删除文件、关闭端口等动作会进入现有审批弹窗。
                </Paragraph>
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Button
                    block
                    icon={<ThunderboltOutlined />}
                    onClick={() => {
                      const firstAction = data.risks.find(risk => risk.remediation_actions?.length)?.remediation_actions?.[0]
                      if (firstAction) startAgentAction(firstAction)
                      else window.dispatchEvent(new CustomEvent('opsguard:navigate', { detail: 'chat' }))
                    }}
                  >
                    调查最高优先级风险
                  </Button>
                  <Text style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                    写操作不会在大屏直接执行，必须由 Agent 触发工具并等待审批。
                  </Text>
                </Space>
              </Card>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function formatDate(value: string) {
  if (!value) return '-'
  return value.slice(0, 19).replace('T', ' ')
}

export default SecurityPosture
