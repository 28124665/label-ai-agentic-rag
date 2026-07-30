'use client';

import { useEffect, useState } from 'react';
import { Activity, TrendingUp, Clock, AlertTriangle, Zap } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Loading } from '@/components/ui/loading';
import { monitorService, MonitorMetrics, ToolCallStats } from '@/services/monitor.service';

export function Dashboard() {
  const [metrics, setMetrics] = useState<MonitorMetrics | null>(null);
  const [toolStats, setToolStats] = useState<ToolCallStats[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date());

  useEffect(() => {
    loadData();
    // 每 30 秒自动刷新
    const interval = setInterval(loadData, 30000);
    return () => clearInterval(interval);
  }, []);

  const loadData = async () => {
    try {
      const [metricsData, toolStatsData] = await Promise.all([
        monitorService.getMetrics(),
        monitorService.getToolCallStats('24h'),
      ]);
      setMetrics(metricsData);
      setToolStats(toolStatsData);
      setLastUpdate(new Date());
    } catch (error) {
      console.error('Failed to load dashboard data:', error);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[200px]">
        <Loading text="加载监控数据..." />
      </div>
    );
  }

  // 计算幻觉率（1 - 忠实度）
  const hallucinationRate = metrics ? (1 - metrics.faithfulness_score) * 100 : 0;

  // 工具调用统计
  const toolUsage = toolStats.map((t) => ({
    name: t.tool_name,
    count: t.call_count,
    color: getToolColor(t.tool_name),
  }));

  return (
    <div className="space-y-4">
      {/* 更新时间 */}
      <div className="flex items-center justify-between text-xs text-gray-500">
        <span>自动刷新：每 30 秒</span>
        <span>最后更新：{lastUpdate.toLocaleTimeString('zh-CN')}</span>
      </div>

      {/* 关键指标卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <MetricCard
          title="总查询数"
          value={metrics?.total_requests.toLocaleString() || '0'}
          icon={<Activity className="h-4 w-4 text-blue-500" />}
          trend="+12%"
          trendColor="text-emerald-600"
        />
        <MetricCard
          title="成功率"
          value={`${((metrics?.success_rate || 0) * 100).toFixed(1)}%`}
          icon={<TrendingUp className="h-4 w-4 text-emerald-500" />}
          trend="+2.3%"
          trendColor="text-emerald-600"
        />
        <MetricCard
          title="平均延迟"
          value={`${(metrics?.avg_response_time || 0).toFixed(0)}ms`}
          icon={<Clock className="h-4 w-4 text-amber-500" />}
          trend="-15%"
          trendColor="text-emerald-600"
        />
        <MetricCard
          title="幻觉率"
          value={`${hallucinationRate.toFixed(1)}%`}
          icon={<AlertTriangle className="h-4 w-4 text-rose-500" />}
          trend="-0.5%"
          trendColor="text-emerald-600"
        />
      </div>

      {/* 工具调用统计 */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Zap className="h-4 w-4 text-violet-500" />
            工具调用统计（24 小时）
          </CardTitle>
        </CardHeader>
        <CardContent>
          {toolUsage.length === 0 ? (
            <div className="text-center text-gray-400 text-sm py-4">暂无数据</div>
          ) : (
            <div className="space-y-3">
              {toolUsage.map((tool) => (
                <div key={tool.name} className="flex items-center gap-3">
                  <span className="text-xs text-gray-600 w-20 truncate">{tool.name}</span>
                  <div className="flex-1 h-6 bg-gray-100 rounded overflow-hidden relative">
                    <div
                      className="h-full rounded transition-all"
                      style={{
                        width: `${getToolPercentage(tool.count, toolUsage)}%`,
                        backgroundColor: tool.color,
                      }}
                    />
                    <span className="absolute inset-0 flex items-center justify-center text-xs font-medium text-gray-700">
                      {tool.count.toLocaleString()} 次
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

interface MetricCardProps {
  title: string;
  value: string;
  icon: React.ReactNode;
  trend?: string;
  trendColor?: string;
}

function MetricCard({ title, value, icon, trend, trendColor = 'text-emerald-600' }: MetricCardProps) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-gray-500">{title}</span>
          {icon}
        </div>
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-bold text-gray-900">{value}</span>
          {trend && (
            <span className={`text-xs ${trendColor}`}>{trend}</span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function getToolColor(toolName: string): string {
  const colors: Record<string, string> = {
    RAG: '#3b82f6',
    Database: '#10b981',
    Web: '#f59e0b',
    API: '#8b5cf6',
  };
  return colors[toolName] || '#6b7280';
}

function getToolPercentage(count: number, allTools: { count: number }[]): number {
  const maxCount = Math.max(...allTools.map((t) => t.count), 1);
  return (count / maxCount) * 100;
}
