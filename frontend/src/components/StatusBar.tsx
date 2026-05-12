import { useEffect } from 'react'
import { Space, Tag, Typography } from 'antd'
import {
  DashboardOutlined,
  HddOutlined,
  CloudServerOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { useSystemStore } from '../stores/systemStore'

const { Text } = Typography

/**
 * Top status bar showing real-time system metrics.
 * Provides at-a-glance system health awareness.
 */
function StatusBar() {
  const { status, fetchStatus } = useSystemStore()

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
        <Space size={4}>
          <DashboardOutlined style={{ color: getStatusColor(status?.cpu?.percent || 0) }} />
          <Text style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
            CPU {status?.cpu?.percent || 0}%
          </Text>
        </Space>

        <Space size={4}>
          <CloudServerOutlined style={{ color: getStatusColor(status?.memory?.percent || 0) }} />
          <Text style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
            MEM {status?.memory?.percent || 0}%
          </Text>
        </Space>

        <Space size={4}>
          <HddOutlined style={{ color: getStatusColor(status?.disk?.percent || 0) }} />
          <Text style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
            DISK {status?.disk?.percent || 0}%
          </Text>
        </Space>
      </Space>
    </div>
  )
}

export default StatusBar
