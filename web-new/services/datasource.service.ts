import { apiClient, PageData } from './api-client';

// 数据源
export interface DataSource {
  id: string;
  user_id: string;
  name: string;
  description?: string;
  db_type: string;
  host: string;
  port: number;
  database: string;
  username: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  last_tested_at?: string;
}

// 表信息
export interface TableInfo {
  name: string;
  comment?: string;
  row_count?: number;
}

// 列信息
export interface ColumnInfo {
  name: string;
  data_type: string;
  nullable: boolean;
  comment?: string;
  is_primary_key: boolean;
}

// 表详情
export interface TableDetail {
  name: string;
  comment?: string;
  columns: ColumnInfo[];
  row_count?: number;
}

// SQL 执行结果
export interface SQLResult {
  sql: string;
  columns: string[];
  rows: any[];
  row_count: number;
  execution_time_ms: number;
  truncated: boolean;
}

class DataSourceService {
  // 获取数据源列表
  async list(page: number = 1, size: number = 20): Promise<PageData<DataSource>> {
    const response = await apiClient.get<PageData<DataSource>>('/datasources', {
      params: { page, size },
    });
    return response.data!;
  }

  // 获取数据源详情
  async get(datasourceId: string): Promise<DataSource> {
    const response = await apiClient.get<DataSource>(`/datasources/${datasourceId}`);
    return response.data!;
  }

  // 创建数据源
  async create(data: {
    name: string;
    description?: string;
    db_type: string;
    host: string;
    port: number;
    database: string;
    username: string;
    password: string;
  }): Promise<DataSource> {
    const response = await apiClient.post<DataSource>('/datasources', data);
    return response.data!;
  }

  // 更新数据源
  async update(datasourceId: string, data: {
    name?: string;
    description?: string;
    host?: string;
    port?: number;
    database?: string;
    username?: string;
    password?: string;
  }): Promise<DataSource> {
    const response = await apiClient.put<DataSource>(`/datasources/${datasourceId}`, data);
    return response.data!;
  }

  // 删除数据源
  async delete(datasourceId: string): Promise<void> {
    await apiClient.delete(`/datasources/${datasourceId}`);
  }

  // 测试连接
  async testConnection(datasourceId: string): Promise<{
    success: boolean;
    message: string;
    execution_time_ms: number;
  }> {
    const response = await apiClient.post(`/datasources/${datasourceId}/test`);
    return response.data!;
  }

  // 获取表列表
  async listTables(datasourceId: string): Promise<TableInfo[]> {
    const response = await apiClient.get<TableInfo[]>(`/datasources/${datasourceId}/tables`);
    return response.data!;
  }

  // 获取表详情
  async describeTable(datasourceId: string, tableName: string): Promise<TableDetail> {
    const response = await apiClient.get<TableDetail>(`/datasources/${datasourceId}/tables/${tableName}`);
    return response.data!;
  }

  // 执行 SQL
  async executeSQL(datasourceId: string, sql: string, maxRows: number = 100): Promise<SQLResult> {
    const response = await apiClient.post<SQLResult>(`/datasources/${datasourceId}/execute`, {
      sql,
      max_rows: maxRows,
    });
    return response.data!;
  }
}

export const dataSourceService = new DataSourceService();
