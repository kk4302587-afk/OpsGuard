import { useState, useEffect } from 'react'
import { Input, Card, List, Tag, Space, Typography, Empty, Badge } from 'antd'
import {
  DatabaseOutlined,
  SearchOutlined,
  BulbOutlined,
  ToolOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons'

const { Title, Text, Paragraph } = Typography
const { Search } = Input

interface KnowledgeEntry {
  id: number
  problem_signature: string
  diagnosis_path: string
  solution: string
  tools_used: string
  success_count: number
  last_used: string
  created_at: string
}

/**
 * Knowledge base panel.
 * Shows accumulated operational knowledge from resolved issues.
 */
function KnowledgePanel() {
  const [entries, setEntries] = useState<KnowledgeEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')

  useEffect(() => {
    fetchKnowledge()
  }, [])

  const fetchKnowledge = async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/knowledge/')
      if (res.ok) {
        const data = await res.json()
        setEntries(data.entries || [])
      }
    } catch (err) {
      console.error('Failed to fetch knowledge:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleSearch = async (value: string) => {
    if (!value.trim()) {
      fetchKnowledge()
      return
    }
    setLoading(true)
    try {
      const res = await fetch(`/api/knowledge/search?q=${encodeURIComponent(value)}`)
      if (res.ok) {
        const data = await res.json()
        setEntries(data.entries || [])
      }
    } catch (err) {
      console.error('Search failed:', err)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ color: 'var(--text-primary)', margin: 0 }}>
          <DatabaseOutlined style={{ marginRight: 8, color: 'var(--accent-green)' }} />
          运维知识库
        </Title>
        <Badge count={entries.length} style={{ backgroundColor: 'var(--accent-green)' }}>
          <Tag>已积累经验</Tag>
        </Badge>
      </div>

      <Search
        placeholder="搜索问题关键词..."
        allowClear
        enterButton={<><SearchOutlined /> 搜索</>}
        size="middle"
        onSearch={handleSearch}
        onChange={(e) => setSearchQuery(e.target.value)}
        style={{ marginBottom: 16 }}
      />

      {entries.length === 0 && !loading ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Text style={{ color: 'var(--text-muted)' }}>
              {searchQuery ? '未找到匹配的知识条目' : '知识库为空，Agent 解决问题后会自动积累经验'}
            </Text>
          }
        />
      ) : (
        <List
          loading={loading}
          dataSource={entries}
          renderItem={(entry) => (
            <Card
              size="small"
              style={{ marginBottom: 12, background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
            >
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {/* Problem */}
                <div>
                  <Space>
                    <BulbOutlined style={{ color: 'var(--accent-yellow)' }} />
                    <Text strong>问题特征</Text>
                    <Tag color="blue">成功 {entry.success_count} 次</Tag>
                  </Space>
                  <Paragraph style={{ margin: '4px 0 0 22px', color: 'var(--text-secondary)', fontSize: 13 }}>
                    {entry.problem_signature}
                  </Paragraph>
                </div>

                {/* Diagnosis */}
                <div>
                  <Space>
                    <SearchOutlined style={{ color: 'var(--accent-blue)' }} />
                    <Text strong>诊断路径</Text>
                  </Space>
                  <Paragraph style={{ margin: '4px 0 0 22px', color: 'var(--text-secondary)', fontSize: 12, fontFamily: 'var(--font-mono)' }}>
                    {entry.diagnosis_path}
                  </Paragraph>
                </div>

                {/* Solution */}
                <div>
                  <Space>
                    <ToolOutlined style={{ color: 'var(--accent-green)' }} />
                    <Text strong>解决方案</Text>
                  </Space>
                  <Paragraph style={{ margin: '4px 0 0 22px', color: 'var(--text-primary)', fontSize: 13 }}>
                    {entry.solution}
                  </Paragraph>
                </div>

                {/* Meta */}
                <div style={{ marginLeft: 22 }}>
                  <Space size={16}>
                    {entry.last_used && (
                      <Text style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        <ClockCircleOutlined style={{ marginRight: 4 }} />
                        最近使用: {new Date(entry.last_used).toLocaleDateString()}
                      </Text>
                    )}
                  </Space>
                </div>
              </Space>
            </Card>
          )}
        />
      )}
    </div>
  )
}

export default KnowledgePanel
