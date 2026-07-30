'use client';

import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Loading } from '@/components/ui/loading';
import { PageHeader } from '@/components/layout/page-header';
import { Dashboard } from '@/components/monitor/dashboard';
import { QualityChart } from '@/components/monitor/quality-chart';
import { CostChart } from '@/components/monitor/cost-chart';
import { monitorService, QualityDataPoint, CostDataPoint, TimeRange } from '@/services/monitor.service';

export default function MonitorPage() {
  const [qualityData, setQualityData] = useState<QualityDataPoint[]>([]);
  const [costData, setCostData] = useState<CostDataPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [costRange, setCostRange] = useState<TimeRange>('7d');

  useEffect(() => {
    loadTrendData();
  }, []);

  const loadTrendData = async (range: TimeRange = '7d') => {
    try {
      const [quality, cost] = await Promise.all([
        monitorService.getQualityTrend(range),
        monitorService.getCostTrend(range),
      ]);
      setQualityData(quality);
      setCostData(cost);
    } catch (error) {
      console.error('Failed to load trend data:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleCostRangeChange = (range: TimeRange) => {
    setCostRange(range);
    loadTrendData(range);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loading text="加载监控数据..." />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="监控中心"
        breadcrumbs={[{ label: '监控' }]}
      />

      <div className="p-6 space-y-6">
        {/* 实时仪表盘 */}
        <section>
          <h2 className="text-lg font-semibold text-gray-900 mb-4">实时仪表盘</h2>
          <Dashboard />
        </section>

        {/* 趋势图 */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* 质量趋势 */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">质量趋势（最近 7 天）</CardTitle>
            </CardHeader>
            <CardContent>
              <QualityChart data={qualityData} />
            </CardContent>
          </Card>

          {/* 成本趋势 */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">
                成本趋势（{costRange === '1h' ? '最近 1 小时' : costRange === '24h' ? '最近 24 小时' : costRange === '7d' ? '最近 7 天' : '最近 30 天'}）
              </CardTitle>
            </CardHeader>
            <CardContent className="relative">
              <CostChart data={costData} onRangeChange={handleCostRangeChange} />
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
