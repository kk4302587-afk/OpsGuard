import { useState, useEffect } from 'react'
import { Input, Card, List, Tag, Space, Typography, Empty, Badge, Collapse, Progress, Descriptions, Button, Popconfirm, message } from 'antd'
import {
  DatabaseOutlined,
  SearchOutlined,
  BulbOutlined,
  ToolOutlined,
  ClockCircleOutlined,
  CheckCircleOutlined,
  LinkOutlined,
  FilterOutlined,
} from '@ant-design/icons'

const { Title, Text, Paragraph } = Typography
const { Search } = Input

interface KnowledgeEntry {
  id: number
  problem_signature: string
  diagnosis_path: string
  solution: string
  tools_used: string[] | string
  success_count: number
  last_used: string
  created_at: string
  match_score?: number
  final_score?: number
  match_reason?: string
  retrieval_sources?: string[]
  score_breakdown?: Record<string, number>
  evidence_refs?: Array<Record<string, unknown>>
  source_session_id?: string
  source_incident_id?: string
  validation_status?: string
  confidence?: string
  recommended_fresh_checks?: string[]
  safe_to_reuse?: boolean
  entities?: Record<string, unknown>
  incident_type?: string
  has_write_action?: boolean
  write_approved?: boolean
  owner?: string
  review_status?: string
  ttl_days?: string | number
  last_validated_at?: string
  staleness_status?: string
}

const confidenceColor = (confidence?: string) => {
  if (confidence === 'high') return 'green'
  if (confidence === 'low') return 'red'
  return 'blue'
}

const validationColor = (status?: string) => {
  if (status === 'validated') return 'green'
  if (status === 'missing') return 'orange'
  if (status === 'failed') return 'red'
  return 'default'
}

const confidenceLabel = (confidence?: string) => ({
  high: '高',
  medium: '中',
  low: '低',
}[confidence || ''] || '未知')

const validationLabel = (status?: string) => ({
  validated: '已验证',
  missing: '未验证',
  failed: '验证失败',
  unknown: '未知',
}[status || ''] || '未知')

const reviewLabel = (status?: string) => ({
  reviewed: '已复核',
  draft: '待复核',
  deprecated: '已废弃',
}[status || ''] || '待复核')

const stalenessLabel = (status?: string) => ({
  fresh: '新鲜',
  review_due: '临近复核',
  stale: '已过期',
  deprecated: '已废弃',
  unknown: '未评估',
}[status || ''] || '未评估')

const retrievalSourceLabel = (source: string) => ({
  fts5_keyword: '关键词命中',
  fuzzy_text: '文本相似',
  structured_semantic: '结构化语义',
  environment_similarity: '环境相似',
  embedding: '语义向量',
  rerank: '综合排序',
}[source] || source)

const scoreLabel = (key: string) => ({
  match_score: '匹配度',
  final_score: '综合分',
  fuzzy_score: '文本相似',
  keyword_score: '关键词',
  semantic_score: '结构化语义',
  embedding_score: '语义向量',
  recentness: '新近程度',
  environment_similarity: '环境相似',
  validation_completeness: '验证完整度',
  success_weight: '成功权重',
  evidence_coverage: '证据覆盖',
}[key] || key)

const incidentTypeLabel = (type?: string) => ({
  service_connectivity: '服务连通性',
  disk: '磁盘空间',
  cpu_load: 'CPU/负载',
  memory: '内存',
  configuration: '配置问题',
  general: '通用事件',
}[type || ''] || type || '')

const evidenceTypeLabel = (type?: unknown) => ({
  tool_call: '工具证据',
  evidence_summary: '证据摘要',
}[String(type || '')] || '证据')

const scorePercent = (value?: number) => Math.round(Math.max(0, Math.min(1, Number(value || 0))) * 100)

const compactValue = (value: unknown, max = 120) => {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? '', null, 0)
  return text.length > max ? `${text.slice(0, max)}...` : text
}

