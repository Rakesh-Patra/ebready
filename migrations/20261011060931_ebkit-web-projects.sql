CREATE TABLE public.ebkit_web_projects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  name text NOT NULL CHECK (name ~ '^[a-z][a-z0-9-]{1,39}$'),
  repository text NOT NULL CHECK (repository ~ '^https://github.com/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$'),
  region text NOT NULL CHECK (region IN ('us-east-1','us-east-2','us-west-2','ap-south-1')),
  port integer NOT NULL CHECK (port BETWEEN 1 AND 65535),
  mode text NOT NULL DEFAULT 'demo' CHECK (mode = 'demo'),
  status text NOT NULL DEFAULT 'deploying' CHECK (status IN ('deploying','ready')),
  stage integer NOT NULL DEFAULT 0 CHECK (stage BETWEEN 0 AND 6),
  replicas integer NOT NULL DEFAULT 1 CHECK (replicas BETWEEN 1 AND 100),
  revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
  variable_names text[] NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ebkit_web_projects_owner_created ON public.ebkit_web_projects(user_id, created_at DESC);
ALTER TABLE public.ebkit_web_projects ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ebkit_web_projects FROM anon, authenticated;
GRANT SELECT, DELETE ON public.ebkit_web_projects TO authenticated;
GRANT INSERT (id,user_id,name,repository,region,port,mode,status,stage,replicas,revision,variable_names,created_at) ON public.ebkit_web_projects TO authenticated;
GRANT UPDATE (name,status,stage,replicas,revision,variable_names) ON public.ebkit_web_projects TO authenticated;
CREATE POLICY owner_select ON public.ebkit_web_projects FOR SELECT TO authenticated USING (user_id = auth.uid());
CREATE POLICY owner_insert ON public.ebkit_web_projects FOR INSERT TO authenticated WITH CHECK (user_id = auth.uid());
CREATE POLICY owner_update ON public.ebkit_web_projects FOR UPDATE TO authenticated USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY owner_delete ON public.ebkit_web_projects FOR DELETE TO authenticated USING (user_id = auth.uid());
