import { useEffect, useState } from 'react'
import { Tooltip } from 'antd'
import {
  MessageOutlined,
  ApartmentOutlined,
  SafetyOutlined,
  RadarChartOutlined,
  MedicineBoxOutlined,
  DatabaseOutlined,
  ApiOutlined,
  ThunderboltOutlined,
  FileTextOutlined,
  LeftOutlined,
  RightOutlined,
} from '@ant-design/icons'
import Sidebar from './components/Sidebar'
import ChatPanel from './components/ChatPanel'
import TracePanel from './components/TracePanel'
import StatusBar from './components/StatusBar'
import ApprovalModal from './components/ApprovalModal'
import TopologyGraph from './components/TopologyGraph'
import SecurityDemo from './components/SecurityDemo'
import SecurityPosture from './components/SecurityPosture'
import HealthReport from './components/HealthReport'
import KnowledgePanel from './components/KnowledgePanel'
import ToolsPanel from './components/ToolsPanel'
import RunbookPanel from './components/RunbookPanel'
import OpsReport from './components/OpsReport'
import { useChatStore } from './stores/chatStore'
import LandingPage from './components/LandingPage'
import './styles/layout.css'

type PageKey = 'chat' | 'topology' | 'securityPosture' | 'security' | 'health' | 'knowledge' | 'tools' | 'runbook' | 'report'

const navItems = [
  { key: 'chat', icon: <MessageOutlined />, label: '对话' },
  { key: 'tools', icon: <ApiOutlined />, label: 'MCP 工具' },
  { key: 'runbook', icon: <ThunderboltOutlined />, label: '运维剧本' },
  { key: 'report', icon: <FileTextOutlined />, label: '运维报告' },
  { key: 'topology', icon: <ApartmentOutlined />, label: '拓扑图谱' },
  { key: 'health', icon: <MedicineBoxOutlined />, label: '健康巡检' },
  { key: 'securityPosture', icon: <RadarChartOutlined />, label: '安全态势' },
  { key: 'security', icon: <SafetyOutlined />, label: '安全靶场' },
  { key: 'knowledge', icon: <DatabaseOutlined />, label: '知识库' },
]

function App() {
  const { pendingApproval, clearApproval } = useChatStore()
  const [activePage, setActivePage] = useState<PageKey>(() => {
    const saved = sessionStorage.getItem('opsguard_active_page')
    return (saved as PageKey) || 'chat'
  })
  const [siderCollapsed, setSiderCollapsed] = useState(false)
  const [btnHovered, setBtnHovered] = useState(false)
  const [enteredApp, setEnteredApp] = useState(() => {
    return sessionStorage.getItem('opsguard_entered') === 'true'
  })

  useEffect(() => {
    const handleNavigate = (event: Event) => {
      const page = (event as CustomEvent<PageKey>).detail
      if (navItems.some((item) => item.key === page)) {
        setActivePage(page)
      }
    }

    const handleExit = () => {
      setEnteredApp(false)
      sessionStorage.removeItem('opsguard_entered')
    }

    window.addEventListener('opsguard:navigate', handleNavigate)
    window.addEventListener('opsguard:exit', handleExit)
    return () => {
      window.removeEventListener('opsguard:navigate', handleNavigate)
      window.removeEventListener('opsguard:exit', handleExit)
    }
  }, [])

  useEffect(() => {
    sessionStorage.setItem('opsguard_active_page', activePage)
  }, [activePage])

  const renderContent = () => {
    switch (activePage) {
      case 'chat':
        return (
          <div style={{ display: 'flex', height: '100%', overflow: 'hidden', position: 'relative' }}>
            <div
              className="session-sider"
              style={{
                width: siderCollapsed ? 0 : 220,
                minWidth: siderCollapsed ? 0 : 220,
                opacity: siderCollapsed ? 0 : 1,
                overflow: 'hidden',
                transition: 'all 0.3s cubic-bezier(0.25, 1, 0.5, 1)',
                borderRight: siderCollapsed ? 'none' : '1px solid var(--border-color-strong)',
              }}
            >
              <Sidebar />
            </div>

            {/* Collapse toggle button */}
            <div
              onClick={() => setSiderCollapsed(!siderCollapsed)}
              onMouseEnter={() => setBtnHovered(true)}
              onMouseLeave={() => setBtnHovered(false)}
              style={{
                position: 'absolute',
                left: siderCollapsed ? 8 : 210,
                top: '50%',
                transform: 'translateY(-50%)',
                zIndex: 100,
                width: 20,
                height: 38,
                background: '#ffffff',
                border: '1px solid var(--border-color-strong)',
                borderRadius: '6px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: '0 2px 8px rgba(15, 23, 42, 0.06)',
                transition: 'all 0.3s cubic-bezier(0.25, 1, 0.5, 1), opacity 0.15s ease',
                opacity: btnHovered ? 1 : 0.45,
              }}
            >
              {siderCollapsed ? (
                <RightOutlined style={{ fontSize: 10, color: 'var(--text-muted)' }} />
              ) : (
                <LeftOutlined style={{ fontSize: 10, color: 'var(--text-muted)' }} />
              )}
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
      case 'securityPosture':
        return <SecurityPosture />
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
      case 'report':
        return <OpsReport />
      default:
        return null
    }
  }

  if (!enteredApp) {
    return (
      <LandingPage
        onEnter={() => {
          setEnteredApp(true)
          sessionStorage.setItem('opsguard_entered', 'true')
        }}
      />
    )
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
                onClick={() => {
                  if (item.key === 'chat' && activePage === 'chat') {
                    setSiderCollapsed(!siderCollapsed)
                  } else {
                    setActivePage(item.key as PageKey)
                  }
                }}
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
