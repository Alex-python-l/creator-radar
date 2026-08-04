#!/usr/bin/env node
/** Assign the pre-workspace shared Radar data to one bootstrap team. */
import postgres from 'postgres';

function arg(name) {
  const index = process.argv.indexOf(`--${name}`);
  const value = index === -1 ? undefined : process.argv[index + 1];
  return value && !value.startsWith('--') ? value : undefined;
}

const dbUrl = arg('db') ?? process.env.DATABASE_URL;
const leaderEmail = arg('leader-email')?.trim().toLowerCase();
const teamName = arg('team-name')?.trim() || 'Iris Internal Team';
const apply = process.argv.includes('--apply');
if (!dbUrl || !leaderEmail) {
  console.error(
    'usage: bootstrap-legacy-team.mjs --db <url> --leader-email <email> '
    + '[--team-name <name>] [--apply]',
  );
  process.exit(1);
}

const sql = postgres(dbUrl, { max: 1 });
try {
  const [leader] = await sql`
    SELECT id, email FROM users WHERE lower(email) = ${leaderEmail} LIMIT 1`;
  if (!leader) throw new Error(`leader not found: ${leaderEmail}`);

  const [before] = await sql`
    SELECT
      (SELECT count(*)::int FROM users) AS users,
      (SELECT count(*)::int FROM account_groups WHERE workspace_id IS NULL) AS shared_groups,
      (SELECT count(*)::int FROM influencers WHERE is_monitored = true) AS monitored`;
  console.log(JSON.stringify({ mode: apply ? 'apply' : 'dry-run', leader, teamName, before }, null, 2));
  if (!apply) {
    console.log('dry-run only; add --apply to commit');
    process.exit(0);
  }

  const result = await sql.begin(async (tx) => {
    await tx`SELECT pg_advisory_xact_lock(20260718, 1010)`;
    let [organization] = await tx`
      SELECT id, name FROM organizations WHERE slug = 'legacy-team' LIMIT 1`;
    if (!organization) {
      [organization] = await tx`
        INSERT INTO organizations(name, slug, monitor_quota, created_at, updated_at)
        VALUES (${teamName}, 'legacy-team', 2147483647, now(), now())
        RETURNING id, name`;
    }
    const [existingOwner] = await tx`
      SELECT user_id FROM members
      WHERE organization_id = ${organization.id} AND role = 'owner' LIMIT 1`;
    if (existingOwner && existingOwner.user_id !== leader.id) {
      throw new Error(`legacy team already has another leader: user ${existingOwner.user_id}`);
    }
    await tx`
      INSERT INTO members(organization_id, user_id, role, created_at)
      VALUES (${organization.id}, ${leader.id}, 'owner', now())
      ON CONFLICT (user_id) DO UPDATE SET
        organization_id = EXCLUDED.organization_id, role = 'owner'`;
    await tx`
      INSERT INTO members(organization_id, user_id, role, created_at)
      SELECT ${organization.id}, id, 'admin', now()
      FROM users WHERE id <> ${leader.id}
      ON CONFLICT (user_id) DO NOTHING`;

    let [workspace] = await tx`
      SELECT id FROM workspaces WHERE organization_id = ${organization.id} LIMIT 1`;
    if (!workspace) {
      [workspace] = await tx`
        INSERT INTO workspaces(kind, organization_id)
        VALUES ('team', ${organization.id}) RETURNING id`;
    }
    await tx`
      UPDATE account_groups SET workspace_id = ${workspace.id}
      WHERE workspace_id IS NULL`;
    await tx`
      INSERT INTO workspace_influencers(workspace_id, influencer_id, group_id, alias, created_at)
      SELECT ${workspace.id}, i.id,
             CASE WHEN g.workspace_id = ${workspace.id} THEN i.group_id ELSE NULL END,
             i.alias,
             now()
      FROM influencers i
      LEFT JOIN account_groups g ON g.id = i.group_id
      ON CONFLICT (workspace_id, influencer_id) DO NOTHING`;
    await tx`
      UPDATE influencers i SET is_monitored = true
      WHERE EXISTS (
        SELECT 1 FROM workspace_influencers wi
        WHERE wi.workspace_id = ${workspace.id} AND wi.influencer_id = i.id
      )`;
    const [counts] = await tx`
      SELECT
        (SELECT count(*)::int FROM members WHERE organization_id = ${organization.id}) AS members,
        (SELECT count(*)::int FROM members WHERE organization_id = ${organization.id}
          AND role = 'owner') AS leaders,
        (SELECT count(*)::int FROM account_groups WHERE workspace_id = ${workspace.id}) AS groups,
        (SELECT count(*)::int FROM workspace_influencers
          WHERE workspace_id = ${workspace.id}) AS subscriptions`;
    if (counts.leaders !== 1) throw new Error(`expected one leader, got ${counts.leaders}`);
    return { organization, workspace, counts };
  });
  console.log(JSON.stringify({ committed: true, ...result }, null, 2));
} finally {
  await sql.end();
}
