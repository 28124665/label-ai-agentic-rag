'use client';

import { useEffect, useState } from 'react';
import { Search, Loader2, Eye, Calendar } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Modal } from '@/components/ui/modal';
import { adminService, type AuditLog, ActionTypes, ResourceTypes } from '@/services/admin.service';
import { cn } from '@/lib/utils';

export function AuditLogList() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionFilter, setActionFilter] = useState<string>('');
  const [resourceFilter, setResourceFilter] = useState<string>('');
  const [startTime, setStartTime] = useState('');
  const [endTime, setEndTime] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [detailDialogOpen, setDetailDialogOpen] = useState(false);
  const [selectedLog, setSelectedLog] = useState<AuditLog | null>(null);

  useEffect(() => {
    loadLogs();
  }, [currentPage, actionFilter, resourceFilter, startTime, endTime]);

  const loadLogs = async () => {
    setLoading(true);
    try {
      const data = await adminService.getAuditLogs({
        page: currentPage,
        size: 20,
        action: actionFilter || undefined,
        resource_type: resourceFilter || undefined,
        start_time: startTime || undefined,
        end_time: endTime || undefined,
      });
      setLogs(data.items);
      setTotalPages(data.pages);
    } catch (error) {
      console.error('加载审计日志失败:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleViewDetail = (log: AuditLog) => {
    setSelectedLog(log);
    setDetailDialogOpen(true);
  };

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  };

  const getActionBadge = (action: string) => {
    const actionConfig = {
      CREATE: { label: '创建', className: 'bg-green-100 text-green-700' },
      UPDATE: { label: '更新', className: 'bg-blue-100 text-blue-700' },
      DELETE: { label: '删除', className: 'bg-red-100 text-red-700' },
      LOGIN: { label: '登录', className: 'bg-purple-100 text-purple-700' },
      LOGOUT: { label: '登出', className: 'bg-gray-100 text-gray-700' },
      EXPORT: { label: '导出', className: 'bg-yellow-100 text-yellow-700' },
      IMPORT: { label: '导入', className: 'bg-indigo-100 text-indigo-700' },
    };
    const config = actionConfig[action as keyof typeof actionConfig] || {
      label: action,
      className: 'bg-gray-100 text-gray-700',
    };
    return (
      <span className={cn('px-2 py-1 rounded-full text-xs font-medium', config.className)}>
        {config.label}
      </span>
    );
  };

  const getResourceBadge = (resourceType: string) => {
    const resourceConfig = {
      USER: { label: '用户', className: 'bg-blue-50 text-blue-600' },
      KNOWLEDGE: { label: '知识库', className: 'bg-green-50 text-green-600' },
      DATASOURCE: { label: '数据源', className: 'bg-purple-50 text-purple-600' },
      AGENT: { label: 'Agent', className: 'bg-orange-50 text-orange-600' },
      CONVERSATION: { label: '对话', className: 'bg-pink-50 text-pink-600' },
      DOCUMENT: { label: '文档', className: 'bg-yellow-50 text-yellow-600' },
    };
    const config = resourceConfig[resourceType as keyof typeof resourceConfig] || {
      label: resourceType,
      className: 'bg-gray-50 text-gray-600',
    };
    return (
      <span className={cn('px-2 py-1 rounded text-xs font-medium', config.className)}>
        {config.label}
      </span>
    );
  };

  return (
    <div className="space-y-4">
      {/* 筛选栏 */}
      <div className="flex flex-wrap gap-3">
        <select
          value={actionFilter}
          onChange={(e) => {
            setActionFilter(e.target.value);
            setCurrentPage(1);
          }}
          className="h-10 px-3 rounded-md border border-input bg-background text-sm"
        >
          <option value="">全部操作</option>
          <option value={ActionTypes.CREATE}>创建</option>
          <option value={ActionTypes.UPDATE}>更新</option>
          <option value={ActionTypes.DELETE}>删除</option>
          <option value={ActionTypes.LOGIN}>登录</option>
          <option value={ActionTypes.LOGOUT}>登出</option>
          <option value={ActionTypes.EXPORT}>导出</option>
          <option value={ActionTypes.IMPORT}>导入</option>
        </select>
        <select
          value={resourceFilter}
          onChange={(e) => {
            setResourceFilter(e.target.value);
            setCurrentPage(1);
          }}
          className="h-10 px-3 rounded-md border border-input bg-background text-sm"
        >
          <option value="">全部资源</option>
          <option value={ResourceTypes.USER}>用户</option>
          <option value={ResourceTypes.KNOWLEDGE}>知识库</option>
          <option value={ResourceTypes.DATASOURCE}>数据源</option>
          <option value={ResourceTypes.AGENT}>Agent</option>
          <option value={ResourceTypes.CONVERSATION}>对话</option>
          <option value={ResourceTypes.DOCUMENT}>文档</option>
        </select>
        <div className="flex items-center gap-2">
          <Calendar className="h-4 w-4 text-gray-400" />
          <Input
            type="date"
            value={startTime}
            onChange={(e) => {
              setStartTime(e.target.value);
              setCurrentPage(1);
            }}
            className="w-40"
            placeholder="开始日期"
          />
          <span className="text-gray-400">-</span>
          <Input
            type="date"
            value={endTime}
            onChange={(e) => {
              setEndTime(e.target.value);
              setCurrentPage(1);
            }}
            className="w-40"
            placeholder="结束日期"
          />
        </div>
      </div>

      {/* 日志列表 */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
          <span className="ml-2 text-gray-500">加载中...</span>
        </div>
      ) : logs.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-gray-400">
          <div className="text-6xl mb-4">📋</div>
          <div className="text-lg font-medium mb-2">暂无日志数据</div>
          <div className="text-sm">尝试使用不同的筛选条件</div>
        </div>
      ) : (
        <div className="border rounded-lg overflow-hidden">
          <table className="w-full">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-700">用户</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-700">操作</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-700">资源</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-700">资源名称</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-700">IP 地址</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-700">时间</th>
                <th className="text-right px-4 py-3 text-sm font-medium text-gray-700">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {logs.map((log) => (
                <tr key={log.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 text-sm">{log.username}</td>
                  <td className="px-4 py-3">{getActionBadge(log.action)}</td>
                  <td className="px-4 py-3">{getResourceBadge(log.resource_type)}</td>
                  <td className="px-4 py-3 text-sm text-gray-600">
                    {log.resource_name || '-'}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-600 font-mono">
                    {log.ip_address}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-600">
                    {formatDate(log.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleViewDetail(log)}
                    >
                      <Eye className="h-4 w-4 mr-1" />
                      详情
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <div className="text-sm text-gray-500">
            第 {currentPage} 页，共 {totalPages} 页
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={currentPage === 1}
              onClick={() => setCurrentPage((p) => p - 1)}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={currentPage === totalPages}
              onClick={() => setCurrentPage((p) => p + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      )}

      {/* 详情弹窗 */}
      <Modal
        open={detailDialogOpen}
        onClose={() => setDetailDialogOpen(false)}
        title="日志详情"
        footer={
          <Button variant="outline" onClick={() => setDetailDialogOpen(false)}>
            关闭
          </Button>
        }
      >
        {selectedLog && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <div className="text-sm text-gray-500 mb-1">用户</div>
                <div className="text-sm font-medium">{selectedLog.username}</div>
              </div>
              <div>
                <div className="text-sm text-gray-500 mb-1">操作</div>
                <div>{getActionBadge(selectedLog.action)}</div>
              </div>
              <div>
                <div className="text-sm text-gray-500 mb-1">资源类型</div>
                <div>{getResourceBadge(selectedLog.resource_type)}</div>
              </div>
              <div>
                <div className="text-sm text-gray-500 mb-1">资源名称</div>
                <div className="text-sm font-medium">{selectedLog.resource_name || '-'}</div>
              </div>
              <div>
                <div className="text-sm text-gray-500 mb-1">IP 地址</div>
                <div className="text-sm font-mono">{selectedLog.ip_address}</div>
              </div>
              <div>
                <div className="text-sm text-gray-500 mb-1">时间</div>
                <div className="text-sm">{formatDate(selectedLog.created_at)}</div>
              </div>
            </div>
            {selectedLog.user_agent && (
              <div>
                <div className="text-sm text-gray-500 mb-1">User Agent</div>
                <div className="text-xs font-mono bg-gray-50 p-2 rounded break-all">
                  {selectedLog.user_agent}
                </div>
              </div>
            )}
            {selectedLog.details && Object.keys(selectedLog.details).length > 0 && (
              <div>
                <div className="text-sm text-gray-500 mb-1">详细信息</div>
                <pre className="text-xs font-mono bg-gray-50 p-3 rounded overflow-auto max-h-60">
                  {JSON.stringify(selectedLog.details, null, 2)}
                </pre>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
