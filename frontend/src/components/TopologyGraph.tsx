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
      backgroundColor: '#21252b',
      borderColor: '#2d3139',
      textStyle: { color: '#e4e7eb', fontSize: 12, fontFamily: "'JetBrains Mono', monospace" },
      formatter: (params: any) => {
        if (params.dataType === 'node') {
          const cat = data.categories[params.data.category]?.name || ''
          return `<strong>${params.data.name}</strong><br/><span style="color:#8b929a">${categoryLabels[cat] || cat}</span>${params.data.value ? `<br/>${params.data.value}` : ''}`
        }
        if (params.dataType === 'edge') {
          return `<span style="color:#8b929a">${params.data.relation || ''}</span>`
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
        animationDuration: 1000,
        label: {
          show: true,
          position: 'bottom',
          color: '#8b929a',
          fontSize: 10,
          fontFamily: "'JetBrains Mono', monospace",
          formatter: (params: any) => {
            const name = params.data.name as string
            return name.length > 20 ? name.slice(0, 20) + '...' : name
          },
        },
        categories: data.categories.map((c) => ({
          name: categoryLabels[c.name] || c.name,
          itemStyle: c.itemStyle,
        })),
        data: data.nodes.map((node) => ({
          name: node.name,
          value: node.value,
          category: categoryMap[node.category] ?? 0,
          symbolSize: node.category === 'service' ? 45 : node.category === 'process' ? 35 : node.category === 'remote' ? 28 : 22,
          itemStyle: {
            shadowBlur: (node as any).highlight ? 20 : 8,
            shadowColor: (node as any).highlight ? '#e06c75' : (data.categories[categoryMap[node.category]]?.itemStyle.color + '40'),
            borderColor: (node as any).highlight ? '#e06c75' : undefined,
            borderWidth: (node as any).highlight ? 3 : 0,
          },
        })),
        edges: data.edges.map((edge) => {
          const sourceNode = data.nodes.find((n) => n.id === edge.source)
          const targetNode = data.nodes.find((n) => n.id === edge.target)
          return {
            source: sourceNode?.name || edge.source,
            target: targetNode?.name || edge.target,
            relation: edge.relation,
            lineStyle: {
              color: '#2d3139',
              curveness: 0.15,
              width: 1.5,
            },
            label: {
              show: false,
            },
          }
        }),
        force: {
          repulsion: 300,
          edgeLength: [100, 200],
          gravity: 0.08,
          friction: 0.6,
        },
        emphasis: {
          focus: 'adjacency',
          lineStyle: { width: 3, color: '#00d4aa' },
          itemStyle: { shadowBlur: 16 },
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
