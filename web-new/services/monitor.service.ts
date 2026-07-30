import { apiClient } from './api-client';

// 监控指标数据
export interface MonitorMetrics {
  total_requests: number;
  success_rate: number;
  avg_response_time: number;
  total_tokens: number;
  total_cost: number;
  active_conversations: number;
  faithfulness_score: number;
}

// 质量趋势数据点
export interface QualityDataPoint {
  date: string;
  relevance_score: number;
  faithfulness_score: number;
  answer_quality: number;
  retrieval_precision: number;
}

// 成本趋势数据点
export interface CostDataPoint {
  date: string;
  llm_cost: number;
  embedding_cost: number;
  total_cost: number;
  token_count: number;
}

// 工具调用统计
export interface ToolCallStats {
  tool_name: string;
  call_count: number;
  success_count: number;
  avg_latency: number;
  error_rate: number;
}

// 系统健康状态
export interface SystemHealth {
  status: 'healthy' | 'degraded' | 'down';
  components: {
    name: string;
    status: 'healthy' | 'degraded' | 'down';
    latency_ms?: number;
    message?: string;
  }[];
}

// 时间范围类型
export type TimeRange = '1h' | '24h' | '7d' | '30d';

class MonitorService {
  // 获取实时指标
  async getMetrics(): Promise<MonitorMetrics> {
    const response = await apiClient.get<MonitorMetrics>('/monitor/metrics');
    return response.data!;
  }

  // 获取质量趋势
  async getQualityTrend(range: TimeRange = '24h'): Promise<QualityDataPoint[]> {
    const response = await apiClient.get<QualityDataPoint[]>('/monitor/quality-trend', {
      params: { range },
    });
    return response.data!;
  }

  // 获取成本趋势
  async getCostTrend(range: TimeRange = '24h'): Promise<CostDataPoint[]> {
    const response = await apiClient.get<CostDataPoint[]>('/monitor/cost-trend', {
      params: { range },
    });
    return response.data!;
  }

  // 获取工具调用统计
  async getToolCallStats(range: TimeRange = '24h'): Promise<ToolCallStats[]> {
    const response = await apiClient.get<ToolCallStats[]>('/monitor/tool-stats', {
      params: { range },
    });
    return response.data!;
  }

  // 获取系统健康状态
  async getSystemHealth(): Promise<SystemHealth> {
    const response = await apiClient.get<SystemHealth>('/monitor/health');
    return response.data!;
  }
}

export const monitorService = new MonitorService();
