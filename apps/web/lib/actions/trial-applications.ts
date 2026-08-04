'use server';

import {and, eq, inArray, sql} from 'drizzle-orm';
import {headers} from 'next/headers';
import {revalidatePath} from 'next/cache';
import {z} from 'zod';

import {adminAuditEntry, getAdminMutationContext} from '@/lib/admin/audit';
import {db} from '@/lib/db';
import {adminAuditLogs, members, organizations, trialApplications, users} from '@/lib/db/schema';
import {
  APPLICATION_STATUSES,
  TRIAL_PLANS,
  type TrialApplicationState,
} from '@/lib/trial-applications';

const publicApplicationSchema = z
  .object({
    companyName: z.string().trim().min(1).max(120),
    contactName: z.string().trim().min(1).max(80),
    workEmail: z.string().trim().max(160),
    wechat: z.string().trim().max(80),
    expectedAccounts: z.coerce.number().int().min(1).max(100_000),
    plan: z.enum(TRIAL_PLANS),
    locale: z.enum(['zh', 'en']).default('zh'),
    notes: z.string().trim().max(1_000),
    consent: z.literal('on'),
    website: z.string().max(0),
  })
  .superRefine((value, ctx) => {
    if (!value.workEmail && !value.wechat) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['workEmail'],
        message: 'contact_required',
      });
    }
    if (value.workEmail && !z.string().email().safeParse(value.workEmail).success) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['workEmail'],
        message: 'invalid_email',
      });
    }
  });

const RATE_LIMIT_WINDOW_MS = 10 * 60 * 1000;
const RATE_LIMIT_MAX = 5;
const publicAttempts = new Map<string, number[]>();

async function publicRequestKey() {
  const h = await headers();
  return h.get('x-real-ip') ?? h.get('x-forwarded-for')?.split(',')[0]?.trim() ?? 'unknown';
}

function rateLimited(key: string) {
  const now = Date.now();
  const recent = (publicAttempts.get(key) ?? []).filter((time) => now - time < RATE_LIMIT_WINDOW_MS);
  recent.push(now);
  publicAttempts.set(key, recent);
  return recent.length > RATE_LIMIT_MAX;
}

function value(formData: FormData, key: string) {
  return String(formData.get(key) ?? '');
}

export async function submitTrialApplication(
  _previous: TrialApplicationState,
  formData: FormData,
): Promise<TrialApplicationState> {
  const formValues = {
    companyName: value(formData, 'companyName'),
    contactName: value(formData, 'contactName'),
    workEmail: value(formData, 'workEmail'),
    wechat: value(formData, 'wechat'),
    expectedAccounts: value(formData, 'expectedAccounts'),
    plan: value(formData, 'plan'),
    notes: value(formData, 'notes'),
    consent: value(formData, 'consent') === 'on',
  };
  const parsed = publicApplicationSchema.safeParse({
    ...formValues,
    locale: value(formData, 'locale'),
    consent: value(formData, 'consent'),
    website: value(formData, 'website'),
  });
  if (!parsed.success) {
    const issue = parsed.error.issues[0]?.message;
    return {
      error:
        issue === 'contact_required'
          ? 'contact_required'
          : issue === 'invalid_email'
            ? 'invalid_email'
            : 'invalid_form',
      values: formValues,
    };
  }
  if (rateLimited(await publicRequestKey())) return {error: 'rate_limited', values: formValues};

  const data = parsed.data;
  const normalizedEmail = data.workEmail ? data.workEmail.toLowerCase() : null;
  const openStatus = inArray(trialApplications.status, ['new', 'contacted']);
  const contactMatch = normalizedEmail
    ? sql`lower(${trialApplications.workEmail}) = ${normalizedEmail}`
    : eq(trialApplications.wechat, data.wechat);
  const [existing] = await db
    .select({id: trialApplications.id})
    .from(trialApplications)
    .where(and(openStatus, contactMatch))
    .limit(1);

  const record = {
    companyName: data.companyName,
    contactName: data.contactName,
    workEmail: normalizedEmail,
    wechat: data.wechat || null,
    expectedAccounts: data.expectedAccounts,
    plan: data.plan,
    locale: data.locale,
    notes: data.notes || null,
    consentAt: new Date(),
    updatedAt: new Date(),
  };
  if (existing) {
    await db.update(trialApplications).set(record).where(eq(trialApplications.id, existing.id));
  } else {
    await db.insert(trialApplications).values(record);
  }
  return {ok: true};
}

