import { useState, useEffect } from 'react'
import { Card, Tag, Space, Typography, Badge, Empty } from 'antd'
import {
  ApiOutlined,
  EyeOutlined,
  EditOutlined,
  DeleteOutlined,
  SettingOutlined,
  HddOutlined,
  WifiOutlined,
  FileTextOutlined,
  ToolOutlined,
  FolderOutlined,
  DesktopOutlined,
} from '@ant-design/icons'

const { Title, Text } = Typography

interface Tool {
  name: string
  display_name?: string
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

const categoryIcons: Record<string, any> = {
  process: <SettingOutlined />,
  disk: <HddOutlined />,
  network: <WifiOutlined />,
  log: <FileTextOutlined />,
  service: <ToolOutlined />,
  config: <FolderOutlined />,
  system: <DesktopOutlined />,
}

/**
 * MCP Tools registry page - displays all available tools grouped by category.
 */
function ToolsPanel() {
  const [data, setData] = useState<{ total: number; categories: ToolCategory[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeCategoryKey, setActiveCategoryKey] = useState<string>('')

  useEffect(() => {
    fetchTools()
  }, [])

  useEffect(() => {
    if (!data?.categories.length) return
    if (!activeCategoryKey || !data.categories.some((category) => category.key === activeCategoryKey)) {
      setActiveCategoryKey(data.categories[0].key)
    }
  }, [data, activeCategoryKey])

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

  const activeCategory = data.categories.find((category) => category.key === activeCategoryKey) || data.categories[0]
  const readCount = data.categories.reduce((sum, c) => sum + c.tools.filter(t => t.risk_level === 'read').length, 0)
  const writeCount = data.categories.reduce((sum, c) => sum + c.tools.filter(t => t.risk_level === 'write').length, 0)

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20, gap: 16 }}>
        <Title level={4} style={{ color: 'var(--text-primary)', margin: 0 }}>
          <ApiOutlined style={{ marginRight: 8, color: 'var(--accent-green)' }} />
          MCP 工具注册表
        </Title>
        <Space wrap>
          <Tag style={{ fontSize: 12 }}>
            <ApiOutlined style={{ marginRight: 4 }} />
            共 {data.total} 个工具
          </Tag>
          <Tag color="green" style={{ fontSize: 11 }}>
            <EyeOutlined style={{ marginRight: 4 }} />
            只读 {readCount}
          </Tag>
          <Tag color="orange" style={{ fontSize: 11 }}>
            <EditOutlined style={{ marginRight: 4 }} />
            写操作 {writeCount}
          </Tag>
        </Space>
      </div>

      <div style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: '280px minmax(0, 1fr)', gap: 20 }}>
        <aside
          style={{
            borderRight: '1px solid var(--border-color)',
            paddingRight: 16,
            overflow: 'auto',
          }}
        >
          <Text style={{ display: 'block', color: 'var(--text-muted)', fontSize: 12, marginBottom: 10 }}>
            工具分类
          </Text>
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {data.categories.map((category) => {
              const active = category.key === activeCategory.key
              return (
                <button
                  key={category.key}
                  type="button"
                  onClick={() => setActiveCategoryKey(category.key)}
                  style={{
                    width: '100%',
                    minHeight: 48,
                    border: `1px solid ${active ? 'rgba(0, 212, 170, 0.45)' : 'var(--border-color)'}`,
                    borderRadius: 6,
                    background: active ? '#ffffff' : 'var(--bg-secondary)',
                    color: 'var(--text-primary)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    padding: '0 12px',
                    cursor: 'pointer',
                    textAlign: 'left',
                  }}
                >
                  <span style={{ color: active ? 'var(--accent-green)' : 'var(--accent-blue)', fontSize: 16, width: 18 }}>
                    {categoryIcons[category.key] || <ApiOutlined />}
                  </span>
                  <span style={{ flex: 1, minWidth: 0, fontWeight: active ? 700 : 600, fontSize: 14 }}>
                    {category.label}
                  </span>
                  <Badge
                    count={category.count}
                    style={{
                      backgroundColor: active ? 'var(--accent-green)' : 'var(--bg-hover)',
                      color: active ? '#ffffff' : 'var(--text-secondary)',
                      boxShadow: 'none',
                    }}
                  />
                </button>
              )
            })}
          </Space>
        </aside>

        <main style={{ minWidth: 0, overflow: 'auto', paddingRight: 4 }}>
          {activeCategory ? (
            <>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 16 }}>
                <Space size={10}>
                  <span style={{ color: 'var(--accent-blue)', fontSize: 20 }}>
                    {categoryIcons[activeCategory.key] || <ApiOutlined />}
                  </span>
                  <div>
                    <Title level={5} style={{ color: 'var(--text-primary)', margin: 0, fontSize: 20 }}>
                      {activeCategory.label}
                    </Title>
                    <Text style={{ color: 'var(--text-muted)', fontSize: 13 }}>
                      当前分类共 {activeCategory.count} 个工具
                    </Text>
                  </div>
                </Space>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 14 }}>
                {activeCategory.tools.map((tool) => {
                  const risk = riskConfig[tool.risk_level] || riskConfig.read
                  const params = tool.parameters?.properties || {}
                  const required = tool.parameters?.required || []

                  return (
                    <Card
                      key={tool.name}
                      size="small"
                      style={{
                        background: 'var(--bg-primary)',
                        border: '1px solid var(--border-color)',
                        minHeight: 176,
                      }}
                      bodyStyle={{ padding: 16 }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, marginBottom: 10 }}>
                        <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                          <Text strong style={{ fontSize: 16, color: 'var(--text-primary)', lineHeight: 1.35 }}>
                            {tool.display_name || tool.name}
                          </Text>
                          <Text
                            style={{
                              fontFamily: 'var(--font-mono)',
                              fontSize: 12,
                              color: 'var(--text-muted)',
                              marginTop: 3,
                            }}
                            ellipsis={{ tooltip: tool.name }}
                          >
                            {tool.name}
                          </Text>
                        </div>
                        <Tag color={risk.color} style={{ fontSize: 12, lineHeight: '22px', padding: '0 8px', margin: 0 }}>
                          {risk.icon} {risk.label}
                        </Tag>
                      </div>
                      <Text style={{ fontSize: 14, color: 'var(--text-secondary)', display: 'block', lineHeight: 1.7, marginBottom: 12 }}>
                        {tool.description}
                      </Text>
                      {Object.keys(params).length > 0 && (
                        <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: 10 }}>
                          <Text style={{ fontSize: 12, color: 'var(--text-muted)' }}>参数</Text>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
                            {Object.entries(params).map(([key, schema]: [string, any]) => (
                              <Tag
                                key={key}
                                style={{
                                  fontSize: 12,
                                  margin: 0,
                                  lineHeight: '24px',
                                  background: 'var(--bg-elevated)',
                                  border: '1px solid var(--border-color)',
                                }}
                              >
                                {key}{required.includes(key) ? '*' : ''}
                                <span style={{ color: 'var(--text-muted)', marginLeft: 5 }}>{schema.type}</span>
                              </Tag>
                            ))}
                          </div>
                        </div>
                      )}
                    </Card>
                  )
                })}
              </div>
            </>
          ) : (
            <Empty description="暂无工具分类" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          )}
        </main>
      </div>
    </div>
  )
}

export default ToolsPanel
