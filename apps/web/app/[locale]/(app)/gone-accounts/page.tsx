import type { Route } from 'next';
import Link from 'next/link';
import { connection } from 'next/server';
import { getTranslations } from 'next-intl/server';

import { PlatformMark, formatDate, formatRelative } from '@/components/format';
import { Card, Td, Th } from '@/components/ui';
import { formatAccountListLabel, formatAccountRawIdentifier } from '@/lib/account-display';
import { requireViewer } from '@/lib/auth/viewer';
import { listGoneAccounts } from '@/lib/queries/influencers';
import { isPlatform, platformProfileUrl } from '@svm/shared/platforms';

export const dynamic = 'force-dynamic';

export default async function GoneAccountsPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  await connection();

  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'goneAccountsPage' });
  const viewer = await requireViewer();
  const accounts = await listGoneAccounts(viewer);

  return (
    <div className="mx-auto max-w-6xl px-8 py-10">
      <div className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight">{t('title')}</h1>
        <p className="mt-2 text-sm text-[color:var(--color-muted-foreground)]">
          {t('description')}
        </p>
      </div>

      <Card>
        {accounts.length === 0 ? (
          <div className="px-6 py-12 text-center text-sm text-[color:var(--color-muted-foreground)]">
            {t('empty')}
          </div>
        ) : (
          <div className="overflow-hidden">
            <table className="w-full table-fixed">
              <colgroup>
                <col className="w-[8%]" />
                <col className="w-[11%]" />
                <col className="w-[24%]" />
                <col className="w-[13%]" />
                <col className="w-[14%]" />
                <col className="w-[15%]" />
                <col className="w-[15%]" />
              </colgroup>
              <thead className="border-b border-[color:var(--color-border)]">
                <tr>
                  <Th>ID</Th>
                  <Th>{t('platform')}</Th>
                  <Th>{t('account')}</Th>
                  <Th>{t('group')}</Th>
                  <Th>{t('importedAt')}</Th>
                  <Th>{t('confirmedAt')}</Th>
                  <Th>{t('reason')}</Th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((account) => {
                  const accountLabel = formatAccountListLabel(account);
                  const rawIdentifier = formatAccountRawIdentifier(account);
                  const confirmedAt =
                    account.latestFetchFinishedAt ?? account.latestFetchStartedAt;
                  return (
                    <tr
                      key={account.id}
                      className="border-b border-[color:var(--color-border)] last:border-0"
                    >
                      <Td>
                        <span className="inline-flex items-center justify-center rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-muted)] px-1.5 py-0.5 text-xs font-mono font-semibold text-[color:var(--color-muted-foreground)]">
                          #{account.id}
                        </span>
                      </Td>
                      <Td>
                        <PlatformMark platform={account.platform} />
                      </Td>
                      <Td className="overflow-hidden">
                        <Link
                          href={`/${locale}/accounts/${account.id}` as Route}
                          className="block truncate font-semibold text-[color:var(--color-destructive)] hover:underline"
                          title={rawIdentifier}
                        >
                          {accountLabel}
                        </Link>
                        {isPlatform(account.platform) && (
                          <a
                            href={platformProfileUrl(account.platform, account.username)}
                            target="_blank"
                            rel="noreferrer"
                            className="mt-0.5 block truncate text-xs text-[color:var(--color-muted-foreground)] hover:underline"
                          >
                            {rawIdentifier}
                          </a>
                        )}
                      </Td>
                      <Td className="truncate">{account.groupName}</Td>
                      <Td className="whitespace-nowrap text-[color:var(--color-muted-foreground)]">
                        {formatDate(account.createdAt, locale)}
                      </Td>
                      <Td className="whitespace-nowrap text-[color:var(--color-muted-foreground)]">
                        {formatRelative(confirmedAt, locale)}
                      </Td>
                      <Td className="truncate text-[color:var(--color-destructive)]">
                        {account.latestFetchError ?? t('accountGone')}
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
