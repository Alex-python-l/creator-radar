'use client';

import {
  AlertCircle,
  ArrowRight,
  Bell,
  Eye,
  EyeOff,
  Loader2,
  Lock,
  Mail,
  ShieldCheck,
  TrendingUp,
  User,
  UsersRound,
} from 'lucide-react';
import Image from 'next/image';
import {useLocale, useTranslations} from 'next-intl';
import {
  useEffect,
  useId,
  useState,
  type FormEvent,
  type InputHTMLAttributes,
  type ReactNode,
} from 'react';

import irisMark from '@/assets/auth/iris-mark.png';
import irisLogin from '@/assets/auth/iris-login-v2.webp';
import irisRegister from '@/assets/auth/iris-register-v2.webp';
import watercolorField from '@/assets/auth/iris-watercolor-field-v2.webp';
import {BotanicalAccent} from '@/components/botanical-accent';
import {authClient} from '@/lib/auth/client';
import {cn} from '@/lib/utils';

type Mode = 'login' | 'register';
type LoginMethod = 'password' | 'otp';
type OtpStep = 'email' | 'verify';
export type RegisterMode = 'open' | 'closed';

function suggestedUsername(rawEmail: string): string {
  const local = rawEmail.split('@')[0]?.toLowerCase() ?? '';
  const normalized = local.replace(/[^a-z0-9_.]/g, '').slice(0, 30);
  return normalized.length >= 2 ? normalized : 'user';
}

/**
 * 只允许回跳到站内(同源)路径。用浏览器自己的 URL 解析器归一化后比对
 * origin——校验和 location.assign 用同一套 WHATWG 解析(含 TAB/CR/LF
 * 剥离),不存在"检查时字符串 ≠ 使用时字符串"的绕过空间。
 */
function safeNext(raw: string | null): string | null {
  if (!raw || !raw.startsWith('/')) return null;
  try {
    const u = new URL(raw, window.location.origin);
    if (u.origin !== window.location.origin) return null;
    return u.pathname + u.search + u.hash;
  } catch {
    return null;
  }
}

/* 登录页专属配色(奶油底 #fbf8f5/紫 #6750d9),取自设计稿切版,勿外溢复用 */
const inputClass = cn(
  'h-[52px] w-full rounded-[10px] border border-[rgba(86,91,111,0.28)] bg-white/55 pl-12 pr-4 text-[15px] text-[#111827]',
  'placeholder:text-[#a2a7b8]',
  'transition-[border-color,box-shadow,background-color] focus:border-[#6750d9] focus:bg-white focus:outline-none focus:ring-4 focus:ring-[#6750d9]/12',
);
const labelClass = 'mb-2 block text-sm font-medium text-[#111827]';
const hintClass = 'mt-2 text-[13px] text-[#6e7485]';
const iconClass = 'pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-[#9aa0b2]';

function Field({
  label,
  icon,
  hint,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & {label: string; icon: ReactNode; hint?: string}) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className={labelClass}>
        {label}
      </label>
      <div className="relative">
        <span className={iconClass}>{icon}</span>
        <input id={id} className={inputClass} {...rest} />
      </div>
      {hint && <p className={hintClass}>{hint}</p>}
    </div>
  );
}

