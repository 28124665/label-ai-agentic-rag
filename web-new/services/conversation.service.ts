import { apiClient, PageData } from './api-client';

// 对话
export interface Conversation {
  id: string;
  user_id: string;
  agent_id?: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  last_message_at?: string;
}

// 消息
export interface Message {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  tool_calls?: any;
  references?: any;
  token_usage?: any;
  execution_time_ms?: number;
  created_at: string;
}

// SSE 事件
export interface SSEEvent {
  type: 'thinking' | 'tool_call' | 'tool_result' | 'text' | 'reference' | 'done' | 'error';
  content?: string;
  tool?: string;
  sql?: string;
  status?: string;
  rows?: any[];
  sources?: any[];
  message_id?: string;
  error?: string;
}

class ConversationService {
  // 创建对话
  async create(agentId?: string, title: string = '新对话'): Promise<Conversation> {
    const response = await apiClient.post<Conversation>('/conversations', {
      agent_id: agentId,
      title,
    });
    return response.data!;
  }

  // 获取对话列表
  async list(page: number = 1, size: number = 20): Promise<PageData<Conversation>> {
    const response = await apiClient.get<PageData<Conversation>>('/conversations', {
      params: { page, size },
    });
    return response.data!;
  }

  // 获取对话详情
  async get(conversationId: string): Promise<Conversation> {
    const response = await apiClient.get<Conversation>(`/conversations/${conversationId}`);
    return response.data!;
  }

  // 更新对话
  async update(conversationId: string, title: string): Promise<Conversation> {
    const response = await apiClient.put<Conversation>(`/conversations/${conversationId}`, {
      title,
    });
    return response.data!;
  }

  // 删除对话
  async delete(conversationId: string): Promise<void> {
    await apiClient.delete(`/conversations/${conversationId}`);
  }

  // 获取消息历史
  async getMessages(conversationId: string, limit: number = 50): Promise<Message[]> {
    const response = await apiClient.get<Message[]>(`/conversations/${conversationId}/messages`, {
      params: { limit },
    });
    return response.data!;
  }

  // 发送消息（SSE 流式）
  async sendMessage(
    conversationId: string,
    content: string,
    onMessage: (event: SSEEvent) => void,
    onError?: (error: Error) => void,
    onComplete?: () => void,
    files?: File[]
  ): Promise<void> {
    await apiClient.sse(
      `/conversations/${conversationId}/messages`,
      { content, files: files && files.length > 0 ? files : undefined },
      (type, data) => {
        onMessage({ type, ...data } as SSEEvent);
      },
      onError,
      onComplete
    );
  }
}

export const conversationService = new ConversationService();
