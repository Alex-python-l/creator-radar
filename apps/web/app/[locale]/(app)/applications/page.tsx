import type { Route } from 'next';
import { redirect } from 'next/navigation';

type Props = { params: Promise<{ locale: string }>; searchParams: Promise<{ status?: string; q?: string }> };

export default async function LegacyApplicationsPage({ params, searchParams }: Props) {
  const [{ locale }, search] = await Promise.all([params, searchParams]);
  const query = new URLSearchParams();
  if (search.status) query.set('status', search.status);
  if (search.q) query.set('q', search.q);
  const suffix = query.size ? `?${query}` : '';
  redirect(`/${locale}/admin/applications${suffix}` as Route);
}
