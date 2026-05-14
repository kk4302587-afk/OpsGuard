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
          colorBgContainer: '#161920',
          colorBgElevated: '#1c2028',
          colorBgLayout: '#0f1117',
          colorBorder: 'rgba(255, 255, 255, 0.06)',
          colorText: '#f0f2f5',
          colorTextSecondary: '#a0a8b4',
          borderRadius: 10,
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Noto Sans SC', sans-serif",
          fontSize: 14,
          lineHeight: 1.6,
        },
        components: {
          Layout: {
            headerBg: '#0f1117',
            siderBg: '#0f1117',
            bodyBg: '#0a0c10',
          },
          Card: {
            colorBgContainer: '#161920',
            colorBorderSecondary: 'rgba(255, 255, 255, 0.06)',
          },
          Button: {
            borderRadius: 6,
          },
          Input: {
            colorBgContainer: '#0f1117',
            colorBorder: 'rgba(255, 255, 255, 0.08)',
          },
          Modal: {
            colorBgElevated: '#1c2028',
          },
        },
      }}
    >
      <App />
    </ConfigProvider>
  </React.StrictMode>,
)
