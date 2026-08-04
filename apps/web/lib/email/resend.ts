import { env } from '@/lib/env';

const EMAIL_WINDOW_MS = 60 * 60 * 1000;
const EMAIL_MAX_PER_WINDOW = 10;
const deliveries = new Map<string, number[]>();

function enforceRecipientRateLimit(email: string) {
  const key = email.trim().toLowerCase();
  const cutoff = Date.now() - EMAIL_WINDOW_MS;
  const recent = (deliveries.get(key) ?? []).filter((sentAt) => sentAt > cutoff);
  if (recent.length >= EMAIL_MAX_PER_WINDOW) {
    throw new Error('too many emails sent to this address; retry later');
  }
  recent.push(Date.now());
  deliveries.set(key, recent);
}

export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => {
    const entities: Record<string, string> = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    };
    return entities[character];
  });
}

export async function sendTransactionalEmail(input: {
  to: string;
  subject: string;
  text: string;
  html: string;
}): Promise<void> {
  enforceRecipientRateLimit(input.to);
  if (!env.RESEND_API_KEY || !env.RESEND_FROM) {
    throw new Error('Resend email credentials are not configured');
  }

  const response = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.RESEND_API_KEY}`,
      'Content-Type': 'application/json',
      'User-Agent': 'Iris/1.0',
    },
    body: JSON.stringify({
      from: env.RESEND_FROM,
      to: [input.to],
      subject: input.subject,
      text: input.text,
      html: input.html,
    }),
  });
  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as { message?: string } | null;
    throw new Error(`Resend email failed (${response.status}): ${error?.message ?? 'unknown error'}`);
  }
}
