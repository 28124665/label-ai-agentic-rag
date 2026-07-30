'use client';

import { useState } from 'react';
import { CostDataPoint, TimeRange } from '@/services/monitor.service';

interface CostChartProps {
  data: CostDataPoint[];
  onRangeChange?: (range: TimeRange) => void;
}

const TIME_RANGE_LABELS: Record<TimeRange, string> = {
  '1h': '最近 1 小时',
  '24h': '最近 24 小时',
  '7d': '最近 7 天',
  '30d': '最近 30 天',
};

const TIME_RANGES: TimeRange[] = ['1h', '24h', '7d', '30d'];

function formatCost(value: number): string {
  if (value >= 1000) return `¥${(value / 1000).toFixed(1)}k`;
  return `¥${value.toFixed(2)}`;
}

function formatTokens(value: number): string {
  if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return `${value}`;
}

export function CostChart({ data }: CostChartProps) {
  const [range, setRange] = useState<TimeRange>('24h');
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-[300px] text-gray-400 text-sm">
        暂无成本数据
      </div>
    );
  }

  // 计算图表尺寸
  const width = 600;
  const height = 260;
  const padding = { top: 20, right: 20, bottom: 40, left: 60 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  // 计算数据范围
  const maxCost = Math.max(...data.map((d) => d.total_cost), 0.01);
  const maxTokens = Math.max(...data.map((d) => d.token_count), 1);

  // 生成路径
  const costPoints = data.map((d, i) => {
    const x = padding.left + (i / Math.max(data.length - 1, 1)) * chartWidth;
    const y = padding.top + chartHeight - (d.total_cost / maxCost) * chartHeight;
    return { x, y, data: d };
  });

  const tokenPoints = data.map((d, i) => {
    const x = padding.left + (i / Math.max(data.length - 1, 1)) * chartWidth;
    const y = padding.top + chartHeight - (d.token_count / maxTokens) * chartHeight;
    return { x, y, data: d };
  });

  const costPath = costPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');
  const tokenPath = tokenPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');

  // 成本区域路径
  const costAreaPath = `${costPath} L ${costPoints[costPoints.length - 1].x} ${padding.top + chartHeight} L ${costPoints[0].x} ${padding.top + chartHeight} Z`;

  // Y 轴刻度
  const yTicks = 5;
  const yTickValues = Array.from({ length: yTicks + 1 }, (_, i) => (maxCost / yTicks) * i);

  // X 轴标签（取部分）
  const xLabelInterval = Math.max(1, Math.floor(data.length / 6));

  return (
    <div>
      {/* 时间范围切换 */}
      <div className="flex items-center gap-1 mb-4">
        {TIME_RANGES.map((r) => (
          <button
            key={r}
            onClick={() => handleRangeChange(r)}
            className={`px-3 py-1 text-xs rounded-md transition-colors ${
              range === r
                ? 'bg-primary text-white'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            {TIME_RANGE_LABELS[r]}
          </button>
        ))}
      </div>

      {/* SVG 图表 */}
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full h-auto"
        onMouseLeave={() => setHoverIndex(null)}
      >
        {/* 网格线 */}
        {yTickValues.map((val, i) => {
          const y = padding.top + chartHeight - (val / maxCost) * chartHeight;
          return (
            <g key={i}>
              <line
                x1={padding.left}
                y1={y}
                x2={padding.left + chartWidth}
                y2={y}
                stroke="#e5e7eb"
                strokeDasharray="3 3"
              />
              <text
                x={padding.left - 8}
                y={y + 4}
                textAnchor="end"
                className="text-[10px] fill-gray-400"
              >
                {formatCost(val)}
              </text>
            </g>
          );
        })}

        {/* X 轴标签 */}
        {data.map((d, i) => {
          if (i % xLabelInterval !== 0 && i !== data.length - 1) return null;
          const x = padding.left + (i / Math.max(data.length - 1, 1)) * chartWidth;
          return (
            <text
              key={i}
              x={x}
              y={height - 8}
              textAnchor="middle"
              className="text-[10px] fill-gray-400"
            >
              {d.date}
            </text>
          );
        })}

        {/* 成本面积 */}
        <path d={costAreaPath} fill="url(#costGradient)" opacity={0.3} />

        {/* 渐变定义 */}
        <defs>
          <linearGradient id="costGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.4} />
            <stop offset="100%" stopColor="#3b82f6" stopOpacity={0} />
          </linearGradient>
        </defs>

        {/* 成本线 */}
        <path d={costPath} fill="none" stroke="#3b82f6" strokeWidth={2} />

        {/* Token 线 */}
        <path d={tokenPath} fill="none" stroke="#10b981" strokeWidth={2} strokeDasharray="4 2" />

        {/* 数据点 */}
        {costPoints.map((p, i) => (
          <circle
            key={`cost-${i}`}
            cx={p.x}
            cy={p.y}
            r={hoverIndex === i ? 5 : 3}
            fill="#3b82f6"
            className="transition-all cursor-pointer"
            onMouseEnter={() => setHoverIndex(i)}
          />
        ))}

        {/* 悬停交互区域 */}
        {data.map((_, i) => {
          const x = padding.left + (i / Math.max(data.length - 1, 1)) * chartWidth;
          const barWidth = chartWidth / data.length;
          return (
            <rect
              key={`hover-${i}`}
              x={x - barWidth / 2}
              y={padding.top}
              width={barWidth}
              height={chartHeight}
              fill="transparent"
              onMouseEnter={() => setHoverIndex(i)}
            />
          );
        })}

        {/* 悬停指示线 */}
        {hoverIndex !== null && (
          <line
            x1={costPoints[hoverIndex].x}
            y1={padding.top}
            x2={costPoints[hoverIndex].x}
            y2={padding.top + chartHeight}
            stroke="#9ca3af"
            strokeWidth={1}
            strokeDasharray="3 3"
          />
        )}
      </svg>

      {/* 悬停提示 */}
      {hoverIndex !== null && data[hoverIndex] && (
        <div className="absolute top-2 right-2 bg-white border border-gray-200 rounded-lg px-3 py-2 text-xs shadow-sm">
          <div className="font-medium text-gray-700 mb-1">{data[hoverIndex].date}</div>
          <div className="flex items-center gap-2 text-blue-600">
            <span className="w-2 h-2 rounded-full bg-blue-500" />
            成本: {formatCost(data[hoverIndex].total_cost)}
          </div>
          <div className="flex items-center gap-2 text-emerald-600">
            <span className="w-2 h-2 rounded-full bg-emerald-500" />
            Token: {formatTokens(data[hoverIndex].token_count)}
          </div>
        </div>
      )}

      {/* 图例 */}
      <div className="flex items-center justify-center gap-6 mt-2 text-xs text-gray-500">
        <div className="flex items-center gap-1.5">
          <span className="w-3 h-0.5 bg-blue-500 rounded" />
          API 成本
        </div>
        <div className="flex items-center gap-1.5">
          <span className="w-3 h-0.5 bg-emerald-500 rounded border-dashed" style={{ borderTop: '2px dashed #10b981', height: 0 }} />
          Token 消耗
        </div>
      </div>
    </div>
  );
}
