import { Alert, Collapse, Modal, Typography, Tag, Space, Descriptions } from 'antd'
import {
  WarningOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { useChatStore } from '../stores/chatStore'
import type { OperationPreview } from '../stores/chatStore'
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
	    preview?: OperationPreview
	    policy?: Record<string, unknown>
    approval_level?: string
    execution_identity?: Record<string, unknown>
  } | null
  onClose: () => void
}

interface DiffLine {
  text: string
  kind: 'add' | 'remove' | 'meta' | 'context'
}

function previewLabel(strategy?: string) {
  const labels: Record<string, string> = {
    impact_only: '仅影响评估',
    check_mode: '检查模式',
    diff: '差异对比',
    dry_run: '预演执行',
    before_after: '前后状态',
    restore_preview: '恢复预览',
    command_dry_run: '命令干跑',
    sandbox_execution: '沙箱执行',
    tool_native_dry_run: '工具原生干跑',
    none: '无预览',
  }
  return labels[strategy || 'none'] || strategy || '无预览'
}

function previewStatusLabel(status?: string) {
  const labels: Record<string, string> = {
    available: '可用',
    partial: '部分可用',
    unavailable: '不可用',
  }
  return labels[status || ''] || status || '未知'
}

function previewStatusColor(status?: string) {
  if (status === 'available') return 'green'
  if (status === 'partial') return 'gold'
  if (status === 'unavailable') return 'red'
  return 'default'
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

function textArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : []
}

function renderPreviewMetadata(preview?: OperationPreview): string {
  if (!preview?.metadata) return ''
  const metadata = preview.metadata
  if (Array.isArray(metadata.planned_commands)) {
    return metadata.planned_commands.map((item) => `$ ${item}`).join('\n')
  }
  const stdout = typeof metadata.stdout === 'string' ? metadata.stdout : ''
  const stderr = typeof metadata.stderr === 'string' ? metadata.stderr : ''
  const command = Array.isArray(metadata.command) ? `$ ${metadata.command.join(' ')}` : ''
  return [command, stdout, stderr ? `stderr:\n${stderr}` : ''].filter(Boolean).join('\n\n')
}

function metadataValue(preview: OperationPreview | undefined, key: string): unknown {
  return preview?.metadata?.[key]
}

function compactPath(path: unknown): string {
  const text = typeof path === 'string' ? path : ''
  if (!text) return '-'
  if (text.length <= 54) return text
  const parts = text.split('/')
  return `${parts.slice(0, 2).join('/')}/.../${parts.slice(-2).join('/')}`
}

function formatBytes(value: unknown): string {
  if (typeof value !== 'number') return '-'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function diffLines(diff?: string): DiffLine[] {
  return (diff || '')
    .split('\n')
    .filter((line) => line.trim() !== '')
    .map((line) => {
      if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@')) {
        return { text: line, kind: 'meta' as const }
      }
      if (line.startsWith('+')) return { text: line, kind: 'add' as const }
      if (line.startsWith('-')) return { text: line, kind: 'remove' as const }
      return { text: line, kind: 'context' as const }
    })
}

function lineClassName(kind: DiffLine['kind']) {
  return `approval-preview-diff-line approval-preview-diff-line-${kind}`
}

function previewTitle(preview?: OperationPreview): string {
  const operation = metadataValue(preview, 'operation')
  if (typeof operation === 'string' && operation) return operation
  return previewLabel(preview?.preview_type)
}

function previewSummaryItems(preview?: OperationPreview): Array<{ label: string; value: string; title?: string }> {
  if (!preview) return []
  const path = metadataValue(preview, 'path')
  if (preview.preview_type === 'restore_preview') {
    const backupId = metadataValue(preview, 'backup_id')
    const currentExists = metadataValue(preview, 'current_exists')
    const willOverwrite = metadataValue(preview, 'will_overwrite')
    return [
      { label: '备份 ID', value: textValue(backupId) },
      { label: '恢复目标', value: compactPath(path), title: typeof path === 'string' ? path : undefined },
      { label: '备份大小', value: formatBytes(metadataValue(preview, 'planned_bytes')) },
      {
        label: '覆盖影响',
        value: willOverwrite ? '会覆盖当前目标' : currentExists === false ? '当前目标不存在' : '不会覆盖',
      },
    ]
  }
  return [
    { label: '目标', value: compactPath(path), title: typeof path === 'string' ? path : undefined },
    { label: '当前大小', value: formatBytes(metadataValue(preview, 'current_bytes')) },
    { label: '计划大小', value: formatBytes(metadataValue(preview, 'planned_bytes')) },
    { label: '变化', value: formatBytes(metadataValue(preview, 'content_bytes')) },
  ]
}

