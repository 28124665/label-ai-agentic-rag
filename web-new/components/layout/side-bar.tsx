'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import {
  MessageSquare,
  BookOpen,
  Database,
  Bot,
  BarChart3,
  Settings,
} from 'lucide-react';

const menuItems = [
  { href: '/chat', label: '对话', icon: MessageSquare },
  { href: '/knowledge', label: '知识库', icon: BookOpen },
  { href: '/datasource', label: '数据源', icon: Database },
  { href: '/agent', label: 'Agent', icon: Bot },
  { href: '/monitor', label: '监控', icon: BarChart3 },
  { href: '/admin', label: '管理', icon: Settings },
];

export function SideBar() {
  const pathname = usePathname();

  return (
    <aside className="w-56 border-r bg-white flex flex-col">
      <nav className="flex-1 p-3 space-y-1">
        {menuItems.map((item) => {
          const isActive = pathname === item.href || pathname.startsWith(item.href + '/');
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                'flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors',
                isActive
                  ? 'bg-primary/10 text-primary font-medium'
                  : 'text-gray-600 hover:bg-gray-100'
              )}
            >
              <item.icon className="h-5 w-5" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="p-3 border-t">
        <div className="px-3 py-2 text-xs text-gray-500">
          v1.0.0
        </div>
      </div>
    </aside>
  );
}
