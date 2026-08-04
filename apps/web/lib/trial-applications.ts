export const TRIAL_PLANS = ['trial', 'pro', 'scale', 'custom'] as const;
export const APPLICATION_STATUSES = ['new', 'contacted', 'approved', 'rejected'] as const;

export type TrialPlan = (typeof TRIAL_PLANS)[number];
export type ApplicationStatus = (typeof APPLICATION_STATUSES)[number];

export type TrialApplicationState = {
  ok?: boolean;
  error?: string;
  values?: {
    companyName: string;
    contactName: string;
    workEmail: string;
    wechat: string;
    expectedAccounts: string;
    plan: string;
    notes: string;
    consent: boolean;
  };
};
