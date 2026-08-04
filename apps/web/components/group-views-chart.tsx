'use client';

import { useEffect, useRef, useState } from 'react';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { formatCompact } from '@/components/format';
import type { GroupViewsPoint } from '@/lib/queries/group-views';

export function GroupViewsChart({
  data,
  locale,
  emptyLabel,
}: {
  data: GroupViewsPoint[];
  locale: string;
  emptyLabel: string;
}) {
  const chartRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const hasData = data.some((p) => p.videoCount > 0 || p.totalViews > 0);

  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;

    const updateWidth = () => {
      setWidth(Math.max(0, Math.floor(el.getBoundingClientRect().width)));
    };

    updateWidth();
    const observer = new ResizeObserver(updateWidth);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  if (data.length === 0 || !hasData) {
    return (
      <div className="flex h-[260px] items-center justify-center text-sm text-[color:var(--color-muted-foreground)]">
        {emptyLabel}
      </div>
    );
  }

  return (
    <div ref={chartRef} className="h-[260px] w-full">
      {width > 0 ? (
        <ComposedChart
          width={width}
          height={260}
          data={data}
          margin={{ top: 8, right: 12, bottom: 4, left: 4 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 11 }}
            stroke="var(--color-muted-foreground)"
          />
          <YAxis
            yAxisId="views"
            tickFormatter={(v: number) => formatCompact(v, locale)}
            tick={{ fontSize: 11 }}
            width={48}
            stroke="var(--color-muted-foreground)"
          />
          <YAxis
            yAxisId="videos"
            orientation="right"
            tickFormatter={(v: number) => formatCompact(v, locale)}
            tick={{ fontSize: 11 }}
            width={36}
            stroke="var(--color-muted-foreground)"
          />
          <Tooltip
            cursor={{ fill: 'var(--color-muted)', opacity: 0.28 }}
            content={(props) => <ChartTooltip {...props} locale={locale} />}
          />
          <Bar
            yAxisId="videos"
            dataKey="videoCount"
            fill="#d9d2e9"
            opacity={0.65}
            radius={[3, 3, 0, 0]}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            yAxisId="views"
            dataKey="totalViews"
            stroke="var(--color-primary)"
            strokeWidth={2.25}
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      ) : null}
    </div>
  );
}

function ChartTooltip({
  active,
  payload,
  locale,
}: {
  active?: boolean;
  payload?: readonly { payload?: GroupViewsPoint }[];
  locale: string;
}) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;

  const numberLocale = locale === 'zh' ? 'zh-CN' : 'en';
  const labels =
    locale === 'zh'
      ? {
          views: '播放量',
          videos: '视频数',
          average: '平均播放',
          viral: '爆款数',
        }
      : {
          views: 'Views',
          videos: 'Videos',
          average: 'Avg views',
          viral: 'Viral videos',
        };

  return (
    <div className="rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-card)] px-3 py-2 text-xs shadow-sm">
      <div className="mb-1 font-semibold text-[color:var(--color-foreground)]">
        {row.label}
      </div>
      <TooltipRow label={labels.views} value={formatCompact(row.totalViews, locale)} />
      <TooltipRow
        label={labels.videos}
        value={row.videoCount.toLocaleString(numberLocale)}
      />
      <TooltipRow label={labels.average} value={formatCompact(row.avgViews, locale)} />
      <TooltipRow
        label={labels.viral}
        value={row.viralCount.toLocaleString(numberLocale)}
      />
    </div>
  );
}

function TooltipRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-32 items-center justify-between gap-4 text-[color:var(--color-muted-foreground)]">
      <span>{label}</span>
      <span className="font-medium text-[color:var(--color-foreground)]">{value}</span>
    </div>
  );
}
