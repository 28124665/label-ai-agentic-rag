import axios, { AxiosError, AxiosInstance, AxiosRequestConfig, AxiosResponse } from 'axios';

// API 响应格式
export interface ApiResponse<T = any> {
  code: number;
  message: string;
  data?: T;
  error?: string;
}

// 分页数据格式
export interface PageData<T = any> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

// 分页响应格式
export interface PageResponse<T = any> {
  code: number;
  message: string;
  data?: PageData<T>;
}

interface FailedQueueItem {
  resolve: (value?: any) => void;
  reject: (reason?: any) => void;
  config: AxiosRequestConfig;
}

class ApiClient {
  private client: AxiosInstance;
  private baseURL: string;
  private isRefreshing = false;
  private failedQueue: FailedQueueItem[] = [];

  constructor(baseURL: string = '/api/v1') {
    this.baseURL = baseURL;
    this.client = axios.create({
      baseURL,
      timeout: 30000,
      headers: {
        'Content-Type': 'application/json',
      },
    });

    // 请求拦截器
    this.client.interceptors.request.use(
      (config) => {
        // 从 localStorage 获取 token
        const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
        if (token) {
          config.headers.Authorization = `Bearer ${token}`;
        }
        return config;
      },
      (error) => {
        return Promise.reject(error);
      }
    );

    // 响应拦截器
    this.client.interceptors.response.use(
      (response: AxiosResponse<ApiResponse>) => {
        const { data } = response;
        if (data.code !== 0) {
          return Promise.reject(new Error(data.message || '请求失败'));
        }
        return response;
      },
      async (error: AxiosError) => {
        const originalRequest = error.config as AxiosRequestConfig & { _retry?: boolean };

        if (error.response?.status === 401 && !originalRequest?._retry) {
          // 如果是刷新 token 的请求本身返回 401，直接走登出逻辑
          if (originalRequest?.url?.includes('/auth/refresh')) {
            this.handleLogout();
            return Promise.reject(error);
          }

          // 如果已经在刷新中，将请求加入队列等待
          if (this.isRefreshing) {
            return new Promise((resolve, reject) => {
              this.failedQueue.push({ resolve, reject, config: originalRequest });
            });
          }

          originalRequest._retry = true;
          this.isRefreshing = true;

          try {
            const newToken = await this.refreshToken();
            
            // 更新原请求的 token 并重试
            originalRequest.headers = originalRequest.headers || {};
            (originalRequest.headers as any).Authorization = `Bearer ${newToken}`;
            
            // 重试队列中的所有请求
            this.processQueue(null, newToken);
            
            // 重试原请求
            return await this.client(originalRequest);
          } catch (refreshError) {
            // 刷新失败，拒绝队列中的所有请求
            this.processQueue(refreshError as Error, null);
            this.handleLogout();
            return Promise.reject(refreshError);
          } finally {
            this.isRefreshing = false;
          }
        }

        // 提取错误信息
        const data = error.response?.data as any;
        const message = data?.message || data?.error || '请求失败';
        return Promise.reject(new Error(message));
      }
    );
  }

  private processQueue(error: Error | null, token: string | null) {
    this.failedQueue.forEach((item) => {
      if (error) {
        item.reject(error);
      } else {
        // 更新队列中请求的 token 后重试
        item.config.headers = item.config.headers || {};
        (item.config.headers as any).Authorization = `Bearer ${token}`;
        item.resolve(this.client(item.config));
      }
    });
    this.failedQueue = [];
  }

  private handleLogout() {
    if (typeof window !== 'undefined') {
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      window.location.href = '/login';
    }
  }

  private async refreshToken(): Promise<string> {
    const refreshToken = typeof window !== 'undefined' ? localStorage.getItem('refresh_token') : null;
    if (!refreshToken) {
      throw new Error('No refresh token');
    }

    const response = await this.client.post<ApiResponse<{ access_token: string; refresh_token: string }>>(
      '/auth/refresh',
      { refresh_token: refreshToken }
    );

    if (response.data.code === 0 && response.data.data) {
      const { access_token, refresh_token } = response.data.data;
      localStorage.setItem('access_token', access_token);
      localStorage.setItem('refresh_token', refresh_token);
      return access_token;
    }

    throw new Error('Refresh token failed');
  }

  // GET 请求
  async get<T = any>(url: string, config?: AxiosRequestConfig): Promise<ApiResponse<T>> {
    const response = await this.client.get<ApiResponse<T>>(url, config);
    return response.data;
  }

  // POST 请求
  async post<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<ApiResponse<T>> {
    const response = await this.client.post<ApiResponse<T>>(url, data, config);
    return response.data;
  }

  // PUT 请求
  async put<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<ApiResponse<T>> {
    const response = await this.client.put<ApiResponse<T>>(url, data, config);
    return response.data;
  }

  // DELETE 请求
  async delete<T = any>(url: string, config?: AxiosRequestConfig): Promise<ApiResponse<T>> {
    const response = await this.client.delete<ApiResponse<T>>(url, config);
    return response.data;
  }

  // SSE 流式请求
  async sse(
    url: string,
    data: any,
    onMessage: (event: string, data: any) => void,
    onError?: (error: Error) => void,
    onComplete?: () => void
  ): Promise<void> {
    const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
    
    try {
      // Check if data contains files (File objects)
      const hasFiles = data.files && Array.isArray(data.files) && data.files.length > 0 && data.files[0] instanceof File;
      
      let body: string | FormData;
      let headers: Record<string, string>;
      
      if (hasFiles) {
        // Use FormData for file uploads
        const formData = new FormData();
        formData.append('content', data.content || '');
        data.files.forEach((file: File) => {
          formData.append('files', file);
        });
        body = formData;
        headers = {
          ...(token && { Authorization: `Bearer ${token}` }),
        };
      } else {
        // Use JSON for regular requests
        body = JSON.stringify(data);
        headers = {
          'Content-Type': 'application/json',
          ...(token && { Authorization: `Bearer ${token}` }),
        };
      }
      
      const response = await fetch(`${this.baseURL}${url}`, {
        method: 'POST',
        headers,
        body,
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('No reader available');
      }

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        
        if (done) {
          onComplete?.();
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const dataStr = line.slice(6);
            try {
              const eventData = JSON.parse(dataStr);
              onMessage(eventData.type, eventData);
            } catch (e) {
              console.error('Failed to parse SSE data:', dataStr);
            }
          }
        }
      }
    } catch (error) {
      if (onError) {
        onError(error as Error);
      } else {
        throw error;
      }
    }
  }
}

// 导出单例
export const apiClient = new ApiClient();
