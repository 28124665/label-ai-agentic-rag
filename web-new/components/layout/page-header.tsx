'use client';

import * as React from 'react';
import { ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface BreadcrumbItem {
  label: string;
  href?: string;
}

export interface PageHeaderProps {
  title: string;
  breadcrumbs?: BreadcrumbItem[];
  actions?: React.ReactNode;
  className?: string;
}

export function PageHeader({ title, breadcrumbs, actions, className }: PageHeaderProps) {
  return (
    <div className={cn('border-b bg-background px-6 py-4', className)}>
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          {breadcrumbs && breadcrumbs.length > 0 && (
            <nav className="flex items-center gap-1 text-sm text-muted-foreground">
              {breadcrumbs.map((item, index) => (
                <React.Fragment key={index}>
                  {index > 0 && <ChevronRight className="h-3.5 w-3.5" />}
                  {item.href ? (
                    <a
                      href={item.href}
                      className="hover:text-foreground transition-colors"
                    >
                      {item.label}
                    </a>
                  ) : (
                    <span className="text-foreground font-medium">{item.label}</span>
                  )}
                </React.Fragment>
              ))}
            </nav>
          )}
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        </div>
        {actions && (
          <div className="flex items-center gap-2">{actions}</div>
        )}
      </div>
    </div>
  );
}
