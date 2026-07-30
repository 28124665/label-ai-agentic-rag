'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ArrowLeft, Database, Wifi, WifiOff, Calendar, User, Server } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { TableList } from '@/components/datasource/table-list';
import { SQLEditor } from '@/components/datasource/sql-editor';
import { dataSourceService, DataSource, TableInfo } from '@/services/datasource.service';
import { formatDate } from '@/lib/utils';
import { cn } from '@/lib/utils';

const dbTypeLabels: Record<string, string> = {
  mysql: 'MySQL',
  postgresql: 'PostgreSQL',
  postgres: 'PostgreSQL',
  sqlite: 'SQLite',
  mssql: 'SQL Server',
  oracle: 'Oracle',
};

export default function DataSourceDetailPage() {
  const params = useParams();
  const router = useRouter();
  const datasourceId = params.id as string;

  const [datasource, setDatasource] = useState<DataSource | null>(null);
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingTables, setLoadingTables] = useState(false);

  useEffect(() => {
    loadDatasource();
    loadTables();
  }, [datasourceId]);

  const loadDatasource = async () => {
    try {
      const data = await dataSourceService.get(datasourceId);
      setDatasource(data);
    } catch (error) {
      console.error('Failed to load datasource:', error);
      router.push('/datasource');
    } finally {
      setLoading(false);
    }
  };

  const loadTables = async () => {
    setLoadingTables(true);
    try {
      const data = await dataSourceService.listTables(datasourceId);
      setTables(data);
    } catch (error) {
      console.error('Failed to load tables:', error);
    } finally {
      setLoadingTables(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-gray-400">加载中...</div>
      </div>
    );
  }

  if (!datasource) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-gray-400">数据源不存在</div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-4 mb-6">
        <Button variant="ghost" size="icon" onClick={() => router.push('/datasource')}>
          <ArrowLeft className="h-5 w-5" />
        </Button>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <Database className="h-6 w-6 text-blue-500" />
            <h1 className="text-2xl font-bold text-gray-900">{datasource.name}</h1>
            <span
              className={cn(
                'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium',
                datasource.is_active
                  ? 'bg-green-50 text-green-700'
                  : 'bg-gray-100 text-gray-500'
              )}
            >
              {datasource.is_active ? (
                <>
                  <Wifi className="h-3 w-3" />
                  已连接
                </>
              ) : (
                <>
                  <WifiOff className="h-3 w-3" />
                  未连接
                </>
              )}
            </span>
          </div>
          {datasource.description && (
            <p className="text-sm text-gray-500 mt-1">{datasource.description}</p>
          )}
        </div>
      </div>

      {/* Info Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-gray-500 flex items-center gap-2">
              <Database className="h-4 w-4" />
              数据库类型
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold">
              {dbTypeLabels[datasource.db_type] || datasource.db_type}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-gray-500 flex items-center gap-2">
              <Server className="h-4 w-4" />
              主机地址
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold truncate">
              {datasource.host}:{datasource.port}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-gray-500 flex items-center gap-2">
              <Database className="h-4 w-4" />
              数据库名
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold truncate">{datasource.database}</div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-gray-500 flex items-center gap-2">
              <User className="h-4 w-4" />
              用户名
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold truncate">{datasource.username}</div>
          </CardContent>
        </Card>
      </div>

      {/* Main Content */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: Table List */}
        <div>
          <TableList
            datasourceId={datasourceId}
            tables={tables}
            loading={loadingTables}
          />
        </div>

        {/* Right: SQL Editor */}
        <div>
          <SQLEditor datasourceId={datasourceId} />
        </div>
      </div>

      {/* Metadata */}
      <div className="mt-6 text-xs text-gray-400 flex items-center gap-4">
        <span className="flex items-center gap-1">
          <Calendar className="h-3 w-3" />
          创建于 {formatDate(datasource.created_at)}
        </span>
        {datasource.last_tested_at && (
          <span>
            最后测试: {formatDate(datasource.last_tested_at)}
          </span>
        )}
      </div>
    </div>
  );
}
