import { useState, useEffect } from 'react'
import { Card, Tag, Space, Typography, Collapse, Badge } from 'antd'
import {
  ApiOutlined,
  EyeOutlined,
  EditOutlined,
  DeleteOutlined,
} from '@ant-design/icons'

const { Title, Text } = Typography

interface Tool {
  name: string
  description: string
  category: string
  risk_level: string
  parameters: { type: string; properties?: Record<string, any>; required?: string[] }
}

interface ToolCategory {
  key: string
  label: string
  tools: Tool[]
  count: number
}

const riskConfig: Record<string, { color: string; label: string; icon: any }> = {
  read: { color: 'green', label: '只读', icon: <EyeOutlined /> },
  write: { color: 'orange', label: '写操作', icon: <EditOutlined /> },
  destructive: { color: 'red', label: '高危', icon: <DeleteOutlined /> },
}

const categoryIcons: Record<string, string> = {
  process: '⚙',
  disk: '💾',
  network: '🌐',
  log: '📋',
  service: '🔧',
  config: '📄',
  system: '🖥',
}

/**
 * MCP Tools registry page - displays all available tools grouped by category.
 */
function ToolsPanel() {
  const [data, setData] = useState<{ total: number; categories: ToolCategory[] } | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchTools()
  }, [])

  const fetchTools = async () => {
    try {
      const res = await fetch('/api/tools/')
      if (res.ok) {
        setData(await res.json())
      }
    } catch (err) {
      console.error('Failed to fetch tools:', err)
    } finally {
      setLoading(false)
    }
  }

  if (loading || !data) return null

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ color: 'var(--text-primary)', margin: 0 }}>
          <ApiOutlined style={{ marginRight: 8, color: 'var(--accent-green)' }} />
          MCP 工具注册表
        </Title>
        <Space>
          <Badge count={data.total} style={{ backgroundColor: 'var(--accent-green)' }}>
            <Tag>已注册工具</Tag>
          </Badge>
          <Tag color="green"><EyeOutlined /> 只读 {data.categories.reduce((sum, c) => sum + c.tools.filter(t => t.risk_level === 'read').length, 0)}</Tag>
          <Tag color="orange"><EditOutlined /> 写操作 {data.categories.reduce((sum, c) => sum + c.tools.filter(t => t.risk_level === 'write').length, 0)}</Tag>
        </Space>
      </div>

      <Collapse
        defaultActiveKey={data.categories.map(c => c.key)}
        ghost
        items={data.categories.map((category) => ({
          key: category.key,
          label: (
            <Space>
              <span style={{ fontSize: 16 }}>{categoryIcons[category.key] || '📦'}</span>
              <Text strong>{category.label}</Text>
              <Badge count={category.count} style={{ backgroundColor: 'var(--bg-hover)', color: 'var(--text-secondary)' }} />
            </Space>
          ),
          children: (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 10 }}>
              {category.tools.map((tool) => {
                const risk = riskConfig[tool.risk_level] || riskConfig.read
                const params = tool.parameters?.properties || {}
                const required = tool.parameters?.required || []

                return (
                  <Card
                    key={tool.name}
                    size="small"
                    style={{ background: 'var(--bg-primary)', border: '1px solid var(--border-color)' }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6 }}>
                      <Text strong style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--accent-blue)' }}>
                        {tool.name}
                      </Text>
                      <Tag color={risk.color} style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
                        {risk.icon} {risk.label}
                      </Tag>
                    </div>
                    <Text style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'block', marginBottom: 8 }}>
                      {tool.description}
                    </Text>
                    {Object.keys(params).length > 0 && (
                      <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: 6 }}>
                        <Text style={{ fontSize: 10, color: 'var(--text-muted)' }}>参数:</Text>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
                          {Object.entries(params).map(([key, schema]: [string, any]) => (
                            <Tag
                              key={key}
                              style={{ fontSize: 10, margin: 0, background: 'var(--bg-elevated)', border: '1px solid var(--border-color)' }}
                            >
                              {key}{required.includes(key) ? '*' : ''}
                              <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>{schema.type}</span>
                            </Tag>
                          ))}
                        </div>
                      </div>
                    )}
                  </Card>
                )
              })}
            </div>
          ),
        }))}
      />
    </div>
  )
}

export default ToolsPanel
