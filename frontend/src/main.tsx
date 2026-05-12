import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider, theme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import './styles/global.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: '#00d4aa',
          colorBgContainer: '#1a1d23',
          colorBgElevated: '#21252b',
          colorBgLayout: '#13151a',
          colorBorder: '#2d3139',
          colorText: '#e4e7eb',
          colorTextSecondary: '#8b929a',
          borderRadius: 8,
          fontFamily: "'JetBrains Mono', 'Fira Code', 'SF Mono', Menlo, monospace",
        },
        components: {
          Layout: {
            headerBg: '#1a1d23',
            siderBg: '#1a1d23',
            bodyBg: '#13151a',
          },
        },
      }}
    >
      <App />
    </ConfigProvider>
  </React.StrictMode>,
)
