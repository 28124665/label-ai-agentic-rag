'use client';

import { useEffect, useState } from 'react';
import { Search, Loader2, Edit2, MoreVertical, UserCheck, UserX } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Label } from '@/components/ui/label';
import { adminService, type User } from '@/services/admin.service';
import { cn } from '@/lib/utils';

export function UserList() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [roleFilter, setRoleFilter] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [selectedRole, setSelectedRole] = useState<string>('');

  useEffect(() => {
    loadUsers();
  }, [currentPage, searchQuery, roleFilter, statusFilter]);

  const loadUsers = async () => {
    setLoading(true);
    try {
      const data = await adminService.getUsers({
        page: currentPage,
        size: 20,
        search: searchQuery || undefined,
        role: roleFilter || undefined,
        is_active: statusFilter === '' ? undefined : statusFilter === 'active',
      });
      setUsers(data.items);
      setTotalPages(data.pages);
    } catch (error) {
      console.error('加载用户列表失败:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleEditRole = (user: User) => {
    setEditingUser(user);
    setSelectedRole(user.role);
    setEditDialogOpen(true);
  };

  const handleSaveRole = async () => {
    if (!editingUser) return;

    try {
      await adminService.updateUser(editingUser.id, { role: selectedRole as any });
      setUsers((prev) =>
        prev.map((u) =>
          u.id === editingUser.id ? { ...u, role: selectedRole as any } : u
        )
      );
      setEditDialogOpen(false);
      setEditingUser(null);
    } catch (error) {
      console.error('更新用户角色失败:', error);
    }
  };

  const handleToggleStatus = async (user: User) => {
    try {
      await adminService.toggleUserStatus(user.id, !user.is_active);
      setUsers((prev) =>
        prev.map((u) =>
          u.id === user.id ? { ...u, is_active: !user.is_active } : u
        )
      );
    } catch (error) {
      console.error('切换用户状态失败:', error);
    }
  };

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const getRoleBadge = (role: string) => {
    const roleConfig = {
      admin: { label: '管理员', className: 'bg-red-100 text-red-700' },
      user: { label: '普通用户', className: 'bg-blue-100 text-blue-700' },
      viewer: { label: '访客', className: 'bg-gray-100 text-gray-700' },
    };
    const config = roleConfig[role as keyof typeof roleConfig] || roleConfig.user;
    return (
      <span className={cn('px-2 py-1 rounded-full text-xs font-medium', config.className)}>
        {config.label}
      </span>
    );
  };

  return (
    <div className="space-y-4">
      {/* 搜索和筛选栏 */}
      <div className="flex gap-3">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          <Input
            placeholder="搜索用户名或邮箱..."
            value={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              setCurrentPage(1);
            }}
            className="pl-10"
          />
        </div>
        <select
          value={roleFilter}
          onChange={(e) => {
            setRoleFilter(e.target.value);
            setCurrentPage(1);
          }}
          className="h-10 px-3 rounded-md border border-input bg-background text-sm"
        >
          <option value="">全部角色</option>
          <option value="admin">管理员</option>
          <option value="user">普通用户</option>
          <option value="viewer">访客</option>
        </select>
        <select
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value);
            setCurrentPage(1);
          }}
          className="h-10 px-3 rounded-md border border-input bg-background text-sm"
        >
          <option value="">全部状态</option>
          <option value="active">已启用</option>
          <option value="inactive">已禁用</option>
        </select>
      </div>

      {/* 用户列表 */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
          <span className="ml-2 text-gray-500">加载中...</span>
        </div>
      ) : users.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-gray-400">
          <div className="text-6xl mb-4">👤</div>
          <div className="text-lg font-medium mb-2">暂无用户数据</div>
          <div className="text-sm">尝试使用不同的搜索条件</div>
        </div>
      ) : (
        <div className="border rounded-lg overflow-hidden">
          <table className="w-full">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-700">用户名</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-700">邮箱</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-700">角色</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-700">状态</th>
                <th className="text-left px-4 py-3 text-sm font-medium text-gray-700">创建时间</th>
                <th className="text-right px-4 py-3 text-sm font-medium text-gray-700">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {users.map((user) => (
                <tr key={user.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      {user.avatar ? (
                        <img src={user.avatar} alt={user.username} className="h-8 w-8 rounded-full" />
                      ) : (
                        <div className="h-8 w-8 rounded-full bg-primary/10 flex items-center justify-center text-primary text-sm font-medium">
                          {user.username.charAt(0).toUpperCase()}
                        </div>
                      )}
                      <span className="text-sm font-medium">{user.username}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-600">{user.email}</td>
                  <td className="px-4 py-3">{getRoleBadge(user.role)}</td>
                  <td className="px-4 py-3">
                    <span
                      className={cn(
                        'px-2 py-1 rounded-full text-xs font-medium',
                        user.is_active
                          ? 'bg-green-100 text-green-700'
                          : 'bg-gray-100 text-gray-500'
                      )}
                    >
                      {user.is_active ? '已启用' : '已禁用'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-600">{formatDate(user.created_at)}</td>
                  <td className="px-4 py-3 text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="sm">
                          <MoreVertical className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => handleEditRole(user)}>
                          <Edit2 className="h-4 w-4 mr-2" />
                          编辑角色
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => handleToggleStatus(user)}>
                          {user.is_active ? (
                            <>
                              <UserX className="h-4 w-4 mr-2" />
                              禁用用户
                            </>
                          ) : (
                            <>
                              <UserCheck className="h-4 w-4 mr-2" />
                              启用用户
                            </>
                          )}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <div className="text-sm text-gray-500">
            第 {currentPage} 页，共 {totalPages} 页
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={currentPage === 1}
              onClick={() => setCurrentPage((p) => p - 1)}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={currentPage === totalPages}
              onClick={() => setCurrentPage((p) => p + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      )}

      {/* 编辑角色弹窗 */}
      <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑用户角色</DialogTitle>
            <DialogDescription>
              修改用户 {editingUser?.username} 的角色权限
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>角色</Label>
              <select
                value={selectedRole}
                onChange={(e) => setSelectedRole(e.target.value)}
                className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm"
              >
                <option value="admin">管理员</option>
                <option value="user">普通用户</option>
                <option value="viewer">访客</option>
              </select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditDialogOpen(false)}>
              取消
            </Button>
            <Button onClick={handleSaveRole}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
