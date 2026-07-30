import { authService } from '@/services/auth.service';

// 检查是否已登录
export function isAuthenticated(): boolean {
  return authService.isAuthenticated();
}

// 获取当前用户
export function getCurrentUser() {
  return authService.getStoredUser();
}

// 登出
export function logout() {
  authService.logout();
}

// 需要认证的路由保护（用于 middleware 或页面）
export function requireAuth() {
  if (typeof window !== 'undefined' && !isAuthenticated()) {
    window.location.href = '/login';
    return false;
  }
  return true;
}
