import { Steps, Typography } from 'antd'
import {
  SearchOutlined,
  SafetyOutlined,
  ToolOutlined,
  CheckCircleOutlined,
  LoadingOutlined,
  BulbOutlined,
} from '@ant-design/icons'

const { Text } = Typography

interface DiagnosisStep {
  title: string
  status: 'wait' | 'process' | 'finish' | 'error'
  description?: string
}

interface DiagnosisProgressProps {
  steps: DiagnosisStep[]
  visible: boolean
}

/**
 * Diagnosis progress indicator shown inside the chat bubble during analysis.
 */
function DiagnosisProgress({ steps, visible }: DiagnosisProgressProps) {
  if (!visible || steps.length === 0) return null

  const getIcon = (index: number, status: string) => {
    const size = 14
    if (status === 'process') return <LoadingOutlined style={{ fontSize: size, color: 'var(--accent-green)' }} />
    if (status === 'finish') return <CheckCircleOutlined style={{ fontSize: size, color: 'var(--accent-green)' }} />
    if (status === 'error') return <SafetyOutlined style={{ fontSize: size, color: 'var(--accent-red)' }} />

    const icons = [
      <SafetyOutlined style={{ fontSize: size }} />,
      <SearchOutlined style={{ fontSize: size }} />,
      <BulbOutlined style={{ fontSize: size }} />,
      <ToolOutlined style={{ fontSize: size }} />,
      <CheckCircleOutlined style={{ fontSize: size }} />,
    ]
    return icons[index] || undefined
  }

  return (
    <div style={{ padding: '4px 0' }}>
      <Steps
        size="small"
        direction="vertical"
        current={steps.findIndex(s => s.status === 'process')}
        items={steps.map((step, idx) => ({
          title: (
            <Text style={{
              fontSize: 12,
              color: step.status === 'finish' ? 'var(--accent-green)' :
                     step.status === 'error' ? 'var(--accent-red)' :
                     step.status === 'process' ? 'var(--text-primary)' : 'var(--text-muted)',
            }}>
              {step.title}
            </Text>
          ),
          description: step.description ? (
            <Text style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              {step.description.length > 40 ? step.description.slice(0, 40) + '...' : step.description}
            </Text>
          ) : undefined,
          status: step.status,
          icon: getIcon(idx, step.status),
        }))}
      />
    </div>
  )
}

export default DiagnosisProgress
