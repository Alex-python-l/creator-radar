import { revalidatePath } from 'next/cache';

const LOCALES = ['zh', 'en'] as const;

export function revalidateAccounts(accountId?: number) {
  revalidatePath('/monitor');
  for (const locale of LOCALES) {
    revalidatePath(`/${locale}/monitor`);
    revalidatePath(`/${locale}/competitors`);
    revalidatePath(`/${locale}/videos`);
    if (accountId) revalidatePath(`/${locale}/accounts/${accountId}`);
  }
}
