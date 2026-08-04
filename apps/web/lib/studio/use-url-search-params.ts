'use client';

import { usePathname, useSearchParams } from 'next/navigation';
import { useCallback, useMemo } from 'react';

type SetOpts = { replace?: boolean };
type Updater = URLSearchParams | ((prev: URLSearchParams) => URLSearchParams);

/**
 * react-router useSearchParams 的等价物(studio 迁入代码的最小改动面)。
 * 写入走 window.history.pushState/replaceState —— Next 15 会把两者同步进
 * useSearchParams(shallow routing),不触发 RSC 往返、不滚顶,和 react-router
 * 的行为一致(replace 默认 false = push,保留"后退关 wizard"语义)。
 */
export function useUrlSearchParams(): [URLSearchParams, (next: Updater, opts?: SetOpts) => void] {
  const spRaw = useSearchParams();
  const pathname = usePathname();
  const sp = useMemo(() => new URLSearchParams(spRaw.toString()), [spRaw]);

  const setSp = useCallback(
    (next: Updater, opts?: SetOpts) => {
      const resolved =
        typeof next === 'function' ? next(new URLSearchParams(window.location.search)) : next;
      const qs = resolved.toString();
      const url = qs ? `${pathname}?${qs}` : pathname;
      if (opts?.replace) window.history.replaceState(null, '', url);
      else window.history.pushState(null, '', url);
    },
    [pathname],
  );

  return [sp, setSp];
}
