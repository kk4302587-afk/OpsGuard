import { useState, useEffect } from 'react'
import { Card, Tag, Space, Typography, Empty, Button, Steps, Badge, Popconfirm, message as antdMessage } from 'antd'
import {
  PlayCircleOutlined,
  ThunderboltOutlined,
  DeleteOutlined,
  ClockCircleOutlined,
  ToolOutlined,
  FileTextOutlined,
  CaretRightOutlined,
} from '@ant-design/icons'
import { useChatStore } from '../stores/chatStore'

const { Title, Text, Paragraph } = Typography

interface RunbookStep {
  tool_name: string
  tool_args: Record<string, any>
  description: string
  risk_level: string
}

interface Runbook {
  id: string
  name: string
  description: string
  trigger_pattern: string
  steps: RunbookStep[]
  step_count: number
  run_count: number
  last_run: string | null
  created_at: string
}

const riskColors: Record<string, string> = {
  read: 'green',
  write: 'orange',
  destructive: 'red',
}

/**
 * Runbook management page - view, run, and manage automated operation playbooks.
 */
function RunbookPanel() {
  const [runbooks, setRunbooks] = useState<Runbook[]>([])
  const [loading, setLoading] = useState(true)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const runRunbookDirectly = useChatStore((s) => s.runRunbookDirectly)
  const activeSessionId = useChatStore((s) => s.activeSessionId)
  const isThinking = useChatStore((s) => s.isThinking)
  const ws = useChatStore((s) => s.ws)

  const handleRunNow = (runbookId: string, name: string) => {
    if (!activeSessionId || !ws || ws.readyState !== WebSocket.OPEN) {
      antdMessage.warning('请先打开一个聊天会话再执行 Runbook')
      return
    }
    if (isThinking) {
      antdMessage.info('当前还有任务在进行，请等待完成')
      return
    }
    runRunbookDirectly(runbookId)
    antdMessage.success(`已开始执行 Runbook「${name}」，请切换到聊天视图查看进度`)
  }

  useEffect(() => {
    fetchRunbooks()
  }, [])

  const fetchRunbooks = async () => {
    try {
      const res = await fetch('/api/runbooks/')
      if (res.ok) {
        const data = await res.json()
        setRunbooks(data.runbooks || [])
      }
    } catch (err) {
      console.error('Failed to fetch runbooks:', err)
    } finally {
      setLoading(false)
    }
  }

  const deleteRunbook = async (id: string) => {
    try {
      await fetch(`/api/runbooks/${id}`, { method: 'DELETE' })
      setRunbooks((prev) => prev.filter((r) => r.id !== id))
    } catch (err) {
      console.error('Failed to delete runbook:', err)
    }
  }

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ color: 'var(--text-primary)', margin: 0 }}>
          <PlayCircleOutlined style={{ marginRight: 8, color: 'var(--accent-green)' }} />
          运维 Runbook
        </Title>
        <Badge count={runbooks.length} style={{ backgroundColor: 'var(--accent-green)' }}>
          <Tag>已保存剧本</Tag>
        </Badge>
      </div>

      <Paragraph style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>
        Runbook 是可重放的操作剧本。Agent 成功解决问题后会自动生成 Runbook，下次遇到类似问题可一键执行。
      </Paragraph>

      {runbooks.length === 0 && !loading ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Text style={{ color: 'var(--text-muted)' }}>
              暂无 Runbook。Agent 解决问题后会自动生成可重放的操作剧本。
            </Text>
          }
        />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {runbooks.map((runbook) => (
            <Card
              key={runbook.id}
              size="small"
              style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
            >
              {/* Header */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                <div>
                  <Space size={8}>
                    <FileTextOutlined style={{ color: 'var(--accent-green)' }} />
                    <Text strong style={{ fontSize: 14 }}>{runbook.name}</Text>
                    <Tag>{runbook.step_count} 步</Tag>
                  </Space>
                  <Paragraph style={{ color: 'var(--text-secondary)', fontSize: 12, margin: '4px 0 0 22px' }}>
                    {runbook.description}
                  </Paragraph>
                </div>
                <Space>
                  <Popconfirm
                    title={`立即执行 Runbook「${runbook.name}」？`}
                    description="每个写操作步骤仍需逐条审批，可随时拒绝中止。"
                    okText="执行"
                    cancelText="取消"
                    onConfirm={() => handleRunNow(runbook.id, runbook.name)}
                  >
                    <Button
                      size="small"
                      type="primary"
                      icon={<CaretRightOutlined />}
                      disabled={isThinking}
                    >
                      立即执行
                    </Button>
                  </Popconfirm>
                  <Button
                    size="small"
                    type="text"
                    icon={expandedId === runbook.id ? <ThunderboltOutlined /> : <PlayCircleOutlined />}
                    onClick={() => setExpandedId(expandedId === runbook.id ? null : runbook.id)}
                  >
                    {expandedId === runbook.id ? '收起' : '查看步骤'}
                  </Button>
                  <Popconfirm title="确认删除此 Runbook？" onConfirm={() => deleteRunbook(runbook.id)}>
                    <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              </div>

              {/* Meta */}
              <div style={{ display: 'flex', gap: 16, marginLeft: 22, marginBottom: 8 }}>
                <Text style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  <ThunderboltOutlined style={{ marginRight: 4 }} />
                  触发条件: {runbook.trigger_pattern}
                </Text>
                <Text style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  <PlayCircleOutlined style={{ marginRight: 4 }} />
                  执行次数: {runbook.run_count}
                </Text>
                {runbook.last_run && (
                  <Text style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    <ClockCircleOutlined style={{ marginRight: 4 }} />
                    最近: {new Date(runbook.last_run).toLocaleDateString()}
                  </Text>
                )}
              </div>

              {/* Expanded steps */}
              {expandedId === runbook.id && (
                <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border-color)' }}>
                  <Steps
                    direction="vertical"
                    size="small"
                    current={-1}
                    items={runbook.steps.map((step) => ({
                      title: (
                        <Space size={6}>
                          <Text style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{step.tool_name}</Text>
                          <Tag color={riskColors[step.risk_level] || 'default'} style={{ fontSize: 10, lineHeight: '14px', padding: '0 3px' }}>
                            {step.risk_level}
                          </Tag>
                        </Space>
                      ),
                      description: (
                        <div>
                          <Text style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{step.description}</Text>
                          {Object.keys(step.tool_args).length > 0 && (
                            <div style={{ marginTop: 4 }}>
                              <code style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                                {JSON.stringify(step.tool_args)}
                              </code>
                            </div>
                          )}
                        </div>
                      ),
                      icon: <ToolOutlined style={{ fontSize: 12 }} />,
                    }))}
                  />
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}

export default RunbookPanel
