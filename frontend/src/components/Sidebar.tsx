import { useEffect } from 'react'
import { Button, Typography, Space } from 'antd'
import { PlusOutlined, MessageOutlined, DeleteOutlined } from '@ant-design/icons'
import { useChatStore } from '../stores/chatStore'

const { Text } = Typography

/**
 * Left sidebar with session/conversation list.
 */
function Sidebar() {
  const { sessions, activeSessionId, createSession, setActiveSession, deleteSession, fetchSessions } = useChatStore()

  useEffect(() => {
    fetchSessions()
  }, [fetchSessions])

  return (
    <div style={{ padding: 12, height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Button
        type="primary"
        ghost
        icon={<PlusOutlined />}
        block
        onClick={createSession}
        style={{ marginBottom: 16, borderRadius: 8, height: 42 }}
      >
        新建会话
      </Button>

      <div style={{ flex: 1, overflow: 'auto' }}>
        {sessions.map((session) => (
          <div
            key={session.id}
            onClick={() => setActiveSession(session.id)}
            className="session-item"
            style={{
              padding: session.id === activeSessionId ? '12px 14px 12px 11px' : '12px 14px',
              borderRadius: 8,
              marginBottom: 4,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              background: session.id === activeSessionId ? 'var(--bg-primary)' : 'transparent',
              border: session.id === activeSessionId ? '1px solid var(--border-color)' : '1px solid transparent',
              borderLeft: session.id === activeSessionId ? '3px solid var(--accent-green)' : '3px solid transparent',
              boxShadow: session.id === activeSessionId ? '0 2px 6px rgba(15, 23, 42, 0.03)' : 'none',
              transition: 'all 0.2s ease',
            }}
            onMouseEnter={(e) => {
              if (session.id !== activeSessionId) {
                e.currentTarget.style.background = 'var(--bg-elevated)'
                e.currentTarget.style.borderLeft = '3px solid transparent'
              }
            }}
            onMouseLeave={(e) => {
              if (session.id !== activeSessionId) {
                e.currentTarget.style.background = 'transparent'
                e.currentTarget.style.borderLeft = '3px solid transparent'
              }
            }}
          >
            <Space size={8}>
              <MessageOutlined style={{ color: session.id === activeSessionId ? 'var(--accent-green)' : 'var(--text-muted)', fontSize: 15 }} />
              <Text
                ellipsis
                style={{
                  maxWidth: 120,
                  fontSize: 14,
                  color: session.id === activeSessionId ? 'var(--text-primary)' : 'var(--text-secondary)',
                }}
              >
                {session.title || '新会话'}
              </Text>
            </Space>
            <DeleteOutlined
              onClick={(e) => {
                e.stopPropagation()
                deleteSession(session.id)
              }}
              style={{ color: 'var(--text-muted)', fontSize: 13, opacity: 0.5 }}
            />
          </div>
        ))}

        {sessions.length === 0 && (
          <div style={{ textAlign: 'center', padding: '40px 0' }}>
            <MessageOutlined style={{ fontSize: 24, color: 'var(--text-muted)', marginBottom: 8 }} />
            <Text style={{ display: 'block', color: 'var(--text-muted)', fontSize: 13 }}>
              暂无会话
            </Text>
          </div>
        )}
      </div>
    </div>
  )
}

export default Sidebar
