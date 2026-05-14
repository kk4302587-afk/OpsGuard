import { useState } from 'react'
import { Button, Card, Space, Typography, Spin, Tag, Statistic, Select } from 'antd'
import {
  FileTextOutlined,
  ReloadOutlined,
  MessageOutlined,
  ToolOutlined,
  SafetyOutlined,
  CheckCircleOutlined,
  BulbOutlined,
  PlayCircleOutlined,
  FilePdfOutlined,
} from '@ant-design/icons'

const { Title, Text, Paragraph } = Typography

interface OpsReportData {
  generated_at: string
  time_range: string
  summary: string
  sections: {
    sessions: { title: string; count: number; items: any[] }
    messages: { title: string; user_messages: number; agent_responses: number }
    tool_calls: { title: string; total: number; by_tool: Record<string, number> }
    security: { title: string; blocks: number; details: string[] }
    approvals: { title: string; total_requests: number; approved: number; rejected: number }
    knowledge: { title: string; count: number; items: any[] }
    runbooks: { title: string; count: number; items: any[] }
  }
}

/**
 * Operations report page - on-demand summary of recent operations.
 */
function OpsReport() {
  const [report, setReport] = useState<OpsReportData | null>(null)
  const [loading, setLoading] = useState(false)
  const [hours, setHours] = useState(24)

  const generateReport = async () => {
    setLoading(true)
    try {
      const res = await fetch(`/api/ops-report/generate?hours=${hours}`)
      if (res.ok) {
        setReport(await res.json())
      }
    } catch (err) {
      console.error('Failed to generate report:', err)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ color: 'var(--text-primary)', margin: 0 }}>
          <FileTextOutlined style={{ marginRight: 8, color: 'var(--accent-green)' }} />
          运维日报
        </Title>
        <Space>
          <Select
            value={hours}
            onChange={setHours}
            style={{ width: 120 }}
            options={[
              { value: 1, label: '最近 1 小时' },
              { value: 6, label: '最近 6 小时' },
              { value: 12, label: '最近 12 小时' },
              { value: 24, label: '最近 24 小时' },
              { value: 72, label: '最近 3 天' },
            ]}
          />
          {report && (
            <Button icon={<FilePdfOutlined />} onClick={() => window.open(`/api/ops-report/export-pdf?hours=${hours}`, '_blank')}>
              导出 PDF
            </Button>
          )}
          <Button type="primary" icon={<ReloadOutlined />} onClick={generateReport} loading={loading}>
            生成报告
          </Button>
        </Space>
      </div>

      {/* Loading */}
      {loading && (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 200 }}>
          <Spin tip="正在汇总运维数据..." size="large" />
        </div>
      )}

      {/* Empty */}
      {!loading && !report && (
        <Card style={{ textAlign: 'center', padding: 40, background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
          <FileTextOutlined style={{ fontSize: 48, color: 'var(--text-muted)', marginBottom: 16 }} />
          <Paragraph style={{ color: 'var(--text-secondary)' }}>
            选择时间范围后点击"生成报告"，汇总该时段内的所有运维操作。
          </Paragraph>
        </Card>
      )}

      {/* Report */}
      {report && !loading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Stats row */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
            <Card size="small" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', textAlign: 'center' }}>
              <Statistic
                title={<Text style={{ color: 'var(--text-muted)', fontSize: 11 }}>会话数</Text>}
                value={report.sections.sessions?.count || 0}
                prefix={<MessageOutlined style={{ color: 'var(--accent-blue)' }} />}
                valueStyle={{ fontSize: 24, color: 'var(--text-primary)' }}
              />
            </Card>
            <Card size="small" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', textAlign: 'center' }}>
              <Statistic
                title={<Text style={{ color: 'var(--text-muted)', fontSize: 11 }}>工具调用</Text>}
                value={report.sections.tool_calls?.total || 0}
                prefix={<ToolOutlined style={{ color: 'var(--accent-green)' }} />}
                valueStyle={{ fontSize: 24, color: 'var(--text-primary)' }}
              />
            </Card>
            <Card size="small" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', textAlign: 'center' }}>
              <Statistic
                title={<Text style={{ color: 'var(--text-muted)', fontSize: 11 }}>安全拦截</Text>}
                value={report.sections.security?.blocks || 0}
                prefix={<SafetyOutlined style={{ color: 'var(--accent-red)' }} />}
                valueStyle={{ fontSize: 24, color: 'var(--text-primary)' }}
              />
            </Card>
            <Card size="small" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', textAlign: 'center' }}>
              <Statistic
                title={<Text style={{ color: 'var(--text-muted)', fontSize: 11 }}>审批通过</Text>}
                value={report.sections.approvals?.approved || 0}
                prefix={<CheckCircleOutlined style={{ color: 'var(--accent-green)' }} />}
                valueStyle={{ fontSize: 24, color: 'var(--text-primary)' }}
              />
            </Card>
            <Card size="small" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', textAlign: 'center' }}>
              <Statistic
                title={<Text style={{ color: 'var(--text-muted)', fontSize: 11 }}>新增知识</Text>}
                value={report.sections.knowledge?.count || 0}
                prefix={<BulbOutlined style={{ color: 'var(--accent-yellow)' }} />}
                valueStyle={{ fontSize: 24, color: 'var(--text-primary)' }}
              />
            </Card>
            <Card size="small" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', textAlign: 'center' }}>
              <Statistic
                title={<Text style={{ color: 'var(--text-muted)', fontSize: 11 }}>新增 Runbook</Text>}
                value={report.sections.runbooks?.count || 0}
                prefix={<PlayCircleOutlined style={{ color: 'var(--accent-purple)' }} />}
                valueStyle={{ fontSize: 24, color: 'var(--text-primary)' }}
              />
            </Card>
          </div>

          {/* Tool usage breakdown */}
          {report.sections.tool_calls?.total > 0 && (
            <Card
              size="small"
              title={<><ToolOutlined style={{ marginRight: 6 }} />工具调用分布</>}
              style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
            >
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {Object.entries(report.sections.tool_calls.by_tool).map(([name, count]) => (
                  <Tag key={name} style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                    {name} <span style={{ color: 'var(--accent-green)', marginLeft: 4 }}>{count}</span>
                  </Tag>
                ))}
              </div>
            </Card>
          )}

          {/* Security events */}
          {report.sections.security?.blocks > 0 && (
            <Card
              size="small"
              title={<><SafetyOutlined style={{ marginRight: 6, color: 'var(--accent-red)' }} />安全拦截详情</>}
              style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
            >
              {report.sections.security.details.map((detail, i) => (
                <Text key={i} style={{ display: 'block', fontSize: 12, color: 'var(--accent-yellow)', marginBottom: 4 }}>
                  {detail}
                </Text>
              ))}
            </Card>
          )}

          {/* Sessions list */}
          {report.sections.sessions?.count > 0 && (
            <Card
              size="small"
              title={<><MessageOutlined style={{ marginRight: 6 }} />会话记录</>}
              style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
            >
              {report.sections.sessions.items.map((session: any) => (
                <div key={session.id} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid var(--border-color)' }}>
                  <Text style={{ fontSize: 12 }}>{session.title || '未命名会话'}</Text>
                  <Text style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    {new Date(session.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                  </Text>
                </div>
              ))}
            </Card>
          )}

          {/* Summary text */}
          <Card size="small" style={{ background: 'var(--bg-primary)', border: '1px solid var(--border-color)' }}>
            <pre style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-secondary)', margin: 0, whiteSpace: 'pre-wrap' }}>
              {report.summary}
            </pre>
          </Card>
        </div>
      )}
    </div>
  )
}

export default OpsReport