const shortId = (id?: string, length = 8) => id ? id.slice(0, length) : ''

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

  const updateReviewStatus = async (entryId: number, reviewStatus: 'draft' | 'reviewed' | 'deprecated') => {
    try {
      const res = await fetch(`/api/knowledge/${entryId}/review`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ review_status: reviewStatus }),
      })
      if (!res.ok) throw new Error(await res.text())
      message.success(reviewStatus === 'reviewed' ? '已标记为已复核' : reviewStatus === 'deprecated' ? '已废弃该经验' : '已退回待复核')
      if (searchQuery.trim()) await handleSearch(searchQuery)
      else await fetchKnowledge()
    } catch (err) {
      console.error('Review update failed:', err)
      message.error('更新复核状态失败')
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
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start' }}>
                    <Space wrap size={8}>
                      <BulbOutlined style={{ color: 'var(--accent-yellow)' }} />
                      <Text strong>问题特征</Text>
                      <Tag color="blue">成功 {entry.success_count} 次</Tag>
                      {entry.confidence && <Tag color={confidenceColor(entry.confidence)}>置信度{confidenceLabel(entry.confidence)}</Tag>}
                      {entry.validation_status && <Tag color={validationColor(entry.validation_status)}>{validationLabel(entry.validation_status)}</Tag>}
                      {entry.review_status && <Tag color={entry.review_status === 'reviewed' ? 'green' : entry.review_status === 'deprecated' ? 'red' : 'gold'}>{reviewLabel(entry.review_status)}</Tag>}
                      {entry.staleness_status && <Tag color={entry.staleness_status === 'fresh' ? 'green' : entry.staleness_status === 'stale' ? 'red' : 'orange'}>{stalenessLabel(entry.staleness_status)}</Tag>}
                    </Space>
                    <Space size={4} style={{ flexShrink: 0 }}>
                      {entry.review_status !== 'reviewed' && entry.review_status !== 'deprecated' && (
                        <Button size="small" type="link" onClick={() => updateReviewStatus(entry.id, 'reviewed')}>
                          标记已复核
                        </Button>
                      )}
                      {entry.review_status !== 'deprecated' && (
                        <Popconfirm
                          title="废弃这条经验？"
                          description="废弃后默认不再显示，也不会参与 Agent 历史经验检索。"
                          okText="废弃"
                          cancelText="取消"
                          onConfirm={() => updateReviewStatus(entry.id, 'deprecated')}
                        >
                          <Button size="small" danger type="link">废弃</Button>
                        </Popconfirm>
                      )}
                    </Space>
                  </div>
                  <Paragraph style={{ margin: '4px 0 0 22px', color: 'var(--text-secondary)', fontSize: 13 }}>
                    {entry.problem_signature}
                  </Paragraph>
                </div>

                {(entry.match_score !== undefined || entry.retrieval_sources?.length || entry.match_reason) && (
                  <div style={{ marginLeft: 22 }}>
                    <Space wrap size={6}>
                      {entry.match_score !== undefined && <Tag color="cyan">匹配 {scorePercent(entry.match_score)}%</Tag>}
                      {entry.final_score !== undefined && <Tag color="geekblue">综合 {scorePercent(entry.final_score)}%</Tag>}
                      {entry.retrieval_sources?.map((source) => <Tag key={source} color="purple">{retrievalSourceLabel(source)}</Tag>)}
                    </Space>
                    {entry.match_reason && (
                      <Paragraph style={{ margin: '6px 0 0', color: 'var(--text-muted)', fontSize: 12 }}>
                        <FilterOutlined style={{ marginRight: 4 }} />
                        {entry.match_reason}
                      </Paragraph>
                    )}
                  </div>
                )}

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
                    {entry.source_incident_id && (
                      <Text style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                        <LinkOutlined style={{ marginRight: 4 }} />
                        复盘追踪: {shortId(entry.source_incident_id)}
                      </Text>
                    )}
                    {entry.incident_type && (
                      <Tag color="default" style={{ margin: 0 }}>{incidentTypeLabel(entry.incident_type)}</Tag>
                    )}
                  </Space>
                </div>

                <Collapse
                  ghost
                  size="small"
                  items={[
                    {
                      key: 'memory-details',
                      label: <Text style={{ fontSize: 12, color: 'var(--text-secondary)' }}>复盘与证据</Text>,
                      children: (
                        <Space direction="vertical" size={10} style={{ width: '100%' }}>
                          {entry.score_breakdown && (
                            <div>
                              <Text strong style={{ fontSize: 12 }}>评分拆解</Text>
                              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8, marginTop: 8 }}>
                                {Object.entries(entry.score_breakdown).map(([key, value]) => (
                                  <div key={key}>
                                    <Text style={{ fontSize: 11, color: 'var(--text-muted)' }}>{scoreLabel(key)}</Text>
                                    <Progress percent={scorePercent(value)} size="small" showInfo={false} />
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {entry.recommended_fresh_checks?.length ? (
                            <div>
                              <Text strong style={{ fontSize: 12 }}><CheckCircleOutlined /> 推荐重新检查</Text>
                              <div style={{ marginTop: 6 }}>
                                {entry.recommended_fresh_checks.map((check) => <Tag key={check} color="blue" style={{ marginBottom: 4 }}>{check}</Tag>)}
                              </div>
                            </div>
                          ) : null}

                          {entry.evidence_refs?.length ? (
                            <div>
                              <Text strong style={{ fontSize: 12 }}><LinkOutlined /> 证据引用</Text>
                              <List
                                size="small"
                                dataSource={entry.evidence_refs.slice(0, 5)}
                                renderItem={(ref, idx) => (
                                  <List.Item style={{ padding: '4px 0' }}>
                                    <Text style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                                      {idx + 1}. {evidenceTypeLabel(ref.type)} {ref.call_id ? `#${String(ref.call_id)}` : ''} {ref.summary ? `- ${compactValue(ref.summary, 180)}` : ''}
                                    </Text>
                                  </List.Item>
                                )}
                              />
                            </div>
                          ) : null}

                          <Descriptions size="small" column={1} bordered={false}>
                            {entry.source_session_id && <Descriptions.Item label="会话追踪编号">{entry.source_session_id}</Descriptions.Item>}
                            {entry.source_incident_id && <Descriptions.Item label="复盘追踪编号">{entry.source_incident_id}</Descriptions.Item>}
                            {entry.source_incident_id && <Descriptions.Item label="编号用途">用于关联本次经验对应的事件时间线，方便审计、复盘和排查证据来源。</Descriptions.Item>}
                            {entry.owner && <Descriptions.Item label="负责人">{entry.owner}</Descriptions.Item>}
                            {entry.ttl_days && <Descriptions.Item label="复核周期">{entry.ttl_days} 天</Descriptions.Item>}
                            {entry.last_validated_at && <Descriptions.Item label="最后验证">{new Date(entry.last_validated_at).toLocaleString()}</Descriptions.Item>}
                            {entry.entities && Object.keys(entry.entities).length > 0 && (
                              <Descriptions.Item label="实体">{compactValue(entry.entities, 260)}</Descriptions.Item>
                            )}
                            <Descriptions.Item label="写操作">
                              {entry.has_write_action ? `有${entry.write_approved ? '，已审批' : '，未确认审批'}` : '无'}
                            </Descriptions.Item>
                          </Descriptions>
                        </Space>
                      ),
                    },
                  ]}
                />
              </Space>
            </Card>
          )}
        />
      )}
    </div>
  )
}

export default KnowledgePanel
