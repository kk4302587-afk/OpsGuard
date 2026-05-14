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
}

/**
 * Fault correlation topology graph using ECharts.
 * Visualizes relationships between processes, ports, services, configs.
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

  const categoryMap: Record<string, number> = {}
  data.categories.forEach((cat, idx) => {
    categoryMap[cat.name] = idx
  })

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(20, 21, 24, 0.95)',
      borderColor: 'rgba(255,255,255,0.08)',
      borderRadius: 8,
      padding: [8, 12],
      textStyle: { color: '#d8dce2', fontSize: 12, fontFamily: "'JetBrains Mono', monospace" },
      formatter: (params: any) => {
        if (params.dataType === 'node') {
          const cat = data.categories[params.data.category]?.name || ''
          return `<strong>${params.data.name}</strong><br/><span style="color:#8b929e">${categoryLabels[cat] || cat}</span>${params.data.value ? `<br/><span style="color:#34d399">${params.data.value}</span>` : ''}`
        }
        if (params.dataType === 'edge') {
          return `<span style="color:#8b929e">${params.data.relation || ''}</span>`
        }
        return ''
      },
    },
    legend: {
      data: data.categories.map((c) => ({ name: categoryLabels[c.name] || c.name, icon: 'circle' })),
      textStyle: { color: '#8b929e', fontSize: 11 },
      top: 16,
      right: 16,
      orient: 'vertical',
      itemGap: 12,
    },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        animation: true,
        animationDuration: 1500,
        animationEasingUpdate: 'cubicInOut',
        // DEFAULT: hide all labels — only show on hover
        label: {
          show: false,
        },
        // Edge arrows to show direction (causality)
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: [0, 7],
        edgeLabel: {
          show: false,
        },
        categories: data.categories.map((c) => ({
          name: categoryLabels[c.name] || c.name,
          itemStyle: c.itemStyle,
        })),
        data: data.nodes.map((node) => {
          // Size hierarchy: service > process > config > remote > port
          let size = 14
          if (node.category === 'service') size = 48
          else if (node.category === 'process') size = 30
          else if (node.category === 'config') size = 22
          else if (node.category === 'remote') size = 18
          else if (node.category === 'port') size = 8  // Tiny dots

          const catColor = data.categories[categoryMap[node.category]]?.itemStyle.color || '#8b929e'
          const isHighlight = (node as any).highlight

          return {
            name: node.name,
            value: node.value,
            category: categoryMap[node.category] ?? 0,
            symbolSize: size,
            // No label by default
            label: { show: false },
            itemStyle: {
              shadowBlur: isHighlight ? 28 : 4,
              shadowColor: isHighlight ? '#f87171' : catColor + '20',
              borderColor: isHighlight ? '#f87171' : catColor + '60',
              borderWidth: isHighlight ? 3 : node.category === 'port' ? 0 : 1.5,
              opacity: node.category === 'port' ? 0.7 : 1,
            },
          }
        }),
        edges: data.edges.map((edge) => {
          const sourceNode = data.nodes.find((n) => n.id === edge.source)
          const targetNode = data.nodes.find((n) => n.id === edge.target)
          const isConnectsTo = edge.relation === 'connects_to'

          return {
            source: sourceNode?.name || edge.source,
            target: targetNode?.name || edge.target,
            relation: edge.relation,
            lineStyle: {
              color: 'source',  // Inherit color from source node
              width: isConnectsTo ? 1 : 1.8,
              curveness: 0.15,
              opacity: isConnectsTo ? 0.4 : 0.6,
              type: isConnectsTo ? 'dashed' as const : 'solid' as const,
            },
          }
        }),
        force: {
          repulsion: 250,       // Prevent overlap
          edgeLength: [60, 140], // Keep related nodes close
          gravity: 0.2,          // Pull toward center
          friction: 0.55,
          layoutAnimation: true,
        },
        // HOVER: show label with background, highlight adjacency
        emphasis: {
          focus: 'adjacency',
          label: {
            show: true,
            fontSize: 11,
            color: '#f0f2f5',
            fontFamily: "'JetBrains Mono', monospace",
            backgroundColor: 'rgba(20, 21, 24, 0.85)',
            borderRadius: 4,
            padding: [4, 8],
            borderColor: 'rgba(255,255,255,0.1)',
            borderWidth: 1,
          },
          lineStyle: {
            width: 3,
            opacity: 1,
          },
          itemStyle: {
            shadowBlur: 24,
            shadowColor: 'rgba(52, 211, 153, 0.3)',
            borderWidth: 2,
          },
        },
        blur: {
          itemStyle: { opacity: 0.15 },
          lineStyle: { opacity: 0.05 },
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
