import { useEffect, useMemo, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { Button, Spin, Empty, Typography, Space, Tag } from 'antd'
import {
  ApartmentOutlined,
  ApiOutlined,
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

/**
 * Fault correlation topology graph using ECharts.
 * Visualizes relationships between processes, ports, services, configs.
 */
function TopologyGraph() {
  const activeSessionId = useChatStore((state) => state.activeSessionId)
  const [data, setData] = useState<TopologyData | null>(null)
  const [loading, setLoading] = useState(false)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [showAllNodes, setShowAllNodes] = useState(false)
  const [enabledCategories, setEnabledCategories] = useState<string[]>([])

  useEffect(() => {
    fetchTopology()
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

  const hasFaultEvidence = useMemo(() => (
    Boolean(
      (data?.annotations?.length || 0) > 0 ||
      data?.nodes.some((node) => node.rca_role || node.highlight || (node.annotations?.length || 0) > 0),
    )
  ), [data])

  const fetchTopology = async () => {
    setLoading(true)
    try {
      const endpoint = activeSessionId ? `/api/topology/graph/${activeSessionId}` : '/api/topology/graph'
      const res = await fetch(endpoint)
      if (res.ok) {
        const result = await res.json()
        setData(result)
        const categoryNames = (result.categories || []).map((category: { name: string }) => category.name)
        setEnabledCategories((current) => (
          current.length > 0 ? current.filter((name) => categoryNames.includes(name)) : categoryNames
        ))
      }
    } catch (err) {
      console.error('Failed to fetch topology:', err)
    } finally {
      setLoading(false)
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
    if (hasFaultEvidence) return node.rca_role ? roleColors[node.rca_role] : backgroundNodeColor
    return categoryColors[node.category] || backgroundNodeColor
  }

  const getNodeStatusLabel = (node?: TopologyNode | null) => {
    if (!node) return '未选择'
    if (node.rca_role) return roleLabels[node.rca_role]
    return hasFaultEvidence ? '背景节点' : '系统拓扑节点'
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

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: '#21252b',
      borderColor: '#2d3139',
      textStyle: { color: '#e4e7eb', fontSize: 12 },
      formatter: (params: any) => {
        if (params.dataType === 'node') {
          const cat = params.data.rawCategory || ''
          const annotations = (params.data.annotations || []) as TopologyAnnotation[]
          const annotationText = annotations.slice(0, 3).map((item) => (
            `<br/><span style="color:${roleColors[item.rca_role] || '#8b929a'}">${roleLabels[item.rca_role] || item.rca_role}</span>` +
            `<br/><span style="color:#8b929a">来源：${item.source} / ${item.execution_state === 'executed' ? '已执行' : '失败'}</span>` +
            `<br/>${item.evidence_summary}`
          )).join('')
          return `<strong>${params.data.name}</strong><br/><span style="color:#8b929a">类型：${categoryLabels[cat] || cat}</span><br/><span style="color:#8b929a">状态：${params.data.statusLabel}</span>${params.data.value ? `<br/>${params.data.value}` : ''}${annotationText}`
        }
        if (params.dataType === 'edge') {
          const inferred = params.data.inferred ? '<br/><span style="color:#d19a66">推断关系</span>' : '<br/><span style="color:#00d4aa">检测关系</span>'
          return `<span style="color:#8b929a">关系：${params.data.relation || ''}</span>${inferred}`
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
          color: '#8b929a',
          fontSize: 9,
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
          const isDimmedBackground = hasFaultEvidence && !role && !isAnnotated && !isSelected

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
              show: isSelected || isAnnotated || Boolean(role) || node.category === 'service' || (!hasFaultEvidence && node.category === 'process'),
              color: isDimmedBackground ? '#6f7782' : '#d8dee9',
              fontWeight: isSelected || isAnnotated || role ? 700 : 500,
            },
            itemStyle: {
              color: nodeColor,
              opacity: isDimmedBackground ? 0.45 : 0.95,
              shadowBlur: isAnnotated || isSelected ? 24 : 6,
              shadowColor: isAnnotated || isSelected ? nodeColor + '80' : nodeColor + '35',
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
              color: hasEvidence ? '#00d4aa' : hasFaultEvidence ? '#3d4450' : '#4b5563',
              width: hasEvidence ? 2.8 : edge.inferred ? 1.2 : 1.8,
              curveness: 0.2,
              opacity: hasEvidence ? 0.9 : hasFaultEvidence ? 0.45 : 0.6,
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
          lineStyle: { width: 4, color: '#00d4aa' },
          itemStyle: { shadowBlur: 20, shadowColor: '#00d4aa40' },
          label: { show: true, fontSize: 11, color: '#e4e7eb' },
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
            展示 {displayedNodes.length}/{data.nodes.length} 节点
          </Tag>
          {activeSessionId && (
            <Tag color={(data.annotations?.length || 0) > 0 ? 'orange' : 'default'}>
              根因线索 {data.annotations?.length || 0}
            </Tag>
          )}
          {hasFaultEvidence ? (
            <>
              <Tag color={roleCounts.suspected_root_cause > 0 ? 'orange' : 'default'}>疑似根因 {roleCounts.suspected_root_cause}</Tag>
              <Tag color={roleCounts.affected > 0 ? 'red' : 'default'}>受影响 {roleCounts.affected}</Tag>
              <Tag color={roleCounts.downstream_impact > 0 ? 'gold' : 'default'}>影响范围 {roleCounts.downstream_impact}</Tag>
            </>
          ) : (
            <Tag color="cyan">系统拓扑视图</Tag>
          )}
        </Space>
        <Button icon={<ReloadOutlined />} onClick={fetchTopology} loading={loading}>
          刷新
        </Button>
      </div>

      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {/* Graph */}
        <div style={{ flex: 1, padding: 8, minWidth: 0 }}>
          <ReactECharts
            option={option}
            style={{ height: '100%', width: '100%' }}
            opts={{ renderer: 'canvas' }}
            onEvents={{ click: handleChartClick }}
          />
        </div>

        <aside
          style={{
            width: 320,
            minWidth: 320,
            borderLeft: '1px solid var(--border-color)',
            padding: 16,
            overflow: 'auto',
            background: 'rgba(255, 255, 255, 0.02)',
          }}
        >
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            {hasFaultEvidence ? (
              <div>
                <Text strong style={{ display: 'block', color: 'var(--text-primary)', marginBottom: 8 }}>
                  故障状态
                </Text>
                {([
                  ['受影响', roleColors.affected],
                  ['疑似根因', roleColors.suspected_root_cause],
                  ['影响范围', roleColors.downstream_impact],
                  ['证据关联', roleColors.evidence],
                  ['背景节点', backgroundNodeColor],
                ] as Array<[string, string]>).map(([label, color]) => (
                  <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span style={{ width: 10, height: 10, borderRadius: 10, background: color, display: 'inline-block' }} />
                    <Text style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{label}</Text>
                  </div>
                ))}
              </div>
            ) : (
              <div>
                <Text strong style={{ display: 'block', color: 'var(--text-primary)', marginBottom: 6 }}>
                  当前视图
                </Text>
                <Text style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.7 }}>
                  当前没有会话故障证据，图中颜色表示节点类型；发起一次诊断后，页面会切换为故障关联视图。
                </Text>
              </div>
            )}

            <div>
              <Text strong style={{ display: 'block', color: 'var(--text-primary)', marginBottom: 8 }}>
                节点类型
              </Text>
              <Space wrap size={[6, 8]}>
                {data.categories.map((category) => {
                  const checked = enabledCategories.includes(category.name)
                  return (
                    <Tag.CheckableTag
                      key={category.name}
                      checked={checked}
                      onChange={() => toggleCategory(category.name)}
                      style={{
                        border: `1px solid ${checked ? (categoryColors[category.name] || 'var(--border-color)') : 'var(--border-color)'}`,
                        borderRadius: 4,
                        color: checked ? 'var(--text-primary)' : 'var(--text-muted)',
                        background: checked ? `${categoryColors[category.name] || '#00d4aa'}22` : 'transparent',
                      }}
                    >
                      <span
                        style={{
                          width: 7,
                          height: 7,
                          borderRadius: 7,
                          background: categoryColors[category.name] || backgroundNodeColor,
                          display: 'inline-block',
                          marginRight: 5,
                        }}
                      />
                      <span style={{ marginRight: 4 }}>{categoryIcons[category.name] || <HddOutlined />}</span>
                      {categoryLabels[category.name] || category.name}
                    </Tag.CheckableTag>
                  )
                })}
              </Space>
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
                    selectedNode.annotations?.slice(0, 4).map((annotation, index) => (
                      <div
                        key={`${annotation.target_id}-${index}`}
                        style={{
                          padding: '8px 10px',
                          border: '1px solid var(--border-color)',
                          borderRadius: 6,
                          background: 'rgba(255, 255, 255, 0.03)',
                        }}
                      >
                        <Tag color={annotation.rca_role === 'affected' ? 'red' : annotation.rca_role === 'suspected_root_cause' ? 'orange' : 'gold'} style={{ marginBottom: 6 }}>
                          {roleLabels[annotation.rca_role]}
                        </Tag>
                        <Text style={{ display: 'block', fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                          {annotation.evidence_summary}
                        </Text>
                        <Text style={{ display: 'block', marginTop: 6, fontSize: 11, color: 'var(--text-muted)' }}>
                          来源：{annotation.source} / {annotation.execution_state === 'executed' ? '已执行' : '失败'}
                        </Text>
                      </div>
                    ))
                  ) : (
                    <Text style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      {hasFaultEvidence
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
          拖拽节点调整布局 / 滚轮缩放 / 点击节点查看证据 / 默认展示高相关节点
        </Text>
      </div>
    </div>
  )
}

export default TopologyGraph
