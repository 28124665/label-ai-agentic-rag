'use client';

import { useEffect, useState, useCallback } from 'react';
import { Plus, Loader2, Database, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { DataSourceCard } from '@/components/datasource/datasource-card';
import { dataSourceService, DataSource } from '@/services/datasource.service';
import { useToast } from '@/components/ui/use-toast';

const DB_TYPES = [
  { value: 'mysql', label: 'MySQL', defaultPort: 3306 },
  { value: 'postgresql', label: 'PostgreSQL', defaultPort: 5432 },
  { value: 'sqlite', label: 'SQLite', defaultPort: 0 },
  { value: 'mssql', label: 'SQL Server', defaultPort: 1433 },
  { value: 'oracle', label: 'Oracle', defaultPort: 1521 },
];

interface FormState {
  name: string;
  description: string;
  db_type: string;
  host: string;
  port: string;
  database: string;
  username: string;
  password: string;
}

const initialForm: FormState = {
  name: '',
  description: '',
  db_type: 'mysql',
  host: '',
  port: '3306',
  database: '',
  username: '',
  password: '',
};

export default function DataSourcePage() {
  const { toast } = useToast();
  const [datasources, setDatasources] = useState<DataSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<FormState>(initialForm);

  const loadDatasources = useCallback(async () => {
    try {
      const data = await dataSourceService.list(1, 100);
      setDatasources(data.items);
    } catch (error) {
      console.error('Failed to load datasources:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDatasources();
  }, [loadDatasources]);

  const handleCreate = async () => {
    if (!form.name.trim() || !form.host.trim() || !form.database.trim() || !form.username.trim() || !form.password.trim()) {
      toast({ title: '请填写必填字段', variant: 'destructive' });
      return;
    }

    setCreating(true);
    try {
      await dataSourceService.create({
        name: form.name.trim(),
        description: form.description.trim() || undefined,
        db_type: form.db_type,
        host: form.host.trim(),
        port: parseInt(form.port) || 0,
        database: form.database.trim(),
        username: form.username.trim(),
        password: form.password,
      });
      toast({ title: '数据源创建成功' });
      setShowCreate(false);
      setForm(initialForm);
      loadDatasources();
    } catch (error: any) {
      toast({ title: '创建失败', description: error.message || '未知错误', variant: 'destructive' });
    } finally {
      setCreating(false);
    }
  };

  const handleDbTypeChange = (dbType: string) => {
    const typeInfo = DB_TYPES.find(t => t.value === dbType);
    setForm(prev => ({
      ...prev,
      db_type: dbType,
      port: typeInfo ? String(typeInfo.defaultPort) : prev.port,
    }));
  };

  return (
    <div className="p-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">数据源管理</h1>
          <p className="text-sm text-gray-500 mt-1">管理数据库连接，查看表结构和执行 SQL 查询</p>
        </div>
        <Button onClick={() => setShowCreate(true)}>
          <Plus className="h-4 w-4 mr-2" />
          新建数据源
        </Button>
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex items-center justify-center py-20 text-gray-400">
          <Loader2 className="h-5 w-5 animate-spin mr-2" />
          加载中...
        </div>
      ) : datasources.length === 0 ? (
        <div className="text-center py-20">
          <Database className="h-12 w-12 text-gray-300 mx-auto mb-4" />
          <div className="text-lg font-medium text-gray-500 mb-2">暂无数据源</div>
          <div className="text-sm text-gray-400 mb-4">添加一个数据源开始使用</div>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4 mr-2" />
            新建数据源
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {datasources.map((ds) => (
            <DataSourceCard key={ds.id} datasource={ds} onRefresh={loadDatasources} />
          ))}
        </div>
      )}

      {/* Create Dialog */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="fixed inset-0 bg-black/50" onClick={() => !creating && setShowCreate(false)} />
          <div className="relative bg-white rounded-lg shadow-xl w-full max-w-lg mx-4 max-h-[90vh] overflow-auto">
            <div className="flex items-center justify-between p-4 border-b">
              <h2 className="text-lg font-semibold">新建数据源</h2>
              <Button variant="ghost" size="icon" onClick={() => setShowCreate(false)} disabled={creating}>
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="p-4 space-y-4">
              <div className="space-y-2">
                <Label htmlFor="ds-name">名称 *</Label>
                <Input
                  id="ds-name"
                  placeholder="例如：生产环境 MySQL"
                  value={form.name}
                  onChange={(e) => setForm(prev => ({ ...prev, name: e.target.value }))}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="ds-desc">描述</Label>
                <Input
                  id="ds-desc"
                  placeholder="可选描述"
                  value={form.description}
                  onChange={(e) => setForm(prev => ({ ...prev, description: e.target.value }))}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="db-type">数据库类型 *</Label>
                <select
                  id="db-type"
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                  value={form.db_type}
                  onChange={(e) => handleDbTypeChange(e.target.value)}
                >
                  {DB_TYPES.map(t => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div className="col-span-2 space-y-2">
                  <Label htmlFor="ds-host">主机 *</Label>
                  <Input
                    id="ds-host"
                    placeholder="localhost 或 IP"
                    value={form.host}
                    onChange={(e) => setForm(prev => ({ ...prev, host: e.target.value }))}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="ds-port">端口 *</Label>
                  <Input
                    id="ds-port"
                    type="number"
                    placeholder="3306"
                    value={form.port}
                    onChange={(e) => setForm(prev => ({ ...prev, port: e.target.value }))}
                  />
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="ds-database">数据库名 *</Label>
                <Input
                  id="ds-database"
                  placeholder="数据库名称"
                  value={form.database}
                  onChange={(e) => setForm(prev => ({ ...prev, database: e.target.value }))}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-2">
                  <Label htmlFor="ds-user">用户名 *</Label>
                  <Input
                    id="ds-user"
                    placeholder="root"
                    value={form.username}
                    onChange={(e) => setForm(prev => ({ ...prev, username: e.target.value }))}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="ds-pass">密码 *</Label>
                  <Input
                    id="ds-pass"
                    type="password"
                    placeholder="密码"
                    value={form.password}
                    onChange={(e) => setForm(prev => ({ ...prev, password: e.target.value }))}
                  />
                </div>
              </div>
            </div>
            <div className="flex justify-end gap-2 p-4 border-t">
              <Button variant="outline" onClick={() => setShowCreate(false)} disabled={creating}>
                取消
              </Button>
              <Button onClick={handleCreate} disabled={creating}>
                {creating ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    创建中...
                  </>
                ) : (
                  '创建'
                )}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