function cleanedImpactText(impact?: string, hasPreview?: boolean): string {
  const text = impact || ''
  if (!hasPreview) return text
  return text
    .split('\n')
    .filter((line) => !line.trim().startsWith('预览：'))
    .join('\n')
    .trim()
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
	  const preview = request.preview
	  const previewWarnings = textArray(preview?.warnings)
	  const previewLimitations = textArray(preview?.limitations)
	  const previewMetadata = renderPreviewMetadata(preview)
	  const impactText = cleanedImpactText(request.impact, Boolean(preview))
	  const addedContent = typeof metadataValue(preview, 'added_content') === 'string'
	    ? String(metadataValue(preview, 'added_content'))
	    : ''
	  const proposedContent = typeof metadataValue(preview, 'proposed_content') === 'string'
	    ? String(metadataValue(preview, 'proposed_content'))
	    : ''
	  const renderedDiffLines = diffLines(preview?.diff)
	  const summaryItems = previewSummaryItems(preview)

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
	              <Text>预览：{previewLabel(preview?.preview_type || request.preview_strategy)}</Text>
	              <Text>
                回滚：{request.supports_rollback
                  ? `${rollbackLabel(request.rollback_strategy)}可用`
                  : '无可靠自动回滚'}
              </Text>
	            </Space>
	          </Descriptions.Item>
	          {preview && (
	            <Descriptions.Item label="审批前预览">
	              <Space direction="vertical" size={10} style={{ width: '100%' }}>
	                <div className="approval-preview-card">
	                  <div className="approval-preview-head">
	                    <Space wrap size={6}>
	                      <Tag color={previewStatusColor(preview.status)}>
	                        {previewStatusLabel(preview.status)}
	                      </Tag>
	                      <Tag color="blue">{previewTitle(preview)}</Tag>
	                      {preview.target && <Tag color="default">{preview.target}</Tag>}
	                    </Space>
	                  </div>
	                  <div className="approval-preview-grid">
	                    {summaryItems.map((item) => (
	                      <div key={item.label}>
	                        <Text type="secondary">{item.label}</Text>
	                        <div className="approval-preview-value" title={item.title}>
	                          {item.value}
	                        </div>
	                      </div>
	                    ))}
	                  </div>
	                </div>
	                {addedContent && (
	                  <div className="approval-preview-section">
	                    <Text type="secondary">将追加</Text>
	                    <pre className="approval-preview-snippet approval-preview-snippet-add">
	                      {addedContent}
	                    </pre>
	                  </div>
	                )}
	                {!addedContent && proposedContent && (
	                  <div className="approval-preview-section">
	                    <Text type="secondary">计划内容</Text>
	                    <pre className="approval-preview-snippet">
	                      {proposedContent}
	                    </pre>
	                  </div>
	                )}
	                {previewWarnings.length > 0 && (
	                  <Alert
	                    type="warning"
	                    showIcon
	                    message={previewWarnings.slice(0, 3).join('；')}
	                  />
	                )}
	                {previewLimitations.length > 0 && (
	                  <Alert
	                    type={preview.status === 'unavailable' ? 'error' : 'info'}
	                    showIcon
	                    message={previewLimitations.slice(0, 3).join('；')}
	                  />
	                )}
	                {renderedDiffLines.length > 0 && (
	                  <Collapse
	                    size="small"
	                    ghost
	                    className="approval-preview-collapse"
	                    items={[{
	                      key: 'diff',
	                      label: '查看完整差异',
	                      children: (
	                        <div className="approval-preview-diff">
	                          {renderedDiffLines.map((line, index) => (
	                            <div className={lineClassName(line.kind)} key={`${line.kind}-${index}`}>
	                              {line.text}
	                            </div>
	                          ))}
	                        </div>
	                      ),
	                    }]}
	                  />
	                )}
	                {!preview.diff && previewMetadata && (
	                  <pre className="approval-preview-snippet">
	                    {previewMetadata}
	                  </pre>
	                )}
	              </Space>
	            </Descriptions.Item>
	          )}
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

	        {impactText && (
	          <div style={{ marginTop: 12, padding: '8px 12px', background: 'rgba(97, 175, 239, 0.08)', borderRadius: 4, border: '1px solid rgba(97, 175, 239, 0.2)' }}>
	            <Text style={{ fontSize: 12, color: 'var(--accent-blue)', whiteSpace: 'pre-line' }}>
	              {impactText}
	            </Text>
	          </div>
	        )}
      </div>
    </Modal>
  )
}

export default ApprovalModal
