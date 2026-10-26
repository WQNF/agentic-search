// d:\Python_exercise\项目2-智慧投研Agent\③项目代码\agentic_research\fronted\src\pages\Report\index.tsx
import React, { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Spin, Result, Button, Tag, Typography, Divider, App } from 'antd'
import {
  ArrowLeftOutlined,
  FileTextOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons'
import { tasksApi, ReportData } from '@/api/tasks'

const { Title, Text, Paragraph } = Typography

const Report: React.FC = () => {
  const { taskId } = useParams<{ taskId: string }>()
  const navigate = useNavigate()
  const { message } = App.useApp()
  const [loading, setLoading] = useState(true)

  const cardStyle: React.CSSProperties = {
    marginBottom: 16,
    borderRadius: 12,
    border: '1px solid #e2e8f0',
    boxShadow: '0 2px 8px rgba(99, 102, 241, 0.06)',
  }
  const [report, setReport] = useState<ReportData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (taskId) fetchReport(taskId)
  }, [taskId])

  const fetchReport = async (id: string) => {
    setLoading(true)
    try {
      const data = await tasksApi.getReport(id)
      setReport(data)
    } catch (err: any) {
      const detail = err?.response?.data?.detail || '获取报告失败'
      setError(detail)
      if (err?.response?.status === 404) {
        message.info('报告尚未生成，请等待任务完成')
      } else {
        message.error(detail)
      }
    } finally {
      setLoading(false)
    }
  }

  const renderSections = (markdown: string) => {
    const normalized = markdown
      .replace(/\\n/g, '\n')
      .replace(/\\"/g, '"')
      .replace(/\\t/g, '\t')
      .replace(/\r\n/g, '\n')
    const parts = normalized.split(/(?=\n## )/)

    return parts.map((part, idx) => {
      const trimmed = part.trim()
      if (!trimmed) return null

      const lines = trimmed.split('\n')
      const firstLine = lines[0]

      const isH2 = firstLine.startsWith('## ')
      if (isH2) {
        const heading = firstLine.replace(/^## /, '')
        const body = lines.slice(1).join('\n').trim()
        return (
          <Card
            key={idx}
            title={
              <span style={{ color: '#4c1d95', fontWeight: 700, fontSize: 15 }}>
                {heading}
              </span>
            }
            style={cardStyle}
            headStyle={{
              borderBottom: '2px solid #e8ecf4',
              background: 'linear-gradient(135deg, #f5f3ff 0%, #ede9fe 100%)',
              borderRadius: '12px 12px 0 0',
            }}
          >
            {renderSectionBody(body)}
          </Card>
        )
      }

      return (
        <Card
          key={idx}
          style={{
            marginBottom: 20,
            borderRadius: 12,
            border: '1px solid #e2e8f0',
            boxShadow: '0 2px 8px rgba(99, 102, 241, 0.06)',
            background: 'linear-gradient(135deg, #fafbff 0%, #f0f4ff 100%)',
          }}
        >
          {renderHeroBlock(trimmed)}
        </Card>
      )
    })
  }

  const renderFormattedText = (text: string): React.ReactNode => {
    const parts = text.split(/(\*\*[^*]+\*\*)/g)
    return parts.map((part, i) => {
      if (part.startsWith('**') && part.endsWith('**')) {
        return <strong key={i}>{part.slice(2, -2)}</strong>
      }
      return <React.Fragment key={i}>{part}</React.Fragment>
    })
  }

  const renderHeroBlock = (content: string) => {
    const blocks = content.split(/\n(?=---|[>#])/)
    return (
      <div>
        {blocks.map((block, i) => {
          const t = block.trim()
          if (!t) return null
          if (t === '---') return <Divider key={i} style={{ margin: '12px 0' }} />
          if (t.startsWith('#')) {
            return (
              <Title key={i} level={2} style={{ color: '#1e293b', marginBottom: 16 }}>
                {renderFormattedText(t.replace(/^# /, ''))}
              </Title>
            )
          }
          if (t.startsWith('> ')) {
            return (
              <blockquote
                key={i}
                style={{
                  borderLeft: '3px solid #a5b4fc',
                  paddingLeft: 16,
                  margin: '12px 0',
                  color: '#64748b',
                  fontStyle: 'italic',
                  lineHeight: 1.8,
                }}
              >
                {t.replace(/^> /gm, '').split('\n').map((line, li) => (
                  <div key={li}>{renderFormattedText(line)}</div>
                ))}
              </blockquote>
            )
          }
          return (
            <div key={i} style={{ color: '#334155', lineHeight: 1.9, whiteSpace: 'pre-wrap', marginBottom: 8 }}>
              {renderFormattedText(t)}
            </div>
          )
        })}
      </div>
    )
  }

  const renderSectionBody = (body: string) => {
    if (!body) return <Text type="secondary">暂无内容</Text>

    const lines = body.split('\n')
    const elements: React.ReactNode[] = []
    let i = 0

    while (i < lines.length) {
      const line = lines[i]
      const trimmed = line.trim()

      if (trimmed === '') { i++; continue }

      if (trimmed === '---') {
        elements.push(<Divider key={elements.length} style={{ margin: '16px 0' }} />)
        i++; continue
      }

      if (line.startsWith('### ') || line.startsWith('#### ')) {
        const level = line.startsWith('#### ') ? 5 : 4
        elements.push(
          <Title key={elements.length} level={level} style={{ color: '#334155', marginTop: 20, marginBottom: 12 }}>
            {renderFormattedText(line.replace(/^#{3,4} /, ''))}
          </Title>
        )
        i++; continue
      }

      if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
        const tableLines: string[] = []
        while (i < lines.length && lines[i].trim().startsWith('|') && lines[i].trim().endsWith('|')) {
          tableLines.push(lines[i])
          i++
        }
        elements.push(renderTableBlock(tableLines, elements.length))
        continue
      }

      if (/^\d+\.\s/.test(line)) {
        const items: string[] = []
        while (i < lines.length && /^\d+\.\s/.test(lines[i])) {
          items.push(lines[i].replace(/^\d+\.\s/, ''))
          i++
        }
        elements.push(
          <ol key={elements.length} style={{ margin: '8px 0', paddingLeft: 24 }}>
            {items.map((item, idx) => (
              <li key={idx} style={{ marginBottom: 6, color: '#334155', lineHeight: 1.9 }}>
                {renderFormattedText(item)}
              </li>
            ))}
          </ol>
        )
        continue
      }

      if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
        const items: string[] = []
        while (i < lines.length && (lines[i].trim().startsWith('- ') || lines[i].trim().startsWith('* '))) {
          items.push(lines[i].trim().replace(/^[-*]\s/, ''))
          i++
        }
        elements.push(
          <ul key={elements.length} style={{ margin: '8px 0', paddingLeft: 24 }}>
            {items.map((item, idx) => (
              <li key={idx} style={{ marginBottom: 6, color: '#334155', lineHeight: 1.9 }}>
                {renderFormattedText(item)}
              </li>
            ))}
          </ul>
        )
        continue
      }

      if (line.startsWith('> ')) {
        const quoteLines: string[] = []
        while (i < lines.length && lines[i].startsWith('> ')) {
          quoteLines.push(lines[i].replace(/^> ?/, ''))
          i++
        }
        elements.push(
          <blockquote key={elements.length} style={{
            borderLeft: '3px solid #a5b4fc',
            paddingLeft: 16, margin: '12px 0',
            color: '#64748b', fontStyle: 'italic', lineHeight: 1.8,
          }}>
            {quoteLines.map((ql, idx) => (
              <div key={idx} style={{ marginBottom: 4 }}>{renderFormattedText(ql)}</div>
            ))}
          </blockquote>
        )
        continue
      }

      const paraLines: string[] = []
      while (i < lines.length) {
        const l = lines[i]
        const tl = l.trim()
        if (tl === '' || tl === '---' ||
            l.startsWith('### ') || l.startsWith('#### ') ||
            (tl.startsWith('|') && tl.endsWith('|')) ||
            /^\d+\.\s/.test(l) ||
            tl.startsWith('- ') || tl.startsWith('* ') ||
            l.startsWith('> ')) {
          break
        }
        paraLines.push(l)
        i++
      }

      const paraText = paraLines.join('\n').trim()
      if (paraText) {
        elements.push(
          <div key={elements.length} style={{ color: '#334155', lineHeight: 1.9, whiteSpace: 'pre-wrap', marginBottom: 12 }}>
            {renderFormattedText(paraText)}
          </div>
        )
      }
    }

    return <div>{elements}</div>
  }

  const renderTableBlock = (tableLines: string[], key: number) => {
    const nonEmpty = tableLines.filter(l => l.trim())
    if (nonEmpty.length < 2) return <Text key={key}>{tableLines.join('\n')}</Text>

    const headers = nonEmpty[0]
      .split('|')
      .map(h => h.trim())
      .filter(h => h !== '')

    const dataLines = nonEmpty.filter(l => !l.includes('---'))
    const rows = dataLines.slice(1).map(row =>
      row.split('|').map(cell => cell.trim()).filter((_, ci) => ci > 0 && ci <= headers.length)
    )

    return (
      <div key={key} style={{ overflowX: 'auto', marginBottom: 16 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ background: 'linear-gradient(135deg, #e0e7ff 0%, #ddd6fe 100%)' }}>
              {headers.map((h, i) => (
                <th key={i} style={{
                  padding: '10px 16px', textAlign: 'left',
                  color: '#4c1d95', fontWeight: 600,
                  borderBottom: '2px solid #c7d2fe',
                }}>
                  {renderFormattedText(h)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri} style={{ background: ri % 2 === 0 ? '#fafbfc' : '#fff' }}>
                {row.map((cell, ci) => (
                  <td key={ci} style={{
                    padding: '8px 16px', borderBottom: '1px solid #f1f5f9', color: '#334155',
                  }}>
                    {renderFormattedText(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  const getStatusTag = (status: string) => {
    switch (status) {
      case 'success':
        return <Tag icon={<CheckCircleOutlined />} color="success">成功</Tag>
      case 'failed':
        return <Tag icon={<CloseCircleOutlined />} color="error">失败</Tag>
      default:
        return <Tag icon={<ClockCircleOutlined />} color="processing">进行中</Tag>
    }
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 400 }}>
        <Spin size="large" tip="加载报告中..." />
      </div>
    )
  }

  if (error || !report) {
    return (
      <Result
        status="warning"
        title="无法获取报告"
        subTitle={error || '报告数据为空'}
        extra={
          <Button type="primary" onClick={() => navigate('/')} icon={<ArrowLeftOutlined />}>
            返回首页
          </Button>
        }
      />
    )
  }

  return (
    <div style={{ background: '#fafbfc', minHeight: '100vh', padding: '24px', maxWidth: 1000, margin: '0 auto' }}>
      <div style={{ marginBottom: 24, display: 'flex', alignItems: 'center', gap: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/')}>
          返回
        </Button>
        <Title level={3} style={{ margin: 0, color: '#1e293b', flex: 1 }}>
          <FileTextOutlined style={{ marginRight: 8, color: '#6366F1' }} />
          研究报告
        </Title>
        {getStatusTag(report.final_status)}
      </div>

      <Divider style={{ marginTop: 0 }} />

      {renderSections(report.report_markdown)}
    </div>
  )
}

export default Report