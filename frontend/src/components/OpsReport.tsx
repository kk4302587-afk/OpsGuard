import { useState } from 'react'
import { Button, Card, Space, Typography, Spin, Tag, Statistic, Select, Modal } from 'antd'
import {
  FileTextOutlined,
  ReloadOutlined,
  MessageOutlined,
  ToolOutlined,
  SafetyOutlined,
  CheckCircleOutlined,
  BulbOutlined,
  PlayCircleOutlined,
  FilePdfOutlined,
  AlertOutlined,
} from '@ant-design/icons'

const { Title, Text, Paragraph } = Typography

const TOOL_DISPLAY_NAMES: Record<string, string> = {
  list_processes: '进程列表',
  find_zombie_processes: '查找僵尸进程',
  get_process_detail: '进程详情',
  kill_process: '终止进程',
  get_disk_usage: '磁盘使用情况',
  find_large_files: '查找大文件',
  get_directory_size: '目录大小',
  get_inode_usage: 'Inode 使用情况',
  check_file_info: '文件信息',
  get_listening_ports: '监听端口',
  get_connections: '网络连接',
  get_connection_count: '连接数统计',
  check_port: '端口占用查询',
  ping_host: 'Ping 主机',
  get_journal_logs: 'systemd 日志',
  get_recent_errors: '最近错误日志',
  tail_log_file: '查看日志末尾',
  search_logs: '搜索日志',
  get_boot_logs: '启动日志',
  list_services: '服务列表',
  get_service_status: '服务状态',
  get_failed_services: '失败的服务',
  restart_service: '重启服务',
  start_service: '启动服务',
  stop_service: '停止服务',
  get_service_logs: '服务日志',
  read_config_file: '读取配置文件',
  check_config_syntax: '检查配置语法',
  diff_config: '对比配置',
  system_overview: '系统概览',
  health_check: '健康检查',
  get_crontab_list: '定时任务列表',
  get_user_sessions: '登录用户会话',
  list_directory: '列出目录',
  read_file: '读取文件',
  find_files: '查找文件/目录',
  create_file: '创建文件',
  create_directory: '创建目录',
  write_file: '写入文件',
  delete_file: '删除文件',
  delete_directory: '删除目录',
  move_file: '移动/重命名文件',
  copy_file: '复制文件/目录',
  change_permissions: '修改文件权限',
  change_owner: '修改文件所有者',
  list_installed_packages: '已安装软件包',
  search_package: '搜索软件包',
  install_package: '安装软件包',
  remove_package: '卸载软件包',
  check_package_updates: '检查软件更新',
  list_users: '用户列表',
  list_groups: '用户组列表',
  get_user_info: '用户详情',
  create_user: '创建用户',
  delete_user: '删除用户',
  lock_user: '锁定用户',
  unlock_user: '解锁用户',
  get_firewall_status: '防火墙状态',
  list_open_ports: '已开放端口',
  allow_port: '开放端口',
  block_port: '关闭端口',
  list_cron_jobs: '定时任务详情',
  list_system_timers: 'systemd 定时器',
  add_cron_job: '添加定时任务',
  remove_cron_job: '删除定时任务',
  list_backups: '备份列表',
  rollback_backup: '恢复备份',
  get_recent_changes: '最近变更',
}

const STATUS_LABELS: Record<string, string> = {
  resolved: '已解决',
  failed: '失败',
  open: '处理中',
  active: '处理中',
}

const STATUS_COLORS: Record<string, string> = {
  resolved: 'green',
  failed: 'red',
  open: 'orange',
  active: 'orange',
}

function displayToolName(name: string): string {
  return TOOL_DISPLAY_NAMES[name] || name
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] || status || '未知'
}

function statusColor(status: string): string {
  return STATUS_COLORS[status] || 'default'
}

interface OpsReportData {
  generated_at: string
  time_range: string
  summary: string
  sections: {
    sessions: { title: string; count: number; items: any[] }
    messages: { title: string; user_messages: number; agent_responses: number }
    tool_calls: { title: string; total: number; by_tool: Record<string, number> }
    security: { title: string; blocks: number; details: string[] }
    approvals: { title: string; total_requests: number; approved: number; rejected: number }
    knowledge: { title: string; count: number; items: any[] }
    runbooks: { title: string; count: number; items: any[] }
    incidents?: { title: string; count: number; by_status: Record<string, number>; items: any[] }
    multimodal_evidence?: { title: string; count: number; images: number; audio: number; items: any[] }
  }
}

