import { apiClient, PageData } from './api-client';

// 用户信息
export interface User {
  id: string;
  username: string;
  email: string;
  role: 'admin' | 'user' | 'viewer';
  avatar?: string;
  company?: string;
  created_at: string;
  updated_at: string;
  is_active: boolean;
  last_login?: string;
}

// 用户列表查询参数
export interface UserListParams {
  page: number;
  size: number;
  search?: string;
  role?: string;
  is_active?: boolean;
}

// 更新用户请求
export interface UpdateUserRequest {
  role?: 'admin' | 'user' | 'viewer';
  is_active?: boolean;
}

// 审计日志
export interface AuditLog {
  id: string;
  user_id: string;
  username: string;
  action: string;
  resource_type: string;
  resource_id?: string;
  resource_name?: string;
  ip_address: string;
  user_agent: string;
  details?: Record<string, any>;
  created_at: string;
}

// 审计日志查询参数
export interface AuditLogListParams {
  page: number;
  size: number;
  action?: string;
  resource_type?: string;
  user_id?: string;
  start_time?: string;
  end_time?: string;
}

// 操作类型枚举
export const ActionTypes = {
  CREATE: 'CREATE',
  UPDATE: 'UPDATE',
  DELETE: 'DELETE',
  LOGIN: 'LOGIN',
  LOGOUT: 'LOGOUT',
  EXPORT: 'EXPORT',
  IMPORT: 'IMPORT',
} as const;

// 资源类型枚举
export const ResourceTypes = {
  USER: 'USER',
  KNOWLEDGE: 'KNOWLEDGE',
  DATASOURCE: 'DATASOURCE',
  AGENT: 'AGENT',
  CONVERSATION: 'CONVERSATION',
  DOCUMENT: 'DOCUMENT',
} as const;

class AdminService {
  // 获取用户列表
  async getUsers(params: UserListParams): Promise<PageData<User>> {
    const response = await apiClient.get<PageData<User>>('/admin/users', {
      params,
    });
    return response.data!;
  }

  // 获取单个用户
  async getUser(userId: string): Promise<User> {
    const response = await apiClient.get<User>(`/admin/users/${userId}`);
    return response.data!;
  }

  // 更新用户
  async updateUser(userId: string, data: UpdateUserRequest): Promise<User> {
    const response = await apiClient.put<User>(`/admin/users/${userId}`, data);
    return response.data!;
  }

  // 启用/禁用用户
  async toggleUserStatus(userId: string, isActive: boolean): Promise<User> {
    const response = await apiClient.put<User>(`/admin/users/${userId}/status`, {
      is_active: isActive,
    });
    return response.data!;
  }

  // 获取审计日志列表
  async getAuditLogs(params: AuditLogListParams): Promise<PageData<AuditLog>> {
    const response = await apiClient.get<PageData<AuditLog>>('/admin/audit-logs', {
      params,
    });
    return response.data!;
  }

  // 获取单个审计日志详情
  async getAuditLog(logId: string): Promise<AuditLog> {
    const response = await apiClient.get<AuditLog>(`/admin/audit-logs/${logId}`);
    return response.data!;
  }
}

export const adminService = new AdminService();
