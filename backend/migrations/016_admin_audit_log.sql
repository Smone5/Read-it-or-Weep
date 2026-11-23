-- Audit trail for destructive and security-sensitive admin actions.

create table if not exists admin_audit_log (
    id          uuid primary key default gen_random_uuid(),
    admin_id    uuid references admin_users (id) on delete set null,
    admin_email text,
    action      text not null,
    target_type text not null,
    target_id   text,
    quiz_id     uuid references quizzes (id) on delete set null,
    details     jsonb not null default '{}'::jsonb,
    created_at  timestamptz not null default now()
);

create index if not exists admin_audit_log_created_idx
    on admin_audit_log (created_at desc);

create index if not exists admin_audit_log_quiz_idx
    on admin_audit_log (quiz_id, created_at desc)
    where quiz_id is not null;
