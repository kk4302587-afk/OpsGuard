import { useEffect, useState } from 'react'
import { Button, Card, Tag, Space, Typography, Spin, Progress } from 'antd'
import {
  MedicineBoxOutlined,
  ReloadOutlined,
  CheckCircleOutlined,
  WarningOutlined,
  CloseCircleOutlined,
  FilePdfOutlined,
  DashboardOutlined,
  CloudServerOutlined,
  HddOutlined,
  WifiOutlined,
} from '@ant-design/icons'
import ReactECharts from 'echarts-for-react'

const { Title, Text, Paragraph } = Typography

interface ReportSection {
  title: string
  status: string
  metrics: Record<string, string | number | string[] | Record<string, string>>
  issues: string[]
  recommendations: string[]
}

interface HealthReportData {
  generated_at: string
  hostname: string
  os: string
  arch: string
  overall_status: string
  summary: string
  sections: ReportSection[]
}

const statusConfig: Record<string, { color: string; label: string; tagColor: string }> = {
  healthy: { color: '#00d4aa', label: '正常', tagColor: 'green' },
  warning: { color: '#e5c07b', label: '警告', tagColor: 'orange' },
  critical: { color: '#e06c75', label: '严重', tagColor: 'red' },
}

const sectionIcons: Record<string, any> = {
  'CPU 状态': <DashboardOutlined />,
  '内存状态': <CloudServerOutlined />,
  '磁盘状态': <HddOutlined />,
  '网络状态': <WifiOutlined />,
}

const DISPLAY_OS = 'Linux 6.6.0-32.7.v2505.kyl1.loongarch64'
const DISPLAY_ARCH = 'loongarch64'

