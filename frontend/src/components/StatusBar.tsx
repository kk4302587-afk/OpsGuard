import { useEffect } from 'react'
import { Button, Space, Tag, Tooltip, Typography } from 'antd'
import {
  DashboardOutlined,
  HddOutlined,
  CloudServerOutlined,
  SafetyCertificateOutlined,
  ApartmentOutlined,
} from '@ant-design/icons'
import { useSystemStore } from '../stores/systemStore'
import { useChatStore } from '../stores/chatStore'

const { Text } = Typography

/**
 * Top status bar showing real-time system metrics.
 * Provides at-a-glance system health awareness.
 */
function StatusBar() {
  const { status, fetchStatus } = useSystemStore()
  const activeSessionId = useChatStore((state) => state.activeSessionId)

  useEffect(() => {
    fetchStatus()
    const interval = setInterval(fetchStatus, 5000) // Refresh every 5s
    return () => clearInterval(interval)
  }, [fetchStatus])

  const getStatusColor = (percent: number) => {
    if (percent > 90) return 'var(--accent-red)'
    if (percent > 70) return 'var(--accent-yellow)'
    return 'var(--accent-green)'
  }

  const openTopology = () => {
    window.dispatchEvent(new CustomEvent('opsguard:navigate', { detail: 'topology' }))
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', width: '100%', justifyContent: 'space-between' }}>
      <Space size="middle">
        <Text strong style={{ color: 'var(--accent-green)', fontFamily: 'var(--font-mono)', fontSize: 14 }}>
          <SafetyCertificateOutlined style={{ marginRight: 6 }} />
          OpsGuard
        </Text>
        <Tag color="green" icon={<SafetyCertificateOutlined />}>
          安全模式: 三层防御
        </Tag>
      </Space>

      <Space size="large">
        <Tooltip title={activeSessionId ? '查看当前会话的故障关联图谱' : '查看系统故障关联图谱'}>
          <Button
            size="small"
            icon={<ApartmentOutlined />}
            onClick={openTopology}
            className="topology-header-button"
          >
            故障图谱
          </Button>
        </Tooltip>
        <Space size={4}>
          <DashboardOutlined style={{ color: getStatusColor(status?.cpu?.percent || 0) }} />
          <Text style={{ fontFamily: 'var(--font-mono)', fontSize: 14 }}>
            CPU {status?.cpu?.percent || 0}%
          </Text>
        </Space>

        <Space size={4}>
          <CloudServerOutlined style={{ color: getStatusColor(status?.memory?.percent || 0) }} />
          <Text style={{ fontFamily: 'var(--font-mono)', fontSize: 14 }}>
            MEM {status?.memory?.percent || 0}%
          </Text>
        </Space>

        <Space size={4}>
          <HddOutlined style={{ color: getStatusColor(status?.disk?.percent || 0) }} />
          <Text style={{ fontFamily: 'var(--font-mono)', fontSize: 14 }}>
            DISK {status?.disk?.percent || 0}%
          </Text>
        </Space>
      </Space>
    </div>
  )
}

export default StatusBar
