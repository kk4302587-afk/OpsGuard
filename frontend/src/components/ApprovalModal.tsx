import { Modal, Typography, Tag, Space, Descriptions } from 'antd'
import {
  WarningOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { useChatStore } from '../stores/chatStore'

const { Text, Paragraph } = Typography

interface ApprovalModalProps {
  visible: boolean
  request: {
    request_id: string
    command: string
    risk_level: string
    description: string
    impact?: string
  } | null
  onClose: () => void
}

/**
 * Modal for approving/rejecting high-risk operations.
 * Shows command details, risk level, and description.
 */
function ApprovalModal({ visible, request, onClose }: ApprovalModalProps) {
  const { ws } = useChatStore()

  if (!request) return null

  const handleApprove = () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: 'approve',
        request_id: request.request_id,
        approved: true,
      }))
    }
    onClose()
  }

  const handleReject = () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: 'approve',
        request_id: request.request_id,
        approved: false,
      }))
    }
    onClose()
  }

  const getRiskColor = (level: string) => {
    if (level === 'destructive') return 'red'
    if (level === 'write') return 'orange'
    return 'default'
  }

  const getRiskIcon = (level: string) => {
    if (level === 'destructive') return <ThunderboltOutlined />
    return <WarningOutlined />
  }

  return (
    <Modal
      title={
        <Space>
          <WarningOutlined style={{ color: 'var(--accent-yellow)' }} />
          <span>操作审批</span>
        </Space>
      }
      open={visible}
      onCancel={handleReject}
      okText={
        <Space>
          <CheckCircleOutlined />
          批准执行
        </Space>
      }
      cancelText={
        <Space>
          <CloseCircleOutlined />
          拒绝
        </Space>
      }
      onOk={handleApprove}
      okButtonProps={{ danger: request.risk_level === 'destructive' }}
      width={560}
      centered
    >
      <div style={{ padding: '12px 0' }}>
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="风险等级">
            <Tag color={getRiskColor(request.risk_level)} icon={getRiskIcon(request.risk_level)}>
              {request.risk_level === 'destructive' ? '高危' : '写操作'}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="操作描述">
            <Text>{request.description}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="执行命令">
            <Paragraph
              code
              copyable
              style={{
                margin: 0,
                fontFamily: 'var(--font-mono)',
                fontSize: 12,
                background: 'var(--bg-primary)',
                padding: '8px 12px',
                borderRadius: 4,
              }}
            >
              {request.command}
            </Paragraph>
          </Descriptions.Item>
        </Descriptions>

        <div style={{ marginTop: 16, padding: '8px 12px', background: 'rgba(229, 192, 123, 0.1)', borderRadius: 4, border: '1px solid rgba(229, 192, 123, 0.3)' }}>
          <Text style={{ fontSize: 12, color: 'var(--accent-yellow)' }}>
            <WarningOutlined style={{ marginRight: 6 }} />
            请确认此操作是否安全。批准后将立即执行，操作前已自动备份相关文件。
          </Text>
        </div>

        {request.impact && (
          <div style={{ marginTop: 12, padding: '8px 12px', background: 'rgba(97, 175, 239, 0.08)', borderRadius: 4, border: '1px solid rgba(97, 175, 239, 0.2)' }}>
            <Text style={{ fontSize: 12, color: 'var(--accent-blue)', whiteSpace: 'pre-line' }}>
              影响评估: {request.impact}
            </Text>
          </div>
        )}
      </div>
    </Modal>
  )
}

export default ApprovalModal
