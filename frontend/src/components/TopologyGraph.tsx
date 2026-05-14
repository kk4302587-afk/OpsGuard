import { useEffect, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { Button, Spin, Empty, Typography, Space, Tag } from 'antd'
import { ApartmentOutlined, ReloadOutlined, NodeIndexOutlined } from '@ant-design/icons'

const { Title, Text } = Typography

interface TopologyNode {
  id: string
  name: string
  category: string
  value?: string
  highlight?: boolean
}

interface TopologyEdge {
  source: string
  target: string
  relation: string
}

interface TopologyData {
  nodes: TopologyNode[]
  edges: TopologyEdge[]
  categories: { name: string; itemStyle: { color: string } }[]
}

const categoryLabels: Record<string, string> = {
  process: '进程',
  port: '端口',
  service: '服务',
  config: '配置',
  remote: '远程连接',
  log: '日志',
}

const categoryColors: Record<string, string> = {
  process: '#60a5fa',
  port: '#fbbf24',
  service: '#34d399',
  config: '#a78bfa',
  remote: '#f87171',
  log: '#fb923c',
}

/**
 * Radial topology graph - center-outward layout like Obsidian.
 * Services at center, processes in middle ring, ports on outer ring.
 */
function TopologyGraph() {
  const [data, setData] = useState<TopologyData | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    fetchTopology()
  }, [])

  const fetchTopology = async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/topology/graph')
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

  // Assign radial positions: services center, processes middle, ports outer
  const categoryOrder: Record<string, number> = {
    service: 0,   // Center
    process: 1,   // Middle ring
    config: 1,
    remote: 2,    // Outer ring
    port: 2,
    log: 2,
  }

  const categoryMap: Record<string, number> = {}
  data.categories.forEach((cat, idx) => {
    categoryMap[cat.name] = idx
  })

  // Group nodes by ring
  const rings: Record<number, TopologyNode[]> = { 0: [], 1: [], 2: [] }
  data.nodes.forEach(node => {
    const ring = categoryOrder[node.category] ?? 1
    rings[ring].push(node)
  })

  // Calculate positions in concentric circles
  const positionedNodes = data.nodes.map(node => {
    const ring = categoryOrder[node.category] ?? 1
    const nodesInRing = rings[ring]
    const indexInRing = nodesInRing.indexOf(node)
    const totalInRing = nodesInRing.length

    // Radius for each ring
    const radius = ring === 0 ? 0 : ring === 1 ? 200 : 360
    const angle = (2 * Math.PI * indexInRing) / totalInRing - Math.PI / 2

    // Add slight randomness to prevent perfect circle (more organic)
    const jitter = ring === 0 ? 0 : (Math.random() - 0.5) * 30

    const x = radius === 0 ? 0 : Math.cos(angle) * (radius + jitter)
    const y = radius === 0 ? 0 : Math.sin(angle) * (radius + jitter)

    let size = 12
    if (node.category === 'service') size = 36
    else if (node.category === 'process') size = 18
    else if (node.category === 'config') size = 14
    else if (node.category === 'remote') size = 12
    else if (node.category === 'port') size = 6

    const color = categoryColors[node.category] || '#8b929e'
    const isHighlight = (node as any).highlight

    return {
      name: node.name,
      value: node.value,
      category: categoryMap[node.category] ?? 0,
      symbolSize: size,
      x,
      y,
      fixed: false,
      label: { show: false },
      itemStyle: {
        color: isHighlight ? '#f87171' : color,
        shadowBlur: isHighlight ? 20 : ring === 0 ? 12 : 4,
        shadowColor: isHighlight ? 'rgba(248,113,113,0.4)' : color + (ring === 0 ? '40' : '20'),
        borderColor: color + '60',
        borderWidth: ring === 0 ? 2 : node.category === 'port' ? 0 : 1,
        opacity: node.category === 'port' ? 0.6 : 0.9,
      },
    }
  })

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(14, 15, 18, 0.94)',
      borderColor: 'rgba(255,255,255,0.08)',
      borderRadius: 8,
      padding: [8, 12],
      textStyle: { color: '#d8dce2', fontSize: 12, fontFamily: "'JetBrains Mono', monospace" },
      formatter: (params: any) => {
        if (params.dataType === 'node') {
          const cat = data.categories[params.data.category]?.name || ''
          return `<div style="max-width:220px"><strong>${params.data.name}</strong><br/><span style="color:#8b929e">${categoryLabels[cat] || cat}</span>${params.data.value ? `<br/><span style="color:#34d399">${params.data.value}</span>` : ''}</div>`
        }
        if (params.dataType === 'edge') {
          return `<span style="color:#8b929e">${params.data.relation || ''}</span>`
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
        animationDuration: 2000,
        animationEasingUpdate: 'cubicInOut',
        label: { show: false },
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: [0, 6],
        categories: data.categories.map((c) => ({
          name: categoryLabels[c.name] || c.name,
          itemStyle: { color: c.itemStyle.color },
        })),
        data: positionedNodes,
        edges: data.edges.map((edge) => {
          const sourceNode = data.nodes.find((n) => n.id === edge.source)
          const targetNode = data.nodes.find((n) => n.id === edge.target)
          const isConnectsTo = edge.relation === 'connects_to'
          const sourceColor = categoryColors[sourceNode?.category || 'process'] || '#555'

          return {
            source: sourceNode?.name || edge.source,
            target: targetNode?.name || edge.target,
            relation: edge.relation,
            lineStyle: {
              color: sourceColor + '60',
              width: isConnectsTo ? 0.8 : 1.5,
              curveness: 0.12,
              type: isConnectsTo ? 'dashed' as const : 'solid' as const,
            },
          }
        }),
        force: {
          // Gentle force to maintain radial shape while allowing some movement
          repulsion: 80,
          edgeLength: [30, 80],
          gravity: 0.05,
          friction: 0.7,
          layoutAnimation: true,
        },
        emphasis: {
          focus: 'adjacency',
          label: {
            show: true,
            fontSize: 11,
            color: '#f0f2f5',
            fontFamily: "'JetBrains Mono', monospace",
            backgroundColor: 'rgba(14, 15, 18, 0.9)',
            borderRadius: 4,
            padding: [4, 8],
            borderColor: 'rgba(255,255,255,0.1)',
            borderWidth: 1,
          },
          lineStyle: {
            width: 2.5,
            opacity: 1,
          },
          itemStyle: {
            shadowBlur: 20,
            shadowColor: 'rgba(52, 211, 153, 0.35)',
            borderWidth: 2,
            borderColor: '#34d399',
          },
        },
        blur: {
          itemStyle: { opacity: 0.12 },
          lineStyle: { opacity: 0.04 },
        },
      },
    ],
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{ padding: 'var(--space-4) var(--space-5)', borderBottom: '1px solid var(--border-color)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Space>
          <Title level={5} style={{ color: 'var(--text-primary)', margin: 0 }}>
            <ApartmentOutlined style={{ marginRight: 8, color: 'var(--accent-green)' }} />
            故障关联图谱
          </Title>
          <Tag style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border-color)' }}>
            <NodeIndexOutlined style={{ marginRight: 4 }} />
            {data.nodes.length} 节点 / {data.edges.length} 关系
          </Tag>
        </Space>
        <Space>
          {Object.entries(categoryLabels).slice(0, 5).map(([key, label]) => (
            <span key={key} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--text-muted)' }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: categoryColors[key], display: 'inline-block' }} />
              {label}
            </span>
          ))}
          <Button size="small" icon={<ReloadOutlined />} onClick={fetchTopology}>
            刷新
          </Button>
        </Space>
      </div>

      {/* Graph */}
      <div style={{ flex: 1 }}>
        <ReactECharts
          option={option}
          style={{ height: '100%', width: '100%' }}
          opts={{ renderer: 'canvas' }}
        />
      </div>

      {/* Footer */}
      <div style={{ padding: '8px var(--space-5)', borderTop: '1px solid var(--border-color)' }}>
        <Text style={{ fontSize: 11, color: 'var(--text-dim)' }}>
          拖拽节点调整 / 滚轮缩放 / 悬浮查看详情 / 点击高亮关联
        </Text>
      </div>
    </div>
  )
}

export default TopologyGraph
