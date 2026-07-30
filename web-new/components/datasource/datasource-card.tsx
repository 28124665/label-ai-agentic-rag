'use client';

import { useRouter } from 'next/navigation';
import { Database, Wifi, WifiOff, Loader2, CheckCircle2, XCircle } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, CardFooter } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { DataSource, dataSourceService } from '@/services/datasource.service';
import { useToast } from '@/components/ui/use-toast';
import { useState } from 'react';
import { cn } from '@/lib/utils';

interface DataSourceCardProps {
  datasource: DataSource;
  onRefresh?: () => void;
}

const dbTypeLabels: Record<string, string> = {
  mysql: 'MySQL',
  postgresql: 'PostgreSQL',
  postgres: 'PostgreSQL',
  sqlite: 'SQLite',
  mssql: 'SQL Server',
  oracle: 'Oracle',
};

export function DataSourceCard({ datasource, onRefresh }: DataSourceCardProps) {
  const router = useRouter();
  const { toast } = useToast();
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  const handleTestConnection = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setTesting(true);
    setTestResult(null);
    try {
      const result = await dataSourceService.testConnection(datasource.id);
      setTestResult({ success: result.success, message: result.message });
      if (result.success) {
        toast({ title: '连接成功', description: `响应时间: ${result.execution_time_ms}ms` });
      } else {
        toast({ title: '连接失败', description: result.message, variant: 'destructive' });
      }
      onRefresh?.();
    } catch (error: any) {
      setTestResult({ success: false, message: error.message || '连接失败' });
      toast({ title: '连接失败', description: error.message || '测试连接出错', variant: 'destructive' });
    } finally {
      setTesting(false);
    }
  };

  return (
    <Card
      className="cursor-pointer hover:shadow-md transition-shadow"
      onClick={() => router.push(`/datasource/${datasource.id}`)}
    >
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Database className="h-5 w-5 text-blue-500" />
            <CardTitle className="text-base">{datasource.name}</CardTitle>
          </div>
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
      </CardHeader>
      <CardContent className="pb-3">
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div>
            <span className="text-gray-500">类型</span>
            <div className="font-medium">{dbTypeLabels[datasource.db_type] || datasource.db_type}</div>
          </div>
          <div>
            <span className="text-gray-500">主机</span>
            <div className="font-medium truncate">{datasource.host}:{datasource.port}</div>
          </div>
          <div>
            <span className="text-gray-500">数据库</span>
            <div className="font-medium truncate">{datasource.database}</div>
          </div>
          <div>
            <span className="text-gray-500">用户</span>
            <div className="font-medium truncate">{datasource.username}</div>
          </div>
        </div>
        {testResult && (
          <div
            className={cn(
              'mt-3 flex items-center gap-1.5 text-xs',
              testResult.success ? 'text-green-600' : 'text-red-600'
            )}
          >
            {testResult.success ? (
              <CheckCircle2 className="h-3.5 w-3.5" />
            ) : (
              <XCircle className="h-3.5 w-3.5" />
            )}
            {testResult.message}
          </div>
        )}
      </CardContent>
      <CardFooter className="pt-0">
        <Button
          variant="outline"
          size="sm"
          onClick={handleTestConnection}
          disabled={testing}
        >
          {testing ? (
            <>
              <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
              测试中...
            </>
          ) : (
            <>
              <Wifi className="h-3.5 w-3.5 mr-1.5" />
              测试连接
            </>
          )}
        </Button>
      </CardFooter>
    </Card>
  );
}
