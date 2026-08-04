'use client';

import {ArrowRight, CheckCircle2, Loader2} from 'lucide-react';
import Link from 'next/link';
import {useActionState} from 'react';
import {useLocale, useTranslations} from 'next-intl';

import {submitTrialApplication} from '@/lib/actions/trial-applications';
import type {TrialApplicationState, TrialPlan} from '@/lib/trial-applications';

const fieldClass =
  'mt-2 h-11 w-full rounded-lg border border-[color:var(--color-input)] bg-white/75 px-3.5 text-sm text-[color:var(--color-foreground)] outline-none transition focus:border-[color:var(--color-primary)] focus:ring-4 focus:ring-[color:var(--color-primary)]/10';

export function TrialApplicationForm({initialPlan}: {initialPlan: TrialPlan}) {
  const locale = useLocale();
  const t = useTranslations('applicationForm');
  const defaultExpected = {trial: '20', pro: '50', scale: '200', custom: '500'}[initialPlan];
  const [state, action, pending] = useActionState(submitTrialApplication, {
    values: {
      companyName: '',
      contactName: '',
      workEmail: '',
      wechat: '',
      expectedAccounts: defaultExpected,
      plan: initialPlan,
      notes: '',
      consent: false,
    },
  } satisfies TrialApplicationState);

  if (state.ok) {
    return (
      <div className="rounded-2xl border border-[color:var(--color-border)] bg-white/80 p-7 shadow-sm sm:p-9">
        <CheckCircle2 className="h-9 w-9 text-[color:var(--color-primary)]" />
        <h2 className="mt-5 text-2xl font-semibold [font-family:var(--font-serif)]">{t('successTitle')}</h2>
        <p className="mt-3 max-w-xl text-sm leading-7 text-[color:var(--color-muted-foreground)]">
          {t('successDescription')}
        </p>
        <ol className="mt-6 grid gap-3 text-sm sm:grid-cols-3">
          {[t('stepVerify'), t('stepTeam'), t('stepApproval')].map((step, index) => (
            <li key={step} className="rounded-xl bg-[color:var(--color-muted)]/55 p-4">
              <span className="mr-2 font-semibold text-[color:var(--color-primary)]">0{index + 1}</span>
              {step}
            </li>
          ))}
        </ol>
        <Link
          href={`/${locale}/login`}
          className="mt-7 inline-flex h-11 items-center gap-2 rounded-lg bg-[color:var(--color-primary)] px-5 text-sm font-semibold text-white transition hover:opacity-90"
        >
          {t('createAccount')}
          <ArrowRight className="h-4 w-4" />
        </Link>
      </div>
    );
  }

  return (
    <form action={action} className="rounded-2xl border border-[color:var(--color-border)] bg-white/80 p-6 shadow-sm sm:p-8">
      <input type="hidden" name="locale" value={locale} />
      <div className="absolute -left-[9999px]" aria-hidden="true">
        <label>
          Website
          <input name="website" tabIndex={-1} autoComplete="off" />
        </label>
      </div>

      <div className="grid gap-5 sm:grid-cols-2">
        <label className="text-sm font-medium">
          {t('company')}
          <input className={fieldClass} name="companyName" required maxLength={120} defaultValue={state.values?.companyName} />
        </label>
        <label className="text-sm font-medium">
          {t('contactName')}
          <input className={fieldClass} name="contactName" required maxLength={80} defaultValue={state.values?.contactName} />
        </label>
        <label className="text-sm font-medium">
          {t('workEmail')}
          <input className={fieldClass} name="workEmail" type="email" maxLength={160} placeholder="name@company.com" defaultValue={state.values?.workEmail} />
        </label>
        <label className="text-sm font-medium">
          {t('wechat')}
          <input className={fieldClass} name="wechat" maxLength={80} defaultValue={state.values?.wechat} />
        </label>
        <label className="text-sm font-medium">
          {t('expectedAccounts')}
          <select className={fieldClass} name="expectedAccounts" defaultValue={state.values?.expectedAccounts ?? defaultExpected}>
            <option value="20">1–20</option>
            <option value="50">21–50</option>
            <option value="200">51–200</option>
            <option value="500">201–500</option>
            <option value="1000">500+</option>
          </select>
        </label>
        <label className="text-sm font-medium">
          {t('plan')}
          <select className={fieldClass} name="plan" defaultValue={state.values?.plan ?? initialPlan}>
            <option value="trial">{t('planTrial')}</option>
            <option value="pro">Pro</option>
            <option value="scale">Scale</option>
            <option value="custom">Custom</option>
          </select>
        </label>
      </div>

      <label className="mt-5 block text-sm font-medium">
        {t('notes')}
        <textarea
          className="mt-2 min-h-28 w-full resize-y rounded-lg border border-[color:var(--color-input)] bg-white/75 px-3.5 py-3 text-sm outline-none transition focus:border-[color:var(--color-primary)] focus:ring-4 focus:ring-[color:var(--color-primary)]/10"
          name="notes"
          maxLength={1000}
          defaultValue={state.values?.notes}
        />
      </label>

      <label className="mt-5 flex items-start gap-3 text-sm leading-6 text-[color:var(--color-muted-foreground)]">
        <input className="mt-1 h-4 w-4 accent-[color:var(--color-primary)]" type="checkbox" name="consent" required defaultChecked={state.values?.consent} />
        <span>{t('consent')}</span>
      </label>
      <p className="mt-2 text-xs text-[color:var(--color-muted-foreground)]">{t('contactHint')}</p>

      {state.error ? (
        <p role="alert" className="mt-5 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {state.error === 'contact_required'
            ? t('contactRequired')
            : state.error === 'invalid_email'
              ? t('invalidEmail')
            : state.error === 'rate_limited'
              ? t('rateLimited')
              : t('invalidForm')}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={pending}
        className="mt-6 inline-flex h-12 w-full items-center justify-center gap-2 rounded-lg bg-[color:var(--color-primary)] px-6 text-sm font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
      >
        {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
        {pending ? t('submitting') : t('submit')}
        {!pending ? <ArrowRight className="h-4 w-4" /> : null}
      </button>
    </form>
  );
}
