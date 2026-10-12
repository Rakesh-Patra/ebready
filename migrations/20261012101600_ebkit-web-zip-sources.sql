ALTER TABLE public.ebkit_web_projects DROP CONSTRAINT ebkit_web_projects_repository_check;
ALTER TABLE public.ebkit_web_projects ADD CONSTRAINT ebkit_web_projects_repository_check
CHECK (
  repository ~ '^https://github.com/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$'
  OR (mode = 'live' AND repository ~ '^zip:[A-Za-z0-9_. -]{1,100}$')
);
