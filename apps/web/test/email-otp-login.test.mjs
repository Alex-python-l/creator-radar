import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

async function source(path) {
  return readFile(new URL(`../${path}`, import.meta.url), 'utf8');
}

test('email OTP uses the existing auth, verification table and mail delivery stack', async () => {
  const [server, client, schema] = await Promise.all([
    source('lib/auth/index.ts'),
    source('lib/auth/client.ts'),
    source('lib/db/schema.ts'),
  ]);

  assert.match(server, /emailOTP\(\{/);
  assert.match(server, /otpLength: 6/);
  assert.match(server, /expiresIn: 5 \* 60/);
  assert.match(server, /allowedAttempts: 3/);
  assert.match(server, /storeOTP: 'hashed'/);
  assert.match(server, /disableSignUp: env\.REGISTER_MODE === 'closed'/);
  assert.match(server, /ctx\.path === '\/sign-in\/email-otp' && env\.REGISTER_MODE === 'open'/);
  assert.match(server, /rateLimit: \{ window: 60, max: 3 \}/);
  assert.match(server, /sendTransactionalEmail/);
  assert.match(server, /reset your Iris password/);
  assert.match(server, /verify your Iris email address/);
  assert.match(server, /If you did not request it, you can safely ignore this email/);
  assert.match(server, /如果不是你本人操作，请忽略此邮件/);
  assert.match(client, /emailOTPClient\(\)/);
  assert.match(schema, /export const verifications/);
});

test('the existing login page offers password and two-step email-code sign in', async () => {
  const [form, page] = await Promise.all([
    source('components/login-form.tsx'),
    source('app/[locale]/page.tsx'),
  ]);

  assert.match(page, /href=\{`\/\$\{locale\}\/login` as Route\}/);
  assert.match(form, /type LoginMethod = 'password' \| 'otp'/);
  assert.match(form, /authClient\.emailOtp\.sendVerificationOtp/);
  assert.match(form, /authClient\.signIn\.emailOtp/);
  assert.match(form, /name: otpUsername, username: otpUsername, displayUsername: otpUsername/);
  assert.match(form, /setResendSeconds\(60\)/);
  assert.match(form, /safeNext\(/);
  assert.match(form, /authClient\.signIn\.email/);
  assert.match(form, /authClient\.signIn\.username/);
  assert.match(form, /authClient\.signUp\.email/);
});

test('email OTP feedback is localized in Chinese and English', async () => {
  const [zh, en] = await Promise.all([
    source('messages/zh.json'),
    source('messages/en.json'),
  ]);

  for (const messages of [zh, en]) {
    assert.match(messages, /"otpLogin"/);
    assert.match(messages, /"otpInvalid"/);
    assert.match(messages, /"otpExpired"/);
    assert.match(messages, /"otpTooManyAttempts"/);
    assert.match(messages, /"otpUsernameHint"/);
  }
});
