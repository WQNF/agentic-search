// d:\Python_exercise\项目2-智慧投研Agent\③项目代码\agentic_research\frontend\src\components\Layout\Header.tsx
import React, { useState } from 'react'
import { Layout, Input, Avatar, Badge, Dropdown, App } from 'antd'
import {
  SearchOutlined,
  BellOutlined,
  UserOutlined,
  LogoutOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import useAuthStore from '@/utils/token'
import { tasksApi } from '@/api/tasks'

const { Header: AntHeader } = Layout

const Header: React.FC = () => {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const { clearToken } = useAuthStore()
  const [searchValue, setSearchValue] = useState('')
  const [searching, setSearching] = useState(false)

  const handleLogout = () => {
    clearToken()
    navigate('/login')
  }

  const handleSearch = async () => {
    const query = searchValue.trim()
    if (!query) {
      message.warning('请输入公司名称或股票代码')
      return
    }
    setSearching(true)
    try {
      const task = await tasksApi.create(query)
      message.success(`分析任务已创建: ${task.query}`)
      setSearchValue('')
      navigate('/')
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '创建任务失败')
    } finally {
      setSearching(false)
    }
  }

  const items = [
    { key: 'profile', icon: <UserOutlined />, label: '个人中心' },
    { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true, onClick: handleLogout },
  ]

  return (
    <AntHeader style={{ background: '#fff', padding: '0 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', boxShadow: '0 2px 8px rgba(0,0,0,0.06)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <div style={{ fontSize: 20, fontWeight: 600, color: '#1890ff' }}>
          🧠 Agentic Research
        </div>
        <Input
          prefix={<SearchOutlined />}
          suffix={
            <SearchOutlined
              style={{ cursor: 'pointer', color: '#1890ff' }}
              onClick={handleSearch}
            />
          }
          placeholder="搜索任意公司，开始AI投研分析..."
          allowClear
          value={searchValue}
          onChange={(e) => setSearchValue(e.target.value)}
          onPressEnter={handleSearch}
          disabled={searching}
          style={{ width: 420 }}
        />
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <Badge size="small">
          <BellOutlined style={{ fontSize: 20, cursor: 'pointer' }} />
        </Badge>
        <Dropdown menu={{ items }} placement="bottomRight">
          <Avatar icon={<UserOutlined />} style={{ cursor: 'pointer' }} />
        </Dropdown>
      </div>
    </AntHeader>
  )
}

export default Header