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

          return {
            name: node.name,
            value: node.value,
            category: categoryMap[node.category] ?? 0,
            symbolSize: size,
            label: {
              show: node.category === 'process' || node.category === 'service',
            },
            itemStyle: {
              shadowBlur: (node as any).highlight ? 24 : 6,
              shadowColor: (node as any).highlight ? '#e06c75' : catColor + '30',
              borderColor: (node as any).highlight ? '#e06c75' : catColor + '80',
              borderWidth: (node as any).highlight ? 3 : 1.5,
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
            lineStyle: {
              color: '#3d4450',
              width: edge.relation === 'connects_to' ? 1 : 2,
              curveness: 0.2,
              opacity: 0.7,
              type: edge.relation === 'connects_to' ? 'dashed' as const : 'solid' as const,
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
