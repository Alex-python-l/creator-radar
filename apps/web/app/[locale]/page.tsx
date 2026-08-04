import type {Metadata} from 'next';
import type {Route} from 'next';
import Image from 'next/image';
import Link from 'next/link';
import {redirect} from 'next/navigation';
import {
  ArrowRight,
  CalendarClock,
  ChartNoAxesCombined,
  MessageSquareText,
  LockKeyhole,
  Smartphone,
} from 'lucide-react';
import {getTranslations} from 'next-intl/server';

import irisMark from '@/assets/auth/iris-mark.png';
import watercolorField from '@/assets/auth/iris-watercolor-field-v2.webp';
import dashboardDemo from '@/assets/marketing/iris-monitor-dashboard-demo.png';
import opsDashboardDemo from '@/assets/marketing/iris-ops-dashboard-demo.png';
import {BotanicalAccent} from '@/components/botanical-accent';
import {PlatformMark} from '@/components/format';
import {getViewer} from '@/lib/auth/viewer';

export const dynamic = 'force-dynamic';

type Props = {
  params: Promise<{locale: string}>;
};

export async function generateMetadata({params}: Props): Promise<Metadata> {
  const {locale} = await params;
  const t = await getTranslations({locale, namespace: 'marketing'});
  return {
    title: t('metaTitle'),
    description: t('metaDescription'),
    alternates: {
      languages: {zh: '/zh', en: '/en'},
    },
  };
}

const platformNames = ['tiktok', 'instagram', 'facebook', 'youtube'] as const;
const platformLabels = {tiktok: 'TikTok', instagram: 'Instagram', facebook: 'Facebook', youtube: 'YouTube'};
const featureIcons = [ChartNoAxesCombined, Smartphone, CalendarClock, MessageSquareText];

