// d:\Python_exercise\项目2-智慧投研Agent\③项目代码\agentic_research\frontend\src\components\Layout\index.tsx
import React from 'react'
import { Layout as AntLayout } from 'antd'
import Header from '@/components/Layout/Header'
import Sider from '@/components/Layout/Sider'

const { Content } = AntLayout

interface Props {
  children: React.ReactNode
}

const Layout: React.FC<Props> = ({ children }) => {
  return (
    <AntLayout style={{ minHeight: '100vh' }}>
      <Sider />
      <AntLayout>
        <Header />
        <Content style={{ margin: '24px 16px', padding: 24, background: '#fff' }}>
          {children}
        </Content>
      </AntLayout>
    </AntLayout>
  )
}

export default Layout