function PasswordField({
  label,
  hint,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & {label: string; hint?: string}) {
  const id = useId();
  const t = useTranslations('auth');
  const [show, setShow] = useState(false);
  return (
    <div>
      <label htmlFor={id} className={labelClass}>
        {label}
      </label>
      <div className="relative">
        <span className={iconClass}>
          <Lock className="h-[18px] w-[18px]" />
        </span>
        <input
          id={id}
          type={show ? 'text' : 'password'}
          className={cn(inputClass, 'pr-12')}
          {...rest}
        />
        <button
          type="button"
          onClick={() => setShow((s) => !s)}
          aria-label={show ? t('hidePassword') : t('showPassword')}
          className="absolute inset-y-0 right-0 flex w-12 cursor-pointer items-center justify-center text-[#9aa0b2] transition-colors hover:text-[#111827]"
        >
          {show ? <EyeOff className="h-[18px] w-[18px]" /> : <Eye className="h-[18px] w-[18px]" />}
        </button>
      </div>
      {hint && <p className={hintClass}>{hint}</p>}
    </div>
  );
}

function AuthVisual({mode}: {mode: Mode}) {
  const t = useTranslations('auth');
  const registering = mode === 'register';

  useEffect(() => {
    // 首屏稳定后预取注册花图,切换时直接使用静态 WebP,不经过图片优化冷启动。
    const image = new window.Image();
    image.src = irisRegister.src;
  }, []);

  return (
    <div className="pointer-events-none fixed inset-0" aria-hidden="true">
      <Image
        src={watercolorField}
        alt=""
        fill
        priority
        unoptimized
        sizes="(min-width: 640px) 100vw, 1px"
        className="hidden object-cover object-center sm:block"
      />

      <BotanicalAccent
        variant="meadow"
        sizes="100vw"
        className="absolute -bottom-2 left-0 w-full opacity-[0.05] sm:opacity-[0.1] lg:opacity-[0.13]"
      />
      <BotanicalAccent
        variant="sprig"
        sizes="620px"
        mirrored
        className="absolute -left-56 bottom-[3vh] hidden w-[620px] -rotate-[8deg] opacity-[0.09] lg:block"
      />
      <BotanicalAccent
        variant="petals"
        sizes="420px"
        motion="drift"
        className="absolute right-[4vw] top-[7vh] hidden w-[420px] opacity-[0.15] sm:block lg:opacity-[0.2]"
      />

      <div
        className={cn(
          'absolute -right-24 top-24 h-[360px] w-[300px] opacity-[0.16]',
          'sm:-right-16 sm:top-20 sm:h-[520px] sm:w-[400px] sm:opacity-[0.22]',
          'lg:top-auto lg:opacity-100',
          registering
            ? 'lg:-right-[1vw] lg:-bottom-[6vh] lg:h-[112vh] lg:w-[56vw]'
            : 'lg:right-[4vw] lg:-bottom-[12vh] lg:h-[108vh] lg:w-[54vw]',
        )}
      >
        <Image
          key={mode}
          src={registering ? irisRegister : irisLogin}
          alt=""
          fill
          priority={!registering}
          loading={registering ? 'lazy' : undefined}
          unoptimized
          sizes="(min-width: 1024px) 56vw, 400px"
          className="auth-art-enter object-contain object-top lg:object-bottom"
        />
      </div>

      <div className="absolute right-[4vw] top-[11vh] hidden w-[204px] rounded-lg border border-[rgba(93,78,154,0.16)] bg-white/82 p-4 shadow-[0_18px_42px_rgba(55,43,104,0.08)] backdrop-blur-sm lg:block">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#eee9fb] text-[#6750d9]">
            <UsersRound className="h-[18px] w-[18px]" />
          </span>
          <div>
            <p className="text-[12px] font-medium text-[#6e7485]">{t('visualAccounts')}</p>
            <div className="mt-1 flex items-end gap-2">
              <span className="text-[30px] font-semibold leading-none text-[#171a27]">98</span>
              <span className="mb-0.5 flex items-center gap-0.5 text-[11px] font-semibold text-[#2eae68]">
                <TrendingUp className="h-3.5 w-3.5" />
                12%
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="absolute bottom-[17vh] right-[7vw] hidden w-[174px] rounded-lg border border-[rgba(93,78,154,0.16)] bg-white/84 p-4 shadow-[0_18px_42px_rgba(55,43,104,0.08)] backdrop-blur-sm lg:block">
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#eee9fb] text-[#6750d9]">
            <Bell className="h-[18px] w-[18px]" />
          </span>
          <div>
            <p className="text-[12px] font-medium text-[#6e7485]">{t('visualAlerts')}</p>
            <p className="mt-1 text-[26px] font-semibold leading-none text-[#171a27]">3</p>
          </div>
        </div>
      </div>
    </div>
  );
}

export function LoginForm({registerMode}: {registerMode: RegisterMode}) {
  const t = useTranslations('auth');
  const locale = useLocale();
  const [mode, setMode] = useState<Mode>('login');
  const [loginMethod, setLoginMethod] = useState<LoginMethod>('password');
  const [identifier, setIdentifier] = useState('');
  const [email, setEmail] = useState('');
  const [pw, setPw] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [verificationEmail, setVerificationEmail] = useState('');
  const [otpStep, setOtpStep] = useState<OtpStep>('email');
  const [otpEmail, setOtpEmail] = useState('');
  const [otpCode, setOtpCode] = useState('');
  const [otpUsername, setOtpUsername] = useState('');
  const [resendSeconds, setResendSeconds] = useState(0);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const registering = mode === 'register';
  const canRegister = registerMode !== 'closed';

  useEffect(() => {
    if (resendSeconds <= 0) return;
    const timer = window.setTimeout(() => setResendSeconds((seconds) => seconds - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [resendSeconds]);

  const pick = (m: Mode) => {
    setMode(m);
    setErr('');
  };

  const pickLoginMethod = (method: LoginMethod) => {
    setLoginMethod(method);
    setOtpStep('email');
    setOtpCode('');
    setErr('');
  };

  const errorText = (code: string | undefined, message: string | undefined): string => {
    switch (code) {
      case 'INVALID_USERNAME_OR_PASSWORD':
      case 'INVALID_EMAIL_OR_PASSWORD':
        return t('badCredentials');
      case 'USERNAME_IS_ALREADY_TAKEN':
      case 'USERNAME_IS_ALREADY_TAKEN_PLEASE_TRY_ANOTHER':
        return t('usernameTaken');
      case 'USER_ALREADY_EXISTS':
      case 'USER_ALREADY_EXISTS_USE_ANOTHER_EMAIL':
        return t('emailTaken');
      case 'PASSWORD_TOO_SHORT':
        return t('passwordTooShort');
      case 'INVALID_USERNAME':
        return t('invalidUsername');
      case 'EMAIL_NOT_VERIFIED':
        return t('emailNotVerified');
      default:
        if (message === 'registration disabled') return t('registrationDisabled');
        return message ?? t('genericError');
    }
  };

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (registering && pw !== confirmPw) {
      setErr(t('passwordMismatch'));
      return;
    }
    setBusy(true);
    setErr('');

    const next = safeNext(new URLSearchParams(window.location.search).get('next'));
    const callbackURL = next ?? `/${locale}/monitor`;

    const result = registering
      ? await authClient.signUp.email({
          name: identifier,
          username: identifier,
          displayUsername: identifier,
          email,
          password: pw,
          callbackURL,
        })
      : identifier.includes('@')
        ? await authClient.signIn.email({email: identifier, password: pw})
        : await authClient.signIn.username({username: identifier, password: pw});

    setBusy(false);
    if (result.error) {
      if (result.error.code === 'EMAIL_NOT_VERIFIED') {
        setVerificationEmail(identifier.includes('@') ? identifier : 'sent');
        return;
      }
      // 登录失败统一模糊文案(公网不做用户名枚举);注册错误给具体原因。
      setErr(
        registering
          ? errorText(result.error.code, result.error.message)
          : t('badCredentials'),
      );
      return;
    }

    if (registering) {
      setVerificationEmail(email);
      return;
    }
    window.location.assign(next ?? `/${locale}/monitor`);
  }

  async function requestOtp(e?: FormEvent) {
    e?.preventDefault();
    const normalizedEmail = otpEmail.trim().toLowerCase();
    setBusy(true);
    setErr('');
    const result = await authClient.emailOtp.sendVerificationOtp({
      email: normalizedEmail,
      type: 'sign-in',
    });
    setBusy(false);
    if (result.error) {
      setErr(result.error.status === 429 ? t('otpRateLimited') : t('genericError'));
      return false;
    }
    setOtpEmail(normalizedEmail);
    setOtpUsername((current) => current || suggestedUsername(normalizedEmail));
    setOtpCode('');
    setOtpStep('verify');
    setResendSeconds(60);
    return true;
  }

  async function verifyOtp(e: FormEvent) {
    e.preventDefault();
    if (canRegister && !/^[a-zA-Z0-9_.]{2,30}$/.test(otpUsername)) {
      setErr(t('invalidUsername'));
      return;
    }
    setBusy(true);
    setErr('');
    const next = safeNext(new URLSearchParams(window.location.search).get('next'));
    const result = await authClient.signIn.emailOtp({
      email: otpEmail,
      otp: otpCode,
      ...(canRegister
        ? {name: otpUsername, username: otpUsername, displayUsername: otpUsername}
        : {}),
    });
    setBusy(false);
    if (result.error) {
      if (
        result.error.code === 'USERNAME_IS_ALREADY_TAKEN' ||
        result.error.code === 'USERNAME_IS_ALREADY_TAKEN_PLEASE_TRY_ANOTHER'
      ) {
        setOtpCode('');
        const resent = await requestOtp();
        if (resent) setErr(t('otpUsernameTakenResent'));
        return;
      }
      switch (result.error.code) {
        case 'OTP_EXPIRED':
          setErr(t('otpExpired'));
          return;
        case 'TOO_MANY_ATTEMPTS':
          setErr(t('otpTooManyAttempts'));
          return;
        case 'INVALID_OTP':
          setErr(t('otpInvalid'));
          return;
        case 'BANNED_USER':
          setErr(t('otpBanned'));
          return;
        default:
          setErr(result.error.status === 429 ? t('otpRateLimited') : t('genericError'));
          return;
      }
    }
    window.location.assign(next ?? `/${locale}/monitor`);
  }

  if (verificationEmail) {
    const canResend = verificationEmail.includes('@');
    return (
      <div className="flex min-h-svh items-center justify-center bg-[#fbf8f5] p-6 text-[#111827]">
        <div className="w-full max-w-md rounded-2xl border border-[#6750d9]/15 bg-white/80 p-8 text-center shadow-sm">
          <Mail className="mx-auto h-9 w-9 text-[#6750d9]" />
          <h1 className="mt-4 text-2xl font-semibold">{t('checkEmailTitle')}</h1>
          <p className="mt-2 text-sm leading-6 text-[#6e7485]">{t('checkEmailHint')}</p>
          {canResend && (
            <button
              type="button"
              className="mt-5 text-sm font-semibold text-[#6750d9]"
              onClick={async () => {
                const next = safeNext(new URLSearchParams(window.location.search).get('next'));
                await authClient.sendVerificationEmail({
                  email: verificationEmail,
                  callbackURL: next ?? `/${locale}/monitor`,
                });
              }}
            >
              {t('resendVerification')}
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="relative min-h-svh overflow-x-hidden bg-[#fbf8f5] text-[#111827]">
      <AuthVisual mode={mode} />

      <section className="login-enter relative z-10 flex min-h-svh w-full flex-col px-5 py-5 animate-[login-enter_0.55s_cubic-bezier(0.16,1,0.3,1)_both] sm:px-8 sm:py-7 lg:w-[min(620px,46vw)]">
        <header className="mx-auto flex w-full max-w-[410px] items-center">
          <div className="flex items-center gap-2.5">
            <Image src={irisMark} alt="" className="h-9 w-auto" aria-hidden="true" />
            <span className="text-[24px] font-semibold text-[#111827] [font-family:var(--font-serif)]">
              Iris
            </span>
          </div>
        </header>

        <main
          className={cn(
            'mx-auto flex w-full max-w-[410px] flex-1',
            registering
              ? 'items-start py-8 sm:py-10 lg:pt-8'
              : 'items-center py-10 sm:py-12 lg:items-start lg:pb-10 lg:pt-[14vh]',
          )}
        >
          <div className="w-full">
            <div className={cn('mb-8', registering && 'mb-6')}>
              <p className="mb-3 text-[13px] font-semibold text-[#6c51df]">{t('tagline')}</p>
              <h1 className="text-[38px] font-bold leading-[1.12] text-[#111827] sm:text-[42px]">
                {registering ? t('createAccount') : t('welcomeBack')}
              </h1>
              <p className="mt-3 max-w-[370px] text-[15px] leading-relaxed text-[#6e7485]">
                {registering ? t('registerHint') : t('loginHint')}
              </p>
              {canRegister && (
                <p className="mt-5 text-[13px] text-[#6e7485]">
                  {registering ? t('haveAccountPrompt') : t('noAccountPrompt')}{' '}
                  <button
                    type="button"
                    onClick={() => pick(registering ? 'login' : 'register')}
                    className="cursor-pointer font-semibold text-[#6c51df] transition-colors hover:text-[#5b3fd0]"
                  >
                    {registering ? t('login') : t('register')}
                  </button>
                </p>
              )}
            </div>

            {!registering && (
              <div className="mb-5 grid grid-cols-2 rounded-[10px] bg-[#eeeaf5] p-1" role="group" aria-label={t('loginMethod')}>
                {(['password', 'otp'] as const).map((method) => (
                  <button
                    key={method}
                    type="button"
                    aria-pressed={loginMethod === method}
                    onClick={() => pickLoginMethod(method)}
                    className={cn(
                      'h-10 rounded-lg text-sm font-semibold transition',
                      loginMethod === method
                        ? 'bg-white text-[#5b3fd0] shadow-sm'
                        : 'text-[#6e7485] hover:text-[#111827]',
                    )}
                  >
                    {method === 'password' ? t('passwordLogin') : t('otpLogin')}
                  </button>
                ))}
              </div>
            )}

            <form
              onSubmit={
                registering || loginMethod === 'password'
                  ? submit
                  : otpStep === 'email'
                    ? requestOtp
                    : verifyOtp
              }
              className={cn('space-y-5', registering && 'space-y-4')}
            >
              {registering ? (
                <>
                  <Field
                    label={t('username')}
                    icon={<User className="h-[18px] w-[18px]" />}
                    placeholder={t('usernamePlaceholder')}
                    required
                    autoFocus
                    autoComplete="username"
                    value={identifier}
                    onChange={(e) => setIdentifier(e.target.value)}
                  />
                  <Field
                    label={t('email')}
                    icon={<Mail className="h-[18px] w-[18px]" />}
                    placeholder={t('emailPlaceholder')}
                    type="email"
                    required
                    autoComplete="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </>
              ) : loginMethod === 'password' ? (
                <Field
                  label={t('usernameOrEmail')}
                  icon={<Mail className="h-[18px] w-[18px]" />}
                  placeholder={t('identifierPlaceholder')}
                  required
                  autoFocus
                  autoComplete="username"
                  value={identifier}
                  onChange={(e) => setIdentifier(e.target.value)}
                />
              ) : otpStep === 'email' ? (
                <Field
                  label={t('email')}
                  icon={<Mail className="h-[18px] w-[18px]" />}
                  placeholder={t('emailPlaceholder')}
                  type="email"
                  required
                  autoFocus
                  autoComplete="email"
                  value={otpEmail}
                  onChange={(e) => setOtpEmail(e.target.value)}
                />
              ) : (
                <>
                  <div className="flex items-center justify-between rounded-[10px] border border-[#6750d9]/15 bg-white/55 px-4 py-3 text-sm">
                    <span className="truncate text-[#6e7485]">{otpEmail}</span>
                    <button
                      type="button"
                      onClick={() => {
                        setOtpStep('email');
                        setOtpCode('');
                        setErr('');
                      }}
                      className="ml-3 shrink-0 font-semibold text-[#6750d9]"
                    >
                      {t('otpChangeEmail')}
                    </button>
                  </div>
                  <Field
                    label={t('otpCode')}
                    icon={<ShieldCheck className="h-[18px] w-[18px]" />}
                    placeholder={t('otpCodePlaceholder')}
                    required
                    autoFocus
                    autoComplete="one-time-code"
                    inputMode="numeric"
                    pattern="[0-9]{6}"
                    minLength={6}
                    maxLength={6}
                    value={otpCode}
                    onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                  />
                  {canRegister && (
                    <Field
                      label={t('otpUsername')}
                      icon={<User className="h-[18px] w-[18px]" />}
                      placeholder={t('usernamePlaceholder')}
                      required
                      minLength={2}
                      maxLength={30}
                      autoComplete="username"
                      hint={t('otpUsernameHint')}
                      value={otpUsername}
                      onChange={(e) => setOtpUsername(e.target.value)}
                    />
                  )}
                  <div className="flex justify-end">
                    <button
                      type="button"
                      disabled={busy || resendSeconds > 0}
                      onClick={() => void requestOtp()}
                      className="text-sm font-semibold text-[#6750d9] disabled:cursor-not-allowed disabled:text-[#9aa0b2]"
                    >
                      {resendSeconds > 0
                        ? t('otpResendCountdown', {seconds: resendSeconds})
                        : t('otpResend')}
                    </button>
                  </div>
                </>
              )}
              {(registering || loginMethod === 'password') && (
                <PasswordField
                  label={t('password')}
                  placeholder={
                    registering ? t('passwordPlaceholderCreate') : t('passwordPlaceholderLogin')
                  }
                  required
                  minLength={registering ? 8 : undefined}
                  autoComplete={registering ? 'new-password' : 'current-password'}
                  hint={registering ? t('passwordHint') : undefined}
                  value={pw}
                  onChange={(e) => setPw(e.target.value)}
                />
              )}
              {registering && (
                <PasswordField
                  label={t('confirmPassword')}
                  placeholder={t('confirmPlaceholder')}
                  required
                  minLength={8}
                  autoComplete="new-password"
                  value={confirmPw}
                  onChange={(e) => setConfirmPw(e.target.value)}
                />
              )}
              {err && (
                <div
                  role="alert"
                  className="flex items-start gap-2 rounded-[10px] border border-red-200 bg-red-50 px-4 py-3 text-[13.5px] leading-snug text-red-600"
                >
                  <AlertCircle className="mt-px h-4 w-4 shrink-0" />
                  <span>{err}</span>
                </div>
              )}

              <button
                type="submit"
                disabled={busy}
                className={cn(
                  'group relative mt-1 flex h-[52px] w-full cursor-pointer items-center justify-center rounded-[10px] text-[16px] font-semibold text-white',
                  'bg-[linear-gradient(102deg,#513093_0%,#6f50ce_100%)] shadow-[0_10px_24px_rgba(82,52,155,0.12)]',
                  'transition-[transform,box-shadow,filter] hover:-translate-y-px hover:shadow-[0_13px_28px_rgba(82,52,155,0.2)] hover:brightness-105',
                  'active:translate-y-px',
                  'disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0 disabled:hover:brightness-100',
                )}
              >
                {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {registering
                  ? t('registerSubmit')
                  : loginMethod === 'password'
                    ? t('loginSubmit')
                    : otpStep === 'email'
                      ? t('otpSend')
                      : t('otpVerify')}
                <ArrowRight className="absolute right-5 h-[18px] w-[18px] transition-transform group-hover:translate-x-1" />
              </button>
            </form>

            <div className="mt-7 flex items-center gap-2.5 text-[13px] text-[#6e7485]">
              <ShieldCheck className="h-[18px] w-[18px] text-[#6750d9]" aria-hidden="true" />
              {t('securityNote')}
            </div>
          </div>
        </main>
      </section>
    </div>
  );
}
