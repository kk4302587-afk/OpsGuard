import { useEffect, useState, useRef, useCallback } from 'react'
import ForceGraph3D from 'react-force-graph-3d'
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
 * 3D Fault correlation topology graph.
 * Uses react-force-graph-3d for Obsidian-like 3D visualization.
 */
function TopologyGraph() {
  const [data, setData] = useState<TopologyData | null>(null)
  const [loading, setLoading] = useState(false)
  const graphRef = useRef<any>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 })

  useEffect(() => {
    fetchTopology()
  }, [])

  useEffect(() => {
    const updateDimensions = () => {
      if (containerRef.current) {
        setDimensions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        })
      }
    }
    updateDimensions()
    window.addEventListener('resize', updateDimensions)
    return () => window.removeEventListener('resize', updateDimensions)
  }, [data])

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

  const getNodeSize = (category: string) => {
    switch (category) {
      case 'service': return 8
      case 'process': return 5
      case 'config': return 4
      case 'remote': return 3.5
      case 'port': return 2
      default: return 3
    }
  }

  const getNodeColor = (node: TopologyNode) => {
    if (node.highlight) return '#f87171'
    return categoryColors[node.category] || '#8b929e'
  }

  const getLinkColor = (link: any) => {
    const sourceNode = data?.nodes.find(n => n.id === link.source?.id || n.id === link.source)
    if (sourceNode) {
      const color = categoryColors[sourceNode.category] || '#555'
      return color + '80' // 50% opacity
    }
    return '#ffffff20'
  }

  const handleNodeClick = useCallback((node: any) => {
    if (graphRef.current) {
      const distance = 120
      const distRatio = 1 + distance / Math.hypot(node.x, node.y, node.z)
      graphRef.current.cameraPosition(
        { x: node.x * distRatio, y: node.y * distRatio, z: node.z * distRatio },
        node,
        1000,
      )
    }
  }, [])

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

  // Transform data for react-force-graph-3d
  const graphData = {
    nodes: data.nodes.map(n => ({ ...n, val: getNodeSize(n.category) })),
    links: data.edges.map(e => ({ source: e.source, target: e.target, relation: e.relation })),
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: 'var(--bg-base)' }}>
      {/* Header */}
      <div style={{ padding: 'var(--space-4) var(--space-5)', borderBottom: '1px solid var(--border-color)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', zIndex: 10 }}>
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
          {/* Legend */}
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

      {/* 3D Graph */}
      <div ref={containerRef} style={{ flex: 1, position: 'relative' }}>
        <ForceGraph3D
          ref={graphRef}
          graphData={graphData}
          width={dimensions.width}
          height={dimensions.height}
          backgroundColor="#09090b"
          nodeVal="val"
          nodeColor={(node: any) => getNodeColor(node)}
          nodeOpacity={0.9}
          nodeResolution={16}
          linkColor={getLinkColor}
          linkWidth={1}
          linkOpacity={0.5}
          linkDirectionalArrowLength={4}
          linkDirectionalArrowRelPos={0.9}
          linkDirectionalArrowColor={getLinkColor}
          linkCurvature={0.1}
          onNodeClick={handleNodeClick}
          nodeLabel={(node: any) => `
            <div style="background:rgba(20,21,24,0.92);padding:6px 10px;border-radius:6px;border:1px solid rgba(255,255,255,0.08);font-family:JetBrains Mono,monospace;font-size:11px;color:#d8dce2;max-width:240px">
              <strong>${node.name}</strong><br/>
              <span style="color:#8b929e">${categoryLabels[node.category] || node.category}</span>
              ${node.value ? `<br/><span style="color:#34d399">${node.value}</span>` : ''}
            </div>
          `}
          linkLabel={(link: any) => `<span style="background:rgba(20,21,24,0.9);padding:2px 6px;border-radius:3px;font-size:10px;color:#8b929e">${link.relation}</span>`}
          d3AlphaDecay={0.02}
          d3VelocityDecay={0.3}
          warmupTicks={50}
          cooldownTicks={100}
        />

        {/* Bottom hint */}
        <div style={{ position: 'absolute', bottom: 12, left: 16, zIndex: 10 }}>
          <Text style={{ fontSize: 11, color: 'var(--text-dim)' }}>
            拖拽旋转 / 滚轮缩放 / 点击节点聚焦
          </Text>
        </div>
      </div>
    </div>
  )
}

export default TopologyGraph
