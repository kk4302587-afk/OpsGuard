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
          colorPrimary: '#34d399',
          colorBgContainer: '#141518',
          colorBgElevated: '#1a1b20',
          colorBgLayout: '#0c0d11',
          colorBorder: 'rgba(255, 255, 255, 0.05)',
          colorText: '#d8dce2',
          colorTextSecondary: '#8b929e',
          colorTextTertiary: '#555b66',
          borderRadius: 10,
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Noto Sans SC', 'Helvetica Neue', sans-serif",
          fontSize: 14,
          lineHeight: 1.65,
          controlHeight: 34,
        },
        components: {
          Layout: {
            headerBg: '#0c0d11',
            siderBg: '#0c0d11',
            bodyBg: '#09090b',
          },
          Card: {
            colorBgContainer: '#141518',
            colorBorderSecondary: 'rgba(255, 255, 255, 0.05)',
            paddingLG: 16,
          },
          Button: {
            borderRadius: 6,
            controlHeight: 32,
          },
          Input: {
            colorBgContainer: '#0c0d11',
            colorBorder: 'rgba(255, 255, 255, 0.06)',
          },
          Modal: {
            colorBgElevated: '#1a1b20',
            borderRadiusLG: 14,
          },
          Select: {
            colorBgContainer: '#141518',
          },
          Collapse: {
            colorBgContainer: 'transparent',
            colorBorder: 'rgba(255, 255, 255, 0.05)',
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
