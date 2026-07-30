import { apiClient, PageData } from './api-client';

// 知识库
export interface Dataset {
  id: string;
  name: string;
  description?: string;
  embedding_model: string;
  chunk_count: number;
  document_count: number;
  created_at: string;
  updated_at: string;
}

// 文档
export interface Document {
  id: string;
  name: string;
  size: number;
  chunk_count: number;
  status: 'uploading' | 'parsing' | 'completed' | 'failed';
  created_at: string;
}

// 检索结果
export interface RetrievalResult {
  content: string;
  similarity: number;
  document_name: string;
  chunk_id: string;
  metadata?: any;
}

class KnowledgeService {
  // 获取知识库列表
  async listDatasets(page: number = 1, size: number = 20): Promise<PageData<Dataset>> {
    const response = await apiClient.get<PageData<Dataset>>('/knowledge/datasets', {
      params: { page, size },
    });
    return response.data!;
  }

  // 获取知识库详情
  async getDataset(datasetId: string): Promise<Dataset> {
    const response = await apiClient.get<Dataset>(`/knowledge/datasets/${datasetId}`);
    return response.data!;
  }

  // 创建知识库
  async createDataset(data: {
    name: string;
    description?: string;
    embedding_model?: string;
    chunk_method?: string;
  }): Promise<Dataset> {
    const response = await apiClient.post<Dataset>('/knowledge/datasets', data);
    return response.data!;
  }

  // 更新知识库
  async updateDataset(datasetId: string, data: {
    name?: string;
    description?: string;
    embedding_model?: string;
  }): Promise<Dataset> {
    const response = await apiClient.put<Dataset>(`/knowledge/datasets/${datasetId}`, data);
    return response.data!;
  }

  // 删除知识库
  async deleteDataset(datasetId: string): Promise<void> {
    await apiClient.delete(`/knowledge/datasets/${datasetId}`);
  }

  // 获取文档列表
  async listDocuments(datasetId: string, page: number = 1, size: number = 20): Promise<PageData<Document>> {
    const response = await apiClient.get<PageData<Document>>(`/knowledge/datasets/${datasetId}/documents`, {
      params: { page, size },
    });
    return response.data!;
  }

  // 上传文档
  async uploadDocument(datasetId: string, file: File): Promise<Document> {
    const formData = new FormData();
    formData.append('file', file);
    
    const response = await apiClient.post<Document>(
      `/knowledge/datasets/${datasetId}/documents`,
      formData,
      {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      }
    );
    return response.data!;
  }

  // 删除文档
  async deleteDocument(datasetId: string, documentId: string): Promise<void> {
    await apiClient.delete(`/knowledge/datasets/${datasetId}/documents/${documentId}`);
  }

  // 检索测试
  async retrieval(datasetId: string, query: string, topK: number = 5): Promise<{
    query: string;
    results: RetrievalResult[];
    total: number;
    execution_time_ms: number;
  }> {
    const response = await apiClient.post(`/knowledge/datasets/${datasetId}/retrieval`, {
      query,
      top_k: topK,
    });
    return response.data!;
  }
}

export const knowledgeService = new KnowledgeService();
