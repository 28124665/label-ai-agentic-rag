import { apiClient, ApiResponse } from './api-client';

// 用户信息
export interface User {
  id: string;
  username: string;
  email: string;
  role: string;
  avatar?: string;
  company?: string;
  created_at: string;
  is_active: boolean;
}

// 登录请求
export interface LoginRequest {
  username: string;
  password: string;
}

// 登录响应
export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

// 注册请求
export interface RegisterRequest {
  username: string;
  password: string;
  email: string;
  company?: string;
}

class AuthService {
  // 登录
  async login(data: LoginRequest): Promise<LoginResponse> {
    const response = await apiClient.post<LoginResponse>('/auth/login', data);
    const loginData = response.data!;
    
    // 保存 token
    if (typeof window !== 'undefined') {
      localStorage.setItem('access_token', loginData.access_token);
      localStorage.setItem('user', JSON.stringify(loginData.user));
    }
    
    return loginData;
  }

  // 注册
  async register(data: RegisterRequest): Promise<User> {
    const response = await apiClient.post<User>('/auth/register', data);
    return response.data!;
  }

  // 获取当前用户
  async getCurrentUser(): Promise<User> {
    const response = await apiClient.get<User>('/auth/me');
    return response.data!;
  }

  // 刷新 token
  async refreshToken(): Promise<{ access_token: string; token_type: string; expires_in: number }> {
    const response = await apiClient.post<{ access_token: string; token_type: string; expires_in: number }>('/auth/refresh');
    const tokenData = response.data!;
    
    // 更新 token
    if (typeof window !== 'undefined') {
      localStorage.setItem('access_token', tokenData.access_token);
    }
    
    return tokenData;
  }

  // 登出
  logout(): void {
    if (typeof window !== 'undefined') {
      localStorage.removeItem('access_token');
      localStorage.removeItem('user');
      window.location.href = '/login';
    }
  }

  // 获取存储的用户信息
  getStoredUser(): User | null {
    if (typeof window === 'undefined') return null;
    const userStr = localStorage.getItem('user');
    if (!userStr) return null;
    try {
      return JSON.parse(userStr);
    } catch {
      return null;
    }
  }

  // 检查是否已登录
  isAuthenticated(): boolean {
    if (typeof window === 'undefined') return false;
    return !!localStorage.getItem('access_token');
  }
}

export const authService = new AuthService();
