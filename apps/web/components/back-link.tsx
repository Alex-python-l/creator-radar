'use client';

import Link from 'next/link';
import type { Route } from 'next';
import { useRouter } from 'next/navigation';
import { type MouseEvent, type ReactNode, useCallback } from 'react';

/**
 * 浏览器历史返回链接 — 优先 router.back() 保留来源页 URL/滚动位置;
 * 没历史(书签/外链直进)就 fallback 到 href 默认 push。
 * 修饰键/中键交回浏览器,不影响"在新标签打开"。
 */
export function BackLink({
  href,
  className,
  children,
}: {
  href: Route;
  className?: string;
  children: ReactNode;
}) {
  const router = useRouter();

  const onClick = useCallback(
    (e: MouseEvent<HTMLAnchorElement>) => {
      if (
        e.defaultPrevented ||
        e.metaKey ||
        e.ctrlKey ||
        e.shiftKey ||
        e.altKey ||
        e.button !== 0
      ) {
        return;
      }
      if (window.history.length > 1) {
        e.preventDefault();
        router.back();
      }
    },
    [router],
  );

  return (
    <Link href={href} onClick={onClick} className={className}>
      {children}
    </Link>
  );
}
