import React, { useState, useEffect, useRef, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { Row, Col, Card, Statistic, Button, Tag, Space, List, Avatar, Select, Empty, Spin, App } from 'antd'
import {
  PlusOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  RocketOutlined,
  FileTextOutlined,
  LoadingOutlined,
} from '@ant-design/icons'
import { Column } from '@ant-design/charts'
import { tasksApi, TaskItem } from '@/api/tasks'
import { authApi } from '@/api/auth'

const buildTrendData = (tasks: TaskItem[], days: number) => {
  const cutoff = new Date()
  cutoff.setDate(cutoff.getDate() - days)
  cutoff.setHours(0, 0, 0, 0)

  const dailyMap = new Map<string, number>()
  for (let d = days - 1; d >= 0; d--) {
    const date = new Date()
    date.setDate(date.getDate() - d)
    const key = `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
    dailyMap.set(key, 0)
  }

  tasks.forEach(t => {
    if (!t.created_at) return
    const date = new Date(t.created_at)
    if (date >= cutoff) {
      const key = `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
      const existing = dailyMap.get(key)
      if (existing !== undefined) dailyMap.set(key, existing + 1)
    }
  })

  return Array.from(dailyMap.entries()).map(([date, value]) => {
    let colorValue = '#10b981'
    if (value > 50 && value <= 100) colorValue = '#f59e0b'
    if (value > 100) colorValue = '#ef4444'
    return { date, value, colorValue }
  })
}

