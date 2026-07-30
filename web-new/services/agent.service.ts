import { apiClient, PageData } from './api-client';

// Agent
export interface Agent {
  id: string;
  user_id: string;
  name: string;
  description?: string;
  tools_config?: {
    tools: string[];
    rag_config?: any;
    database_config?: any;
    web_config?: any;
  };
  routing_config?: {
    strategy: string;
    rules?: any[];
  };
  degradation_config?: {
    max_retries: number;
    fallback: string;
    timeout_seconds: number;
  };
  model_config?: {
    llm: string;
    embedding: string;
    rerank?: string;
    temperature: number;
  };
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

class AgentService {
  // 获取 Agent 列表
  async list(page: number = 1, size: number = 20): Promise<PageData<Agent>> {
    const response = await apiClient.get<PageData<Agent>>('/agents', {
      params: { page, size },
    });
    return response.data!;
  }

  // 获取 Agent 详情
  async get(agentId: string): Promise<Agent> {
    const response = await apiClient.get<Agent>(`/agents/${agentId}`);
    return response.data!;
  }

  // 创建 Agent
  async create(data: {
    name: string;
    description?: string;
    tools_config?: any;
    routing_config?: any;
    degradation_config?: any;
    model_config?: any;
  }): Promise<Agent> {
    const response = await apiClient.post<Agent>('/agents', data);
    return response.data!;
  }

  // 更新 Agent
  async update(agentId: string, data: {
    name?: string;
    description?: string;
    tools_config?: any;
    routing_config?: any;
    degradation_config?: any;
    model_config?: any;
    is_active?: boolean;
  }): Promise<Agent> {
    const response = await apiClient.put<Agent>(`/agents/${agentId}`, data);
    return response.data!;
  }

  // 删除 Agent
  async delete(agentId: string): Promise<void> {
    await apiClient.delete(`/agents/${agentId}`);
  }

  // 获取默认 Agent
  async getDefault(): Promise<Agent> {
    const response = await apiClient.get<Agent>('/agents/default');
    return response.data!;
  }
}

export const agentService = new AgentService();
