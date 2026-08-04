'use client';

import type { Route } from 'next';
import { useLocale } from 'next-intl';
import { useCallback } from 'react';

/**
 * studio 内部路径('/'、'/videos?x=1')→ 带 locale 的应用路径。
 * typedRoutes 的 as Route 断言统一收口在这里。
 */
export function useStudioHref(): (path: string) => Route {
  const locale = useLocale();
  return useCallback(
    (path: string) => {
      const suffix = path === '/' ? '' : path;
      return `/${locale}/studio${suffix}` as Route;
    },
    [locale],
  );
}
