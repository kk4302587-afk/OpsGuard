import { useState } from 'react'
import { Tooltip } from 'antd'
import {
  MessageOutlined,
  ApartmentOutlined,
  SafetyOutlined,
  MedicineBoxOutlined,
  DatabaseOutlined,
  ApiOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import Sidebar from './components/Sidebar'
import ChatPanel from './components/ChatPanel'
import TracePanel from './components/TracePanel'
import StatusBar from './components/StatusBar'
import ApprovalModal from './components/ApprovalModal'
import TopologyGraph from './components/TopologyGraph'
import SecurityDemo from './components/SecurityDemo'
import HealthReport from './components/HealthReport'
import KnowledgePanel from './components/KnowledgePanel'
import ToolsPanel from './components/ToolsPanel'
import RunbookPanel from './components/RunbookPanel'
import { useChatStore } from './stores/chatStore'
import './styles/layout.css'

type PageKey = 'chat' | 'topology' | 'security' | 'health' | 'knowledge' | 'tools' | 'runbook'

const navItems = [
  { key: 'chat', icon: <MessageOutlined />, label: '对话' },
  { key: 'tools', icon: <ApiOutlined />, label: 'MCP 工具' },
  { key: 'runbook', icon: <ThunderboltOutlined />, label: '运维剧本' },
  { key: 'topology', icon: <ApartmentOutlined />, label: '拓扑图谱' },
  { key: 'health', icon: <MedicineBoxOutlined />, label: '健康巡检' },
  { key: 'security', icon: <SafetyOutlined />, label: '安全演示' },
  { key: 'knowledge', icon: <DatabaseOutlined />, label: '知识库' },
]

function App() {
  const { pendingApproval, clearApproval } = useChatStore()
  const [activePage, setActivePage] = useState<PageKey>('chat')

  const renderContent = () => {
    switch (activePage) {
      case 'chat':
        return (
          <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
            <div className="session-sider" style={{ width: 220, minWidth: 220 }}>
              <Sidebar />
            </div>
            <div className="chat-content" style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
              <ChatPanel />
            </div>
            <div className="trace-sider" style={{ width: 320, minWidth: 320 }}>
              <TracePanel />
            </div>
          </div>
        )
      case 'topology':
        return <TopologyGraph />
      case 'security':
        return <SecurityDemo />
      case 'health':
        return <HealthReport />
      case 'knowledge':
        return <KnowledgePanel />
      case 'tools':
        return <ToolsPanel />
      case 'runbook':
        return <RunbookPanel />
      default:
        return null
    }
  }

  return (
    <div className="app-layout">
      {/* Top status bar */}
      <div className="status-header">
        <StatusBar />
      </div>

      <div style={{ display: 'flex', flex: 1, height: 0 }}>
        {/* Left icon navigation */}
        <div className="nav-rail">
          {navItems.map((item) => (
            <Tooltip key={item.key} title={item.label} placement="right">
              <div
                className={`nav-rail-item ${activePage === item.key ? 'nav-rail-item-active' : ''}`}
                onClick={() => setActivePage(item.key as PageKey)}
              >
                {item.icon}
              </div>
            </Tooltip>
          ))}
        </div>

        {/* Main content area */}
        <div style={{ flex: 1, height: '100%', overflow: 'hidden' }}>
          {renderContent()}
        </div>
      </div>

      {/* Approval modal */}
      <ApprovalModal
        visible={!!pendingApproval}
        request={pendingApproval}
        onClose={clearApproval}
      />
    </div>
  )
}

export default App
