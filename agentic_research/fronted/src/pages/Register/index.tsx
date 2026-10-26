// d:\Python_exercise\项目2-智慧投研Agent\③项目代码\agentic_research\frontend\src\pages\Register\index.tsx
import React from 'react'
import { Form, Input, Button, Card, Typography, message } from 'antd'
import { UserOutlined, LockOutlined, MailOutlined } from '@ant-design/icons'
import { Link, useNavigate } from 'react-router-dom'
import { authApi } from '@/api/auth'
import useAuthStore from '@/utils/token'

const { Title, Text } = Typography

const Register: React.FC = () => {
  const [loading, setLoading] = React.useState(false)
  const navigate = useNavigate()
  const setToken = useAuthStore((state) => state.setToken)

  const onFinish = async (values: { username: string; password: string; email: string }) => {
    setLoading(true)
    try {
      const data = await authApi.register(values)
      setToken(data.access_token)
      message.success('注册成功！')
      navigate('/')
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '注册失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center',
      background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
    }}>
      <Card style={{ width: 420, boxShadow: '0 8px 32px rgba(0,0,0,0.15)' }}>
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <Title level={2} style={{ marginBottom: 8 }}>
            🧠 Agentic Research
          </Title>
          <Text type="secondary">智慧投研 Agent 平台</Text>
        </div>

        <Form name="register" onFinish={onFinish} size="large" layout="vertical">
          <Form.Item
            name="username"
            rules={[
              { required: true, message: '请输入用户名!' },
              { min: 3, max: 50, message: '用户名长度为 3-50 个字符' },
            ]}
          >
            <Input prefix={<UserOutlined />} placeholder="用户名（3-50个字符）" />
          </Form.Item>

          <Form.Item
            name="email"
            rules={[
              { required: true, message: '请输入邮箱!' },
              { type: 'email', message: '邮箱格式不正确!' },
            ]}
          >
            <Input prefix={<MailOutlined />} placeholder="邮箱地址" />
          </Form.Item>

          <Form.Item
            name="password"
            rules={[
              { required: true, message: '请输入密码!' },
              { min: 6, max: 128, message: '密码长度为 6-128 个字符' },
            ]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="密码（6-128个字符）" />
          </Form.Item>

          <Form.Item
            name="confirmPassword"
            dependencies={['password']}
            rules={[
              { required: true, message: '请确认密码!' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('password') === value) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error('两次密码不一致!'))
                },
              }),
            ]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="确认密码" />
          </Form.Item>

          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} block>
              注册
            </Button>
          </Form.Item>

          <div style={{ textAlign: 'center' }}>
            <Text type="secondary">已有账号？</Text>{' '}
            <Link to="/login">返回登录</Link>
          </div>
        </Form>
      </Card>
    </div>
  )
}

export default Register