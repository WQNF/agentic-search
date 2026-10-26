// d:\Python_exercise\项目2-智慧投研Agent\③项目代码\agentic_research\frontend\src\components\Layout\Sider.tsx
import React from 'react'
import { Layout, Menu } from 'antd'
import {
  DashboardOutlined,
  FileTextOutlined,
  BarChartOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { useNavigate, useLocation } from 'react-router-dom'

const { Sider: AntSider } = Layout

const menuItems = [
  { key: '/', icon: <DashboardOutlined style={{ fontSize: 24 }} />, label: '工作台' },
  { key: '/tasks', icon: <FileTextOutlined style={{ fontSize: 24 }} />, label: '任务' },
  { key: '/reports', icon: <BarChartOutlined style={{ fontSize: 24 }} />, label: '报告' },
  { key: '/settings', icon: <SettingOutlined style={{ fontSize: 24 }} />, label: '设置' },
]

const Sider: React.FC = () => {
  const navigate = useNavigate()
  const location = useLocation()

  return (
    <AntSider width={320} theme="light" style={{ borderRight: '1px solid #f0f0f0' }}>
      <div style={{ height: 64, display: 'flex', alignItems: 'center', justifyContent: 'center', borderBottom: '1px solid #f0f0f0' }}>
        <span style={{ fontSize: 24, fontWeight: 600 }}>📊 菜单</span>
      </div>
      <Menu
        mode="inline"
        selectedKeys={[location.pathname]}
        items={menuItems}
        onClick={({ key }) => navigate(key)}
        style={{ height: '100%', borderRight: 0, fontSize: 20, fontWeight: 500 }}
      />
    </AntSider>
  )
}

export default Sider