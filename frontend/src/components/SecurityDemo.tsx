import { useState, useEffect } from 'react'
import { Input, Button, Card, Tag, Space, Typography, List } from 'antd'
import {
  SafetyOutlined,
  BugOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ThunderboltOutlined,
  ExperimentOutlined,
} from '@ant-design/icons'

const { TextArea } = Input
const { Text, Title, Paragraph } = Typography

interface TestResult {
  input_text: string
  is_blocked: boolean
  layers_checked: string[]
  blocked_by: string | null
  detail: string | null
  timestamp: string
}

interface AttackExample {
  label: string
  text: string
}

/**
 * Security red-team testing page.
 * Allows evaluators to test prompt injection attacks and see defense in action.
 */
function SecurityDemo() {
  const [inputText, setInputText] = useState('')
  const [results, setResults] = useState<TestResult[]>([])
  const [loading, setLoading] = useState(false)
  const [examples, setExamples] = useState<{
    injection_examples: AttackExample[]
    command_examples: AttackExample[]
    safe_examples: AttackExample[]
  } | null>(null)

  useEffect(() => {
    fetchExamples()
  }, [])

  const fetchExamples = async () => {
    try {
      const res = await fetch('/api/security/attack-examples')
      if (res.ok) {
        setExamples(await res.json())
      }
    } catch (err) {
      console.error('Failed to fetch examples:', err)
    }
  }

  const testInput = async (text: string) => {
    setLoading(true)
    try {
      const res = await fetch('/api/security/test-attack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ input_text: text }),
      })
      if (res.ok) {
        const result = await res.json()
        setResults((prev) => [result, ...prev])
      }
    } catch (err) {
      console.error('Test failed:', err)
    } finally {
      setLoading(false)
    }
  }

  const testCommand = async (text: string) => {
    setLoading(true)
    try {
      const res = await fetch('/api/security/test-command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ input_text: text }),
      })
      if (res.ok) {
        const result = await res.json()
        setResults((prev) => [{
          input_text: result.command,
          is_blocked: result.is_blocked,
          layers_checked: result.layers_checked,
          blocked_by: result.blocked_by,
          detail: result.detail,
          timestamp: result.timestamp,
        }, ...prev])
      }
    } catch (err) {
      console.error('Test failed:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleTest = () => {
    if (inputText.trim()) {
      testInput(inputText.trim())
    }
  }

  const renderResultItem = (item: TestResult) => {
    const isWarning = !item.is_blocked && item.blocked_by === 'high_risk_intent'
    const statusIcon = item.is_blocked
      ? <CloseCircleOutlined style={{ fontSize: 16, color: 'var(--accent-red)' }} />
      : isWarning
        ? <ExperimentOutlined style={{ fontSize: 16, color: 'var(--accent-yellow)' }} />
        : <CheckCircleOutlined style={{ fontSize: 16, color: 'var(--accent-green)' }} />
    const statusTag = item.is_blocked
      ? <Tag color="red">已拦截</Tag>
      : isWarning
        ? <Tag color="orange">高风险警告</Tag>
        : <Tag color="green">已放行</Tag>

    return (
      <List.Item style={{ borderBottom: '1px solid var(--border-color)', padding: '12px 0' }}>
        <div style={{ width: '100%' }}>
          <Space style={{ marginBottom: 8 }} wrap>
            {statusIcon}
            {statusTag}
            {item.blocked_by && item.blocked_by !== 'high_risk_intent' && (
              <Tag color="volcano">防御层: {item.blocked_by}</Tag>
            )}
          </Space>
          <Text style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginBottom: 6 }}>
            检查层: {item.layers_checked.join(' → ')}
          </Text>
          <div
            style={{
              background: 'var(--bg-primary)',
              padding: '8px 12px',
              borderRadius: 4,
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {item.input_text}
          </div>
          {item.detail && (
            <Text style={{
              fontSize: 12,
              color: item.is_blocked ? 'var(--accent-red)' : isWarning ? 'var(--accent-yellow)' : 'var(--text-muted)',
              marginTop: 6,
              display: 'block',
            }}>
              {item.detail}
            </Text>
          )}
        </div>
      </List.Item>
    )
  }

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column', boxSizing: 'border-box' }}>
      <Title level={4} style={{ color: 'var(--text-primary)' }}>
        <SafetyOutlined style={{ marginRight: 8, color: 'var(--accent-green)' }} />
        安全攻防演示
      </Title>
      <Paragraph style={{ color: 'var(--text-secondary)' }}>
        测试 OpsGuard 的三层安全防御系统。尝试输入注入攻击或危险命令，观察系统如何识别和拦截。
      </Paragraph>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 390px', gap: 16, flex: 1, minHeight: 0 }}>
        <div style={{ minWidth: 0, overflow: 'auto', paddingRight: 4 }}>
          {/* Input area */}
          <Card size="small" style={{ marginBottom: 16, background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <TextArea
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                placeholder="输入要测试的文本（注入攻击或危险命令）..."
                rows={3}
                style={{ background: 'var(--bg-primary)', fontFamily: 'var(--font-mono)', fontSize: 13 }}
              />
              <Space>
                <Button type="primary" icon={<ExperimentOutlined />} onClick={handleTest} loading={loading}>
                  测试输入安全
                </Button>
                <Button icon={<ThunderboltOutlined />} onClick={() => inputText.trim() && testCommand(inputText.trim())} loading={loading}>
                  测试命令安全
                </Button>
              </Space>
            </Space>
          </Card>

          {/* Example attacks */}
          {examples && (
            <Card size="small" title={<><BugOutlined style={{ marginRight: 6 }} />预设攻击样本</>} style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                <Text strong style={{ fontSize: 12 }}>注入攻击:</Text>
                <Space wrap>
                  {examples.injection_examples.map((ex) => (
                    <Tag key={ex.label} color="red" style={{ cursor: 'pointer' }} onClick={() => testInput(ex.text)}>
                      {ex.label}
                    </Tag>
                  ))}
                </Space>
                <Text strong style={{ fontSize: 12 }}>危险命令:</Text>
                <Space wrap>
                  {examples.command_examples.map((ex) => (
                    <Tag key={ex.label} color="orange" style={{ cursor: 'pointer' }} onClick={() => testCommand(ex.text)}>
                      {ex.label}
                    </Tag>
                  ))}
                </Space>
                <Text strong style={{ fontSize: 12 }}>正常请求 (应放行):</Text>
                <Space wrap>
                  {examples.safe_examples.map((ex) => (
                    <Tag key={ex.label} color="green" style={{ cursor: 'pointer' }} onClick={() => testInput(ex.text)}>
                      {ex.label}
                    </Tag>
                  ))}
                </Space>
              </Space>
            </Card>
          )}
        </div>

        {/* Results */}
        <section
          style={{
            minWidth: 0,
            height: '100%',
            display: 'flex',
            flexDirection: 'column',
            background: 'var(--bg-secondary)',
            border: '1px solid var(--border-color)',
            borderRadius: 6,
            padding: 14,
            boxSizing: 'border-box',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, marginBottom: 12 }}>
            <div style={{ minWidth: 0 }}>
              <Title level={5} style={{ color: 'var(--text-primary)', margin: 0 }}>
                检测结果
              </Title>
              <Text style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
                拦截 {results.filter(r => r.is_blocked).length} / 警告 {results.filter(r => !r.is_blocked && r.blocked_by === 'high_risk_intent').length} / 放行 {results.filter(r => !r.is_blocked && r.blocked_by !== 'high_risk_intent').length}
              </Text>
            </div>
            {results.length > 0 && (
              <Button size="small" onClick={() => setResults([])}>
                清空
              </Button>
            )}
          </div>
          <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
            <List
              dataSource={results.slice(0, 20)}
              locale={{ emptyText: '点击左侧按钮或标签开始测试' }}
              renderItem={renderResultItem}
            />
          </div>
        </section>
      </div>
    </div>
  )
}

export default SecurityDemo