interface IncidentDraft {
  type: 'handoff' | 'postmortem'
  incidentId: string
  markdown: string
}

/**
 * Operations report page - on-demand summary of recent operations.
 */
function OpsReport() {
  const [report, setReport] = useState<OpsReportData | null>(null)
  const [loading, setLoading] = useState(false)
  const [hours, setHours] = useState(24)
  const [draft, setDraft] = useState<IncidentDraft | null>(null)
  const [draftLoading, setDraftLoading] = useState(false)

  const generateReport = async () => {
    setLoading(true)
    try {
      const res = await fetch(`/api/ops-report/generate?hours=${hours}`)
      if (res.ok) {
        setReport(await res.json())
      }
    } catch (err) {
      console.error('Failed to generate report:', err)
    } finally {
      setLoading(false)
    }
  }

  const openIncidentDraft = async (incidentId: string, type: 'handoff' | 'postmortem') => {
    setDraftLoading(true)
    try {
      const res = await fetch(`/api/incidents/${incidentId}/${type}`)
      if (res.ok) {
        const data = await res.json()
        setDraft({ type, incidentId, markdown: data.markdown || '' })
      }
    } catch (err) {
      console.error('Failed to load incident draft:', err)
    } finally {
      setDraftLoading(false)
    }
  }

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ color: 'var(--text-primary)', margin: 0 }}>
          <FileTextOutlined style={{ marginRight: 8, color: 'var(--accent-green)' }} />
          运维日报
        </Title>
        <Space>
          <Select
            value={hours}
            onChange={setHours}
            style={{ width: 120 }}
            options={[
              { value: 1, label: '最近 1 小时' },
              { value: 6, label: '最近 6 小时' },
              { value: 12, label: '最近 12 小时' },
              { value: 24, label: '最近 24 小时' },
              { value: 72, label: '最近 3 天' },
            ]}
          />
          {report && (
            <Button icon={<FilePdfOutlined />} onClick={() => window.open(`/api/ops-report/export-pdf?hours=${hours}`, '_blank')}>
              导出 PDF
            </Button>
          )}
          <Button type="primary" icon={<ReloadOutlined />} onClick={generateReport} loading={loading}>
            生成报告
          </Button>
        </Space>
      </div>

      {/* Loading */}
      {loading && (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 200 }}>
          <Spin tip="正在汇总运维数据..." size="large" />
        </div>
      )}

      {/* Empty */}
      {!loading && !report && (
        <Card style={{ textAlign: 'center', padding: 40, background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}>
          <FileTextOutlined style={{ fontSize: 48, color: 'var(--text-muted)', marginBottom: 16 }} />
          <Paragraph style={{ color: 'var(--text-secondary)' }}>
            选择时间范围后点击"生成报告"，汇总该时段内的所有运维操作。
          </Paragraph>
        </Card>
      )}

      {/* Report */}
      {report && !loading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Stats row */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
            <Card size="small" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', textAlign: 'center' }}>
              <Statistic
                title={<Text style={{ color: 'var(--text-muted)', fontSize: 11 }}>会话数</Text>}
                value={report.sections.sessions?.count || 0}
                prefix={<MessageOutlined style={{ color: 'var(--accent-blue)' }} />}
                valueStyle={{ fontSize: 24, color: 'var(--text-primary)' }}
              />
            </Card>
            <Card size="small" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', textAlign: 'center' }}>
              <Statistic
                title={<Text style={{ color: 'var(--text-muted)', fontSize: 11 }}>工具调用</Text>}
                value={report.sections.tool_calls?.total || 0}
                prefix={<ToolOutlined style={{ color: 'var(--accent-green)' }} />}
                valueStyle={{ fontSize: 24, color: 'var(--text-primary)' }}
              />
            </Card>
            <Card size="small" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', textAlign: 'center' }}>
              <Statistic
                title={<Text style={{ color: 'var(--text-muted)', fontSize: 11 }}>安全拦截</Text>}
                value={report.sections.security?.blocks || 0}
                prefix={<SafetyOutlined style={{ color: 'var(--accent-red)' }} />}
                valueStyle={{ fontSize: 24, color: 'var(--text-primary)' }}
              />
            </Card>
            <Card size="small" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', textAlign: 'center' }}>
              <Statistic
                title={<Text style={{ color: 'var(--text-muted)', fontSize: 11 }}>审批通过</Text>}
                value={report.sections.approvals?.approved || 0}
                prefix={<CheckCircleOutlined style={{ color: 'var(--accent-green)' }} />}
                valueStyle={{ fontSize: 24, color: 'var(--text-primary)' }}
              />
            </Card>
            <Card size="small" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', textAlign: 'center' }}>
              <Statistic
                title={<Text style={{ color: 'var(--text-muted)', fontSize: 11 }}>新增知识</Text>}
                value={report.sections.knowledge?.count || 0}
                prefix={<BulbOutlined style={{ color: 'var(--accent-yellow)' }} />}
                valueStyle={{ fontSize: 24, color: 'var(--text-primary)' }}
              />
            </Card>
            <Card size="small" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', textAlign: 'center' }}>
              <Statistic
                title={<Text style={{ color: 'var(--text-muted)', fontSize: 11 }}>新增 Runbook</Text>}
                value={report.sections.runbooks?.count || 0}
                prefix={<PlayCircleOutlined style={{ color: 'var(--accent-purple)' }} />}
                valueStyle={{ fontSize: 24, color: 'var(--text-primary)' }}
              />
            </Card>
            <Card size="small" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', textAlign: 'center' }}>
              <Statistic
                title={<Text style={{ color: 'var(--text-muted)', fontSize: 11 }}>事件</Text>}
                value={report.sections.incidents?.count || 0}
                suffix={report.sections.incidents?.by_status?.failed ? ` / ${report.sections.incidents.by_status.failed} 失败` : ''}
                prefix={<AlertOutlined style={{ color: 'var(--accent-yellow)' }} />}
                valueStyle={{ fontSize: 24, color: 'var(--text-primary)' }}
              />
            </Card>
            <Card size="small" style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', textAlign: 'center' }}>
              <Statistic
                title={<Text style={{ color: 'var(--text-muted)', fontSize: 11 }}>多模态证据</Text>}
                value={report.sections.multimodal_evidence?.count || 0}
                suffix={(report.sections.multimodal_evidence?.count || 0) > 0 ? ` / ${report.sections.multimodal_evidence?.images || 0} 图` : ''}
                prefix={<FileTextOutlined style={{ color: 'var(--accent-blue)' }} />}
                valueStyle={{ fontSize: 24, color: 'var(--text-primary)' }}
              />
            </Card>
          </div>

          {/* Tool usage breakdown */}
          {report.sections.tool_calls?.total > 0 && (
            <Card
              size="small"
              title={<><ToolOutlined style={{ marginRight: 6 }} />工具调用分布</>}
              style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
            >
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {Object.entries(report.sections.tool_calls.by_tool).map(([name, count]) => (
                  <Tag key={name} style={{ fontSize: 11 }}>
                    {displayToolName(name)} <span style={{ color: 'var(--accent-green)', marginLeft: 4 }}>{count}</span>
                  </Tag>
                ))}
              </div>
            </Card>
          )}

          {/* Security events */}
          {report.sections.security?.blocks > 0 && (
            <Card
              size="small"
              title={<><SafetyOutlined style={{ marginRight: 6, color: 'var(--accent-red)' }} />安全拦截详情</>}
              style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
            >
              {report.sections.security.details.map((detail, i) => (
                <Text key={i} style={{ display: 'block', fontSize: 12, color: 'var(--accent-yellow)', marginBottom: 4 }}>
                  {detail}
                </Text>
              ))}
            </Card>
          )}

          {/* Multimodal evidence */}
          {(report.sections.multimodal_evidence?.items?.length || 0) > 0 && (
            <Card
              size="small"
              title={<><FileTextOutlined style={{ marginRight: 6, color: 'var(--accent-blue)' }} />多模态证据</>}
              style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
            >
              {report.sections.multimodal_evidence?.items.map((item: any, index: number) => (
                <div key={`${item.incident_id}-${item.timestamp}-${index}`} style={{ padding: '8px 0', borderBottom: '1px solid var(--border-color)' }}>
                  <Space size={6} wrap>
                    <Tag color={item.input_type === 'audio' ? 'blue' : 'geekblue'}>
                      {item.input_type === 'audio' ? '语音识别' : '图片识别'}
                    </Tag>
                    {item.confidence && <Tag color={item.confidence === 'low' ? 'orange' : 'green'}>置信度 {item.confidence}</Tag>}
                    <Text style={{ fontSize: 11, color: 'var(--text-muted)' }}>{item.incident_id}</Text>
                  </Space>
                  <Text style={{ display: 'block', marginTop: 4, fontSize: 12, color: 'var(--text-primary)' }}>
                    {item.summary || item.recognized_text || '已记录识别结果'}
                  </Text>
                  {item.entities && Object.keys(item.entities).length > 0 && (
                    <Text style={{ display: 'block', marginTop: 2, fontSize: 11, color: 'var(--text-muted)' }}>
                      实体：{Object.entries(item.entities)
                        .filter(([, value]) => Array.isArray(value) && value.length > 0)
                        .slice(0, 4)
                        .map(([key, value]) => `${key}=${(value as any[]).slice(0, 3).join(',')}`)
                        .join('；') || '无'}
                    </Text>
                  )}
                  {(item.verification?.length || 0) > 0 ? (
                    <div style={{ marginTop: 6 }}>
                      <Text style={{ fontSize: 11, color: 'var(--accent-green)' }}>真实工具验证：</Text>
                      {item.verification.slice(0, 3).map((verify: any, verifyIndex: number) => (
                        <Text key={verifyIndex} style={{ display: 'block', fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>
                          {verify.execution_state === 'failed' ? '失败' : '已执行'}：{verify.source || verify.title}
                        </Text>
                      ))}
                    </div>
                  ) : (
                    <Text style={{ display: 'block', marginTop: 6, fontSize: 11, color: 'var(--accent-yellow)' }}>
                      尚未找到真实工具验证结果，不能仅凭识别内容下结论
                    </Text>
                  )}
                </div>
              ))}
            </Card>
          )}

          {/* Incident drafts */}
          {(report.sections.incidents?.items?.length || 0) > 0 && (
            <Card
              size="small"
              title={<><AlertOutlined style={{ marginRight: 6, color: 'var(--accent-yellow)' }} />事件草稿</>}
              style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
            >
              {report.sections.incidents?.items.map((incident: any) => (
                <div key={incident.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '8px 0', borderBottom: '1px solid var(--border-color)', alignItems: 'center' }}>
                  <div style={{ minWidth: 0 }}>
                    <Space size={6} wrap>
                      <Tag color={statusColor(incident.status)}>
                        {statusLabel(incident.status)}
                      </Tag>
                      <Text style={{ fontSize: 12, color: 'var(--text-primary)' }}>
                        {incident.problem_statement || incident.id}
                      </Text>
                    </Space>
                    <Text style={{ display: 'block', fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>
                      {incident.id}
                    </Text>
                  </div>
                  <Space>
                    <Button size="small" icon={<FileTextOutlined />} loading={draftLoading} onClick={() => openIncidentDraft(incident.id, 'handoff')}>
                      交接
                    </Button>
                    <Button size="small" icon={<FileTextOutlined />} loading={draftLoading} onClick={() => openIncidentDraft(incident.id, 'postmortem')}>
                      复盘
                    </Button>
                  </Space>
                </div>
              ))}
            </Card>
          )}

          {/* Sessions list */}
          {report.sections.sessions?.count > 0 && (
            <Card
              size="small"
              title={<><MessageOutlined style={{ marginRight: 6 }} />会话记录</>}
              style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)' }}
            >
              {report.sections.sessions.items.map((session: any) => (
                <div key={session.id} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid var(--border-color)' }}>
                  <Text style={{ fontSize: 12 }}>{session.title || '未命名会话'}</Text>
                  <Text style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    {new Date(session.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                  </Text>
                </div>
              ))}
            </Card>
          )}

          {/* Summary text */}
          <Card size="small" style={{ background: 'var(--bg-primary)', border: '1px solid var(--border-color)' }}>
            <pre style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-secondary)', margin: 0, whiteSpace: 'pre-wrap' }}>
              {report.summary}
            </pre>
          </Card>
        </div>
      )}
      <Modal
        open={!!draft}
        title={draft ? `${draft.type === 'handoff' ? '事件交接' : '事件复盘草稿'} - ${draft.incidentId}` : '事件草稿'}
        onCancel={() => setDraft(null)}
        footer={[
          <Button key="close" onClick={() => setDraft(null)}>关闭</Button>,
          <Button
            key="copy"
            type="primary"
            onClick={() => draft && navigator.clipboard?.writeText(draft.markdown)}
          >
            复制 Markdown
          </Button>,
        ]}
        width={900}
      >
        <pre style={{ maxHeight: 520, overflow: 'auto', whiteSpace: 'pre-wrap', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
          {draft?.markdown}
        </pre>
      </Modal>
    </div>
  )
}

export default OpsReport
