import { Collapse, Modal, Typography, Tag, Space, Descriptions } from 'antd'
import {
  WarningOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { useChatStore } from '../stores/chatStore'
import { summarizeOperation } from '../utils/operationSummary'

const { Text, Paragraph } = Typography

interface ApprovalModalProps {
  visible: boolean
  request: {
    request_id: string
    command: string
    risk_level: string
    description: string
    impact?: string
    rollback_strategy?: string
    supports_rollback?: boolean
    preview_strategy?: string
    policy?: Record<string, unknown>
    approval_level?: string
    execution_identity?: Record<string, unknown>
  } | null
  onClose: () => void
}

function previewLabel(strategy?: string) {
  const labels: Record<string, string> = {
    impact_only: '仅影响评估',
    check_mode: '检查模式',
    diff: '差异对比',
    dry_run: '预演执行',
    none: '无预览',
  }
  return labels[strategy || 'none'] || strategy || '无预览'
}

function rollbackLabel(strategy?: string) {
  const labels: Record<string, string> = {
    backup: '备份回滚',
    manual: '手动回滚',
    inverse_action: '反向操作',
    none: '无可靠自动回滚',
  }
  return labels[strategy || 'none'] || strategy || '无可靠自动回滚'
}

function riskLabel(level: string) {
  if (level === 'destructive') return '高危操作'
  if (level === 'write') return '写操作'
  return '需要确认'
}

function approvalLevelLabel(level?: string) {
  const labels: Record<string, string> = {
    none: '无需审批',
    standard: '标准审批',
    explicit: '显式审批',
    destructive: '高危审批',
  }
  return labels[level || 'standard'] || level || '标准审批'
}

function policyActionLabel(allowed: boolean) {
  return allowed ? '允许' : '拒绝'
}

function textList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => String(item)).filter(Boolean)
    : []
}

function textValue(value: unknown, fallback = '-'): string {
  if (typeof value === 'string' && value.trim()) return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return fallback
}

/**
 * Modal for approving or rejecting high-risk operations.
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
  const summary = summarizeOperation(request.command)
  const policy = request.policy || {}
  const identity = request.execution_identity || policy.execution_identity as Record<string, unknown> | undefined || {}
  const matchedRules = textList(policy.matched_rules)
  const policyWarnings = textList(policy.warnings)
  const policyReasons = textList(policy.reasons)
  const policyAllowed = policy.allowed !== false

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
        <div className="approval-modal-summary">
          <div>
            <Text style={{ color: 'var(--text-muted)', fontSize: 12 }}>准备执行</Text>
            <div className="approval-modal-title">{summary.title}</div>
          </div>
          <Tag color={getRiskColor(request.risk_level)} icon={getRiskIcon(request.risk_level)}>
            {riskLabel(request.risk_level)}
          </Tag>
        </div>

        <Descriptions column={1} size="small" bordered>
          {summary.target && (
            <Descriptions.Item label="目标">
              <Text>{summary.target}</Text>
            </Descriptions.Item>
          )}
          {summary.detail && (
            <Descriptions.Item label="变更内容">
              <Text>{summary.detail}</Text>
            </Descriptions.Item>
          )}
          <Descriptions.Item label="风险等级">
            <Tag color={getRiskColor(request.risk_level)} icon={getRiskIcon(request.risk_level)}>
              {riskLabel(request.risk_level)}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="操作描述">
            <Text>{request.description}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="预览/回滚">
            <Space direction="vertical" size={2}>
              <Text>预览：{previewLabel(request.preview_strategy)}</Text>
              <Text>
                回滚：{request.supports_rollback
                  ? `${rollbackLabel(request.rollback_strategy)}可用`
                  : '无可靠自动回滚'}
              </Text>
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="策略">
            <Space direction="vertical" size={2}>
              <Space wrap>
                <Tag color={policyAllowed ? 'green' : 'red'}>
                  {policyActionLabel(policyAllowed)}
                </Tag>
                <Tag color={request.approval_level === 'destructive' ? 'red' : 'blue'}>
                  审批级别：{approvalLevelLabel(request.approval_level)}
                </Tag>
                <Tag color="default">
                  影响上限：{textValue(policy.max_blast_radius, '1')}
                </Tag>
              </Space>
              {matchedRules.length > 0 && (
                <Text>命中规则：{matchedRules.join('、')}</Text>
              )}
              {policyReasons.length > 0 && (
                <Text type="danger">阻断原因：{policyReasons.join('；')}</Text>
              )}
              {policyWarnings.length > 0 && (
                <Text type="secondary">策略提示：{policyWarnings.slice(0, 2).join('；')}</Text>
              )}
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="执行身份">
            <Space direction="vertical" size={2}>
              <Text>用户：{textValue(identity.run_as_user, 'current process')}</Text>
              <Text>
                sudo：{identity.uses_sudo ? `需要 (${textValue(identity.sudo_command)})` : '不需要'}
              </Text>
            </Space>
          </Descriptions.Item>
        </Descriptions>

        <Collapse
          ghost
          size="small"
          style={{ marginTop: 10 }}
          items={[{
            key: 'raw-command',
            label: '查看原始命令',
            children: (
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
            ),
          }]}
        />

        <div style={{ marginTop: 16, padding: '8px 12px', background: 'rgba(229, 192, 123, 0.1)', borderRadius: 4, border: '1px solid rgba(229, 192, 123, 0.3)' }}>
          <Text style={{ fontSize: 12, color: 'var(--accent-yellow)' }}>
            <WarningOutlined style={{ marginRight: 6 }} />
            请确认此操作是否安全。批准后会立即执行；只有弹窗明确显示回滚可用的操作，才会尝试创建可恢复的备份点。
          </Text>
        </div>

        {request.impact && (
          <div style={{ marginTop: 12, padding: '8px 12px', background: 'rgba(97, 175, 239, 0.08)', borderRadius: 4, border: '1px solid rgba(97, 175, 239, 0.2)' }}>
            <Text style={{ fontSize: 12, color: 'var(--accent-blue)', whiteSpace: 'pre-line' }}>
              {request.impact}
            </Text>
          </div>
        )}
      </div>
    </Modal>
  )
}

export default ApprovalModal
