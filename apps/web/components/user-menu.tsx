'use client';

import {KeyRound, LogOut, ShieldCheck, UserRound} from 'lucide-react';
import {useLocale, useTranslations} from 'next-intl';
import {useState, type FormEvent} from 'react';

import {authClient} from '@/lib/auth/client';
import {Button, Input} from '@/components/ui';

/**
 * 侧栏底部的当前用户菜单:改密 + 登出。
 * 登出后整页跳登录页(清 client 侧缓存,与 RSC 状态彻底断开)。
 */
export function UserMenu({username, isAdmin = false}: {username: string; isAdmin?: boolean}) {
  const t = useTranslations('auth');
  const locale = useLocale();
  const [open, setOpen] = useState(false);
  const [changingPw, setChangingPw] = useState(false);

  const logout = async () => {
    try {
      await authClient.signOut();
    } catch {
      // 网络失败也照样跳登录页,下一次请求的 401 会兜底。
    }
    window.location.assign(`/${locale}/login`);
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex h-8 w-full items-center gap-1.5 rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-card)] px-2 text-xs font-semibold text-[color:var(--color-foreground)] transition hover:bg-[color:var(--color-muted)]"
      >
        <UserRound className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span className="truncate">{username}</span>
      </button>
      {open && (
        <div className="absolute bottom-full left-0 z-20 mb-1 w-full overflow-hidden rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-popover)] shadow-md">
          {isAdmin && (
            <MenuItem
              icon={<ShieldCheck className="h-3.5 w-3.5" aria-hidden />}
              label={t('adminConsole')}
              onClick={() => window.location.assign(`/${locale}/admin`)}
            />
          )}
          <MenuItem
            icon={<KeyRound className="h-3.5 w-3.5" aria-hidden />}
            label={t('changePassword')}
            onClick={() => {
              setOpen(false);
              setChangingPw(true);
            }}
          />
          <MenuItem
            icon={<LogOut className="h-3.5 w-3.5" aria-hidden />}
            label={t('logout')}
            onClick={logout}
          />
        </div>
      )}
      {changingPw && <ChangePasswordDialog onClose={() => setChangingPw(false)} />}
    </div>
  );
}

function MenuItem({
  icon,
  label,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left text-xs font-medium text-[color:var(--color-foreground)] hover:bg-[color:var(--color-muted)]"
    >
      {icon}
      {label}
    </button>
  );
}

function ChangePasswordDialog({onClose}: {onClose: () => void}) {
  const t = useTranslations('auth');
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [err, setErr] = useState('');
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (next !== confirm) {
      setErr(t('passwordMismatch'));
      return;
    }
    setBusy(true);
    setErr('');
    const {error} = await authClient.changePassword({
      currentPassword: current,
      newPassword: next,
      revokeOtherSessions: true,
    });
    setBusy(false);
    if (error) {
      setErr(error.message ?? t('changePasswordFailed'));
      return;
    }
    setOk(true);
    setTimeout(onClose, 900);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      onClick={onClose}
    >
      <form
        onSubmit={submit}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-xs space-y-3 rounded-xl border border-[color:var(--color-border)] bg-[color:var(--color-card)] p-4 shadow-lg"
      >
        <div className="text-sm font-semibold text-[color:var(--color-foreground)]">
          {t('changePassword')}
        </div>
        <Input
          type="password"
          required
          autoComplete="current-password"
          placeholder={t('currentPassword')}
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
        <Input
          type="password"
          required
          minLength={8}
          autoComplete="new-password"
          placeholder={t('newPassword')}
          value={next}
          onChange={(e) => setNext(e.target.value)}
        />
        <Input
          type="password"
          required
          minLength={8}
          autoComplete="new-password"
          placeholder={t('confirmPassword')}
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
        {err && <p className="text-xs text-[color:var(--color-destructive)]">{err}</p>}
        {ok && <p className="text-xs text-[color:var(--color-tier-growing)]">{t('changePasswordOk')}</p>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            {t('cancel')}
          </Button>
          <Button type="submit" disabled={busy}>
            {t('save')}
          </Button>
        </div>
      </form>
    </div>
  );
}