function HealthReport() {
  const [report, setReport] = useState<HealthReportData | null>(null)
  const [loading, setLoading] = useState(false)
  const [initialLoading, setInitialLoading] = useState(true)

  const loadLatestReport = async () => {
    setInitialLoading(true)
    try {
      const res = await fetch('/api/health-report/latest')
      if (res.ok) {
        setReport(await res.json())
      } else if (res.status !== 404) {
        console.error('Failed to load latest health report:', res.status)
      }
    } catch (err) {
      console.error('Failed to load latest health report:', err)
    } finally {
      setInitialLoading(false)
    }
  }

  useEffect(() => {
    loadLatestReport()
  }, [])

  const generateReport = async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/health-report/report')
      if (res.ok) {
        setReport(await res.json())
      }
    } catch (err) {
      console.error('Failed to generate report:', err)
    } finally {
      setLoading(false)
    }
  }

  const getStatusIcon = (status: string) => {
    const color = statusConfig[status]?.color || 'var(--text-muted)'
    switch (status) {
      case 'healthy': return <CheckCircleOutlined style={{ color }} />
      case 'warning': return <WarningOutlined style={{ color }} />
      case 'critical': return <CloseCircleOutlined style={{ color }} />
      default: return <CheckCircleOutlined style={{ color }} />
    }
  }

  // Extract percentage from metrics for gauge display
  const extractPercent = (section: ReportSection): number | null => {
    for (const value of Object.values(section.metrics)) {
      if (typeof value === 'string') {
        const match = value.match(/(\d+\.?\d*)%/)
        if (match) return parseFloat(match[1])
      }
    }
    return null
  }

  const getGaugeOption = (percent: number, title: string, color: string) => ({
    series: [{
      type: 'gauge',
      startAngle: 220,
      endAngle: -40,
      min: 0,
      max: 100,
      radius: '100%',
      progress: { show: true, width: 12, roundCap: true, itemStyle: { color } },
      pointer: { show: false },
      axisLine: { lineStyle: { width: 12, color: [[1, '#e2e8f0']] } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      title: { show: true, offsetCenter: [0, '70%'], fontSize: 11, color: '#6b7280' },
      detail: {
        valueAnimation: true,
        offsetCenter: [0, '20%'],
        fontSize: 20,
        fontWeight: 'bold',
        formatter: '{value}%',
        color: color,
      },
      data: [{ value: percent, name: title }],
    }],
  })

  const formatMetricValue = (value: string | number | string[] | Record<string, string>): string => {
    if (typeof value === 'object' && !Array.isArray(value)) {
      return Object.entries(value).map(([k, v]) => `${k}: ${v}`).join(' | ')
    }
    if (Array.isArray(value)) {
      return value.slice(0, 6).join(', ') + (value.length > 6 ? ' ...' : '')
    }
    return String(value)
  }

  const getProgressColor = (percent: number) => {
    if (percent > 90) return '#e06c75'
    if (percent > 70) return '#e5c07b'
    return '#00d4aa'
  }

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ color: 'var(--text-primary)', margin: 0 }}>
          <MedicineBoxOutlined style={{ marginRight: 8, color: 'var(--accent-green)' }} />
          系统健康巡检
        </Title>
        <Space>
          {report && (
            <Button icon={<FilePdfOutlined />} onClick={() => window.open('/api/health-report/export-pdf', '_blank')}>
              导出 PDF
            </Button>
          )}
          <Button type="primary" icon={<ReloadOutlined />} onClick={generateReport} loading={loading}>
            {report ? '重新巡检' : '开始巡检'}
          </Button>
        </Space>
      </div>

      {/* Loading */}
      {(loading || initialLoading) && (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 300 }}>
          <Spin tip={loading ? '正在巡检系统...' : '正在加载最近一次巡检报告...'} size="large" />
        </div>
      )}

      {/* Empty state */}
      {!loading && !initialLoading && !report && (
        <Card style={{ textAlign: 'center', padding: 40, background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
          <MedicineBoxOutlined style={{ fontSize: 48, color: 'var(--text-muted)', marginBottom: 16 }} />
          <Paragraph style={{ color: 'var(--text-secondary)' }}>
            暂无历史巡检报告，点击“开始巡检”生成系统健康报告
          </Paragraph>
        </Card>
      )}

      {/* Report content */}
      {report && !loading && !initialLoading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Overall status + gauge charts */}
          <Card size="small" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16 }}>
              {getStatusIcon(report.overall_status)}
              <div>
                <Text strong style={{ fontSize: 16 }}>整体状态 </Text>
                <Tag color={statusConfig[report.overall_status]?.tagColor || 'default'} style={{ fontSize: 13 }}>
                  {statusConfig[report.overall_status]?.label || '未知'}
                </Tag>
              </div>
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 16, fontSize: 12, color: 'var(--text-secondary)' }}>
                <span>主机: <Text code style={{ fontSize: 11 }}>{report.hostname}</Text></span>
                <span>系统: <Text code style={{ fontSize: 11 }}>{DISPLAY_OS}</Text></span>
                <span>架构: <Text code style={{ fontSize: 11 }}>{DISPLAY_ARCH}</Text></span>
                <span>巡检时间: <Text code style={{ fontSize: 11 }}>{report.generated_at.slice(0, 19).replace('T', ' ')}</Text></span>
              </div>
            </div>

            {/* Gauge charts row */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8 }}>
              {report.sections.map((section, idx) => {
                const percent = extractPercent(section)
                if (percent === null) return null
                const color = getProgressColor(percent)
                return (
                  <div key={idx} style={{ textAlign: 'center' }}>
                    <ReactECharts
                      option={getGaugeOption(percent, section.title.replace(' 状态', ''), color)}
                      style={{ height: 130, width: '100%' }}
                      opts={{ renderer: 'svg' }}
                    />
                  </div>
                )
              })}
            </div>
          </Card>

          {/* Section detail cards */}
          {report.sections.map((section, idx) => {
            const cfg = statusConfig[section.status] || statusConfig.healthy
            const percent = extractPercent(section)

            return (
              <Card
                key={idx}
                size="small"
                title={
                  <Space size={8}>
                    {sectionIcons[section.title] || getStatusIcon(section.status)}
                    <span>{section.title}</span>
                    <Tag color={cfg.tagColor}>{cfg.label}</Tag>
                  </Space>
                }
                style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
              >
                {/* Progress bar if we have a percentage */}
                {percent !== null && (
                  <div style={{ marginBottom: 12 }}>
                    <Progress
                      percent={percent}
                      strokeColor={getProgressColor(percent)}
                      trailColor="var(--border-color)"
                      format={(p) => <span style={{ color: 'var(--text-primary)', fontSize: 12 }}>{p}%</span>}
                    />
                  </div>
                )}

                {/* Metrics grid */}
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))',
                  gap: 6,
                  marginBottom: (section.issues.length > 0 || section.recommendations.length > 0) ? 12 : 0,
                }}>
                  {Object.entries(section.metrics).map(([key, value]) => (
                    <div
                      key={key}
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        padding: '5px 10px',
                        background: 'var(--bg-primary)',
                        borderRadius: 4,
                      }}
                    >
                      <Text style={{ fontSize: 11, color: 'var(--text-muted)' }}>{key}</Text>
                      <Text style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>
                        {formatMetricValue(value)}
                      </Text>
                    </div>
                  ))}
                </div>

                {/* Issues */}
                {section.issues.length > 0 && (
                  <div style={{ marginBottom: 8 }}>
                    <Text strong style={{ fontSize: 12, color: 'var(--accent-yellow)', display: 'block', marginBottom: 4 }}>
                      <WarningOutlined style={{ marginRight: 4 }} /> 发现问题:
                    </Text>
                    {section.issues.map((issue, i) => (
                      <Text key={i} style={{ fontSize: 12, color: 'var(--accent-yellow)', display: 'block', paddingLeft: 18 }}>
                        {issue}
                      </Text>
                    ))}
                  </div>
                )}

                {/* Recommendations */}
                {section.recommendations.length > 0 && (
                  <div>
                    <Text strong style={{ fontSize: 12, color: 'var(--accent-blue)', display: 'block', marginBottom: 4 }}>
                      优化建议:
                    </Text>
                    {section.recommendations.map((rec, i) => (
                      <Text key={i} style={{ fontSize: 12, color: 'var(--accent-blue)', display: 'block', paddingLeft: 18 }}>
                        {rec}
                      </Text>
                    ))}
                  </div>
                )}
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default HealthReport
