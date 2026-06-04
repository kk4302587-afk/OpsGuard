import { useEffect, useMemo, useRef, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { Button, Spin, Empty, Typography, Space, Tag, Segmented } from 'antd'
import {
  ApartmentOutlined,
  ApiOutlined,
  ArrowLeftOutlined,
  ClusterOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  HddOutlined,
  LinkOutlined,
  NodeIndexOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { useChatStore } from '../stores/chatStore'

const { Title, Text } = Typography

interface TopologyNode {
  id: string
  name: string
  category: string
  value?: string
  highlight?: boolean
  rca_role?: RcaRole
  annotations?: TopologyAnnotation[]
}

interface TopologyEdge {
  source: string
  target: string
  relation: string
  inferred?: boolean
  annotations?: TopologyAnnotation[]
}

type RcaRole = 'affected' | 'suspected_root_cause' | 'downstream_impact' | 'evidence'
type TopologyViewMode = 'system' | 'latest' | 'session'

interface TopologyAnnotation {
  target_id: string
  target_type: string
  rca_role: RcaRole
  evidence_summary: string
  source: string
  phase: string
  event_type: string
  execution_state: 'executed' | 'failed'
  inferred?: boolean
}

interface TopologyData {
  nodes: TopologyNode[]
  edges: TopologyEdge[]
  categories: { name: string; itemStyle: { color: string } }[]
  annotations?: TopologyAnnotation[]
  rca_candidates?: RcaCandidate[]
}

interface RcaCandidate {
  candidate_id: string
  candidate_type: string
  name: string
  confidence: 'high' | 'medium' | 'low'
  score: number
  reasons: string[]
  evidence_summaries: string[]
  impact_path: string[]
  affected_targets: string[]
}

const categoryLabels: Record<string, string> = {
  process: '进程',
  port: '端口',
  service: '服务',
  config: '配置',
  remote: '远程连接',
  log: '日志',
}

const roleLabels: Record<RcaRole, string> = {
  affected: '受影响',
  suspected_root_cause: '疑似根因',
  downstream_impact: '影响范围',
  evidence: '证据关联',
}

const roleColors: Record<RcaRole, string> = {
  affected: '#e06c75',
  suspected_root_cause: '#d19a66',
  downstream_impact: '#e5c07b',
  evidence: '#00d4aa',
}

const confidenceLabels: Record<RcaCandidate['confidence'], string> = {
  high: '高',
  medium: '中',
  low: '低',
}

const confidenceColors: Record<RcaCandidate['confidence'], string> = {
  high: 'red',
  medium: 'orange',
  low: 'blue',
}

const backgroundNodeColor = '#5c6370'

const categoryColors: Record<string, string> = {
  process: '#61afef',
  port: '#e5c07b',
  service: '#00d4aa',
  config: '#c678dd',
  remote: '#e06c75',
  log: '#d19a66',
}

const categorySymbols: Record<string, string> = {
  service: 'roundRect',
  process: 'circle',
  port: 'diamond',
  config: 'rect',
  remote: 'triangle',
  log: 'pin',
}

const categoryIcons: Record<string, JSX.Element> = {
  service: <ApartmentOutlined />,
  process: <ApiOutlined />,
  port: <ClusterOutlined />,
  config: <FileTextOutlined />,
  remote: <LinkOutlined />,
  log: <DatabaseOutlined />,
}

const categoryShapeLabels: Record<string, string> = {
  service: '圆角方块',
  process: '圆形',
  port: '菱形',
  config: '方块',
  remote: '三角形',
  log: '图钉',
}

const escapeHtml = (value: string) => (
  value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
)

const compactText = (value: string, maxLength = 180) => {
  const normalized = value.replace(/\s+/g, ' ').trim()
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength)}...` : normalized
}

const formatEvidenceText = (value: string, maxLength = 500) => (
  compactText(value, maxLength)
    .split(/(?<=\.)\s+|(?<=。)\s*|(?<=!)\s+|(?<=！)\s+/)
    .filter(Boolean)
    .slice(0, 6)
    .join('\n')
)

/**
 * Fault correlation topology graph using ECharts.
 * Visualizes relationships between processes, ports, services, configs.
 */
function TopologyGraph() {
  const activeSessionId = useChatStore((state) => state.activeSessionId)
  const [data, setData] = useState<TopologyData | null>(null)
  const [loading, setLoading] = useState(false)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [showAllNodes, setShowAllNodes] = useState(true)
  const [enabledCategories, setEnabledCategories] = useState<string[]>([])
  const [chartResetKey, setChartResetKey] = useState(0)
  const [viewMode, setViewMode] = useState<TopologyViewMode>('system')
  const requestSeq = useRef(0)

  useEffect(() => {
    fetchTopology()
  }, [activeSessionId, viewMode])

  useEffect(() => {
    if (viewMode !== 'system') {
      setViewMode('system')
    }
  }, [activeSessionId])

  const categoryMap = useMemo(() => {
    const map: Record<string, number> = {}
    data?.categories.forEach((cat, idx) => {
      map[cat.name] = idx
    })
    return map
  }, [data])

  const annotatedNodeIds = useMemo(() => {
    const ids = new Set<string>()
    data?.annotations?.forEach((annotation) => ids.add(annotation.target_id))
    data?.nodes.forEach((node) => {
      if (node.highlight || node.rca_role || node.annotations?.length) {
        ids.add(node.id)
      }
    })
    return ids
  }, [data])

  const neighborOfAnnotatedIds = useMemo(() => {
    const ids = new Set<string>()
    data?.edges.forEach((edge) => {
      if (annotatedNodeIds.has(edge.source)) ids.add(edge.target)
      if (annotatedNodeIds.has(edge.target)) ids.add(edge.source)
    })
    return ids
  }, [data, annotatedNodeIds])

  const getNodeScore = (node: TopologyNode) => {
    const roleScore: Record<RcaRole, number> = {
      affected: 100,
      suspected_root_cause: 95,
      downstream_impact: 80,
      evidence: 70,
    }
    const categoryScore: Record<string, number> = {
      service: 35,
      process: 28,
      config: 22,
      log: 20,
      remote: 14,
      port: 10,
    }
    return (
      (node.rca_role ? roleScore[node.rca_role] : 0) +
      (node.highlight ? 45 : 0) +
      (node.annotations?.length || 0) * 18 +
      (annotatedNodeIds.has(node.id) ? 40 : 0) +
      (neighborOfAnnotatedIds.has(node.id) ? 25 : 0) +
      (categoryScore[node.category] || 8)
    )
  }

  const displayedNodes = useMemo(() => {
    if (!data) return []

    const categoryEnabled = new Set(enabledCategories)
    const filteredByType = data.nodes.filter((node) => categoryEnabled.has(node.category))
    if (showAllNodes) return filteredByType

    const maxNodes = data.annotations?.length ? 24 : 30
    return [...filteredByType]
      .sort((a, b) => getNodeScore(b) - getNodeScore(a))
      .slice(0, maxNodes)
  }, [data, enabledCategories, showAllNodes, annotatedNodeIds, neighborOfAnnotatedIds])

  const displayedNodeIds = useMemo(
    () => new Set(displayedNodes.map((node) => node.id)),
    [displayedNodes],
  )

  const displayedEdges = useMemo(() => (
    data?.edges.filter((edge) => (
      displayedNodeIds.has(edge.source) && displayedNodeIds.has(edge.target)
    )) || []
  ), [data, displayedNodeIds])

  const selectedNode = useMemo(() => (
    selectedNodeId && data
      ? data.nodes.find((node) => node.id === selectedNodeId) || null
      : null
  ), [data, selectedNodeId])

  const roleCounts = useMemo(() => (
    data?.nodes.reduce<Record<RcaRole, number>>((acc, node) => {
      if (node.rca_role) acc[node.rca_role] += 1
      return acc
    }, { affected: 0, suspected_root_cause: 0, downstream_impact: 0, evidence: 0 }) ||
    { affected: 0, suspected_root_cause: 0, downstream_impact: 0, evidence: 0 }
  ), [data])

  const rcaCandidates = data?.rca_candidates || []

  const visibleCategoryCounts = useMemo(() => (
    displayedNodes.reduce<Record<string, number>>((acc, node) => {
      acc[node.category] = (acc[node.category] || 0) + 1
      return acc
    }, {})
  ), [displayedNodes])

  const hasFaultEvidence = useMemo(() => (
    Boolean(
      (data?.annotations?.length || 0) > 0 ||
      data?.nodes.some((node) => node.rca_role || node.highlight || (node.annotations?.length || 0) > 0),
    )
  ), [data])

  const isEvidenceView = viewMode !== 'system' && Boolean(activeSessionId)
  const hasMappedEvidence = isEvidenceView && hasFaultEvidence
  const shouldShowEvidenceEmptyState = isEvidenceView && !hasFaultEvidence
  const visibleNodeCount = shouldShowEvidenceEmptyState ? 0 : displayedNodes.length
  const emptyEvidenceTitle = viewMode === 'latest' ? '本轮请求暂无拓扑证据' : '整个会话暂无拓扑证据'
  const emptyEvidenceDescription = (
    viewMode === 'latest'
      ? '本轮诊断没有产生可映射到服务、日志、端口、配置或进程的证据。系统状态指标会保留在对话和推理链路中，不作为拓扑实体展示。'
      : '当前会话还没有可映射到服务、日志、端口、配置或进程的证据。系统状态指标会保留在对话和推理链路中，不作为拓扑实体展示。'
  )

  const fetchTopology = async () => {
    const requestId = requestSeq.current + 1
    requestSeq.current = requestId
    setLoading(true)
    try {
      const endpoint = (
        activeSessionId && viewMode !== 'system'
          ? `/api/topology/graph/${activeSessionId}?scope=${viewMode === 'latest' ? 'latest' : 'session'}`
          : '/api/topology/graph'
      )
      const res = await fetch(endpoint)
      if (res.ok) {
        const result = await res.json()
        if (requestId !== requestSeq.current) return
        setData(result)
        setSelectedNodeId(null)
        const categoryNames = (result.categories || []).map((category: { name: string }) => category.name)
        setEnabledCategories((current) => (
          current.length > 0 ? current.filter((name) => categoryNames.includes(name)) : categoryNames
        ))
      }
    } catch (err) {
      console.error('Failed to fetch topology:', err)
    } finally {
      if (requestId === requestSeq.current) {
        setLoading(false)
      }
    }
  }

  if (loading && !data) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
        <Spin>
          <div style={{ width: 180, height: 80, display: 'flex', alignItems: 'flex-end', justifyContent: 'center', color: 'var(--text-muted)' }}>
            正在扫描系统拓扑...
          </div>
        </Spin>
      </div>
    )
  }

  if (!data || data.nodes.length === 0) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
        <Empty description="暂无拓扑数据" image={Empty.PRESENTED_IMAGE_SIMPLE}>
          <Button type="primary" icon={<ReloadOutlined />} onClick={fetchTopology}>重新扫描</Button>
        </Empty>
      </div>
    )
  }

  const getNodeColor = (node: TopologyNode) => {
    if (hasMappedEvidence) return node.rca_role ? roleColors[node.rca_role] : backgroundNodeColor
    return categoryColors[node.category] || backgroundNodeColor
  }

  const getNodeStatusLabel = (node?: TopologyNode | null) => {
    if (!node) return '未选择'
    if (node.rca_role) return roleLabels[node.rca_role]
    return hasMappedEvidence ? '背景节点' : '系统拓扑节点'
  }

  const toggleCategory = (category: string) => {
    setEnabledCategories((current) => {
      if (current.includes(category)) {
        return current.length > 1 ? current.filter((item) => item !== category) : current
      }
      return [...current, category]
    })
  }

  const handleChartClick = (params: any) => {
    if (params.dataType === 'node' && params.data?.id) {
      setSelectedNodeId(params.data.id)
    }
  }

  const resetView = () => {
    setViewMode('system')
    setSelectedNodeId(null)
    setShowAllNodes(true)
    setEnabledCategories(data?.categories.map((category) => category.name) || [])
    setChartResetKey((value) => value + 1)
  }

  const returnToChat = () => {
    window.dispatchEvent(new CustomEvent('opsguard:navigate', { detail: 'chat' }))
  }

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: '#ffffff',
      borderColor: '#c8d2df',
      confine: true,
      extraCssText: 'max-width: 360px; white-space: normal; word-break: break-word; line-height: 1.5; box-shadow: 0 10px 30px rgba(15, 23, 42, 0.14);',
      textStyle: { color: '#1f2937', fontSize: 12 },
      formatter: (params: any) => {
        if (params.dataType === 'node') {
          const cat = params.data.rawCategory || ''
          const annotations = (params.data.annotations || []) as TopologyAnnotation[]
          const annotationText = annotations.slice(0, 3).map((item) => (
            `<br/><span style="color:${roleColors[item.rca_role] || '#6b7280'}">${roleLabels[item.rca_role] || item.rca_role}</span>` +
            `<br/><span style="color:#6b7280">来源：${item.source} / ${item.execution_state === 'executed' ? '已执行' : '失败'}</span>` +
            `<br/>${escapeHtml(compactText(item.evidence_summary, 160))}`
          )).join('')
          return `<strong>${escapeHtml(params.data.name)}</strong><br/><span style="color:#6b7280">类型：${categoryLabels[cat] || cat}</span><br/><span style="color:#6b7280">状态：${params.data.statusLabel}</span>${params.data.value ? `<br/>${escapeHtml(compactText(params.data.value, 120))}` : ''}${annotationText}`
        }
        if (params.dataType === 'edge') {
          const inferred = params.data.inferred ? '<br/><span style="color:#b7791f">推断关系</span>' : '<br/><span style="color:#059669">检测关系</span>'
          return `<span style="color:#6b7280">关系：${params.data.relation || ''}</span>${inferred}`
        }
        return ''
      },
    },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        animation: true,
        animationDuration: 1500,
        animationEasingUpdate: 'quinticInOut',
        label: {
          show: true,
          position: 'bottom',
          color: '#4b5563',
          fontSize: 11,
          fontFamily: "'JetBrains Mono', monospace",
          formatter: (params: any) => {
            const name = params.data.name as string
            // Only show labels for process and service nodes, hide port/remote labels
            if (params.data.category === categoryMap['port'] || params.data.category === categoryMap['remote']) {
              return ''
            }
            return name.length > 16 ? name.slice(0, 16) + '..' : name
          },
        },
        edgeLabel: {
          show: false,
        },
        categories: data.categories.map((c) => ({ name: categoryLabels[c.name] || c.name })),
        data: displayedNodes.map((node) => {
          // Size based on category importance
          let size = 16
          if (node.category === 'service') size = 38
          else if (node.category === 'process') size = 30
          else if (node.category === 'remote') size = 24
          else if (node.category === 'port') size = 14
          else if (node.category === 'config') size = 20
          else if (node.category === 'log') size = 20

          const role = node.rca_role
          const nodeColor = getNodeColor(node)
          const isAnnotated = Boolean(node.highlight || node.annotations?.length)
          const isSelected = selectedNodeId === node.id
          const isDimmedBackground = hasMappedEvidence && !role && !isAnnotated && !isSelected

          return {
            id: node.id,
            name: node.name,
            value: node.value,
            rawCategory: node.category,
            category: categoryMap[node.category] ?? 0,
            annotations: node.annotations || [],
            rca_role: role,
            statusLabel: getNodeStatusLabel(node),
            symbol: categorySymbols[node.category] || 'circle',
            symbolSize: role ? size + 8 : size,
            label: {
              show: isSelected || isAnnotated || Boolean(role) || node.category === 'service' || (!hasMappedEvidence && node.category === 'process'),
              color: isDimmedBackground ? '#9aa3af' : '#1f2937',
              fontWeight: isSelected || isAnnotated || role ? 700 : 500,
            },
            itemStyle: {
              color: nodeColor,
              opacity: isDimmedBackground ? 0.45 : 0.95,
              shadowBlur: isAnnotated || isSelected ? 14 : 3,
              shadowColor: isAnnotated || isSelected ? nodeColor + '45' : 'rgba(15, 23, 42, 0.16)',
              borderColor: isAnnotated || isSelected ? nodeColor : nodeColor + '99',
              borderWidth: isSelected ? 4 : isAnnotated ? 3 : 1.5,
            },
          }
        }),
        edges: displayedEdges.map((edge) => {
          const hasEvidence = edge.relation === 'evidence_link' || Boolean(edge.annotations?.length)
          return {
            source: edge.source,
            target: edge.target,
            relation: edge.relation,
            inferred: edge.inferred,
            annotations: edge.annotations || [],
            lineStyle: {
              color: hasEvidence ? '#059669' : hasMappedEvidence ? '#c8d2df' : '#94a3b8',
              width: hasEvidence ? 2.8 : edge.inferred ? 1.2 : 1.8,
              curveness: 0.2,
              opacity: hasEvidence ? 0.9 : hasMappedEvidence ? 0.45 : 0.6,
              type: edge.inferred || edge.relation === 'connects_to' ? 'dashed' as const : 'solid' as const,
            },
          }
        }),
        force: {
          repulsion: 260,
          edgeLength: [80, 170],
          gravity: 0.06,
          friction: 0.65,
          layoutAnimation: true,
        },
        emphasis: {
          focus: 'adjacency',
          lineStyle: { width: 4, color: '#059669' },
          itemStyle: { shadowBlur: 12, shadowColor: 'rgba(5, 150, 105, 0.22)' },
          label: { show: true, fontSize: 13, color: '#1f2937' },
        },
        blur: {
          itemStyle: { opacity: 0.3 },
          lineStyle: { opacity: 0.1 },
        },
      },
    ],
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{ padding: '16px 24px', borderBottom: '1px solid var(--border-color)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Space wrap>
          <Title level={5} style={{ color: 'var(--text-primary)', margin: 0 }}>
            <ApartmentOutlined style={{ marginRight: 8, color: 'var(--accent-green)' }} />
            故障关联图谱
          </Title>
          <Tag>
            <NodeIndexOutlined style={{ marginRight: 4 }} />
            展示 {visibleNodeCount}/{data.nodes.length} 节点
          </Tag>
          {viewMode !== 'system' && activeSessionId && (
            <Tag color={(data.annotations?.length || 0) > 0 ? 'orange' : 'default'}>
              根因线索 {data.annotations?.length || 0}
            </Tag>
          )}
          {viewMode !== 'system' && activeSessionId && (
            <Tag color={rcaCandidates.length > 0 ? 'red' : 'default'}>
              RCA候选 {rcaCandidates.length}
            </Tag>
          )}
          {viewMode === 'latest' && <Tag color="blue">本轮请求</Tag>}
          {viewMode === 'session' && <Tag color="purple">整个会话</Tag>}
          {hasMappedEvidence ? (
            <>
              <Tag color={roleCounts.suspected_root_cause > 0 ? 'orange' : 'default'}>疑似根因 {roleCounts.suspected_root_cause}</Tag>
              <Tag color={roleCounts.affected > 0 ? 'red' : 'default'}>受影响 {roleCounts.affected}</Tag>
              <Tag color={roleCounts.downstream_impact > 0 ? 'gold' : 'default'}>影响范围 {roleCounts.downstream_impact}</Tag>
            </>
          ) : (
            <Tag color={isEvidenceView ? 'default' : 'cyan'}>
              {isEvidenceView ? '无可映射证据' : '系统拓扑视图'}
            </Tag>
          )}
        </Space>
        <Space>
          <Segmented
            size="small"
            value={viewMode}
            onChange={(value) => setViewMode(value as TopologyViewMode)}
            options={[
              { label: '系统拓扑', value: 'system' },
              { label: '本轮请求', value: 'latest', disabled: !activeSessionId },
              { label: '整个会话', value: 'session', disabled: !activeSessionId },
            ]}
          />
          <Button icon={<ArrowLeftOutlined />} onClick={returnToChat}>
            返回对话
          </Button>
          <Button onClick={resetView}>
            恢复默认
          </Button>
          <Button icon={<ReloadOutlined />} onClick={fetchTopology} loading={loading}>
            刷新
          </Button>
        </Space>
      </div>

      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {/* Graph */}
        <div style={{ flex: 1, padding: 8, minWidth: 0 }}>
          {shouldShowEvidenceEmptyState ? (
            <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  <Space direction="vertical" size={6}>
                    <Text style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{emptyEvidenceTitle}</Text>
                    <Text style={{ maxWidth: 520, display: 'block', color: 'var(--text-secondary)', lineHeight: 1.7 }}>
                      {emptyEvidenceDescription}
                    </Text>
                  </Space>
                }
              >
                <Space>
                  <Button onClick={() => setViewMode('system')}>查看系统拓扑</Button>
                  <Button icon={<ReloadOutlined />} onClick={fetchTopology} loading={loading}>刷新证据</Button>
                </Space>
              </Empty>
            </div>
          ) : (
            <ReactECharts
              key={chartResetKey}
              option={option}
              style={{ height: '100%', width: '100%' }}
              opts={{ renderer: 'canvas' }}
              onEvents={{ click: handleChartClick }}
            />
          )}
        </div>

        <aside
          style={{
            width: 320,
            minWidth: 320,
            borderLeft: '1px solid var(--border-color)',
            padding: 16,
            overflow: 'auto',
            background: 'var(--bg-primary)',
          }}
        >
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            {hasMappedEvidence ? (
              <div>
                <Text strong style={{ display: 'block', color: 'var(--text-primary)', marginBottom: 8 }}>
                  颜色含义
                </Text>
                {([
                  ['受影响', roleColors.affected, roleCounts.affected],
                  ['疑似根因', roleColors.suspected_root_cause, roleCounts.suspected_root_cause],
                  ['影响范围', roleColors.downstream_impact, roleCounts.downstream_impact],
                  ['证据关联', roleColors.evidence, roleCounts.evidence],
                  ['背景节点', backgroundNodeColor, displayedNodes.filter((node) => !node.rca_role && !node.annotations?.length && !node.highlight).length],
                ] as Array<[string, string, number]>).map(([label, color, count]) => (
                  <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span style={{ width: 10, height: 10, borderRadius: 10, background: color, display: 'inline-block' }} />
                    <Text style={{ fontSize: 12, color: 'var(--text-secondary)', flex: 1 }}>{label}</Text>
                    <Text style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>{count}</Text>
                  </div>
                ))}
                <Text style={{ display: 'block', marginTop: 6, fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.6 }}>
                  当前故障视图中，颜色只表示证据角色，不表示节点类型。
                </Text>
              </div>
            ) : (
              <div>
                <Text strong style={{ display: 'block', color: 'var(--text-primary)', marginBottom: 6 }}>
                  当前视图
                </Text>
                <Text style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.7 }}>
                  {isEvidenceView
                    ? '当前范围没有可映射到拓扑实体的诊断证据，暂时只显示系统拓扑底图。系统指标会保留在报告和证据链中；日志、服务、端口、配置和进程证据会在这里形成故障关联。'
                    : '当前展示系统拓扑，图中颜色表示节点类型；选择“本轮请求”或“整个会话”后，会叠加诊断证据形成故障关联图谱。'}
                </Text>
              </div>
            )}

            {rcaCandidates.length > 0 && (
              <div>
                <Text strong style={{ display: 'block', color: 'var(--text-primary)', marginBottom: 8 }}>
                  RCA 候选
                </Text>
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  {rcaCandidates.slice(0, 3).map((candidate, index) => (
                    <div
                      key={candidate.candidate_id}
                      style={{
                        padding: '8px 10px',
                        border: '1px solid var(--border-color)',
                        borderRadius: 6,
                        background: 'var(--bg-secondary)',
                      }}
                    >
                      <Space wrap size={4} style={{ marginBottom: 6 }}>
                        <Tag color={index === 0 ? 'red' : 'orange'} style={{ margin: 0 }}>
                          #{index + 1}
                        </Tag>
                        <Tag color={confidenceColors[candidate.confidence]} style={{ margin: 0 }}>
                          置信度 {confidenceLabels[candidate.confidence]}
                        </Tag>
                        <Tag style={{ margin: 0 }}>分数 {candidate.score}</Tag>
                      </Space>
                      <Text style={{ display: 'block', color: 'var(--text-primary)', fontWeight: 600, wordBreak: 'break-word' }}>
                        {candidate.name}
                      </Text>
                      {candidate.impact_path?.length > 0 && (
                        <Text style={{ display: 'block', marginTop: 4, fontSize: 12, color: 'var(--text-secondary)', wordBreak: 'break-word' }}>
                          路径：{candidate.impact_path.join(' → ')}
                        </Text>
                      )}
                      {candidate.reasons?.slice(0, 3).map((reason) => (
                        <Text key={reason} style={{ display: 'block', marginTop: 4, fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                          - {reason}
                        </Text>
                      ))}
                    </div>
                  ))}
                </Space>
              </div>
            )}

            <div>
              <Text strong style={{ display: 'block', color: 'var(--text-primary)', marginBottom: 8 }}>
                {hasMappedEvidence ? '形状与筛选' : '节点类型'}
              </Text>
              <Space wrap size={[6, 8]}>
                {data.categories.map((category) => {
                  const checked = enabledCategories.includes(category.name)
                  const categoryColor = hasMappedEvidence ? backgroundNodeColor : (categoryColors[category.name] || backgroundNodeColor)
                  return (
                    <Tag.CheckableTag
                      key={category.name}
                      checked={checked}
                      onChange={() => toggleCategory(category.name)}
                      style={{
                        border: `1px solid ${checked ? categoryColor : 'var(--border-color)'}`,
                        borderRadius: 4,
                        color: checked ? 'var(--text-primary)' : 'var(--text-muted)',
                        background: checked ? `${categoryColor}22` : 'transparent',
                      }}
                    >
                      <span
                        style={{
                          width: 7,
                          height: 7,
                          borderRadius: category.name === 'process' ? 7 : category.name === 'service' ? 2 : 0,
                          transform: category.name === 'port' ? 'rotate(45deg)' : undefined,
                          clipPath: category.name === 'remote' ? 'polygon(50% 0, 100% 100%, 0 100%)' : undefined,
                          background: categoryColor,
                          display: 'inline-block',
                          marginRight: 5,
                        }}
                      />
                      <span style={{ marginRight: 4 }}>{categoryIcons[category.name] || <HddOutlined />}</span>
                      {categoryLabels[category.name] || category.name}
                      {hasMappedEvidence && (
                        <span style={{ marginLeft: 4, color: 'var(--text-muted)', fontSize: 11 }}>
                          {visibleCategoryCounts[category.name] || 0}
                        </span>
                      )}
                    </Tag.CheckableTag>
                  )
                })}
              </Space>
              {hasMappedEvidence && (
                <Text style={{ display: 'block', marginTop: 8, fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.6 }}>
                  节点类型由形状区分：{data.categories.map((category) => `${categoryLabels[category.name] || category.name}=${categoryShapeLabels[category.name] || '默认形状'}`).join('，')}。
                </Text>
              )}
            </div>

            <div>
              <Text strong style={{ display: 'block', color: 'var(--text-primary)', marginBottom: 8 }}>
                关系说明
              </Text>
              <Text style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>
                实线表示检测到的直接关系
              </Text>
              <Text style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)' }}>
                虚线表示系统推断或弱关联关系
              </Text>
            </div>

            <div>
              <Button size="small" block onClick={() => setShowAllNodes((value) => !value)}>
                {showAllNodes ? '只看关键节点' : `显示全部节点（隐藏 ${data.nodes.length - displayedNodes.length} 个）`}
              </Button>
            </div>

            <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: 14 }}>
              <Text strong style={{ display: 'block', color: 'var(--text-primary)', marginBottom: 8 }}>
                节点详情
              </Text>
              {selectedNode ? (
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Text style={{ color: 'var(--text-primary)', fontWeight: 600, wordBreak: 'break-word' }}>
                    {selectedNode.name}
                  </Text>
                  <Space wrap>
                    <Tag>{categoryLabels[selectedNode.category] || selectedNode.category}</Tag>
                    <Tag color={selectedNode.rca_role ? 'orange' : 'default'}>{getNodeStatusLabel(selectedNode)}</Tag>
                  </Space>
                  {selectedNode.value && (
                    <Text style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      {selectedNode.value}
                    </Text>
                  )}
                  {(selectedNode.annotations || []).length > 0 ? (
                    <>
                      {selectedNode.annotations?.slice(0, 3).map((annotation, index) => (
                        <div
                          key={`${annotation.target_id}-${index}`}
                          style={{
                            padding: '8px 10px',
                            border: '1px solid var(--border-color)',
                            borderRadius: 6,
                            background: 'var(--bg-secondary)',
                          }}
                        >
                          <Tag color={annotation.rca_role === 'affected' ? 'red' : annotation.rca_role === 'suspected_root_cause' ? 'orange' : 'gold'} style={{ marginBottom: 6 }}>
                            {roleLabels[annotation.rca_role]}
                          </Tag>
                          <div
                            style={{
                              maxHeight: 132,
                              overflow: 'auto',
                              paddingRight: 4,
                            }}
                          >
                            <Text style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.6 }}>
                              {formatEvidenceText(annotation.evidence_summary)}
                            </Text>
                          </div>
                          <Text style={{ display: 'block', marginTop: 6, fontSize: 11, color: 'var(--text-muted)' }}>
                            来源：{annotation.source} / {annotation.execution_state === 'executed' ? '已执行' : '失败'}
                          </Text>
                        </div>
                      ))}
                      {(selectedNode.annotations?.length || 0) > 3 && (
                        <Text style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                          还有 {(selectedNode.annotations?.length || 0) - 3} 条证据，已折叠以保持页面可读。
                        </Text>
                      )}
                    </>
                  ) : (
                    <Text style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      {hasMappedEvidence
                        ? '该节点暂未绑定故障证据，可点击相邻节点继续查看上下游关系。'
                        : '该节点来自系统拓扑扫描，暂未和当前会话故障证据关联。'}
                    </Text>
                  )}
                </Space>
              ) : (
                <Text style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  点击图中的服务、进程或证据节点，查看它为什么和当前故障有关。
                </Text>
              )}
            </div>
          </Space>
        </aside>
      </div>

      {/* Footer hint */}
      <div style={{ padding: '8px 24px', borderTop: '1px solid var(--border-color)' }}>
        <Text style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          拖拽节点调整布局 / 滚轮缩放 / 点击节点查看证据 / 默认进入系统拓扑视图
        </Text>
      </div>
    </div>
  )
}

export default TopologyGraph
