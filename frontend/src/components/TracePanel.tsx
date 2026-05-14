import { Typography, Tag, Empty, Button } from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  SafetyOutlined,
  SearchOutlined,
  ToolOutlined,
  BulbOutlined,
  ApartmentOutlined,
  StopOutlined,
  DatabaseOutlined,
  MessageOutlined,
} from '@ant-design/icons'
import { useChatStore } from '../stores/chatStore'

const { Text, Title } = Typography

/**
 * Right panel showing the reasoning trace (ThoughtChain).
 * Visualizes: input → safety check → plan → tool calls → verify → respond
 */
function TracePanel() {
  const { traceEvents, sendMessage } = useChatStore()

  const getPhaseIcon = (phase: string, eventType: string) => {
    const iconStyle = { fontSize: 14 }

    if (eventType === 'blocked') return <StopOutlined style={{ ...iconStyle, color: 'var(--accent-red)' }} />
    if (eventType === 'failure') return <CloseCircleOutlined style={{ ...iconStyle, color: 'var(--accent-red)' }} />

    switch (phase) {
      case 'safety_check': return <SafetyOutlined style={{ ...iconStyle, color: 'var(--accent-green)' }} />
      case 'knowledge_retrieval': return <SearchOutlined style={{ ...iconStyle, color: 'var(--accent-blue)' }} />
      case 'planning': return <BulbOutlined style={{ ...iconStyle, color: 'var(--accent-yellow)' }} />
      case 'tool_call': return <ToolOutlined style={{ ...iconStyle, color: 'var(--accent-purple)' }} />
      case 'execution': return <LoadingOutlined style={{ ...iconStyle, color: 'var(--accent-blue)' }} />
      case 'approval_request': return <SafetyOutlined style={{ ...iconStyle, color: 'var(--accent-yellow)' }} />
      case 'approval_response': return <CheckCircleOutlined style={{ ...iconStyle, color: 'var(--accent-green)' }} />
      case 'verification': return <CheckCircleOutlined style={{ ...iconStyle, color: 'var(--accent-green)' }} />
      case 'response': return <MessageOutlined style={{ ...iconStyle, color: 'var(--accent-green)' }} />
      case 'knowledge_save': return <DatabaseOutlined style={{ ...iconStyle, color: 'var(--accent-blue)' }} />
      default: return <CheckCircleOutlined style={iconStyle} />
    }
  }

  const getEventColor = (eventType: string): string => {
    switch (eventType) {
      case 'success': return 'green'
      case 'failure': return 'red'
      case 'blocked': return 'red'
      case 'pending': return 'orange'
      case 'start': return 'blue'
      default: return 'default'
    }
  }

  const getPhaseLabel = (phase: string) => {
    const labels: Record<string, string> = {
      input_received: '接收指令',
      safety_check: '安全校验',
      knowledge_retrieval: '知识检索',
      planning: '推理规划',
      tool_call: '工具调用',
      approval_request: '审批请求',
      approval_response: '审批响应',
      execution: '执行操作',
      verification: '结果验证',
      response: '生成回复',
      error: '错误',
      knowledge_save: '知识沉淀',
    }
    return labels[phase] || phase
  }

  return (
    <div style={{ padding: 16, height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Title level={5} style={{ color: 'var(--text-primary)', marginBottom: 16, fontSize: 14 }}>
        <ApartmentOutlined style={{ marginRight: 8, color: 'var(--accent-green)' }} />
        推理链路
      </Title>

      {traceEvents.length === 0 ? (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Empty
            description={<Text style={{ color: 'var(--text-muted)', fontSize: 12 }}>发送消息后查看推理过程</Text>}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        </div>
      ) : (
        <div style={{ flex: 1, overflow: 'auto' }}>
          {traceEvents.map((event, index) => (
            <div
              key={index}
              style={{
                display: 'flex',
                gap: 10,
                marginBottom: 12,
                paddingBottom: 12,
                borderBottom: index < traceEvents.length - 1 ? '1px solid var(--border-color)' : 'none',
              }}
            >
              {/* Icon */}
              <div style={{ paddingTop: 2 }}>
                {getPhaseIcon(event.phase, event.event_type)}
              </div>

              {/* Content */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  <Tag
                    color={getEventColor(event.event_type)}
                    style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px', margin: 0 }}
                  >
                    {getPhaseLabel(event.phase)}
                  </Tag>
                  <Text style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                    {new Date(event.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                  </Text>
                </div>
                <Text
                  style={{
                    fontSize: 11,
                    color: event.event_type === 'blocked' ? 'var(--accent-red)' : 'var(--text-secondary)',
                    fontFamily: 'var(--font-mono)',
                    wordBreak: 'break-all',
                  }}
                >
                  {event.content}
                </Text>
                {event.phase === 'tool_call' && event.content.includes('调用工具') && (
                  <Button
                    size="small"
                    type="link"
                    icon={<BulbOutlined />}
                    style={{ padding: 0, marginTop: 4, fontSize: 11, height: 'auto' }}
                    onClick={() => sendMessage(`请解释这个操作的含义和作用：${event.content.replace('调用工具: ', '')}`)}
                  >
                    解释
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default TracePanel
