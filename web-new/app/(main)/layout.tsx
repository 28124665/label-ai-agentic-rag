'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { TopNavBar } from '@/components/layout/top-nav-bar';
import { SideBar } from '@/components/layout/side-bar';
import { authService } from '@/services/auth.service';

export default function MainLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();

  useEffect(() => {
    if (!authService.isAuthenticated()) {
      router.push('/login');
    }
  }, [router]);

  return (
    <div className="min-h-screen flex flex-col">
      <TopNavBar />
      <div className="flex flex-1">
        <SideBar />
        <main className="flex-1 overflow-auto bg-gray-50">
          {children}
        </main>
      </div>
    </div>
  );
}
