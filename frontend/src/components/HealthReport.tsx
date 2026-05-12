import { useState } from 'react'
import { Button, Card, Tag, Space, Typography, Spin } from 'antd'
import {
  MedicineBoxOutlined,
  ReloadOutlined,
  CheckCircleOutlined,
  WarningOutlined,
  CloseCircleOutlined,
  FilePdfOutlined,
} from '@ant-design/icons'

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
  healthy: { color: 'var(--accent-green)', label: '正常', tagColor: 'green' },
  warning: { color: 'var(--accent-yellow)', label: '警告', tagColor: 'orange' },
  critical: { color: 'var(--accent-red)', label: '严重', tagColor: 'red' },
}

function HealthReport() {
  const [report, setReport] = useState<HealthReportData | null>(null)
  const [loading, setLoading] = useState(false)

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
    const cfg = statusConfig[status]
    const color = cfg?.color || 'var(--text-muted)'
    switch (status) {
      case 'healthy': return <CheckCircleOutlined style={{ color }} />
      case 'warning': return <WarningOutlined style={{ color }} />
      case 'critical': return <CloseCircleOutlined style={{ color }} />
      default: return <CheckCircleOutlined style={{ color }} />
    }
  }

  const formatMetricValue = (value: string | number | string[] | Record<string, string>): string => {
    if (typeof value === 'object' && !Array.isArray(value)) {
      return Object.entries(value).map(([k, v]) => `${k}: ${v}`).join(', ')
    }
    if (Array.isArray(value)) {
      return value.join(', ')
    }
    return String(value)
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
            <Button
              icon={<FilePdfOutlined />}
              onClick={() => window.open('/api/health-report/export-pdf', '_blank')}
            >
              导出 PDF
            </Button>
          )}
          <Button type="primary" icon={<ReloadOutlined />} onClick={generateReport} loading={loading}>
            {report ? '重新巡检' : '开始巡检'}
          </Button>
        </Space>
      </div>

      {/* Loading */}
      {loading && (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 300 }}>
          <Spin tip="正在巡检系统..." size="large" />
        </div>
      )}

      {/* Empty state */}
      {!loading && !report && (
        <Card style={{ textAlign: 'center', padding: 40, background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
          <MedicineBoxOutlined style={{ fontSize: 48, color: 'var(--text-muted)', marginBottom: 16 }} />
          <Paragraph style={{ color: 'var(--text-secondary)' }}>
            点击"开始巡检"生成系统健康报告
          </Paragraph>
        </Card>
      )}

      {/* Report content */}
      {report && !loading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Overall status bar */}
          <Card size="small" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 24, flexWrap: 'wrap' }}>
              <Space size={8}>
                {getStatusIcon(report.overall_status)}
                <Text strong>整体状态:</Text>
                <Tag color={statusConfig[report.overall_status]?.tagColor || 'default'}>
                  {statusConfig[report.overall_status]?.label || '未知'}
                </Tag>
              </Space>
              <Text style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                主机: <Text code style={{ fontSize: 11 }}>{report.hostname}</Text>
              </Text>
              <Text style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                系统: <Text code style={{ fontSize: 11 }}>{report.os}</Text>
              </Text>
              <Text style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                架构: <Text code style={{ fontSize: 11 }}>{report.arch}</Text>
              </Text>
            </div>
          </Card>

          {/* Section cards */}
          {report.sections.map((section, idx) => {
            const cfg = statusConfig[section.status] || statusConfig.healthy
            return (
              <Card
                key={idx}
                size="small"
                title={
                  <Space size={8}>
                    {getStatusIcon(section.status)}
                    <span>{section.title}</span>
                    <Tag color={cfg.tagColor} style={{ marginLeft: 4 }}>{cfg.label}</Tag>
                  </Space>
                }
                style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
              >
                {/* Metrics grid */}
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
                  gap: 8,
                  marginBottom: (section.issues.length > 0 || section.recommendations.length > 0) ? 12 : 0,
                }}>
                  {Object.entries(section.metrics).map(([key, value]) => (
                    <div
                      key={key}
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        padding: '6px 12px',
                        background: 'var(--bg-primary)',
                        borderRadius: 6,
                        border: '1px solid var(--border-color)',
                      }}
                    >
                      <Text style={{ fontSize: 12, color: 'var(--text-muted)' }}>{key}</Text>
                      <Text style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>
                        {formatMetricValue(value)}
                      </Text>
                    </div>
                  ))}
                </div>

                {/* Issues */}
                {section.issues.length > 0 && (
                  <div style={{ marginBottom: 8 }}>
                    <Text strong style={{ fontSize: 12, color: 'var(--accent-yellow)', display: 'block', marginBottom: 4 }}>
                      <WarningOutlined style={{ marginRight: 4 }} />
                      发现问题:
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