const Dashboard: React.FC = () => {
  const navigate = useNavigate()
  const { message } = App.useApp()
  const [loading, setLoading] = useState(true)
  const [userName, setUserName] = useState('用户')
  const [queryInput, setQueryInput] = useState('')
  const [creating, setCreating] = useState(false)
  const quickInputRef = useRef<HTMLInputElement>(null)
  const [allTasks, setAllTasks] = useState<TaskItem[]>([])
  const [trendRange, setTrendRange] = useState(90)
  const [stats, setStats] = useState({
    total_tasks: 0,
    success_rate: 0,
    running_tasks: 0,
    avg_duration: 0,
    recent_reports: [] as TaskItem[]
  })

  const trendData = useMemo(
    () => buildTrendData(allTasks, trendRange),
    [allTasks, trendRange]
  )

  useEffect(() => {
    fetchDashboardData()
  }, [])

  // 轮询：有运行中的任务时，每 4 秒静默刷新状态
  useEffect(() => {
    if (stats.running_tasks === 0) return

    const interval = setInterval(() => {
      refreshSilently()
    }, 4000)

    return () => clearInterval(interval)
  }, [stats.running_tasks])

  const computeStats = (tasks: TaskItem[]) => {
    const total = tasks.length
    const successCount = tasks.filter(t => t.status === 'success' || t.final_status === 'success').length
    const runningCount = tasks.filter(t => t.status === 'running' || t.status === 'pending').length
    const completedTasks = tasks.filter(t => t.duration_ms != null)
    const avgDuration = completedTasks.length > 0
      ? Math.round(completedTasks.reduce((sum, t) => sum + (t.duration_ms || 0), 0) / completedTasks.length / 1000 * 10) / 10
      : 0
    const recentReports = tasks.slice(0, 6)
    return {
      total_tasks: total,
      success_rate: total > 0 ? Math.round(successCount / total * 1000) / 10 : 0,
      running_tasks: runningCount,
      avg_duration: avgDuration,
      recent_reports: recentReports
    }
  }

  const refreshSilently = async () => {
    try {
      const taskData = await tasksApi.list(0, 50)
      const tasks = taskData.items || []
      setAllTasks(tasks)
      setStats(computeStats(tasks))
    } catch {
      // 静默失败
    }
  }

  const fetchDashboardData = async () => {
    setLoading(true)
    try {
      const [taskData, userData] = await Promise.all([
        tasksApi.list(0, 50),
        authApi.getMe().catch(() => null)
      ])

      if (userData) {
        setUserName(userData.username)
      }

      const tasks = taskData.items || []
      setAllTasks(tasks)
      setStats(computeStats(tasks))
    } catch {
      // 接口不可用时展示空状态
    } finally {
      setLoading(false)
    }
  }

  const handleCreateTask = async () => {
    const query = queryInput.trim()
    if (!query) {
      message.warning('请输入公司名称或股票代码')
      return
    }
    setCreating(true)
    try {
      await tasksApi.create(query)
      message.success(`分析任务已创建: ${query}`)
      setQueryInput('')
      fetchDashboardData()
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '创建任务失败')
    } finally {
      setCreating(false)
    }
  }

  const handleFocusQuickInput = () => {
    quickInputRef.current?.focus()
    quickInputRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }



  const getStatusTag = (status: string) => {
    switch (status) {
      case 'success':
        return <Tag icon={<CheckCircleOutlined />} color="success">成功</Tag>
      case 'running':
        return <Tag icon={<ClockCircleOutlined />} color="processing">进行中</Tag>
      case 'failed':
        return <Tag icon={<CloseCircleOutlined />} color="error">失败</Tag>
      default:
        return <Tag>未知</Tag>
    }
  }

  const cardStyle = {
    background: 'linear-gradient(135deg, #f8f9ff 0%, #f0f4ff 100%)',
    borderRadius: 12,
    boxShadow: '0 2px 12px rgba(99, 102, 241, 0.08)',
    border: 'none',
  }

  const primaryColor = '#6366F1'

  return (
    <div style={{ background: '#fafbfc', minHeight: '100vh', padding: '24px' }}>
      {/* 顶部欢迎区域 */}
      <Row justify="space-between" align="middle" style={{ marginBottom: 24 }}>
        <Col>
          <h1 style={{ fontSize: 28, fontWeight: 600, margin: 0, color: '#1e293b' }}>
            你好，{userName}
          </h1>
          <p style={{ color: '#64748b', marginTop: 8 }}>
            累计 {stats.total_tasks} 个分析任务 · {stats.running_tasks} 个进行中
          </p>
        </Col>
        <Col>
          <Button 
            type="primary" 
            size="large" 
            icon={<PlusOutlined />}
            onClick={handleFocusQuickInput}
            style={{ 
              background: `linear-gradient(135deg, ${primaryColor} 0%, #8B5CF6 100%)`,
              border: 'none',
              height: 48,
              paddingLeft: 24,
              paddingRight: 24,
              fontSize: 16,
              boxShadow: '0 4px 12px rgba(99, 102, 241, 0.35)',
            }}
          >
            新建分析
          </Button>
        </Col>
      </Row>

      {/* 统计卡片 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card style={cardStyle}>
            <Statistic 
              title="总任务数" 
              value={stats.total_tasks}
              valueStyle={{ color: '#1e293b', fontWeight: 700, fontSize: 28 }}
            />
            <div style={{ marginTop: 8, color: '#64748b' }}>所有分析任务</div>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card style={cardStyle}>
            <Statistic 
              title="成功率" 
              value={stats.success_rate}
              precision={1}
              suffix="%"
              valueStyle={{ color: '#1e293b', fontWeight: 700, fontSize: 28 }}
            />
            <div style={{ marginTop: 8, color: '#64748b' }}>{stats.running_tasks} 个进行中</div>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card style={cardStyle}>
            <Statistic 
              title="平均耗时" 
              value={stats.avg_duration}
              suffix="秒"
              valueStyle={{ color: '#1e293b', fontWeight: 700, fontSize: 28 }}
            />
            <div style={{ marginTop: 8, color: '#64748b' }}>已完成任务均值</div>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card style={cardStyle}>
            <Statistic 
              title="运行中任务" 
              value={stats.running_tasks}
              valueStyle={{ color: '#1e293b', fontWeight: 700, fontSize: 28 }}
            />
            <div style={{ marginTop: 8, color: '#64748b' }}>当前活跃任务</div>
          </Card>
        </Col>
      </Row>

      {/* 使用 Flex 布局确保左右等高 */}
      <div style={{ display: 'flex', gap: 20, alignItems: 'stretch' }}>
        {/* 左侧：分析任务趋势图 */}
        <div style={{ flex: '0 0 62.5%', maxWidth: '62.5%' }}>
          <Card 
            title="分析任务趋势" 
            style={{ ...cardStyle, height: '100%', overflow: 'hidden' }}
            extra={
              <Select 
                value={`${trendRange}天`}
                style={{ width: 90 }}
                onChange={(val) => setTrendRange(parseInt(val))}
                options={[
                  { value: '7', label: '7天' },
                  { value: '30', label: '30天' },
                  { value: '90', label: '90天' },
                ]} 
              />
            }
          >
            <div style={{ width: '100%', height: 400, position: 'relative', paddingTop: 30 }}>
              {trendData.length > 0 ? (
                <Column {...({
                  data: trendData,
                  xField: 'date',
                  yField: 'value',
                  colorField: 'colorValue',
                  scale: {
                    color: { type: 'identity' },
                  },
                  columnStyle: {
                    radius: [6, 6, 0, 0],
                  },
                  autoFit: true,
                } as any)} />
              ) : (
                <Empty description="暂无趋势数据" style={{ marginTop: 80 }} />
              )}
            </div>
          </Card>
        </div>

        {/* 右侧：快速启动 + 最近的分析报告 */}
        <div style={{ flex: '0 0 37.5%', maxWidth: '37.5%', display: 'flex', flexDirection: 'column', gap: 20 }}>
          {/* 快速启动卡片 */}
          <Card 
            title="快速启动" 
            style={cardStyle}
            extra={<a style={{ color: primaryColor }}>收起</a>}
          >
            <div style={{ textAlign: 'center', padding: '24px 0' }}>
              <Avatar 
                size={64} 
                icon={<RocketOutlined />} 
                style={{ 
                  background: `linear-gradient(135deg, #e0e7ff 0%, #ddd6fe 100%)`,
                  color: primaryColor, 
                  marginBottom: 16 
                }} 
              />
              <h3 style={{ marginBottom: 8, color: '#1e293b', fontWeight: 600, fontSize: 16 }}>开始新的投研分析</h3>
              <p style={{ color: '#64748b', marginBottom: 20, fontSize: 13 }}>
                输入公司名称或股票代码，AI 将自动生成专业研究报告
              </p>
              <Space.Compact style={{ width: '100%', display: 'flex' }}>
                <input
                  ref={quickInputRef}
                  type="text"
                  value={queryInput}
                  onChange={(e) => setQueryInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleCreateTask() }}
                  placeholder="Apple Inc.、腾讯控股..."
                  style={{
                    flex: 1,
                    height: 40,
                    borderRadius: '6px 0 0 6px',
                    border: '1px solid #e2e8f0',
                    padding: '0 12px',
                    outline: 'none',
                    fontSize: 13,
                    transition: 'all 0.3s',
                  }}
                  onFocus={(e) => e.target.style.borderColor = primaryColor}
                  onBlur={(e) => e.target.style.borderColor = '#e2e8f0'}
                />
                <Button 
                  type="primary"
                  loading={creating}
                  onClick={handleCreateTask}
                  style={{ 
                    height: 40, 
                    borderRadius: '0 6px 6px 0',
                    background: `linear-gradient(135deg, ${primaryColor} 0%, #8B5CF6 100%)`,
                    border: 'none',
                    paddingLeft: 20,
                    paddingRight: 20,
                    fontWeight: 500,
                  }}
                >
                  开始分析
                </Button>
              </Space.Compact>
            </div>
          </Card>

          {/* 最近的分析报告卡片 */}
          <Card 
            title="最近的分析报告" 
            style={{ ...cardStyle, flex: 1 }}
            extra={<a style={{ color: primaryColor, cursor: 'pointer' }}>查看全部 →</a>}
          >
            {stats.recent_reports.length > 0 ? (
              <List
                dataSource={stats.recent_reports}
                renderItem={(item) => (
                  <List.Item actions={[<a key="view" style={{ color: primaryColor, cursor: 'pointer' }} onClick={() => navigate(`/report/${item.id}`)}>查看报告 →</a>]}>
                    <List.Item.Meta
                      avatar={
                        <Avatar
                          style={{
                            backgroundColor: item.status === 'success' ? '#10b981' : item.status === 'running' ? primaryColor : '#ef4444',
                          }}
                          icon={
                            item.status === 'success' ? <CheckCircleOutlined /> :
                            item.status === 'running' ? <ClockCircleOutlined /> : <CloseCircleOutlined />
                          }
                        />
                      }
                      title={
                        <Space>
                          <strong style={{ color: '#1e293b' }}>{item.query}</strong>
                          {getStatusTag(item.status)}
                        </Space>
                      }
                      description={
                        <span style={{ color: '#64748b' }}>
                          {item.created_at ? new Date(item.created_at).toLocaleString() : ''}
                        </span>
                      }
                    />
                  </List.Item>
                )}
              />
            ) : (
              <Empty description="暂无分析报告" style={{ padding: '40px 0' }} />
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}

export default Dashboard