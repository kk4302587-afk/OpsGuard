import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import './styles/global.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#059669',
          colorBgContainer: '#ffffff',
          colorBgElevated: '#ffffff',
          colorBgLayout: '#f3f5f8',
          colorBorder: '#d9e0ea',
          colorText: '#1f2937',
          colorTextSecondary: '#4b5563',
          colorTextTertiary: '#6b7280',
          borderRadius: 10,
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Noto Sans SC', 'Helvetica Neue', sans-serif",
          fontSize: 14,
          lineHeight: 1.65,
          controlHeight: 34,
        },
        components: {
          Layout: {
            headerBg: '#ffffff',
            siderBg: '#e9eef5',
            bodyBg: '#f3f5f8',
          },
          Card: {
            colorBgContainer: '#ffffff',
            colorBorderSecondary: '#d9e0ea',
            paddingLG: 16,
          },
          Button: {
            borderRadius: 6,
            controlHeight: 32,
          },
          Input: {
            colorBgContainer: '#ffffff',
            colorBorder: '#c8d2df',
          },
          Modal: {
            colorBgElevated: '#ffffff',
            borderRadiusLG: 14,
          },
          Select: {
            colorBgContainer: '#ffffff',
          },
          Collapse: {
            colorBgContainer: 'transparent',
            colorBorder: '#d9e0ea',
          },
          Tag: {
            borderRadiusSM: 4,
          },
        },
      }}
    >
      <App />
    </ConfigProvider>
  </React.StrictMode>,
)