const applicationIdSchema = z.coerce.number().int().positive();

export async function updateTrialApplicationStatus(formData: FormData) {
  const context = await getAdminMutationContext();
  const id = applicationIdSchema.safeParse(formData.get('applicationId'));
  const status = z.enum(APPLICATION_STATUSES).safeParse(formData.get('status'));
  if (!id.success || !status.success) return;
  await db.transaction(async (tx) => {
    await tx.execute(sql`SELECT pg_advisory_xact_lock(20260721, ${id.data})`);
    const [target] = await tx
      .select({status: trialApplications.status})
      .from(trialApplications)
      .where(eq(trialApplications.id, id.data))
      .limit(1);
    if (!target) return;
    await tx
      .update(trialApplications)
      .set({
        status: status.data,
        processedBy: context.actor.id,
        processedAt: new Date(),
        updatedAt: new Date(),
      })
      .where(eq(trialApplications.id, id.data));
    await tx.insert(adminAuditLogs).values(
      adminAuditEntry(context, {
        action: 'application.status.update',
        targetType: 'trial_application',
        targetId: String(id.data),
        before: {status: target.status},
        after: {status: status.data},
        reason: null,
      }),
    );
  });
  revalidatePath('/zh/applications');
  revalidatePath('/en/applications');
  revalidatePath('/zh/admin/applications');
  revalidatePath('/en/admin/applications');
  revalidatePath('/zh/admin');
  revalidatePath('/en/admin');
}

export async function grantTrialApplicationQuota(formData: FormData) {
  const context = await getAdminMutationContext();
  const id = applicationIdSchema.safeParse(formData.get('applicationId'));
  const quota = z.coerce.number().int().min(0).max(100_000).safeParse(formData.get('quota'));
  if (!id.success || !quota.success) return;

  await db.transaction(async (tx) => {
    await tx.execute(sql`SELECT pg_advisory_xact_lock(20260721, ${id.data})`);
    const [target] = await tx
      .select({
        applicationId: trialApplications.id,
        applicationStatus: trialApplications.status,
        applicationQuota: trialApplications.grantedQuota,
        organizationId: organizations.id,
        organizationQuota: organizations.monitorQuota,
      })
      .from(trialApplications)
      .leftJoin(users, sql`lower(${users.email}) = lower(${trialApplications.workEmail})`)
      .leftJoin(
        members,
        and(eq(members.userId, users.id), eq(members.role, 'owner')),
      )
      .leftJoin(organizations, eq(organizations.id, members.organizationId))
      .where(eq(trialApplications.id, id.data))
      .limit(1);
    if (!target?.organizationId) return;
    await tx
      .update(organizations)
      .set({monitorQuota: quota.data, updatedAt: new Date()})
      .where(eq(organizations.id, target.organizationId!));
    await tx
      .update(trialApplications)
      .set({
        organizationId: target.organizationId,
        grantedQuota: quota.data,
        status: quota.data > 0 ? 'approved' : 'contacted',
        processedBy: context.actor.id,
        processedAt: new Date(),
        updatedAt: new Date(),
      })
      .where(eq(trialApplications.id, target.applicationId));
    await tx.insert(adminAuditLogs).values(
      adminAuditEntry(context, {
        action: 'application.quota.grant',
        targetType: 'trial_application',
        targetId: String(target.applicationId),
        before: {
          status: target.applicationStatus,
          grantedQuota: target.applicationQuota,
          organizationId: target.organizationId,
          organizationQuota: target.organizationQuota,
        },
        after: {
          status: quota.data > 0 ? 'approved' : 'contacted',
          grantedQuota: quota.data,
          organizationId: target.organizationId,
          organizationQuota: quota.data,
        },
        reason: null,
      }),
    );
  });
  revalidatePath('/zh/applications');
  revalidatePath('/en/applications');
  revalidatePath('/zh/monitor');
  revalidatePath('/en/monitor');
  revalidatePath('/zh/admin/applications');
  revalidatePath('/en/admin/applications');
  revalidatePath('/zh/admin');
  revalidatePath('/en/admin');
}
