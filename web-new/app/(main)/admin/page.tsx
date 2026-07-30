'use client';

import { useState } from 'react';
import { Users, FileText } from 'lucide-react';
import { cn } from '@/lib/utils';
import { UserList } from '@/components/admin/user-list';
import { AuditLogList } from '@/components/admin/audit-log';

type TabType = 'users' | 'audit-logs';

export default function AdminPage() {
  const [activeTab, setActiveTab] = useState<TabType>('users');

  const tabs = [
    { id: 'users' as TabType, label: '用户列表', icon: Users },
    { id: 'audit-logs' as TabType, label: '审计日志', icon: FileText },
  ];

  return (
    <div className="p-6 space-y-6">
      {/* 页面标题 */}
      <div>
        <h1 className="text-2xl font-bold">管理后台</h1>
        <p className="text-gray-500 text-sm mt-1">管理系统用户和查看操作日志</p>
      </div>

      {/* Tab 切换 */}
      <div className="border-b">
        <nav className="flex gap-6">
          {tabs.map((tab) => {
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={cn(
                  'flex items-center gap-2 px-1 py-3 text-sm font-medium border-b-2 transition-colors',
                  isActive
                    ? 'border-primary text-primary'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                )}
              >
                <tab.icon className="h-4 w-4" />
                {tab.label}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Tab 内容 */}
      <div>
        {activeTab === 'users' && <UserList />}
        {activeTab === 'audit-logs' && <AuditLogList />}
      </div>
    </div>
  );
}
