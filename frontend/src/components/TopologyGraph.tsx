import { useEffect, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { Button, Spin, Empty, Typography, Space, Tag } from 'antd'
import { ApartmentOutlined, ReloadOutlined, NodeIndexOutlined } from '@ant-design/icons'
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
  log: 'Log',
}

const roleLabels: Record<RcaRole, string> = {
  affected: 'Affected',
  suspected_root_cause: 'Suspected RCA',
  downstream_impact: 'Impact',
  evidence: 'Evidence',
}

const roleColors: Record<RcaRole, string> = {
  affected: '#e06c75',
  suspected_root_cause: '#d19a66',
  downstream_impact: '#e5c07b',
  evidence: '#00d4aa',
}

/**
 * Fault correlation topology graph using ECharts.
 * Visualizes relationships between processes, ports, services, configs.
 */
function TopologyGraph() {
  const activeSessionId = useChatStore((state) => state.activeSessionId)
  const [data, setData] = useState<TopologyData | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    fetchTopology()
  }, [activeSessionId])

  const fetchTopology = async () => {
    setLoading(true)
    try {
      const endpoint = activeSessionId ? `/api/topology/graph/${activeSessionId}` : '/api/topology/graph'
      const res = await fetch(endpoint)
      if (res.ok) {
        const result = await res.json()
        setData(result)
      }
    } catch (err) {
      console.error('Failed to fetch topology:', err)
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
        <Spin tip="正在扫描系统拓扑..." />
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

  const categoryMap: Record<string, number> = {}
  data.categories.forEach((cat, idx) => {
    categoryMap[cat.name] = idx
  })

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: '#21252b',
      borderColor: '#2d3139',
      textStyle: { color: '#e4e7eb', fontSize: 12, fontFamily: "'JetBrains Mono', monospace" },
      formatter: (params: any) => {
        if (params.dataType === 'node') {
          const cat = data.categories[params.data.category]?.name || ''
          const annotations = (params.data.annotations || []) as TopologyAnnotation[]
          const annotationText = annotations.slice(0, 3).map((item) => (
            `<br/><span style="color:${roleColors[item.rca_role] || '#8b929a'}">${roleLabels[item.rca_role] || item.rca_role}</span>` +
            `<br/><span style="color:#8b929a">${item.source} / ${item.execution_state}</span>` +
            `<br/>${item.evidence_summary}`
          )).join('')
          return `<strong>${params.data.name}</strong><br/><span style="color:#8b929a">${categoryLabels[cat] || cat}</span>${params.data.value ? `<br/>${params.data.value}` : ''}${annotationText}`
        }
        if (params.dataType === 'edge') {
          const inferred = params.data.inferred ? '<br/><span style="color:#d19a66">inferred relationship</span>' : ''
          return `<span style="color:#8b929a">${params.data.relation || ''}</span>${inferred}`
        }
        return ''
      },
    },
    legend: {
      data: data.categories.map((c) => ({ name: categoryLabels[c.name] || c.name, icon: 'circle' })),
      textStyle: { color: '#8b929a', fontSize: 11 },
      top: 16,
      right: 16,
      orient: 'vertical',
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
        categories: data.categories.map((c) => ({
          name: categoryLabels[c.name] || c.name,
          itemStyle: c.itemStyle,
        })),
        data: data.nodes.map((node) => {
          // Size based on category importance
          let size = 16
          if (node.category === 'service') size = 42
          else if (node.category === 'process') size = 32
          else if (node.category === 'remote') size = 24
          else if (node.category === 'port') size = 12
          else if (node.category === 'config') size = 20

          const catColor = data.categories[categoryMap[node.category]]?.itemStyle.color || '#8b929a'
          const role = node.rca_role
          const roleColor = role ? roleColors[role] : catColor
          const isAnnotated = Boolean(node.highlight || node.annotations?.length)

          return {
            id: node.id,
            name: node.name,
            value: node.value,
            category: categoryMap[node.category] ?? 0,
            annotations: node.annotations || [],
            rca_role: role,
            symbolSize: size,
            label: {
              show: isAnnotated || node.category === 'process' || node.category === 'service',
              color: isAnnotated ? '#e4e7eb' : '#8b929a',
              fontWeight: isAnnotated ? 700 : 400,
            },
            itemStyle: {
              shadowBlur: isAnnotated ? 24 : 6,
              shadowColor: isAnnotated ? roleColor + '80' : catColor + '30',
              borderColor: isAnnotated ? roleColor : catColor + '80',
              borderWidth: isAnnotated ? 3 : 1.5,
            },
          }
        }),
        edges: data.edges.map((edge) => {
          const sourceNode = data.nodes.find((n) => n.id === edge.source)
          const targetNode = data.nodes.find((n) => n.id === edge.target)
          return {
            source: sourceNode?.name || edge.source,
            target: targetNode?.name || edge.target,
            relation: edge.relation,
            inferred: edge.inferred,
            annotations: edge.annotations || [],
            lineStyle: {
              color: edge.relation === 'evidence_link' ? '#d19a66' : '#3d4450',
              width: edge.relation === 'evidence_link' ? 2.5 : edge.relation === 'connects_to' ? 1 : 2,
              curveness: 0.2,
              opacity: edge.relation === 'evidence_link' ? 0.9 : 0.7,
              type: edge.inferred || edge.relation === 'connects_to' ? 'dashed' as const : 'solid' as const,
            },
          }
        }),
        force: {
          repulsion: 120,
          edgeLength: [40, 100],
          gravity: 0.15,
          friction: 0.5,
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
        <Space>
          <Title level={5} style={{ color: 'var(--text-primary)', margin: 0 }}>
            <ApartmentOutlined style={{ marginRight: 8, color: 'var(--accent-green)' }} />
            故障关联图谱
          </Title>
          <Tag>
            <NodeIndexOutlined style={{ marginRight: 4 }} />
            {data.nodes.length} 节点 / {data.edges.length} 关系
          </Tag>
          {activeSessionId && (
            <Tag color={(data.annotations?.length || 0) > 0 ? 'orange' : 'default'}>
              RCA {data.annotations?.length || 0}
            </Tag>
          )}
          <Tag color="red">Affected</Tag>
          <Tag color="orange">Suspected</Tag>
          <Tag color="gold">Impact</Tag>
          <Tag>Dashed=inferred</Tag>
        </Space>
        <Button icon={<ReloadOutlined />} onClick={fetchTopology} loading={loading}>
          刷新
        </Button>
      </div>

      {/* Graph */}
      <div style={{ flex: 1, padding: 8 }}>
        <ReactECharts
          option={option}
          style={{ height: '100%', width: '100%' }}
          opts={{ renderer: 'canvas' }}
        />
      </div>

      {/* Footer hint */}
      <div style={{ padding: '8px 24px', borderTop: '1px solid var(--border-color)' }}>
        <Text style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          拖拽节点调整布局 / 滚轮缩放 / 点击节点高亮关联
        </Text>
      </div>
    </div>
  )
}

export default TopologyGraph