export default async function MarketingHome({params}: Props) {
  const {locale} = await params;
  const viewer = await getViewer();
  if (viewer) redirect(`/${locale}/monitor` as Route);

  const t = await getTranslations({locale, namespace: 'marketing'});
  const otherLocale = locale === 'zh' ? 'en' : 'zh';
  const languageLabel = locale === 'zh' ? 'English' : '中文';

  const applyHref = (plan: 'trial' | 'pro' | 'scale' | 'custom') =>
    `/${locale}/apply?plan=${plan}` as Route;

  return (
    <main className="relative overflow-hidden bg-[#fbf8f5] text-[#171827]">
      <Image
        src={watercolorField}
        alt=""
        fill
        priority
        aria-hidden
        className="pointer-events-none absolute inset-0 object-cover opacity-[0.22]"
      />

      <div className="relative z-10">
        <header className="mx-auto flex h-20 max-w-[1240px] items-center justify-between px-5 sm:px-8">
          <Link href={`/${locale}` as Route} className="flex items-center gap-2.5">
            <Image src={irisMark} alt="" className="h-8 w-auto" aria-hidden />
            <span className="text-[24px] font-semibold [font-family:var(--font-serif)]">Iris</span>
          </Link>
          <nav className="hidden items-center gap-8 text-sm font-medium md:flex" aria-label={t('navAria')}>
            <a className="transition hover:text-[color:var(--color-primary)]" href="#product">{t('navProduct')}</a>
            <a className="transition hover:text-[color:var(--color-primary)]" href="#pricing">{t('navPricing')}</a>
            <a className="transition hover:text-[color:var(--color-primary)]" href="#about">{t('navAbout')}</a>
            <Link className="transition hover:text-[color:var(--color-primary)]" href={`/${otherLocale}` as Route}>
              {languageLabel}
            </Link>
          </nav>
          <div className="flex items-center gap-3">
            <Link
              href={`/${locale}/login` as Route}
              className="text-sm font-semibold transition hover:text-[color:var(--color-primary)]"
            >
              {t('login')}
            </Link>
            <Link
              href={applyHref('trial')}
              className="inline-flex h-10 items-center rounded-lg bg-[color:var(--color-primary)] px-4 text-sm font-semibold text-white transition hover:-translate-y-0.5 hover:shadow-lg"
            >
              {t('apply')}
            </Link>
          </div>
        </header>

        <section className="relative mx-auto grid min-h-[650px] max-w-[1240px] items-center gap-12 px-5 pb-16 pt-12 sm:px-8 lg:grid-cols-[0.95fr_1.05fr] lg:pb-24 lg:pt-16">
          <BotanicalAccent
            variant="petals"
            sizes="340px"
            motion="drift"
            className="absolute -bottom-8 -right-14 w-[190px] opacity-[0.1] sm:bottom-auto sm:left-[36%] sm:right-auto sm:top-10 sm:w-[340px] sm:opacity-[0.18]"
          />
          <div className="relative z-10 max-w-[540px]">
            <p className="text-sm font-semibold text-[color:var(--color-primary)]">{t('eyebrow')}</p>
            <h1 className="mt-5 text-[42px] font-semibold leading-[1.14] tracking-[-0.025em] [font-family:var(--font-serif)] sm:text-[54px]">
              {t('heroTitle')}
            </h1>
            <p className="mt-6 max-w-[510px] text-base leading-8 text-[#666273]">{t('heroDescription')}</p>
            <div className="mt-8 flex flex-col gap-3 sm:flex-row">
              <Link
                href={applyHref('trial')}
                className="inline-flex h-13 items-center justify-center gap-3 rounded-lg bg-[color:var(--color-primary)] px-7 text-base font-semibold text-white shadow-[0_12px_30px_rgba(96,69,194,0.16)] transition hover:-translate-y-0.5"
              >
                {t('apply')}
                <ArrowRight className="h-4 w-4" />
              </Link>
              <a
                href="#product"
                className="inline-flex h-13 items-center justify-center rounded-lg border border-[color:var(--color-primary)] bg-white/45 px-7 text-base font-semibold text-[color:var(--color-primary)] transition hover:bg-white/80"
              >
                {t('viewProduct')}
              </a>
            </div>
            <p className="mt-7 flex items-center gap-2 text-sm text-[#747080]">
              <LockKeyhole className="h-4 w-4 text-[color:var(--color-primary)]" />
              {t('inviteOnly')}
            </p>
          </div>

          <div className="relative min-w-0 lg:-mr-24">
            <BotanicalAccent
              variant="cluster"
              sizes="560px"
              priority
              motion="sway"
              className="absolute -right-8 -top-64 z-0 hidden h-[570px] w-auto opacity-[0.2] lg:block"
            />
            <BotanicalAccent
              variant="sprig"
              sizes="520px"
              mirrored
              className="absolute -bottom-20 -left-36 z-0 hidden w-[520px] rotate-[7deg] opacity-[0.12] sm:block"
            />
            <div className="relative z-10 space-y-3 sm:pb-24">
              <div className="overflow-hidden rounded-2xl border border-[#ded8e7] bg-white/80 p-2 shadow-[0_28px_80px_rgba(52,39,101,0.13)] backdrop-blur-sm sm:w-[88%]">
                <Image
                  src={dashboardDemo}
                  alt={t('monitorDashboardAlt')}
                  priority
                  sizes="(max-width: 640px) 94vw, (max-width: 1024px) 82vw, 670px"
                  className="h-auto w-full rounded-xl"
                />
              </div>
              <div className="overflow-hidden rounded-2xl border border-[#ded8e7] bg-white/90 p-2 shadow-[0_24px_64px_rgba(52,39,101,0.16)] backdrop-blur-sm sm:absolute sm:bottom-0 sm:right-0 sm:w-[72%]">
                <Image
                  src={opsDashboardDemo}
                  alt={t('opsDashboardAlt')}
                  priority
                  sizes="(max-width: 640px) 94vw, (max-width: 1024px) 68vw, 550px"
                  className="h-auto w-full rounded-xl"
                />
              </div>
            </div>
          </div>
        </section>

        <section className="border-y border-[#e7e1eb] bg-white/55 py-6" aria-label={t('platformsAria')}>
          <div className="mx-auto flex max-w-[900px] flex-wrap items-center justify-center gap-x-10 gap-y-4 px-5 sm:justify-between">
            {platformNames.map((platform) => (
              <div key={platform} className="flex items-center gap-2 text-sm font-medium">
                <PlatformMark platform={platform} />
                <span>{platformLabels[platform]}</span>
              </div>
            ))}
          </div>
        </section>

        <section id="product" className="mx-auto max-w-[1240px] scroll-mt-10 px-5 py-20 sm:px-8 lg:py-28">
          <div className="relative overflow-hidden rounded-[24px] border border-[#e6dff0] bg-[#f7f2fc]/88 px-6 py-12 sm:px-10 lg:px-14">
            <BotanicalAccent
              variant="sprig"
              sizes="660px"
              mirrored
              className="absolute -bottom-28 -right-40 w-[660px] -rotate-[8deg] opacity-[0.15]"
            />
            <BotanicalAccent
              variant="petals"
              sizes="220px"
              className="absolute -left-20 -top-14 w-[220px] opacity-[0.1]"
            />
            <div className="relative">
              <h2 className="text-center text-3xl font-semibold [font-family:var(--font-serif)]">{t('featuresTitle')}</h2>
              <div className="mt-12 grid gap-8 sm:grid-cols-2 lg:grid-cols-4 lg:gap-0">
                {featureIcons.map((Icon, index) => (
                  <article key={index} className="px-2 lg:border-l lg:border-[#ded7e8] lg:px-8 first:lg:border-0">
                    <Icon className="h-8 w-8 text-[color:var(--color-primary)]" strokeWidth={1.8} />
                    <h3 className="mt-5 text-base font-semibold">{t(`feature${index + 1}Title`)}</h3>
                    <p className="mt-3 text-sm leading-7 text-[#6d6878]">{t(`feature${index + 1}Description`)}</p>
                  </article>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section id="about" className="relative mx-auto grid max-w-[1160px] scroll-mt-10 gap-12 px-5 pb-24 sm:px-8 lg:grid-cols-[0.92fr_1.08fr] lg:items-center lg:pb-28">
          <BotanicalAccent
            variant="petals"
            sizes="280px"
            motion="drift"
            className="absolute -left-44 bottom-2 w-[280px] opacity-[0.12]"
          />
          <div className="relative z-10">
            <p className="text-sm font-semibold text-[color:var(--color-primary)]">{t('workflowEyebrow')}</p>
            <h2 className="mt-4 text-4xl font-semibold leading-tight [font-family:var(--font-serif)]">{t('workflowTitle')}</h2>
            <div className="mt-9 divide-y divide-[#e3dde7]">
              {[1, 2, 3].map((item) => (
                <article key={item} className="grid grid-cols-[58px_1fr] gap-4 py-6 first:pt-0">
                  <div className="text-4xl text-[#cfc3ef] [font-family:var(--font-serif)]">0{item}</div>
                  <div>
                    <h3 className="font-semibold">{t(`workflow${item}Title`)}</h3>
                    <p className="mt-2 text-sm leading-7 text-[#6d6878]">{t(`workflow${item}Description`)}</p>
                  </div>
                </article>
              ))}
            </div>
          </div>
          <div className="relative z-10 overflow-hidden rounded-2xl border border-[#ded8e7] bg-white/80 p-2 shadow-[0_22px_60px_rgba(52,39,101,0.1)]">
            <Image
              src={opsDashboardDemo}
              alt={t('opsDashboardAlt')}
              sizes="(max-width: 1024px) 94vw, 620px"
              className="h-auto w-full rounded-xl"
            />
          </div>
        </section>

        <section id="pricing" className="relative scroll-mt-10 overflow-hidden border-y border-[#e8e1eb] bg-white/48 py-20 lg:py-24">
          <BotanicalAccent
            variant="cluster"
            sizes="440px"
            mirrored
            className="absolute -bottom-44 -left-32 hidden h-[520px] w-auto opacity-[0.1] lg:block"
          />
          <BotanicalAccent
            variant="petals"
            sizes="300px"
            motion="drift"
            className="absolute -right-24 top-8 w-[300px] opacity-[0.11]"
          />
          <div className="relative mx-auto max-w-[1050px] px-5 sm:px-8">
            <h2 className="text-center text-4xl font-semibold [font-family:var(--font-serif)]">{t('pricingTitle')}</h2>
            <p className="mt-3 text-center text-sm text-[#726d7b]">{t('pricingDescription')}</p>
            <div className="mt-12 grid gap-5 md:grid-cols-2 xl:grid-cols-4">
              {(['trial', 'pro', 'scale', 'custom'] as const).map((plan) => (
                <article
                  key={plan}
                  className="flex min-h-[290px] flex-col rounded-2xl border border-[#ded8e7] bg-white/80 p-7 shadow-[0_12px_32px_rgba(49,37,91,0.06)]"
                >
                  <h3 className="text-2xl font-semibold [font-family:var(--font-serif)]">{t(`${plan}Name`)}</h3>
                  <p className="mt-2 text-sm text-[#716c7b]">{t(`${plan}Capacity`)}</p>
                  <div className="mt-7 flex items-end gap-2">
                    <span className="text-4xl font-semibold tracking-tight">{t(`${plan}Price`)}</span>
                    {plan === 'pro' || plan === 'scale' ? <span className="pb-1 text-sm text-[#716c7b]">{t('perMonth')}</span> : null}
                  </div>
                  <p className="mt-5 text-sm leading-6 text-[#716c7b]">{t('planIncludes')}</p>
                  <Link
                    href={applyHref(plan)}
                    className={`mt-auto inline-flex h-11 items-center justify-center gap-2 rounded-lg px-5 text-sm font-semibold transition ${
                      plan === 'custom'
                        ? 'border border-[color:var(--color-primary)] text-[color:var(--color-primary)] hover:bg-[color:var(--color-muted)]'
                        : 'bg-[color:var(--color-primary)] text-white hover:opacity-90'
                    }`}
                  >
                    {plan === 'custom' ? t('contactUs') : t('apply')}
                    <ArrowRight className="h-4 w-4" />
                  </Link>
                </article>
              ))}
            </div>
            <p className="mx-auto mt-7 max-w-[760px] text-center text-sm leading-6 text-[#716c7b]">
              {t('pricingFootnote')}
            </p>
          </div>
        </section>

        <section className="relative overflow-hidden bg-[#f1eafa]/80 py-12">
          <BotanicalAccent
            variant="meadow"
            sizes="100vw"
            className="absolute bottom-0 right-0 w-full opacity-[0.2]"
          />
          <BotanicalAccent
            variant="sprig"
            sizes="520px"
            className="absolute -right-32 -top-28 hidden w-[520px] rotate-[8deg] opacity-[0.14] md:block"
          />
          <div className="relative mx-auto flex max-w-[1100px] flex-col items-start justify-between gap-6 px-5 sm:px-8 md:flex-row md:items-center">
            <div>
              <h2 className="text-3xl font-semibold text-[color:var(--color-primary)] [font-family:var(--font-serif)]">{t('ctaTitle')}</h2>
              <p className="mt-2 text-sm text-[#6d6878]">{t('inviteOnly')}</p>
            </div>
            <Link
              href={applyHref('trial')}
              className="inline-flex h-12 items-center gap-3 rounded-lg bg-[color:var(--color-primary)] px-7 text-sm font-semibold text-white transition hover:-translate-y-0.5"
            >
              {t('apply')}
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </section>

        <footer className="relative overflow-hidden bg-white/60 py-10">
          <BotanicalAccent
            variant="sprig"
            sizes="480px"
            mirrored
            className="absolute -bottom-24 -right-36 w-[480px] -rotate-[6deg] opacity-[0.08]"
          />
          <div className="relative mx-auto flex max-w-[1100px] flex-col justify-between gap-7 px-5 text-sm text-[#6d6878] sm:px-8 md:flex-row">
            <div>
              <div className="flex items-center gap-2 text-[#171827]">
                <Image src={irisMark} alt="" className="h-7 w-auto" aria-hidden />
                <span className="text-xl font-semibold [font-family:var(--font-serif)]">Iris</span>
              </div>
              <p className="mt-3 max-w-[360px] leading-6">{t('footerDescription')}</p>
            </div>
            <div className="flex gap-8">
              <a href="#product" className="hover:text-[color:var(--color-primary)]">{t('navProduct')}</a>
              <a href="#pricing" className="hover:text-[color:var(--color-primary)]">{t('navPricing')}</a>
              <Link href={`/${otherLocale}` as Route} className="hover:text-[color:var(--color-primary)]">{languageLabel}</Link>
              <Link href={`/${locale}/login` as Route} className="hover:text-[color:var(--color-primary)]">{t('login')}</Link>
              <Link href={applyHref('trial')} className="hover:text-[color:var(--color-primary)]">{t('apply')}</Link>
            </div>
          </div>
        </footer>
      </div>
    </main>
  );
}
