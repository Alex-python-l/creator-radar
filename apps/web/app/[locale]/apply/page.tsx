import type {Metadata} from 'next';
import type {Route} from 'next';
import Image from 'next/image';
import Link from 'next/link';
import {ArrowLeft} from 'lucide-react';
import {getTranslations} from 'next-intl/server';

import irisMark from '@/assets/auth/iris-mark.png';
import watercolorField from '@/assets/auth/iris-watercolor-field-v2.webp';
import {BotanicalAccent} from '@/components/botanical-accent';
import {TrialApplicationForm} from '@/components/marketing/trial-application-form';
import {TRIAL_PLANS, type TrialPlan} from '@/lib/trial-applications';

type Props = {
  params: Promise<{locale: string}>;
  searchParams: Promise<{plan?: string}>;
};

export async function generateMetadata({params}: Pick<Props, 'params'>): Promise<Metadata> {
  const {locale} = await params;
  const t = await getTranslations({locale, namespace: 'applicationForm'});
  return {title: t('metaTitle'), description: t('metaDescription')};
}

export default async function ApplyPage({params, searchParams}: Props) {
  const [{locale}, {plan}] = await Promise.all([params, searchParams]);
  const t = await getTranslations({locale, namespace: 'applicationForm'});
  const initialPlan: TrialPlan = TRIAL_PLANS.includes(plan as TrialPlan) ? (plan as TrialPlan) : 'trial';

  return (
    <main className="relative min-h-screen overflow-hidden bg-[#fbf8f5] px-5 py-8 text-[#171827] sm:px-8 lg:py-12">
      <Image src={watercolorField} alt="" fill priority aria-hidden className="pointer-events-none object-cover opacity-[0.25]" />
      <BotanicalAccent
        variant="cluster"
        sizes="760px"
        priority
        className="absolute -right-52 -top-44 h-[780px] w-auto opacity-[0.15]"
      />
      <BotanicalAccent
        variant="petals"
        sizes="360px"
        motion="drift"
        className="absolute right-[20%] top-12 hidden w-[360px] opacity-[0.14] sm:block"
      />
      <BotanicalAccent
        variant="sprig"
        sizes="640px"
        mirrored
        className="absolute -bottom-6 -left-56 hidden w-[640px] rotate-[9deg] opacity-[0.1] lg:block"
      />
      <BotanicalAccent
        variant="meadow"
        sizes="100vw"
        className="absolute bottom-0 left-0 w-full opacity-[0.07] sm:opacity-[0.1]"
      />
      <div className="relative z-10 mx-auto max-w-[980px]">
        <header className="flex items-center justify-between">
          <Link href={`/${locale}` as Route} className="flex items-center gap-2.5">
            <Image src={irisMark} alt="" className="h-8 w-auto" aria-hidden />
            <span className="text-2xl font-semibold [font-family:var(--font-serif)]">Iris</span>
          </Link>
          <Link
            href={`/${locale}` as Route}
            className="inline-flex items-center gap-2 text-sm font-semibold text-[color:var(--color-primary)] hover:opacity-80"
          >
            <ArrowLeft className="h-4 w-4" />
            {t('back')}
          </Link>
        </header>

        <section className="pb-12 pt-16 sm:pt-20">
          <p className="text-sm font-semibold text-[color:var(--color-primary)]">{t('eyebrow')}</p>
          <h1 className="mt-4 max-w-[760px] text-4xl font-semibold leading-tight [font-family:var(--font-serif)] sm:text-5xl">
            {t('title')}
          </h1>
          <p className="mt-5 max-w-[680px] text-base leading-8 text-[color:var(--color-muted-foreground)]">
            {t('description')}
          </p>
        </section>

        <TrialApplicationForm initialPlan={initialPlan} />
      </div>
    </main>
  );
}
